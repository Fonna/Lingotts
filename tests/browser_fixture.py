"""Isolated browser fixture: copied metadata and hard-linked, read-only media."""
import http.server
import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ted_server
import video_catalog as vc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--without-transcript', action='store_true')
    args = parser.parse_args()
    output = ted_server.BASE_DIR / "output" / "playwright"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="catalog-browser-", dir=output) as temporary:
        root = Path(temporary).resolve()
        assert root.is_relative_to(output.resolve())
        media = root / "media"
        media.mkdir()
        directory = root / "catalog"
        shutil.copytree(vc.CATALOG_DIR, directory)
        for video in vc.catalog()["videos"]:
            folder = media / video["folder"]
            folder.mkdir()
            os.link(vc.MEDIA_DIR / video["folder"] / video["media_file"], folder / video["media_file"])
            if video['transcript_status'] == 'ready':
                shutil.copyfile(vc.MEDIA_DIR / video["folder"] / video["transcript_file"], folder / video["transcript_file"])
        eighth = media / "Import demo #8 你好"
        eighth.mkdir()
        with wave.open(str(eighth / "demo #8.wav"), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\x00\x00" * 16000 * 6)
        if not args.without_transcript:
            (eighth / "transcript.txt").write_text("Title: Browser import demo | Demo speaker | TED\n"
                "[0.0s -> 2.0s] Hello from the eighth video.\n[2.0s -> 5.0s] This is a reusable player.\n", encoding="utf-8")
        ted_server.MEDIA_DIR = media
        ted_server.CATALOG_DIR = directory
        ted_server.VOCAB_FILE = root / "vocab.json"
        shutil.copyfile(ted_server.BASE_DIR / "data" / "vocab.json", ted_server.VOCAB_FILE)
        ted_server.REVIEW_FILE = root / "review.json"
        shutil.copyfile(ted_server.BASE_DIR / "data" / "review.json", ted_server.REVIEW_FILE)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 8767), ted_server.Handler)
        server.daemon_threads = True
        def stop_from_stdin():
            for line in sys.stdin:
                if line.strip() == 'quit':
                    server.shutdown()
                    break
        threading.Thread(target=stop_from_stdin, daemon=True).start()
        print("CATALOG_BROWSER_READY:8767", flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
