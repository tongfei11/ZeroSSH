"""Regression checks for the Zero keyboard's evdev symbol codes."""

import runpy
import unittest
from pathlib import Path
from unittest.mock import patch

NATIVE = runpy.run_path(str(Path(__file__).with_name("zero-ssh.py")))
KEY_CODES = {pair[0]: code for code, pair in NATIVE["PRINTABLE"].items()}
SYM, PERIOD = 127, 91


class ZeroKeyboardTest(unittest.TestCase):
    def test_symbol_layer_period_in_host_address(self):
        app = NATIVE["App"]("http://127.0.0.1:4173", None)
        app.apply_state({"hosts": [], "sessions": []})

        def tap(code):
            app.handle_key(code, 1)
            app.handle_key(code, 0)

        tap(NATIVE["ENTER"])
        tap(NATIVE["ENTER"])
        for char in "192.0.2.1":
            if char == ".":
                app.handle_key(SYM, 1)
                tap(PERIOD)
                app.handle_key(SYM, 0)
            else:
                tap(KEY_CODES[char])

        self.assertEqual(app.host_form["hostname"], "192.0.2.1")
        tap(NATIVE["ENTER"])
        tap(PERIOD)
        self.assertEqual(app.host_form["port"], "")

    def test_save_preserves_password_and_defaults(self):
        for password in ("", " a.b "):
            with self.subTest(password_saved=bool(password)):
                app = NATIVE["App"]("http://127.0.0.1:4173", None)
                app.apply_state({"hosts": [], "sessions": []})
                app.handle_key(NATIVE["ENTER"], 1)
                self.assertEqual(
                    list(app.host_form), ["name", "hostname", "port", "user", "password"]
                )
                app.host_form.update(hostname="example.test", user="admin", password=password)
                with patch.object(app, "request", return_value={"id": "new"}) as request:
                    app.save_host()
                expected = {
                    "name": "example.test", "hostname": "example.test",
                    "port": 22, "user": "admin", "authMode": "password",
                }
                if password:
                    expected["password"] = password
                request.assert_called_once_with("/api/hosts", "POST", expected)

    def test_short_escape_cancels_and_long_escape_exits(self):
        app = NATIVE["App"]("http://127.0.0.1:4173", None)
        app.apply_state({"hosts": [], "sessions": []})
        esc = NATIVE["ESC"]
        with patch("time.monotonic", side_effect=[11, 20, 23]):
            app.handle_key(NATIVE["ENTER"], 1)
            self.assertTrue(app.handle_key(esc, 1))
            self.assertIsNotNone(app.host_form)
            self.assertTrue(app.handle_key(esc, 0))
            self.assertIsNone(app.host_form)
            self.assertEqual(app.toast_until, 0)
            self.assertTrue(app.handle_key(esc, 1))
            self.assertEqual(app.exit_at, 23)
            self.assertTrue(app.handle_key(esc, 2))
            self.assertFalse(app.handle_key(esc, 0))

    def test_terminal_uses_the_device_symbol_codes(self):
        decode = NATIVE["terminal_key"]
        self.assertEqual(decode(PERIOD, {SYM}, False), ".")
        self.assertEqual(decode(52, {SYM}, False), "*")
        self.assertEqual(decode(KEY_CODES["b"], set(), False), "b")


if __name__ == "__main__":
    unittest.main()
