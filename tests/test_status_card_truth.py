"""apk-v53 tests: the status card must tell the truth after the sign-in dies.

Live v1.51 bug (Nov 9 02:08 CST): the listing page said BOTH "eBay
connected. Ready to list." (status card, rendered from
GET /api/ebay/status, which only checks the token file for a
refresh_token at page load) AND "eBay is not connected. Open the app
in this browser and tap Connect eBay." (publish error, raised by
_ebay_access_token() after a failed renewal / 401 wiped the tokens
mid-page). Two checks, two moments, one dead sign-in — and the card
never re-checked.

The v53 fix is twofold: (1) the JS re-runs the status check after
any token-death message (tests/status-card-refresh.test.js proves
the card flips); (2) the server messages stop saying "in this
browser" — he IS in the app. This file pins (2) plus the scope
message staying byte-identical (the JS matches on its substring).
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PY_DIR = os.path.join(REPO, "app", "src", "main", "python")
APP_JS = os.path.join(REPO, "app", "src", "main", "python", "static",
                      "app.js")
sys.path.insert(0, PY_DIR)

import app as m  # noqa: E402  (imports cleanly; __main__ guard prevents serving)


def tearDownModule():
    # test_version_badge.py re-imports `app` with its own APP_VERSION env var;
    # drop our cached copy so its import is fresh and order-independent.
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


class TokenDeathWordingTest(unittest.TestCase):
    """Every token-death message names Connect eBay plainly — no
    "in this browser", because he IS in the app."""

    def test_not_connected_message_plain(self):
        with mock.patch.object(m, "_ebay_configured", return_value=True), \
                mock.patch.object(m, "_load_ebay_tokens", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                m._ebay_access_token()
        self.assertEqual(
            str(ctx.exception),
            "eBay is not connected. Tap Connect eBay to sign in again.")
        self.assertNotIn("in this browser", str(ctx.exception))

    def test_refresh_failure_message_plain(self):
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
        self.assertEqual(
            str(ctx.exception),
            "Your eBay sign-in expired and could not be renewed. "
            "Tap Connect eBay to sign in again.")
        self.assertNotIn("in this browser", str(ctx.exception))

    def test_401_message_plain(self):
        resp = _fake_resp(401, json_body={"errors": [{"message": "bad token",
                                                      "errorId": 190001}]})
        with mock.patch.object(m.requests, "request",
                               return_value=resp), \
                mock.patch.object(m, "_clear_ebay_tokens"):
            with self.assertRaises(RuntimeError) as ctx:
                m._ebay_call("GET", "/sell/inventory/v1/location", "tok")
        self.assertEqual(
            str(ctx.exception),
            "Your eBay sign-in stopped working. "
            "Tap Connect eBay to sign in again.")
        self.assertNotIn("in this browser", str(ctx.exception))

    def test_media_403_photo_permission_message_plain(self):
        body = b'{"errorId": 190013, "message": "not authorized"}'
        with mock.patch.object(m, "_clear_ebay_tokens"):
            result = self._upload([_fake_resp(403, body)])
        self.assertIsInstance(result, RuntimeError)
        self.assertIn("Tap Connect eBay to grant it.", str(result))
        self.assertNotIn("in this browser", str(result))

    def test_media_401_message_plain(self):
        with mock.patch.object(m, "_clear_ebay_tokens"):
            result = self._upload([_fake_resp(401, b"unauthorized")])
        self.assertIsInstance(result, RuntimeError)
        self.assertIn("sign-in stopped working", str(result))
        self.assertIn("Tap Connect eBay to sign in again.", str(result))
        self.assertNotIn("in this browser", str(result))

    def _upload(self, posts, token="<redacted>"):
        """Run _ebay_upload_picture with requests.post mocked.
        Returns the URL or the raised RuntimeError."""
        tmp = tempfile.TemporaryDirectory()
        photo = os.path.join(tmp.name, "p.jpg")
        with open(photo, "wb") as f:
            f.write(b"\xff\xd8fakejpgbytes")
        try:
            with mock.patch.object(m.requests, "post",
                                   side_effect=posts):
                try:
                    return m._ebay_upload_picture(photo, token)
                except RuntimeError as exc:
                    return exc
        finally:
            tmp.cleanup()


class ScopeMessageUnchangedTest(unittest.TestCase):
    """The v33 scope message is matched by substring in the JS
    (isScopeReconnectText) — its text must stay byte-identical."""

    def test_scope_reconnect_message_unchanged(self):
        self.assertEqual(
            m.SCOPE_RECONNECT_MSG,
            "The app needs one new permission to read your shipping "
            "policies. Tap Connect eBay and approve it.")


class NoInThisBrowserGuardTest(unittest.TestCase):
    """Never-again guard: the confusing "in this browser" phrasing
    must not come back in any token message, server or client. The
    one legitimate "in the browser" (the remote-host connect
    refusal, which really does mean a browser on the device) uses
    different words and is untouched."""

    def test_no_in_this_browser_in_app_py_or_app_js(self):
        for path in (os.path.join(PY_DIR, "app.py"), APP_JS):
            with open(path, encoding="utf-8") as f:
                src = f.read()
            self.assertNotIn(
                "in this browser", src,
                "%s still says 'in this browser' — the v53 wording "
                "fix regressed." % path)


if __name__ == "__main__":
    unittest.main()
