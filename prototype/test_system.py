"""Requirement checks in retained mock homes; never use real AI settings."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import fcntl
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tomllib
import unittest

from prototype.engine import Manager
from prototype.fixtures import make_bundle
from prototype.sandbox import check_home, create_sandbox


REPO = Path(__file__).resolve().parents[1]
MORNING = datetime(2026, 10, 20, 10, 0, tzinfo=timezone(timedelta(hours=9)))
RULES = {"codex": ".codex/AGENTS.md", "claude": ".claude/CLAUDE.md"}
SKILLS = {
    "codex": ".agents/skills/ai-knowledge-example/SKILL.md",
    "claude": ".claude/skills/ai-knowledge-example/SKILL.md",
}
HOOK_CONFIG = {"codex": ".codex/hooks.json", "claude": ".claude/settings.json"}
TOOLS = {"personal": ("codex",), "work": ("codex", "claude")}


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def target_snapshot(home):
    """Observe settings and skill files without following links or internal logs."""
    result = {}

    def visit(path):
        if path.is_symlink():
            result[str(path.relative_to(home))] = ("link", str(path.readlink()))
        elif path.is_file():
            result[str(path.relative_to(home))] = ("file", path.read_bytes())
        elif path.is_dir():
            for child in path.iterdir():
                visit(child)

    for relative in (".codex", ".claude", ".agents", "Library/LaunchAgents"):
        visit(home / relative)
    return result


class SharingSystemTests(unittest.TestCase):
    def setUp(self):
        self.root = create_sandbox(prefix="system-test-")
        self.source = self.root / "source"
        self.bundle = self.publish("v1")

    def publish(self, version="v1", bundle=None):
        content = make_bundle(version=version) if bundle is None else bundle
        write_text(self.source / "bundle.json", json.dumps(content, ensure_ascii=False))
        return content

    def manager(self, profile="personal"):
        return Manager(self.root / profile, self.source, profile=profile)

    def assert_version(self, manager, version):
        self.assertEqual(manager.status()["active"]["bundle"]["version"], version)

    def assert_deployed(self, profile, bundle):
        home = self.root / profile
        for tool in TOOLS[profile]:
            self.assertIn(bundle["rules"].rstrip(), (home / RULES[tool]).read_text())
            self.assertEqual((home / SKILLS[tool]).read_text(),
                             bundle["files"]["skills/ai-knowledge-example/SKILL.md"])

    def local_configuration(self, profile):
        home = self.root / profile
        entries = {}
        for tool in TOOLS[profile]:
            write_text(home / RULES[tool], f"{tool}: local rules before installation\n")
            entry = {"hooks": [{"type": "command", "command": f"echo local-{tool}"}]}
            entries[tool] = entry
            data = {"hooks": {"SessionStart": [entry]}, "localSetting": f"keep-{tool}"}
            write_text(home / HOOK_CONFIG[tool], json.dumps(data))
        write_text(home / ".agents/skills/local-only/SKILL.md", "local skill\n")
        return entries

    def assert_local_configuration(self, profile, entries):
        home = self.root / profile
        for tool in TOOLS[profile]:
            self.assertIn(f"{tool}: local rules before installation",
                          (home / RULES[tool]).read_text())
            data = json.loads((home / HOOK_CONFIG[tool]).read_text())
            self.assertEqual(data["localSetting"], f"keep-{tool}")
            self.assertIn(entries[tool], data["hooks"]["SessionStart"])
        self.assertEqual((home / ".agents/skills/local-only/SKILL.md").read_text(),
                         "local skill\n")

    def hook_process(self, profile="personal", tool="codex"):
        return subprocess.run(
            [sys.executable, "-B", str(REPO / "prototype/hook_runner.py"),
             "--home", str(self.root / profile), "--tool", tool,
             "--hook", "shared-version"],
            input=json.dumps({"hook_event_name": "SessionStart"}),
            capture_output=True, text=True, timeout=10, cwd=REPO,
        )

    def run_hook(self, profile="personal", tool="codex"):
        result = self.hook_process(profile, tool)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def assert_hook_version(self, version, profile="personal"):
        for tool in TOOLS[profile]:
            output = self.run_hook(profile, tool)
            self.assertEqual(output["hookSpecificOutput"]["additionalContext"],
                             f"共有設定 {version}")

    def test_profiles_preserve_existing_settings_and_generate_only_a_schedule_file(self):
        for profile in TOOLS:
            with self.subTest(profile=profile):
                entries = self.local_configuration(profile)
                manager = self.manager(profile)
                self.assertEqual(manager.sync(now=MORNING)["outcome"], "updated")
                self.assert_deployed(profile, self.bundle)
                self.assert_local_configuration(profile, entries)
                self.assert_version(manager, "v1")
                home = self.root / profile
                self.assertEqual(check_home(home), home.resolve())
                self.assertTrue((home / ".local/share/ai-knowledge/state.json").is_file())
                schedule = home / "Library/LaunchAgents/local.ai-knowledge.sync.plist"
                with schedule.open("rb") as stream:
                    data = plistlib.load(stream)
                self.assertEqual(data["StartCalendarInterval"], {"Hour": 10, "Minute": 0})
                self.assertIs(data["RunAtLoad"], True)
        self.assertFalse((self.root / "personal/.claude").exists())

    def test_daily_boundary_and_next_day_continue_from_saved_state(self):
        manager = self.manager()
        self.assertEqual(manager.sync(now=MORNING - timedelta(minutes=1))["outcome"], "not_due")
        self.assertIsNone(manager.status()["active"])
        self.assertEqual(manager.sync(now=MORNING)["outcome"], "updated")
        before = target_snapshot(self.root / "personal")
        second = self.publish("v2")
        self.assertEqual(manager.sync(now=MORNING + timedelta(hours=1))["outcome"], "already_today")
        self.assertEqual(target_snapshot(self.root / "personal"), before)
        restarted = self.manager()
        self.assert_version(restarted, "v1")
        self.assertEqual(restarted.sync(now=MORNING + timedelta(days=1, minutes=-1))["outcome"],
                         "not_due")
        self.assertEqual(restarted.sync(now=MORNING + timedelta(days=1))["outcome"], "updated")
        self.assert_deployed("personal", second)
        self.assert_version(self.manager(), "v2")
        self.assertEqual(restarted.sync(now=MORNING + timedelta(days=1), force=True)["outcome"],
                         "unchanged")

    def test_failed_attempt_does_not_consume_that_days_retry(self):
        manager = self.manager()
        bundle_file = self.source / "bundle.json"
        offline_copy = self.source / "bundle-offline.json"
        bundle_file.rename(offline_copy)
        before = target_snapshot(self.root / "personal")
        self.assertEqual(manager.sync(now=MORNING)["outcome"], "failed")
        self.assertEqual(target_snapshot(self.root / "personal"), before)
        offline_copy.rename(bundle_file)
        recovered = self.manager()
        self.assertEqual(recovered.sync(now=MORNING + timedelta(minutes=1))["outcome"], "updated")
        self.assert_version(recovered, "v1")
        before = target_snapshot(self.root / "personal")
        next_day = MORNING + timedelta(days=1)
        bundle_file.rename(offline_copy)
        offline = self.manager()
        self.assertEqual(offline.sync(now=next_day)["outcome"], "failed")
        self.assertEqual(target_snapshot(self.root / "personal"), before)
        self.assert_version(offline, "v1")
        offline_copy.rename(bundle_file)
        write_text(self.source / "bundle.json", "{invalid json")
        before = target_snapshot(self.root / "personal")
        self.assertEqual(recovered.sync(now=next_day)["outcome"], "failed")
        self.assertEqual(target_snapshot(self.root / "personal"), before)
        self.publish("v2")
        self.assertEqual(recovered.sync(now=next_day + timedelta(minutes=1))["outcome"], "updated")
        self.assert_version(recovered, "v2")

    def test_hook_scripts_update_and_the_two_pause_controls_are_independent(self):
        manager = self.manager("work")
        manager.sync(now=MORNING)
        self.assert_hook_version("v1", "work")
        manager.pause_hooks()
        for tool in TOOLS["work"]:
            self.assertEqual(self.run_hook("work", tool), {})
        manager.pause_sync()
        schedule = self.root / "work/Library/LaunchAgents/local.ai-knowledge.sync.plist"
        self.assertFalse(schedule.exists())
        manager.resume_sync()
        self.assertTrue(schedule.exists())
        self.publish("v2")
        self.assertEqual(self.manager("work").sync(now=MORNING + timedelta(days=1))["outcome"], "updated")
        for tool in TOOLS["work"]:
            self.assertEqual(self.run_hook("work", tool), {})
        manager = self.manager("work")
        manager.resume_hooks()
        self.assert_hook_version("v2", "work")
        manager.pause_sync()
        self.publish("v3")
        self.assertEqual(manager.sync(now=MORNING + timedelta(days=2))["outcome"], "paused")
        self.assert_hook_version("v2", "work")

    def test_restore_preview_is_read_only_and_restores_one_coherent_shared_version(self):
        entries = self.local_configuration("work")
        manager = self.manager("work")
        manager.sync(now=MORNING)
        self.publish("v2")
        manager.sync(now=MORNING, force=True)
        home = self.root / "work"
        for tool in TOOLS["work"]:
            path = home / RULES[tool]
            write_text(path, path.read_text() + "new local rules after installation\n")
        before = target_snapshot(home)
        state_before = (home / ".local/share/ai-knowledge/state.json").read_bytes()
        preview = manager.restore(previous=True, apply=False)
        self.assertTrue(preview["changes"])
        self.assertEqual(target_snapshot(home), before)
        self.assertEqual((home / ".local/share/ai-knowledge/state.json").read_bytes(), state_before)
        self.assert_version(manager, "v2")
        result = manager.restore(previous=True, apply=True)
        self.assertNotEqual(result.get("outcome"), "failed", result)
        status = manager.status()
        self.assertFalse(status["sync_enabled"])
        self.assertFalse(status["hooks_enabled"])
        self.assert_version(manager, "v1")
        self.assert_deployed("work", self.bundle)
        self.assert_local_configuration("work", entries)
        for tool in TOOLS["work"]:
            self.assertIn("new local rules after installation", (home / RULES[tool]).read_text())
            self.assertEqual(self.run_hook("work", tool), {})
        manager.resume_hooks()
        self.assert_hook_version("v1", "work")

    def test_restore_with_no_previous_version_or_a_local_edit_changes_no_targets(self):
        manager = self.manager()
        self.assertEqual(manager.restore(previous=True, apply=True)["outcome"], "failed")
        manager.sync(now=MORNING)
        before = target_snapshot(self.root / "personal")
        self.assertEqual(manager.restore(previous=True, apply=True)["outcome"], "failed")
        self.assertEqual(target_snapshot(self.root / "personal"), before)
        self.publish("v2")
        manager.sync(now=MORNING, force=True)
        path = self.root / "personal" / SKILLS["codex"]
        write_text(path, path.read_text() + "local edit\n")
        before = target_snapshot(self.root / "personal")
        for apply in (False, True):
            self.assertEqual(manager.restore(previous=True, apply=apply)["outcome"], "failed")
            self.assertEqual(target_snapshot(self.root / "personal"), before)
        self.assert_version(manager, "v2")

    def test_uninstall_preserves_existing_and_later_individual_settings_and_retires_skills(self):
        entries = self.local_configuration("work")
        manager = self.manager("work")
        manager.sync(now=MORNING)
        home = self.root / "work"
        for tool in TOOLS["work"]:
            path = home / RULES[tool]
            write_text(path, path.read_text() + "later individual instructions\n")
            path = home / HOOK_CONFIG[tool]
            data = json.loads(path.read_text())
            data["addedLater"] = {"keep": True}
            data["hooks"]["SessionStart"].append(
                {"hooks": [{"type": "command", "command": "echo added-later"}]})
            write_text(path, json.dumps(data))
        before = target_snapshot(home)
        state_before = (home / ".local/share/ai-knowledge/state.json").read_bytes()
        self.assertTrue(manager.uninstall(apply=False)["changes"])
        self.assertEqual(target_snapshot(home), before)
        self.assertEqual((home / ".local/share/ai-knowledge/state.json").read_bytes(), state_before)
        result = manager.uninstall(apply=True)
        self.assertNotEqual(result.get("outcome"), "failed", result)
        self.assertEqual(manager.status()["phase"], "uninstalled")
        self.assert_local_configuration("work", entries)
        for tool in TOOLS["work"]:
            self.assertIn("later individual instructions", (home / RULES[tool]).read_text())
            self.assertNotIn(self.bundle["rules"].rstrip(), (home / RULES[tool]).read_text())
            data = json.loads((home / HOOK_CONFIG[tool]).read_text())
            self.assertEqual(data["addedLater"], {"keep": True})
            self.assertIn({"hooks": [{"type": "command", "command": "echo added-later"}]},
                          data["hooks"]["SessionStart"])
            self.assertFalse((home / SKILLS[tool]).exists())
            self.assertEqual(self.run_hook("work", tool), {})
        retired = list((home / ".local/share/ai-knowledge/retired").rglob("SKILL.md"))
        self.assertTrue(retired)
        self.assertTrue(all(path.read_text() == self.bundle["files"]["skills/ai-knowledge-example/SKILL.md"]
                            for path in retired))

    def test_uninstall_keeps_new_settings_files_instead_of_deleting_them(self):
        manager = self.manager()
        manager.sync(now=MORNING)
        manager.uninstall(apply=True)
        home = self.root / "personal"
        self.assertTrue((home / RULES["codex"]).is_file())
        self.assertEqual((home / RULES["codex"]).read_text().strip(), "")
        self.assertTrue((home / HOOK_CONFIG["codex"]).is_file())
        data = json.loads((home / HOOK_CONFIG["codex"]).read_text())
        self.assertFalse(data.get("hooks", {}).get("SessionStart"))

    def test_existing_inline_codex_hooks_keep_comments_models_and_later_settings(self):
        home = self.root / "personal"
        config = home / ".codex/config.toml"
        original = (
            '# Keep this local model comment.\n'
            'model = "local-model"\n\n'
            '[hooks]\n'
            '# Keep this existing hook comment.\n'
            '[[hooks.SessionStart]]\n'
            '[[hooks.SessionStart.hooks]]\n'
            'type = "command"\n'
            'command = "echo local-inline-hook"\n'
        )
        write_text(config, original)
        original_group = tomllib.loads(original)["hooks"]["SessionStart"][0]
        manager = self.manager()
        self.assertEqual(manager.sync(now=MORNING)["outcome"], "updated")
        self.assertFalse((home / ".codex/hooks.json").exists())
        deployed = config.read_text()
        self.assertIn(original, deployed)
        data = tomllib.loads(deployed)
        self.assertEqual(data["model"], "local-model")
        self.assertIn(original_group, data["hooks"]["SessionStart"])
        self.assertGreater(len(data["hooks"]["SessionStart"]), 1)
        self.assert_hook_version("v1")
        later = '\n# Added after installation.\n[local_notice]\ntext = "keep later"\n'
        write_text(config, deployed + later)
        result = manager.uninstall(apply=True)
        self.assertNotEqual(result.get("outcome"), "failed", result)
        restored = config.read_text()
        self.assertIn(original, restored)
        self.assertIn(later, restored)
        data = tomllib.loads(restored)
        self.assertEqual(data["hooks"]["SessionStart"], [original_group])
        self.assertEqual(data["model"], "local-model")
        self.assertEqual(data["local_notice"], {"text": "keep later"})

    def test_tampering_with_another_release_file_stops_hooks_and_further_updates(self):
        bundle = make_bundle("v1")
        bundle["files"]["hooks/support.txt"] = "original shared support file\n"
        self.publish(bundle=bundle)
        manager = self.manager()
        self.assertEqual(manager.sync(now=MORNING)["outcome"], "updated")
        self.assert_hook_version("v1")
        releases = self.root / "personal/.local/share/ai-knowledge/releases"
        support_files = list(releases.glob("*/hooks/support.txt"))
        self.assertEqual(len(support_files), 1)
        write_text(support_files[0], "unexpected direct edit of shared support file\n")
        result = self.hook_process()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("additionalContext", result.stdout)
        bundle = make_bundle("v2")
        bundle["files"]["hooks/support.txt"] = "original shared support file\n"
        self.publish(bundle=bundle)
        before = target_snapshot(self.root / "personal")
        result = manager.sync(now=MORNING, force=True)
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(target_snapshot(self.root / "personal"), before)
        self.assert_version(manager, "v1")

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
from prototype import engine

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
