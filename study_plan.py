"""Explainable daily work from current evidence; no opaque model ranking."""
import copy
import datetime as dt
import re
from urllib.parse import urlencode
from review_schedule import parse_due, review_queue
from study_learning import STEPS, validate
from word_lookup import normalize_word

QUOTAS = (1,3,5,10)


def calendar(day,offset):
    if not isinstance(day,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day): raise ValueError('计划日期无效')
    try: date = dt.date.fromisoformat(day)
    except ValueError as exc: raise ValueError('计划日期无效') from exc
    if not 1900 <= date.year <= 9998: raise ValueError('计划日期超出支持范围')
    if type(offset) is not int or not -840 <= offset <= 840: raise ValueError('计划时区无效')
    start = dt.datetime.combine(date,dt.time(),dt.timezone(dt.timedelta(minutes=offset)))
    return start,start+dt.timedelta(days=1)


def recommend(data,entries,states,units,day,offset,now=None):
    start,end = calendar(day,offset); now = now or dt.datetime.now(dt.timezone.utc)
    settings = data['days'].get(day,{'quota':3,'skipped':[]})
    skipped = set(settings['skipped']); candidates = []
    for card in review_queue(entries,states,now)['cards']:
        word = card['word']; due = card['review']['due_at']
        candidates.append({'id':'word:'+word, 'kind':'word','title':'语境复习 · ' + word,
                           'reason':('复习时间已到：' + due) if due else '这是尚未评分的生词，用原句回忆并尝试造句。',
                           'href':'review.html?' + urlencode({'word':word}), 'minutes':2})
    latest = {}
    for item in sorted(data['attempts'],key=lambda item:parse_due(item['created_at'])):
        latest[item['task']] = item
    unresolved = [item for item in latest.values() if (item['correct'] is False or item['self_rating']=='needs_work') and parse_due(item['created_at']) >= now-dt.timedelta(days=14)]
    units_by_id = {unit['id']:unit for unit in units}
    for item in sorted(unresolved,key=lambda item:parse_due(item['created_at']),reverse=True):
        if item['kind'] == 'context': continue  # Word reviews already provide a context task.
        clip = units_by_id.get(item['clip'])
        if clip:
            candidates.append({'id':'task:'+item['task'],'kind':'retry','title':'重做练习 · ' + clip['title'] + ' · ' + clip['label'],
                               'reason':'最近一次回答未吻合原文或自评还需练习；先回听依据，再重新回答。',
                               'href':'study.html?' + urlencode({'video':clip['video'],'clip':clip['id'],'task':item['task']}), 'minutes':4})
    pending = [unit for unit in units if unit['id'] in data['segments'] and set(data['segments'][unit['id']]['steps']) != set(STEPS)]
    pending.sort(key=lambda unit:parse_due(data['segments'][unit['id']]['updated_at']),reverse=True)
    for clip in pending:
        count = len(data['segments'][clip['id']]['steps'])
        candidates.append({'id':'clip:'+clip['id'],'kind':'continue','title':'继续精听 · ' + clip['title'] + ' · ' + clip['label'],
                           'reason':f'上次已完成 {count}/4 个精听步骤，保存的位置和笔记可继续使用。',
                           'href':'study.html?' + urlencode({'video':clip['video'],'clip':clip['id']}),'minutes':8})
    # Supply a concrete first step even with no personal history.
    fresh = next((unit for unit in units if unit['id'] not in data['segments'] and 'clip:'+unit['id'] not in skipped),None)
    if fresh:
        candidates.append({'id':'clip:'+fresh['id'],'kind':'new','title':'学习新片段 · ' + fresh['title'] + ' · ' + fresh['label'],
                           'reason':'先从一段有字幕的材料完成盲听与核对，建立自己的学习记录。',
                           'href':'study.html?' + urlencode({'video':fresh['video'],'clip':fresh['id']}),'minutes':8})
    def today(value):
        parsed = parse_due(value)
        return parsed is not None and start <= parsed < end
    counts = {'words':sum(today(state.get('last_review')) for state in states.values()),
              'clips':sum(today(state['updated_at']) and set(state['steps'])==set(STEPS) for state in data['segments'].values()),
              'attempts':sum(today(item['created_at']) for item in data['attempts'])}
    visible = [item for item in candidates if item['id'] not in skipped]
    # Give each available activity a first slot before filling with more words.
    first, rest, kinds = [], [], set()
    for item in visible:
        group = 'listen' if item['kind'] in ('continue','new') else item['kind']
        if group not in kinds: first.append(item); kinds.add(group)
        else: rest.append(item)
    visible = first + rest
    return {'day':day,'quota':settings['quota'],'items':visible[:settings['quota']],
            'remaining':len(visible),'skipped':[item for item in candidates if item['id'] in skipped], 'skipped_ids':settings['skipped'], 'counts':counts,
            'fallback':'今天暂无待办。可以浏览视频库、选择其他片段，或等待下一次复习。' if units else '还没有可精听的字幕材料。先导入视频或补充字幕；没有字幕的视频仍可观看。'}


def settings(data,request):
    calendar(request.get('day'),request.get('offset'))
    if set(request) != {'day','offset','version','quota','skipped'}: raise ValueError('计划设置字段无效')
    if type(request['version']) is not int or request['version'] != data['version']: raise ValueError('学习记录已变化，请刷新计划')
    day = {'quota':request['quota'],'skipped':request['skipped'],'updated_at':dt.datetime.now(dt.timezone.utc).isoformat()}
    result = copy.deepcopy(data); result['days'][request['day']] = day; result['version'] += 1
    return validate(result)
