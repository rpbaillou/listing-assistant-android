"""apk-v34 tests: stale-offer discard so a picked shipping policy
actually takes effect and a failed publish never retries the same
stale offer forever.

Root cause (verified in code + two Gemini consults): _do_publish
reuses a saved d.ebay_offer_id and skips offer creation entirely, so
a policy pick re-post carries the picked ID in the request but eBay
publishes the OLD offer's saved state. Live proof Oct 29 02:13: a
publish sent a CALCULATED policy WITH a valid packageWeightAndSize
block yet still got 25007, because the reused offer predated the
package block. v34 discards the stale offer (best-effort DELETE +
clear local ID) in three cases: (1) a policy-pick re-post, single and
batch; (2) a publish failure with 25007 or 25604; (3) a recorded
policy mismatch (the saved offer was created with a different
fulfillment policy than the current one). Other publish failures keep
F3 reuse intact, and a live listing is never touched.

PRIVACY: the repo is PUBLIC. Every policy ID, offer ID, and address
here is obviously dummy (111/999, OLD-OFFER/OFFER-NEW). No real policy
ID, address, or secret appears in code, fixtures, or test output.

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


FLAT_FOR = lambda pid: {"fulfillmentPolicyId": pid, "name": "Dummy Flat",
                        "marketplaceId": "EBAY_US",
                        "shippingOptions": [{"optionType": "DOMESTIC",
                                             "costType": "FLAT_RATE"}]}


class _ChainBase(unittest.TestCase):
    """_do_publish with eBay fully mocked + a call log."""

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
        self.publish_error = None
        self.delete_error = None
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
            return FLAT_FOR(path.rsplit("/", 1)[-1])
        if method == "PUT" and path.startswith(
                "/sell/inventory/v1/inventory_item/"):
            return {}
        if (method, path) == ("POST", "/sell/inventory/v1/offer"):
            self.offer_payload = payload
            return {"offerId": "OFFER-NEW"}
        if method == "POST" and path.startswith("/sell/inventory/v1/offer/"):
            if self.publish_error is not None:
                raise self.publish_error
            return {"listingId": "999"}
        if method == "DELETE" and path.startswith("/sell/inventory/v1/offer/"):
            if self.delete_error is not None:
                raise self.delete_error
            return {}
        if method == "GET" and path.startswith("/sell/listing/v1/item/"):
            return {}
        raise AssertionError(f"unexpected eBay call {method} {path}")

    def _fields(self, fulfillment_id="111", fresh_offer=False):
        f = {"title": "Test item", "description": "A test item.",
             "price_val": 10.0, "condition": "NEW", "condition_details": "",
             "category_id": "123", "fulfillment_id": fulfillment_id,
             "payment_id": "222", "return_id": "333", "attributes": {},
             "best_offer_terms": None}
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

    def _deletes(self):
        return [p for (method, p) in self.calls if method == "DELETE"]

    def _offer_posts(self):
        return [p for (method, p) in self.calls
                if (method, p) == ("POST", "/sell/inventory/v1/offer")]


class StaleOfferChainTest(_ChainBase):
    def test_pick_discards_old_offer_and_creates_fresh(self):
        d = {"id": "d1", "photos": ["p.jpg"], "ebay_offer_id": "OLD-OFFER",
             "ebay_offer_fulfillment_id": "111"}
        listing_id, _url = self._run(
            d, self._fields(fulfillment_id="999", fresh_offer=True))
        self.assertEqual(listing_id, "999")
        self.assertEqual(self._deletes(),
                         ["/sell/inventory/v1/offer/OLD-OFFER"])
        self.assertEqual(len(self._offer_posts()), 1)
        self.assertEqual(self.offer_payload["listingPolicies"]
                         ["fulfillmentPolicyId"], "999")
        saved = m._load_draft("d1")
        self.assertEqual(saved["ebay_offer_id"], "OFFER-NEW")
        self.assertEqual(saved["ebay_offer_fulfillment_id"], "999")

    def test_recorded_policy_mismatch_discards(self):
        d = {"id": "d1", "photos": ["p.jpg"], "ebay_offer_id": "OLD-OFFER",
             "ebay_offer_fulfillment_id": "111"}
        listing_id, _url = self._run(d, self._fields(fulfillment_id="999"))
        self.assertEqual(listing_id, "999")
        self.assertEqual(self._deletes(),
                         ["/sell/inventory/v1/offer/OLD-OFFER"])
        self.assertEqual(len(self._offer_posts()), 1)
        self.assertEqual(self.offer_payload["listingPolicies"]
                         ["fulfillmentPolicyId"], "999")

    def test_same_policy_reuses_offer(self):
        d = {"id": "d1", "photos": ["p.jpg"], "ebay_offer_id": "OLD-OFFER",
             "ebay_offer_fulfillment_id": "111"}
        listing_id, _url = self._run(d, self._fields(fulfillment_id="111"))
        self.assertEqual(listing_id, "999")
        self.assertEqual(self._deletes(), [])
        self.assertEqual(self._offer_posts(), [])
        publish_paths = [p for (method, p) in self.calls
                         if method == "POST" and "/publish" in p]
        self.assertEqual(publish_paths,
                         ["/sell/inventory/v1/offer/OLD-OFFER/publish"])

    def test_no_recorded_policy_reuses_offer(self):
        # Drafts saved before v34 carry no recorded policy: reuse is
        # preserved (the pick flag covers picks for those drafts).
        d = {"id": "d1", "photos": ["p.jpg"], "ebay_offer_id": "OLD-OFFER"}
        listing_id, _url = self._run(d, self._fields(fulfillment_id="999"))
        self.assertEqual(listing_id, "999")
        self.assertEqual(self._deletes(), [])
        self.assertEqual(self._offer_posts(), [])

    def test_25007_publish_failure_discards(self):
        self.publish_error = RuntimeError(
            "eBay said (error 25007): invalid shipping policy.")
        d = {"id": "d1", "photos": ["p.jpg"], "ebay_offer_id": "OLD-OFFER",
             "ebay_offer_fulfillment_id": "111"}
        with self.assertRaises(RuntimeError) as ctx:
            self._run(d, self._fields(fulfillment_id="111"))
        self.assertIn("25007", str(ctx.exception))
        self.assertEqual(self._deletes(),
                         ["/sell/inventory/v1/offer/OLD-OFFER"])
        saved = m._load_draft("d1")
        self.assertNotIn("ebay_offer_id", saved)

    def test_25604_publish_failure_discards(self):
        self.publish_error = RuntimeError(
            "eBay said (error 25604): Availability not found.")
        d = {"id": "d1", "photos": ["p.jpg"], "ebay_offer_id": "OLD-OFFER",
             "ebay_offer_fulfillment_id": "111"}
        with self.assertRaises(RuntimeError) as ctx:
            self._run(d, self._fields(fulfillment_id="111"))
        self.assertIn("25604", str(ctx.exception))
        self.assertEqual(self._deletes(),
                         ["/sell/inventory/v1/offer/OLD-OFFER"])
        saved = m._load_draft("d1")
        self.assertNotIn("ebay_offer_id", saved)

    def test_other_publish_failure_keeps_offer(self):
        self.publish_error = RuntimeError("eBay said (error 500): boom.")
        d = {"id": "d1", "photos": ["p.jpg"], "ebay_offer_id": "OLD-OFFER",
             "ebay_offer_fulfillment_id": "111"}
        with self.assertRaises(RuntimeError):
            self._run(d, self._fields(fulfillment_id="111"))
        self.assertEqual(self._deletes(), [])
        # The in-memory draft still carries the offer for F3 retry.
        self.assertEqual(d["ebay_offer_id"], "OLD-OFFER")

    def test_delete_failure_ignored_on_pick(self):
        self.delete_error = RuntimeError("delete boom")
        d = {"id": "d1", "photos": ["p.jpg"], "ebay_offer_id": "OLD-OFFER",
             "ebay_offer_fulfillment_id": "111"}
        listing_id, _url = self._run(
            d, self._fields(fulfillment_id="999", fresh_offer=True))
        self.assertEqual(listing_id, "999")
        self.assertEqual(len(self._offer_posts()), 1)

    def test_discard_never_when_listed(self):
        d = {"id": "d1", "photos": ["p.jpg"], "ebay_offer_id": "OLD-OFFER",
             "ebay_listing_id": "777"}
        with mock.patch.object(m, "_ebay_call",
                               side_effect=self._fake_call):
            m._discard_offer(d, "tok")
        self.assertEqual(self._deletes(), [])
        self.assertEqual(d["ebay_offer_id"], "OLD-OFFER")
        self.assertEqual(d["ebay_listing_id"], "777")

    def test_offer_create_records_policy(self):
        d = {"id": "d1", "photos": ["p.jpg"]}
        listing_id, _url = self._run(d, self._fields(fulfillment_id="111"))
        self.assertEqual(listing_id, "999")
        saved = m._load_draft("d1")
        self.assertEqual(saved["ebay_offer_id"], "OFFER-NEW")
        self.assertEqual(saved["ebay_offer_fulfillment_id"], "111")


class StaleOfferRouteTest(unittest.TestCase):
    """Single + batch pick re-posts discard via the HTTP routes."""

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
        self.calls = []
        self.offer_payloads = []

    def tearDown(self):
        for attr, val in self.orig.items():
            setattr(m, attr, val)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_call(self, method, path, token, payload=None):
        self.calls.append((method, path))
        if (method, path) == ("GET", "/sell/inventory/v1/location"):
            return {"locations": [{"merchantLocationKey": "LOC1"}]}
        if method == "GET" and path.startswith(
                "/sell/account/v1/fulfillment_policy"):
            return FLAT_FOR(path.rsplit("/", 1)[-1])
        if method == "PUT" and path.startswith(
                "/sell/inventory/v1/inventory_item/"):
            return {}
        if (method, path) == ("POST", "/sell/inventory/v1/offer"):
            self.offer_payloads.append(payload)
            return {"offerId": "OFFER-NEW"}
        if method == "POST" and path.startswith("/sell/inventory/v1/offer/"):
            return {"listingId": "999"}
        if method == "DELETE" and path.startswith("/sell/inventory/v1/offer/"):
            return {}
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

    def _seed(self, draft_id, offer_id=None, recorded=None):
        d = {"id": draft_id, "photos": ["p.jpg"], "title": "T",
             "description": "D", "price": "10.00", "condition": "NEW",
             "condition_details": "", "category_id": "123",
             "attributes": {}}
        if offer_id:
            d["ebay_offer_id"] = offer_id
        if recorded:
            d["ebay_offer_fulfillment_id"] = recorded
        with open(os.path.join(m.DRAFTS_DIR, draft_id + ".json"), "w",
                  encoding="utf-8") as f:
            json.dump(d, f)
        return d

    def _deletes(self):
        return [p for (method, p) in self.calls if method == "DELETE"]

    def test_single_pick_endpoint_discards(self):
        self._seed("d1", offer_id="OLD-OFFER", recorded="111")
        patches = self._patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        resp = self.client.post(
            "/api/ebay/publish",
            json={"draft_id": "d1", "fulfillment_policy_id": "999",
                  "payment_policy_id": "222", "return_policy_id": "333",
                  "policy_pick": True})
        body = resp.get_json()
        self.assertEqual(body.get("listing_id"), "999")
        self.assertEqual(self._deletes(),
                         ["/sell/inventory/v1/offer/OLD-OFFER"])
        self.assertEqual(len(self.offer_payloads), 1)
        self.assertEqual(self.offer_payloads[0]["listingPolicies"]
                         ["fulfillmentPolicyId"], "999")

    def test_batch_pick_discards_each_draft(self):
        self._seed("d1", offer_id="OLD-1", recorded="111")
        self._seed("d2", offer_id="OLD-2", recorded="111")
        patches = self._patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        resp = self.client.post(
            "/api/ebay/batch-publish",
            json={"draft_ids": ["d1", "d2"], "category_id": "123",
                  "fulfillment_policy_id": "999"})
        results = resp.get_json()["results"]
        self.assertEqual([r["ok"] for r in results], [True, True])
        self.assertEqual(sorted(self._deletes()),
                         ["/sell/inventory/v1/offer/OLD-1",
                          "/sell/inventory/v1/offer/OLD-2"])
        self.assertEqual(len(self.offer_payloads), 2)
        for payload in self.offer_payloads:
            self.assertEqual(payload["listingPolicies"]
                             ["fulfillmentPolicyId"], "999")


if __name__ == "__main__":
    unittest.main()
