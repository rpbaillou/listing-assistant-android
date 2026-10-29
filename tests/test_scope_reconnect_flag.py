"""apk-v33 tests: the scope-reconnect flag must reach the frontend.

Live Oct 29 01:35 on v1.31, Reynard correctly reported NO Connect
button: the app showed the pink scope message but the button stayed
hidden. Root cause chain, all in app.py:
  (1) the token-refresh helper raised a BARE RuntimeError carrying the
      scope message, and
  (2) _do_publish wrapped the token call in a broad `except
      RuntimeError -> _EbayAuthError`, which swallows even the typed
      signal (it subclasses RuntimeError),
so the publish/batch handlers returned the message WITHOUT the
needs_scope_reconnect flag, and the frontend only unhides the
scope-reconnect-box on the flag. v33 fixes every link: the refresh
helper raises _NeedsScopeReconnect, _do_publish re-raises it before
the broad catch, both endpoints map any scope-text error to the flag,
and the frontend unhides the box on the message text itself.

PRIVACY: the repo is PUBLIC. Every policy ID here is obviously dummy
(111/222/333). No real policy ID, address, or secret appears in code,
fixtures, or test output.

eBay is always mocked; no test here reaches the network. Storage dirs
are monkeypatched to a temp dir per test.
"""

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PY_DIR = os.path.join(REPO, "app", "src", "main", "python")
APP_SRC = os.path.join(PY_DIR, "app.py")
sys.path.insert(0, PY_DIR)

import app as m  # noqa: E402  (imports cleanly; __main__ guard prevents serving)


def tearDownModule():
    sys.modules.pop("app", None)


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


class RefreshSignalTest(unittest.TestCase):
    """The refresh helper must raise the typed signal, not a bare error."""

    def test_scope_failure_raises_typed_signal(self):
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
            with self.assertRaises(m._NeedsScopeReconnect) as ctx:
                m._ebay_access_token()
        clear.assert_called_once()
        self.assertEqual(str(ctx.exception), m.SCOPE_RECONNECT_MSG)

    def test_plain_refresh_failure_unchanged(self):
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
        self.assertNotEqual(str(ctx.exception), m.SCOPE_RECONNECT_MSG)


class DoPublishSignalTest(unittest.TestCase):
    """_do_publish must not convert the scope signal to an auth error."""

    def test_token_scope_signal_survives_do_publish(self):
        draft = {"id": "d1", "photos": ["p.jpg"]}
        fields = {"title": "Test item", "description": "A test item.",
                  "price_val": 10.0, "condition": "NEW",
                  "condition_details": "", "category_id": "123",
                  "fulfillment_id": "111", "payment_id": "222",
                  "return_id": "333", "attributes": {},
                  "best_offer_terms": None}
        with mock.patch.object(
                m, "_ebay_access_token",
                side_effect=m._NeedsScopeReconnect(m.SCOPE_RECONNECT_MSG)):
            with self.assertRaises(m._NeedsScopeReconnect) as ctx:
                m._do_publish(dict(draft), fields)
        self.assertEqual(str(ctx.exception), m.SCOPE_RECONNECT_MSG)


