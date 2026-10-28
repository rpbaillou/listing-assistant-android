"""apk-v26 tests: picture hosting must speak eBay's current Trading API
version.

Regression test for the v1.24 first-live-publish failure: tapping "List it
on eBay now" showed "eBay picture hosting answered with error 404:
Resource not found." Probes ruled out endpoint, headers, body size, token
presence, and token type; the remaining difference that only bites a real,
valid token is the API version — the app sent compat level 1193 (circa
2021) while eBay's Trading API is at 1451 and old versions are being
decommissioned (a retired version makes the gateway answer 404 instead of
a proper error). This test pins the upload request to 1451.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PY_DIR = os.path.join(REPO, "app", "src", "main", "python")
sys.path.insert(0, PY_DIR)

import app as m  # noqa: E402  (imports cleanly; __main__ guard prevents serving)


def tearDownModule():
    # test_version_badge.py re-imports `app` with its own APP_VERSION env var;
    # drop our cached copy so its import is fresh and order-independent.
    sys.modules.pop("app", None)


def _success_xml(url="https://i.ebayimg.com/example.jpg"):
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<UploadSiteHostedPicturesResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
        "<Ack>Success</Ack>"
        "<SiteHostedPictureDetails>"
        f"<FullURL>{url}</FullURL>"
        "</SiteHostedPictureDetails>"
        "</UploadSiteHostedPicturesResponse>"
    ).encode("utf-8")


class TradingCompatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.photo = os.path.join(self.tmp.name, "p.jpg")
        with open(self.photo, "wb") as f:
            f.write(b"\xff\xd8fakejpgbytes")

    def tearDown(self):
        self.tmp.cleanup()

    def test_compat_level_is_current(self):
        """The Trading compat constant must be 1451, not the retired 1193."""
        self.assertEqual(m.EBAY_TRADING_COMPAT_LEVEL, "1451")

    def test_upload_request_carries_compat_1451(self):
        """The actual UploadSiteHostedPictures request must carry the
        X-EBAY-API-COMPATIBILITY-LEVEL: 1451 header."""
        resp = mock.Mock()
        resp.status_code = 200
        resp.content = _success_xml()
        resp.text = _success_xml().decode("utf-8")
        with mock.patch.object(m.requests, "post",
                               return_value=resp) as post:
            url = m._ebay_upload_picture(self.photo, "tok-abc", "sku-1")
        self.assertEqual(url, "https://i.ebayimg.com/example.jpg")
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["X-EBAY-API-COMPATIBILITY-LEVEL"], "1451")
        self.assertEqual(
            headers["X-EBAY-API-CALL-NAME"], "UploadSiteHostedPictures")


if __name__ == "__main__":
    unittest.main()
