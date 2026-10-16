import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from ocode import cli, docker
from ocode.model import ProjectConfig
from ocode.tui import LaunchRequest


class LaunchDiagnosticTests(unittest.TestCase):
    def test_tui_reports_selected_container_without_exposing_command(self):
        project = ProjectConfig("test-id", "demo", "branch", ".", port=4097)
        store = Mock()
        store.get_project.return_value = project
        target = "ocode-contenedor-seleccionado"
        preview = docker.CommandPreview(
            [], foreground_args=docker._tui_client_command(
                docker.Engine("docker", ["docker"]), target,
            ),
        )
        with patch.object(cli, "OcodeApp") as app, \
             patch.object(cli, "build_launch_command", return_value=preview), \
             patch.object(cli, "sensitive_applied_paths", return_value=[]), \
             patch.object(cli, "launch_foreground", return_value=0), \
             patch("builtins.print") as output:
            app.return_value.run.side_effect = [LaunchRequest(project.project_id, "tui"), None]
            self.assertEqual(cli.run_tui(store, Mock()), 0)

        lines = [call.args[0] for call in output.call_args_list]
        self.assertIn(f"[ocode] TUI: contenedor={target}", lines)
        self.assertNotIn(f"[ocode] TUI: contenedor={project.container_name}", lines)
        self.assertNotIn(preview.foreground_args[-1], "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
