"""Installation proposals must leave even a fabricated target home untouched."""

import json
from pathlib import Path
import plistlib
import shlex
import stat
import unittest
from unittest.mock import patch

from prototype.fixtures import make_bundle
from prototype.preflight import project_installation
from prototype.sandbox import create_sandbox


SOURCE = {"url": "git@github.com:example/knowledge.git", "branch": "main",
          "manifest": "shared/manifest.json"}


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def snapshot(root):
    result = {}

    def visit(path):
        metadata = path.lstat()
        identity = (metadata.st_ino, metadata.st_mtime_ns, stat.S_IMODE(metadata.st_mode))
        if path.is_symlink():
            value = ("link", identity, str(path.readlink()))
        elif path.is_file():
            value = ("file", identity, path.read_bytes())
        else:
            value = ("directory", identity)
        result[str(path.relative_to(root))] = value
        if path.is_dir() and not path.is_symlink():
            for child in path.iterdir():
                visit(child)

    visit(root)
    return result


def proposed_text(patch_text, target):
    """Read context/additions from one small fixture file's complete diff hunk."""
    selected = False
    result = []
    for line in patch_text.splitlines(keepends=True):
        if line.startswith("--- "):
            selected = False
        elif line.startswith("+++ "):
            selected = line.rstrip("\n") == f"+++ {target} (導入案)"
        elif selected and line[:1] in {" ", "+"}:
            result.append(line[1:])
    return "".join(result)


class InstallationProjectionTests(unittest.TestCase):
    def setUp(self):
        self.root = create_sandbox(prefix="preflight-test-")
        self.live_home = self.root / "read-only-home"
        self.seed(self.live_home)
        self.bundle = make_bundle("v1")

    def seed(self, home, linked_rules=False):
        home.mkdir()
        codex_rules = home / ".codex/AGENTS.md"
        if linked_rules:
            target = home.parent / "outside-link-target.txt"
            write(target, "linked instructions must never be changed\n")
            codex_rules.parent.mkdir(parents=True)
            codex_rules.symlink_to(target)
        else:
            write(codex_rules, "Keep these local Codex instructions.\n")
        write(home / ".claude/CLAUDE.md", "Keep these local Claude instructions.\n")
        write(home / ".codex/config.toml", '# Keep this comment.\nmodel = "local-model"\n')
        for tool, relative in (("codex", ".codex/hooks.json"),
                               ("claude", ".claude/settings.json")):
            entry = {"hooks": [{"type": "command", "command": f"echo local-{tool}"}]}
            write(home / relative, json.dumps({"hooks": {"SessionStart": [entry]}}))
        write(home / ".agents/skills/local-only/SKILL.md", "Keep this unrelated skill.\n")
        write(home / "Documents/unrelated.txt", "Keep unrelated files too.\n")

    def test_projection_is_read_only_and_does_not_contact_the_candidate_source(self):
        before = snapshot(self.root)
        with patch("subprocess.run") as command:
            command.side_effect = AssertionError("A read-only proposal must not run a command")
            result = project_installation(self.live_home, self.bundle, SOURCE)
            command.assert_not_called()
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(result["live_settings_changed"])
        self.assertIs(result["ready_to_apply"], False)
        self.assertEqual(result["read_home"], str(self.live_home))
        self.assertEqual(result["source_candidate"], SOURCE)
        self.assertEqual(set(result["profiles"]), {"personal", "work"})
        for profile in result["profiles"].values():
            self.assertEqual(profile["outcome"], "projected")
        output = Path(result["report"]).parent
        self.assertNotEqual(output, self.root)
        self.assertEqual(output.parent, self.root.parent)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((output / "evidence").stat().st_mode), 0o700)
        for path in (output / "report.md", output / "evidence/summary.json",
                     *(Path(item["patch"]) for item in result["profiles"].values())):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_patches_preserve_local_content_and_show_the_target_paths_and_deferred_runtime(self):
        result = project_installation(self.live_home, self.bundle, SOURCE)
        output = Path(result["report"]).parent
        for name, profile in result["profiles"].items():
            with self.subTest(profile=name):
                text = Path(profile["patch"]).read_text()
                self.assertIn("適用不可", text.splitlines()[0])
                self.assertNotIn(str(output), text)
                self.assertTrue(profile["changes"])
                for change in profile["changes"]:
                    self.assertTrue(Path(change["path"]).is_relative_to(self.live_home))
                codex_rules = proposed_text(text, self.live_home / ".codex/AGENTS.md")
                self.assertIn("Keep these local Codex instructions.", codex_rules)
                self.assertIn(self.bundle["rules"].rstrip(), codex_rules)
                tools = ("codex",) if name == "personal" else ("codex", "claude")
                for tool in tools:
                    relative = ".codex/hooks.json" if tool == "codex" else ".claude/settings.json"
                    data = json.loads(proposed_text(text, self.live_home / relative))
                    groups = data["hooks"]["SessionStart"]
                    local = {"hooks": [{"type": "command", "command": f"echo local-{tool}"}]}
                    self.assertIn(local, groups)
                    shared = [group for group in groups if group != local]
                    self.assertEqual(len(shared), 1)
                    arguments = shlex.split(shared[0]["hooks"][0]["command"])
                    self.assertIn(str(self.live_home), arguments)
                    self.assertIn(str(self.live_home / ".local/share/ai-knowledge/runtime/hook_runner.py"),
                                  arguments)
                if name == "personal":
                    self.assertFalse(any("/.claude/" in item["path"] for item in profile["changes"]))
                else:
                    claude_rules = proposed_text(text, self.live_home / ".claude/CLAUDE.md")
                    self.assertIn("Keep these local Claude instructions.", claude_rules)
                schedule_path = self.live_home / "Library/LaunchAgents/local.ai-knowledge.sync.plist"
                schedule = plistlib.loads(proposed_text(text, schedule_path).encode())
                self.assertEqual(schedule["StartCalendarInterval"], {"Hour": 10, "Minute": 0})
                self.assertIn(str(self.live_home / ".local/share/ai-knowledge/runtime/sync.py"),
                              schedule["ProgramArguments"])
                scripts = [Path(item["path"]) for item in profile["changes"]
                           if item["path"].endswith("/hooks/session_start.py")]
                self.assertEqual(len(scripts), 1)
                self.assertIn("共有設定", proposed_text(text, scripts[0]))
                receiver_config = self.live_home / ".local/share/ai-knowledge/source/git.json"
                self.assertEqual(json.loads(proposed_text(text, receiver_config)), SOURCE)
        self.assertIn("適用できるパッチではありません", Path(result["report"]).read_text())
        self.assertIs(result["ready_to_apply"], False)

    def test_existing_links_same_name_skills_and_override_instructions_block_without_writes(self):
        for reason in ("link", "same-name-skill", "override"):
            with self.subTest(reason=reason):
                root = create_sandbox(prefix="preflight-blocked-")
                home = root / "read-only-home"
                self.seed(home, linked_rules=reason == "link")
                if reason == "same-name-skill":
                    write(home / ".agents/skills/ai-knowledge-example/SKILL.md", "Existing user skill.\n")
                elif reason == "override":
                    write(home / ".codex/AGENTS.override.md", "Existing override has priority.\n")
                before = snapshot(root)
                result = project_installation(home, self.bundle, SOURCE)
                self.assertEqual(snapshot(root), before)
                self.assertIs(result["ready_to_apply"], False)
                for profile in result["profiles"].values():
                    self.assertEqual(profile["outcome"], "blocked")
                    self.assertTrue(profile["reason"])
                    self.assertFalse(profile.get("changes"))


if __name__ == "__main__":
    unittest.main()
