from __future__ import annotations

import shutil
import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ocode import update
from ocode.model import ContainerRuntime, GlobalConfig
from ocode.paths import Paths
from ocode.update import Release, UpdateError

try:  # The TUI tests need Textual; the pure logic tests do not.
    from ocode.tui import MessageScreen, OcodeApp, UpdateScreen
except ImportError:  # pragma: no cover - depends on the environment
    MessageScreen = OcodeApp = UpdateScreen = None  # type: ignore[assignment]

TEXTUAL_AVAILABLE = UpdateScreen is not None and OcodeApp is not None


def build_archive(
    path: Path,
    *,
    top: str = "ocode-0.7.0",
    complete: bool = True,
    version: str = "0.7.0",
) -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr(f"{top}/install/install.cmd", "@echo off\r\n")
        bundle.writestr(f"{top}/install/install.py", "# installer\n")
        bundle.writestr(
            f"{top}/install/payload/pyproject.toml", '[project]\nname = "ocode"\n'
        )
        bundle.writestr(
            f"{top}/install/payload/src/ocode/__init__.py",
            f'__version__ = "{version}"\n',
        )
        if complete:
            bundle.writestr(f"{top}/install/payload/src/ocode/__main__.py", "")
        bundle.writestr(f"{top}/README.md", "# ocode\n")
    return path


class VersionTests(unittest.TestCase):
    def test_parse_version_ignores_prefix_and_pads(self):
        self.assertEqual(update.parse_version("v0.6.0"), (0, 6, 0))
        self.assertEqual(update.parse_version("0.7"), (0, 7, 0))
        self.assertEqual(update.parse_version("V1.2.3-rc1"), (1, 2, 3))
        self.assertIsNone(update.parse_version("sin-version"))

    def test_is_newer_compares_numeric_versions(self):
        self.assertTrue(update.is_newer("0.7.0", "0.6.0"))
        self.assertTrue(update.is_newer("0.6.1", "0.6.0"))
        self.assertFalse(update.is_newer("0.6.0", "0.6.0"))
        self.assertFalse(update.is_newer("0.5.9", "0.6.0"))
        self.assertFalse(update.is_newer("no-valida", "0.6.0"))

    def test_prerelease_detection(self):
        self.assertFalse(update.is_prerelease("v0.7.0"))
        self.assertFalse(update.is_prerelease("0.7.0"))
        self.assertTrue(update.is_prerelease("v0.7.0-rc1"))
        self.assertTrue(update.is_prerelease("0.7.0-beta"))
        self.assertTrue(update.is_prerelease("v0.7.0a1"))


class ReleaseLookupTests(unittest.TestCase):
    def test_check_for_update_returns_only_newer_releases(self):
        payload = {
            "tag_name": "v0.7.0",
            "name": "0.7.0",
            "body": "Novedades",
            "html_url": "https://example.test/release",
        }
        with patch("ocode.update._request_json", return_value=(200, payload)):
            release = update.check_for_update("0.6.0")
        self.assertIsNotNone(release)
        self.assertEqual(release.version, "0.7.0")
        self.assertEqual(release.notes, "Novedades")
        self.assertEqual(release.archive_url,
                         "https://github.com/andresaven/ocode/archive/refs/tags/v0.7.0.zip")

    def test_check_for_update_returns_none_when_up_to_date(self):
        with patch("ocode.update._request_json",
                   return_value=(200, {"tag_name": "v0.6.0"})):
            self.assertIsNone(update.check_for_update("0.6.0"))

    def test_missing_release_falls_back_to_highest_stable_tag(self):
        seen = []

        def request(url):
            seen.append(url)
            if url.endswith("/releases/latest"):
                return 404, None
            return 200, [
                {"name": "v0.5.0"},
                {"name": "v0.7.0-rc1"},
                {"name": "v0.7.0"},
                {"name": "no-es-version"},
            ]

        with patch("ocode.update._request_json", side_effect=request):
            release = update.latest_release()
        self.assertEqual(release.version, "0.7.0")
        self.assertEqual(release.tag, "v0.7.0")
        self.assertEqual(len(seen), 2)

    def test_network_failure_raises_update_error(self):
        with patch("ocode.update._request_json",
                   side_effect=UpdateError("sin conexión")):
            with self.assertRaises(UpdateError):
                update.check_for_update("0.6.0")


