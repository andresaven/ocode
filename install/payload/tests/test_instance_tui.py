import subprocess
from contextlib import ExitStack
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ocode import docker
from ocode.model import ContainerRuntime, GlobalConfig, ProjectConfig
from ocode.tui import (
    ConfirmScreen,
    MessageScreen,
    OcodeApp,
    ProjectScreen,
    ProjectSettingsScreen,
    SensitivePathsScreen,
)
from textual.widgets import Button, Input, Label, ListView, Static
from textual.coordinate import Coordinate


class ContainerStatusTests(unittest.TestCase):
    def setUp(self):
        self.cfg = GlobalConfig.defaults()
        self.cfg.container_runtime = ContainerRuntime(kind="docker")

    def test_state_query_is_read_only(self):
        with patch.object(docker.subprocess, "run", return_value=
                          subprocess.CompletedProcess([], 0, "running\n")) as run:
            self.assertEqual(docker.container_status(self.cfg, "demo"), "running")
        self.assertEqual(run.call_args.args[0], [
            "docker", "ps", "-a", "--filter", "name=^demo$", "--format", "{{.State}}",
        ])
        self.assertTrue(run.call_args.kwargs["check"])
        self.assertEqual(run.call_args.kwargs["timeout"], 10)

    def test_absent_container(self):
        with patch.object(docker.subprocess, "run", return_value=
                          subprocess.CompletedProcess([], 0, "")):
            self.assertEqual(docker.container_status(self.cfg, "demo"), "absent")

    def test_engine_errors_are_not_absence(self):
        for error in (FileNotFoundError(), subprocess.CalledProcessError(1, []),
                      subprocess.TimeoutExpired([], 10)):
            with self.subTest(error=type(error).__name__):
                with patch.object(docker.subprocess, "run", side_effect=error):
                    with self.assertRaises(docker.DockerError):
                        docker.container_status(self.cfg, "demo")

    def test_published_ports_reads_only_bindings(self):
        with patch.object(docker, "run_capture", return_value=Mock(stdout=
                          '{"4096/tcp": [{"HostPort": "4321"}]}')) as run:
            self.assertEqual(docker.container_published_ports(self.cfg, "demo"), {4321})
        self.assertEqual(run.call_args.args[1], [
            "inspect", "demo", "--format", "{{json .HostConfig.PortBindings}}",
        ])

    def test_published_ports_handles_legacy_and_invalid_response(self):
        with patch.object(docker, "run_capture", return_value=Mock(stdout="null")):
            self.assertEqual(docker.container_published_ports(self.cfg, "demo"), set())
        with patch.object(docker, "run_capture", return_value=Mock(stdout="invalid")):
            with self.assertRaises(docker.DockerError):
                docker.container_published_ports(self.cfg, "demo")


