"""Local UI review with synthetic data; no cloud account or media is accessed."""
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'portal'))
os.environ['DASHBOARD_PASSWORD'] = 'preview-local-only-2026'
os.environ['DASHBOARD_ALLOWED_ORIGINS'] = 'http://127.0.0.1:18090'
import app

snapshot = {'generatedAt': int(time.time()), 'vpn': False, 'downloads': [], 'downloadTotal': 0,
            'queue': [], 'library': [], 'requests': [], 'disk': {'total': 5e12, 'used': 3e12, 'free': 2e12, 'percent': 60},
            'transfer': {}, 'services': [{'name': key, 'ok': True} for key in ('torrents', 'transfer', 'radarr', 'sonarr', 'requests')],
            'stats': {'movies': 0, 'moviesAvailable': 0, 'series': 0, 'episodes': 0, 'active': 0}}
items = [
    {'code': 'SAL-0123456789ABCDEF01234567', 'title': 'Viagem em família — álbum de verão', 'description': 'Fotos originais do álbum, preservadas sem compressão adicional.', 'category': 'Fotos', 'relative_path': 'photos/Viagem em família/IMG_2048.jpg', 'size': 18400000, 'drive_state': 'verified', 'telegram_state': 'uploaded', 'drive_url': 'https://drive.google.com/file/d/preview-id/view', 'telegram_url': 'https://t.me/c/1234567890/42'},
    {'code': 'SAL-1123456789ABCDEF01234567', 'title': 'Sessão de cinema — arquivo original', 'description': 'Arquivo acima do limite do Telegram. A cópia integral fica no Drive, sem dividir em partes.', 'category': 'Filmes', 'relative_path': 'movies/Sessão de cinema/Filme.mkv', 'size': 18400000000, 'drive_state': 'verified', 'telegram_state': 'too_large', 'drive_url': 'https://drive.google.com/file/d/preview-id/view'},
    {'code': 'SAL-2123456789ABCDEF01234567', 'title': 'Coleção de música — gravação ao vivo', 'description': 'Áudio original. As duas filas avançam de forma independente.', 'category': 'Musicas', 'relative_path': 'music/Ao vivo/01 - Abertura.flac', 'size': 420000000, 'drive_state': 'uploading', 'telegram_state': 'pending'},
    {'code': 'SAL-3123456789ABCDEF01234567', 'title': 'Série da biblioteca — episódio 01', 'description': 'Exemplo de erro temporário com nova tentativa agendada. O original não é removido.', 'category': 'Series', 'relative_path': 'tv/Série/Season 01/S01E01.mp4', 'size': 1400000000, 'drive_state': 'failed', 'telegram_state': 'uploaded', 'drive_error': 'Drive indisponível; nova tentativa agendada.', 'telegram_url': 'https://t.me/c/1234567890/43'},
]
catalog = {'enabled': True, 'items': items, 'files': 24681, 'bytes': 3e12, 'total': 4, 'page': 1, 'pageSize': 50,
           'paused': False, 'lastScan': str(time.time()), 'scanError': '', 'catalogBackupError': '',
           'quota': {'total': 5e12, 'free': 2e12},
           'summary': {'drive': {'verified': {'count': 18240, 'bytes': 2.2e12}},
                       'telegram': {'uploaded': {'count': 15409, 'bytes': 0.8e12}, 'too_large': {'count': 2711, 'bytes': 1.5e12}}}}


def call(path, payload=None):
    if payload:
        catalog['paused'] = payload.get('action') == 'pause'
        return {'ok': True}
    return catalog


app.snapshot = lambda: snapshot
app.archive_client.enabled = lambda: True
app.archive_client.call = call
print('Synthetic preview on http://127.0.0.1:18090', flush=True)
handler = app.Handler
if '--auto-login' in sys.argv:
    # Synthetic localhost-only screenshot fixture, never used by production.
    session = app.new_session()
    class DemoHandler(app.Handler):
        def session(self):
            return session
    handler = DemoHandler
app.ThreadingHTTPServer(('127.0.0.1', 18090), handler).serve_forever()
