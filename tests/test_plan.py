import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
import learning_data as ld
import study_learning as sl
import study_tasks as st
import study_plan as sp
import review_schedule as rs

NOW = dt.datetime(2026,10,1,3,tzinfo=dt.timezone.utc)
ENTRY = {'id':'e1','word':'hello','slug':'a'*32,'sentence':'Hello from the experiment.','video_title':'Talk','t':0,'created':'2026-10-01T02:00:00Z'}


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.clip = dict(sl.clips('a'*32,[{'start':0,'end':65,'text':'Hello from the experiment in this talk.'}])[0],title='Talk')
        self.data = sl.empty()

    def plan(self,data=None,entries=None,states=None,units=None,day='2026-10-01'):
        return sp.recommend(self.data if data is None else data,[ENTRY] if entries is None else entries,{} if states is None else states,[self.clip] if units is None else units,day,480,NOW)

    def test_cold_start_missing_transcript_and_due_word_reasons(self):
        plan = self.plan()
        self.assertEqual([item['kind'] for item in plan['items']],['word','new'])
        self.assertTrue(all(item['reason'] for item in plan['items']))
        self.assertIn('没有',self.plan(entries=[],units=[])['fallback'])
        self.assertEqual(self.plan(entries=[],units=[])['items'],[])

    def test_completed_clip_and_corrected_error_stop_recommendations(self):
        self.data = sl.progress(self.data,self.clip,{'video':self.clip['video'],'clip':self.clip['id'],'version':0,'t':12,'mode':'blind','loop':False,'speed':1,'steps':list(sl.STEPS),'note':''})
        self.assertEqual(self.plan(entries=[])['items'],[])
        task = st.tasks(self.clip)[0]
        request = {'video':self.clip['video'],'clip':self.clip['id'],'task':task['id'],'version':self.data['version'],'answer':'wrong','self_rating':None,'request_id':'b'*32}
        self.data,_ = st.attempt(self.data,self.clip,request)
        self.assertEqual(self.plan(entries=[])['items'][0]['kind'],'retry')
        self.data,_ = st.attempt(self.data,self.clip,dict(request,version=self.data['version'],answer=task['answer'],request_id='c'*32))
        self.assertEqual(self.plan(entries=[])['items'],[])

    def test_day_skip_quota_conflict_and_timezone(self):
        request = {'day':'2026-10-01','offset':480,'version':0,'quota':1,'skipped':['word:hello']}
        self.data = sp.settings(self.data,request)
        self.assertEqual(self.plan()['quota'],1)
        self.assertFalse(any(item['kind']=='word' for item in self.plan()['items']))
        self.assertTrue(any(item['kind']=='word' for item in self.plan(day='2026-10-02')['items']))
        with self.assertRaisesRegex(ValueError,'已变化'): sp.settings(self.data,request)
        for key,value in [('quota',True),('skipped',[[]]),('day','2026-02-30'),('offset',99999)]:
            with self.subTest(key=key), self.assertRaises(ValueError): sp.settings(sl.empty(),dict(request,**{key:value}))
        start,end = sp.calendar('2026-10-01',480)
        self.assertEqual(start.astimezone(dt.timezone.utc).hour,16)
        self.assertEqual((end-start).days,1)

    def test_progress_and_context_do_not_fabricate_sm2_grades(self):
        request = {'answer':'Hello means a greeting. Hello everyone.','self_rating':'clear','version':0,'request_id':'b'*32}
        self.data,record = st.context(self.data,ENTRY,request)
        self.assertIsNone(record['correct']); self.assertEqual(record['kind'],'context')
        duplicate,_ = st.context(self.data,ENTRY,request)
        self.assertEqual(len(duplicate['attempts']),1)
        states = {'hello':rs.next_state(None,'known',NOW)}
        plan = self.plan(states=states)
        self.assertEqual(plan['counts']['words'],1)
        self.assertFalse(any(item['kind']=='word' for item in plan['items']))

    def test_plan_and_context_backup_restore_preserve_all_activity(self):
        self.data,_ = st.context(self.data,ENTRY,{'answer':'Hello everyone.','self_rating':'needs_work','version':0,'request_id':'b'*32})
        self.data = sp.settings(self.data,{'day':'2026-10-01','offset':480,'version':1,'quota':5,'skipped':['word:hello']})
        with tempfile.TemporaryDirectory() as temporary:
            vocab = Path(temporary)/'vocab.json'; review = Path(temporary)/'review.json'
            sl.save(vocab,self.data); backup = ld.export_data(vocab,review,[])
            sl.save(vocab,sl.empty())
            plan = ld.plan_restore(vocab,review,[],json.dumps(backup),'replace'); ld.commit_restore(vocab,review,plan)
            restored = sl.load(vocab)
            self.assertEqual(restored['days'],self.data['days']); self.assertEqual(restored['attempts'],self.data['attempts'])

    def test_three_categories_get_slots_before_extra_words(self):
        entries = [dict(ENTRY,word=word,id=word) for word in ('hello','world','learn','speak')]
        task = st.tasks(self.clip)[0]
        self.data,_ = st.attempt(self.data,self.clip,{'video':self.clip['video'],'clip':self.clip['id'],'task':task['id'],'version':0,'answer':'wrong','self_rating':None,'request_id':'b'*32})
        self.assertEqual([item['kind'] for item in self.plan(entries=entries)['items']],['word','retry','new'])

    def test_obsolete_task_history_is_preserved_without_unusable_retry_links(self):
        task = st.tasks(self.clip)[0]
        self.data,_ = st.attempt(self.data,self.clip,{'video':self.clip['video'],'clip':self.clip['id'],'task':task['id'],
                                'version':0,'answer':'wrong','self_rating':None,'request_id':'b'*32})
        self.data['attempts'][0]['task'] = 'f'*32  # Older exercise selection/wording, same captions.
        original = copy.deepcopy(self.data)
        self.assertFalse(any(item['kind']=='retry' for item in self.plan(entries=[])['items']))
        self.assertEqual(self.data,original)
