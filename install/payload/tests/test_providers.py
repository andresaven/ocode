import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from textual.widgets import Input, ListView

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from ocode import docker, providers
from ocode.model import ContainerRuntime, GlobalConfig, ProjectConfig
from ocode.paths import Paths
from ocode.tui import (
    MessageScreen,
    OcodeApp,
    ProveedoresScreen,
    ProviderKeyScreen,
)

KEY = "dummy-key-for-tests-1234567890"
OTHER_KEY = "other-dummy-key-for-tests-0987654321"


class ProvidersStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.paths = Paths(Path(self.directory.name))

    def env_file(self, provider_id="opencode-go") -> Path:
        return providers.env_file(self.paths, provider_id)

    def test_save_writes_canonical_file_per_provider(self):
        for provider_id, provider in providers.PROVIDERS.items():
            with self.subTest(provider=provider_id):
                providers.save_key(self.paths, provider_id, KEY)
                self.assertEqual(
                    self.env_file(provider_id).read_bytes(),
                    f"{provider.env_var}={KEY}\n".encode("utf-8"),
                )
                self.assertTrue(providers.key_configured(self.paths, provider_id))
                self.assertEqual(
                    providers.key_last4(self.paths, provider_id), KEY[-4:]
                )

    def test_keys_are_independent(self):
        providers.save_key(self.paths, "opencode-go", KEY)
        providers.save_key(self.paths, "openai", OTHER_KEY)
        self.assertEqual(providers.key_last4(self.paths, "opencode-go"), KEY[-4:])
        self.assertEqual(
            providers.key_last4(self.paths, "openai"), OTHER_KEY[-4:]
        )
        providers.clear_key(self.paths, "openai")
        self.assertFalse(providers.key_configured(self.paths, "openai"))
        self.assertTrue(providers.key_configured(self.paths, "opencode-go"))

    def test_save_strips_surrounding_whitespace(self):
        providers.save_key(self.paths, "opencode-go", f"  {KEY}\n")
        self.assertEqual(providers.key_last4(self.paths, "opencode-go"), KEY[-4:])

    def test_file_is_not_bom_or_crlf(self):
        providers.save_key(self.paths, "openai", KEY)
        raw = self.env_file("openai").read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", raw)

    def test_save_is_atomic_and_leaves_no_temp_files(self):
        providers.save_key(self.paths, "opencode-go", KEY)
        providers.save_key(self.paths, "openai", OTHER_KEY)
        expected = {entry.filename for entry in providers.PROVIDERS.values()}
        leftovers = [
            item.name
            for item in self.paths.secrets_dir.iterdir()
            if item.name not in expected
        ]
        self.assertEqual(leftovers, [])

    def test_invalid_keys_are_rejected_without_echoing_them(self):
        for value in ("", "   ", "short-key", f"{KEY} extra", f"{KEY}\nmore",
                      "clave-con-control\x07suficientemente-larga"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError) as raised:
                    providers.save_key(self.paths, "opencode-go", value)
                self.assertNotIn(KEY, str(raised.exception))
                self.assertNotIn("\x07", str(raised.exception))
        self.assertFalse(self.env_file().exists())

    def test_clear_is_idempotent(self):
        providers.save_key(self.paths, "opencode-go", KEY)
        providers.clear_key(self.paths, "opencode-go")
        providers.clear_key(self.paths, "opencode-go")
        self.assertFalse(providers.key_configured(self.paths, "opencode-go"))
        self.assertIsNone(providers.key_fingerprint(self.paths, "opencode-go"))

    def test_fingerprint_is_stable_and_changes_with_the_key(self):
        providers.save_key(self.paths, "opencode-go", KEY)
        expected = hashlib.sha256(KEY.encode("utf-8")).hexdigest()[:12]
        self.assertEqual(
            providers.key_fingerprint(self.paths, "opencode-go"), expected
        )
        self.assertEqual(
            providers.key_fingerprint(self.paths, "opencode-go"), expected
        )
        providers.save_key(self.paths, "opencode-go", f"{KEY}-other")
        self.assertNotEqual(
            providers.key_fingerprint(self.paths, "opencode-go"), expected
        )

    def test_parser_tolerates_crlf_comments_and_blank_lines(self):
        self.env_file().parent.mkdir(parents=True)
        self.env_file().write_bytes(
            b"# comentario\r\n\r\nOPENCODE_API_KEY=" + KEY.encode() + b"\r\n"
        )
        self.assertTrue(providers.key_configured(self.paths, "opencode-go"))
        self.assertEqual(providers.key_last4(self.paths, "opencode-go"), KEY[-4:])

    def test_malformed_files_are_not_configured(self):
        self.env_file().parent.mkdir(parents=True)
        for content in (
            b"",
            b"# solo un comentario\n",
            b"OTRA_CLAVE=" + KEY.encode() + b"\n",
            b"OPENCODE_API_KEY=short\n",
            b"OPENCODE_API_KEY=" + KEY.encode() + b" \n",
            b"\xef\xbb\xbfOPENCODE_API_KEY=" + KEY.encode() + b"\n",
            b"OPENAI_API_KEY=" + KEY.encode() + b"\n",
        ):
            with self.subTest(content=content):
                self.env_file().write_bytes(content)
                self.assertFalse(providers.key_configured(self.paths, "opencode-go"))
                self.assertIsNone(
                    providers.key_fingerprint(self.paths, "opencode-go")
                )


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

    def env_files(self, args):
        return [
            args[index + 1]
            for index, arg in enumerate(args)
            if arg == "--env-file"
        ]

    def test_key_is_injected_with_env_file_and_label(self):
        providers.save_key(self.paths, "opencode-go", KEY)
        preview = self.build()
        args = preview.args

        self.assertEqual(
            self.env_files(args),
            [str(self.paths.secrets_dir / providers.PROVIDERS["opencode-go"].filename)],
        )
        self.assertIn(
            "io.ocode.opencode-go="
            f"{providers.key_fingerprint(self.paths, 'opencode-go')}",
            args,
        )
        for arg in args:
            self.assertNotIn(KEY, arg)
        self.assertNotIn(KEY, preview.powershell())

    def test_both_providers_are_injected(self):
        providers.save_key(self.paths, "opencode-go", KEY)
        providers.save_key(self.paths, "openai", OTHER_KEY)
        args = self.build().args

        self.assertEqual(
            self.env_files(args),
            [
                str(self.paths.secrets_dir / providers.PROVIDERS["opencode-go"].filename),
                str(self.paths.secrets_dir / providers.PROVIDERS["openai"].filename),
            ],
        )
        self.assertIn(
            "io.ocode.opencode-go="
            f"{providers.key_fingerprint(self.paths, 'opencode-go')}",
            args,
        )
        self.assertIn(
            f"io.ocode.openai={providers.key_fingerprint(self.paths, 'openai')}",
            args,
        )
        for arg in args:
            self.assertNotIn(KEY, arg)
            self.assertNotIn(OTHER_KEY, arg)

    def test_no_key_adds_no_env_file_or_label(self):
        preview = self.build()
        self.assertNotIn("--env-file", preview.args)
        for entry in providers.PROVIDERS.values():
            self.assertFalse(
                any(str(arg).startswith(f"{entry.label}=") for arg in preview.args)
            )

    def test_malformed_file_is_not_passed_to_docker(self):
        self.paths.secrets_dir.mkdir(parents=True)
        (self.paths.secrets_dir / providers.PROVIDERS["opencode-go"].filename).write_bytes(
            b"OPENCODE_API_KEY=short\n"
        )
        preview = self.build()
        self.assertNotIn("--env-file", preview.args)
        self.assertFalse(
            any(
                str(arg).startswith("io.ocode.opencode-go=")
                for arg in preview.args
            )
        )

    def test_env_file_path_uses_wsl_translation(self):
        providers.save_key(self.paths, "opencode-go", KEY)
        providers.save_key(self.paths, "openai", OTHER_KEY)
        cfg = GlobalConfig.defaults()
        cfg.container_runtime = ContainerRuntime(kind="wsl-docker")
        preview = self.build(cfg)
        for value in self.env_files(preview.args):
            self.assertTrue(value.startswith("/mnt/"))
            self.assertNotIn("\\", value)


class StaleLabelTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.paths = Paths(Path(self.directory.name))
        self.cfg = GlobalConfig.defaults()
        self.cfg.container_runtime = ContainerRuntime(kind="docker")

    def stale(self, labels):
        if labels is None:
            stdout = "null"
        else:
            stdout = json.dumps(labels)
        result = subprocess.CompletedProcess([], 0, stdout)
        with patch.object(docker, "run_capture", return_value=result):
            return docker.stale_providers(self.cfg, self.paths, "demo")

    def test_matching_labels_are_current(self):
        providers.save_key(self.paths, "opencode-go", KEY)
        providers.save_key(self.paths, "openai", OTHER_KEY)
        labels = {
            "io.ocode.opencode-go": providers.key_fingerprint(
                self.paths, "opencode-go"
            ),
            "io.ocode.openai": providers.key_fingerprint(self.paths, "openai"),
        }
        self.assertEqual(self.stale(labels), [])

    def test_old_label_is_stale(self):
        providers.save_key(self.paths, "opencode-go", KEY)
        self.assertEqual(
            self.stale({"io.ocode.opencode-go": "000000000000"}),
            ["opencode-go"],
        )

    def test_missing_label_is_stale_when_key_is_configured(self):
        providers.save_key(self.paths, "opencode-go", KEY)
        self.assertEqual(self.stale({}), ["opencode-go"])
        self.assertEqual(self.stale(None), ["opencode-go"])

    def test_label_is_stale_after_key_is_removed(self):
        self.assertEqual(
            self.stale({"io.ocode.opencode-go": "abc123def456"}),
            ["opencode-go"],
        )

    def test_missing_label_is_current_without_key(self):
        self.assertEqual(self.stale({}), [])
        self.assertEqual(self.stale(None), [])

    def test_each_provider_is_checked_independently(self):
        providers.save_key(self.paths, "openai", OTHER_KEY)
        labels = {
            "io.ocode.opencode-go": "",
            "io.ocode.openai": "000000000000",
        }
        self.assertEqual(self.stale(labels), ["openai"])

    def test_invalid_json_raises_docker_error(self):
        result = subprocess.CompletedProcess([], 0, "not-json")
        with patch.object(docker, "run_capture", return_value=result):
            with self.assertRaises(docker.DockerError):
                docker.stale_providers(self.cfg, self.paths, "demo")

    def test_engine_error_propagates(self):
        with patch.object(
            docker, "run_capture", side_effect=docker.DockerError("boom")
        ):
            with self.assertRaises(docker.DockerError):
                docker.stale_providers(self.cfg, self.paths, "demo")


