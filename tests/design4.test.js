/* apk-v49 node tests for the Diffui design-4 look on all four screens
 * (static/app.js). Loads the REAL app.js in a vm with a
 * REAL-browser-faithful DOM, same pattern as
 * tests/draft-sections.test.js:
 *  - el.children are HTMLCollection stand-ins: indexed items + length
 *    + item(), NO Array methods (exactly like a browser).
 *  - addEventListener records listeners; el.click() dispatches them;
 *    fire(el, type) dispatches any other recorded event.
 *  - document.querySelectorAll walks the created elements by
 *    ancestor chain, so "#attrs .attr-row .ak" finds real inputs.
 *  - fetch is deferred per URL; the test resolves /api/drafts itself.
 * Covers: the step-1 2x2 photo grid (uniform square cells, X +
 * pencil Edit pill on every photo, count line, no Take-another
 * button); the collapsed listing rows with live summaries
 * (Shipping shows carrier/service/lb+oz/dimensions, Item specifics
 * shows row names or "(n rows)", the Shipping row opens itself
 * when a pick screen or create form needs to be seen); the back
 * arrows on step 1 and the listing page (both go to My drafts);
 * the drafts sections with the v49 wording (no listed pill on any
 * row, no Post button on listed rows); and the batch panel
 * (checkbox + thumb + title + date rows, unticking drops a draft
 * from the batch, the confirm card heading and "Post all N"
 * button follow the count, the category starts empty).
 * Run: node tests/design4.test.js (exit 0 = pass). */
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

/* Match one simple selector part (#id, .class, or tag) against an
   element, then walk its ancestors for the earlier parts. */
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

