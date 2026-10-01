"""Content-derived learning units and versioned local activity storage."""
import copy
import datetime as dt
import hashlib
import json
import math
import re
from pathlib import Path

STEPS = ('blind', 'subtitles', 'loop', 'relisten')
SPEEDS = (0.5, 0.75, 1, 1.25, 1.5, 2)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:32]


def clips(video_id, lines):
    groups, group = [], []
    for line in lines:
        if group and line['end'] - group[0]['start'] > 180:
            groups.append(group); group = []
        group.append({key:line[key] for key in ('start','end','text')})
        if group[-1]['end'] - group[0]['start'] >= 60:
            groups.append(group); group = []
    if group:
        if groups and group[-1]['end'] - group[0]['start'] < 60 and group[-1]['end'] - groups[-1][0]['start'] <= 180:
            groups[-1].extend(group)
        else: groups.append(group)
    return [{'id':digest([video_id, rows]), 'video':video_id, 'revision':digest(rows),
             'start':rows[0]['start'], 'end':max(row['end'] for row in rows), 'lines':rows,
             'label':f"片段 {index + 1}"} for index, rows in enumerate(groups)]


def empty():
    return {'schema_version':1, 'version':0, 'segments':{}, 'attempts':[], 'days':{}}


def validate(data):
    from learning_data import text, seconds
    from review_schedule import parse_due
    if not isinstance(data, dict) or set(data) != set(empty()) or data['schema_version'] != 1 or type(data['schema_version']) is not int:
        raise ValueError('学习活动数据版本或字段不受支持')
    if type(data['version']) is not int or data['version'] < 0:
        raise ValueError('学习活动版本无效')
    if not isinstance(data['segments'], dict) or len(data['segments']) > 10000:
        raise ValueError('片段进度格式或数量无效')
    fields = {'video','revision','start','end','t','mode','loop','speed','steps','note','updated_at'}
    for identifier, state in data['segments'].items():
        if not isinstance(identifier,str) or not re.fullmatch('[a-f0-9]{32}', identifier) or not isinstance(state, dict) or set(state) != fields:
            raise ValueError('片段进度字段无效')
        for key in ('video','revision'):
            if not isinstance(state[key], str) or not re.fullmatch('[a-f0-9]{32}',state[key]): raise ValueError('片段关联无效')
        for key in ('start','end','t'): seconds(state[key], '片段位置')
        if not state['start'] <= state['t'] <= state['end'] or state['end'] <= state['start']: raise ValueError('片段范围无效')
        if state['mode'] not in ('blind','subtitles') or type(state['loop']) is not bool or type(state['speed']) not in (int,float) or state['speed'] not in SPEEDS:
            raise ValueError('播放设置无效')
        if not isinstance(state['steps'], list) or any(not isinstance(step,str) or step not in STEPS for step in state['steps']) or len(set(state['steps'])) != len(state['steps']):
            raise ValueError('片段步骤无效')
        text(state['note'],'理解笔记',2000)
        if not parse_due(state['updated_at']): raise ValueError('片段更新时间无效')
    if not isinstance(data['attempts'],list) or len(data['attempts']) > 10000: raise ValueError('练习记录格式或数量无效')
    identifiers = set()
    fields = {'id','video','clip','task','kind','answer','correct','self_rating','created_at','reference','evidence','explanation'}
    for item in data['attempts']:
        if not isinstance(item,dict) or set(item) != fields: raise ValueError('练习记录字段无效')
        for key in ('id','video','clip','task'):
            if not isinstance(item[key],str) or not re.fullmatch('[a-f0-9]{32}',item[key]): raise ValueError('练习关联无效')
        if item['id'] in identifiers: raise ValueError('练习编号重复')
        identifiers.add(item['id'])
        if item['kind'] not in ('cloze','dictation','choice','summary','understanding','context'): raise ValueError('练习类型无效')
        subjective = item['kind'] in ('summary','understanding','context')
        if (subjective and (item['correct'] is not None or item['self_rating'] not in ('needs_work','clear'))) or (not subjective and (type(item['correct']) is not bool or item['self_rating'] is not None)):
            raise ValueError('练习评判格式无效')
        for key,limit in [('answer',4000),('reference',4000),('explanation',2000)]: text(item[key],key,limit,True)
        if not parse_due(item['created_at']): raise ValueError('练习时间无效')
        if not isinstance(item['evidence'],list) or not 1 <= len(item['evidence']) <= 10: raise ValueError('练习依据无效')
        for row in item['evidence']:
            if not isinstance(row,dict) or set(row) != {'start','end','text'}: raise ValueError('原文依据字段无效')
            seconds(row['start'],'原文位置'); seconds(row['end'],'原文位置')
            if row['end'] <= row['start']: raise ValueError('原文范围无效')
            text(row['text'],'原文',4000,True)
    if not isinstance(data['days'],dict) or len(data['days']) > 10000: raise ValueError('每日计划格式或数量无效')
    for day,settings in data['days'].items():
        try:
            if not isinstance(day,str) or dt.date.fromisoformat(day).isoformat() != day: raise ValueError()
        except (ValueError,TypeError): raise ValueError('计划日期无效')
        if not isinstance(settings,dict) or set(settings) != {'quota','skipped','updated_at'}: raise ValueError('计划设置无效')
        if type(settings['quota']) is not int or settings['quota'] not in (1,3,5,10): raise ValueError('计划负荷无效')
        if not isinstance(settings['skipped'],list) or len(settings['skipped']) > 10000: raise ValueError('跳过项目无效')
        for key in settings['skipped']:
            if not isinstance(key,str) or not re.fullmatch(r'(?:clip|task):[a-f0-9]{32}|word:[a-z]+(?:[\x27-][a-z]+)*',key): raise ValueError('跳过项目编号无效')
        if len(set(settings['skipped'])) != len(settings['skipped']) or not parse_due(settings['updated_at']): raise ValueError('计划时间或跳过列表无效')
    return copy.deepcopy(data)


