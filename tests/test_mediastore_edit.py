"""apk-v56 tests: the Pixel/Google Photos editor handoff is gone.

v36-v44 handed a listing photo to the phone's editor (ACTION_EDIT on a
MediaStore working copy, pinned to Google Photos in v44). Nov 8 proved
the handoff fragile: Android can kill the app while the outside editor
is open, and on relaunch the app opens step 1 fresh with no record of
the draft he was in. Nov 18 he asked for eBay-app-like editing in our
app, and the eBay app itself will not edit these listings (something
about an inventory process), so our editor is the only editor he has.

v56 replaces the handoff with a full in-app editor (pure HTML/JS/canvas
web code, shared by step 1 and the draft page). These tests pin the
removal: no edit-launch code, no MediaStore working copy, no edit
bridge, no PhotosEditor, and the draft replace endpoint the in-app
editor saves through still exists.

Kotlin cannot execute in this sandbox (no JVM/kotlinc, as with
v15-v55), so these are source-contract regression tests over the real
MainActivity.kt, AndroidManifest.xml, app.js, and app.py. CI Gradle
compilation is the Kotlin execution gate; the live Edit test on his
Pixel is the proof. Dummy data only; eBay is never touched.
"""

import os
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
KT = os.path.join(
    REPO, "app", "src", "main", "java",
    "com", "rpbdesign", "listingassistant", "MainActivity.kt")
PHOTOS_EDITOR = os.path.join(
    REPO, "app", "src", "main", "java",
    "com", "rpbdesign", "listingassistant", "PhotosEditor.kt")
JS = os.path.join(REPO, "app", "src", "main", "python", "static", "app.js")
PY = os.path.join(REPO, "app", "src", "main", "python", "app.py")

with open(KT, encoding="utf-8") as f:
    SRC = f.read()
with open(JS, encoding="utf-8") as f:
    JS_SRC = f.read()
with open(PY, encoding="utf-8") as f:
    PY_SRC = f.read()


class TestNoEditHandoffInKotlin(unittest.TestCase):
    def test_no_edit_launch_or_result_handlers(self):
        for name in ("launchPixelEditor", "handlePixelEditResult",
                     "insertMediaStoreWorkingCopy", "downloadLocalPhoto",
                     "postEditedPhoto", "clearPendingEdit"):
            self.assertNotIn(name, SRC, name + " must be gone with the handoff")

    def test_no_pending_edit_state(self):
        for name in ("pendingEditDraftId", "pendingEditIndex",
                     "pendingEditMediaUri", "pendingEditOut",
                     "pendingEditOutUri", "pendingEditOrig"):
            self.assertNotIn(name, SRC, name + " must be gone with the handoff")

    def test_no_edit_bridge_or_request_code(self):
        self.assertNotIn("editDraftPhoto", SRC)
        self.assertNotIn("EDIT_PHOTO_REQUEST", SRC)

    def test_no_action_edit_intent(self):
        self.assertNotIn("Intent.ACTION_EDIT", SRC)
        self.assertNotIn("No photo editor found on this phone.", SRC)
        self.assertNotIn("The editor didn't return a photo.", SRC)

    def test_photos_editor_file_deleted(self):
        self.assertFalse(os.path.exists(PHOTOS_EDITOR),
                         "PhotosEditor.kt must be deleted with the handoff")

    def test_camera_path_survives(self):
        # Only the edit handoff goes: the in-app camera and its
        # fallback are untouched.
        self.assertIn("openInAppCamera", SRC)
        self.assertIn("CAMERA_JS_REQUEST", SRC)
        self.assertIn("handleCameraJsResult", SRC)


class TestInAppEditorIsTheEditPath(unittest.TestCase):
    def test_js_has_no_pixel_handoff(self):
        self.assertNotIn("editDraftPhotoInPixel", JS_SRC)
        self.assertNotIn("window.Android.editDraftPhoto", JS_SRC)
        # The v56 comment in app.js names the removed bridge for the
        # record; the calls above are what must be gone.

    def test_js_opens_the_in_app_editor_from_both_screens(self):
        self.assertIn("function openEditor(", JS_SRC)
        self.assertIn("function openDraftEditor(", JS_SRC)
        self.assertIn("openDraftEditor(i)", JS_SRC)

    def test_draft_replace_endpoint_still_exists(self):
        # The in-app editor saves a draft photo through this endpoint;
        # deleting the handoff must not take the save path with it.
        self.assertIn("api_draft_replace_photo", PY_SRC)
        self.assertIn('/photos/<int:index>/replace', PY_SRC)


if __name__ == "__main__":
    unittest.main()
