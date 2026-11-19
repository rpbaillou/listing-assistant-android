"""apk-v63 tests: quantity — enter it before listing, change it live.

Before v63 every listing went up as 1: the quantity was hardcoded in
two places on the publish path (the inventory item availability and
the offer availableQuantity) and no field existed anywhere to change
it, and a live listing's quantity could not be changed from the app
at all (the v57 revise flow is photos-only). v63 adds a Quantity
field on the listing page, sends it on both publish levels, discards
a saved offer when the quantity changes (the v34/v35 snapshot rule),
carries each draft's quantity through batch publish, and adds a
quantity-only live update that follows the v57 revise pattern
(preview against the live offer, confirm card, update both levels,
read back and prove it).

eBay's Inventory API is production-only for the revise calls, so the
live-update flow cannot be proven end to end in CI: the eBay calls
here are mocked (GET offer, GET item, PUT item, PUT offer, GET
verify) and the report must say live proof needs his one-listing
trial after install — same as v57.
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


class ParseQuantityTest(unittest.TestCase):
    def test_whole_numbers(self):
        self.assertEqual(m._parse_quantity("3"), (3, None))
        self.assertEqual(m._parse_quantity(" 3 "), (3, None))
        self.assertEqual(m._parse_quantity("1"), (1, None))
        self.assertEqual(m._parse_quantity(3), (3, None))

    def test_invalid_values(self):
        msg = "Enter a whole number of 1 or more for the quantity."
        for bad in ("", "   ", "0", "-2", "2.5", "3.0", "abc", "1,000"):
            value, err = m._parse_quantity(bad)
            self.assertIsNone(value, "should reject %r" % bad)
            self.assertEqual(err, msg, "wrong message for %r" % bad)


FLAT_POLICY = {"fulfillmentPolicyId": "111", "name": "Dummy Flat",
               "marketplaceId": "EBAY_US",
               "shippingOptions": [{"optionType": "DOMESTIC",
                                    "costType": "FLAT_RATE",
                                    "shippingServices": [{"shippingServiceCode": "USPSParcel"}]}]}
OTHER_POLICY = {"fulfillmentPolicyId": "999", "name": "Dummy Other",
                "marketplaceId": "EBAY_US",
                "shippingOptions": [{"optionType": "DOMESTIC",
                                     "costType": "FLAT_RATE",
                                     "shippingServices": [{"shippingServiceCode": "USPSParcel"}]}]}


class PublishQuantityTest(unittest.TestCase):
    """_do_publish with eBay fully mocked (ComplianceTest pattern)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig = {}
        for attr, sub in (("DRAFTS_DIR", "drafts"), ("PHOTOS_DIR", "photos")):
            self.orig[attr] = getattr(m, attr)
            p = os.path.join(self.tmp, sub)
            os.makedirs(p, exist_ok=True)
            setattr(m, attr, p)
        self.photo = os.path.join(m.PHOTOS_DIR, "p.jpg")
        with open(self.photo, "wb") as f:
            f.write(b"\xff\xd8fakejpgbytes")
        self.draft = {"id": "d1", "photos": ["p.jpg"]}
        self.fields = {
            "title": "Test item",
            "description": "A test item.",
            "price_val": 10.0,
            "quantity": 3,
            "condition": "NEW",
            "condition_details": "",
            "category_id": "123",
            "fulfillment_id": "111",
            "payment_id": "222",
            "return_id": "333",
            "attributes": {},
            "best_offer_terms": None,
        }
        self.policy_mode = "flat"
        self.publish_error = None
        self.offer_error = None
        self.inventory_payload = None
        self.offer_payload = None
        self.uploads = 0
        self.deletes = []
        self.saved = []
        self.aspects = (None, None)
        self.conditions = (None, None)
        self.reused_offer = None

    def tearDown(self):
        for attr, val in self.orig.items():
            setattr(m, attr, val)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_call(self, method, path, token, payload=None):
        if (method, path) == ("GET", "/sell/inventory/v1/location"):
            return {"locations": [{"merchantLocationKey": "LOC1"}]}
        if method == "GET" and path.startswith(
                "/sell/account/v1/fulfillment_policy"):
            if "?" in path:
                return {"fulfillmentPolicies": [OTHER_POLICY]}
            if self.policy_mode == "missing":
                raise m._EbayApiError("not found", status=404,
                                      error_id="20404")
            return FLAT_POLICY
        if method == "PUT" and path.startswith(
                "/sell/inventory/v1/inventory_item/"):
            self.inventory_payload = payload
            return {}
        if (method, path) == ("POST", "/sell/inventory/v1/offer"):
            if self.offer_error is not None:
                raise self.offer_error
            self.offer_payload = payload
            return {"offerId": "OFFER1"}
        if method == "POST" and path.startswith("/sell/inventory/v1/offer/"):
            if self.publish_error is not None:
                raise self.publish_error
            return {"listingId": "999"}
        if method == "DELETE" and path.startswith("/sell/inventory/v1/offer/"):
            self.deletes.append(path)
            return {}
        if method == "GET" and path.startswith("/sell/inventory/v1/offer?sku="):
            if self.reused_offer is not None:
                return {"offers": [self.reused_offer]}
            return {"offers": []}
        if method == "GET" and path.startswith("/sell/listing/v1/item/"):
            return {}
        raise AssertionError(f"unexpected eBay call {method} {path}")

    def _run(self, fields=None, package=None, draft=None, drop=()):
        f = dict(self.fields)
        if fields:
            f.update(fields)
        for k in drop:
            f.pop(k, None)
        if package is not None:
            f["package"] = package
        d = dict(draft) if draft is not None else dict(self.draft)
        with mock.patch.object(m, "_ebay_access_token",
                               return_value="tok"), \
                mock.patch.object(m, "_ebay_upload_picture",
                                  side_effect=self._count_upload), \
                mock.patch.object(m, "_category_aspects",
                                  return_value=self.aspects), \
                mock.patch.object(m, "_category_conditions",
                                  return_value=self.conditions), \
                mock.patch.object(m, "_ebay_call",
                                  side_effect=self._fake_call), \
                mock.patch.object(m, "_save_draft",
                                  side_effect=self._capture_save), \
                mock.patch.object(m, "EBAY_MERCHANT_LOCATION_KEY", ""):
            return m._do_publish(d, f)

    def _count_upload(self, *args, **kwargs):
        self.uploads += 1
        return "https://i.ebayimg.com/fake.jpg"

    def _capture_save(self, d):
        self.saved.append(dict(d))
        return d

    def test_publish_quantity_3_sends_3_both_places(self):
        self._run()
        self.assertEqual(
            self.inventory_payload["availability"]
            ["shipToLocationAvailability"]["quantity"], 3)
        self.assertEqual(self.offer_payload["availableQuantity"], 3)
        final = self.saved[-1]
        self.assertEqual(final["ebay_offer_quantity"], 3)
        self.assertEqual(final["quantity"], "3")
        self.assertEqual(final["ebay_listing_id"], "999")

    def test_publish_defaults_to_1_when_untouched(self):
        self._run(drop=("quantity",))
        self.assertEqual(
            self.inventory_payload["availability"]
            ["shipToLocationAvailability"]["quantity"], 1)
        self.assertEqual(self.offer_payload["availableQuantity"], 1)

    def test_publish_invalid_quantity_blocked_plainly(self):
        with self.assertRaises(m._PublishValidationError) as cm:
            self._run(fields={"quantity": None,
                              "quantity_error":
                                  "Enter a whole number of 1 or more for "
                                  "the quantity."})
        self.assertEqual(
            str(cm.exception),
            "Enter a whole number of 1 or more for the quantity.")
        self.assertEqual(self.uploads, 0)
        self.assertIsNone(self.inventory_payload)

    def test_publish_bad_quantity_value_blocked_plainly(self):
        with self.assertRaises(m._PublishValidationError) as cm:
            self._run(fields={"quantity": 0})
        self.assertIn("Set a quantity of 1 or more before listing.",
                      str(cm.exception))
        self.assertIsNone(self.inventory_payload)

    def test_quantity_change_discards_saved_offer(self):
        draft = dict(self.draft)
        draft["ebay_offer_id"] = "OLD"
        draft["ebay_offer_quantity"] = 1
        draft["ebay_offer_fulfillment_id"] = "111"
        draft["ebay_offer_condition"] = "NEW"
        self._run(draft=draft)
        self.assertEqual(self.deletes, ["/sell/inventory/v1/offer/OLD"])
        self.assertEqual(self.offer_payload["availableQuantity"], 3)
        final = self.saved[-1]
        self.assertEqual(final["ebay_offer_id"], "OFFER1")
        self.assertEqual(final["ebay_offer_quantity"], 3)

    def test_same_quantity_reuses_saved_offer(self):
        draft = dict(self.draft)
        draft["ebay_offer_id"] = "OLD"
        draft["ebay_offer_quantity"] = 3
        draft["ebay_offer_fulfillment_id"] = "111"
        draft["ebay_offer_condition"] = "NEW"
        self._run(draft=draft)
        self.assertEqual(self.deletes, [])
        self.assertIsNone(self.offer_payload)
        final = self.saved[-1]
        self.assertEqual(final["ebay_offer_id"], "OLD")

    def test_reused_offer_records_quantity_from_ebay(self):
        self.offer_error = RuntimeError(
            "an offer already exists for this SKU (error 25002)")
        self.reused_offer = {
            "offerId": "REUSED", "availableQuantity": 5,
            "listingPolicies": {"fulfillmentPolicyId": "111"}}
        self._run()
        final = self.saved[-1]
        self.assertEqual(final["ebay_offer_id"], "REUSED")
        # The listing went live with the reused offer's 5, not the
        # requested 3 — the draft records the live number, so a later
        # change is judged against the truth.
        self.assertEqual(final["ebay_offer_quantity"], 5)
        self.assertEqual(final["quantity"], "5")


