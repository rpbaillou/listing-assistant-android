"""apk-v50 tests: the Nov 8 shipping audit fixes (F1-F5, F7, F8) and the
never-again enum guard.

F1: UPSGroundSaver is NOT in eBay's Trading API ShippingServiceCodeType
enum and is unproven on create, so it leaves the create form's service
options (matching and display keep it). FedExSmartPost deliberately
stays offered until the live retry on 1.48 settles it.
F2: typed Other codes match case-insensitively (and by display label)
against the known list; an unknown typed code is refused in plain
words instead of reaching eBay and failing with 20403.
F3: the policy-for-service matcher reports a NOT_SPECIFIED policy by
name instead of claiming no policy exists.
F4: the publish gate rejects a policy whose domestic option has no
shipping services, before any photo work or offer creation.
F7: eBay's parameters text reaches the screen on EVERY _plain_ebay_error
branch, including the 190013, business-policies, opt/program, and
401/403 branches that used to drop it.
F8: draft save canonicalizes ship_service_code, so the rejected code
USPSGroundAdvantage can never sit in storage.
Guard: the picker catalog is a subset of eBay's enum (pinned fixture),
and harvested codes outside the enum are flagged as unproven on
create — today exactly FedExSmartPost and UPSGroundSaver.

PRIVACY: the repo is PUBLIC. Every policy ID and service code here is
dummy. No real policy ID, address, or secret appears in code,
fixtures, or test output.

eBay is always mocked; no test here reaches the network.
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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as m  # noqa: E402  (imports cleanly; __main__ guard prevents serving)
from ebay_shipping_service_codes import (  # noqa: E402
    EBAY_SHIPPING_SERVICE_CODES)


def tearDownModule():
    sys.modules.pop("app", None)


def _fake_resp(status, content=b"", json_body=None, headers=None):
    r = mock.Mock()
    r.status_code = status
    r.content = content
    r.text = content.decode("utf-8", "replace") if isinstance(
        content, (bytes, bytearray)) else content
    r.headers = headers or {}
    if json_body is not None:
        r.json = mock.Mock(return_value=json_body)
    else:
        r.json = mock.Mock(side_effect=ValueError("no json"))
    return r


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


def _policy(pid, cost_type, services, name="Dummy"):
    return {"fulfillmentPolicyId": pid, "name": name,
            "marketplaceId": "EBAY_US",
            "shippingOptions": [{"optionType": "DOMESTIC",
                                 "costType": cost_type,
                                 "shippingServices": [
                                     {"shippingServiceCode": s}
                                     for s in services]}]}


class CreateExcludedTest(unittest.TestCase):
    """F1: UPSGroundSaver leaves the create form, stays for match/display."""

    def setUp(self):
        self.client = m.app.test_client()

    def _services_call(self, fake_list):
        def fake_call(method, path, token, payload=None):
            return fake_list
        with mock.patch.object(m, "_ebay_access_token",
                               return_value="DUMMYTOKEN"), \
                mock.patch.object(m, "_ebay_call", side_effect=fake_call):
            return self.client.get("/api/ebay/shipping-services")

    def test_create_lists_exclude_ups_ground_saver_but_keep_it_for_match(self):
        # His "Calculated 2lb" policy carries UPSGroundSaver (primary),
        # FedExSmartPost, and USPS Ground Advantage.
        policies = {"fulfillmentPolicies": [
            _policy("111", "CALCULATED",
                    ["UPSGroundSaver", "FedExSmartPost", "USPSParcel"],
                    name="Dummy Calculated 2lb")]}
        r = self._services_call(policies)
        data = r.get_json()
        self.assertEqual(r.status_code, 200)
        domestic = [s["code"] for s in data["domestic"]]
        create = [s["code"] for s in data["create_domestic"]]
        self.assertIn("UPSGroundSaver", domestic)
        self.assertNotIn("UPSGroundSaver", create)
        # FedExSmartPost stays offered until the live retry settles it.
        self.assertIn("FedExSmartPost", create)
        self.assertIn("USPSParcel", create)

    def test_create_body_refuses_ups_ground_saver_from_dropdown(self):
        with self.assertRaises(m._PublishValidationError) as ctx:
            m._build_fulfillment_policy_body(
                _flat_form(dom_service_1="UPSGroundSaver"))
        self.assertIn("UPS Ground Saver", str(ctx.exception))
        self.assertIn("cannot be used for a new policy yet",
                      str(ctx.exception))

    def test_create_body_refuses_ups_ground_saver_typed_in_other(self):
        with self.assertRaises(m._PublishValidationError) as ctx:
            m._build_fulfillment_policy_body(
                _flat_form(dom_service_1="__OTHER__",
                           dom_service_1_other="UPS Ground Saver"))
        self.assertIn("UPS Ground Saver", str(ctx.exception))

    def test_create_body_still_accepts_fedex_smart_post(self):
        body = m._build_fulfillment_policy_body(
            _flat_form(dom_service_1="FedExSmartPost"))
        codes = [s["shippingServiceCode"]
                 for s in body["shippingOptions"][0]["shippingServices"]]
        self.assertEqual(codes, ["FedExSmartPost"])


class TypedCodeTest(unittest.TestCase):
    """F2: typed Other codes match case-insensitively, by code or label."""

    def test_canonical_lowercase_code(self):
        self.assertEqual(m._canonical_service_code("uspsparcel"), "USPSParcel")
        self.assertEqual(m._canonical_service_code("fedexsmartpost"),
                         "FedExSmartPost")
        self.assertEqual(m._canonical_service_code("upsgroundsaver"),
                         "UPSGroundSaver")

    def test_canonical_display_label_with_spaces(self):
        self.assertEqual(m._canonical_service_code("USPS Ground Advantage"),
                         "USPSParcel")
        self.assertEqual(m._canonical_service_code("usps ground advantage"),
                         "USPSParcel")
        self.assertEqual(m._canonical_service_code("UPS Ground Saver"),
                         "UPSGroundSaver")
        self.assertEqual(m._canonical_service_code("  FedEx Ground Economy  "),
                         "FedExSmartPost")

    def test_canonical_rejected_code_still_maps(self):
        self.assertEqual(m._canonical_service_code("USPSGroundAdvantage"),
                         "USPSParcel")
        self.assertEqual(m._canonical_service_code("uspsgroundadvantage"),
                         "USPSParcel")

    def test_canonical_unknown_passes_through(self):
        self.assertEqual(m._canonical_service_code("BogusCode"), "BogusCode")

    def test_resolve_typed_known_and_unknown(self):
        self.assertEqual(m._resolve_typed_service_code("uspsparcel"),
                         ("USPSParcel", True))
        self.assertEqual(m._resolve_typed_service_code("BogusCode"),
                         ("BogusCode", False))
        self.assertEqual(m._resolve_typed_service_code(""), ("", False))

    def test_create_body_refuses_unknown_typed_code(self):
        with self.assertRaises(m._PublishValidationError) as ctx:
            m._build_fulfillment_policy_body(
                _flat_form(dom_service_1="__OTHER__",
                           dom_service_1_other="BogusCode"))
        self.assertIn("BogusCode", str(ctx.exception))
        self.assertIn("may not know the shipping code", str(ctx.exception))

    def test_create_body_accepts_lowercase_typed_code(self):
        body = m._build_fulfillment_policy_body(
            _flat_form(dom_service_1="__OTHER__",
                       dom_service_1_other="uspsparcel"))
        codes = [s["shippingServiceCode"]
                 for s in body["shippingOptions"][0]["shippingServices"]]
        self.assertEqual(codes, ["USPSParcel"])


class NotSpecifiedMatcherTest(unittest.TestCase):
    """F3: a NOT_SPECIFIED policy carrying the service is reported by name."""

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

    def test_not_specified_policy_reported_by_name(self):
        policies = {"fulfillmentPolicies": [
            _policy("111", "NOT_SPECIFIED", ["USPSPriority"],
                    name="Dummy Unset Price")]}
        r = self._call({"carrier": "USPS", "service_code": "USPSPriority"},
                       policies)
        data = r.get_json()
        self.assertIsNone(data["policy_id"])
        self.assertEqual(data["not_specified_policy_id"], "111")
        self.assertEqual(data["not_specified_name"], "Dummy Unset Price")

    def test_flat_beats_not_specified(self):
        policies = {"fulfillmentPolicies": [
            _policy("111", "NOT_SPECIFIED", ["USPSPriority"],
                    name="Dummy Unset Price"),
            _policy("222", "FLAT_RATE", ["USPSPriority"],
                    name="Dummy Flat")]}
        r = self._call({"carrier": "USPS", "service_code": "USPSPriority"},
                       policies)
        data = r.get_json()
        self.assertEqual(data["flat_policy_id"], "222")
        self.assertIsNone(data["not_specified_policy_id"])

    def test_calculated_still_beats_not_specified(self):
        policies = {"fulfillmentPolicies": [
            _policy("111", "NOT_SPECIFIED", ["USPSPriority"],
                    name="Dummy Unset Price"),
            _policy("222", "CALCULATED", ["USPSPriority"],
                    name="Dummy Calculated")]}
        r = self._call({"carrier": "USPS", "service_code": "USPSPriority"},
                       policies)
        data = r.get_json()
        self.assertEqual(data["policy_id"], "222")
        self.assertIsNone(data["not_specified_policy_id"])


class GateEmptyServicesTest(unittest.TestCase):
    """F4: the publish gate rejects a policy with no shipping services."""

    def _gate(self, policy):
        with mock.patch.object(m, "_read_fulfillment_policy",
                               return_value=(policy, [])):
            return m._check_fulfillment_policy(
                "DUMMYTOKEN", "111",
                {"weight_value": "4", "weight_unit": "OUNCE",
                 "length": "12", "width": "9", "height": "1"})

    def test_empty_services_list_raises_pick_signal(self):
        policy = _policy("111", "CALCULATED", [])
        with self.assertRaises(m._NeedsFulfillmentPolicyChoice) as ctx:
            self._gate(policy)
        self.assertIn("no shipping services", str(ctx.exception))

    def test_missing_services_key_raises_pick_signal(self):
        policy = {"fulfillmentPolicyId": "111", "name": "Dummy",
                  "marketplaceId": "EBAY_US",
                  "shippingOptions": [{"optionType": "DOMESTIC",
                                       "costType": "CALCULATED"}]}
        with self.assertRaises(m._NeedsFulfillmentPolicyChoice) as ctx:
            self._gate(policy)
        self.assertIn("no shipping services", str(ctx.exception))

    def test_policy_with_services_passes_gate(self):
        policy = _policy("111", "CALCULATED", ["USPSParcel"])
        cost_type, block = self._gate(policy)
        self.assertEqual(cost_type, "CALCULATED")
        self.assertIsNotNone(block)

    def test_pickup_only_still_gets_the_pickup_message(self):
        policy = {"fulfillmentPolicyId": "111", "name": "Dummy Pickup",
                  "marketplaceId": "EBAY_US", "localPickup": True,
                  "shippingOptions": []}
        with self.assertRaises(m._NeedsFulfillmentPolicyChoice) as ctx:
            self._gate(policy)
        self.assertIn("no shippable service", str(ctx.exception))
        self.assertNotIn("no shipping services", str(ctx.exception))


class PlainErrorParamsTest(unittest.TestCase):
    """F7: eBay's parameters reach the screen on every error branch."""

    def _err(self, status, error_id, message, params=None):
        body = {"errors": [{"errorId": error_id, "message": message}]}
        if params is not None:
            body["errors"][0]["parameters"] = params
        return m._plain_ebay_error(_fake_resp(status, json_body=body))

    def test_190013_branch_carries_parameters(self):
        text = self._err(403, "190013", "Missing scope.",
                         [{"name": "fieldName", "value": "sell.account"}])
        self.assertIn("fieldName: sell.account", text)
        self.assertIn("190013", text)

    def test_business_policies_branch_carries_parameters(self):
        text = self._err(400, "25710", "Business policies are required.",
                         [{"name": "fieldName", "value": "fulfillmentPolicyId"}])
        self.assertIn("fieldName: fulfillmentPolicyId", text)

    def test_opt_program_branch_carries_parameters(self):
        text = self._err(400, "20403", "You must opt in to the program.",
                         [{"name": "program", "value": "business-policies"}])
        self.assertIn("program: business-policies", text)

    def test_401_branch_carries_parameters(self):
        text = self._err(401, "1001", "Unauthorized.",
                         [{"name": "fieldName", "value": "Authorization"}])
        self.assertIn("fieldName: Authorization", text)

    def test_no_parameters_still_reads_clean(self):
        text = self._err(403, "190013", "Missing scope.")
        self.assertIn("190013", text)
        self.assertNotIn("Details: Details", text)


