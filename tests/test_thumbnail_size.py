"""apk-v48 tests: static guard for one shared thumbnail size.

Reynard: "make sure they match for all iterations". v44 grew the
step-1 strip to 128px but left the listing-page draft strip at 96px
and the drafts-list rows at 56px, so the smaller screens looked like
the photos had gone back to small. v48 unifies all three on one CSS
variable, --thumb-size: 128px. This guard fails the suite if any
thumbnail slot gets its own size again: every slot must read the
shared variable, the variable must be defined exactly once at
128px, and no stale small size (56px / 96px) may remain on a
thumbnail selector.

v49 exception (deliberate, not drift): the step-1 photo grid
(design 4) fills its two columns with square cells via width 100%
+ aspect-ratio 1/1, so the grid thumbs are NOT --thumb-size. The
listing-page draft strip and the drafts-list rows still read the
shared variable, and the batch rows (new in v49) read it too.
The grid exception is pinned by test_photo_grid_exception below:
exactly the .thumbs.photo-grid selectors may use 100% + aspect
ratio, and nowhere else.
"""

import os
import re
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
CSS_PATH = os.path.join(REPO, "app", "src", "main", "python", "static",
                        "style.css")
HTML_PATH = os.path.join(REPO, "app", "src", "main", "python", "templates",
                         "index.html")

# Every selector that sizes a photo thumbnail. The QR code image
# (#step-qrcode img) is not a photo thumbnail and is out of scope.
# The step-1 grid selectors are NOT here: they are the v49
# exception (see test_photo_grid_exception) and read 100% +
# aspect-ratio instead of --thumb-size.
THUMB_RULES = (
    ".thumbs img",
    ".thumbs .thumb-skip",
    ".thumb-wrap",
    ".thumb-wrap img",
    ".thumb-img",
    ".draft-item img",
    ".batch-row img",
)

# The only selectors allowed to size a thumbnail without
# --thumb-size: the v49 step-1 grid, whose cells fill their column.
GRID_RULES = (
    ".thumbs.photo-grid .thumb-wrap",
    ".thumbs.photo-grid .thumb-img",
    ".thumbs.photo-grid img",
    ".thumbs.photo-grid .thumb-skip",
)


def parse_rules(css):
    """Same small parser as tests/fit_check.py: selector -> props."""
    rules = {}
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selector = " ".join(m.group(1).split())
        props = {}
        for decl in m.group(2).split(";"):
            if ":" in decl:
                k, v = decl.split(":", 1)
                props[k.strip()] = v.strip()
        for sel in selector.split(","):
            rules.setdefault(sel.strip(), {}).update(props)
    return rules


class ThumbnailSizeTest(unittest.TestCase):
    def setUp(self):
        with open(CSS_PATH, encoding="utf-8") as f:
            self.css = f.read()
        self.rules = parse_rules(self.css)

    def test_shared_size_defined_once_at_128px(self):
        found = re.findall(r"--thumb-size\s*:\s*(\d+)px", self.css)
        self.assertEqual(
            found, ["128"],
            "--thumb-size must be defined exactly once, at 128px: %r"
            % (found,))

    def test_every_thumbnail_slot_reads_the_shared_size(self):
        for sel in THUMB_RULES:
            props = self.rules.get(sel, {})
            self.assertEqual(
                props.get("width"), "var(--thumb-size)",
                "%s must size its width from --thumb-size: %r" % (sel, props))
            # .thumb-wrap sets only the width; the img inside sets the
            # height, so no height is expected on that selector.
            if sel == ".thumb-wrap":
                continue
            self.assertEqual(
                props.get("height"), "var(--thumb-size)",
                "%s must size its height from --thumb-size: %r" % (sel, props))

    def test_no_stale_small_size_remains(self):
        # No .thumbs.small rule may remain (the comment in style.css
        # mentions the old override by name, so check parsed rules,
        # not the raw text).
        self.assertFalse(
            any(".thumbs.small" in sel for sel in self.rules),
            "the .thumbs.small 96px overrides are gone in v48 — no stale "
            "small size may remain")
        for sel in THUMB_RULES:
            props = self.rules.get(sel, {})
            for prop in ("width", "height"):
                v = props.get(prop, "")
                self.assertNotIn(
                    v, ("56px", "96px"),
                    "%s %s must not carry a stale small size: %s"
                    % (sel, prop, v))

    def test_draft_photos_strip_uses_the_shared_strip(self):
        with open(HTML_PATH, encoding="utf-8") as f:
            html = f.read()
        self.assertIn('id="draft-photos" class="thumbs"', html)
        self.assertNotIn('id="draft-photos" class="thumbs small"', html)

    def test_photo_grid_exception(self):
        # v49: the step-1 grid is the ONE exception to the shared
        # size. Its cells fill their column (width 100%) and stay
        # square (aspect-ratio 1/1), so the grid thumbs are uniform
        # with each other at any phone width. No other thumbnail
        # selector may use 100% or an aspect ratio.
        for sel in GRID_RULES:
            props = self.rules.get(sel, {})
            self.assertEqual(
                props.get("width"), "100%",
                "%s must fill its grid cell: %r" % (sel, props))
            if sel == ".thumbs.photo-grid .thumb-wrap":
                self.assertEqual(
                    props.get("aspect-ratio"), "1 / 1",
                    "%s must stay square: %r" % (sel, props))
            elif sel == ".thumbs.photo-grid .thumb-skip":
                self.assertEqual(
                    props.get("aspect-ratio"), "1 / 1",
                    "%s must stay square: %r" % (sel, props))
            else:
                self.assertEqual(
                    props.get("height"), "100%",
                    "%s must fill its grid cell: %r" % (sel, props))
        self.assertEqual(
            self.rules.get(".thumbs.photo-grid", {}).get(
                "grid-template-columns"), "1fr 1fr",
            "the step-1 grid must be two equal columns")
        for sel, props in self.rules.items():
            if sel in GRID_RULES or sel == ".thumbs.photo-grid":
                continue
            self.assertNotIn(
                props.get("aspect-ratio"), ("1 / 1", "1/1"),
                "only the step-1 grid may use aspect-ratio: %s" % sel)

    def test_step1_grid_is_a_grid(self):
        with open(HTML_PATH, encoding="utf-8") as f:
            html = f.read()
        self.assertIn('id="thumbs" class="thumbs photo-grid"', html)
        self.assertIn('id="draft-photos" class="thumbs"', html)


if __name__ == "__main__":
    unittest.main()
