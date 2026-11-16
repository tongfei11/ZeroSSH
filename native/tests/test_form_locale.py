import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("zero_ssh", Path(__file__).parents[1] / "zero-ssh.py")
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


class FormTests(unittest.TestCase):
    def test_locale_precedence(self):
        self.assertEqual(ui.detect_language({"LANG": "zh_CN.UTF-8", "LC_MESSAGES": "en_US.UTF-8"}), "en")
        self.assertEqual(ui.detect_language({"LANG": "en_US", "LC_ALL": "zh_TW"}), "zh")

    def test_pages_back_retains_password_and_address(self):
        app = ui.App("http://127.0.0.1:4173", None)
        app.request = lambda *args: {"url": "http://192.168.22.29:4173/", "code": "12345678"}
        app.handle_host_key(ui.ENTER, 1)
        app.host_form["hostname"] = "server"
        for _ in range(3):
            app.handle_form_key(ui.ENTER, 1)
        self.assertEqual(app.form_focus, 3)
        app.host_form["password"] = "secret"
        app.handle_escape(1)
        app.handle_escape(0)
        self.assertEqual(app.form_focus, 2)
        self.assertEqual(app.host_form["password"], "secret")
        self.assertEqual(app.host_form["hostname"], "server")
        self.assertEqual(app.access["code"], "12345678")

    def test_hold_esc_exits(self):
        app = ui.App("", None)
        app.exit_at = 0
        app.escape_pressed = True
        self.assertFalse(app.handle_escape(0))


if __name__ == "__main__":
    unittest.main()
