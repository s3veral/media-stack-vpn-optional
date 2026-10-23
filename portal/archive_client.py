"""Private, fixed-destination client for Sala's optional archive worker."""
import json
import os
from pathlib import Path
import urllib.request


def enabled():
    return os.getenv('ARCHIVE_ENABLED', 'false') == 'true'


def open_request(path, payload=None, headers=None):
    if not enabled():
        raise OSError('Arquivo em nuvem nao configurado.')
    key = Path(os.getenv('ARCHIVE_PROXY_KEY_FILE', '/run/archive/proxy-key')).read_text().strip()
    request_headers = dict(headers or {}, Authorization='Bearer ' + key)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        request_headers['Content-Type'] = 'application/json'
    # Never accept a host or URL from a browser request.
    request = urllib.request.Request('http://archive:8082' + path, data, request_headers)
    return urllib.request.urlopen(request, timeout=45)


def call(path, payload=None):
    with open_request(path, payload) as response:
        return json.load(response)
