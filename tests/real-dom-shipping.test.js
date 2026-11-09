/* apk-v43 real-DOM regression suite for the shipping flows.
 * Promoted from the Nov 8 whole-shipping audit's reproduction
 * harness (/tmp/audit-repro/repro.js), with every assertion flipped
 * to the FIXED behavior. Loads the REAL app.js in a vm with a
 * REAL-browser-faithful DOM:
 *  - el.children / el.options are HTMLCollection stand-ins: indexed
 *    items + length + item(), NO Array methods (.some/.map/.find/
 *    .forEach/.filter/.includes all absent, exactly like a browser).
 *  - select.value validates against the current options: setting a
 *    value with no matching option lands on "" (real select behavior).
 *  - addEventListener records listeners; fireClick(id) dispatches them.
 *  - fetch is deferred per URL so the two parallel page-load fetches
 *    can resolve in either order (the real race on his phone).
 * Covers: restore-on-load in BOTH fetch orders, the error path and
 * BOTH Try again buttons (shipping retry, create-form retry),
 * openCreateFormPrefilled direct, pick-mode re-post, browse-mode
 * pick, entering pick mode, confirm-after-create (including a
 * post-create select failure), the "It is live!" note surviving
 * prefillListing, batch buttons without IDs, and the flat-policy
 * offer. The Python guard (tests/test_js_collection_guard.py) is the
 * static backstop for the same bug class.
 * Run: node tests/real-dom-shipping.test.js
 */
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

const SELECT_IDS = new Set([
  "ship-carrier", "ship-service", "shipping-select",
  "pc-dom-cost-type", "pc-dom-service-1", "pc-dom-service-2",
  "pc-intl-cost-type", "pc-intl-service-1", "pc-intl-service-2",
  "f-condition",
]);

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
  const el = {
    _kids: kids,
    _listeners: listeners,
    _isSelect: SELECT_IDS.has(id) || tag === "select",
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
    appendChild(child) { kids.push(child); return child; },
    querySelector() { return makeEl("", "div"); },
    querySelectorAll() { return []; },
    getContext() { return null; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 100 }; },
    scrollIntoView() {},
    setPointerCapture() {},
    releasePointerCapture() {},
    focus() {},
    click() { (listeners.click || []).forEach((f) => f({})); },
  };
  Object.defineProperty(el, "children", {
    get() { return makeCollection(kids); }, enumerable: true, configurable: true,
  });
  Object.defineProperty(el, "options", {
    get() { return makeCollection(kids); }, enumerable: true, configurable: true,
  });
  Object.defineProperty(el, "value", {
    get() { return el._value; },
    set(v) {
      v = v == null ? "" : String(v);
      if (el._isSelect && v !== "") {
        const found = kids.some((k) => k.value === v);
        el._value = found ? v : ""; // real select: no match -> ""
      } else { el._value = v; }
    },
    enumerable: true, configurable: true,
  });
  let _html = "";
  Object.defineProperty(el, "innerHTML", {
    get() { return _html; },
    set(v) {
      _html = v; kids.length = 0;
      // Real DOM: innerHTML also determines textContent (tags stripped).
      el.textContent = String(v).replace(/<[^>]*>/g, "");
    },
    enumerable: true, configurable: true,
  });
  return el;
}

