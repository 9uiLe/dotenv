"""共有設定の衝突拒否と、中断・失敗時の復旧を確認する。"""

from copy import deepcopy
from datetime import timedelta
import fcntl
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import shutil
import unittest

from tests import test_system as system
from ai_knowledge.engine import Manager
from tests.fixtures import make_bundle
from ai_knowledge.sandbox import create_sandbox
from tests.test_system import MORNING, REPO, RULES, SKILLS, TOOLS, target_snapshot, write_text


class SharingRecoveryTests(system.SharingSystemCase):
    def test_invalid_removed_and_traversing_bundles_do_not_change_active_targets(self):
        manager = self.manager("work")
        manager.sync(now=MORNING)
        invalid = {"version": "invalid"}
        removed = deepcopy(self.bundle)
        removed["version"] = "removed"
        removed["files"].pop("skills/ai-knowledge-example/SKILL.md")
        traversal = deepcopy(self.bundle)
        traversal["version"] = "traversal"
        traversal["files"]["../escaped-by-bundle.txt"] = "must not be written"
        for candidate in (invalid, removed, traversal):
            with self.subTest(version=candidate["version"]):
                self.publish(bundle=candidate)
                before = target_snapshot(self.root / "work")
                self.assertEqual(manager.sync(now=MORNING, force=True)["outcome"], "failed")
                self.assertEqual(target_snapshot(self.root / "work"), before)
                self.assert_version(manager, "v1")
        self.assertFalse((self.root / "escaped-by-bundle.txt").exists())


    def test_direct_shared_edits_and_existing_same_name_skills_are_never_overwritten(self):
        for relative in (RULES["codex"], SKILLS["codex"]):
            with self.subTest(relative=relative):
                root = create_sandbox(prefix="system-conflict-")
                self.addCleanup(shutil.rmtree, root)
                home = root / "personal"
                source = root / "source"
                write_text(source / "bundle.json", json.dumps(make_bundle("v1")))
                manager = Manager(home, source, profile="personal")
                manager.sync(now=MORNING)
                path = home / relative
                content = path.read_text()
                original = self.bundle["rules"].rstrip() if relative == RULES["codex"] else content
                write_text(path, content.replace(original, original + " directly edited"))
                write_text(source / "bundle.json", json.dumps(make_bundle("v2")))
                before = target_snapshot(home)
                self.assertEqual(manager.sync(now=MORNING, force=True)["outcome"], "failed")
                self.assertEqual(target_snapshot(home), before)
        home = self.root / "personal"
        write_text(home / SKILLS["codex"], "pre-existing same-name skill\n")
        manager = self.manager()
        before = target_snapshot(home)
        self.assertEqual(manager.sync(now=MORNING)["outcome"], "failed")
        self.assertEqual(target_snapshot(home), before)


    def test_unmarked_homes_and_symlink_destinations_are_rejected_without_writes(self):
        unmarked = self.root / "not-a-mock-home"
        unmarked.mkdir()
        with self.assertRaises(ValueError):
            Manager(unmarked, self.source, profile="personal")
        home = self.root / "personal"
        outside = self.root / "outside-target.txt"
        write_text(outside, "outside target must remain unchanged\n")
        target = home / RULES["codex"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(outside)
        before = target_snapshot(home)
        try:
            outcome = self.manager().sync(now=MORNING)
        except ValueError:
            pass
        else:
            self.assertEqual(outcome["outcome"], "failed")
        self.assertEqual(target_snapshot(home), before)
        self.assertEqual(outside.read_text(), "outside target must remain unchanged\n")


    def test_update_failure_rolls_back_settings_and_keeps_the_old_hook_version(self):
        manager = self.manager("work")
        manager.sync(now=MORNING)
        before = target_snapshot(self.root / "work")
        self.publish("v2")
        result = manager.sync(now=MORNING, force=True, fail_after=1)
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(target_snapshot(self.root / "work"), before)
        self.assert_version(self.manager("work"), "v1")
        self.assert_hook_version("v1", "work")


    def test_lock_prevents_a_second_update_before_any_target_is_written(self):
        manager = self.manager()
        manager.sync(now=MORNING)
        self.publish("v2")
        before = target_snapshot(self.root / "personal")
        manager.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with manager.lock_path.open("a+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = manager.sync(now=MORNING, force=True)
            self.assertIn(result["outcome"], ("failed", "paused"))
            self.assertEqual(target_snapshot(self.root / "personal"), before)
            fcntl.flock(stream, fcntl.LOCK_UN)
        self.assert_version(manager, "v1")


    def test_individually_edited_schedule_is_not_replaced_retired_or_uninstalled(self):
        manager = self.manager()
        manager.sync(now=MORNING)
        home = self.root / "personal"
        schedule = home / "Library/LaunchAgents/local.ai-knowledge.sync.plist"
        with schedule.open("rb") as stream:
            data = plistlib.load(stream)
        data["StartCalendarInterval"]["Hour"] = 11
        with schedule.open("wb") as stream:
            plistlib.dump(data, stream)
        self.publish("v2")
        before = target_snapshot(home)
        schedule_stat = schedule.stat()
        retired = home / ".local/share/ai-knowledge/retired"
        retired_before = sorted(str(path) for path in retired.rglob("*"))
        operations = {
            "pause": manager.pause_sync,
            "uninstall": lambda: manager.uninstall(apply=True),
            "update": lambda: manager.sync(now=MORNING + timedelta(days=1)),
        }
        for name, operation in operations.items():
            with self.subTest(operation=name):
                self.assertEqual(operation()["outcome"], "failed")
                self.assertEqual(target_snapshot(home), before)
                self.assertEqual((schedule.stat().st_ino, schedule.stat().st_mtime_ns),
                                 (schedule_stat.st_ino, schedule_stat.st_mtime_ns))
                self.assertEqual(sorted(str(path) for path in retired.rglob("*")), retired_before)
                self.assert_version(manager, "v1")


    def test_a_killed_update_is_recovered_before_the_next_sync_decision(self):
        manager = self.manager("work")
        manager.sync(now=MORNING)
        home = self.root / "work"
        before = target_snapshot(home)
        self.publish("v2")
        child_code = '''
from datetime import datetime
import os
from pathlib import Path
import sys
from ai_knowledge import engine

home = Path(sys.argv[1])
original_write = engine.write_atomic

def crash_after_first_setting_write(path, text, mode=0o600):
    original_write(path, text, mode)
    relative = Path(path).relative_to(home)
    if relative.parts[0] in {'.codex', '.claude', '.agents', 'Library'}:
        os._exit(86)

engine.write_atomic = crash_after_first_setting_write
engine.Manager(home, Path(sys.argv[2]), profile='work').sync(
    now=datetime.fromisoformat(sys.argv[3]), force=True)
'''
        child = subprocess.run(
            [sys.executable, "-B", "-c", child_code, str(home), str(self.source),
             (MORNING + timedelta(minutes=30)).isoformat()],
            capture_output=True, text=True, timeout=10, cwd=REPO,
        )
        self.assertEqual(child.returncode, 86, child.stderr + child.stdout)
        self.assertNotEqual(target_snapshot(home), before)
        self.assertEqual(self.manager("work").status()["phase"], "applying")
        for tool in TOOLS["work"]:
            self.assertEqual(self.run_hook("work", tool), {})
        restarted = self.manager("work")
        result = restarted.sync(now=MORNING + timedelta(minutes=31))
        self.assertEqual(result["outcome"], "already_today")
        self.assertEqual(target_snapshot(home), before)
        self.assertEqual(restarted.status()["phase"], "ready")
        self.assert_version(restarted, "v1")
        self.assert_hook_version("v1", "work")
        self.assertEqual(restarted.sync(now=MORNING + timedelta(days=1))["outcome"], "updated")
        self.assert_version(restarted, "v2")
        self.assert_hook_version("v2", "work")



if __name__ == "__main__":
    unittest.main()
