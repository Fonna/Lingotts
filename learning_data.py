"""Versioned learning data; media and catalog metadata are not backup payloads."""
import copy
import base64
import bisect
import datetime as dt
import json
import math
import re
import hashlib
import os
import tempfile
import uuid
from pathlib import Path

from review_schedule import RATINGS, parse_due, load_epoch
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
    if not isinstance(data, dict) or set(data) - ({key, 'schema_version', 'epoch'} if key == 'words' else {key, 'schema_version'}):
        raise ValueError(f'{path.name} 的数据结构不受支持')
    version = data.get('schema_version', VERSION)
    if type(version) is not int or version != VERSION:
        raise ValueError(f'{path.name} 的数据版本不受支持')
    if key == 'words' and (type(data.get('epoch', 0)) is not int or data.get('epoch', 0) < 0):
        raise ValueError('复习数据的恢复代次无效')
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
    return validate_references([{'id': record.get('id'), 'aliases': record.get('aliases', []),
             'title': record.get('title', ''), 'folder': record.get('folder', '')}
            for record in records])


def validate_references(references):
    if not isinstance(references, list) or len(references) > MAX_ITEMS:
        raise ValueError('视频映射格式无效')
    identifiers = set()
    owners = {}
    for item in references:
        if not isinstance(item, dict) or set(item) != {'id', 'aliases', 'title', 'folder'}:
            raise ValueError('视频映射字段无效')
        if not isinstance(item['id'], str) or not re.fullmatch(r'[a-f0-9]{32}', item['id']) or item['id'] in identifiers:
            raise ValueError('视频映射 ID 无效或重复')
        identifiers.add(item['id'])
        text(item['title'], '映射视频标题', 255)
        text(item['folder'], '映射视频目录', 255, True)
        if not isinstance(item['aliases'], list) or len(item['aliases']) > 100:
            raise ValueError('视频别名格式无效')
        for alias in item['aliases']: text(alias, '视频别名', 255, True)
        for key in [item['id'], item['folder'], *item['aliases']]:
            if key in owners and owners[key] != item['id']:
                raise ValueError('视频映射的别名冲突：' + key)
            owners[key] = item['id']
    return copy.deepcopy(references)


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


def validate_backup(data):
    if not isinstance(data, dict) or data.get('format') != FORMAT:
        raise ValueError('请选择 TedLib 学习备份 JSON')
    if type(data.get('schema_version')) is not int or data['schema_version'] != VERSION:
        raise ValueError('备份版本不受支持，请使用相同或更新版本的程序')
    if set(data) != {'format', 'schema_version', 'exported_at', 'vocab', 'review', 'resume', 'videos', 'warnings'}:
        raise ValueError('备份字段不完整或不受支持')
    if not parse_due(data['exported_at']):
        raise ValueError('备份导出时间无效')
    if not isinstance(data['resume'], dict) or set(data['resume']) != {'last'}:
        raise ValueError('备份续播记录格式无效')
    if not isinstance(data['warnings'], list) or len(data['warnings']) > MAX_ITEMS:
        raise ValueError('备份提示格式无效')
    for warning in data['warnings']: text(warning, '备份提示', 1000)
    result = copy.deepcopy(data)
    result['vocab'] = validate_vocab(data['vocab'])
    result['review'] = validate_review(data['review'])
    result['resume']['last'] = validate_last(data['resume']['last'])
    result['videos'] = validate_references(data['videos'])
    return result


def fingerprint(vocab_path, review_path, records, last):
    digest = hashlib.sha256()
    for path in (Path(vocab_path), Path(review_path)):
        digest.update(b'present:' + path.read_bytes() if path.exists() else b'missing:')
        digest.update(b'\x00')
    digest.update(json.dumps([video_references(records), validate_last(last)], sort_keys=True,
                             ensure_ascii=False, allow_nan=False).encode('utf-8'))
    return digest.hexdigest()


