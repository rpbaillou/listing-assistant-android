/* apk-v63 node tests for quantity (static/app.js). Loads the REAL
 * app.js in a vm with a REAL-browser-faithful DOM, same pattern as
 * tests/revise-photos.test.js.
 * Covers: parseQuantity (whole numbers of 1 or more only);
 * listingPrefill quantity (form wins, then the draft, then 1);
 * collectForm carries the quantity into the draft autosave; the
 * listing page shows the Update live listing quantity button only
 * for listed drafts; tapping it asks eBay for the preview and shows
 * exactly what will change on the confirm card; Cancel sends
 * nothing; confirming posts confirmed:true with the quantity and
 * shows the success note with the listing link; an unchanged preview
 * says so plainly with no confirm card; a bad quantity shows a plain
 * message and sends nothing; a preview failure shows eBay's message
 * plainly.
 * Run: node tests/quantity.test.js (exit 0 = pass). */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP_JS = path.join(__dirname, "..",
  "app/src/main/python/static/app.js");
const SRC = fs.readFileSync(APP_JS, "utf8");

/* ---- faithful HTMLCollection stand-in (from draft-sections.test.js) ---- */
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
    appendChild(child) { kids.push(child); child._parent = el; return child; },
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
  const ctx = (code) => vm.runInContext(code, sandbox);
  const resolveFetch = (urlPart, json, ok = true) => {
    const i = pending.findIndex((p) => String(p.url).includes(urlPart));
    if (i < 0) throw new Error("no pending fetch for " + urlPart +
      " (pending: " + pending.map((p) => p.url).join(", ") + ")");
    const [p] = pending.splice(i, 1);
    p.resolve({ ok, status: ok ? 200 : 500,
      text: async () => JSON.stringify(json === null ? {} : json) });
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
function check(name, cond, extra) {
  if (!cond) { failures++; console.log("FAIL: " + name + (extra ? " -- " + extra : "")); }
  else { console.log("ok:   " + name); }
}

const DRAFT = { id: "d1", title: "Listed doll", photos: ["d1/1.jpg"],
  ebay_listing_id: "123", ebay_listing_url: "https://www.ebay.com/itm/123",
  ebay_offer_id: "off1", quantity: "1" };
const PLAN = { listing_id: "123",
  listing_url: "https://www.ebay.com/itm/123", title: "Listed doll",
  live_quantity: 1, requested_quantity: 3, unchanged: false,
  changes: ["Quantity changes from 1 to 3."] };

async function main() {
  console.log("== parseQuantity: whole numbers of 1 or more only ==");
  {
    const c = newContext(SRC);
    check("3 parses to 3", c.ctx("parseQuantity('3')") === 3);
    check("padded 3 parses to 3", c.ctx("parseQuantity(' 3 ')") === 3);
    check("1 parses to 1", c.ctx("parseQuantity('1')") === 1);
    check("0 is null", c.ctx("parseQuantity('0')") === null);
    check("negative is null", c.ctx("parseQuantity('-2')") === null);
    check("fraction is null", c.ctx("parseQuantity('2.5')") === null);
    check("3.0 is null", c.ctx("parseQuantity('3.0')") === null);
    check("letters are null", c.ctx("parseQuantity('abc')") === null);
    check("blank is null", c.ctx("parseQuantity('')") === null);
    check("null is null", c.ctx("parseQuantity(null)") === null);
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== listingPrefill quantity: form wins, then draft, then 1 ==");
  {
    const c = newContext(SRC);
    check("form value wins",
      c.ctx("listingPrefill({quantity: '5'}, null, {}, {quantity: '2'}).quantity") === "2");
    check("draft value next",
      c.ctx("listingPrefill({quantity: '5'}, null, {}, {}).quantity") === "5");
    check("no value defaults to 1",
      c.ctx("listingPrefill({}, null, {}, {}).quantity") === "1");
    check("blank draft defaults to 1",
      c.ctx("listingPrefill({quantity: ''}, null, {}, {}).quantity") === "1");
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== collectForm carries the quantity into the autosave ==");
  {
    const c = newContext(SRC);
    c.$("f-quantity").value = "3";
    check("collectForm returns the typed quantity",
      c.ctx("collectForm().quantity") === "3");
    c.$("f-quantity").value = "";
    check("collectForm returns the blank as-is (server decides)",
      c.ctx("collectForm().quantity") === "");
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== listing page shows Update quantity only for listed drafts ==");
  {
    const c = newContext(SRC);
    c.ctx("draft = " + JSON.stringify(DRAFT) + ";");
    const p = c.ctx("refreshEbayStatus();");
    await c.tick();
    c.resolveFetch("/api/ebay/status", { ebay_configured: true,
      connected: true, runame: "Reynard_Baillou-ReynardB-Mr-PRD-eayxussz",
      policies_configured: true });
    await p; await c.tick();
    check("listed draft shows Update live listing quantity",
      !c.$("btn-update-quantity").classList.contains("hidden"));
    c.ctx("draft = { id: 'd2', title: 'Working draft', photos: ['d2/1.jpg'] };");
    const p2 = c.ctx("refreshEbayStatus();");
    await c.tick();
    c.resolveFetch("/api/ebay/status", { ebay_configured: true,
      connected: true, runame: "Reynard_Baillou-ReynardB-Mr-PRD-eayxussz",
      policies_configured: true });
    await p2; await c.tick();
    check("unlisted draft hides Update quantity",
      c.$("btn-update-quantity").classList.contains("hidden"));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== Update shows the confirm card; Cancel sends nothing ==");
  {
    const c = newContext(SRC);
    c.ctx("draft = " + JSON.stringify(DRAFT) + ";");
    c.$("f-quantity").value = "3";
    c.$("btn-update-quantity").click();
    await c.tick(); await c.tick();
    // The handler saves the draft first (PUT), then asks for the preview.
    c.resolveFetch("/api/drafts/d1", { draft: DRAFT });
    await c.tick(); await c.tick();
    check("preview is a GET on the revise-quantity endpoint with the quantity",
      c.fetchCalls.some((f) => String(f.url) === "/api/drafts/d1/revise-quantity?quantity=3" &&
        (f.opts.method || "GET") === "GET"));
    c.resolveFetch("/api/drafts/d1/revise-quantity", PLAN);
    await c.tick(); await c.tick();
    check("confirm card opens with the change list",
      !c.$("revise-qty-confirm").classList.contains("hidden") &&
      c.$("revise-qty-changes").children.length === 1 &&
      c.$("revise-qty-changes").children[0].textContent.includes("Quantity changes from 1 to 3."));
    c.$("btn-revise-qty-cancel").click();
    check("Cancel hides the card",
      c.$("revise-qty-confirm").classList.contains("hidden"));
    check("Cancel sent nothing to eBay",
      !c.fetchCalls.some((f) => (f.opts.method || "") === "POST"));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== Confirm posts confirmed:true with the quantity and shows the result ==");
  {
    const c = newContext(SRC);
    c.ctx("draft = " + JSON.stringify(DRAFT) + ";");
    c.$("f-quantity").value = "3";
    c.$("btn-update-quantity").click();
    await c.tick(); await c.tick();
    c.resolveFetch("/api/drafts/d1", { draft: DRAFT });
    await c.tick(); await c.tick();
    c.resolveFetch("/api/drafts/d1/revise-quantity", PLAN);
    await c.tick(); await c.tick();
    c.$("btn-revise-qty-confirm").click();
    await c.tick(); await c.tick();
    const post = c.fetchCalls.find((f) => (f.opts.method || "") === "POST");
    check("confirm posts to the revise-quantity endpoint",
      post && String(post.url) === "/api/drafts/d1/revise-quantity");
    check("confirm sends the agreement and the quantity, never a plan",
      post && JSON.parse(post.opts.body).confirmed === true &&
      JSON.parse(post.opts.body).quantity === 3 &&
      Object.keys(JSON.parse(post.opts.body)).length === 2);
    c.resolveFetch("/api/drafts/d1/revise-quantity",
      { ok: true, listing_id: "123",
        listing_url: "https://www.ebay.com/itm/123", live_quantity: 3,
        message: "The live listing now has a quantity of 3." });
    await c.tick(); await c.tick();
    check("success note shows the new quantity with the listing link",
      !c.$("revise-qty-ok").classList.contains("hidden") &&
      c.$("revise-qty-ok").innerHTML.includes("now has a quantity of 3") &&
      c.$("revise-qty-ok").innerHTML.includes("https://www.ebay.com/itm/123"));
    check("confirm card and spinner hide after success",
      c.$("revise-qty-confirm").classList.contains("hidden") &&
      c.$("revise-qty-spinner").classList.contains("hidden"));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== unchanged preview says so, no confirm card ==");
  {
    const c = newContext(SRC);
    c.ctx("draft = " + JSON.stringify(DRAFT) + ";");
    c.$("f-quantity").value = "1";
    c.$("btn-update-quantity").click();
    await c.tick(); await c.tick();
    c.resolveFetch("/api/drafts/d1", { draft: DRAFT });
    await c.tick(); await c.tick();
    c.resolveFetch("/api/drafts/d1/revise-quantity",
      Object.assign({}, PLAN, { live_quantity: 1, requested_quantity: 1,
        unchanged: true,
        changes: ["The live listing already has a quantity of 1."] }));
    await c.tick(); await c.tick();
    check("unchanged preview shows the plain note",
      !c.$("revise-qty-ok").classList.contains("hidden") &&
      c.$("revise-qty-ok").textContent.includes("already has a quantity of 1"));
    check("unchanged preview never opens the confirm card",
      c.$("revise-qty-confirm").classList.contains("hidden"));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== bad quantity shows a plain message and sends nothing ==");
  {
    const c = newContext(SRC);
    c.ctx("draft = " + JSON.stringify(DRAFT) + ";");
    c.$("f-quantity").value = "0";
    c.$("btn-update-quantity").click();
    await c.tick(); await c.tick();
    c.resolveFetch("/api/drafts/d1", { draft: DRAFT });
    await c.tick(); await c.tick();
    check("bad quantity shows the plain message",
      !c.$("revise-qty-error").classList.contains("hidden") &&
      c.$("revise-qty-error").textContent.includes("whole number of 1 or more"));
    check("bad quantity never asks eBay for a preview",
      !c.fetchCalls.some((f) => String(f.url).includes("revise-quantity")));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== preview failure shows the message plainly ==");
  {
    const c = newContext(SRC);
    c.ctx("draft = " + JSON.stringify(DRAFT) + ";");
    c.$("f-quantity").value = "3";
    c.$("btn-update-quantity").click();
    await c.tick(); await c.tick();
    c.resolveFetch("/api/drafts/d1", { draft: DRAFT });
    await c.tick(); await c.tick();
    c.resolveFetch("/api/drafts/d1/revise-quantity",
      { error: "eBay no longer has this listing's offer. It may have been ended or removed on eBay's side. Check Seller Hub." }, false);
    await c.tick(); await c.tick();
    check("preview failure shows eBay's message, no confirm card",
      !c.$("revise-qty-error").classList.contains("hidden") &&
      c.$("revise-qty-error").textContent.includes("no longer has this listing's offer") &&
      c.$("revise-qty-confirm").classList.contains("hidden"));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log(failures === 0 ? "ALL PASS" : failures + " FAILURES");
  process.exit(failures === 0 ? 0 : 1);
}

main().catch((e) => { console.error(e); process.exit(1); });
