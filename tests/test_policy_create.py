"""apk-v41 tests: create a shipping policy inside the app.

Before v41 the app only READ fulfillment policies (GET list); a seller
whose policies did not fit the item had to leave the app for Seller
Hub. v41 adds choose-or-create: a full create form (flat and
calculated, US plus worldwide self-ship international, local pickup),
a mandatory review-before-create (creating a policy changes the eBay
account), then POST /sell/account/v1/fulfillment_policy through the
existing _ebay_call pattern, a dropdown refresh, and selection for
the current draft. Editing existing policies is out of scope.

Official Account API shapes (see docs/ebay-listing-requirements.md):
FulfillmentPolicyRequest (name, marketplaceId, handlingTime,
localPickup, description, shippingOptions), ShippingOption
(optionType, costType, shippingServices), ShippingService
(shippingServiceCode, shippingCost, freeShipping, shipToLocations).
globalShipping is EBAY_GB only and is never sent. eBay International
Shipping is account-level, never a policy field.

PRIVACY: the repo is PUBLIC. Every policy name, ID, and service code
here is dummy. No real policy ID, address, or secret appears in code,
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
            "dom_cost_type": "FLAT_RATE",
            "dom_service_1": "USPSPriority", "dom_service_1_other": "",
            "dom_service_2": "", "dom_service_2_other": "",
            "dom_cost_1": "12.50", "dom_cost_2": "",
            "dom_free_shipping": False}
    form.update(over)
    return form


class BodyBuilderTest(unittest.TestCase):
    def test_flat_domestic_body_shape(self):
        body = m._build_fulfillment_policy_body(_flat_form())
        self.assertEqual(body["name"], "Dummy Doll Policy")
        self.assertEqual(body["marketplaceId"], "EBAY_US")
        self.assertEqual(body["handlingTime"], {"unit": "DAY", "value": 2})
        self.assertNotIn("globalShipping", body)
        self.assertNotIn("localPickup", body)
        opt = body["shippingOptions"][0]
        self.assertEqual((opt["optionType"], opt["costType"]),
                         ("DOMESTIC", "FLAT_RATE"))
        svc = opt["shippingServices"][0]
        self.assertEqual(svc["shippingServiceCode"], "USPSPriority")
        self.assertEqual(svc["shippingCost"],
                         {"currency": "USD", "value": "12.50"})
        self.assertIs(svc["freeShipping"], False)
        self.assertEqual(len(body["shippingOptions"]), 1)

    def test_free_shipping_only_first_domestic(self):
        body = m._build_fulfillment_policy_body(
            _flat_form(dom_free_shipping=True, dom_cost_1=""))
        svc = body["shippingOptions"][0]["shippingServices"][0]
        self.assertIs(svc["freeShipping"], True)
        self.assertEqual(svc["shippingCost"],
                         {"currency": "USD", "value": "0.00"})

    def test_second_service_never_free(self):
        body = m._build_fulfillment_policy_body(_flat_form(
            dom_service_2="UPS3rdDay", dom_cost_2="18.00",
            dom_free_shipping=True, dom_cost_1=""))
        services = body["shippingOptions"][0]["shippingServices"]
        self.assertEqual(len(services), 2)
        self.assertIs(services[0]["freeShipping"], True)
        self.assertIs(services[1]["freeShipping"], False)
        self.assertEqual(services[1]["shippingCost"],
                         {"currency": "USD", "value": "18.00"})

    def test_calculated_carries_no_cost_or_free(self):
        body = m._build_fulfillment_policy_body(_flat_form(
            dom_cost_type="CALCULATED", dom_cost_1="", dom_cost_2=""))
        svc = body["shippingOptions"][0]["shippingServices"][0]
        self.assertNotIn("shippingCost", svc)
        self.assertNotIn("freeShipping", svc)

    def test_international_worldwide_shape(self):
        body = m._build_fulfillment_policy_body(_flat_form(
            ship_international=True, intl_cost_type="FLAT_RATE",
            intl_service_1="USPSPriority", intl_service_1_other="",
            intl_service_2="", intl_service_2_other="",
            intl_cost_1="25.00", intl_cost_2=""))
        self.assertEqual(len(body["shippingOptions"]), 2)
        opt = body["shippingOptions"][1]
        self.assertEqual((opt["optionType"], opt["costType"]),
                         ("INTERNATIONAL", "FLAT_RATE"))
        svc = opt["shippingServices"][0]
        self.assertIs(svc["freeShipping"], False)
        self.assertEqual(svc["shipToLocations"], {
            "regionIncluded": [{"regionName": "Worldwide",
                                "regionType": "WORLDWIDE"}]})

    def test_other_code_typed_in(self):
        body = m._build_fulfillment_policy_body(_flat_form(
            dom_service_1="__OTHER__", dom_service_1_other="USPSParcel"))
        svc = body["shippingOptions"][0]["shippingServices"][0]
        self.assertEqual(svc["shippingServiceCode"], "USPSParcel")

    def test_other_without_typed_code_rejected(self):
        with self.assertRaises(m._PublishValidationError):
            m._build_fulfillment_policy_body(_flat_form(
                dom_service_1="__OTHER__", dom_service_1_other=""))

    def test_pickup_only_body(self):
        body = m._build_fulfillment_policy_body(_flat_form(pickup_only=True))
        self.assertIs(body["localPickup"], True)
        self.assertNotIn("shippingOptions", body)
        self.assertNotIn("handlingTime", body)

    def test_also_pickup_keeps_options_and_handling(self):
        body = m._build_fulfillment_policy_body(
            _flat_form(local_pickup=True))
        self.assertIs(body["localPickup"], True)
        self.assertEqual(len(body["shippingOptions"]), 1)
        self.assertEqual(body["handlingTime"], {"unit": "DAY", "value": 2})

    def test_validation_errors(self):
        bad_forms = [
            _flat_form(name=""),
            _flat_form(name="x" * 101),
            _flat_form(description="d" * 251),
            _flat_form(dom_service_1=""),
            _flat_form(dom_cost_1="abc"),
            _flat_form(dom_cost_1="0"),
            _flat_form(dom_cost_1="-5"),
            _flat_form(dom_cost_type="BOGUS"),
            _flat_form(handling_days=""),
            _flat_form(handling_days="two"),
            _flat_form(handling_days="31"),
            _flat_form(handling_days="-1"),
            _flat_form(ship_international=True, intl_cost_type="FLAT_RATE",
                        intl_service_1="", intl_cost_1=""),
            _flat_form(ship_international=True, intl_cost_type="FLAT_RATE",
                        intl_service_1="USPSPriority", intl_cost_1=""),
        ]
        for form in bad_forms:
            with self.assertRaises(m._PublishValidationError, msg=str(form)):
                m._build_fulfillment_policy_body(form)

    def test_non_dict_form_rejected(self):
        with self.assertRaises(m._PublishValidationError):
            m._build_fulfillment_policy_body(None)


class HarvestTest(unittest.TestCase):
    def test_split_dedupe_and_labels(self):
        policies = [
            {"fulfillmentPolicyId": "111", "name": "Dummy A",
             "marketplaceId": "EBAY_US",
             "shippingOptions": [
                 {"optionType": "DOMESTIC", "costType": "FLAT_RATE",
                  "shippingServices": [
                      {"shippingServiceCode": "USPSPriority"},
                      {"shippingServiceCode": "USPSPriority"},
                      {"shippingServiceCode": "ZZZCustom"}]},
                 {"optionType": "INTERNATIONAL", "costType": "FLAT_RATE",
                  "shippingServices": [
                      {"shippingServiceCode": "USPSPriority"}]}]},
            {"fulfillmentPolicyId": "222", "name": "Dummy B",
             "marketplaceId": "EBAY_US",
             "shippingOptions": [
                 {"optionType": "DOMESTIC", "costType": "CALCULATED",
                  "shippingServices": [{"shippingServiceCode": "FedEx2Day"}]}]},
        ]
        out = m._harvest_shipping_services(policies)
        dom = {s["code"]: s["label"] for s in out["domestic"]}
        self.assertEqual(dom["USPSPriority"], "USPS Priority Mail")
        self.assertEqual(dom["ZZZCustom"], "ZZZCustom")
        self.assertIn("UPS3rdDay", dom)  # documented set, always present
        self.assertIn("FedEx2Day", dom)
        intl = {s["code"]: s["label"] for s in out["international"]}
        self.assertEqual(intl, {"USPSPriority": "USPS Priority Mail"})
        # No documented codes leak into international.
        self.assertNotIn("UPS3rdDay", intl)

    def test_empty_policies_still_offer_documented(self):
        out = m._harvest_shipping_services([])
        self.assertEqual([s["code"] for s in out["domestic"]],
                         sorted(m.DOCUMENTED_DOMESTIC_SERVICES))
        self.assertEqual(out["international"], [])


class CreateEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = m.app.test_client()

    def _call(self, form, fake_call):
        with mock.patch.object(m, "_ebay_access_token",
                               return_value="DUMMYTOKEN"), \
                mock.patch.object(m, "_ebay_call", side_effect=fake_call):
            return self.client.post(
                "/api/ebay/fulfillment-policies/create",
                data=json.dumps(form), content_type="application/json")

    def _list_body(self):
        return {"fulfillmentPolicies": [
            {"fulfillmentPolicyId": "777", "name": "Dummy Doll Policy",
             "marketplaceId": "EBAY_US",
             "shippingOptions": [{"optionType": "DOMESTIC",
                                  "costType": "FLAT_RATE"}]}]}

    def test_success_returns_id_and_fresh_list(self):
        seen = {}

        def fake_call(method, path, token, payload=None):
            seen[(method, path.split("?")[0])] = payload
            if method == "POST":
                self.assertEqual(path, "/sell/account/v1/fulfillment_policy")
                self.assertEqual(payload["marketplaceId"], "EBAY_US")
                self.assertNotIn("globalShipping", payload)
                return {"fulfillmentPolicyId": "777"}
            return self._list_body()

        r = self._call(_flat_form(), fake_call)
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data["policy_id"], "777")
        self.assertTrue(any(p["id"] == "777" for p in data["policies"]))

    def test_success_without_id_resolves_by_name(self):
        def fake_call(method, path, token, payload=None):
            if method == "POST":
                return {}
            if "get_by_policy_name" in path:
                return {"fulfillmentPolicyId": "777",
                        "name": "Dummy Doll Policy",
                        "marketplaceId": "EBAY_US",
                        "shippingOptions": [{"optionType": "DOMESTIC",
                                             "costType": "FLAT_RATE"}]}
            return self._list_body()

        r = self._call(_flat_form(), fake_call)
        self.assertEqual(r.get_json()["policy_id"], "777")

    def test_name_taken_is_plain_409_and_keeps_form(self):
        def fake_call(method, path, token, payload=None):
            raise m._EbayApiError("name taken", status=409,
                                  error_id="20418")

        r = self._call(_flat_form(), fake_call)
        self.assertEqual(r.status_code, 409)
        data = r.get_json()
        self.assertTrue(data["name_taken"])
        self.assertIn("already used", data["error"])

    def test_403_maps_to_reconnect(self):
        def fake_call(method, path, token, payload=None):
            raise m._EbayApiError("forbidden", status=403,
                                  error_id="190013")

        r = self._call(_flat_form(), fake_call)
        data = r.get_json()
        self.assertTrue(data["needs_scope_reconnect"])

    def test_validation_error_posts_nothing(self):
        calls = []

        def fake_call(method, path, token, payload=None):
            calls.append((method, path))
            return {}

        r = self._call(_flat_form(name=""), fake_call)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(calls, [])

    def test_retry_reuses_lost_success_without_post(self):
        calls = []

        def fake_call(method, path, token, payload=None):
            calls.append((method, path))
            if "get_by_policy_name" in path:
                return {"fulfillmentPolicyId": "777",
                        "name": "Dummy Doll Policy",
                        "marketplaceId": "EBAY_US",
                        "shippingOptions": [{"optionType": "DOMESTIC",
                                             "costType": "FLAT_RATE"}]}
            return self._list_body()

        r = self._call(_flat_form(retry=True), fake_call)
        self.assertEqual(r.get_json()["policy_id"], "777")
        self.assertFalse(any(c[0] == "POST" for c in calls))

    def test_network_failure_recovers_by_name(self):
        def fake_call(method, path, token, payload=None):
            if method == "POST":
                raise RuntimeError("Could not reach eBay: boom")
            if "get_by_policy_name" in path:
                return {"fulfillmentPolicyId": "777",
                        "name": "Dummy Doll Policy",
                        "marketplaceId": "EBAY_US",
                        "shippingOptions": [{"optionType": "DOMESTIC",
                                             "costType": "FLAT_RATE"}]}
            return self._list_body()

        r = self._call(_flat_form(), fake_call)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["policy_id"], "777")

    def test_network_failure_unconfirmed_when_nothing_found(self):
        def fake_call(method, path, token, payload=None):
            if method == "POST":
                raise RuntimeError("Could not reach eBay: boom")
            if "get_by_policy_name" in path:
                raise m._EbayApiError("not found", status=404)
            return self._list_body()

        r = self._call(_flat_form(), fake_call)
        self.assertEqual(r.status_code, 502)
        data = r.get_json()
        self.assertTrue(data["unconfirmed"])
        self.assertIn("Seller Hub", data["error"])

    def test_scope_reconnect_on_token(self):
        with mock.patch.object(
                m, "_ebay_access_token",
                side_effect=m._NeedsScopeReconnect(m.SCOPE_RECONNECT_MSG)):
            r = self.client.post(
                "/api/ebay/fulfillment-policies/create",
                data=json.dumps(_flat_form()),
                content_type="application/json")
        self.assertTrue(r.get_json()["needs_scope_reconnect"])


class ServicesEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = m.app.test_client()

    def test_services_split_by_option_type(self):
        body = {"fulfillmentPolicies": [
            {"fulfillmentPolicyId": "111", "name": "Dummy A",
             "marketplaceId": "EBAY_US",
             "shippingOptions": [
                 {"optionType": "DOMESTIC", "costType": "FLAT_RATE",
                  "shippingServices": [{"shippingServiceCode": "ZZZDom"}]},
                 {"optionType": "INTERNATIONAL", "costType": "FLAT_RATE",
                  "shippingServices": [{"shippingServiceCode": "ZZZIntl"}]}]}]}
        with mock.patch.object(m, "_ebay_access_token",
                               return_value="DUMMYTOKEN"), \
                mock.patch.object(m, "_ebay_call", return_value=body):
            r = self.client.get("/api/ebay/shipping-services")
        data = r.get_json()
        self.assertIn("ZZZDom", [s["code"] for s in data["domestic"]])
        self.assertEqual([s["code"] for s in data["international"]],
                         ["ZZZIntl"])


if __name__ == "__main__":
    unittest.main()
