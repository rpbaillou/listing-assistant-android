"""apk-v23 tests: category Suggest must call eBay's Taxonomy API with GET.

Regression test for the v1.21 bug: api_ebay_category_suggest called
get_category_suggestions with requests.post and a JSON body, but that
endpoint is GET-only with q as a URL query parameter, so eBay answered
400 invalid_request ("eBay said: Invalid request" in the publish step).
The fix uses requests.get with params={"q": ...}; this test pins that.
"""
import os
import sys
import unittest
from unittest import mock

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PY_DIR = os.path.join(REPO, "app", "src", "main", "python")
sys.path.insert(0, PY_DIR)

import app as m  # noqa: E402  (imports cleanly; __main__ guard prevents serving)


def tearDownModule():
    # test_version_badge.py re-imports `app` with its own APP_VERSION env var;
    # drop our cached copy so its import is fresh and order-independent.
    sys.modules.pop("app", None)


def _fake_resp(payload, status=200):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = payload
    return r


PAYLOAD = {"categorySuggestions": [
    {"category": {"categoryId": "178893", "categoryName": "Figurines"}},
    {"category": {"categoryId": "178889", "categoryName": "Plates"}},
]}


class CategorySuggestMethodTest(unittest.TestCase):
    def test_uses_get_with_q_as_url_parameter(self):
        """The Taxonomy call must be GET with q in params, never POST+JSON."""
        client = m.app.test_client()
        with mock.patch.object(m, "_app_token", return_value="tok"), \
             mock.patch.object(m.requests, "get",
                               return_value=_fake_resp(PAYLOAD)) as get, \
             mock.patch.object(m.requests, "post",
                               side_effect=AssertionError(
                                   "Suggest must not POST to eBay")) as post:
            resp = client.post("/api/ebay/category-suggest",
                               json={"q": "Royal Doulton figurine"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(post.call_count, 0)
        self.assertEqual(get.call_count, 1)
        args, kwargs = get.call_args
        self.assertIn("get_category_suggestions", args[0])
        self.assertEqual(kwargs.get("params"),
                         {"q": "Royal Doulton figurine"})
        self.assertEqual(kwargs.get("headers"),
                         {"Authorization": "Bearer tok"})
        self.assertEqual(kwargs.get("timeout"), 30)
        body = resp.get_json()
        self.assertEqual(body["suggestions"], [
            {"id": "178893", "name": "Figurines"},
            {"id": "178889", "name": "Plates"},
        ])

    def test_truncates_q_to_100_chars(self):
        client = m.app.test_client()
        with mock.patch.object(m, "_app_token", return_value="tok"), \
             mock.patch.object(m.requests, "get",
                               return_value=_fake_resp(PAYLOAD)) as get:
            client.post("/api/ebay/category-suggest",
                        json={"q": "x" * 150})
        self.assertEqual(get.call_args[1]["params"], {"q": "x" * 100})

    def test_caps_suggestions_at_12(self):
        payload = {"categorySuggestions": [
            {"category": {"categoryId": str(i),
                          "categoryName": "Cat %d" % i}}
            for i in range(20)]}
        client = m.app.test_client()
        with mock.patch.object(m, "_app_token", return_value="tok"), \
             mock.patch.object(m.requests, "get",
                               return_value=_fake_resp(payload)):
            resp = client.post("/api/ebay/category-suggest",
                               json={"q": "figurine"})
        self.assertEqual(len(resp.get_json()["suggestions"]), 12)

    def test_empty_q_rejected_without_calling_ebay(self):
        client = m.app.test_client()
        with mock.patch.object(m, "_app_token", return_value="tok"), \
             mock.patch.object(m.requests, "get") as get, \
             mock.patch.object(m.requests, "post") as post:
            resp = client.post("/api/ebay/category-suggest",
                               json={"q": "   "})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(get.call_count, 0)
        self.assertEqual(post.call_count, 0)


if __name__ == "__main__":
    unittest.main()
