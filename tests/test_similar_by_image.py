"""apk-v51 tests: similar items by image (eBay Browse search_by_image).

The listing page's "Check sold prices" opens one results screen with
two halves. The sold half is the title-based eBay sold search, exactly
as before (no public sold-data API exists). The image half sends the
draft's first photo to eBay's Browse API
POST /buy/browse/v1/item_summary/search_by_image with the app token
(client credentials, no user consent) and shows the active listings
that look like it, each with its own "Use this price".

Rules pinned here:
- the request is a POST to search_by_image with the first photo as
  base64, limit 10, and category_ids only when the draft has a
  numeric category number;
- only rows with a title and a numeric USD price are kept (the price
  is what "Use this price" sets);
- the image half NEVER errors hard: eBay failures, token failures,
  missing photos and empty results all come back as 200 with an
  empty list and a plain-words note, so the sold half keeps working;
- a repeat lookup for the same photo reuses the cached result
  instead of calling eBay again.

eBay is always mocked; no test here reaches the network.
"""
import base64
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PY_DIR = os.path.join(REPO, "app", "src", "main", "python")
sys.path.insert(0, PY_DIR)

import app as m  # noqa: E402  (imports cleanly; __main__ guard prevents serving)


def tearDownModule():
    sys.modules.pop("app", None)


def _jpeg_bytes(color="red"):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (50, 40), color).save(buf, "JPEG")
    buf.seek(0)
    return buf.read()


def _fake_resp(payload, status=200):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = payload
    r.text = json.dumps(payload)
    return r


PAYLOAD = {"itemSummaries": [
    {"itemId": "v1|1|0", "title": "Royal Doulton figurine",
     "price": {"value": "24.50", "currency": "USD"},
     "image": {"imageUrl": "https://example.com/a.jpg"},
     "itemWebUrl": "https://www.ebay.com/itm/1"},
    {"itemId": "v1|2|0", "title": "No price here",
     "image": {"imageUrl": "https://example.com/b.jpg"},
     "itemWebUrl": "https://www.ebay.com/itm/2"},
    {"itemId": "v1|3|0", "title": "Priced in pounds",
     "price": {"value": "19.00", "currency": "GBP"},
     "image": {"imageUrl": "https://example.com/c.jpg"},
     "itemWebUrl": "https://www.ebay.com/itm/3"},
    {"itemId": "v1|4|0", "title": "Free, apparently",
     "price": {"value": "0", "currency": "USD"},
     "image": {"imageUrl": "https://example.com/d.jpg"},
     "itemWebUrl": "https://www.ebay.com/itm/4"},
]}


class SimilarByImageTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig = {}
        for attr, sub in (("DRAFTS_DIR", "drafts"), ("PHOTOS_DIR", "photos")):
            self.orig[attr] = getattr(m, attr)
            p = os.path.join(self.tmp, sub)
            os.makedirs(p, exist_ok=True)
            setattr(m, attr, p)
        m._similar_cache.clear()
        self.client = m.app.test_client()

    def tearDown(self):
        for attr, val in self.orig.items():
            setattr(m, attr, val)
        m._similar_cache.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_draft(self, draft_id, photos, **fields):
        d = {"id": draft_id, "title": "Dummy " + draft_id,
             "photos": photos, "price": ""}
        d.update(fields)
        with open(os.path.join(m.DRAFTS_DIR, draft_id + ".json"),
                  "w", encoding="utf-8") as f:
            json.dump(d, f)
        return d

    def _write_photo(self, rel, color="red"):
        full = os.path.join(m.PHOTOS_DIR, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        data = _jpeg_bytes(color)
        with open(full, "wb") as f:
            f.write(data)
        return data

    def test_request_shape_with_category(self):
        """POST search_by_image, base64 first photo, limit + category."""
        data = self._write_photo("d1/1.jpg")
        self._write_draft("d1", ["d1/1.jpg"], category_id="178893")
        with mock.patch.object(m, "_app_token", return_value="tok"), \
             mock.patch.object(m.requests, "post",
                               return_value=_fake_resp(PAYLOAD)) as post:
            resp = self.client.get("/api/drafts/d1/similar")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(post.call_count, 1)
        args, kwargs = post.call_args
        self.assertTrue(args[0].endswith(
            "/buy/browse/v1/item_summary/search_by_image"))
        self.assertEqual(kwargs.get("params"),
                         {"limit": 10, "category_ids": "178893"})
        self.assertEqual(kwargs.get("headers"),
                         {"Authorization": "Bearer tok",
                          "Content-Type": "application/json"})
        self.assertEqual(kwargs.get("json", {}).get("image"),
                         base64.b64encode(data).decode())

    def test_no_category_param_without_category(self):
        self._write_photo("d1/1.jpg")
        self._write_draft("d1", ["d1/1.jpg"])
        with mock.patch.object(m, "_app_token", return_value="tok"), \
             mock.patch.object(m.requests, "post",
                               return_value=_fake_resp(PAYLOAD)) as post:
            resp = self.client.get("/api/drafts/d1/similar")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(post.call_args[1].get("params"), {"limit": 10})

    def test_parsing_keeps_only_priced_usd_rows(self):
        """Rows without a price, a zero price, or a non-USD price drop."""
        self._write_photo("d1/1.jpg")
        self._write_draft("d1", ["d1/1.jpg"])
        with mock.patch.object(m, "_app_token", return_value="tok"), \
             mock.patch.object(m.requests, "post",
                               return_value=_fake_resp(PAYLOAD)):
            resp = self.client.get("/api/drafts/d1/similar")
        body = resp.get_json()
        self.assertEqual(body["note"], "")
        self.assertEqual(len(body["matches"]), 1)
        row = body["matches"][0]
        self.assertEqual(row["title"], "Royal Doulton figurine")
        self.assertEqual(row["price"], "24.50")
        self.assertEqual(row["thumb"], "https://example.com/a.jpg")
        self.assertEqual(row["url"], "https://www.ebay.com/itm/1")

    def test_empty_results_get_a_plain_note(self):
        self._write_photo("d1/1.jpg")
        self._write_draft("d1", ["d1/1.jpg"])
        with mock.patch.object(m, "_app_token", return_value="tok"), \
             mock.patch.object(m.requests, "post",
                               return_value=_fake_resp({"itemSummaries": []})):
            resp = self.client.get("/api/drafts/d1/similar")
        body = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(body["matches"], [])
        self.assertIn("no similar items", body["note"])

    def test_ebay_error_never_blocks_the_sold_half(self):
        """A 500 from eBay is a 200 with a note, never a 500."""
        self._write_photo("d1/1.jpg")
        self._write_draft("d1", ["d1/1.jpg"])
        err = {"errors": [{"errorId": "11500",
                           "message": "There was a problem."}]}
        with mock.patch.object(m, "_app_token", return_value="tok"), \
             mock.patch.object(m.requests, "post",
                               return_value=_fake_resp(err, status=500)):
            resp = self.client.get("/api/drafts/d1/similar")
        body = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(body["matches"], [])
        self.assertTrue(body["note"])

    def test_token_failure_is_a_note(self):
        self._write_photo("d1/1.jpg")
        self._write_draft("d1", ["d1/1.jpg"])
        with mock.patch.object(m, "_app_token",
                               side_effect=RuntimeError(
                                   "eBay is not set up yet.")), \
             mock.patch.object(m.requests, "post",
                               side_effect=AssertionError(
                                   "must not call eBay without a token")):
            resp = self.client.get("/api/drafts/d1/similar")
        body = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(body["matches"], [])
        self.assertIn("not set up", body["note"])

    def test_no_photos_is_a_note(self):
        self._write_draft("d1", [])
        with mock.patch.object(m.requests, "post",
                               side_effect=AssertionError(
                                   "must not call eBay with no photo")):
            resp = self.client.get("/api/drafts/d1/similar")
        body = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(body["matches"], [])
        self.assertIn("Add a photo", body["note"])

    def test_missing_photo_file_is_a_note(self):
        self._write_draft("d1", ["d1/1.jpg"])
        with mock.patch.object(m.requests, "post",
                               side_effect=AssertionError(
                                   "must not call eBay with no photo file")):
            resp = self.client.get("/api/drafts/d1/similar")
        body = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(body["matches"], [])
        self.assertIn("could not be read", body["note"])

    def test_missing_draft_is_404(self):
        resp = self.client.get("/api/drafts/nope/similar")
        self.assertEqual(resp.status_code, 404)

    def test_repeat_lookup_uses_the_cache(self):
        """The same photo is not sent to eBay twice in ten minutes."""
        self._write_photo("d1/1.jpg")
        self._write_draft("d1", ["d1/1.jpg"])
        with mock.patch.object(m, "_app_token", return_value="tok"), \
             mock.patch.object(m.requests, "post",
                               return_value=_fake_resp(PAYLOAD)) as post:
            first = self.client.get("/api/drafts/d1/similar")
            second = self.client.get("/api/drafts/d1/similar")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(first.get_json(), second.get_json())


if __name__ == "__main__":
    unittest.main()
