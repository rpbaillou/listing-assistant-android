"""apk-v25 tests: eBay picture-hosting reply handling must never discard
what eBay sent.

Regression test for the v1.23 first-live-publish failure: tapping "List it
on eBay now" showed "eBay picture hosting sent back something unreadable.
Try again." The cause was _ebay_upload_picture parsing resp.text (str)
with ET.fromstring and, on ANY ParseError, throwing away eBay's status
and body. The fix parses resp.content (bytes), surfaces status + body
snippet on failures, and retries once only on transient failures.
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


def _failure_xml(message="Picture format not supported"):
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<UploadSiteHostedPicturesResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
        "<Ack>Failure</Ack>"
        f"<Errors><LongMessage>{message}</LongMessage></Errors>"
        "</UploadSiteHostedPicturesResponse>"
    ).encode("utf-8")


def _fake_resp(content, status=200):
    r = mock.Mock()
    r.status_code = status
    r.content = content
    r.text = content.decode("utf-8", "replace") if isinstance(
        content, (bytes, bytearray)) else content
    return r


class PictureUploadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.photo = os.path.join(self.tmp.name, "p.jpg")
        with open(self.photo, "wb") as f:
            f.write(b"\xff\xd8fakejpgbytes")

    def tearDown(self):
        self.tmp.cleanup()

    def _upload(self, responses, token="tok-abc"):
        """Run _ebay_upload_picture with requests.post mocked to return
        each response in turn. Returns (result-or-raised, post_mock)."""
        with mock.patch.object(m.requests, "post",
                               side_effect=responses) as post:
            try:
                return m._ebay_upload_picture(self.photo, token, "sku-1"), post
            except RuntimeError as exc:
                return exc, post

    def test_bom_body_parses(self):
        """Bytes with a BOM + encoding declaration must parse (str parsing
        chokes on exactly this)."""
        body = b"\xef\xbb\xbf" + _success_xml()
        result, post = self._upload([_fake_resp(body)])
        self.assertEqual(result, "https://i.ebayimg.com/example.jpg")
        self.assertEqual(post.call_count, 1)

    def test_encoding_declaration_body_parses(self):
        result, post = self._upload([_fake_resp(_success_xml())])
        self.assertEqual(result, "https://i.ebayimg.com/example.jpg")
        self.assertEqual(post.call_count, 1)

    def test_warning_ack_returns_url(self):
        body = _success_xml().replace(b"<Ack>Success</Ack>",
                                      b"<Ack>Warning</Ack>")
        result, _ = self._upload([_fake_resp(body)])
        self.assertEqual(result, "https://i.ebayimg.com/example.jpg")

    def test_hosted_but_no_address_unchanged(self):
        body = _success_xml().replace(
            b"<SiteHostedPictureDetails>"
            b"<FullURL>https://i.ebayimg.com/example.jpg</FullURL>"
            b"</SiteHostedPictureDetails>",
            b"<SiteHostedPictureDetails></SiteHostedPictureDetails>")
        result, _ = self._upload([_fake_resp(body)])
        self.assertIsInstance(result, RuntimeError)
        self.assertIn("did not return its address", str(result))

    def test_5xx_retries_once_then_surfaces_status_and_snippet(self):
        body = b"<html>Bad Gateway from eBay</html>"
        result, post = self._upload([_fake_resp(body, 502),
                                     _fake_resp(body, 502)])
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(post.call_count, 2)
        self.assertIn("502", str(result))
        self.assertIn("Bad Gateway", str(result))
        self.assertNotIn("unreadable", str(result))

    def test_5xx_then_success_retries_with_identical_request(self):
        body = b"<html>Bad Gateway</html>"
        result, post = self._upload([_fake_resp(body, 502),
                                     _fake_resp(_success_xml())])
        self.assertEqual(result, "https://i.ebayimg.com/example.jpg")
        self.assertEqual(post.call_count, 2)
        first, second = post.call_args_list[0], post.call_args_list[1]
        self.assertEqual(first[0], second[0])
        self.assertEqual(first[1], second[1])

    def test_4xx_never_retried_and_surfaces_status_and_snippet(self):
        body = b"Bad Request: picture too large"
        result, post = self._upload([_fake_resp(body, 400)])
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(post.call_count, 1)
        self.assertIn("400", str(result))
        self.assertIn("picture too large", str(result))
        self.assertNotIn("unreadable", str(result))

    def test_parse_failure_200_retries_once_then_surfaces(self):
        body = b"\x00\x01garbled-not-xml"
        result, post = self._upload([_fake_resp(body, 200),
                                     _fake_resp(body, 200)])
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(post.call_count, 2)
        self.assertIn("200", str(result))
        self.assertNotIn("unreadable", str(result))

    def test_parse_failure_then_success(self):
        result, post = self._upload([_fake_resp(b"garbled", 200),
                                     _fake_resp(_success_xml())])
        self.assertEqual(result, "https://i.ebayimg.com/example.jpg")
        self.assertEqual(post.call_count, 2)

    def test_empty_body_200_retries_once_then_surfaces(self):
        result, post = self._upload([_fake_resp(b"", 200),
                                     _fake_resp(b"   ", 200)])
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(post.call_count, 2)
        self.assertIn("200", str(result))
        self.assertNotIn("unreadable", str(result))

    def test_ack_failure_never_retried_and_keeps_long_message(self):
        result, post = self._upload([_fake_resp(_failure_xml())])
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(post.call_count, 1)
        self.assertIn("Picture format not supported", str(result))

    def test_401_unparseable_clears_tokens_and_reconnects(self):
        with mock.patch.object(m, "_clear_ebay_tokens") as clear:
            result, post = self._upload([_fake_resp(b"not xml", 401)])
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(clear.call_count, 1)
        self.assertIn("sign-in stopped working", str(result))

    def test_token_never_appears_in_error_message(self):
        token = "SECRET-TOKEN-123"
        body = ("<html>error involving " + token + "</html>").encode("utf-8")
        result, _ = self._upload([_fake_resp(body, 500),
                                  _fake_resp(body, 500)], token=token)
        self.assertIsInstance(result, RuntimeError)
        self.assertNotIn(token, str(result))
        self.assertIn("[removed]", str(result))

    def test_snippet_capped_at_300_chars(self):
        body = b"x" * 1000
        result, _ = self._upload([_fake_resp(body, 500),
                                  _fake_resp(body, 500)])
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(str(result).count("x"), 300)


if __name__ == "__main__":
    unittest.main()
