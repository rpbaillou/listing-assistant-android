"""apk-v66 tests: the step-1 photo grid flows (layout must flow,
nothing overlaps).

Nov 20 he reported the grid overlapping the elements below it — the
photo-count line and Take/Choose buttons covered, the bottom row of
photos sitting on the Draft button — with 6 Barbie photos. The
cause, proven in a real Chromium layout: the wrap carried
aspect-ratio 1/1, so its height was locked to its width while its
content (frame + an Undo edit button, ~31px) was taller. The grid's
rows were sized from the locked height, so every row's content
spilled ~31px past its row — accumulating to ~92px below the grid
box for 3 rows — and the locked height transferred back into width,
widening the columns ~31px so the grid ran off the right edge of
the screen (sideways scroll, right-column X buttons half
off-screen). The fix moves the square onto the frame
(aspect-ratio on .thumb-img); the wrap is height:auto, so it grows
with its content and the grid — and everything below it — flows.

Two layers: static CSS assertions (run everywhere, in the standard
suite) that pin the sizing contract, and a real-layout test
(TestPhotoGridLayout, Playwright + Chromium) that reproduces the
reported state — 6 photos, edited ones carrying Undo buttons — and
asserts geometry: the grid's box grows to contain its content, the
Analyze button's top sits below the grid's bottom, no photo or
button overlaps the controls below, and the page never scrolls
sideways. The layout test skips cleanly when Playwright or
Chromium is unavailable (it cannot measure layout without a
browser); the static assertions still guard the cause.
"""

import os
import re
import tempfile
import unittest
import urllib.parse

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
CSS_PATH = os.path.join(REPO, "app", "src", "main", "python", "static",
                        "style.css")

# One portrait phone photo as an inline SVG (intrinsic 756x1008, so
# the layout engine treats it like a real camera photo).
SVG = ("<svg xmlns='http://www.w3.org/2000/svg' width='756' "
       "height='1008'><rect width='756' height='1008' fill='#787878'/>"
       "</svg>")
IMG_SRC = "data:image/svg+xml," + urllib.parse.quote(SVG)

PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>v66 layout repro</title><style>{css}</style></head>
<body><header><h1>Listing Assistant</h1></header>
<main><section id="step-photos" class="step">
<p class="hint">Choose up to 24 photos.</p>
<div id="thumbs" class="thumbs photo-grid"></div>
<p id="photo-count" class="hint"></p>
<div class="photobtns">
<button class="primary" type="button">Take photo</button>
<label class="ghost">Choose photos</label></div>
<button id="btn-analyze" class="primary big" type="button">Draft my listing with AI</button>
<button id="btn-quick-post" class="secondary big" type="button">Quick post (skip AI)</button>
<p class="hint">Quick post skips the AI wait.</p>
</section></main>
<script>
const q = new URLSearchParams(location.search);
const N = parseInt(q.get('n') || '6', 10);
const undoSet = new Set((q.get('undo') || '').split(',').filter(Boolean).map(Number));
const box = document.getElementById('thumbs');
for (let i = 0; i < N; i++) {{
  const wrap = document.createElement('div'); wrap.className = 'thumb-wrap';
  const frame = document.createElement('div'); frame.className = 'thumb-img';
  const img = document.createElement('img'); img.src = {img!r};
  const x = document.createElement('button'); x.type = 'button'; x.className = 'thumb-x'; x.textContent = '\\u00d7';
  const e = document.createElement('button'); e.type = 'button'; e.className = 'thumb-edit'; e.textContent = '\\u270e Edit';
  frame.appendChild(img); frame.appendChild(x); frame.appendChild(e);
  wrap.appendChild(frame);
  if (undoSet.has(i)) {{
    const u = document.createElement('button'); u.type = 'button'; u.className = 'thumb-edit-below'; u.textContent = 'Undo edit';
    wrap.appendChild(u);
  }}
  box.appendChild(wrap);
}}
document.getElementById('photo-count').textContent = N + ' of 24 photos';
</script></body></html>
"""

# The listing-page draft strip, same builder shape as
# renderDraftPhotos(): wrap > frame(img + X) + Edit(below) +
# optional Undo(below), in the flex .thumbs strip.
STRIP_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>v66 strip repro</title><style>{css}</style></head>
<body><main><section class="step">
<div id="draft-photos" class="thumbs"></div>
<p id="draft-photo-count" class="hint"></p>
<button id="btn-analyze" class="primary big" type="button">Draft my listing with AI</button>
</section></main>
<script>
const q = new URLSearchParams(location.search);
const N = parseInt(q.get('n') || '6', 10);
const undoSet = new Set((q.get('undo') || '').split(',').filter(Boolean).map(Number));
const box = document.getElementById('draft-photos');
for (let i = 0; i < N; i++) {{
  const wrap = document.createElement('div'); wrap.className = 'thumb-wrap';
  const frame = document.createElement('div'); frame.className = 'thumb-img';
  const img = document.createElement('img'); img.src = {img!r};
  const x = document.createElement('button'); x.type = 'button'; x.className = 'thumb-x'; x.textContent = '\\u00d7';
  frame.appendChild(img); frame.appendChild(x); wrap.appendChild(frame);
  const e = document.createElement('button'); e.type = 'button'; e.className = 'thumb-edit-below'; e.textContent = 'Edit';
  wrap.appendChild(e);
  if (undoSet.has(i)) {{
    const u = document.createElement('button'); u.type = 'button'; u.className = 'thumb-edit-below'; u.textContent = 'Undo edit';
    wrap.appendChild(u);
  }}
  box.appendChild(wrap);
}}
document.getElementById('draft-photo-count').textContent = N + ' photos';
</script></body></html>
"""

