import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("host_actions_ui", Path(__file__).parents[1] / "zero-ssh.py")
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


@unittest.skipUnless(importlib.util.find_spec("websocket"), "Requires python3-websocket")
class HostActionsTests(unittest.TestCase):
    def test_edit_disconnect_and_confirmed_delete(self):
        with tempfile.TemporaryDirectory() as folder:
            app = ui.App("", None, ui.Settings(Path(folder) / "settings.json"))
            host = {"id": "one", "name": "NAS", "hostname": "example.test", "port": 22,
                    "user": "admin", "authMode": "password"}
            app.hosts = [host]
            app.active = {"one": {"id": "session"}}
            calls = []

            def request(path, method="GET", body=None):
                calls.append((path, method, body))
                if path == "/api/access":
                    return {}
                if path == "/api/state":
                    return {"hosts": [host], "sessions": []}
                return host

            app.request = request
            app.handle_host_key(ui.TAB, 1)
            self.assertEqual(app.index, 1)
            app.handle_host_key(ui.TAB, 1)
            self.assertEqual(app.index, 0)
            app.handle_host_key(ui.TAB, 1)
            self.assertEqual(app.index, 1)
            app.handle_host_key(ui.TAB, 1)
            self.assertEqual(app.index, 0)
            app.handle_host_key(32, 1)
            self.assertIn(("/api/sessions/session", "DELETE", None), calls)
            app.handle_host_key(18, 1)
            self.assertEqual(app.host_form["name"], "NAS")
            app.host_form["name"] = "Renamed"
            app.save_host()
            self.assertEqual(calls[-1][0:2], ("/api/hosts/one", "PUT"))
            self.assertNotIn("password", calls[-1][2])
            app.handle_host_key(18, 1)
            app.form_focus = 6
            app.handle_form_key(ui.ENTER, 1)
            self.assertTrue(app.delete_confirm)
            self.assertFalse(any(call[0] == "/api/hosts/one" and call[1] == "DELETE" for call in calls))
            app.action_focus = 1
            app.handle_action_key(ui.ENTER, 1)
            self.assertIn(("/api/hosts/one", "DELETE", None), calls)
