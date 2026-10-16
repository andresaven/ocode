import ast
from contextlib import ExitStack
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from ocode import docker
from ocode.model import ContainerRuntime, GlobalConfig, MAVEN_VERSION, ProjectConfig
from ocode.paths import Paths


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.cfg = GlobalConfig.defaults()
        self.cfg.container_runtime = ContainerRuntime(kind="docker")
        self.project = ProjectConfig("test-id", "demo", "branch", ".")

    def reuse(self, mode, running, metadata=None):
        container_mode = "tui" if mode == "tui" else "shared"
        result = subprocess.CompletedProcess([], 0, metadata or f'{container_mode}|false|[]')
        with patch.object(docker, "container_exists", return_value=True), \
             patch.object(docker, "container_running", return_value=running), \
             patch.object(docker, "run_capture", return_value=result), \
             patch.object(docker, "build_run_command") as create:
            preview = docker.build_launch_command(
                paths=None, global_cfg=self.cfg, project=self.project,
                mode=mode, detached=(mode == "serve"),
            )
            create.assert_not_called()
            return preview

    def test_stopped_tui_is_started_and_attached(self):
        preview = self.reuse("tui", False)
        self.assertEqual(preview.args, ["docker", "start", "-ai", self.project.container_name])

    def test_running_tui_is_attached(self):
        preview = self.reuse("tui", True)
        self.assertEqual(preview.args, ["docker", "attach", self.project.container_name])

    def test_stopped_web_is_started(self):
        preview = self.reuse("serve", False)
        self.assertEqual(preview.args, ["docker", "start", self.project.container_name])

    def test_running_web_is_not_restarted(self):
        self.assertEqual(self.reuse("serve", True).args, [])

    def test_old_web_mode_is_detected(self):
        self.assertTrue(self.reuse(
            "serve", True, '<no value>|false|["/usr/local/lib/ocode-web-proxy.mjs"]',
        ).reused)

    def test_stopped_legacy_web_requires_explicit_recreation(self):
        for metadata in (
            'serve|false|[]',
            '<no value>|false|["/usr/local/lib/ocode-web-proxy.mjs"]',
        ):
            for mode in ("serve", "tui"):
                with self.subTest(metadata=metadata, mode=mode):
                    with patch.object(docker, "launch_detached") as launch, \
                         patch.object(docker, "stop_container") as stop, \
                         patch.object(docker, "remove_container") as remove:
                        with self.assertRaisesRegex(docker.DockerError, "Guardar y recrear contenedor"):
                            self.reuse(mode, False, metadata)
                        launch.assert_not_called()
                        stop.assert_not_called()
                        remove.assert_not_called()

    def test_stopped_web_opens_tui_in_same_container(self):
        preview = self.reuse("tui", False, 'shared|false|[]')
        self.assertEqual(preview.args, ["docker", "start", self.project.container_name])
        self.assertEqual(preview.foreground_args[:6], [
            "docker", "exec", "-it", self.project.container_name, "sh", "-c",
        ])
        self.assertIn('export OPENCODE_PASSWORD="$OPENCODE_SERVER_PASSWORD"', preview.foreground_args[-1])
        self.assertIn("--server", preview.foreground_args[-1])

    def test_running_web_opens_tui_without_restart(self):
        preview = self.reuse("tui", True, 'serve|false|[]')
        self.assertEqual(preview.args, [])
        self.assertIsNotNone(preview.foreground_args)

    def test_shared_container_supports_both_interfaces(self):
        for running in (False, True):
            for mode in ("tui", "serve"):
                preview = self.reuse(mode, running, 'shared|false|[]')
                self.assertEqual(preview.args, [] if running else [
                    "docker", "start", self.project.container_name,
                ])
                self.assertEqual(preview.foreground_args is not None, mode == "tui")

    def test_legacy_tui_web_request_is_non_destructive(self):
        with self.assertRaisesRegex(docker.DockerError, "no tiene el servidor Web"):
            self.reuse("serve", False, 'tui|false|[]')

    def test_old_disposable_tui_is_rejected(self):
        with self.assertRaises(docker.DockerError):
            self.reuse("tui", True, 'tui|true|[]')

    def test_new_containers_are_persistent(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = Paths(Path(directory))
            paths.web_proxy_script.parent.mkdir(parents=True)
            paths.web_proxy_script.touch()
            paths.instructions_script.touch()
            self.project.workspace = directory
            for mode in ("tui", "serve"):
                preview = docker.build_run_command(
                    paths=paths, global_cfg=self.cfg, project=self.project,
                    mode=mode, detached=(mode == "serve"),
                )
                self.assertNotIn("--rm", preview.args)
                self.assertIn("io.ocode.mode=shared", preview.args)
                self.assertIn("-d", preview.args)
                self.assertIn("-p", preview.args)
                self.assertEqual(preview.args[-1], "/usr/local/lib/ocode-web-proxy.mjs")
                self.assertEqual(preview.foreground_args is not None, mode == "tui")
                self.assertNotIn(preview.server_password, preview.powershell())
                self.assertNotIn(preview.web_token, preview.powershell())

    def test_runtimes_pin_maven_without_building_images(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = Paths(Path(directory))
            for name, profile in self.cfg.runtimes.items():
                with self.subTest(name=name), patch.object(
                    docker, "run_capture", return_value=Mock(stdout="preparado")
                ) as run:
                    self.assertIn(f"maven{MAVEN_VERSION}", profile.image)
                    self.assertEqual(docker.build_runtime(paths, self.cfg, name), "preparado")
                    dockerfile = (paths.runtimes_dir / name / "Dockerfile").read_text()
                    self.assertIn(f"maven={MAVEN_VERSION}-r0", dockerfile)
                    self.assertIn("alpine/v3.21/community", dockerfile)
                    self.assertIn(f"apk add --no-cache openjdk{profile.java}-jdk", dockerfile)
                    self.assertIn("RUN mvn -version", dockerfile)
                    run.assert_called_once_with(docker.Engine("docker", ["docker"]), [
                        "build", "-t", profile.image, str(paths.runtimes_dir / name),
                    ])

    def test_previous_runtime_image_is_replaced_by_maven_pinned_tag(self):
        old = self.cfg.to_dict()
        old["runtimes"]["java8"]["image"] = "ocode-runtime:2.0.24-java8"
        loaded = GlobalConfig.from_dict(old)
        self.assertEqual(loaded.runtimes["java8"].image,
                         self.cfg.runtimes["java8"].image)

    def test_maven_repository_is_optional_and_readonly(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            repository = root / "mi usuario" / ".m2" / "repository"
            repository.mkdir(parents=True)
            paths = Paths(root / "ocode")
            paths.web_proxy_script.parent.mkdir(parents=True)
            paths.web_proxy_script.touch()
            paths.instructions_script.touch()
            self.project.workspace = directory
            self.project.maven_repository = str(repository)

            preview = docker.build_run_command(
                paths=paths, global_cfg=self.cfg, project=self.project,
                mode="tui", detached=False,
            )
            mounts = [preview.args[i + 1] for i, arg in enumerate(preview.args)
                      if arg == "--mount"]
            self.assertIn(
                f"type=bind,source={repository},target=/root/.m2/repository,readonly",
                mounts,
            )
            self.assertEqual(sum("target=/root/.m2/repository" in mount for mount in mounts), 1)
            self.assertNotIn(str(repository.parent), mounts)

            self.project.maven_repository = ""
            preview = docker.build_run_command(
                paths=paths, global_cfg=self.cfg, project=self.project,
                mode="serve", detached=True,
            )
            self.assertNotIn("target=/root/.m2/repository", " ".join(preview.args))

    def test_maven_repository_rejects_parent_missing_and_unsafe_path(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            repository = Path(directory) / ".m2" / "repository"
            repository.mkdir(parents=True)
            for value in (str(repository.parent), str(repository / "missing"),
                          str(Path(directory) / "missing"), "repository", str(repository) + ",other"):
                with self.subTest(value=value):
                    with self.assertRaises(docker.DockerError):
                        docker.maven_repository_path(value)

    def test_foreground_starts_server_before_client(self):
        preview = docker.CommandPreview(
            ["docker", "start", "demo"],
            foreground_args=docker._tui_client_command(docker.Engine("docker", ["docker"]), "demo"),
        )
        with patch.object(docker, "launch_detached") as start, \
             patch.object(docker, "wait_for_backend") as ready, \
             patch.object(docker.subprocess, "call", return_value=0) as client:
            self.assertEqual(docker.launch_foreground(preview), 0)
            start.assert_called_once_with(preview)
            ready.assert_called_once_with(preview.foreground_args)
            client.assert_called_once_with(preview.foreground_args)

    def test_configuration_mounts_stay_outside_working_copy(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            workspace = root / "working-copy"
            workspace.mkdir()
            paths = Paths(root / "ocode")
            paths.web_proxy_script.parent.mkdir(parents=True)
            paths.web_proxy_script.touch()
            paths.instructions_script.touch()
            paths.config_dir.mkdir()
            paths.global_agents.write_text("Global rules", encoding="utf-8")
            paths.opencode_config.write_text("{}", encoding="utf-8")
            paths.project_dir(self.project.project_id).mkdir(parents=True)
            project_agents = paths.project_agents(self.project.project_id)
            project_agents.write_text("Project rules", encoding="utf-8")
            project_config = paths.project_config(self.project.project_id)
            project_config.write_text("{}", encoding="utf-8")
            self.project.workspace = str(workspace)
            self.project.sensitive_paths = []

            # An empty checkout stays empty; existing project files are neither
            # overwritten nor hidden behind ocode's bind mounts.
            for existing in (False, True):
                if existing:
                    (workspace / "AGENTS.md").write_text("Repository guidance")
                    (workspace / "opencode.jsonc").write_text('{"model":"repo/model"}')
                before = {p.name: p.read_bytes() for p in workspace.iterdir()}
                for enabled in (False, True):
                    self.project.project_agents_enabled = enabled
                    self.project.project_config_enabled = enabled
                    preview = docker.build_run_command(
                        paths=paths, global_cfg=self.cfg, project=self.project,
                        mode="serve", detached=True,
                    )
                    mounts = [preview.args[i + 1] for i, arg in enumerate(preview.args)
                              if arg == "--mount"]
                    targets = {spec.split("target=", 1)[1].split(",", 1)[0]
                               for spec in mounts}
                    self.assertNotIn("/workspace/AGENTS.md", targets)
                    self.assertNotIn("/workspace/opencode.jsonc", targets)
                    self.assertIn("/etc/ocode/global-AGENTS.md", targets)
                    self.assertIn(
                        f"type=volume,source={self.project.data_volume},target=/root/.local/share/opencode",
                        mounts,
                    )
                    self.assertIn(
                        f"type=volume,source={self.project.state_volume},target=/root/.local/state/opencode",
                        mounts,
                    )
                    self.assertEqual("/etc/ocode/project-AGENTS.md" in targets, enabled)
                    self.assertEqual("/opencode.jsonc" in targets, enabled)
                    self.assertIn("OCODE_GLOBAL_AGENTS=/etc/ocode/global-AGENTS.md", preview.args)
                    self.assertEqual(
                        "OCODE_PROJECT_AGENTS=/etc/ocode/project-AGENTS.md" in preview.args,
                        enabled,
                    )
                    self.assertTrue(all(spec.endswith(",readonly") for spec in mounts
                                        if spec.startswith("type=bind,")
                                        and "target=/workspace" not in spec))
                    self.assertEqual(before, {p.name: p.read_bytes() for p in workspace.iterdir()})

    def test_running_server_is_not_started_on_tui_connect(self):
        preview = self.reuse("tui", True, 'shared|false|[]')
        with patch.object(docker, "launch_detached") as start, \
             patch.object(docker, "wait_for_backend"), \
             patch.object(docker.subprocess, "call", return_value=0):
            docker.launch_foreground(preview)
            start.assert_not_called()

    def test_client_exit_never_stops_or_removes_shared_container(self):
        for result in (0, 1, 130):
            with self.subTest(result=result):
                preview = self.reuse("tui", True, 'shared|false|[]')
                with patch.object(docker, "wait_for_backend"), \
                     patch.object(docker.subprocess, "call", return_value=result) as client, \
                     patch.object(docker, "stop_container") as stop, \
                     patch.object(docker, "remove_container") as remove, \
                     patch.object(docker, "remove_state") as state:
                    self.assertEqual(docker.launch_foreground(preview), result)
                    client.assert_called_once_with(preview.foreground_args)
                    stop.assert_not_called()
                    remove.assert_not_called()
                    state.assert_not_called()

    def test_remove_state_only_removes_selected_instance_volumes(self):
        other = ProjectConfig("other-id", "other", "branch", ".", port=4321)
        with patch.object(docker, "container_running", return_value=False) as running, \
             patch.object(docker.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "removed")) as run:
            docker.remove_state(self.cfg, self.project)
            running.assert_called_once_with(self.cfg, self.project.container_name)
            self.assertEqual([call.args[0] for call in run.call_args_list], [
                ["docker", "volume", "rm", self.project.data_volume],
                ["docker", "volume", "rm", self.project.state_volume],
            ])
            self.assertNotEqual(self.project.data_volume, other.data_volume)
            self.assertNotEqual(self.project.state_volume, other.state_volume)

    def test_readiness_preserves_engine_prefix_and_hides_credentials(self):
        for command in (["docker"], ["wsl", "docker"], ["podman"]):
            client = docker._tui_client_command(docker.Engine("custom", command), "demo")
            with patch.object(docker.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
                docker.wait_for_backend(client)
                args, kwargs = run.call_args
                self.assertEqual(args[0][:-1], command + ["exec", "demo", "node", "-e"])
                self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)

    def test_failed_backend_does_not_launch_client_or_remove_container(self):
        preview = self.reuse("tui", True, 'shared|false|[]')
        with patch.object(docker.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)), \
             patch.object(docker.subprocess, "call") as client, \
             patch.object(docker, "remove_container") as remove:
            with self.assertRaises(docker.DockerError):
                docker.launch_foreground(preview)
            client.assert_not_called()
            remove.assert_not_called()

    def test_stop_does_not_remove(self):
        with patch.object(docker, "run_capture", return_value=subprocess.CompletedProcess([], 0, "stopped")) as run:
            self.assertEqual(docker.stop_container(self.cfg, "demo"), "stopped")
            run.assert_called_once_with(docker.Engine("docker", ["docker"]), ["stop", "demo"])

    def test_remove_is_checked_and_not_forced(self):
        with patch.object(docker, "run_capture") as run:
            docker.remove_container(self.cfg, "demo")
            run.assert_called_once_with(docker.Engine("docker", ["docker"]), ["rm", "demo"])

    def test_reopen_registers_via_stdin_without_inspecting_auth(self):
        preview = docker.CommandPreview([])
        with patch.object(docker.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            docker.prepare_web_login(self.cfg, "demo", preview)
            args, kwargs = run.call_args
            self.assertEqual(args[0][:5], ["docker", "exec", "-i", "demo", "node"])
            self.assertTrue(preview.web_token)
            self.assertEqual(kwargs["input"], preview.web_token)
            self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
            self.assertTrue(preview.web_login_url("127.0.0.1", 4096).startswith(
                "http://127.0.0.1:4096/?ocode_login="
            ))

    def test_failed_reopen_preserves_container(self):
        with patch.object(docker.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)), \
             patch.object(docker.time, "sleep"), \
             patch.object(docker, "remove_container") as remove:
            with self.assertRaises(docker.DockerError):
                docker.prepare_web_login(self.cfg, "demo", docker.CommandPreview([]))
            remove.assert_not_called()

    def test_python_sources_parse(self):
        for filename in ("docker.py", "cli.py", "tui.py"):
            ast.parse((SRC / "ocode" / filename).read_text(encoding="utf-8"))


class RecreateContainerTests(unittest.TestCase):
    def setUp(self):
        self.cfg = GlobalConfig.defaults()
        self.cfg.container_runtime = ContainerRuntime(kind="docker")
        self.project = ProjectConfig("test-id", "demo", "branch", ".")
        self.paths = Mock()
        self.preview = docker.CommandPreview(["docker", "run", "-d", "image"])
        self.calls = Mock()
        stack = self.enterContext(ExitStack())
        for name in (
            "build_run_command", "image_exists", "container_status",
            "stop_container", "remove_container", "launch_detached",
            "wait_for_backend", "remove_state",
        ):
            mock = stack.enter_context(patch.object(docker, name))
            setattr(self, name, mock)
            self.calls.attach_mock(mock, name)
        self.build_run_command.return_value = self.preview
        self.image_exists.return_value = True
        self.container_status.return_value = "running"

    def recreate(self):
        docker.recreate_container(
            paths=self.paths, global_cfg=self.cfg, project=self.project,
        )

    def test_running_container_is_validated_then_replaced_and_checked(self):
        self.recreate()
        self.assertEqual([call[0] for call in self.calls.mock_calls], [
            "build_run_command", "image_exists", "container_status", "stop_container",
            "remove_container", "launch_detached", "wait_for_backend",
        ])
        self.build_run_command.assert_called_once_with(
            paths=self.paths, global_cfg=self.cfg, project=self.project,
            mode="serve", detached=True,
        )
        self.remove_container.assert_called_once_with(self.cfg, self.project.container_name)
        self.stop_container.assert_called_once_with(self.cfg, self.project.container_name)
        self.launch_detached.assert_called_once_with(self.preview)
        self.remove_state.assert_not_called()

    def test_stopped_or_absent_containers_do_not_need_stop(self):
        for status in ("exited", "created", "dead", "absent"):
            with self.subTest(status=status):
                self.calls.reset_mock()
                self.container_status.return_value = status
                self.recreate()
                self.stop_container.assert_not_called()
                self.assertEqual(self.remove_container.call_count, int(status != "absent"))
                self.launch_detached.assert_called_once_with(self.preview)
                self.remove_state.assert_not_called()

    def test_validation_failure_does_not_touch_existing_container(self):
        self.build_run_command.side_effect = docker.DockerError("Invalid protection")
        with self.assertRaises(docker.DockerError):
            self.recreate()
        self.stop_container.assert_not_called()
        self.remove_container.assert_not_called()
        self.launch_detached.assert_not_called()

    def test_missing_image_does_not_touch_existing_container(self):
        self.image_exists.return_value = False
        with self.assertRaisesRegex(docker.DockerError, "Prepara el runtime"):
            self.recreate()
        self.stop_container.assert_not_called()
        self.remove_container.assert_not_called()

    def test_missing_configuration_does_not_touch_existing_container(self):
        self.paths.global_agents.is_file.return_value = False
        with self.assertRaisesRegex(docker.DockerError, "Falta un fichero"):
            self.recreate()
        self.stop_container.assert_not_called()
        self.remove_container.assert_not_called()

    def test_engine_error_is_not_treated_as_absent(self):
        self.container_status.side_effect = docker.DockerError("Engine unavailable")
        with self.assertRaises(docker.DockerError):
            self.recreate()
        self.stop_container.assert_not_called()
        self.remove_container.assert_not_called()
        self.launch_detached.assert_not_called()

    def test_paused_container_is_not_removed(self):
        self.container_status.return_value = "paused"
        with self.assertRaisesRegex(docker.DockerError, "no se ha modificado"):
            self.recreate()
        self.stop_container.assert_not_called()
        self.remove_container.assert_not_called()

    def test_stop_or_removal_failure_prevents_launch(self):
        for operation in (self.stop_container, self.remove_container):
            with self.subTest(operation=operation):
                operation.side_effect = docker.DockerError("Engine failure")
                with self.assertRaises(docker.DockerError):
                    self.recreate()
                self.launch_detached.assert_not_called()
                self.remove_state.assert_not_called()
                operation.side_effect = None

    def test_failed_launch_or_backend_preserves_data_for_retry(self):
        for operation in (self.launch_detached, self.wait_for_backend):
            with self.subTest(operation=operation):
                operation.side_effect = docker.DockerError("Backend unavailable")
                with self.assertRaisesRegex(docker.DockerError, "Se conservan la configuración"):
                    self.recreate()
                self.remove_state.assert_not_called()
                operation.side_effect = None


if __name__ == "__main__":
    unittest.main()
