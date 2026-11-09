"""apk-v49 tests: USPS Ground Advantage sends USPSParcel, never
USPSGroundAdvantage.

The Nov 8 live policy create failed with eBay error 20403
UNKNOWN_SHIPPING_SERVICE because the form sent shippingServiceCode
USPSGroundAdvantage. eBay's LSAS checker rejects that code on this
account. eBay's own code for the same Ground Advantage service is
USPSParcel: eBay's reports use USPSParcel for Ground Advantage, and
his old policy is literally named "Calculated: USPSParcel". So the
app canonicalizes: every code that enters a policy body, a match,
or a picker passes through _canonical_service_code, which maps
USPSGroundAdvantage to USPSParcel and leaves everything else alone.

FedExSmartPost (FedEx Ground Economy) is deliberately NOT mapped or
dropped here: whether create accepts it is unproven, and the retry
test settles it — the same combination (Ground Advantage plus Ground
Economy) either creates, which proves FedExSmartPost is fine, or
fails again with UNKNOWN_SHIPPING_SERVICE, which proves it is also
bad. Do not guess it away.

PRIVACY: the repo is PUBLIC. Every policy ID and service code here
is dummy. No real policy ID, address, or secret appears in code,
fixtures, or test output.

eBay is always mocked; no test here reaches the network.
"""

import json
import os
import sys
import unittest
from unittest import mock

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PY_DIR = os.path.join(REPO, "app", "src", "main", "python")
sys.path.insert(0, PY_DIR)

import app as m  # noqa: E402  (imports cleanly; __main__ guard prevents serving)


def tearDownModule():
    sys.modules.pop("app", None)


def _flat_form(**over):
    form = {"name": "Dummy Doll Policy", "description": "",
            "pickup_only": False, "local_pickup": False,
            "ship_international": False, "handling_days": "2",
            "dom_cost_type": "CALCULATED",
            "dom_service_1": "USPSPriority", "dom_service_1_other": "",
            "dom_service_2": "", "dom_service_2_other": "",
            "dom_cost_1": "", "dom_cost_2": "",
            "dom_free_shipping": False}
    form.update(over)
    return form


def _calc_policy(pid, services):
    opts = [{"optionType": "DOMESTIC", "costType": "CALCULATED",
             "shippingServices": [{"shippingServiceCode": s}
                                  for s in services]}]
    return {"fulfillmentPolicyId": pid, "name": "Dummy " + pid,
            "marketplaceId": "EBAY_US", "shippingOptions": opts}


