import contextlib
import io
import unittest
from importlib.metadata import version

import spacial_ui
from spacial_ui.cli import main


class PackageTests(unittest.TestCase):
    def test_cli_version_matches_distribution(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as error:
            main(["--version"])
        self.assertEqual(error.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), f"spacial-ui {version('spacial-ui')}")
        self.assertEqual(spacial_ui.__version__, version("spacial-ui"))


if __name__ == "__main__":
    unittest.main()
