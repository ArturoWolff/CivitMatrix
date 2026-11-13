"""Ctrl-C at startup should return a shell interruption status, not a traceback."""
import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from civitmatrix import cli


class CliInterruptTests(unittest.TestCase):
    def test_startup_interrupt_exits_cleanly(self):
        errors = io.StringIO()
        with patch.object(cli, 'load_dotenv'), \
                patch.object(cli, 'build_parser', side_effect=KeyboardInterrupt), \
                redirect_stderr(errors):
            result = cli.main(['--cli', '--heal'])
        self.assertEqual(result, 130)
        self.assertIn('Interrupted', errors.getvalue())
        self.assertNotIn('Traceback', errors.getvalue())


if __name__ == '__main__':
    unittest.main()
