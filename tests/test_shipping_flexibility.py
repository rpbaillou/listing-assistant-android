"""apk-v42 tests: shipping flexibility like eBay's lister.

Carrier picker (USPS/UPS/FedEx) + service picker per carrier; weight
as pounds + ounces boxes, always visible; calculated shipping
requires the full package; flat/free saves the weight for the
seller's records and never sends it to eBay; picking carrier +
service finds the matching domestic-only CALCULATED policy or opens
the create form (review-before-create still stands); every service
code is attested (eBay's own docs or the account harvest), never
invented.

PRIVACY: the repo is PUBLIC. Every policy ID, offer ID, and address
here is obviously dummy (111/222/999). No real policy ID, address,
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
def _intl_shell(shape):
    """v43: an INTERNATIONAL option shell with no real service — the
    shape eBay may serialize on a domestic-only policy."""
    if shape == "missing":
        return {"optionType": "INTERNATIONAL", "costType": "FLAT_RATE"}
    if shape == "empty":
        return {"optionType": "INTERNATIONAL", "costType": "FLAT_RATE",
                "shippingServices": []}
    if shape == "blank":
        return {"optionType": "INTERNATIONAL", "costType": "FLAT_RATE",
                "shippingServices": [{"shippingServiceCode": ""}]}
    raise ValueError(shape)


def _calc_policy(pid, services, intl=False, intl_shell=None):
    opts = [{"optionType": "DOMESTIC", "costType": "CALCULATED",
             "shippingServices": [{"shippingServiceCode": s}
                                  for s in services]}]
    if intl:
        opts.append({"optionType": "INTERNATIONAL", "costType": "FLAT_RATE",
                     "shippingServices": [{"shippingServiceCode": "USPSPriority"}]})
    if intl_shell is not None:
        opts.append(_intl_shell(intl_shell))
    return {"fulfillmentPolicyId": pid, "name": "Dummy " + pid,
            "marketplaceId": "EBAY_US", "shippingOptions": opts}


def _flat_policy(pid, services, intl=False, intl_shell=None):
    opts = [{"optionType": "DOMESTIC",
             "costType": "FLAT_RATE",
             "shippingServices": [
                 {"shippingServiceCode": s}
                 for s in services]}]
    if intl:
        opts.append({"optionType": "INTERNATIONAL", "costType": "FLAT_RATE",
                     "shippingServices": [{"shippingServiceCode": "USPSPriority"}]})
    if intl_shell is not None:
        opts.append(_intl_shell(intl_shell))
    return {"fulfillmentPolicyId": pid, "name": "Dummy " + pid,
            "marketplaceId": "EBAY_US", "shippingOptions": opts}


POLICY_LIST = {"fulfillmentPolicies": [
    _calc_policy("111", ["USPSPriority"]),
    _calc_policy("222", ["USPSPriority"], intl=True),
    _flat_policy("333", ["USPSPriority"]),
    _calc_policy("444", ["UPSGround", "USPSPriority"]),
]}


class CatalogTest(unittest.TestCase):
    """Every service code is attested, never invented."""

    EXPECTED = [
        "USPSParcel", "USPSPriority",
        "UPSGround", "UPS3rdDay", "UPS2DayAirAM", "UPSNextDay",
        "FedExGround", "FedExHomeDelivery", "FedEx2Day",
        "FedExExpressSaver",
    ]

    def test_documented_set_is_exactly_the_attested_catalog(self):
        self.assertEqual(list(m.DOCUMENTED_DOMESTIC_SERVICES), self.EXPECTED)

    def test_every_documented_code_has_a_plain_label(self):
        for code in m.DOCUMENTED_DOMESTIC_SERVICES:
            self.assertIn(code, m.SERVICE_LABELS)
            self.assertTrue(m.SERVICE_LABELS[code])
            self.assertNotEqual(m.SERVICE_LABELS[code], code)

    def test_harvested_only_codes_have_plain_labels(self):
        # v43: UPSGroundSaver and FedExSmartPost are harvested from his
        # own policies (not picker codes), but they must not render as
        # raw codes. Labels are display-only; the code itself is
        # always sent verbatim. They stay out of the picker catalog.
        self.assertEqual(m.SERVICE_LABELS["UPSGroundSaver"],
                         "UPS Ground Saver")
        self.assertEqual(m.SERVICE_LABELS["FedExSmartPost"],
                         "FedEx Ground Economy")
        self.assertNotIn("UPSGroundSaver", m.DOCUMENTED_DOMESTIC_SERVICES)
        self.assertNotIn("FedExSmartPost", m.DOCUMENTED_DOMESTIC_SERVICES)

    def test_carrier_map_covers_the_whole_catalog(self):
        mapped = [c for carrier in m.CARRIERS
                  for c in m.CARRIER_SERVICES[carrier]]
        self.assertEqual(sorted(mapped), sorted(self.EXPECTED))
        # eBay's own ShippingService page examples stay in the set.
        self.assertIn("USPSPriority", mapped)
        self.assertIn("FedEx2Day", mapped)
        self.assertIn("UPS3rdDay", mapped)

    def test_canonical_carrier_accepts_any_case(self):
        self.assertEqual(m._canonical_carrier("usps"), "USPS")
        self.assertEqual(m._canonical_carrier("fedex"), "FedEx")
        self.assertEqual(m._canonical_carrier("FEDEX"), "FedEx")
        self.assertEqual(m._canonical_carrier("UPS"), "UPS")
        self.assertIsNone(m._canonical_carrier("DHL"))
        self.assertIsNone(m._canonical_carrier(""))
        self.assertIsNone(m._canonical_carrier(None))

    def test_carrier_for_service_code(self):
        self.assertEqual(m._carrier_for_service_code("USPSPriority"), "USPS")
        self.assertEqual(m._carrier_for_service_code("UPSGround"), "UPS")
        self.assertEqual(m._carrier_for_service_code("FedEx2Day"), "FedEx")
        self.assertIsNone(m._carrier_for_service_code("ZZZCustom"))
        self.assertIsNone(m._carrier_for_service_code(""))
        self.assertIsNone(m._carrier_for_service_code(None))

    def test_harvest_groups_carriers_and_keeps_other(self):
        out = m._harvest_shipping_services(POLICY_LIST["fulfillmentPolicies"])
        usps = [s["code"] for s in out["carriers"]["USPS"]]
        self.assertIn("USPSPriority", usps)
        # Documented order first: Ground Advantage (as USPSParcel)
        # before Priority.
        self.assertLess(usps.index("USPSParcel"),
                        usps.index("USPSPriority"))
        # v49: the rejected code never reaches the picker.
        self.assertNotIn("USPSGroundAdvantage", usps)
        self.assertIn("UPSGround",
                      [s["code"] for s in out["carriers"]["UPS"]])
        self.assertIn("FedEx2Day",
                      [s["code"] for s in out["carriers"]["FedEx"]])
        self.assertEqual(out["other"], [])
        out2 = m._harvest_shipping_services(
            [_flat_policy("999", ["ZZZCustom"])])
        self.assertEqual([s["code"] for s in out2["other"]], ["ZZZCustom"])
        # The flat domestic/international shape is unchanged for v41.
        self.assertIn("ZZZCustom",
                      [s["code"] for s in out2["domestic"]])

    def test_pick_entry_carries_service_and_carrier(self):
        entry = m._policy_pick_entry(_calc_policy("111", ["USPSPriority"]))
        self.assertEqual(entry["service_code"], "USPSPriority")
        self.assertEqual(entry["carrier"], "USPS")
        entry2 = m._policy_pick_entry(_flat_policy("333", ["UPSGround"]))
        self.assertEqual(entry2["service_code"], "UPSGround")
        self.assertEqual(entry2["carrier"], "UPS")
        entry3 = m._policy_pick_entry({"fulfillmentPolicyId": "555",
                                       "name": "Pickup",
                                       "marketplaceId": "EBAY_US",
                                       "localPickup": True,
                                       "shippingOptions": []})
        self.assertEqual(entry3["service_code"], "")
        self.assertEqual(entry3["carrier"], "")


class LbOzTest(unittest.TestCase):
    """Pounds + ounces boxes combine into eBay's weight shape."""

    def test_lb_and_oz_combine_to_total_ounces(self):
        out = m._package_from_lb_oz({
            "package_weight_lb": "2", "package_weight_oz": "8",
            "package_length": "12", "package_width": "9",
            "package_height": "1"})
        self.assertEqual(out["weight_value"], "40")
        self.assertEqual(out["weight_unit"], "OUNCE")

    def test_pounds_alone_stay_pounds(self):
        out = m._package_from_lb_oz({
            "package_weight_lb": "2", "package_weight_oz": "",
            "package_length": "12", "package_width": "9",
            "package_height": "1"})
        self.assertEqual((out["weight_value"], out["weight_unit"]),
                         ("2", "POUND"))

    def test_ounces_alone_stay_ounces(self):
        out = m._package_from_lb_oz({
            "package_weight_lb": "", "package_weight_oz": "8",
            "package_length": "", "package_width": "",
            "package_height": ""})
        self.assertEqual((out["weight_value"], out["weight_unit"]),
                         ("8", "OUNCE"))

    def test_fractional_pounds_stay_exact(self):
        out = m._package_from_lb_oz({
            "package_weight_lb": "2.5", "package_weight_oz": "",
            "package_length": "12", "package_width": "9",
            "package_height": "1"})
        self.assertEqual((out["weight_value"], out["weight_unit"]),
                         ("2.5", "POUND"))

    def test_empty_is_none(self):
        self.assertIsNone(m._package_from_lb_oz({
            "package_weight_lb": "", "package_weight_oz": "",
            "package_length": "", "package_width": "",
            "package_height": ""}))

    def test_non_numeric_weight_is_a_plain_error(self):
        with self.assertRaises(m._PublishValidationError) as ctx:
            m._package_from_lb_oz({
                "package_weight_lb": "abc", "package_weight_oz": "",
                "package_length": "12", "package_width": "9",
                "package_height": "1"})
        self.assertIn("numbers", str(ctx.exception))

    def test_draft_package_prefers_lb_oz_over_legacy(self):
        d = {"package_weight_lb": "2", "package_weight_oz": "8",
             "package_weight_value": "4", "package_weight_unit": "OUNCE",
             "package_length": "12", "package_width": "9",
             "package_height": "1"}
        self.assertEqual(m._draft_package(d)["weight_value"], "40")
        self.assertEqual(m._draft_package(d)["weight_unit"], "OUNCE")

    def test_draft_package_falls_back_to_legacy(self):
        d = {"package_weight_value": "4", "package_weight_unit": "OUNCE",
             "package_length": "12", "package_width": "9",
             "package_height": "1"}
        self.assertEqual(m._draft_package(d),
                         {"weight_value": "4", "weight_unit": "OUNCE",
                          "length": "12", "width": "9", "height": "1"})

    def test_draft_package_dims_only_keep_legacy_weight(self):
        # Regression: dims alone must not route the block through the
        # lb/oz branch, or the legacy weight comes back empty.
        d = {"package_weight_value": "4", "package_weight_unit": "OUNCE",
             "package_length": "12", "package_width": "9",
             "package_height": "1"}
        self.assertEqual(m._draft_package(d)["weight_value"], "4")
        self.assertIsNone(m._draft_package({"package_weight_unit": "OUNCE"}))
        self.assertIsNone(m._draft_package(
            {"package_weight_lb": "", "package_weight_oz": ""}))


