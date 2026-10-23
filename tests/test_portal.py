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


class CloudflareTests(unittest.TestCase):
    def fake(self, records=None):
        self.calls = []
        def call(method, path, body=None):
            self.calls.append((method, path, body))
            if path.startswith('zones?'):
                return [{'id': 'zone', 'name': 'exemplo.com.br', 'account': {'id': 'account'}}]
            if 'dns_records?' in path:
                return records or []
            if 'cfd_tunnel?' in path:
                return []
            if path.endswith('/cfd_tunnel'):
                return {'id': 'new-tunnel'}
            if path.endswith('/configurations'):
                return {'config': {}} if method == 'GET' else {}
            if path.endswith('/token'):
                return 'connector-token-test-only'
            return {}
        return call

    def test_provision_subdomain_and_keep_api_token_out_of_saved_files(self):
        with tempfile.TemporaryDirectory() as folder:
            result = cf.provision('sala.exemplo.com.br', 'api-token-test-only', folder, self.fake())
            self.assertEqual(result['hostname'], 'sala.exemplo.com.br')
            self.assertEqual((Path(folder) / 'tunnel_token').read_text(), 'connector-token-test-only')
            self.assertNotIn('api-token-test-only', (Path(folder) / 'tunnel.json').read_text())
        write = next(body for method, path, body in self.calls if method == 'PUT')
        self.assertEqual(write['config']['ingress'][0]['service'], 'http://dashboard:8080')

    def test_existing_unrelated_dns_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                cf.provision('sala.exemplo.com.br', 'test', folder, self.fake([{'type': 'A', 'content': '192.0.2.1'}]))
        self.assertFalse(any(method in ('PUT', 'POST') for method, _, _ in self.calls))

    def test_reject_url_instead_of_hostname(self):
        with self.assertRaises(ValueError):
            cf.provision('https://sala.exemplo.com', 'test', '/unused', self.fake())
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
