"""apk-v44 tests: in-app camera (CameraX) for rapid multi-shot.

The Take photo button used to hand off to the Pixel camera app, whose
review screen makes him tap a check after every shot — slow, and one
photo per round trip. v44 builds the camera into the app: a big
shutter button, tap tap tap, each tap saves a photo at once, the
camera stays open, no review screen, no check.

Kotlin cannot execute in this sandbox (no working Gradle daemon here,
as with v15-v38), so these are source-contract regression tests over
the real sources: they pin the permission, the CameraX wiring, the
Take-photo routing, the fallback, and the photo pipeline (same 1600px
downscale + onCameraPhoto handoff as the old path). CI Gradle
compilation is the Kotlin execution gate; the rapid multi-shot test
on his Pixel is the proof. Dummy data only; eBay is never touched.
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
IN_APP = os.path.join(
    REPO, "app", "src", "main", "java",
    "com", "rpbdesign", "listingassistant", "InAppCamera.kt")
LAYOUT = os.path.join(REPO, "app", "src", "main", "res", "layout",
                      "activity_main.xml")
GRADLE = os.path.join(REPO, "app", "build.gradle.kts")
PROPS = os.path.join(REPO, "gradle.properties")
CAPTURE = os.path.join(
    REPO, "app", "src", "main", "java",
    "com", "rpbdesign", "listingassistant", "CameraCapture.kt")

with open(MANIFEST, encoding="utf-8") as f:
    MANIFEST_SRC = f.read()
with open(MAIN, encoding="utf-8") as f:
    MAIN_SRC = f.read()
with open(IN_APP, encoding="utf-8") as f:
    IN_APP_SRC = f.read()
with open(LAYOUT, encoding="utf-8") as f:
    LAYOUT_SRC = f.read()
with open(GRADLE, encoding="utf-8") as f:
    GRADLE_SRC = f.read()
with open(PROPS, encoding="utf-8") as f:
    PROPS_SRC = f.read()
with open(CAPTURE, encoding="utf-8") as f:
    CAPTURE_SRC = f.read()

NS = "{http://schemas.android.com/apk/res/android}"


class TestCameraPermission(unittest.TestCase):
    def test_manifest_declares_camera_permission(self):
        root = ET.fromstring(MANIFEST_SRC)
        perms = [p.get(NS + "name") for p in root.findall("uses-permission")]
        self.assertIn("android.permission.CAMERA", perms)

    def test_camera_feature_is_not_required(self):
        root = ET.fromstring(MANIFEST_SRC)
        feats = {f.get(NS + "name"): f.get(NS + "required")
                 for f in root.findall("uses-feature")}
        self.assertEqual(feats.get("android.hardware.camera"), "false",
                         "the app must still install on a device with no camera")

    def test_manifest_is_well_formed_xml(self):
        ET.fromstring(MANIFEST_SRC)


class TestCameraXWiring(unittest.TestCase):
    def test_camerax_deps_present(self):
        for artifact in ("camera-core", "camera-camera2",
                         "camera-lifecycle", "camera-view"):
            self.assertIn("androidx.camera:%s:" % artifact, GRADLE_SRC)

    def test_androidx_enabled(self):
        self.assertIn("android.useAndroidX=true", PROPS_SRC)

    def test_in_app_camera_binds_back_camera(self):
        self.assertIn("CameraSelector.DEFAULT_BACK_CAMERA", IN_APP_SRC)
        self.assertIn("bindToLifecycle", IN_APP_SRC)

    def test_capture_minimizes_latency(self):
        self.assertIn("CAPTURE_MODE_MINIMIZE_LATENCY", IN_APP_SRC)

    def test_shutter_ignored_while_capturing(self):
        self.assertIn("isCapturing()", IN_APP_SRC)
        self.assertIn("if (capture == null || capturing) return", IN_APP_SRC)

    def test_stop_unbinds(self):
        self.assertIn("unbindAll()", IN_APP_SRC)


class TestTakePhotoRouting(unittest.TestCase):
    def test_open_camera_routes_to_in_app_camera(self):
        self.assertIn("runOnUiThread { openInAppCamera() }", MAIN_SRC)

    def test_permission_gate_uses_decision_helper(self):
        self.assertIn("CameraCapture.useInAppCamera(", MAIN_SRC)
        self.assertIn("fun useInAppCamera(permissionGranted: Boolean): Boolean",
                      CAPTURE_SRC)

    def test_denied_permission_falls_back_to_camera_app(self):
        self.assertIn("CAMERA_PERMISSION_REQUEST", MAIN_SRC)
        # The denial branch names the fallback in plain words and calls it.
        self.assertIn("Camera permission is off, so photos open in the camera app instead.",
                      MAIN_SRC)
        self.assertIn("launchCameraForJs()", MAIN_SRC)

    def test_bind_failure_falls_back_to_camera_app(self):
        self.assertIn("Couldn't start the camera. Opening the camera app instead.",
                      MAIN_SRC)

    def test_camera_app_path_still_exists(self):
        self.assertIn("fun launchCameraForJs()", MAIN_SRC)
        self.assertIn("MediaStore.ACTION_IMAGE_CAPTURE", MAIN_SRC)


class TestInAppPhotoPipeline(unittest.TestCase):
    def test_shutter_uses_same_downscale_and_handoff(self):
        # The in-app shot goes through the exact pipeline the old
        # camera-app result used, so step-1/draft routing is unchanged.
        self.assertIn("CameraDownscale.downscale(saved)", MAIN_SRC)
        self.assertIn("onCameraPhoto('data:image/jpeg;base64,$b64')", MAIN_SRC)

    def test_temp_file_is_deleted_after_handoff(self):
        self.assertIn("saved.delete()", MAIN_SRC)

    def test_back_closes_camera_not_the_app(self):
        self.assertIn("if (cameraOpen) {", MAIN_SRC)
        self.assertIn("closeInAppCamera()", MAIN_SRC)

    def test_destroy_stops_the_camera(self):
        self.assertIn("inAppCamera?.stop()", MAIN_SRC)


class TestCameraOverlay(unittest.TestCase):
    def test_overlay_ids_exist_in_layout(self):
        for view_id in ("camera_view", "camera_preview", "camera_count",
                        "camera_shutter", "camera_done"):
            self.assertIn('android:id="@+id/%s"' % view_id, LAYOUT_SRC)

    def test_overlay_starts_hidden(self):
        m = re.search(
            r'android:id="@\+id/camera_view".*?android:visibility="gone"',
            LAYOUT_SRC, re.S)
        self.assertIsNotNone(m, "camera overlay must start hidden")

    def test_preview_is_camerax_view(self):
        self.assertIn("androidx.camera.view.PreviewView", LAYOUT_SRC)

    def test_shutter_is_a_big_round_button(self):
        self.assertIn("@drawable/shutter_button", LAYOUT_SRC)
        self.assertIn('android:layout_width="88dp"', LAYOUT_SRC)


if __name__ == "__main__":
    unittest.main()
