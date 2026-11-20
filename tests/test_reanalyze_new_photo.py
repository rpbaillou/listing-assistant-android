"""apk-v67 tests: reanalyze sees a photo added to an existing draft.

He reported (Nov 20) that a photo taken after the draft was made was
never taken into consideration for the listing — a photo of an item's
contents added to an existing draft left the title and description
stale. The reanalyze endpoint already runs _gemini_identify over ALL
the draft's photos, so the server side of the fix is that a photo
appended through the draft photos endpoint is in the list reanalyze
reads. These tests pin that: append a photo, reanalyze, and the
identify call receives every photo including the new one.

Also pinned here is the exact field contract the v67 confirm wording
is written from: reanalyze replaces exactly the five fields
_gemini_identify returns (title, description, category, attributes,
identification_notes) and keeps everything else — the typed price,
the condition and condition details, the quantity, and the best
offer settings. The client confirm names the replaced fields and
promises the price is kept; if this contract ever changes, the
confirm wording has to change with it.

PRIVACY: the repo is PUBLIC. No real policy ID, address, or secret
appears in code, fixtures, or test output.

Storage dirs are monkeypatched to a temp dir per test, so nothing
here touches the repo's real data dir. eBay and Gemini are always
mocked; no test here reaches the network.
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


class ReanalyzeNewPhotoTestCase(unittest.TestCase):
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

    def _post_photos(self, draft_id, files):
        data = {"photos": [(b, name) for b, name in files]}
        return self.client.post(f"/api/drafts/{draft_id}/photos",
                                data=data,
                                content_type="multipart/form-data")

    def test_reanalyze_sees_a_photo_added_after_the_first_analysis(self):
        # The reported bug: a photo added to an existing draft was
        # never considered by the listing. Reanalyze must read every
        # photo on the draft, including one appended after the first
        # analysis, in order.
        d = self.client.post("/api/auto-draft").get_json()["draft"]
        r = self._post_photos(d["id"], [(_jpeg_bytes("red"), "a.jpg")])
        self.assertEqual(r.status_code, 200)
        seen = []

        def fake_identify(photo_relpaths):
            seen.append(list(photo_relpaths))
            return {"title": "First pass", "description": "One photo.",
                    "category": "Dolls", "attributes": {},
                    "identification_notes": "Saw one photo."}

        with mock.patch.object(m, "_gemini_identify",
                               side_effect=fake_identify):
            r = self.client.post(f"/api/drafts/{d['id']}/reanalyze")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(seen, [[d["id"] + "/1.jpg"]])
        # Now the photo he takes after the draft exists.
        r = self._post_photos(d["id"], [(_jpeg_bytes("blue"), "b.jpg")])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["added"], 1)

        def fake_identify_two(photo_relpaths):
            seen.append(list(photo_relpaths))
            return {"title": "Second pass", "description": "Two photos.",
                    "category": "Dolls", "attributes": {},
                    "identification_notes": "Saw both photos."}

        with mock.patch.object(m, "_gemini_identify",
                               side_effect=fake_identify_two):
            r = self.client.post(f"/api/drafts/{d['id']}/reanalyze")
        self.assertEqual(r.status_code, 200)
        # Both photos reach Gemini, the new one included, in order.
        self.assertEqual(seen[1], [d["id"] + "/1.jpg", d["id"] + "/2.jpg"])
        out = r.get_json()["draft"]
        self.assertEqual(out["title"], "Second pass")
        self.assertEqual(len(out["photos"]), 2)

    def test_reanalyze_replaces_only_the_five_ai_fields(self):
        # The v67 confirm promises exactly this: the title,
        # description, category, details (attributes), and AI notes
        # are replaced; the typed price is kept. The condition,
        # condition details, quantity, and best offer settings are
        # kept too — reanalyze never touches them.
        d = self.client.post("/api/auto-draft").get_json()["draft"]
        self._post_photos(d["id"], [(_jpeg_bytes("red"), "a.jpg")])
        typed = {"title": "His typed title",
                 "description": "His typed description.",
                 "category": "His category",
                 "attributes": {"Brand": "His brand"},
                 "identification_notes": "His old notes.",
                 "price": "12.34",
                 "condition": "USED_EXCELLENT",
                 "condition_details": "Light wear on the box.",
                 "quantity": 3,
                 "best_offer": True,
                 "best_offer_auto_decline": "5.00",
                 "best_offer_auto_accept": "11.00"}
        stored = m._load_draft(d["id"])
        stored.update(typed)
        m._save_draft(stored)
        identified = {"title": "AI title", "description": "AI description.",
                      "category": "AI category",
                      "attributes": {"Brand": "AI brand"},
                      "identification_notes": "AI saw the new photo."}
        with mock.patch.object(m, "_gemini_identify",
                               return_value=identified):
            r = self.client.post(f"/api/drafts/{d['id']}/reanalyze")
        self.assertEqual(r.status_code, 200)
        out = r.get_json()["draft"]
        for key in ("title", "description", "category", "attributes",
                    "identification_notes"):
            self.assertEqual(out[key], identified[key],
                             key + " was not replaced")
        for key in ("price", "condition", "condition_details",
                    "quantity", "best_offer", "best_offer_auto_decline",
                    "best_offer_auto_accept"):
            self.assertEqual(out[key], typed[key], key + " was not kept")

    def test_reanalyze_with_no_photos_is_a_404(self):
        d = self.client.post("/api/auto-draft").get_json()["draft"]
        r = self.client.post(f"/api/drafts/{d['id']}/reanalyze")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
