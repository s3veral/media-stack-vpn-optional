"""Incremental, non-destructive Drive + Telegram archive. Standard library only.

The worker has read-only media access. It never deletes or moves originals,
never divides/transcodes files, and never changes cloud sharing permissions.
"""
from contextlib import contextmanager
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import html
import json
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import unicodedata
import xml.etree.ElementTree as ET

TELEGRAM_LIMIT = 2000 * 1024 * 1024
CODE_PATTERN = r'SAL-[0-9A-F]{24}'
SKIP_DIRS = {'downloads', 'download', 'incomplete', 'crossseed', '@recycle', '#recycle', '$recycle.bin', 'system volume information'}
SKIP_EXTENSIONS = {'.part', '.partial', '.tmp', '.!qb', '.crdownload'}
PHOTO_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif', '.raw', '.cr2', '.nef', '.arw', '.dng', '.tif', '.tiff', '.gif'}
MUSIC_EXTENSIONS = {'.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.wav', '.wma', '.aiff', '.alac'}


class ArchiveError(Exception):
    pass


class StaleSource(ArchiveError):
    pass


class RetryLater(ArchiveError):
    def __init__(self, message, retry_after):
        super().__init__(message)
        self.retry_after = max(1, int(retry_after))


def normalized(text):
    return ''.join(char for char in unicodedata.normalize('NFKD', str(text)).casefold() if not unicodedata.combining(char))


def source_file(media, relative):
    path = (Path(media) / relative).resolve()
    try:
        path.relative_to(Path(media).resolve())
    except ValueError:
        raise ArchiveError('Caminho fora da biblioteca.') from None
    if not path.is_file() or path.is_symlink():
        raise StaleSource('Original local indisponivel; a copia remota foi preservada.')
    return path


def file_metadata(path, relative):
    parts = Path(relative).parts
    top = parts[0].casefold() if len(parts) > 1 else ''
    category = 'Outros'
    if top in ('movies', 'filmes', 'films'):
        category = 'Filmes'
    elif top in ('tv', 'series', 'séries', 'shows'):
        category = 'Series'
    elif top in ('music', 'musicas', 'músicas') or path.suffix.lower() in MUSIC_EXTENSIONS:
        category = 'Musicas'
    elif top in ('photos', 'fotos', 'pictures') or path.suffix.lower() in PHOTO_EXTENSIONS:
        category = 'Fotos'
    title, description = path.stem, str(Path(relative).parent)
    for sidecar in (path.with_suffix('.nfo'), path.parent / 'movie.nfo', path.parent / 'tvshow.nfo'):
        try:
            if sidecar.stat().st_size > 1024 * 1024:
                continue
            raw = sidecar.read_bytes()
            if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:
                continue
            tree = ET.fromstring(raw)
            title = tree.findtext('title') or title
            description = tree.findtext('plot') or tree.findtext('outline') or description
            break
        except (OSError, ET.ParseError):
            pass
    return title[:300], description[:4000], category


