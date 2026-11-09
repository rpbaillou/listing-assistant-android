"""apk-v45 tests: the drafts list is newest first.

v45: /api/drafts used to iterate sorted(os.listdir(DRAFTS_DIR),
reverse=True), but draft files are named <random-uuid>.json, so that
sort was effectively random and his drafts did not show newest first.
The list now sorts by the date shown on the row ("updated", falling
back to "created"); a draft missing both dates sorts last. These tests
pin that, with filenames deliberately ordered against the dates so a
filename sort would fail them. The JS renders drafts in server order,
so no JS change is needed for this fix.

Storage dirs are monkeypatched to a temp dir per test, so nothing here
touches the repo's real data dir. No test here reaches the network.
"""
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


class DraftSortTestCase(unittest.TestCase):
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

    def _write_draft(self, draft_id, **fields):
        d = {"id": draft_id, "title": "Draft " + draft_id,
             "photos": [], "price": ""}
        d.update(fields)
        with open(os.path.join(m.DRAFTS_DIR, draft_id + ".json"),
                  "w", encoding="utf-8") as f:
            json.dump(d, f)
        return d

    def _list_ids(self):
        resp = self.client.get("/api/drafts")
        self.assertEqual(resp.status_code, 200)
        return [d["id"] for d in resp.get_json()["drafts"]]

    # ---- newest first by "updated" ----

    def test_list_newest_first_by_updated_not_filename(self):
        # Filenames sort z > a, but the dates run the other way: if the
        # endpoint still sorted by filename, "zzzz" would come first.
        self._write_draft("zzzzold", updated="2026-11-01T10:00:00",
                          created="2026-11-01T10:00:00")
        self._write_draft("aaaamid", updated="2026-11-05T10:00:00",
                          created="2026-11-05T10:00:00")
        self._write_draft("mmmnew", updated="2026-11-08T10:00:00",
                          created="2026-11-08T10:00:00")
        self.assertEqual(self._list_ids(), ["mmmnew", "aaaamid", "zzzzold"])

    def test_updated_beats_created(self):
        # A draft created recently but last touched long ago is old;
        # "updated" is the sort key, not "created".
        self._write_draft("touchedlongago", updated="2026-11-02T10:00:00",
                          created="2026-11-08T10:00:00")
        self._write_draft("touchedrecently", updated="2026-11-03T10:00:00",
                          created="2026-11-01T10:00:00")
        self.assertEqual(self._list_ids(),
                         ["touchedrecently", "touchedlongago"])

    # ---- fallbacks ----

    def test_missing_updated_falls_back_to_created(self):
        self._write_draft("nodate-new", created="2026-11-07T10:00:00")
        self._write_draft("dated-old", updated="2026-11-06T10:00:00",
                          created="2026-11-06T10:00:00")
        self._write_draft("dated-new", updated="2026-11-08T10:00:00",
                          created="2026-11-08T10:00:00")
        self.assertEqual(self._list_ids(),
                         ["dated-new", "nodate-new", "dated-old"])

    def test_missing_both_dates_sorts_last(self):
        self._write_draft("zzznodates")
        self._write_draft("aaadated", updated="2026-11-08T10:00:00",
                          created="2026-11-08T10:00:00")
        self._write_draft("mmmnodates", created="2026-11-01T10:00:00")
        # "mmmnodates" has a created date, so it sits between the dated
        # draft and the draft with no date at all.
        self.assertEqual(self._list_ids(),
                         ["aaadated", "mmmnodates", "zzznodates"])

    def test_list_response_shape_unchanged(self):
        self._write_draft("d1", updated="2026-11-08T10:00:00",
                          created="2026-11-08T10:00:00",
                          photos=["d1/1.jpg"], ebay_listing_id="999")
        resp = self.client.get("/api/drafts")
        d = resp.get_json()["drafts"][0]
        self.assertEqual(d["id"], "d1")
        self.assertEqual(d["title"], "Draft d1")
        self.assertEqual(d["updated"], "2026-11-08T10:00:00")
        self.assertEqual(d["listed"], True)
        self.assertEqual(d["thumb"], "d1/1.jpg")
        self.assertNotIn("_sort", d)
        self.assertNotIn("_mtime", d)

    def test_same_updated_stamp_orders_by_write_time(self):
        # v51: "updated" is stamped to the second, so two drafts
        # touched in the same second tie — the file written later
        # is the newer one. Before the tie-break, a stable sort over
        # random filenames made the order a coin flip, and the v50
        # newest-first test failed 4 times in 5 runs.
        self._write_draft("aaa-first", updated="2026-11-08T10:00:00",
                          created="2026-11-08T10:00:00")
        self._write_draft("zzz-second", updated="2026-11-08T10:00:00",
                          created="2026-11-08T10:00:00")
        self.assertEqual(self._list_ids(), ["zzz-second", "aaa-first"])


if __name__ == "__main__":
    unittest.main()
