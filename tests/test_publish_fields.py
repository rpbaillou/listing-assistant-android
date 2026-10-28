"""apk-v27 tests: publish-panel persistence — panel fields save into the
draft and restore on panel load, so nothing is lost on restart/update.

Storage dirs are monkeypatched to a temp dir per test, so nothing here
touches the repo's real data dir. eBay is always mocked; no test here
reaches the network.
"""
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


class PublishFieldsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig = {}
        for attr, sub in (("DRAFTS_DIR", "drafts"), ("PHOTOS_DIR", "photos")):
            self.orig[attr] = getattr(m, attr)
            p = os.path.join(self.tmp, sub)
            os.makedirs(p, exist_ok=True)
            setattr(m, attr, p)
        self.client = m.app.test_client()

    def tearDown(self):
        for attr, val in self.orig.items():
            setattr(m, attr, val)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _draft(self, **over):
        d = {"id": "d1", "title": "Test item", "description": "A thing.",
             "photos": ["d1/1.jpg"], "price": "10.00",
             "attributes": {"Brand": "Acme"}}
        d.update(over)
        return m._save_draft(d)

    def _panel_body(self, **over):
        body = {"category_id": "178893",
                "category_name": "Collectibles > Plates",
                "condition": "USED_VERY_GOOD", "price": "12.50",
                "fulfillment_policy_id": "111", "payment_policy_id": "222",
                "return_policy_id": "333", "best_offer": True,
                "best_offer_auto_decline": "8.00",
                "best_offer_auto_accept": "11.00",
                "attributes": {"Era": "1950s"}}
        body.update(over)
        return body

    # ---- save endpoint ----

    def test_save_merges_without_clobbering(self):
        self._draft(ebay_listing_id="999", ebay_listing_url="https://e/999")
        resp = self.client.post("/api/drafts/d1/publish-fields",
                                json=self._panel_body())
        self.assertEqual(resp.status_code, 200)
        d = m._load_draft("d1")
        self.assertEqual(d["category_id"], "178893")
        self.assertEqual(d["category_name"], "Collectibles > Plates")
        self.assertEqual(d["condition"], "USED_VERY_GOOD")
        self.assertEqual(d["price"], "12.50")
        self.assertEqual(d["fulfillment_policy_id"], "111")
        self.assertEqual(d["best_offer"], True)
        self.assertEqual(d["best_offer_auto_decline"], "8.00")
        self.assertEqual(d["attributes"]["Brand"], "Acme")
        self.assertEqual(d["attributes"]["Era"], "1950s")
        # untouched: identity, photos, copy, live-listing state
        self.assertEqual(d["id"], "d1")
        self.assertEqual(d["photos"], ["d1/1.jpg"])
        self.assertEqual(d["title"], "Test item")
        self.assertEqual(d["description"], "A thing.")
        self.assertEqual(d["ebay_listing_id"], "999")
        self.assertEqual(d["ebay_listing_url"], "https://e/999")

    def test_save_ignores_secrets_and_identity(self):
        self._draft()
        resp = self.client.post("/api/drafts/d1/publish-fields", json={
            "id": "evil", "photos": ["evil/9.jpg"],
            "ebay_listing_id": "555", "ebay_listing_url": "https://e/555",
            "EBAY_CLIENT_SECRET": "shh", "access_token": "tok",
            "refresh_token": "rtok", "token": "tok", "category_id": "178893"})
        self.assertEqual(resp.status_code, 200)
        d = m._load_draft("d1")
        self.assertEqual(d["id"], "d1")
        self.assertEqual(d["photos"], ["d1/1.jpg"])
        self.assertNotIn("ebay_listing_id", d)
        for key in ("EBAY_CLIENT_SECRET", "access_token", "refresh_token",
                    "token"):
            self.assertNotIn(key, d)
        self.assertEqual(d["category_id"], "178893")

    def test_save_invalid_condition_ignored_valid_saved(self):
        self._draft()
        self.client.post("/api/drafts/d1/publish-fields",
                         json={"condition": "BOGUS"})
        self.assertNotIn("condition", m._load_draft("d1"))
        self.client.post("/api/drafts/d1/publish-fields",
                         json={"condition": "NEW"})
        self.assertEqual(m._load_draft("d1")["condition"], "NEW")

    def test_save_empty_clears_scalar(self):
        self._draft()
        self.client.post("/api/drafts/d1/publish-fields",
                         json={"category_id": "178893",
                               "category_name": "Collectibles > Plates"})
        self.client.post("/api/drafts/d1/publish-fields",
                         json={"category_id": "", "category_name": ""})
        d = m._load_draft("d1")
        self.assertEqual(d["category_id"], "")
        self.assertEqual(d["category_name"], "")

    def test_save_attributes_merge_and_remove(self):
        self._draft(attributes={"Brand": "Acme", "Era": "1900s"})
        self.client.post("/api/drafts/d1/publish-fields",
                         json={"attributes": {"Era": "1950s",
                                              "Color": "Blue"}})
        attrs = m._load_draft("d1")["attributes"]
        self.assertEqual(attrs, {"Brand": "Acme", "Era": "1950s",
                                 "Color": "Blue"})
        self.client.post("/api/drafts/d1/publish-fields",
                         json={"attributes": {"Brand": ""}})
        self.assertNotIn("Brand", m._load_draft("d1")["attributes"])

    def test_save_partial_does_not_wipe_others(self):
        self._draft()
        self.client.post("/api/drafts/d1/publish-fields",
                         json=self._panel_body())
        self.client.post("/api/drafts/d1/publish-fields",
                         json={"price": "15.00"})
        d = m._load_draft("d1")
        self.assertEqual(d["price"], "15.00")
        self.assertEqual(d["category_id"], "178893")
        self.assertEqual(d["condition"], "USED_VERY_GOOD")

    def test_save_missing_draft_404(self):
        resp = self.client.post("/api/drafts/nope/publish-fields",
                                json={"category_id": "1"})
        self.assertEqual(resp.status_code, 404)

    # ---- publish wrapper fallback ----

    def test_publish_falls_back_to_draft_category_and_policies(self):
        self._draft()
        self.client.post("/api/drafts/d1/publish-fields",
                         json={"category_id": "178893",
                               "fulfillment_policy_id": "111",
                               "payment_policy_id": "222",
                               "return_policy_id": "333"})
        seen = {}
        def fake_publish(d, f):
            seen.update(f)
            return ("123", "https://example.com/123")
        with mock.patch.object(m, "_do_publish", side_effect=fake_publish):
            resp = self.client.post("/api/ebay/publish", json={
                "draft_id": "d1", "title": "Test item", "price": "10.00"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(seen["category_id"], "178893")
        self.assertEqual(seen["fulfillment_id"], "111")
        self.assertEqual(seen["payment_id"], "222")
        self.assertEqual(seen["return_id"], "333")

    def test_publish_genuinely_empty_category_still_400(self):
        self._draft()
        resp = self.client.post("/api/ebay/publish", json={
            "draft_id": "d1", "title": "Test item", "price": "10.00"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Pick an eBay category first",
                      resp.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
