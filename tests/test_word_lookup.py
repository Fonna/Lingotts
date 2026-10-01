import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from contextlib import closing
from unittest.mock import patch

import ted_server
import word_lookup


class WordLookupTests(unittest.TestCase):
    def test_offline_lookup_and_input_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "words.sqlite"
            with closing(sqlite3.connect(database)) as db:
                db.execute("CREATE TABLE words (word TEXT PRIMARY KEY, phonetic TEXT, definition TEXT, translation TEXT, pos TEXT)")
                db.execute("INSERT INTO words VALUES (?,?,?,?,?)", ("hello", "həˈləʊ", "greeting", "你好", "int."))
                db.commit()
            self.assertEqual(word_lookup.lookup("HELLO", database)["zh"], "你好")
            self.assertIsNone(word_lookup.lookup("unknown", database))
            for invalid in ("", "a; DROP TABLE words", "x" * 81, None):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    word_lookup.lookup(invalid, database)

    def test_analysis_is_contextual_and_cached(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, _limit):
                return json.dumps({"choices": [{"message": {"content": "```json\n" +
                    json.dumps({"zh": "偶然发现", "en": "lucky discovery", "root": "", "examples": ["example"]}) +
                    "\n```"}}]}).encode("utf-8")

        calls = []

        def opener(request, timeout):
            calls.append(json.loads(request.data))
            self.assertEqual(timeout, 45)
            return Response()

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"ARK_API_KEY": "test-key"}):
            cache_file = Path(directory) / "cache.json"
            result, cached = word_lookup.analyze("Serendipity", "A chance discovery.", cache_file, opener)
            self.assertFalse(cached)
            self.assertEqual(result["zh"], "偶然发现")
            again, cached = word_lookup.analyze("serendipity", "A chance discovery.", cache_file, opener)
            self.assertTrue(cached)
            self.assertEqual(again, result)
            self.assertEqual(len(calls), 1)
            self.assertIn("A chance discovery.", calls[0]["messages"][0]["content"])
            with patch.dict(os.environ, {}, clear=True):
                offline, cached = word_lookup.analyze('SERENDIPITY', '  A chance discovery.  ', cache_file, opener)
                self.assertTrue(cached); self.assertEqual(offline,result)
                self.assertEqual(word_lookup.cached_analysis('serendipity','A chance discovery.',cache_file),result)
                self.assertIsNone(word_lookup.cached_analysis('serendipity','A different sentence.',cache_file))
                self.assertEqual(len(calls),1)

    def test_missing_key_and_invalid_response_do_not_cache(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            cache_file = Path(directory) / "cache.json"
            with self.assertRaises(word_lookup.AnalysisError) as error:
                word_lookup.analyze("word", "sentence", cache_file)
            self.assertEqual(error.exception.status, 503)
            self.assertFalse(cache_file.exists())
        with self.assertRaises(word_lookup.AnalysisError):
            word_lookup.parse_analysis("```json\nnot json\n```")
        with self.assertRaises(word_lookup.AnalysisError):
            word_lookup.parse_analysis(None)

    def test_rate_limit_has_actionable_error(self):
        def limited(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {}, None)

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"ARK_API_KEY": "test-key"}):
            with self.assertRaises(word_lookup.AnalysisError) as error:
                word_lookup.analyze("word", "sentence", Path(directory) / "cache.json", limited)
            self.assertEqual(error.exception.status, 503)
            self.assertIn("过于频繁", str(error.exception))


class DictionaryApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ted_server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), ted_server.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_lookup_statuses(self):
        with urllib.request.urlopen(self.base + "/api/dictionary?word=serendipity") as response:
            body = json.load(response)
        self.assertEqual(body["entry"]["word"], "serendipity")
        for path, status in (("?word=", 400), ("?word=zzznotawordzzz", 404)):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(self.base + "/api/dictionary" + path)
            self.assertEqual(error.exception.code, status)
            error.exception.close()

    def test_analysis_route_and_validation(self):
        request = urllib.request.Request(self.base + "/api/word-analysis",
                                         data=json.dumps({"word": "test", "sentence": "A test."}).encode(),
                                         headers={"Content-Type": "application/json"})
        with patch.object(ted_server, "analyze", return_value=({"zh": "测试"}, True)) as mocked:
            with urllib.request.urlopen(request) as response:
                self.assertEqual(json.load(response), {"analysis": {"zh": "测试"}, "cached": True})
            mocked.assert_called_once_with("test", "A test.")
        invalid = urllib.request.Request(self.base + "/api/word-analysis", data=b"invalid",
                                         headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(invalid)
        self.assertEqual(error.exception.code, 400)
        error.exception.close()

    def test_reading_saved_analysis_never_generates_a_new_analysis(self):
        with patch.object(ted_server,'cached_analysis',return_value={'zh':'saved'}) as cached, \
                patch.object(ted_server,'analyze') as paid:
            with urllib.request.urlopen(self.base+'/api/word-analysis?word=hello&sentence=Hello.') as response:
                self.assertEqual(json.load(response),{'analysis':{'zh':'saved'}})
            cached.assert_called_once_with('hello','Hello.')
            paid.assert_not_called()

    def test_vocab_rejects_null_slug(self):
        invalid = urllib.request.Request(
            self.base + "/api/vocab",
            data=json.dumps({"word": "negative", "slug": None, "sentence": "Example", "t": 1}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(invalid)
        self.assertEqual(error.exception.code, 400)
        error.exception.close()

    def test_paid_endpoint_rejects_cross_origin_and_plain_text(self):
        for headers, status in (
            ({"Content-Type": "text/plain"}, 415),
            ({"Content-Type": "application/json", "Origin": "https://example.org"}, 403),
        ):
            with self.subTest(headers=headers):
                request = urllib.request.Request(self.base + "/api/word-analysis", data=b"{}", headers=headers)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request)
                self.assertEqual(error.exception.code, status)
                error.exception.close()


if __name__ == "__main__":
    unittest.main()
