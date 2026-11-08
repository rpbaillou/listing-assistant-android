"""apk-v47 tests: policy-create error 20403 names its field, and the
policy name cap matches eBay's 64.

Live failure on 1.45 (Nov 8): creating a shipping policy answered
eBay error 20403 with the message "Invalid ." — the field name came
back empty, and the app dropped eBay's `parameters` array (where the
field name lives), so the pink error box named nothing. The long
policy name is the leading suspect but NOT proven: eBay's official
FulfillmentPolicyRequest caps the name at 64 characters, the app
allowed 100 and the name input had no maxlength at all.

A missing categoryTypes is ruled out as the cause: eBay answers a
missing required field with 20401 "Missing field", not 20403. A
retired FedExSmartPost code is weakened, not proven: isdntek's eBay
shipping-code table still lists FedExSmartPost, and the app's codes
come from live GETs of his own policies or the documented set. No
service code changes in this build.

eBay is always mocked; no test here reaches the network.
"""

import os
import sys
import unittest
from unittest import mock

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PY_DIR = os.path.join(REPO, "app", "src", "main", "python")
sys.path.insert(0, PY_DIR)

import app as m  # noqa: E402  (imports cleanly; __main__ guard prevents serving)


def tearDownModule():
    sys.modules.pop("app", None)


def _fake_resp(status, content=b"", json_body=None, headers=None):
    r = mock.Mock()
    r.status_code = status
    r.content = content
    r.text = content.decode("utf-8", "replace") if isinstance(
        content, (bytes, bytearray)) else content
    r.headers = headers or {}
    if json_body is not None:
        r.json = mock.Mock(return_value=json_body)
    else:
        r.json = mock.Mock(side_effect=ValueError("no json"))
    return r


def _flat_form(**over):
    form = {"name": "Dummy Doll Policy", "description": "",
            "pickup_only": False, "local_pickup": False,
            "ship_international": False, "handling_days": "2",
            "dom_cost_type": "FLAT_RATE",
            "dom_service_1": "USPSPriority", "dom_service_1_other": "",
            "dom_service_2": "", "dom_service_2_other": "",
            "dom_cost_1": "12.50", "dom_cost_2": "",
            "dom_free_shipping": False}
    form.update(over)
    return form


class NameCapTest(unittest.TestCase):
    def test_65_char_name_rejected_with_64_message(self):
        with self.assertRaises(m._PublishValidationError) as ctx:
            m._build_fulfillment_policy_body(_flat_form(name="x" * 65))
        self.assertIn("64", str(ctx.exception))

    def test_64_char_name_accepted(self):
        body = m._build_fulfillment_policy_body(_flat_form(name="x" * 64))
        self.assertEqual(body["name"], "x" * 64)


class ParametersSurfacedTest(unittest.TestCase):
    def _resp(self, error):
        return _fake_resp(400, json_body={"errors": [error]})

    def test_20403_parameters_name_the_field(self):
        # The live Nov 8 shape: message "Invalid .", errorId 20403,
        # field name carried in parameters.
        resp = self._resp({"message": "Invalid .", "errorId": "20403",
                           "parameters": [{"name": "name",
                                           "value": "USPS Gr Adv/FedEx Barbie 1"}]})
        out = m._plain_ebay_error(resp)
        self.assertIn("(error 20403)", out)
        self.assertIn("Invalid .", out)
        self.assertIn("name: USPS Gr Adv/FedEx Barbie 1", out)

    def test_no_parameters_means_no_detail_suffix(self):
        resp = self._resp({"message": "Invalid .", "errorId": "20403"})
        out = m._plain_ebay_error(resp)
        self.assertEqual(out, "eBay said (error 20403): Invalid .")

    def test_non_list_parameters_ignored(self):
        resp = self._resp({"message": "Invalid .", "errorId": "20403",
                           "parameters": "junk"})
        out = m._plain_ebay_error(resp)
        self.assertEqual(out, "eBay said (error 20403): Invalid .")

    def test_parameter_without_value_renders_bare_name(self):
        resp = self._resp({"message": "Invalid .", "errorId": "20403",
                           "parameters": [{"name": "handlingTime"}]})
        out = m._plain_ebay_error(resp)
        self.assertIn("handlingTime", out)

    def test_rewrite_branch_keeps_parameters(self):
        # A 20403 whose message happens to contain a keyword must
        # still surface the parameters, not drop them in the rewrite.
        resp = self._resp({"message": "Invalid category", "errorId": "25709",
                           "parameters": [{"name": "categoryId",
                                           "value": "123"}]})
        out = m._plain_ebay_error(resp)
        self.assertIn("Tap Suggest", out)
        self.assertIn("categoryId: 123", out)

    def test_parameters_never_drive_keyword_matching(self):
        # A parameter VALUE mentioning a keyword must not rewrite the
        # message: matching reads message + errorId only.
        resp = self._resp({"message": "Invalid .", "errorId": "20403",
                           "parameters": [{"name": "note",
                                           "value": "category 123"}]})
        out = m._plain_ebay_error(resp)
        self.assertNotIn("Tap Suggest", out)
        self.assertIn("note: category 123", out)


if __name__ == "__main__":
    unittest.main()
