/* apk-v51 node tests for the live n/65 counter on item-specifics
 * value fields (static/app.js). Loads the REAL app.js in a vm with
 * a REAL-browser-faithful DOM, same pattern as
 * tests/design4.test.js:
 *  - el.children are HTMLCollection stand-ins: indexed items + length
 *    + item(), NO Array methods (exactly like a browser).
 *  - addEventListener records listeners; el.click() dispatches them;
 *    fire(el, type) dispatches any other recorded event.
 *  - fetch is deferred per URL; the test resolves /api/drafts itself.
 * Covers: attrRow's value field carries a "n/65" counter span
 * (same .count style as the v48 policy counters); updateAttrCount
 * refreshes the counter from the live value (82/65 for the live
 * failure's Set Includes value, 65/65 at the limit, 5/65 after
 * shortening); a row with no value input never crashes the update.
 * Harness limit, stated honestly: attrRow builds its row with
 * innerHTML, which the harness does not parse into children, so the
 * creation-time count on a real attrRow is only pinned by its
 * markup here — the live update itself is pinned on a synthetic
 * row built with createElement, which the harness does parse.
 * Run: node tests/attrs-counter.test.js (exit 0 = pass). */
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
    fetchCalls, pending, unhandled, dispose };
}

let failures = 0;
function check(name, ok, extra) {
  if (ok) console.log("ok:  ", name);
  else {
    failures++;
    console.log("FAIL:", name, extra === undefined ? "" : extra);
  }
}

const LONG_VALUE = "Doll, Gown, Stole, Gloves, Jewelry, Shoes, " +
  "Doll Stand, Certificate of Authenticity";

(async () => {
  const { $, ctx, tick, unhandled, dispose } = newContext(SRC);

  /* ---------- 1. attrRow carries the counter span ---------- */
  ctx("window.__row = attrRow('Set Includes', '" + LONG_VALUE + "');");
  const markup = ctx("window.__row.innerHTML");
  check("value row carries an n/65 counter span",
    markup.includes('class="count av-count"') &&
    markup.includes("0/65"), markup);
  check("counter sits between the value field and the remove button",
    markup.indexOf("av-count") > markup.indexOf('class="av"') &&
    markup.indexOf("av-count") < markup.indexOf("Remove"), markup);

  /* ---------- 2. updateAttrCount refreshes from the live value ---------- */
  // Synthetic row: real children (the harness parses createElement
  // appends, unlike innerHTML), same shape as a browser row.
  ctx(`window.__trow = document.createElement("div");
window.__trow.className = "attr-row";
window.__tav = document.createElement("input");
window.__tav.className = "av";
window.__tav.value = "` + LONG_VALUE + `";
window.__trow.appendChild(window.__tav);
window.__tcount = document.createElement("span");
window.__tcount.className = "count av-count";
window.__trow.appendChild(window.__tcount);
updateAttrCount(window.__trow);`);
  check("counter shows 82/65 for the live failure value",
    ctx("window.__tcount.textContent") === "82/65",
    ctx("window.__tcount.textContent"));
  ctx(`window.__tav.value = "${"x".repeat(65)}";
updateAttrCount(window.__trow);`);
  check("counter shows 65/65 at the limit",
    ctx("window.__tcount.textContent") === "65/65",
    ctx("window.__tcount.textContent"));
  ctx(`window.__tav.value = "short";
updateAttrCount(window.__trow);`);
  check("counter follows shortening back under the limit",
    ctx("window.__tcount.textContent") === "5/65",
    ctx("window.__tcount.textContent"));

  /* ---------- 3. A row with no value input never crashes ---------- */
  let threw = false;
  try {
    ctx(`window.__empty = document.createElement("div");
updateAttrCount(window.__empty);`);
  } catch (e) { threw = true; }
  check("update with no value input does not throw", !threw);

  check("no unhandled rejections", unhandled.length === 0,
    unhandled.map(String).join(" | "));
  dispose();
  console.log(failures === 0 ? "ALL PASS" : failures + " FAILURES");
  process.exit(failures === 0 ? 0 : 1);
})();
