"""Provision a named Cloudflare Tunnel and DNS using a user-supplied API token.

API token is read from the environment or hidden terminal prompt, never saved.
The connector token is written only to the local secrets directory.
"""
import getpass
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


def provision(hostname, api_token, secret_dir, call=None):
    hostname = hostname.strip().lower()
    if not re.fullmatch(r'(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', hostname):
        raise ValueError('Use um hostname completo, por exemplo sala.exemplo.com.br.')

    def api(method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request('https://api.cloudflare.com/client/v4/' + path, data,
                                     {'Authorization': 'Bearer ' + api_token, 'Content-Type': 'application/json'}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=25) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f'Cloudflare HTTP {error.code}. Confira as permissoes do API token.') from None
        if not result.get('success'):
            raise RuntimeError('A Cloudflare rejeitou a operacao. Confira o token e o dominio.')
        return result['result']

    api = call or api
    # Read only zones accessible to the supplied scoped token; support .com.br.
    zones = []
    for page in range(1, 21):
        batch = api('GET', f'zones?per_page=50&page={page}&status=active')
        zones.extend(batch)
        if len(batch) < 50:
            break
    matches = [zone for zone in zones if hostname.endswith('.' + zone['name'])]
    if not matches:
        raise ValueError('Nenhuma zona DNS ativa corresponde ao subdominio. O dominio deve estar na sua Cloudflare e o token precisa de Zone Read.')
    zone = max(matches, key=lambda item: len(item['name']))
    account = zone['account']['id']
    name = 'media-stack-' + hostname.replace('.', '-')
    tunnels = api('GET', f'accounts/{account}/cfd_tunnel?' + urllib.parse.urlencode({'name': name, 'is_deleted': 'false'}))
    dns_path = f'zones/{zone["id"]}/dns_records'
    records = api('GET', dns_path + '?' + urllib.parse.urlencode({'name': hostname}))
    tunnel = next((item for item in tunnels if item['name'] == name), None)
    if records and not tunnel:
        raise ValueError('Este hostname ja possui um registro DNS. Escolha um subdominio livre; nenhum registro foi substituido.')
    if not tunnel:
        tunnel = api('POST', f'accounts/{account}/cfd_tunnel', {'name': name, 'config_src': 'cloudflare'})
    tunnel_id = tunnel['id']
    target = tunnel_id + '.cfargotunnel.com'
    if records and any(item['type'] != 'CNAME' or item.get('content') != target for item in records):
        raise ValueError('O DNS existente aponta para outro servico; nenhum registro foi substituido.')
    config_path = f'accounts/{account}/cfd_tunnel/{tunnel_id}/configurations'
    existing = api('GET', config_path).get('config') or {}
    routes = [item for item in existing.get('ingress', []) if item.get('hostname') and item['hostname'] != hostname]
    routes += [{'hostname': hostname, 'service': 'http://dashboard:8080'}, {'service': 'http_status:404'}]
    api('PUT', config_path, {'config': {**existing, 'ingress': routes}})
    if not records:
        api('POST', dns_path, {'type': 'CNAME', 'name': hostname, 'content': target, 'proxied': True, 'ttl': 1})
    token = api('GET', f'accounts/{account}/cfd_tunnel/{tunnel_id}/token')
    secret_dir = Path(secret_dir)
    secret_dir.mkdir(parents=True, exist_ok=True)
    token_file = secret_dir / 'tunnel_token'
    token_file.write_text(token, encoding='utf-8')
    token_file.chmod(0o600)
    metadata = {'hostname': hostname, 'tunnelId': tunnel_id, 'zoneId': zone['id'], 'accountId': account}
    (secret_dir / 'tunnel.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    return metadata


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        hostname = os.getenv('PUBLIC_HOSTNAME') or input('Subdominio completo (sala.exemplo.com): ')
        token = os.getenv('CLOUDFLARE_API_TOKEN') or getpass.getpass('Cloudflare API token (entrada oculta): ')
        result = provision(hostname, token, os.getenv('SECRET_DIR', '/secrets'))
        print('Tunnel e DNS prontos: https://' + result['hostname'])
    except (RuntimeError, ValueError, OSError) as error:
        raise SystemExit(str(error)) from None
