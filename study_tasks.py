"""Small source-backed exercises; subjective responses never receive fake scores."""
import copy
import datetime as dt
import re
from study_learning import digest

WORDS = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*|\d+")


def normalized(value):
    return ' '.join(word.lower().replace('’',"'") for word in WORDS.findall(value))


def tasks(clip):
    rows = [row for row in clip['lines'] if len(WORDS.findall(row['text'])) >= 5]
    if not rows: rows = clip['lines']
    if not rows: return []
    first = rows[0]; matches = list(WORDS.finditer(first['text']))
    target = max(matches,key=lambda match:len(match[0])) if matches else None
    result = []
    if target:
        masked = first['text'][:target.start()] + '____' + first['text'][target.end():]
        result.append({'kind':'cloze','prompt':'听这句并补全空缺：' + masked,'answer':target[0],
                       'evidence':[first],'explanation':'空缺对应原句中的单词；可回到原时间核对声音和拼写。'})
    result.append({'kind':'dictation','prompt':'听指定原句，写下完整英文（忽略大小写与标点）。',
                   'answer':first['text'],'evidence':[first], 'explanation':'按原文词序核对，大小写和标点不计错；漏词或词形不同需回听原句。'})
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
        task['listen_end'] = task['evidence'][0]['end']
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
