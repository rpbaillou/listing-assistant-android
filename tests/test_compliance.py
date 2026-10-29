"""apk-v35 tests: pre-publish compliance check + condition fix.

v34's live publish got past location, policies, photos, and inventory,
then failed with eBay error 25021 ("provided condition id is invalid
for this category") — and the app mislabeled it as a category error,
because _plain_ebay_error checked "categor" before "condition". v35
fixes the wording, adds 25021 to the offer-discard set (a reused offer
carries its old condition snapshot), offers only the conditions the
category allows (Metadata API), and — the headline change — checks
the whole draft against eBay's verified rules BEFORE any eBay call,
reporting ALL problems at once instead of one at a time.

PRIVACY: the repo is PUBLIC. Every policy ID, category ID, and address
here is obviously dummy. No real policy ID, address, or secret appears
in code, fixtures, or test output.

eBay is always mocked; no test here reaches the network. Storage dirs
are monkeypatched to a temp dir per test.
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


# Dummy policies only — never real policy IDs.
FLAT_POLICY = {"fulfillmentPolicyId": "111", "name": "Dummy Flat",
               "marketplaceId": "EBAY_US",
               "shippingOptions": [{"optionType": "DOMESTIC",
                                    "costType": "FLAT_RATE"}]}
CALC_POLICY = {"fulfillmentPolicyId": "111", "name": "Dummy Calculated",
               "marketplaceId": "EBAY_US",
               "shippingOptions": [{"optionType": "DOMESTIC",
                                    "costType": "CALCULATED"}]}
OTHER_POLICY = {"fulfillmentPolicyId": "999", "name": "Dummy Other",
                "marketplaceId": "EBAY_US",
                "shippingOptions": [{"optionType": "DOMESTIC",
                                     "costType": "FLAT_RATE"}]}

DUMMY_PACKAGE = {"weight_value": "4", "weight_unit": "OUNCE",
                 "length": "12", "width": "9", "height": "1"}
UNIT_ONLY = {"weight_value": "", "weight_unit": "OUNCE",
             "length": "", "width": "", "height": ""}
PARTIAL_PACKAGE = {"weight_value": "4", "weight_unit": "OUNCE",
                   "length": "12", "width": "", "height": ""}


def _fake_resp(status, json_body=None):
    r = mock.Mock()
    r.status_code = status
    r.content = b""
    r.text = ""
    r.headers = {}
    if json_body is not None:
        r.json = mock.Mock(return_value=json_body)
    else:
        r.json = mock.Mock(side_effect=ValueError("no json"))
    return r


class ComplianceTest(unittest.TestCase):
    """_do_publish's v35 readiness check, eBay fully mocked."""

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
                # List fallback: the stored ID is absent (stale).
                return {"fulfillmentPolicies": [OTHER_POLICY]}
            if self.policy_mode == "missing":
                raise m._EbayApiError("not found", status=404,
                                      error_id="20404")
            if self.policy_mode == "calculated":
                return CALC_POLICY
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
            return {"offers": []}
        if method == "GET" and path.startswith("/sell/listing/v1/item/"):
            return {}
        raise AssertionError(f"unexpected eBay call {method} {path}")

    def _run(self, fields=None, package=None, draft=None):
        f = dict(self.fields)
        if fields:
            f.update(fields)
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

    def test_all_problems_at_once(self):
        """Empty title + bad price + bad condition + missing aspect +
        calculated-no-package -> ONE error listing every problem, and
        nothing reached eBay (no uploads)."""
        self.policy_mode = "calculated"
        self.aspects = ([{"name": "Material", "values": []}], [])
        with self.assertRaises(m._PublishValidationError) as ctx:
            self._run(fields={"title": "", "price_val": None,
                              "price_error": "Set a price above $0 before listing.",
                              "condition": "BOGUS"},
                      package=None)
        msg = str(ctx.exception)
        self.assertIn("Fix these before listing:", msg)
        self.assertIn("The title is empty", msg)
        self.assertIn("Set a price above $0", msg)
        self.assertIn("Pick a condition from the list.", msg)
        self.assertIn("Material", msg)
        self.assertIn("charges by weight", msg)
        self.assertEqual(self.uploads, 0)

    def test_single_problem_stands_alone(self):
        """One problem keeps the exact legacy wording, no header."""
        with self.assertRaises(m._PublishValidationError) as ctx:
            self._run(fields={"title": ""})
        self.assertEqual(str(ctx.exception),
                         "The title is empty. Go back and write a title first.")
        with self.assertRaises(m._PublishValidationError) as ctx:
            self._run(fields={"condition": ""})
        self.assertEqual(str(ctx.exception), "Pick a condition first.")
        with self.assertRaises(m._PublishValidationError) as ctx:
            self._run(fields={"category_id": ""})
        self.assertEqual(str(ctx.exception),
                         "Pick an eBay category first (tap Suggest).")

    def test_condition_not_allowed_for_category(self):
        """The Metadata set is known and the draft's condition is not
        in it: the problem names the allowed choices."""
        self.conditions = (["NEW", "USED_EXCELLENT"], True)
        with self.assertRaises(m._PublishValidationError) as ctx:
            self._run(fields={"condition": "USED_VERY_GOOD"})
        msg = str(ctx.exception)
        self.assertIn("isn't allowed for this category", msg)
        self.assertIn("New, Used", msg)
        self.assertEqual(self.uploads, 0)

    def test_condition_allowed_for_category_passes(self):
        self.conditions = (["NEW", "USED_EXCELLENT"], True)
        listing_id, _url = self._run(fields={"condition": "USED_EXCELLENT"})
        self.assertEqual(listing_id, "999")

    def test_condition_check_skipped_when_fetch_fails(self):
        """Fetch failure is a note, never a block: eBay decides."""
        self.conditions = (None, None)
        listing_id, _url = self._run(fields={"condition": "USED_VERY_GOOD"})
        self.assertEqual(listing_id, "999")

    def test_no_photos_is_a_problem(self):
        with self.assertRaises(m._PublishValidationError) as ctx:
            self._run(draft={"id": "d1", "photos": []})
        self.assertEqual(str(ctx.exception), "This draft has no photos.")

    def test_pick_signal_carries_problems(self):
        """Stale policy + bad condition: the pick screen gets the
        condition problem too, instead of surfacing one wall at a
        time."""
        self.policy_mode = "missing"
        with self.assertRaises(m._NeedsFulfillmentPolicyChoice) as ctx:
            self._run(fields={"condition": ""})
        self.assertIn("Pick a condition first.", ctx.exception.problems)

    def test_25021_publish_discards_offer(self):
        """v35: a 25021 at publish discards the stale offer (DELETE +
        local ID cleared), because the offer snapshot carries the bad
        condition. The guard never discards a live listing."""
        self.publish_error = m._EbayApiError(
            "That condition choice isn't allowed for this category. "
            "Pick a different condition. (error 25021). Details: The "
            "provided condition id is invalid for this category.",
            status=400, error_id="25021")
        draft = dict(self.draft, ebay_offer_id="OFFER1",
                     ebay_offer_condition="USED_VERY_GOOD")
        with self.assertRaises(m._EbayApiError):
            self._run(fields={"condition": "USED_VERY_GOOD"}, draft=draft)
        self.assertEqual(self.deletes,
                         ["/sell/inventory/v1/offer/OFFER1"])
        cleared = [s for s in self.saved if "ebay_offer_id" not in s]
        self.assertTrue(cleared, "discard must save the draft cleared")

    def test_discard_never_when_live(self):
        draft = dict(self.draft, ebay_offer_id="OFFER1",
                     ebay_listing_id="999")
        self.assertFalse(m._discard_offer(draft, "tok"))
        self.assertEqual(self.deletes, [])

    def test_condition_change_discards_offer(self):
        """Recorded condition differs from the current pick: the old
        offer is deleted and a fresh one created carrying the new
        condition (recorded on creation)."""
        draft = dict(self.draft, ebay_offer_id="OFFER1",
                     ebay_offer_condition="NEW",
                     ebay_offer_fulfillment_id="111")
        listing_id, _url = self._run(fields={"condition": "USED_EXCELLENT"},
                                     draft=draft)
        self.assertEqual(listing_id, "999")
        self.assertEqual(self.deletes,
                         ["/sell/inventory/v1/offer/OFFER1"])
        self.assertEqual(self.offer_payload["listingPolicies"]
                         ["fulfillmentPolicyId"], "111")
        # Condition lives on the inventory item, not the offer.
        self.assertEqual(self.inventory_payload["condition"],
                         "USED_EXCELLENT")
        self.assertEqual(self.saved[-1]["ebay_offer_condition"],
                         "USED_EXCELLENT")

    def test_same_condition_reuses_offer(self):
        draft = dict(self.draft, ebay_offer_id="OFFER1",
                     ebay_offer_condition="NEW",
                     ebay_offer_fulfillment_id="111")
        listing_id, _url = self._run(draft=draft)
        self.assertEqual(listing_id, "999")
        self.assertEqual(self.deletes, [])
        self.assertIsNone(self.offer_payload)

    def test_condition_recorded_on_creation(self):
        listing_id, _url = self._run(fields={"condition": "USED_GOOD"})
        self.assertEqual(listing_id, "999")
        self.assertEqual(self.saved[-1]["ebay_offer_condition"], "USED_GOOD")

    def test_flat_with_unit_only_sends_no_package(self):
        """v35 package-presence fix: a lone weight-unit default (the
        select's OUNCE) is not a package. Flat + unit-only -> no
        package block sent, no package problem."""
        listing_id, _url = self._run(package=dict(UNIT_ONLY))
        self.assertEqual(listing_id, "999")
        self.assertNotIn("packageWeightAndSize", self.inventory_payload)

    def test_calculated_with_unit_only_asks_for_package(self):
        self.policy_mode = "calculated"
        with self.assertRaises(m._PublishValidationError) as ctx:
            self._run(package=dict(UNIT_ONLY))
        self.assertIn("charges by weight", str(ctx.exception))

    def test_calculated_with_partial_package_asks_for_all_fields(self):
        self.policy_mode = "calculated"
        with self.assertRaises(m._PublishValidationError) as ctx:
            self._run(package=dict(PARTIAL_PACKAGE))
        self.assertIn("all three dimensions", str(ctx.exception))

    def test_package_has_content(self):
        self.assertFalse(m._package_has_content(dict(UNIT_ONLY)))
        self.assertFalse(m._package_has_content({}))
        self.assertTrue(m._package_has_content(dict(DUMMY_PACKAGE)))
        self.assertTrue(m._package_has_content(
            {"weight_value": "4", "weight_unit": "OUNCE",
             "length": "", "width": "", "height": ""}))

    def test_draft_package_unit_only_is_none(self):
        d = {"package_weight_unit": "OUNCE"}
        self.assertIsNone(m._draft_package(d))
        d2 = dict(DUMMY_PACKAGE)
        d2 = {"package_weight_value": d2["weight_value"],
              "package_weight_unit": d2["weight_unit"],
              "package_length": d2["length"],
              "package_width": d2["width"],
              "package_height": d2["height"]}
        self.assertEqual(m._draft_package(d2), dict(DUMMY_PACKAGE))


