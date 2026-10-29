/* v34: node tests for the frontend Shipping chooser in static/app.js.
   app.js is a browser script, so this stubs the DOM with STATEFUL
   elements (unlike scope-reconnect.test.js, whose proxy elements are
   no-ops): values, text, and hidden toggles must persist across $()
   calls, or the chooser cannot be observed at all. The test drives the
   real functions: openShippingPick seeds the policy list + pick mode,
   hidePublishMsg resets pick mode (browse mode), selectShippingPolicy
   picks, refreshShippingChooser follows the number box.
   Run: node tests/shipping-chooser.test.js (exit 0 = pass). */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

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

function makeEl() {
  const el = {
    value: "",
    textContent: "",
    checked: false,
    disabled: false,
    children: [],
    style: {},
    dataset: {},
    options: [],
    classList: makeClassList(),
    addEventListener() {},
    removeEventListener() {},
    appendChild(child) { el.children.push(child); return child; },
    querySelector() { return makeEl(); },
    querySelectorAll() { return []; },
    getContext() { return null; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 100 }; },
    scrollIntoView() {},
    setPointerCapture() {},
    releasePointerCapture() {},
    focus() {},
    click() {},
  };
  /* Real DOM: setting innerHTML clears the child nodes. The stub must
     model that, or a re-render appends duplicates instead of replacing. */
  let _html = "";
  Object.defineProperty(el, "innerHTML", {
    get() { return _html; },
    set(v) { _html = v; el.children = []; },
  });
  return new Proxy(el, {
    get(t, p) {
      if (p in t) return t[p];
      if (typeof p === "string") return undefined;
      return undefined;
    },
    set(t, p, v) { t[p] = v; return true; },
  });
}

const registry = {};
const fetchCalls = [];
let fetchQueue = [];
const sandbox = {
  document: {
    getElementById(id) {
      if (!registry[id]) registry[id] = makeEl();
      return registry[id];
    },
    createElement() { return makeEl(); },
    querySelectorAll() { return []; },
    addEventListener() {},
    body: makeEl(),
  },
  window: {
    addEventListener() {},
    location: { search: "" },
    history: {},
    innerWidth: 400,
  },
  fetch: async (url, opts = {}) => {
    fetchCalls.push({ url, opts });
    const next = fetchQueue.length ? fetchQueue.shift() : {};
    return { ok: true, status: 200, text: async () => JSON.stringify(next) };
  },
  URLSearchParams,
  console,
};
sandbox.window.document = sandbox.document;
vm.createContext(sandbox);
const src = fs.readFileSync(
  path.join(__dirname, "..", "app", "src", "main", "python", "static", "app.js"), "utf8");
vm.runInContext(src, sandbox);

let failures = 0;
function check(name, cond) {
  if (!cond) { failures++; console.log("FAIL: " + name); }
  else { console.log("ok: " + name); }
}
const $ = (id) => registry[id];
const hidden = (id) => $(id).classList.contains("hidden");

(async () => {
  // Plain type labels (shared with the old pick list).
  check("calculated label", sandbox.policyTypeLabel("CALCULATED") === "calculated (needs package weight)");
  check("flat label", sandbox.policyTypeLabel("FLAT_RATE") === "flat rate");
  check("pickup label", sandbox.policyTypeLabel("NOT_SPECIFIED") === "local pickup only");
  check("unknown label", sandbox.policyTypeLabel(null) === "unknown type");

  // Seed via the reactive-pick entry: list renders, pick message shows.
  const POLICIES = [
    { id: "111", name: "Dummy Calculated", cost_type: "CALCULATED" },
    { id: "222", name: "Dummy Flat", cost_type: "FLAT_RATE" },
  ];
  sandbox.openShippingPick(POLICIES, "That shipping policy number no longer works. Pick one below.");
  const list = $("shipping-list");
  check("list renders two buttons", list.children.length === 2);
  check("first button names calculated policy",
    list.children[0].textContent.includes("Dummy Calculated") &&
    list.children[0].textContent.includes("calculated (needs package weight)") &&
    list.children[0].textContent.includes("(111)"));
  check("second button names flat policy",
    list.children[1].textContent.includes("Dummy Flat") &&
    list.children[1].textContent.includes("flat rate") &&
    list.children[1].textContent.includes("(222)"));
  check("pick message shown", !hidden("shipping-pick-msg") &&
    $("shipping-pick-msg").textContent.includes("no longer works"));

  // Selected line + package visibility follow the number box.
  $("p-fulfillment").value = "111";
  sandbox.refreshShippingChooser();
  check("calculated selected line names policy + type",
    $("shipping-selected").textContent.includes("Dummy Calculated") &&
    $("shipping-selected").textContent.includes("calculated"));
  check("calculated shows package fields", !hidden("shipping-package"));
  check("selected button carries check mark",
    $("shipping-list").children[0].textContent.startsWith("✓ "));
  $("p-fulfillment").value = "222";
  sandbox.refreshShippingChooser();
  check("flat selected line names policy + type",
    $("shipping-selected").textContent.includes("Dummy Flat") &&
    $("shipping-selected").textContent.includes("flat rate"));
  check("flat hides package fields", hidden("shipping-package"));
  $("p-fulfillment").value = "999";
  sandbox.refreshShippingChooser();
  check("unknown number shows details-not-loaded + package fields",
    $("shipping-selected").textContent.includes("(details not loaded yet)") &&
    !hidden("shipping-package"));
  $("p-fulfillment").value = "";
  sandbox.refreshShippingChooser();
  check("empty number shows not-picked line",
    $("shipping-selected").textContent.includes("not picked yet"));

  // Browse mode: selecting saves the number + refreshes, never publishes.
  sandbox.hidePublishMsg();
  check("pick message hidden after fresh attempt", hidden("shipping-pick-msg"));
  fetchCalls.length = 0;
  $("p-fulfillment").value = "111";
  sandbox.refreshShippingChooser();
  await sandbox.selectShippingPolicy("222");
  check("browse select sets the number box", $("p-fulfillment").value === "222");
  check("browse select refreshes to flat + hides package",
    $("shipping-selected").textContent.includes("Dummy Flat") &&
    hidden("shipping-package"));
  check("browse select never publishes",
    !fetchCalls.some((c) => String(c.url).includes("/api/ebay/publish")));

  // Pick mode: selecting re-posts the confirmed publish with the picked
  // number + the v34 discard flag (postPublish stubbed to capture).
  const PICK_BODY = { draft_id: "d1", title: "T", price: "10.00" };
  fetchQueue = [{ needs_policy_pick: true, policies: POLICIES, error: "Pick one below." }];
  await sandbox.postPublish(PICK_BODY);
  check("pick mode entered via failed publish", !hidden("shipping-pick-msg"));
  let repostBody = null;
  sandbox.postPublish = async (body) => { repostBody = body; return "listed"; };
  await sandbox.selectShippingPolicy("111");
  check("pick re-post carries picked number",
    repostBody && repostBody.fulfillment_policy_id === "111");
  check("pick re-post carries discard flag",
    repostBody && repostBody.policy_pick === true);
  check("pick re-post keeps confirmed body",
    repostBody && repostBody.draft_id === "d1" && repostBody.title === "T");
  check("listed pick exits pick mode + hides message", hidden("shipping-pick-msg"));

  if (failures) { console.log(failures + " failure(s)"); process.exit(1); }
  console.log("all shipping-chooser checks pass");
})().catch((e) => { console.log("ERROR: " + (e && e.stack || e)); process.exit(1); });
