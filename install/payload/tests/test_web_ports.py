import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ocode.model import ProjectConfig, next_project_port, projects_on_port
from ocode.paths import Paths
from ocode.store import Store


class WebPortTests(unittest.TestCase):
    def test_suggests_first_free_port_from_default(self):
        projects = [
            ProjectConfig("one", "Primero", "main", ".", port=4096),
            ProjectConfig("two", "Segundo", "main", ".", port=4098),
        ]
        self.assertEqual(next_project_port(4096, []), 4096)
        self.assertEqual(next_project_port(4096, projects), 4097)
        self.assertEqual(next_project_port(4100, projects), 4100)
        self.assertEqual(projects_on_port(projects, 4096), [projects[0]])
        self.assertEqual(projects_on_port(projects, 4096, exclude_id="one"), [])

    def test_end_of_port_range_is_reported(self):
        project = ProjectConfig("one", "Primero", "main", ".", port=65535)
        with self.assertRaisesRegex(ValueError, "No quedan puertos"):
            next_project_port(65535, [project])
        with self.assertRaisesRegex(ValueError, "predeterminado"):
            next_project_port(65536, [])

    def test_store_refuses_port_already_assigned_even_without_running_container(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            store = Store(Paths(Path(directory)))
            other = ProjectConfig("old", "Antigua", "main", directory, port=4096)
            with patch.object(store, "list_projects", return_value=[other]):
                with self.assertRaisesRegex(ValueError, "ya está asociado"):
                    store.create_project(
                        name="Nueva", branch="main", workspace=directory,
                        runtime="java8", default_mode="serve", port=4096,
                    )
            self.assertFalse(store.paths.projects_dir.exists())
