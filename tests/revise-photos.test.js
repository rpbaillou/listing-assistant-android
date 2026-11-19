/* apk-v57/v64 node tests for the live-listing update (static/app.js).
 * Loads the REAL app.js in a vm with a REAL-browser-faithful DOM, same
 * pattern as tests/draft-sections.test.js. v57 covered the photos-only
 * update; v64 merged the photos and quantity updates into one button,
 * one preview, and one confirm card. Covers: listed rows get an
 * "Update listing" button (unlisted rows keep Post and get no Update
 * button); the listing page shows the one Update live listing button
 * only for listed drafts, and hides List there; tapping it asks eBay
 * for the combined preview and shows both parts on the confirm card;
 * Cancel sends nothing; confirming posts confirmed:true with the
 * quantity and shows the success note with the listing link; an
 * unchanged preview says so plainly with no confirm card; a preview
 * failure shows eBay's message plainly; a partial failure shows the
 * success note for the part that went through and the error note for
 * the part that failed; a bad quantity shows the plain message and
 * sends nothing.
 * Run: node tests/revise-photos.test.js (exit 0 = pass). */
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

const LISTED = { id: "d1", title: "Listed doll", listed: true,
  updated: "2026-11-08T10:00:00", thumb: "d1/1.jpg",
  ebay_listing_id: "123", ebay_listing_url: "https://www.ebay.com/itm/123" };
const UNLISTED = { id: "d2", title: "Working draft", listed: false,
  updated: "2026-11-08T09:00:00", thumb: "d2/1.jpg" };
const DRAFT = { id: "d1", title: "Listed doll", photos: ["d1/1.jpg", "d1/2.jpg"],
  ebay_listing_id: "123", ebay_listing_url: "https://www.ebay.com/itm/123" };
/* v64: one preview plans both parts. The card lists the photo change
   and the quantity change together. */
const PLAN = { listing_id: "123",
  listing_url: "https://www.ebay.com/itm/123", title: "Listed doll",
  photos: { changes: ["Photo 2 was edited and will be uploaded."],
    unchanged: false, error: null },
  quantity: { changes: ["Quantity changes from 1 to 3."],
    unchanged: false, live_quantity: 1, requested_quantity: 3,
    error: null },
  unchanged: false,
  changes: ["Photo 2 was edited and will be uploaded.",
    "Quantity changes from 1 to 3."] };
const RESULT = { ok: true, listing_id: "123",
  listing_url: "https://www.ebay.com/itm/123",
  photos: { ok: true, unchanged: false,
    message: "The live listing now shows the new photos.",
    uploaded: 1, removed: 0 },
  quantity: { ok: true, unchanged: false, live_quantity: 3,
    message: "The live listing now has a quantity of 3." },
  message: "The live listing now shows the new photos. The live " +
    "listing now has a quantity of 3." };

async function openDrafts(c, drafts) {
  c.$("btn-drafts").click();
  await c.tick(); await c.tick();
  c.resolveFetch("/api/drafts", { drafts });
  await c.tick(); await c.tick(); await c.tick();
  return c.$("drafts-list");
}

