"""Versioned learning data; media and catalog metadata are not backup payloads."""
import copy
import datetime as dt
import json
import math
import re
from pathlib import Path

from review_schedule import RATINGS, parse_due
from word_lookup import normalize_word

FORMAT = 'tedlib-learning-data'
VERSION = 1
MAX_ITEMS = 10000
MAX_BYTES = 16 * 1024 * 1024


def json_loads(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('JSON 包含重复字段：' + key)
            result[key] = value
        return result
    def constant(value):
        raise ValueError('JSON 不允许非有限数值：' + value)
    try:
        result = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
        raise ValueError('JSON 文件格式无效') from exc
    stack = [(result, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > 32:
            raise ValueError('JSON 嵌套层级过深')
        if isinstance(node, dict):
            stack.extend((value, depth + 1) for value in node.values())
        elif isinstance(node, list):
            stack.extend((value, depth + 1) for value in node)
    return result


def read_collection(path, key):
    path = Path(path)
    if not path.exists():
        return {key: [] if key == 'entries' else {}}
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f'{path.name} 超过 16 MiB，无法处理')
    data = json_loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or set(data) - {key, 'schema_version'}:
        raise ValueError(f'{path.name} 的数据结构不受支持')
    version = data.get('schema_version', VERSION)
    if type(version) is not int or version != VERSION:
        raise ValueError(f'{path.name} 的数据版本不受支持')
    return data


def text(value, name, limit, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f'{name} 必须是长度不超过 {limit} 的文本')
    return value


def seconds(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(name + ' 必须是非负有限秒数')
    return value


def validate_vocab(data):
    if not isinstance(data, dict) or set(data) != {'entries'}:
        raise ValueError('生词数据需要 entries 列表')
    entries = data['entries']
    if not isinstance(entries, list) or len(entries) > MAX_ITEMS:
        raise ValueError('生词记录不能超过 10000 条')
    identifiers = set()
    fields = {'id', 'word', 'sentence', 'slug', 'video_title', 't', 'created'}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != fields:
            raise ValueError('生词记录字段不完整或不受支持')
        identifier = text(entry['id'], '生词 ID', 80, True)
        if not re.fullmatch(r'[A-Za-z0-9_-]+', identifier):
            raise ValueError('生词 ID 只能包含字母、数字、横线和下划线')
        if identifier in identifiers:
            raise ValueError('生词 ID 重复：' + identifier)
        identifiers.add(identifier)
        text(entry['word'], '单词', 80, True)
        if not normalize_word(entry['word']):
            raise ValueError('生词必须是英语单词')
        text(entry['sentence'], '原句', 1000)
        text(entry['slug'], '视频关联', 255, True)
        text(entry['video_title'], '视频标题', 255)
        seconds(entry['t'], '生词时间位置')
        text(entry['created'], '创建时间', 80, True)
        try:
            dt.datetime.fromisoformat(entry['created'].replace('Z', '+00:00'))
        except ValueError as exc:
            raise ValueError('生词创建时间无效') from exc
    return copy.deepcopy(data)


def validate_review(data):
    if not isinstance(data, dict) or set(data) != {'words'} or not isinstance(data['words'], dict):
        raise ValueError('复习数据需要 words 对象')
    if len(data['words']) > MAX_ITEMS:
        raise ValueError('复习单词不能超过 10000 个')
    fields = {'version', 'repetitions', 'interval_days', 'ease', 'due_at', 'last_review', 'last_rating'}
    for word, state in data['words'].items():
        if normalize_word(word) != word:
            raise ValueError('复习单词必须是规范化的英语单词')
        if not isinstance(state, dict) or set(state) != fields:
            raise ValueError('复习安排字段不完整或不受支持')
        for key in ('version', 'repetitions', 'interval_days'):
            if type(state[key]) is not int or not 0 <= state[key] <= 100000000:
                raise ValueError('复习计数无效：' + key)
        ease = state['ease']
        if type(ease) not in (float, int) or not math.isfinite(ease) or not 1.3 <= ease <= 100:
            raise ValueError('复习难度系数无效')
        for key in ('due_at', 'last_review'):
            if state[key] is not None and not parse_due(state[key]):
                raise ValueError('复习时间必须包含时区：' + key)
        if state['last_rating'] is not None and (not isinstance(state['last_rating'], str) or state['last_rating'] not in RATINGS):
            raise ValueError('复习评级无效')
    return copy.deepcopy(data)


def validate_last(last):
    if last is None:
        return None
    if not isinstance(last, dict) or set(last) != {'slug', 't', 'title'}:
        raise ValueError('续播记录格式无效')
    text(last['slug'], '续播视频关联', 255, True)
    text(last['title'], '续播视频标题', 255)
    seconds(last['t'], '续播时间位置')
    return copy.deepcopy(last)


def video_references(records):
    return [{'id': record['id'], 'aliases': list(record.get('aliases', [])),
             'title': record.get('title', ''), 'folder': record.get('folder', '')}
            for record in records]


def canonicalize(vocab, last, references):
    aliases = {}
    for video in references:
        for key in [video['id'], video['folder'], *video['aliases']]:
            if key in aliases and aliases[key] != video['id']:
                raise ValueError('视频别名冲突：' + key)
            aliases[key] = video['id']
    warnings = []
    for entry in [*vocab['entries'], *([last] if last else [])]:
        slug = entry['slug']
        if slug in aliases:
            entry['slug'] = aliases[slug]
        elif slug not in warnings:
            warnings.append(slug)
    return ['未在当前目录找到视频，保留原关联：' + slug for slug in warnings]


def export_data(vocab_path, review_path, records, last=None):
    vocab_raw = read_collection(vocab_path, 'entries')
    review_raw = read_collection(review_path, 'words')
    vocab = validate_vocab({'entries': vocab_raw.get('entries')})
    review = validate_review({'words': review_raw.get('words')})
    last = validate_last(last)
    references = video_references(records)
    warnings = canonicalize(vocab, last, references)
    orphaned = set(review['words']) - {normalize_word(e['word']) for e in vocab['entries']}
    if orphaned:
        warnings.append(f'{len(orphaned)} 个复习安排暂无对应生词，保留在备份中')
    result = {'format': FORMAT, 'schema_version': VERSION,
            'exported_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
            'vocab': vocab, 'review': review, 'resume': {'last': last},
            'videos': references, 'warnings': warnings}
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')) > MAX_BYTES:
        raise ValueError('整套备份超过 16 MiB，无法处理')
    return result
