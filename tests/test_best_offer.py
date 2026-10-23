"""apk-v9 tests: Best Offer option on the publish panel.

Covers: (1) bestOfferTerms is built inside listingPolicies (verified against
eBay's Sell Inventory docs via the Microsoft eBay connector reference —
developer.ebay.com was unreachable from here), (2) validation rules
(auto-decline < auto-accept < listing price, strictly), (3) toggle off sends
nothing (today's behavior, unchanged).
"""
import os
import sys
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PY_DIR = os.path.join(REPO, "app", "src", "main", "python")
sys.path.insert(0, PY_DIR)

import app as m  # noqa: E402


def tearDownModule():
    sys.modules.pop("app", None)


class ParseMoneyTest(unittest.TestCase):
    def test_blank_is_none(self):
        self.assertIsNone(m._parse_money(""))
        self.assertIsNone(m._parse_money("   "))
        self.assertIsNone(m._parse_money(None))

    def test_valid_amounts(self):
        self.assertEqual(m._parse_money("25"), 25.0)
        self.assertEqual(m._parse_money("25.00"), 25.0)
        self.assertEqual(m._parse_money("$25"), 25.0)
        self.assertEqual(m._parse_money("1,299.99"), 1299.99)

    def test_invalid_is_none(self):
        self.assertIsNone(m._parse_money("abc"))
        self.assertIsNone(m._parse_money("0"))
        self.assertIsNone(m._parse_money("-5"))


class BestOfferTermsTest(unittest.TestCase):
    def test_off_sends_nothing(self):
        terms, err = m._best_offer_terms(False, "20", "40", 50.0)
        self.assertIsNone(terms)
        self.assertIsNone(err)

    def test_on_no_auto_prices(self):
        terms, err = m._best_offer_terms(True, "", "", 50.0)
        self.assertIsNone(err)
        self.assertEqual(terms, {"bestOfferEnabled": True})

    def test_full_terms_shape(self):
        terms, err = m._best_offer_terms(True, "20", "40", 50.0)
        self.assertIsNone(err)
        # Field names per eBay's Sell Inventory docs: bestOfferTerms lives
        # INSIDE listingPolicies (not top-level), with bestOfferEnabled /
        # autoDeclinePrice / autoAcceptPrice as Amount objects.
        self.assertEqual(terms, {
            "bestOfferEnabled": True,
            "autoDeclinePrice": {"currency": "USD", "value": "20.00"},
            "autoAcceptPrice": {"currency": "USD", "value": "40.00"},
        })

    def test_only_decline(self):
        terms, err = m._best_offer_terms(True, "20", "", 50.0)
        self.assertIsNone(err)
        self.assertNotIn("autoAcceptPrice", terms)
        self.assertEqual(terms["autoDeclinePrice"]["value"], "20.00")

    def test_decline_must_be_below_price(self):
        _, err = m._best_offer_terms(True, "60", "", 50.0)
        self.assertIn("below your listing price", err)
        _, err = m._best_offer_terms(True, "50", "", 50.0)
        self.assertIn("below your listing price", err)

    def test_accept_must_be_below_price(self):
        _, err = m._best_offer_terms(True, "", "50", 50.0)
        self.assertIn("below your listing price", err)

    def test_accept_strictly_above_decline(self):
        # eBay rejects accept <= decline ("can't be lower than or equal").
        _, err = m._best_offer_terms(True, "30", "30", 50.0)
        self.assertIn("higher than the auto-decline price", err)
        _, err = m._best_offer_terms(True, "40", "30", 50.0)
        self.assertIn("higher than the auto-decline price", err)

    def test_invalid_amounts_rejected(self):
        _, err = m._best_offer_terms(True, "abc", "", 50.0)
        self.assertIn("auto-decline", err)
        _, err = m._best_offer_terms(True, "", "xyz", 50.0)
        self.assertIn("auto-accept", err)

    def test_blank_strings_are_not_amounts(self):
        # Whitespace-only is treated as blank, not as an invalid amount.
        terms, err = m._best_offer_terms(True, "  ", "", 50.0)
        self.assertIsNone(err)
        self.assertEqual(terms, {"bestOfferEnabled": True})


if __name__ == "__main__":
    unittest.main()