class DraftSaveCanonicalTest(unittest.TestCase):
    """F8: draft save canonicalizes ship_service_code."""

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

    def _write_draft(self, draft_id):
        d = {"id": draft_id, "title": "Dummy", "photos": [], "price": ""}
        with open(os.path.join(m.DRAFTS_DIR, draft_id + ".json"),
                  "w", encoding="utf-8") as f:
            json.dump(d, f)
        return d

    def test_save_canonicalizes_rejected_code(self):
        self._write_draft("d1")
        r = self.client.put("/api/drafts/d1",
                            json={"ship_service_code": "USPSGroundAdvantage"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["draft"]["ship_service_code"],
                         "USPSParcel")

    def test_save_canonicalizes_lowercase_code(self):
        self._write_draft("d1")
        r = self.client.put("/api/drafts/d1",
                            json={"ship_service_code": "uspsparcel"})
        self.assertEqual(r.get_json()["draft"]["ship_service_code"],
                         "USPSParcel")

    def test_save_keeps_unproven_code_untouched(self):
        self._write_draft("d1")
        r = self.client.put("/api/drafts/d1",
                            json={"ship_service_code": "FedExSmartPost"})
        self.assertEqual(r.get_json()["draft"]["ship_service_code"],
                         "FedExSmartPost")


class EnumGuardTest(unittest.TestCase):
    """The never-again guard: the picker catalog is a subset of eBay's
    enum, and harvested codes outside the enum are flagged."""

    def test_picker_catalog_is_a_subset_of_ebay_enum(self):
        missing = [c for c in m.DOCUMENTED_DOMESTIC_SERVICES
                   if c not in EBAY_SHIPPING_SERVICE_CODES]
        self.assertEqual(missing, [])

    def test_unproven_codes_are_absent_from_the_enum(self):
        for code in ("USPSGroundAdvantage", "FedExSmartPost",
                     "UPSGroundSaver"):
            self.assertNotIn(code, EBAY_SHIPPING_SERVICE_CODES)

    def test_harvested_codes_outside_enum_are_flagged(self):
        # His real policies' codes, as harvested: USPSParcel and
        # UPSGround are in the enum; FedExSmartPost and UPSGroundSaver
        # are not, and must be flagged as unproven on create.
        policies = [_policy("111", "CALCULATED",
                            ["UPSGroundSaver", "FedExSmartPost", "USPSParcel"]),
                    _policy("222", "FLAT_RATE", ["UPSGround", "USPSParcel"])]
        harvested = m._harvest_shipping_services(policies)
        codes = {s["code"] for s in
                 harvested["domestic"] + harvested["international"]}
        unproven = sorted(c for c in codes
                          if c not in EBAY_SHIPPING_SERVICE_CODES)
        self.assertEqual(unproven, ["FedExSmartPost", "UPSGroundSaver"])

    def test_all_valid_harvested_codes_pass_the_flag(self):
        policies = [_policy("111", "CALCULATED", ["USPSParcel", "UPSGround"])]
        harvested = m._harvest_shipping_services(policies)
        codes = {s["code"] for s in harvested["domestic"]}
        unproven = [c for c in codes
                    if c not in EBAY_SHIPPING_SERVICE_CODES]
        self.assertEqual(unproven, [])


if __name__ == "__main__":
    unittest.main()
