import contextlib
import io
import sys
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ocode import __version__
from ocode import cli
from ocode.tui import OcodeApp


class VersionTests(unittest.TestCase):
    def test_version_matches_package(self):
        path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        with path.open("rb") as source:
            self.assertEqual(__version__, tomllib.load(source)["project"]["version"])

    def test_header_includes_version(self):
        self.assertEqual(OcodeApp.TITLE, f"ocode {__version__}")

    def test_cli_version_does_not_initialize_configuration(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["ocode", "--version"]), \
                patch.object(cli, "Store") as store, \
                patch.object(cli, "Paths") as paths, \
                contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as exit_result:
                cli.main()
        self.assertEqual(exit_result.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), f"ocode {__version__}")
        store.assert_not_called()
        paths.assert_not_called()
