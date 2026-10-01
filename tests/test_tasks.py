import copy
import json
import tempfile
import unittest
from pathlib import Path
import learning_data as ld
import study_learning as sl
import study_tasks as st


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.clip = sl.clips('a'*32,[{'start':0,'end':10,'text':"It's a really wonderful experiment."},
                                   {'start':10,'end':65,'text':'We learn from trying again.'}])[0]
        self.data = sl.empty()

    def request(self,task,answer,rating=None,identifier='b'*32):
        return {'video':self.clip['video'],'clip':self.clip['id'],'task':task['id'],'version':self.data['version'],
                'answer':answer,'self_rating':rating,'request_id':identifier}

    def test_public_tasks_hide_answers_and_source_remains_exact(self):
        for task in st.public_tasks(self.clip):
            self.assertNotIn('answer',task); self.assertNotIn('evidence',task)
        for task in st.tasks(self.clip):
            self.assertTrue(all(row in self.clip['lines'] for row in task['evidence']))

    def test_dictation_normalization_and_wrong_word_feedback(self):
        task = next(task for task in st.tasks(self.clip) if task['kind'] == 'dictation')
        self.data,record = st.attempt(self.data,self.clip,self.request(task,'IT’S a really wonderful experiment!'))
        self.assertTrue(record['correct'])
        self.data,record = st.attempt(self.data,self.clip,self.request(task,"It's a bad experiment.",identifier='c'*32))
        self.assertFalse(record['correct']); self.assertEqual(len(self.data['attempts']),2)
        self.assertEqual(record['reference'],self.clip['lines'][0]['text'])

    def test_subjective_records_self_assessment_never_an_objective_score(self):
        task = next(task for task in st.tasks(self.clip) if task['kind'] == 'summary')
        with self.assertRaisesRegex(ValueError,'自评'): st.attempt(self.data,self.clip,self.request(task,'We try.'))
        self.data,record = st.attempt(self.data,self.clip,self.request(task,'We try.','needs_work'))
        self.assertIsNone(record['correct']); self.assertEqual(record['self_rating'],'needs_work')

    def test_idempotency_conflicts_and_caption_revision_are_guarded(self):
        task = st.tasks(self.clip)[0]; request = self.request(task,task['answer'])
        self.data,record = st.attempt(self.data,self.clip,request)
        data,repeated = st.attempt(self.data,self.clip,request)
        self.assertEqual(len(data['attempts']),1); self.assertEqual(record,repeated)
        with self.assertRaisesRegex(ValueError,'不一致'): st.attempt(self.data,self.clip,dict(request,answer='other'))
        newer = copy.deepcopy(self.clip); newer['id'] = 'd'*32
        with self.assertRaisesRegex(ValueError,'已变化'): st.attempt(self.data,newer,dict(request,request_id='e'*32))

    def test_attempts_backup_merge_and_replace_roundtrip(self):
        task = st.tasks(self.clip)[0]; self.data,_ = st.attempt(self.data,self.clip,self.request(task,'wrong'))
        with tempfile.TemporaryDirectory() as temporary:
            vocab = Path(temporary)/'vocab.json'; review = Path(temporary)/'review.json'
            sl.save(vocab,self.data); backup = ld.export_data(vocab,review,[])
            merge = ld.plan_restore(vocab,review,[],json.dumps(backup),'merge')
            self.assertEqual(len(merge['learning']['attempts']),1)
            sl.save(vocab,sl.empty())
            plan = ld.plan_restore(vocab,review,[],json.dumps(backup),'replace'); ld.commit_restore(vocab,review,plan)
            self.assertEqual(sl.load(vocab)['attempts'],self.data['attempts'])

    def test_curated_question_only_when_current_captions_supply_evidence(self):
        rows = [{'start':28,'end':33,'text':'The gas form of nitrogen occupies 700 times'},
                {'start':36,'end':40,'text':"You don't trap it in a small container"}]
        clip = sl.clips('52a953504cca4845b0b9a55b2f732359',rows)[0]
        question = next(task for task in st.tasks(clip) if task['kind'] == 'choice')
        data,record = st.attempt(sl.empty(),clip,{'video':clip['video'],'clip':clip['id'],'task':question['id'],'version':0,'answer':'0','self_rating':None,'request_id':'a'*32})
        self.assertTrue(record['correct']); self.assertEqual(record['evidence'],rows)
        rows[0]['text'] = 'The gas occupies an unknown amount.'
        self.assertFalse(any(task['kind']=='choice' for task in st.tasks(sl.clips(clip['video'],rows)[0])))