(async () => {
  const { sandbox, $, ctx, tick, fire, fetchCalls, unhandled, dispose } =
    newContext(SRC);

  /* ---------- 1. Step-1 2x2 photo grid ---------- */
  // Mirror the template: #thumbs carries the grid class in v49.
  $("thumbs").className = "thumbs photo-grid";
  ctx("maxPhotos = 24;");
  ctx(`selectedFiles = [
    { url: "blob:a", name: "a.jpg", blob: null },
    { url: "blob:b", name: "b.jpg", blob: null },
    { url: "blob:c", name: "c.jpg", blob: null },
  ];`);
  sandbox.renderThumbs();
  const grid = $("thumbs");
  check("step-1 thumbs live in the 2x2 grid",
    grid.className === "thumbs photo-grid" &&
    grid.children.length === 3);
  check("every grid photo has an X and a pencil Edit pill",
    [0, 1, 2].every((i) => {
      const wrap = grid.children[i];
      const frame = wrap.children[0];
      // Step 1 keeps the overlaid pill inside the frame (design 4);
      // only the listing-page Edit sits below the frame.
      return frame.children[0].tagName === "IMG" &&
        frame.children[1].textContent === "×" &&
        frame.children[2].textContent === "✎ Edit" &&
        frame.children[2].className === "thumb-edit";
    }));
  check("grid count line reads 3 of 24 photos",
    $("photo-count").textContent === "3 of 24 photos");
  check("no Take-another button is wired in v49",
    !$("btn-take-another")._listeners.click ||
    $("btn-take-another")._listeners.click.length === 0);

  /* ---------- 2. Collapsed listing rows + live summaries ---------- */
  // Shipping summary from carrier + service + lb/oz + dimensions.
  $("ship-carrier").value = "USPS";
  $("ship-service").value = "USPSPriority";
  $("p-weight-lb").value = "2";
  $("p-weight-oz").value = "8";
  $("p-length").value = "12";
  $("p-width").value = "9";
  $("p-height").value = "1";
  check("shipping summary shows carrier, service, weight, size",
    sandbox.shippingSummaryText() ===
    "USPS · USPS Priority Mail · 2 lb 8 oz · 12 × 9 × 1 in");
  // Weight alone, no dimensions yet.
  $("p-length").value = ""; $("p-width").value = ""; $("p-height").value = "";
  check("shipping summary without dimensions omits them",
    sandbox.shippingSummaryText() === "USPS · USPS Priority Mail · 2 lb 8 oz");
  // Nothing picked at all.
  $("ship-carrier").value = ""; $("ship-service").value = "";
  $("p-weight-lb").value = ""; $("p-weight-oz").value = "";
  check("shipping summary with nothing set says so",
    sandbox.shippingSummaryText() === "Not set yet.");
  // A saved policy with no carrier pick falls back to its name.
  ctx(`shippingPolicies = [{ id: "278217256016", name: "Free Shipping - Unframed Prints", cost_type: "FLAT_RATE" }];`);
  $("p-fulfillment").value = "278217256016";
  check("shipping summary falls back to the saved policy name",
    sandbox.shippingSummaryText() ===
    "Free Shipping - Unframed Prints · Flat rate.");
  sandbox.updateShippingSummary();
  check("updateShippingSummary writes the summary line",
    $("shipping-summary").textContent ===
    "Free Shipping - Unframed Prints · Flat rate.");

  // Item specifics summary: pure helper + the DOM reader.
  check("attrs summary lists the first three row names",
    sandbox.attrsSummaryText(["Brand", "Material", "Color", "Size"], 4) ===
    "Brand · Material · Color …");
  check("attrs summary falls back to (n rows) with no names",
    sandbox.attrsSummaryText(["", ""], 2) === "(2 rows)" &&
    sandbox.attrsSummaryText([], 1) === "(1 row)");
  // Build the #attrs DOM the way renderAttrs would (rows with .ak
  // inputs) and check the DOM reader follows it.
  const attrsBox = $("attrs");
  const mkRow = (name) => {
    const row = sandbox.document.createElement("div");
    row.className = "attr-row";
    const ak = sandbox.document.createElement("input");
    ak.className = "ak"; ak.value = name;
    row.appendChild(ak);
    attrsBox.appendChild(row);
    return row;
  };
  mkRow("Brand"); mkRow("Material"); mkRow("");
  sandbox.updateAttrsSummary();
  check("updateAttrsSummary reads the live rows",
    $("attrs-summary").textContent === "Brand · Material");

  // The Shipping row opens itself when a pick screen needs it.
  ctx(`shippingPolicies = [{ id: "278217256016", name: "Free Shipping - Unframed Prints", cost_type: "FLAT_RATE" }];`);
  sandbox.openShippingPick([], "That shipping policy number no longer works.");
  check("openShippingPick opens the Shipping row",
    $("row-shipping").open === true);
  check("openShippingPick shows the pick message",
    $("shipping-pick-msg").textContent.includes("no longer works"));

  /* ---------- 3. Back arrows go to My drafts ---------- */
  check("back arrows are wired on both screens",
    ($("btn-back-photos")._listeners.click || []).length === 1 &&
    ($("btn-back-draft")._listeners.click || []).length === 1);
  fetchCalls.length = 0;
  $("btn-back-photos").click();
  await tick();
  check("step-1 back arrow loads My drafts",
    fetchCalls.some((c) => String(c.url).includes("/api/drafts")));

  /* ---------- 4. Drafts sections, v49 wording ---------- */
  // The section flow itself is covered by tests/draft-sections.test.js;
  // here the v49 part is pinned on the row builder directly: a listed
  // draft's row carries no pill and no Post button, and a working
  // draft's row keeps its Post button and carries no pill either.
  const drafts = {
    drafts: [
      { id: "d1", title: "Working doll", updated: "2026-11-08T10:00:00", listed: false, thumb: "d1/1.jpg" },
      { id: "d2", title: "Listed doll", updated: "2026-11-07T09:00:00", listed: true, thumb: "d2/1.jpg" },
      { id: "d3", title: "Listed mitts", updated: "2026-11-06T09:00:00", listed: true, thumb: "d3/1.jpg" },
    ],
  };
  const listedRow = sandbox.makeDraftRow(drafts.drafts[1]);
  check("listed row carries NO listed pill",
    !listedRow.innerHTML.includes("d-listed") &&
    !listedRow.innerHTML.includes("Listed on eBay"));
  check("listed row has NO Post button",
    !listedRow.innerHTML.includes('class="primary post"'));
  check("listed row keeps Open and grey Delete",
    listedRow.innerHTML.includes(">Open<") &&
    listedRow.innerHTML.includes("🗑 Delete"));
  const workingRow = sandbox.makeDraftRow(drafts.drafts[0]);
  check("working row keeps its Post button",
    workingRow.innerHTML.includes('class="primary post"'));
  check("working row carries NO listed pill either",
    !workingRow.innerHTML.includes("d-listed") &&
    !workingRow.innerHTML.includes("Listed on eBay"));

  /* ---------- 5. Batch panel ---------- */
  sandbox.openBatchPanel([
    { id: "d1", title: "Doll", price: "50", updated: "2026-11-08T10:00:00", photos: ["d1/1.jpg"] },
    { id: "d2", title: "Mitts", price: "30", updated: "2026-11-07T09:00:00", photos: [] },
    { id: "d3", title: "Live one", price: "20", updated: "2026-11-06T09:00:00", ebay_listing_id: "2371", photos: ["d3/1.jpg"] },
    { id: "d4", title: "No price", price: "", updated: "2026-11-05T09:00:00", photos: ["d4/1.jpg"] },
  ]);
  const rows = $("batch-review-list").children;
  check("batch shows the two ready drafts, not the skipped ones",
    rows.length === 2);
  check("batch row 1 has a checked box, a thumb, a title and a date",
    rows[0].children[0].tagName === "INPUT" &&
    rows[0].children[0].checked === true &&
    rows[0].children[1].tagName === "IMG" &&
    rows[0].children[1].src === "/photos/d1/1.jpg" &&
    rows[0].children[2].innerHTML.includes("Doll") &&
    rows[0].children[2].innerHTML.includes("2026-11-08"));
  check("batch row 2 has no thumb but still title and date",
    rows[1].children[0].tagName === "INPUT" &&
    rows[1].children[1].className === "bt" &&
    rows[1].children[1].innerHTML.includes("Mitts"));
  // The container's innerHTML does not reflect appended children in
  // this harness (innerHTML is a plain string store), so read the
  // excluded rows themselves.
  const excRows = $("batch-excluded").children;
  check("batch excluded list names the live draft and the no-price draft",
    excRows.length === 2 &&
    excRows[0].innerHTML.includes("Live one") &&
    excRows[1].innerHTML.includes("No price"));
  check("confirm card heading reads 2 drafts, 1 category",
    $("batch-confirm-heading").textContent === "2 drafts, 1 category");
  check("confirm button reads Post all 2 and is disabled without a category",
    $("btn-batch-confirm").textContent === "Post all 2" &&
    $("btn-batch-confirm").disabled === true);
  check("batch category starts empty (the 123456 is a placeholder, never a value)",
    $("b-category").value === "");
  $("b-category").value = "178893";
  sandbox.updateBatchConfirm();
  check("typing a category enables Post all 2",
    $("btn-batch-confirm").disabled === false);
  // Unticking the first row drops it from the batch.
  const box1 = rows[0].children[0];
  box1.checked = false;
  fire(box1, "change");
  check("unticking a row drops it from the batch",
    $("batch-confirm-heading").textContent === "1 draft, 1 category" &&
    $("btn-batch-confirm").textContent === "Post all 1");
  box1.checked = true;
  fire(box1, "change");
  check("re-ticking puts the draft back in the batch",
    $("batch-confirm-heading").textContent === "2 drafts, 1 category" &&
    $("btn-batch-confirm").textContent === "Post all 2");

  /* ---------- 6. USPS Ground Advantage sends USPSParcel (v49 fold-in) ----------
     The Nov 8 live create failed with 20403 UNKNOWN_SHIPPING_SERVICE
     because the form sent USPSGroundAdvantage; eBay's LSAS checker
     rejects it on this account, and eBay's own code for the same
     service is USPSParcel. */
  const labels = sandbox.carrierServiceLabels();
  check("picker catalog has 10 codes, Ground Advantage as USPSParcel",
    ["USPSParcel", "USPSPriority", "UPSGround", "UPS3rdDay",
     "UPS2DayAirAM", "UPSNextDay", "FedExGround", "FedExHomeDelivery",
     "FedEx2Day", "FedExExpressSaver"].every((c) => c in labels));
  check("USPSParcel is labeled USPS Ground Advantage in the picker",
    labels.USPSParcel === "USPS Ground Advantage");
  check("the rejected code is not offered in the picker",
    !("USPSGroundAdvantage" in labels));
  check("both codes read as USPS Ground Advantage",
    sandbox.createServiceLabel("USPSParcel") === "USPS Ground Advantage" &&
    sandbox.createServiceLabel("USPSGroundAdvantage") === "USPS Ground Advantage");
  check("canonicalServiceCode maps the old code, passes the rest through",
    sandbox.canonicalServiceCode("USPSGroundAdvantage") === "USPSParcel" &&
    sandbox.canonicalServiceCode("USPSParcel") === "USPSParcel" &&
    sandbox.canonicalServiceCode("FedExSmartPost") === "FedExSmartPost" &&
    sandbox.canonicalServiceCode("") === "");
  check("create form maps a picked Ground Advantage to USPSParcel",
    sandbox.createServiceCodeFor("dom", 1,
      { dom_service_1: "USPSGroundAdvantage" }) === "USPSParcel");
  check("create form maps a typed Ground Advantage to USPSParcel",
    sandbox.createServiceCodeFor("dom", 1,
      { dom_service_1: "__OTHER__",
        dom_service_1_other: "USPSGroundAdvantage" }) === "USPSParcel");
  check("create form leaves FedEx Ground Economy as-is (unproven, not guessed away)",
    sandbox.createServiceCodeFor("dom", 2,
      { dom_service_2: "FedExSmartPost" }) === "FedExSmartPost");
  // A draft saved with the old code restores onto the USPSParcel option.
  ctx(`carrierServicesFetched = true; carrierServices = { USPS: [{ code: "USPSParcel", label: "USPS Ground Advantage" }, { code: "USPSPriority", label: "USPS Priority Mail" }], UPS: [], FedEx: [] };`);
  $("ship-carrier").value = "USPS";
  sandbox.populateShipService();
  sandbox.restoreShipService("USPSGroundAdvantage");
  check("restore of an old saved Ground Advantage selects USPSParcel",
    $("ship-service").value === "USPSParcel");
  $("ship-service").value = "USPSParcel";
  check("shipping summary names the service USPS Ground Advantage",
    sandbox.shippingSummaryText().includes("USPS Ground Advantage"));

  if (unhandled.length) {
    failures++;
    console.log("FAIL: unhandled rejections:", unhandled);
  }
  console.log(failures ? `design4: ${failures} FAILURE(S)` : "design4: all checks pass");
  dispose();
  process.exit(failures ? 1 : 0);
})();
