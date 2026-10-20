"""Requirement checks in retained mock homes; never use real AI settings."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import plistlib
import subprocess
import sys
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


class SharingSystemCase(unittest.TestCase):
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



class SharingSystemTests(SharingSystemCase):
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



if __name__ == "__main__":
    unittest.main()