class StageUpdateTests(unittest.TestCase):
    def stage(self, home: Path, archive: Path) -> Path:
        release = Release(tag="v0.7.0", version="0.7.0")

        def fake_download(_release, destination):
            shutil.copy2(archive, destination)
            return destination

        with patch("ocode.update._download", side_effect=fake_download):
            return update.stage_update(release, home)

    def test_stage_replaces_install_and_keeps_user_data(self):
        with TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "install").mkdir()
            (home / "install" / "viejo.txt").write_text("antiguo", encoding="utf-8")
            (home / "config").mkdir()
            (home / "config" / "config.json").write_text("{}", encoding="utf-8")
            archive = build_archive(home / "descarga.zip")

            launcher = self.stage(home, archive)

            self.assertFalse((home / "install" / "viejo.txt").exists())
            self.assertTrue((home / "install" / "install.py").is_file())
            self.assertTrue(
                (home / "install" / "payload" / "src" / "ocode" / "__main__.py").is_file()
            )
            self.assertEqual(
                (home / "config" / "config.json").read_text(encoding="utf-8"), "{}"
            )
            self.assertTrue((home / "README.md").is_file())
            self.assertFalse((home / "install.ocode-backup").exists())
            # No update.cmd yet: the staged installer is the entry point.
            self.assertEqual(launcher, home / "install" / "install.cmd")

    def test_stage_prefers_host_update_launcher(self):
        with TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "update.cmd").write_text("@echo off", encoding="utf-8")
            archive = build_archive(home / "descarga.zip")
            self.assertEqual(self.stage(home, archive), home / "update.cmd")

    def test_incomplete_distribution_is_rejected(self):
        with TemporaryDirectory() as directory:
            home = Path(directory)
            archive = build_archive(home / "descarga.zip", complete=False)
            with self.assertRaises(UpdateError):
                self.stage(home, archive)
            self.assertFalse((home / "install").exists())

    def test_archive_version_mismatch_is_rejected_before_replacing_install(self):
        with TemporaryDirectory() as directory:
            home = Path(directory)
            install = home / "install"
            install.mkdir()
            marker = install / "keep.txt"
            marker.write_text("previous install", encoding="utf-8")
            archive = build_archive(home / "download.zip", version="0.6.9")

            with self.assertRaisesRegex(UpdateError, "incluye ocode 0.6.9"):
                self.stage(home, archive)

            self.assertEqual(marker.read_text(encoding="utf-8"), "previous install")

    def test_extract_rejects_path_traversal(self):
        with TemporaryDirectory() as directory:
            archive = Path(directory) / "malicioso.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../fuera.txt", "contenido")
            with self.assertRaises(UpdateError):
                update._extract(archive, Path(directory) / "destino")


@unittest.skipUnless(TEXTUAL_AVAILABLE, "textual no está instalado")
class UpdateTuiTests(unittest.IsolatedAsyncioTestCase):
    def make_app(self, home: Path, *, check_updates: bool = False) -> OcodeApp:
        cfg = GlobalConfig.defaults()
        cfg.container_runtime = ContainerRuntime(kind="docker")
        cfg.check_updates = check_updates
        store = Mock()
        store.load_global.return_value = cfg
        store.list_projects.return_value = []
        return OcodeApp(store, Paths(home))

    async def wait_for(self, pilot, predicate, attempts: int = 50) -> bool:
        for _ in range(attempts):
            if predicate():
                return True
            await pilot.pause()
        return predicate()

    async def test_auto_check_prompts_and_skip_is_persisted(self):
        release = Release(tag="v9.9.9", version="9.9.9", notes="Novedades")
        with TemporaryDirectory() as directory:
            app = self.make_app(Path(directory), check_updates=True)
            with patch("ocode.tui.check_for_update", return_value=release):
                async with app.run_test() as pilot:
                    self.assertTrue(await self.wait_for(
                        pilot, lambda: isinstance(app.screen, UpdateScreen)
                    ))
                    app.screen.choose_skip()
                    await pilot.pause()
            self.assertEqual(app.global_cfg.skipped_update_version, "9.9.9")
            app.store.save_global.assert_called_once_with(app.global_cfg)

    async def test_automatic_check_can_be_disabled(self):
        release = Release(tag="v9.9.9", version="9.9.9")
        with TemporaryDirectory() as directory:
            app = self.make_app(Path(directory), check_updates=False)
            with patch("ocode.tui.check_for_update", return_value=release):
                async with app.run_test() as pilot:
                    await pilot.pause()
                    self.assertNotIsInstance(app.screen, UpdateScreen)

    async def test_manual_check_reports_when_up_to_date(self):
        with TemporaryDirectory() as directory:
            app = self.make_app(Path(directory))
            with patch("ocode.tui.check_for_update", return_value=None):
                async with app.run_test() as pilot:
                    app.check_updates_command(True)
                    self.assertTrue(await self.wait_for(
                        pilot, lambda: isinstance(app.screen, MessageScreen)
                    ))
                    self.assertIn("última versión", app.screen.message)

    async def test_update_downloads_and_launches_installer(self):
        release = Release(tag="v9.9.9", version="9.9.9")
        with TemporaryDirectory() as directory:
            home = Path(directory)
            launcher = home / "install" / "install.cmd"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("@echo off\r\n", encoding="utf-8")
            app = self.make_app(home)
            with patch("ocode.tui.stage_update", return_value=launcher) as stage, \
                 patch("ocode.tui.os.startfile", create=True) as startfile, \
                 patch.object(app, "exit") as exit_app:
                async with app.run_test() as pilot:
                    app.perform_update(release)
                    self.assertTrue(await self.wait_for(pilot, lambda: exit_app.called))
            stage.assert_called_once_with(release, home)
            startfile.assert_called_once_with(launcher)
            exit_app.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
