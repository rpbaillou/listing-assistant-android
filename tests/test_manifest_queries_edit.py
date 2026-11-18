"""apk-v56 tests: the manifest no longer declares ACTION_EDIT.

v39 declared android.intent.action.EDIT in <queries> so the Pixel
editor handoff could resolve on Android 11+, and v44 added a Google
Photos package query so Edit could pin to Photos. v56 deletes the
handoff entirely — photo editing is in-app web code now, so no outside
editor is ever resolved, and the declarations go with it. The camera
declaration stays: the in-app camera still falls back to a camera app.

Kotlin cannot execute in this sandbox (no JVM/kotlinc, as with
v15-v55), so these are source-contract regression tests over the real
AndroidManifest.xml and MainActivity.kt. CI Gradle compilation is the
Kotlin execution gate; the live Edit test on his Pixel is the proof.
Dummy data only; eBay is never touched.
"""

import os
import unittest
import xml.etree.ElementTree as ET

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
MANIFEST = os.path.join(REPO, "app", "src", "main", "AndroidManifest.xml")
MAIN = os.path.join(
    REPO, "app", "src", "main", "java",
    "com", "rpbdesign", "listingassistant", "MainActivity.kt")

with open(MANIFEST, encoding="utf-8") as f:
    MANIFEST_SRC = f.read()
with open(MAIN, encoding="utf-8") as f:
    MAIN_SRC = f.read()

NS = "{http://schemas.android.com/apk/res/android}"


def queries_intents():
    root = ET.fromstring(MANIFEST_SRC)
    queries = root.find("queries")
    assert queries is not None, "manifest has no <queries> element"
    out = []
    for intent in queries.findall("intent"):
        actions = [a.get(NS + "name") for a in intent.findall("action")]
        datas = [d.get(NS + "mimeType") for d in intent.findall("data")]
        out.append((actions, datas))
    packages = [p.get(NS + "name") for p in queries.findall("package")]
    return out, packages


class TestNoEditQueriesDeclaration(unittest.TestCase):
    def test_manifest_is_well_formed_xml(self):
        ET.fromstring(MANIFEST_SRC)  # raises on malformed XML

    def test_no_action_edit_declared(self):
        intents, _ = queries_intents()
        self.assertFalse(
            any("android.intent.action.EDIT" in actions
                for actions, _ in intents),
            "manifest <queries> must not declare ACTION_EDIT anymore")

    def test_no_google_photos_package_declared(self):
        _, packages = queries_intents()
        self.assertNotIn("com.google.android.apps.photos", packages,
                         "Google Photos package query goes with the handoff")

    def test_camera_intent_still_declared(self):
        intents, _ = queries_intents()
        self.assertTrue(
            any("android.media.action.IMAGE_CAPTURE" in actions
                for actions, _ in intents),
            "camera queries entry must survive the v56 edit")

    def test_no_photos_editor_reference_in_main(self):
        self.assertNotIn("PhotosEditor", MAIN_SRC)
        self.assertNotIn("GOOGLE_PHOTOS_PACKAGE", MAIN_SRC)


if __name__ == "__main__":
    unittest.main()
