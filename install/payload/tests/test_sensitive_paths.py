import json
import shutil
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PAYLOAD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PAYLOAD / "src"))

from ocode.model import ProjectConfig, SensitivePathsError, read_sensitive_paths
from ocode.paths import Paths
from ocode.store import BUILTIN_SENSITIVE_PATHS, Store


class SensitivePathsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.paths = Paths(Path(self.temp.name))
        shutil.copytree(PAYLOAD / "defaults", self.paths.defaults_dir)
        self.store = Store(self.paths)
        self.store.initialize()
        self.project = self.store.create_project(
            name="demo", branch="main", workspace=self.temp.name,
            runtime="java8", default_mode="tui", port=4096,
        )
        self.rules = self.paths.project_sensitive_paths(self.project.project_id)
        self.config = self.paths.project_json(self.project.project_id)

    def test_new_project_has_separate_file(self):
        self.assertEqual(read_sensitive_paths(self.rules), BUILTIN_SENSITIVE_PATHS)
        self.assertNotIn("sensitive_paths", json.loads(self.config.read_text()))

    def test_creation_does_not_add_configuration_to_working_copy(self):
        workspace = Path(self.temp.name) / "working-copy"
        workspace.mkdir()
        project = self.store.create_project(
            name="isolated", branch="main", workspace=str(workspace),
            runtime="java8", default_mode="tui", port=4097,
        )
        self.assertEqual(list(workspace.iterdir()), [])
        self.assertTrue(self.paths.project_agents(project.project_id).is_file())
        self.assertTrue(self.paths.project_config(project.project_id).is_file())

    def test_manual_edits_survive_restart_and_metadata_save(self):
        text = "# Mis reglas\n\n/k8s/\nsecrets\\private\n/k8s/\n"
        self.rules.write_text(text, encoding="utf-8-sig")
        original = self.rules.read_bytes()
        self.store.initialize()
        loaded = self.store.get_project(self.project.project_id)
        self.assertEqual(loaded.sensitive_paths, ["**/k8s", "**/secrets/private"])
        # Even a previously loaded object must not overwrite external edits.
        self.project.port = 4097
        self.store.save_project(self.project)
        self.assertEqual(self.rules.read_bytes(), original)

    def test_empty_file_is_respected(self):
        self.rules.write_text("# Sin reglas de montaje\n", encoding="utf-8")
        self.store.initialize()
        self.assertEqual(self.store.get_project(self.project.project_id).sensitive_paths, [])

    def test_explicit_rule_update(self):
        self.project.sensitive_paths = ["/private/"]
        self.store.save_project(self.project, save_sensitive_paths=True)
        self.assertEqual(read_sensitive_paths(self.rules), ["**/private"])

    def test_legacy_project_is_migrated_once(self):
        data = self.project.to_dict()
        data["sensitive_paths"] = ["custom", "src/main/resources/certs"]
        self.config.write_text(json.dumps(data), encoding="utf-8")
        self.rules.unlink()
        self.store.initialize()
        migrated = read_sensitive_paths(self.rules)
        self.assertIn("**/custom", migrated)
        self.assertTrue(set(BUILTIN_SENSITIVE_PATHS).issubset(migrated))
        self.assertNotIn("sensitive_paths", json.loads(self.config.read_text()))

    def test_missing_file_is_not_silently_recreated(self):
        self.rules.unlink()
        with self.assertRaises(SensitivePathsError):
            self.store.get_project(self.project.project_id)
        with self.assertRaises(SensitivePathsError):
            self.store.save_project(self.project)
        self.assertFalse(self.rules.exists())

    def test_invalid_rule_reports_line(self):
        self.rules.write_text("# comentario\n../secret\n", encoding="utf-8")
        with self.assertRaisesRegex(SensitivePathsError, "línea 2"):
            self.store.get_project(self.project.project_id)


if __name__ == "__main__":
    unittest.main()
