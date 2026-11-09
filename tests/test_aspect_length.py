"""apk-v51 tests: eBay caps every item-specific (aspect) value at
65 characters.

Live failure, Nov 8 2026 (v1.49): tapping List failed with eBay
error 25002 — the AI had written an 82-character "Set Includes"
value ("Doll, Gown, Stole, Gloves, Jewelry, Shoes, Doll Stand,
Certificate of Authenticity"), and _clean_aspects only caps values
at 500, so it sailed through to the inventory item PUT. The fix:
_listing_readiness reports every over-long value at once in plain
words (field name, current length, the 65 limit) before any eBay
call, the Gemini prompt tells the AI the 65 limit up front, and
the listing page shows a live n/65 counter on each value field.
Values are NEVER silently truncated — truncation would mangle his
words; the message plus his edit is the fix.

No test here reaches the network: eBay is mocked or never called.
"""
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


LONG_VALUE = ("Doll, Gown, Stole, Gloves, Jewelry, Shoes, Doll Stand, "
              "Certificate of Authenticity")


class AspectLengthTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig = {}
        for attr, sub in (("DRAFTS_DIR", "drafts"), ("PHOTOS_DIR", "photos")):
            self.orig[attr] = getattr(m, attr)
            p = os.path.join(self.tmp, sub)
            os.makedirs(p, exist_ok=True)
            setattr(m, attr, p)
        self.draft = {"id": "d1", "photos": ["p.jpg"]}
        self.fields = {
            "title": "Test item",
            "description": "A test item.",
            "price_val": 10.0,
            "condition": "NEW",
            "condition_details": "",
            "category_id": "123",
            "fulfillment_id": "111",
            "payment_id": "222",
            "return_id": "333",
            "attributes": {},
            "best_offer_terms": None,
        }

    def tearDown(self):
        for attr, val in self.orig.items():
            setattr(m, attr, val)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _readiness(self, attributes):
        f = dict(self.fields)
        f["attributes"] = attributes
        with mock.patch.object(m, "_check_fulfillment_policy",
                               return_value=None), \
             mock.patch.object(m, "_category_conditions",
                               return_value=(["NEW"], False)), \
             mock.patch.object(m, "_category_aspects",
                               return_value=(None, None)):
            return m._listing_readiness(dict(self.draft), f, "tok")

    def test_flags_82_char_value_naming_the_field(self):
        """The live failure: 82 chars on Set Includes, named + counted."""
        self.assertEqual(len(LONG_VALUE), 82)
        problems, _notes = self._readiness({"Set Includes": LONG_VALUE})
        self.assertEqual(len(problems), 1)
        self.assertIn("Set Includes", problems[0])
        self.assertIn("82", problems[0])
        self.assertIn("65", problems[0])

    def test_several_overlong_reported_at_once(self):
        """v35 pattern: all problems in one message, none hidden."""
        problems, _notes = self._readiness({
            "Set Includes": LONG_VALUE,
            "Material": "x" * 70,
            "Brand": "Short",
        })
        self.assertEqual(len(problems), 1)
        self.assertIn("Set Includes", problems[0])
        self.assertIn("82", problems[0])
        self.assertIn("Material", problems[0])
        self.assertIn("70", problems[0])
        self.assertNotIn("Brand", problems[0])

    def test_exactly_65_passes(self):
        problems, _notes = self._readiness({"Brand": "x" * 65})
        self.assertEqual(problems, [])

    def test_66_fails(self):
        problems, _notes = self._readiness({"Brand": "x" * 66})
        self.assertEqual(len(problems), 1)
        self.assertIn("Brand", problems[0])
        self.assertIn("66", problems[0])

    def test_prompt_carries_the_65_limit(self):
        """The AI is told the limit up front, so it stops writing
        over-long values in the first place."""
        self.assertIn("65 characters", m.IDENTIFY_PROMPT)

    def test_publish_blocked_before_any_ebay_call(self):
        """The readiness failure fires before photos upload or any
        eBay state change — a retry after shortening the value is
        clean, with no stale offer to discard."""
        f = dict(self.fields)
        f["attributes"] = {"Set Includes": LONG_VALUE}
        with mock.patch.object(m, "_ebay_access_token",
                               return_value="tok"), \
             mock.patch.object(m, "_ebay_call",
                               side_effect=AssertionError(
                                   "must not reach eBay")), \
             mock.patch.object(m, "_ebay_upload_picture",
                               side_effect=AssertionError(
                                   "must not upload photos")), \
             mock.patch.object(m, "_check_fulfillment_policy",
                               return_value=None), \
             mock.patch.object(m, "_category_conditions",
                               return_value=(["NEW"], False)), \
             mock.patch.object(m, "_category_aspects",
                               return_value=(None, None)):
            with self.assertRaises(m._PublishValidationError) as caught:
                m._do_publish(dict(self.draft), f)
        self.assertIn("Set Includes", str(caught.exception))
        self.assertIn("82", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
