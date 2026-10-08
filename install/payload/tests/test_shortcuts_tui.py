import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from textual.command import CommandPalette
from textual.widgets import Input, Static

from ocode.model import ContainerRuntime, GlobalConfig, ProjectConfig
from ocode.paths import Paths
from ocode.tui import ProjectStorageScreen, ConfirmScreen, MessageScreen, OcodeApp, ProjectSettingsScreen


class ShortcutTests(unittest.IsolatedAsyncioTestCase):
    def make_app(self, home):
        cfg = GlobalConfig.defaults()
        cfg.container_runtime = ContainerRuntime(kind="docker")
        store = Mock()
        store.load_global.return_value = cfg
        project = ProjectConfig("demo", "Proyecto", "rama", str(home / "workspace"))
        store.list_projects.return_value = [project]
        store.get_project.return_value = project
        return OcodeApp(store, Paths(home))

    async def test_palette_label_and_only_requested_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(Path(directory))
            async with app.run_test() as pilot:
                commands = list(app.get_system_commands(app.screen))
                self.assertEqual(len(commands), 8)
                self.assertEqual(commands[0].title, "Salir")
                self.assertNotIn("Theme", [command.title for command in commands])
                self.assertEqual(app.active_bindings["ctrl+p"].binding.description, "Atajos")
                self.assertFalse(app.active_bindings["ctrl+p"].binding.show)
                await pilot.press("ctrl+p")
                await pilot.pause()
                self.assertIsInstance(app.screen, CommandPalette)
                self.assertEqual(app.screen.query_one(Input).placeholder, "Buscar atajos…")

    async def test_configuration_files_open_in_host(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(Path(directory))
            paths = app.paths
            expected = [
                paths.global_agents, paths.opencode_config,
                paths.project_agents("demo"), paths.project_config("demo"),
                paths.project_sensitive_paths("demo"),
            ]
            for path in expected:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            with patch("ocode.tui.os.startfile", create=True) as startfile:
                async with app.run_test():
                    commands = list(app.get_system_commands(app.screen))
                    for command in commands[1:-2]:
                        command.callback()
                    self.assertEqual([call.args[0] for call in startfile.call_args_list], expected)
                    app.store.save_project.assert_not_called()

    async def test_shortcut_opens_storage_mapping_view(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(Path(directory))
            with patch("ocode.tui.storage_report", return_value="Correspondencias de montajes") as report, \
                 patch("ocode.tui.os.startfile", create=True) as startfile:
                async with app.run_test() as pilot:
                    commands = list(app.get_system_commands(app.screen))
                    commands[-2].callback()
                    await pilot.pause()
                    self.assertEqual(commands[-2].title, "Ver almacenamiento del proyecto")
                    self.assertIsInstance(app.screen, ProjectStorageScreen)
                    await app.screen.workers.wait_for_complete()
                    report.assert_called_once_with(
                        app.paths, app.global_cfg, app.store.get_project.return_value,
                    )
                    self.assertIn("Correspondencias", str(app.screen.query_one("#storage-report", Static).render()))
                    startfile.assert_not_called()

    async def test_project_settings_take_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(Path(directory))
            async with app.run_test() as pilot:
                app.push_screen(ProjectSettingsScreen("other"))
                await pilot.pause()
                self.assertEqual(app.shortcut_project(app.screen), "other")

    async def test_storage_view_refreshes_and_keeps_navigation_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(Path(directory))
            with patch("ocode.tui.storage_report", side_effect=["Inicial\n" * 100, "Actualizado"]) as report:
                async with app.run_test(size=(80, 24)) as pilot:
                    app.open_project_path("demo", "storage")
                    await pilot.pause()
                    screen = app.screen
                    await screen.workers.wait_for_complete()
                    self.assertLessEqual(screen.query_one("#storage-back").region.bottom, 23)
                    screen.reload_storage()
                    await pilot.pause()
                    await screen.workers.wait_for_complete()
                    self.assertEqual(report.call_count, 2)
                    self.assertIn("Actualizado", str(screen.query_one("#storage-report", Static).render()))
                    screen.back()
                    await pilot.pause()
                    self.assertNotIsInstance(app.screen, ProjectStorageScreen)

    async def test_no_selected_project_and_missing_file_report_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(Path(directory))
            app.store.list_projects.return_value = []
            with patch("ocode.tui.os.startfile", create=True) as startfile:
                async with app.run_test() as pilot:
                    commands = list(app.get_system_commands(app.screen))
                    commands[3].callback()
                    await pilot.pause()
                    self.assertIsInstance(app.screen, MessageScreen)
                    self.assertIn("Selecciona", app.screen.message)
                    app.pop_screen()
                    commands[1].callback()
                    await pilot.pause()
                    self.assertIn("No existe", app.screen.message)
                    startfile.assert_not_called()

    async def test_update_requires_confirmation_and_exits_after_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(Path(directory))
            launcher = app.paths.home / "update.cmd"
            launcher.touch()
            with patch("ocode.tui.os.startfile", create=True) as startfile:
                async with app.run_test() as pilot:
                    app.confirm_update()
                    await pilot.pause()
                    self.assertIsInstance(app.screen, ConfirmScreen)
                    app.screen.no()
                    await pilot.pause()
                    startfile.assert_not_called()
                    app.confirm_update()
                    await pilot.pause()
                    with patch.object(app, "exit") as exit_app:
                        app.screen.yes()
                        await pilot.pause()
                        startfile.assert_called_once_with(launcher)
                        exit_app.assert_called_once_with()
