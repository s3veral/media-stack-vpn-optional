"""Local credential convergence. Never print secrets or put them in argv."""
import copy
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


class SetupError(Exception):
    pass


def private_file(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        shutil.copyfile(path, str(path) + '.bak')
        Path(str(path) + '.bak').chmod(0o600)
    temporary = path.with_name(path.name + '.new')
    temporary.write_text(content, encoding='utf-8')
    temporary.chmod(0o600)
    os.replace(temporary, path)


def client():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def http(url, payload=None, headers=None, method=None, opener=None, form=False):
    headers = dict(headers or {})
    data = None
    if payload is not None:
        data = (urllib.parse.urlencode(payload) if form else json.dumps(payload)).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
    request = urllib.request.Request(url, data, headers, method=method or ('POST' if data is not None else 'GET'))
    try:
        response = (opener or client()).open(request, timeout=20)
    except urllib.error.HTTPError as error:
        raise SetupError('HTTP ' + str(error.code)) from None
    except (OSError, urllib.error.URLError, TimeoutError):
        raise SetupError('sem conexao') from None
    with response:
        raw = response.read(8 * 1024 * 1024)
        try:
            return json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return raw.decode(errors='replace')


def download_client(schema, username, password, category, existing=None):
    result = copy.deepcopy(existing if existing is not None else schema)
    values = {'host': 'qbittorrent', 'port': 8080, 'useSsl': False, 'urlBase': '',
              'username': username, 'password': password,
              'category': category, 'movieCategory': category, 'tvCategory': category}
    for field in result['fields']:
        if field['name'] in values:
            field['value'] = values[field['name']]
    result.update(name='qBittorrent', enable=True)
    if existing is None:
        result.pop('id', None)
        result.update(priority=1, removeCompletedDownloads=False, removeFailedDownloads=True)
    return result


def seerr_service(name, port, key, metadata):
    profiles = metadata.get('profiles') or []
    stock = {'Any', 'SD', 'HD-720p', 'HD-1080p', 'Ultra-HD', 'HD - 720p/1080p'}
    profile = (next((p for p in profiles if p['name'] == '1080p Balanced'), None)
               or next((p for p in profiles if '1080' in p['name'] and p['name'] not in stock), None)
               or next((p for p in profiles if p['name'] == 'HD-1080p'), None)
               or next(iter(profiles), None))
    roots = [r['path'] for r in metadata.get('rootFolders', [])]
    if not profile or not roots:
        raise SetupError('Seerr: faltam perfis ou pasta de biblioteca no ' + name + '.')
    wanted = '/media/movies' if name == 'radarr' else '/media/tv'
    result = {'name': name.capitalize(), 'hostname': name, 'port': port, 'apiKey': key,
              'useSsl': False, 'baseUrl': metadata.get('urlBase') or '',
              'activeProfileId': profile['id'], 'activeProfileName': profile['name'],
              'activeDirectory': wanted if wanted in roots else roots[0],
              'is4k': False, 'tags': [], 'isDefault': True, 'externalUrl': '',
              'syncEnabled': True, 'preventSearch': False, 'tagRequests': False}
    if name == 'radarr':
        result['minimumAvailability'] = 'released'
    else:
        result.update(enableSeasonFolders=True, seriesType='standard', animeSeriesType='anime',
                      animeTags=[], monitorNewItems='all')
    return result


class AuthSetup:
    def __init__(self, username, password, configs='/configs', secrets='/secrets'):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', username) or len(password) < 12 or any(c in password for c in '\r\n'):
            raise SetupError('Login local invalido. Preserve a senha gerada pelo instalador.')
        self.username, self.password = username, password
        self.configs, self.secrets = Path(configs), Path(secrets)
        self.qb_cookie = ''

    def prepare(self):
        # Docker may populate this volume as root before Qui's UID 1000 starts.
        qui = self.configs / 'qui'
        qui.mkdir(parents=True, exist_ok=True)
        for folder, dirs, files in os.walk(qui, followlinks=False):
            for path in [Path(folder)] + [Path(folder) / name for name in files]:
                if not path.is_symlink():
                    os.chown(path, 1000, 1000)
        try:
            generated = subprocess.run(['docker', 'run', '--rm', '-i', '--network', 'none',
                '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                'amir20/dozzle:latest', 'generate', self.username, '--name', 'Admin', '--user-roles', 'none'],
                input=(self.password + '\n').encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        except (OSError, subprocess.TimeoutExpired):
            raise SetupError('Nao foi possivel gerar o login Dozzle.') from None
        document = generated.stdout.decode(errors='replace')
        if generated.returncode or not re.search(r'password:\s*[\x27\"]?\$2[aby]\$\d\d\$', document):
            raise SetupError('Dozzle nao produziu um hash bcrypt valido; nenhum login foi substituido.')
        if self.password in document:
            raise SetupError('Saida Dozzle inesperada; nenhum segredo foi salvo em texto simples.')
        private_file(self.secrets / 'dozzle/users.yml', document)
        print('OK Dozzle: hash privado gerado; acoes e shell continuam desabilitados.')

    def qb_native(self, path, payload=None):
        # The portable Bash setup used the Docker hostname even inside qBit,
        # unintentionally bypassing the loopback route needed for bootstrap.
        options = [('url', 'http://localhost:8080/api/v2/' + path),
                   ('header', 'Origin: http://localhost:8080'),
                   ('header', 'Referer: http://localhost:8080/')]
        if self.qb_cookie:
            options.append(('header', 'Cookie: ' + self.qb_cookie))
        if payload is not None:
            options.extend([('request', 'POST'), ('header', 'Content-Type: application/x-www-form-urlencoded'),
                            ('data', urllib.parse.urlencode(payload))])
        config = '\n'.join(name + ' = ' + json.dumps(value, ensure_ascii=False) for name, value in options) + '\n'
        result = subprocess.run(['docker', 'exec', '-i', 'qbittorrent', 'curl', '-sS', '-m', '20', '-i',
                                 '--config', '-', '-w', '\n%{http_code}'],
                                input=config.encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if result.returncode:
            raise SetupError('qBittorrent nao respondeu na conexao local.')
        content, status = result.stdout.rsplit(b'\n', 1)
        header, body = content.split(b'\r\n\r\n', 1)
        for line in header.decode(errors='replace').splitlines():
            if line.lower().startswith('set-cookie:'):
                # qBittorrent 5.2 can use a session-cookie name other than SID.
                # Forward the first cookie pair, not attributes, without
                # assuming its name or exposing it in a command argument.
                pair = line.split(':', 1)[1].strip().split(';', 1)[0]
                if '=' in pair and not any(c in pair for c in '\r\n'):
                    self.qb_cookie = pair
        return int(status), body.decode(errors='replace')

    def qb(self):
        status, body = self.qb_native('auth/login', {'username': self.username, 'password': self.password})
        if status not in (200, 204) or body.strip() == 'Fails.':
            logs = subprocess.run(['docker', 'logs', 'qbittorrent'], stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, timeout=20).stdout.decode(errors='replace')
            temporary = re.findall(r'temporary password is provided for this session:\s*(\S+)', logs)
            if not temporary:
                raise SetupError('Login atual desconhecido; ajuste o WebUI do qBittorrent antes de repetir.')
            status, body = self.qb_native('auth/login', {'username': 'admin', 'password': temporary[-1]})
            if status not in (200, 204) or body.strip() == 'Fails.':
                raise SetupError('qBittorrent rejeitou a autorizacao inicial; nenhuma tentativa adicional foi feita.')
        preferences = {'web_ui_username': self.username, 'web_ui_password': self.password,
                       'save_path': '/media/downloads', 'temp_path_enabled': True,
                       'temp_path': '/media/downloads/torrent', 'queueing_enabled': True,
                       'max_active_downloads': 2}
        status, _ = self.qb_native('app/setPreferences', {'json': json.dumps(preferences)})
        if status not in (200, 204):
            raise SetupError('Nao foi possivel salvar o login do qBittorrent (HTTP ' + str(status) + ').')
        status, raw = self.qb_native('torrents/categories')
        categories = json.loads(raw) if status == 200 else {}
        for category in ('radarr', 'sonarr'):
            method = 'editCategory' if category in categories else 'createCategory'
            status, _ = self.qb_native('torrents/' + method, {'category': category, 'savePath': '/media/downloads'})
            if status not in (200, 204, 409):
                raise SetupError('Nao foi possivel configurar as categorias de download.')
        # Clear only qBit's in-memory login bans; never delete config/torrents.
        subprocess.run(['docker', 'restart', 'qbittorrent'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=60)
        connection = client()
        for attempt in range(30):
            try:
                result = http('http://qbittorrent:8080/api/v2/auth/login',
                              {'username': self.username, 'password': self.password}, opener=connection, form=True)
                if result.strip() == 'Fails.':
                    raise SetupError('Login qBittorrent ainda nao foi aceito.')
                http('http://qbittorrent:8080/api/v2/app/version', opener=connection)
                return
            except SetupError as error:
                if str(error).startswith('HTTP') or 'aceito' in str(error):
                    raise
                time.sleep(1)
        raise SetupError('qBittorrent nao voltou a responder apos o reinicio.')

    def arr(self, name, port, version):
        key = ET.parse(self.configs / name / 'config.xml').findtext('ApiKey')
        base = f'http://{name}:{port}/api/{version}/'
        headers = {'X-Api-Key': key}
        settings = http(base + 'config/host', headers=headers)
        settings.update(authenticationMethod='forms', authenticationRequired='enabled',
                        username=self.username, password=self.password, passwordConfirmation=self.password)
        http(base + 'config/host', settings, headers, method='PUT')
        if name == 'prowlarr':
            return
        existing = next((x for x in http(base + 'downloadclient', headers=headers)
                         if x.get('implementation') == 'QBittorrent'), None)
        schemas = http(base + 'downloadclient/schema', headers=headers)
        schema = next(x for x in schemas if x.get('implementation') == 'QBittorrent')
        body = download_client(schema, self.username, self.password, name, existing)
        errors = http(base + 'downloadclient/test', body, headers)
        if isinstance(errors, list) and errors:
            raise SetupError('Teste do cliente qBittorrent falhou; confira a conexao local.')
        http(base + 'downloadclient' + ('/' + str(existing['id']) if existing else ''), body, headers,
             method='PUT' if existing else 'POST')

    def bazarr(self):
        text = (self.configs / 'bazarr/config/config.yaml').read_text()
        match = re.search(r'(?m)^\s*apikey:\s*[\x27\"]?([a-fA-F0-9]{32})', text)
        if not match:
            raise SetupError('Chave Bazarr ainda nao foi criada.')
        headers = {'X-API-KEY': match[1]}
        http('http://bazarr:6767/api/system/settings', {
            'settings-auth-type': 'form', 'settings-auth-username': self.username,
            'settings-auth-password': self.password}, headers, form=True)
        settings = http('http://bazarr:6767/api/system/settings', headers=headers)
        if settings.get('auth', {}).get('username') != self.username or settings['auth'].get('type') != 'form':
            raise SetupError('Bazarr nao confirmou a autenticacao por formulario.')

    def qui(self):
        state = http('http://qui:7476/api/auth/check-setup')
        method = 'setup' if state.get('setupRequired') else 'login'
        http('http://qui:7476/api/auth/' + method, {'username': self.username, 'password': self.password})

    def verify_jellyfin(self):
        result = http('http://jellyfin:8096/Users/AuthenticateByName',
                      {'Username': self.username, 'Pw': self.password},
                      {'Authorization': 'MediaBrowser Client="Sala", Device="Local", DeviceId="sala-auth", Version="1"'})
        if not result.get('AccessToken'):
            raise SetupError('Jellyfin nao aceitou o login padrao.')

    def verify_seerr(self):
        http('http://seerr:5055/api/v1/auth/jellyfin', {'username': self.username, 'password': self.password})

    def seerr_services(self):
        settings = json.loads((self.configs / 'seerr/settings.json').read_text())
        headers = {'X-Api-Key': settings['main']['apiKey']}
        for name, port in [('radarr', 7878), ('sonarr', 8989)]:
            base = 'http://seerr:5055/api/v1/settings/' + name
            existing = http(base, headers=headers)
            if existing:
                default = next((s for s in existing if s.get('isDefault') and not s.get('is4k')), None)
                if not default:
                    raise SetupError('Escolha um servidor padrao ' + name + ' no Seerr; configuracao existente preservada.')
                http(base + '/test', default, headers)
                continue
            key = ET.parse(self.configs / name / 'config.xml').findtext('ApiKey')
            metadata = http(base + '/test', {'hostname': name, 'port': port, 'apiKey': key,
                                           'useSsl': False, 'baseUrl': ''}, headers)
            body = seerr_service(name, port, key, metadata)
            http(base, body, headers)
            saved = http(base, headers=headers)
            if not any(s.get('hostname') == name and s.get('isDefault') for s in saved):
                raise SetupError('Seerr nao confirmou o servidor ' + name + '.')
        public_url = 'http://seerr:5055/api/v1/settings/public'
        if not http(public_url).get('initialized'):
            # Finish the wizard only after both service tests and a real
            # administrator login. Preserve every existing library setting.
            connection = client()
            http('http://seerr:5055/api/v1/auth/jellyfin',
                 {'username': self.username, 'password': self.password}, opener=connection)
            http('http://seerr:5055/api/v1/settings/initialize', {}, opener=connection)
            if not http(public_url).get('initialized'):
                raise SetupError('Seerr ainda nao confirmou a conclusao do assistente.')

    def verify_profilarr(self):
        connection = client()
        http('http://profilarr:6868/auth/login', {'username': self.username, 'password': self.password},
             {'Origin': 'http://profilarr:6868', 'Accept': 'text/html'}, opener=connection, form=True)
        result = http('http://profilarr:6868/api/v1/arr', opener=connection)
        if not isinstance(result, (dict, list)):
            raise SetupError('Profilarr nao confirmou a sessao autenticada.')

    def apply(self):
        checks = [('qBittorrent', self.qb),
                  ('Radarr', lambda: self.arr('radarr', 7878, 'v3')),
                  ('Sonarr', lambda: self.arr('sonarr', 8989, 'v3')),
                  ('Prowlarr', lambda: self.arr('prowlarr', 9696, 'v1')),
                  ('Bazarr', self.bazarr), ('Qui', self.qui),
                  ('Jellyfin', self.verify_jellyfin), ('Seerr', self.verify_seerr),
                  ('Seerr -> Radarr/Sonarr', self.seerr_services),
                  ('Profilarr', self.verify_profilarr)]
        failed = []
        for name, action in checks:
            try:
                action()
                print('OK ' + name + ': login padrao verificado/configurado.', flush=True)
            except (SetupError, OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError) as error:
                detail = str(error) if isinstance(error, SetupError) else type(error).__name__
                print('PENDENTE ' + name + ': ' + detail + '; nenhum segredo foi exibido.', flush=True)
                failed.append(name)
        return failed


if __name__ == '__main__':
    try:
        setup = AuthSetup(os.getenv('ADMIN_USER', 'admin'), os.environ['ADMIN_PASS'])
        if len(sys.argv) > 1 and sys.argv[1] == 'prepare':
            setup.prepare()
        else:
            raise SystemExit(2 if setup.apply() else 0)
    except (SetupError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit('Preparacao de login falhou: ' + (str(error) if isinstance(error, SetupError) else type(error).__name__)) from None