def remap_backup(backup, current_references):
    # Exact IDs take precedence. A migrated library may share an old directory alias.
    local = {}
    for video in current_references:
        for key in [video['id'], video['folder'], *video['aliases']]:
            if key in local and local[key] != video['id']:
                raise ValueError('当前视频目录存在别名冲突')
            local[key] = video['id']
    mapping = dict(local)
    for source in backup['videos']:
        target = local.get(source['id'])
        if not target:
            matches = {local[key] for key in [source['folder'], *source['aliases']] if key in local}
            if len(matches) > 1:
                raise ValueError('备份视频别名对应多个本地视频，请先修复目录')
            target = next(iter(matches), source['id'])
        for key in [source['id'], source['folder'], *source['aliases']]:
            if key not in local:
                if key in mapping and mapping[key] != target:
                    raise ValueError('备份视频别名冲突')
                mapping[key] = target
    warnings = []
    entries = [*backup['vocab']['entries'], *([backup['resume']['last']] if backup['resume']['last'] else [])]
    local_ids = {video['id'] for video in current_references}
    for entry in entries:
        entry['slug'] = mapping.get(entry['slug'], entry['slug'])
        if entry['slug'] not in local_ids and entry['slug'] not in warnings:
            warnings.append(entry['slug'])
    return ['本地暂无对应视频，学习关联仍保留：' + key for key in warnings]


def plan_restore(vocab_path, review_path, records, source, mode, last=None):
    if mode not in ('merge', 'replace'):
        raise ValueError('请选择合并或覆盖模式')
    if not isinstance(source, str) or len(source.encode('utf-8')) > MAX_BYTES:
        raise ValueError('备份必须是最多 16 MiB 的 JSON 文本')
    incoming = validate_backup(json_loads(source.lstrip('\ufeff')))
    current = export_data(vocab_path, review_path, records, last)
    warnings = remap_backup(incoming, current['videos'])
    duplicates, id_conflicts, review_kept = 0, 0, 0
    if mode == 'replace':
        vocab, states = incoming['vocab'], incoming['review']
        restored_last = incoming['resume']['last']
    else:
        vocab, states = copy.deepcopy(current['vocab']), copy.deepcopy(current['review'])
        identifiers = {entry['id'] for entry in vocab['entries']}
        positions = {}
        for entry in vocab['entries']:
            positions.setdefault((normalize_word(entry['word']), entry['slug']), []).append(entry['t'])
        for bucket in positions.values(): bucket.sort()
        for entry in incoming['vocab']['entries']:
            bucket = positions.setdefault((normalize_word(entry['word']), entry['slug']), [])
            position = bisect.bisect_left(bucket, entry['t'])
            if any(abs(bucket[index] - entry['t']) < 0.5 for index in (position - 1, position)
                   if 0 <= index < len(bucket)):
                duplicates += 1
                continue
            if entry['id'] in identifiers:
                id_conflicts += 1
                base = hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()[:12]
                entry['id'] = base
                suffix = 1
                while entry['id'] in identifiers:
                    entry['id'] = f'{base}_{suffix}'; suffix += 1
            identifiers.add(entry['id']); vocab['entries'].append(entry)
            bisect.insort(bucket, entry['t'])
        for word, state in incoming['review']['words'].items():
            old = states['words'].get(word)
            old_time = parse_due(old['last_review']) if old else None
            new_time = parse_due(state['last_review'])
            if old is None or (new_time is not None and (old_time is None or new_time > old_time)):
                states['words'][word] = state
            else:
                review_kept += 1
        restored_last = current['resume']['last'] or incoming['resume']['last']
    validate_vocab(vocab); validate_review(states)
    token_data = json.dumps([source, mode, fingerprint(vocab_path, review_path, records, last)], ensure_ascii=False).encode('utf-8')
    token = hashlib.sha256(token_data).hexdigest()
    return {'token':token, 'mode':mode, 'warnings':warnings, 'exported_at':incoming['exported_at'],
            'counts':{'before_vocab':len(current['vocab']['entries']), 'incoming_vocab':len(incoming['vocab']['entries']),
                      'after_vocab':len(vocab['entries']), 'after_review':len(states['words']),
                      'duplicates':duplicates, 'id_conflicts':id_conflicts, 'review_kept':review_kept},
            'resume':{'last':restored_last}, 'vocab':vocab, 'review':states, 'before':current}


