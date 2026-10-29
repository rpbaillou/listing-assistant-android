"""apk-v30 tests: every finding from the Oct 28, 2026 audit (v1.28).

The audit proved the photo step already worked on v1.28 (Media API) and
the chain failed at the inventory PUT for a missing Content-Language
header — then named the blockers waiting behind it. These tests pin the
fixes, in severity order:
  F1  Content-Language: en-US on every _ebay_call request.
  F2  merchantLocationKey on the offer: .env pin wins, else the first
      location from GET /sell/inventory/v1/location; an empty list raises
      the plain no-location error before any photo work, never inventing
      an address.
  F3  offer_id saved to the draft IMMEDIATELY after offer creation (before
      publish), and a 25002-style failure reuses the existing offer via
      GET offer?sku=.
  F4  Empty condition raises "Pick a condition first." — never a silent
      NEW default.
  F6  errorId in every surfaced _plain_ebay_error message; keyword
      rewrites only on a tight match (aspect messages are not rewritten
      as category errors).
  F7  401 clears tokens + reconnects; 403 surfaces without clearing and
      names 190013.
  F8  _media_api_error extracts message + errorId, raw snippet only when
      unparseable.
  F9  listingDuration is a top-level offer field (no nested "listing").
  F11 Photo cap is 12 MB.
F5 needs no fix (eBay's message already surfaces); F10 is narrowed by F3.

eBay is always mocked; no test here reaches the network. Storage dirs
are monkeypatched to a temp dir per test.
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


def _ebay_err(message, error_id):
    return {"errors": [{"message": message, "errorId": error_id}]}


class EbayCallTest(unittest.TestCase):
    def test_content_language_en_us_on_every_call(self):
        seen = {}

        def fake_request(method, url, headers=None, **kw):
            seen.update(headers or {})
            return _fake_resp(200, json_body={})

        with mock.patch.object(m.requests, "request",
                               side_effect=fake_request):
            m._ebay_call("GET", "/sell/inventory/v1/location", "tok")
        self.assertEqual(seen.get("Content-Language"), "en-US")
        self.assertEqual(seen.get("Authorization"), "Bearer tok")

    def test_401_clears_tokens_and_reconnects(self):
        resp = _fake_resp(401, json_body=_ebay_err("bad token", 190001))
        with mock.patch.object(m.requests, "request",
                               return_value=resp), \
                mock.patch.object(m, "_clear_ebay_tokens") as clear:
            with self.assertRaises(RuntimeError) as ctx:
                m._ebay_call("GET", "/sell/inventory/v1/location", "tok")
        clear.assert_called_once()
        self.assertIn("stopped working", str(ctx.exception))

    def test_403_surfaces_without_clearing_and_names_190013(self):
        resp = _fake_resp(403, json_body=_ebay_err(
            "The access token lacks scope sell.inventory", 190013))
        with mock.patch.object(m.requests, "request",
                               return_value=resp), \
                mock.patch.object(m, "_clear_ebay_tokens") as clear:
            with self.assertRaises(RuntimeError) as ctx:
                m._ebay_call("GET", "/sell/inventory/v1/location", "tok")
        clear.assert_not_called()
        self.assertIn("(error 190013)", str(ctx.exception))
        self.assertIn("permission", str(ctx.exception).lower())


class PlainErrorTest(unittest.TestCase):
    def test_fallback_includes_error_id(self):
        resp = _fake_resp(400, json_body=_ebay_err("Something broke", 25709))
        out = m._plain_ebay_error(resp)
        self.assertIn("(error 25709)", out)
        self.assertIn("Something broke", out)

    def test_rewrites_include_error_id(self):
        resp = _fake_resp(400, json_body=_ebay_err(
            "business policies are required", 25710))
        out = m._plain_ebay_error(resp)
        self.assertIn("(error 25710)", out)
        self.assertIn("policy numbers", out)

    def test_aspect_message_not_rewritten_as_category(self):
        resp = _fake_resp(400, json_body=_ebay_err(
            "Missing required item specific Brand for category 123", 21919303))
        out = m._plain_ebay_error(resp)
        self.assertNotIn("Tap Suggest", out)
        self.assertIn("(error 21919303)", out)
        self.assertIn("Missing required item specific Brand", out)

    def test_category_message_still_rewritten(self):
        resp = _fake_resp(400, json_body=_ebay_err("Invalid category", 25709))
        out = m._plain_ebay_error(resp)
        self.assertIn("Tap Suggest", out)
        self.assertIn("(error 25709)", out)


class MediaErrorTest(unittest.TestCase):
    def test_extracts_message_and_error_id(self):
        resp = _fake_resp(400, json_body=_ebay_err("File too big", 190201))
        out = m._media_api_error(resp, "tok")
        self.assertIn("File too big", out)
        self.assertIn("(error 190201)", out)

    def test_unparseable_falls_back_to_snippet(self):
        resp = _fake_resp(400, content=b"gateway hiccup")
        out = m._media_api_error(resp, "tok")
        self.assertIn("gateway hiccup", out)
        self.assertIn("400", out)

    def test_token_redacted_in_fallback(self):
        resp = _fake_resp(400, content=b"leaked tok123 here")
        out = m._media_api_error(resp, "tok123")
        self.assertNotIn("tok123", out)
        self.assertIn("[removed]", out)


class PublishChainTest(unittest.TestCase):
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

    def tearDown(self):
        for attr, val in self.orig.items():
            setattr(m, attr, val)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_call(self, method, path, token, payload=None):
        self.events.append(("call", method, path))
        if (method, path) == ("GET", "/sell/inventory/v1/location"):
            return self.behavior.get("locations",
                                     {"locations": [{"merchantLocationKey": "LOC1"}]})
        if method == "PUT" and path.startswith("/sell/inventory/v1/inventory_item/"):
            return {}
        if (method, path) == ("POST", "/sell/inventory/v1/offer"):
            if "offer_error" in self.behavior:
                raise self.behavior["offer_error"]
            self.offer_payload = payload
            return {"offerId": "OFFER1"}
        if method == "POST" and path.startswith("/sell/inventory/v1/offer/"):
            return {"listingId": "999"}
        if method == "GET" and path.startswith("/sell/inventory/v1/offer?sku="):
            return self.behavior.get("existing_offers", {"offers": []})
        if method == "GET" and path.startswith("/sell/listing/v1/item/"):
            return {}
        if method == "GET" and path.startswith("/sell/account/v1/fulfillment_policy"):
            # v32: the publish helper reads the shipping policy before
            # offering. The fixture seller's policy is flat-rate, so no
            # package block is needed. Dummy IDs only.
            flat = {"fulfillmentPolicyId": "111", "name": "Dummy Flat",
                    "marketplaceId": "EBAY_US",
                    "shippingOptions": [{"optionType": "DOMESTIC",
                                         "costType": "FLAT_RATE"}]}
            if "?" in path:
                return {"fulfillmentPolicies": [flat]}
            return flat
        raise AssertionError(f"unexpected eBay call {method} {path}")

    def _run(self, fields=None, env_key=""):
        real_save = m._save_draft

        def saving(d):
            self.events.append(("save", d.get("ebay_offer_id")))
            return real_save(d)

        patches = [
            mock.patch.object(m, "_ebay_access_token", return_value="tok"),
            mock.patch.object(m, "_ebay_upload_picture",
                              return_value="https://i.ebayimg.com/fake.jpg"),
            mock.patch.object(m, "_category_aspects",
                              return_value=(None, None)),
            mock.patch.object(m, "_category_conditions",
                              return_value=(None, None)),
            mock.patch.object(m, "_ebay_call", side_effect=self._fake_call),
            mock.patch.object(m, "_save_draft", side_effect=saving),
            mock.patch.object(m, "EBAY_MERCHANT_LOCATION_KEY", env_key),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        return m._do_publish(dict(self.draft), dict(fields or self.fields))

    def test_offer_has_location_key_and_top_level_duration(self):
        listing_id, _url = self._run()
        self.assertEqual(listing_id, "999")
        self.assertEqual(self.offer_payload["merchantLocationKey"], "LOC1")
        self.assertEqual(self.offer_payload["listingDuration"], "GTC")
        self.assertNotIn("listing", self.offer_payload)

    def test_env_location_key_wins_and_skips_lookup(self):
        self._run(env_key="ENVLOC")
        self.assertEqual(self.offer_payload["merchantLocationKey"], "ENVLOC")
        lookups = [e for e in self.events
                   if e[0] == "call" and e[2] == "/sell/inventory/v1/location"]
        self.assertEqual(lookups, [])

    def test_empty_location_list_fails_fast(self):
        # v31 supersedes the v30 contract: an empty list without a
        # ship-from address now raises _NeedsShipFromAddress (the app
        # shows the address form) instead of the old dead-end
        # validation error. The fail-fast part is unchanged.
        # v35: the compliance check's policy read runs before location
        # resolution (it is a harmless read, and the report must cover
        # policy problems too) — so the events now open with it.
        self.behavior["locations"] = {"locations": []}
        with self.assertRaises(m._NeedsShipFromAddress) as ctx:
            self._run()
        self.assertIn("No shop location found", str(ctx.exception))
        # Fail fast: only reads ran — no photo upload, no
        # inventory PUT, no offer, no publish, no draft save.
        self.assertEqual(
            self.events,
            [("call", "GET", "/sell/account/v1/fulfillment_policy/111"),
             ("call", "GET", "/sell/inventory/v1/location")])

    def test_offer_id_saved_before_publish(self):
        self._run()
        save_idx = next(i for i, e in enumerate(self.events)
                        if e == ("save", "OFFER1"))
        pub_idx = next(i for i, e in enumerate(self.events)
                       if e[0] == "call" and e[1] == "POST"
                       and e[2].endswith("/publish"))
        self.assertLess(save_idx, pub_idx)
        saved = m._load_draft("d1")
        self.assertEqual(saved["ebay_offer_id"], "OFFER1")
        self.assertEqual(saved["ebay_listing_id"], "999")

    def test_25002_reuses_existing_offer(self):
        self.behavior["offer_error"] = RuntimeError(
            "eBay said (error 25002): An offer already exists for this SKU.")
        self.behavior["existing_offers"] = {"offers": [{"offerId": "OLD9"}]}
        listing_id, _url = self._run()
        self.assertEqual(listing_id, "999")
        pubs = [e for e in self.events
                if e[0] == "call" and e[1] == "POST" and "OLD9" in e[2]]
        self.assertEqual(len(pubs), 1)
        saved = m._load_draft("d1")
        self.assertEqual(saved["ebay_offer_id"], "OLD9")

    def test_25002_lookup_empty_reraises(self):
        self.behavior["offer_error"] = RuntimeError(
            "eBay said (error 25002): An offer already exists for this SKU.")
        self.behavior["existing_offers"] = {"offers": []}
        with self.assertRaises(RuntimeError) as ctx:
            self._run()
        self.assertIn("25002", str(ctx.exception))

    def test_empty_condition_rejected_without_default(self):
        fields = dict(self.fields, condition="")
        with self.assertRaises(m._PublishValidationError) as ctx:
            self._run(fields=fields)
        self.assertEqual(str(ctx.exception), "Pick a condition first.")
        # v35: compliance reports ALL problems at once, so its policy
        # gate reads the policy even when the condition is already
        # wrong (a stale policy must still surface in the same
        # report). That read is the only eBay call: no photo upload,
        # no inventory PUT, no offer, no publish, no draft save.
        self.assertEqual(
            self.events,
            [("call", "GET", "/sell/account/v1/fulfillment_policy/111")])

    def test_invalid_condition_rejected(self):
        fields = dict(self.fields, condition="BOGUS")
        with self.assertRaises(m._PublishValidationError) as ctx:
            self._run(fields=fields)
        self.assertEqual(str(ctx.exception), "Pick a condition from the list.")

    def test_photo_cap_is_12(self):
        self.assertEqual(m.MAX_FILE_MB, 12)


if __name__ == "__main__":
    unittest.main()
