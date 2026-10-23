"""Real rclone + HTTP archive smoke test using local fixtures, never cloud data."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('archive', ROOT / 'portal/archive.py')
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


class Bot(BaseHTTPRequestHandler):
    messages = []
    def log_message(self, *_): pass
    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        path = Path(archive.urllib.parse.unquote(archive.urllib.parse.urlsplit(data['document']).path))
        Bot.messages.append(data)
        body = json.dumps({'ok': True, 'result': {'message_id': len(Bot.messages), 'document': {'file_size': path.stat().st_size}}}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LocalDrive(archive.Drive):
    def run(self, *args):
        result = super().run(*args)
        if args[0] == 'lsjson':
            data = json.loads(result)
            # Only Google Drive has Drive file IDs; the local fixture injects one.
            data['ID'] = 'fixture-id'
            return json.dumps(data).encode()
        return result


with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    media = root / 'media'
    media.mkdir()
    sources = {'photos/family & friends.jpg': b'preserve photo bytes',
               'music/track.flac': b'preserve music bytes', 'movies/large.mp4': os.urandom(16384)}
    for name, content in sources.items():
        path = media / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        os.utime(path, (10, 10))
    config_file = root / 'rclone.conf'
    config_file.write_text('[sala]\ntype = local\n')
    config = {'media_root': str(media), 'rclone_config': str(config_file),
              'drive_remote': 'sala:' + (root / 'remote').as_posix(),
              'telegram_bot_token': 'fixture-not-a-token', 'telegram_chat_id': '-1001234567890'}
    catalog = archive.Catalog(root / 'state')
    archive.TELEGRAM_LIMIT = 8192
    catalog.scan(media, now=1000)
    assert catalog.scan(media, now=1121) == 3
    bot = ThreadingHTTPServer(('127.0.0.1', 0), Bot)
    threading.Thread(target=bot.serve_forever, daemon=True).start()
    telegram = archive.Telegram(config)
    telegram.base = 'http://127.0.0.1:' + str(bot.server_port) + '/botfixture/'
    drive = LocalDrive(config)
    worker = archive.Worker(config, catalog, drive, telegram)
    try:
        while worker.process_one('drive'): pass
        while worker.process_one('telegram'): pass
        listing = catalog.listing()
        assert listing['summary']['drive']['verified']['count'] == 3, listing
        assert listing['summary']['telegram']['uploaded']['count'] == 2, listing
        assert listing['summary']['telegram']['too_large']['count'] == 1, listing
        assert len(Bot.messages) == 2
        for item in listing['items']:
            copied = Path(item['drive_path'].split(':', 1)[1])
            assert copied.read_bytes() == sources[item['relative_path']]
            assert (media / item['relative_path']).read_bytes() == sources[item['relative_path']]
        drive.backup_catalog(catalog)
        manifest = root / 'remote/catalog/latest.json'
        assert len(json.loads(manifest.read_text())['items']) == 3
        server = ThreadingHTTPServer(('127.0.0.1', 0), archive.make_handler(worker, 'fixture-private-key'))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            item = listing['items'][0]
            request = urllib.request.Request('http://127.0.0.1:' + str(server.server_port) + '/file/' + item['code'],
                headers={'Authorization': 'Bearer fixture-private-key', 'Range': 'bytes=2-7'})
            with urllib.request.urlopen(request, timeout=15) as response:
                assert response.status == 206
                assert response.read() == sources[item['relative_path']][2:8]
        finally:
            server.shutdown()
            server.server_close()
    finally:
        bot.shutdown()
        bot.server_close()
print('PASS: real rclone copy, MD5 verification, independent Telegram file routing, catalog backup and ranged streaming. Originals unchanged.')
