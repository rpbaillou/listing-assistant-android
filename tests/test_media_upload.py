"""apk-v29 tests: eBay photo upload goes through the Media API now.

The Trading API call UploadSiteHostedPictures was decommissioned by eBay
on Sept 30, 2026, so every upload format failed with a gateway 404. The
replacement is POST apim.ebay.com/commerce/media/v1_beta/image/
create_image_from_file with the same OAuth user token (sell.inventory
scope), multipart form key "image" = the photo's raw bytes, no JSON
payload. The create response's imageUrl is what goes in the listing's
imageUrls — never the Location URI or the bare image_id. If the create
body has no imageUrl, GET image/{id} from the Location header supplies
it. Error handling keeps the v25 pattern: status + first ~300 chars of
eBay's reply (token redacted, photo bytes never in messages), one retry
on 5xx/empty-body only, never on 4xx; 401/403 clears tokens and tells
him to reconnect, with a plain photo-permission message for 403/190013.
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


class MediaUploadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.photo = os.path.join(self.tmp.name, "p.jpg")
        with open(self.photo, "wb") as f:
            f.write(b"\xff\xd8fakejpgbytes")

    def tearDown(self):
        self.tmp.cleanup()

    def _upload(self, posts, gets=None, token="tok-abc"):
        """Run _ebay_upload_picture with requests.post/get mocked.
        Returns (result-or-raised, post_mock, get_mock)."""
        with mock.patch.object(m.requests, "post",
                               side_effect=posts) as post, \
                mock.patch.object(m.requests, "get",
                                  side_effect=gets or []) as get:
            try:
                return m._ebay_upload_picture(self.photo, token), post, get
            except RuntimeError as exc:
                return exc, post, get

    def test_201_image_url_returned(self):
        body = {"imageUrl": "https://i.ebayimg.com/media/abc.jpg",
                "expirationDate": "2026-11-27T00:00:00.000Z"}
        result, post, get = self._upload([_fake_resp(201, json_body=body)])
        self.assertEqual(result, "https://i.ebayimg.com/media/abc.jpg")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(get.call_count, 0)

    def test_request_shape_is_media_api_multipart(self):
        """POST apim URL, Bearer auth only, files key "image" with the
        photo's raw bytes — no Trading headers, no PictureData, no JSON
        payload, no ws/api.dll URL."""
        body = {"imageUrl": "https://i.ebayimg.com/media/abc.jpg"}
        _, post, _ = self._upload([_fake_resp(201, json_body=body)])
        args, kwargs = post.call_args
        self.assertEqual(
            args[0],
            "https://apim.ebay.com/commerce/media/v1_beta/"
            "image/create_image_from_file")
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer tok-abc"})
        self.assertEqual(set(kwargs["files"].keys()), {"image"})
        name, raw, content_type = kwargs["files"]["image"]
        self.assertEqual(name, "p.jpg")
        self.assertEqual(raw, b"\xff\xd8fakejpgbytes")
        self.assertEqual(content_type, "image/jpeg")
        self.assertNotIn("data", kwargs)
        self.assertNotIn("json", kwargs)
        self.assertNotIn("CALL-NAME", str(kwargs))
        self.assertNotIn("api.dll", args[0])

    def test_png_content_type_detected_from_magic_bytes(self):
        png = os.path.join(self.tmp.name, "p.png")
        with open(png, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\nfakepng")
        body = {"imageUrl": "https://i.ebayimg.com/media/png.jpg"}
        with mock.patch.object(m.requests, "post",
                               return_value=_fake_resp(201, json_body=body)) as post:
            result = m._ebay_upload_picture(png, "tok-abc")
        self.assertEqual(result, "https://i.ebayimg.com/media/png.jpg")
        self.assertEqual(post.call_args[1]["files"]["image"][2], "image/png")

    def test_get_fallback_when_create_lacks_image_url(self):
        """Create 201 with no imageUrl but a Location header: GET
        image/{id} supplies the URL. The Location URI itself is never
        returned."""
        create = _fake_resp(201, json_body={},
                            headers={"Location":
                                     "https://apim.ebay.com/commerce/media/"
                                     "v1_beta/image/abc123"})
        got = _fake_resp(200, json_body={
            "imageUrl": "https://i.ebayimg.com/media/abc123.jpg"})
        result, post, get = self._upload([create], gets=[got])
        self.assertEqual(result, "https://i.ebayimg.com/media/abc123.jpg")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(get.call_count, 1)
        get_args, get_kwargs = get.call_args
        self.assertEqual(get_args[0],
                         "https://apim.ebay.com/commerce/media/v1_beta/"
                         "image/abc123")
        self.assertEqual(get_kwargs["headers"],
                         {"Authorization": "Bearer tok-abc"})

    def test_no_image_url_and_no_location_raises_naming_missing_address(self):
        result, _, get = self._upload([_fake_resp(201, json_body={})])
        self.assertIsInstance(result, RuntimeError)
        self.assertIn("did not return its address", str(result))
        self.assertEqual(get.call_count, 0)

    def test_get_fallback_failure_raises_naming_missing_address(self):
        create = _fake_resp(201, json_body={},
                            headers={"Location":
                                     "https://apim.ebay.com/commerce/media/"
                                     "v1_beta/image/abc123"})
        result, _, get = self._upload([create],
                                      gets=[_fake_resp(404, b"not found")])
        self.assertIsInstance(result, RuntimeError)
        self.assertIn("did not return its address", str(result))
        self.assertEqual(get.call_count, 1)

    def test_5xx_retries_once_then_surfaces_status_and_snippet(self):
        body = b"<html>Bad Gateway from eBay</html>"
        result, post, _ = self._upload([_fake_resp(502, body),
                                        _fake_resp(502, body)])
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(post.call_count, 2)
        self.assertIn("502", str(result))
        self.assertIn("Bad Gateway", str(result))

    def test_5xx_then_success_retries_with_identical_request(self):
        body = b"<html>Bad Gateway</html>"
        ok = {"imageUrl": "https://i.ebayimg.com/media/abc.jpg"}
        result, post, _ = self._upload([_fake_resp(502, body),
                                        _fake_resp(201, json_body=ok)])
        self.assertEqual(result, "https://i.ebayimg.com/media/abc.jpg")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0], post.call_args_list[1])

    def test_4xx_never_retried_and_surfaces_status_and_snippet(self):
        body = b'{"errorId": 190201, "message": "image too large"}'
        result, post, _ = self._upload([_fake_resp(400, body)])
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(post.call_count, 1)
        self.assertIn("400", str(result))
        self.assertIn("image too large", str(result))

    def test_empty_body_retries_once_then_surfaces(self):
        result, post, _ = self._upload([_fake_resp(200, b""),
                                        _fake_resp(200, b"   ")])
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(post.call_count, 2)
        self.assertIn("200", str(result))

    def test_401_clears_tokens_and_reconnects(self):
        with mock.patch.object(m, "_clear_ebay_tokens") as clear:
            result, post, _ = self._upload([_fake_resp(401, b"unauthorized")])
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(clear.call_count, 1)
        self.assertIn("sign-in stopped working", str(result))

    def test_403_with_190013_names_missing_photo_permission(self):
        body = b'{"errorId": 190013, "message": "not authorized"}'
        with mock.patch.object(m, "_clear_ebay_tokens") as clear:
            result, post, _ = self._upload([_fake_resp(403, body)])
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(clear.call_count, 1)
        self.assertIn("photo permission", str(result))
        self.assertIn("sell.inventory", str(result))

    def test_403_without_scope_marker_still_reconnects(self):
        with mock.patch.object(m, "_clear_ebay_tokens") as clear:
            result, _, _ = self._upload([_fake_resp(403, b"forbidden")])
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(clear.call_count, 1)
        self.assertIn("sign-in stopped working", str(result))

    def test_token_never_appears_in_error_message(self):
        token = "SECRET-TOKEN-123"
        body = ("<html>error involving " + token + "</html>").encode("utf-8")
        result, _, _ = self._upload([_fake_resp(500, body),
                                     _fake_resp(500, body)], token=token)
        self.assertIsInstance(result, RuntimeError)
        self.assertNotIn(token, str(result))
        self.assertIn("[removed]", str(result))

    def test_photo_bytes_never_appear_in_error_message(self):
        body = b"<html>Bad Gateway</html>"
        result, _, _ = self._upload([_fake_resp(500, body),
                                     _fake_resp(500, body)])
        self.assertIsInstance(result, RuntimeError)
        self.assertNotIn("fakejpgbytes", str(result))

    def test_snippet_capped_at_300_chars(self):
        body = b"x" * 1000
        result, _, _ = self._upload([_fake_resp(500, body),
                                     _fake_resp(500, body)])
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(str(result).count("x"), 300)


if __name__ == "__main__":
    unittest.main()