MEASURE = """
() => {
  const r = (el) => { const b = el.getBoundingClientRect();
    return {top: b.top, bottom: b.bottom, left: b.left, right: b.right,
            height: b.height, width: b.width}; };
  const out = {};
  out.viewportW = innerWidth;
  out.scrollW = document.documentElement.scrollWidth;
  const grid = document.getElementById('thumbs') || document.getElementById('draft-photos');
  out.grid = r(grid);
  out.wraps = Array.from(grid.querySelectorAll('.thumb-wrap')).map(r);
  out.frames = Array.from(grid.querySelectorAll('.thumb-img')).map(r);
  out.undos = Array.from(grid.querySelectorAll('.thumb-edit-below')).map(r);
  out.undoTexts = Array.from(grid.querySelectorAll('.thumb-edit-below')).map(el => el.textContent);
  out.edits = Array.from(grid.querySelectorAll('.thumb-edit')).map(r);
  out.xs = Array.from(grid.querySelectorAll('.thumb-x')).map(r);
  const below = (id) => { const el = document.getElementById(id); return el ? r(el) : null; };
  out.count = below('photo-count') || below('draft-photo-count');
  out.photobtns = document.querySelector('.photobtns') ? r(document.querySelector('.photobtns')) : null;
  out.analyze = r(document.getElementById('btn-analyze'));
  return out;
}
"""

# Playwright is installed in the test venv for the layout proof. If
# it (or a Chromium) is missing, the layout class skips cleanly.
try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT = True
except ImportError:
    _PLAYWRIGHT = False


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


def _px(value, root=17):
    """Pixel value of a rem/px length at the 17px root, or None."""
    if value is None:
        return None
    m = re.match(r"^(-?[\d.]+)(px|rem)$", value)
    if not m:
        return None
    v = float(m.group(1))
    return v * root if m.group(2) == "rem" else v


def _overlaps(a, b):
    return not (a["bottom"] <= b["top"] or a["top"] >= b["bottom"]
                or a["right"] <= b["left"] or a["left"] >= b["right"])


