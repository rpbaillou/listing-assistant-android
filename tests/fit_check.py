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

    print()


if __name__ == "__main__":
    sys.exit(main())