class Catalog:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'catalog.sqlite'
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS items (
                  code TEXT PRIMARY KEY, sha256 TEXT NOT NULL, md5 TEXT NOT NULL,
                  relative_path TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL,
                  category TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                  drive_state TEXT NOT NULL DEFAULT 'pending', drive_id TEXT, drive_url TEXT, drive_path TEXT,
                  telegram_state TEXT NOT NULL DEFAULT 'pending', telegram_message INTEGER, telegram_url TEXT,
                  drive_error TEXT, telegram_error TEXT, next_drive REAL DEFAULT 0, next_telegram REAL DEFAULT 0,
                  attempts_drive INTEGER DEFAULT 0, attempts_telegram INTEGER DEFAULT 0,
                  created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS observations (
                  path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, stable_since REAL, code TEXT);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
                CREATE INDEX IF NOT EXISTS drive_queue ON items(drive_state,next_drive);
                CREATE INDEX IF NOT EXISTS telegram_queue ON items(telegram_state,next_telegram);
            ''')
            for provider in ('drive', 'telegram'):
                db.execute(f"UPDATE items SET {provider}_state='pending' WHERE {provider}_state='uploading'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=30)
        db.row_factory = sqlite3.Row
        db.create_function('NORM', 1, normalized)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def setting(self, key, value=None):
        with self.connect() as db:
            if value is not None:
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, str(value)))
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            return row[0] if row else ''

    def item(self, code):
        if not re.fullmatch(CODE_PATTERN, code):
            raise ValueError('Codigo invalido.')
        with self.connect() as db:
            row = db.execute('SELECT * FROM items WHERE code=?', (code,)).fetchone()
        if not row:
            raise KeyError(code)
        return dict(row)

    def scan(self, media, now=None, stable_seconds=120):
        now = time.time() if now is None else now
        media = Path(media).resolve()
        count = 0
        for directory, dirs, files in os.walk(media, followlinks=False):
            dirs[:] = [name for name in sorted(dirs) if not name.startswith('.') and name.casefold() not in SKIP_DIRS
                       and not (Path(directory) / name).is_symlink()]
            for name in sorted(files):
                if name.startswith('.') or Path(name).suffix.lower() in SKIP_EXTENSIONS:
                    continue
                path = Path(directory) / name
                if path.is_symlink():
                    continue
                try:
                    relative = path.relative_to(media).as_posix()
                    stat = path.stat()
                    with self.connect() as db:
                        observation = db.execute('SELECT * FROM observations WHERE path=?', (relative,)).fetchone()
                        if not observation or (observation['size'], observation['mtime_ns']) != (stat.st_size, stat.st_mtime_ns):
                            db.execute('INSERT OR REPLACE INTO observations VALUES (?,?,?,?,NULL)',
                                       (relative, stat.st_size, stat.st_mtime_ns, now))
                            continue
                        if observation['code']:
                            existing = db.execute('SELECT relative_path FROM items WHERE code=?', (observation['code'],)).fetchone()
                            if existing and not (media / existing['relative_path']).is_file():
                                db.execute('UPDATE items SET relative_path=?,mtime_ns=? WHERE code=?', (relative, stat.st_mtime_ns, observation['code']))
                                for provider in ('drive', 'telegram'):
                                    db.execute(f"UPDATE items SET {provider}_state='pending',next_{provider}=0 WHERE code=? AND {provider}_state='source_missing'", (observation['code'],))
                            continue
                        if now - observation['stable_since'] < stable_seconds or now - stat.st_mtime < stable_seconds:
                            continue
                    sha, md5 = hashlib.sha256(), hashlib.md5()
                    with path.open('rb') as source:
                        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b''):
                            sha.update(chunk)
                            md5.update(chunk)
                    after = path.stat()
                    if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                        continue
                    code = 'SAL-' + sha.hexdigest()[:24].upper()
                    title, description, category = file_metadata(path, relative)
                    state = 'too_large' if stat.st_size > TELEGRAM_LIMIT else ('empty' if not stat.st_size else 'pending')
                    with self.connect() as db:
                        existing = db.execute('SELECT sha256 FROM items WHERE code=?', (code,)).fetchone()
                        if existing and existing['sha256'] != sha.hexdigest():
                            raise ArchiveError('Colisao de identificador; nenhum registro foi substituido.')
                        db.execute('''INSERT OR IGNORE INTO items
                            (code,sha256,md5,relative_path,title,description,category,size,mtime_ns,telegram_state,created_at,updated_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', (code, sha.hexdigest(), md5.hexdigest(), relative,
                            title, description, category, stat.st_size, stat.st_mtime_ns, state, now, now))
                        # Identical contents are deduplicated. A move updates only the local source.
                        db.execute('UPDATE items SET relative_path=?,mtime_ns=? WHERE code=?', (relative, stat.st_mtime_ns, code))
                        for provider in ('drive', 'telegram'):
                            db.execute(f"UPDATE items SET {provider}_state='pending',next_{provider}=0 WHERE code=? AND {provider}_state='source_missing'", (code,))
                        db.execute('UPDATE observations SET code=? WHERE path=?', (code, relative))
                    count += 1
                except OSError:
                    continue
        self.setting('last_scan', now)
        self.setting('scan_error', '')
        return count

    def queue_item(self, provider, now=None):
        if provider not in ('drive', 'telegram'):
            raise ValueError('Destino invalido.')
        now = time.time() if now is None else now
        if self.setting('paused') == 'true' or float(self.setting('pause_' + provider) or 0) > now:
            return None
        with self.connect() as db:
            row = db.execute(f'''SELECT * FROM items WHERE {provider}_state IN ('pending','failed')
                                 AND next_{provider}<=? ORDER BY created_at,code LIMIT 1''', (now,)).fetchone()
            if row:
                db.execute(f"UPDATE items SET {provider}_state='uploading',updated_at=? WHERE code=?", (now, row['code']))
        return dict(row) if row else None

    def complete(self, provider, code, result):
        with self.connect() as db:
            if provider == 'drive':
                db.execute("UPDATE items SET drive_state='verified',drive_id=?,drive_url=?,drive_path=?,drive_error=NULL,updated_at=? WHERE code=?",
                           (result['id'], result['url'], result['path'], time.time(), code))
            else:
                db.execute("UPDATE items SET telegram_state='uploaded',telegram_message=?,telegram_url=?,telegram_error=NULL,updated_at=? WHERE code=?",
                           (result['message_id'], result['url'], time.time(), code))

    def fail(self, provider, code, message, retry_after=None):
        now = time.time()
        with self.connect() as db:
            row = db.execute(f'SELECT attempts_{provider} FROM items WHERE code=?', (code,)).fetchone()
            attempts = (row[0] or 0) + 1
            delay = min(6 * 3600, 60 * (2 ** min(attempts, 9)))
            if retry_after is not None:
                delay = max(delay, int(retry_after) + 5)
            db.execute(f"UPDATE items SET {provider}_state='failed',{provider}_error=?,next_{provider}=?,attempts_{provider}=?,updated_at=? WHERE code=?",
                       (str(message)[:300], now + delay, attempts, now, code))
        # One unavailable provider must not cause an error storm across 3 TB.
        self.setting('pause_' + provider, now + (delay if retry_after is not None else min(delay, 3600)))

    def listing(self, query='', category='', page=1):
        page = max(1, min(int(page), 1000000))
        escaped = normalized(query[:150]).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        clause = "(NORM(title) LIKE ? ESCAPE '\\' OR NORM(description) LIKE ? ESCAPE '\\' OR NORM(code) LIKE ? ESCAPE '\\' OR NORM(relative_path) LIKE ? ESCAPE '\\')"
        values = ['%' + escaped + '%'] * 4
        if category:
            clause += ' AND category=?'
            values.append(category[:40])
        with self.connect() as db:
            total = db.execute('SELECT count(*) FROM items WHERE ' + clause, values).fetchone()[0]
            items = [dict(row) for row in db.execute('SELECT * FROM items WHERE ' + clause + ' ORDER BY created_at DESC,code LIMIT 50 OFFSET ?', values + [(page - 1) * 50])]
            summary = {}
            for provider in ('drive', 'telegram'):
                summary[provider] = {row[0]: {'count': row[1], 'bytes': row[2]} for row in db.execute(
                    f'SELECT {provider}_state,count(*),sum(size) FROM items GROUP BY {provider}_state')}
            counts = db.execute('SELECT count(*),coalesce(sum(size),0) FROM items').fetchone()
        return {'enabled': True, 'items': items, 'total': total, 'page': page, 'pageSize': 50,
                'summary': summary, 'files': counts[0], 'bytes': counts[1],
                'paused': self.setting('paused') == 'true', 'lastScan': self.setting('last_scan'),
                'scanError': self.setting('scan_error'), 'quota': json.loads(self.setting('quota') or 'null'),
                'telegramLimit': TELEGRAM_LIMIT}

    def export(self):
        with self.connect() as db:
            return {'version': 1, 'exportedAt': time.time(), 'items': [dict(row) for row in db.execute('SELECT * FROM items ORDER BY code')]}

    def restore(self, data):
        if data.get('version') != 1 or not isinstance(data.get('items'), list):
            raise ValueError('Formato de catalogo invalido.')
        with self.connect() as db:
            if db.execute('SELECT count(*) FROM items').fetchone()[0]:
                raise ValueError('Importacao permitida somente em catalogo vazio. O catalogo existente nao foi alterado.')
            columns = [row[1] for row in db.execute('PRAGMA table_info(items)')]
            for item in data['items']:
                if not re.fullmatch(CODE_PATTERN, item.get('code', '')) or not re.fullmatch(r'[0-9a-f]{64}', item.get('sha256', '')):
                    raise ValueError('Identificador/digest invalido no catalogo.')
                if item['code'] != 'SAL-' + item['sha256'][:24].upper() or not re.fullmatch(r'[0-9a-f]{32}', item.get('md5', '')):
                    raise ValueError('Checksum/identificador inconsistente no catalogo.')
                if type(item.get('size')) is not int or item['size'] < 0:
                    raise ValueError('Tamanho invalido no catalogo.')
                required = ('relative_path', 'title', 'description', 'category', 'mtime_ns', 'created_at', 'updated_at')
                if any(key not in item for key in required):
                    raise ValueError('Catalogo incompleto.')
                allowed = {key: item[key] for key in columns if key in item}
                for provider in ('drive', 'telegram'):
                    if allowed.get(provider + '_state') == 'uploading':
                        allowed[provider + '_state'] = 'pending'
                names = list(allowed)
                db.execute('INSERT INTO items (' + ','.join(names) + ') VALUES (' + ','.join('?' for _ in names) + ')', [allowed[key] for key in names])