def _codes_in_body(body):
    """Every shippingServiceCode anywhere in a built policy body."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            if "shippingServiceCode" in node:
                found.append(node["shippingServiceCode"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(body)
    return found


class CanonicalCodeTest(unittest.TestCase):
    def test_ground_advantage_maps_to_usps_parcel(self):
        self.assertEqual(m._canonical_service_code("USPSGroundAdvantage"),
                         "USPSParcel")

    def test_usps_parcel_and_everything_else_pass_through(self):
        for code in ("USPSParcel", "USPSPriority", "UPSGround",
                     "FedExSmartPost", "FedEx2Day", "UPSGroundSaver",
                     "ZZZCustom"):
            self.assertEqual(m._canonical_service_code(code), code)

    def test_blank_and_none_become_empty(self):
        self.assertEqual(m._canonical_service_code(""), "")
        self.assertEqual(m._canonical_service_code(None), "")
        self.assertEqual(m._canonical_service_code("  "), "")


class CreateBodyTest(unittest.TestCase):
    def test_dropdown_ground_advantage_sends_usps_parcel(self):
        body = m._build_fulfillment_policy_body(
            _flat_form(dom_service_1="USPSGroundAdvantage"))
        self.assertEqual(_codes_in_body(body), ["USPSParcel"])

    def test_second_service_maps_too(self):
        body = m._build_fulfillment_policy_body(_flat_form(
            dom_service_1="USPSPriority", dom_service_2="USPSGroundAdvantage"))
        self.assertEqual(_codes_in_body(body),
                         ["USPSPriority", "USPSParcel"])

    def test_international_service_maps_too(self):
        body = m._build_fulfillment_policy_body(_flat_form(
            ship_international=True, intl_cost_type="FLAT_RATE",
            intl_service_1="USPSGroundAdvantage", intl_service_1_other="",
            intl_cost_1="25.00", intl_service_2="", intl_service_2_other="",
            intl_cost_2=""))
        self.assertEqual(_codes_in_body(body), ["USPSPriority", "USPSParcel"])

    def test_typed_other_ground_advantage_maps_too(self):
        body = m._build_fulfillment_policy_body(_flat_form(
            dom_service_1="__OTHER__",
            dom_service_1_other="USPSGroundAdvantage"))
        self.assertEqual(_codes_in_body(body), ["USPSParcel"])

    def test_rejected_code_never_appears_in_any_built_body(self):
        body = m._build_fulfillment_policy_body(_flat_form(
            dom_service_1="USPSGroundAdvantage",
            dom_service_2="USPSGroundAdvantage",
            ship_international=True, intl_cost_type="FLAT_RATE",
            intl_service_1="USPSGroundAdvantage", intl_service_1_other="",
            intl_cost_1="25.00", intl_service_2="", intl_service_2_other="",
            intl_cost_2=""))
        self.assertNotIn("USPSGroundAdvantage", _codes_in_body(body))
        self.assertNotIn("USPSGroundAdvantage", json.dumps(body))

    def test_fedex_smart_post_is_left_as_is(self):
        # Unproven, not guessed away: the retry test settles it.
        body = m._build_fulfillment_policy_body(_flat_form(
            dom_service_1="USPSGroundAdvantage",
            dom_service_2="FedExSmartPost"))
        self.assertEqual(_codes_in_body(body),
                         ["USPSParcel", "FedExSmartPost"])


class MatcherTest(unittest.TestCase):
    """POST /api/ebay/policy-for-service treats USPSParcel as
    Ground Advantage, in both directions."""

    def setUp(self):
        self.client = m.app.test_client()

    def _call(self, body, fake_list):
        def fake_call(method, path, token, payload=None):
            return fake_list
        with mock.patch.object(m, "_ebay_access_token",
                               return_value="DUMMYTOKEN"), \
                mock.patch.object(m, "_ebay_call", side_effect=fake_call):
            return self.client.post("/api/ebay/policy-for-service",
                                    json=body)

    def test_policy_carrying_usps_parcel_matches_ground_advantage_pick(self):
        # The picker's Ground Advantage pick (USPSParcel) finds his
        # existing Ground Advantage policy (which carries USPSParcel).
        fake = {"fulfillmentPolicies": [_calc_policy("111", ["USPSParcel"])]}
        r = self._call({"carrier": "USPS", "service_code": "USPSParcel"},
                       fake)
        self.assertEqual(r.get_json()["policy_id"], "111")

    def test_old_code_in_the_request_still_matches(self):
        # An old saved draft carrying USPSGroundAdvantage still finds
        # the same policy: the request code is canonicalized too.
        fake = {"fulfillmentPolicies": [_calc_policy("111", ["USPSParcel"])]}
        r = self._call({"carrier": "USPS",
                        "service_code": "USPSGroundAdvantage"}, fake)
        self.assertEqual(r.get_json()["policy_id"], "111")

    def test_policy_carrying_the_old_code_still_matches(self):
        # A policy eBay returns carrying USPSGroundAdvantage matches
        # a USPSParcel pick: its codes are canonicalized as well.
        fake = {"fulfillmentPolicies":
                [_calc_policy("111", ["USPSGroundAdvantage"])]}
        r = self._call({"carrier": "USPS", "service_code": "USPSParcel"},
                       fake)
        self.assertEqual(r.get_json()["policy_id"], "111")


class HarvestTest(unittest.TestCase):
    def test_harvested_old_code_merges_into_usps_parcel(self):
        out = m._harvest_shipping_services(
            [_calc_policy("111", ["USPSGroundAdvantage"])])
        codes = [s["code"] for s in out["domestic"]]
        self.assertIn("USPSParcel", codes)
        self.assertNotIn("USPSGroundAdvantage", codes)
        self.assertNotIn("USPSGroundAdvantage", json.dumps(out))

    def test_harvest_labels_the_merged_code_as_ground_advantage(self):
        out = m._harvest_shipping_services(
            [_calc_policy("111", ["USPSGroundAdvantage"])])
        labels = {s["code"]: s["label"] for s in out["domestic"]}
        self.assertEqual(labels["USPSParcel"], "USPS Ground Advantage")


class CatalogAndLabelsTest(unittest.TestCase):
    def test_rejected_code_is_not_a_picker_code(self):
        self.assertNotIn("USPSGroundAdvantage",
                         m.DOCUMENTED_DOMESTIC_SERVICES)
        self.assertNotIn("USPSGroundAdvantage",
                         m.CARRIER_SERVICES["USPS"])
        self.assertEqual(len(m.DOCUMENTED_DOMESTIC_SERVICES), 10)

    def test_usps_parcel_is_the_ground_advantage_picker_entry(self):
        self.assertEqual(m.CARRIER_SERVICES["USPS"],
                         ["USPSParcel", "USPSPriority"])
        self.assertEqual(m.SERVICE_LABELS["USPSParcel"],
                         "USPS Ground Advantage")

    def test_old_code_kept_for_reading_only(self):
        # Display only: an old payload carrying it still reads as
        # the service name, never as a raw code.
        self.assertEqual(m.SERVICE_LABELS["USPSGroundAdvantage"],
                         "USPS Ground Advantage")

    def test_pick_entry_carries_the_canonical_code(self):
        entry = m._policy_pick_entry(
            _calc_policy("111", ["USPSGroundAdvantage"]))
        self.assertEqual(entry["service_code"], "USPSParcel")
        self.assertEqual(entry["carrier"], "USPS")


if __name__ == "__main__":
    unittest.main()
