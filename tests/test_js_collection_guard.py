"""apk-v43 tests: static guard against the HTMLCollection bug.

v42 shipped `(sel.children || []).some(...)` in app.js. On a real
<select>, .children is an HTMLCollection — indexed items + length,
no Array methods — so .some throws and the whole shipping flow dies.
The node mocks gave elements array children (which DO have .some),
so every suite passed while the real phone failed. This guard fails
the suite if app.js ever calls an Array method on .children or
.options again (same pattern as the v38 KDoc guard test in
tests/test_updater_cache_buster.py). The real-DOM regression suite
(tests/real-dom-shipping.test.js) proves the flows work on a
browser-faithful DOM.
"""

import os
import re
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
APP_JS = os.path.join(REPO, "app", "src", "main", "python", "static",
                      "app.js")

ARRAY_METHODS = ("some", "map", "find", "filter", "forEach", "includes")
PATTERN = re.compile(
    r"\.(?:children|options)\s*\.\s*(?:" + "|".join(ARRAY_METHODS) + r")\b")


class CollectionGuardTest(unittest.TestCase):
    def test_no_array_method_on_children_or_options(self):
        with open(APP_JS, encoding="utf-8") as f:
            src = f.read()
        bad = PATTERN.findall(src)
        self.assertEqual(
            bad, [],
            "app.js calls an Array method on .children/.options: %r — "
            "wrap in Array.from(...) first, an HTMLCollection has no "
            "Array methods." % (bad,))

    def test_array_from_wrap_is_present_at_the_fixed_sites(self):
        # The two v42 throw sites must keep the Array.from wrap.
        with open(APP_JS, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("Array.from(sel.children || []).some(", src)
        self.assertIn("Array.from(dom1.children || []).some(", src)
