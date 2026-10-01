import http.server, json, os, urllib.parse, uuid, datetime, threading, math
from pathlib import Path
from word_lookup import AnalysisError, analyze, cached_analysis, lookup, normalize_word
from review_schedule import REVIEW_FILE, current_state, load_states, next_state, review_queue, save_states, load_epoch
import video_catalog as vc
from pronunciation import PronunciationError, speech_audio
import learning_data as ld
import hmac
import study_learning as sl

BASE_DIR = Path(__file__).parent.resolve()
MEDIA_DIR = BASE_DIR.parent / "TED"
DATA_DIR = BASE_DIR / "data"
VOCAB_FILE = DATA_DIR / "vocab.json"
PORT = 8765

MAX_BODY = 256 * 1024  # 256 KB cap for API request bodies
VOCAB_LOCK = threading.Lock()
ANALYSIS_LOCK = threading.Lock()
REVIEW_LOCK = threading.Lock()
CATALOG_DIR = vc.CATALOG_DIR
CATALOG_LOCK = threading.Lock()


def load_catalog():
    with CATALOG_LOCK:
        return vc.catalog(CATALOG_DIR, MEDIA_DIR)


def canonical_vocab(entries, videos):
    result = []
    for entry in entries:
        video = vc.resolve_video(entry.get("slug"), videos)
        result.append(dict(entry, slug=video["id"]) if video else dict(entry))
    return result


# ===== Vocab storage (plain JSON file) =====

