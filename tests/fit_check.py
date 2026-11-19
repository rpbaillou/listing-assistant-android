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
import json
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
PY_PATH = os.path.join(REPO, "app", "src", "main", "python", "app.py")
GRADLE_PATH = os.path.join(REPO, "app", "build.gradle.kts")
VERSION_PATH = os.path.join(REPO, "version.json")

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
    with open(PY_PATH, encoding="utf-8") as f:
        py = f.read()
    rules = parse_rules(css)

    # 1. Viewport meta (without it, phones render a desktop-width page).
    check("viewport meta present",
          'name="viewport"' in html and "width=device-width" in html)

    # 2. Every flex row wraps (v21 rule).
    for sel in (".btnrow", ".pricerow", ".photobtns", ".attr-row",
                ".draft-item", ".draft-actions", ".batch-row", ".thumbs",
                ".ship-row"):
        check("row wraps: %s" % sel,
              rules.get(sel, {}).get("flex-wrap") == "wrap",
              rules.get(sel, {}).get("flex-wrap", "missing"))

    # 3. Text/input columns can actually shrink (min-width: 0).
    for sel in (".btnrow > *", ".pricerow input", ".attr-row input",
                ".draft-item .t", ".batch-row .bt", ".batch-row .bp",
                ".ship-row > *", ".ship-field"):
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
    # (2rem) - step padding (2.2rem). Since v48 the thumb is 128px, so
    # line 1 holds checkbox + thumb + gaps only; the title/date block
    # and the actions wrap below (the row flex-wraps by design, see
    # check 13). The date part "2026-10-27" (10 chars, 0.85rem,
    # ~0.62em/char, conservative) fits on its own wrapped line, which
    # gets the full content width.
    content = 320 - 32 - 35.2
    fixed = 22.4 + 128 + 3 * 11.2  # checkbox + thumb + gaps
    check("draft-row budget at 320dp",
          content >= fixed,
          "content %.0fpx >= line-1 fixed %.0fpx" % (content, fixed))
    date_part = 10 * 13.6 * 0.62
    check("draft-row date fits on its wrapped line",
          content >= date_part,
          "content %.0fpx >= date %.0fpx" % (content, date_part))
    # Same budget at tablet width is comfortably larger.
    content768 = 640 - 32 - 35.2  # main caps at 640px
    check("draft-row budget at 768dp",
          content768 >= fixed,
          "content %.0fpx >= line-1 fixed %.0fpx" % (content768, fixed))

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
        # v49: the drafts-list Delete is a grey button now (design 4),
        # not the old red one. The red #aa0000 on white pair still
        # lives on in "batch skipped" below.
        ("draft delete grey", "#1c1c1e", "#ececef"),
        ("batch skipped", "#aa0000", "#ffffff"),
        ("error/banner", "#7a1010", "#fde8e8"),
        ("ok banner", "#145214", "#e6f7e6"),
        ("note text", "#1c1c1e", "#f2f6ff"),
        ("photo remove X", "#ffffff", "#cc0000"),
        # v42: the open option list of every native select (Android
        # draws it on a white sheet; the rule keeps it readable).
        ("select options", "#ffffff", "#1c1c1e"),
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

    # 10. v42 shipping flexibility: the carrier/service pickers and
    # the lb/oz weight boxes exist; the package section is never
    # hidden; the select-option safety net is a real rule (not just
    # colors that happen to appear elsewhere).
    for name, needle, hay in (
            ("carrier picker", 'id="ship-carrier"', html),
            ("service picker", 'id="ship-service"', html),
            ("pounds box", 'id="p-weight-lb"', html),
            ("ounces box", 'id="p-weight-oz"', html),
            ("package length", 'id="p-length"', html),
            ("carrier find endpoint", "/api/ebay/policy-for-service", js),
            ("carrier service labels", "carrierServiceLabels", js),
            ("lb/oz restore", "draftPackageLbOz", js),
            ("old single weight box gone", 'id="p-weight"', html)):
        if name == "old single weight box gone":
            check("present: %s" % name, needle not in hay)
        else:
            check("present: %s" % name, needle in hay)
    check("package section never hidden",
          not re.search(r'id="shipping-package"[^>]*class="[^"]*hidden',
                        html))
    opt_rule = rules.get("select option", {})
    check("select option rule is dark bg + light text",
          opt_rule.get("background") == "#1c1c1e" and
          opt_rule.get("color") in ("#ffffff", "#fff"),
          str(opt_rule))

    # 11. v46 drafts list sections: the Listed section toggle exists in
    # the shipped JS and CSS, is a 44px tap target, and reuses the
    # verified body-text-on-white contrast pair.
    for name, needle, hay in (
            ("draft sections", "Listed on eBay (", js),
            ("section toggle style", ".draft-section-toggle", css),
            ("section header style", ".draft-section h3", css)):
        check("present: %s" % name, needle in hay)
    check("section toggle is a 44px tap target",
          rules.get(".draft-section-toggle", {}).get("min-height") == "44px",
          str(rules.get(".draft-section-toggle", {})))
    ratio = contrast("#1c1c1e", "#ffffff")
    check("contrast draft section header %.1f:1" % ratio,
          ratio >= 4.5 and "#1c1c1e" in css_expanded and
          "#ffffff" in css_expanded,
          "in-css=%s" % ("#1c1c1e" in css_expanded and
                         "#ffffff" in css_expanded))

    # 12. v48 character counters on the create-policy form: the two
    # capped fields show a live count in the same .count style as the
    # title counter, so he never has to guess a length. Label + counter
    # must fit on one line at the narrowest phone: content = 320 - main
    # padding (2rem) - step padding (2.2rem). Label text is 17px bold
    # (~0.52em/char, conservative); the counter is 0.85rem (~0.55em/
    # char); the at-limit counter is the worst case ("64/64", "250/250").
    # The description label is just "Description" (the "(optional)"
    # moved to the placeholder) so the pair fits at 320dp.
    for name, needle, hay in (
            ("name counter span", 'id="pc-name-count" class="count">0/64<', html),
            ("description counter span", 'id="pc-description-count" class="count">0/250<', html),
            ("counter updater", "updatePolicyCreateCounts", js)):
        check("present: %s" % name, needle in hay)
    content320 = 320 - 32 - 35.2
    label_w = lambda chars: chars * 17 * 0.52
    count_w = lambda chars: chars * 13.6 * 0.55
    space_w = 17 * 0.28
    name_line = label_w(len("Policy name")) + space_w + count_w(len("64/64"))
    desc_line = label_w(len("Description")) + space_w + count_w(len("250/250"))
    check("name label + counter fits at 320dp",
          name_line <= content320,
          "line %.0fpx <= content %.0fpx" % (name_line, content320))
    check("description label + counter fits at 320dp",
          desc_line <= content320,
          "line %.0fpx <= content %.0fpx" % (desc_line, content320))
    check("counter spans reuse the .count style (contrast already checked)",
          rules.get(".count", {}).get("color") == "#666",
          str(rules.get(".count", {})))

    # 13. v48 one thumbnail size for every photo slot: --thumb-size
    # 128px, read by the step-1 strip, the listing-page draft strip,
    # and the drafts-list rows. No stale small size remains.
    check("shared thumbnail size defined at 128px",
          re.search(r"--thumb-size\s*:\s*128px", css) is not None)
    for sel in (".thumbs img", ".thumbs .thumb-skip", ".thumb-wrap",
                ".thumb-wrap img", ".thumb-img", ".draft-item img",
                ".batch-row img"):
        # .thumb-wrap sets only the width; the img inside sets the
        # height, so height is not expected on that selector.
        width_ok = rules.get(sel, {}).get("width") == "var(--thumb-size)"
        height_ok = (sel == ".thumb-wrap" or
                     rules.get(sel, {}).get("height") == "var(--thumb-size)")
        check("thumbnail slot shares the size: %s" % sel,
              width_ok and height_ok,
              str(rules.get(sel, {})))
    check("no stale .thumbs.small override remains",
          not any(".thumbs.small" in sel for sel in rules))
    # Drafts-list row at 320dp with a 128px thumb: line 1 holds the
    # checkbox (1.4rem) + thumb + gaps (2 x 0.7rem); the title/date
    # block (flex-basis 10rem, min-width 0) wraps below instead of
    # clipping. The row already flex-wraps (check 2) and the text
    # column can shrink (check 3).
    row_fixed = 22.4 + 128 + 2 * 11.2
    check("drafts row with 128px thumb fits at 320dp",
          row_fixed <= content320,
          "thumb+checkbox+gaps %.0fpx <= content %.0fpx" % (row_fixed, content320))
    content360 = 360 - 32 - 35.2
    check("drafts row with 128px thumb fits at 360dp",
          row_fixed <= content360,
          "thumb+checkbox+gaps %.0fpx <= content %.0fpx" % (row_fixed, content360))

    # 14. v49 Diffui design 4 on all four screens (look-and-layout
    # only): screen headers with a 44px back arrow and a centered
    # title; step-1 photos in a 2x2 grid of uniform square thumbs
    # (the deliberate exception to the shared-size rule — the grid
    # cells fill their column); the listing page's collapsed rows
    # with live summaries; drafts Open outlined and Delete grey;
    # the batch confirm card. Currency is $ everywhere — the
    # mockups' £ and ₹ never reach the app.
    for name, needle, hay in (
            ("back arrow step 1", 'id="btn-back-photos"', html),
            ("back arrow listing page", 'id="btn-back-draft"', html),
            ("photo grid class", 'id="thumbs" class="thumbs photo-grid"', html),
            ("shipping collapsed row", 'id="row-shipping" class="collapse-row"', html),
            ("item specifics collapsed row", 'id="row-attrs" class="collapse-row"', html),
            ("policy numbers collapsed row", 'id="row-policies" class="collapse-row"', html),
            ("manual collapsed row", 'id="row-manual" class="collapse-row"', html),
            ("shipping summary span", 'id="shipping-summary" class="cr-sub"', html),
            ("item specifics summary span", 'id="attrs-summary" class="cr-sub"', html),
            ("batch confirm card", 'id="batch-confirm" class="card batch-confirm"', html),
            ("batch confirm heading", 'id="batch-confirm-heading"', html),
            ("List button wording", ">List it on eBay now<", html),
            ("analyze button wording", ">Draft my listing with AI<", html),
            ("batch category label", "Category for the whole batch", html),
            ("batch category placeholder", 'placeholder="e.g. 123456"', html),
            ("shipping summary updater", "updateShippingSummary", js),
            ("attrs summary updater", "updateAttrsSummary", js),
            ("batch confirm updater", "updateBatchConfirm", js),
            ("row opener", "openListingRow", js),
            ("take-another gone from HTML", "btn-take-another", html),
            ("take-another gone from JS", "btn-take-another", js)):
        if name.startswith("take-another gone"):
            check("present: %s" % name, needle not in hay)
        else:
            check("present: %s" % name, needle in hay)
    check("no pound or rupee sign anywhere in the app",
          "£" not in html and "₹" not in html and
          "£" not in js and "₹" not in js)
    check("back arrow is a 44px tap target",
          rules.get(".screen-head .back", {}).get("min-height") == "44px" and
          rules.get(".screen-head .back", {}).get("width") == "44px",
          str(rules.get(".screen-head .back", {})))
    check("screen-head title centers",
          rules.get(".screen-head h2", {}).get("text-align") == "center",
          str(rules.get(".screen-head h2", {})))
    check("photo grid is two equal columns",
          rules.get(".thumbs.photo-grid", {}).get("grid-template-columns") == "1fr 1fr",
          str(rules.get(".thumbs.photo-grid", {})))
    check("photo grid cells are square",
          rules.get(".thumbs.photo-grid .thumb-wrap", {}).get("aspect-ratio") == "1 / 1",
          str(rules.get(".thumbs.photo-grid .thumb-wrap", {})))
    check("collapsed row summary is a 48px tap target",
          rules.get(".collapse-row summary", {}).get("min-height") == "48px",
          str(rules.get(".collapse-row summary", {})))
    check("collapsed row summary line is readable",
          rules.get(".collapse-row .cr-sub", {}).get("color") == "#666",
          str(rules.get(".collapse-row .cr-sub", {})))
    check("listing Edit below photo is a real button, min-height 26px",
          rules.get(".thumb-edit-below", {}).get("min-height") == "26px" and
          rules.get(".thumb-edit-below", {}).get("color") == "#2a63d8",
          str(rules.get(".thumb-edit-below", {})))
    check("drafts Open is the outlined button",
          rules.get(".draft-actions .secondary", {}).get("background") == "#fff" and
          rules.get(".draft-actions .secondary", {}).get("color") == "#2a63d8",
          str(rules.get(".draft-actions .secondary", {})))
    check("drafts Delete is grey, not red",
          rules.get(".draft-item .del", {}).get("background") == "#ececef" and
          rules.get(".draft-item .del", {}).get("color") == "#1c1c1e",
          str(rules.get(".draft-item .del", {})))
    check("batch title is big and bold",
          rules.get("#batch-panel > h2", {}).get("font-size") == "1.7rem" and
          rules.get("#batch-panel > h2", {}).get("font-weight") == "800",
          str(rules.get("#batch-panel > h2", {})))
    check("batch confirm heading centers",
          rules.get(".batch-confirm h3", {}).get("text-align") == "center",
          str(rules.get(".batch-confirm h3", {})))
    # The grid at 320dp: two columns share the content width
    # (252.8px) minus the 0.5rem gap, so each cell is ~122px square
    # — large thumbs that still fit.
    grid_cell = (content320 - 8) / 2
    check("photo grid cells fit at 320dp",
          grid_cell >= 100,
          "cell %.0fpx" % grid_cell)
    # The collapsed-row summary line at 320dp: icon (24px) + gaps +
    # chevron (~22px) leave room for the title + summary to wrap
    # below the icon row rather than clip — the summary wraps
    # (overflow-wrap) and the row is a flex item with min-width 0.
    check("collapsed summary can shrink and wrap",
          rules.get(".collapse-row .cr-text", {}).get("min-width") == "0" and
          rules.get(".collapse-row .cr-sub", {}).get("overflow-wrap") == "break-word",
          str(rules.get(".collapse-row .cr-text", {})))

    # 15. v50 resume card on step 1: a plain white card (not an error
    # banner) with the draft's name, a blue Continue button and a
    # small dismiss. It starts hidden and only shows when the server
    # remembers an open draft. The text shrinks and wraps, the
    # buttons wrap below it on a narrow phone — nothing clips.
    for name, needle, hay in (
            ("resume card", 'id="resume-card" class="hidden"', html),
            ("resume text", 'id="resume-text"', html),
            ("resume Continue is the primary button", 'id="btn-resume" class="primary"', html),
            ("resume dismiss has a label", 'id="btn-resume-dismiss" class="ghost" type="button" aria-label="Dismiss"', html),
            ("resume check on launch", "checkResumeDraft", js),
            ("resume card builder", "showResumeCard", js),
            ("resume open", "openResumeDraft", js),
            ("resume dismiss", "dismissResumeCard", js)):
        check("present: %s" % name, needle in hay)
    check("resume card is a flex row that wraps",
          rules.get("#resume-card", {}).get("display") == "flex" and
          rules.get("#resume-card", {}).get("flex-wrap") == "wrap",
          str(rules.get("#resume-card", {})))
    check("resume card is a white card, not an error banner",
          rules.get("#resume-card", {}).get("background") == "#fff",
          str(rules.get("#resume-card", {})))
    check("resume text shrinks and wraps",
          rules.get("#resume-text", {}).get("flex") == "1 1 10rem" and
          rules.get("#resume-text", {}).get("min-width") == "0" and
          rules.get("#resume-text", {}).get("overflow-wrap") == "break-word",
          str(rules.get("#resume-text", {})))
    # At 320dp the text flex-basis is 10rem (160px) of a 252.8px
    # content width; the buttons wrap below it when they cannot fit
    # beside it, so the card never overflows — flex-wrap is the fit.
    check("resume card wraps instead of overflowing at 320dp",
          rules.get("#resume-card", {}).get("flex-wrap") == "wrap" and
          px(rules.get("#resume-text", {}).get("flex", "").split()[2]
             if len(rules.get("#resume-text", {}).get("flex", "").split()) == 3
             else None) is not None and
          px(rules.get("#resume-text", {}).get("flex").split()[2]) <= content320,
          "text basis %s <= content %.0fpx" % (
              rules.get("#resume-text", {}).get("flex"), content320))

    # 16. v51 combined price check: one white card with two halves.
    # Sold half opens eBay's title-based sold search in the pop-up
    # (same URL as before); image half renders similar active
    # listings from the first photo, thumb + title + price + a
    # "Use this price" button per row. Rows wrap, the title column
    # shrinks below its basis (min-width 0), the price never wraps,
    # and the thumb is a fixed 72px reference image — never the
    # shared 128px photo size (see tests/test_thumbnail_size.py).
    # At 320dp the fixed floor (72px thumb + 16px padding + two
    # 0.6rem gaps + an 80px price allowance) fits the 252.8px
    # content width with room to spare; the button wraps below
    # when it cannot fit beside the price.
    for name, needle, hay in (
            ("results section", 'id="sold-results" class="card hidden"', html),
            ("sold half heading", "What they sold for", html),
            ("sold half button", 'id="btn-solds-open"', html),
            ("image half heading", "Similar items listed now", html),
            ("similar status", 'id="similar-status"', html),
            ("similar list", 'id="similar-list"', html),
            ("similar note", 'id="similar-note"', html),
            ("price pick note", 'id="sold-price-note"', html),
            ("similar loader", "loadSimilarItems", js),
            ("similar renderer", "renderSimilarItems", js),
            ("similar price pick", "useSimilarPrice", js),
            ("sold search still title based", "LH_Sold=1", js)):
        check("present: %s" % name, needle in hay)
    check("similar row is a flex row that wraps",
          rules.get(".similar-row", {}).get("display") == "flex" and
          rules.get(".similar-row", {}).get("flex-wrap") == "wrap",
          str(rules.get(".similar-row", {})))
    check("similar thumb is a fixed 72px reference image",
          rules.get(".similar-row img", {}).get("width") == "72px" and
          rules.get(".similar-row img", {}).get("height") == "72px",
          str(rules.get(".similar-row img", {})))
    check("similar title column shrinks and wraps",
          rules.get(".similar-row .st", {}).get("flex") == "1 1 10rem" and
          rules.get(".similar-row .st", {}).get("min-width") == "0" and
          rules.get(".similar-row .st", {}).get("overflow-wrap") == "break-word",
          str(rules.get(".similar-row .st", {})))
    check("similar price never wraps, dark on white",
          rules.get(".similar-row .sp", {}).get("white-space") == "nowrap" and
          rules.get(".similar-row .sp", {}).get("color") == "#1c1c1e",
          str(rules.get(".similar-row .sp", {})))
    check("results headings are dark on white",
          rules.get("#sold-results h4", {}).get("color") == "#1c1c1e",
          str(rules.get("#sold-results h4", {})))
    floor = (px(rules.get(".similar-row img", {}).get("width")) or 0) + 16 + 2 * 9.6 + 80
    check("similar row floor fits at 320dp",
          floor <= content320,
          "floor %.0fpx <= content %.0fpx" % (floor, content320))
    # v51 (aspect length): the live n/65 counter on each item-specifics
    # value field uses the same .count style as the v48 policy
    # counters (#666 on white, 5.7:1). It sits beside the value input,
    # vertically centered, never shrinks, and the row wraps before
    # anything clips.
    for name, needle, hay in (
            ("attrs counter span", "av-count", js),
            ("attrs counter updater", "updateAttrCount", js)):
        check("present: %s" % name, needle in hay)
    check("attrs counter is vertically centered and never shrinks",
          rules.get(".attr-row .av-count", {}).get("align-self") == "center" and
          rules.get(".attr-row .av-count", {}).get("flex-shrink") == "0",
          str(rules.get(".attr-row .av-count", {})))
    check("attrs row wraps",
          rules.get(".attr-row", {}).get("flex-wrap") == "wrap",
          str(rules.get(".attr-row", {})))

    # 17. v51 List button move: the whole publish block sits at the
    # bottom of the listing page, directly above Or list manually.
    # Exactly one List button exists, the error/ok feedback and the
    # spinner sit with it in the new spot (after the scope-reconnect
    # box, before the manual row), and the big button is width 100%
    # so it fits at 320dp with no fixed width to overflow.
    pub = html.find('id="btn-publish"')
    scope_box = html.find('id="scope-reconnect-box"')
    manual = html.find('id="row-manual"')
    check("exactly one List button", html.count('id="btn-publish"') == 1)
    check("List button sits above Or list manually",
          0 < pub < manual,
          "button at %d, manual row at %d" % (pub, manual))
    check("List button sits below the collapsed rows",
          html.find('id="row-shipping"') < pub and scope_box < pub)
    for name, needle, before in (
            ("publish error", 'id="publish-error"', True),
            ("publish ok", 'id="publish-ok"', True),
            ("publish spinner", 'id="publish-spinner"', False)):
        pos = html.find(needle)
        ok = (scope_box < pos < pub) if before else (pub < pos < manual)
        check("publish feedback %s moves with the button" % name,
              ok, "%s at %d" % (name, pos))
    check("big List button is full width, fits at 320dp",
          rules.get("button.primary.big", {}).get("width") == "100%",
          str(rules.get("button.primary.big", {})))

    # 18. v53 status card tells the truth: after a token-death
    # failure the page re-runs the status check and the
    # not-connected card shows a Connect eBay button in a wrapping
    # row (the v22 narrow-safe rule), so it fits at 320dp with no
    # clipping; the card hint no longer says "in this browser".
    check("status card refreshes after a token-death message",
          "async function refreshEbayStatus()" in js and
          "async function refreshStatusIfTokenDied(text)" in js)
    check("token-death refresh is wired into the failure paths",
          js.count("refreshStatusIfTokenDied(") >= 12,
          "%d call sites" % js.count("refreshStatusIfTokenDied("))
    check("not-connected card has a Connect eBay button",
          "btn-ebay-connect" in js)
    check("Connect button sits in a wrapping row",
          "btnrow wrap" in js and
          rules.get(".btnrow", {}).get("flex-wrap") == "wrap",
          str(rules.get(".btnrow", {})))
    check("no 'in this browser' left in the UI",
          "in this browser" not in js,
          "%d occurrences" % js.count("in this browser"))

    # 19. v54 photo fixes: round X buttons, breathing room in the grid,
    # edited thumbnails that show the edit, a visible Saving note, and
    # one listing that never splits into two drafts.
    # The X is 26x26 with a 50% radius, but the global button rule
    # forces min-height 48px — without its own min-height 0 the X
    # renders 26 wide by 48 tall, an oval. The escape must be on the
    # rule itself (the global rule wins on specificity otherwise).
    x = rules.get(".thumb-x", {})
    check("X button escapes the global 48px min-height, stays round",
          x.get("min-height") == "0" and x.get("width") == "26px" and
          x.get("height") == "26px" and x.get("border-radius") == "50%",
          str(x))
    # The grid gap must clear BOTH overhangs: the X sticks out 8px
    # (top/right -8px) and the Edit pill sticks out 8px (bottom/left
    # -8px), so the gap has to beat 16px. 1.25rem is 20px at a 16px
    # root. The 2-column square grid and the drag indicators must
    # survive the gap change. (px() takes no sign, so negatives are
    # parsed here.)
    def neg_px(value):
        if value is None:
            return None
        m = re.match(r"^-?([\d.]+)(px|rem)$", value)
        if not m:
            return None
        v = float(m.group(1))
        return v * 16 if m.group(2) == "rem" else v
    gap = px(rules.get(".thumbs.photo-grid", {}).get("gap"))
    overhang = (abs(neg_px(rules.get(".thumb-x", {}).get("right")) or 0) +
                abs(neg_px(rules.get(".thumb-edit", {}).get("left")) or 0))
    check("photo grid gap clears both button overhangs",
          gap is not None and gap > overhang,
          "gap %.0fpx > overhang %.0fpx" % (gap or -1, overhang))
    check("photo grid stays 2 columns, square cells",
          rules.get(".thumbs.photo-grid", {}).get("grid-template-columns") == "1fr 1fr" and
          rules.get(".thumbs.photo-grid .thumb-wrap", {}).get("aspect-ratio") == "1 / 1",
          str(rules.get(".thumbs.photo-grid", {})))
    check("drag indicators survive the gap change",
          "box-shadow" in rules.get(".thumb-wrap.drop-before", {}) and
          "box-shadow" in rules.get(".thumb-wrap.drop-after", {}))
    # The Saving note exists in the page and starts hidden.
    check("Saving note exists and starts hidden",
          'id="save-note" class="hint hidden"' in html)
    # An edited photo revalidates: /photos/ answers no-cache, so the
    # draft page and the restore fetch always see the current file.
    serve = py[py.find("def serve_photo"):]
    serve = serve[:serve.find("\nif __name__")]
    check("edited photos revalidate (no-cache on /photos/)",
          "cache_control.no_cache = True" in serve and
          "cache_control.max_age = 0" in serve)
    # One listing never splits: intake waits on the launch restore,
    # appends retry the same draft, and a failed save keeps its id.
    check("photo intake waits on the launch restore",
          "resumeCheck = checkResumeDraft();" in js and
          js.count("await resumeCheck;") >= 3,
          "%d resumeCheck awaits" % js.count("await resumeCheck;"))
    check("appends retry the same draft on failure",
          "async function apiWithRetries(" in js and
          "apiWithRetries(\"/api/drafts/\" + autoDraftId + \"/photos\"" in js)
    check("a failed save keeps the same draft id",
          "keeps the SAME draft" in js and
          '"Draft not found"' in js)

    # 20. v55 Hide button on the price-check results card: the card
    # had no way to close once opened, so it sat as a big wall over
    # the category fields. A ghost Hide button sits in the card
    # header next to the heading; tapping it adds hidden to the
    # card, and Check sold prices re-opens it with a fresh check.
    # The header is a wrapping flex row (heading + button fit at
    # 320dp on one line, and wrap rather than clip if they ever
    # cannot), and the ghost button reuses the verified ghost
    # contrast pair (#2a63d8 on white, check 8).
    sold = html.find('id="sold-results"')
    hide = html.find('id="btn-solds-hide"')
    sold_open = html.find('id="btn-solds-open"')
    check("Hide button exists in the results card",
          0 < sold < hide < sold_open,
          "card at %d, Hide at %d, sold-open at %d" % (sold, hide, sold_open))
    check("Hide button is the ghost button, labeled Hide",
          'id="btn-solds-hide" class="ghost" type="button">Hide<' in html)
    check("Hide wiring adds hidden to the card",
          '"btn-solds-hide"' in js and
          '$("sold-results").classList.add("hidden")' in js)
    check("card header is a wrapping flex row",
          rules.get(".card-head", {}).get("display") == "flex" and
          rules.get(".card-head", {}).get("flex-wrap") == "wrap",
          str(rules.get(".card-head", {})))
    check("card heading takes the free space, never clips",
          rules.get(".card-head h3", {}).get("flex") == "1 1 auto" and
          rules.get(".card-head h3", {}).get("min-width") == "0",
          str(rules.get(".card-head h3", {})))
    check("Hide button is a 40px ghost tap target",
          rules.get("button.ghost", {}).get("min-height") == "40px",
          str(rules.get("button.ghost", {})))
    # Heading + Hide on one line at 320dp: "Price check" (11 chars,
    # h3 ~1.17em bold, ~0.52em/char) + the ghost button ("Hide",
    # 4 chars at 1rem, ~0.55em/char, plus 0.9rem padding each side
    # and a 2px border) + the 0.5rem header gap. The row wraps
    # anyway, so this is a fit check, not a clipping risk.
    head_w = 11 * 18.7 * 0.52
    hide_w = 4 * 16 * 0.55 + 2 * 14.4 + 2
    check("heading + Hide fit on one line at 320dp",
          head_w + hide_w + 8 <= content320,
          "line %.0fpx <= content %.0fpx" % (head_w + hide_w + 8, content320))

    # 21. v55: the APK's own version must match version.json. The
    # updater compares version.json's versionCode against the
    # installed app's manifest versionCode, and the header badge
    # shows the manifest versionName — so a release whose
    # build.gradle.kts was not bumped installs as the old version
    # and the updater offers it forever. v54 shipped exactly that
    # way (manifest 53/1.52, version.json 54/1.53). The gradle
    # version is the source of truth for the build; version.json
    # is what the updater reads, so the two must agree or the
    # build does not ship.
    with open(VERSION_PATH, encoding="utf-8") as f:
        ver = json.load(f)
    with open(GRADLE_PATH, encoding="utf-8") as f:
        gradle = f.read()
    check("gradle versionCode matches version.json",
          re.search(r"versionCode\s*=\s*%d\b" % ver["versionCode"],
                    gradle) is not None,
          "version.json says %d" % ver["versionCode"])
    check("gradle versionName matches version.json",
          re.search(r'versionName\s*=\s*"%s"' %
                    re.escape(ver["versionName"]), gradle) is not None,
          "version.json says %s" % ver["versionName"])

    # 22. v56 full in-app photo editor: Auto enhance, five adjustment
    # sliders, five filter chips, four crop presets, rotate, straighten,
    # flip, Reset, Apply/Cancel — one shared editor on both screens
    # (step 1 and the draft page), in pure web code. The draft Edit
    # button must open it, never the old Pixel handoff.
    editor_ids = ["edit-brightness", "edit-contrast", "edit-saturation",
                  "edit-warmth", "edit-sharpness", "edit-straighten",
                  "edit-brightness-val", "edit-contrast-val",
                  "edit-saturation-val", "edit-warmth-val",
                  "edit-sharpness-val", "edit-straighten-val",
                  "btn-edit-auto", "btn-edit-reset", "btn-edit-flip",
                  "btn-edit-apply", "btn-edit-cancel",
                  "btn-edit-rotl", "btn-edit-rotr"]
    check("editor controls all exist in the page",
          all('id="%s"' % i in html for i in editor_ids),
          "missing: %s" % [i for i in editor_ids if 'id="%s"' % i not in html])
    check("five filter chips, one per preset",
          html.count('class="edit-filter') == 5 and
          all('data-filter="%s"' % f in html
              for f in ("original", "vivid", "warm", "cool", "mono")),
          "chips: %d" % html.count('class="edit-filter'))
    check("four crop presets, three with an aspect ratio",
          html.count('class="edit-crop-preset') == 4 and
          all('data-ratio="%s"' % r in html
              for r in ("1", "1.3333", "1.7778")),
          "presets: %d" % html.count('class="edit-crop-preset'))
    check("Apply is the primary save button, labeled Apply",
          'id="btn-edit-apply" class="primary" type="button">Apply<' in html)
    check("draft Edit opens the in-app editor, never the handoff",
          "function openDraftEditor(" in js and
          "openDraftEditor(i)" in js and
          "editDraftPhotoInPixel" not in js and
          "window.Android.editDraftPhoto" not in js)
    check("all six sliders are bound to live input",
          all('bindEditorSlider("edit-%s"' % s in js
              for s in ("brightness", "contrast", "saturation",
                       "warmth", "sharpness", "straighten")))
    check("filter presets and auto enhance are wired",
          "FILTER_PRESETS" in js and
          '"btn-edit-auto"' in js and "autoEnhanceValues" in js)
    check("Reset restores defaults, Cancel closes without saving",
          '"btn-edit-reset"' in js and "defaultAdjustments()" in js and
          '"btn-edit-cancel"' in js)

    # 23. v56 editor fit: the box scrolls instead of clipping on a
    # phone, sliders are full-width with a 44px touch height, and the
    # active filter chip reuses the verified primary contrast pair.
    check("editor box scrolls inside the overlay",
          rules.get(".editor-box", {}).get("max-height") is not None and
          rules.get(".editor-box", {}).get("overflow-y") == "auto",
          str(rules.get(".editor-box", {})))
    check("range inputs are full-width, 44px touch height",
          rules.get('.edit-slider input[type="range"]', {}).get("width") == "100%" and
          rules.get('.edit-slider input[type="range"]', {}).get("min-height") == "44px",
          str(rules.get('.edit-slider input[type="range"]', {})))
    check("range inputs use the brand-blue accent",
          rules.get('.edit-slider input[type="range"]', {}).get("accent-color") == "#2a63d8",
          str(rules.get('.edit-slider input[type="range"]', {})))
    check("active filter chip is white on brand blue",
          rules.get('.edit-filter[aria-pressed="true"]', {}).get("background") == "#2a63d8" and
          rules.get('.edit-filter[aria-pressed="true"]', {}).get("color") == "#fff" and
          contrast("#fff", "#2a63d8") >= 4.5,
          "contrast %.1f:1" % contrast("#fff", "#2a63d8"))
    check("slider labels spread label and value apart",
          rules.get(".edit-slider label", {}).get("display") == "flex" and
          rules.get(".edit-slider label", {}).get("justify-content") == "space-between",
          str(rules.get(".edit-slider label", {})))

    # 24. v57 photos-only live-listing update: the Listed section rows
    # get an Update button (unlisted rows keep Post), the listing page
    # shows the Update button only for listed drafts and hides List
    # there, the confirm card lists the changes with a Confirm + Cancel
    # pair, and the server side is photos-only: the execute endpoint
    # recomputes the plan, requires confirmed:true, PUTs the item back
    # with only imageUrls swapped, reads it back to verify, and never
    # creates an offer or re-publishes. v64: the photos and quantity
    # updates merged into one button and one card — the row button
    # reads Update listing, the page button is Update live listing,
    # and the card names both parts. The single-part endpoints below
    # still exist (the merged flow reuses their apply helpers).
    check("listed rows get an Update listing button, unlisted keep Post",
          'class="secondary update-listing">Update listing<' in js and
          "(d.listed" in js and "Update photos" not in html,
          "row button missing from app.js")
    check("update button + confirm card exist in the page",
          all('id="%s"' % i in html for i in (
              "btn-update-listing", "revise-confirm", "revise-changes",
              "btn-revise-confirm", "btn-revise-cancel", "revise-ok",
              "revise-error", "revise-spinner")),
          "missing revise ids")
    check("update button starts hidden; List stays the default",
          'id="btn-update-listing" class="primary big hidden"' in html and
          'id="btn-publish" class="primary big"' in html)
    check("confirm card names the combined scope",
          "Only the photos and quantity change. Title, price, and shipping stay as they are." in html)
    check("status card shows Update only for listed drafts",
          'btn-update-listing").classList.remove("hidden")' in js and
          js.count('btn-update-listing").classList.add("hidden")') >= 3)
    check("revise endpoints exist (preview GET + execute POST)",
          '@app.route("/api/drafts/<draft_id>/revise-photos", methods=["GET"])' in py and
          '@app.route("/api/drafts/<draft_id>/revise-photos", methods=["POST"])' in py)
    check("execute requires confirmed:true before any eBay call",
          'if not body.get("confirmed"):' in py and
          'Confirm the photo update first.' in py)
    check("execute PUTs the item back with only imageUrls swapped",
          'product["imageUrls"] = new_urls' in py and
          'put_body = json.loads(json.dumps(plan["item"]))' in py)
    check("execute verifies by reading the item back and comparing",
          'after_urls != new_urls' in py and
          "did not show the new photos" in py)
    # v63: the slice ends at the next function definition, not the next
    # route — the revise-quantity helpers now sit between the photos
    # execute and the /photos/ route, and they legitimately touch the
    # offer (the quantity lives on it). The check still proves the
    # PHOTOS execute itself never creates an offer or re-publishes.
    check("execute never creates an offer or re-publishes",
          "/offer" not in py.split(
              "def api_revise_photos_execute")[1].split(
              "\ndef ")[0] and
          "/publish" not in py.split(
              "def api_revise_photos_execute")[1].split(
              "\ndef ")[0] and
          "_do_publish" not in py.split(
              "def api_revise_photos_execute")[1].split(
              "\ndef ")[0],
          "revise execute touches offers or publish")
    check("publish records the live photo list + hashes for later revise",
          'd["ebay_image_urls"] = list(image_urls)' in py and
          'd["ebay_photo_hashes"] = _draft_photo_hashes(photos)' in py)

    # 25. v58 editor fixes. (a) Straighten leaked into a fresh open
    # from the previous session: tapping Reset straightened the
    # photo, which proved the tilt was a stuck Straighten value, not
    # the photo and not the camera. The stale value survived Cancel
    # in the state and on the sliders, and any input event in the
    # window before the photo loaded read it back in. The open path
    # now zeroes the sliders synchronously before the image loads,
    # closing clears the leftover state, and the render reads state
    # only. (b) Crop corners are finger-sized (44px drawn and hit)
    # and the crop keeps its relative place in the frame, so undoing
    # straighten grows it back. (c) Auto enhance never brightens an
    # already-bright photo, and a failed probe leaves the photo
    # alone instead of a blind boost. Sliders also let a vertical
    # scroll pass through, so scrolling to them cannot drag
    # Straighten by accident.
    oi = js.find("function openEditorFor(blob, source) {")
    check("editor opens with the sliders zeroed before the photo loads",
          oi >= 0 and
          js.find("syncEditorControls();", oi) != -1 and
          js.find("syncEditorControls();", oi) <
          js.find("const url = URL.createObjectURL(blob);", oi),
          "sync must run before the Image is created")
    cbody = js.split("function closeEditor() {")[1].split(
        '$("btn-edit-cancel")')[0]
    check("closing the editor clears the leftover adjustments",
          "editStraighten = 0;" in cbody and
          "syncEditorControls();" in cbody,
          "closeEditor must reset state and sliders")
    check("the render reads rotation from state, never the sliders",
          "ctx.rotate((editRot + editStraighten) * Math.PI / 180);" in js and
          '$("edit-straighten").value' not in js,
          "rotation must come from editStraighten state")
    check("crop corners are finger-sized in the drawing and the hit test",
          "const hs = 22;" in js and
          "const tol = 44 * canvas.width / r.width;" in js,
          "handles and tolerance must be 44px")
    check("straightening keeps the crop's relative place so it grows back",
          "function remapCropToFrame(" in js and
          "remapCropToFrame(editCrop, before, editVisibleFrame(), EDIT_MIN_CROP)" in js,
          "straighten handler must remap, not only clamp")
    check("Auto enhance never brightens an already-bright photo",
          "if (mean >= 118) brightness = Math.min(brightness, 0);" in js,
          "bright photos must never gain brightness")
    check("a failed Auto enhance probe leaves the photo alone",
          "brightness: 8, contrast: 12" not in js and
          "Auto enhance could not read the photo. Nothing changed." in js,
          "no blind boost when the probe fails")
    check("editor sliders let a vertical scroll pass through",
          rules.get('.edit-slider input[type="range"]', {}).get("touch-action") == "pan-y",
          str(rules.get('.edit-slider input[type="range"]', {})))

    # 26. v59 editor feedback + undo + stale-app.js. (a) The preview
    # used to scroll off the top when he went down to the buttons,
    # so Auto enhance looked dead until Apply, when the change was
    # already baked in. The preview now sticks to the top of the
    # panel while the controls scroll beneath it, and a status line
    # says what each button just did. Taps draw right away instead
    # of only on the next animation frame, and Apply says Saving
    # while it works. (b) Apply had no undo: the pre-edit photo is
    # stashed now, and an Undo edit button on the photo puts the
    # exact original back, on both screens. (c) The tilt "came back"
    # after v58 because Flask's static route answers with a 12h
    # max-age and the WebView does not clear its HTTP cache on an
    # app update, so the badge said 1.57 while the editor still ran
    # the cached v56 app.js. The page and /static/* now revalidate
    # on every load.
    check("editor preview sticks to the top of the panel",
          rules.get(".editor-preview", {}).get("position") == "sticky" and
          rules.get(".editor-preview", {}).get("top") == "0" and
          rules.get(".editor-preview", {}).get("background") == "#fff",
          str(rules.get(".editor-preview", {})))
    check("editor status line exists in the page and is dark on white",
          'id="editor-status"' in html and
          rules.get(".editor-status", {}).get("color") == "#1c1c1e" and
          contrast("#1c1c1e", "#fff") >= 4.5,
          "contrast %.1f:1" % contrast("#1c1c1e", "#fff"))
    check("every editor action reports itself on the status line",
          js.count("setEditorStatus(") >= 10 and
          '"btn-edit-auto"' in js and "autoEnhanceStatus(" in js,
          "status must be set after open, sliders, filters, crop, rotate, flip, auto, reset")
    check("Auto enhance and filter taps draw right away, not only on rAF",
          '"btn-edit-auto"' in js and
          js.split('function autoEnhanceStatus(')[1].split(
              '$("btn-edit-reset")')[0].count("drawEditor();") >= 1 and
          js.split('document.querySelectorAll(".edit-filter")')[-1].split(
              'document.querySelectorAll(".edit-crop-preset")')[0].count(
              "drawEditor();") >= 1,
          "taps must draw synchronously so the preview visibly changes")
    check("Apply says Saving while it works and restores Apply after",
          'btn.textContent = "Saving…";' in js and
          'btn.textContent = "Apply";' in js,
          "Apply must show its working state")
    check("Apply stashes the pre-edit photo for Undo on both screens",
          "editSourceBlob = blob;" in js and
          "entry._undoBlob = beforeBlob;" in js and
          "draftUndoByRel[editSource.rel] = beforeBlob;" in js,
          "the stash must hold the exact pre-edit bytes")
    check("Undo restores the exact original and is offered on the photo",
          "function undoStep1Edit(" in js and
          "function undoDraftEdit(" in js and
          'u.textContent = "Undo edit";' in js and
          js.count('u.textContent = "Undo edit";') >= 2,
          "both screens need a one-tap Undo on the photo")
    check("the app shell revalidates instead of serving a cached update",
          'request.path == "/" or request.path.startswith("/static/")' in py and
          "response.cache_control.no_cache = True" in py and
          "response.cache_control.max_age = 0" in py,
          "page and /static/* must send no-cache")

    # 27. v60 editor redesign: one tool at a time, behind icons
    # (standing rule, Nov 18). The old layout jammed every control
    # onto one long scrolling page, and scrolling down to Apply kept
    # hitting a slider by accident. A toolbar of four tools sits
    # under the preview; tapping one opens only that tool's panel.
    # Crop opens by default, since cropping is most of what he does,
    # and the crop handles only live in Crop mode. Apply and Cancel
    # sit in a bar stuck to the bottom of the panel, always in reach.
    tools = html.split('<div class="editor-tools"')[1].split("</div>")[0]
    check("editor toolbar holds four tools, Crop pressed by default",
          tools.count('class="edit-tool') == 4 and
          all('data-tool="%s"' % t in tools
              for t in ("adjust", "crop", "rotate", "filters")) and
          'data-tool="crop" type="button" aria-pressed="true"' in tools and
          tools.count('aria-pressed="true"') == 1,
          "toolbar: %d tools" % tools.count('class="edit-tool'))
    check("four tool panels exist, Crop open and the rest hidden",
          html.count('class="edit-panel"') == 4 and
          all('data-panel="%s"' % t in html
              for t in ("adjust", "crop", "rotate", "filters")) and
          'class="edit-panel" data-panel="crop">' in html and
          all('class="edit-panel" data-panel="%s" hidden' % t in html
              for t in ("adjust", "rotate", "filters")),
          "panels: %d" % html.count('class="edit-panel"'))
    check("each tool's controls live inside its own panel",
          html.find('data-panel="adjust"') < html.find('id="edit-brightness"') and
          html.find('data-panel="crop"') < html.find('class="edit-crop-preset') and
          html.find('data-panel="rotate"') < html.find('id="edit-straighten"') and
          html.find('data-panel="filters"') < html.find('class="edit-filter'),
          "controls must sit inside their tool panels, not one flat list")
    actions = html.split('<div class="editor-actions')[1].split("</div>")[0]
    check("Apply, Cancel, and Reset sit in a fixed bar at the bottom",
          all('id="%s"' % i in actions
              for i in ("btn-edit-reset", "btn-edit-cancel", "btn-edit-apply")),
          "the bar must hold Reset, Cancel, Apply")
    check("the action bar sticks to the bottom of the panel",
          rules.get(".editor-actions", {}).get("position") == "sticky" and
          rules.get(".editor-actions", {}).get("bottom") == "0" and
          rules.get(".editor-actions", {}).get("background") == "#fff",
          str(rules.get(".editor-actions", {})))
    check("tool buttons are finger-sized and share the row evenly",
          px(rules.get(".edit-tool", {}).get("min-height")) is not None and
          px(rules.get(".edit-tool", {}).get("min-height")) >= 44 and
          rules.get(".edit-tool", {}).get("flex") == "1 1 0",
          str(rules.get(".edit-tool", {})))
    check("the active tool is white on brand blue",
          rules.get('.edit-tool[aria-pressed="true"]', {}).get("background") == "#2a63d8" and
          rules.get('.edit-tool[aria-pressed="true"]', {}).get("color") == "#fff" and
          contrast("#fff", "#2a63d8") >= 4.5,
          "contrast %.1f:1" % contrast("#fff", "#2a63d8"))
    check("hidden panels are never shown",
          rules.get(".edit-panel[hidden]", {}).get("display") == "none",
          str(rules.get(".edit-panel[hidden]", {})))
    check("Crop is the default tool on open and on close",
          js.count('editTool = "crop";') >= 2 and
          "let editTool = \"crop\";" in js,
          "openEditorFor and closeEditor must both reset to Crop")
    check("switching tools shows only that tool's panel",
          "function setEditorTool(" in js and
          "p.hidden = p.dataset.panel !== editTool;" in js and
          'document.querySelectorAll(".edit-tool")' in js,
          "setEditorTool must hide the other panels")
    check("crop handles only live in Crop mode",
          'if (editTool !== "crop") return;' in js,
          "pointerdown must ignore the canvas outside Crop mode")

    # 28. v61 clear the photos after a listing goes live. Twice he
    # took pictures, made a draft, and listed it, then went back to
    # start and found the old pictures still there — he had to delete
    # them to start a new set. The cause: postPublish on success only
    # showed "It is live!" and re-prefilled the listing; it never
    # cleared selectedFiles, never reset the auto draft id, never
    # cleared the resume. Only the New button did that reset. After
    # a confirmed publish the step-1 working set is now cleared the
    # same way — but only when the photos on step 1 are that draft's
    # photos (Analyze / Quick post record the draft id; a restored
    # auto draft is matched by its id), so listing a different draft
    # from My drafts while step 1 holds another set leaves that set
    # alone. The listing page is untouched: draft stays set and the
    # success note stays visible. The same reset runs when a batch
    # publishes the step-1 draft. The revise flow never resets.
    check("the step-1 draft is tracked from Analyze and Quick post",
          "let step1DraftId = null;" in js and
          js.count("step1DraftId = d.id;") == 4 and
          "step1DraftId = null; /* v61: New abandons the step-1 set too */" in js,
          "%d set sites" % js.count("step1DraftId = d.id;"))
    success = js.split("prefillListing({ keepPublishOk: true });")[1].split(
        'return "listed";')[0]
    check("a confirmed publish clears step 1 for its own draft only",
          "if (step1BelongsToDraft(draft.id)) resetStep1AfterPublish();" in success,
          "the reset must sit in the listed tail, after prefill")
    reset = js.split("function resetStep1AfterPublish() {")[1].split(
        "\nfunction ")[0]
    check("the reset clears the working set like New does",
          "clearPreviews();" in reset and
          "autoDraftId = null;" in reset and
          "step1DraftId = null;" in reset and
          "recordResumeDraft(null);" in reset and
          '$("photo-input").value = "";' in reset,
          "clearPreviews + ids + resume + input")
    check("the reset keeps the listing page working",
          "draft = null" not in reset and
          'show("step-photos")' not in reset and
          "publish-ok" not in reset,
          "draft stays set, no navigation, note untouched")
    batch = js.split("async function runBatch(")[1].split("return signals;")[0]
    check("a batch clears step 1 only when its draft went live",
          "step1BelongsToDraft(r.draft_id)" in batch and
          "r.ok &&" in batch,
          "only an ok row for the step-1 draft triggers it")
    revise = js.split('$("btn-update-listing").addEventListener("click"')[1].split(
        "async function postPublish")[0]
    check("the revise flow never clears the step-1 photos",
          "resetStep1AfterPublish" not in revise,
          "revise updates a live listing; it must not reset step 1")

    # 29. v62 condition list: one list everywhere. Nov 18 he picked
    # "New other (see details)" — the category allows it and the
    # dropdown offered it — but the listing check said "Pick a
    # condition from the list." The static CONDITIONS list had only
    # six values and no NEW_OTHER, so the check rejected a pick the
    # app itself had offered, and the error sat at the bottom by the
    # List button while the dropdown is at the top, so he scrolled
    # up and down to guess. The static list, the check, and the
    # dropdown now carry the full ConditionEnum set; the hint under
    # the dropdown names what the category takes; a condition error
    # clears the moment the pick changes.
    conds = py.split("CONDITIONS = [")[1].split("]")[0]
    check("CONDITIONS carries the full enum set, NEW_OTHER included",
          all('"%s"' % v in conds for v in (
              "NEW", "NEW_OTHER", "NEW_WITH_DEFECTS",
              "CERTIFIED_REFURBISHED", "EXCELLENT_REFURBISHED",
              "VERY_GOOD_REFURBISHED", "GOOD_REFURBISHED",
              "SELLER_REFURBISHED", "LIKE_NEW", "PRE_OWNED_EXCELLENT",
              "USED_EXCELLENT", "PRE_OWNED_FAIR", "USED_VERY_GOOD",
              "USED_GOOD", "USED_ACCEPTABLE",
              "FOR_PARTS_OR_NOT_WORKING")) and
          '"USED"' not in conds,
          "no bare USED: USED_EXCELLENT is plain Used")
    select = html.split('<select id="f-condition">')[1].split("</select>")[0]
    check("the static dropdown matches the check list",
          select.count('<option value="') == 17 and
          all('value="%s"' % v in select for v in (
              "NEW_OTHER", "NEW_WITH_DEFECTS", "SELLER_REFURBISHED",
              "LIKE_NEW", "PRE_OWNED_EXCELLENT", "PRE_OWNED_FAIR")),
          "%d options" % select.count('<option value="'))
    check("the hint under the dropdown names what the category takes",
          'id="condition-allowed-hint"' in html and
          '"This category takes: "' in js and
          "data.conditions.map((c) => c.label).join" in js,
          "hint element + takes line")
    check("a junk condition still fails but names the allowed ones",
          py.count("This category takes:") >= 2 and
          '"Pick a condition from the list."' in py,
          "category message + junk-value message")
    check("a condition error clears the moment the pick changes",
          "function clearConditionPublishError(" in js and
          '$("f-condition").addEventListener("change", clearConditionPublishError);' in js and
          'startsWith("Fix these before listing:")' in js,
          "live clear, multi-problem list stays")

    # 30. v63 quantity: enter it before listing, change it live. Nov 19
    # he could not enter a quantity anywhere and could not edit it on
    # eBay either, so every listing went up as 1. The quantity was
    # hardcoded to 1 in two places on the publish path (the inventory
    # item availability and the offer availableQuantity) and no field
    # existed. The listing page now has a Quantity field by Price, the
    # publish sends it on both levels, a saved offer is discarded when
    # the quantity changes (the v34/v35 snapshot rule), batch carries
    # each draft's quantity, and a listed draft gets a quantity-only
    # live update that follows the v57 revise pattern (preview against
    # the live offer, confirm card naming "Quantity 1 to 3", update
    # both levels because the listing shows the smaller of the two,
    # read both back and prove it).
    check("no hardcoded quantity 1 left on the publish path",
          '"quantity": 1' not in py and '"availableQuantity": 1' not in py,
          "both hardcoded 1s gone")
    check("Quantity field on the listing page, by Price",
          'id="f-quantity"' in html and
          '<input id="f-quantity" type="number" min="1" step="1"' in html and
          "How many of this item you have to sell." in html,
          "field + plain hint")
    check("quantity autosaves and prefills like price",
          'quantity: $("f-quantity").value.trim()' in js and
          '"f-quantity"' in js and
          'out.quantity' in js and '$("f-quantity").value = pre.quantity;' in js,
          "collectForm + prefill")
    check("publish sends the quantity and names it in the confirm",
          'quantity: $("f-quantity").value.trim()' in js and
          '"Quantity: " + $("f-quantity").value.trim()' in js and
          '"availableQuantity": quantity' in py,
          "body + confirm + offer")
    check("a saved offer is discarded when the quantity changes",
          'recorded_quantity = d.get("ebay_offer_quantity")' in py and
          'd["ebay_offer_quantity"] = quantity' in py and
          'd.pop("ebay_offer_quantity", None)' in py,
          "snapshot record + discard + clear")
    check("live quantity update follows the v57 revise pattern",
          '"/revise?quantity="' in js and
          '"Quantity changes from %d to %d."' in py and
          'def _revise_quantity_plan(' in py and
          'def _offer_put_body(' in py and
          'id="btn-update-listing"' in html and
          'id="revise-confirm"' in html,
          "preview + confirm card + plan + full offer body")
    check("live update changes both levels and proves both",
          'ship_to["quantity"] = new' in py and
          '"availableQuantity"] = quantity' in py and
          'after_qty != new or after_item_qty != new' in py,
          "item PUT + offer PUT + verify")

    # 31. v64 one update button: Nov 19 he asked "What's up with all
    # these update buttons? One update button and I click it updates."
    # The listing page had two blue buttons (one for photos, one for
    # quantity) plus each confirm card's own Update live listing
    # button. Now there is one button, one preview that plans both
    # parts, one confirm card listing everything, and one confirm
    # that runs both parts — photos first, then quantity, each part
    # re-reading live state before writing, so the photos PUT (whole
    # item body) can never overwrite the quantity change. A part that
    # fails does not stop the other part; the answer names each part's
    # result, and the draft record updates only for the part that
    # actually succeeded.
    check("neither old update button survives anywhere",
          "btn-update-photos" not in html and
          "btn-update-photos" not in js and
          "btn-update-quantity" not in html and
          "btn-update-quantity" not in js and
          "revise-qty" not in html and "revise-qty" not in js and
          "update-photos" not in js,
          "old button or card ids left behind")
    check("combined revise endpoints exist (preview GET + execute POST)",
          '@app.route("/api/drafts/<draft_id>/revise", methods=["GET"])' in py and
          '@app.route("/api/drafts/<draft_id>/revise", methods=["POST"])' in py)
    check("combined preview plans both parts into one change list",
          'def api_revise_preview' in py and
          'photos["changes"] = plan["changes"]' in py and
          'quantity["changes"] = qplan["changes"]' in py and
          'changes = list(photos["changes"]) + list(quantity["changes"])' in py,
          "per-part plans + merged changes")
    check("combined preview does not let a failed part block the other",
          'Photos could not be checked: ' in py and
          'Quantity could not be checked: ' in py and
          'Only the quantity will change.' in py and
          'Only the photos will change.' in py,
          "per-part error notes")
    check("combined execute runs photos first, then quantity",
          "def api_revise_execute" in py and
          py.split("def api_revise_execute")[1].split(
              "\ndef ")[0].index("_apply_photo_revise") <
          py.split("def api_revise_execute")[1].split(
              "\ndef ")[0].index("_apply_quantity_revise"),
          "photos before quantity")
    check("combined execute requires confirmed:true before any eBay call",
          'Confirm the update first.' in py)
    check("combined execute reports each part plainly, never claims a failure",
          '"photos": photos, "quantity": quantity' in py and
          'the photos did not change: " + photos["error"]' in py and
          'the quantity did not change: " + quantity["error"]' in py and
          'Nothing changed: ' in py,
          "per-part results + combined message")
    check("the single-part endpoints still exist underneath",
          '@app.route("/api/drafts/<draft_id>/revise-photos", methods=["POST"])' in py and
          '@app.route("/api/drafts/<draft_id>/revise-quantity", methods=["POST"])' in py and
          'def _apply_photo_revise(' in py and
          'def _apply_quantity_revise(' in py,
          "wrappers + shared apply helpers")

    print()
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
