import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ocode.model import next_project_port


class WebPortTests(unittest.TestCase):
    def test_suggests_first_free_port_from_default(self):
        self.assertEqual(next_project_port(4096, set()), 4096)
        self.assertEqual(next_project_port(4096, {4096, 4098}), 4097)
        self.assertEqual(next_project_port(4100, {4096, 4098}), 4100)

    def test_end_of_port_range_is_reported(self):
        with self.assertRaisesRegex(ValueError, "No quedan puertos"):
            next_project_port(65535, {65535})
        with self.assertRaisesRegex(ValueError, "predeterminado"):
            next_project_port(65536, set())
