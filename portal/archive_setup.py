"""Interactive local-only archive setup; never put credentials in Git or chat."""
import configparser
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sys
import urllib.error
import urllib.request


def yes(prompt, default=False):
    while True:
        answer = input(prompt + (' [S/n]: ' if default else ' [s/N]: ')).strip().lower()
        if not answer:
            return default
        if answer in ('s', 'sim', 'y', 'yes', 'n', 'nao', 'não', 'no'):
            return answer in ('s', 'sim', 'y', 'yes')


def private(path, data, uid=None):
    path = Path(path)
    path.write_text(data, encoding='utf-8')
    path.chmod(0o600)
    if uid is not None and hasattr(os, 'chown'):
        os.chown(path, uid, uid)


def cloud_bot_call(token, method):
    try:
        request = urllib.request.Request('https://api.telegram.org/bot' + token + '/' + method, b'{}', {'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
        if not result.get('ok'):
            raise ValueError()
        return result['result']
    except (ValueError, urllib.error.URLError, OSError):
        raise RuntimeError('Nao foi possivel validar/migrar o bot. Confira o token e a conexao; nenhum segredo foi exibido.') from None


def configure(secret_dir, rclone_source=None, uid=None):
    root = Path(secret_dir)
    settings = root / 'archive' / 'config.json'
    for directory in (root / 'archive', root / 'rclone'):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        if uid is not None and hasattr(os, 'chown'):
            os.chown(directory, uid, uid)
    for path in (settings, root / 'archive/proxy-key', root / 'archive/telegram.env', root / 'rclone/rclone.conf'):
        if path.is_file():
            path.chmod(0o600)
            if uid is not None and hasattr(os, 'chown'):
                os.chown(path, uid, uid)
    if settings.exists():
        old = json.loads(settings.read_text())
        complete = all(path.is_file() and path.stat().st_size for path in
                       (root / 'archive/proxy-key', root / 'archive/telegram.env', root / 'rclone/rclone.conf'))
        if old.get('telegram_bot_token') and complete and yes('Reutilizar a configuracao Drive/Telegram ja salva?', True):
            return old
    else:
        old = {}
    target = root / 'rclone' / 'rclone.conf'
    if rclone_source and Path(rclone_source).resolve() != target.resolve():
        shutil.copyfile(rclone_source, target)
    if not target.is_file() or not target.stat().st_size:
        raise ValueError('Configure primeiro o Drive com connect-drive.ps1 e forneca o rclone.conf local. Veja README.md.')
    target.chmod(0o600)
    if uid is not None and hasattr(os, 'chown'):
        os.chown(target, uid, uid)
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(target, encoding='utf-8-sig')
    names = [name for name in parser.sections() if parser.get(name, 'type', fallback='') == 'drive']
    if not names:
        raise ValueError('O rclone.conf precisa conter um remote do tipo drive. Configuracao criptografada deve ser exportada para um arquivo privado antes de importar.')
    default_remote = old.get('drive_remote') or names[0] + ':MediaStack'
    remote = input(f'Destino no Drive (Enter: {default_remote}): ').strip() or default_remote
    if not re.fullmatch(r'[A-Za-z0-9_-]+:[^\r\n]*', remote) or remote.split(':', 1)[0] not in names:
        raise ValueError('Escolha um remote Drive presente no arquivo importado.')
    print('\nCrie um bot EXCLUSIVO em https://t.me/BotFather e um canal PRIVADO para a biblioteca.')
    print('Adicione esse bot como administrador do canal, com permissao para publicar.')
    print('Para o Bot API local (ate 2.000 MB), obtenha api_id e api_hash em https://my.telegram.org/apps .')
    api_id = input('Telegram api_id (numerico): ').strip()
    api_hash = getpass.getpass('Telegram api_hash (oculto): ').strip()
    token = getpass.getpass('Token do bot (oculto): ').strip()
    chat = input('ID do canal privado (-100...): ').strip()
    if not re.fullmatch(r'[1-9][0-9]{2,12}', api_id) or not re.fullmatch(r'[a-fA-F0-9]{32}', api_hash):
        raise ValueError('api_id/api_hash invalidos. Use os dados do seu aplicativo Telegram, nao os do bot.')
    if not re.fullmatch(r'[0-9]{5,16}:[A-Za-z0-9_-]{20,}', token) or not re.fullmatch(r'-100[0-9]{5,}', chat):
        raise ValueError('Token do bot ou ID do canal privado invalido.')
    limit = input('Limite de upload para Drive (Enter: 8M; 0 = sem limite): ').strip() or '8M'
    if limit != '0' and not re.fullmatch(r'[1-9][0-9]{0,5}[kKmMgG]?', limit):
        raise ValueError('Use um limite rclone como 8M, 20M, ou 0.')
    if token != old.get('telegram_bot_token'):
        print('O bot sera migrado da API publica para o servidor local. Nao use um bot de outro sistema.')
        if not yes('Confirmar que esse bot e exclusivo desta biblioteca?'):
            raise ValueError('Configuracao cancelada sem migrar o bot.')
        cloud_bot_call(token, 'getMe')
        cloud_bot_call(token, 'logOut')
    config = {'drive_remote': remote, 'upload_limit': '' if limit == '0' else limit,
              'media_root': '/media', 'rclone_config': '/run/rclone/rclone.conf',
              'telegram_bot_token': token, 'telegram_chat_id': chat, 'stable_seconds': 120}
    key = root / 'archive' / 'proxy-key'
    if not key.is_file() or len(key.read_text().strip()) < 32:
        private(key, secrets.token_hex(32), uid)
    else:
        private(key, key.read_text().strip(), uid)
    private(settings, json.dumps(config, indent=2), uid)
    private(root / 'archive' / 'telegram.env', 'TELEGRAM_API_ID=' + api_id + '\nTELEGRAM_API_HASH=' + api_hash + '\n', uid)
    print('Configuracao privada salva. Drive recebe tudo; Telegram tambem recebe o que couber, sem dividir ou apagar originais.')
    return config


if __name__ == '__main__':
    try:
        configure(os.getenv('SECRET_DIR', '/secrets'), uid=1000)
    except (ValueError, RuntimeError, OSError, EOFError, configparser.Error) as error:
        raise SystemExit(str(error)) from None
