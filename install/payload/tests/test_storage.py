import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ocode.model import ContainerRuntime, GlobalConfig, ProjectConfig
from ocode.paths import Paths
from ocode.storage import host_mount_path, storage_report


class StorageReportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.paths = Paths(Path(self.directory.name))
        self.cfg = GlobalConfig.defaults()
        self.cfg.container_runtime = ContainerRuntime(kind="docker")
        self.project = ProjectConfig("demo", "Proyecto", "rama", ".")

    def test_only_inspects_mounts_and_reports_real_mappings(self):
        mounts = [
            {"Type": "bind", "Source": "/host/code", "Destination": "/workspace", "RW": True},
            {"Type": "bind", "Source": "/host/old/AGENTS.md",
             "Destination": "/workspace/AGENTS.md", "RW": False},
            {"Type": "bind", "Source": "/host/config/opencode.jsonc",
             "Destination": "/root/.config/opencode/opencode.jsonc", "RW": False},
            {"Type": "volume", "Name": "real-data", "Source": "/var/lib/docker/volumes/real-data/_data",
             "Destination": "/root/.local/share/opencode", "RW": True},
            {"Type": "bind", "Source": "/dev/null", "Destination": "/workspace/protected-file", "RW": False},
        ]
        with patch("ocode.storage.subprocess.run", return_value=
                   subprocess.CompletedProcess([], 0, json.dumps(mounts), "")) as run:
            report = storage_report(self.paths, self.cfg, self.project)
        self.assertEqual(run.call_args.args[0], [
            "docker", "inspect", "--type", "container", "--format",
            "{{json .Mounts}}", self.project.container_name,
        ])
        self.assertEqual(run.call_count, 1)
        self.assertNotIn("/host/code", report)
        self.assertIn("/host/old/AGENTS.md", report)
        self.assertIn("montaje antiguo", report)
        self.assertIn("solo lectura", report)
        self.assertIn("lectura y escritura", report)
        self.assertIn("Volumen Docker: real-data", report)
        self.assertIn("/var/lib/docker/volumes/real-data/_data", report)
        self.assertIn("no en una carpeta de Windows", report)
        self.assertIn("Máscara de protección", report)
        self.assertIn(str(self.paths.project_sensitive_paths("demo")), report)
        self.assertIn("No se monta", report)

    def test_missing_container_keeps_host_paths_and_does_not_invent_mounts(self):
        with patch("ocode.storage.subprocess.run", return_value=
                   subprocess.CompletedProcess([], 1, "", "not found")):
            report = storage_report(self.paths, self.cfg, self.project)
        self.assertIn("Montajes no disponibles", report)
        self.assertIn(str(self.paths.global_agents), report)
        self.assertNotIn("Contenedor: /etc/ocode/global-AGENTS.md", report)

    def test_timeout_is_reported(self):
        with patch("ocode.storage.subprocess.run", side_effect=subprocess.TimeoutExpired([], 15)):
            report = storage_report(self.paths, self.cfg, self.project)
        self.assertIn("Montajes no disponibles", report)

    def test_windows_bind_paths_are_translated_but_volume_paths_are_not(self):
        for prefix in ("/mnt/", "/host_mnt/", "/run/desktop/mnt/host/"):
            self.assertEqual(host_mount_path(prefix + "c/ocode/config/AGENTS.md"),
                             r"C:\ocode\config\AGENTS.md")
        self.assertEqual(host_mount_path("/var/lib/docker/volumes/data/_data"),
                         "/var/lib/docker/volumes/data/_data")
        self.assertEqual(host_mount_path("/mnt/projects/ocode"), "/mnt/projects/ocode")
