import threading
import unittest
import urllib.error
import urllib.request

import ted_server


class StaticCacheTests(unittest.TestCase):
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

    def test_pages_and_shared_assets_revalidate_on_navigation(self):
        for path in ['/', '/review.html', '/review.html?v=current', '/review%2ehtml', '/assets/pronunciation.js', '/assets/common.css']:
            with self.subTest(path=path), urllib.request.urlopen(self.base + path) as response:
                self.assertEqual(response.headers.get_all('Cache-Control'), ['no-cache'])
                if path == '/review.html':
                    page = response.read().decode('utf-8')
                    self.assertIn('reviewPronunciation', page)
                    self.assertIn('assets/pronunciation.js', page)

    def test_conditional_response_keeps_cache_policy_and_api_keeps_no_store(self):
        with urllib.request.urlopen(self.base + '/review.html') as response:
            modified = response.headers['Last-Modified']
        request = urllib.request.Request(self.base + '/review.html', headers={'If-Modified-Since': modified})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        self.assertEqual(error.exception.code, 304)
        self.assertEqual(error.exception.headers.get_all('Cache-Control'), ['no-cache'])
        error.exception.close()
        with urllib.request.urlopen(self.base + '/api/review') as response:
            self.assertEqual(response.headers.get_all('Cache-Control'), ['no-store'])


if __name__ == '__main__':
    unittest.main()
