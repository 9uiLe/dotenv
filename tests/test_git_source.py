"""Git-backed receiving checks against retained, local-only mock repositories."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from ai_knowledge.engine import Manager
from tests.fixtures import make_bundle
from tests.git_fixtures import LocalRemote
from ai_knowledge.sandbox import create_sandbox


MORNING = datetime(2026, 10, 20, 10, 0, tzinfo=timezone(timedelta(hours=9)))


def target_snapshot(home):
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


class GitSourceTests(unittest.TestCase):
    def setUp(self):
        self.root = create_sandbox(prefix="git-source-test-")
        self.addCleanup(shutil.rmtree, self.root)
        self.remote = LocalRemote(self.root)
        self.first_commit = self.remote.publish("v1")
        self.config = self.remote.configure()
        self.source = self.root / "source"
        self.home = self.root / "work"
        self.manager = Manager(self.home, self.source, profile="work")

    def configure(self, **changes):
        config = dict(self.config, **changes)
        (self.source / "git.json").write_text(json.dumps(config), encoding="utf-8")
        return config

    def assert_active(self, version, commit):
        active = self.manager.status()["active"]
        self.assertEqual(active["bundle"]["version"], version)
        self.assertEqual(active["source"], {"kind": "git", **self.config, "commit": commit})

    def first_sync(self):
        self.assertEqual(self.manager.sync(now=MORNING)["outcome"], "updated")
        self.assert_active("v1", self.first_commit)
        self.assertEqual(self.manager.status()["last_received_source"]["commit"], self.first_commit)

    def assert_failed_without_advancing(self, now=MORNING, **kwargs):
        before = target_snapshot(self.home)
        previous = deepcopy(self.manager.status()["active"])
        received = deepcopy(self.manager.status().get("last_received_source"))
        result = self.manager.sync(now=now, force=True, **kwargs)
        self.assertEqual(result["outcome"], "failed", result)
        self.assertEqual(target_snapshot(self.home), before)
        self.assertEqual(self.manager.status()["active"], previous)
        self.assertEqual(self.manager.status().get("last_received_source"), received)
        return result

    def commit_manifest(self, content, message):
        path = self.remote.remote / "shared/manifest.json"
        path.write_text(content, encoding="utf-8")
        self.remote._git("add", "--", "shared/manifest.json")
        self.remote._git("commit", "-m", message)
        return self.remote._git("rev-parse", "HEAD")

    def test_initial_receive_fast_forward_and_unchanged_retain_the_accepted_commit(self):
        self.first_sync()
        for relative in (".codex/AGENTS.md", ".claude/CLAUDE.md"):
            self.assertIn("v1", (self.home / relative).read_text())
        cache = self.home / ".local/share/ai-knowledge/receive-cache.git"
        self.assertTrue(cache.is_dir())
        self.assertTrue((cache / "HEAD").is_file())
        self.assertFalse((cache / "shared").exists())
        before = target_snapshot(self.home)
        self.assertEqual(self.manager.sync(now=MORNING, force=True)["outcome"], "unchanged")
        self.assertEqual(target_snapshot(self.home), before)
        self.assert_active("v1", self.first_commit)
        second_commit = self.remote.publish("v2")
        self.assertEqual(self.manager.sync(now=MORNING + timedelta(days=1))["outcome"], "updated")
        self.assert_active("v2", second_commit)
        self.assertEqual(self.manager.status()["last_received_source"]["commit"], second_commit)
        for relative in (".codex/AGENTS.md", ".claude/CLAUDE.md"):
            self.assertIn("v2", (self.home / relative).read_text())

    def test_clock_same_day_and_pause_skip_receiving_even_when_remote_is_unavailable(self):
        unavailable = self.root / "offline-remote.git"
        self.remote.remote.rename(unavailable)
        self.assertEqual(self.manager.sync(now=MORNING - timedelta(minutes=1))["outcome"], "not_due")
        self.assertIsNone(self.manager.status()["active"])
        cache = self.home / ".local/share/ai-knowledge/receive-cache.git"
        self.assertFalse(cache.exists())
        unavailable.rename(self.remote.remote)
        self.first_sync()
        self.remote.publish("v2")
        self.remote.remote.rename(unavailable)
        before = target_snapshot(self.home)
        self.assertEqual(self.manager.sync(now=MORNING + timedelta(hours=1))["outcome"], "already_today")
        self.assertEqual(target_snapshot(self.home), before)
        self.manager.pause_sync()
        before = target_snapshot(self.home)
        for force in (False, True):
            self.assertEqual(self.manager.sync(now=MORNING + timedelta(days=1), force=force)["outcome"],
                             "paused")
            self.assertEqual(target_snapshot(self.home), before)
        self.assert_active("v1", self.first_commit)
        unavailable.rename(self.remote.remote)

    def test_fetch_failure_keeps_the_previous_version_and_commit_then_allows_retry(self):
        self.first_sync()
        unavailable = self.root / "offline-remote.git"
        self.remote.remote.rename(unavailable)
        self.assert_failed_without_advancing(now=MORNING + timedelta(days=1))
        self.assert_active("v1", self.first_commit)
        unavailable.rename(self.remote.remote)
        second_commit = self.remote.publish("v2")
        result = self.manager.sync(now=MORNING + timedelta(days=1, minutes=1))
        self.assertEqual(result["outcome"], "updated")
        self.assert_active("v2", second_commit)

    def test_remote_rewind_and_divergent_history_are_rejected(self):
        self.first_sync()
        second_commit = self.remote.publish("v2")
        self.manager.sync(now=MORNING, force=True)
        self.assert_active("v2", second_commit)
        self.remote.reset(self.first_commit)
        self.assert_failed_without_advancing()
        self.assert_active("v2", second_commit)
        result = self.manager.restore(previous=True, apply=True)
        self.assertNotEqual(result["outcome"], "failed", result)
        self.assert_active("v1", self.first_commit)
        self.assertEqual(self.manager.status()["last_received_source"]["commit"], second_commit)
        self.remote.reset(self.first_commit)
        self.manager.resume_sync()
        self.assert_failed_without_advancing()
        self.assert_active("v1", self.first_commit)
        divergent = self.remote.publish("v3")
        self.assertNotEqual(divergent, second_commit)
        self.assert_failed_without_advancing()
        self.assert_active("v1", self.first_commit)

    def test_invalid_manifests_outside_paths_and_symlink_blobs_are_rejected(self):
        self.first_sync()
        manifest_path = self.remote.remote / "shared/manifest.json"
        original = manifest_path.read_text()
        self.commit_manifest("{ invalid json", "Invalid manifest syntax")
        self.assert_failed_without_advancing()
        manifest = json.loads(original)
        manifest["rules"] = "../outside-rules.md"
        self.commit_manifest(json.dumps(manifest), "Reject an outside rules path")
        self.assert_failed_without_advancing()
        self.commit_manifest(original, "Restore valid manifest")
        link_contents = self.root / "link-blob-contents.txt"
        link_contents.write_text("rules.md", encoding="utf-8")
        blob = self.remote._git("hash-object", "-w", str(link_contents))
        self.remote._git("update-index", "--add", "--cacheinfo", f"120000,{blob},shared/rules.md")
        self.remote._git("commit", "-m", "A Git symlink without a filesystem symlink")
        self.assertFalse((self.remote.remote / "shared/rules.md").is_symlink())
        self.assert_failed_without_advancing()
        self.assert_active("v1", self.first_commit)

    def test_invalid_bundle_does_not_become_the_accepted_source(self):
        self.first_sync()
        invalid = make_bundle("invalid")
        invalid["files"]["hooks/session_start.py"] = "def invalid(:\n"
        self.remote.publish(bundle=invalid)
        self.assert_failed_without_advancing()
        self.assert_active("v1", self.first_commit)
        second_commit = self.remote.publish("v2")
        self.assertEqual(self.manager.sync(now=MORNING, force=True)["outcome"], "updated")
        self.assert_active("v2", second_commit)

    def test_preview_receives_before_clock_and_while_paused_without_applying_or_accepting(self):
        before = target_snapshot(self.home)
        initial_status = self.manager.status()
        result = self.manager.preview_sync(now=MORNING - timedelta(minutes=1))
        self.assertEqual(result["outcome"], "preview", result)
        self.assertEqual(result["source"]["commit"], self.first_commit)
        self.assertEqual(result["version"], "v1")
        self.assertTrue(result["changes"])
        self.assertEqual(target_snapshot(self.home), before)
        self.assertEqual(self.manager.status(), initial_status)
        self.first_sync()
        self.manager.pause_sync()
        self.manager.pause_hooks()
        second_commit = self.remote.publish("v2")
        before = target_snapshot(self.home)
        state_path = self.home / ".local/share/ai-knowledge/state.json"
        state_before = state_path.read_bytes()
        result = self.manager.preview_sync(now=MORNING + timedelta(minutes=1))
        self.assertEqual(result["outcome"], "preview", result)
        self.assertEqual(result["source"]["commit"], second_commit)
        self.assertEqual(result["version"], "v2")
        self.assertTrue(result["changes"])
        self.assertEqual(target_snapshot(self.home), before)
        self.assertEqual(state_path.read_bytes(), state_before)
        self.assert_active("v1", self.first_commit)

    def test_distribution_conflict_and_failed_write_do_not_advance_the_git_commit(self):
        self.first_sync()
        skill = self.home / ".claude/skills/ai-knowledge-example/SKILL.md"
        original = skill.read_text()
        skill.write_text(original + "local edit\n", encoding="utf-8")
        second_commit = self.remote.publish("v2")
        self.assert_failed_without_advancing()
        self.assert_active("v1", self.first_commit)
        skill.write_text(original, encoding="utf-8")
        self.assert_failed_without_advancing(fail_after=1)
        self.assert_active("v1", self.first_commit)
        self.assertEqual(self.manager.sync(now=MORNING, force=True)["outcome"], "updated")
        self.assert_active("v2", second_commit)

    def test_changing_the_branch_identity_requires_a_separate_decision(self):
        self.first_sync()
        self.remote._git("branch", "other-source", self.first_commit)
        self.configure(branch="other-source")
        self.assert_failed_without_advancing()
        self.assert_active("v1", self.first_commit)
        self.configure()
        self.assertEqual(self.manager.sync(now=MORNING, force=True)["outcome"], "unchanged")

    def test_unsafe_urls_are_rejected_before_any_git_or_network_command(self):
        for url in ("https://example.invalid/team/knowledge.git",
                    "ssh://git@example.invalid/team/knowledge.git",
                    "file:///tmp/unapproved-repository.git", "--upload-pack=unexpected-command"):
            with self.subTest(url=url):
                self.configure(url=url)
                before = target_snapshot(self.home)
                with patch("ai_knowledge.git_source.subprocess.run") as command:
                    command.side_effect = AssertionError("Unsafe URL reached a subprocess")
                    result = self.manager.sync(now=MORNING)
                    command.assert_not_called()
                self.assertEqual(result["outcome"], "failed", result)
                self.assertEqual(target_snapshot(self.home), before)
                self.assertIsNone(self.manager.status()["active"])


if __name__ == "__main__":
    unittest.main()
