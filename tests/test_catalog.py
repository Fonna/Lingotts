import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

import ted_server
import video_catalog as vc


class CatalogFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.media = self.root / "media"
        self.directory = self.root / "catalog"
        self.media.mkdir()
        self.directory.mkdir()
        self.folder = 'Talk #1 & 你好？'
        self.add_folder(self.folder)

    def tearDown(self):
        self.temp.cleanup()

    def add_folder(self, name):
        folder = self.media / name
        folder.mkdir()
        (folder / 'clip #1 & 你好.wav').write_bytes(b'RIFFtest-audio')
        (folder / 'transcript.txt').write_text('Title: A talk | Speaker | TED\nSource: https://example.org/talk\n'
            '[0.0s -> 1.0s] Hello <script>alert(1)</script>.\n[1.0s -> 2.0s] Second sentence.\n', encoding='utf-8')
        return folder

    def save(self, folder=None, fields=None, video_id=None):
        return vc.save_video(folder or self.folder, fields or {}, video_id, self.directory, self.media)


class CatalogTests(CatalogFixture):
    def test_cli_unicode_output_works_when_windows_pipe_defaults_to_gbk(self):
        script = str(Path(__file__).resolve().parents[1] / 'scripts' / 'import_video.py')
        code = ('import runpy,sys,video_catalog; '
                'video_catalog.catalog=lambda: {"videos":[{"title":"Unicode \\u2212"}],"issues":[]}; '
                'sys.argv=["import_video.py","--check"]; '
                f'runpy.run_path({script!r},run_name="__main__")')
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, encoding='utf-8',
                                env=dict(os.environ, PYTHONIOENCODING='gbk'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['videos'][0]['title'], 'Unicode −')


    def test_import_inspects_headers_and_encodes_each_path_component(self):
        record = self.save()
        self.assertEqual(record['title'], 'A talk')
        self.assertEqual(record['speaker'], 'Speaker')
        self.assertEqual(record['duration_s'], 2)
        data = vc.catalog(self.directory, self.media)
        self.assertFalse(data['issues'])
        video = data['videos'][0]
        self.assertIn('%23', video['media_url'])
        self.assertIn('%EF%BC%9F', video['media_url'])
        self.assertEqual(urllib.parse.unquote(video['media_url']), '/TED/' + self.folder + '/clip #1 & 你好.wav')
        self.assertEqual(vc.resolve_video(self.folder, data['videos'])['id'], record['id'])

    def test_new_content_appears_without_modifying_pages(self):
        self.save()
        self.add_folder('Second')
        self.save('Second', {'title': 'Another talk', 'category': 'New category'})
        data = vc.catalog(self.directory, self.media)
        self.assertEqual(len(data['videos']), 2)
        self.assertFalse(data['issues'])

    def test_directory_rename_preserves_id_aliases_and_old_vocabulary(self):
        old = self.save()
        (self.media / self.folder).rename(self.media / 'Renamed')
        updated = self.save('Renamed', {'title': 'New title'}, old['id'])
        self.assertEqual(updated['id'], old['id'])
        videos = vc.catalog(self.directory, self.media)['videos']
        self.assertEqual(vc.resolve_video(self.folder, videos)['title'], 'New title')
        entries = [{'slug': self.folder, 'word': 'hello'}]
        self.assertEqual(ted_server.canonical_vocab(entries, videos)[0]['slug'], old['id'])
        self.assertEqual(entries[0]['slug'], self.folder)

    def test_duplicate_import_and_alias_collision_are_rejected_without_writes(self):
        first = self.save()
        with self.assertRaisesRegex(ValueError, '已被导入'):
            self.save()
        self.add_folder('Other')
        second = self.save('Other')
        with self.assertRaises(ValueError):
            self.save(self.folder, {}, second['id'])
        self.assertEqual(len(list(self.directory.glob('*.json'))), 2)
        self.assertEqual(vc.resolve_video(self.folder, vc.catalog(self.directory, self.media)['videos'])['id'], first['id'])

    def test_invalid_metadata_leaves_existing_file_untouched(self):
        first = self.save()
        path = self.directory / (first['id'] + '.json')
        original = path.read_bytes()
        for fields in ({'title': ''}, {'youtube': 'javascript:alert(1)'}, {'tags': 'oops'},
                       {'published': '2026-02-30'}, {'duration_s': float('nan')},
                       {'duration_s': 10**400},
                       {'media_file': '../outside.wav'}, {'id': 'override'}, {'folder': 'C:/Windows'},
                       {'transcript_file': 'missing.txt'}, {'summary_en': None}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.save(fields=fields, video_id=first['id'])
            self.assertEqual(path.read_bytes(), original)

    def test_bad_transcripts_are_rejected(self):
        path = self.media / self.folder / 'transcript.txt'
        for text in ('[2s -> 1s] Bad', '[0s -> 1s] OK\n[0.1s -> 0.1s] Bad',
                     '[2s -> 3s] One\n[1s -> 2s] Backward', '[oops] Bad', 'No timestamps'):
            path.write_text(text, encoding='utf-8')
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.save()
        self.assertFalse(list(self.directory.glob('*.json')))

    def test_paths_cannot_escape_root_or_follow_symlinks(self):
        for value in ('../media-sibling/file', '/absolute', 'C:/Windows', 'a\\b', 'a/../b', ''):
            with self.subTest(value=value), self.assertRaises(ValueError):
                vc.contained_path(self.media, value)
        outside = self.root / 'outside'
        outside.mkdir()
        try:
            (self.media / 'link').symlink_to(outside, target_is_directory=True)
        except OSError:
            return  # Windows installations may not grant symlink privileges.
        with self.assertRaises(ValueError):
            vc.contained_path(self.media, 'link')

    def test_broken_record_does_not_hide_valid_videos(self):
        record = self.save()
        (self.directory / 'broken.json').write_text('{broken', encoding='utf-8')
        data = vc.catalog(self.directory, self.media)
        self.assertEqual(data['videos'][0]['id'], record['id'])
        self.assertEqual(len(data['issues']), 1)

    def test_corrupt_identity_is_reported_without_hiding_other_content(self):
        self.save()
        self.add_folder('Other')
        second = self.save('Other')
        second['aliases'] = [['invalid nested alias']]
        (self.directory / (second['id'] + '.json')).write_text(json.dumps(second), encoding='utf-8')
        data = vc.catalog(self.directory, self.media)
        self.assertEqual(len(data['videos']), 1)
        self.assertEqual(len(data['issues']), 1)
        with self.assertRaises(ValueError):
            self.save('Other', {'title':'repair'}, second['id'])

    def test_ambiguous_media_requires_explicit_selection(self):
        (self.media / self.folder / 'other.wav').write_bytes(b'other')
        with self.assertRaises(ValueError):
            self.save()
        record = self.save(fields={'media_file': 'other.wav'})
        self.assertEqual(record['media_file'], 'other.wav')


class CatalogAPITests(CatalogFixture):
    def setUp(self):
        super().setUp()
        self.vocab = self.root / 'vocab.json'
        self.vocab.write_text(json.dumps({'entries': []}), encoding='utf-8')
        self.patches = [patch.object(ted_server, 'CATALOG_DIR', self.directory),
                        patch.object(ted_server, 'MEDIA_DIR', self.media),
                        patch.object(ted_server, 'VOCAB_FILE', self.vocab)]
        for item in self.patches:
            item.start()
        self.server = ted_server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), ted_server.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        for item in reversed(self.patches):
            item.stop()
        super().tearDown()

    def request(self, path, data=None, headers=None):
        request = urllib.request.Request(self.url + path, data=json.dumps(data).encode() if data is not None else None,
            headers=headers or ({'Content-Type': 'application/json'} if data is not None else {}))
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error)

    def test_api_import_list_legacy_lookup_and_transcript(self):
        status, data = self.request('/api/videos', {'folder': self.folder, 'metadata': {'title': 'Imported'}})
        self.assertEqual(status, 201)
        video_id = data['video']['id']
        self.assertEqual(len(self.request('/api/videos')[1]['videos']), 1)
        old_link = '/api/video?video=' + urllib.parse.quote(self.folder, safe='')
        self.assertEqual(self.request(old_link)[1]['video']['id'], video_id)
        status, data = self.request('/api/transcript?video=' + video_id)
        self.assertEqual(status, 200)
        self.assertEqual(len(data['segments']), 2)
        self.assertTrue(data['segments'][0]['id'].startswith(video_id + ':'))
        self.assertEqual(self.request('/api/video?video=unknown')[0], 404)
        folders = self.request('/api/video-folders')[1]['folders']
        self.assertEqual(folders[0]['video_id'], video_id)

    def test_api_rejects_cross_origin_invalid_body_and_duplicate(self):
        data = {'folder': self.folder, 'metadata': {}}
        self.assertEqual(self.request('/api/videos', data, {'Content-Type': 'application/json', 'Origin':'https://outside.example'})[0], 403)
        self.assertEqual(self.request('/api/videos', data, {'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request('/api/videos', {'folder':'../escape'})[0], 400)
        self.assertEqual(self.request('/api/videos', dict(data, id=0))[0], 400)
        self.assertEqual(self.request('/api/videos', data)[0], 201)
        self.assertEqual(self.request('/api/videos', data)[0], 400)

    def test_api_can_repair_a_moved_folder_and_keep_old_link(self):
        original = self.save()
        (self.media / self.folder).rename(self.media / 'Moved')
        folders = self.request('/api/video-folders')[1]['folders']
        missing = next(f for f in folders if f['video_id'] == original['id'])
        self.assertIn('error', missing)
        status, data = self.request('/api/videos', {'id':original['id'], 'folder':'Moved', 'metadata':{}})
        self.assertEqual(status, 200)
        self.assertEqual(data['video']['id'], original['id'])
        self.assertEqual(self.request('/api/video?video=' + urllib.parse.quote(self.folder))[0], 200)

    def test_static_route_rejects_prefix_sibling_and_serves_encoded_range(self):
        outside = self.root / 'media-escape'
        outside.mkdir()
        (outside / 'secret.txt').write_text('private', encoding='utf-8')
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.url + '/TED/..%2Fmedia-escape/secret.txt')
        self.assertEqual(error.exception.code, 404)
        error.exception.close()
        path = '/TED/' + urllib.parse.quote(self.folder, safe='') + '/' + urllib.parse.quote('clip #1 & 你好.wav', safe='')
        request = urllib.request.Request(self.url + path, headers={'Range':'bytes=0-3'})
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(), b'RIFF')

    def test_old_vocab_and_new_id_deduplicate_without_rewriting_progress(self):
        record = self.save()
        old = {'id':'original', 'word':'hello', 'slug':self.folder, 't':0.0, 'sentence':'Hello', 'video_title':'A talk'}
        self.vocab.write_text(json.dumps({'entries':[old]}), encoding='utf-8')
        before = self.vocab.read_bytes()
        status, data = self.request('/api/vocab')
        self.assertEqual(data['entries'][0]['slug'], record['id'])
        self.assertEqual(self.vocab.read_bytes(), before)
        status, data = self.request('/api/vocab', dict(old, slug=record['id']))
        self.assertEqual(status, 200)
        self.assertTrue(data['deduplicated'])
        self.assertEqual(data['entry']['id'], 'original')
        self.assertEqual(self.vocab.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
