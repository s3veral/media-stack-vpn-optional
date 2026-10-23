"""Provision Cloudflare through its official browser login, without API prompts.

Only the runtime directory is mounted into the persistent connector. The more
powerful account certificate stays in the installer's private login directory.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid


def write_private(path, content):
    path = Path(path)
    path.write_text(content, encoding='utf-8')
    path.chmod(0o600)


def valid_hostname(hostname):
    hostname = hostname.strip().lower()
    if not re.fullmatch(r'(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', hostname):
        raise ValueError('Use um subdominio completo, por exemplo sala.exemplo.com.br, sem https:// ou caminhos.')
    return hostname


class Cloudflared:
    def __init__(self, secret_dir, docker=False):
        self.root = Path(secret_dir).resolve()
        self.docker = docker

    def path(self, path):
        path = Path(path).resolve()
        if self.docker:
            return '/secrets/' + path.relative_to(self.root).as_posix()
        return str(path)

    def __call__(self, *args):
        login_home = self.path(self.root / 'login-home')
        if self.docker:
            command = ['docker', 'run', '--rm', '--user', '0:0',
                       '-e', 'HOME=' + login_home, '-v', str(self.root) + ':/secrets',
                       'cloudflare/cloudflared:latest']
            environment = None
        else:
            command = ['cloudflared']
            environment = dict(os.environ, HOME=login_home)
        # Inherit the terminal: cloudflared prints the unique official login URL.
        try:
            subprocess.run(command + list(args), env=environment, check=True)
        except subprocess.CalledProcessError:
            raise RuntimeError('A operacao Cloudflare falhou; veja a mensagem acima. Nenhum DNS existente foi sobrescrito. '
                               'Confira o dominio autorizado e tente novamente.') from None


def provision(hostname, secret_dir, runner=None, prompt=input):
    if hostname:
        hostname = valid_hostname(hostname)
    root = Path(secret_dir).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    runtime = root / 'runtime'
    runtime.mkdir(exist_ok=True, mode=0o700)
    metadata_file = root / 'tunnel.json'
    metadata = json.loads(metadata_file.read_text()) if metadata_file.exists() else {}
    hostname = hostname or metadata.get('hostname', '')
    same_host = hostname and metadata.get('hostname') == hostname
    credentials_file = runtime / 'credentials.json'

    def activate_local(credentials, selected_hostname):
        tunnel_id = str(uuid.UUID(credentials['TunnelID']))
        if not credentials.get('AccountTag') or not credentials.get('TunnelSecret'):
            raise ValueError('Credenciais do tunnel incompletas. Restaure o backup da pasta .secrets.')
        write_private(credentials_file, json.dumps(credentials))
        config = {'tunnel': tunnel_id, 'credentials-file': '/etc/cloudflared/credentials.json',
                  'ingress': [{'hostname': selected_hostname, 'service': 'http://dashboard:8080'},
                              {'service': 'http_status:404'}]}
        write_private(runtime / 'config.yml', json.dumps(config, indent=2))  # JSON is also valid YAML.
        result = {'hostname': selected_hostname, 'tunnelId': tunnel_id, 'mode': 'browser-login'}
        write_private(metadata_file, json.dumps(result, indent=2))
        return result

    if same_host and metadata.get('mode') == 'browser-login' and credentials_file.exists():
        credentials = json.loads(credentials_file.read_text())
        if credentials.get('TunnelID') != metadata.get('tunnelId'):
            raise ValueError('O ID nas credenciais difere do tunnel salvo. Restaure o backup da pasta .secrets.')
        return activate_local(credentials, hostname)

    # Preserve older API-token installations without replacing their DNS/tunnel.
    legacy_token = root / 'tunnel_token'
    if same_host and metadata.get('mode') != 'browser-login' and legacy_token.is_file() and legacy_token.stat().st_size:
        shutil.copyfile(legacy_token, runtime / 'tunnel_token')
        (runtime / 'tunnel_token').chmod(0o600)
        write_private(runtime / 'config.yml', json.dumps({'token-file': '/etc/cloudflared/tunnel_token'}, indent=2))
        return metadata

    login_dir = root / 'login-home' / '.cloudflared'
    login_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    certificate = login_dir / 'cert.pem'
    runner = runner or Cloudflared(root)
    if not certificate.is_file() or not certificate.stat().st_size:
        print('\nAbra o link oficial exibido abaixo no navegador (pode ser em outro computador).', flush=True)
        print('Entre na Cloudflare, selecione o dominio e clique em Authorize/Autorizar.', flush=True)
        print('O instalador aguardara a autorizacao. Nao e necessario criar nem colar um API token.\n', flush=True)
        runner('tunnel', 'login')
        if not certificate.is_file() or not certificate.stat().st_size:
            raise RuntimeError('A autorizacao nao gerou cert.pem. Reexecute e conclua a autorizacao pelo link.')
        certificate.chmod(0o600)
    else:
        print('Reutilizando a autorizacao Cloudflare local. O subdominio deve pertencer ao dominio autorizado.', flush=True)
    if not hostname:
        hostname = valid_hostname(prompt('Endereco completo do painel no dominio autorizado (ex.: sala.exemplo.com): '))
    else:
        hostname = valid_hostname(hostname)
    pending = root / 'tunnels'
    pending.mkdir(exist_ok=True, mode=0o700)
    key = hashlib.sha256(hostname.encode()).hexdigest()[:20]
    pending_file = pending / (key + '.json')
    prefix = ('tunnel', '--origincert', runner.path(certificate))
    if not pending_file.exists():
        runner(*prefix, 'create', '--credentials-file', runner.path(pending_file), 'media-stack-local-' + key)
    credentials = json.loads(pending_file.read_text())
    tunnel_id = str(uuid.UUID(credentials['TunnelID']))
    # No --overwrite-dns. Retry routing with saved credentials after a DNS error,
    # without making another tunnel or disturbing the current runtime files.
    runner(*prefix, 'route', 'dns', tunnel_id, hostname)
    return activate_local(credentials, hostname)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        result = provision(os.getenv('PUBLIC_HOSTNAME', ''), os.getenv('SECRET_DIR', '/secrets'))
        print('Tunnel e DNS prontos: https://' + result['hostname'])
    except (KeyError, RuntimeError, ValueError, OSError, EOFError) as error:
        raise SystemExit(str(error)) from None
