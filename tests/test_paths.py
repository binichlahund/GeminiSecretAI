import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from geminisecretai.paths import AppPaths, PATHS


class PathsTests(unittest.TestCase):
    def test_new_install_uses_data_folder_without_creating_browser_profile(self):
        with tempfile.TemporaryDirectory(prefix="Gemini install ") as folder:
            paths = AppPaths(Path(folder))
            paths.prepare()
            self.assertEqual(paths.profile, paths.data / "browser")
            self.assertTrue(paths.logs.is_dir())
            self.assertFalse(paths.profile.exists())
            paths.prepare()

    def test_legacy_config_and_login_migration_is_lossless_and_repeatable(self):
        with tempfile.TemporaryDirectory(prefix="Gemini legacy ") as folder:
            root = Path(folder)
            old_config = root / "config.json"
            content = b'{"system_prompt":"My chemistry instructions", "hotkey":"ctrl+f9", "dot":false}'
            old_config.write_bytes(content)
            profile = root / "gemini-chrome"
            profile.mkdir()
            cookie_marker = profile / "do-not-move"
            cookie_marker.write_bytes(b"synthetic cookie placeholder")
            (root / "gemini_hotkey.log").write_text("old log", encoding="utf-8")
            (root / "captures").mkdir()
            (root / "captures" / "keep.txt").write_text("capture", encoding="utf-8")
            paths = AppPaths(root).prepare()
            self.assertEqual(paths.config.read_bytes(), content)
            self.assertEqual((paths.data / "legacy" / "config-original.json").read_bytes(), content)
            self.assertEqual(paths.profile, profile.resolve())
            self.assertEqual(cookie_marker.read_bytes(), b"synthetic cookie placeholder")
            self.assertEqual((paths.logs / "gemini_hotkey.log").read_text(), "old log")
            self.assertEqual((paths.data / "legacy" / "captures" / "keep.txt").read_text(), "capture")
            paths.prepare()
            self.assertEqual(paths.config.read_bytes(), content)
            self.assertTrue(cookie_marker.exists())

    def test_existing_new_settings_are_not_overwritten_by_old_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = AppPaths(root).prepare()
            paths.config.write_text('{"system_prompt":"new"}')
            (root / "config.json").write_text('{"system_prompt":"old"}')
            paths.prepare()
            self.assertEqual(json.loads(paths.config.read_text())["system_prompt"], "new")
            self.assertTrue((root / "config.json").exists())

    def test_invalid_legacy_settings_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "config.json").write_text("{broken")
            with self.assertRaises(ValueError):
                AppPaths(root).prepare()
            self.assertEqual((root / "config.json").read_text(), "{broken")
            self.assertFalse((root / "data" / "config.json").exists())

    def test_package_import_is_independent_of_working_directory(self):
        with tempfile.TemporaryDirectory(prefix="Gemini alternate cwd ") as folder:
            result = subprocess.run(
                [sys.executable, "-c", "from geminisecretai.paths import PATHS; from geminisecretai.browser import EXTRACT; print(PATHS.root); assert 'katex' in EXTRACT"],
                cwd=folder, capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(PATHS.root), result.stdout)


if __name__ == "__main__":
    unittest.main()