class PackageValidationTest(unittest.TestCase):
    """Calculated needs the full package; flat tolerates a partial one."""

    FULL = {"weight_value": "4", "weight_unit": "OUNCE",
            "length": "12", "width": "9", "height": "1"}
    WEIGHT_ONLY = {"weight_value": "2", "weight_unit": "POUND",
                   "length": "", "width": "", "height": ""}

    def test_strict_rejects_partial(self):
        with self.assertRaises(m._PublishValidationError):
            m._validate_package(dict(self.WEIGHT_ONLY))

    def test_lenient_accepts_partial(self):
        self.assertIsNone(m._validate_package_lenient(dict(self.WEIGHT_ONLY)))
        self.assertIsNone(m._validate_package_lenient(dict(self.FULL)))
        self.assertIsNone(m._validate_package_lenient({}))

    def test_lenient_still_flags_garbage(self):
        with self.assertRaises(m._PublishValidationError):
            m._validate_package_lenient(dict(self.WEIGHT_ONLY,
                                             weight_value="abc"))
        with self.assertRaises(m._PublishValidationError):
            m._validate_package_lenient(dict(self.WEIGHT_ONLY,
                                             weight_value="0"))
        with self.assertRaises(m._PublishValidationError):
            m._validate_package_lenient(dict(self.WEIGHT_ONLY,
                                             weight_value="500",
                                             weight_unit="POUND"))
        with self.assertRaises(m._PublishValidationError):
            m._validate_package_lenient(dict(self.FULL, length="200"))

    def _gate(self, policy, package):
        with mock.patch.object(m, "_read_fulfillment_policy",
                               return_value=(policy, None)):
            return m._check_fulfillment_policy("tok", "111", package)

    def test_flat_with_partial_package_never_sends_it(self):
        flat = _flat_policy("111", ["USPSPriority"])
        cost, block = self._gate(flat, dict(self.WEIGHT_ONLY))
        self.assertEqual(cost, "FLAT_RATE")
        self.assertIsNone(block)

    def test_flat_with_garbage_still_flags_it(self):
        flat = _flat_policy("111", ["USPSPriority"])
        with self.assertRaises(m._PublishValidationError):
            self._gate(flat, dict(self.WEIGHT_ONLY, weight_value="abc"))

    def test_calculated_with_partial_package_raises(self):
        calc = _calc_policy("111", ["USPSPriority"])
        with self.assertRaises(m._PublishValidationError):
            self._gate(calc, dict(self.WEIGHT_ONLY))

    def test_calculated_with_full_package_sends_block(self):
        calc = _calc_policy("111", ["USPSPriority"])
        cost, block = self._gate(calc, dict(self.FULL))
        self.assertEqual(cost, "CALCULATED")
        self.assertEqual(block["weight"], {"value": 4.0, "unit": "OUNCE"})