class PhotoGridFlowTest(unittest.TestCase):
    def setUp(self):
        with open(CSS_PATH, encoding="utf-8") as f:
            self.css = f.read()
        self.rules = parse_rules(self.css)

    def test_grid_wrap_has_no_aspect_ratio_and_no_fixed_height(self):
        props = self.rules.get(".thumbs.photo-grid .thumb-wrap", {})
        self.assertNotIn("aspect-ratio", props,
                         "the wrap must not lock its height to its width: "
                         "an Undo button below the frame is taller than "
                         "the square, and the locked height spills: %r"
                         % (props,))
        self.assertNotIn("height", props)
        self.assertNotIn("max-height", props)

    def test_grid_frame_is_the_square(self):
        props = self.rules.get(".thumbs.photo-grid .thumb-img", {})
        self.assertEqual(props.get("aspect-ratio"), "1 / 1",
                         "the FRAME carries the square now: %r" % (props,))
        self.assertEqual(props.get("height"), "auto")
        self.assertEqual(props.get("width"), "100%")

    def test_grid_itself_has_no_fixed_height(self):
        props = self.rules.get(".thumbs.photo-grid", {})
        self.assertNotIn("height", props)
        self.assertNotIn("max-height", props)
        self.assertEqual(props.get("grid-template-columns"), "1fr 1fr")

    def test_base_wrap_has_no_aspect_ratio(self):
        # The listing-page draft strip shares .thumb-wrap; it must
        # flow with its content too (Edit + Undo below the photo).
        self.assertNotIn("aspect-ratio",
                         self.rules.get(".thumb-wrap", {}))

    def test_undo_margin_clears_the_hanging_edit_pill(self):
        # The step-1 Edit pill hangs 8px below the frame; the Undo
        # button below it must start further down than the pill ends.
        pill = abs(_px(self.rules.get(".thumb-edit", {}).get("bottom")) or 0)
        margin = _px(self.rules.get(".thumb-edit-below", {}).get("margin-top"))
        self.assertEqual(pill, 8)
        self.assertIsNotNone(margin)
        self.assertGreater(margin, pill,
                           "Undo margin-top %r must beat the 8px pill "
                           "overhang" % margin)

    def test_no_height_is_fixed_anywhere_on_the_grid_stack(self):
        # Nothing in the grid's own stack may pin a height that clips
        # or spills: wrap, frame, skip placeholder, grid. height:auto
        # is fine (it lets the box follow its content); a length is
        # what pins it.
        for sel in (".thumbs.photo-grid .thumb-wrap",
                    ".thumbs.photo-grid",
                    ".thumbs.photo-grid .thumb-skip"):
            props = self.rules.get(sel, {})
            h = props.get("height")
            self.assertTrue(
                h is None or h == "auto",
                "%s must not pin a height, got %r" % (sel, h))


@unittest.skipUnless(_PLAYWRIGHT,
                     "Playwright is not installed; layout proof skipped")
