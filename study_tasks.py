"""Small source-backed exercises; subjective responses never receive fake scores."""
import copy
import datetime as dt
import re
from array import array
from collections import Counter
from functools import lru_cache
from study_learning import digest

WORDS = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*|\d+")
FUNCTION_WORDS = frozenset('''a an the this that these those i you he she it we they
me him her us them my your his its our their mine yours ours theirs myself yourself
is am are was were be been being have has had do does did can could will would shall
should may might must and or but if so because as while than then of to in on at by
for from with without into through about over under between up down off out not no
yes all any some each every both other another such only very really just here there
now how what which who whom whose when where why also again get got going want like
it's that's there's i'm you're we're they're i've you've we've they've don't doesn't
didn't isn't aren't wasn't weren't can't couldn't won't wouldn't shouldn't'''.split())


def normalized(value):
    return ' '.join(word.lower().replace('’',"'") for word in WORDS.findall(value))


@lru_cache(maxsize=128)
def _word_diff(reference, answer):
    """Minimum edit alignment. Prefer exact repeated-word matches over substitutions."""
    expected, actual = normalized(reference).split(), normalized(answer).split()
    costs = [array('H', range(len(actual) + 1))]
    for i, word in enumerate(expected, 1):
        row = array('H', [i])
        previous = costs[-1]
        for j, written in enumerate(actual, 1):
            row.append(min(previous[j] + 1, row[j-1] + 1,
                           previous[j-1] + (word != written)))
        costs.append(row)
    edits = []; i, j = len(expected), len(actual)
    while i or j:
        if i and j and expected[i-1] == actual[j-1] and costs[i][j] == costs[i-1][j-1]:
            kind = 'equal'; left, right = [expected[i-1]], [actual[j-1]]; i -= 1; j -= 1
        elif i and costs[i][j] == costs[i-1][j] + 1:
            kind = 'missing'; left, right = [expected[i-1]], []; i -= 1
        elif j and costs[i][j] == costs[i][j-1] + 1:
            kind = 'extra'; left, right = [], [actual[j-1]]; j -= 1
        else:
            kind = 'replace'; left, right = [expected[i-1]], [actual[j-1]]; i -= 1; j -= 1
        edits.append({'kind':kind, 'expected':left, 'actual':right})
    edits.reverse()
    return edits


def word_diff(reference, answer):
    # Callers cannot mutate the shared cached alignment.
    return copy.deepcopy(_word_diff(reference, answer))


def public_attempt(record):
    result = copy.deepcopy(record)
    if record['kind'] in ('cloze', 'dictation'):
        result['word_diff'] = word_diff(record['reference'], record['answer'])
    return result


def public_learning(data, video):
    """Derive feedback for visible history only; the persisted/backup schema stays intact."""
    result = copy.deepcopy(data); counts = Counter()
    for index in range(len(result['attempts']) - 1, -1, -1):
        record = result['attempts'][index]
        if record['video'] == video:
            counts[record['clip']] += 1
            if counts[record['clip']] <= 20:
                result['attempts'][index] = public_attempt(record)
    return result


def sentence_candidates(clip):
    """Keep original timed rows; join adjacent caption fragments up to a sentence end."""
    candidates = []
    for index, first in enumerate(clip['lines']):
        starts_sentence = index == 0 or bool(re.search(r'[.!?]["\u201d\x27]*$', clip['lines'][index-1]['text'].strip()))
        rows = []
        for line in clip['lines'][index:index+3]:
            if rows and line['start'] - rows[-1]['end'] > 1.5: break
            rows.append(line)
            text = ' '.join(row['text'] for row in rows)
            count = len(WORDS.findall(text))
            if count > 24 or line['end'] - first['start'] > 15: break
            complete = bool(re.search(r'[.!?]["\u201d\x27]*$', text.strip()))
            if count >= 5:
                candidates.append({'text':text, 'evidence':rows[:],
                                   'complete':complete and starts_sentence and text.lstrip()[0].isupper()})
            if complete: break
    if not candidates:
        candidates = [{'text':row['text'], 'evidence':[row], 'complete':False}
                      for row in clip['lines'] if WORDS.search(row['text'])]
    return sorted(candidates, key=lambda item:(not item['complete'],
                  not 6 <= len(WORDS.findall(item['text'])) <= 18,
                  abs(len(WORDS.findall(item['text'])) - 12), item['evidence'][0]['start']))