function newContext(source) {
  const registry = {};
  const fetchCalls = [];
  const pending = []; // {url, opts, resolve}
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
  const hidden = (id) => $(id).classList.contains("hidden");
  const setCtx = (code) => vm.runInContext(code, sandbox);
  const resolveFetch = (urlPart, json, ok = true) => {
    const i = pending.findIndex((p) => String(p.url).includes(urlPart));
    if (i < 0) throw new Error("no pending fetch for " + urlPart +
      " (pending: " + pending.map((p) => p.url).join(", ") + ")");
    const [p] = pending.splice(i, 1);
    p.resolve({ ok, status: ok ? 200 : 500,
      text: async () => JSON.stringify(json) });
  };
  // Mirror the template: these start hidden (index.html class="... hidden").
  ["ai-notes", "analyze-error", "batch-note", "batch-panel",
   "batch-policy-pick-error", "batch-policy-pick-form", "batch-progress",
   "batch-scope-box", "batch-ship-from-error", "batch-ship-from-form",
   "batch-suggestions", "best-offer-fields", "btn-batch-done",
   "cat-saved", "cat-suggestions",
   "condition-fetch-hint", "copy-ok", "draft-photo-error",
   "draft-photo-note", "drafts-panel", "ebay-banner", "editor",
   "key-warning", "listing-note", "pc-dom-service-1-other",
   "pc-dom-service-2-other", "pc-intl", "pc-intl-service-1-other",
   "pc-intl-service-2-other", "pc-review-warning", "policy-create-error",
   "policy-create-form", "policy-create-note", "policy-create-review",
   "preset-note", "publish-error", "publish-ok", "publish-spinner",
   "scope-reconnect-box", "ship-from-error", "ship-from-form",
   "ship-service-note", "ship-service-other", "shipping-error",
   "shipping-pick-msg", "skip-note", "spinner", "step-draft"]
    .forEach((id) => $(id).classList.add("hidden"));
  // Mirror the template's STATIC select options (index.html).
  const addOption = (selId, value, text) => {
    const o = sandbox.document.createElement("option");
    o.value = value; o.textContent = text;
    $(selId).appendChild(o);
  };
  addOption("ship-carrier", "", "— Pick a carrier —");
  addOption("ship-carrier", "USPS", "USPS");
  addOption("ship-carrier", "UPS", "UPS");
  addOption("ship-carrier", "FedEx", "FedEx");
  addOption("ship-service", "", "— Pick a carrier first —");
  addOption("shipping-select", "", "— Pick a shipping policy —");
  addOption("pc-dom-cost-type", "FLAT_RATE", "Flat rate");
  addOption("pc-dom-cost-type", "CALCULATED", "Calculated (by weight)");
  addOption("pc-intl-cost-type", "FLAT_RATE", "Flat rate");
  addOption("pc-intl-cost-type", "CALCULATED", "Calculated (by weight)");
  // app.js fires GET /api/config on load (line 1402); drain it.
  const ci = pending.findIndex((p) => String(p.url).includes("/api/config"));
  if (ci >= 0) {
    const [p] = pending.splice(ci, 1);
    p.resolve({ ok: true, status: 200, text: async () => "{}" });
  }
  const tick = () => new Promise((r) => setTimeout(r, 0));
  const fireClick = async (id) => {
    $(id).click();
    await tick(); await tick();
  };
  const dispose = () => process.removeListener("unhandledRejection", onUnhandled);
  return { sandbox, $, hidden, setCtx, resolveFetch, tick, fireClick,
    fetchCalls, pending, unhandled, dispose };
}

/* ---- fixtures (dummy IDs; shapes mirror the real account) ---- */
const POLICIES = [
  { id: "111", name: "Calculated: USPSParcel", cost_type: "CALCULATED",
    service_code: "USPSParcel", carrier: "USPS" },
  { id: "222", name: "Free Shipping - Unframed Prints", cost_type: "FLAT_RATE",
    service_code: "USPSParcel", carrier: "USPS" },
  { id: "333", name: "Calculated 2lb - Framed Prints", cost_type: "CALCULATED",
    service_code: "UPSGroundSaver", carrier: "UPS" },
];
const CARRIERS = {
  USPS: [{ code: "USPSParcel", label: "USPS Ground Advantage" },
         { code: "USPSPriority", label: "USPS Priority Mail" }],
  UPS: [{ code: "UPSGround", label: "UPS Ground" }],
  FedEx: [{ code: "FedExGround", label: "FedEx Ground" },
          { code: "FedEx2Day", label: "FedEx 2Day" }],
};

let failures = 0;
function check(name, cond, extra) {
  if (!cond) { failures++; console.log("FAIL: " + name + (extra ? " -- " + extra : "")); }
  else { console.log("ok:   " + name); }
}

