import hashlib
from http.server import ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sala_archive', ROOT / 'portal/archive.py')
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.media = self.root / 'media'
        self.media.mkdir()
        self.catalog = archive.Catalog(self.root / 'state')
        self.config = {'media_root': str(self.media), 'drive_remote': 'sala:MediaStack',
                       'telegram_bot_token': 'test-only-not-a-real-token', 'telegram_chat_id': '-1001234567890'}

    def tearDown(self):
        self.temp.cleanup()

    def file(self, name='movies/Example.mp4', data=b'unit-test-content'):
        path = self.media / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        os.utime(path, (10, 10))
        return path

    def scan(self):
        self.catalog.scan(self.media, now=1000)
        return self.catalog.scan(self.media, now=1121)

    def item(self):
        return self.catalog.listing()['items'][0]


class CatalogTests(Fixture):
    def test_stable_files_only_and_incremental_restart(self):
        self.file()
        self.assertEqual(self.catalog.scan(self.media, now=1000), 0)
        self.assertEqual(self.catalog.scan(self.media, now=1050), 0)
        self.assertEqual(self.catalog.scan(self.media, now=1121), 1)
        self.assertEqual(self.item()['drive_state'], 'pending')
        code = self.item()['code']
        restarted = archive.Catalog(self.root / 'state')
        self.assertEqual(restarted.scan(self.media, now=1300), 0)
        self.assertEqual(restarted.item(code)['sha256'], hashlib.sha256(b'unit-test-content').hexdigest())

    def test_drive_everything_telegram_only_what_fits(self):
        self.file('movies/small.mp4', b'ab')
        self.file('movies/large.mp4', b'abcde')
        with patch.object(archive, 'TELEGRAM_LIMIT', 3):
            self.scan()
        items = self.catalog.listing()['items']
        self.assertTrue(all(item['drive_state'] == 'pending' for item in items))
        self.assertEqual({item['size']: item['telegram_state'] for item in items}, {2: 'pending', 5: 'too_large'})

    def test_photos_music_series_movies_and_other_categories(self):
        for name in ('movies/film.mkv', 'tv/episode.mkv', 'albums/photo.heic', 'albums/music.flac', 'documents/file.pdf'):
            self.file(name, name.encode())
        self.scan()
        self.assertEqual({item['category'] for item in self.catalog.listing()['items']}, {'Filmes', 'Series', 'Fotos', 'Musicas', 'Outros'})

    def test_incomplete_downloads_hidden_files_and_symlinks_not_archived(self):
        for name in ('downloads/film.mp4', 'photos/in-progress.part', '.private/key', 'photos/.hidden'):
            self.file(name)
        self.file('photos/real.jpg')
        self.scan()
        self.assertEqual(self.catalog.listing()['files'], 1)
        self.assertEqual(self.item()['relative_path'], 'photos/real.jpg')

    def test_changed_original_keeps_previous_version(self):
        path = self.file()
        self.scan()
        first = self.item()['code']
        path.write_bytes(b'new-version')
        os.utime(path, (20, 20))
        self.catalog.scan(self.media, now=1300)
        self.catalog.scan(self.media, now=1421)
        self.assertEqual(self.catalog.listing()['files'], 2)
        self.assertEqual(self.catalog.item(first)['sha256'], hashlib.sha256(b'unit-test-content').hexdigest())

    def test_identical_content_deduplicated(self):
        self.file('photos/a.jpg')
        self.file('photos/b.jpg')
        self.scan()
        self.assertEqual(self.catalog.listing()['files'], 1)

    def test_duplicate_source_recovers_after_selected_copy_is_removed(self):
        self.file('photos/a.jpg')
        self.file('photos/b.jpg')
        self.scan()
        selected = self.item()['relative_path']
        (self.media / selected).unlink()
        self.catalog.scan(self.media, now=1400)
        self.assertTrue((self.media / self.item()['relative_path']).is_file())
        self.assertEqual(self.catalog.listing()['files'], 1)

    def test_search_handles_portuguese_accents_and_case(self):
        self.file('photos/Férias em Família.jpg')
        self.scan()
        self.assertEqual(self.catalog.listing(query='FERIAS EM FAMILIA')['total'], 1)

    def test_search_code_category_and_sql_wildcards(self):
        self.file('photos/100% family.jpg', b'photo')
        self.file('music/song.mp3', b'music')
        self.scan()
        self.assertEqual(self.catalog.listing(query='%')['total'], 1)
        self.assertEqual(self.catalog.listing(query="' OR 1=1--")['total'], 0)
        self.assertEqual(self.catalog.listing(category='Fotos')['total'], 1)
        self.assertEqual(self.catalog.listing(query=self.item()['code'])['total'], 1)

    def test_nfo_metadata_read_without_inventing_descriptions(self):
        video = self.file()
        self.file('movies/Example.nfo', b'<movie><title>Example title</title><plot>Original description</plot></movie>')
        title, description, category = archive.file_metadata(video, 'movies/Example.mp4')
        self.assertEqual((title, description, category), ('Example title', 'Original description', 'Filmes'))

    def test_restore_is_transactional_and_refuses_overwriting_existing_catalog(self):
        self.file()
        self.scan()
        data = self.catalog.export()
        restored = archive.Catalog(self.root / 'restored')
        restored.restore(data)
        self.assertEqual(restored.listing()['items'], self.catalog.listing()['items'])
        with self.assertRaises(ValueError):
            self.catalog.restore(data)
        broken = archive.Catalog(self.root / 'broken')
        bad = json.loads(json.dumps(data))
        bad['items'].append(dict(data['items'][0], code='INVALID'))
        with self.assertRaises(ValueError):
            broken.restore(bad)
        self.assertEqual(broken.listing()['files'], 0)

    def test_export_does_not_contain_runtime_credentials(self):
        self.file()
        self.scan()
        text = json.dumps(self.catalog.export())
        self.assertNotIn(self.config['telegram_bot_token'], text)
        self.assertNotIn('refresh_token', text)
        self.assertNotIn('proxy-key', text)

    def test_pause_blocks_new_uploads_but_not_catalog_reads(self):
        self.file()
        self.scan()
        self.catalog.setting('paused', 'true')
        self.assertIsNone(self.catalog.queue_item('drive'))
        self.assertIsNone(self.catalog.queue_item('telegram'))
        self.assertEqual(self.catalog.listing()['files'], 1)