def path_for(vocab_path):
    return Path(vocab_path).parent/'learning.json'


def load(vocab_path):
    from learning_data import json_loads, MAX_BYTES
    path = path_for(vocab_path)
    if not path.exists(): return empty()
    if path.stat().st_size > MAX_BYTES: raise ValueError('学习活动文件过大')
    return validate(json_loads(path.read_text(encoding='utf-8-sig')))


def save(vocab_path, data):
    from learning_data import atomic_write, MAX_BYTES
    payload = json.dumps(validate(data), ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(payload) > MAX_BYTES: raise ValueError('学习活动文件过大，请先导出备份')
    atomic_write(path_for(vocab_path),payload)


def progress(data, clip, request):
    if set(request) != {'video','clip','version','t','mode','loop','speed','steps','note'}:
        raise ValueError('片段保存字段无效')
    if type(request['version']) is not int or request['version'] != data['version']:
        raise ValueError('学习记录已变化，请刷新后继续')
    state = {key:request[key] for key in ('t','mode','loop','speed','steps','note')}
    state.update({key:clip[key] for key in ('video','revision','start','end')})
    state['updated_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
    data = copy.deepcopy(data); data['segments'][clip['id']] = state; data['version'] += 1
    return validate(data)


def merge(local, incoming):
    from review_schedule import parse_due
    result = copy.deepcopy(local)
    for key, value in incoming['segments'].items():
        if key not in result['segments'] or parse_due(value['updated_at']) > parse_due(result['segments'][key]['updated_at']):
            result['segments'][key] = value
    identifiers = {item['id'] for item in result['attempts']}
    for item in incoming['attempts']:
        if item['id'] not in identifiers:
            result['attempts'].append(copy.deepcopy(item)); identifiers.add(item['id'])
        elif next(old for old in result['attempts'] if old['id'] == item['id']) != item:
            raise ValueError('练习编号对应不同记录，请核对备份')
    for day,value in incoming['days'].items():
        if day not in result['days'] or parse_due(value['updated_at']) > parse_due(result['days'][day]['updated_at']):
            result['days'][day] = copy.deepcopy(value)
    return validate(result)
