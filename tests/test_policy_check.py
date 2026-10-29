"""apk-v32 tests: sell.account permission + shipping-policy check.

v31 created the shop location in-app and proceeded, but the first live
publish after that failed with eBay error 25007 (invalid Fulfillment
policy). Two independent analyses (the Oct 28 audit's unsettled
policy-type item + a Gemini consult Oct 29) rank the causes: (1) the
saved fulfillment policy is Calculated shipping and the offer sends no
packageWeightAndSize; (2) the saved policy ID is stale/deleted;
(3) marketplace mismatch; (4) restricted service. v32 adds the
sell.account scope so the app reads the policy itself and stops 25007
at its cause: flat proceeds, calculated requires a real package block,
a stale ID offers a one-tap pick, and a token without the scope shows
a plain reconnect message.

PRIVACY: the repo is PUBLIC. Every policy ID and address here is
obviously dummy (111/222/333, 123 Test St). No real policy ID, address,
or secret appears in code, fixtures, or test output.

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
GB_POLICY = {"fulfillmentPolicyId": "111", "name": "Dummy GB",
             "marketplaceId": "EBAY_GB",
             "shippingOptions": [{"optionType": "DOMESTIC",
                                  "costType": "FLAT_RATE"}]}
PICKUP_POLICY = {"fulfillmentPolicyId": "111", "name": "Dummy Pickup",
                 "marketplaceId": "EBAY_US", "localPickup": True,
                 "shippingOptions": []}

DUMMY_PACKAGE = {"weight_value": "4", "weight_unit": "OUNCE",
                 "length": "12", "width": "9", "height": "1"}


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


class ScopeTest(unittest.TestCase):
    def test_sell_account_in_authorize_scopes(self):
        self.assertIn("https://api.ebay.com/oauth/api_scope/sell.account",
                      m.EBAY_SCOPES)
        self.assertIn("https://api.ebay.com/oauth/api_scope/sell.inventory",
                      m.EBAY_SCOPES)
        self.assertIn("https://api.ebay.com/oauth/api_scope/sell.listing",
                      m.EBAY_SCOPES)

    def test_connect_redirect_carries_new_scope(self):
        with mock.patch.object(m, "EBAY_CLIENT_ID", "DummyId"), \
                mock.patch.object(m, "EBAY_CLIENT_SECRET", "DummySecret"), \
                mock.patch.object(m, "EBAY_RUNAME", "Dummy-RuName"):
            client = m.app.test_client()
            resp = client.get("/api/ebay/connect")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("sell.account", resp.headers["Location"])

    def test_refresh_scope_failure_raises_typed_signal(self):
        err = _fake_resp(400, {"error": "invalid_scope",
                               "error_description":
                                   "scope exceeds the granted scope"})
        with mock.patch.object(m, "_ebay_configured", return_value=True), \
                mock.patch.object(m, "_load_ebay_tokens",
                                  return_value={"refresh_token": "rt",
                                                "access_token": "old",
                                                "expires_at": 0}), \
                mock.patch.object(m.requests, "post", return_value=err), \
                mock.patch.object(m, "_clear_ebay_tokens") as clear:
            # v33: the scope failure must carry the typed signal (it
            # subclasses RuntimeError, so the old assertion still
            # passes, but only the type carries the frontend flag).
            with self.assertRaises(m._NeedsScopeReconnect) as ctx:
                m._ebay_access_token()
        clear.assert_called_once()
        self.assertEqual(str(ctx.exception), m.SCOPE_RECONNECT_MSG)

    def test_refresh_plain_failure_keeps_expired_message(self):
        err = _fake_resp(400, {"error": "invalid_grant",
                               "error_description": "token expired"})
        with mock.patch.object(m, "_ebay_configured", return_value=True), \
                mock.patch.object(m, "_load_ebay_tokens",
                                  return_value={"refresh_token": "rt",
                                                "access_token": "old",
                                                "expires_at": 0}), \
                mock.patch.object(m.requests, "post", return_value=err), \
                mock.patch.object(m, "_clear_ebay_tokens"):
            with self.assertRaises(RuntimeError) as ctx:
                m._ebay_access_token()
        self.assertIn("expired", str(ctx.exception))


class EbayApiErrorTest(unittest.TestCase):
    def test_status_and_error_id_attached(self):
        exc = m._EbayApiError("boom", status=403, error_id="190013")
        self.assertEqual(exc.status, 403)
        self.assertEqual(exc.error_id, "190013")
        self.assertEqual(str(exc), "boom")

    def test_still_caught_by_except_runtime_error(self):
        try:
            raise m._EbayApiError("boom", status=404)
        except RuntimeError as exc:
            self.assertEqual(exc.status, 404)
        else:
            self.fail("not caught as RuntimeError")

    def test_ebay_call_raises_typed_error(self):
        resp = _fake_resp(403, {"errors": [{"message": "no scope",
                                            "errorId": 190013}]})
        with mock.patch.object(m.requests, "request", return_value=resp):
            with self.assertRaises(m._EbayApiError) as ctx:
                m._ebay_call("GET", "/sell/account/v1/fulfillment_policy/111",
                             "tok")
        self.assertEqual(ctx.exception.status, 403)
        self.assertEqual(ctx.exception.error_id, "190013")
        self.assertIn("190013", str(ctx.exception))


class PolicyCostTypeTest(unittest.TestCase):
    def test_domestic_decides(self):
        self.assertEqual(m._policy_cost_type(FLAT_POLICY), "FLAT_RATE")
        self.assertEqual(m._policy_cost_type(CALC_POLICY), "CALCULATED")

    def test_international_only_is_none(self):
        # No domestic option means no shippable domestic service, even
        # when an international option exists: the app only lists in
        # the US, so an international rate never stands in.
        p = {"shippingOptions": [{"optionType": "INTERNATIONAL",
                                  "costType": "FLAT_RATE"}]}
        self.assertIsNone(m._policy_cost_type(p))

    def test_no_options_is_none(self):
        self.assertIsNone(m._policy_cost_type(PICKUP_POLICY))
        self.assertIsNone(m._policy_cost_type({}))
        self.assertIsNone(m._policy_cost_type(None))


class PackageValidationTest(unittest.TestCase):
    def test_valid_block_cleaned(self):
        out = m._validate_package(dict(DUMMY_PACKAGE, weight_unit="ounce",
                                       weight_value=" 4 "))
        self.assertEqual(out, {"dimensions": {"height": 1.0, "length": 12.0,
                                              "width": 9.0, "unit": "INCH"},
                               "weight": {"value": 4.0, "unit": "OUNCE"}})

    def test_partial_block_rejected(self):
        for missing in ("weight_value", "weight_unit", "length",
                        "width", "height"):
            bad = dict(DUMMY_PACKAGE)
            bad[missing] = "  "
            with self.assertRaises(m._PublishValidationError):
                m._validate_package(bad)

    def test_bad_unit_and_nonpositive_rejected(self):
        with self.assertRaises(m._PublishValidationError):
            m._validate_package(dict(DUMMY_PACKAGE, weight_unit="GRAMS"))
        with self.assertRaises(m._PublishValidationError):
            m._validate_package(dict(DUMMY_PACKAGE, weight_value="0"))
        with self.assertRaises(m._PublishValidationError):
            m._validate_package(dict(DUMMY_PACKAGE, length="-2"))

    def test_absurd_size_rejected(self):
        with self.assertRaises(m._PublishValidationError):
            m._validate_package(dict(DUMMY_PACKAGE, weight_value="500",
                                     weight_unit="POUND"))


class PolicyChainTest(unittest.TestCase):
    """_do_publish through the v32 policy gate, eBay fully mocked."""

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
        self.offer_error = None
        self.publish_error = None
        self.inventory_payload = None
        self.offer_payload = None
        self.uploads = 0

    def tearDown(self):
        for attr, val in self.orig.items():
            setattr(m, attr, val)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_call(self, method, path, token, payload=None):
        if (method, path) == ("GET", "/sell/inventory/v1/location"):
            return {"locations": [{"merchantLocationKey": "LOC1"}]}
        if method == "GET" and path.startswith(
                "/sell/account/v1/fulfillment_policy"):
            if self.policy_mode == "scope403":
                raise m._EbayApiError("forbidden", status=403,
                                      error_id="")
            if "?" in path:
                # List fallback: the stored ID is absent (stale), unless
                # the mode says the list holds it.
                if self.policy_mode == "list_has":
                    return {"fulfillmentPolicies": [FLAT_POLICY]}
                return {"fulfillmentPolicies": [OTHER_POLICY]}
            if self.policy_mode == "missing":
                raise m._EbayApiError("not found", status=404,
                                      error_id="20404")
            if self.policy_mode == "mismatch":
                return GB_POLICY
            if self.policy_mode == "calculated":
                return CALC_POLICY
            if self.policy_mode == "pickup":
                return PICKUP_POLICY
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
        if method == "GET" and path.startswith("/sell/inventory/v1/offer?sku="):
            return {"offers": []}
        if method == "GET" and path.startswith("/sell/listing/v1/item/"):
            return {}
        raise AssertionError(f"unexpected eBay call {method} {path}")

    def _run(self, fields=None, package=None):
        f = dict(self.fields)
        if fields:
            f.update(fields)
        if package is not None:
            f["package"] = package
        with mock.patch.object(m, "_ebay_access_token",
                               return_value="tok"), \
                mock.patch.object(m, "_ebay_upload_picture",
                                  side_effect=self._count_upload), \
                mock.patch.object(m, "_category_aspects",
                                  return_value=(None, None)), \
                mock.patch.object(m, "_ebay_call",
                                  side_effect=self._fake_call), \
                mock.patch.object(m, "EBAY_MERCHANT_LOCATION_KEY", ""):
            return m._do_publish(dict(self.draft), f)

    def _count_upload(self, *args, **kwargs):
        self.uploads += 1
        return "https://i.ebayimg.com/fake.jpg"

    def test_flat_proceeds_without_weight(self):
        listing_id, _url = self._run()
        self.assertEqual(listing_id, "999")
        self.assertNotIn("packageWeightAndSize", self.inventory_payload)
        self.assertEqual(self.offer_payload["listingPolicies"]
                         ["fulfillmentPolicyId"], "111")

    def test_flat_with_entered_weight_does_not_send_it(self):
        listing_id, _url = self._run(package=dict(DUMMY_PACKAGE))
        self.assertEqual(listing_id, "999")
        self.assertNotIn("packageWeightAndSize", self.inventory_payload)

    def test_calculated_without_weight_blocks_before_uploads(self):
        self.policy_mode = "calculated"
        with self.assertRaises(m._PublishValidationError) as ctx:
            self._run()
        self.assertIn("charges by weight", str(ctx.exception))
        self.assertEqual(self.uploads, 0)

    def test_calculated_with_weight_sends_block(self):
        self.policy_mode = "calculated"
        listing_id, _url = self._run(package=dict(DUMMY_PACKAGE))
        self.assertEqual(listing_id, "999")
        self.assertEqual(self.inventory_payload["packageWeightAndSize"],
                         {"dimensions": {"height": 1.0, "length": 12.0,
                                         "width": 9.0, "unit": "INCH"},
                          "weight": {"value": 4.0, "unit": "OUNCE"}})

    def test_calculated_with_partial_block_rejected(self):
        self.policy_mode = "calculated"
        bad = dict(DUMMY_PACKAGE)
        bad["height"] = ""
        with self.assertRaises(m._PublishValidationError):
            self._run(package=bad)

    def test_missing_policy_raises_pick_with_list(self):
        self.policy_mode = "missing"
        with self.assertRaises(m._NeedsFulfillmentPolicyChoice) as ctx:
            self._run()
        self.assertEqual(len(ctx.exception.policies), 1)
        self.assertEqual(ctx.exception.policies[0]["id"], "999")
        self.assertIn("no longer", str(ctx.exception))

    def test_missing_policy_found_via_list_proceeds(self):
        self.policy_mode = "list_has"
        # Single read misses; the list holds the stored ID.
        orig = self._fake_call

        def fake_single_miss(method, path, token, payload=None):
            if method == "GET" and path == \
                    "/sell/account/v1/fulfillment_policy/111":
                raise m._EbayApiError("not found", status=404,
                                      error_id="20404")
            return orig(method, path, token, payload)

        with mock.patch.object(m, "_ebay_access_token",
                               return_value="tok"), \
                mock.patch.object(m, "_ebay_upload_picture",
                                  side_effect=self._count_upload), \
                mock.patch.object(m, "_category_aspects",
                                  return_value=(None, None)), \
                mock.patch.object(m, "_ebay_call",
                                  side_effect=fake_single_miss), \
                mock.patch.object(m, "EBAY_MERCHANT_LOCATION_KEY", ""):
            listing_id, _url = m._do_publish(dict(self.draft),
                                             dict(self.fields))
        self.assertEqual(listing_id, "999")

    def test_marketplace_mismatch_raises_pick(self):
        self.policy_mode = "mismatch"
        with self.assertRaises(m._NeedsFulfillmentPolicyChoice) as ctx:
            self._run()
        self.assertIn("EBAY_GB", str(ctx.exception))

    def test_pickup_only_policy_raises_pick(self):
        self.policy_mode = "pickup"
        with self.assertRaises(m._NeedsFulfillmentPolicyChoice):
            self._run()

    def test_scope_403_raises_reconnect_without_wiping(self):
        self.policy_mode = "scope403"
        with mock.patch.object(m, "_clear_ebay_tokens") as clear:
            with self.assertRaises(m._NeedsScopeReconnect) as ctx:
                self._run()
        clear.assert_not_called()
        self.assertEqual(str(ctx.exception), m.SCOPE_RECONNECT_MSG)

    def test_25007_on_offer_names_policy_id_and_type(self):
        self.offer_error = RuntimeError(
            "eBay said (error 25007): invalid shipping policy.")
        with self.assertRaises(RuntimeError) as ctx:
            self._run()
        self.assertIn("25007", str(ctx.exception))
        self.assertIn("111", str(ctx.exception))
        self.assertIn("FLAT_RATE", str(ctx.exception))

    def test_25007_on_publish_names_policy_id_and_type(self):
        self.publish_error = RuntimeError(
            "eBay said (error 25007): invalid shipping policy.")
        with self.assertRaises(RuntimeError) as ctx:
            self._run()
        self.assertIn("25007", str(ctx.exception))
        self.assertIn("111", str(ctx.exception))
        self.assertIn("FLAT_RATE", str(ctx.exception))


class PolicyRouteTest(unittest.TestCase):
    """Single + batch wrappers map the v32 signals to UI flags."""

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
            json.dump({"fulfillment_policy_id": "111",
                       "payment_policy_id": "222",
                       "return_policy_id": "333"}, f)
        self.client = m.app.test_client()
        self.photo = os.path.join(m.PHOTOS_DIR, "p.jpg")
        with open(self.photo, "wb") as f:
            f.write(b"\xff\xd8fakejpgbytes")
        self.policy_mode = "flat"

    def tearDown(self):
        for attr, val in self.orig.items():
            setattr(m, attr, val)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_call(self, method, path, token, payload=None):
        if (method, path) == ("GET", "/sell/inventory/v1/location"):
            return {"locations": [{"merchantLocationKey": "LOC1"}]}
        if method == "GET" and path.startswith(
                "/sell/account/v1/fulfillment_policy"):
            if self.policy_mode == "scope403":
                raise m._EbayApiError("forbidden", status=403,
                                      error_id="")
            if "?" in path:
                return {"fulfillmentPolicies": [OTHER_POLICY]}
            if self.policy_mode == "missing":
                raise m._EbayApiError("not found", status=404,
                                      error_id="20404")
            policy = dict(FLAT_POLICY)
            policy["fulfillmentPolicyId"] = path.rsplit("/", 1)[-1]
            return policy
        if method == "PUT" and path.startswith(
                "/sell/inventory/v1/inventory_item/"):
            return {}
        if (method, path) == ("POST", "/sell/inventory/v1/offer"):
            return {"offerId": "OFFER1"}
        if method == "POST" and path.startswith("/sell/inventory/v1/offer/"):
            return {"listingId": "999"}
        if method == "GET" and path.startswith("/sell/listing/v1/item/"):
            return {}
        raise AssertionError(f"unexpected eBay call {method} {path}")

    def _patches(self):
        return [
            mock.patch.object(m, "_ebay_access_token", return_value="tok"),
            mock.patch.object(m, "_ebay_upload_picture",
                              return_value="https://i.ebayimg.com/fake.jpg"),
            mock.patch.object(m, "_category_aspects",
                              return_value=(None, None)),
            mock.patch.object(m, "_ebay_call", side_effect=self._fake_call),
            mock.patch.object(m, "EBAY_MERCHANT_LOCATION_KEY", ""),
        ]

    def _seed_draft(self, draft_id):
        d = {"id": draft_id, "photos": ["p.jpg"], "title": "T",
             "description": "D", "price": "10.00", "condition": "NEW",
             "condition_details": "", "category_id": "123",
             "attributes": {}}
        with open(os.path.join(m.DRAFTS_DIR, draft_id + ".json"), "w",
                  encoding="utf-8") as f:
            json.dump(d, f)
        return d

    def test_single_scope_flag(self):
        self.policy_mode = "scope403"
        self._seed_draft("d1")
        patches = self._patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        resp = self.client.post("/api/ebay/publish",
                                json={"draft_id": "d1",
                                      "fulfillment_policy_id": "111",
                                      "payment_policy_id": "222",
                                      "return_policy_id": "333"})
        body = resp.get_json()
        self.assertTrue(body.get("needs_scope_reconnect"))
        self.assertIn("new permission", body.get("error", ""))

    def test_single_pick_flag_and_repost_with_pick_lists(self):
        self.policy_mode = "missing"
        self._seed_draft("d1")
        patches = self._patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        resp = self.client.post("/api/ebay/publish",
                                json={"draft_id": "d1",
                                      "fulfillment_policy_id": "111",
                                      "payment_policy_id": "222",
                                      "return_policy_id": "333"})
        body = resp.get_json()
        self.assertTrue(body.get("needs_policy_pick"))
        self.assertEqual(body["policies"][0]["id"], "999")
        # Re-post with the picked ID: the gate reads it and proceeds.
        self.policy_mode = "flat"
        resp = self.client.post(
            "/api/ebay/publish",
            json={"draft_id": "d1", "fulfillment_policy_id": "999",
                  "payment_policy_id": "222", "return_policy_id": "333"})
        body = resp.get_json()
        self.assertEqual(body.get("listing_id"), "999")

    def test_batch_stops_at_first_scope_signal(self):
        self.policy_mode = "scope403"
        self._seed_draft("d1")
        self._seed_draft("d2")
        patches = self._patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        resp = self.client.post("/api/ebay/batch-publish",
                                json={"draft_ids": ["d1", "d2"],
                                      "category_id": "123"})
        results = resp.get_json()["results"]
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].get("needs_scope_reconnect"))

    def test_batch_stops_at_first_pick_signal(self):
        self.policy_mode = "missing"
        self._seed_draft("d1")
        self._seed_draft("d2")
        patches = self._patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        resp = self.client.post("/api/ebay/batch-publish",
                                json={"draft_ids": ["d1", "d2"],
                                      "category_id": "123"})
        results = resp.get_json()["results"]
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].get("needs_policy_pick"))
        self.assertEqual(results[0]["policies"][0]["id"], "999")

    def test_batch_pick_override_reruns_whole_batch(self):
        self.policy_mode = "missing"
        self._seed_draft("d1")
        self._seed_draft("d2")
        patches = self._patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        # Re-run with the picked ID: the gate reads it and both list.
        self.policy_mode = "flat"
        resp = self.client.post(
            "/api/ebay/batch-publish",
            json={"draft_ids": ["d1", "d2"], "category_id": "123",
                  "fulfillment_policy_id": "999"})
        results = resp.get_json()["results"]
        self.assertEqual([r["ok"] for r in results], [True, True])

    def test_draft_put_saves_package_fields(self):
        self._seed_draft("d1")
        resp = self.client.put("/api/drafts/d1",
                               json={"package_weight_value": "4",
                                     "package_weight_unit": "ounce",
                                     "package_length": "12",
                                     "package_width": "9",
                                     "package_height": "1",
                                     "package_weight_unit_bad": "GRAMS"})
        saved = resp.get_json()["draft"]
        self.assertEqual(saved["package_weight_value"], "4")
        self.assertEqual(saved["package_weight_unit"], "OUNCE")
        self.assertEqual(saved["package_height"], "1")
        self.assertNotIn("package_weight_unit_bad", saved)


if __name__ == "__main__":
    unittest.main()
