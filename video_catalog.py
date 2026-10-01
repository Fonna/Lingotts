"""Single source of video metadata; media files remain outside the repository."""
import datetime as dt
import json
import math
import os
import re
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

BASE_DIR = Path(__file__).resolve().parent
CATALOG_DIR = BASE_DIR / "catalog"
MEDIA_DIR = BASE_DIR.parent / "TED"
MEDIA_EXTENSIONS = {".mp4", ".webm", ".m4a", ".mp3", ".wav", ".ogg", ".mov"}
FIELDS = {"title", "speaker", "category", "tags", "published", "downloaded",
          "youtube", "media_file", "transcript_file", "duration_s", "language",
          "summary_zh", "summary_en", "folder"}
SEGMENT = re.compile(r"^\[([\d.]+)s\s*->\s*([\d.]+)s\]\s+(.+)$")


def contained_path(root, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("路径必须是非空相对路径，使用 / 分隔")
    parts = relative.split("/")
    if any(p in ("", ".", "..") or ":" in p for p in parts):
        raise ValueError("路径不能包含绝对路径或父目录")
    root = Path(root).resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or target == root:
        raise ValueError("路径超出媒体目录")
    return target


def read_segments(path):
    segments = []
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        match = SEGMENT.fullmatch(line)
        if not match:
            if line.startswith("["):
                raise ValueError(f"字幕第 {number} 行时间戳格式错误")
            continue
        start, end = float(match[1]), float(match[2])
        if (not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start
                or (segments and start < segments[-1]["start"])):
            raise ValueError(f"字幕第 {number} 行时间戳无效或顺序错误")
        segments.append({"start": start, "end": end, "text": match[3],
                         "id": f"segment-{len(segments)}"})
    if not segments:
        raise ValueError("字幕没有可用的 [开始s -> 结束s] 文本")
    return segments


def validate(record, media_dir):
    if (not isinstance(record, dict) or type(record.get("schema_version")) is not int
            or record.get("schema_version") != 1):
        raise ValueError("不支持的元数据版本")
    if not re.fullmatch(r"[a-f0-9]{32}", str(record.get("id", ""))):
        raise ValueError("视频 ID 必须是固定的 32 位十六进制标识")
    for field in FIELDS - {"tags", "duration_s"}:
        value = record.get(field, "")
        limit = 6000 if field.startswith("summary_") else 255
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f"{field} 必须是长度不超过 {limit} 的文本")
    if not record.get("title", "").strip():
        raise ValueError("请填写标题")
    tags = record.get("tags", [])
    if (not isinstance(tags, list) or len(tags) > 30
            or any(not isinstance(t, str) or len(t) > 80 for t in tags)):
        raise ValueError("tags 必须是最多 30 项的文本列表")
    aliases = record.get("aliases", [])
    if (not isinstance(aliases, list) or len(aliases) > 100
            or any(not isinstance(a, str) or not a or len(a) > 255 for a in aliases)):
        raise ValueError("aliases 格式错误")
    for field in ("published", "downloaded"):
        if record.get(field):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", record[field]):
                raise ValueError(f"{field} 使用 YYYY-MM-DD")
            dt.date.fromisoformat(record[field])
    if record.get("youtube"):
        url = urlsplit(record["youtube"])
        if url.scheme not in ("https", "http") or not url.netloc or url.username or url.password:
            raise ValueError("来源链接必须是 HTTP/HTTPS 地址")
    duration = record.get("duration_s", 0)
    if (isinstance(duration, bool) or not isinstance(duration, (int, float))
            or duration < 0):
        raise ValueError("时长必须是非负有限数值")
    try:
        finite_duration = math.isfinite(duration)
    except OverflowError:
        finite_duration = False
    if not finite_duration:
        raise ValueError("时长必须是非负有限数值")
    folder = contained_path(media_dir, record.get("folder"))
    if not folder.is_dir():
        raise ValueError("媒体目录不存在")
    media = contained_path(folder, record.get("media_file"))
    if not media.is_file() or media.suffix.lower() not in MEDIA_EXTENSIONS:
        raise ValueError("媒体文件不存在或格式不受支持")
    transcript = contained_path(folder, record.get("transcript_file"))
    if not transcript.is_file():
        raise ValueError("字幕文件不存在")
    return read_segments(transcript)


def records(catalog_dir):
    items, issues = [], []
    for path in sorted(Path(catalog_dir).glob("*.json")):
        try:
            record = json.loads(contained_path(catalog_dir, path.name).read_text(encoding="utf-8-sig"))
            if not isinstance(record, dict) or path.stem != record.get("id"):
                raise ValueError("文件名必须与视频 ID 一致")
            items.append(record)
        except (OSError, ValueError) as exc:
            issues.append({"file": path.name, "error": str(exc)})
    return items, issues


def identity_keys(record):
    aliases = record.get("aliases", [])
    if (not isinstance(record.get("id"), str) or not isinstance(record.get("folder"), str)
            or not isinstance(aliases, list) or any(not isinstance(a, str) for a in aliases)):
        raise ValueError("视频身份字段损坏，请修复 id、folder 或 aliases")
    return {record["id"], record["folder"], *aliases}


