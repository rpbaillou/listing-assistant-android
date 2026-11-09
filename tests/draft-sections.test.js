/* apk-v46 node tests for the drafts list sections (static/app.js).
 * Loads the REAL app.js in a vm with a REAL-browser-faithful DOM,
 * same pattern as tests/real-dom-shipping.test.js:
 *  - el.children are HTMLCollection stand-ins: indexed items + length
 *    + item(), NO Array methods (exactly like a browser).
 *  - addEventListener records listeners; el.click() dispatches them.
 *  - fetch is deferred per URL; the test resolves /api/drafts itself.
 * Covers: unlisted rows in the top "Drafts" section in server order
 * (newest first, v45), listed rows in the bottom "Listed on eBay"
 * section, the Listed section starts CLOSED with a count and
 * aria-expanded=false, tapping the header expands and collapses it,
 * listed rows have no Post button and unlisted rows do, and the empty
 * states (no drafts at all; only listed; only unlisted).
 * Run: node tests/draft-sections.test.js (exit 0 = pass). */
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
  return {
    add(c) { set.add(c); },
    remove(c) { set.delete(c); },
    toggle(c, force) {
      if (force === undefined) { set.has(c) ? set.delete(c) : set.add(c); }
      else if (force) set.add(c);
      else set.delete(c);
    },
    contains(c) { return set.has(c); },
  };
}

function makeEl(id, tag) {
  const kids = [];
  const listeners = {};
  const attrs = {};
  const el = {
    _kids: kids,
    _listeners: listeners,
    _attrs: attrs,
    _value: "",
    textContent: "",
    checked: false,
    disabled: false,
    style: {},
    dataset: {},
    classList: makeClassList(),
    type: "",
    addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
    removeEventListener() {},
    setAttribute(k, v) { attrs[k] = String(v); },
    getAttribute(k) { return attrs[k] === undefined ? null : attrs[k]; },
    appendChild(child) { kids.push(child); return child; },
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
  return el;
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
      querySelectorAll() { return []; },
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
  const dispose = () => process.removeListener("unhandledRejection", onUnhandled);
  return { sandbox, $, resolveFetch, tick, fetchCalls, pending, unhandled, dispose };
}

let failures = 0;
function check(name, cond, extra) {
  if (!cond) { failures++; console.log("FAIL: " + name + (extra ? " -- " + extra : "")); }
  else { console.log("ok:   " + name); }
}

const D = (id, title, listed, updated) => (
  { id, title, listed, updated, thumb: null });

async function openDrafts(c, drafts) {
  c.$("btn-drafts").click();
  await c.tick(); await c.tick();
  c.resolveFetch("/api/drafts", { drafts });
  await c.tick(); await c.tick(); await c.tick();
  return c.$("drafts-list");
}

async function main() {
  console.log("== mixed drafts: sections, order, closed-by-default ==");
  {
    const c = newContext(SRC);
    const list = await openDrafts(c, [
      D("a1", "Newest draft", false, "2026-11-08T10:00:00"),
      D("a2", "Listed doll", true, "2026-11-07T09:00:00"),
      D("a3", "Older draft", false, "2026-11-06T08:00:00"),
      D("a4", "Listed mitts", true, "2026-11-05T07:00:00"),
    ]);
    check("two sections render", list.children.length === 2);
    const top = list.children[0];
    const bottom = list.children[1];
    check("top header counts the working drafts",
      top.children[0].textContent === "Drafts (2)");
    check("top rows stay in server order (newest first)",
      top.children[1].innerHTML.includes("Newest draft") &&
      top.children[2].innerHTML.includes("Older draft"));
    check("unlisted rows keep a Post button",
      top.children[1].innerHTML.includes('class="primary post"') &&
      top.children[2].innerHTML.includes('class="primary post"'));
    const toggle = bottom.children[0];
    const body = bottom.children[1];
    check("Listed header shows the count, closed chevron",
      toggle.textContent === "▸ Listed on eBay (2)");
    check("Listed section starts closed (aria-expanded false)",
      toggle.getAttribute("aria-expanded") === "false");
    check("Listed rows start hidden", body.classList.contains("hidden"));
    check("listed rows sit in the bottom section, newest first",
      body.children[0].innerHTML.includes("Listed doll") &&
      body.children[1].innerHTML.includes("Listed mitts"));
    check("listed rows have NO Post button",
      !body.children[0].innerHTML.includes('class="primary post"') &&
      !body.children[1].innerHTML.includes('class="primary post"'));
    /* v49: the small "Listed on eBay" pill is gone — listed rows
       live ONLY in this section, so a pill inside it would be a
       lie, and no listed pill may appear on any row. */
    check("listed rows carry NO listed pill",
      !body.children[0].innerHTML.includes("d-listed") &&
      !body.children[1].innerHTML.includes("d-listed") &&
      !body.children[0].innerHTML.includes("Listed on eBay") &&
      !body.children[1].innerHTML.includes("Listed on eBay"));
    // Toggle open, then closed again.
    toggle.click();
    check("tap expands the Listed section",
      toggle.getAttribute("aria-expanded") === "true" &&
      toggle.textContent === "▾ Listed on eBay (2)" &&
      !body.classList.contains("hidden"));
    toggle.click();
    check("tap again collapses it",
      toggle.getAttribute("aria-expanded") === "false" &&
      toggle.textContent === "▸ Listed on eBay (2)" &&
      body.classList.contains("hidden"));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== only unlisted: no Listed section at all ==");
  {
    const c = newContext(SRC);
    const list = await openDrafts(c, [
      D("b1", "Only draft", false, "2026-11-08T10:00:00"),
    ]);
    check("one section only", list.children.length === 1);
    check("header counts one draft",
      list.children[0].children[0].textContent === "Drafts (1)");
    check("the draft row is there",
      list.children[0].children[1].innerHTML.includes("Only draft"));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== only listed: Drafts section shows the hint ==");
  {
    const c = newContext(SRC);
    const list = await openDrafts(c, [
      D("c1", "Only listed", true, "2026-11-08T10:00:00"),
    ]);
    check("two sections render", list.children.length === 2);
    check("Drafts header counts zero",
      list.children[0].children[0].textContent === "Drafts (0)");
    check("Drafts section shows the empty hint",
      list.children[0].children[1].textContent.includes("No drafts yet"));
    check("Listed section holds the one row",
      list.children[1].children[1].children[0].innerHTML.includes("Only listed"));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== no drafts at all: plain hint, no sections ==");
  {
    const c = newContext(SRC);
    const list = await openDrafts(c, []);
    check("no sections render", list.children.length === 0);
    check("plain empty hint shows",
      list.textContent.includes("No drafts yet. Start one from step 1."));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log(failures === 0 ? "\nAll draft-section checks pass."
    : `\nFAILED: ${failures} check(s).`);
  process.exit(failures === 0 ? 0 : 1);
}

main();