def cloze_target(text, frequency):
    candidates = []
    for index, match in enumerate(WORDS.finditer(text)):
        word = normalized(match[0])
        if (word in FUNCTION_WORDS or not word.isalpha() or not 4 <= len(word) <= 14
                or (index > 0 and match[0][0].isupper())): continue
        candidates.append(match)
    if not candidates: return None
    return min(candidates, key=lambda match:(-frequency[normalized(match[0])],
               abs(len(match[0]) - 7), abs((match.start()+match.end())/2 - len(text)/2), match.start()))


def tasks(clip):
    rows = [row for row in clip['lines'] if len(WORDS.findall(row['text'])) >= 5]
    if not rows: rows = clip['lines']
    if not rows: return []
    sentences = sentence_candidates(clip)
    if not sentences: return []
    listening = sentences[0]
    frequency = Counter(normalized(' '.join(row['text'] for row in clip['lines'])).split())
    # Prefer a different source for cloze when suitable content words exist.
    cloze_sources = [item for item in sentences if item['evidence'] != listening['evidence']] + [listening]
    selected = None
    for item in cloze_sources:
        target = cloze_target(item['text'], frequency)
        if target:
            selected = item, target
            break
    result = []
    if selected:
        source, target = selected
        masked = source['text'][:target.start()] + '____' + source['text'][target.end():]
        result.append({'kind':'cloze','prompt':'听这句并补全空缺：' + masked,'answer':target[0],
                       'evidence':source['evidence'],'explanation':'空缺选取原文内容词，优先片段内重复出现的词；按原句核对声音和拼写。'})
    result.append({'kind':'dictation','prompt':'听指定原句，写下完整英文（忽略大小写与标点）。',
                   'answer':listening['text'],'evidence':listening['evidence'],
                   'explanation':'按原文词序逐词核对，忽略大小写与标点；漏写、多写和用词/词形不一致分别标出，词序不同也可能显示为漏写与多写。'})
    first = rows[0]
    result.append({'kind':'understanding','prompt':'这个片段在说明什么？写下核心信息和一个支持细节。对照原文后自评。',
                   'answer':first['text'] + '\n' + rows[-1]['text'],'evidence':[first,rows[-1]],
                   'explanation':'以下是可核对的原文锚点，不是唯一标准概括。检查自己的主旨与细节是否有原文支持。'})
    result.append({'kind':'summary','prompt':'用 2–3 句英语概述这个片段。可以借用关键词，尽量用自己的表达。',
                   'answer':first['text'] + '\n' + rows[-1]['text'],'evidence':[first,rows[-1]],
                   'explanation':'参考原文核对主旨、关键细节和表达是否清楚。此题只记录自评，不生成客观分数或语言等级。'})
    # One curated initial unit: condition and all evidence are checked against live captions.
    nitrogen = [row for row in clip['lines'] if '700 times' in row['text'] or "don't trap" in row['text']]
    if len(nitrogen) == 2 and clip['video'] == '52a953504cca4845b0b9a55b2f732359':
        evidence = clip['lines'][clip['lines'].index(nitrogen[0]):clip['lines'].index(nitrogen[-1])+1]
        result.append({'kind':'choice','prompt':'为什么演讲者提醒不要把变成气体的氮困在小容器里？',
                       'choices':['气态氮体积膨胀到液态的约 700 倍','氮会立即变成水','盖子会让氮停止汽化'],
                       'answer':'0','evidence':evidence,'explanation':'原文先说明气态氮体积为液态的 700 倍，随后提醒不要困在小容器内；这解释了膨胀风险。'})
    for task in result:
        task['id'] = digest([clip['id'],task])
        task['listen_at'] = task['evidence'][0]['start']
        task['listen_end'] = task['evidence'][-1]['end']
    return result


