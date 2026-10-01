import copy
import datetime as dt
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

    def backup(self):
        return ld.export_data(self.vocab, self.review, [VIDEO], {'slug':'old-folder','t':5,'title':'Talk'})

    def test_preview_is_read_only_and_merge_deduplicates_by_location_not_id(self):
        backup = self.backup()
        backup['vocab']['entries'][0]['id'] = 'other-id'
        backup['vocab']['entries'].append(dict(ENTRY,id='entry1',word='world',t=30))
        before = self.vocab.read_bytes(), self.review.read_bytes()
        plan = ld.plan_restore(self.vocab,self.review,[VIDEO],json.dumps(backup),'merge',{'slug':VIDEO['id'],'t':99,'title':'local'})
        self.assertEqual(plan['counts']['duplicates'],1)
        self.assertEqual(plan['counts']['id_conflicts'],1)
        self.assertEqual(plan['counts']['after_vocab'],2)
        self.assertEqual(plan['resume']['last']['t'],99)
        self.assertEqual((self.vocab.read_bytes(),self.review.read_bytes()),before)
        self.assertFalse((self.root/'backups').exists())

    def test_merge_uses_latest_review_time_and_replace_preserves_exact_learning_schedule(self):
        backup = self.backup()
        state = backup['review']['words']['hello']
        state['last_review'] = '2000-01-01T00:00:00Z'
        plan = ld.plan_restore(self.vocab,self.review,[VIDEO],json.dumps(backup),'merge')
        self.assertEqual(plan['counts']['review_kept'],1)
        state['last_review'] = '2099-01-01T00:00:00Z'
        plan = ld.plan_restore(self.vocab,self.review,[VIDEO],json.dumps(backup),'merge')
        self.assertEqual(plan['review']['words']['hello']['last_review'],state['last_review'])
        plan = ld.plan_restore(self.vocab,self.review,[VIDEO],json.dumps(backup),'replace')
        identifier = ld.commit_restore(self.vocab,self.review,plan)
        self.assertEqual(review.load_states(self.review)['hello'],state)
        self.assertEqual(review.load_epoch(self.review),1)
        self.assertEqual(ld.load_backup(self.vocab,identifier)['review']['words']['hello']['last_review'],
                         plan['before']['review']['words']['hello']['last_review'])

    def test_restore_rebinds_legacy_aliases_without_loading_missing_media(self):
        backup = self.backup()
        source_id = 'b'*32
        backup['videos'][0]['id'] = source_id
        backup['vocab']['entries'][0]['slug'] = source_id
        plan = ld.plan_restore(self.vocab,self.review,[VIDEO],json.dumps(backup),'replace')
        self.assertEqual(plan['vocab']['entries'][0]['slug'],VIDEO['id'])
        backup['videos'].append(dict(VIDEO,id='c'*32))
        with self.assertRaises(ValueError): ld.plan_restore(self.vocab,self.review,[VIDEO],json.dumps(backup),'replace')

    def test_bad_versions_nested_values_and_unsupported_fields_cannot_preview(self):
        for field, value in [('schema_version',True), ('schema_version',99), ('videos',[{'id':[]}]), ('resume',[]),('warnings',{})]:
            backup = self.backup(); backup[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                ld.plan_restore(self.vocab,self.review,[VIDEO],json.dumps(backup),'replace')

    def test_second_file_write_failure_rolls_back_both_files_and_keeps_backup(self):
        backup = self.backup(); backup['vocab']['entries'] = []
        plan = ld.plan_restore(self.vocab,self.review,[VIDEO],json.dumps(backup),'replace')
        original = self.vocab.read_bytes(), self.review.read_bytes()
        replace, failed = ld.os.replace, []
        def fail_once(source,destination):
            if Path(destination) == self.review and not failed:
                failed.append(True); raise OSError('disk failure')
            return replace(source,destination)
        with patch.object(ld.os,'replace',side_effect=fail_once), self.assertRaises(OSError):
            ld.commit_restore(self.vocab,self.review,plan)
        self.assertEqual((self.vocab.read_bytes(),self.review.read_bytes()),original)
        self.assertEqual(len(list((self.root/'backups').glob('*/backup.json'))),1)
        self.assertFalse((self.root/'backups/pending.json').exists())

    def test_interrupted_process_is_recovered_before_next_use(self):
        backup = self.backup(); backup['vocab']['entries'] = []
        plan = ld.plan_restore(self.vocab,self.review,[VIDEO],json.dumps(backup),'replace')
        original = self.vocab.read_bytes(), self.review.read_bytes()
        write = ld.atomic_write
        def crash(path,data):
            if Path(path) == self.review: raise SystemExit('simulated process loss')
            return write(path,data)
        with patch.object(ld,'atomic_write',side_effect=crash), self.assertRaises(SystemExit):
            ld.commit_restore(self.vocab,self.review,plan)
        self.assertNotEqual(self.vocab.read_bytes(),original[0])
        ld.recover_pending(self.vocab,self.review)
        self.assertEqual((self.vocab.read_bytes(),self.review.read_bytes()),original)

    def test_interrupted_restore_cannot_overwrite_subsequent_manual_changes(self):
        backup = self.backup(); backup['vocab']['entries'] = []
        plan = ld.plan_restore(self.vocab,self.review,[VIDEO],json.dumps(backup),'replace')
        write = ld.atomic_write
        def crash(path,data):
            if Path(path) == self.review: raise SystemExit()
            return write(path,data)
        with patch.object(ld,'atomic_write',side_effect=crash), self.assertRaises(SystemExit):
            ld.commit_restore(self.vocab,self.review,plan)
        self.vocab.write_text('manual change',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'手工修改'): ld.recover_pending(self.vocab,self.review)
        self.assertEqual(self.vocab.read_text(encoding='utf-8'),'manual change')

    def test_backup_id_cannot_read_arbitrary_files(self):
        for identifier in ['../vocab', None, '<script>', 'a'*31]:
            with self.subTest(identifier=identifier), self.assertRaises(ValueError): ld.load_backup(self.vocab,identifier)


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

    def request(self, body, headers=None, endpoint='export'):
        request = urllib.request.Request(self.base+'/api/learning-data/'+endpoint,
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

    def test_restore_rejects_stale_preview_and_old_review_cards(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); vocab, states = root/'vocab.json',root/'review.json'
            vocab.write_text(json.dumps({'entries':[ENTRY]}),encoding='utf-8')
            states.write_text('{"words":{}}',encoding='utf-8')
            with patch.object(ted_server,'VOCAB_FILE',vocab), patch.object(ted_server,'REVIEW_FILE',states), \
                    patch.object(ted_server.vc,'records',return_value=([VIDEO],[])):
                backup = self.request({})[1]['backup']
                payload = {'text':json.dumps(backup),'mode':'replace','last':None}
                preview = self.request(payload,endpoint='preview')[1]['preview']
                self.assertFalse((root/'backups').exists())
                invalid = dict(payload,token='😀'*64)
                self.assertEqual(self.request(invalid,endpoint='restore')[0],409)
                payload['token'] = preview['token']
                vocab.write_text(json.dumps({'entries':[dict(ENTRY,t=99)]}),encoding='utf-8')
                self.assertEqual(self.request(payload,endpoint='restore')[0],409)
                payload['token'] = self.request(payload,endpoint='preview')[1]['preview']['token']
                status, restored = self.request(payload,endpoint='restore')
                self.assertEqual(status,200)
                self.assertTrue(restored['ok'])
                request = urllib.request.Request(self.base+'/api/review',data=json.dumps({'word':'hello','rating':'known','version':0,'epoch':0}).encode(),headers={'Content-Type':'application/json'})
                with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(request)
                self.assertEqual(error.exception.code,409); error.exception.close()
                with urllib.request.urlopen(self.base+'/api/learning-data/backup?id='+restored['backup_id']) as response:
                    self.assertEqual(json.load(response)['backup']['vocab']['entries'][0]['t'],99)
