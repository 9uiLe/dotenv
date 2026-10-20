"""共有Hookの更新と既存Codex設定との共存を確認する。"""

from datetime import timedelta
import json
import tomllib
import unittest

from prototype import test_system as system
from prototype.engine import Manager
from prototype.fixtures import make_bundle
from prototype.sandbox import create_sandbox
from prototype.test_system import MORNING, TOOLS, target_snapshot, write_text


class SharingHookTests(system.SharingSystemCase):
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


    def test_codex_trust_metadata_keeps_json_hooks_and_does_not_block_the_next_update(self):
        original = '# Keep the local model comment.\nmodel = "local-model"\n'
        trust_metadata = '\n[hooks.state]\n"example-hook" = { hash = "' + 'a' * 64 + '" }\n'
        local_group = {"hooks": [{"type": "command", "command": "echo local-json-hook"}]}
        for metadata_before_install in (True, False):
            with self.subTest(metadata_before_install=metadata_before_install):
                root = create_sandbox(prefix="system-hook-trust-")
                home, source = root / "personal", root / "source"
                config, hook_config = home / ".codex/config.toml", home / ".codex/hooks.json"
                write_text(config, original + (trust_metadata if metadata_before_install else ''))
                write_text(hook_config, json.dumps({"hooks": {"SessionStart": [local_group]}}))
                write_text(source / "bundle.json", json.dumps(make_bundle("v1")))
                manager = Manager(home, source, profile="personal")
                self.assertEqual(manager.sync(now=MORNING)["outcome"], "updated")
                self.assertIn(".codex/hooks.json", manager.status()["active"]["managed"])
                self.assertNotIn(".codex/config.toml", manager.status()["active"]["managed"])
                self.assertEqual(config.read_text(),
                                 original + (trust_metadata if metadata_before_install else ''))

                # Codex may persist trust after reviewing a newly installed JSON hook.
                if not metadata_before_install:
                    write_text(config, original + trust_metadata)
                write_text(source / "bundle.json", json.dumps(make_bundle("v2")))
                result = manager.sync(now=MORNING + timedelta(days=1))
                self.assertEqual(result["outcome"], "updated", result)
                self.assertEqual(manager.status()["active"]["bundle"]["version"], "v2")
                self.assertEqual(config.read_text(), original + trust_metadata)
                self.assertEqual(tomllib.loads(config.read_text())["hooks"]["state"],
                                 {"example-hook": {"hash": "a" * 64}})
                groups = json.loads(hook_config.read_text())["hooks"]["SessionStart"]
                self.assertIn(local_group, groups)
                self.assertEqual(len(groups), 2)
                self.assertIn(".codex/hooks.json", manager.status()["active"]["managed"])
                self.assertNotIn(".codex/config.toml", manager.status()["active"]["managed"])


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



if __name__ == "__main__":
    unittest.main()
