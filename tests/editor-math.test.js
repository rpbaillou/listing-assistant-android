/* v18: node tests for the photo editor's pure helpers in static/app.js.
   app.js is a browser script, so this stubs the DOM globals it touches at
   load time, evaluates it in a vm sandbox, then asserts on rotatedDims and
   clampCropRect. Run: node tests/editor-math.test.js (exit 0 = pass). */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

function makeEl() {
  return new Proxy({}, {
    get(_t, p) {
      if (p === "classList") return { add() {}, remove() {}, toggle() {}, contains() { return false; } };
      if (p === "style" || p === "dataset") return {};
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
function eq(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

const { rotatedDims, clampCropRect } = sandbox;
check("rotatedDims 0 keeps dims", eq(rotatedDims(100, 200, 0), { w: 100, h: 200 }));
check("rotatedDims 90 swaps", eq(rotatedDims(100, 200, 90), { w: 200, h: 100 }));
check("rotatedDims 180 keeps", eq(rotatedDims(100, 200, 180), { w: 100, h: 200 }));
check("rotatedDims 270 swaps", eq(rotatedDims(100, 200, 270), { w: 200, h: 100 }));
check("rotatedDims -90 normalizes to 270", eq(rotatedDims(100, 200, -90), { w: 200, h: 100 }));
check("rotatedDims 450 normalizes to 90", eq(rotatedDims(100, 200, 450), { w: 200, h: 100 }));
check("rotatedDims square unchanged", eq(rotatedDims(300, 300, 90), { w: 300, h: 300 }));

check("clamp inside rect untouched",
  eq(clampCropRect({ x: 10, y: 20, w: 100, h: 80 }, 1000, 800, 50), { x: 10, y: 20, w: 100, h: 80 }));
check("clamp negative origin to 0",
  eq(clampCropRect({ x: -30, y: -5, w: 100, h: 80 }, 1000, 800, 50), { x: 0, y: 0, w: 100, h: 80 }));
check("clamp right overflow shifts left",
  eq(clampCropRect({ x: 950, y: 10, w: 100, h: 80 }, 1000, 800, 50), { x: 900, y: 10, w: 100, h: 80 }));
check("clamp bottom overflow shifts up",
  eq(clampCropRect({ x: 10, y: 750, w: 100, h: 80 }, 1000, 800, 50), { x: 10, y: 720, w: 100, h: 80 }));
check("clamp oversize rect to bounds",
  eq(clampCropRect({ x: 0, y: 0, w: 2000, h: 1500 }, 1000, 800, 50), { x: 0, y: 0, w: 1000, h: 800 }));
check("clamp enforces min size",
  eq(clampCropRect({ x: 100, y: 100, w: 10, h: 5 }, 1000, 800, 50), { x: 100, y: 100, w: 50, h: 50 }));
check("clamp min size at far edge stays in bounds",
  eq(clampCropRect({ x: 990, y: 790, w: 10, h: 5 }, 1000, 800, 50), { x: 950, y: 750, w: 50, h: 50 }));

if (failures > 0) { console.log(failures + " FAILURES"); process.exit(1); }
console.log("all editor-math tests pass");
