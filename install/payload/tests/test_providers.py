import hashlib
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from textual.widgets import Input

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from ocode import docker, providers
from ocode.model import ContainerRuntime, GlobalConfig, ProjectConfig
from ocode.paths import Paths
from ocode.tui import MessageScreen, OcodeApp, ProveedoresScreen

KEY = "dummy-key-for-tests-1234567890"


class ProvidersStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.paths = Paths(Path(self.directory.name))

    def env_file(self) -> Path:
        return providers.go_env_file(self.paths)

    def test_save_writes_canonical_file(self):
        providers.save_go_key(self.paths, KEY)
        self.assertEqual(
            self.env_file().read_bytes(),
            f"OPENCODE_API_KEY={KEY}\n".encode("utf-8"),
        )
        self.assertTrue(providers.go_key_configured(self.paths))
        self.assertEqual(providers.go_key_last4(self.paths), KEY[-4:])

    def test_save_strips_surrounding_whitespace(self):
        providers.save_go_key(self.paths, f"  {KEY}\n")
        self.assertEqual(providers.go_key_last4(self.paths), KEY[-4:])

    def test_file_is_not_bom_or_crlf(self):
        providers.save_go_key(self.paths, KEY)
        raw = self.env_file().read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", raw)

    def test_save_is_atomic_and_leaves_no_temp_files(self):
        providers.save_go_key(self.paths, KEY)
        leftovers = [
            item.name
            for item in self.paths.secrets_dir.iterdir()
            if item.name != providers.FILENAME
        ]
        self.assertEqual(leftovers, [])

    def test_invalid_keys_are_rejected_without_echoing_them(self):
        for value in ("", "   ", "short-key", f"{KEY} extra", f"{KEY}\nmore",
                      "clave-con-control\x07suficientemente-larga"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError) as raised:
                    providers.save_go_key(self.paths, value)
                self.assertNotIn(KEY, str(raised.exception))
                self.assertNotIn("\x07", str(raised.exception))
        self.assertFalse(self.env_file().exists())

    def test_clear_is_idempotent(self):
        providers.save_go_key(self.paths, KEY)
        providers.clear_go_key(self.paths)
        providers.clear_go_key(self.paths)
        self.assertFalse(providers.go_key_configured(self.paths))
        self.assertIsNone(providers.go_key_fingerprint(self.paths))

    def test_fingerprint_is_stable_and_changes_with_the_key(self):
        providers.save_go_key(self.paths, KEY)
        expected = hashlib.sha256(KEY.encode("utf-8")).hexdigest()[:12]
        self.assertEqual(providers.go_key_fingerprint(self.paths), expected)
        self.assertEqual(providers.go_key_fingerprint(self.paths), expected)
        providers.save_go_key(self.paths, f"{KEY}-other")
        self.assertNotEqual(providers.go_key_fingerprint(self.paths), expected)

    def test_parser_tolerates_crlf_comments_and_blank_lines(self):
        self.env_file().parent.mkdir(parents=True)
        self.env_file().write_bytes(
            b"# comentario\r\n\r\nOPENCODE_API_KEY=" + KEY.encode() + b"\r\n"
        )
        self.assertTrue(providers.go_key_configured(self.paths))
        self.assertEqual(providers.go_key_last4(self.paths), KEY[-4:])

    def test_malformed_files_are_not_configured(self):
        self.env_file().parent.mkdir(parents=True)
        for content in (
            b"",
            b"# solo un comentario\n",
            b"OTRA_CLAVE=" + KEY.encode() + b"\n",
            b"OPENCODE_API_KEY=short\n",
            b"OPENCODE_API_KEY=" + KEY.encode() + b" \n",
            b"\xef\xbb\xbfOPENCODE_API_KEY=" + KEY.encode() + b"\n",
        ):
            with self.subTest(content=content):
                self.env_file().write_bytes(content)
                self.assertFalse(providers.go_key_configured(self.paths))
                self.assertIsNone(providers.go_key_fingerprint(self.paths))


class RunCommandTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.paths = Paths(Path(self.directory.name))
        self.paths.web_proxy_script.parent.mkdir(parents=True)
        self.paths.web_proxy_script.touch()
        self.paths.instructions_script.touch()
        self.cfg = GlobalConfig.defaults()
        self.cfg.container_runtime = ContainerRuntime(kind="docker")
        self.project = ProjectConfig(
            "test-id", "demo", "branch", self.directory.name
        )

    def build(self, cfg=None):
        return docker.build_run_command(
            paths=self.paths,
            global_cfg=cfg or self.cfg,
            project=self.project,
            mode="serve",
            detached=True,
        )

    def test_key_is_injected_with_env_file_and_label(self):
        providers.save_go_key(self.paths, KEY)
        preview = self.build()
        args = preview.args

        self.assertIn("--env-file", args)
        self.assertEqual(
            args[args.index("--env-file") + 1],
            str(self.paths.secrets_dir / providers.FILENAME),
        )
        self.assertIn(
            f"io.ocode.opencode-go={providers.go_key_fingerprint(self.paths)}",
            args,
        )
        for arg in args:
            self.assertNotIn(KEY, arg)
        self.assertNotIn(KEY, preview.powershell())

    def test_no_key_adds_no_env_file_or_label(self):
        preview = self.build()
        self.assertNotIn("--env-file", preview.args)
        self.assertFalse(
            any(str(arg).startswith("io.ocode.opencode-go=") for arg in preview.args)
        )

    def test_malformed_file_is_not_passed_to_docker(self):
        self.paths.secrets_dir.mkdir(parents=True)
        (self.paths.secrets_dir / providers.FILENAME).write_bytes(
            b"OPENCODE_API_KEY=short\n"
        )
        preview = self.build()
        self.assertNotIn("--env-file", preview.args)
        self.assertFalse(
            any(str(arg).startswith("io.ocode.opencode-go=") for arg in preview.args)
        )

    def test_env_file_path_uses_wsl_translation(self):
        providers.save_go_key(self.paths, KEY)
        cfg = GlobalConfig.defaults()
        cfg.container_runtime = ContainerRuntime(kind="wsl-docker")
        preview = self.build(cfg)
        value = preview.args[preview.args.index("--env-file") + 1]
        self.assertTrue(value.startswith("/mnt/"))
        self.assertIn(providers.FILENAME, value)
        self.assertNotIn("\\", value)


class StaleLabelTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.paths = Paths(Path(self.directory.name))
        self.cfg = GlobalConfig.defaults()
        self.cfg.container_runtime = ContainerRuntime(kind="docker")

    def stale(self, stdout, returncode=0):
        result = subprocess.CompletedProcess([], returncode, stdout)
        with patch.object(docker, "run_capture", return_value=result):
            return docker.opencode_go_stale(self.cfg, self.paths, "demo")

    def test_matching_label_is_current(self):
        providers.save_go_key(self.paths, KEY)
        fingerprint = providers.go_key_fingerprint(self.paths)
        self.assertFalse(self.stale(f"{fingerprint}\n"))

    def test_old_label_is_stale(self):
        providers.save_go_key(self.paths, KEY)
        self.assertTrue(self.stale("000000000000\n"))

    def test_missing_label_is_stale_when_key_is_configured(self):
        providers.save_go_key(self.paths, KEY)
        self.assertTrue(self.stale("<no value>\n"))
        self.assertTrue(self.stale("\n"))

    def test_label_is_stale_after_key_is_removed(self):
        self.assertTrue(self.stale("abc123def456\n"))

    def test_missing_label_is_current_without_key(self):
        self.assertFalse(self.stale("<no value>\n"))
        self.assertFalse(self.stale("\n"))

    def test_engine_error_propagates(self):
        with patch.object(
            docker, "run_capture", side_effect=docker.DockerError("boom")
        ):
            with self.assertRaises(docker.DockerError):
                docker.opencode_go_stale(self.cfg, self.paths, "demo")


class ProvidersScreenTests(unittest.IsolatedAsyncioTestCase):
    async def test_screen_saves_and_clears_the_key(self):
        cfg = GlobalConfig.defaults()
        cfg.container_runtime = ContainerRuntime(kind="docker")
        cfg.check_updates = False
        store = Mock()
        store.load_global.return_value = cfg
        store.list_projects.return_value = []

        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = Paths(Path(directory))
            app = OcodeApp(store, paths)
            async with app.run_test() as pilot:
                screen = ProveedoresScreen()
                app.push_screen(screen)
                await pilot.pause()
                self.assertIn(
                    "no configurada",
                    str(screen.query_one("#go-status").content),
                )

                screen.query_one("#go-key", Input).value = KEY
                await screen.save()
                await pilot.pause()
                self.assertTrue(providers.go_key_configured(paths))
                self.assertIn("configurada", str(screen.query_one("#go-status").content))
                self.assertIsInstance(app.screen, MessageScreen)
                self.assertNotIn(KEY, str(app.screen.query_one("#message").content))
                app.screen.dismiss(None)
                await pilot.pause()

                await screen.clear()
                await pilot.pause()
                self.assertFalse(providers.go_key_configured(paths))
                self.assertIn("no configurada", str(screen.query_one("#go-status").content))


if __name__ == "__main__":
    unittest.main()
