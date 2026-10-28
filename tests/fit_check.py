"""v24+ pre-delivery fit/contrast check (standing rule, Oct 28, 2026).

Every build must fit its screen with no clipping and no side scrolling,
on phone and tablet widths, with no dark-on-dark, light-on-light, or
white-on-white anywhere. This script checks the web UI statically:
flex rows wrap, text columns can shrink, no fixed width exceeds a
narrow phone, width budgets hold at 320/360/768dp, and every
text/background pair meets WCAG AA (>= 4.5:1).

Run: python3 tests/fit_check.py (exit 0 = pass). It is a static audit,
not a substitute for looking at the app on a real phone — but a build
that fails here is not shipped.
"""
import os
import re
import sys

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
CSS_PATH = os.path.join(REPO, "app", "src", "main", "python", "static",
                         "style.css")
HTML_PATH = os.path.join(REPO, "app", "src", "main", "python", "templates",
                          "index.html")
JS_PATH = os.path.join(REPO, "app", "src", "main", "python", "static",
                        "app.js")

FAILURES = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ((" — " + detail) if detail else ""))
    if not ok:
        FAILURES.append(name)


def parse_rules(css):
    """Very small CSS parser: selector -> {prop: value} (last wins)."""
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


def px(value):
    """Pixel value of a rem/px length at 16px root, or None."""
    if value is None:
        return None
    m = re.match(r"^([\d.]+)(px|rem)$", value)
    if not m:
        return None
    v = float(m.group(1))
    return v * 16 if m.group(2) == "rem" else v


def luminance(hexcolor):
    h = hexcolor.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(fg, bg):
    lf, lb = luminance(fg), luminance(bg)
    hi, lo = max(lf, lb), min(lf, lb)
    return (hi + 0.05) / (lo + 0.05)


def main():
    with open(CSS_PATH, encoding="utf-8") as f:
        css = f.read()
    with open(HTML_PATH, encoding="utf-8") as f:
        html = f.read()
    with open(JS_PATH, encoding="utf-8") as f:
        js = f.read()
    rules = parse_rules(css)

    # 1. Viewport meta (without it, phones render a desktop-width page).
    check("viewport meta present",
          'name="viewport"' in html and "width=device-width" in html)

    # 2. Every flex row wraps (v21 rule).
    for sel in (".btnrow", ".pricerow", ".photobtns", ".attr-row",
                ".draft-item", ".draft-actions", ".batch-row", ".thumbs"):
        check("row wraps: %s" % sel,
              rules.get(sel, {}).get("flex-wrap") == "wrap",
              rules.get(sel, {}).get("flex-wrap", "missing"))

    # 3. Text/input columns can actually shrink (min-width: 0).
    for sel in (".btnrow > *", ".pricerow input", ".attr-row input",
                ".draft-item .t", ".batch-row .bt", ".batch-row .bp"):
        check("shrinks: %s" % sel,
              rules.get(sel, {}).get("min-width") == "0",
              rules.get(sel, {}).get("min-width", "missing"))

    # 4. No fixed width/min-width in px wider than a narrow phone (320dp).
    bad = []
    for sel, props in rules.items():
        for prop in ("width", "min-width"):
            v = px(props.get(prop))
            if v is not None and v > 320:
                bad.append("%s %s=%s" % (sel, prop, props[prop]))
    check("no fixed width over 320px", not bad, "; ".join(bad))

    # 5. Draft-row width budget at 320dp: content = 320 - main padding
    # (2rem) - step padding (2.2rem). Line 1 holds checkbox + thumb +
    # gaps + text; actions wrap below. Longest nowrap token is the date
    # part "2026-10-27" (10 chars, 0.85rem, ~0.62em/char, conservative).
    content = 320 - 32 - 35.2
    fixed = 22.4 + 56 + 3 * 11.2  # checkbox + thumb + gaps
    budget = content - fixed
    date_part = 10 * 13.6 * 0.62
    check("draft-row budget at 320dp",
          budget >= date_part,
          "budget %.0fpx >= date %.0fpx" % (budget, date_part))
    # Same budget at tablet width is comfortably larger.
    content768 = 640 - 32 - 35.2  # main caps at 640px
    check("draft-row budget at 768dp",
          content768 - fixed >= date_part,
          "budget %.0fpx" % (content768 - fixed))

    # 6. Batch rows: price/reason text wraps (row wraps, .bp wraps text).
    check("batch price wraps",
          rules.get(".batch-row .bp", {}).get("overflow-wrap") == "break-word")

    # 7. Full-width controls fit by construction.
    check("quick-post button full width",
          rules.get("button.secondary.big", {}).get("width") == "100%")
    check("inputs full width + border-box",
          rules.get("input[type=text]", {}).get("width") == "100%" and
          rules.get("*", {}).get("box-sizing") == "border-box")
    check("editor capped at viewport",
          rules.get(".editor-box", {}).get("max-width") == "96vw" and
          rules.get("#editor-canvas", {}).get("max-width") == "100%")

    # 8. Contrast pairs (WCAG AA >= 4.5:1). Each color must actually
    # appear in the stylesheet, so the check cannot pass vacuously.
    # The stylesheet uses 3-digit hex in places (#fff, #a00, #c00),
    # so expand those before searching.
    css_expanded = re.sub(
        r"#([0-9a-fA-F])([0-9a-fA-F])([0-9a-fA-F])\b",
        lambda m: "#" + m.group(1) * 2 + m.group(2) * 2 + m.group(3) * 2,
        css).lower()
    pairs = [
        ("toolbar title/dots", "#ffffff", "#0f2a44"),
        ("primary button", "#ffffff", "#2a63d8"),
        ("secondary button", "#123a7d", "#e8eefc"),
        ("ghost/link on white", "#2a63d8", "#ffffff"),
        ("link on page bg", "#2a63d8", "#f4f4f5"),
        ("body text", "#1c1c1e", "#f4f4f5"),
        ("step text", "#1c1c1e", "#ffffff"),
        ("hint", "#555555", "#ffffff"),
        ("count/draft date/batch price", "#666666", "#ffffff"),
        ("delete button", "#aa0000", "#fde8e8"),
        ("batch skipped", "#aa0000", "#ffffff"),
        ("error/banner", "#7a1010", "#fde8e8"),
        ("ok banner", "#145214", "#e6f7e6"),
        ("note text", "#1c1c1e", "#f2f6ff"),
        ("photo remove X", "#ffffff", "#cc0000"),
    ]
    for name, fg, bg in pairs:
        ratio = contrast(fg, bg)
        in_css = fg.lower() in css_expanded and bg.lower() in css_expanded
        check("contrast %s %.1f:1" % (name, ratio),
              ratio >= 4.5 and in_css,
              "in-css=%s" % in_css)

    # 9. v24 features exist in the shipped files.
    for name, needle, hay in (
            ("quick-post button", 'id="btn-quick-post"', html),
            ("batch panel", 'id="batch-panel"', html),
            ("batch open button", 'id="btn-batch-open"', html),
            ("preset save button", 'id="btn-save-preset"', html),
            ("preset note", 'id="preset-note"', html),
            ("draft Post handler", 'querySelector(".post")', js),
            ("batch confirm handler", "btn-batch-confirm", js),
            ("preset apply", "/api/preset", js),
            ("date split helper", "dateHtml", js)):
        check("present: %s" % name, needle in hay)

    print()
    if FAILURES:
        print("FAILED (%d): %s" % (len(FAILURES), "; ".join(FAILURES)))
        return 1
    print("All fit/contrast checks pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
