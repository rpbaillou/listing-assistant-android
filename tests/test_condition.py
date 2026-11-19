"""apk-v6 tests: the AI must never assert an item's condition, and the
condition dropdown values must stay valid eBay condition enums.

These tests read app.py as source (via ast) instead of importing it, so they
run anywhere without Flask/requests/Pillow installed.
"""
import ast
import os
import unittest

APP_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "app", "src", "main", "python", "app.py")


def _get_constant(name):
    with open(APP_PY, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(name + " not found in app.py")


class IdentifyPromptGuardrailTest(unittest.TestCase):
    def test_condition_guardrail_present(self):
        prompt = _get_constant("IDENTIFY_PROMPT")
        for phrase in (
            "CONDITION RULE",
            "Do NOT describe, assess, or speculate",
            "The seller states the condition separately",
            "Leave condition out of the description entirely",
        ):
            self.assertIn(phrase, prompt,
                          "guardrail phrase missing from IDENTIFY_PROMPT: " + phrase)

    def test_old_condition_invite_removed(self):
        prompt = _get_constant("IDENTIFY_PROMPT").lower()
        # The old wording invited condition claims ("condition details you can
        # actually see") — that is what produced "no chips" on the figurine.
        self.assertNotIn("condition details you can actually see", prompt)

    def test_forbidden_claim_examples_listed(self):
        prompt = _get_constant("IDENTIFY_PROMPT").lower()
        for claim in ('"no chips"', '"no cracks"', '"good condition"', '"mint"'):
            self.assertIn(claim, prompt,
                          "example forbidden claim missing from guardrail: " + claim)


class ConditionEnumsTest(unittest.TestCase):
    def test_conditions_are_valid_ebay_enums(self):
        conds = _get_constant("CONDITIONS")
        values = [v for v, _ in conds]
        # Core values the publish flow depends on (eBay Sell Inventory API
        # ConditionEnum). If eBay ever renames one, this fails loudly.
        for v in ("NEW", "USED_EXCELLENT", "USED_VERY_GOOD", "USED_GOOD",
                  "USED_ACCEPTABLE", "FOR_PARTS_OR_NOT_WORKING"):
            self.assertIn(v, values, "eBay condition enum missing: " + v)

    def test_condition_labels_are_plain_words(self):
        conds = _get_constant("CONDITIONS")
        for _, label in conds:
            self.assertFalse(label.isupper(),
                             "condition label should not be ALL CAPS: " + label)

    def test_conditions_cover_the_full_enum_table(self):
        # v62: every enum the official table knows must be offerable,
        # in official (numeric ID) order. NEW_OTHER was missing and
        # the check rejected a pick the category fetch had offered.
        conds = _get_constant("CONDITIONS")
        idmap = _get_constant("CONDITION_ID_TO_ENUM")
        self.assertEqual([v for v, _ in conds],
                         [idmap[i] for i in sorted(idmap)])

    def test_condition_labels_match_the_display_table(self):
        conds = _get_constant("CONDITIONS")
        display = _get_constant("CONDITION_DISPLAY")
        self.assertEqual([v for v, _ in conds], list(display))
        for v, label in conds:
            self.assertEqual(label, display[v],
                             "label drift for " + v)

    def test_no_plain_used_enum(self):
        # There is NO bare USED enum: USED_EXCELLENT (3000) IS plain
        # "Used". A bare USED would fail at publish (25021).
        values = [v for v, _ in _get_constant("CONDITIONS")]
        self.assertNotIn("USED", values)
        self.assertIn("USED_EXCELLENT", values)

    def test_template_dropdown_matches_conditions(self):
        # v62: the static dropdown, the check list, and the display
        # table must stay one list, or the mismatch comes back.
        import re
        html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "app", "src", "main", "python",
                                 "templates", "index.html")
        with open(html_path, encoding="utf-8") as f:
            html = f.read()
        m = re.search(r'<select id="f-condition">(.*?)</select>', html, re.S)
        self.assertIsNotNone(m, "f-condition select missing from template")
        found = re.findall(r'<option value="([^"]*)">', m.group(1))
        values = [v for v, _ in _get_constant("CONDITIONS")]
        self.assertEqual(found, [""] + values)


if __name__ == "__main__":
    unittest.main()
