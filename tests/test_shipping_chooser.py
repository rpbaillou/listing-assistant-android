"""apk-v34 tests: the always-visible Shipping chooser on the listing
page (scope correction: calculated shipping is the path for the
current item — flat rate is NOT the fix, the default, or the path).

The chooser lists the account's current fulfillment policies (GET via
sell.account) with plain type labels, one-tap select saves the choice
to the draft so it sticks across reloads, calculated policies show the
shipping package fields inside the section (eBay uses them to work out
the buyer's shipping cost) and require them before List, flat/free
policies hide the fields and send no package block. A reactive pick
(saved policy unusable) opens this same chooser and re-posts with the
picked number, which the v34 discard turns into a fresh offer.

PRIVACY: the repo is PUBLIC. Every policy ID, offer ID, and address
here is obviously dummy (111/222/999, OLD-OFFER/OFFER-NEW). No real
policy ID, address, or secret appears in code, fixtures, or test output.

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


CALC_FOR = lambda pid: {"fulfillmentPolicyId": pid, "name": "Dummy Calculated",
                        "marketplaceId": "EBAY_US",
                        "shippingOptions": [{"optionType": "DOMESTIC",
                                             "costType": "CALCULATED"}]}
FLAT_FOR = lambda pid: {"fulfillmentPolicyId": pid, "name": "Dummy Flat",
                        "marketplaceId": "EBAY_US",
                        "shippingOptions": [{"optionType": "DOMESTIC",
                                             "costType": "FLAT_RATE"}]}

POLICY_LIST = {"fulfillmentPolicies": [CALC_FOR("111"), FLAT_FOR("222")]}


class ChooserRouteTest(unittest.TestCase):
    """GET /api/ebay/fulfillment-policies + draft PUT persistence."""

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
        with open(m.PRESET_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)
        self.client = m.app.test_client()
        self.call_mode = "list"

    def tearDown(self):
        for attr, val in self.orig.items():
            setattr(m, attr, val)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_call(self, method, path, token, payload=None):
        if (method, path) == ("GET", "/sell/account/v1/fulfillment_policy"
                              "?marketplace_id=EBAY_US"):
            if self.call_mode == "scope403":
                raise m._EbayApiError("forbidden", status=403, error_id="")
            if self.call_mode == "server500":
                raise m._EbayApiError("eBay said (error 99999): boom.",
                                      status=500, error_id="99999")
            return POLICY_LIST
        raise AssertionError(f"unexpected eBay call {method} {path}")

    def _patches(self):
        return [
            mock.patch.object(m, "_ebay_access_token", return_value="tok"),
            mock.patch.object(m, "_ebay_call", side_effect=self._fake_call),
        ]

    def _get_policies(self):
        patches = self._patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        return self.client.get("/api/ebay/fulfillment-policies")

    def test_chooser_lists_policies_with_plain_types(self):
        resp = self._get_policies()
        self.assertEqual(resp.status_code, 200)
        policies = resp.get_json()["policies"]
        by_id = {p["id"]: p for p in policies}
        self.assertEqual(by_id["111"]["cost_type"], "CALCULATED")
        self.assertEqual(by_id["111"]["name"], "Dummy Calculated")
        self.assertEqual(by_id["222"]["cost_type"], "FLAT_RATE")
        self.assertEqual(by_id["222"]["name"], "Dummy Flat")

    def test_chooser_403_maps_to_reconnect_flag(self):
        self.call_mode = "scope403"
        resp = self._get_policies()
        body = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(body.get("needs_scope_reconnect"))
        self.assertIn("new permission", body.get("error", ""))

    def test_chooser_other_error_surfaced_not_reconnect(self):
        self.call_mode = "server500"
        resp = self._get_policies()
        body = resp.get_json()
        self.assertEqual(resp.status_code, 502)
        self.assertFalse(body.get("needs_scope_reconnect"))
        self.assertIn("99999", body.get("error", ""))

    def test_selection_saves_to_draft_and_persists(self):
        d = {"id": "d1", "photos": [], "title": "T", "description": "D",
             "price": "10.00", "condition": "NEW", "condition_details": "",
             "category_id": "123", "attributes": {}}
        with open(os.path.join(m.DRAFTS_DIR, "d1.json"), "w",
                  encoding="utf-8") as f:
            json.dump(d, f)
        resp = self.client.put("/api/drafts/d1",
                               json={"fulfillment_policy_id": "222"})
        saved = resp.get_json()["draft"]
        self.assertEqual(saved["fulfillment_policy_id"], "222")
        # A reload reads the same saved choice back (sticks across reloads).
        reloaded = m._load_draft("d1")
        self.assertEqual(reloaded["fulfillment_policy_id"], "222")


class ChooserChainTest(unittest.TestCase):
    """_do_publish payloads: calculated sends the package block, flat
    never does — the current item's path is calculated + package +
    fresh offer."""

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
        self.calls = []
        self.inventory_payload = None
        self.offer_payload = None

    def tearDown(self):
        for attr, val in self.orig.items():
            setattr(m, attr, val)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_call(self, method, path, token, payload=None):
        self.calls.append((method, path))
        if (method, path) == ("GET", "/sell/inventory/v1/location"):
            return {"locations": [{"merchantLocationKey": "LOC1"}]}
        if method == "GET" and path.startswith(
                "/sell/account/v1/fulfillment_policy/"):
            pid = path.rsplit("/", 1)[-1]
            return CALC_FOR(pid) if pid == "111" else FLAT_FOR(pid)
        if method == "PUT" and path.startswith(
                "/sell/inventory/v1/inventory_item/"):
            self.inventory_payload = payload
            return {}
        if (method, path) == ("POST", "/sell/inventory/v1/offer"):
            self.offer_payload = payload
            return {"offerId": "OFFER-NEW"}
        if method == "POST" and path.startswith("/sell/inventory/v1/offer/"):
            return {"listingId": "999"}
        if method == "DELETE" and path.startswith("/sell/inventory/v1/offer/"):
            return {}
        if method == "GET" and path.startswith("/sell/listing/v1/item/"):
            return {}
        raise AssertionError(f"unexpected eBay call {method} {path}")

    def _fields(self, fulfillment_id, package=None, fresh_offer=False):
        f = {"title": "Test item", "description": "A test item.",
             "price_val": 10.0, "condition": "NEW", "condition_details": "",
             "category_id": "123", "fulfillment_id": fulfillment_id,
             "payment_id": "222", "return_id": "333", "attributes": {},
             "best_offer_terms": None, "package": package}
        if fresh_offer:
            f["fresh_offer"] = True
        return f

    def _run(self, draft, fields):
        with mock.patch.object(m, "_ebay_access_token",
                               return_value="tok"), \
                mock.patch.object(m, "_ebay_upload_picture",
                                  return_value="https://i.ebayimg.com/fake.jpg"), \
                mock.patch.object(m, "_category_aspects",
                                  return_value=(None, None)), \
                mock.patch.object(m, "_ebay_call",
                                  side_effect=self._fake_call), \
                mock.patch.object(m, "EBAY_MERCHANT_LOCATION_KEY", ""):
            return m._do_publish(draft, fields)

    def test_calculated_plus_package_plus_fresh_offer_publishes(self):
        # The current item's path after v34: calculated policy + real
        # package + a pick that discards the stale offer, so the fresh
        # offer carries the calculated policy and the inventory item
        # carries the package block eBay prices from.
        package = {"weight_value": "4", "weight_unit": "POUND",
                   "length": "12", "width": "9", "height": "1"}
        d = {"id": "d1", "photos": ["p.jpg"], "ebay_offer_id": "OLD-OFFER",
             "ebay_offer_fulfillment_id": "222"}
        listing_id, _url = self._run(
            d, self._fields("111", package=package, fresh_offer=True))
        self.assertEqual(listing_id, "999")
        deletes = [p for (method, p) in self.calls if method == "DELETE"]
        self.assertEqual(deletes, ["/sell/inventory/v1/offer/OLD-OFFER"])
        self.assertEqual(self.offer_payload["listingPolicies"]
                         ["fulfillmentPolicyId"], "111")
        block = self.inventory_payload["packageWeightAndSize"]
        self.assertEqual(block["weight"], {"value": 4.0, "unit": "POUND"})
        self.assertEqual(block["dimensions"]["unit"], "INCH")
        saved = m._load_draft("d1")
        self.assertEqual(saved["ebay_offer_id"], "OFFER-NEW")
        self.assertEqual(saved["ebay_offer_fulfillment_id"], "111")

    def test_flat_with_entered_weight_sends_no_package(self):
        # Flat/free hides the package fields, and a stray entered block
        # is validated for typos but never sent (v32 behavior kept).
        package = {"weight_value": "4", "weight_unit": "POUND",
                   "length": "12", "width": "9", "height": "1"}
        d = {"id": "d1", "photos": ["p.jpg"]}
        listing_id, _url = self._run(
            d, self._fields("222", package=package))
        self.assertEqual(listing_id, "999")
        self.assertNotIn("packageWeightAndSize", self.inventory_payload)


if __name__ == "__main__":
    unittest.main()
