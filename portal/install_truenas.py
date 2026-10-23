"""Install Sala alongside the upstream TrueNAS Compose project."""
import getpass
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import urllib.request


def ask_yes(prompt, default=False):
    while True:
        answer = input(prompt + (' [S/n]: ' if default else ' [s/N]: ')).strip().lower()
        if not answer:
            return default
        if answer in ('s', 'sim', 'y', 'yes'):
            return True
        if answer in ('n', 'nao', 'não', 'no'):
            return False


def main():
    config_pool = os.environ['CONFIG_POOL']
    media_pool = os.environ['MEDIA_POOL']
    configs = Path('/mnt') / config_pool / 'configs'
    media = Path('/mnt') / media_pool / 'media'
    root = Path('/mnt') / config_pool / 'docker' / 'sala'
    root.mkdir(parents=True, exist_ok=True)
    previous = root / 'portal-settings.json'
    saved = json.loads(previous.read_text()) if previous.exists() else {}
    password = saved.get('password') or secrets.token_urlsafe(24)
    remote = ask_yes('Publicar a homepage com Cloudflare Tunnel?', saved.get('remote', False))
    archive_enabled = ask_yes('Ativar arquivo Drive + Telegram (originais preservados)?', saved.get('archive', False))
    hostname = saved.get('hostname', '')
    if remote and hostname:
        hostname = input(f'Subdominio completo (Enter para manter {hostname}): ').strip() or hostname
    assets = ['portal/Dockerfile', 'portal/Dockerfile.archive', 'portal/Dockerfile.telegram',
              'portal/app.py', 'portal/archive.py', 'portal/archive_client.py', 'portal/archive_setup.py', 'portal/cloudflare_setup.py',
              'portal/static/index.html', 'portal/static/style.css', 'portal/static/app.js', 'portal/static/cloud.css', 'portal/static/cloud.js']
    for asset in assets:
        target = root / asset
        target.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen('https://raw.githubusercontent.com/s3veral/media-stack-vpn-optional/main/' + asset, timeout=30) as response:
            target.write_bytes(response.read())
    sys.path.insert(0, str(root / 'portal'))
    secret_dir = root / '.secrets'
    secret_dir.mkdir(exist_ok=True, mode=0o700)
    (secret_dir / 'runtime').mkdir(exist_ok=True, mode=0o700)
    archive_dir = secret_dir / 'archive'
    archive_dir.mkdir(exist_ok=True, mode=0o700)
    (secret_dir / 'rclone').mkdir(exist_ok=True, mode=0o700)
    from archive_setup import configure, private
    for directory in (archive_dir, secret_dir / 'rclone', root / '.archive-data', root / '.telegram-data'):
        directory.mkdir(exist_ok=True, mode=0o700)
        os.chown(directory, 568, 568)
    proxy_key = archive_dir / 'proxy-key'
    if not proxy_key.exists():
        private(proxy_key, secrets.token_hex(32), 568)
    telegram_env = archive_dir / 'telegram.env'
    if not telegram_env.exists():
        private(telegram_env, '', 568)
    if archive_enabled:
        source = None
        if not (secret_dir / 'rclone' / 'rclone.conf').exists():
            print('Autorize o Drive no seu computador com connect-drive.ps1 e transfira o rclone.conf por um canal privado.')
            print('Guia: https://github.com/s3veral/media-stack-vpn-optional#arquivo-drive--telegram')
            source = input('Caminho local no TrueNAS para o rclone.conf autorizado: ').strip()
        configure(secret_dir, rclone_source=source, uid=568)
    if remote:
        from cloudflare_setup import Cloudflared, provision
        result = provision(hostname, secret_dir, runner=Cloudflared(secret_dir, docker=True))
        hostname = result['hostname']
    inspection = json.loads(subprocess.check_output(['docker', 'inspect', 'qbittorrent']))[0]
    networks = inspection['NetworkSettings']['Networks']
    network = next(iter(networks))
    host_ip = os.getenv('HOST_IP', 'localhost')
    config = {
        'name': 'media-portal',
        'services': {
            'dashboard': {
                'build': {'context': '.', 'dockerfile': 'portal/Dockerfile'},
                'restart': 'unless-stopped', 'user': '568:568', 'group_add': ['1000'],
                'ports': ['8090:8080'], 'read_only': True, 'cap_drop': ['ALL'],
                'security_opt': ['no-new-privileges:true'],
                'environment': {'ADMIN_USER': os.getenv('ADMIN_USER', 'admin'),
                    'ADMIN_PASS': os.getenv('ADMIN_PASS') or getpass.getpass('Senha interna do qBittorrent: '),
                    'DASHBOARD_PASSWORD': password, 'PUBLIC_HOSTNAME': hostname,
                    'ARCHIVE_ENABLED': str(archive_enabled).lower(),
                    'DASHBOARD_ALLOWED_ORIGINS': f'http://{host_ip}:8090',
                    'VPN_ENABLED': os.getenv('VPN_ENABLED', 'false')},
                'volumes': [f'{configs / app}:/configs/{app}:ro' for app in ('radarr', 'sonarr', 'seerr')] + [f'{media}:/media:ro', f'{proxy_key}:/run/archive/proxy-key:ro'],
                'networks': ['default', 'archive-private'],
            },
            'cloudflared': {
                'profiles': ['tunnel'], 'image': 'cloudflare/cloudflared:latest',
                'restart': 'unless-stopped', 'user': '0:0', 'read_only': True,
                'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
                'command': ['tunnel', '--config', '/etc/cloudflared/config.yml', '--no-autoupdate', 'run'],
                'volumes': [f'{secret_dir / "runtime"}:/etc/cloudflared:ro'], 'depends_on': ['dashboard'],
            },
            'archive': {
                'profiles': ['archive'], 'build': {'context': '.', 'dockerfile': 'portal/Dockerfile.archive'},
                'restart': 'unless-stopped', 'user': '568:568', 'group_add': ['1000'],
                'read_only': True, 'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
                'tmpfs': ['/tmp'], 'environment': {'XDG_CACHE_HOME': '/tmp/rclone-cache'},
                'volumes': [f'{media}:/media:ro', f'{root / ".archive-data"}:/data',
                            f'{archive_dir}:/run/archive:ro', f'{secret_dir / "rclone"}:/run/rclone'],
                'networks': ['archive-private'], 'depends_on': ['telegram-bot-api'],
            },
            'telegram-bot-api': {
                'profiles': ['archive'], 'build': {'context': '.', 'dockerfile': 'portal/Dockerfile.telegram'},
                'restart': 'unless-stopped', 'user': '568:568', 'group_add': ['1000'],
                'read_only': True, 'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
                'env_file': [str(telegram_env)],
                'volumes': [f'{media}:/media:ro', f'{root / ".telegram-data"}:/var/lib/telegram-bot-api'],
                'networks': ['archive-private'],
            },
        },
        'networks': {'default': {'external': True, 'name': network}, 'archive-private': {}},
    }
    compose = root / 'compose.portal.json'
    compose.write_text(json.dumps(config, indent=2), encoding='utf-8')
    compose.chmod(0o600)
    previous.write_text(json.dumps({'password': password, 'remote': remote, 'hostname': hostname, 'archive': archive_enabled}), encoding='utf-8')
    previous.chmod(0o600)
    command = ['docker', 'compose', '-f', str(compose)]
    subprocess.run(command + ['config', '--quiet'], check=True)
    subprocess.run(command + ['up', '-d', '--build', 'dashboard'], check=True)
    subprocess.run(command + ['--profile', 'tunnel'] + (['up', '-d', 'cloudflared'] if remote else ['stop', 'cloudflared']), check=True)
    if archive_enabled:
        print('Compilando o Bot API a partir do codigo oficial Telegram (primeira vez pode levar varios minutos).', flush=True)
        subprocess.run(command + ['--profile', 'archive', 'up', '-d', '--build', 'archive', 'telegram-bot-api'], check=True)
    else:
        subprocess.run(command + ['--profile', 'archive', 'stop', 'archive', 'telegram-bot-api'], check=True)
    print(f'Homepage: http://{host_ip}:8090 | usuario: {os.getenv("ADMIN_USER", "admin")} | senha: {password}')
    if remote:
        print('Acesso externo: https://' + hostname)
    print(f'Gerenciar: docker compose -f {compose} ps')


if __name__ == '__main__':
    try:
        main()
    except (KeyError, OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(f'Falha ao instalar homepage: {error}') from None
