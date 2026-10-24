import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('archive_setup', ROOT / 'portal/archive_setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class SetupTests(unittest.TestCase):
    def test_private_setup_and_reuse_never_repeat_bot_logout(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source.conf'
            source.write_text('[sala]\ntype = drive\ntoken = {"refresh_token":"fixture-drive-token"}\n')
            secret = root / 'secrets'
            token = '123456789:abcdefghijklmno_pqrstuvwxyz12345'
            captured = io.StringIO()
            with patch('builtins.input', side_effect=['', '123456', '-1001234567890', '', 's']), \
                 patch.object(setup.getpass, 'getpass', side_effect=['a' * 32, token]), \
                 patch.object(setup, 'cloud_bot_call', return_value=True) as cloud, contextlib.redirect_stdout(captured):
                config = setup.configure(secret, source)
            self.assertEqual(config['drive_remote'], 'sala:MediaStack')
            self.assertEqual([call.args[1] for call in cloud.call_args_list], ['getMe', 'logOut'])
            self.assertNotIn(token, captured.getvalue())
            self.assertNotIn('a' * 32, captured.getvalue())
            key = (secret / 'archive/proxy-key').read_text()
            self.assertEqual(len(key), 64)
            self.assertEqual(json.loads((secret / 'archive/config.json').read_text())['telegram_bot_token'], token)
            with patch('builtins.input', return_value=''), patch.object(setup, 'cloud_bot_call') as cloud:
                self.assertEqual(setup.configure(secret), config)
            cloud.assert_not_called()
            self.assertEqual((secret / 'archive/proxy-key').read_text(), key)

    def test_invalid_credentials_never_contact_telegram(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source.conf'
            source.write_text('[sala]\ntype = drive\n')
            with patch('builtins.input', side_effect=['', 'not-an-id', '-1001234567890']), \
                 patch.object(setup.getpass, 'getpass', side_effect=['not-a-hash', 'not-a-token']), \
                 patch.object(setup, 'cloud_bot_call') as cloud, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(ValueError):
                    setup.configure(root / 'secrets', source)
            cloud.assert_not_called()

    def test_non_drive_remote_is_rejected_before_credentials_are_requested(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source.conf'
            source.write_text('[scratch]\ntype = local\n')
            with patch('builtins.input') as prompt, patch.object(setup, 'cloud_bot_call') as cloud:
                with self.assertRaises(ValueError):
                    setup.configure(root / 'secrets', source)
            prompt.assert_not_called()
            cloud.assert_not_called()


if __name__ == '__main__':
    unittest.main()
