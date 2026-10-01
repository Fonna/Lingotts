import datetime as dt
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import review_schedule as review
import ted_server
import word_lookup


NOW = dt.datetime(2026, 10, 1, 8, 0, tzinfo=dt.timezone.utc)


class ScheduleTests(unittest.TestCase):
    def test_imported_large_schedule_remains_gradeable_without_date_overflow(self):
        old = dict(review.default_state(), repetitions=5, interval_days=100000000, ease=100)
        state = review.next_state(old, 'known', NOW)
        self.assertLessEqual(state['ease'],100)
        self.assertIsNotNone(review.parse_due(state['due_at']))
        self.assertLess(review.parse_due(state['due_at']).year,2200)

    def test_known_intervals_and_ease_progression(self):
        first = review.next_state(None, "known", NOW)
        self.assertEqual(first["interval_days"], 1)
        self.assertEqual(first["due_at"], "2026-10-02T08:00:00Z")
        self.assertEqual(first["ease"], 2.6)

        second = review.next_state(first, "known", NOW + dt.timedelta(days=1))
        self.assertEqual(second["interval_days"], 6)
        third = review.next_state(second, "known", NOW + dt.timedelta(days=7))
        self.assertEqual(third["interval_days"], 17)
        self.assertEqual(third["version"], 3)

    def test_fuzzy_and_unknown_relearn(self):
        learned = review.next_state(review.next_state(None, "known", NOW), "known", NOW)
        fuzzy = review.next_state(learned, "fuzzy", NOW)
        self.assertEqual(fuzzy["repetitions"], 0)
        self.assertEqual(fuzzy["interval_days"], 1)
        self.assertEqual(fuzzy["due_at"], "2026-10-02T08:00:00Z")
        self.assertLess(fuzzy["ease"], learned["ease"])

        unknown = review.next_state(learned, "unknown", NOW)
        self.assertEqual(unknown["repetitions"], 0)
        self.assertEqual(unknown["due_at"], "2026-10-01T08:10:00Z")
        self.assertGreaterEqual(unknown["ease"], 1.3)

    def test_queue_is_per_word_and_uses_latest_context(self):
        entries = [
            {"word": "Negative", "sentence": "old context", "created": "2026-09-01T10:00:00"},
            {"word": "negative", "sentence": "new context", "created": "2026-10-01T10:00:00"},
            {"word": "fahrenheit", "sentence": "temperature", "created": "2026-09-02T10:00:00"},
        ]
        queue = review.review_queue(entries, {}, NOW)
        self.assertEqual(queue["stats"]["total"], 2)
        self.assertEqual(queue["stats"]["due"], 2)
        negative = next(card for card in queue["cards"] if card["word"] == "negative")
        self.assertEqual(negative["entry"]["sentence"], "new context")
        self.assertEqual(negative["review"]["version"], 0)

        states = {"negative": review.next_state(None, "known", NOW)}
        later = review.review_queue(entries, states, NOW)
        self.assertEqual(later["stats"]["due"], 1)
        self.assertEqual(later["stats"]["reviewed"], 1)
        self.assertEqual(later["stats"]["next_due_at"], "2026-10-02T08:00:00Z")

    def test_invalid_rating_and_bad_state(self):
        with self.assertRaises(ValueError):
            review.next_state(None, "perfect", NOW)
        self.assertEqual(review.current_state({"ease": -1, "version": True})["version"], 0)
        self.assertIsNone(review.parse_due("garbage"))

    def test_due_today_includes_later_today_but_queue_waits_until_due(self):
        entries = [{"word": "negative", "sentence": "That's negative.", "created": "2026-10-01"}]
        states = {"negative": review.next_state(None, "unknown", NOW)}
        queue = review.review_queue(entries, states, NOW)
        self.assertEqual(queue["stats"]["due_today"], 1)
        self.assertEqual(queue["stats"]["due"], 0)
        self.assertEqual(queue["stats"]["next_due_at"], "2026-10-01T08:10:00Z")
        self.assertEqual(review.review_queue(entries, states, NOW + dt.timedelta(minutes=10))["stats"]["due"], 1)

    def test_cached_analysis_uses_normalized_word_and_sentence(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_file = Path(directory) / "analysis.json"
            key = word_lookup.cache_key("negative", "That's negative.")
            word_lookup.save_cache({key: {"zh": "否定的"}}, cache_file)
            self.assertEqual(
                word_lookup.cached_analysis("NEGATIVE", " That's negative. ", cache_file),
                {"zh": "否定的"},
            )
            self.assertIsNone(word_lookup.cached_analysis("negative", "Another sentence.", cache_file))


class ReviewApiTests(unittest.TestCase):
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

    def request(self, data):
        return urllib.request.Request(
            self.base + "/api/review",
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    def test_review_persists_and_rejects_stale_grade(self):
        with tempfile.TemporaryDirectory() as directory:
            vocab_file = Path(directory) / "vocab.json"
            review_file = Path(directory) / "review.json"
            vocab_file.write_text(json.dumps({"entries": [
                {"id": "a", "word": "Negative", "sentence": "first", "created": "2026-09-01"},
                {"id": "b", "word": "negative", "sentence": "second", "created": "2026-09-02"},
            ]}), encoding="utf-8")
            with patch.object(ted_server, "VOCAB_FILE", vocab_file), patch.object(ted_server, "REVIEW_FILE", review_file):
                with urllib.request.urlopen(self.base + "/api/review") as response:
                    initial = json.load(response)
                self.assertEqual(initial["stats"]["due"], 1)
                self.assertEqual(initial["cards"][0]["entry"]["sentence"], "second")

                with urllib.request.urlopen(self.request({"word": "NEGATIVE", "rating": "known", "version": 0})) as response:
                    result = json.load(response)
                self.assertEqual(result["review"]["version"], 1)
                self.assertEqual(result["stats"]["due"], 0)
                self.assertEqual(json.loads(review_file.read_text(encoding="utf-8"))["words"]["negative"]["version"], 1)

                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(self.request({"word": "negative", "rating": "known", "version": 0}))
                self.assertEqual(error.exception.code, 409)
                error.exception.close()

                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(self.request({"word": "absent", "rating": "known", "version": 0}))
                self.assertEqual(error.exception.code, 404)
                error.exception.close()

    def test_rejects_invalid_grade_and_cross_origin(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.request({"word": "negative", "rating": "known", "version": True}))
        self.assertEqual(error.exception.code, 400)
        error.exception.close()

        request = self.request({"word": "negative", "rating": "known", "version": 0})
        request.add_header("Origin", "https://example.org")
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        self.assertEqual(error.exception.code, 403)
        error.exception.close()


if __name__ == "__main__":
    unittest.main()
