"""apk-v31 tests: in-app shop-location creation.

v30 stopped a first publish with a plain no-location message when GET
/sell/inventory/v1/location returned an empty list. That stop is now
confirmed live (the account has no inventory location, even though eBay
holds the seller's address for KYC and as the return address — the
merchant location is a third, separate record). v31 instead shows a
one-time ship-from address form and creates the location, then the
publish proceeds automatically.

PRIVACY: the repo is PUBLIC. Every address in this file is obviously
dummy (123 Test St, Testville, TX 00000). No real address appears in
code, fixtures, or test output — the real address reaches the app only
when the seller types it on their own phone, and the app never stores
it (see test_ship_from_never_persisted).

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


# Dummy address only — never a real address, anywhere in this file.
DUMMY_SHIP_FROM = {"street": "123 Test St", "city": "Testville",
                   "state": "TX", "zip": "00000"}
DUMMY_ADDRESS_BODY = {"addressLine1": "123 Test St", "city": "Testville",
                      "stateOrProvince": "TX", "postalCode": "00000",
                      "country": "US"}


def _ebay_err(message, error_id):
    return {"errors": [{"message": message, "errorId": error_id}]}


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


class ShipFromValidationTest(unittest.TestCase):
    def test_valid_address_cleaned_and_country_forced_us(self):
        out = m._validate_ship_from(dict(DUMMY_SHIP_FROM, country="XX",
                                         street="  123 Test St  ",
                                         state="tx"))
        self.assertEqual(out, DUMMY_ADDRESS_BODY)

    def test_missing_field_rejected(self):
        for missing in ("street", "city", "state", "zip"):
            bad = dict(DUMMY_SHIP_FROM)
            bad[missing] = "  "
            with self.assertRaises(m._PublishValidationError):
                m._validate_ship_from(bad)

    def test_non_dict_rejected(self):
        with self.assertRaises(m._PublishValidationError):
            m._validate_ship_from("123 Test St")

    def test_state_must_be_two_letters(self):
        with self.assertRaises(m._PublishValidationError):
            m._validate_ship_from(dict(DUMMY_SHIP_FROM, state="Texas"))
        with self.assertRaises(m._PublishValidationError):
            m._validate_ship_from(dict(DUMMY_SHIP_FROM, state="T"))

    def test_zip_must_be_five_digits(self):
        with self.assertRaises(m._PublishValidationError):
            m._validate_ship_from(dict(DUMMY_SHIP_FROM, zip="ABC"))
        out = m._validate_ship_from(dict(DUMMY_SHIP_FROM, zip="00000-1234"))
        self.assertEqual(out["postalCode"], "00000-1234")


class LocationCreateChainTest(unittest.TestCase):
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
        self.events = []
        self.behavior = {}
        self.created = False

    def tearDown(self):
        for attr, val in self.orig.items():
            setattr(m, attr, val)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_call(self, method, path, token, payload=None):
        self.events.append(("call", method, path, payload))
        if (method, path) == ("GET", "/sell/inventory/v1/location"):
            if self.created:
                return {"locations": [{"merchantLocationKey": "home"}]}
            return self.behavior.get("locations", {"locations": []})
        if (method, path) == ("POST", "/sell/inventory/v1/location/home"):
            if "create_error" in self.behavior:
                raise self.behavior["create_error"]
            self.created = True
            self.create_payload = payload
            return {}
        if method == "PUT" and path.startswith("/sell/inventory/v1/inventory_item/"):
            return {}
        if (method, path) == ("POST", "/sell/inventory/v1/offer"):
            self.offer_payload = payload
            return {"offerId": "OFFER1"}
        if method == "POST" and path.startswith("/sell/inventory/v1/offer/"):
            return {"listingId": "999"}
        if method == "GET" and path.startswith("/sell/inventory/v1/offer?sku="):
            return {"offers": []}
        if method == "GET" and path.startswith("/sell/listing/v1/item/"):
            return {}
        raise AssertionError(f"unexpected eBay call {method} {path}")

    def _run(self, ship_from=None, env_key=""):
        patches = [
            mock.patch.object(m, "_ebay_access_token", return_value="tok"),
            mock.patch.object(m, "_ebay_upload_picture",
                              return_value="https://i.ebayimg.com/fake.jpg"),
            mock.patch.object(m, "_category_aspects",
                              return_value=(None, None)),
            mock.patch.object(m, "_ebay_call", side_effect=self._fake_call),
            mock.patch.object(m, "EBAY_MERCHANT_LOCATION_KEY", env_key),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        return m._do_publish(dict(self.draft), dict(self.fields),
                             ship_from=ship_from)

    def test_empty_list_without_address_raises_needs_address(self):
        with self.assertRaises(m._NeedsShipFromAddress) as ctx:
            self._run()
        self.assertIn("No shop location found", str(ctx.exception))
        # Fails fast: only the location lookup ran — no create, no photo
        # upload, no inventory PUT, no offer, no publish.
        self.assertEqual([(e[0], e[1], e[2]) for e in self.events],
                         [("call", "GET", "/sell/inventory/v1/location")])

    def test_empty_list_with_address_creates_then_publishes(self):
        listing_id, _url = self._run(ship_from=dict(DUMMY_SHIP_FROM))
        self.assertEqual(listing_id, "999")
        self.assertEqual(self.create_payload, {
            "name": "Home",
            "locationTypes": ["WAREHOUSE"],
            "location": {"address": DUMMY_ADDRESS_BODY},
        })
        self.assertEqual(self.offer_payload["merchantLocationKey"], "home")
        calls = [(e[1], e[2]) for e in self.events]
        self.assertLess(calls.index(("GET", "/sell/inventory/v1/location")),
                        calls.index(("POST", "/sell/inventory/v1/location/home")))
        self.assertLess(calls.index(("POST", "/sell/inventory/v1/location/home")),
                        calls.index(("POST", "/sell/inventory/v1/offer")))

    def test_nonempty_list_skips_creation_entirely(self):
        self.behavior["locations"] = {"locations": [{"merchantLocationKey": "LOC1"}]}
        listing_id, _url = self._run(ship_from=dict(DUMMY_SHIP_FROM))
        self.assertEqual(listing_id, "999")
        self.assertEqual(self.offer_payload["merchantLocationKey"], "LOC1")
        self.assertFalse(any(e[2] == "/sell/inventory/v1/location/home"
                             for e in self.events))

    def test_env_pin_skips_lookup_and_creation(self):
        listing_id, _url = self._run(ship_from=dict(DUMMY_SHIP_FROM),
                                     env_key="ENVLOC")
        self.assertEqual(listing_id, "999")
        self.assertEqual(self.offer_payload["merchantLocationKey"], "ENVLOC")
        self.assertFalse(any(e[2].startswith("/sell/inventory/v1/location")
                             for e in self.events))

    def test_create_failure_surfaces_error_id_and_stops(self):
        self.behavior["create_error"] = RuntimeError(
            "eBay said (error 25001): Invalid postal code.")
        with self.assertRaises(RuntimeError) as ctx:
            self._run(ship_from=dict(DUMMY_SHIP_FROM))
        self.assertIn("(error 25001)", str(ctx.exception))
        # No inventory PUT, no offer, no publish after the failed create
        # (the second GET is the race fallback re-check, not progress).
        self.assertFalse(any(e[1] == "PUT" for e in self.events))
        self.assertFalse(any(e[2] == "/sell/inventory/v1/offer"
                             for e in self.events))

    def test_create_failure_with_location_now_present_uses_it(self):
        def failing_create_then_present(method, path, token, payload=None):
            if (method, path) == ("POST", "/sell/inventory/v1/location/home"):
                # Simulate the create landing while its reply was lost:
                # the follow-up GET now finds the location.
                self.created = True
                raise RuntimeError("eBay said (error 500): hiccup.")
            return self._fake_call(method, path, token, payload)

        with mock.patch.object(m, "_ebay_access_token", return_value="tok"), \
                mock.patch.object(m, "_ebay_upload_picture",
                                  return_value="https://i.ebayimg.com/fake.jpg"), \
                mock.patch.object(m, "_category_aspects",
                                  return_value=(None, None)), \
                mock.patch.object(m, "_ebay_call",
                                  side_effect=failing_create_then_present), \
                mock.patch.object(m, "EBAY_MERCHANT_LOCATION_KEY", ""):
            listing_id, _url = m._do_publish(dict(self.draft),
                                             dict(self.fields),
                                             ship_from=dict(DUMMY_SHIP_FROM))
        self.assertEqual(listing_id, "999")
        self.assertEqual(self.offer_payload["merchantLocationKey"], "home")

    def test_ship_from_never_persisted(self):
        self._run(ship_from=dict(DUMMY_SHIP_FROM))
        saved = m._load_draft("d1")
        self.assertNotIn("ship_from", saved)
        with open(os.path.join(m.DRAFTS_DIR, "d1.json")) as f:
            raw = f.read()
        self.assertNotIn("123 Test St", raw)
        self.assertNotIn("Testville", raw)


class LocationCreateRouteTest(unittest.TestCase):
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
        # Batch publish takes its policy ids from the preset (else .env,
        # which is empty under test), so seed one.
        with open(m.PRESET_FILE, "w", encoding="utf-8") as f:
            json.dump({"fulfillment_policy_id": "111",
                       "payment_policy_id": "222",
                       "return_policy_id": "333"}, f)
        self.client = m.app.test_client()
        self.photo = os.path.join(m.PHOTOS_DIR, "p.jpg")
        with open(self.photo, "wb") as f:
            f.write(b"\xff\xd8fakejpgbytes")
        self.created = False
        self.calls = []

    def tearDown(self):
        for attr, val in self.orig.items():
            setattr(m, attr, val)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_call(self, method, path, token, payload=None):
        self.calls.append((method, path))
        if (method, path) == ("GET", "/sell/inventory/v1/location"):
            if self.created:
                return {"locations": [{"merchantLocationKey": "home"}]}
            return {"locations": []}
        if (method, path) == ("POST", "/sell/inventory/v1/location/home"):
            self.created = True
            return {}
        if method == "PUT" and path.startswith("/sell/inventory/v1/inventory_item/"):
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

    def _draft_body(self, draft_id, **over):
        d = {"id": draft_id, "title": "Test item", "description": "Desc.",
             "price": "10.00", "condition": "NEW", "condition_details": "",
             "category": "", "attributes": {}, "photos": ["p.jpg"],
             "best_offer": False, "best_offer_auto_decline": "",
             "best_offer_auto_accept": ""}
        d.update(over)
        m._save_draft(d)
        return d

    def _publish_body(self, draft_id):
        return {"draft_id": draft_id, "title": "Test item",
                "description": "Desc.", "price": "10.00", "condition": "NEW",
                "condition_details": "", "category_id": "123",
                "fulfillment_policy_id": "111", "payment_policy_id": "222",
                "return_policy_id": "333", "attributes": {},
                "best_offer": False, "best_offer_auto_decline": "",
                "best_offer_auto_accept": ""}

    def test_single_publish_returns_needs_ship_from_flag(self):
        self._draft_body("d1")
        with mock.patch.object(m, "_ebay_access_token", return_value="tok"), \
                mock.patch.object(m, "_ebay_upload_picture",
                                  return_value="https://i.ebayimg.com/fake.jpg"), \
                mock.patch.object(m, "_category_aspects",
                                  return_value=(None, None)), \
                mock.patch.object(m, "_ebay_call",
                                  side_effect=self._fake_call), \
                mock.patch.object(m, "EBAY_MERCHANT_LOCATION_KEY", ""):
            resp = self.client.post("/api/ebay/publish",
                                    json=self._publish_body("d1"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json().get("needs_ship_from"))
        saved = m._load_draft("d1")
        self.assertIsNone(saved.get("ebay_listing_id"))

    def test_single_publish_with_address_lists(self):
        self._draft_body("d1")
        body = self._publish_body("d1")
        body["ship_from"] = dict(DUMMY_SHIP_FROM)
        with mock.patch.object(m, "_ebay_access_token", return_value="tok"), \
                mock.patch.object(m, "_ebay_upload_picture",
                                  return_value="https://i.ebayimg.com/fake.jpg"), \
                mock.patch.object(m, "_category_aspects",
                                  return_value=(None, None)), \
                mock.patch.object(m, "_ebay_call",
                                  side_effect=self._fake_call), \
                mock.patch.object(m, "EBAY_MERCHANT_LOCATION_KEY", ""):
            resp = self.client.post("/api/ebay/publish", json=body)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["listing_id"], "999")
        self.assertIn(("POST", "/sell/inventory/v1/location/home"),
                      self.calls)

    def test_batch_stops_at_first_needs_address_draft(self):
        self._draft_body("d1")
        self._draft_body("d2")
        with mock.patch.object(m, "_ebay_access_token", return_value="tok"), \
                mock.patch.object(m, "_ebay_upload_picture",
                                  return_value="https://i.ebayimg.com/fake.jpg"), \
                mock.patch.object(m, "_category_aspects",
                                  return_value=(None, None)), \
                mock.patch.object(m, "_ebay_call",
                                  side_effect=self._fake_call), \
                mock.patch.object(m, "EBAY_MERCHANT_LOCATION_KEY", ""):
            resp = self.client.post("/api/ebay/batch-publish",
                                    json={"draft_ids": ["d1", "d2"],
                                          "category_id": "123"})
        results = resp.get_json()["results"]
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["needs_ship_from"])
        self.assertFalse(results[0]["ok"])
        # The second draft was never attempted: same wall, no point.
        self.assertFalse(any(p == "/sell/inventory/v1/offer"
                             for _m, p in self.calls))

    def test_batch_with_address_lists_both(self):
        self._draft_body("d1")
        self._draft_body("d2")
        with mock.patch.object(m, "_ebay_access_token", return_value="tok"), \
                mock.patch.object(m, "_ebay_upload_picture",
                                  return_value="https://i.ebayimg.com/fake.jpg"), \
                mock.patch.object(m, "_category_aspects",
                                  return_value=(None, None)), \
                mock.patch.object(m, "_ebay_call",
                                  side_effect=self._fake_call), \
                mock.patch.object(m, "EBAY_MERCHANT_LOCATION_KEY", ""):
            resp = self.client.post(
                "/api/ebay/batch-publish",
                json={"draft_ids": ["d1", "d2"], "category_id": "123",
                      "ship_from": dict(DUMMY_SHIP_FROM)})
        results = resp.get_json()["results"]
        self.assertEqual([r["ok"] for r in results], [True, True])
        # Created exactly once; the second draft found it via GET.
        self.assertEqual(self.calls.count(
            ("POST", "/sell/inventory/v1/location/home")), 1)


if __name__ == "__main__":
    unittest.main()
