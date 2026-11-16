import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("lifecycle_ui", Path(__file__).parents[1] / "zero-ssh.py")
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


class LifecycleTests(unittest.TestCase):
    def test_closes_web_listener_on_return_and_failure(self):
        for error in (None, OSError("display failed")):
            with self.subTest(failure=bool(error)):
                with patch.object(ui.subprocess, "run") as control, patch.object(ui, "run_ui", side_effect=error):
                    if error:
                        with self.assertRaises(OSError):
                            ui.run(None)
                    else:
                        ui.run(None)
                self.assertEqual(control.call_args_list[0].args[0][2], "start")
                self.assertEqual(control.call_args_list[-1].args[0][2], "stop")
                self.assertIn("zero-ssh.socket", control.call_args_list[-1].args[0])
