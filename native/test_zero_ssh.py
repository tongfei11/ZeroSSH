"""Regression checks for the Zero keyboard's evdev symbol codes."""

import runpy
import unittest
from pathlib import Path

NATIVE = runpy.run_path(str(Path(__file__).with_name("zero-ssh.py")))
KEY_CODES = {pair[0]: code for code, pair in NATIVE["PRINTABLE"].items()}
SYM, PERIOD = 127, 91


class ZeroKeyboardTest(unittest.TestCase):
    def test_symbol_layer_period_in_host_address(self):
        app = NATIVE["App"]("http://127.0.0.1:4173", None)

        def tap(code):
            app.handle_key(code, 1)
            app.handle_key(code, 0)

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
        tap(NATIVE["ENTER"])
        tap(PERIOD)
        self.assertEqual(app.host_form["port"], "")

    def test_terminal_uses_the_device_symbol_codes(self):
        decode = NATIVE["terminal_key"]
        self.assertEqual(decode(PERIOD, {SYM}, False), ".")
        self.assertEqual(decode(52, {SYM}, False), "*")
        self.assertEqual(decode(KEY_CODES["b"], set(), False), "b")


if __name__ == "__main__":
    unittest.main()