class ProvidersScreenTests(unittest.IsolatedAsyncioTestCase):
    def make_app(self, paths):
        cfg = GlobalConfig.defaults()
        cfg.container_runtime = ContainerRuntime(kind="docker")
        cfg.check_updates = False
        store = Mock()
        store.load_global.return_value = cfg
        store.list_projects.return_value = []
        return OcodeApp(store, paths)

    async def test_screen_lists_providers_without_showing_input(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = Paths(Path(directory))
            app = self.make_app(paths)
            async with app.run_test() as pilot:
                screen = ProveedoresScreen()
                app.push_screen(screen)
                await pilot.pause()
                view = screen.query_one("#providers-list", ListView)
                self.assertEqual(len(view.children), len(providers.PROVIDERS))
                texts = [
                    str(item.query_one("Label").content)
                    for item in view.children
                ]
                for provider in providers.PROVIDERS.values():
                    self.assertTrue(
                        any(provider.name in text for text in texts)
                    )
                self.assertFalse(screen.query(Input))

    async def test_dialog_saves_and_clears_the_key(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = Paths(Path(directory))
            app = self.make_app(paths)
            async with app.run_test() as pilot:
                screen = ProveedoresScreen()
                app.push_screen(screen)
                await pilot.pause()

                view = screen.query_one("#providers-list", ListView)
                view.index = 0
                view.focus()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
                dialog = app.screen
                self.assertIsInstance(dialog, ProviderKeyScreen)
                self.assertEqual(dialog.provider.id, "opencode-go")

                dialog.query_one("#provider-input", Input).value = KEY
                await dialog.save()
                await pilot.pause()
                await pilot.pause()
                self.assertTrue(providers.key_configured(paths, "opencode-go"))
                self.assertIsInstance(app.screen, MessageScreen)
                self.assertNotIn(
                    KEY, str(app.screen.query_one("#message").content)
                )
                app.screen.dismiss(None)
                await pilot.pause()
                self.assertIsInstance(app.screen, ProveedoresScreen)
                self.assertIn(
                    "configurada",
                    str(
                        screen.query_one("#providers-list", ListView)
                        .children[0]
                        .query_one("Label")
                        .content
                    ),
                )

                await self.open_dialog_and_clear(pilot, screen, app)
                self.assertFalse(providers.key_configured(paths, "opencode-go"))

    async def open_dialog_and_clear(self, pilot, screen, app):
        view = screen.query_one("#providers-list", ListView)
        view.index = 0
        view.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        dialog = app.screen
        self.assertIsInstance(dialog, ProviderKeyScreen)
        dialog.query_one("#provider-clear").press()
        await pilot.pause()
        await pilot.pause()
        self.assertIsInstance(app.screen, MessageScreen)
        app.screen.dismiss(None)
        await pilot.pause()

    async def test_dialog_rejects_invalid_key_without_echoing_it(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = Paths(Path(directory))
            app = self.make_app(paths)
            async with app.run_test() as pilot:
                screen = ProveedoresScreen()
                app.push_screen(screen)
                await pilot.pause()
                view = screen.query_one("#providers-list", ListView)
                view.index = 1
                view.focus()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
                dialog = app.screen
                self.assertEqual(dialog.provider.id, "openai")
                invalid = "clave con espacios suficientemente larga"
                dialog.query_one("#provider-input", Input).value = invalid
                await dialog.save()
                await pilot.pause()
                self.assertIsInstance(app.screen, MessageScreen)
                self.assertNotIn(
                    invalid, str(app.screen.query_one("#message").content)
                )
                self.assertFalse(providers.key_configured(paths, "openai"))


if __name__ == "__main__":
    unittest.main()
