from html.parser import HTMLParser
import http.client
import importlib.util
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'portal'))
spec = importlib.util.spec_from_file_location('public_portal_app', ROOT / 'portal/app.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class PageLinks(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids = set()
        self.links = []
        self.resources = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get('id'):
            self.ids.add(values['id'])
        if tag == 'a' and values.get('href'):
            self.links.append(values['href'])
        if tag in ('script', 'img', 'iframe'):
            self.resources.append(values.get('src', ''))
        if tag == 'link':
            self.resources.append(values.get('href', ''))


class PublicPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def fetch(self, path, method='GET'):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_home_privacy_terms_are_public_and_do_not_query_private_services(self):
        with patch.object(app, 'snapshot') as snapshot, patch.object(app.archive_client, 'call') as archive, patch.object(app, 'seerr') as seerr:
            for route in ('/', '/privacy', '/privacy/', '/terms', '/terms/'):
                with self.subTest(route=route):
                    status, headers, body = self.fetch(route)
                    self.assertEqual(status, 200)
                    self.assertIn('text/html', headers['Content-Type'])
                    self.assertIn('charset=utf-8', headers['Content-Type'])
                    self.assertNotIn('Set-Cookie', headers)
                    self.assertIn('frame-ancestors', headers['Content-Security-Policy'])
                    html = body.decode('utf-8')
                    self.assertIn('Pobreflix', html)
                    self.assertNotIn('apps.googleusercontent.com', html)
                    self.assertNotIn('refresh_token', html)
                    self.assertNotIn('GOCSPX-', html)
            snapshot.assert_not_called()
            archive.assert_not_called()
            seerr.assert_not_called()

    def test_dashboard_login_stays_available_separately(self):
        for route in ('/app', '/app/'):
            status, headers, body = self.fetch(route)
            self.assertEqual(status, 200)
            self.assertIn(b'id="login-form"', body)
            self.assertIn(b'href="/privacy"', body)
            self.assertIn(b'href="/terms"', body)
            self.assertNotIn('Set-Cookie', headers)
        self.assertIn(b'href="/app"', self.fetch('/')[2])

    def test_public_pages_have_no_tracking_or_external_resources_and_valid_links(self):
        pages = {route: PageLinks(self.fetch(route)[2].decode()) for route in ('/', '/privacy', '/terms')}
        for route, page in pages.items():
            with self.subTest(route=route):
                self.assertTrue(page.resources)
                for resource in page.resources:
                    self.assertEqual(resource, '/public.css')
                for href in page.links:
                    link = urlsplit(href)
                    if link.scheme or link.netloc:
                        continue
                    target = link.path or route
                    self.assertIn(target, app.PUBLIC_FILES)
                    if link.fragment:
                        self.assertIn(link.fragment, pages[target].ids)
        status, headers, css = self.fetch('/public.css')
        self.assertEqual(status, 200)
        self.assertIn('text/css', headers['Content-Type'])
        self.assertNotIn(b'@import', css)

    def test_public_head_returns_headers_and_no_body(self):
        for route in ('/', '/privacy', '/terms', '/app', '/public.css', '/health'):
            with self.subTest(route=route):
                status, headers, body = self.fetch(route, 'HEAD')
                self.assertEqual(status, 200)
                self.assertGreater(int(headers['Content-Length']), 0)
                self.assertEqual(body, b'')
                self.assertEqual(headers['Content-Length'], self.fetch(route)[1]['Content-Length'])

    def test_public_allowlist_never_exposes_private_api_or_arbitrary_files(self):
        for route in ('/api/status', '/api/session', '/api/archive', '/api/archive/export', '/api/search?q=test', '/.env', '/.secrets/rclone/rclone.conf', '/app.py', '/privacy.html', '/../app.py', '/%2e%2e/app.py'):
            with self.subTest(route=route):
                self.assertEqual(self.fetch(route)[0], 401)
                self.assertEqual(self.fetch(route, 'HEAD')[0], 405)

    def test_privacy_discloses_actual_integrations_controls_and_contact(self):
        html = self.fetch('/privacy')[2].decode()
        for disclosure in ('drive.file', 'Telegram', 'Cloudflare', 'myaccount.google.com/connections', 'Uso Limitado', 'mailto:alvarofernandesalves@gmail.com'):
            self.assertIn(disclosure, html)
        self.assertNotIn('TODO', html)
        self.assertNotIn('example.com', html)


if __name__ == '__main__':
    unittest.main()
