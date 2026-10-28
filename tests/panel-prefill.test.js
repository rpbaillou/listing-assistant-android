/* v27: node tests for the publish-panel prefill precedence in static/app.js.
   app.js is a browser script, so this stubs the DOM globals it touches at
   load time, evaluates it in a vm sandbox, then asserts on panelPrefill.
   Run: node tests/panel-prefill.test.js (exit 0 = pass). */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

function makeEl() {
  return new Proxy({}, {
    get(_t, p) {
      if (p === "classList") return { add() {}, remove() {}, toggle() {}, contains() { return false; } };
      if (p === "style" || p === "dataset" || p === "options") return p === "options" ? [] : {};
      if (p === "addEventListener" || p === "removeEventListener" ||
          p === "setPointerCapture" || p === "releasePointerCapture") return () => {};
      if (p === "querySelector" || p === "querySelectorAll") return () => [];
      if (p === "getContext") return () => null;
      if (p === "getBoundingClientRect") return () => ({ left: 0, top: 0, width: 100, height: 100 });
      return undefined;
    },
    set() { return true; },
  });
}

const sandbox = {
  document: {
    getElementById: () => makeEl(),
    createElement: () => makeEl(),
    querySelectorAll: () => [],
    addEventListener() {},
    body: makeEl(),
  },
  window: {
    addEventListener() {},
    location: { search: "" },
    history: {},
    innerWidth: 400,
  },
  fetch: async () => ({ ok: true, status: 200, text: async () => "{}" }),
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
  if (cond) { console.log("ok - " + name); }
  else { failures++; console.log("FAIL - " + name); }
}

const { panelPrefill } = sandbox;
const defs = { fulfillment: "F0", payment: "P0", return: "R0" };

// Draft beats preset beats .env on every field.
check("draft category wins over preset",
  panelPrefill({ category_id: "111", category_name: "Saved Cat" },
    { category_id: "222" }, defs, {}).category_id === "111");
check("draft category name restored with its number",
  panelPrefill({ category_id: "111", category_name: "Saved Cat" },
    { category_id: "222" }, defs, {}).category_name === "Saved Cat");
check("preset category fills a draft without one",
  (() => { const r = panelPrefill({}, { category_id: "222" }, defs, {});
    return r.category_id === "222" && r.applied.includes("category"); })());
check("preset category does not count as applied when draft has one",
  !panelPrefill({ category_id: "111" }, { category_id: "222" }, defs, {})
    .applied.includes("category"));
check("draft policies win over preset and .env",
  (() => { const r = panelPrefill({ fulfillment_policy_id: "F9" },
      { fulfillment_policy_id: "F8" }, defs, {});
    return r.fulfillment === "F9" && !r.applied.includes("policies"); })());
check("preset policies fill drafts without them",
  (() => { const r = panelPrefill({}, { fulfillment_policy_id: "F8" }, defs, {});
    return r.fulfillment === "F8" && r.applied.includes("policies"); })());
check(".env defaults fill when neither draft nor preset has policies",
  (() => { const r = panelPrefill({}, {}, defs, {});
    return r.fulfillment === "F0" && r.payment === "P0" && r.return === "R0" &&
      r.applied.length === 0; })());
check("draft condition wins over preset",
  panelPrefill({ condition: "NEW" }, { condition: "USED_GOOD" }, defs, {})
    .condition === "NEW");
check("preset condition fills when draft and form lack one",
  (() => { const r = panelPrefill({}, { condition: "USED_GOOD" }, defs, {});
    return r.condition === "USED_GOOD" && r.applied.includes("condition"); })());
check("freshly typed form condition wins over draft",
  panelPrefill({ condition: "NEW" }, {}, defs, { condition: "USED_GOOD" })
    .condition === "USED_GOOD");
check("freshly typed form price wins over draft price",
  panelPrefill({ price: "10.00" }, {}, defs, { price: "12.50" }).price === "12.50");
check("draft price fills when form price is empty",
  panelPrefill({ price: "10.00" }, {}, defs, { price: "" }).price === "10.00");
check("draft Best Offer wins over preset",
  (() => { const r = panelPrefill(
      { best_offer: true, best_offer_auto_decline: "8.00" },
      { best_offer: false }, defs, {});
    return r.best_offer === true && r.best_offer_auto_decline === "8.00" &&
      !r.applied.includes("Best Offer"); })());
check("preset Best Offer fills a draft without one",
  (() => { const r = panelPrefill({}, { best_offer: true,
      best_offer_auto_decline: "8.00", best_offer_auto_accept: "11.00" }, defs, {});
    return r.best_offer === true && r.best_offer_auto_accept === "11.00" &&
      r.applied.includes("Best Offer"); })());
check("genuinely empty category stays empty",
  panelPrefill({}, {}, defs, {}).category_id === "");
check("category name is empty without a draft category number",
  panelPrefill({ category_name: "Orphan" }, {}, defs, {}).category_name === "");

if (failures > 0) { console.log(failures + " FAILURES"); process.exit(1); }
console.log("all panel-prefill tests pass");