class ProviderTests(Fixture):
    def test_drive_verifies_remote_md5_and_never_syncs_deletes_or_shares(self):
        self.file()
        self.scan()
        item = self.item()
        drive = archive.Drive(self.config)
        metadata = {'Size': item['size'], 'Hashes': {'MD5': item['md5']}, 'ID': 'private-drive-id'}
        with patch.object(drive, 'run', side_effect=[b'', json.dumps(metadata).encode()]) as run:
            result = drive.upload(item, self.media)
        self.assertIn('--immutable', run.call_args_list[0].args)
        self.assertIn('--checksum', run.call_args_list[0].args)
        self.assertIn(item['sha256'], result['path'])
        self.assertNotIn('sync', str(run.call_args_list))
        self.assertNotIn('delete', str(run.call_args_list))
        self.assertNotIn('public', str(run.call_args_list))
        self.catalog.complete('drive', item['code'], result)
        saved = self.catalog.item(item['code'])
        self.assertEqual(saved['drive_state'], 'verified')
        self.assertEqual(drive.object_path(dict(saved, relative_path='new.extension')), result['path'])

    def test_drive_checksum_mismatch_is_not_success(self):
        self.file()
        self.scan()
        item = self.item()
        drive = archive.Drive(self.config)
        with patch.object(drive, 'run', side_effect=[b'', json.dumps({'Size': item['size'], 'Hashes': {'MD5': 'wrong'}, 'ID': 'id'}).encode()]):
            with self.assertRaises(archive.ArchiveError):
                drive.upload(item, self.media)
        self.assertEqual(self.item()['drive_state'], 'pending')

    def test_outside_library_and_foreign_remote_rejected(self):
        outside = self.root / 'secret'
        outside.write_text('do-not-upload')
        with self.assertRaises(archive.ArchiveError):
            archive.source_file(self.media, '../secret')
        self.file()
        self.scan()
        with self.assertRaises(archive.ArchiveError):
            archive.Drive(self.config).object_path(dict(self.item(), drive_path='other:secret'))

    def test_telegram_preserves_document_bytes_encodes_local_uri_and_private_link(self):
        self.file('photos/family & friends.jpg')
        self.scan()
        item = self.item()
        item.update(title='<b>Not HTML</b>', description='A & B')
        telegram = archive.Telegram(self.config)
        with patch.object(telegram, 'call', return_value={'message_id': 42, 'document': {'file_size': item['size']}}) as call:
            result = telegram.upload(item, self.media)
        self.assertEqual(call.call_args.args[0], 'sendDocument')
        payload = call.call_args.args[1]
        self.assertTrue(payload['document'].startswith('file:'))
        self.assertIn('%20', payload['document'])
        self.assertIn('&lt;b&gt;', payload['caption'])
        self.assertIn('A &amp; B', payload['caption'])
        self.assertIn(item['code'], payload['caption'])
        self.assertEqual(result['url'], 'https://t.me/c/1234567890/42')

    def test_telegram_oversize_and_unconfirmed_size_never_succeed(self):
        self.file()
        self.scan()
        telegram = archive.Telegram(self.config)
        with patch.object(telegram, 'call') as call:
            with self.assertRaises(archive.ArchiveError):
                telegram.upload(dict(self.item(), size=archive.TELEGRAM_LIMIT + 1), self.media)
            call.assert_not_called()
        with patch.object(telegram, 'call', return_value={'message_id': 42, 'document': {'file_size': 1}}):
            with self.assertRaises(archive.ArchiveError):
                telegram.upload(self.item(), self.media)

    def test_drive_failure_does_not_block_telegram_and_retries_back_off(self):
        self.file()
        self.scan()
        drive, telegram = archive.Drive(self.config), archive.Telegram(self.config)
        worker = archive.Worker(self.config, self.catalog, drive=drive, telegram=telegram)
        with patch.object(drive, 'upload', side_effect=archive.ArchiveError('quota indisponivel')):
            self.assertTrue(worker.process_one('drive'))
        with patch.object(telegram, 'upload', return_value={'message_id': 1, 'url': 'https://t.me/c/1234567890/1'}):
            self.assertTrue(worker.process_one('telegram'))
        self.assertEqual(self.item()['drive_state'], 'failed')
        self.assertEqual(self.item()['telegram_state'], 'uploaded')
        self.assertGreater(self.item()['next_drive'], time.time())
        self.assertIsNone(self.catalog.queue_item('drive'))

    def test_missing_source_does_not_pause_other_uploads(self):
        self.file()
        self.scan()
        worker = archive.Worker(self.config, self.catalog)
        with patch.object(worker.drive, 'upload', side_effect=archive.StaleSource('original ausente')):
            worker.process_one('drive')
        self.assertEqual(self.item()['drive_state'], 'source_missing')
        self.assertEqual(self.catalog.setting('pause_drive'), '')

    def test_telegram_server_retry_after_is_respected(self):
        self.file()
        self.scan()
        worker = archive.Worker(self.config, self.catalog)
        with patch.object(worker.telegram, 'upload', side_effect=archive.RetryLater('aguarde', 7200)):
            worker.process_one('telegram')
        self.assertGreater(float(self.catalog.setting('pause_telegram')), time.time() + 7190)
        self.assertGreater(self.item()['next_telegram'], time.time() + 7190)
        self.assertIsNotNone(self.catalog.queue_item('drive'))


