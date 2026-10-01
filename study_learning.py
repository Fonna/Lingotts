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
        if not re.fullmatch('[a-f0-9]{32}', identifier) or not isinstance(state, dict) or set(state) != fields:
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
    if data['attempts'] != [] or data['days'] != {}:
        raise ValueError('当前版本尚不支持此练习或计划数据')
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
    result = copy.deepcopy(local)
    for key, value in incoming['segments'].items():
        if key not in result['segments'] or value['updated_at'] > result['segments'][key]['updated_at']:
            result['segments'][key] = value
    return validate(result)
