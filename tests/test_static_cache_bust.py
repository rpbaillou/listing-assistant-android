"""apk-v59 tests: the app shell never serves stale after an update.

v59: Flask's static route answers with a 12h max-age, and the WebView
does not clear its HTTP cache when the app updates. After the v58
update the badge said 1.57 (version.json is fetched with a cache
buster) while the editor still ran the cached v56 app.js, which has
the closeEditor leak — so the tilt "came back" on the next photo even
though the fix had shipped. The page ("/") and /static/* now send
no-cache (revalidate on every load), so an update always reaches the
WebView. Same class of bug as the /photos/ no-cache fix in v54.
An unchanged file still answers 304, so this costs nothing on a
local server.
"""
import os
import sys
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PY_DIR = os.path.join(REPO, "app", "src", "main", "python")
sys.path.insert(0, PY_DIR)

import app as m  # noqa: E402  (imports cleanly; __main__ guard prevents serving)


def tearDownModule():
    sys.modules.pop("app", None)


class StaticCacheBustTestCase(unittest.TestCase):
    def setUp(self):
        self.client = m.app.test_client()

    def test_static_app_js_sends_no_cache(self):
        r = self.client.get("/static/app.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("no-cache", r.headers.get("Cache-Control", ""))
        self.assertIn("max-age=0", r.headers.get("Cache-Control", ""))
        self.assertTrue(r.headers.get("ETag"))

    def test_static_style_css_sends_no_cache(self):
        r = self.client.get("/static/style.css")
        self.assertEqual(r.status_code, 200)
        self.assertIn("no-cache", r.headers.get("Cache-Control", ""))

    def test_index_page_sends_no_cache(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("no-cache", r.headers.get("Cache-Control", ""))

    def test_api_responses_are_not_forced_no_cache(self):
        # The after_request hook is scoped to the shell only; API
        # responses keep Flask's default (no Cache-Control header),
        # so this change cannot slow down or alter API traffic.
        r = self.client.get("/api/config")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("no-cache", r.headers.get("Cache-Control", ""))


if __name__ == "__main__":
    unittest.main()
