"""Validate generated ingress with the real cloudflared binary (no account needed)."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cloudflare_setup', ROOT / 'portal/cloudflare_setup.py')
cf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cf)


with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    (root / 'runtime').mkdir()
    credentials = {'TunnelID': '12345678-1234-4234-8234-123456789abc', 'AccountTag': 'test-only',
                   'TunnelSecret': 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='}
    (root / 'runtime/credentials.json').write_text(json.dumps(credentials))
    (root / 'tunnel.json').write_text(json.dumps({'hostname': 'sala.example.com',
        'tunnelId': credentials['TunnelID'], 'mode': 'browser-login'}))
    cf.provision('sala.example.com', root)
    subprocess.run(['cloudflared', 'tunnel', '--config', str(root / 'runtime/config.yml'),
                    'ingress', 'validate'], check=True)
    subprocess.run(['cloudflared', 'tunnel', '--config', str(root / 'runtime/config.yml'),
                    'ingress', 'rule', 'https://sala.example.com'], check=True)
    subprocess.run(['cloudflared', 'tunnel', '--origincert', str(root / 'cert.pem'),
                    'route', 'dns', '--help'], check=True, stdout=subprocess.DEVNULL)
print('PASS: official cloudflared accepts generated config, ingress and CLI options.')