def atomic_write(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.tmp', delete=False) as temporary:
        temporary.write(data); temporary.flush(); os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists(): temporary_path.unlink()


def encoded_file(path):
    return base64.b64encode(Path(path).read_bytes()).decode('ascii') if Path(path).exists() else None


def restore_encoded(path, encoded):
    if encoded is None:
        Path(path).unlink(missing_ok=True)
    else:
        atomic_write(path, base64.b64decode(encoded, validate=True))


def recover_pending(vocab_path, review_path):
    directory = Path(vocab_path).parent / 'backups'
    pending = directory / 'pending.json'
    if not pending.exists(): return
    pointer = json_loads(pending.read_text(encoding='utf-8'))
    identifier = pointer.get('id') if isinstance(pointer, dict) else None
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise ValueError('恢复事务编号无效，请保留备份并检查数据')
    journal_path = directory / identifier / 'transaction.json'
    journal = json_loads(journal_path.read_text(encoding='utf-8'))
    if (not isinstance(journal, dict) or set(journal) != {'status','before','after'} or
            any(not isinstance(journal.get(key), dict) or set(journal[key]) != {'vocab','review'}
                for key in ('before','after'))):
        raise ValueError('恢复事务内容无效，请保留备份并检查数据')
    if journal.get('status') == 'prepared':
        for name, path in [('vocab',vocab_path), ('review',review_path)]:
            present = encoded_file(path)
            if present not in (journal['before'][name], journal['after'][name]):
                raise ValueError('未完成恢复后的文件已被手工修改，请先保留文件并处理恢复前备份')
        for name, path in [('vocab',vocab_path), ('review',review_path)]:
            restore_encoded(path, journal['before'][name])
        journal['status'] = 'rolled_back'
        atomic_write(journal_path, json.dumps(journal).encode('utf-8'))
    elif journal.get('status') not in ('committed', 'rolled_back'):
        raise ValueError('恢复事务状态无效，请保留备份并检查数据')
    try: pending.unlink()
    except OSError: pass


def commit_restore(vocab_path, review_path, plan):
    identifier = uuid.uuid4().hex
    directory = Path(vocab_path).parent / 'backups' / identifier
    directory.mkdir(parents=True)
    payloads = {'vocab':json.dumps(dict(schema_version=1, **plan['vocab']), ensure_ascii=False, allow_nan=False).encode('utf-8'),
                'review':json.dumps(dict(schema_version=1, epoch=load_epoch(review_path) + 1, **plan['review']), ensure_ascii=False, allow_nan=False).encode('utf-8')}
    journal = {'status':'prepared', 'before':{'vocab':encoded_file(vocab_path),'review':encoded_file(review_path)},
               'after':{key:base64.b64encode(value).decode('ascii') for key, value in payloads.items()}}
    atomic_write(directory/'backup.json', json.dumps(plan['before'], ensure_ascii=False, allow_nan=False).encode('utf-8'))
    atomic_write(directory/'transaction.json', json.dumps(journal).encode('utf-8'))
    pending = directory.parent/'pending.json'
    atomic_write(pending, json.dumps({'id':identifier}).encode('utf-8'))
    try:
        atomic_write(vocab_path, payloads['vocab'])
        atomic_write(review_path, payloads['review'])
        journal['status'] = 'committed'
        atomic_write(directory/'transaction.json', json.dumps(journal).encode('utf-8'))
    except OSError:
        # Prepared journals also permit rollback after process termination.
        journal['status'] = 'prepared'
        recover_pending(vocab_path, review_path)
        raise
    # A committed journal makes cleanup retryable without undoing committed data.
    try: pending.unlink(missing_ok=True)
    except OSError: pass
    return identifier


def load_backup(vocab_path, identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise ValueError('备份编号无效')
    path = Path(vocab_path).parent / 'backups' / identifier / 'backup.json'
    if not path.resolve().is_relative_to((Path(vocab_path).parent / 'backups').resolve()):
        raise ValueError('备份路径超出数据目录')
    if not path.is_file(): raise ValueError('恢复前备份不存在')
    return validate_backup(json_loads(path.read_text(encoding='utf-8')))
