import http.cookiejar
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('portal_app', ROOT / 'portal/app.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
cf_spec = importlib.util.spec_from_file_location('cloudflare_setup', ROOT / 'portal/cloudflare_setup.py')
cf = importlib.util.module_from_spec(cf_spec)
cf_spec.loader.exec_module(cf)


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        app.LOGIN_ATTEMPTS.clear()
        self.password_patch = patch.object(app, 'PASSWORD', 'unit-test-password-only')
        self.password_patch.start()

    def tearDown(self):
        self.password_patch.stop()

    def call(self, path, body=None, csrf=None, origin=None):
        headers = {}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        if csrf:
            headers['X-CSRF-Token'] = csrf
        if origin:
            headers['Origin'] = origin
        req = urllib.request.Request(self.base + path, json.dumps(body).encode() if body is not None else None, headers)
        try:
            response = self.client.open(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        return response.code, json.load(response), response.headers

    def login(self):
        status, session, headers = self.call('/api/login', {'username': 'admin', 'password': 'unit-test-password-only'})
        self.assertEqual(status, 200)
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        return session['csrf']

    def test_private_api_requires_login(self):
        self.assertEqual(self.call('/api/status')[0], 401)
        self.assertEqual(self.call('/api/search?q=test')[0], 401)

    def test_session_and_invalid_password(self):
        self.assertEqual(self.call('/api/login', {'username': 'admin', 'password': 'wrong'})[0], 401)
        self.login()
        self.assertEqual(self.call('/api/session')[0], 200)

    def test_cross_origin_login_is_blocked(self):
        self.assertEqual(self.call('/api/login', {'username': 'admin', 'password': 'unit-test-password-only'}, origin='https://attacker.example')[0], 403)

    def test_mutations_require_csrf(self):
        self.login()
        with patch.object(app, 'seerr') as service:
            self.assertEqual(self.call('/api/request', {'id': 123, 'type': 'movie'})[0], 403)
            service.assert_not_called()

    def test_request_is_allowlisted_not_arbitrary_proxy(self):
        csrf = self.login()
        with patch.object(app, 'seerr', return_value={'id': 99, 'status': 2}) as service:
            status, result, _ = self.call('/api/request', {'id': 123, 'type': 'movie', 'apiKey': 'must-not-forward', 'url': 'http://attacker'}, csrf)
            self.assertEqual(status, 201)
            service.assert_called_once_with('request', {'mediaType': 'movie', 'mediaId': 123})
            self.assertNotIn('apiKey', json.dumps(result))
        self.assertEqual(self.call('/api/request', {'id': -1, 'type': 'movie'}, csrf)[0], 400)

    def test_logout_clears_session(self):
        csrf = self.login()
        self.assertEqual(self.call('/api/logout', {}, csrf)[0], 200)
        self.assertEqual(self.call('/api/session')[0], 401)

    def test_login_rate_limit(self):
        for _ in range(10):
            self.call('/api/login', {'username': 'admin', 'password': 'wrong'})
        self.assertEqual(self.call('/api/login', {'username': 'admin', 'password': 'wrong'})[0], 429)

    def test_forged_and_expired_sessions_fail(self):
        token = app.new_session()
        self.assertTrue(app.valid_session(token))
        self.assertFalse(app.valid_session(token + '0'))
        with patch.object(app.time, 'time', return_value=time.time() + app.SESSION_AGE + 1):
            self.assertFalse(app.valid_session(token))


class SnapshotTests(unittest.TestCase):
    def test_qbittorrent_204_login_is_supported(self):
        with patch.object(app, 'request_json', side_effect=[app.ServiceError('HTTP 401'), '', {'dl_info_speed': 42}]) as request:
            self.assertEqual(app.qb('transfer/info')['dl_info_speed'], 42)
            self.assertEqual(request.call_count, 3)

    def test_service_failure_is_visible_without_exposing_secrets(self):
        with patch.object(app, 'arr', side_effect=app.ServiceError('configuracao inicial pendente')), patch.object(app, 'qb', return_value=[]), patch.object(app, 'seerr', side_effect=app.ServiceError('conclua o setup do Seerr')), patch.object(app, 'disk_stats', return_value={'total': 100, 'used': 40, 'free': 60, 'percent': 40}):
            # Transfer response has its own dict; handle each endpoint separately.
            with patch.object(app, 'qb', side_effect=lambda path: [] if path.startswith('torrents/') else {}):
                result = app.build_snapshot()
        self.assertEqual(result['library'], [])
        self.assertEqual(result['disk']['free'], 60)
        self.assertFalse(next(item for item in result['services'] if item['name'] == 'radarr')['ok'])
        self.assertNotIn('apiKey', json.dumps(result))


class FakeCloudflared(cf.Cloudflared):
    def __init__(self, folder, fail_dns=False, missing_cert=False):
        super().__init__(folder)
        self.calls = []
        self.fail_dns = fail_dns
        self.missing_cert = missing_cert

    def __call__(self, *args):
        self.calls.append(args)
        if args == ('tunnel', 'login') and not self.missing_cert:
            (self.root / 'login-home/.cloudflared/cert.pem').write_text('fake-account-certificate')
        if 'create' in args:
            target = Path(args[args.index('--credentials-file') + 1])
            target.write_text(json.dumps({'TunnelID': '12345678-1234-4234-8234-123456789abc',
                                          'AccountTag': 'account', 'TunnelSecret': 'fake-tunnel-secret'}))
        if 'route' in args and self.fail_dns:
            raise RuntimeError('Hostname ja ocupado; nenhum DNS substituido.')


class CloudflareTests(unittest.TestCase):
    def test_browser_login_precedes_hostname_prompt_and_provisions(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = FakeCloudflared(folder)
            def prompt(_):
                self.assertEqual(runner.calls, [('tunnel', 'login')])
                return 'sala.exemplo.com.br'
            result = cf.provision('', folder, runner=runner, prompt=prompt)
            self.assertEqual(result['hostname'], 'sala.exemplo.com.br')
            self.assertEqual(result['mode'], 'browser-login')
            self.assertIn('route', runner.calls[-1])
            self.assertNotIn('--overwrite-dns', str(runner.calls))
            config = json.loads((Path(folder) / 'runtime/config.yml').read_text())
            self.assertEqual(config['ingress'][0]['service'], 'http://dashboard:8080')
            self.assertEqual(config['ingress'][0]['hostname'], 'sala.exemplo.com.br')
            self.assertEqual(config['credentials-file'], '/etc/cloudflared/credentials.json')
            self.assertFalse((Path(folder) / 'runtime/cert.pem').exists())
            self.assertNotIn('fake-account-certificate', json.dumps(config))

    def test_reinstall_reuses_tunnel_without_login_or_remote_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = FakeCloudflared(folder)
            first = cf.provision('sala.exemplo.com', folder, runner=runner)
            runner.calls.clear()
            self.assertEqual(cf.provision('sala.exemplo.com', folder, runner=runner), first)
            self.assertEqual(runner.calls, [])

    def test_cached_account_authorization_is_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'login-home/.cloudflared').mkdir(parents=True)
            (root / 'login-home/.cloudflared/cert.pem').write_text('cached')
            runner = FakeCloudflared(folder)
            cf.provision('sala.exemplo.com', folder, runner=runner)
            self.assertFalse(any('login' in args for args in runner.calls))

    def test_dns_failure_preserves_live_configuration_and_can_be_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = FakeCloudflared(folder)
            cf.provision('primeiro.exemplo.com', folder, runner=runner)
            config = Path(folder) / 'runtime/config.yml'
            before = config.read_text()
            runner.fail_dns = True
            with self.assertRaises(RuntimeError):
                cf.provision('segundo.exemplo.com', folder, runner=runner)
            self.assertEqual(config.read_text(), before)
            runner.calls.clear()
            runner.fail_dns = False
            cf.provision('segundo.exemplo.com', folder, runner=runner)
            self.assertFalse(any('create' in args for args in runner.calls))
            self.assertEqual(json.loads(config.read_text())['ingress'][0]['hostname'], 'segundo.exemplo.com')

    def test_login_without_certificate_stops_before_create_or_dns(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = FakeCloudflared(folder, missing_cert=True)
            with self.assertRaises(RuntimeError):
                cf.provision('sala.exemplo.com', folder, runner=runner)
            self.assertEqual(runner.calls, [('tunnel', 'login')])

    def test_legacy_tunnel_is_preserved_without_api_prompt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'tunnel_token').write_text('legacy-token')
            metadata = {'hostname': 'sala.exemplo.com', 'tunnelId': 'legacy-id'}
            (root / 'tunnel.json').write_text(json.dumps(metadata))
            runner = FakeCloudflared(folder)
            self.assertEqual(cf.provision('sala.exemplo.com', folder, runner=runner), metadata)
            self.assertEqual(runner.calls, [])
            self.assertEqual((root / 'runtime/tunnel_token').read_text(), 'legacy-token')
            self.assertEqual(json.loads((root / 'runtime/config.yml').read_text()),
                             {'token-file': '/etc/cloudflared/tunnel_token'})

    def test_reject_url_instead_of_hostname_before_login(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = FakeCloudflared(folder)
            with self.assertRaises(ValueError):
                cf.provision('https://sala.exemplo.com', folder, runner=runner)
            self.assertEqual(runner.calls, [])

    def test_truenas_runner_uses_isolated_docker_home_and_streams_login_link(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = cf.Cloudflared(folder, docker=True)
            with patch.object(cf.subprocess, 'run') as run:
                runner('tunnel', 'login')
            command = run.call_args[0][0]
            self.assertEqual(command[:4], ['docker', 'run', '--rm', '--user'])
            self.assertIn('HOME=/secrets/login-home', command)
            self.assertEqual(command[-2:], ['tunnel', 'login'])
            self.assertNotIn('capture_output', run.call_args[1])
            self.assertEqual(runner.path(Path(folder) / 'login-home/.cloudflared/cert.pem'),
                             '/secrets/login-home/.cloudflared/cert.pem')

    def test_corrupt_saved_credentials_do_not_silently_replace_tunnel(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = FakeCloudflared(folder)
            cf.provision('sala.exemplo.com', folder, runner=runner)
            credentials = Path(folder) / 'runtime/credentials.json'
            value = json.loads(credentials.read_text())
            value['TunnelID'] = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
            credentials.write_text(json.dumps(value))
            runner.calls.clear()
            with self.assertRaises(ValueError):
                cf.provision('sala.exemplo.com', folder, runner=runner)
            self.assertEqual(runner.calls, [])


if __name__ == '__main__':
    unittest.main()
