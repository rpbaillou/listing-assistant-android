"""apk-v24 tests: fast-post pack — presets, quick post, batch publish,
and the single-publish refactor (the wrapper must behave exactly as
it did before the publish logic moved into _do_publish).

Storage dirs are monkeypatched to a temp dir per test, so nothing here
touches the repo's real data dir. eBay is always mocked; no test here
reaches the network.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest import mock

from PIL import Image

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PY_DIR = os.path.join(REPO, "app", "src", "main", "python")
sys.path.insert(0, PY_DIR)

import app as m  # noqa: E402  (imports cleanly; __main__ guard prevents serving)


def tearDownModule():
    # Other test modules re-import `app` with their own env; drop our
    # cached copy so their import is fresh and order-independent.
    sys.modules.pop("app", None)


def _jpeg_bytes():
    img = Image.new("RGB", (10, 10), color="red")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)
    return buf.read()


def _draft(draft_id, **over):
    d = {"id": draft_id, "created": "2026-10-28T00:00:00",
         "photos": [], "price": "", "condition": "", "condition_details": "",
         "best_offer": False, "best_offer_auto_decline": "",
         "best_offer_auto_accept": "", "title": "", "description": "",
         "category": "", "attributes": {}, "identification_notes": ""}
    d.update(over)
    return d


def _ebay_patches(fail_sku=None):
    """Mock the whole eBay surface the publish helper touches."""
    def fake_call(method, path, token, payload=None):
        if method == "PUT" and "inventory_item" in path:
            return {}
        if method == "POST" and path == "/sell/inventory/v1/offer":
            if fail_sku and payload and payload.get("sku") == fail_sku:
                raise RuntimeError("boom")
            return {"offerId": "OFF-" + (payload.get("sku") or "x")}
        if method == "POST" and path.endswith("/publish"):
            return {"listingId": "999"}
        if method == "GET" and path == "/sell/inventory/v1/location":
            # v30 (audit F2): the publish helper resolves a merchant
            # location before offering; the fixture seller has one.
            return {"locations": [{"merchantLocationKey": "LOC1"}]}
        if method == "GET":
            return {}
        raise AssertionError("unexpected eBay call %s %s" % (method, path))

    return [
        mock.patch.object(m, "_ebay_access_token", return_value="tok"),
        mock.patch.object(m, "_ebay_upload_picture",
                          return_value="https://example.com/p.jpg"),
        mock.patch.object(m, "_ebay_call", side_effect=fake_call),
        mock.patch.object(m, "_category_aspects", return_value=([], [])),
    ]


class FastpostTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig = {}
        for attr, sub in (("DRAFTS_DIR", "drafts"), ("PHOTOS_DIR", "photos")):
            self.orig[attr] = getattr(m, attr)
            p = os.path.join(self.tmp, sub)
            os.makedirs(p, exist_ok=True)
            setattr(m, attr, p)
        self.orig["PRESET_FILE"] = m.PRESET_FILE
        m.PRESET_FILE = os.path.join(self.tmp, "preset.json")
        self.client = m.app.test_client()

    def tearDown(self):
        for attr, val in self.orig.items():
            setattr(m, attr, val)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_photo(self, draft_id):
        folder = os.path.join(m.PHOTOS_DIR, draft_id)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "1.jpg"), "wb") as f:
            f.write(_jpeg_bytes())
        return "%s/1.jpg" % draft_id

    def _save(self, draft):
        return m._save_draft(draft)

    # ---- presets ----

    def test_preset_save_get_roundtrip(self):
        body = {"condition": "USED_VERY_GOOD", "category_id": "178893",
                "fulfillment_policy_id": "111", "payment_policy_id": "222",
                "return_policy_id": "333", "best_offer": True,
                "best_offer_auto_decline": "20.00",
                "best_offer_auto_accept": "35.00"}
        resp = self.client.post("/api/preset", json=body)
        self.assertEqual(resp.status_code, 200)
        got = self.client.get("/api/preset").get_json()["preset"]
        for k, v in body.items():
            self.assertEqual(got[k], v)

    def test_preset_invalid_condition_dropped(self):
        self.client.post("/api/preset",
                         json={"condition": "BOGUS", "category_id": "178893"})
        got = self.client.get("/api/preset").get_json()["preset"]
        self.assertIsNone(got["condition"])
        self.assertEqual(got["category_id"], "178893")

    def test_preset_overwrite_replaces(self):
        self.client.post("/api/preset",
                         json={"condition": "NEW", "category_id": "111"})
        self.client.post("/api/preset",
                         json={"condition": "USED_GOOD", "category_id": "222"})
        got = self.client.get("/api/preset").get_json()["preset"]
        self.assertEqual(got["condition"], "USED_GOOD")
        self.assertEqual(got["category_id"], "222")

    def test_preset_corrupt_file_means_no_preset(self):
        with open(m.PRESET_FILE, "w", encoding="utf-8") as f:
            f.write("{not json")
        resp = self.client.get("/api/preset")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.get_json()["preset"])

    def test_preset_missing_file_means_no_preset(self):
        resp = self.client.get("/api/preset")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.get_json()["preset"])

    # ---- quick post ----

    def test_quick_post_creates_blank_draft_without_ai(self):
        with mock.patch.object(
                m, "_gemini_identify",
                side_effect=AssertionError("quick post must not call Gemini")):
            resp = self.client.post(
                "/api/quick-post",
                data={"photos": (io.BytesIO(_jpeg_bytes()), "p1.jpg")},
                content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()["draft"]
        self.assertEqual(d["title"], "")
        self.assertEqual(d["description"], "")
        self.assertEqual(len(d["photos"]), 1)
        # The draft really landed on disk, ready for step 2.
        self.assertIsNotNone(m._load_draft(d["id"]))

    def test_quick_post_needs_photos(self):
        resp = self.client.post("/api/quick-post", data={},
                                content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 400)

    # ---- single-publish wrapper (refactor regression) ----

    def test_publish_wrapper_happy_path(self):
        photo = self._write_photo("d1")
        self._save(_draft("d1", title="Test item", price="30.00",
                          photos=[photo], condition="USED_VERY_GOOD"))
        body = {"draft_id": "d1", "category_id": "178893",
                "fulfillment_policy_id": "111", "payment_policy_id": "222",
                "return_policy_id": "333"}
        with ExitStack() as stack:
            for p in _ebay_patches():
                stack.enter_context(p)
            resp = self.client.post("/api/ebay/publish", json=body)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["listing_id"], "999")
        saved = m._load_draft("d1")
        self.assertEqual(saved["ebay_listing_id"], "999")
        self.assertEqual(saved["ebay_listing_url"],
                         "https://www.ebay.com/itm/999")

    def test_publish_wrapper_missing_category_400(self):
        photo = self._write_photo("d1")
        self._save(_draft("d1", title="Test item", price="30.00",
                          photos=[photo]))
        with mock.patch.object(
                m, "_ebay_access_token",
                side_effect=AssertionError("must fail before any eBay call")):
            resp = self.client.post("/api/ebay/publish",
                                    json={"draft_id": "d1"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("category", resp.get_json()["error"].lower())

    def test_publish_wrapper_no_price_400(self):
        photo = self._write_photo("d1")
        self._save(_draft("d1", title="Test item", photos=[photo]))
        resp = self.client.post("/api/ebay/publish",
                                json={"draft_id": "d1", "category_id": "178893"})
        self.assertEqual(resp.status_code, 400)

    def test_publish_wrapper_already_listed_409(self):
        photo = self._write_photo("d1")
        self._save(_draft("d1", title="Test item", price="30.00",
                          photos=[photo], ebay_listing_id="555",
                          ebay_listing_url="https://www.ebay.com/itm/555"))
        resp = self.client.post("/api/ebay/publish",
                                json={"draft_id": "d1", "category_id": "178893"})
        self.assertEqual(resp.status_code, 409)

    # ---- batch publish ----

    def _batch_setup(self):
        """Three drafts: one good, one priceless, one already live."""
        p1 = self._write_photo("good")
        self._save(_draft("good", title="Good item", price="30.00",
                          photos=[p1], condition="USED_VERY_GOOD"))
        self._save(_draft("noprice", title="No price item", price="",
                          photos=[self._write_photo("noprice")]))
        self._save(_draft("live", title="Live item", price="25.00",
                          photos=[self._write_photo("live")],
                          ebay_listing_id="555",
                          ebay_listing_url="https://www.ebay.com/itm/555"))
        self.client.post("/api/preset",
                         json={"fulfillment_policy_id": "111",
                               "payment_policy_id": "222",
                               "return_policy_id": "333"})

    def test_batch_excludes_incomplete_and_listed(self):
        self._batch_setup()
        with ExitStack() as stack:
            for p in _ebay_patches():
                stack.enter_context(p)
            resp = self.client.post(
                "/api/ebay/batch-publish",
                json={"draft_ids": ["good", "noprice", "live"],
                      "category_id": "178893"})
        self.assertEqual(resp.status_code, 200)
        by_id = {r["draft_id"]: r for r in resp.get_json()["results"]}
        self.assertTrue(by_id["good"]["ok"])
        self.assertEqual(by_id["good"]["listing_id"], "999")
        self.assertFalse(by_id["noprice"]["ok"])
        self.assertIn("price", by_id["noprice"]["error"].lower())
        self.assertFalse(by_id["live"]["ok"])
        self.assertIn("live", by_id["live"]["error"].lower())
        # The good draft is now marked listed on disk.
        self.assertEqual(m._load_draft("good")["ebay_listing_id"], "999")

    def test_batch_one_failure_does_not_stop_the_rest(self):
        self._batch_setup()
        # A second good draft, posted after the failing one.
        p2 = self._write_photo("good2")
        self._save(_draft("good2", title="Second good item", price="40.00",
                          photos=[p2], condition="USED_VERY_GOOD"))
        with ExitStack() as stack:
            for p in _ebay_patches(fail_sku="lagood"):
                stack.enter_context(p)
            resp = self.client.post(
                "/api/ebay/batch-publish",
                json={"draft_ids": ["good", "good2"],
                      "category_id": "178893"})
        by_id = {r["draft_id"]: r for r in resp.get_json()["results"]}
        self.assertFalse(by_id["good"]["ok"])
        self.assertIn("boom", by_id["good"]["error"])
        self.assertTrue(by_id["good2"]["ok"])
        self.assertEqual(by_id["good2"]["listing_id"], "999")

    def test_batch_needs_category_and_ids(self):
        resp = self.client.post("/api/ebay/batch-publish",
                                json={"draft_ids": ["x"]})
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post("/api/ebay/batch-publish",
                                json={"draft_ids": [],
                                      "category_id": "178893"})
        self.assertEqual(resp.status_code, 400)

    def test_batch_missing_policies_reported_per_item(self):
        # No preset and no .env policies: the helper's fallback finds
        # nothing, so the item fails with the policies message — and
        # the batch still returns a per-item result, not a 500.
        photo = self._write_photo("d1")
        self._save(_draft("d1", title="Test item", price="30.00",
                          photos=[photo]))
        with ExitStack() as stack:
            for p in _ebay_patches():
                stack.enter_context(p)
            resp = self.client.post(
                "/api/ebay/batch-publish",
                json={"draft_ids": ["d1"], "category_id": "178893"})
        self.assertEqual(resp.status_code, 200)
        result = resp.get_json()["results"][0]
        self.assertFalse(result["ok"])
        self.assertIn("policy", result["error"].lower())


if __name__ == "__main__":
    unittest.main()
