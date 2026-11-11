"""apk-v54 tests: an edited photo shows the edited picture.

v54: a photo edit replaces the same file path on the server
(api_draft_replace_photo overwrites folder/N.jpg), and the old
/photos/ route answered with Flask's default 12h cache — so the
draft page and the restart/restore fetch kept showing the pre-edit
picture even though the edited bytes had landed. serve_photo now
sends no-cache (revalidate on every load), so a replaced photo
renders its new bytes.

Storage dirs are monkeypatched to a temp dir per test, so nothing
here touches the repo's real data dir. Dummy data only; eBay is
never touched.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PY_DIR = os.path.join(REPO, "app", "src", "main", "python")
sys.path.insert(0, PY_DIR)

import app as m  # noqa: E402  (imports cleanly; __main__ guard prevents serving)


def tearDownModule():
    sys.modules.pop("app", None)


def _jpeg_bytes(color="red", w=50, h=40):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG")
    buf.seek(0)
    return buf.read()


class PhotoCacheBustTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig = {}
        for attr, sub in (("DRAFTS_DIR", "drafts"), ("PHOTOS_DIR", "photos")):
            self.orig[attr] = getattr(m, attr)
            p = os.path.join(self.tmp, sub)
            os.makedirs(p, exist_ok=True)
            setattr(m, attr, p)
        self.orig["RESUME_FILE"] = m.RESUME_FILE
        m.RESUME_FILE = os.path.join(self.tmp, "resume_draft.json")
        self.client = m.app.test_client()

    def tearDown(self):
        for attr, val in self.orig.items():
            setattr(m, attr, val)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_draft_with_photo(self, color="red"):
        r = self.client.post("/api/auto-draft")
        self.assertEqual(r.status_code, 200)
        draft_id = r.get_json()["draft"]["id"]
        data = {"photos": [(io.BytesIO(_jpeg_bytes(color)), "photo.jpg")]}
        r = self.client.post(f"/api/drafts/{draft_id}/photos",
                             data=data, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200)
        rel = r.get_json()["draft"]["photos"][0]
        return draft_id, rel

    def test_serve_photo_sends_no_cache(self):
        _, rel = self._make_draft_with_photo()
        r = self.client.get("/photos/" + rel)
        self.assertEqual(r.status_code, 200)
        self.assertIn("no-cache", r.headers.get("Cache-Control", ""))
        # Revalidation must be possible, so an unchanged photo still
        # answers 304 instead of a full re-download.
        self.assertTrue(r.headers.get("ETag"))

    def test_replaced_photo_serves_new_bytes(self):
        draft_id, rel = self._make_draft_with_photo("red")
        first = self.client.get("/photos/" + rel)
        self.assertEqual(first.status_code, 200)
        red_bytes = first.data
        self.assertTrue(red_bytes)
        # Replace the photo in place (the editor save path).
        blue = _jpeg_bytes("blue")
        data = {"photo": (io.BytesIO(blue), "edited.jpg")}
        r = self.client.post(f"/api/drafts/{draft_id}/photos/0/replace",
                             data=data, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200)
        # The next load must be the new picture, not the cached old one.
        second = self.client.get("/photos/" + rel)
        self.assertEqual(second.status_code, 200)
        self.assertNotEqual(second.data, red_bytes)
        self.assertEqual(second.data, blue)


if __name__ == "__main__":
    unittest.main()
