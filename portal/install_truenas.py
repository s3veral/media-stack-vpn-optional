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
    hostname = saved.get('hostname', '')
    if remote:
        hostname = input(f'Subdominio completo (Enter: {hostname or "sala.exemplo.com"}): ').strip() or hostname
    assets = ['portal/Dockerfile', 'portal/app.py', 'portal/cloudflare_setup.py',
              'portal/static/index.html', 'portal/static/style.css', 'portal/static/app.js']
    for asset in assets:
        target = root / asset
        target.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen('https://raw.githubusercontent.com/s3veral/media-stack-vpn-optional/main/' + asset, timeout=30) as response:
            target.write_bytes(response.read())
    sys.path.insert(0, str(root / 'portal'))
    secret_dir = root / '.secrets'
    secret_dir.mkdir(exist_ok=True, mode=0o700)
    token_file = secret_dir / 'tunnel_token'
    if not token_file.exists():
        token_file.touch(mode=0o600)
    if remote:
        metadata_file = secret_dir / 'tunnel.json'
        metadata = json.loads(metadata_file.read_text()) if metadata_file.exists() else {}
        if metadata.get('hostname') != hostname or not token_file.stat().st_size:
            print('Crie um API token Cloudflare com Cloudflare Tunnel Edit, DNS Edit e Zone Read para sua conta/zona.')
            print('O API token sera usado apenas agora; nao sera salvo no servidor.')
            from cloudflare_setup import provision
            provision(hostname, getpass.getpass('Cloudflare API token (oculto): '), secret_dir)
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
                    'DASHBOARD_ALLOWED_ORIGINS': f'http://{host_ip}:8090',
                    'VPN_ENABLED': os.getenv('VPN_ENABLED', 'false')},
                'volumes': [f'{configs / app}:/configs/{app}:ro' for app in ('radarr', 'sonarr', 'seerr')] + [f'{media}:/media:ro'],
            },
            'cloudflared': {
                'profiles': ['tunnel'], 'image': 'cloudflare/cloudflared:latest',
                'restart': 'unless-stopped', 'user': '0:0', 'read_only': True,
                'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
                'command': ['tunnel', '--no-autoupdate', 'run', '--token-file', '/run/secrets/tunnel_token'],
                'secrets': ['tunnel_token'], 'depends_on': ['dashboard'],
            },
        },
        'secrets': {'tunnel_token': {'file': str(token_file)}},
        'networks': {'default': {'external': True, 'name': network}},
    }
    compose = root / 'compose.portal.json'
    compose.write_text(json.dumps(config, indent=2), encoding='utf-8')
    compose.chmod(0o600)
    previous.write_text(json.dumps({'password': password, 'remote': remote, 'hostname': hostname}), encoding='utf-8')
    previous.chmod(0o600)
    command = ['docker', 'compose', '-f', str(compose)]
    subprocess.run(command + ['config', '--quiet'], check=True)
    subprocess.run(command + ['up', '-d', '--build', 'dashboard'], check=True)
    subprocess.run(command + ['--profile', 'tunnel'] + (['up', '-d', 'cloudflared'] if remote else ['stop', 'cloudflared']), check=True)
    print(f'Homepage: http://{host_ip}:8090 | usuario: {os.getenv("ADMIN_USER", "admin")} | senha: {password}')
    if remote:
        print('Acesso externo: https://' + hostname)
    print(f'Gerenciar: docker compose -f {compose} ps')


if __name__ == '__main__':
    try:
        main()
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f'Falha ao instalar homepage: {error}') from None
