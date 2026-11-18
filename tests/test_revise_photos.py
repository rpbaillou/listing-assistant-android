"""apk-v57 tests: photos-only update (revise) for an already-listed draft.

The editor (v56) only works on drafts, and List only ever creates a new
listing, so a live listing's photos could not be changed from the app at
all. v57 adds a photos-only revise: preview the change against the live
inventory item, confirm it on a card, then upload only the changed/new
photos, PUT the item back with only the photo list swapped, and read the
item back to prove the new photos took. No new offer, no re-publish, no
other field touched.

eBay's Media API is production-only (not supported in Sandbox), so the
revise flow cannot be proven end to end in CI: the eBay calls here are
mocked (GET item, Media upload, PUT item, GET verify) and the report
must say live proof needs his one-listing trial after install.
"""

import hashlib
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


def _sha(b):
    return hashlib.sha256(b).hexdigest()


class RevisePhotosTestCase(unittest.TestCase):
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

    # ---- fixtures ----

    def _photo_file(self, rel, content):
        full = os.path.join(m.PHOTOS_DIR, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(content)
        return full

    def _listed_draft(self, photos, urls, hashes=None, **extra):
        d = {"id": "d1", "title": "Test item", "photos": list(photos),
             "ebay_listing_id": "123", "ebay_offer_id": "off1",
             "ebay_listing_url": "https://www.ebay.com/itm/123",
             "ebay_sku": "lad1",
             "ebay_image_urls": list(urls),
             "ebay_photo_hashes": (hashes if hashes is not None
                                   else m._draft_photo_hashes(photos))}
        d.update(extra)
        return m._save_draft(d)

    def _three_photos(self):
        self._photo_file("d1/1.jpg", b"one")
        self._photo_file("d1/2.jpg", b"two")
        self._photo_file("d1/3.jpg", b"three")
        urls = ["https://i.ebayimg.com/1.jpg",
                "https://i.ebayimg.com/2.jpg",
                "https://i.ebayimg.com/3.jpg"]
        return self._listed_draft(["d1/1.jpg", "d1/2.jpg", "d1/3.jpg"], urls)

    def _item_body(self, urls):
        return {"product": {"title": "Test item",
                            "description": "A thing.",
                            "imageUrls": list(urls)},
                "availability": {"shipToLocationAvailability": {"quantity": 1}}}

    def _fake_ebay(self, live_urls, put_applies=True):
        """A fake eBay that holds one inventory item. GET returns its
        current photo list; PUT replaces it (unless put_applies is
        False, which simulates eBay ignoring the update). Records
        every call as (method, path)."""
        state = {"urls": list(live_urls)}
        calls = []

        def fake(method, path, token, payload=None):
            calls.append((method, path))
            if method == "GET" and "/inventory_item/" in path:
                return self._item_body(state["urls"])
            if method == "PUT" and "/inventory_item/" in path:
                if put_applies:
                    state["urls"] = list(
                        (payload.get("product") or {}).get("imageUrls") or [])
                return {}
            raise AssertionError("unexpected eBay call %s %s" % (method, path))

        return fake, calls, state

    def _fake_upload(self):
        """Media upload mock: one fresh URL per local file, recorded."""
        uploads = []

        def fake(full, token):
            uploads.append(full)
            base = os.path.basename(full)
            return "https://i.ebayimg.com/new-%s" % base

        return fake, uploads

    def _preview(self, ebay_fake=None):
        with mock.patch.object(m, "_ebay_access_token",
                               return_value="tok"), \
                mock.patch.object(m, "_ebay_call",
                                  side_effect=ebay_fake) as call:
            resp = self.client.get("/api/drafts/d1/revise-photos")
            return resp, call

    def _execute(self, ebay_fake, upload_fake=None):
        with mock.patch.object(m, "_ebay_access_token",
                               return_value="tok"), \
                mock.patch.object(m, "_ebay_call",
                                  side_effect=ebay_fake), \
                mock.patch.object(m, "_ebay_upload_picture",
                                  side_effect=upload_fake or
                                  (lambda full, token: "https://i.ebayimg.com/x.jpg")):
            return self.client.post("/api/drafts/d1/revise-photos",
                                    json={"confirmed": True})

    # ---- hashing ----

    def test_photo_hashes_match_file_bytes(self):
        self._photo_file("d1/1.jpg", b"one")
        self._photo_file("d1/2.jpg", b"two")
        self.assertEqual(m._draft_photo_hashes(["d1/1.jpg", "d1/2.jpg"]),
                         [_sha(b"one"), _sha(b"two")])
        self.assertEqual(m._draft_photo_hashes(["d1/gone.jpg"]), [None])

    # ---- eligibility ----

    def test_preview_missing_draft_404(self):
        resp, _ = self._preview()
        self.assertEqual(resp.status_code, 404)
        self.assertIn("Draft not found", resp.get_json()["error"])

    def test_preview_unlisted_draft_409(self):
        self._photo_file("d1/1.jpg", b"one")
        m._save_draft({"id": "d1", "title": "T", "photos": ["d1/1.jpg"]})
        resp, _ = self._preview()
        self.assertEqual(resp.status_code, 409)
        self.assertIn("not published by the app", resp.get_json()["error"])

    def test_preview_listed_without_offer_id_409(self):
        self._photo_file("d1/1.jpg", b"one")
        m._save_draft({"id": "d1", "title": "T", "photos": ["d1/1.jpg"],
                       "ebay_listing_id": "123"})
        resp, _ = self._preview()
        self.assertEqual(resp.status_code, 409)

    def test_preview_no_photos_400(self):
        m._save_draft({"id": "d1", "title": "T", "photos": [],
                       "ebay_listing_id": "123", "ebay_offer_id": "off1"})
        resp, _ = self._preview()
        self.assertEqual(resp.status_code, 400)

    # ---- preview plans ----

    def test_preview_unchanged_plan(self):
        d = self._three_photos()
        urls = d["ebay_image_urls"]
        fake, _, _ = self._fake_ebay(urls)
        resp, _ = self._preview(fake)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["unchanged"])
        self.assertEqual(body["uploads"], [])
        self.assertEqual(body["removed"], 0)
        self.assertFalse(body["reordered"])
        self.assertEqual(body["changes"],
                         ["No photo changes. The live listing already shows "
                          "these photos."])

    def test_preview_edited_photo_uploads_only_it(self):
        d = self._three_photos()
        self._photo_file("d1/2.jpg", b"two-edited")
        fake, _, _ = self._fake_ebay(d["ebay_image_urls"])
        resp, _ = self._preview(fake)
        body = resp.get_json()
        self.assertEqual(body["uploads"], [1])
        self.assertEqual(body["removed"], 0)
        self.assertFalse(body["reordered"])
        self.assertIn("Photo 2 was edited and will be uploaded.",
                      body["changes"])

    def test_preview_added_photo(self):
        d = self._three_photos()
        self._photo_file("d1/4.jpg", b"four")
        d["photos"].append("d1/4.jpg")
        m._save_draft(d)
        fake, _, _ = self._fake_ebay(d["ebay_image_urls"])
        resp, _ = self._preview(fake)
        body = resp.get_json()
        self.assertEqual(body["uploads"], [3])
        self.assertIn("Photo 4 is new and will be uploaded.",
                      body["changes"])

    def test_preview_removed_photo(self):
        d = self._three_photos()
        d["photos"] = ["d1/1.jpg", "d1/3.jpg"]
        m._save_draft(d)
        fake, _, _ = self._fake_ebay(d["ebay_image_urls"])
        resp, _ = self._preview(fake)
        body = resp.get_json()
        self.assertEqual(body["uploads"], [])
        self.assertEqual(body["removed"], 1)
        self.assertIn("1 photo on the live listing will be removed.",
                      body["changes"])

    def test_preview_reordered_photos_upload_nothing(self):
        d = self._three_photos()
        d["photos"] = ["d1/2.jpg", "d1/1.jpg", "d1/3.jpg"]
        m._save_draft(d)
        fake, _, _ = self._fake_ebay(d["ebay_image_urls"])
        resp, _ = self._preview(fake)
        body = resp.get_json()
        self.assertEqual(body["uploads"], [])
        self.assertEqual(body["removed"], 0)
        self.assertTrue(body["reordered"])
        self.assertIn("The photo order changed. Photo 1 stays the main "
                      "photo.", body["changes"])

    def test_preview_legacy_draft_uploads_all(self):
        self._photo_file("d1/1.jpg", b"one")
        self._photo_file("d1/2.jpg", b"two")
        m._save_draft({"id": "d1", "title": "T",
                       "photos": ["d1/1.jpg", "d1/2.jpg"],
                       "ebay_listing_id": "123", "ebay_offer_id": "off1",
                       "ebay_sku": "lad1"})
        fake, _, _ = self._fake_ebay(["https://i.ebayimg.com/1.jpg",
                                      "https://i.ebayimg.com/2.jpg"])
        resp, _ = self._preview(fake)
        body = resp.get_json()
        self.assertEqual(body["uploads"], [0, 1])
        self.assertIn("no record of which photos eBay already has",
                      body["changes"][0])

    def test_preview_live_changed_elsewhere_uploads_all(self):
        d = self._three_photos()
        fake, _, _ = self._fake_ebay(["https://i.ebayimg.com/other.jpg"])
        resp, _ = self._preview(fake)
        body = resp.get_json()
        self.assertEqual(body["uploads"], [0, 1, 2])
        self.assertIn("changed somewhere other than this app",
                      body["changes"][0])

    def test_preview_item_gone_404_is_plain(self):
        self._three_photos()

        def gone(method, path, token, payload=None):
            raise m._EbayApiError("not found", status=404, error_id="")

        resp, _ = self._preview(gone)
        self.assertEqual(resp.status_code, 502)
        self.assertIn("no longer has this item", resp.get_json()["error"])

    def test_preview_missing_local_file_names_it(self):
        d = self._three_photos()
        os.remove(os.path.join(m.PHOTOS_DIR, "d1/2.jpg"))
        fake, _, _ = self._fake_ebay(d["ebay_image_urls"])
        resp, _ = self._preview(fake)
        self.assertEqual(resp.status_code, 502)
        self.assertIn("Photo #2 is missing", resp.get_json()["error"])

    # ---- execute ----

    def test_execute_requires_confirmed(self):
        self._three_photos()
        calls = []

        def no_ebay(method, path, token, payload=None):
            calls.append((method, path))
            raise AssertionError("no eBay call before confirm")

        with mock.patch.object(m, "_ebay_access_token",
                               return_value="tok"), \
                mock.patch.object(m, "_ebay_call", side_effect=no_ebay):
            resp = self.client.post("/api/drafts/d1/revise-photos", json={})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Confirm the photo update first",
                      resp.get_json()["error"])
        self.assertEqual(calls, [])

    def test_execute_uploads_only_changed_puts_full_item_verifies(self):
        d = self._three_photos()
        self._photo_file("d1/2.jpg", b"two-edited")
        live = d["ebay_image_urls"]
        fake, calls, state = self._fake_ebay(live)
        upload_fake, uploads = self._fake_upload()
        # Capture the PUT body.
        put_bodies = []
        orig_fake = fake

        def spy(method, path, token, payload=None):
            if method == "PUT":
                put_bodies.append(json.loads(json.dumps(payload)))
            return orig_fake(method, path, token, payload)

        resp = self._execute(spy, upload_fake)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["uploaded"], 1)
        self.assertEqual(body["removed"], 0)
        # Only the edited photo was uploaded.
        self.assertEqual(len(uploads), 1)
        self.assertTrue(uploads[0].endswith("d1/2.jpg"))
        # The PUT carried the GET item back with only the photo list
        # swapped: title, description, availability untouched.
        self.assertEqual(len(put_bodies), 1)
        put = put_bodies[0]
        self.assertEqual(put["product"]["title"], "Test item")
        self.assertEqual(put["product"]["description"], "A thing.")
        self.assertEqual(put["availability"],
                         {"shipToLocationAvailability": {"quantity": 1}})
        self.assertEqual(put["product"]["imageUrls"],
                         [live[0], "https://i.ebayimg.com/new-2.jpg",
                          live[2]])
        # No offer create, no publish, no new listing anywhere.
        self.assertEqual([c[0] for c in calls], ["GET", "PUT", "GET"])
        self.assertTrue(all("/offer" not in c[1] and "/publish" not in c[1]
                            for c in calls))
        # The draft now records the new live photo list + hashes.
        after = m._load_draft("d1")
        self.assertEqual(after["ebay_image_urls"],
                         [live[0], "https://i.ebayimg.com/new-2.jpg",
                          live[2]])
        self.assertEqual(after["ebay_photo_hashes"],
                         [_sha(b"one"), _sha(b"two-edited"), _sha(b"three")])

    def test_execute_unchanged_sends_nothing(self):
        d = self._three_photos()
        fake, calls, _ = self._fake_ebay(d["ebay_image_urls"])
        upload_fake, uploads = self._fake_upload()
        resp = self._execute(fake, upload_fake)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["unchanged"])
        self.assertEqual([c[0] for c in calls], ["GET"])
        self.assertEqual(uploads, [])

    def test_execute_put_error_surfaces_and_keeps_draft(self):
        d = self._three_photos()
        self._photo_file("d1/2.jpg", b"two-edited")
        live = d["ebay_image_urls"]

        def failing_put(method, path, token, payload=None):
            if method == "PUT":
                raise m._EbayApiError(
                    "eBay said (error 21919302): The listing cannot be "
                    "revised because it has bids.",
                    status=400, error_id="21919302")
            return self._item_body(live)

        upload_fake, _ = self._fake_upload()
        resp = self._execute(failing_put, upload_fake)
        self.assertEqual(resp.status_code, 502)
        self.assertIn("has bids", resp.get_json()["error"])
        self.assertIn("21919302", resp.get_json()["error"])
        # The draft still records the OLD live photos: a retry can
        # recompute the plan instead of trusting a failed update.
        self.assertEqual(m._load_draft("d1")["ebay_image_urls"], live)

    def test_execute_verify_mismatch_errors(self):
        d = self._three_photos()
        self._photo_file("d1/2.jpg", b"two-edited")
        # PUT is ignored by eBay, so the read-back still shows the old
        # photos and the update is reported as not taken.
        fake, _, _ = self._fake_ebay(d["ebay_image_urls"], put_applies=False)
        upload_fake, _ = self._fake_upload()
        resp = self._execute(fake, upload_fake)
        self.assertEqual(resp.status_code, 502)
        self.assertIn("did not show the new photos",
                      resp.get_json()["error"])
        self.assertEqual(m._load_draft("d1")["ebay_image_urls"],
                         d["ebay_image_urls"])

    def test_execute_scope_reconnect_flag(self):
        self._three_photos()
        with mock.patch.object(m, "_ebay_access_token",
                               side_effect=m._NeedsScopeReconnect(
                                   m.SCOPE_RECONNECT_MSG)):
            resp = self.client.post("/api/drafts/d1/revise-photos",
                                    json={"confirmed": True})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["needs_scope_reconnect"])
        self.assertIn("one new permission", body["error"])


if __name__ == "__main__":
    unittest.main()
