"""apk-v36 tests: listing-page photo intake (append + replace).

v36 adds Take photo / Choose photos to the listing page so pictures can
be added to an existing draft any time, plus draft-photo editing through
the existing editor. These tests pin the server side: appends never
replace or reorder existing photos, the 24 cap is enforced with a plain
message instead of a silent drop, and replacement overwrites the same
stored file in place. Storage dirs are monkeypatched to a temp dir per
test, so nothing here touches the repo's real data dir. Dummy images
only; eBay is never touched.
"""
import io
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


def _jpeg_bytes(color="red"):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (50, 40), color).save(buf, "JPEG")
    buf.seek(0)
    return buf


class MultiPhotoTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig = {}
        for attr, sub in (("DRAFTS_DIR", "drafts"), ("PHOTOS_DIR", "photos")):
            self.orig[attr] = getattr(m, attr)
            p = os.path.join(self.tmp, sub)
            os.makedirs(p, exist_ok=True)
            setattr(m, attr, p)
        self.client = m.app.test_client()

    def tearDown(self):
        for attr, val in self.orig.items():
            setattr(m, attr, val)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _draft(self, photos):
        d = {"id": "d1", "title": "Test item", "description": "A thing.",
             "photos": list(photos), "price": "10.00"}
        return m._save_draft(d)

    def _photo_file(self, rel):
        full = os.path.join(m.PHOTOS_DIR, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(_jpeg_bytes().getvalue())
        return full

    def _post_photos(self, draft_id, files):
        data = {"photos": [(b, name) for b, name in files]}
        return self.client.post(f"/api/drafts/{draft_id}/photos",
                                data=data, content_type="multipart/form-data")

    # ---- append ----

    def test_append_keeps_existing_order_and_folder(self):
        self._photo_file("d1/1.jpg")
        self._draft(["d1/1.jpg"])
        resp = self._post_photos("d1", [(_jpeg_bytes("blue"), "a.jpg")])
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["added"], 1)
        self.assertEqual(body["dropped"], 0)
        self.assertEqual(body["draft"]["photos"], ["d1/1.jpg", "d1/2.jpg"])
        self.assertTrue(os.path.isfile(os.path.join(m.PHOTOS_DIR, "d1/2.jpg")))

    def test_append_multiple_at_once(self):
        self._photo_file("d1/1.jpg")
        self._draft(["d1/1.jpg"])
        resp = self._post_photos("d1", [(_jpeg_bytes("blue"), "a.jpg"),
                                        (_jpeg_bytes("green"), "b.jpg")])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["draft"]["photos"],
                         ["d1/1.jpg", "d1/2.jpg", "d1/3.jpg"])

    def test_append_full_draft_says_so_plainly(self):
        photos = [f"d1/{i}.jpg" for i in range(1, m.MAX_PHOTOS + 1)]
        self._draft(photos)
        resp = self._post_photos("d1", [(_jpeg_bytes(), "a.jpg")])
        self.assertEqual(resp.status_code, 400)
        self.assertIn("already has 24 photos", resp.get_json()["error"])
        self.assertEqual(m._load_draft("d1")["photos"], photos)

    def test_append_partial_room_reports_dropped(self):
        photos = [f"d1/{i}.jpg" for i in range(1, m.MAX_PHOTOS)]
        self._draft(photos)
        resp = self._post_photos("d1", [(_jpeg_bytes(), "a.jpg"),
                                        (_jpeg_bytes(), "b.jpg")])
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["added"], 1)
        self.assertEqual(body["dropped"], 1)
        self.assertEqual(len(body["draft"]["photos"]), m.MAX_PHOTOS)

    def test_append_missing_draft_404(self):
        resp = self._post_photos("nope", [(_jpeg_bytes(), "a.jpg")])
        self.assertEqual(resp.status_code, 404)

    def test_append_no_files_400(self):
        self._draft(["d1/1.jpg"])
        resp = self.client.post("/api/drafts/d1/photos",
                                data={}, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 400)

    def test_append_unreadable_only_400(self):
        self._draft(["d1/1.jpg"])
        resp = self._post_photos("d1", [(io.BytesIO(b"not an image"), "a.jpg")])
        self.assertEqual(resp.status_code, 400)
        self.assertIn("No usable photos", resp.get_json()["error"])

    def test_append_bad_extension_skipped(self):
        self._photo_file("d1/1.jpg")
        self._draft(["d1/1.jpg"])
        resp = self._post_photos("d1", [(io.BytesIO(b"hello"), "a.txt"),
                                        (_jpeg_bytes(), "b.jpg")])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["draft"]["photos"],
                         ["d1/1.jpg", "d1/2.jpg"])

    # ---- replace ----

    def test_replace_overwrites_same_file_in_place(self):
        before = self._photo_file("d1/1.jpg")
        self._photo_file("d1/2.jpg")
        self._draft(["d1/1.jpg", "d1/2.jpg"])
        with open(before, "rb") as f:
            old_bytes = f.read()
        resp = self.client.post("/api/drafts/d1/photos/0/replace",
                                data={"photo": (_jpeg_bytes("blue"), "edited.jpg")},
                                content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["draft"]["photos"], ["d1/1.jpg", "d1/2.jpg"])
        with open(before, "rb") as f:
            new_bytes = f.read()
        self.assertNotEqual(old_bytes, new_bytes)

    def test_replace_bad_index_404(self):
        self._draft(["d1/1.jpg"])
        resp = self.client.post("/api/drafts/d1/photos/5/replace",
                                data={"photo": (_jpeg_bytes(), "e.jpg")},
                                content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 404)

    def test_replace_no_file_400(self):
        self._draft(["d1/1.jpg"])
        resp = self.client.post("/api/drafts/d1/photos/0/replace",
                                data={}, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 400)

    def test_replace_missing_draft_404(self):
        resp = self.client.post("/api/drafts/nope/photos/0/replace",
                                data={"photo": (_jpeg_bytes(), "e.jpg")},
                                content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 404)

    def test_replace_unreadable_400(self):
        self._photo_file("d1/1.jpg")
        self._draft(["d1/1.jpg"])
        resp = self.client.post("/api/drafts/d1/photos/0/replace",
                                data={"photo": (io.BytesIO(b"junk"), "e.jpg")},
                                content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 400)

    # ---- _store_uploads refactor regression ----

    def test_store_uploads_still_creates_new_folder(self):
        class _FakeUpload:
            """Flask FileStorage shape: filename + stream."""
            def __init__(self, buf, filename):
                self.stream = buf
                self.filename = filename

        draft_id, rels = m._store_uploads([_FakeUpload(_jpeg_bytes(), "a.jpg")])
        self.assertEqual(rels, [f"{draft_id}/1.jpg"])
        self.assertTrue(os.path.isfile(os.path.join(m.PHOTOS_DIR, rels[0])))


if __name__ == "__main__":
    unittest.main()
