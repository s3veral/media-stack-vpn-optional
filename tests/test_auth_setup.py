import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('auth_setup', ROOT / 'windows/auth_setup.py')
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)


class CredentialTests(unittest.TestCase):
    def test_seerr_selects_existing_1080p_profile_and_real_root(self):
        metadata = {'profiles': [{'id': 1, 'name': 'Any'}, {'id': 2, 'name': 'HD-1080p'}],
                    'rootFolders': [{'path': '/media/movies'}]}
        body = auth.seerr_service('radarr', 7878, 'unit-key', metadata)
        self.assertEqual(body['activeProfileId'], 2)
        self.assertEqual(body['activeDirectory'], '/media/movies')
        self.assertEqual(body['minimumAvailability'], 'released')
        self.assertTrue(body['isDefault'])
        with self.assertRaises(auth.SetupError):
            auth.seerr_service('radarr', 7878, 'unit-key', {'profiles': metadata['profiles']})

    def test_existing_seerr_services_are_tested_but_never_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'seerr/settings.json'
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({'main': {'apiKey': 'unit-key'}}))
            setup = auth.AuthSetup('admin', 'unit-only-password', configs=folder)
            existing = [{'id': 4, 'hostname': 'custom-host', 'isDefault': True, 'is4k': False}]
            with patch.object(auth, 'http', side_effect=[existing, {}, existing, {}]) as http:
                setup.seerr_services()
            self.assertEqual(len(http.call_args_list), 4)
            self.assertEqual(http.call_args_list[1].args[1], existing[0])
            self.assertTrue(all(call.args[0].endswith('/test') for call in http.call_args_list if len(call.args) > 1))

    def test_missing_seerr_services_are_tested_created_and_confirmed(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'seerr/settings.json'
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({'main': {'apiKey': 'unit-key'}}))
            replies = []
            for name, root in [('radarr', '/media/movies'), ('sonarr', '/media/tv')]:
                xml = Path(folder) / name / 'config.xml'
                xml.parent.mkdir()
                xml.write_text('<Config><ApiKey>unit-arr-key</ApiKey></Config>')
                replies.extend([[], {'profiles': [{'id': 3, 'name': 'HD-1080p'}],
                                     'rootFolders': [{'path': root}]}, {},
                                [{'hostname': name, 'isDefault': True}]])
            setup = auth.AuthSetup('admin', 'unit-only-password', configs=folder)
            with patch.object(auth, 'http', side_effect=replies) as http:
                setup.seerr_services()
            self.assertEqual(http.call_count, 8)
            self.assertEqual(http.call_args_list[2].args[1]['activeDirectory'], '/media/movies')
            self.assertEqual(http.call_args_list[6].args[1]['activeDirectory'], '/media/tv')

    def test_seerr_does_not_override_an_existing_nondefault_server(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'seerr/settings.json'
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({'main': {'apiKey': 'unit-key'}}))
            setup = auth.AuthSetup('admin', 'unit-only-password', configs=folder)
            with patch.object(auth, 'http', return_value=[{'id': 4, 'isDefault': False}]) as http:
                with self.assertRaises(auth.SetupError):
                    setup.seerr_services()
            self.assertEqual(http.call_count, 1)

    def test_http_client_can_create_cookie_session(self):
        self.assertTrue(hasattr(auth.client(), 'open'))

    def test_invalid_credentials_rejected_before_side_effects(self):
        for username, password in [('admin', 'short'), ('bad user', 'long-enough-password'), ('admin', 'long-password\nnext')]:
            with self.assertRaises(auth.SetupError):
                auth.AuthSetup(username, password)

    def test_download_client_preserves_schema_and_existing_preferences(self):
        schema = {'id': 0, 'implementation': 'QBittorrent', 'configContract': 'QBittorrentSettings',
                  'fields': [{'name': name, 'value': None} for name in
                             ('host', 'port', 'username', 'password', 'movieCategory', 'useSsl')], 'enable': False}
        original = copy.deepcopy(schema)
        result = auth.download_client(schema, 'admin', 'unit-only-password', 'radarr')
        values = {x['name']: x['value'] for x in result['fields']}
        self.assertEqual(schema, original)
        self.assertEqual(values['host'], 'qbittorrent')
        self.assertEqual(values['password'], 'unit-only-password')
        self.assertEqual(values['movieCategory'], 'radarr')
        self.assertTrue(result['enable'])
        self.assertFalse(result['removeCompletedDownloads'])
        self.assertNotIn('id', result)
        existing = dict(schema, id=7, priority=3, removeCompletedDownloads=False)
        updated = auth.download_client(schema, 'admin', 'new-unit-password', 'sonarr', existing)
        self.assertEqual(updated['id'], 7)
        self.assertEqual(updated['priority'], 3)
        self.assertEqual(existing['fields'], schema['fields'])

    def test_dozzle_generation_uses_stdin_and_saves_only_bcrypt(self):
        with tempfile.TemporaryDirectory() as folder:
            setup = auth.AuthSetup('admin', 'unit-only-password', configs=Path(folder) / 'configs', secrets=Path(folder) / 'secrets')
            document = ('users:\n  admin:\n    password: $2a$11$' + 'a' * 53 + '\n').encode()
            with patch.object(auth.os, 'chown', create=True), patch.object(auth.subprocess, 'run',
                    return_value=subprocess.CompletedProcess([], 0, document, b'')) as run:
                setup.prepare()
            self.assertNotIn(setup.password, ' '.join(run.call_args.args[0]))
            self.assertEqual(run.call_args.kwargs['input'], (setup.password + '\n').encode())
            self.assertEqual((setup.secrets / 'dozzle/users.yml').read_text().encode(), document)
            self.assertNotIn(setup.password.encode(), document)

    def test_private_updates_keep_previous_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'users.yml'
            auth.private_file(path, 'users: {}\n')
            auth.private_file(path, 'users: new\n')
            self.assertEqual(path.read_text(), 'users: new\n')
            self.assertEqual(Path(str(path) + '.bak').read_text(), 'users: {}\n')

    def test_qbit_native_sends_password_and_cookie_only_via_stdin(self):
        setup = auth.AuthSetup('admin', 'unit-only-password')
        setup.qb_cookie = 'SID=unit-private-cookie'
        reply = b'HTTP/1.1 204 No Content\r\nSet-Cookie: SID=new-cookie; HttpOnly\r\n\r\n\n204'
        with patch.object(auth.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, reply, b'')) as run:
            status, body = setup.qb_native('auth/login', {'username': 'admin', 'password': setup.password})
        self.assertEqual(status, 204)
        self.assertEqual(body, '')
        self.assertNotIn(setup.password, ' '.join(run.call_args.args[0]))
        self.assertNotIn('unit-private-cookie', ' '.join(run.call_args.args[0]))
        self.assertIn(setup.password.encode(), run.call_args.kwargs['input'])
        self.assertIn(b'unit-private-cookie', run.call_args.kwargs['input'])
        self.assertEqual(setup.qb_cookie, 'SID=new-cookie')

    def test_bazarr_only_changes_auth_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'bazarr/config/config.yaml'
            config.parent.mkdir(parents=True)
            config.write_text('auth:\n  apikey: ' + 'a' * 32 + '\n')
            setup = auth.AuthSetup('admin', 'unit-only-password', configs=folder)
            with patch.object(auth, 'http', side_effect=['', {'auth': {'type': 'form', 'username': 'admin'}}]) as http:
                setup.bazarr()
            self.assertEqual(set(http.call_args_list[0].args[1]),
                             {'settings-auth-type', 'settings-auth-username', 'settings-auth-password'})

    def test_qbit_session_cookie_name_is_not_assumed(self):
        setup = auth.AuthSetup('admin', 'unit-only-password')
        reply = b'HTTP/1.1 204 No Content\r\nSet-Cookie: QBT_SESSION=private-value; HttpOnly; Path=/\r\n\r\n\n204'
        with patch.object(auth.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, reply, b'')):
            self.assertEqual(setup.qb_native('auth/login', {'password': setup.password})[0], 204)
        self.assertEqual(setup.qb_cookie, 'QBT_SESSION=private-value')


if __name__ == '__main__':
    unittest.main()
