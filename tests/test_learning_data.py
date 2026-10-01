import copy
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import learning_data as ld
import review_schedule as review
import ted_server

VIDEO = {'id':'a' * 32, 'folder':'renamed', 'aliases':['old-folder'], 'title':'Talk'}
ENTRY = {'id':'entry1', 'word':'hello', 'sentence':'Hello world.', 'slug':'old-folder',
         'video_title':'Talk', 't':12.5, 'created':'2026-09-30T22:29:32'}


class LearningDataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.vocab = self.root / 'vocab.json'
        self.review = self.root / 'review.json'
        self.vocab.write_text(json.dumps({'entries':[ENTRY]}), encoding='utf-8')
        self.review.write_text(json.dumps({'words':{'hello':review.next_state(None, 'known')}}), encoding='utf-8')

    def test_export_captures_complete_state_without_mutating_storage_or_resume(self):
        original = self.vocab.read_bytes(), self.review.read_bytes()
        last = {'slug':'old-folder', 't':20.5, 'title':'Talk'}
        data = ld.export_data(self.vocab, self.review, [VIDEO], last)
        self.assertEqual(data['format'], ld.FORMAT)
        self.assertEqual(data['schema_version'], 1)
        self.assertEqual(data['vocab']['entries'][0]['slug'], VIDEO['id'])
        self.assertEqual(data['resume']['last']['slug'], VIDEO['id'])
        self.assertEqual(last['slug'], 'old-folder')
        self.assertEqual(data['videos'][0]['aliases'], ['old-folder'])
        self.assertEqual(data['review']['words']['hello']['version'], 1)
        self.assertEqual((self.vocab.read_bytes(), self.review.read_bytes()), original)

    def test_unresolved_media_and_orphan_review_are_preserved_with_warnings(self):
        data = ld.export_data(self.vocab, self.review, [])
        self.assertEqual(data['vocab']['entries'][0]['slug'], 'old-folder')
        self.assertIn('保留原关联', data['warnings'][0])
        self.review.write_text(json.dumps({'words':{'other':review.next_state(None, 'known')}}), encoding='utf-8')
        data = ld.export_data(self.vocab, self.review, [VIDEO])
        self.assertIn('other', data['review']['words'])
        self.assertIn('暂无对应生词', data['warnings'][0])

    def test_missing_files_are_empty_but_corrupt_files_are_never_silently_exported(self):
        data = ld.export_data(self.root/'missing1', self.root/'missing2', [])
        self.assertEqual(data['vocab']['entries'], [])
        self.vocab.write_text('{bad', encoding='utf-8')
        with self.assertRaises(ValueError): ld.export_data(self.vocab, self.review, [])
        self.vocab.write_text(json.dumps({'schema_version':999, 'entries':[]}), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '版本'): ld.export_data(self.vocab, self.review, [])

    def test_json_duplicate_keys_non_finite_and_deep_data_are_rejected(self):
        for payload in ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '[' * 2000 + ']' * 2000]:
            with self.subTest(payload=payload[:20]), self.assertRaises(ValueError): ld.json_loads(payload)

    def test_bad_words_ids_timestamps_and_duplicates_are_rejected(self):
        for key, value in [('id','<img>'), ('word','<script>'), ('t',float('nan')),
                           ('t',True), ('created','bad date'), ('sentence',None)]:
            entry = dict(ENTRY, **{key:value})
            with self.subTest(key=key), self.assertRaises(ValueError): ld.validate_vocab({'entries':[entry]})
        with self.assertRaisesRegex(ValueError, '重复'): ld.validate_vocab({'entries':[ENTRY, ENTRY]})

    def test_review_and_resume_validation_never_resets_bad_state(self):
        state = review.next_state(None, 'known')
        for key, value in [('version',True), ('ease',float('inf')), ('due_at','2026-10-01'), ('last_rating',[])]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                ld.validate_review({'words':{'hello':dict(state, **{key:value})}})
        with self.assertRaises(ValueError): ld.validate_last({'slug':'video','t':-1,'title':''})
        with self.assertRaises(ValueError): ld.validate_last({'slug':'video','t':2,'title':'','unexpected':1})


class LearningDataApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ted_server.http.server.ThreadingHTTPServer(('127.0.0.1',0), ted_server.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(timeout=2)

    def request(self, body, headers=None):
        request = urllib.request.Request(self.base+'/api/learning-data/export',
            data=json.dumps(body).encode(), headers=headers or {'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request) as response: return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error: return error.code, json.load(error)

    def test_export_reads_real_sources_and_enforces_request_origin(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vocab, states = root/'vocab.json', root/'review.json'
            vocab.write_text(json.dumps({'entries':[ENTRY]}), encoding='utf-8')
            states.write_text('{"words":{}}', encoding='utf-8')
            with patch.object(ted_server, 'VOCAB_FILE',vocab), patch.object(ted_server,'REVIEW_FILE',states), \
                    patch.object(ted_server.vc, 'records',return_value=([VIDEO],[])):
                status, data = self.request({'last':None})
                self.assertEqual(status,200)
                self.assertEqual(data['backup']['vocab']['entries'][0]['slug'],VIDEO['id'])
                self.assertEqual(self.request({}, {'Content-Type':'text/plain'})[0],415)
                self.assertEqual(self.request({}, {'Content-Type':'application/json','Origin':'https://example.org'})[0],403)
                self.assertEqual(self.request({'last':[]})[0],400)