def catalog(catalog_dir=CATALOG_DIR, media_dir=MEDIA_DIR):
    items, issues = records(catalog_dir)
    videos, valid = [], []
    for record in items:
        try:
            validate(record, media_dir)
            valid.append(record)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            issues.append({"file": str(record.get("id", "?")) + ".json", "error": str(exc)})
    for record in valid:
        try:
            if any(identity_keys(record) & identity_keys(other)
                   for other in valid if other is not record):
                raise ValueError("视频 ID、目录或旧别名与其他记录冲突")
            video = dict(record)
            base = "/TED/" + "/".join(quote(p, safe="") for p in record["folder"].split("/")) + "/"
            video["media_url"] = base + "/".join(quote(p, safe="") for p in record["media_file"].split("/"))
            video["transcript_url"] = base + "/".join(quote(p, safe="") for p in record["transcript_file"].split("/"))
            videos.append(video)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            issues.append({"file": str(record.get("id", "?")) + ".json", "error": str(exc)})
    videos.sort(key=lambda v: (v.get("published", ""), v["title"]), reverse=True)
    return {"schema_version": 1, "videos": videos, "issues": issues}


def resolve_video(key, videos):
    return next((v for v in videos if key in identity_keys(v)), None)


def inspect_folder(folder, media_dir=MEDIA_DIR):
    directory = contained_path(media_dir, folder)
    if not directory.is_dir():
        raise ValueError("媒体目录不存在")
    source = contained_path(directory, "meta.json")
    metadata = json.loads(source.read_text(encoding="utf-8-sig")) if source.exists() else {}
    if not isinstance(metadata, dict):
        raise ValueError("旧 meta.json 必须是对象")
    result = {k: v for k, v in metadata.items() if k in FIELDS}
    media = sorted(p.name for p in directory.iterdir()
                   if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS and p.name != "audio_16k.wav")
    preferred = [m for m in media if Path(m).suffix.lower() in {".mp4", ".webm", ".mov"}]
    if not result.get("media_file") and media:
        candidates = preferred or media
        if len(candidates) == 1:
            result["media_file"] = candidates[0]
    result["folder"] = folder
    result.setdefault("transcript_file", "transcript.txt")
    result.setdefault("title", folder)
    result.setdefault("language", "en")
    transcript = contained_path(directory, result["transcript_file"])
    if transcript.is_file():
        text = transcript.read_text(encoding="utf-8-sig")
        title = re.search(r"^Title:\s*(.+)$", text, re.M)
        if title and "title" not in metadata:
            parts = title[1].split(" | ")
            result["title"] = parts[0].strip()
            if len(parts) > 2:
                result.setdefault("speaker", parts[1].strip())
        link = re.search(r"^Source:\s*(https?://\S+)", text, re.M)
        if link:
            result.setdefault("youtube", link[1])
        try:
            segments = read_segments(transcript)
        except ValueError:
            segments = []  # Discovery permits repair; save always performs strict validation.
        duration = re.search(r"^Duration:\s*([\d.]+)s", text, re.M)
        result.setdefault("duration_s", float(duration[1]) if duration else (segments[-1]["end"] if segments else 0))
    result.setdefault("downloaded", dt.datetime.fromtimestamp(directory.stat().st_mtime).date().isoformat())
    return {"metadata": result, "media_files": media}


def save_video(folder, fields, video_id=None, catalog_dir=CATALOG_DIR, media_dir=MEDIA_DIR):
    if not isinstance(fields, dict) or set(fields) - FIELDS:
        raise ValueError("元数据包含未知字段")
    if "folder" in fields and fields["folder"] != folder:
        raise ValueError("补充元数据中的 folder 与导入目录不一致")
    if video_id is not None and (not isinstance(video_id, str) or not re.fullmatch(r"[a-f0-9]{32}", video_id)):
        raise ValueError("要编辑的视频 ID 格式错误")
    items, issues = records(catalog_dir)
    if issues:
        raise ValueError("请先修复目录元数据：" + issues[0]["error"])
    old = next((r for r in items if r["id"] == video_id), None)
    if video_id and old is None:
        raise ValueError("要编辑的视频不存在")
    if old:
        identity_keys(old)
    record = dict(old) if old else inspect_folder(folder, media_dir)["metadata"]
    record.update(fields)
    record["folder"] = folder
    record["id"] = old["id"] if old else uuid.uuid4().hex
    record["schema_version"] = 1
    record["aliases"] = list(dict.fromkeys((old or {}).get("aliases", []) +
                                           ([(old or {})["folder"]] if old else []) + [folder]))
    segments = validate(record, media_dir)
    record.setdefault("duration_s", segments[-1]["end"])
    for other in items:
        if other is not old and identity_keys(record) & identity_keys(other):
            raise ValueError("这个媒体目录或旧别名已被导入，请编辑已有视频")
    directory = Path(catalog_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (record["id"] + ".json")
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return record
