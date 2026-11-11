/* apk-v51 node tests for the combined price check (static/app.js).
 * Loads the REAL app.js in a vm with a REAL-browser-faithful DOM,
 * same pattern as tests/design4.test.js:
 *  - el.children are HTMLCollection stand-ins: indexed items + length
 *    + item(), NO Array methods (exactly like a browser).
 *  - addEventListener records listeners; el.click() dispatches them;
 *    fire(el, type) dispatches any other recorded event.
 *  - fetch is deferred per URL; the test resolves /api/drafts itself.
 * Covers: "Check sold prices" opens one results screen with two
 * halves; the sold half still opens eBay's title-based sold search
 * in the pop-up (same URL as before, "vintage item" fallback for an
 * empty title); the image half renders similar active listings from
 * the draft's first photo (thumb, title, price, "Use this price"
 * per row); a match's "Use this price" sets the draft price and
 * confirms it; a failed or empty image search shows a plain note
 * and leaves the sold half working; a second check clears stale
 * rows while the new lookup runs.
 * v55: Hide folds the results card away (twice is harmless); Check
 * sold prices re-opens it with a fresh check.
 * Run: node tests/sold-image.test.js (exit 0 = pass). */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP_JS = path.join(__dirname, "..",
  "app/src/main/python/static/app.js");
const SRC = fs.readFileSync(APP_JS, "utf8");

/* ---- faithful HTMLCollection stand-in ---- */
function makeCollection(backing) {
  const coll = {};
  for (let i = 0; i < 64; i++) {
    Object.defineProperty(coll, String(i), {
      get() { return backing[i]; }, enumerable: true, configurable: true,
    });
  }
  Object.defineProperty(coll, "length", {
    get() { return backing.length; }, enumerable: true, configurable: true,
  });
  coll.item = (i) => backing[i] || null;
  coll.namedItem = () => null;
  return coll;
}

function makeClassList() {
  const set = new Set();
  const list = {
    add(c) { set.add(c); },
    remove(c) { set.delete(c); },
    toggle(c, force) {
      if (force === undefined) { set.has(c) ? set.delete(c) : set.add(c); }
      else if (force) set.add(c);
      else set.delete(c);
    },
    contains(c) { return set.has(c); },
    _set: set,
  };
  return list;
}

const ALL_ELS = [];

function makeEl(id, tag) {
  const kids = [];
  const listeners = {};
  const attrs = {};
  const classList = makeClassList();
  const el = {
    _kids: kids,
    _listeners: listeners,
    _attrs: attrs,
    _parent: null,
    _value: "",
    id: id || "",
    tagName: (tag || "").toUpperCase(),
    textContent: "",
    checked: false,
    disabled: false,
    style: {},
    dataset: {},
    classList,
    type: "",
    addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
    removeEventListener() {},
    setAttribute(k, v) { attrs[k] = String(v); },
    getAttribute(k) { return attrs[k] === undefined ? null : attrs[k]; },
    appendChild(child) {
      kids.push(child);
      child._parent = el;
      return child;
    },
    querySelector() { return makeEl("", "div"); },
    querySelectorAll() { return []; },
    getContext() { return null; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 100 }; },
    scrollIntoView() {},
    setPointerCapture() {},
    releasePointerCapture() {},
    focus() {},
    click() { (listeners.click || []).forEach((f) => f({ preventDefault() {} })); },
  };
  Object.defineProperty(el, "className", {
    get() { return [...classList._set].join(" "); },
    set(v) {
      classList._set.clear();
      String(v || "").split(/\s+/).filter(Boolean)
        .forEach((c) => classList._set.add(c));
    },
    enumerable: true, configurable: true,
  });
  Object.defineProperty(el, "children", {
    get() { return makeCollection(kids); }, enumerable: true, configurable: true,
  });
  Object.defineProperty(el, "value", {
    get() { return el._value; },
    set(v) { el._value = v == null ? "" : String(v); },
    enumerable: true, configurable: true,
  });
  let _html = "";
  Object.defineProperty(el, "innerHTML", {
    get() { return _html; },
    set(v) {
      _html = v; kids.length = 0;
      el.textContent = String(v).replace(/<[^>]*>/g, "");
    },
    enumerable: true, configurable: true,
  });
  ALL_ELS.push(el);
  return el;
}

function matchesPart(el, part) {
  if (!el) return false;
  if (part.startsWith("#")) return el.id === part.slice(1);
  if (part.startsWith(".")) return el.classList.contains(part.slice(1));
  return el.tagName === part.toUpperCase();
}
function matchesSelector(el, parts) {
  if (!matchesPart(el, parts[parts.length - 1])) return false;
  let ancestor = el._parent;
  for (let i = parts.length - 2; i >= 0; i--) {
    while (ancestor && !matchesPart(ancestor, parts[i])) {
      ancestor = ancestor._parent;
    }
    if (!ancestor) return false;
    ancestor = ancestor._parent;
  }
  return true;
}

