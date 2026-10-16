import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from textual.widgets import Input

from ocode.model import ContainerRuntime, GlobalConfig, ProjectConfig
from ocode.tui import (
    BusyScreen, ConfirmScreen, ContainerRuntimeScreen, MessageScreen,
    OcodeApp, ProjectSettingsScreen,
)


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    def make_app(self, required=False):
        cfg = GlobalConfig.defaults()
        cfg.container_runtime = ContainerRuntime(kind="unset" if required else "docker")
        cfg.check_updates = False
        store = Mock()
        store.load_global.return_value = cfg
        project = ProjectConfig("demo", "Proyecto", "rama", ".")
        store.list_projects.return_value = [project]
        store.get_project.return_value = project
        return OcodeApp(store, Mock())

    async def test_keyboard_back_forward_preserve_form_and_main_screen(self):
        app = self.make_app()
        async with app.run_test() as pilot:
            main = app.screen
            settings = ProjectSettingsScreen("demo")
            app.push_screen(settings)
            await pilot.pause()
            field = settings.query_one("#workspace", Input)
            field.value = "edición sin guardar"
            field.focus()
            await pilot.press("alt+left")
            await pilot.pause()
            self.assertIs(app.screen, main)
            await pilot.press("alt+right")
            await pilot.pause()
            self.assertIs(app.screen, settings)
            self.assertEqual(settings.query_one("#workspace", Input).value, "edición sin guardar")
            app.store.save_project.assert_not_called()

    async def test_back_button_and_palette_share_navigation_history(self):
        app = self.make_app()
        async with app.run_test() as pilot:
            main = app.screen
            settings = ProjectSettingsScreen("demo")
            app.push_screen(settings)
            await pilot.pause()
            commands = {command.title: command for command in app.get_system_commands(settings)}
            self.assertIn("Atrás", commands)
            settings.back()
            await pilot.pause()
            self.assertIs(app.screen, main)
            commands = {command.title: command for command in app.get_system_commands(main)}
            commands["Adelante"].callback()
            await pilot.pause()
            self.assertIs(app.screen, settings)

    async def test_multiple_back_and_forward_steps(self):
        app = self.make_app()
        async with app.run_test() as pilot:
            main = app.screen
            first = ProjectSettingsScreen("demo")
            second = ProjectSettingsScreen("other")
            app.push_screen(first)
            await pilot.pause()
            app.push_screen(second)
            await pilot.pause()
            for expected in (first, main):
                await pilot.press("alt+left")
                await pilot.pause()
                self.assertIs(app.screen, expected)
            for expected in (first, second):
                await pilot.press("alt+right")
                await pilot.pause()
                self.assertIs(app.screen, expected)

    async def test_new_branch_discards_forward_history(self):
        app = self.make_app()
        async with app.run_test() as pilot:
            old = ProjectSettingsScreen("demo")
            app.push_screen(old)
            await pilot.pause()
            app.action_navigation_back()
            await pilot.pause()
            new = ProjectSettingsScreen("other")
            app.push_screen(new)
            await pilot.pause()
            await pilot.press("alt+right")
            await pilot.pause()
            self.assertIs(app.screen, new)
            self.assertFalse(app.can_navigate_forward())
            self.assertFalse(app.is_screen_installed(old))

    async def test_dialogs_do_not_navigate_or_clear_forward_history(self):
        app = self.make_app()
        async with app.run_test() as pilot:
            settings = ProjectSettingsScreen("demo")
            app.push_screen(settings)
            await pilot.pause()
            app.action_navigation_back()
            await pilot.pause()
            for modal in (ConfirmScreen("Confirmar"), BusyScreen(), MessageScreen("Aviso", "Mensaje")):
                app.push_screen(modal)
                await pilot.pause()
                await pilot.press("alt+left", "alt+right")
                await pilot.pause()
                self.assertIs(app.screen, modal)
                app.pop_screen()
                await pilot.pause()
            await pilot.press("ctrl+p")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            app.action_navigation_forward()
            await pilot.pause()
            self.assertIs(app.screen, settings)

    async def test_programmatic_exit_does_not_preserve_completed_form(self):
        app = self.make_app()
        async with app.run_test() as pilot:
            settings = ProjectSettingsScreen("demo")
            app.push_screen(settings)
            await pilot.pause()
            app.action_navigation_back()
            await pilot.pause()
            app.action_navigation_forward()
            await pilot.pause()
            app.pop_screen()
            await pilot.pause()
            self.assertFalse(app.can_navigate_forward())
            self.assertFalse(app.is_screen_installed(settings))

    async def test_root_and_required_setup_cannot_be_skipped(self):
        for required in (False, True):
            app = self.make_app(required=required)
            async with app.run_test() as pilot:
                original = app.screen
                if required:
                    self.assertIsInstance(original, ContainerRuntimeScreen)
                await pilot.press("alt+left", "alt+right")
                await pilot.pause()
                self.assertIs(app.screen, original)
                self.assertFalse(app.can_navigate_back())
                self.assertFalse(app.can_navigate_forward())
