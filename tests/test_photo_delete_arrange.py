"""apk-v40 tests: per-photo Delete + Move left/right (arrange).

Listing-page photos previously had Edit only, and the server had no
delete or reorder endpoint, so a saved draft's photos could neither be
deleted nor reordered. These tests pin the server side: delete removes
the right entry AND its file, renumbers survivors so a later append
never overwrites one, and preserves ebay_* fields; move swaps adjacent
entries (direction) or lands a photo at a final index ("to", v44 drag
reorder) and persists; bad indexes/directions are plain errors that
leave the draft unchanged; publish sends imageUrls in draft order
(first = main); a zero-photo draft is blocked by the readiness report.
Storage dirs are monkeypatched to a temp dir per test, so nothing here
touches the repo's real data dir. Dummy images only; eBay is mocked.
"""

import io
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
    return buf


class DeleteArrangeTestCase(unittest.TestCase):
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

    def _draft(self, photos, **extra):
        d = {"id": "d1", "title": "Test item", "description": "A thing.",
             "photos": list(photos), "price": "10.00"}
        d.update(extra)
        return m._save_draft(d)

    def _photo_file(self, rel, content=None):
        full = os.path.join(m.PHOTOS_DIR, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(content if content is not None
                    else _jpeg_bytes().getvalue())
        return full

    def _read(self, rel):
        with open(os.path.join(m.PHOTOS_DIR, rel), "rb") as f:
            return f.read()

    # ---- delete ----

    def test_delete_middle_removes_entry_file_and_renumbers(self):
        self._photo_file("d1/1.jpg", b"one")
        self._photo_file("d1/2.jpg", b"two")
        self._photo_file("d1/3.jpg", b"three")
        self._draft(["d1/1.jpg", "d1/2.jpg", "d1/3.jpg"])
        resp = self.client.delete("/api/drafts/d1/photos/1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["draft"]["photos"],
                         ["d1/1.jpg", "d1/2.jpg"])
        self.assertEqual(self._read("d1/1.jpg"), b"one")
        self.assertEqual(self._read("d1/2.jpg"), b"three")
        self.assertFalse(os.path.exists(os.path.join(m.PHOTOS_DIR, "d1/3.jpg")))
        self.assertEqual(m._load_draft("d1")["photos"],
                         ["d1/1.jpg", "d1/2.jpg"])

    def test_delete_then_append_never_overwrites_survivor(self):
        self._photo_file("d1/1.jpg", b"one")
        self._photo_file("d1/2.jpg", b"two")
        self._photo_file("d1/3.jpg", b"three")
        self._draft(["d1/1.jpg", "d1/2.jpg", "d1/3.jpg"])
        self.client.delete("/api/drafts/d1/photos/0")
        resp = self.client.post(
            "/api/drafts/d1/photos",
            data={"photos": [(_jpeg_bytes("blue"), "new.jpg")]},
            content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["draft"]["photos"],
                         ["d1/1.jpg", "d1/2.jpg", "d1/3.jpg"])
        # Survivors kept their bytes; the new photo took the free number.
        self.assertEqual(self._read("d1/1.jpg"), b"two")
        self.assertEqual(self._read("d1/2.jpg"), b"three")

    def test_delete_out_of_range_leaves_draft_untouched(self):
        self._photo_file("d1/1.jpg", b"one")
        self._draft(["d1/1.jpg"])
        resp = self.client.delete("/api/drafts/d1/photos/5")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("Photo not found", resp.get_json()["error"])
        self.assertEqual(m._load_draft("d1")["photos"], ["d1/1.jpg"])
        self.assertEqual(self._read("d1/1.jpg"), b"one")

    def test_delete_missing_draft_404(self):
        resp = self.client.delete("/api/drafts/nope/photos/0")
        self.assertEqual(resp.status_code, 404)

    def test_delete_only_photo_empties_and_blocks_publish(self):
        self._photo_file("d1/1.jpg", b"one")
        self._draft(["d1/1.jpg"])
        resp = self.client.delete("/api/drafts/d1/photos/0")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["draft"]["photos"], [])
        self.assertFalse(os.path.isdir(os.path.join(m.PHOTOS_DIR, "d1")))
        d = m._load_draft("d1")
        problems, _notes = m._listing_readiness(
            d, {"title": "T", "price_val": 10.0, "condition": "",
                "category_id": "", "fulfillment_id": "", "payment_id": "",
                "return_id": "", "attributes": {}, "best_offer_terms": None},
            None)
        self.assertIn("This draft has no photos.", problems)

    def test_delete_preserves_ebay_fields(self):
        self._photo_file("d1/1.jpg", b"one")
        self._photo_file("d1/2.jpg", b"two")
        self._draft(["d1/1.jpg", "d1/2.jpg"],
                    ebay_listing_id="999", ebay_offer_id="OFFER1",
                    ebay_sku="lad1")
        resp = self.client.delete("/api/drafts/d1/photos/0")
        body = resp.get_json()["draft"]
        self.assertEqual(body["photos"], ["d1/1.jpg"])
        self.assertEqual(body["ebay_listing_id"], "999")
        self.assertEqual(body["ebay_offer_id"], "OFFER1")
        self.assertEqual(body["ebay_sku"], "lad1")

    def test_delete_does_not_touch_other_draft(self):
        self._photo_file("d1/1.jpg", b"one")
        self._photo_file("d2/1.jpg", b"other")
        self._draft(["d1/1.jpg"])
        d2 = {"id": "d2", "title": "Other", "photos": ["d2/1.jpg"]}
        m._save_draft(d2)
        self.client.delete("/api/drafts/d1/photos/0")
        self.assertEqual(m._load_draft("d2")["photos"], ["d2/1.jpg"])
        self.assertEqual(self._read("d2/1.jpg"), b"other")

    # ---- move ----

    def test_move_left_right_swaps_and_persists(self):
        for i in (1, 2, 3):
            self._photo_file(f"d1/{i}.jpg")
        self._draft(["d1/1.jpg", "d1/2.jpg", "d1/3.jpg"])
        resp = self.client.post("/api/drafts/d1/photos/2/move",
                                json={"direction": "left"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["draft"]["photos"],
                         ["d1/1.jpg", "d1/3.jpg", "d1/2.jpg"])
        self.assertEqual(m._load_draft("d1")["photos"],
                         ["d1/1.jpg", "d1/3.jpg", "d1/2.jpg"])
        resp = self.client.post("/api/drafts/d1/photos/0/move",
                                json={"direction": "right"})
        self.assertEqual(resp.get_json()["draft"]["photos"],
                         ["d1/3.jpg", "d1/1.jpg", "d1/2.jpg"])

    def test_move_edges_and_bad_direction_are_plain_errors(self):
        for i in (1, 2):
            self._photo_file(f"d1/{i}.jpg")
        self._draft(["d1/1.jpg", "d1/2.jpg"])
        resp = self.client.post("/api/drafts/d1/photos/0/move",
                                json={"direction": "left"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("already first", resp.get_json()["error"])
        resp = self.client.post("/api/drafts/d1/photos/1/move",
                                json={"direction": "right"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("already last", resp.get_json()["error"])
        resp = self.client.post("/api/drafts/d1/photos/0/move",
                                json={"direction": "up"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(m._load_draft("d1")["photos"],
                         ["d1/1.jpg", "d1/2.jpg"])

    def test_move_missing_draft_or_photo_404(self):
        self.assertEqual(self.client.post(
            "/api/drafts/nope/photos/0/move",
            json={"direction": "left"}).status_code, 404)
        self._draft(["d1/1.jpg"])
        self.assertEqual(self.client.post(
            "/api/drafts/d1/photos/9/move",
            json={"direction": "left"}).status_code, 404)

    # ---- move by "to" (v44 drag reorder) ----

    def test_move_to_lands_at_final_index_and_persists(self):
        for i in (1, 2, 3, 4):
            self._photo_file(f"d1/{i}.jpg")
        self._draft(["d1/1.jpg", "d1/2.jpg", "d1/3.jpg", "d1/4.jpg"])
        resp = self.client.post("/api/drafts/d1/photos/0/move",
                                json={"to": 3})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["draft"]["photos"],
                         ["d1/2.jpg", "d1/3.jpg", "d1/4.jpg", "d1/1.jpg"])
        self.assertEqual(m._load_draft("d1")["photos"],
                         ["d1/2.jpg", "d1/3.jpg", "d1/4.jpg", "d1/1.jpg"])
        resp = self.client.post("/api/drafts/d1/photos/3/move",
                                json={"to": 0})
        self.assertEqual(resp.get_json()["draft"]["photos"],
                         ["d1/1.jpg", "d1/2.jpg", "d1/3.jpg", "d1/4.jpg"])

    def test_move_to_same_slot_is_a_noop_success(self):
        for i in (1, 2):
            self._photo_file(f"d1/{i}.jpg")
        self._draft(["d1/1.jpg", "d1/2.jpg"])
        resp = self.client.post("/api/drafts/d1/photos/1/move",
                                json={"to": 1})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["draft"]["photos"],
                         ["d1/1.jpg", "d1/2.jpg"])

    def test_move_to_out_of_range_or_bad_type_is_a_plain_error(self):
        for i in (1, 2):
            self._photo_file(f"d1/{i}.jpg")
        self._draft(["d1/1.jpg", "d1/2.jpg"])
        for bad in (2, -1, "1", True, None):
            resp = self.client.post("/api/drafts/d1/photos/0/move",
                                    json={"to": bad})
            # None means "no to given": falls through to direction, which
            # is also missing, so the same 400 shape applies.
            self.assertEqual(resp.status_code, 400)
        self.assertEqual(m._load_draft("d1")["photos"],
                         ["d1/1.jpg", "d1/2.jpg"])

    # ---- publish order ----

    def test_publish_sends_imageurls_in_draft_order(self):
        for i in (1, 2, 3):
            self._photo_file(f"d1/{i}.jpg")
        self._draft(["d1/1.jpg", "d1/2.jpg", "d1/3.jpg"])
        # Arrange first: move photo 1 right, so draft order is 2, 1, 3.
        self.client.post("/api/drafts/d1/photos/0/move",
                         json={"direction": "right"})
        uploads = []
        payloads = {}

        def fake_upload(full, token):
            uploads.append(os.path.relpath(full, m.PHOTOS_DIR))
            return f"https://i.ebayimg.com/{os.path.basename(full)}"

        def fake_call(method, path, token, payload=None):
            if method == "PUT" and path.startswith(
                    "/sell/inventory/v1/inventory_item/"):
                payloads["inventory"] = payload
                return {}
            if (method, path) == ("POST", "/sell/inventory/v1/offer"):
                payloads["offer"] = payload
                return {"offerId": "OFFER1"}
            if method == "POST" and path.startswith(
                    "/sell/inventory/v1/offer/"):
                return {"listingId": "999"}
            if method == "GET" and path.startswith("/sell/listing/v1/item/"):
                return {}
            raise AssertionError(f"unexpected eBay call {method} {path}")

        d = m._load_draft("d1")
        f = {"title": "Test item", "description": "A test item.",
             "price_val": 10.0, "condition": "NEW", "condition_details": "",
             "category_id": "123", "fulfillment_id": "111",
             "payment_id": "222", "return_id": "333",
             "attributes": {}, "best_offer_terms": None}
        with mock.patch.object(m, "_ebay_access_token",
                               return_value="tok"), \
                mock.patch.object(m, "_ebay_upload_picture",
                                  side_effect=fake_upload), \
                mock.patch.object(m, "_category_aspects",
                                  return_value=(None, None)), \
                mock.patch.object(m, "_category_conditions",
                                  return_value=(None, None)), \
                mock.patch.object(m, "_check_fulfillment_policy",
                                  return_value=("FLAT_RATE", None)), \
                mock.patch.object(m, "_resolve_merchant_location_key",
                                  return_value="home"), \
                mock.patch.object(m, "_ebay_call", side_effect=fake_call):
            m._do_publish(d, f)
        self.assertEqual(uploads, ["d1/2.jpg", "d1/1.jpg", "d1/3.jpg"])
        self.assertEqual(
            payloads["inventory"]["product"]["imageUrls"],
            ["https://i.ebayimg.com/2.jpg",
             "https://i.ebayimg.com/1.jpg",
             "https://i.ebayimg.com/3.jpg"])


if __name__ == "__main__":
    unittest.main()