class ErrorWordingTest(unittest.TestCase):
    """v35: _plain_ebay_error checks condition BEFORE category.

    _plain_ebay_error takes eBay's response object (the shape
    _ebay_call passes it), so these tests build that shape: a Mock
    with .json() returning the errors container and .status_code.
    """

    def _resp(self, message, error_id):
        r = mock.Mock()
        r.status_code = 400
        r.json = mock.Mock(return_value={
            "errors": [{"message": message, "errorId": error_id}]})
        return r

    def test_25021_maps_to_condition_not_category(self):
        msg = ("The eBay listing associated with the inventory item, "
               "or the unpublished offer has invalid item condition "
               "information. The provided condition id is invalid "
               "for this category.")
        out = m._plain_ebay_error(self._resp(msg, "25021"))
        self.assertIn("That condition choice isn't allowed for this "
                      "category. Pick a different condition.", out)
        self.assertIn("25021", out)
        self.assertNotIn("Suggest", out)

    def test_condition_without_25021_keeps_generic_condition_message(self):
        out = m._plain_ebay_error(
            self._resp("condition information is invalid", "25022"))
        self.assertIn("condition detail", out)
        self.assertNotIn("Suggest", out)

    def test_category_message_still_fires_without_condition(self):
        out = m._plain_ebay_error(
            self._resp("The category id is invalid.", "25003"))
        self.assertIn("category", out)
        self.assertIn("Suggest", out)


