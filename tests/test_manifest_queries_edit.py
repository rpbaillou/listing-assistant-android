"""apk-v39 tests: ACTION_EDIT declared in manifest <queries>.

Tapping Edit on a listing photo showed "No photo editor found on this
phone." on BOTH v36 (private provider URI) and v37/1.37 (MediaStore
URI). That toast fires ONLY at the resolveActivity == null branch in
launchPixelEditor; a MediaStore insert failure gives a DIFFERENT toast
("Couldn't open that photo for editing.") which never appeared, so
download plus insert succeeded and resolution failed. Root cause: the
manifest <queries> declared ONLY android.media.action.IMAGE_CAPTURE
(the v17 camera fix) and never declared android.intent.action.EDIT,
so on Android 11+ package-visibility rules resolveActivity /
queryIntentActivities returned empty for ACTION_EDIT no matter what
URI shape Edit used. The URI was never the problem.

Kotlin cannot execute in this sandbox (no JVM/kotlinc, as with
v15-v38), so these are source-contract regression tests over the real
AndroidManifest.xml and MainActivity.kt: they pin the declaration Edit
needs, keep the camera declaration intact, and pin the edit intent
unchanged (no CATEGORY_DEFAULT added, which would restrict matching
to filters containing DEFAULT and could break resolution if the
editor's filter lacks it). CI Gradle compilation is the Kotlin
execution gate; the live Edit test on his Pixel is the proof. Dummy
data only; eBay is never touched.
"""

import os
import re
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
    return out


class TestEditQueriesDeclaration(unittest.TestCase):
    def test_manifest_is_well_formed_xml(self):
        ET.fromstring(MANIFEST_SRC)  # raises on malformed XML

    def test_edit_intent_declared_with_image_mime_type(self):
        intents = queries_intents()
        self.assertTrue(
            any("android.intent.action.EDIT" in actions
                and "image/*" in datas for actions, datas in intents),
            "manifest <queries> must declare ACTION_EDIT with image/*")

    def test_camera_intent_still_declared(self):
        intents = queries_intents()
        self.assertTrue(
            any("android.media.action.IMAGE_CAPTURE" in actions
                for actions, _ in intents),
            "camera queries entry must survive the v39 edit")

    def test_edit_intent_not_duplicated(self):
        count = sum(
            1 for actions, _ in queries_intents()
            if "android.intent.action.EDIT" in actions)
        self.assertEqual(count, 1, "exactly one ACTION_EDIT queries entry")


class TestEditIntentUnchanged(unittest.TestCase):
    def test_edit_intent_uses_media_uri_and_jpeg_type(self):
        self.assertIn('setDataAndType(mediaUri, "image/jpeg")', MAIN_SRC)

    def test_edit_intent_keeps_extra_output_and_grants(self):
        self.assertIn("MediaStore.EXTRA_OUTPUT", MAIN_SRC)
        self.assertIn("FLAG_GRANT_READ_URI_PERMISSION", MAIN_SRC)
        self.assertIn("FLAG_GRANT_WRITE_URI_PERMISSION", MAIN_SRC)

    def test_no_category_default_added_anywhere(self):
        # Adding CATEGORY_DEFAULT would restrict matching to filters
        # containing DEFAULT and could break resolution if the editor's
        # filter lacks it; the v39 fix is visibility only, not intent
        # surgery.
        self.assertNotIn("CATEGORY_DEFAULT", MAIN_SRC)

    def test_resolve_null_still_shows_plain_message(self):
        self.assertIn('"No photo editor found on this phone."', MAIN_SRC)


class TestGooglePhotosTarget(unittest.TestCase):
    """v44: Edit pins to Google Photos when it can handle the edit."""

    def test_photos_package_declared_in_queries(self):
        root = ET.fromstring(MANIFEST_SRC)
        queries = root.find("queries")
        packages = [p.get(NS + "name") for p in queries.findall("package")]
        self.assertIn("com.google.android.apps.photos", packages,
                      "manifest <queries> must name Google Photos")

    def test_edit_pins_to_photos_when_available(self):
        self.assertIn("PhotosEditor.GOOGLE_PHOTOS_PACKAGE", MAIN_SRC)
        self.assertIn("targetPackage(photosCanEdit)", MAIN_SRC)

    def test_edit_still_falls_back_to_system_resolver(self):
        # targetPackage returns null when Photos cannot edit, and the
        # intent is only pinned inside a ?.let — with no pin the
        # resolveActivity + grant loop below runs exactly as v36/v37.
        self.assertIn("?.let {", MAIN_SRC)
        self.assertIn("intent.setPackage(it)", MAIN_SRC)

    def test_edit_intent_shape_unchanged(self):
        # Still the MediaStore URI + JPEG type + EXTRA_OUTPUT from v37;
        # only the package pin is new.
        self.assertIn('setDataAndType(mediaUri, "image/jpeg")', MAIN_SRC)


if __name__ == "__main__":
    unittest.main()
