"""apk-v37 tests: MediaStore working-copy edit (Pixel editor handoff).

v36 fired ACTION_EDIT on the app's private provider URI and his Pixel
resolved it to nothing ("No photo editor found on this phone.").
v37 inserts the photo into MediaStore as a working copy and fires
ACTION_EDIT on that library URI instead, reads the edited bytes back
into the same draft slot, and deletes the working copy on every round
end so his gallery is never polluted.

Kotlin cannot execute in this sandbox (no JVM/kotlinc, as with
v15-v36), so these are source-contract regression tests over the real
MainActivity.kt: they pin the exact behaviors the live Pixel test must
prove, and they fail if anyone reverts to the private provider URI,
reorders the return shapes, or drops the working-copy cleanup. CI
Gradle compilation is the Kotlin execution gate; the live return on
his Pixel is the proof. Dummy data only; eBay is never touched.
"""

import os
import re
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
KT = os.path.join(
    REPO, "app", "src", "main", "java",
    "com", "rpbdesign", "listingassistant", "MainActivity.kt")

with open(KT, encoding="utf-8") as f:
    SRC = f.read()


def _fn_body(src, start_marker, end_marker):
    start = src.index(start_marker)
    end = src.index(end_marker, start)
    return src[start:end]


def _launch_body():
    return _fn_body(SRC, "private fun launchPixelEditor",
                    "private fun clearPendingEdit")


def _clear_body():
    return _fn_body(SRC, "private fun clearPendingEdit",
                    "private fun downloadLocalPhoto")


def _handle_body():
    return _fn_body(SRC, "private fun handlePixelEditResult",
                    "override fun onActivityResult")


def _insert_body():
    return _fn_body(SRC, "private fun insertMediaStoreWorkingCopy",
                    "private fun launchPixelEditor")


class TestMediaStoreEdit(unittest.TestCase):
    def test_insert_uses_mediastore_images_uri(self):
        body = _insert_body()
        self.assertIn("contentResolver.insert(", body)
        self.assertIn("MediaStore.Images.Media.EXTERNAL_CONTENT_URI", body)

    def test_insert_sets_name_mime_and_pending(self):
        body = _insert_body()
        self.assertIn("MediaStore.Images.Media.DISPLAY_NAME", body)
        self.assertIn('"image/jpeg"', body)
        # Published only after bytes land, so the editor never sees a
        # half-written file (Android 10+ path).
        self.assertIn("MediaStore.Images.Media.IS_PENDING, 1", body)
        self.assertIn("MediaStore.Images.Media.IS_PENDING, 0", body)

    def test_intent_uses_media_uri_never_provider_uri(self):
        body = _launch_body()
        self.assertIn("insertMediaStoreWorkingCopy(orig)", body)
        self.assertIn("setDataAndType(mediaUri", body)
        self.assertNotIn("setDataAndType(inUri", SRC)

    def test_no_private_input_state_remains(self):
        self.assertNotIn("pendingEditInUri", SRC)
        self.assertIsNone(re.search(r"pendingEditIn\s*=", SRC))
        self.assertNotIn("edit_in_", SRC)

    def test_resolve_null_keeps_plain_last_resort_message(self):
        body = _launch_body()
        self.assertIn("resolveActivity(packageManager) == null", body)
        self.assertIn("No photo editor found on this phone.", body)

    def test_resolution_order_media_then_returned_then_outfile(self):
        body = _handle_body()
        i_media = body.index("mediaBytes")
        i_returned = body.index("data?.data")
        i_out = body.index("outFile.length()")
        self.assertLess(i_media, i_returned)
        self.assertLess(i_returned, i_out)

    def test_media_change_detected_by_comparison_to_original(self):
        body = _handle_body()
        self.assertIn("mediaBytes.isNotEmpty()", body)
        self.assertIn("!mediaBytes.contentEquals(orig)", body)

    def test_clear_deletes_media_working_copy_and_revokes(self):
        body = _clear_body()
        self.assertIn("contentResolver.delete(", body)
        self.assertIn("pendingEditMediaUri = null", body)
        self.assertIn("revokeUriPermission(mediaUri", body)

    def test_clear_runs_on_every_round_end(self):
        # Resolve-null + launch catch + handle finally: success, cancel,
        # failure, and no-return all clean up the working copy.
        self.assertGreaterEqual(SRC.count("clearPendingEdit()"), 3)

    def test_nothing_returned_keeps_original_and_says_so(self):
        body = _handle_body()
        self.assertIn("if (edited == null)", body)
        self.assertIn("The editor didn't return a photo. "
                      "Your original is unchanged.", body)
        self.assertIn("postEditedPhoto(id, index, edited)", body)


if __name__ == "__main__":
    unittest.main()