class EndpointFlagTest(unittest.TestCase):
    """Publish + batch map every scope-text error to the flag."""

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

    def tearDown(self):
        for attr, val in self.orig.items():
            setattr(m, attr, val)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_draft(self, draft_id):
        d = {"id": draft_id, "photos": ["p.jpg"], "title": "T",
             "description": "D", "price": "10.00", "condition": "NEW",
             "condition_details": "", "category_id": "123",
             "attributes": {}}
        with open(os.path.join(m.DRAFTS_DIR, draft_id + ".json"), "w",
                  encoding="utf-8") as f:
            json.dump(d, f)

    def _publish_body(self, draft_id):
        return {"draft_id": draft_id,
                "fulfillment_policy_id": "111",
                "payment_policy_id": "222",
                "return_policy_id": "333"}

    def test_publish_bare_scope_error_maps_to_flag(self):
        self._seed_draft("d1")
        with mock.patch.object(
                m, "_do_publish",
                side_effect=RuntimeError(m.SCOPE_RECONNECT_MSG)):
            resp = self.client.post("/api/ebay/publish",
                                    json=self._publish_body("d1"))
        body = resp.get_json()
        self.assertTrue(body.get("needs_scope_reconnect"))
        self.assertEqual(body.get("error"), m.SCOPE_RECONNECT_MSG)

    def test_publish_auth_error_scope_text_maps_to_flag(self):
        self._seed_draft("d1")
        with mock.patch.object(
                m, "_do_publish",
                side_effect=m._EbayAuthError(m.SCOPE_RECONNECT_MSG)):
            resp = self.client.post("/api/ebay/publish",
                                    json=self._publish_body("d1"))
        body = resp.get_json()
        self.assertTrue(body.get("needs_scope_reconnect"))
        self.assertEqual(body.get("error"), m.SCOPE_RECONNECT_MSG)

    def test_publish_typed_signal_maps_to_flag(self):
        self._seed_draft("d1")
        with mock.patch.object(
                m, "_do_publish",
                side_effect=m._NeedsScopeReconnect(m.SCOPE_RECONNECT_MSG)):
            resp = self.client.post("/api/ebay/publish",
                                    json=self._publish_body("d1"))
        body = resp.get_json()
        self.assertTrue(body.get("needs_scope_reconnect"))
        self.assertEqual(body.get("error"), m.SCOPE_RECONNECT_MSG)

    def test_publish_other_error_stays_bare(self):
        self._seed_draft("d1")
        with mock.patch.object(
                m, "_do_publish",
                side_effect=RuntimeError("Something else broke.")):
            resp = self.client.post("/api/ebay/publish",
                                    json=self._publish_body("d1"))
        body = resp.get_json()
        self.assertIsNone(body.get("needs_scope_reconnect"))
        self.assertEqual(body.get("error"), "Something else broke.")

    def test_batch_bare_scope_error_maps_to_flag_and_stops(self):
        self._seed_draft("d1")
        self._seed_draft("d2")
        with mock.patch.object(
                m, "_do_publish",
                side_effect=RuntimeError(m.SCOPE_RECONNECT_MSG)):
            resp = self.client.post("/api/ebay/batch-publish",
                                    json={"draft_ids": ["d1", "d2"],
                                          "category_id": "123"})
        results = resp.get_json()["results"]
        # The scope wall hits every draft, so the run stops at the
        # first draft instead of reporting a bare error per draft.
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].get("needs_scope_reconnect"))
        self.assertEqual(results[0].get("error"), m.SCOPE_RECONNECT_MSG)

    def test_batch_typed_signal_maps_to_flag(self):
        self._seed_draft("d1")
        with mock.patch.object(
                m, "_do_publish",
                side_effect=m._NeedsScopeReconnect(m.SCOPE_RECONNECT_MSG)):
            resp = self.client.post("/api/ebay/batch-publish",
                                    json={"draft_ids": ["d1"],
                                          "category_id": "123"})
        results = resp.get_json()["results"]
        self.assertTrue(results[0].get("needs_scope_reconnect"))


class TokenWipeScopeTest(unittest.TestCase):
    """The refresh-failure wipe removes only the token file."""

    def test_clear_removes_only_token_file(self):
        token_path = os.path.join(self.tmp_dir(), "ebay_token.json")
        draft_path = os.path.join(self.tmp_dir(), "d1.json")
        preset_path = os.path.join(self.tmp_dir(), "preset.json")
        for p in (token_path, draft_path, preset_path):
            with open(p, "w", encoding="utf-8") as f:
                f.write("{}")
        with mock.patch.object(m, "EBAY_TOKEN_FILE", token_path):
            m._clear_ebay_tokens()
        self.assertFalse(os.path.exists(token_path))
        self.assertTrue(os.path.exists(draft_path))
        self.assertTrue(os.path.exists(preset_path))

    def tmp_dir(self):
        if not hasattr(self, "_tmp"):
            self._tmp = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, self._tmp, True)
        return self._tmp


class SourceAuditTest(unittest.TestCase):
    """Static guard: no scope raise may ever be bare again."""

    def test_no_bare_scope_raise_remains(self):
        with open(APP_SRC, encoding="utf-8") as f:
            src = f.read()
        raises = re.findall(r"raise\s+(\w+)\(SCOPE_RECONNECT_MSG\)", src)
        self.assertTrue(raises, "expected at least one scope raise")
        for name in raises:
            self.assertEqual(name, "_NeedsScopeReconnect",
                             "scope message must only ever be raised typed")

    def test_endpoint_handlers_check_scope_text(self):
        with open(APP_SRC, encoding="utf-8") as f:
            src = f.read()
        # Publish auth handler, publish runtime handler, batch runtime
        # handler: each maps scope text to the flag as a safety net.
        self.assertGreaterEqual(src.count("== SCOPE_RECONNECT_MSG"), 3)


if __name__ == "__main__":
    unittest.main()