class TestPhotoGridLayout(unittest.TestCase):
    """Real Chromium geometry for the reported state."""

    @classmethod
    def setUpClass(cls):
        with open(CSS_PATH, encoding="utf-8") as f:
            css = f.read()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.page_path = os.path.join(cls._tmp.name, "index.html")
        with open(cls.page_path, "w", encoding="utf-8") as f:
            f.write(PAGE.format(css=css, img=IMG_SRC))
        cls.strip_path = os.path.join(cls._tmp.name, "strip.html")
        with open(cls.strip_path, "w", encoding="utf-8") as f:
            f.write(STRIP_PAGE.format(css=css, img=IMG_SRC))
        try:
            cls._pw = sync_playwright().start()
            cls._browser = cls._pw.chromium.launch()
        except Exception as exc:  # no Chromium to measure with
            raise unittest.SkipTest(
                "Chromium is not available: %s" % (exc,))

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()
        cls._tmp.cleanup()

    def _measure(self, path, width, query):
        page = self._browser.new_page(viewport={"width": width,
                                                "height": 900})
        try:
            page.goto("file://" + path + query)
            page.wait_for_timeout(400)
            return page.evaluate(MEASURE)
        finally:
            page.close()

    def _assert_flows(self, m, label, grid_page=True):
        grid = m["grid"]
        self.assertLessEqual(
            m["scrollW"], m["viewportW"],
            "%s: page must not scroll sideways (scrollW %s > viewport %s)"
            % (label, m["scrollW"], m["viewportW"]))
        if m["wraps"]:
            max_wrap_bottom = max(w["bottom"] for w in m["wraps"])
            self.assertAlmostEqual(
                max_wrap_bottom, grid["bottom"], delta=1.0,
                msg="%s: the grid box must grow to contain its content "
                    "(grid bottom %.1f, deepest wrap %.1f)"
                    % (label, grid["bottom"], max_wrap_bottom))
            if grid_page:
                # Grid columns are 1fr each: a wrap must stay inside
                # its column, not widen it (the v66 breakage).
                col = (grid["width"] - 21.25) / 2  # 1.25rem gap, 17px root
                for w in m["wraps"]:
                    self.assertAlmostEqual(
                        w["width"], col, delta=1.0,
                        msg="%s: a wrap must stay inside its column "
                            "(%.1f wide, column %.1f)" % (label, w["width"], col))
            for fr in m["frames"]:
                self.assertAlmostEqual(
                    fr["width"], fr["height"], delta=1.0,
                    msg="%s: photos stay square (%.1f x %.1f)"
                    % (label, fr["width"], fr["height"]))
        for name in ("count", "photobtns", "analyze"):
            target = m.get(name)
            if target is None:
                continue
            self.assertGreaterEqual(
                target["top"], grid["bottom"] - 1.0,
                "%s: %s must sit below the grid, not under it "
                    "(top %.1f, grid bottom %.1f)"
                    % (label, name, target["top"], grid["bottom"]))
            for kind in ("wraps", "frames", "undos"):
                hit = [i for i, b in enumerate(m[kind])
                       if _overlaps(b, target)]
                self.assertEqual(
                    hit, [],
                    "%s: %s overlaps %s: %s" % (label, kind, name, hit))

    def test_reported_state_6_photos_undo_on_all(self):
        # The exact Nov 20 report: 6 photos, edited ones carrying
        # Undo buttons, on his phone width.
        m = self._measure(self.page_path, 412,
                          "?n=6&undo=0,1,2,3,4,5")
        self.assertEqual(len(m["wraps"]), 6)
        self.assertEqual(len(m["undos"]), 6)
        self._assert_flows(m, "6 photos, Undo on all, 412px")

    def test_24_photos_undo_on_all(self):
        # The spill grew with the row count, so the cap state must
        # flow too.
        m = self._measure(self.page_path, 412,
                          "?n=24&undo=" + ",".join(str(i) for i in range(24)))
        self.assertEqual(len(m["wraps"]), 24)
        self._assert_flows(m, "24 photos, Undo on all, 412px")

    def test_no_undo_and_narrow_widths(self):
        m = self._measure(self.page_path, 412, "?n=6&undo=")
        self._assert_flows(m, "6 photos, no Undo, 412px")
        m = self._measure(self.page_path, 360,
                          "?n=6&undo=0,1,2,3,4,5")
        self._assert_flows(m, "6 photos, Undo on all, 360px")
        m = self._measure(self.page_path, 320,
                          "?n=6&undo=0,1,2,3,4,5")
        self._assert_flows(m, "6 photos, Undo on all, 320px")

    def test_draft_strip_flows_with_edit_and_undo(self):
        # The same pattern, checked on the other strip that uses
        # .thumbs: Edit + Undo below every photo must grow the strip
        # and never cover the controls below it, and an X must never
        # paint over another photo's Edit/Undo button.
        m = self._measure(self.strip_path, 412, "?n=6&undo=0,1,2,3,4,5")
        self.assertEqual(len(m["wraps"]), 6)
        # On the strip every photo has an Edit button below it AND an
        # Undo button when edited — both are .thumb-edit-below, so
        # tell them apart by their text.
        self.assertEqual(m["undoTexts"].count("Edit"), 6)
        self.assertEqual(m["undoTexts"].count("Undo edit"), 6)
        self._assert_flows(m, "draft strip, Edit+Undo on all, 412px",
                           grid_page=False)
        for i, x in enumerate(m["xs"]):
            hit = [j for j, b in enumerate(m["undos"])
                   if _overlaps(x, b)]
            self.assertEqual(hit, [],
                             "X %d overlaps an Undo button: %s" % (i, hit))


if __name__ == "__main__":
    unittest.main()