async function main() {
  console.log("== listed rows get Update listing, unlisted keep Post ==");
  {
    const c = newContext(SRC);
    const list = await openDrafts(c, [UNLISTED, LISTED]);
    const top = list.children[0];
    const bottom = list.children[1];
    const workingRow = top.children[1].innerHTML;
    const listedRow = bottom.children[1].children[0].innerHTML;
    check("unlisted row keeps a Post button",
      workingRow.includes('class="primary post"'));
    check("unlisted row has no Update button",
      !workingRow.includes("Update listing"));
    check("listed row has an Update listing button",
      listedRow.includes("Update listing") &&
      listedRow.includes('class="secondary update-listing"'));
    check("listed row has no old Update photos button",
      !listedRow.includes("Update photos"));
    check("listed row still has no Post button",
      !listedRow.includes('class="primary post"'));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== listing page shows one Update button only for listed drafts ==");
  {
    const c = newContext(SRC);
    c.ctx("draft = " + JSON.stringify(DRAFT) + ";");
    const p = c.ctx("refreshEbayStatus();");
    await c.tick();
    c.resolveFetch("/api/ebay/status", { ebay_configured: true,
      connected: true, runame: "Reynard_Baillou-ReynardB-Mr-PRD-eayxussz",
      policies_configured: true });
    await p; await c.tick();
    check("listed draft hides List, shows the one Update live listing button",
      c.$("btn-publish").classList.contains("hidden") &&
      !c.$("btn-update-listing").classList.contains("hidden"));
    c.ctx("draft = { id: 'd2', title: 'Working draft', photos: ['d2/1.jpg'] };");
    const p2 = c.ctx("refreshEbayStatus();");
    await c.tick();
    c.resolveFetch("/api/ebay/status", { ebay_configured: true,
      connected: true, runame: "Reynard_Baillou-ReynardB-Mr-PRD-eayxussz",
      policies_configured: true });
    await p2; await c.tick();
    check("unlisted draft shows List, hides Update",
      !c.$("btn-publish").classList.contains("hidden") &&
      c.$("btn-update-listing").classList.contains("hidden"));
    check("neither old button id survives in app.js",
      !SRC.includes("btn-update-photos") &&
      !SRC.includes("btn-update-quantity") &&
      !SRC.includes("revise-qty"));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== Update shows the one confirm card with both parts; Cancel sends nothing ==");
  {
    const c = newContext(SRC);
    c.ctx("draft = " + JSON.stringify(DRAFT) + ";");
    c.$("f-quantity").value = "3";
    c.$("btn-update-listing").click();
    await c.tick(); await c.tick();
    // The handler saves the draft first (PUT), then asks for the preview.
    c.resolveFetch("/api/drafts/d1", { draft: DRAFT });
    await c.tick(); await c.tick();
    check("preview is a GET on the combined revise endpoint with the quantity",
      c.fetchCalls.some((f) => String(f.url) === "/api/drafts/d1/revise?quantity=3" &&
        (f.opts.method || "GET") === "GET"));
    c.resolveFetch("/api/drafts/d1/revise", PLAN);
    await c.tick(); await c.tick();
    check("confirm card opens with both changes listed",
      !c.$("revise-confirm").classList.contains("hidden") &&
      c.$("revise-changes").children.length === 2 &&
      c.$("revise-changes").children[0].textContent.includes("Photo 2 was edited") &&
      c.$("revise-changes").children[1].textContent.includes("Quantity changes from 1 to 3."));
    c.$("btn-revise-cancel").click();
    check("Cancel hides the card",
      c.$("revise-confirm").classList.contains("hidden"));
    check("Cancel sent nothing to eBay",
      !c.fetchCalls.some((f) => (f.opts.method || "") === "POST"));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== Confirm posts confirmed:true with the quantity and shows both results ==");
  {
    const c = newContext(SRC);
    c.ctx("draft = " + JSON.stringify(DRAFT) + ";");
    c.$("f-quantity").value = "3";
    c.$("btn-update-listing").click();
    await c.tick(); await c.tick();
    c.resolveFetch("/api/drafts/d1", { draft: DRAFT });
    await c.tick(); await c.tick();
    c.resolveFetch("/api/drafts/d1/revise", PLAN);
    await c.tick(); await c.tick();
    c.$("btn-revise-confirm").click();
    await c.tick(); await c.tick();
    const post = c.fetchCalls.find((f) => (f.opts.method || "") === "POST");
    check("confirm posts to the combined revise endpoint",
      post && String(post.url) === "/api/drafts/d1/revise");
    check("confirm sends the agreement and the quantity, never a plan",
      post && JSON.parse(post.opts.body).confirmed === true &&
      JSON.parse(post.opts.body).quantity === 3 &&
      Object.keys(JSON.parse(post.opts.body)).length === 2);
    c.resolveFetch("/api/drafts/d1/revise", RESULT);
    await c.tick(); await c.tick();
    check("success note names both parts with the listing link",
      !c.$("revise-ok").classList.contains("hidden") &&
      c.$("revise-ok").innerHTML.includes("now shows the new photos") &&
      c.$("revise-ok").innerHTML.includes("quantity of 3") &&
      c.$("revise-ok").innerHTML.includes("https://www.ebay.com/itm/123"));
    check("no error note on a full success",
      c.$("revise-error").classList.contains("hidden"));
    check("confirm card and spinner hide after success",
      c.$("revise-confirm").classList.contains("hidden") &&
      c.$("revise-spinner").classList.contains("hidden"));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== unchanged preview says so, no confirm card ==");
  {
    const c = newContext(SRC);
    c.ctx("draft = " + JSON.stringify(DRAFT) + ";");
    c.$("f-quantity").value = "3";
    c.$("btn-update-listing").click();
    await c.tick(); await c.tick();
    c.resolveFetch("/api/drafts/d1", { draft: DRAFT });
    await c.tick(); await c.tick();
    c.resolveFetch("/api/drafts/d1/revise",
      Object.assign({}, PLAN, { unchanged: true,
        photos: { changes: ["No photo changes. The live listing already shows these photos."],
          unchanged: true, error: null },
        quantity: { changes: ["The live listing already has a quantity of 3."],
          unchanged: true, live_quantity: 3, requested_quantity: 3, error: null },
        changes: ["No photo changes. The live listing already shows these photos.",
          "The live listing already has a quantity of 3."] }));
    await c.tick(); await c.tick();
    check("unchanged preview shows the plain note",
      !c.$("revise-ok").classList.contains("hidden") &&
      c.$("revise-ok").textContent.includes("already shows these photos") &&
      c.$("revise-ok").textContent.includes("already has a quantity of 3"));
    check("unchanged preview never opens the confirm card",
      c.$("revise-confirm").classList.contains("hidden"));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== preview failure shows the message plainly ==");
  {
    const c = newContext(SRC);
    c.ctx("draft = " + JSON.stringify(DRAFT) + ";");
    c.$("f-quantity").value = "3";
    c.$("btn-update-listing").click();
    await c.tick(); await c.tick();
    c.resolveFetch("/api/drafts/d1", { draft: DRAFT });
    await c.tick(); await c.tick();
    c.resolveFetch("/api/drafts/d1/revise",
      { error: "eBay no longer has this item. It may have been ended, sold, or removed on eBay's side. Check Seller Hub." }, false);
    await c.tick(); await c.tick();
    check("preview failure shows eBay's message, no confirm card",
      !c.$("revise-error").classList.contains("hidden") &&
      c.$("revise-error").textContent.includes("no longer has this item") &&
      c.$("revise-confirm").classList.contains("hidden"));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== partial failure shows both notes, success and failure ==");
  {
    const c = newContext(SRC);
    c.ctx("draft = " + JSON.stringify(DRAFT) + ";");
    c.$("f-quantity").value = "3";
    c.$("btn-update-listing").click();
    await c.tick(); await c.tick();
    c.resolveFetch("/api/drafts/d1", { draft: DRAFT });
    await c.tick(); await c.tick();
    c.resolveFetch("/api/drafts/d1/revise", PLAN);
    await c.tick(); await c.tick();
    c.$("btn-revise-confirm").click();
    await c.tick(); await c.tick();
    c.resolveFetch("/api/drafts/d1/revise",
      { ok: true, listing_id: "123",
        listing_url: "https://www.ebay.com/itm/123",
        photos: { ok: true, unchanged: false,
          message: "The live listing now shows the new photos.",
          uploaded: 1, removed: 0 },
        quantity: { ok: false,
          error: "eBay said (error 21919302): The listing cannot be revised because it has bids." },
        message: "The live listing now shows the new photos. But the quantity did not change: eBay said (error 21919302): The listing cannot be revised because it has bids." });
    await c.tick(); await c.tick();
    check("success note still names the photos that went through",
      !c.$("revise-ok").classList.contains("hidden") &&
      c.$("revise-ok").innerHTML.includes("now shows the new photos"));
    check("error note names the failed quantity with eBay's message",
      !c.$("revise-error").classList.contains("hidden") &&
      c.$("revise-error").textContent.includes("quantity did not change") &&
      c.$("revise-error").textContent.includes("has bids"));
    check("confirm card and spinner hide after a partial result",
      c.$("revise-confirm").classList.contains("hidden") &&
      c.$("revise-spinner").classList.contains("hidden"));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== bad quantity shows a plain message and sends nothing ==");
  {
    const c = newContext(SRC);
    c.ctx("draft = " + JSON.stringify(DRAFT) + ";");
    c.$("f-quantity").value = "0";
    c.$("btn-update-listing").click();
    await c.tick(); await c.tick();
    c.resolveFetch("/api/drafts/d1", { draft: DRAFT });
    await c.tick(); await c.tick();
    check("bad quantity shows the plain message",
      !c.$("revise-error").classList.contains("hidden") &&
      c.$("revise-error").textContent.includes("whole number of 1 or more"));
    check("bad quantity never asks eBay for a preview",
      !c.fetchCalls.some((f) => String(f.url).includes("/revise")));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log(failures === 0 ? "revise-photos: all pass"
    : "revise-photos: " + failures + " FAILURES");
  process.exit(failures === 0 ? 0 : 1);
}

main().catch((e) => { console.log("ERROR: " + (e && e.stack || e)); process.exit(1); });
