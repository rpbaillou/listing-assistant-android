"""apk-v28 tests: eBay picture hosting must use the multipart EPS form.

Regression test for the v1.24/v1.25 first-live-publish failure: tapping
"List it on eBay now" reached picture hosting and eBay answered HTTP 404
with the plain-text body "Resource not found." Probes ruled out the
endpoint, headers, body size, token presence, token type, and compat
level (1193 -> 1451 changed nothing). What differs is the upload form:
the app sent the photo as base64 PictureData inside a text/xml body,
while eBay's photo layer in 2026 wants the multipart form (XML part
first with PictureName + PictureSet, raw image bytes as the second
part — same shape as the hendt ebay-api library's
UploadSiteHostedPictures example).
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


class PictureMultipartTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.jpg = os.path.join(self.tmp.name, "p.jpg")
        with open(self.jpg, "wb") as f:
            f.write(b"\xff\xd8fakejpgbytes")
        self.png = os.path.join(self.tmp.name, "p.png")
        with open(self.png, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\nfakepngbytes")

    def tearDown(self):
        self.tmp.cleanup()

    def test_xml_part_has_name_and_pictureset_no_picturedata(self):
        """The XML part carries PictureName + PictureSet and NO
        PictureData element (the photo travels as raw bytes)."""
        _files, xml = m._build_picture_upload(self.jpg, "sku-1")
        self.assertIn("<PictureName>sku-1</PictureName>", xml)
        self.assertIn("<PictureSet>Supersize</PictureSet>", xml)
        self.assertNotIn("PictureData", xml)
        self.assertNotIn("ZmFrZWpwZ2J5dGVz", xml)  # no base64 of the photo

    def test_xml_part_first_and_image_part_raw(self):
        """Part order is XML first, then raw image bytes with a
        matching Content-Type and a filename."""
        files, _xml = m._build_picture_upload(self.jpg, "sku-1")
        keys = list(files.keys())
        self.assertEqual(keys[0], "XML Payload")
        xml_name, xml_bytes, xml_type = files["XML Payload"]
        self.assertEqual(xml_name, "payload.xml")
        self.assertEqual(xml_type, "text/xml")
        self.assertIn(b"<PictureName>sku-1</PictureName>", xml_bytes)
        img_name, img_bytes, img_type = files["dummy"]
        self.assertTrue(img_name.startswith("sku-1"))
        self.assertEqual(img_bytes, b"\xff\xd8fakejpgbytes")
        self.assertEqual(img_type, "image/jpeg")

    def test_content_type_detected_from_magic_bytes(self):
        """PNG magic bytes win over the extension; an unknown file
        defaults to image/jpeg."""
        _files, _xml = m._build_picture_upload(self.png, "sku-1")
        self.assertEqual(m._picture_content_type(
            b"\x89PNG\r\n\x1a\nx", "p.jpg"), "image/png")
        self.assertEqual(m._picture_content_type(
            b"\xff\xd8\xffx", "p.png"), "image/jpeg")
        self.assertEqual(m._picture_content_type(b"????", "p.jpg"),
                         "image/jpeg")

    def test_upload_request_is_multipart_with_trading_headers(self):
        """The live request uses files= (requests builds the multipart
        boundary), carries NO text/xml Content-Type and NO data= body,
        and keeps every Trading header including compat 1451."""
        resp = mock.Mock()
        resp.status_code = 200
        resp.content = _success_xml()
        resp.text = _success_xml().decode("utf-8")
        with mock.patch.object(m.requests, "post",
                               return_value=resp) as post:
            url = m._ebay_upload_picture(self.jpg, "tok-abc", "sku-1")
        self.assertEqual(url, "https://i.ebayimg.com/example.jpg")
        kwargs = post.call_args.kwargs
        self.assertIn("files", kwargs)
        self.assertNotIn("data", kwargs)
        headers = kwargs["headers"]
        self.assertNotIn("Content-Type", headers)
        self.assertEqual(headers["X-EBAY-API-CALL-NAME"],
                         "UploadSiteHostedPictures")
        self.assertEqual(headers["X-EBAY-API-SITEID"], "0")
        self.assertEqual(headers["X-EBAY-API-COMPATIBILITY-LEVEL"], "1451")
        self.assertEqual(headers["X-EBAY-API-IAF-TOKEN"], "tok-abc")

    def test_no_token_or_photo_bytes_in_surfaced_error(self):
        """A surfaced 4xx error must contain neither the token nor the
        raw photo bytes."""
        resp = mock.Mock()
        resp.status_code = 404
        resp.content = b"Resource not found."
        resp.text = "Resource not found."
        with mock.patch.object(m.requests, "post",
                               return_value=resp):
            try:
                m._ebay_upload_picture(self.jpg, "tok-abc-secret",
                                       "sku-1")
                self.fail("expected RuntimeError")
            except RuntimeError as exc:
                msg = str(exc)
        self.assertNotIn("tok-abc-secret", msg)
        self.assertNotIn("fakejpgbytes", msg)


if __name__ == "__main__":
    unittest.main()