async function main() {
  console.log("== sanity: the mock collection really has no Array methods ==");
  {
    const c = newContext(SRC);
    const box = c.sandbox.document.createElement("div");
    const o = c.sandbox.document.createElement("option");
    o.value = "USPSPriority"; o.textContent = "USPS Priority Mail";
    box.appendChild(o);
    check("children.length is 1", box.children.length === 1);
    check("children[0] is the option", box.children[0] === o);
    check("children.some is NOT a function (real browser)",
      typeof box.children.some !== "function");
    check("children.map is NOT a function", typeof box.children.map !== "function");
    const sel = c.$("ship-service");
    sel.appendChild(o); // list the option on the select itself
    check("select.value validates against options (no match -> '')",
      (() => { sel.value = "Nope"; return sel.value === ""; })());
    check("select.value accepts a listed option",
      (() => { sel.value = "USPSPriority"; return sel.value === "USPSPriority"; })());
    check("source keeps the Array.from wrap at both fixed sites",
      SRC.includes("Array.from(sel.children || []).some(") &&
      SRC.includes("Array.from(dom1.children || []).some("));
    c.dispose();
  }

  console.log("\n== A: services fetch resolves FIRST (the screenshot race) ==");
  {
    const c = newContext(SRC);
    c.$("p-fulfillment").value = "111";
    c.setCtx(`shippingPolicies = []; carrierServicesFetched = false; carrierServices = {USPS:[],UPS:[],FedEx:[]};`);
    c.sandbox.populateShipService();
    c.sandbox.restoreShipService("USPSGroundAdvantage"); // v49: an old saved
    // code parks canonicalized as USPSParcel, never the rejected code
    check("restore parks the code while services unfetched",
      c.setCtx("pendingShipService") === "USPSParcel");
    // services fetch has now resolved (carrierServicesFetched=true)
    c.setCtx(`carrierServicesFetched = true; carrierServices = ${JSON.stringify(CARRIERS)};`);
    c.sandbox.fetchShippingPolicies();
    c.resolveFetch("fulfillment-policies", { policies: POLICIES });
    await c.tick(); await c.tick();
    check("no pink error when services win the race", c.hidden("shipping-error"));
    check("policy dropdown populated (placeholder + 3 policies)",
      c.$("shipping-select").children.length === 4);
    check("saved policy IS selected in the dropdown",
      c.$("shipping-select").value === "111");
    check("carrier/service pickers synced to the saved policy",
      c.$("ship-service").value === "USPSParcel");
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("\n== B: policies fetch resolves FIRST (the silent variant) ==");
  {
    const c = newContext(SRC);
    c.$("p-fulfillment").value = "111";
    c.setCtx(`shippingPolicies = []; carrierServicesFetched = false; carrierServices = {USPS:[],UPS:[],FedEx:[]};`);
    c.sandbox.populateShipService();
    c.sandbox.restoreShipService("USPSGroundAdvantage"); // v49: old saved code
    c.sandbox.fetchShippingPolicies();
    c.resolveFetch("fulfillment-policies", { policies: POLICIES });
    await c.tick(); await c.tick();
    check("no pink error when policies win the race",
      c.hidden("shipping-error"));
    check("code still parked as pending", c.setCtx("pendingShipService") === "USPSParcel");
    // services fetch resolves afterwards; restore applies the parked code.
    c.sandbox.fetchCarrierServices();
    c.resolveFetch("shipping-services", { carriers: CARRIERS });
    await c.tick(); await c.tick();
    check("still no error shown", c.hidden("shipping-error"));
    check("service picker WAS restored", c.$("ship-service").value === "USPSParcel");
    check("pending code cleared after a successful restore",
      c.setCtx("pendingShipService") === "");
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("\n== C: browse-mode policy pick saves the number and syncs ==");
  {
    const c = newContext(SRC);
    c.setCtx(`shippingPolicies = ${JSON.stringify(POLICIES)}; carrierServicesFetched = true; carrierServices = ${JSON.stringify(CARRIERS)}; draft = {id: 'd1'};`);
    c.$("p-fulfillment").value = "111";
    c.sandbox.renderShippingDropdown(); // real page: policies fetched, dropdown rendered
    let setupThrew = null;
    try { c.sandbox.refreshShippingChooser(); } catch (e) { setupThrew = e; }
    check("refreshShippingChooser does NOT throw on a real select", setupThrew === null,
      setupThrew && setupThrew.message);
    c.$("ship-carrier").value = "USPS";
    c.sandbox.populateShipService();
    c.$("ship-service").value = "USPSParcel";
    let threw = null;
    const p = c.sandbox.selectShippingPolicy("333").catch((e) => { threw = e; });
    c.resolveFetch("/api/drafts/d1", { draft: { id: "d1" } });
    await p; await c.tick();
    check("selectShippingPolicy resolves (no throw)", threw === null,
      threw && threw.message);
    check("the number WAS saved (p-fulfillment=333)",
      c.$("p-fulfillment").value === "333");
    check("dropdown + type line updated",
      c.$("shipping-select").value === "333" &&
      c.$("shipping-type-line").textContent === "Charges by weight.");
    // 333 carries UPSGroundSaver, which is not in the harvested UPS list,
    // so it lands in the Other box — that IS the sync working.
    check("carrier/service pickers synced to the new policy",
      c.$("ship-carrier").value === "UPS" &&
      c.$("ship-service").value === "__OTHER__" &&
      c.$("ship-service-other").value === "UPSGroundSaver");
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("\n== C2: pick-mode re-post fires ==");
  {
    const c = newContext(SRC);
    c.setCtx(`shippingPolicies = ${JSON.stringify(POLICIES)}; carrierServicesFetched = true; carrierServices = ${JSON.stringify(CARRIERS)}; draft = {id: 'd1'}; pendingPublishBody = {draft_id: 'd1', title: 'T', price: '10.00'}; shippingPickMode = true;`);
    c.$("p-fulfillment").value = "111";
    c.sandbox.renderShippingDropdown();
    let reposted = null;
    c.sandbox.postPublish = async (body) => { reposted = body; return "listed"; };
    let threw = null;
    const p = c.sandbox.selectShippingPolicy("222").catch((e) => { threw = e; });
    c.resolveFetch("/api/drafts/d1", { draft: { id: "d1" } });
    await p; await c.tick();
    check("pick-mode select resolves (no throw)", threw === null,
      threw && threw.message);
    check("the confirmed re-post FIRED with the picked number",
      reposted && reposted.fulfillment_policy_id === "222",
      "reposted=" + JSON.stringify(reposted));
    check("pick mode is off after the re-post", c.setCtx("shippingPickMode") === false);
    c.dispose();
  }

  console.log("\n== C3: entering pick mode works ==");
  {
    const c = newContext(SRC);
    c.setCtx(`carrierServicesFetched = true; carrierServices = ${JSON.stringify(CARRIERS)}; draft = {id: 'd1'};`);
    c.$("p-fulfillment").value = "111";
    c.sandbox.renderShippingDropdown();
    let threw = null;
    try { c.sandbox.openShippingPick(POLICIES, "Pick one below.", ["Pick a condition first."]); }
    catch (e) { threw = e; }
    check("openShippingPick does NOT throw", threw === null,
      threw && threw.message);
    check("pick message IS set and visible",
      !c.hidden("shipping-pick-msg") &&
      c.$("shipping-pick-msg").textContent.includes("Pick one below."));
    check("pick mode is on", c.setCtx("shippingPickMode") === true);
    c.dispose();
  }

  console.log("\n== D: no-match carrier pick -> prefilled create form ==");
  {
    const c = newContext(SRC);
    c.setCtx(`shippingPolicies = ${JSON.stringify(POLICIES)}; carrierServicesFetched = true; carrierServices = ${JSON.stringify(CARRIERS)}; draft = {id: 'd1'}; createServicesFetched = true;`);
    c.$("ship-carrier").value = "FedEx";
    c.sandbox.populateShipService();
    c.$("ship-service").value = "FedEx2Day";
    // Real phone: openCreateForm's fetch already populated the create
    // service selects, so dom1.children is NON-empty there too.
    c.sandbox.populateServiceSelect("pc-dom-service-1", CARRIERS.FedEx);
    let threw = null;
    try { await c.sandbox.openCreateFormPrefilled("FedEx", "FedEx2Day"); }
    catch (e) { threw = e; }
    await c.tick();
    check("openCreateFormPrefilled completes (no throw)", threw === null,
      threw && threw.message);
    check("name WAS set (a suggestion, still editable)",
      c.$("pc-name").value.includes("FedEx"));
    check("cost type WAS set to CALCULATED",
      c.$("pc-dom-cost-type").value === "CALCULATED");
    check("the service WAS set", c.$("pc-dom-service-1").value === "FedEx2Day");
    check("visibility refresh RAN (cost wrap hidden for CALCULATED)",
      c.hidden("pc-dom-cost-1-wrap"));
    check("no unhandled rejection", c.unhandled.length === 0);
    // un-awaited path: onShipServicePicked calls it without await/catch.
    const c2 = newContext(SRC);
    c2.setCtx(`shippingPolicies = ${JSON.stringify(POLICIES)}; carrierServicesFetched = true; carrierServices = ${JSON.stringify(CARRIERS)}; draft = {id: 'd1'}; createServicesFetched = true;`);
    c2.$("ship-carrier").value = "FedEx";
    c2.sandbox.populateShipService();
    c2.$("ship-service").value = "FedEx2Day";
    c2.sandbox.populateServiceSelect("pc-dom-service-1", CARRIERS.FedEx);
    c2.sandbox.onShipServicePicked();
    await c2.tick();
    c2.resolveFetch("/api/drafts/d1", { draft: { id: "d1" } }); // onShipServicePicked saves first
    await c2.tick();
    c2.resolveFetch("policy-for-service", { policy_id: null });
    await c2.tick(); await c2.tick(); await c2.tick();
    check("onShipServicePicked: no unhandled rejection",
      c2.unhandled.length === 0,
      "unhandled: " + JSON.stringify(c2.unhandled.map((e) => String(e && e.message))));
    check("onShipServicePicked: honest no-match note shows",
      !c2.hidden("ship-service-note") &&
      c2.$("ship-service-note").textContent.includes("No policy with that service yet"));
    check("onShipServicePicked: create form opened prefilled",
      c2.$("pc-name").value.includes("FedEx") &&
      c2.$("pc-dom-service-1").value === "FedEx2Day");
    c2.dispose();
    c.dispose();
  }

  console.log("\n== E: policy created on eBay, UI says so ==");
  {
    const c = newContext(SRC);
    c.setCtx(`shippingPolicies = ${JSON.stringify(POLICIES)}; carrierServicesFetched = true; carrierServices = ${JSON.stringify(CARRIERS)}; draft = {id: 'd1'}; pendingCreateForm = {name: 'Test Policy'}; createRetry = false;`);
    c.$("pc-name").value = "Test Policy";
    c.$("pc-dom-cost-type").value = "CALCULATED";
    c.sandbox.populateServiceSelect("pc-dom-service-1", CARRIERS.USPS);
    c.$("pc-dom-service-1").value = "USPSPriority";
    c.$("pc-dom-handling").value = "2";
    const NEW_POLICIES = POLICIES.concat([
      { id: "999", name: "Test Policy", cost_type: "CALCULATED",
        service_code: "USPSPriority", carrier: "USPS" }]);
    const p = c.sandbox.confirmCreatePolicy();
    // Server answers with the new policy AND the fresh list incl. it.
    c.resolveFetch("fulfillment-policies/create",
      { policy_id: "999", name: "Test Policy", policies: NEW_POLICIES });
    await c.tick(); await c.tick(); // let the chain reach saveDraft's fetch
    c.resolveFetch("/api/drafts/d1", { draft: { id: "d1" } });
    await p; await c.tick(); await c.tick();
    check("the create POST DID reach the server (policy exists on eBay now)",
      c.fetchCalls.some((f) => String(f.url).includes("fulfillment-policies/create") &&
        String(f.opts.method) === "POST"));
    check("no error box", c.hidden("policy-create-error"));
    check("success note shows 'Policy created and selected.'",
      !c.hidden("policy-create-note") &&
      c.$("policy-create-note").textContent === "Policy created and selected.");
    check("form stays hidden", c.hidden("policy-create-form"));
    check("new policy IS selected in the dropdown",
      c.$("shipping-select").value === "999");
    check("pickers synced to the new policy",
      c.$("ship-service").value === "USPSPriority");
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("\n== E2: post-create select failure never reports the create as failed ==");
  {
    const c = newContext(SRC);
    c.setCtx(`shippingPolicies = ${JSON.stringify(POLICIES)}; carrierServicesFetched = true; carrierServices = ${JSON.stringify(CARRIERS)}; draft = {id: 'd1'}; pendingCreateForm = {name: 'Test Policy'}; createRetry = false;`);
    c.$("pc-name").value = "Test Policy";
    c.$("pc-dom-cost-type").value = "CALCULATED";
    c.sandbox.populateServiceSelect("pc-dom-service-1", CARRIERS.USPS);
    c.$("pc-dom-service-1").value = "USPSPriority";
    c.$("pc-dom-handling").value = "2";
    const NEW_POLICIES = POLICIES.concat([
      { id: "999", name: "Test Policy", cost_type: "CALCULATED",
        service_code: "USPSPriority", carrier: "USPS" }]);
    // Force the post-create select to fail (any throw in the select
    // path must never report the create itself as failed). The draft
    // save is best-effort by design and swallows its own errors, so
    // a failed save cannot be the trigger — stub the chooser refresh
    // instead, which is the step that threw in the v42 bug.
    c.sandbox.refreshShippingChooser = () => { throw new Error("select boom"); };
    const p = c.sandbox.confirmCreatePolicy();
    c.resolveFetch("fulfillment-policies/create",
      { policy_id: "999", name: "Test Policy", policies: NEW_POLICIES });
    await c.tick(); await c.tick();
    c.resolveFetch("/api/drafts/d1", { draft: { id: "d1" } });
    await p; await c.tick(); await c.tick();
    check("the create POST DID reach the server",
      c.fetchCalls.some((f) => String(f.url).includes("fulfillment-policies/create") &&
        String(f.opts.method) === "POST"));
    check("no error box (the create did not fail)",
      c.hidden("policy-create-error"));
    check("note says the policy was created and where to pick it",
      !c.hidden("policy-create-note") &&
      c.$("policy-create-note").textContent ===
        "Policy created. Pick it from your usual policies below.");
    check("form stays hidden", c.hidden("policy-create-form"));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("\n== G: List succeeds and the 'It is live!' note STAYS visible ==");
  {
    const c = newContext(SRC);
    c.setCtx(`draft = {id: 'd1'}; shippingPolicies = ${JSON.stringify(POLICIES)}; carrierServicesFetched = true; carrierServices = ${JSON.stringify(CARRIERS)};`);
    c.$("p-fulfillment").value = "111";
    c.sandbox.fetchShippingPolicies();
    c.resolveFetch("fulfillment-policies", { policies: POLICIES });
    await c.tick(); await c.tick();
    check("precondition: no pink error", c.hidden("shipping-error"));
    const form = c.sandbox.collectForm();
    check("collectForm still carries the saved policy number",
      form.fulfillment_policy_id === "111");
    check("List button was never disabled by the error",
      c.$("btn-publish").disabled === false);
    const outcome = c.sandbox.postPublish({
      draft_id: "d1", title: "T", price: "10.00", fulfillment_policy_id: "111",
    });
    c.resolveFetch("/api/ebay/publish", { listing_id: "999999", listing_url: "https://www.ebay.com/itm/999999" });
    const res = await outcome;
    check("postPublish with the saved policy succeeds (server-side gate is what matters)",
      res === "listed");
    check("success note IS visible right after the publish",
      !c.hidden("publish-ok") &&
      c.$("publish-ok").textContent.includes("It is live!"));
    // Let the post-publish prefillListing() run to completion; the
    // note must survive the refresh, not just the same tick.
    c.resolveFetch("/api/ebay/status", {});
    await c.tick(); await c.tick(); await c.tick();
    check("success note STAYS visible after the page refresh",
      !c.hidden("publish-ok") &&
      c.$("publish-ok").textContent.includes("It is live!"));
    // A plain draft open still clears a stale note (the hide itself
    // is correct there — only the fresh-publish path keeps it).
    c.sandbox.prefillListing();
    await c.tick(); await c.tick();
    check("a plain prefillListing() still hides a stale note",
      c.hidden("publish-ok"));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("\n== H: batch pick list shows NO policy IDs ==");
  {
    const c = newContext(SRC);
    c.sandbox.renderBatchPolicyPick(POLICIES, "Pick one below.");
    const texts = c.$("batch-policy-pick-list")._kids.map((b) => b.textContent);
    check("batch buttons carry no raw policy IDs",
      texts.every((t) => !/\(\d+\)/.test(t)), JSON.stringify(texts));
    check("batch buttons still carry name + type",
      texts.every((t, i) => t.startsWith(POLICIES[i].name + " — ")),
      JSON.stringify(texts));
    c.dispose();
  }

  console.log("\n== I: shipping Try again succeeds after a transient failure ==");
  {
    const c = newContext(SRC);
    c.$("p-fulfillment").value = "111";
    c.setCtx(`shippingPolicies = []; carrierServicesFetched = true; carrierServices = ${JSON.stringify(CARRIERS)};`);
    c.sandbox.populateShipService();
    c.sandbox.restoreShipService("USPSGroundAdvantage"); // v49: old saved code
    c.sandbox.fetchShippingPolicies();
    c.resolveFetch("fulfillment-policies", { error: "boom" }, false);
    await c.tick(); await c.tick();
    check("transient failure shows the error", !c.hidden("shipping-error"));
    await c.fireClick("btn-shipping-retry");
    c.resolveFetch("fulfillment-policies", { policies: POLICIES });
    await c.tick(); await c.tick();
    check("retry succeeds: error gone, dropdown populated",
      c.hidden("shipping-error") &&
      c.$("shipping-select").children.length === 4);
    check("retry succeeds: service restored",
      c.$("ship-service").value === "USPSParcel");
    check("retry fired a second policy fetch",
      c.fetchCalls.filter((f) => String(f.url).includes("fulfillment-policies")).length === 2);
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("\n== J: create-form Try again succeeds after a transient failure ==");
  {
    const c = newContext(SRC);
    c.setCtx(`createServicesFetched = false; draft = {id: 'd1'};`);
    const p = c.sandbox.openCreateForm();
    c.resolveFetch("shipping-services", { error: "boom" }, false);
    await p; await c.tick();
    check("transient failure shows the error with a Try again button",
      !c.hidden("policy-create-error") &&
      c.$("policy-create-error").textContent.includes("Try again"));
    check("createServicesFetched still false after failure",
      c.setCtx("createServicesFetched") === false);
    await c.fireClick("btn-pc-services-retry");
    await c.tick();
    check("retry re-fires the services fetch",
      c.fetchCalls.filter((f) => String(f.url).includes("shipping-services")).length === 2);
    c.resolveFetch("shipping-services",
      { domestic: CARRIERS.USPS.concat(CARRIERS.UPS, CARRIERS.FedEx), international: [] });
    await c.tick(); await c.tick();
    check("retry succeeds: dom service select populated",
      c.$("pc-dom-service-1").children.length > 1);
    check("createServicesFetched now true",
      c.setCtx("createServicesFetched") === true);
    check("error hidden after the retry succeeds",
      c.hidden("policy-create-error"));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("\n== K: flat policy carrying the service is offered honestly ==");
  {
    const c = newContext(SRC);
    c.setCtx(`shippingPolicies = ${JSON.stringify(POLICIES)}; carrierServicesFetched = true; carrierServices = ${JSON.stringify(CARRIERS)}; draft = {id: 'd1'};`);
    c.sandbox.renderShippingDropdown(); // real page: policies fetched, dropdown rendered
    c.$("ship-carrier").value = "USPS";
    c.sandbox.populateShipService();
    c.$("ship-service").value = "USPSParcel";
    c.sandbox.onShipServicePicked();
    await c.tick();
    c.resolveFetch("/api/drafts/d1", { draft: { id: "d1" } }); // onShipServicePicked saves first
    await c.tick();
    c.resolveFetch("policy-for-service",
      { policy_id: null, flat_policy_id: "222",
        flat_name: "Free Shipping - Unframed Prints" });
    await c.tick(); await c.tick();
    check("flat offer names the flat policy honestly",
      !c.hidden("ship-service-note") &&
      c.$("ship-service-note").textContent.includes(
        "Your flat policy Free Shipping - Unframed Prints already offers USPS Ground Advantage."),
      "got: " + JSON.stringify(c.$("ship-service-note").textContent));
    check("create form did NOT open", c.hidden("policy-create-form"));
    const btn = c.$("ship-service-note")._kids[0];
    check("offer button exists ('Use that policy')",
      !!btn && btn.textContent === "Use that policy");
    btn.click();
    await c.tick();
    c.resolveFetch("/api/drafts/d1", { draft: { id: "d1" } });
    await c.tick(); await c.tick();
    check("using the flat policy selects it",
      c.$("p-fulfillment").value === "222" &&
      c.$("shipping-select").value === "222");
    check("note hides after picking", c.hidden("ship-service-note"));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("\n" + (failures ? failures + " CHECK(S) FAILED" : "ALL CHECKS PASSED"));
  process.exit(failures ? 1 : 0);
}

main().catch((e) => { console.error("HARNESS ERROR:", e); process.exit(2); });
