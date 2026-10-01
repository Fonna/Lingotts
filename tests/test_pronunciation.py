import base64
import io
import json
import os
import subprocess
import threading
import unittest
import urllib.error
import urllib.request
import wave
from unittest.mock import patch

import pronunciation as p
import ted_server


def sample_wave():
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b'\0\0' * 1600)
    return stream.getvalue()


class PronunciationTests(unittest.TestCase):
    def tearDown(self):
        p._synthesize.cache_clear()

    def test_invalid_words_never_start_native_process(self):
        with patch.object(p.subprocess, 'run') as run:
            for word in [None, '', 'hello world', 'a' * 81, '$(whoami)', '<script>', '中文']:
                with self.subTest(word=word), self.assertRaises(ValueError):
                    p.speech_audio(word)
            run.assert_not_called()

    def test_native_command_is_static_and_audio_is_cached_by_normalized_word(self):
        audio = sample_wave()
        with patch.object(p.os, 'name', 'nt'), patch.object(p.subprocess, 'run',
                return_value=subprocess.CompletedProcess([], 0, base64.b64encode(audio), b'')) as run:
            self.assertEqual(p.speech_audio(' It’s '), audio)
            self.assertEqual(p.speech_audio("it's"), audio)
            run.assert_called_once()
            args, kwargs = run.call_args
            self.assertEqual(args[0][-1], p._SCRIPT)
            self.assertEqual(kwargs['env']['TEDLIB_PRONUNCIATION_WORD'], "it's")
            self.assertNotIn("it's", args[0][-1])
            self.assertEqual(kwargs['timeout'], 10)
            self.assertEqual(p._synthesize.cache_info().maxsize, 128)

    def test_failures_are_actionable_and_not_cached(self):
        with patch.object(p.os, 'name', 'nt'), patch.object(p.subprocess, 'run',
                side_effect=subprocess.TimeoutExpired('powershell', 10)) as run:
            for _ in range(2):
                with self.assertRaisesRegex(p.PronunciationError, '英语语音包'):
                    p.speech_audio('word')
            self.assertEqual(run.call_count, 2)

    def test_invalid_audio_is_rejected(self):
        for output in [b'not base64', base64.b64encode(b'wrong audio' * 20)]:
            with self.subTest(output=output), patch.object(p.os, 'name', 'nt'), \
                    patch.object(p.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output, b'')), \
                    self.assertRaises(p.PronunciationError):
                p.speech_audio('word')

    def test_parallel_work_is_bounded_and_unsupported_platform_is_explained(self):
        with patch.object(p.os, 'name', 'posix'), self.assertRaisesRegex(p.PronunciationError, 'Windows'):
            p.speech_audio('word')
        p._LOCK.acquire()
        try:
            with self.assertRaisesRegex(p.PronunciationError, '稍后重试'):
                p.speech_audio('word')
        finally:
            p._LOCK.release()

    @unittest.skipUnless(os.name == 'nt', 'Windows native speech integration')
    def test_installed_english_voice_generates_non_silent_audio(self):
        data = p.speech_audio('comfortable')
        with wave.open(io.BytesIO(data)) as audio:
            self.assertGreater(audio.getnframes() / audio.getframerate(), 0.3)
            self.assertTrue(any(audio.readframes(audio.getnframes())))


class PronunciationApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ted_server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), ted_server.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_wave_response_and_error_statuses(self):
        with patch.object(ted_server, 'speech_audio', return_value=sample_wave()):
            with urllib.request.urlopen(self.base + '/api/pronunciation?word=comfy') as response:
                self.assertEqual(response.headers['Content-Type'], 'audio/wav')
                self.assertEqual(response.read(), sample_wave())
        for exception, status in [(ValueError(), 400), (p.PronunciationError('英语语音不可用'), 503)]:
            with self.subTest(status=status), patch.object(ted_server, 'speech_audio', side_effect=exception):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(self.base + '/api/pronunciation?word=comfy')
                self.assertEqual(error.exception.code, status)
                self.assertIn('error', json.load(error.exception))
                error.exception.close()

    def test_foreign_origins_are_rejected_before_synthesis(self):
        with patch.object(ted_server, 'speech_audio') as speech:
            for headers in [{'Origin':'https://example.org'}, {'Referer':'http://127.0.0.1:9999/'},
                            {'Sec-Fetch-Site':'same-site'}, {'Sec-Fetch-Site':'cross-site'}]:
                request = urllib.request.Request(self.base + '/api/pronunciation?word=comfy', headers=headers)
                with self.subTest(headers=headers), self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request)
                self.assertEqual(error.exception.code, 403)
                error.exception.close()
            speech.assert_not_called()


if __name__ == '__main__':
    unittest.main()
