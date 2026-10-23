"""CI: transfer a generated fixture between two isolated BitTorrent peers.

No tracker, public DHT, indexer, web seed or third-party media is used.
"""
import hashlib
import http.cookiejar
import json
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request


def bencode(value):
    if isinstance(value, int):
        return b'i' + str(value).encode() + b'e'
    if isinstance(value, bytes):
        return str(len(value)).encode() + b':' + value
    if isinstance(value, list):
        return b'l' + b''.join(map(bencode, value)) + b'e'
    return b'd' + b''.join(bencode(k) + bencode(v) for k, v in sorted(value.items())) + b'e'


class Client:
    def __init__(self, port, container):
        self.base = f'http://localhost:{port}'
        self.container = container
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def api(self, path, data=None, multipart=None):
        headers = {'Origin': self.base, 'Referer': self.base + '/'}
        payload = urllib.parse.urlencode(data).encode() if data is not None else None
        if multipart:
            boundary = 'MediaStackFixtureBoundary'
            fields = {'savepath': '/media/downloads', 'paused': 'false', 'stopped': 'false'}
            payload = b''
            for key, value in fields.items():
                payload += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
            payload += f'--{boundary}\r\nContent-Disposition: form-data; name="torrents"; filename="fixture.torrent"\r\nContent-Type: application/x-bittorrent\r\n\r\n'.encode()
            payload += multipart + f'\r\n--{boundary}--\r\n'.encode()
            headers['Content-Type'] = 'multipart/form-data; boundary=' + boundary
        request = urllib.request.Request(self.base + '/api/v2/' + path, payload, headers)
        with self.opener.open(request, timeout=10) as response:
            return response.read().strip()

    def ready(self):
        for attempt in range(60):
            try:
                logs = subprocess.check_output(['docker', 'logs', self.container], stderr=subprocess.STDOUT).decode()
                passwords = re.findall(r'temporary password is provided for this session:\s*(\S+)', logs)
                if passwords:
                    self.api('auth/login', {'username': 'admin', 'password': passwords[-1]})
                    if isinstance(json.loads(self.api('app/preferences')), dict):
                        return
            except (OSError, ValueError, urllib.error.URLError) as error:
                if attempt < 3:
                    print('Waiting for', self.container, type(error).__name__, flush=True)
            time.sleep(1)
        raise RuntimeError(self.container + ' WebUI did not become ready')


def main():
    fixture = Path('.test-output/fixture')
    fixture.mkdir(parents=True, exist_ok=True)
    fixture.chmod(0o777)
    content = bytes(range(256)) * 4096
    (fixture / 'sample.bin').write_bytes(content)
    piece_length = 16384
    pieces = b''.join(hashlib.sha1(content[i:i + piece_length]).digest()
                      for i in range(0, len(content), piece_length))
    info = {b'name': b'sample.bin', b'length': len(content), b'private': 1,
            b'piece length': piece_length, b'pieces': pieces}
    torrent = bencode({b'info': info})
    info_hash = hashlib.sha1(bencode(info)).hexdigest()
    subprocess.run(['docker', 'run', '-d', '--name', 'torrent-seed',
                    '--network', 'media-stack_default', '-p', '127.0.0.1:8081:8081',
                    '-e', 'VPN_ENABLED=false', '-e', 'PUID=1000', '-e', 'PGID=1000',
                    '-e', 'WEBUI_PORTS=8081/tcp', '-v', f'{fixture.resolve()}:/media/downloads',
                    'ghcr.io/hotio/qbittorrent:latest'], check=True)
    try:
        receiver, seed = Client(8080, 'qbittorrent'), Client(8081, 'torrent-seed')
        receiver.ready()
        seed.ready()
        for client in (receiver, seed):
            client.api('app/setPreferences', {'json': json.dumps({'current_network_interface': '',
                'listen_port': 6881, 'random_port': False, 'dht': False, 'pex': False, 'lsd': False})})
            client.api('torrents/add', multipart=torrent)
        for _ in range(45):
            rows = json.loads(seed.api('torrents/info?hashes=' + info_hash))
            if rows and rows[0]['progress'] == 1:
                break
            time.sleep(1)
        else:
            raise RuntimeError('Fixture seed did not finish its local file check')
        inspection = json.loads(subprocess.check_output(['docker', 'inspect', 'torrent-seed']))[0]
        seed_ip = inspection['NetworkSettings']['Networks']['media-stack_default']['IPAddress']
        receiver.api('torrents/addPeers', {'hashes': info_hash, 'peers': seed_ip + ':6881'})
        for attempt in range(60):
            rows = json.loads(receiver.api('torrents/info?hashes=' + info_hash))
            if rows and rows[0]['progress'] == 1:
                downloaded = Path('.test-output/media/downloads/sample.bin').read_bytes()
                assert downloaded == content, 'Downloaded file differs from fixture'
                print('PASS: local BitTorrent peer transfer completed without VPN; bytes verified.')
                return
            if attempt % 10 == 0:
                print('Fixture transfer:', [{key: item.get(key) for key in ('name', 'state', 'progress', 'num_seeds')} for item in rows], flush=True)
            time.sleep(1)
        raise RuntimeError('Local torrent fixture did not finish without VPN')
    except Exception:
        print(subprocess.check_output(['docker', 'logs', 'torrent-seed'], stderr=subprocess.STDOUT).decode(), flush=True)
        raise
    finally:
        subprocess.run(['docker', 'rm', '-f', 'torrent-seed'], check=False)


if __name__ == '__main__':
    main()

