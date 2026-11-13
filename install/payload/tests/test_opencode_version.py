import io
import json
import subprocess
import sys
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ocode import docker, versions
from ocode.model import (
    ContainerRuntime,
    GlobalConfig,
    OPENCODE_IMAGE,
    OPENCODE_VERSION,
    OpenCodeVersionError,
    ProjectConfig,
    normalize_opencode_version,
)
from ocode.versions import VersionListError


class OpenCodeVersionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = GlobalConfig.defaults()
        self.cfg.container_runtime = ContainerRuntime(kind="docker")

    def test_defaults_pin_base_image_and_runtime_images(self):
        self.assertEqual(self.cfg.opencode_version, OPENCODE_VERSION)
        self.assertEqual(self.cfg.opencode_image, OPENCODE_IMAGE)
        self.assertEqual(
            self.cfg.runtimes["java17"].image,
            f"ocode-runtime:{OPENCODE_VERSION}-maven3.9.9-java17",
        )

    def test_default_version_change_does_not_change_runtime_images(self):
        moved = self.cfg.with_opencode_default("v2.2.7")
        self.assertEqual(moved.opencode_version, "2.2.7")
        self.assertEqual(moved.opencode_image, OPENCODE_IMAGE)
        self.assertEqual(
            moved.runtimes["java17"].image,
            f"ocode-runtime:{OPENCODE_VERSION}-maven3.9.9-java17",
        )

    def test_config_roundtrip_keeps_default_version(self):
        data = self.cfg.to_dict()
        data["opencode_version"] = "2.3.0"
        data["opencode_image"] = "ghcr.io/anomalyco/opencode:1.0.94"
        loaded = GlobalConfig.from_dict(data)
        self.assertEqual(loaded.opencode_version, "2.3.0")
        self.assertEqual(loaded.opencode_image, OPENCODE_IMAGE)
        self.assertEqual(
            loaded.runtimes["java11"].image,
            f"ocode-runtime:{OPENCODE_VERSION}-maven3.9.9-java11",
        )

    def test_invalid_reserve_version_falls_back_to_the_base(self):
        data = self.cfg.to_dict()
        data["opencode_version"] = "1.0.94"
        loaded = GlobalConfig.from_dict(data)
        self.assertEqual(loaded.opencode_version, OPENCODE_VERSION)

    def test_invalid_versions_are_rejected(self):
        for value in (
            "latest", "2.2", "", "2.2.7/evil", "2.2.7 beta", "1.0.94",
            "2.0.23", "2.0.0",
        ):
            with self.subTest(value=value), self.assertRaises(OpenCodeVersionError):
                normalize_opencode_version(value)
        self.assertEqual(normalize_opencode_version(" 2.2.7 "), "2.2.7")
        self.assertEqual(normalize_opencode_version("2.0.24"), "2.0.24")

    def test_project_version_is_read_and_validated(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            project = ProjectConfig("id", "demo", "trunk", str(root))
            project.opencode_version = "v2.2.7"
            project.save(root / "project.json")
            (root / "sensitive-paths.txt").write_text("", encoding="utf-8")
            loaded = ProjectConfig.from_file(root / "project.json")
            self.assertEqual(loaded.opencode_version, "2.2.7")

            data = json.loads((root / "project.json").read_text(encoding="utf-8"))
            data["opencode_version"] = "latest"
            (root / "project.json").write_text(
                json.dumps(data), encoding="utf-8"
            )
            self.assertEqual(
                ProjectConfig.from_file(root / "project.json").opencode_version,
                "latest",
            )

    def test_container_version_is_read_from_the_binary(self):
        with patch.object(
            docker,
            "run_capture",
            return_value=Mock(stdout="opencode v2.2.5\n"),
        ) as run:
            self.assertEqual(
                docker.container_opencode_version(self.cfg, "demo"), "2.2.5"
            )
        self.assertEqual(run.call_args.args[1], ["exec", "demo", "opencode", "--version"])
        self.assertEqual(run.call_args.kwargs["timeout"], 15)

    def test_stopped_container_has_no_readable_version(self):
        with patch.object(
            docker,
            "run_capture",
            side_effect=docker.DockerError("not running"),
        ), patch.object(
            docker, "container_package_version", return_value=""
        ):
            self.assertEqual(
                docker.container_opencode_version(self.cfg, "demo"), ""
            )

    def test_stopped_container_version_comes_from_package_metadata(self):
        with patch.object(
            docker,
            "run_capture",
            side_effect=docker.DockerError("not running"),
        ), patch.object(
            docker, "container_package_version", return_value="2.2.5"
        ):
            self.assertEqual(
                docker.container_opencode_version(self.cfg, "demo"), "2.2.5"
            )

    def test_package_version_is_read_from_the_stopped_container(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            data = json.dumps({"version": "2.2.5"}).encode("utf-8")
            info = tarfile.TarInfo("package.json")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        completed = subprocess.CompletedProcess(
            [], 0, stdout=buffer.getvalue(), stderr=b""
        )
        with patch.object(docker.subprocess, "run", return_value=completed) as run:
            self.assertEqual(
                docker.container_package_version(self.cfg, "demo"), "2.2.5"
            )
        self.assertEqual(run.call_args.args[0][-3:], [
            "cp", "demo:/usr/local/lib/node_modules/@opencode/cli/package.json", "-",
        ])

    def test_upgrade_runs_npm_restarts_and_verifies(self):
        responses = [
            Mock(stdout="upgrade"),
            Mock(stdout="demo"),
            Mock(stdout="opencode v2.2.7\n"),
        ]
        with patch.object(docker, "run_capture", side_effect=responses) as run:
            self.assertEqual(
                docker.upgrade_opencode(self.cfg, "demo", "2.2.7"), "2.2.7"
            )
        self.assertEqual(run.call_args_list[0].args[1], [
            "exec", "demo", "opencode", "upgrade", "2.2.7", "--method", "npm",
        ])
        self.assertEqual(run.call_args_list[1].args[1], ["restart", "demo"])

    def test_upgrade_reports_a_version_mismatch(self):
        responses = [
            Mock(stdout="upgrade"),
            Mock(stdout="demo"),
            Mock(stdout="opencode v2.0.24\n"),
        ]
        with patch.object(docker, "run_capture", side_effect=responses):
            with self.assertRaisesRegex(docker.DockerError, "no quedó en"):
                docker.upgrade_opencode(self.cfg, "demo", "2.2.7")

    def test_update_existing_container_starts_and_stops_when_needed(self):
        project = ProjectConfig("id", "demo", "trunk", ".")
        with patch.object(docker, "container_status", return_value="exited"), \
             patch.object(docker, "container_opencode_version", return_value=""), \
             patch.object(docker, "start_container") as start, \
             patch.object(docker, "upgrade_opencode", return_value="2.2.7"), \
             patch.object(docker, "stop_container") as stop:
            self.assertEqual(
                docker.update_project_opencode_version(
                    self.cfg, project, "2.2.7"
                ),
                "2.2.7",
            )
        start.assert_called_once()
        stop.assert_called_once()

    def test_update_running_container_does_not_stop_it(self):
        project = ProjectConfig("id", "demo", "trunk", ".")
        with patch.object(docker, "container_status", return_value="running"), \
             patch.object(docker, "container_opencode_version", return_value="2.2.5"), \
             patch.object(docker, "upgrade_opencode", return_value="2.2.7"), \
             patch.object(docker, "stop_container") as stop:
            docker.update_project_opencode_version(self.cfg, project, "2.2.7")
        stop.assert_not_called()

    def test_update_skips_an_already_current_container(self):
        project = ProjectConfig("id", "demo", "trunk", ".")
        with patch.object(docker, "container_status", return_value="running"), \
             patch.object(docker, "container_opencode_version", return_value="2.2.7"), \
             patch.object(docker, "upgrade_opencode") as upgrade:
            self.assertEqual(
                docker.update_project_opencode_version(
                    self.cfg, project, "2.2.7"
                ),
                "2.2.7",
            )
        upgrade.assert_not_called()

    def test_update_skips_an_already_current_stopped_container(self):
        project = ProjectConfig("id", "demo", "trunk", ".")
        with patch.object(docker, "container_status", return_value="exited"), \
             patch.object(docker, "container_opencode_version", return_value="2.2.7"), \
             patch.object(docker, "start_container") as start, \
             patch.object(docker, "upgrade_opencode") as upgrade, \
             patch.object(docker, "stop_container") as stop:
            self.assertEqual(
                docker.update_project_opencode_version(
                    self.cfg, project, "2.2.7"
                ),
                "2.2.7",
            )
        start.assert_not_called()
        upgrade.assert_not_called()
        stop.assert_not_called()

    def test_update_without_container_only_saves_later(self):
        project = ProjectConfig("id", "demo", "trunk", ".")
        with patch.object(docker, "container_status", return_value="absent"):
            self.assertEqual(
                docker.update_project_opencode_version(
                    self.cfg, project, "2.2.7"
                ),
                "",
            )

    def test_update_engine_failure_does_not_touch_anything(self):
        project = ProjectConfig("id", "demo", "trunk", ".")
        with patch.object(
            docker,
            "container_status",
            side_effect=docker.DockerError("motor no disponible"),
        ), patch.object(docker, "start_container") as start, \
           patch.object(docker, "upgrade_opencode") as upgrade:
            with self.assertRaises(docker.DockerError):
                docker.update_project_opencode_version(
                    self.cfg, project, "2.2.7"
                )
        start.assert_not_called()
        upgrade.assert_not_called()

    def test_apply_project_version_skips_the_base_versions(self):
        project = ProjectConfig("id", "demo", "trunk", ".")
        with patch.object(docker, "upgrade_opencode") as upgrade:
            self.assertEqual(
                docker.apply_project_opencode_version(self.cfg, project), ""
            )
        upgrade.assert_not_called()

        project.opencode_version = "2.2.7"
        with patch.object(
            docker, "upgrade_opencode", return_value="2.2.7"
        ) as upgrade:
            self.assertEqual(
                docker.apply_project_opencode_version(self.cfg, project), "2.2.7"
            )
        upgrade.assert_called_once()


class RemoteVersionTests(unittest.TestCase):
    def test_npm_version_available(self):
        with patch.object(versions, "_request_status", return_value=200) as status:
            self.assertTrue(versions.npm_version_available("v2.2.7"))
        self.assertEqual(
            status.call_args.args[0],
            "https://registry.npmjs.org/@opencode%2Fcli/2.2.7",
        )
        with patch.object(versions, "_request_status", return_value=404):
            self.assertFalse(versions.npm_version_available("9.9.9"))
        with patch.object(versions, "_request_status", return_value=500):
            with self.assertRaises(VersionListError):
                versions.npm_version_available("2.2.7")

    def test_parse_stable_version(self):
        self.assertEqual(versions.parse_stable_version("2.2.7"), (2, 2, 7))
        self.assertIsNone(versions.parse_stable_version("latest"))
        self.assertIsNone(versions.parse_stable_version("2.3.0-rc1"))
        self.assertIsNone(versions.parse_stable_version("v2.2.7"))

    def test_next_link(self):
        self.assertEqual(
            versions._next_link(
                '</v2/anomalyco/opencode/tags/list?last=1.0.94&n=1000>; rel="next"'
            ),
            "/v2/anomalyco/opencode/tags/list?last=1.0.94&n=1000",
        )
        self.assertIsNone(versions._next_link('</tags/list>; rel="prev"'))

    def test_list_remote_versions_paginates_and_sorts(self):
        seen = []

        def request(url, headers=None):
            seen.append((url, headers))
            if "ghcr.io/token" in url:
                return 200, {"token": "secret"}, {}
            if "last=1.0.94" in url:
                return 200, {"tags": ["2.10.0", "2.2.7", "latest", "2.3.0-rc1"]}, {}
            return 200, {"tags": ["2.0.24", "1.0.94", "sin.version"]}, {
                "Link": '</v2/anomalyco/opencode/tags/list?last=1.0.94&n=1000>; rel="next"'
            }

        with patch.object(versions, "_request_json", side_effect=request):
            result = versions.list_remote_versions()

        self.assertEqual(result, ["2.10.0", "2.2.7", "2.0.24"])
        self.assertEqual(seen[1][1], {"Authorization": "Bearer secret"})

    def test_token_failure_is_reported(self):
        with patch.object(versions, "_request_json", return_value=(403, None, {})):
            with self.assertRaises(VersionListError):
                versions.list_remote_versions()

    def test_tags_failure_is_reported(self):
        responses = [
            (200, {"token": "secret"}, {}),
            (500, None, {}),
        ]
        with patch.object(versions, "_request_json", side_effect=responses):
            with self.assertRaises(VersionListError):
                versions.list_remote_versions()

    def test_network_failure_is_reported(self):
        with patch.object(
            versions,
            "_request_json",
            side_effect=VersionListError("sin conexión"),
        ):
            with self.assertRaises(VersionListError):
                versions.list_remote_versions()

    def test_release_summary_returns_highlights_and_url(self):
        body = (
            "Portada\n\n### Highlights\n\n- Uno\n- Dos\n\n"
            "### Otros\n\n- Detalle"
        )
        with patch.object(
            versions,
            "_request_json",
            return_value=(200, {
                "html_url": "https://github.com/anomalyco/opencode/releases/tag/v2.2.7",
                "body": body,
            }, {}),
        ) as request:
            notes, url = versions.release_summary("v2.2.7")
        self.assertEqual(notes, "- Uno\n- Dos")
        self.assertEqual(
            url, "https://github.com/anomalyco/opencode/releases/tag/v2.2.7"
        )
        self.assertEqual(
            request.call_args.args[0],
            "https://api.github.com/repos/anomalyco/opencode/releases/tags/v2.2.7",
        )

    def test_release_summary_falls_back_to_the_releases_page(self):
        for response in ((404, None, {}), (200, {"body": ""}, {})):
            with self.subTest(response=response), patch.object(
                versions, "_request_json", return_value=response
            ):
                notes, url = versions.release_summary("2.0.24")
                self.assertEqual(notes, "")
                self.assertEqual(
                    url, "https://github.com/anomalyco/opencode/releases"
                )

    def test_release_summary_survives_network_errors(self):
        with patch.object(
            versions,
            "_request_json",
            side_effect=VersionListError("sin conexión"),
        ):
            self.assertEqual(
                versions.release_summary("2.2.7"),
                ("", "https://github.com/anomalyco/opencode/releases"),
            )


if __name__ == "__main__":
    unittest.main()
