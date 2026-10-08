from __future__ import annotations

import importlib.util
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


if __name__ == "__main__":
    unittest.main()