function newContext(source) {
  const registry = {};
  const fetchCalls = [];
  const pending = [];
  const unhandled = [];
  const opened = [];
  const onUnhandled = (err) => unhandled.push(err);
  process.on("unhandledRejection", onUnhandled);
  const sandbox = {
    document: {
      getElementById(id) {
        if (!registry[id]) registry[id] = makeEl(id, "");
        return registry[id];
      },
      createElement(tag) { return makeEl("", tag); },
      querySelectorAll(selector) {
        const parts = String(selector).split(/\s+/).filter(Boolean);
        return ALL_ELS.filter((el) => matchesSelector(el, parts));
      },
      querySelector() { return makeEl("", "div"); },
      addEventListener() {},
      body: makeEl("body", "body"),
    },
    window: {
      addEventListener() {},
      location: { search: "" },
      history: {},
      innerWidth: 400,
      scrollTo() {},
      confirm: () => true,
      open(url) { opened.push(url); },
    },
    fetch: (url, opts = {}) => {
      fetchCalls.push({ url, opts });
      return new Promise((resolve) => { pending.push({ url, opts, resolve }); });
    },
    URLSearchParams,
    console,
    setTimeout,
    clearTimeout,
  };
  sandbox.window.document = sandbox.document;
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  const $ = (id) => sandbox.document.getElementById(id);
  const ctx = (code) => vm.runInContext(code, sandbox);
  const resolveFetch = (urlPart, json, ok = true) => {
    const i = pending.findIndex((p) => String(p.url).includes(urlPart));
    if (i < 0) throw new Error("no pending fetch for " + urlPart +
      " (pending: " + pending.map((p) => p.url).join(", ") + ")");
    const [p] = pending.splice(i, 1);
    p.resolve({ ok, status: ok ? 200 : 500,
      text: async () => JSON.stringify(json) });
  };
  // app.js fires GET /api/config on load; drain it.
  const ci = pending.findIndex((p) => String(p.url).includes("/api/config"));
  if (ci >= 0) {
    const [p] = pending.splice(ci, 1);
    p.resolve({ ok: true, status: 200, text: async () => "{}" });
  }
  const tick = () => new Promise((r) => setTimeout(r, 0));
  const fire = (el, type, event) =>
    (el._listeners[type] || []).forEach((f) => f(event || {}));
  const dispose = () => process.removeListener("unhandledRejection", onUnhandled);
  return { sandbox, $, ctx, resolveFetch, tick, fire,
    fetchCalls, pending, unhandled, opened, dispose };
}

let failures = 0;
function check(name, ok, extra) {
  if (ok) console.log("ok:  ", name);
  else {
    failures++;
    console.log("FAIL:", name, extra === undefined ? "" : extra);
  }
}

const DRAFT = { id: "d1", title: "Royal Doulton figurine",
  photos: ["d1/1.jpg"], price: "" };
const MATCHES = { matches: [
  { title: "Royal Doulton figurine", price: "24.50",
    thumb: "https://example.com/a.jpg", url: "https://www.ebay.com/itm/1",
    item_id: "v1|1|0" },
  { title: "Doulton lady, no picture", price: "18.00",
    thumb: "", url: "https://www.ebay.com/itm/2", item_id: "v1|2|0" },
], note: "" };

/* Open the price check and resolve the draft save + similar lookup. */
async function openPriceCheck(ctx, $, resolveFetch, tick, similar) {
  ctx("draft = " + JSON.stringify(DRAFT) + ";");
  $("sold-results").classList.add("hidden");
  $("btn-solds").click();
  resolveFetch("/api/drafts/d1", { draft: DRAFT });
  await tick(); await tick();
  resolveFetch("/similar", similar);
  await tick(); await tick();
}

