"""One spaced-repetition schedule per normalized vocabulary word.

The successful intervals follow SM-2 (1 day, 6 days, then interval × ease).
The three-button interface adds short relearning steps for uncertain answers.
All timestamps are UTC ISO 8601 strings so scheduling is independent of the
computer's display timezone.
"""

import datetime as dt
import json
import math
import os
from pathlib import Path

from word_lookup import normalize_word


BASE_DIR = Path(__file__).resolve().parent
REVIEW_FILE = BASE_DIR / "data" / "review.json"
RATINGS = {"known": 5, "fuzzy": 3, "unknown": 1}


def utc_now():
    return dt.datetime.now(dt.timezone.utc)


def iso(value):
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_due(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(dt.timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def default_state():
    return {"version": 0, "repetitions": 0, "interval_days": 0,
            "ease": 2.5, "due_at": None, "last_review": None, "last_rating": None}


def current_state(raw):
    state = default_state()
    if not isinstance(raw, dict):
        return state
    for name in ("version", "repetitions", "interval_days"):
        value = raw.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            state[name] = value
    ease = raw.get("ease")
    if isinstance(ease, (int, float)) and not isinstance(ease, bool) and math.isfinite(ease):
        state["ease"] = max(1.3, float(ease))
    for name in ("due_at", "last_review"):
        if parse_due(raw.get(name)):
            state[name] = raw[name]
    if raw.get("last_rating") in RATINGS:
        state["last_rating"] = raw["last_rating"]
    return state


def next_state(previous, rating, now=None):
    if rating not in RATINGS:
        raise ValueError("invalid rating")
    now = now or utc_now()
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    previous = current_state(previous)
    quality = RATINGS[rating]
    ease = max(1.3, round(previous["ease"] + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02), 2))

    if rating == "known":
        repetitions = previous["repetitions"] + 1
        interval = 1 if repetitions == 1 else 6 if repetitions == 2 else math.ceil(previous["interval_days"] * previous["ease"])
        due = now + dt.timedelta(days=interval)
    elif rating == "fuzzy":
        repetitions = 0
        interval = 1
        due = now + dt.timedelta(days=1)
    else:
        repetitions = 0
        interval = 0
        due = now + dt.timedelta(minutes=10)

    return {"version": previous["version"] + 1, "repetitions": repetitions,
            "interval_days": interval, "ease": ease, "due_at": iso(due),
            "last_review": iso(now), "last_rating": rating}


def load_states(path=REVIEW_FILE):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    words = data.get("words") if isinstance(data, dict) else None
    return words if isinstance(words, dict) else {}


def save_states(states, path=REVIEW_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps({"words": states}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def review_queue(entries, states, now=None):
    now = now or utc_now()
    # A word is one learning unit. Use its most recently saved sentence as context.
    newest = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        word = normalize_word(entry.get("word"))
        if not word:
            continue
        if word not in newest or (entry.get("created") or "") > (newest[word].get("created") or ""):
            newest[word] = entry

    due, upcoming = [], []
    due_today = 0
    today = now.astimezone().date()
    for word, entry in newest.items():
        state = current_state(states.get(word))
        due_at = parse_due(state["due_at"])
        card = {"word": word, "entry": entry, "review": state}
        if due_at is None or due_at.astimezone().date() <= today:
            due_today += 1
        if due_at is None or due_at <= now:
            due.append(card)
        else:
            upcoming.append(due_at)
    due.sort(key=lambda card: (card["review"]["due_at"] or "", card["entry"].get("created") or "", card["word"]))
    return {"cards": due, "stats": {"due": len(due), "due_today": due_today,
            "total": len(newest),
            "reviewed": sum(current_state(states.get(word))["version"] > 0 for word in newest),
            "next_due_at": iso(min(upcoming)) if upcoming else None}}
