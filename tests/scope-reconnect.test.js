/* v33: node tests for the frontend scope-reconnect defense in static/app.js.
   app.js is a browser script, so this stubs the DOM globals it touches at
   load time, evaluates it in a vm sandbox, then asserts on
   isScopeReconnectText. The live v1.31 gap: the server answered the scope
   message without the flag, so the message showed but the Connect button
   stayed hidden. The text check must catch the message even when wrapped.
   Run: node tests/scope-reconnect.test.js (exit 0 = pass). */
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
  if (!cond) { failures++; console.log("FAIL: " + name); }
  else { console.log("ok: " + name); }
}

const SCOPE_MSG = "The app needs one new permission to read your shipping policies. Tap Connect eBay and approve it.";
check("exact scope message matches", sandbox.isScopeReconnectText(SCOPE_MSG) === true);
check("wrapped scope message matches", sandbox.isScopeReconnectText("Error: " + SCOPE_MSG) === true);
check("other eBay error does not match",
  sandbox.isScopeReconnectText("eBay said (error 25007): invalid shipping policy.") === false);
check("empty string does not match", sandbox.isScopeReconnectText("") === false);
check("null does not match", sandbox.isScopeReconnectText(null) === false);
check("undefined does not match", sandbox.isScopeReconnectText(undefined) === false);
check("partial permission phrase without the shipping-policies clause does not match",
  sandbox.isScopeReconnectText("The app needs one new permission.") === false);

if (failures) { console.log(failures + " failure(s)"); process.exit(1); }
console.log("all scope-reconnect text checks pass");