class PublishEndpointQuantityTest(unittest.TestCase):
    """The single-publish endpoint parses the quantity into f."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig = {}
        for attr, sub in (("DRAFTS_DIR", "drafts"), ("PHOTOS_DIR", "photos")):
            self.orig[attr] = getattr(m, attr)
            p = os.path.join(self.tmp, sub)
            os.makedirs(p, exist_ok=True)
            setattr(m, attr, p)
        self.client = m.app.test_client()
        self.captured = None

    def tearDown(self):
        for attr, val in self.orig.items():
            setattr(m, attr, val)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _post(self, body):
        draft = {"id": "d1", "title": "Test", "photos": ["p.jpg"],
                 "price": "10.00"}
        def fake_publish(d, f, ship_from=None):
            self.captured = dict(f)
            return "999", "https://www.ebay.com/itm/999"
        with mock.patch.object(m, "_load_draft", return_value=draft), \
                mock.patch.object(m, "_do_publish",
                                  side_effect=fake_publish):
            return self.client.post("/api/ebay/publish", json=body)

    def test_endpoint_passes_quantity(self):
        r = self._post({"draft_id": "d1", "quantity": "3"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.captured["quantity"], 3)
        self.assertIsNone(self.captured.get("quantity_error"))

    def test_endpoint_invalid_quantity_rides_as_error(self):
        r = self._post({"draft_id": "d1", "quantity": "0"})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(self.captured["quantity"])
        self.assertEqual(
            self.captured["quantity_error"],
            "Enter a whole number of 1 or more for the quantity.")

    def test_endpoint_defaults_to_1_when_untouched(self):
        r = self._post({"draft_id": "d1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.captured["quantity"], 1)
        self.assertIsNone(self.captured.get("quantity_error"))


class BatchQuantityTest(unittest.TestCase):
    """Batch publish carries each draft's own quantity."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig = {}
        for attr, sub in (("DRAFTS_DIR", "drafts"), ("PHOTOS_DIR", "photos")):
            self.orig[attr] = getattr(m, attr)
            p = os.path.join(self.tmp, sub)
            os.makedirs(p, exist_ok=True)
            setattr(m, attr, p)
        self.client = m.app.test_client()
        self.captured = []

    def tearDown(self):
        for attr, val in self.orig.items():
            setattr(m, attr, val)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_batch(self, drafts):
        for d in drafts:
            m._save_draft(dict(d))
        def fake_publish(d, f, ship_from=None):
            self.captured.append((d["id"], dict(f)))
            return "999", "https://www.ebay.com/itm/999"
        with mock.patch.object(m, "_do_publish",
                               side_effect=fake_publish):
            return self.client.post(
                "/api/ebay/batch-publish",
                json={"draft_ids": [d["id"] for d in drafts],
                      "category_id": "123"})

    def test_batch_carries_each_drafts_quantity(self):
        r = self._run_batch([
            {"id": "d1", "title": "One", "photos": ["p.jpg"],
             "price": "10.00", "quantity": "3"},
            {"id": "d2", "title": "Two", "photos": ["p.jpg"],
             "price": "12.00"},
        ])
        body = r.get_json()
        self.assertEqual([x["ok"] for x in body["results"]], [True, True])
        by_id = {d: f for d, f in self.captured}
        self.assertEqual(by_id["d1"]["quantity"], 3)
        self.assertEqual(by_id["d2"]["quantity"], 1)

    def test_batch_bad_quantity_skips_that_draft_only(self):
        r = self._run_batch([
            {"id": "d1", "title": "Bad", "photos": ["p.jpg"],
             "price": "10.00", "quantity": "0"},
            {"id": "d2", "title": "Good", "photos": ["p.jpg"],
             "price": "12.00", "quantity": "2"},
        ])
        body = r.get_json()
        self.assertFalse(body["results"][0]["ok"])
        self.assertEqual(body["results"][0]["error"],
                         "Enter a whole number of 1 or more for the "
                         "quantity.")
        self.assertTrue(body["results"][1]["ok"])
        self.assertEqual([d for d, _ in self.captured], ["d2"])
        self.assertEqual(self.captured[0][1]["quantity"], 2)