def load_vocab():
    try:
        with open(VOCAB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("entries"), list):
            return data["entries"]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def save_vocab(entries):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = VOCAB_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"schema_version":1, "entries": entries}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, VOCAB_FILE)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def translate_path(self, path):
        path = urllib.parse.urlparse(path).path
        path = urllib.parse.unquote(path).lstrip('/')
        # Route /TED/... URLs to MEDIA_DIR (sibling project at parent/TED)
        if path == 'TED' or path.startswith('TED/'):
            rel = path[4:] if path.startswith('TED/') else ''
            base = str(MEDIA_DIR)
        else:
            rel = path
            base = str(BASE_DIR)
        full = Path(base, rel).resolve()
        if full.is_relative_to((VOCAB_FILE.parent / 'backups').resolve()):
            return str(BASE_DIR / '__forbidden_path__')
        if not full.is_relative_to(Path(base).resolve()):
            return str(BASE_DIR / "__forbidden_path__")
        return str(full)

    def log_message(self, fmt, *args):
        pass  # silent

    def end_headers(self):
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        if not path.startswith('/api/') and (path == '/' or Path(path).suffix.lower() in ('.html', '.js', '.css')):
            # Keep pages and shared helpers in sync after local development updates.
            self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    # ----- API helpers -----

    def send_json(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self, max_body=MAX_BODY, strict=False):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0:
            return {}
        if length > max_body:
            return None
        raw = self.rfile.read(length)
        try:
            data = ld.json_loads(raw.decode('utf-8')) if strict else json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    # ----- Routing -----

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/api/study':
            key = urllib.parse.parse_qs(parsed.query).get('video',[''])[0]
            try:
                with VOCAB_LOCK, REVIEW_LOCK, CATALOG_LOCK:
                    ld.recover_pending(VOCAB_FILE, REVIEW_FILE)
                    video = vc.resolve_video(key,vc.catalog(CATALOG_DIR,MEDIA_DIR)['videos'])
                    if not video: raise ValueError('视频不存在或目录有错误')
                    units = sl.clips(video['id'],vc.transcript_segments(video,MEDIA_DIR))
                    data = sl.load(VOCAB_FILE)
                    self.send_json(200,{'video':video,'clips':units,'learning':data})
            except (OSError,ValueError) as exc: self.send_json(400,{'error':str(exc)})
            return
        if parsed.path == '/api/learning-data/backups':
            with VOCAB_LOCK, REVIEW_LOCK:
                try:
                    ld.recover_pending(VOCAB_FILE, REVIEW_FILE)
                    self.send_json(200, ld.backup_history(VOCAB_FILE))
                except (OSError, ValueError) as exc:
                    self.send_json(503, {'error':str(exc)})
            return
        if parsed.path == '/api/learning-data/backup':
            identifier = urllib.parse.parse_qs(parsed.query).get('id', [''])[0]
            try:
                backup = ld.load_backup(VOCAB_FILE, identifier)
            except (OSError, ValueError) as exc:
                self.send_json(404, {'error':str(exc)})
                return
            self.send_json(200, {'backup':backup})
            return
        if parsed.path == "/api/pronunciation":
            port = self.server.server_port
            allowed = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
            origin = self.headers.get('Origin')
            referer = urllib.parse.urlparse(self.headers.get('Referer', ''))
            referer_origin = f'{referer.scheme}://{referer.netloc}' if referer.netloc else None
            if ((origin and origin not in allowed) or
                    (referer_origin and referer_origin not in allowed) or
                    self.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site')):
                self.send_json(403, {"error": "cross-origin API request denied"})
                return
            word = urllib.parse.parse_qs(parsed.query).get('word', [''])[0]
            try:
                audio = speech_audio(word)
            except ValueError:
                self.send_json(400, {"error": "invalid word"})
                return
            except PronunciationError as exc:
                self.send_json(503, {"error": str(exc)})
                return
            self.send_response(200)
            self.send_header('Content-Type', 'audio/wav')
            self.send_header('Content-Length', str(len(audio)))
            self.send_header('Cache-Control', 'private, max-age=86400')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(audio)
            return
        if parsed.path == "/api/videos":
            self.send_json(200, load_catalog())
            return
        if parsed.path in ("/api/video", "/api/transcript"):
            key = urllib.parse.parse_qs(parsed.query).get("video", [""])[0]
            video = vc.resolve_video(key, load_catalog()["videos"])
            if not video:
                self.send_json(404, {"error": "视频不存在或文件校验失败，请在内容管理中检查"})
                return
            if parsed.path == "/api/video":
                self.send_json(200, {"video": video})
            else:
                try:
                    segments = vc.transcript_segments(video, MEDIA_DIR)
                    video["transcript_status"] = "ready" if segments else "missing"
                    for segment in segments:
                        segment["id"] = video["id"] + ":" + segment["id"]
                    self.send_json(200, {"video": video, "segments": segments})
                except (OSError, ValueError) as exc:
                    self.send_json(422, {"error": str(exc)})
            return
        if parsed.path == "/api/video-folders":
            videos, issues = vc.records(CATALOG_DIR)
            folders = []
            for directory in sorted(MEDIA_DIR.iterdir()) if MEDIA_DIR.is_dir() else []:
                if not directory.is_dir() or directory.name.startswith("."):
                    continue
                old = next((v for v in videos if v.get("folder") == directory.name), None)
                item = {"folder": directory.name, "video_id": old["id"] if old else None}
                try:
                    vc.contained_path(MEDIA_DIR, directory.name)
                except ValueError as exc:
                    item.update({"error": str(exc), "media_files": []})
                    folders.append(item)
                    continue
                try:
                    if old:
                        item.update({"metadata": {k: v for k, v in old.items() if k in vc.FIELDS},
                                     "media_files": sorted(p.name for p in directory.iterdir()
                                         if p.is_file() and p.suffix.lower() in vc.MEDIA_EXTENSIONS)})
                    else:
                        item.update(vc.inspect_folder(directory.name, MEDIA_DIR))
                except (OSError, ValueError) as exc:
                    item["error"] = str(exc)
                    item["media_files"] = sorted(p.name for p in directory.iterdir()
                        if p.is_file() and p.suffix.lower() in vc.MEDIA_EXTENSIONS)
                folders.append(item)
            listed = {item["video_id"] for item in folders}
            for old in videos:
                if old["id"] not in listed:
                    folders.append({"folder": old.get("folder", ""), "video_id": old["id"],
                        "metadata": {k: v for k, v in old.items() if k in vc.FIELDS},
                        "media_files": [old.get("media_file", "")],
                        "error": "媒体目录不存在，请修改路径以重新关联。固定 ID 与旧别名会保留。"})
            self.send_json(200, {"folders": folders, "issues": issues})
            return
        if parsed.path == "/api/vocab":
            with VOCAB_LOCK, REVIEW_LOCK:
                try: ld.recover_pending(VOCAB_FILE, REVIEW_FILE)
                except (OSError, ValueError) as exc:
                    self.send_json(503, {'error':'未完成恢复需要处理：' + str(exc)}); return
                entries = canonical_vocab(load_vocab(), load_catalog()["videos"])
            self.send_json(200, {"entries": entries})
            return
        if parsed.path == "/api/review":
            with VOCAB_LOCK, REVIEW_LOCK:
                try: ld.recover_pending(VOCAB_FILE, REVIEW_FILE)
                except (OSError, ValueError) as exc:
                    self.send_json(503, {'error':'未完成恢复需要处理：' + str(exc)}); return
                queue = review_queue(load_vocab(), load_states(REVIEW_FILE))
                queue['epoch'] = load_epoch(REVIEW_FILE)
            self.send_json(200, queue)
            return
        if parsed.path == "/api/word-analysis":
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            try:
                cached = cached_analysis(query.get("word", [""])[0], query.get("sentence", [""])[0])
            except ValueError:
                self.send_json(400, {"error": "invalid word or sentence"})
                return
            self.send_json(200, {"analysis": cached})
            return
        if parsed.path == "/api/dictionary":
            word = urllib.parse.parse_qs(parsed.query).get("word", [""])[0]
            try:
                entry = lookup(word)
            except ValueError:
                self.send_json(400, {"error": "invalid word"})
                return
            except AnalysisError as exc:
                self.send_json(exc.status, {"error": str(exc)})
                return
            if entry is None:
                self.send_json(404, {"error": "本地词典暂无此词"})
            else:
                self.send_json(200, {"entry": entry})
            return

        range_header = self.headers.get('Range')
        if range_header and range_header.startswith('bytes='):
            path = self.translate_path(self.path)
            if path and os.path.isfile(path):
                try:
                    file_size = os.path.getsize(path)
                    spec = range_header[len('bytes='):].strip()
                    if spec.startswith('-'):
                        length = int(spec[1:]); start = max(0, file_size - length); end = file_size - 1
                    else:
                        parts = spec.split('-', 1)
                        start = int(parts[0]); end = int(parts[1]) if parts[1] else file_size - 1
                    if start < 0 or start >= file_size or end >= file_size or start > end:
                        self.send_response(416)
                        self.send_header('Content-Range', f'bytes */{file_size}')
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.end_headers()
                        return
                    length = end - start + 1
                    with open(path, 'rb') as f:
                        f.seek(start); data = f.read(length)
                    self.send_response(206)
                    self.send_header('Content-Type', self.guess_type(path))
                    self.send_header('Content-Length', str(length))
                    self.send_header('Content-Range', f'bytes {start}-{end}/{file_size}')
                    self.send_header('Accept-Ranges', 'bytes')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.send_header('Cache-Control', 'no-cache')
                    self.end_headers()
                    self.wfile.write(data)
                    return
                except (OSError, ValueError):
                    pass
        super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/api/word-analysis", "/api/vocab", "/api/review", "/api/videos", '/api/study/progress') or parsed.path.startswith('/api/learning-data/'):
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                self.send_json(415, {"error": "Content-Type must be application/json"})
                return
            origin = self.headers.get("Origin")
            allowed = (f"http://127.0.0.1:{self.server.server_port}",
                       f"http://localhost:{self.server.server_port}")
            if origin and origin not in allowed:
                self.send_json(403, {"error": "cross-origin API request denied"})
                return
        if parsed.path == '/api/study/progress':
            request = self.read_json_body(strict=True)
            if request is None: self.send_json(400,{'error':'片段请求格式无效'}); return
            try:
                with VOCAB_LOCK, REVIEW_LOCK, CATALOG_LOCK:
                    ld.recover_pending(VOCAB_FILE, REVIEW_FILE)
                    video = vc.resolve_video(request.get('video'),vc.catalog(CATALOG_DIR,MEDIA_DIR)['videos'])
                    if not video: raise ValueError('视频不存在')
                    units = sl.clips(video['id'],vc.transcript_segments(video,MEDIA_DIR))
                    clip = next((unit for unit in units if unit['id'] == request.get('clip')),None)
                    if not clip: raise ValueError('字幕或片段已变化，请刷新后重新选择；旧进度保留')
                    data = sl.progress(sl.load(VOCAB_FILE),clip,request)
                    sl.save(VOCAB_FILE,data)
                    self.send_json(200,{'learning':data})
            except (ValueError,OSError) as exc: self.send_json(409,{'error':str(exc)})
            return
        if parsed.path in ('/api/learning-data/preview', '/api/learning-data/restore'):
            data = self.read_json_body(ld.MAX_BYTES * 2 + 4096, strict=True)
            if data is None or set(data) - {'text', 'mode', 'last', 'token'}:
                self.send_json(400, {'error':'恢复请求格式无效'}); return
            try:
                with VOCAB_LOCK, REVIEW_LOCK, CATALOG_LOCK:
                    ld.recover_pending(VOCAB_FILE, REVIEW_FILE)
                    records, issues = vc.records(CATALOG_DIR)
                    if issues: raise ValueError('当前视频目录有校验问题，请先修复')
                    plan = ld.plan_restore(VOCAB_FILE, REVIEW_FILE, records, data.get('text'), data.get('mode'), data.get('last'))
                    if parsed.path.endswith('/preview'):
                        preview = {key:plan[key] for key in ('token','mode','warnings','counts','exported_at','resume')}
                        self.send_json(200, {'preview':preview}); return
                    token = data.get('token')
                    if (not isinstance(token, str) or len(token) != 64 or
                            any(char not in '0123456789abcdef' for char in token) or
                            not hmac.compare_digest(token, plan['token'])):
                        self.send_json(409, {'error':'预览后学习记录或恢复选项已变化，请重新预览'}); return
                    identifier = ld.commit_restore(VOCAB_FILE, REVIEW_FILE, plan)
            except ValueError as exc:
                self.send_json(400, {'error':str(exc)}); return
            except OSError as exc:
                self.send_json(503, {'error':'恢复未完成，请检查磁盘后重新预览；已保存的恢复前备份会保留：' + str(exc)}); return
            self.send_json(200, {'ok':True, 'backup_id':identifier, 'resume':plan['resume'], 'counts':plan['counts']})
            return
        if parsed.path == '/api/learning-data/export':
            data = self.read_json_body(strict=True)
            if data is None or set(data) - {'last'}:
                self.send_json(400, {'error': '导出参数格式无效'})
                return
            try:
                with VOCAB_LOCK, REVIEW_LOCK, CATALOG_LOCK:
                    ld.recover_pending(VOCAB_FILE, REVIEW_FILE)
                    records, issues = vc.records(CATALOG_DIR)
                    if issues:
                        raise ValueError('视频目录有校验问题，请先在内容管理中修复后导出')
                    backup = ld.export_data(VOCAB_FILE, REVIEW_FILE, records, data.get('last'))
            except (OSError, ValueError) as exc:
                self.send_json(400, {'error': str(exc)})
                return
            self.send_json(200, {'backup': backup})
            return
        if parsed.path == "/api/videos":
            data = self.read_json_body()
            if data is None:
                self.send_json(400, {"error": "invalid JSON body"})
                return
            try:
                with CATALOG_LOCK:
                    record = vc.save_video(data.get("folder"), data.get("metadata", {}),
                                           data.get("id"), CATALOG_DIR, MEDIA_DIR)
            except (OSError, ValueError) as exc:
                self.send_json(400, {"error": str(exc)})
                return
            self.send_json(200 if data.get("id") else 201, {"video": record})
            return
        if parsed.path == "/api/word-analysis":
            data = self.read_json_body()
            if data is None:
                self.send_json(400, {"error": "invalid JSON body"})
                return
            try:
                with ANALYSIS_LOCK:
                    result, cached = analyze(data.get("word"), data.get("sentence", ""))
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
                return
            except AnalysisError as exc:
                self.send_json(exc.status, {"error": str(exc)})
                return
            self.send_json(200, {"analysis": result, "cached": cached})
            return
        if parsed.path == "/api/review":
            data = self.read_json_body()
            if data is None:
                self.send_json(400, {"error": "invalid JSON body"})
                return
            word = normalize_word(data.get("word"))
            rating = data.get("rating")
            version = data.get("version")
            if (not word or rating not in ("known", "fuzzy", "unknown")
                    or not isinstance(version, int) or isinstance(version, bool) or version < 0):
                self.send_json(400, {"error": "invalid review grade"})
                return
            with VOCAB_LOCK, REVIEW_LOCK:
                try: ld.recover_pending(VOCAB_FILE, REVIEW_FILE)
                except (OSError, ValueError) as exc:
                    self.send_json(503, {'error':str(exc)}); return
                epoch = load_epoch(REVIEW_FILE)
                if type(data.get('epoch',0)) is not int or data.get('epoch',0) != epoch:
                    self.send_json(409, {'error':'学习数据已恢复，请刷新复习卡后评分'}); return
                entries = load_vocab()
                if not any(normalize_word(e.get("word")) == word for e in entries if isinstance(e, dict)):
                    self.send_json(404, {"error": "word is no longer in the vocabulary book"})
                    return
                states = load_states(REVIEW_FILE)
                previous = current_state(states.get(word))
                if previous["version"] != version:
                    self.send_json(409, {"error": "review state changed; reload the page"})
                    return
                updated = next_state(previous, rating)
                states[word] = updated
                save_states(states, REVIEW_FILE)
                stats = review_queue(entries, states)["stats"]
            self.send_json(200, {"review": updated, "stats": stats})
            return
        if parsed.path == "/api/vocab":
            data = self.read_json_body()
            if data is None:
                self.send_json(400, {"error": "invalid JSON body"})
                return
            word = data.get("word")
            sentence = data.get("sentence", "")
            slug = data.get("slug")
            video_title = data.get("video_title", "")
            if not (isinstance(word, str) and isinstance(sentence, str)
                    and isinstance(slug, str) and isinstance(video_title, str)):
                self.send_json(400, {"error": "invalid vocabulary entry"})
                return
            word, sentence, slug, video_title = (
                word.strip(), sentence.strip(), slug.strip(), video_title.strip())
            try:
                t = float(data.get("t"))
            except (TypeError, ValueError):
                t = 0.0
            if (not normalize_word(word) or not slug or len(word) > 80 or len(sentence) > 1000
                    or len(slug) > 255 or len(video_title) > 255
                    or not math.isfinite(t) or t < 0):
                self.send_json(400, {"error": "invalid vocabulary entry"})
                return

            with VOCAB_LOCK, REVIEW_LOCK:
                try: ld.recover_pending(VOCAB_FILE, REVIEW_FILE)
                except (OSError, ValueError) as exc:
                    self.send_json(503, {'error':str(exc)}); return
                entries = load_vocab()
                videos = load_catalog()["videos"]
                video = vc.resolve_video(slug, videos)
                if video:
                    slug = video["id"]
                for e in entries:  # dedupe same word at same spot in same video
                    old_video = vc.resolve_video(e["slug"], videos)
                    old_slug = old_video["id"] if old_video else e["slug"]
                    if (old_slug == slug and e["word"].lower() == word.lower()
                            and abs(float(e.get("t") or 0.0) - t) < 0.5):
                        self.send_json(200, {"entry": dict(e, slug=slug), "deduplicated": True})
                        return

                entry = {
                    "id": uuid.uuid4().hex[:12],
                    "word": word,
                    "sentence": sentence,
                    "slug": slug,
                    "video_title": video_title,
                    "t": round(t, 2),
                    "created": datetime.datetime.now().isoformat(timespec="seconds"),
                }
                entries.append(entry)
                save_vocab(entries)
            self.send_json(201, {"entry": entry})
            return
        self.send_json(404, {"error": "unknown endpoint"})

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/vocab":
            entry_id = urllib.parse.parse_qs(parsed.query).get("id", [""])[0]
            with VOCAB_LOCK, REVIEW_LOCK:
                try: ld.recover_pending(VOCAB_FILE, REVIEW_FILE)
                except (OSError, ValueError) as exc:
                    self.send_json(503, {'error':str(exc)}); return
                entries = load_vocab()
                remaining = [e for e in entries if e["id"] != entry_id]
                if len(remaining) == len(entries):
                    self.send_json(404, {"error": "entry not found"})
                    return
                save_vocab(remaining)
            self.send_json(200, {"ok": True})
            return
        self.send_json(404, {"error": "unknown endpoint"})


if __name__ == "__main__":
    os.chdir(str(BASE_DIR))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    httpd.daemon_threads = True
    print(f"SERVER_READY:{PORT}", flush=True)
    httpd.serve_forever()
