"""apk-v64 tests: one update button for a live listing.

v57 added a photos-only update and v63 added a quantity-only update,
each with its own button ("Update live listing photos", "Update live
listing quantity") and its own confirm card. He asked why there are
all these update buttons and wants one button that updates. v64
merges them: a single Update live listing button plans both parts
together, shows one confirm card listing everything that will
change, and one confirm runs both parts (photos first, then
quantity — each part re-reads live state before writing, so the
photos PUT, which carries the whole item body, runs before the
quantity change exists and can never overwrite it). A part that
fails does not stop the other part; the answer names each part's
result, and the draft record updates only for the part that actually
succeeded.

The single-part endpoints keep working exactly as before (their
tests still pass); the combined endpoints reuse the same apply
helpers. eBay is mocked here, as in the v57/v63 tests, so live proof
still needs his one-listing trial after install.
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


class ReviseCombinedTestCase(unittest.TestCase):
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

    def _listed_draft(self, photos, urls, quantity="1", offer_qty=1,
                      **extra):
        d = {"id": "d1", "title": "Test item", "photos": list(photos),
             "ebay_listing_id": "123", "ebay_offer_id": "off1",
             "ebay_listing_url": "https://www.ebay.com/itm/123",
             "ebay_sku": "lad1", "quantity": quantity,
             "ebay_offer_quantity": offer_qty,
             "ebay_image_urls": list(urls),
             "ebay_photo_hashes": m._draft_photo_hashes(photos)}
        d.update(extra)
        return m._save_draft(d)

    def _three_photos(self, quantity="1", offer_qty=1):
        self._photo_file("d1/1.jpg", b"one")
        self._photo_file("d1/2.jpg", b"two")
        self._photo_file("d1/3.jpg", b"three")
        urls = ["https://i.ebayimg.com/1.jpg",
                "https://i.ebayimg.com/2.jpg",
                "https://i.ebayimg.com/3.jpg"]
        return self._listed_draft(["d1/1.jpg", "d1/2.jpg", "d1/3.jpg"],
                                  urls, quantity=quantity,
                                  offer_qty=offer_qty)

    def _offer_body(self, qty):
        return {"offerId": "off1", "sku": "lad1",
                "marketplaceId": "EBAY_US", "format": "FIXED_PRICE",
                "availableQuantity": qty, "categoryId": "123",
                "listingDescription": "A thing.",
                "listingPolicies": {"fulfillmentPolicyId": "111",
                                    "paymentPolicyId": "222",
                                    "returnPolicyId": "333"},
                "pricingSummary": {"price": {"currency": "USD",
                                             "value": "10.00"}},
                "listingDuration": "GTC", "merchantLocationKey": "home",
                "status": "PUBLISHED"}

    def _item_body(self, urls, qty):
        return {"product": {"title": "Test item",
                            "description": "A thing.",
                            "imageUrls": list(urls)},
                "availability": {"shipToLocationAvailability":
                                 {"quantity": qty}}}

    def _fake_ebay(self, live_urls, offer_qty=1, item_qty=1,
                   fail_offer_put=None, fail_first_item_put=False,
                   fail_all_puts=False, item_gone=False):
        """A fake eBay holding one item + one offer. GET returns the
        current photos and quantities; PUT applies what it is given.
        Records every call as (method, path). fail_offer_put is an
        _EbayApiError raised by PUT /offer; fail_first_item_put fails
        only the first PUT /inventory_item (the photos PUT); fail_all_puts
        fails every PUT; item_gone makes every GET 404."""
        state = {"urls": list(live_urls), "offer": offer_qty,
                 "item": item_qty}
        calls = []
        puts = {"item": [], "offer": []}

        def fake(method, path, token, payload=None):
            calls.append((method, path))
            if method == "GET" and "/inventory_item/" in path:
                if item_gone:
                    raise m._EbayApiError("not found", status=404,
                                          error_id="")
                return self._item_body(state["urls"], state["item"])
            if method == "GET" and path == "/sell/inventory/v1/offer/off1":
                if item_gone:
                    raise m._EbayApiError("not found", status=404,
                                          error_id="25001")
                return self._offer_body(state["offer"])
            if method == "GET" and path.startswith(
                    "/sell/inventory/v1/offer?sku="):
                if item_gone:
                    return {"offers": []}
                return {"offers": [self._offer_body(state["offer"])]}
            if method == "PUT" and "/inventory_item/" in path:
                puts["item"].append(json.loads(json.dumps(payload)))
                if fail_all_puts or (
                        fail_first_item_put and len(puts["item"]) == 1):
                    raise m._EbayApiError(
                        "eBay said (error 21919302): The listing cannot "
                        "be revised because it has bids.",
                        status=400, error_id="21919302")
                urls = ((payload.get("product") or {})
                        .get("imageUrls"))
                if urls is not None:
                    state["urls"] = list(urls)
                qty = (((payload.get("availability") or {})
                        .get("shipToLocationAvailability") or {})
                       .get("quantity"))
                if qty is not None:
                    state["item"] = qty
                return {}
            if method == "PUT" and "/offer/" in path:
                puts["offer"].append(json.loads(json.dumps(payload)))
                if fail_all_puts:
                    raise m._EbayApiError(
                        "eBay said (error 21919302): The listing cannot "
                        "be revised because it has bids.",
                        status=400, error_id="21919302")
                if fail_offer_put is not None:
                    raise fail_offer_put
                state["offer"] = payload.get("availableQuantity")
                return {}
            raise AssertionError("unexpected eBay call %s %s" % (method, path))

        return fake, calls, puts, state

    def _fake_upload(self):
        uploads = []

        def fake(full, token):
            uploads.append(full)
            base = os.path.basename(full)
            return "https://i.ebayimg.com/new-%s" % base

        return fake, uploads

    def _preview(self, ebay_fake, quantity="3"):
        with mock.patch.object(m, "_ebay_access_token",
                               return_value="tok"), \
                mock.patch.object(m, "_ebay_call",
                                  side_effect=ebay_fake):
            return self.client.get("/api/drafts/d1/revise?quantity=" +
                                   quantity)

    def _execute(self, ebay_fake, upload_fake=None, quantity=3,
                 confirmed=True):
        with mock.patch.object(m, "_ebay_access_token",
                               return_value="tok"), \
                mock.patch.object(m, "_ebay_call",
                                  side_effect=ebay_fake), \
                mock.patch.object(m, "_ebay_upload_picture",
                                  side_effect=upload_fake or
                                  (lambda full, token:
                                   "https://i.ebayimg.com/x.jpg")):
            return self.client.post(
                "/api/drafts/d1/revise",
                json={"confirmed": confirmed, "quantity": quantity})

    # ---- preview: one card for both parts ----

    def test_preview_both_changed_one_card(self):
        d = self._three_photos()
        self._photo_file("d1/2.jpg", b"two-edited")
        fake, _, _, _ = self._fake_ebay(d["ebay_image_urls"])
        resp = self._preview(fake, "3")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertFalse(body["unchanged"])
        self.assertFalse(body["photos"]["unchanged"])
        self.assertFalse(body["quantity"]["unchanged"])
        self.assertEqual(body["quantity"]["live_quantity"], 1)
        self.assertEqual(body["quantity"]["requested_quantity"], 3)
        self.assertIn("Photo 2 was edited and will be uploaded.",
                      body["changes"])
        self.assertIn("Quantity changes from 1 to 3.", body["changes"])

    def test_preview_photos_only(self):
        d = self._three_photos(quantity="3", offer_qty=3)
        self._photo_file("d1/2.jpg", b"two-edited")
        fake, _, _, _ = self._fake_ebay(d["ebay_image_urls"],
                                        offer_qty=3, item_qty=3)
        resp = self._preview(fake, "3")
        body = resp.get_json()
        self.assertFalse(body["unchanged"])
        self.assertFalse(body["photos"]["unchanged"])
        self.assertTrue(body["quantity"]["unchanged"])
        self.assertIn("Photo 2 was edited and will be uploaded.",
                      body["changes"])
        self.assertNotIn("Quantity changes from", " ".join(body["changes"]))

    def test_preview_quantity_only(self):
        d = self._three_photos()
        fake, _, _, _ = self._fake_ebay(d["ebay_image_urls"])
        resp = self._preview(fake, "3")
        body = resp.get_json()
        self.assertFalse(body["unchanged"])
        self.assertTrue(body["photos"]["unchanged"])
        self.assertFalse(body["quantity"]["unchanged"])
        self.assertEqual(body["changes"],
                         ["No photo changes. The live listing already shows "
                          "these photos.",
                          "Quantity changes from 1 to 3."])

    def test_preview_nothing_changed(self):
        d = self._three_photos(quantity="3", offer_qty=3)
        fake, _, _, _ = self._fake_ebay(d["ebay_image_urls"],
                                        offer_qty=3, item_qty=3)
        resp = self._preview(fake, "3")
        body = resp.get_json()
        self.assertTrue(body["unchanged"])
        self.assertEqual(body["changes"],
                         ["No photo changes. The live listing already shows "
                          "these photos.",
                          "The live listing already has a quantity of 3."])

    def test_preview_missing_photo_does_not_block_quantity(self):
        d = self._three_photos()
        os.remove(os.path.join(m.PHOTOS_DIR, "d1/2.jpg"))
        fake, _, _, _ = self._fake_ebay(d["ebay_image_urls"])
        resp = self._preview(fake, "3")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertFalse(body["unchanged"])
        self.assertIsNotNone(body["photos"]["error"])
        self.assertIn("Photo #2 is missing", body["photos"]["error"])
        self.assertFalse(body["quantity"]["unchanged"])
        self.assertIn("Quantity changes from 1 to 3.", body["changes"])
        self.assertTrue(any("Photos could not be checked" in c and
                            "Only the quantity will change." in c
                            for c in body["changes"]))

    def test_preview_no_photos_at_all_does_not_block_quantity(self):
        m._save_draft({"id": "d1", "title": "T", "photos": [],
                       "ebay_listing_id": "123", "ebay_offer_id": "off1",
                       "ebay_sku": "lad1", "quantity": "1",
                       "ebay_offer_quantity": 1})
        fake, _, _, _ = self._fake_ebay(
            ["https://i.ebayimg.com/1.jpg"])
        resp = self._preview(fake, "3")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertFalse(body["unchanged"])
        self.assertIn("no photos to update", body["photos"]["error"])
        self.assertIn("Quantity changes from 1 to 3.", body["changes"])

    def test_preview_both_parts_gone_is_502(self):
        d = self._three_photos()
        fake, _, _, _ = self._fake_ebay(d["ebay_image_urls"],
                                        item_gone=True)
        resp = self._preview(fake, "3")
        self.assertEqual(resp.status_code, 502)
        self.assertIn("no longer has this item", resp.get_json()["error"])

    def test_preview_missing_draft_404(self):
        resp = self._preview(None)
        self.assertEqual(resp.status_code, 404)
        self.assertIn("Draft not found", resp.get_json()["error"])

    def test_preview_unlisted_draft_409(self):
        m._save_draft({"id": "d1", "title": "T", "photos": ["d1/1.jpg"]})
        resp = self._preview(None)
        self.assertEqual(resp.status_code, 409)
        self.assertIn("not published by the app", resp.get_json()["error"])

    def test_preview_invalid_quantity_400(self):
        self._three_photos()
        fake, _, _, _ = self._fake_ebay(
            ["https://i.ebayimg.com/1.jpg"])
        resp = self._preview(fake, "0")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("whole number of 1 or more",
                      resp.get_json()["error"])

    # ---- execute: one confirm runs both parts ----

    def test_execute_both_changed_photos_first_then_quantity(self):
        d = self._three_photos()
        self._photo_file("d1/2.jpg", b"two-edited")
        live = d["ebay_image_urls"]
        fake, calls, puts, state = self._fake_ebay(live)
        upload_fake, uploads = self._fake_upload()
        resp = self._execute(fake, upload_fake, 3)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["photos"]["ok"])
        self.assertFalse(body["photos"]["unchanged"])
        self.assertTrue(body["quantity"]["ok"])
        self.assertFalse(body["quantity"]["unchanged"])
        self.assertEqual(body["quantity"]["live_quantity"], 3)
        self.assertIn("now shows the new photos", body["message"])
        self.assertIn("quantity of 3", body["message"])
        # Only the edited photo was uploaded.
        self.assertEqual(len(uploads), 1)
        self.assertTrue(uploads[0].endswith("d1/2.jpg"))
        # The photos PUT ran before the quantity item PUT, so the
        # whole-item photos body can never overwrite the new quantity.
        photos_put = calls.index(
            ("PUT", "/sell/inventory/v1/inventory_item/lad1"))
        offer_put = calls.index(("PUT", "/sell/inventory/v1/offer/off1"))
        self.assertLess(photos_put, offer_put)
        self.assertEqual(len(puts["item"]), 2)
        # The live state ends with the new photos AND the new quantity.
        self.assertEqual(state["urls"],
                         [live[0], "https://i.ebayimg.com/new-2.jpg",
                          live[2]])
        self.assertEqual(state["offer"], 3)
        self.assertEqual(state["item"], 3)
        # The draft records both parts.
        after = m._load_draft("d1")
        self.assertEqual(after["ebay_image_urls"], state["urls"])
        self.assertEqual(after["ebay_photo_hashes"],
                         [_sha(b"one"), _sha(b"two-edited"), _sha(b"three")])
        self.assertEqual(after["quantity"], "3")
        self.assertEqual(after["ebay_offer_quantity"], 3)

    def test_execute_photos_only_sends_no_offer_put(self):
        d = self._three_photos(quantity="3", offer_qty=3)
        self._photo_file("d1/2.jpg", b"two-edited")
        fake, calls, _, state = self._fake_ebay(d["ebay_image_urls"],
                                                offer_qty=3, item_qty=3)
        upload_fake, _ = self._fake_upload()
        resp = self._execute(fake, upload_fake, 3)
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["photos"]["ok"])
        self.assertTrue(body["quantity"]["ok"])
        self.assertTrue(body["quantity"]["unchanged"])
        self.assertEqual([c for c in calls if c == (
            "PUT", "/sell/inventory/v1/offer/off1")], [])
        self.assertEqual(state["offer"], 3)
        self.assertEqual(m._load_draft("d1")["ebay_offer_quantity"], 3)

    def test_execute_quantity_only_uploads_nothing(self):
        d = self._three_photos()
        fake, calls, _, state = self._fake_ebay(d["ebay_image_urls"])
        upload_fake, uploads = self._fake_upload()
        resp = self._execute(fake, upload_fake, 3)
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["photos"]["unchanged"])
        self.assertTrue(body["quantity"]["ok"])
        self.assertEqual(uploads, [])
        self.assertEqual(state["urls"], d["ebay_image_urls"])
        self.assertEqual(state["offer"], 3)
        # The photo record is untouched.
        self.assertEqual(m._load_draft("d1")["ebay_image_urls"],
                         d["ebay_image_urls"])

    def test_execute_nothing_changed_sends_no_puts(self):
        d = self._three_photos(quantity="3", offer_qty=3)
        fake, calls, _, _ = self._fake_ebay(d["ebay_image_urls"],
                                            offer_qty=3, item_qty=3)
        upload_fake, uploads = self._fake_upload()
        resp = self._execute(fake, upload_fake, 3)
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["photos"]["unchanged"])
        self.assertTrue(body["quantity"]["unchanged"])
        self.assertIn("Nothing to change", body["message"])
        self.assertEqual([c for c in calls if c[0] == "PUT"], [])
        self.assertEqual(uploads, [])

    # ---- execute: partial failure ----

    def test_execute_quantity_fails_photos_still_complete(self):
        d = self._three_photos()
        self._photo_file("d1/2.jpg", b"two-edited")
        live = d["ebay_image_urls"]
        err = m._EbayApiError(
            "eBay said (error 21919302): The listing cannot be revised "
            "because it has bids.", status=400, error_id="21919302")
        fake, _, _, state = self._fake_ebay(live, fail_offer_put=err)
        upload_fake, uploads = self._fake_upload()
        resp = self._execute(fake, upload_fake, 3)
        # Partial failure answers 200: one part went through.
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["photos"]["ok"])
        self.assertFalse(body["quantity"]["ok"])
        self.assertIn("has bids", body["quantity"]["error"])
        self.assertIn("21919302", body["quantity"]["error"])
        self.assertIn("now shows the new photos", body["message"])
        self.assertIn("quantity did not change", body["message"])
        # Photos went through and the draft records them; the
        # quantity did not, and the draft still says 1.
        self.assertEqual(len(uploads), 1)
        self.assertEqual(state["urls"],
                         [live[0], "https://i.ebayimg.com/new-2.jpg",
                          live[2]])
        self.assertEqual(state["offer"], 1)
        after = m._load_draft("d1")
        self.assertEqual(after["ebay_image_urls"], state["urls"])
        self.assertEqual(after["quantity"], "1")
        self.assertEqual(after["ebay_offer_quantity"], 1)

    def test_execute_photos_fail_quantity_still_completes(self):
        d = self._three_photos()
        self._photo_file("d1/2.jpg", b"two-edited")
        live = d["ebay_image_urls"]
        fake, _, _, state = self._fake_ebay(live,
                                            fail_first_item_put=True)
        upload_fake, uploads = self._fake_upload()
        resp = self._execute(fake, upload_fake, 3)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["photos"]["ok"])
        self.assertIn("has bids", body["photos"]["error"])
        self.assertTrue(body["quantity"]["ok"])
        self.assertEqual(body["quantity"]["live_quantity"], 3)
        self.assertIn("quantity of 3", body["message"])
        self.assertIn("photos did not change", body["message"])
        # The quantity went through and the draft records it; the
        # photos did not, and the draft still records the old list.
        self.assertEqual(state["offer"], 3)
        self.assertEqual(state["urls"], live)
        after = m._load_draft("d1")
        self.assertEqual(after["ebay_image_urls"], live)
        self.assertEqual(after["quantity"], "3")
        self.assertEqual(after["ebay_offer_quantity"], 3)

    def test_execute_both_fail_is_502_and_claims_nothing(self):
        d = self._three_photos()
        self._photo_file("d1/2.jpg", b"two-edited")
        live = d["ebay_image_urls"]
        fake, _, _, state = self._fake_ebay(live, fail_all_puts=True)
        upload_fake, _ = self._fake_upload()
        resp = self._execute(fake, upload_fake, 3)
        self.assertEqual(resp.status_code, 502)
        body = resp.get_json()
        self.assertFalse(body["photos"]["ok"])
        self.assertFalse(body["quantity"]["ok"])
        self.assertIn("Nothing changed", body["error"])
        self.assertIn("photos did not change", body["error"])
        self.assertIn("quantity did not change", body["error"])
        self.assertEqual(state["urls"], live)
        self.assertEqual(state["offer"], 1)
        after = m._load_draft("d1")
        self.assertEqual(after["ebay_image_urls"], live)
        self.assertEqual(after["quantity"], "1")

    def test_execute_requires_confirmed(self):
        d = self._three_photos()
        fake, calls, _, _ = self._fake_ebay(d["ebay_image_urls"])
        upload_fake, _ = self._fake_upload()
        resp = self._execute(fake, upload_fake, 3, confirmed=False)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Confirm the update first", resp.get_json()["error"])
        self.assertEqual([c for c in calls if c[0] == "PUT"], [])

    def test_execute_scope_reconnect_flag(self):
        self._three_photos()
        with mock.patch.object(m, "_ebay_access_token",
                               side_effect=m._NeedsScopeReconnect(
                                   m.SCOPE_RECONNECT_MSG)):
            resp = self.client.post("/api/drafts/d1/revise",
                                    json={"confirmed": True, "quantity": 3})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["needs_scope_reconnect"])
        self.assertIn("one new permission", body["error"])


if __name__ == "__main__":
    unittest.main()