class WorkerHttpTests(Fixture):
    def setUp(self):
        super().setUp()
        self.file()
        self.scan()
        self.worker = archive.Worker(self.config, self.catalog)
        self.key = 'unit-test-proxy-key-not-a-real-key'
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), archive.make_handler(self.worker, self.key))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def call(self, path, payload=None, key=True, headers=None):
        headers = dict(headers or {})
        if key:
            headers['Authorization'] = 'Bearer ' + self.key
        request = urllib.request.Request(self.base + path, json.dumps(payload).encode() if payload is not None else None, headers)
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.code, response.read(), response.headers

    def test_internal_api_requires_proxy_key(self):
        self.assertEqual(self.call('/catalog', key=False)[0], 401)
        self.assertEqual(self.call('/catalog')[0], 200)

    def test_pause_resume_and_reject_arbitrary_actions(self):
        self.assertEqual(self.call('/control', {'action': 'pause'})[0], 200)
        self.assertEqual(self.catalog.setting('paused'), 'true')
        self.assertEqual(self.call('/control', {'action': 'resume'})[0], 200)
        self.assertEqual(self.call('/control', {'action': 'delete'})[0], 400)
        self.assertEqual(self.call('/control', [1, 2])[0], 400)

    def test_pending_copy_cannot_stream_and_export_is_authenticated(self):
        self.assertEqual(self.call('/file/' + self.item()['code'])[0], 409)
        self.assertEqual(self.call('/export', key=False)[0], 401)
        self.assertEqual(json.loads(self.call('/export')[1])['version'], 1)

    def test_byte_range_rules(self):
        self.assertEqual(archive.byte_range('bytes=2-4', 10), (2, 4, True))
        self.assertEqual(archive.byte_range('bytes=-3', 10), (7, 9, True))
        self.assertEqual(archive.byte_range('bytes=8-', 10), (8, 9, True))
        for header in ('bytes=10-', 'bytes=4-2', 'bytes=1-2,4-5', 'bytes=-0', 'bytes=-'):
            with self.assertRaises(ValueError):
                archive.byte_range(header, 10)

    def test_range_stream_uses_bounded_rclone_cat(self):
        item = self.item()
        target = self.worker.drive.object_path(item)
        self.catalog.complete('drive', item['code'], {'id': 'id', 'url': 'https://drive.google.com/file/d/id/view', 'path': target})
        class Process:
            def __init__(self):
                self.stdout = io.BytesIO(b'nit-')
            def poll(self): return 0
            def wait(self, timeout=0): return 0
        with patch.object(archive.subprocess, 'Popen', return_value=Process()) as popen:
            status, body, headers = self.call('/file/' + item['code'], headers={'Range': 'bytes=1-4'})
        self.assertEqual(status, 206)
        self.assertEqual(body, b'nit-')
        self.assertEqual(headers['Content-Range'], 'bytes 1-4/' + str(item['size']))
        self.assertIn('--offset', popen.call_args.args[0])
        self.assertIn('--count', popen.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
