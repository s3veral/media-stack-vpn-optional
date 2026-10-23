"""Local media portal. Standard library only; credentials stay on the server."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.cookiejar
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

STATIC = Path(__file__).parent / 'static'
CONFIGS = Path(os.getenv('CONFIG_ROOT', '/configs'))
PASSWORD = os.getenv('DASHBOARD_PASSWORD', '')
USERNAME = os.getenv('ADMIN_USER', 'admin')
PUBLIC_HOSTNAME = os.getenv('PUBLIC_HOSTNAME', '').lower()
SECRET = hashlib.sha256(('portal-session:' + PASSWORD).encode()).digest()
SESSION_AGE = 12 * 60 * 60
SNAPSHOT_LOCK = threading.Lock()
CACHE = {'at': 0, 'data': None}
RATE_LOCK = threading.Lock()
LOGIN_ATTEMPTS = {}
QB_LOCK = threading.Lock()
QB_CLIENT = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


class ServiceError(Exception):
    pass


def request_json(url, method='GET', payload=None, headers=None, client=None, form=False):
    headers = dict(headers or {})
    data = None
    if payload is not None:
        data = (urllib.parse.urlencode(payload) if form else json.dumps(payload)).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
    req = urllib.request.Request(url, data, headers, method=method)
    try:
        with (client or urllib.request.build_opener()).open(req, timeout=8) as response:
            raw = response.read(12 * 1024 * 1024)
        try:
            return json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return raw.decode('utf-8', errors='replace')
    except urllib.error.HTTPError as error:
        raise ServiceError(f'HTTP {error.code}') from None
    except (OSError, urllib.error.URLError, TimeoutError):
        raise ServiceError('sem conexao') from None


def arr(app, path):
    try:
        key = ET.parse(CONFIGS / app / 'config.xml').findtext('ApiKey')
    except (OSError, ET.ParseError):
        key = None
    if not key:
        raise ServiceError('configuracao inicial pendente')
    port = 7878 if app == 'radarr' else 8989
    return request_json(f'http://{app}:{port}/api/v3/{path}', headers={'X-Api-Key': key})


def seerr(path, payload=None):
    try:
        key = json.loads((CONFIGS / 'seerr' / 'settings.json').read_text())['main']['apiKey']
    except (OSError, ValueError, KeyError):
        raise ServiceError('conclua o setup do Seerr') from None
    return request_json('http://seerr:5055/api/v1/' + path, 'POST' if payload is not None else 'GET',
                        payload, {'X-Api-Key': key})


def qb(path, payload=None):
    with QB_LOCK:
        url = 'http://qbittorrent:8080/api/v2/'
        for attempt in range(2):
            try:
                return request_json(url + path, 'POST' if payload is not None else 'GET', payload,
                                    {'Origin': 'http://qbittorrent:8080', 'Referer': 'http://qbittorrent:8080/'}, client=QB_CLIENT, form=True)
            except ServiceError as error:
                if str(error) not in ('HTTP 401', 'HTTP 403') or attempt:
                    raise
                result = request_json(url + 'auth/login', 'POST',
                                      {'username': USERNAME, 'password': os.getenv('ADMIN_PASS', '')},
                                      {'Origin': 'http://qbittorrent:8080', 'Referer': 'http://qbittorrent:8080/'}, client=QB_CLIENT, form=True)
                if result.strip() not in ('Ok.', ''):
                    raise ServiceError('login do qBittorrent pendente')


def safe_result(call):
    try:
        return {'ok': True, 'data': call()}
    except ServiceError as error:
        return {'ok': False, 'error': str(error)}
    except (ValueError, TypeError, KeyError, ET.ParseError):
        return {'ok': False, 'error': 'resposta inesperada do servico'}


def disk_stats():
    usage = shutil.disk_usage(os.getenv('MEDIA_ROOT', '/media'))
    return {'total': usage.total, 'used': usage.used, 'free': usage.free,
            'percent': round(100 * usage.used / usage.total, 1)}


def media_card(item):
    media_type = item.get('mediaType', 'movie')
    return {'id': item['id'], 'type': media_type,
            'title': item.get('title') or item.get('name') or 'Sem titulo',
            'year': (item.get('releaseDate') or item.get('firstAirDate') or '')[:4],
            'overview': item.get('overview', ''), 'poster': item.get('posterPath'),
            'score': item.get('voteAverage', 0), 'status': (item.get('mediaInfo') or {}).get('status', 1)}


def build_snapshot():
    tasks = {
        'torrents': lambda: qb('torrents/info?sort=added_on&reverse=true'),
        'transfer': lambda: qb('transfer/info'),
        'radarr': lambda: arr('radarr', 'movie'),
        'sonarr': lambda: arr('sonarr', 'series'),
        'movieQueue': lambda: arr('radarr', 'queue?page=1&pageSize=50&includeMovie=true'),
        'tvQueue': lambda: arr('sonarr', 'queue?page=1&pageSize=50&includeSeries=true&includeEpisode=true'),
        'requests': lambda: seerr('request?take=8&sort=added&sortDirection=desc'),
        'disk': disk_stats,
    }
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {key: pool.submit(safe_result, fn) for key, fn in tasks.items()}
        results = {key: future.result() for key, future in futures.items()}
    data = lambda key, default: results[key].get('data', default)
    torrents = data('torrents', [])
    movies = data('radarr', [])
    series = data('sonarr', [])
    library = []
    for item in movies:
        library.append({'id': item['id'], 'type': 'movie', 'title': item['title'],
                        'year': item.get('year'), 'available': bool(item.get('hasFile')),
                        'size': item.get('sizeOnDisk', 0), 'monitored': item.get('monitored', False)})
    for item in series:
        stats = item.get('statistics', {})
        library.append({'id': item['id'], 'type': 'tv', 'title': item['title'],
                        'year': item.get('year'), 'available': stats.get('episodeFileCount', 0) > 0,
                        'size': stats.get('sizeOnDisk', 0), 'episodes': stats.get('episodeFileCount', 0),
                        'episodeTotal': stats.get('episodeCount', 0), 'monitored': item.get('monitored', False)})
    library.sort(key=lambda item: item['title'].casefold())
    downloads = [{key: item.get(key) for key in ('hash', 'name', 'progress', 'state', 'dlspeed',
                 'upspeed', 'eta', 'size', 'downloaded', 'num_seeds', 'num_leechs', 'category', 'added_on')}
                 for item in torrents[:100]]
    queue = []
    for key, media_type in [('movieQueue', 'movie'), ('tvQueue', 'tv')]:
        for item in data(key, {}).get('records', []):
            queue.append({'id': item.get('id'), 'type': media_type, 'title': item.get('title', ''),
                          'status': item.get('status'), 'size': item.get('size', 0),
                          'sizeLeft': item.get('sizeleft', 0), 'timeLeft': item.get('timeleft'),
                          'trackedStatus': item.get('trackedDownloadStatus')})
    recent_requests = []
    def request_card(item):
        media = item.get('media', {})
        media_type = media.get('mediaType', 'movie')
        tmdb_id = media.get('tmdbId')
        card = safe_result(lambda: seerr(f'{media_type}/{int(tmdb_id)}')) if tmdb_id else {}
        detail = card.get('data', {})
        return {'id': item['id'], 'title': detail.get('title') or detail.get('name') or f'TMDB {tmdb_id}',
                'type': media_type, 'status': item.get('status'), 'mediaStatus': media.get('status'),
                'createdAt': item.get('createdAt')}
    with ThreadPoolExecutor(max_workers=4) as pool:
        recent_requests = list(pool.map(request_card, data('requests', {}).get('results', [])))
    transfer = data('transfer', {})
    return {'generatedAt': int(time.time()), 'vpn': os.getenv('VPN_ENABLED', 'false') == 'true',
            'downloads': downloads, 'downloadTotal': len(torrents), 'queue': queue,
            'library': library, 'requests': recent_requests,
            'disk': data('disk', None), 'transfer': {key: transfer.get(key, 0) for key in
              ('dl_info_speed', 'up_info_speed', 'dl_info_data', 'up_info_data', 'connection_status')},
            'services': [{'name': key, 'ok': value['ok'], 'error': value.get('error')}
                         for key, value in results.items()],
            'stats': {'movies': len(movies), 'moviesAvailable': sum(bool(item.get('hasFile')) for item in movies),
                      'series': len(series), 'episodes': sum(item.get('statistics', {}).get('episodeFileCount', 0) for item in series),
                      'active': sum(item.get('dlspeed', 0) > 0 for item in torrents)}}


def snapshot():
    with SNAPSHOT_LOCK:
        if time.monotonic() - CACHE['at'] > 10 or CACHE['data'] is None:
            CACHE['data'] = build_snapshot()
            CACHE['at'] = time.monotonic()
        return CACHE['data']


def sign(value):
    return hmac.new(SECRET, value.encode(), hashlib.sha256).hexdigest()


def new_session():
    value = f'{int(time.time())}.{secrets.token_hex(16)}'
    return value + '.' + sign(value)


def valid_session(token):
    try:
        timestamp, nonce, signature = token.split('.')
        age = time.time() - int(timestamp)
        return 0 <= age < SESSION_AGE and len(nonce) == 32 and hmac.compare_digest(signature, sign(timestamp + '.' + nonce))
    except (ValueError, AttributeError):
        return False


class Handler(BaseHTTPRequestHandler):
    server_version = 'MediaPortal'

    def log_message(self, format, *args):
        # Avoid logging searches, credentials, session tokens or API payloads.
        pass

    def send(self, status, body, content_type='application/json; charset=utf-8', cookie=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' https://image.tmdb.org; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(body)

    def session(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            token = cookie['media_session'].value
        except (KeyError, ValueError):
            return ''
        return token if valid_session(token) else ''

    def json_body(self):
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            raise ValueError('Use application/json.')
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 16384:
                raise ValueError()
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, UnicodeDecodeError):
            raise ValueError('Corpo JSON invalido.') from None

    def origin_allowed(self):
        origin = self.headers.get('Origin')
        if not origin:
            return True
        parsed = urllib.parse.urlsplit(origin)
        if PUBLIC_HOSTNAME and parsed.netloc == PUBLIC_HOSTNAME:
            return parsed.scheme == 'https'
        if origin in os.getenv('DASHBOARD_ALLOWED_ORIGINS', '').split(','):
            return True
        return parsed.scheme == 'http' and parsed.netloc in ('localhost:8090', '127.0.0.1:8090')

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path == '/health':
            return self.send(200, {'ok': True})
        if path in ('/', '/style.css', '/app.js'):
            file = STATIC / ('index.html' if path == '/' else path[1:])
            return self.send(200, file.read_bytes(), mimetypes.guess_type(str(file))[0] or 'text/plain')
        token = self.session()
        if not token:
            return self.send(401, {'error': 'Entre para acessar o painel.'})
        try:
            if path == '/api/session':
                return self.send(200, {'username': USERNAME, 'csrf': sign('csrf:' + token)})
            if path == '/api/status':
                return self.send(200, snapshot())
            if path == '/api/search':
                query = urllib.parse.parse_qs(parsed.query).get('q', [''])[0].strip()[:120]
                if len(query) < 2:
                    return self.send(200, {'results': []})
                data = seerr('search?' + urllib.parse.urlencode({'query': query, 'language': 'pt-BR', 'page': 1}))
                cards = [media_card(item) for item in data.get('results', []) if item.get('mediaType') in ('movie', 'tv')]
                return self.send(200, {'results': cards})
            return self.send(404, {'error': 'Pagina nao encontrada.'})
        except ServiceError as error:
            return self.send(502, {'error': 'Servico indisponivel: ' + str(error)})
        except Exception:
            return self.send(500, {'error': 'Nao foi possivel atualizar. Verifique a configuracao dos servicos.'})

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if not self.origin_allowed():
            return self.send(403, {'error': 'Origem nao permitida.'})
        try:
            body = self.json_body()
            if path == '/api/login':
                address = self.client_address[0]
                with RATE_LOCK:
                    now = time.monotonic()
                    for key in list(LOGIN_ATTEMPTS):
                        LOGIN_ATTEMPTS[key] = [t for t in LOGIN_ATTEMPTS[key] if now - t < 60]
                        if not LOGIN_ATTEMPTS[key]:
                            del LOGIN_ATTEMPTS[key]
                    attempts = LOGIN_ATTEMPTS.setdefault(address, [])
                    if len(attempts) >= 10:
                        return self.send(429, {'error': 'Muitas tentativas. Aguarde um minuto.'})
                    attempts.append(now)
                if not hmac.compare_digest(str(body.get('password', '')), PASSWORD) or body.get('username') != USERNAME:
                    return self.send(401, {'error': 'Usuario ou senha incorretos.'})
                token = new_session()
                secure = '; Secure' if self.headers.get('X-Forwarded-Proto') == 'https' else ''
                return self.send(200, {'username': USERNAME, 'csrf': sign('csrf:' + token)},
                                 cookie=f'media_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_AGE}{secure}')
            token = self.session()
            if not token:
                return self.send(401, {'error': 'Entre para acessar o painel.'})
            if not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), sign('csrf:' + token)):
                return self.send(403, {'error': 'Sessao invalida. Atualize a pagina.'})
            if path == '/api/logout':
                return self.send(200, {'ok': True}, cookie='media_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0')
            if path == '/api/request':
                media_type = body.get('type')
                media_id = body.get('id')
                if media_type not in ('movie', 'tv') or type(media_id) is not int or media_id <= 0:
                    raise ValueError('Titulo invalido.')
                payload = {'mediaType': media_type, 'mediaId': media_id}
                if media_type == 'tv':
                    payload['seasons'] = 'all'
                result = seerr('request', payload)
                CACHE['at'] = 0
                return self.send(201, {'id': result.get('id'), 'status': result.get('status')})
            if path == '/api/torrent':
                torrent_hash = body.get('hash', '')
                action = body.get('action')
                if not re.fullmatch(r'[0-9a-fA-F]{40}|[0-9a-fA-F]{64}', str(torrent_hash)) or action not in ('pause', 'resume'):
                    raise ValueError('Acao invalida.')
                endpoint = 'stop' if action == 'pause' else 'start'
                try:
                    qb('torrents/' + endpoint, {'hashes': torrent_hash})
                except ServiceError as error:
                    if str(error) != 'HTTP 404':
                        raise
                    qb('torrents/' + action, {'hashes': torrent_hash})
                CACHE['at'] = 0
                return self.send(200, {'ok': True})
            return self.send(404, {'error': 'Acao nao encontrada.'})
        except ValueError as error:
            return self.send(400, {'error': str(error)})
        except ServiceError as error:
            return self.send(502, {'error': 'Servico indisponivel: ' + str(error)})
        except Exception:
            return self.send(500, {'error': 'Nao foi possivel concluir a acao.'})


if __name__ == '__main__':
    if len(PASSWORD) < 12:
        raise SystemExit('DASHBOARD_PASSWORD must contain at least 12 characters.')
    ThreadingHTTPServer((os.getenv('BIND_ADDRESS', '0.0.0.0'), int(os.getenv('PORT', '8080'))), Handler).serve_forever()
