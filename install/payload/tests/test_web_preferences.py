import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ocode.model import GlobalConfig, ProjectConfig
from ocode.paths import Paths
from ocode.store import Store


class WebPreferencesTests(unittest.TestCase):
    def test_new_projects_snapshot_global_defaults(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paths = Paths(Path(directory))
            paths.defaults_dir.mkdir(parents=True)
            paths.default_project_agents.write_text("Reglas", encoding="utf-8")
            paths.default_project_config.write_text("{}", encoding="utf-8")
            store = Store(paths)
            store.save_global(GlobalConfig.defaults())

            def create(name, port):
                return store.create_project(
                    name=name, branch="main", workspace=directory,
                    runtime="java8", default_mode="serve", port=port,
                )

            first = create("primera", 4100)
            self.assertEqual(store.get_project(first.project_id).web_initial_preferences,
                             {"show_agent": True, "color_scheme": "dark"})
            cfg = store.load_global()
            cfg.web_show_agent = False
            cfg.web_color_scheme = "light"
            store.save_global(cfg)
            second = create("segunda", 4101)
            self.assertEqual(store.get_project(second.project_id).web_initial_preferences,
                             {"show_agent": False, "color_scheme": "light"})
            self.assertEqual(store.get_project(first.project_id).web_initial_preferences,
                             {"show_agent": True, "color_scheme": "dark"})

    def test_existing_project_without_preferences_remains_unchanged(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "project.json"
            project = ProjectConfig("old", "old", "main", directory, sensitive_paths=[])
            project.save(path)
            self.assertIsNone(ProjectConfig.from_file(path).web_initial_preferences)
