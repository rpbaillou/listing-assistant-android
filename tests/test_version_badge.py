"""apk-v6: the header version badge must show the real app version.

Regression test for the "apk-v1" incident (Oct 23, 2026): apprunner.py
hardcoded APP_VERSION="apk-v1", so his phone kept showing v1 even with v5
installed, and he thought the update didn't take. The version now flows
MainActivity (Android versionName) -> apprunner.start(version) -> APP_VERSION
env -> template badge, with no hardcoded per-release string anywhere.
"""
import ast
import os
import sys
import tempfile
import types
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PY_DIR = os.path.join(REPO, "app", "src", "main", "python")
APPRUNNER = os.path.join(PY_DIR, "apprunner.py")


class VersionBadgeTest(unittest.TestCase):
    def test_no_hardcoded_version_in_apprunner(self):
        with open(APPRUNNER, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("apk-v1", src)
        self.assertNotIn("apk-v5", src)
        self.assertNotIn("apk-v6", src)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "APP_VERSION":
                        self.fail("APP_VERSION must never be assigned a literal "
                                  "in apprunner.py (it comes from MainActivity)")

    def test_start_sets_version_from_argument(self):
        # Fake the `app` module so start() doesn't launch Flask.
        saved = {k: sys.modules.get(k) for k in ("app", "apprunner")}
        fake = types.ModuleType("app")
        fake.app = types.SimpleNamespace(run=lambda *a, **k: None)
        sys.modules["app"] = fake
        sys.path.insert(0, PY_DIR)
        try:
            import apprunner
            with tempfile.TemporaryDirectory() as tmp:
                os.environ.pop("APP_VERSION", None)
                apprunner.start(tmp, "1.5")
                self.assertEqual(os.environ.get("APP_VERSION"), "v1.5")
                # already-prefixed values pass through untouched
                apprunner.start(tmp, "v2.0")
                self.assertEqual(os.environ.get("APP_VERSION"), "v2.0")
                # empty version leaves whatever was there (never a stale literal)
                apprunner.start(tmp, "")
                self.assertEqual(os.environ.get("APP_VERSION"), "v2.0")
        finally:
            sys.path.remove(PY_DIR)
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

    def test_badge_renders_current_version(self):
        # End-to-end: APP_VERSION env -> Flask index -> badge HTML.
        os.environ["LA_DATA_DIR"] = tempfile.mkdtemp()
        os.environ["APP_VERSION"] = "v1.5"
        sys.path.insert(0, PY_DIR)
        try:
            import app as flask_app
            client = flask_app.app.test_client()
            html = client.get("/").get_data(as_text=True)
        finally:
            sys.path.remove(PY_DIR)
        self.assertIn('<span class="vtag">v1.5</span>', html)


if __name__ == "__main__":
    unittest.main()
