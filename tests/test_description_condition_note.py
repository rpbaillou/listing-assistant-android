"""apk-v65 tests: the Condition details ride in the Description.

Before v65 the Condition details lived only in eBay's
conditionDescription field, so when he copied the description to
Poshmark or Mercari the condition information never travelled with
it. v65 appends the details to the Description field itself, live,
as a trailing "Condition: <details>" note (app.js syncConditionNote),
so the note is visible in the field, copyable with Copy description,
and published to eBay that way. A server backstop
(_with_condition_note, used in _do_publish) covers a draft published
without ever opening the listing page, such as a batch publish of a
pre-v65 draft. The inventory item's conditionDescription still
carries the details separately, exactly as before, and the AI rule
(never guess at condition in the description) still stands — the note
is the seller's own words, never an AI guess.
"""

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


class WithConditionNoteTest(unittest.TestCase):
    def test_appends_the_note(self):
        self.assertEqual(
            m._with_condition_note("A nice doll.", "small chips on the base"),
            "A nice doll.\n\nCondition: small chips on the base")

    def test_empty_description_is_just_the_note(self):
        self.assertEqual(
            m._with_condition_note("", "small chips on the base"),
            "Condition: small chips on the base")

    def test_empty_details_leaves_the_description_alone(self):
        self.assertEqual(
            m._with_condition_note("A nice doll.", ""), "A nice doll.")
        self.assertEqual(
            m._with_condition_note("A nice doll.", "   "), "A nice doll.")

    def test_note_already_there_is_never_duplicated(self):
        desc = "A nice doll.\n\nCondition: small chips on the base"
        self.assertEqual(
            m._with_condition_note(desc, "small chips on the base"), desc)

    def test_details_are_trimmed(self):
        self.assertEqual(
            m._with_condition_note("A nice doll.", "  chips  "),
            "A nice doll.\n\nCondition: chips")


FLAT_POLICY = {"fulfillmentPolicyId": "111", "name": "Dummy Flat",
               "marketplaceId": "EBAY_US",
               "shippingOptions": [{"optionType": "DOMESTIC",
                                    "costType": "FLAT_RATE",
                                    "shippingServices": [{"shippingServiceCode": "USPSParcel"}]}]}


class PublishNoteTest(unittest.TestCase):
    """_do_publish with eBay fully mocked (PublishQuantityTest pattern)."""

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
            "quantity": 1,
            "condition": "NEW_OTHER",
            "condition_details": "small chips on the base",
            "category_id": "123",
            "fulfillment_id": "111",
            "payment_id": "222",
            "return_id": "333",
            "attributes": {},
            "best_offer_terms": None,
        }
        self.inventory_payload = None
        self.offer_payload = None
        self.saved = []

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
                return {"fulfillmentPolicies": [FLAT_POLICY]}
            return FLAT_POLICY
        if method == "PUT" and path.startswith(
                "/sell/inventory/v1/inventory_item/"):
            self.inventory_payload = payload
            return {}
        if (method, path) == ("POST", "/sell/inventory/v1/offer"):
            self.offer_payload = payload
            return {"offerId": "OFFER1"}
        if method == "POST" and path.startswith("/sell/inventory/v1/offer/"):
            return {"listingId": "999"}
        if method == "DELETE" and path.startswith("/sell/inventory/v1/offer/"):
            return {}
        if method == "GET" and path.startswith("/sell/inventory/v1/offer?sku="):
            return {"offers": []}
        if method == "GET" and path.startswith("/sell/listing/v1/item/"):
            return {}
        raise AssertionError(f"unexpected eBay call {method} {path}")

    def _run(self, fields=None, draft=None):
        f = dict(self.fields)
        if fields:
            f.update(fields)
        d = dict(draft) if draft is not None else dict(self.draft)
        with mock.patch.object(m, "_ebay_access_token",
                               return_value="tok"), \
                mock.patch.object(m, "_ebay_upload_picture",
                                  return_value="https://i.ebayimg.com/fake.jpg"), \
                mock.patch.object(m, "_category_aspects",
                                  return_value=(None, None)), \
                mock.patch.object(m, "_category_conditions",
                                  return_value=(None, None)), \
                mock.patch.object(m, "_ebay_call",
                                  side_effect=self._fake_call), \
                mock.patch.object(m, "_save_draft",
                                  side_effect=lambda d: self.saved.append(dict(d)) or d), \
                mock.patch.object(m, "EBAY_MERCHANT_LOCATION_KEY", ""):
            return m._do_publish(d, f)

    def test_publish_carries_the_note_on_both_levels(self):
        self._run()
        item_desc = self.inventory_payload["product"]["description"]
        self.assertIn("Condition: small chips on the base", item_desc)
        self.assertTrue(item_desc.startswith("A test item."))
        offer_desc = self.offer_payload["listingDescription"]
        self.assertIn("Condition: small chips on the base", offer_desc)
        self.assertTrue(offer_desc.startswith("A test item."))

    def test_publish_does_not_duplicate_a_note_already_in_the_field(self):
        self._run(fields={
            "description": "A test item.\n\nCondition: small chips on the base"})
        item_desc = self.inventory_payload["product"]["description"]
        self.assertEqual(item_desc.count("Condition: small chips on the base"), 1)
        offer_desc = self.offer_payload["listingDescription"]
        self.assertEqual(offer_desc.count("Condition: small chips on the base"), 1)

    def test_publish_without_details_leaves_the_description_alone(self):
        self._run(fields={"condition_details": ""})
        item_desc = self.inventory_payload["product"]["description"]
        self.assertEqual(item_desc, "A test item.")
        self.assertNotIn("Condition:", item_desc)

    def test_publish_still_sends_condition_description_separately(self):
        self._run()
        self.assertEqual(
            self.inventory_payload["conditionDescription"],
            "small chips on the base")


class BatchNoteTest(unittest.TestCase):
    """Batch publish passes the draft's description and details through
    to _do_publish, where the backstop appends the note."""

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
        self.client = m.app.test_client()
        self.captured = []

    def tearDown(self):
        for attr, val in self.orig.items():
            setattr(m, attr, val)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_batch_passes_description_and_details_to_publish(self):
        draft = {"id": "d1", "title": "Test", "photos": ["p.jpg"],
                 "price": "10.00", "description": "A test item.",
                 "condition": "NEW_OTHER",
                 "condition_details": "small chips on the base",
                 "quantity": "1"}
        m._save_draft(dict(draft))

        def fake_publish(d, f, ship_from=None):
            self.captured.append((d["id"], dict(f)))
            return "999", "https://www.ebay.com/itm/999"

        with mock.patch.object(m, "_do_publish",
                               side_effect=fake_publish):
            r = self.client.post(
                "/api/ebay/batch-publish",
                json={"draft_ids": ["d1"], "category_id": "123",
                      "fulfillment_policy_id": "111"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self.captured), 1)
        _draft_id, form = self.captured[0]
        self.assertEqual(form["description"], "A test item.")
        self.assertEqual(form["condition_details"], "small chips on the base")
        # The backstop in _do_publish turns exactly these two into the note.
        self.assertEqual(
            m._with_condition_note(form["description"],
                                   form["condition_details"]),
            "A test item.\n\nCondition: small chips on the base")


if __name__ == "__main__":
    unittest.main()
