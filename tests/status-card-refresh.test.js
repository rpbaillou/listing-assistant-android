/* v53: node tests for the status card telling the truth after the
   sign-in dies (static/app.js).

   Live v1.51 bug (Nov 9 02:08 CST): the listing page said BOTH
   "eBay connected. Ready to list." (status card, rendered from
   GET /api/ebay/status at page load) AND "eBay is not connected."
   (publish error, after a failed renewal / 401 wiped the tokens
   mid-page). The fix: any token-death message re-runs the status
   check (refreshStatusIfTokenDied), so the card flips to
   not-connected with the Connect button right there and the List
   button hides until he reconnects.

   app.js is a browser script, so this stubs the DOM globals it
   touches at load time, evaluates it in a vm sandbox, then drives
   refreshEbayStatus / refreshStatusIfTokenDied against a scripted
   /api/ebay/status. The ebay-status box and the List button are
   stateful stubs (innerHTML + classList with real state); every
   other element is the generic no-op proxy from
   tests/scope-reconnect.test.js.
   Run: node tests/status-card-refresh.test.js (exit 0 = pass). */
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

function makeStatefulEl() {
  const classes = new Set();
  const el = {
    _html: "",
    classList: {
      add(c) { classes.add(c); },
      remove(c) { classes.delete(c); },
      toggle(c, f) {
        if (f === undefined) { if (classes.has(c)) classes.delete(c); else classes.add(c); }
        else if (f) classes.add(c); else classes.delete(c);
      },
      contains(c) { return classes.has(c); },
    },
    addEventListener() {},
    set innerHTML(v) { el._html = String(v); },
    get innerHTML() { return el._html; },
  };
  return el;
}

const statusBox = makeStatefulEl();
const publishBtn = makeStatefulEl();

const CONNECTED = {
  ebay_configured: true, connected: true,
  runame: "Reynard_Baillou-ReynardB-Mr-PRD-eayxussz",
  policies_configured: true, policy_defaults: {},
};
const NOT_CONNECTED = {
  ebay_configured: true, connected: false,
  runame: "Reynard_Baillou-ReynardB-Mr-PRD-eayxussz",
  policies_configured: true, policy_defaults: {},
};

let statusResponse = CONNECTED;
let statusFetches = 0;
let failStatus = false;

const sandbox = {
  document: {
    getElementById: (id) => {
      if (id === "ebay-status") return statusBox;
      if (id === "btn-publish") return publishBtn;
      return makeEl();
    },
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
  fetch: async (path) => {
    if (String(path).indexOf("/api/ebay/status") !== -1) {
      statusFetches++;
      if (failStatus) throw new Error("network down");
      return { ok: true, status: 200, text: async () => JSON.stringify(statusResponse) };
    }
    return { ok: true, status: 200, text: async () => "{}" };
  },
  setTimeout, clearTimeout,
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

(async () => {
  // 1. The card renders connected from a connected status, List visible.
  statusResponse = CONNECTED;
  await sandbox.refreshEbayStatus();
  check("card says connected on a connected status",
    statusBox.innerHTML.indexOf("eBay connected.") !== -1);
  check("List button visible when connected",
    !publishBtn.classList.contains("hidden"));

  // 2. The live v1.51 case: status said connected at load, then the
  // sign-in died. A token-death message re-fetches status and the
  // card flips to not-connected with the Connect button right there.
  statusResponse = NOT_CONNECTED;
  const before = statusFetches;
  await sandbox.refreshStatusIfTokenDied(
    "eBay is not connected. Tap Connect eBay to sign in again.");
  check("status re-fetched after a token-death message",
    statusFetches === before + 1);
  check("card flips to not-connected after the sign-in dies",
    statusBox.innerHTML.indexOf("eBay is not connected.") !== -1);
  check("flipped card shows a Connect eBay button",
    statusBox.innerHTML.indexOf("btn-ebay-connect") !== -1);
  check("flipped card hint has no 'in this browser'",
    statusBox.innerHTML.indexOf("in this browser") === -1);
  check("List button hides until he reconnects",
    publishBtn.classList.contains("hidden"));

  // 3. A non-death message must not re-fetch or touch the card.
  statusResponse = CONNECTED;
  await sandbox.refreshEbayStatus();
  const before2 = statusFetches;
  await sandbox.refreshStatusIfTokenDied("Could not reach eBay: boom");
  check("non-death message does not re-fetch status",
    statusFetches === before2);
  check("card stays connected after a non-death message",
    statusBox.innerHTML.indexOf("eBay connected.") !== -1);

  // 4. A failed status check never throws: plain hint, List visible.
  failStatus = true;
  await sandbox.refreshEbayStatus();
  check("failed status check shows a plain hint",
    statusBox.innerHTML.indexOf("Could not check eBay status.") !== -1);
  check("List button visible when status is unknown",
    !publishBtn.classList.contains("hidden"));
  failStatus = false;

  // 5. isTokenDeathText: every token-death message matches, nothing else does.
  const SCOPE_MSG = "The app needs one new permission to read your shipping policies. Tap Connect eBay and approve it.";
  check("not-connected message matches",
    sandbox.isTokenDeathText("eBay is not connected. Tap Connect eBay to sign in again.") === true);
  check("wrapped not-connected message matches",
    sandbox.isTokenDeathText("Error: eBay is not connected.") === true);
  check("stopped-working message matches",
    sandbox.isTokenDeathText("Your eBay sign-in stopped working. Tap Connect eBay to sign in again.") === true);
  check("expired-renewal message matches",
    sandbox.isTokenDeathText("Your eBay sign-in expired and could not be renewed. Tap Connect eBay to sign in again.") === true);
  check("photo-permission message matches",
    sandbox.isTokenDeathText("Your eBay sign-in is missing the photo permission (sell.inventory). Tap Connect eBay to grant it.") === true);
  check("scope message matches",
    sandbox.isTokenDeathText(SCOPE_MSG) === true);
  check("network failure does not match",
    sandbox.isTokenDeathText("Could not reach eBay: boom") === false);
  check("other eBay error does not match",
    sandbox.isTokenDeathText("eBay said (error 25007): invalid shipping policy.") === false);
  check("empty string does not match",
    sandbox.isTokenDeathText("") === false);
  check("null does not match",
    sandbox.isTokenDeathText(null) === false);
  check("undefined does not match",
    sandbox.isTokenDeathText(undefined) === false);

  if (failures) { console.log(failures + " failure(s)"); process.exit(1); }
  console.log("all status-card-refresh checks pass");
})();