class PolicyForServiceTest(unittest.TestCase):
    """POST /api/ebay/policy-for-service finds the matching policy."""

    def setUp(self):
        self.client = m.app.test_client()

    def _call(self, body, fake_list=None, fake_error=None):
        def fake_call(method, path, token, payload=None):
            if fake_error is not None:
                raise fake_error
            return fake_list if fake_list is not None else POLICY_LIST
        with mock.patch.object(m, "_ebay_access_token",
                               return_value="DUMMYTOKEN"), \
                mock.patch.object(m, "_ebay_call", side_effect=fake_call):
            return self.client.post("/api/ebay/policy-for-service",
                                    json=body)

    def test_match_is_first_domestic_only_calculated(self):
        r = self._call({"carrier": "USPS", "service_code": "USPSPriority"})
        self.assertEqual(r.status_code, 200)
        # 111 matches; 222 has the service but also self-ship
        # international, 333 is flat, 444 also matches but is later.
        self.assertEqual(r.get_json()["policy_id"], "111")
        self.assertEqual(r.get_json()["name"], "Dummy 111")

    def test_match_counts_any_domestic_service(self):
        r = self._call({"carrier": "UPS", "service_code": "UPSGround"})
        self.assertEqual(r.get_json()["policy_id"], "444")

    def test_lowercase_fedex_carrier_still_matches(self):
        r = self._call({"carrier": "fedex", "service_code": "FedEx2Day"})
        self.assertEqual(r.status_code, 200)
        # No FedEx policy in POLICY_LIST, so null — but not a 400.
        self.assertEqual(r.get_json(),
                         {"policy_id": None, "flat_policy_id": None,
                          "flat_name": None, "not_specified_policy_id": None,
                          "not_specified_name": None})

    def test_no_match_returns_null(self):
        r = self._call({"carrier": "FedEx", "service_code": "FedEx2Day"})
        self.assertEqual(r.get_json(),
                         {"policy_id": None, "flat_policy_id": None,
                          "flat_name": None, "not_specified_policy_id": None,
                          "not_specified_name": None})

    def test_bad_carrier_and_empty_service_are_400(self):
        r = self._call({"carrier": "DHL", "service_code": "USPSPriority"})
        self.assertEqual(r.status_code, 400)
        r = self._call({"carrier": "USPS", "service_code": ""})
        self.assertEqual(r.status_code, 400)

    def test_403_maps_to_reconnect(self):
        r = self._call({"carrier": "USPS", "service_code": "USPSPriority"},
                       fake_error=m._EbayApiError("forbidden", status=403,
                                                  error_id=""))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["needs_scope_reconnect"])

    def test_empty_international_shell_does_not_disqualify(self):
        # v43: eBay may serialize an INTERNATIONAL option with no
        # shippingServices key at all on a domestic-only policy. That
        # shell is not self-ship international; the policy matches.
        only = {"fulfillmentPolicies": [
            _calc_policy("555", ["USPSPriority"], intl_shell="missing")]}
        r = self._call({"carrier": "USPS", "service_code": "USPSPriority"},
                       fake_list=only)
        self.assertEqual(r.get_json()["policy_id"], "555")

    def test_international_option_with_empty_services_does_not_disqualify(self):
        only = {"fulfillmentPolicies": [
            _calc_policy("555", ["USPSPriority"], intl_shell="empty")]}
        r = self._call({"carrier": "USPS", "service_code": "USPSPriority"},
                       fake_list=only)
        self.assertEqual(r.get_json()["policy_id"], "555")

    def test_international_option_with_blank_code_does_not_disqualify(self):
        only = {"fulfillmentPolicies": [
            _calc_policy("555", ["USPSPriority"], intl_shell="blank")]}
        r = self._call({"carrier": "USPS", "service_code": "USPSPriority"},
                       fake_list=only)
        self.assertEqual(r.get_json()["policy_id"], "555")

    def test_real_international_service_still_disqualifies(self):
        # v43: the eIS-only intent is preserved — a real self-ship
        # international service still means the policy never matches.
        only = {"fulfillmentPolicies": [
            _calc_policy("222", ["USPSPriority"], intl=True)]}
        r = self._call({"carrier": "USPS", "service_code": "USPSPriority"},
                       fake_list=only)
        self.assertEqual(r.get_json()["policy_id"], None)
        self.assertEqual(r.get_json()["flat_policy_id"], None)

    def test_flat_policy_carrying_service_is_reported_honestly(self):
        # v43: the matcher is calculated-only, but a flat policy
        # carrying the service is reported as a flat match — never as
        # "no policy with that service yet".
        only = {"fulfillmentPolicies": [
            _flat_policy("333", ["USPSPriority"])]}
        r = self._call({"carrier": "USPS", "service_code": "USPSPriority"},
                       fake_list=only)
        self.assertEqual(r.get_json()["policy_id"], None)
        self.assertEqual(r.get_json()["flat_policy_id"], "333")
        self.assertEqual(r.get_json()["flat_name"], "Dummy 333")

    def test_calculated_match_wins_over_flat(self):
        r = self._call({"carrier": "USPS", "service_code": "USPSPriority"})
        self.assertEqual(r.get_json()["policy_id"], "111")
        self.assertEqual(r.get_json()["flat_policy_id"], None)

    def test_flat_policy_with_international_service_is_not_offered(self):
        only = {"fulfillmentPolicies": [
            _flat_policy("333", ["USPSPriority"], intl=True)]}
        r = self._call({"carrier": "USPS", "service_code": "USPSPriority"},
                       fake_list=only)
        self.assertEqual(r.get_json()["policy_id"], None)
        self.assertEqual(r.get_json()["flat_policy_id"], None)

    def test_flat_policy_with_empty_international_shell_is_offered(self):
        only = {"fulfillmentPolicies": [
            _flat_policy("333", ["USPSPriority"], intl_shell="empty")]}
        r = self._call({"carrier": "USPS", "service_code": "USPSPriority"},
                       fake_list=only)
        self.assertEqual(r.get_json()["flat_policy_id"], "333")


