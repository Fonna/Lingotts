"""Build a small offline ECDICT SQLite database for TedLib.

Usage: python scripts/build_dictionary.py [--archive path-to-ecdict-npm.tgz]
Only common entries and words already present in local TED transcripts are kept.
"""

import argparse
import csv
import hashlib
import io
import re
import shutil
import sqlite3
import tarfile
import tempfile
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://registry.npmjs.org/ecdict/-/ecdict-0.0.4.tgz"
SOURCE_SHA1 = "aadfa6e46ed9c586144a3f749d585111168ec5f2"
OUTPUT = ROOT / "data" / "ecdict.sqlite"
TRANSCRIPTS = ROOT.parent / "TED"
RANK_LIMIT = 60_000
WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def transcript_words():
    words = set()
    for path in TRANSCRIPTS.glob("*/transcript.txt"):
        words.update(w.lower() for w in WORD_RE.findall(path.read_text(encoding="utf-8-sig")))
    return words


def ranked(row):
    for field in ("frq", "bnc"):
        value = row.get(field, "").strip()
        if value.isdigit() and 0 < int(value) <= RANK_LIMIT:
            return True
    return False


def build(archive=None):
    wanted = transcript_words()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".sqlite.tmp")
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    try:
        connection.execute("CREATE TABLE words (word TEXT PRIMARY KEY, phonetic TEXT, "
                           "definition TEXT, translation TEXT, pos TEXT)")
        with tempfile.TemporaryDirectory() as tmpdir:
            if archive is None:
                archive = Path(tmpdir) / "ecdict.tgz"
                request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "TedLib dictionary builder"})
                with urllib.request.urlopen(request, timeout=120) as response, archive.open("wb") as target:
                    shutil.copyfileobj(response, target)
            archive = Path(archive)
            digest = hashlib.sha1(archive.read_bytes()).hexdigest()
            if digest != SOURCE_SHA1:
                raise ValueError(f"ECDICT archive hash mismatch: {digest}")
            with tarfile.open(archive, "r:gz") as bundle:
                source = bundle.extractfile("package/assets/ecdict.csv")
                reader = csv.DictReader(io.TextIOWrapper(source, encoding="utf-8-sig", newline=""))
                batch = []
                for row in reader:
                    word = row["word"].strip().lower()
                    if not word or not WORD_RE.fullmatch(word):
                        continue
                    if not (word in wanted or ranked(row)):
                        continue
                    if not (row.get("translation") or row.get("definition")):
                        continue
                    batch.append((word, row.get("phonetic", ""), row.get("definition", ""),
                                  row.get("translation", ""), row.get("pos", "")))
                    if len(batch) >= 1000:
                        connection.executemany("INSERT OR REPLACE INTO words VALUES (?,?,?,?,?)", batch)
                        batch.clear()
                if batch:
                    connection.executemany("INSERT OR REPLACE INTO words VALUES (?,?,?,?,?)", batch)
        connection.commit()
        count = connection.execute("SELECT COUNT(*) FROM words").fetchone()[0]
        connection.execute("VACUUM")
    except BaseException:
        connection.close()
        temporary.unlink(missing_ok=True)
        raise
    connection.close()
    temporary.replace(OUTPUT)
    print(f"Built {OUTPUT}: {count} words, {OUTPUT.stat().st_size:,} bytes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path)
    build(parser.parse_args().archive)