class Drive:
    def __init__(self, config):
        self.config = config
        self.base = config.get('drive_remote', 'sala:MediaStack').rstrip('/')
        if not re.fullmatch(r'[A-Za-z0-9_-]+:[^\r\n]*', self.base):
            raise ValueError('Use um remote rclone, por exemplo sala:MediaStack.')

    def command(self, *args):
        return ['rclone', '--config', self.config.get('rclone_config', '/run/rclone/rclone.conf'),
                '--retries', '1', '--low-level-retries', '2', '--contimeout', '20s', '--timeout', '5m'] + list(args)

    def run(self, *args):
        result = subprocess.run(self.command(*args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=24 * 3600)
        if result.returncode:
            # Do not return process stderr: OAuth tokens can occur in diagnostics.
            raise ArchiveError(f'Drive indisponivel (rclone {result.returncode}); confira cota, conexao e autorizacao.')
        return result.stdout

    def object_path(self, item):
        # Content-addressed objects retain old versions without using sync/delete.
        # Stable basename is derived from code, not from a mutable local path.
        if item.get('drive_path'):
            expected = re.escape(self.base + '/objects/' + item['sha256'] + '/original') + r'(?:\.[a-z0-9]{1,12})?'
            if not re.fullmatch(expected, item['drive_path']):
                raise ArchiveError('O objeto salvo pertence a outro destino. Confira o remote/caminho configurado antes de restaurar.')
            return item['drive_path']
        extension = Path(item['relative_path']).suffix.lower()
        extension = extension if re.fullmatch(r'\.[a-z0-9]{1,12}', extension) else '.bin'
        return self.base + '/objects/' + item['sha256'] + '/original' + extension

    def upload(self, item, media):
        path = source_file(media, item['relative_path'])
        before = path.stat()
        if (before.st_size, before.st_mtime_ns) != (item['size'], item['mtime_ns']):
            raise StaleSource('Original mudou; aguardando nova varredura.')
        target = self.object_path(item)
        args = ['copyto', str(path), target, '--immutable', '--checksum', '--drive-stop-on-upload-limit']
        if self.config.get('upload_limit'):
            args += ['--bwlimit', self.config['upload_limit']]
        self.run(*args)
        remote = json.loads(self.run('lsjson', target, '--stat', '--hash', '--hash-type', 'MD5'))
        digest = {key.lower(): value.lower() for key, value in remote.get('Hashes', {}).items()}.get('md5')
        if remote.get('Size') != item['size'] or digest != item['md5'].lower() or not remote.get('ID'):
            raise ArchiveError('A copia no Drive nao passou na verificacao MD5/tamanho; original preservado.')
        return {'id': remote['ID'], 'url': 'https://drive.google.com/file/d/' + urllib.parse.quote(remote['ID'], safe='') + '/view', 'path': target}

    def quota(self):
        return json.loads(self.run('about', self.base.split(':', 1)[0] + ':', '--json'))

    def backup_catalog(self, catalog):
        snapshot = catalog.root / 'catalog-export.json'
        data = catalog.export()
        if not data['items']:
            return
        snapshot.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        # Only the rolling metadata file is replaced. Media objects never are.
        self.run('copyto', str(snapshot), self.base + '/catalog/latest.json', '--checksum')
        day = time.strftime('%Y-%m-%d', time.gmtime())
        if catalog.setting('catalog_snapshot_day') != day:
            self.run('copyto', str(snapshot), self.base + '/catalog/' + day + '.json', '--immutable')
            catalog.setting('catalog_snapshot_day', day)


class Telegram:
    def __init__(self, config):
        self.config = config
        self.base = 'http://telegram-bot-api:8081/bot' + config['telegram_bot_token'] + '/'
        chat = str(config['telegram_chat_id'])
        if not re.fullmatch(r'-100[0-9]+', chat):
            raise ValueError('Use o ID -100... do seu canal privado Telegram.')
        self.chat = chat

    def call(self, method, payload):
        request = urllib.request.Request(self.base + method, json.dumps(payload).encode(), {'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=6 * 3600) as response:
                result = json.load(response)
            if not result.get('ok'):
                retry_after = (result.get('parameters') or {}).get('retry_after')
                if isinstance(retry_after, int) and retry_after > 0:
                    raise RetryLater('Telegram solicitou espera; retomada automatica apos o prazo informado.', retry_after)
                raise ArchiveError('Telegram recusou o envio; confira permissoes do bot e limites.')
            return result['result']
        except urllib.error.HTTPError as error:
            try:
                result = json.loads(error.read(65536))
                retry_after = (result.get('parameters') or {}).get('retry_after')
            except (ValueError, AttributeError):
                retry_after = None
            if isinstance(retry_after, int) and retry_after > 0:
                raise RetryLater('Telegram solicitou espera; retomada automatica apos o prazo informado.', retry_after) from None
            raise ArchiveError('Telegram recusou o envio; confira o canal, permissoes e o Bot API local.') from None
        except (urllib.error.URLError, OSError, ValueError):
            raise ArchiveError('Telegram indisponivel; confira o bot, canal privado e servidor Bot API local.') from None

    def upload(self, item, media):
        if item['size'] > TELEGRAM_LIMIT:
            raise ArchiveError('Arquivo acima do limite Telegram; somente Drive.')
        path = source_file(media, item['relative_path'])
        stat = path.stat()
        if (stat.st_size, stat.st_mtime_ns) != (item['size'], item['mtime_ns']):
            raise StaleSource('Original mudou; aguardando nova varredura.')
        caption = ('<b>' + html.escape(item['title'][:120]) + '</b>\n<code>' + item['code'] + '</code>\n'
                   + html.escape(item['category']) + ' | ' + str(round(item['size'] / (1024 * 1024), 1)) + ' MiB\n'
                   + html.escape(item['description'][:220]) + '\nSHA256: <code>' + item['sha256'] + '</code>')
        payload = {'chat_id': self.chat, 'caption': caption, 'parse_mode': 'HTML', 'disable_notification': True}
        # Documents preserve original photo/audio/video bytes and metadata.
        # Telegram can open the file; no lossy re-encode or photo compression.
        payload['document'] = path.as_uri()
        result = self.call('sendDocument', payload)
        after = path.stat()
        if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
            raise StaleSource('Original mudou durante o envio; documento remoto nao foi marcado como copia concluida.')
        document = result.get('document') or {}
        if document.get('file_size') != item['size'] or not result.get('message_id'):
            raise ArchiveError('Telegram nao confirmou o tamanho do documento enviado.')
        return {'message_id': result['message_id'], 'url': 'https://t.me/c/' + self.chat[4:] + '/' + str(result['message_id'])}


def byte_range(header, size):
    if not header:
        return 0, size - 1, False
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', header.strip())
    if not match or not any(match.groups()) or size <= 0:
        raise ValueError('Range invalido.')
    first, last = match.groups()
    if not first:
        count = int(last)
        if count <= 0:
            raise ValueError('Range invalido.')
        return max(0, size - count), size - 1, True
    first = int(first)
    last = min(int(last), size - 1) if last else size - 1
    if first >= size or last < first:
        raise ValueError('Range fora do arquivo.')
    return first, last, True


class Worker:
    def __init__(self, config, catalog, drive=None, telegram=None):
        self.config, self.catalog = config, catalog
        self.media = Path(config.get('media_root', '/media'))
        self.drive = drive or Drive(config)
        self.telegram = telegram or Telegram(config)
        self.stop = threading.Event()

    def process_one(self, provider):
        item = self.catalog.queue_item(provider)
        if not item:
            return False
        try:
            result = getattr(self, provider).upload(item, self.media)
            self.catalog.complete(provider, item['code'], result)
        except StaleSource as error:
            with self.catalog.connect() as db:
                db.execute(f"UPDATE items SET {provider}_state='source_missing',{provider}_error=? WHERE code=?", (str(error), item['code']))
        except (ArchiveError, OSError, ValueError, subprocess.SubprocessError) as error:
            message = str(error) if isinstance(error, ArchiveError) else 'Falha temporaria durante o envio; original preservado.'
            self.catalog.fail(provider, item['code'], message, retry_after=getattr(error, 'retry_after', None))
        return True

    def upload_loop(self, provider):
        while not self.stop.is_set():
            try:
                if not self.process_one(provider):
                    self.stop.wait(5)
                elif provider == 'telegram':
                    self.stop.wait(1.1)
            except Exception:
                self.stop.wait(30)

    def scan_loop(self):
        while not self.stop.is_set():
            try:
                self.catalog.scan(self.media, stable_seconds=int(self.config.get('stable_seconds', 120)))
            except Exception:
                self.catalog.setting('scan_error', 'Falha na varredura; confira montagem e permissoes da biblioteca.')
            self.stop.wait(60)

    def maintenance_loop(self):
        while not self.stop.is_set():
            if self.catalog.setting('paused') != 'true':
                try:
                    self.catalog.setting('quota', json.dumps(self.drive.quota()))
                    self.drive.backup_catalog(self.catalog)
                except Exception:
                    self.catalog.setting('catalog_backup_error', 'Backup do catalogo no Drive pendente; confira conexao/cota.')
                else:
                    self.catalog.setting('catalog_backup_error', '')
                    self.catalog.setting('catalog_backup_at', time.time())
            self.stop.wait(3600)

    def start(self):
        for target, args in ((self.scan_loop, ()), (self.upload_loop, ('drive',)),
                             (self.upload_loop, ('telegram',)), (self.maintenance_loop, ())):
            threading.Thread(target=target, args=args, daemon=True).start()


def make_handler(worker, proxy_key):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'SalaArchive'

        def log_message(self, *_):
            pass

        def authorized(self):
            return hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + proxy_key)

        def send(self, status, data):
            raw = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if not self.authorized():
                return self.send(401, {'error': 'Nao autorizado.'})
            parsed = urllib.parse.urlsplit(self.path)
            try:
                if parsed.path == '/catalog':
                    query = urllib.parse.parse_qs(parsed.query)
                    data = worker.catalog.listing(query.get('q', [''])[0], query.get('category', [''])[0], query.get('page', ['1'])[0])
                    data['catalogBackupAt'] = worker.catalog.setting('catalog_backup_at')
                    data['catalogBackupError'] = worker.catalog.setting('catalog_backup_error')
                    return self.send(200, data)
                if parsed.path == '/export':
                    return self.send(200, worker.catalog.export())
                if parsed.path.startswith('/file/'):
                    item = worker.catalog.item(parsed.path[6:])
                    if item['drive_state'] != 'verified':
                        return self.send(409, {'error': 'Aguarde a copia verificada no Drive.'})
                    return self.stream(item)
                return self.send(404, {'error': 'Nao encontrado.'})
            except (ValueError, KeyError):
                return self.send(400, {'error': 'Consulta invalida.'})
            except (ArchiveError, OSError, sqlite3.Error):
                return self.send(503, {'error': 'Arquivo em nuvem temporariamente indisponivel.'})

        def do_POST(self):
            if not self.authorized():
                return self.send(401, {'error': 'Nao autorizado.'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if self.path != '/control' or not 0 < length <= 4096:
                    raise ValueError()
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError()
                action = data.get('action')
                if action in ('pause', 'resume'):
                    worker.catalog.setting('paused', str(action == 'pause').lower())
                elif action == 'retry':
                    provider = data.get('provider')
                    if provider not in ('drive', 'telegram'):
                        raise ValueError()
                    item = worker.catalog.item(data.get('code', ''))
                    if item[provider + '_state'] != 'failed':
                        raise ValueError()
                    with worker.catalog.connect() as db:
                        db.execute(f"UPDATE items SET {provider}_state='pending',next_{provider}=0 WHERE code=?", (item['code'],))
                    worker.catalog.setting('pause_' + provider, 0)
                else:
                    raise ValueError()
                return self.send(200, {'ok': True})
            except (ValueError, KeyError, TypeError):
                return self.send(400, {'error': 'Acao invalida.'})

        def stream(self, item):
            size = item['size']
            try:
                start, end, partial = byte_range(self.headers.get('Range'), size)
            except ValueError:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            command = worker.drive.command('cat', worker.drive.object_path(item), '--offset', str(start), '--count', str(max(0, end - start + 1)))
            with tempfile.TemporaryFile() as errors:
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors)
                try:
                    first = process.stdout.read(min(256 * 1024, max(0, end - start + 1)))
                    if not first and size:
                        return self.send(502, {'error': 'Drive nao iniciou a leitura; tente abrir diretamente no Drive.'})
                    self.send_response(206 if partial else 200)
                    self.send_header('Content-Type', mimetypes.guess_type(item['relative_path'])[0] or 'application/octet-stream')
                    self.send_header('Content-Length', str(max(0, end - start + 1)))
                    self.send_header('Accept-Ranges', 'bytes')
                    self.send_header('Cache-Control', 'private, no-store')
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    if partial:
                        self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                    self.end_headers()
                    self.wfile.write(first)
                    for chunk in iter(lambda: process.stdout.read(256 * 1024), b''):
                        self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    if process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    process.stdout.close()
    return Handler


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--import-catalog':
        catalog = Catalog(os.getenv('ARCHIVE_DATA', '/data'))
        catalog.restore(json.loads(Path(sys.argv[2]).read_text(encoding='utf-8')))
        print('Catalogo restaurado; nenhuma midia local/remota foi alterada.')
        raise SystemExit(0)
    configuration = json.loads(Path(os.getenv('ARCHIVE_CONFIG', '/run/archive/config.json')).read_text())
    key = Path(os.getenv('ARCHIVE_PROXY_KEY_FILE', '/run/archive/proxy-key')).read_text().strip()
    if len(key) < 32:
        raise SystemExit('Archive proxy key must contain at least 32 characters.')
    worker = Worker(configuration, Catalog(os.getenv('ARCHIVE_DATA', '/data')))
    worker.start()
    ThreadingHTTPServer(('0.0.0.0', int(os.getenv('ARCHIVE_PORT', '8082'))), make_handler(worker, key)).serve_forever()
