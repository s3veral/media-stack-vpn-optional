"""Reuse upstream setup functions without executing its TrueNAS/ZFS installer.

Emits Bash to stdout. No network and no third-party Python dependencies.
"""
from pathlib import Path
import re
import sys


def build(source):
    helpers = source[source.index('# \u2500\u2500 Helpers'):source.index('# \u2500\u2500 Pre-flight')]
    configure = source[source.index('export ADMIN_USER ADMIN_PASS ADMIN_EMAIL CONFIG_POOL MEDIA_POOL'):]
    configure = configure.split('# Optional portal installer')[0]
    configure = configure.replace('/mnt/$CONFIG_POOL/configs', '/configs')
    hosts = {8080: 'qbittorrent', 7878: 'radarr', 8989: 'sonarr',
             9696: 'prowlarr', 8191: 'flaresolverr', 6868: 'profilarr',
             8096: 'jellyfin', 5055: 'seerr', 6767: 'bazarr', 7476: 'qui'}
    for port, host in hosts.items():
        configure = configure.replace(f'http://localhost:{port}', f'http://{host}:{port}')
    # Summary is for the host browser, rather than Docker DNS names.
    configure = configure.replace('http://$HOST_IP:$port', 'http://localhost:$port')
    # A failed setup step is visible to Compose/PowerShell, not just a log message.
    configure += '\n[ "${#TODO_LIST[@]}" -eq 0 ] || exit 2\n'
    header = '''#!/usr/bin/env bash
set -uo pipefail
: "${ADMIN_USER:=admin}" "${ADMIN_PASS:?ADMIN_PASS is required}" "${ADMIN_EMAIL:=admin@localhost.local}"
: "${VPN_ENABLED:=false}" "${QBIT_NETWORK_INTERFACE:=}"
CONFIG_POOL=unused
MEDIA_POOL=unused
HOST_IP=host.docker.internal
export VPN_ENABLED QBIT_NETWORK_INTERFACE
'''
    result = header + helpers + configure
    if re.search(r'/mnt/\$|http://localhost:', result.split('# \u2500\u2500 Summary')[0]):
        raise ValueError('Unconverted TrueNAS path or localhost API URL')
    return result


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    source = Path(__file__).resolve().parents[1] / 'truenas-file-structure.sh'
    print(build(source.read_text(encoding='utf-8')))