class DraftSaveCarrierTest(unittest.TestCase):
    """Draft autosave stores the carrier pick + lb/oz boxes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig = dict(DRAFTS_DIR=m.DRAFTS_DIR, PHOTOS_DIR=m.PHOTOS_DIR)
        for attr, sub in (("DRAFTS_DIR", "drafts"), ("PHOTOS_DIR", "photos")):
            p = os.path.join(self.tmp, sub)
            os.makedirs(p, exist_ok=True)
            setattr(m, attr, p)
        self.client = m.app.test_client()
        m._save_draft({"id": "d1", "photos": []})

    def tearDown(self):
        for attr, val in self.orig.items():
            setattr(m, attr, val)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_carrier_service_and_lb_oz_save(self):
        r = self.client.put("/api/drafts/d1", json={
            "ship_carrier": "usps",
            "ship_service_code": "USPSPriority",
            "package_weight_lb": "2", "package_weight_oz": "8",
            "package_length": "12", "package_width": "9",
            "package_height": "1"})
        self.assertEqual(r.status_code, 200)
        saved = r.get_json()["draft"]
        self.assertEqual(saved["ship_carrier"], "USPS")
        self.assertEqual(saved["ship_service_code"], "USPSPriority")
        self.assertEqual(saved["package_weight_lb"], "2")
        self.assertEqual(saved["package_weight_oz"], "8")

    def test_invalid_carrier_is_ignored_not_saved(self):
        r = self.client.put("/api/drafts/d1", json={
            "ship_carrier": "DHL", "ship_service_code": "DHLExpress"})
        saved = r.get_json()["draft"]
        self.assertEqual(saved["ship_carrier"], "")
        self.assertEqual(saved["ship_service_code"], "DHLExpress")

    def test_fedex_carrier_saves_canonical_mixed_case(self):
        r = self.client.put("/api/drafts/d1", json={
            "ship_carrier": "fedex"})
        self.assertEqual(r.get_json()["draft"]["ship_carrier"], "FedEx")


class FlatPartialChainTest(unittest.TestCase):
    """_do_publish, eBay fully mocked: a flat policy with only a
    weight entered still lists, and the inventory item carries no
    package block; a calculated policy with only a weight blocks
    before any upload."""

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
            "title": "Test item", "description": "A test item.",
            "price_val": 10.0, "condition": "NEW", "condition_details": "",
            "category_id": "123", "fulfillment_id": "111",
            "payment_id": "222", "return_id": "333",
            "attributes": {}, "best_offer_terms": None,
        }
        self.policy_mode = "flat"
        self.inventory_payload = None
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
            if self.policy_mode == "calculated":
                return _calc_policy("111", ["USPSPriority"])
            return _flat_policy("111", ["USPSPriority"])
        if method == "PUT" and path.startswith(
                "/sell/inventory/v1/inventory_item/"):
            self.inventory_payload = payload
            return {}
        if (method, path) == ("POST", "/sell/inventory/v1/offer"):
            return {"offerId": "OFFER1"}
        if method == "POST" and path.startswith("/sell/inventory/v1/offer/"):
            return {"listingId": "999"}
        if method == "GET" and path.startswith("/sell/inventory/v1/offer?sku="):
            return {"offers": []}
        if method == "GET" and path.startswith("/sell/listing/v1/item/"):
            return {}
        raise AssertionError(f"unexpected eBay call {method} {path}")

    def _run(self, package):
        f = dict(self.fields, package=package)
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

    def test_flat_weight_only_lists_without_sending_package(self):
        weight_only = m._package_from_lb_oz({
            "package_weight_lb": "2", "package_weight_oz": "",
            "package_length": "", "package_width": "",
            "package_height": ""})
        listing_id, _url = self._run(weight_only)
        self.assertEqual(listing_id, "999")
        self.assertNotIn("packageWeightAndSize", self.inventory_payload)

    def test_calculated_weight_only_blocks_before_uploads(self):
        self.policy_mode = "calculated"
        weight_only = m._package_from_lb_oz({
            "package_weight_lb": "2", "package_weight_oz": "",
            "package_length": "", "package_width": "",
            "package_height": ""})
        with self.assertRaises(m._PublishValidationError):
            self._run(weight_only)
        self.assertEqual(self.uploads, 0)


if __name__ == "__main__":
    unittest.main()