def public_tasks(clip):
    return [{key:value for key,value in task.items() if key not in ('answer','evidence','explanation')} for task in tasks(clip)]


def attempt(data,clip,request):
    from study_learning import validate
    from learning_data import text
    required = {'video','clip','task','version','answer','self_rating','request_id'}
    if set(request) != required: raise ValueError('练习提交字段无效')
    text(request['answer'],'练习回答',4000,True)
    if not isinstance(request['request_id'],str) or not re.fullmatch('[a-f0-9]{32}',request['request_id']):
        raise ValueError('练习提交编号无效')
    task = next((item for item in tasks(clip) if item['id'] == request['task']),None)
    if not task: raise ValueError('题目或字幕已变化，请刷新练习')
    existing = next((item for item in data['attempts'] if item['id'] == request['request_id']),None)
    if existing:
        if existing['task'] == task['id'] and existing['answer'] == request['answer'] and existing['self_rating'] == request['self_rating']:
            return data, existing
        raise ValueError('重复提交编号与回答不一致')
    if type(request['version']) is not int or request['version'] != data['version']: raise ValueError('学习记录已变化，请刷新练习')
    subjective = task['kind'] in ('summary','understanding')
    if subjective:
        if request['self_rating'] not in ('needs_work','clear'): raise ValueError('请先选择自评')
        correct = None
    else:
        if request['self_rating'] is not None: raise ValueError('客观题无需自评')
        if task['kind'] == 'choice' and request['answer'] not in ('0','1','2'): raise ValueError('请选择有效选项')
        correct = normalized(request['answer']) == normalized(task['answer'])
    record = {'id':request['request_id'],'video':clip['video'],'clip':clip['id'],'task':task['id'],
              'kind':task['kind'],'answer':request['answer'],'correct':correct,'self_rating':request['self_rating'],
              'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),'reference':task['answer'],
              'evidence':copy.deepcopy(task['evidence']), 'explanation':task['explanation']}
    updated = copy.deepcopy(data); updated['attempts'].append(record); updated['version'] += 1
    return validate(updated),record


def context(data,entry,request):
    from study_learning import validate
    from learning_data import text
    text(request.get('answer'),'语境回答',4000,True)
    if request.get('self_rating') not in ('clear','needs_work'): raise ValueError('请选择语境自评')
    identifier = request.get('request_id')
    if not isinstance(identifier,str) or not re.fullmatch('[a-f0-9]{32}',identifier): raise ValueError('语境提交编号无效')
    task_id = digest(['context',entry['word'],entry['slug'],entry['t'],entry['sentence']])
    old = next((item for item in data['attempts'] if item['id']==identifier),None)
    if old:
        if old['task']==task_id and old['answer']==request['answer'] and old['self_rating']==request['self_rating']: return data,old
        raise ValueError('语境重复提交内容不一致')
    if type(request.get('version')) is not int or request['version'] != data['version']: raise ValueError('学习记录已变化，请重新提交')
    video = entry['slug'] if re.fullmatch('[a-f0-9]{32}',entry['slug']) else digest(entry['slug'])
    record = {'id':identifier,'video':video,'clip':digest(['context',entry['id']]),'task':task_id,'kind':'context',
              'answer':request['answer'],'correct':None,'self_rating':request['self_rating'],
              'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),'reference':entry['sentence'] or entry['word'],
              'evidence':[{'start':entry['t'],'end':entry['t']+1,'text':entry['sentence'] or entry['word']}],
              'explanation':'对照原句核对含义，再检查自己的造句是否表达清楚。自评不替代单词的间隔复习评分。'}
    updated = copy.deepcopy(data); updated['attempts'].append(record); updated['version'] += 1
    return validate(updated),record
