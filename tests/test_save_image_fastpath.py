"""apk-v54 tests: the server skips the redundant re-encode.

v54: bytes already within the 2048px cap need no second
decode/re-encode — camera photos are shrunk natively and gallery
picks by the JS downscale, both at 2048/q85, so re-shrinking them
on the server was the slowest step in the save line. _save_image_file
now keeps an RGB JPEG that already fits the cap byte for byte.
Anything bigger, non-JPEG, or non-RGB (a CMYK JPEG still needs its
color convert) falls through to the full normalize path, unchanged.

Dummy data only; eBay is never touched.
"""
import io
import os
import sys
import tempfile
import types
import unittest

from PIL import Image

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PY_DIR = os.path.join(REPO, "app", "src", "main", "python")
sys.path.insert(0, PY_DIR)

import app as m  # noqa: E402  (imports cleanly; __main__ guard prevents serving)


def tearDownModule():
    sys.modules.pop("app", None)


def _image_stream(w, h, color="red", fmt="JPEG", mode="RGB"):
    buf = io.BytesIO()
    Image.new(mode, (w, h), color).save(buf, fmt)
    buf.seek(0)
    return buf


def _fake_upload(name, stream):
    return types.SimpleNamespace(filename=name, stream=stream)


class TestSaveImageFastPath(unittest.TestCase):
    def test_small_rgb_jpeg_kept_byte_identical(self):
        raw = _image_stream(800, 600).read()
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "1.jpg")
            fs = _fake_upload("photo.jpg", io.BytesIO(raw))
            self.assertTrue(m._save_image_file(fs, out))
            with open(out, "rb") as f:
                self.assertEqual(f.read(), raw)

    def test_big_jpeg_is_still_resized(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "1.jpg")
            fs = _fake_upload("big.jpg", _image_stream(4096, 3072))
            self.assertTrue(m._save_image_file(fs, out))
            with Image.open(out) as stored:
                self.assertEqual(max(stored.size), 2048)

    def test_png_still_converts_to_jpeg(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "1.jpg")
            fs = _fake_upload("pic.png", _image_stream(100, 80, fmt="PNG"))
            self.assertTrue(m._save_image_file(fs, out))
            with Image.open(out) as stored:
                self.assertEqual(stored.format, "JPEG")

    def test_cmyk_jpeg_falls_through_to_rgb(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "1.jpg")
            fs = _fake_upload("cmyk.jpg",
                              _image_stream(800, 600, fmt="JPEG", mode="CMYK"))
            self.assertTrue(m._save_image_file(fs, out))
            with Image.open(out) as stored:
                self.assertEqual(stored.format, "JPEG")
                self.assertEqual(stored.mode, "RGB")

    def test_grayscale_jpeg_falls_through_to_rgb(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "1.jpg")
            fs = _fake_upload("gray.jpg",
                              _image_stream(800, 600, color=128, mode="L"))
            self.assertTrue(m._save_image_file(fs, out))
            with Image.open(out) as stored:
                self.assertEqual(stored.mode, "RGB")


if __name__ == "__main__":
    unittest.main()
