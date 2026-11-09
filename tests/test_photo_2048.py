"""apk-v52 tests: photos are 2K (2048px long edge) and the in-app camera
asks for optical stabilization.

v52 raises the photo cap from 1600px to 2048px on all three paths that
can shrink a photo, so no path shrinks one twice:
- native CameraDownscale.downscale (CameraDownscale.kt, MAX_EDGE),
- JS downscaleFile (app.js, MAX_EDGE),
- server _save_image_file (app.py, thumbnail).
JPEG quality stays 85 and EXIF rotation is untouched. Thumbnails stay
128px (the v48 shared variable) and the v49 pick-photos grid is
unchanged; this cap is only about the stored listing photo.

v52 also turns on optical stabilization explicitly in the in-app
camera (InAppCamera.kt, Camera2 interop): LENS_OPTICAL_STABILIZATION_
MODE ON, requested only when the back camera reports it, with a
silent fallback to today's behavior when it does not — never a crash,
never a blocked capture. Capture mode stays MINIMIZE_LATENCY (fast
shutter); EIS is a video feature and does not apply to still capture.

Kotlin cannot execute in this sandbox (no working Gradle daemon here,
as with v15-v38), so the OIS wiring and the native cap are pinned by
source-contract tests here plus unit tests in
app/src/test/java/.../CameraDownscaleTest.kt; CI Gradle compilation
is the Kotlin execution gate. The on-phone test is the proof.
Dummy data only; eBay is never touched.
"""

import io
import os
import types
import unittest

from PIL import Image

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PY_DIR = os.path.join(REPO, "app", "src", "main", "python")
import sys
sys.path.insert(0, PY_DIR)

import app as m  # noqa: E402  (imports cleanly; __main__ guard prevents serving)


def tearDownModule():
    sys.modules.pop("app", None)


def _jpeg_stream(w, h, color="red"):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG")
    buf.seek(0)
    return buf


def _fake_upload(name, w, h):
    return types.SimpleNamespace(filename=name, stream=_jpeg_stream(w, h))


class TestServerSaveIs2048(unittest.TestCase):
    def test_big_photo_shrinks_to_2048_long_edge(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "1.jpg")
            self.assertTrue(m._save_image_file(_fake_upload("big.jpg", 4096, 3072), out))
            with Image.open(out) as img:
                self.assertEqual(img.size, (2048, 1536))
                self.assertEqual(img.format, "JPEG")

    def test_tall_photo_shrinks_on_its_long_edge(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "1.jpg")
            self.assertTrue(m._save_image_file(_fake_upload("tall.jpg", 1800, 2400), out))
            with Image.open(out) as img:
                self.assertEqual(img.size, (1536, 2048))

    def test_photo_at_cap_is_untouched(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "1.jpg")
            self.assertTrue(m._save_image_file(_fake_upload("atcap.jpg", 2048, 1536), out))
            with Image.open(out) as img:
                self.assertEqual(img.size, (2048, 1536))

    def test_small_photo_is_untouched(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "1.jpg")
            self.assertTrue(m._save_image_file(_fake_upload("small.jpg", 800, 600), out))
            with Image.open(out) as img:
                self.assertEqual(img.size, (800, 600))


def _read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


class TestCap2048Contracts(unittest.TestCase):
    def test_native_cap_is_2048(self):
        src = _read(os.path.join("app", "src", "main", "java",
                                 "com", "rpbdesign", "listingassistant",
                                 "CameraDownscale.kt"))
        self.assertIn("const val MAX_EDGE = 2048", src)
        self.assertIn("maxEdge: Int = MAX_EDGE", src)
        self.assertNotIn("maxEdge: Int = 1600", src)

    def test_js_cap_is_2048(self):
        src = _read(os.path.join("app", "src", "main", "python",
                                 "static", "app.js"))
        self.assertIn("const MAX_EDGE = 2048;", src)
        self.assertNotIn("const MAX_EDGE = 1600;", src)

    def test_server_cap_is_2048(self):
        src = _read(os.path.join("app", "src", "main", "python", "app.py"))
        self.assertIn("img.thumbnail((2048, 2048))", src)
        self.assertNotIn("img.thumbnail((1600, 1600))", src)

    def test_no_stale_1600_cap_on_the_photo_path(self):
        # The only "1600" left in the photo-path files is the v52 history
        # note ("the cap was 1600px"); no code still caps at 1600.
        for rel in (
                os.path.join("app", "src", "main", "java", "com",
                             "rpbdesign", "listingassistant",
                             "CameraDownscale.kt"),
                os.path.join("app", "src", "main", "python", "static",
                             "app.js"),
                os.path.join("app", "src", "main", "python", "app.py")):
            src = _read(rel)
            for line in src.splitlines():
                if "1600" in line:
                    self.assertIn("was 1600px", line,
                                  "stale 1600 cap in %s: %r" % (rel, line))


class TestOpticalStabilizationContracts(unittest.TestCase):
    SRC = None

    @classmethod
    def setUpClass(cls):
        cls.SRC = _read(os.path.join(
            "app", "src", "main", "java", "com", "rpbdesign",
            "listingassistant", "InAppCamera.kt"))

    def test_uses_camera2_interop(self):
        self.assertIn("androidx.camera.camera2.interop.Camera2Interop",
                      self.SRC)
        self.assertIn("androidx.camera.camera2.interop.Camera2CameraInfo",
                      self.SRC)

    def test_requests_ois_on(self):
        self.assertIn("CaptureRequest.LENS_OPTICAL_STABILIZATION_MODE",
                      self.SRC)
        self.assertIn("CaptureRequest.LENS_OPTICAL_STABILIZATION_MODE_ON",
                      self.SRC)

    def test_ois_only_when_the_camera_reports_it(self):
        self.assertIn(
            "CameraCharacteristics.LENS_INFO_AVAILABLE_OPTICAL_STABILIZATION",
            self.SRC)
        # The request sits behind the availability guard.
        self.assertIn("if (oisOn) {", self.SRC)

    def test_bind_failure_retries_without_ois(self):
        # A rejected OIS request must never cost him the camera: the
        # bind is retried plain before giving up.
        self.assertIn(
            "bindUseCases(provider, lifecycleOwner, previewView, false)",
            self.SRC)

    def test_fast_capture_mode_kept(self):
        self.assertIn("CAPTURE_MODE_MINIMIZE_LATENCY", self.SRC)
        self.assertNotIn("CAPTURE_MODE_MAXIMIZE_QUALITY", self.SRC)

    def test_no_video_stabilization(self):
        # EIS is a video feature; it does not apply to still capture
        # and must not be claimed or requested here.
        self.assertNotIn("VIDEO_STABILIZATION", self.SRC)


if __name__ == "__main__":
    unittest.main()