(async () => {
  const { $, ctx, resolveFetch, tick, fetchCalls, unhandled, opened, dispose } =
    newContext(SRC);

  /* ---------- 1. Results screen starts hidden, opens on tap ---------- */
  // Mirror the template: #sold-results carries "card hidden" in v51.
  $("sold-results").className = "card hidden";
  check("results section starts hidden",
    $("sold-results").classList.contains("hidden"));
  await openPriceCheck(ctx, $, resolveFetch, tick, MATCHES);
  check("results section shows after Check sold prices",
    !$("sold-results").classList.contains("hidden"));
  check("similar lookup fired for the open draft",
    fetchCalls.some((c) => String(c.url).includes("/similar")));

  /* ---------- 2. Similar matches render thumb, title, price, button ---------- */
  const rows = $("similar-list").children;
  check("two similar rows render", rows.length === 2, rows.length);
  const row0 = rows[0];
  const img = row0.children[0];
  check("first row shows the match thumbnail",
    img && img.tagName === "IMG" &&
    img.src === "https://example.com/a.jpg", img && img.src);
  const title = row0.children[1].children[0];
  check("first row shows the match title",
    title && title.textContent === "Royal Doulton figurine",
    title && title.textContent);
  const price = row0.children[2];
  check("first row shows the match price with $",
    price && price.textContent === "$24.50", price && price.textContent);
  const useBtn = row0.children[3];
  check("first row has a Use this price button",
    useBtn && useBtn.tagName === "BUTTON" &&
    useBtn.textContent === "Use this price",
    useBtn && useBtn.textContent);
  check("match without a thumb still renders its row",
    rows[1].children.length === 3, rows[1].children.length);

  /* ---------- 3. Sold half: same title-based sold search as before ---------- */
  $("f-title").value = "Royal Doulton figurine";
  $("btn-solds-open").click();
  check("sold half opens eBay sold search with the title words",
    opened.length === 1 &&
    opened[0].includes("https://www.ebay.com/sch/i.html") &&
    opened[0].includes("_nkw=Royal%20Doulton%20figurine") &&
    opened[0].includes("LH_Sold=1") &&
    opened[0].includes("LH_Complete=1"), opened[0]);
  $("f-title").value = "";
  $("btn-solds-open").click();
  check("empty title falls back to vintage item",
    opened[1].includes("_nkw=vintage%20item"), opened[1]);

  /* ---------- 4. "Use this price" sets the draft price ---------- */
  useBtn.click();
  resolveFetch("/api/drafts/d1",
    { draft: { ...DRAFT, price: "24.50" } });
  await tick(); await tick();
  check("price box shows the picked price", $("f-price").value === "24.50",
    $("f-price").value);
  const lastPut = [...fetchCalls].reverse()
    .find((c) => String(c.url).includes("/api/drafts/d1"));
  check("price pick saves the draft with the picked price",
    lastPut && JSON.parse(lastPut.opts.body).price === "24.50",
    lastPut && lastPut.opts.body);
  check("price pick confirms in plain words",
    $("sold-price-note").textContent ===
      "Price set to $24.50 from a similar listing.",
    $("sold-price-note").textContent);

  /* ---------- 5. A second check clears stale rows while it runs ---------- */
  $("btn-solds").click();
  resolveFetch("/api/drafts/d1", { draft: { ...DRAFT, price: "24.50" } });
  await tick(); await tick();
  check("stale rows clear before the new lookup lands",
    $("similar-list").children.length === 0,
    $("similar-list").children.length);
  resolveFetch("/similar", { matches: [MATCHES.matches[0]], note: "" });
  await tick(); await tick();
  check("new lookup renders its own rows",
    $("similar-list").children.length === 1,
    $("similar-list").children.length);

  /* ---------- 6. Image half fails: plain note, sold half still works ---------- */
  $("btn-solds").click();
  resolveFetch("/api/drafts/d1", { draft: DRAFT });
  await tick(); await tick();
  resolveFetch("/similar",
    { matches: [], note: "eBay found no similar items for this photo." });
  await tick(); await tick();
  check("empty image search shows a plain note",
    $("similar-note").textContent.includes("no similar items") &&
    !$("similar-note").classList.contains("hidden"),
    $("similar-note").textContent);
  check("empty image search leaves no rows",
    $("similar-list").children.length === 0);
  const before = opened.length;
  $("btn-solds-open").click();
  check("sold half still opens after the image half fails",
    opened.length === before + 1, opened.length);

  /* ---------- 7. v55: Hide folds the results away; Check re-opens them ---------- */
  check("results card is open before Hide",
    !$("sold-results").classList.contains("hidden"));
  $("btn-solds-hide").click();
  check("Hide folds the results card away",
    $("sold-results").classList.contains("hidden"));
  $("btn-solds-hide").click();
  check("Hide twice is harmless, card stays hidden",
    $("sold-results").classList.contains("hidden"));
  $("btn-solds").click();
  resolveFetch("/api/drafts/d1", { draft: { ...DRAFT, price: "24.50" } });
  await tick(); await tick();
  check("Check sold prices re-opens the card",
    !$("sold-results").classList.contains("hidden"));
  check("re-open clears stale rows while the new lookup runs",
    $("similar-list").children.length === 0,
    $("similar-list").children.length);
  resolveFetch("/similar", MATCHES);
  await tick(); await tick();
  check("re-open renders the fresh rows",
    $("similar-list").children.length === 2,
    $("similar-list").children.length);

  check("no unhandled rejections", unhandled.length === 0,
    unhandled.map(String).join(" | "));
  dispose();
  console.log(failures === 0 ? "ALL PASS" : failures + " FAILURES");
  process.exit(failures === 0 ? 0 : 1);
})();