class ReviseQuantityTest(unittest.TestCase):
    """The live quantity update, eBay mocked (v57 revise pattern)."""

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

    def _listed_draft(self, **extra):
        d = {"id": "d1", "title": "Test item",
             "ebay_listing_id": "123", "ebay_offer_id": "off1",
             "ebay_listing_url": "https://www.ebay.com/itm/123",
             "ebay_sku": "lad1", "quantity": "1",
             "ebay_offer_quantity": 1}
        d.update(extra)
        return m._save_draft(d)

    def _offer_body(self, qty, **over):
        body = {"offerId": "off1", "sku": "lad1",
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
        body.update(over)
        return body

    def _item_body(self, qty):
        return {"product": {"title": "Test item",
                            "description": "A thing.",
                            "imageUrls": ["https://i.ebayimg.com/1.jpg"]},
                "availability": {"shipToLocationAvailability":
                                 {"quantity": qty}}}

    def _fake_ebay(self, offer_qty=1, item_qty=1, put_applies=True,
                   offer_by_id_404=False, offer_missing=False,
                   offer_fields=None):
        """A fake eBay holding one item + one offer. GET returns the
        current quantities; PUT replaces them (unless put_applies is
        False, which simulates eBay ignoring the update). Records
        every call as (method, path)."""
        state = {"offer": offer_qty, "item": item_qty}
        calls = []
        puts = {}

        def fake(method, path, token, payload=None):
            calls.append((method, path))
            if method == "GET" and path == "/sell/inventory/v1/offer/off1":
                if offer_by_id_404 or offer_missing:
                    raise m._EbayApiError("not found", status=404,
                                          error_id="25001")
                body = self._offer_body(state["offer"])
                if offer_fields is not None:
                    body = dict(offer_fields)
                    body.setdefault("availableQuantity", state["offer"])
                return body
            if method == "GET" and path.startswith(
                    "/sell/inventory/v1/offer?sku="):
                if offer_missing:
                    return {"offers": []}
                body = self._offer_body(state["offer"])
                if offer_fields is not None:
                    body = dict(offer_fields)
                    body.setdefault("availableQuantity", state["offer"])
                return {"offers": [body]}
            if method == "GET" and "/inventory_item/" in path:
                return self._item_body(state["item"])
            if method == "PUT" and "/inventory_item/" in path:
                puts["item"] = payload
                if put_applies:
                    state["item"] = (
                        (payload.get("availability") or {})
                        .get("shipToLocationAvailability") or {}
                    ).get("quantity")
                return {}
            if method == "PUT" and "/offer/" in path:
                puts["offer"] = payload
                if put_applies:
                    state["offer"] = payload.get("availableQuantity")
                return {}
            raise AssertionError("unexpected eBay call %s %s" % (method, path))

        return fake, calls, puts, state

    def _preview(self, ebay_fake, quantity="3"):
        with mock.patch.object(m, "_ebay_access_token",
                               return_value="tok"), \
                mock.patch.object(m, "_ebay_call",
                                  side_effect=ebay_fake):
            return self.client.get(
                "/api/drafts/d1/revise-quantity?quantity=" + quantity)

    def _execute(self, ebay_fake, quantity=3, confirmed=True):
        with mock.patch.object(m, "_ebay_access_token",
                               return_value="tok"), \
                mock.patch.object(m, "_ebay_call",
                                  side_effect=ebay_fake):
            return self.client.post(
                "/api/drafts/d1/revise-quantity",
                json={"confirmed": confirmed, "quantity": quantity})

    # ---- eligibility ----

    def test_preview_missing_draft_404(self):
        resp = self._preview(None)
        self.assertEqual(resp.status_code, 404)
        self.assertIn("Draft not found", resp.get_json()["error"])

    def test_preview_unlisted_draft_409(self):
        m._save_draft({"id": "d1", "title": "T"})
        resp = self._preview(None)
        self.assertEqual(resp.status_code, 409)
        self.assertIn("not published by the app", resp.get_json()["error"])

    def test_preview_listed_without_offer_id_409(self):
        m._save_draft({"id": "d1", "title": "T", "ebay_listing_id": "123"})
        resp = self._preview(None)
        self.assertEqual(resp.status_code, 409)

    # ---- preview plans ----

    def test_preview_change_names_live_and_new(self):
        self._listed_draft()
        fake, _, _, _ = self._fake_ebay(offer_qty=1)
        resp = self._preview(fake, "3")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["live_quantity"], 1)
        self.assertEqual(body["requested_quantity"], 3)
        self.assertFalse(body["unchanged"])
        self.assertEqual(body["changes"],
                         ["Quantity changes from 1 to 3."])

    def test_preview_unchanged(self):
        self._listed_draft()
        fake, _, _, _ = self._fake_ebay(offer_qty=3)
        resp = self._preview(fake, "3")
        body = resp.get_json()
        self.assertTrue(body["unchanged"])
        self.assertEqual(body["changes"],
                         ["The live listing already has a quantity of 3."])

    def test_preview_invalid_quantity_400(self):
        self._listed_draft()
        fake, _, _, _ = self._fake_ebay()
        resp = self._preview(fake, "0")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("whole number of 1 or more",
                      resp.get_json()["error"])

    def test_preview_offer_gone_502(self):
        self._listed_draft()
        fake, _, _, _ = self._fake_ebay(offer_missing=True)
        resp = self._preview(fake, "3")
        self.assertEqual(resp.status_code, 502)
        self.assertIn("no longer has this listing's offer",
                      resp.get_json()["error"])

    def test_preview_falls_back_to_sku_lookup(self):
        self._listed_draft()
        fake, calls, _, _ = self._fake_ebay(offer_by_id_404=True)
        resp = self._preview(fake, "3")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["live_quantity"], 1)
        self.assertIn(("GET", "/sell/inventory/v1/offer?sku=lad1"), calls)

    # ---- execute ----

    def test_execute_updates_both_levels_item_first(self):
        self._listed_draft()
        fake, calls, puts, _ = self._fake_ebay(offer_qty=1, item_qty=1)
        resp = self._execute(fake, 3)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["live_quantity"], 3)
        self.assertEqual(body["message"],
                         "The live listing now has a quantity of 3.")
        item_put = calls.index(
            ("PUT", "/sell/inventory/v1/inventory_item/lad1"))
        offer_put = calls.index(("PUT", "/sell/inventory/v1/offer/off1"))
        self.assertLess(item_put, offer_put)
        self.assertEqual(
            puts["item"]["availability"]["shipToLocationAvailability"]
            ["quantity"], 3)
        # The offer PUT is a full replacement: the live offer's fields
        # ride along with only the quantity swapped, and read-only
        # fields (status) never go out.
        self.assertEqual(puts["offer"]["availableQuantity"], 3)
        self.assertEqual(puts["offer"]["listingDescription"], "A thing.")
        self.assertEqual(puts["offer"]["pricingSummary"],
                         {"price": {"currency": "USD", "value": "10.00"}})
        self.assertEqual(puts["offer"]["listingPolicies"],
                         {"fulfillmentPolicyId": "111",
                          "paymentPolicyId": "222",
                          "returnPolicyId": "333"})
        self.assertNotIn("status", puts["offer"])
        self.assertNotIn("listingId", puts["offer"])
        # The draft now agrees with the live listing.
        d = m._load_draft("d1")
        self.assertEqual(d["quantity"], "3")
        self.assertEqual(d["ebay_offer_quantity"], 3)

    def test_execute_unchanged_sends_nothing(self):
        self._listed_draft()
        fake, calls, _, _ = self._fake_ebay(offer_qty=3, item_qty=3)
        resp = self._execute(fake, 3)
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["unchanged"])
        self.assertEqual([c for c in calls if c[0] == "PUT"], [])

    def test_execute_verify_mismatch_502(self):
        self._listed_draft()
        fake, _, _, _ = self._fake_ebay(put_applies=False)
        resp = self._execute(fake, 3)
        self.assertEqual(resp.status_code, 502)
        self.assertIn("did not show the new quantity",
                      resp.get_json()["error"])

    def test_execute_unconfirmed_400(self):
        self._listed_draft()
        fake, calls, _, _ = self._fake_ebay()
        resp = self._execute(fake, 3, confirmed=False)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Confirm the quantity update first.",
                      resp.get_json()["error"])
        self.assertEqual([c for c in calls if c[0] == "PUT"], [])

    def test_execute_offer_body_missing_field_fails_before_send(self):
        self._listed_draft()
        fields = self._offer_body(1)
        del fields["listingDescription"]
        fake, calls, _, _ = self._fake_ebay(offer_fields=fields)
        resp = self._execute(fake, 3)
        self.assertEqual(resp.status_code, 502)
        self.assertIn("did not return the full listing",
                      resp.get_json()["error"])
        self.assertEqual([c for c in calls if c[0] == "PUT"], [])


if __name__ == "__main__":
    unittest.main()
