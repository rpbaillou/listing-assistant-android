"""apk-v8 tests: item specifics must be validated and cleaned before publish.

Covers the audit's P0 gaps: (1) required aspects are enforced per category
via the Taxonomy API, (2) "Unknown"/empty aspect values are never sent to
eBay, (3) the photo cap is 24.
"""
import os
import sys
import types
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


class CleanAspectsTest(unittest.TestCase):
    def test_drops_unknown_case_insensitive(self):
        out = m._clean_aspects({"Brand": "Unknown", "MPN": "UNKNOWN",
                                "Color": "unknown ", "Type": "Figurine"})
        self.assertEqual(out, {"Type": ["Figurine"]})

    def test_drops_empty_values_and_keys(self):
        out = m._clean_aspects({"Brand": "", "": "x", "  ": "y",
                                "Material": "  ", "Year": "1990"})
        self.assertEqual(out, {"Year": ["1990"]})

    def test_keeps_good_values_and_truncates(self):
        out = m._clean_aspects({"Brand": "Royal Doulton",
                                "K" * 50: "V" * 600})
        self.assertEqual(out, {"Brand": ["Royal Doulton"],
                               "K" * 40: ["V" * 500]})

    def test_none_and_empty(self):
        self.assertEqual(m._clean_aspects(None), {})
        self.assertEqual(m._clean_aspects({}), {})


class MissingRequiredAspectsTest(unittest.TestCase):
    def test_all_filled_passes(self):
        attrs = {"Brand": "Royal Doulton", "MPN": "HN1315"}
        req = [{"name": "Brand"}, {"name": "MPN"}]
        self.assertEqual(m._missing_required_aspects(attrs, req), [])

    def test_missing_and_unknown_reported(self):
        attrs = {"Brand": "Unknown", "Color": "Red"}
        req = [{"name": "Brand"}, {"name": "MPN"}]
        self.assertEqual(m._missing_required_aspects(attrs, req),
                         ["Brand", "MPN"])

    def test_empty_required_list(self):
        self.assertEqual(m._missing_required_aspects({"a": "b"}, []), [])
        self.assertEqual(m._missing_required_aspects({"a": "b"}, None), [])


class CategoryAspectsParsingTest(unittest.TestCase):
    def _fake_resp(self, payload, status=200):
        r = mock.Mock()
        r.status_code = status
        r.json.return_value = payload
        return r

    def test_parses_required_and_recommended(self):
        payload = {"aspects": [
            {"localizedAspectName": "Brand",
             "aspectConstraint": {"aspectRequired": True},
             "aspectValues": [{"localizedValue": "Royal Doulton"}]},
            {"localizedAspectName": "Color",
             "aspectConstraint": {"aspectRequired": False},
             "aspectValues": [{"localizedValue": "Multicolor"}]},
            {"localizedAspectName": "",  # skipped: no name
             "aspectConstraint": {"aspectRequired": True}},
        ]}
        with mock.patch.object(m, "_app_token", return_value="tok"), \
             mock.patch.object(m.requests, "get",
                               return_value=self._fake_resp(payload)):
            m._aspects_cache.clear()
            required, recommended = m._category_aspects("178893")
        self.assertEqual([a["name"] for a in required], ["Brand"])
        self.assertEqual(required[0]["values"], ["Royal Doulton"])
        self.assertEqual([a["name"] for a in recommended], ["Color"])

    def test_api_failure_returns_none_none(self):
        with mock.patch.object(m, "_app_token", return_value="tok"), \
             mock.patch.object(m.requests, "get",
                               return_value=self._fake_resp({}, status=500)):
            m._aspects_cache.clear()
            self.assertEqual(m._category_aspects("178893"), (None, None))

    def test_request_exception_returns_none_none(self):
        import requests as rq
        with mock.patch.object(m, "_app_token", return_value="tok"), \
             mock.patch.object(m.requests, "get",
                               side_effect=rq.RequestException("down")):
            m._aspects_cache.clear()
            self.assertEqual(m._category_aspects("178893"), (None, None))

    def test_result_is_cached(self):
        payload = {"aspects": []}
        with mock.patch.object(m, "_app_token", return_value="tok") as tok, \
             mock.patch.object(m.requests, "get",
                               return_value=self._fake_resp(payload)) as get:
            m._aspects_cache.clear()
            m._category_aspects("178893")
            m._category_aspects("178893")
            self.assertEqual(get.call_count, 1)
            self.assertEqual(tok.call_count, 1)


class PhotoCapTest(unittest.TestCase):
    def test_max_photos_is_24(self):
        self.assertEqual(m.MAX_PHOTOS, 24)


if __name__ == "__main__":
    unittest.main()
