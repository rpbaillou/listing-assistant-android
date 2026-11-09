"""apk-v50 tests: photo persistence (auto draft) and return-to-draft
(resume) on the server side.

Auto draft: POST /api/auto-draft makes a blank draft the moment the
first photo is picked or taken, so photos have a home on the server
before "Draft my listing with AI" or Quick post is ever tapped. The
client uploads each photo to it through the existing draft photo
append path. The auto draft is an ordinary draft otherwise: it shows
in My drafts newest first, "(untitled)" until he types a title, and
Analyze reuses it (reanalyze) instead of making a second draft with
the same photos.
Resume: the last draft he had open is remembered in resume_draft.json
and returned by GET /api/resume-draft, so launch can offer it in one
tap. A listed or deleted draft is forgotten, never offered again.

PRIVACY: the repo is PUBLIC. No real policy ID, address, or secret
appears in code, fixtures, or test output.

Storage dirs are monkeypatched to a temp dir per test, so nothing here
touches the repo's real data dir. eBay and Gemini are always mocked;
no test here reaches the network.
"""
import io
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


def _jpeg_bytes(color="red"):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (50, 40), color).save(buf, "JPEG")
    buf.seek(0)
    return buf


class AutoDraftResumeTestCase(unittest.TestCase):
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

    def _write_draft(self, draft_id, **fields):
        d = {"id": draft_id, "title": "Dummy " + draft_id,
             "photos": [], "price": ""}
        d.update(fields)
        with open(os.path.join(m.DRAFTS_DIR, draft_id + ".json"),
                  "w", encoding="utf-8") as f:
            json.dump(d, f)
        return d

    def _post_photos(self, draft_id, files):
        data = {"photos": [(b, name) for b, name in files]}
        return self.client.post(f"/api/drafts/{draft_id}/photos",
                                data=data,
                                content_type="multipart/form-data")

    def _set_resume(self, draft_id):
        r = self.client.post("/api/resume-draft",
                             json={"draft_id": draft_id})
        self.assertEqual(r.status_code, 200)
        return r

    # ---- auto draft ----

    def test_auto_draft_creates_a_blank_draft(self):
        r = self.client.post("/api/auto-draft")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()["draft"]
        self.assertTrue(d["id"])
        self.assertEqual(d["photos"], [])
        self.assertEqual(d["title"], "")
        self.assertEqual(d["description"], "")
        self.assertNotIn("ebay_listing_id", d)

    def test_two_auto_drafts_are_two_drafts(self):
        a = self.client.post("/api/auto-draft").get_json()["draft"]["id"]
        b = self.client.post("/api/auto-draft").get_json()["draft"]["id"]
        self.assertNotEqual(a, b)

    def test_append_to_a_blank_draft_records_the_photo(self):
        # v50 regression: _append_uploads used "draft.get('photos') or
        # []", which hands back a throwaway list when the stored one
        # is empty — the file landed on disk while the draft stayed
        # empty. The auto draft starts blank, so this fired on the
        # very first photo. The photo must be recorded, in the
        # draft's own folder.
        d = self.client.post("/api/auto-draft").get_json()["draft"]
        r = self._post_photos(d["id"], [(_jpeg_bytes("blue"), "a.jpg")])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["added"], 1)
        self.assertEqual(m._load_draft(d["id"])["photos"],
                         [d["id"] + "/1.jpg"])
        self.assertTrue(os.path.isfile(
            os.path.join(m.PHOTOS_DIR, d["id"], "1.jpg")))

    def test_auto_draft_visible_in_list_as_untitled_newest_first(self):
        first = self.client.post("/api/auto-draft").get_json()["draft"]
        second = self.client.post("/api/auto-draft").get_json()["draft"]
        self._post_photos(second["id"], [(_jpeg_bytes("blue"), "a.jpg")])
        r = self.client.get("/api/drafts")
        rows = {d["id"]: d for d in r.get_json()["drafts"]}
        self.assertEqual(rows[second["id"]]["title"], "(untitled)")
        self.assertEqual(rows[second["id"]]["thumb"],
                         second["id"] + "/1.jpg")
        ids = [d["id"] for d in r.get_json()["drafts"]]
        self.assertEqual(ids[0], second["id"])
        self.assertEqual(ids[1], first["id"])

    def test_blank_title_lists_as_untitled(self):
        self._write_draft("d1", title="")
        rows = self.client.get("/api/drafts").get_json()["drafts"]
        self.assertEqual(rows[0]["title"], "(untitled)")

    def test_reanalyze_reuses_the_auto_draft_without_duplicating_photos(self):
        d = self.client.post("/api/auto-draft").get_json()["draft"]
        self._post_photos(d["id"], [(_jpeg_bytes("red"), "a.jpg"),
                                    (_jpeg_bytes("blue"), "b.jpg")])
        identified = {"title": "Test item", "description": "A thing.",
                      "category": "Dolls", "attributes": {},
                      "identification_notes": "AI saw two photos."}
        with mock.patch.object(m, "_gemini_identify",
                               return_value=identified):
            r = self.client.post(f"/api/drafts/{d['id']}/reanalyze")
        self.assertEqual(r.status_code, 200)
        out = r.get_json()["draft"]
        self.assertEqual(out["id"], d["id"])
        self.assertEqual(out["title"], "Test item")
        # Same two photos, in the same order — no duplicates, no new draft.
        self.assertEqual(len(out["photos"]), 2)
        self.assertEqual([d2["id"] for d2 in
                          self.client.get("/api/drafts").get_json()["drafts"]],
                         [d["id"]])

    def test_auto_draft_append_enforces_the_24_cap(self):
        d = self.client.post("/api/auto-draft").get_json()["draft"]
        photos = [f"{d['id']}/{i}.jpg" for i in range(1, m.MAX_PHOTOS + 1)]
        for rel in photos:
            full = os.path.join(m.PHOTOS_DIR, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as f:
                f.write(_jpeg_bytes().getvalue())
        m._save_draft({**d, "photos": photos})
        r = self._post_photos(d["id"], [(_jpeg_bytes(), "extra.jpg")])
        self.assertEqual(r.status_code, 400)
        self.assertIn("already has 24 photos", r.get_json()["error"])

    # ---- resume ----

    def test_resume_set_get_clear_round_trip(self):
        self._write_draft("d1", photos=["d1/1.jpg"])
        self._set_resume("d1")
        r = self.client.get("/api/resume-draft")
        self.assertEqual(r.get_json()["draft_id"], "d1")
        self.assertEqual(r.get_json()["title"], "Dummy d1")
        self.assertEqual(r.get_json()["photo_count"], 1)
        self._set_resume(None)
        self.assertIsNone(self.client.get("/api/resume-draft")
                          .get_json()["draft_id"])

    def test_resume_get_without_any_record_is_null(self):
        self.assertIsNone(self.client.get("/api/resume-draft")
                          .get_json()["draft_id"])

    def test_resume_forgets_a_listed_draft(self):
        self._write_draft("d1", ebay_listing_id="123",
                          ebay_listing_url="https://www.ebay.com/itm/123")
        self._set_resume("d1")
        r = self.client.get("/api/resume-draft")
        self.assertIsNone(r.get_json()["draft_id"])
        # The stale record is cleared, so a second read stays null.
        self.assertIsNone(m._load_resume_draft_id())

    def test_resume_forgets_a_deleted_draft(self):
        self._write_draft("d1")
        self._set_resume("d1")
        os.remove(os.path.join(m.DRAFTS_DIR, "d1.json"))
        r = self.client.get("/api/resume-draft")
        self.assertIsNone(r.get_json()["draft_id"])
        self.assertIsNone(m._load_resume_draft_id())

    def test_deleting_the_resume_draft_clears_the_resume(self):
        self._write_draft("d1")
        self._write_draft("d2")
        self._set_resume("d1")
        r = self.client.delete("/api/drafts/d1")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(m._load_resume_draft_id())

    def test_deleting_another_draft_keeps_the_resume(self):
        self._write_draft("d1")
        self._write_draft("d2")
        self._set_resume("d1")
        self.client.delete("/api/drafts/d2")
        self.assertEqual(m._load_resume_draft_id(), "d1")

    def test_corrupt_resume_file_means_no_resume_not_a_500(self):
        with open(m.RESUME_FILE, "w", encoding="utf-8") as f:
            f.write("{not json")
        r = self.client.get("/api/resume-draft")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.get_json()["draft_id"])

    def test_clear_resume_if_only_clears_its_own_draft(self):
        self._set_resume("d1")
        m._clear_resume_if("d2")
        self.assertEqual(m._load_resume_draft_id(), "d1")
        m._clear_resume_if("d1")
        self.assertIsNone(m._load_resume_draft_id())


if __name__ == "__main__":
    unittest.main()
