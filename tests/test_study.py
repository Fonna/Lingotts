import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import study_learning as sl
import learning_data as ld

VIDEO = 'a'*32
LINES = [{'start':i*10,'end':i*10+10,'text':f'English sentence number {i}.'} for i in range(15)]


class StudyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.vocab = Path(self.temp.name)/'vocab.json'; self.review = self.vocab.with_name('review.json')
        self.vocab.write_text('{"entries":[]}'); self.review.write_text('{"words":{}}')
        self.clip = sl.clips(VIDEO,LINES)[0]

    def request(self,**changes):
        return dict(video=VIDEO,clip=self.clip['id'],version=0,t=23.5,mode='blind',loop=True,speed=0.75,steps=['blind'],note='My understanding',**changes)

    def test_units_cover_source_once_and_changes_invalidate_only_the_changed_unit(self):
        units = sl.clips(VIDEO,LINES)
        self.assertEqual([row for unit in units for row in unit['lines']],LINES)
        self.assertTrue(all(60 <= unit['end']-unit['start'] <= 180 for unit in units))
        changed = copy.deepcopy(LINES); changed[0]['text'] += ' Revised'
        newer = sl.clips(VIDEO,changed)
        self.assertNotEqual(units[0]['id'],newer[0]['id']); self.assertEqual(units[1]['id'],newer[1]['id'])
        self.assertEqual(sl.clips(VIDEO,[]),[])

    def test_progress_roundtrip_and_concurrent_edit_guard(self):
        data = sl.progress(sl.empty(),self.clip,self.request())
        sl.save(self.vocab,data)
        self.assertEqual(sl.load(self.vocab),data)
        with self.assertRaisesRegex(ValueError,'已变化'): sl.progress(data,self.clip,self.request())
        for key,value in [('t',999),('steps',[[]]),('speed',True),('loop',1),('note','a'*2001)]:
            bad = self.request(); bad[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): sl.progress(sl.empty(),self.clip,bad)

    def test_version_two_backup_restores_learning_and_version_one_preserves_it(self):
        data = sl.progress(sl.empty(),self.clip,self.request()); sl.save(self.vocab,data)
        backup = ld.export_data(self.vocab,self.review,[])
        sl.save(self.vocab,sl.empty())
        plan = ld.plan_restore(self.vocab,self.review,[],json.dumps(backup),'replace')
        ld.commit_restore(self.vocab,self.review,plan)
        self.assertEqual(sl.load(self.vocab)['segments'],data['segments'])
        old = copy.deepcopy(backup); old.pop('learning'); old['schema_version'] = 1
        plan = ld.plan_restore(self.vocab,self.review,[],json.dumps(old),'replace')
        self.assertEqual(plan['learning']['segments'],data['segments'])
        self.assertTrue(any('保留本地片段' in note for note in plan['warnings']))

    def test_third_file_write_failure_rolls_back_all_three_files(self):
        data = sl.progress(sl.empty(),self.clip,self.request()); sl.save(self.vocab,data)
        backup = ld.export_data(self.vocab,self.review,[])
        plan = ld.plan_restore(self.vocab,self.review,[],json.dumps(backup),'replace')
        paths = (self.vocab,self.review,sl.path_for(self.vocab)); before = [path.read_bytes() for path in paths]
        original = ld.atomic_write; failed = False
        def failing(path,payload):
            nonlocal failed
            if Path(path) == paths[2] and not failed:
                failed = True; raise OSError('full disk')
            return original(path,payload)
        with patch.object(ld,'atomic_write',side_effect=failing),self.assertRaises(OSError): ld.commit_restore(self.vocab,self.review,plan)
        self.assertEqual([path.read_bytes() for path in paths],before)
        self.assertEqual(ld.backup_history(self.vocab)['backups'][0]['status'],'rolled_back')