class InstanceScreenTests(unittest.IsolatedAsyncioTestCase):
    def make_app(self):
        cfg = GlobalConfig.defaults()
        cfg.container_runtime = ContainerRuntime(kind="docker")
        project = ProjectConfig("demo", "Proyecto", "rama", ".")
        store = Mock()
        store.load_global.return_value = cfg
        store.list_projects.return_value = [project]
        store.get_project.return_value = project
        return OcodeApp(store, Mock())

    async def test_main_screen_refreshes_status_by_cell_coordinate(self):
        app = self.make_app()
        with patch("ocode.tui.container_status", return_value="exited"):
            async with app.run_test() as pilot:
                await pilot.pause()
                table = app.screen.query_one("#projects")
                self.assertEqual(
                    table.get_cell_at(Coordinate(0, 3)),
                    "Detenida",
                )

    def mock_launch(self, app, *, reused=False):
        stack = ExitStack()
        self.addCleanup(stack.close)
        other = ProjectConfig("other", "Otro proyecto", "rama", ".", port=9999)
        app.store.list_projects.return_value = [app.store.get_project.return_value, other]
        def status(cfg, name):
            if name == other.container_name:
                return "running"
            return "running" if reused else "absent"

        mocks = {}
        for name, kwargs in {
            "container_status": {"side_effect": status},
            "container_exists": {"return_value": reused},
            "image_exists": {"return_value": True},
            "build_launch_command": {"return_value": docker.CommandPreview([] if reused else ["mock"])},
            "container_published_ports": {"return_value": {4096}},
            "stop_container": {},
            "remove_container": {},
            "remove_state": {},
            "launch_detached": {"return_value": "new-container"},
            "container_running": {"return_value": True},
            "prepare_web_login": {},
            "sensitive_applied_paths": {"return_value": []},
            "webbrowser.open": {},
        }.items():
            mocks[name] = stack.enter_context(patch(f"ocode.tui.{name}", **kwargs))
        return mocks, other

    async def test_port_conflict_is_non_destructive_for_both_interfaces(self):
        for mode in ("tui", "serve"):
            for status in ("running", "restarting", "paused"):
                with self.subTest(mode=mode, status=status):
                    app = self.make_app()
                    mocks, other = self.mock_launch(app)
                    mocks["container_status"].return_value = status
                    mocks["container_status"].side_effect = None
                    with patch.object(app, "exit") as exit_app:
                        async with app.run_test(size=(100, 40)) as pilot:
                            app.open_project("demo")
                            await pilot.pause()
                            await app.screen.launch(mode)
                            await pilot.pause()
                            self.assertIsInstance(app.screen, MessageScreen)
                            self.assertEqual(app.screen.title_text, "Error")
                            self.assertIn(other.name, app.screen.message)
                            self.assertIn("puerto distinto", app.screen.message)
                            mocks["stop_container"].assert_not_called()
                            mocks["remove_container"].assert_not_called()
                            mocks["remove_state"].assert_not_called()
                            mocks["launch_detached"].assert_not_called()
                            mocks["webbrowser.open"].assert_not_called()
                            app.store.save_project.assert_not_called()
                            app.store.delete_project_config.assert_not_called()
                            exit_app.assert_not_called()

    async def test_distinct_ports_allow_all_interface_combinations(self):
        for mode in ("tui", "serve"):
            for other_mode in ("tui", "serve"):
                with self.subTest(mode=mode, other_mode=other_mode):
                    app = self.make_app()
                    mocks, other = self.mock_launch(app)
                    other.default_mode = other_mode
                    third = ProjectConfig("third", "Tercer proyecto", "rama", ".", port=4322)
                    app.store.list_projects.return_value.append(third)
                    mocks["container_status"].side_effect = None
                    mocks["container_status"].return_value = "running"
                    mocks["container_published_ports"].side_effect = lambda cfg, name: (
                        {4321} if name == other.container_name else {4322}
                    )
                    with patch.object(app, "exit") as exit_app:
                        async with app.run_test(size=(100, 40)) as pilot:
                            app.open_project("demo")
                            await pilot.pause()
                            await app.screen.launch(mode)
                            await pilot.pause()
                            mocks["stop_container"].assert_not_called()
                            mocks["remove_container"].assert_not_called()
                            mocks["remove_state"].assert_not_called()
                            if mode == "serve":
                                mocks["launch_detached"].assert_called_once()
                                mocks["webbrowser.open"].assert_called_once()
                                exit_app.assert_not_called()
                            else:
                                request = exit_app.call_args.args[0]
                                self.assertEqual(request.project_id, "demo")
                                self.assertEqual(request.mode, "tui")
                                mocks["launch_detached"].assert_not_called()

    async def test_stopped_container_uses_actual_ports_for_collision(self):
        for mode in ("tui", "serve"):
            with self.subTest(mode=mode):
                app = self.make_app()
                mocks, _ = self.mock_launch(app, reused=True)
                app.store.get_project.return_value.port = 4321
                mocks["build_launch_command"].return_value = docker.CommandPreview(["mock"], reused=True)
                async with app.run_test(size=(100, 40)) as pilot:
                    app.open_project("demo")
                    await pilot.pause()
                    await app.screen.launch(mode)
                    await pilot.pause()
                    mocks["stop_container"].assert_not_called()
                    mocks["launch_detached"].assert_not_called()
                    mocks["webbrowser.open"].assert_not_called()
                    self.assertEqual(app.screen.title_text, "Error")

    async def test_same_instance_reuses_web_without_stopping_others(self):
        app = self.make_app()
        mocks, _ = self.mock_launch(app, reused=True)
        mocks["container_published_ports"].return_value = {4321}
        async with app.run_test(size=(100, 40)) as pilot:
            app.open_project("demo")
            await pilot.pause()
            await app.screen.launch("serve")
            await pilot.pause()
            mocks["stop_container"].assert_not_called()
            mocks["launch_detached"].assert_not_called()
            mocks["webbrowser.open"].assert_called_once_with("http://127.0.0.1:4321/", new=2)

    async def test_same_instance_reuses_tui_without_stopping_others(self):
        app = self.make_app()
        mocks, _ = self.mock_launch(app, reused=True)
        with patch.object(app, "exit") as exit_app:
            async with app.run_test(size=(100, 40)) as pilot:
                app.open_project("demo")
                await pilot.pause()
                await app.screen.launch("tui")
                await pilot.pause()
                mocks["stop_container"].assert_not_called()
                mocks["launch_detached"].assert_not_called()
                self.assertEqual(exit_app.call_args.args[0].project_id, "demo")
                self.assertEqual(exit_app.call_args.args[0].mode, "tui")

    async def test_engine_port_collision_does_not_modify_other_instances(self):
        app = self.make_app()
        mocks, _ = self.mock_launch(app)
        mocks["container_published_ports"].return_value = {4321}
        mocks["launch_detached"].side_effect = docker.DockerError("Port already allocated")
        async with app.run_test(size=(100, 40)) as pilot:
            app.open_project("demo")
            await pilot.pause()
            await app.screen.launch("serve")
            await pilot.pause()
            self.assertEqual(app.screen.title_text, "Error")
            mocks["stop_container"].assert_not_called()
            mocks["remove_container"].assert_not_called()
            mocks["remove_state"].assert_not_called()
            mocks["webbrowser.open"].assert_not_called()

    async def test_delete_only_removes_selected_instance(self):
        app = self.make_app()
        mocks, _ = self.mock_launch(app, reused=True)
        project = app.store.get_project.return_value
        mocks["container_running"].return_value = False
        mocks["remove_state"].return_value = "Volúmenes eliminados"
        async with app.run_test(size=(100, 40)) as pilot:
            app.open_project("demo")
            await pilot.pause()
            await app.screen._delete_instance_async(project)
            await pilot.pause()
            mocks["container_running"].assert_called_once_with(app.global_cfg, project.container_name)
            mocks["remove_container"].assert_called_once_with(app.global_cfg, project.container_name)
            mocks["remove_state"].assert_called_once_with(app.global_cfg, project)
            app.store.delete_project_config.assert_called_once_with(project.project_id)
            mocks["stop_container"].assert_not_called()
            self.assertEqual(app.screen.title_text, "Instancia eliminada")

    async def test_different_port_does_not_stop_other_instance(self):
        app = self.make_app()
        mocks, _ = self.mock_launch(app)
        mocks["container_published_ports"].return_value = {4321}
        async with app.run_test(size=(100, 40)) as pilot:
            app.open_project("demo")
            await pilot.pause()
            await app.screen.launch("serve")
            await pilot.pause()
            mocks["stop_container"].assert_not_called()
            mocks["launch_detached"].assert_called_once()

    async def test_fast_web_checks_start_only_after_busy_screen_mounts(self):
        app = self.make_app()
        mocks, _ = self.mock_launch(app)
        mocks["container_published_ports"].return_value = {4321}
        mounted = []

        def exists(*args):
            mounted.append(app.screen.is_mounted)
            app.screen.query_one(Static)
            return False

        mocks["container_exists"].side_effect = exists
        async with app.run_test(size=(100, 40)) as pilot:
            app.open_project("demo")
            await pilot.pause()
            await app.screen.launch("serve")
            await pilot.pause()
            self.assertEqual(mounted, [True])
            mocks["prepare_web_login"].assert_called_once()
            mocks["webbrowser.open"].assert_called_once()

    async def test_invalid_target_does_not_stop_active_instance(self):
        app = self.make_app()
        mocks, other = self.mock_launch(app)
        mocks["build_launch_command"].side_effect = docker.DockerError("Invalid configuration")
        async with app.run_test(size=(100, 40)) as pilot:
            app.open_project("demo")
            await pilot.pause()
            await app.screen.launch("serve")
            await pilot.pause()
            self.assertEqual(app.screen.title_text, "Error")
            mocks["stop_container"].assert_not_called()
            mocks["launch_detached"].assert_not_called()

    async def test_legacy_web_error_does_not_stop_port_owner(self):
        app = self.make_app()
        mocks, other = self.mock_launch(app)
        mocks["build_launch_command"].side_effect = docker.DockerError(
            "Este contenedor Web antiguo necesita Guardar y recrear contenedor."
        )
        async with app.run_test(size=(100, 40)) as pilot:
            app.open_project("demo")
            await pilot.pause()
            await app.screen.launch("serve")
            await pilot.pause()
            self.assertIsInstance(app.screen, MessageScreen)
            self.assertIn("Guardar y recrear contenedor", app.screen.message)
            mocks["stop_container"].assert_not_called()
            mocks["launch_detached"].assert_not_called()
            mocks["webbrowser.open"].assert_not_called()

    async def test_missing_backend_setup_reports_explicit_recreation(self):
        app = self.make_app()
        mocks, _ = self.mock_launch(app)
        mocks["container_running"].return_value = False
        mocks["container_published_ports"].return_value = {4321}
        with patch("ocode.tui.container_exit_report", return_value=
                   "[ocode-proxy] missing OPENCODE_SERVER_PASSWORD"):
            async with app.run_test(size=(100, 40)) as pilot:
                app.open_project("demo")
                await pilot.pause()
                await app.screen.launch("serve")
                await pilot.pause()
                self.assertIsInstance(app.screen, MessageScreen)
                self.assertIn("Guardar y recrear", app.screen.message)
                mocks["prepare_web_login"].assert_not_called()
                mocks["webbrowser.open"].assert_not_called()

    async def test_labels_sections_and_status_refresh(self):
        app = self.make_app()
        with patch("ocode.tui.container_status", return_value="running") as status:
            async with app.run_test(size=(100, 40)) as pilot:
                self.assertEqual(str(app.screen.query_one("#doctor", Button).label), "Diagnóstico")
                self.assertEqual(str(app.screen.query_one("#open", Button).label), "Gestionar instancia")
                app.open_project("demo")
                await pilot.pause()
                screen = app.screen
                self.assertIsInstance(screen, ProjectScreen)
                await screen.workers.wait_for_complete()
                self.assertIn("En ejecución", str(screen.query_one("#instance-status", Static).render()))
                self.assertEqual(len(screen.query(".action-group")), 4)
                self.assertEqual(str(screen.query_one("#tui", Button).label), "Abrir TUI")
                self.assertEqual(len(screen.query("#sensitive-report")), 0)
                self.assertEqual(len(screen.query("#open-config")), 0)
                self.assertEqual(len(screen.query("#recreate-container")), 0)
                self.assertIn("AGENTS.md", str(screen.query_one("#configuration-help", Static).render()))
                status.return_value = "exited"
                app.push_screen(MessageScreen("Prueba", "Cerrar para refrescar"))
                await pilot.pause()
                app.pop_screen()
                await pilot.pause()
                await screen.workers.wait_for_complete()
                self.assertIn("Detenida", str(screen.query_one("#instance-status", Static).render()))

    async def test_settings_names_files_and_opens_configuration(self):
        app = self.make_app()
        with patch("ocode.tui.os.startfile", create=True) as open_directory:
            async with app.run_test(size=(100, 40)) as pilot:
                app.push_screen(ProjectSettingsScreen("demo"))
                await pilot.pause()
                labels = [str(label.render()) for label in app.screen.query(Label)]
                self.assertIn("Cargar AGENTS.md específico (fuera del workspace)", labels)
                self.assertIn("Cargar opencode.jsonc específico (fuera del workspace)", labels)
                await pilot.click("#open-config")
                open_directory.assert_called_once_with(app.paths.project_dir.return_value)
                app.paths.project_dir.assert_called_once_with("demo")
                app.store.save_project.assert_not_called()
                self.assertIsInstance(app.screen, ProjectSettingsScreen)

    async def test_settings_reports_configuration_open_error(self):
        app = self.make_app()
        with patch("ocode.tui.os.startfile", create=True, side_effect=OSError("No se puede abrir")):
            async with app.run_test(size=(100, 40)) as pilot:
                app.push_screen(ProjectSettingsScreen("demo"))
                await pilot.pause()
                await pilot.click("#open-config")
                self.assertIsInstance(app.screen, MessageScreen)
                self.assertEqual(app.screen.title_text, "Error")

    async def test_settings_navigation_stays_at_bottom_on_small_terminal(self):
        app = self.make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(ProjectSettingsScreen("demo"))
            await pilot.pause()
            screen = app.screen
            back = screen.query_one("#back", Button)
            save = screen.query_one("#save", Button)
            self.assertEqual(back.region.height, 3)
            self.assertLess(back.region.bottom, 24)
            self.assertLess(back.region.x, save.region.x)
            self.assertEqual(back.region.y, save.region.y)
            self.assertIs(back.parent, screen.query_one("#settings-navigation"))

    async def test_settings_confirmation_saves_edited_form(self):
        app = self.make_app()
        with patch("ocode.tui.recreate_container") as recreate:
            async with app.run_test(size=(100, 40)) as pilot:
                app.push_screen(ProjectSettingsScreen("demo"))
                await pilot.pause()
                app.screen.query_one("#port", Input).value = "4321"
                await pilot.click("#save")
                await pilot.pause()
                self.assertIsInstance(app.screen, ConfirmScreen)
                app.store.save_project.assert_not_called()
                self.assertNotEqual(app.store.get_project.return_value.port, 4321)
                await pilot.click("#yes")
                await pilot.pause()
                saved = app.store.save_project.call_args.args[0]
                self.assertEqual(saved.port, 4321)
                recreate.assert_called_once_with(
                    paths=app.paths, global_cfg=app.global_cfg, project=saved,
                )
                self.assertIsInstance(app.screen, MessageScreen)

    async def test_protection_confirmation_cancel_and_save(self):
        app = self.make_app()
        app.store.get_project.return_value.sensitive_paths = []
        with patch("ocode.tui.sensitive_mount_report", return_value="Informe"), \
             patch("ocode.tui.recreate_container") as recreate:
            async with app.run_test(size=(100, 40)) as pilot:
                app.push_screen(SensitivePathsScreen("demo"))
                await pilot.pause()
                screen = app.screen
                screen._add_sensitive_value("src/foo")
                await pilot.pause()
                await screen.workers.wait_for_complete()
                await pilot.click("#save")
                await pilot.pause()
                self.assertIsInstance(app.screen, ConfirmScreen)
                await pilot.click("#no")
                await pilot.pause()
                app.store.save_project.assert_not_called()
                recreate.assert_not_called()
                self.assertEqual(screen.draft.sensitive_paths, ["**/src/foo"])
                self.assertEqual(app.store.get_project.return_value.sensitive_paths, [])
                await pilot.click("#save")
                await pilot.pause()
                await pilot.click("#yes")
                await pilot.pause()
                saved = app.store.save_project.call_args.args[0]
                self.assertEqual(saved.sensitive_paths, ["**/src/foo"])
                app.store.save_project.assert_called_once_with(saved, save_sensitive_paths=True)
                recreate.assert_called_once_with(
                    paths=app.paths, global_cfg=app.global_cfg, project=saved,
                )

    async def test_protection_report_is_integrated_and_updates_with_rules(self):
        app = self.make_app()
        app.store.get_project.return_value.sensitive_paths = []
        with patch("ocode.tui.sensitive_mount_report", side_effect=[
            "Sin coincidencias", "FICHERO [foo]", "Sin coincidencias",
        ]) as report:
            async with app.run_test(size=(80, 24)) as pilot:
                app.push_screen(SensitivePathsScreen("demo"))
                await pilot.pause()
                await app.screen.workers.wait_for_complete()
                self.assertIn("Sin coincidencias", str(app.screen.query_one("#protection-report", Static).render()))
                back = app.screen.query_one("#back", Button)
                self.assertEqual(back.region.height, 3)
                self.assertLess(back.region.bottom, 24)
                app.screen._add_sensitive_value("src/foo")
                await pilot.pause()
                await app.screen.workers.wait_for_complete()
                self.assertIn("FICHERO [foo]", str(app.screen.query_one("#protection-report", Static).render()))
                app.screen.query_one("#pathlist", ListView).index = 0
                app.screen.remove_path()
                await pilot.pause()
                await app.screen.workers.wait_for_complete()
                self.assertIn("Sin coincidencias", str(app.screen.query_one("#protection-report", Static).render()))
                self.assertEqual(report.call_count, 3)
                app.store.save_project.assert_not_called()
                self.assertEqual(app.store.get_project.return_value.sensitive_paths, [])

    async def test_protection_report_error_keeps_editor_available(self):
        app = self.make_app()
        with patch("ocode.tui.sensitive_mount_report", side_effect=docker.DockerError("Motor no disponible")):
            async with app.run_test(size=(80, 24)) as pilot:
                app.push_screen(SensitivePathsScreen("demo"))
                await pilot.pause()
                await app.screen.workers.wait_for_complete()
                self.assertIsInstance(app.screen, SensitivePathsScreen)
                self.assertIn("Motor no disponible", str(app.screen.query_one("#protection-report", Static).render()))
                self.assertFalse(app.screen.query_one("#add", Button).disabled)
                await pilot.click("#back")
                self.assertNotIsInstance(app.screen, SensitivePathsScreen)

    async def test_unavailable_state_and_small_terminal(self):
        app = self.make_app()
        with patch("ocode.tui.container_status", side_effect=docker.DockerError("Motor no disponible")):
            async with app.run_test(size=(80, 24)) as pilot:
                app.open_project("demo")
                await pilot.pause()
                await app.screen.workers.wait_for_complete()
                self.assertIn("no disponible", str(app.screen.query_one("#instance-status", Static).render()))
                back = app.screen.query_one("#back", Button)
                self.assertLess(back.region.bottom, 24)
                await pilot.click("#back")
                self.assertNotIsInstance(app.screen, ProjectScreen)

    async def test_toolbar_keeps_full_height_buttons(self):
        app = self.make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            toolbar = app.screen.query_one("#toolbar")
            self.assertEqual(toolbar.region.height, 6)
            for button in toolbar.query(Button):
                self.assertEqual(button.region.height, 3)
                self.assertLessEqual(button.region.right, 80)
                self.assertLessEqual(button.region.bottom, 23)

    async def test_cancel_recreation_does_not_change_instance(self):
        app = self.make_app()
        with patch("ocode.tui.container_status", return_value="running"), \
             patch("ocode.tui.recreate_container") as recreate:
            async with app.run_test(size=(100, 40)) as pilot:
                app.push_screen(ProjectSettingsScreen("demo"))
                await pilot.pause()
                screen = app.screen
                screen.save()
                await pilot.pause()
                self.assertIsInstance(app.screen, ConfirmScreen)
                self.assertIn("Se conservarán", app.screen.text)
                self.assertIn("Se perderán", app.screen.text)
                self.assertEqual(str(app.screen.query_one("#yes", Button).label), "Guardar y recrear contenedor")
                app.screen.no()
                await pilot.pause()
                recreate.assert_not_called()
                app.store.save_project.assert_not_called()
                app.store.delete_project_config.assert_not_called()

    async def test_recreation_preserves_configuration_and_reports_result(self):
        for error in (None, docker.DockerError("Creation failed; volumes retained")):
            with self.subTest(error=error):
                app = self.make_app()
                with patch("ocode.tui.container_status", return_value="running"), \
                     patch("ocode.tui.recreate_container", side_effect=error) as recreate:
                    async with app.run_test(size=(100, 40)) as pilot:
                        app.push_screen(ProjectSettingsScreen("demo"))
                        await pilot.pause()
                        screen = app.screen
                        await screen._save_recreate_async(app.store.get_project.return_value)
                        await pilot.pause()
                        recreate.assert_called_once_with(
                            paths=app.paths, global_cfg=app.global_cfg,
                            project=app.store.get_project.return_value,
                        )
                        app.store.delete_project_config.assert_not_called()
                        app.store.save_project.assert_called_once_with(app.store.get_project.return_value)
                        self.assertIsInstance(app.screen, MessageScreen)
                        self.assertEqual(
                            app.screen.title_text,
                            "Error al guardar y recrear" if error else "Contenedor recreado",
                        )


if __name__ == "__main__":
    unittest.main()
