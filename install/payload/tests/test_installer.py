from __future__ import annotations

import importlib.util
from unittest.mock import patch
import tempfile
import unittest
from pathlib import Path


INSTALLER_PATH = Path(__file__).resolve().parents[2] / "install.py"
spec = importlib.util.spec_from_file_location("ocode_installer", INSTALLER_PATH)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class UpdateLauncherTests(unittest.TestCase):
    def test_creates_launcher_in_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ocode-install-") as directory:
            root = Path(directory) / "ocode with spaces"
            root.mkdir()

            launcher = installer.create_update_launcher(root)

            self.assertEqual(launcher, root / "update.cmd")
            content = launcher.read_bytes()
            self.assertIn(b'if not exist "%~dp0install\\install.cmd"', content)
            self.assertIn(b'call "%~dp0install\\install.cmd" %*\r\n', content)
            self.assertTrue(content.endswith(b"exit /b %ERRORLEVEL%\r\n"))
            self.assertNotIn(b"\n", content.replace(b"\r\n", b""))

    def test_refreshes_launcher_without_changing_other_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ocode-install-") as directory:
            root = Path(directory)
            launcher = root / "update.cmd"
            launcher.write_text("old launcher", encoding="ascii")
            config = root / "config"
            config.mkdir()
            rules = config / "AGENTS.md"
            rules.write_text("custom rules", encoding="utf-8")

            installer.create_update_launcher(root)

            self.assertIn("call", launcher.read_text(encoding="ascii"))
            self.assertEqual(rules.read_text(encoding="utf-8"), "custom rules")


class ConfigUpdateTests(unittest.TestCase):
    def test_declining_update_preserves_custom_config(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ocode-config-") as directory:
            root = Path(directory)
            source = root / "default.jsonc"
            target = root / "config.jsonc"
            source.write_text('{"value": "new"}\n', encoding="utf-8")
            target.write_text('{"value": "custom"}\n', encoding="utf-8")

            with patch("builtins.input", return_value="n"):
                replaced = installer.offer_config_update(source, target)

            self.assertFalse(replaced)
            self.assertEqual(target.read_text(encoding="utf-8"), '{"value": "custom"}\n')
            self.assertEqual(list(root.glob("*.bak-*")), [])

    def test_confirmed_update_shows_diff_and_keeps_backup(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ocode-config-") as directory:
            root = Path(directory)
            source = root / "default.jsonc"
            target = root / "config.jsonc"
            source.write_text('{"value": "new"}\n', encoding="utf-8")
            target.write_text('{"value": "custom"}\n', encoding="utf-8")

            with patch("builtins.input", return_value="s"):
                replaced = installer.offer_config_update(source, target)

            self.assertTrue(replaced)
            self.assertEqual(target.read_text(encoding="utf-8"), '{"value": "new"}\n')
            backups = list(root.glob("config.jsonc.bak-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), '{"value": "custom"}\n')

    def test_unchanged_config_does_not_prompt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ocode-config-") as directory:
            root = Path(directory)
            source = root / "default.jsonc"
            target = root / "config.jsonc"
            source.write_text('{"value": "same"}\n', encoding="utf-8")
            target.write_text('{"value": "same"}\n', encoding="utf-8")

            with patch("builtins.input") as prompt:
                replaced = installer.offer_config_update(source, target)

            self.assertFalse(replaced)
            prompt.assert_not_called()


class AppLauncherTests(unittest.TestCase):
    def test_launcher_selects_its_installation_code(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ocode-install-") as directory:
            root = Path(directory) / "ocode with spaces"

            launcher = installer.create_app_launcher(root, r"C:\Python\python.exe")

            content = launcher.read_text(encoding="ascii")
            self.assertIn(f'set "OCODE_HOME={root}"', content)
            self.assertIn('set "PYTHONPATH=%~dp0..\\src;%PYTHONPATH%"', content)
            self.assertIn('"C:\\Python\\python.exe" -m ocode %*', content)


if __name__ == "__main__":
    unittest.main()
