"""CI: download a deterministic local file using qBittorrent's torrent engine.

Fixture is served by an isolated web seed on the Compose network. No indexers,
public trackers, or third-party media are used.
"""
import hashlib
import http.cookiejar
import json
from pathlib import Path
import re
import subprocess
import time
import urllib.parse
import urllib.error
import urllib.request


def bencode(value):
    if isinstance(value, int):
        return b'i' + str(value).encode() + b'e'
    if isinstance(value, bytes):
        return str(len(value)).encode() + b':' + value
    if isinstance(value, list):
        return b'l' + b''.join(map(bencode, value)) + b'e'
    return b'd' + b''.join(bencode(k) + bencode(v) for k, v in sorted(value.items())) + b'e'


def main():
    fixture = Path('.test-output/fixture')
    fixture.mkdir(parents=True, exist_ok=True)
    content = bytes(range(256)) * 4096
    (fixture / 'sample.bin').write_bytes(content)
    piece_length = 16384
    pieces = b''.join(hashlib.sha1(content[i:i + piece_length]).digest()
                      for i in range(0, len(content), piece_length))
    info = {b'name': b'sample.bin', b'length': len(content),
            b'piece length': piece_length, b'pieces': pieces}
    torrent = bencode({b'info': info, b'url-list': [b'http://fixture:8000/sample.bin']})
    (fixture / 'sample.torrent').write_bytes(torrent)
    info_hash = hashlib.sha1(bencode(info)).hexdigest()
    subprocess.run(['docker', 'run', '-d', '--rm', '--name', 'fixture',
                    '--network', 'media-stack_default', '-v', f'{fixture.resolve()}:/fixture:ro',
                    'python:3.12-alpine', 'python', '-m', 'http.server', '8000',
                    '--directory', '/fixture'], check=True)
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def api(path, data=None):
        payload = urllib.parse.urlencode(data).encode() if data is not None else None
        request = urllib.request.Request('http://localhost:8080/api/v2/' + path, payload,
                                         {'Origin': 'http://localhost:8080', 'Referer': 'http://localhost:8080/'})
        return client.open(request, timeout=10).read().strip()

    for attempt in range(90):
        try:
            logs = subprocess.check_output(['docker', 'logs', 'qbittorrent'], stderr=subprocess.STDOUT).decode()
            passwords = re.findall(r'temporary password is provided for this session:\s*(\S+)', logs)
            if passwords:
                response = api('auth/login', {'username': 'admin', 'password': passwords[-1]})
                # qBittorrent >=5.2 returns 204 with an empty body on success.
                if response in (b'Ok.', b'') and isinstance(json.loads(api('app/preferences')), dict):
                    break
                if attempt < 10:
                    print('Login response:', repr(response), flush=True)
        except (OSError, urllib.error.URLError) as error:
            if attempt < 10:
                print('Waiting for WebUI:', type(error).__name__, str(error)[:160], flush=True)
        time.sleep(2)
    else:
        raise RuntimeError('qBittorrent did not become ready')
    api('app/setPreferences', {'json': json.dumps({'current_network_interface': '',
        'listen_port': 6881, 'dht': False, 'pex': False, 'lsd': False})})
    api('torrents/add', {'urls': 'http://fixture:8000/sample.torrent', 'savepath': '/media/downloads', 'paused': 'false'})
    for _ in range(90):
        rows = json.loads(api('torrents/info?hashes=' + info_hash))
        if rows and rows[0]['progress'] == 1:
            downloaded = Path('.test-output/media/downloads/sample.bin').read_bytes()
            assert downloaded == content, 'Downloaded file differs from fixture'
            print('PASS: torrent engine completed local fixture without VPN; bytes verified.')
            return
        time.sleep(2)
    raise RuntimeError('Local torrent fixture did not finish without VPN')


if __name__ == '__main__':
    main()
