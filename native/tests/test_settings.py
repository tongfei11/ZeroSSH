import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('settings_ui', Path(__file__).parents[1] / 'zero-ssh.py')
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


class SettingsTests(unittest.TestCase):
    def test_roundtrip_private_and_atomic_failure_preserves_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            settings = ui.Settings(path)
            self.assertFalse(settings.keep_services)
            settings.keep_services = True
            settings.language, settings.theme = 'zh', 'Nord'
            settings.save()
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            settings.theme = 'Dracula'
            with patch.object(ui.os, 'replace', side_effect=OSError('disk')):
                with self.assertRaises(OSError):
                    settings.save()
            loaded = ui.Settings(path)
            self.assertEqual((loaded.language, loaded.theme), ('zh', 'Nord'))
            self.assertTrue(loaded.keep_services)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_corrupt_preferences_and_cli_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            path.write_text('[]')
            settings = ui.Settings(path)
            self.assertEqual(settings.theme, 'Default')
            settings.language = 'zh'
            with patch.object(ui, 'LANG_OVERRIDE', 'en'):
                settings.apply()
                self.assertEqual(ui.LANGUAGE, 'en')