class CategoryConditionsTest(unittest.TestCase):
    """Metadata get_item_condition_policies mapping + endpoint."""

    def setUp(self):
        m._conditions_cache.clear()

    def tearDown(self):
        m._conditions_cache.clear()

    def _meta_resp(self, body):
        return _fake_resp(200, body)

    def test_maps_numeric_ids_to_enums(self):
        body = {"itemConditionPolicies": [
            {"categoryId": "123", "itemConditionRequired": True,
             "itemConditions": [{"conditionId": 3000},
                                {"conditionId": 4000},
                                {"conditionId": 1000}]}]}
        with mock.patch.object(m, "_app_token", return_value="tok"), \
                mock.patch.object(m.requests, "get",
                                  return_value=self._meta_resp(body)) as get:
            allowed, required = m._category_conditions("123")
        self.assertEqual(allowed,
                         ["USED_EXCELLENT", "USED_VERY_GOOD", "NEW"])
        self.assertTrue(required)
        _url, kwargs = get.call_args[0][0], get.call_args[1]
        self.assertIn("categoryIds:123", kwargs["params"]["filter"])

    def test_wrong_category_entry_ignored(self):
        body = {"itemConditionPolicies": [
            {"categoryId": "999", "itemConditionRequired": True,
             "itemConditions": [{"conditionId": 1000}]}]}
        with mock.patch.object(m, "_app_token", return_value="tok"), \
                mock.patch.object(m.requests, "get",
                                  return_value=self._meta_resp(body)):
            self.assertEqual(m._category_conditions("123"), (None, None))

    def test_fetch_failure_is_fallback_not_block(self):
        with mock.patch.object(m, "_app_token", return_value="tok"), \
                mock.patch.object(m.requests, "get",
                                  return_value=_fake_resp(500)):
            self.assertEqual(m._category_conditions("123"), (None, None))

    def test_endpoint_returns_mapped_list(self):
        with mock.patch.object(m, "_category_conditions",
                               return_value=(["NEW", "USED_EXCELLENT"],
                                             True)):
            r = m.app.test_client().get(
                "/api/ebay/category-conditions?category_id=123")
        data = json.loads(r.data)
        self.assertTrue(data["available"])
        self.assertTrue(data["condition_required"])
        self.assertEqual(data["conditions"],
                         [{"value": "NEW", "label": "New"},
                          {"value": "USED_EXCELLENT", "label": "Used"}])

    def test_endpoint_fallback_returns_static_list(self):
        with mock.patch.object(m, "_category_conditions",
                               return_value=(None, None)):
            r = m.app.test_client().get(
                "/api/ebay/category-conditions?category_id=123")
        data = json.loads(r.data)
        self.assertFalse(data["available"])
        values = [c["value"] for c in data["conditions"]]
        self.assertIn("USED_EXCELLENT", values)
        labels = {c["value"]: c["label"] for c in data["conditions"]}
        self.assertEqual(labels["USED_EXCELLENT"], "Used")
        self.assertEqual(labels["USED_VERY_GOOD"], "Very Good")


class PublishEndpointTest(unittest.TestCase):
    """v35: single publish passes problems through on the pick signal."""

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

    def test_pick_response_carries_problems(self):
        draft = {"id": "d1", "title": "Test", "photos": ["p.jpg"]}
        exc = m._NeedsFulfillmentPolicyChoice(
            "That shipping policy number is no longer on your eBay "
            "account. Pick one below.",
            policies=[{"id": "111", "name": "Dummy Flat",
                       "cost_type": "FLAT_RATE"}],
            problems=["Pick a condition first."])
        with mock.patch.object(m, "_load_draft", return_value=draft), \
                mock.patch.object(m, "_do_publish", side_effect=exc):
            r = self.client.post("/api/ebay/publish",
                                 json={"draft_id": "d1"})
        data = json.loads(r.data)
        self.assertTrue(data["needs_policy_pick"])
        self.assertEqual(data["problems"], ["Pick a condition first."])
        self.assertEqual(data["policies"][0]["name"], "Dummy Flat")


if __name__ == "__main__":
    unittest.main()
