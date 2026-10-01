import copy
import json
import tempfile
import unittest
import itertools
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

    def test_word_diff_handles_omissions_extras_forms_order_and_repeats(self):
        cases = [
            ('We learn from trying.', 'we learned trying!', ['equal','replace','missing','equal']),
            ('We learn.', 'We really learn.', ['equal','extra','equal']),
            ('We can can learn.', 'We can learn.', ['equal','missing','equal','equal']),
            ('We learn.', 'We learn learn.', ['equal','extra','equal']),
            ('red blue', 'blue red', ['extra','equal','missing']),
            ("IT’S a two-step experiment, 700 times!", "it's a two-step experiment 700 times", ['equal']*6),
            ('one two', '', ['missing','missing']),
            ('', 'one two', ['extra','extra']),
            ('', '', []),
        ]
        for reference, answer, kinds in cases:
            with self.subTest(reference=reference,answer=answer):
                diff = st.word_diff(reference,answer)
                self.assertEqual([item['kind'] for item in diff],kinds)
                self.assertEqual(' '.join(word for item in diff for word in item['expected']),st.normalized(reference))
                self.assertEqual(' '.join(word for item in diff for word in item['actual']),st.normalized(answer))
        result = st.word_diff('we learn','we learned'); result[0]['expected'].clear()
        self.assertEqual(st.word_diff('we learn','we learned')[0]['expected'],['we'])

    def test_word_diff_minimality_against_independent_short_sequence_oracle(self):
        # Exhaustive repeated-word/order cases catch greedy alignment mistakes.
        from functools import lru_cache
        @lru_cache(None)
        def distance(left,right):
            if not left: return len(right)
            if not right: return len(left)
            return min(1+distance(left[1:],right),1+distance(left,right[1:]),
                       (left[0]!=right[0])+distance(left[1:],right[1:]))
        sequences = [tuple(words) for size in range(4) for words in itertools.product(('a','b'),repeat=size)]
        for left in sequences:
            for right in sequences:
                diff = st.word_diff(' '.join(left),' '.join(right))
                self.assertEqual(sum(item['kind']!='equal' for item in diff),distance(left,right))

    def test_selection_prefers_bounded_complete_sentences_and_joins_caption_fragments(self):
        lines = [{'start':0,'end':1,'text':'Welcome everyone.'},
                 {'start':1,'end':4,'text':'The experiment helps us understand'},
                 {'start':4,'end':8,'text':'how liquid nitrogen changes into a gas.'},
                 {'start':8,'end':10,'text':'Nitrogen expands rapidly when it becomes gas.'}]
        clip = sl.clips('a'*32,lines)[0]
        tasks = st.tasks(clip)
        listening = next(task for task in tasks if task['kind']=='dictation')
        cloze = next(task for task in tasks if task['kind']=='cloze')
        self.assertEqual(listening['answer'],' '.join(row['text'] for row in lines[1:3]))
        self.assertEqual(listening['evidence'],lines[1:3])
        self.assertEqual((listening['listen_at'],listening['listen_end']),(1,8))
        self.assertNotEqual(cloze['evidence'],listening['evidence'])
        self.assertEqual(st.tasks(clip),tasks)
        public = st.public_tasks(clip)
        self.assertFalse(any('answer' in task or 'evidence' in task for task in public))

    def test_cloze_avoids_function_words_numbers_names_and_longest_word_bias(self):
        text = 'The gas expands in an extraordinarily surprising experiment with Alice at 700 degrees.'
        frequency = st.Counter({'expands':3,'extraordinarily':1,'Alice':10,'700':10,'the':10})
        target = st.cloze_target(text,frequency)
        self.assertEqual(target[0],'expands')
        self.assertIsNone(st.cloze_target('This is what we do with Alice at 700.',frequency))
        clip = sl.clips('a'*32,[{'start':0,'end':4,'text':'This is what we do at 700.'}])[0]
        self.assertFalse(any(task['kind']=='cloze' for task in st.tasks(clip)))
        self.assertTrue(any(task['kind']=='dictation' for task in st.tasks(clip)))

    def test_selection_does_not_join_across_long_gaps_or_exceed_bounds(self):
        clip = sl.clips('a'*32,[{'start':0,'end':3,'text':'We carefully learn about the experiment'},
                              {'start':8,'end':12,'text':'and understand how the gas expands.'}])[0]
        self.assertTrue(all(len(item['evidence'])==1 for item in st.sentence_candidates(clip)))
        self.assertEqual(st.tasks(sl.clips('a'*32,[{'start':0,'end':3,'text':'...'}])[0]),[])
        short = sl.clips('a'*32,[{'start':0,'end':3,'text':'Gas expands.'}])[0]
        self.assertTrue(st.tasks(short))

    def test_public_history_derives_feedback_without_modifying_saved_or_backup_data(self):
        task = next(task for task in st.tasks(self.clip) if task['kind']=='dictation')
        self.data,record = st.attempt(self.data,self.clip,self.request(task,'We learned.'))
        original = copy.deepcopy(self.data)
        visible = st.public_learning(self.data,self.clip['video'])
        self.assertIn('word_diff',visible['attempts'][0])
        self.assertEqual(visible['attempts'][0]['word_diff'],st.word_diff(record['reference'],record['answer']))
        self.assertEqual(self.data,original)
        self.assertEqual(st.public_learning(self.data,'f'*32),original)
        self.assertNotIn('word_diff',st.public_attempt(dict(record,kind='summary')))
        with tempfile.TemporaryDirectory() as temporary:
            vocab = Path(temporary)/'vocab.json'; review = vocab.with_name('review.json')
            sl.save(vocab,self.data)
            backup = ld.export_data(vocab,review,[])
            self.assertEqual(backup['learning']['attempts'],original['attempts'])
            sl.save(vocab,sl.empty())
            ld.commit_restore(vocab,review,ld.plan_restore(vocab,review,[],json.dumps(backup),'replace'))
            self.assertEqual(st.public_learning(sl.load(vocab),self.clip['video'])['attempts'],visible['attempts'])

    def test_public_history_only_decorates_twenty_visible_attempts_per_clip(self):
        task = st.tasks(self.clip)[0]
        for index in range(22):
            self.data,_ = st.attempt(self.data,self.clip,self.request(task,'wrong',identifier=f'{index:032x}'))
        visible = st.public_learning(self.data,self.clip['video'])
        self.assertEqual(sum('word_diff' in item for item in visible['attempts']),20)
        self.assertNotIn('word_diff',visible['attempts'][0])
