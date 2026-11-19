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

/* v56: full-editor pure helpers. */
const { defaultAdjustments, adjustmentFilter, autoEnhanceValues,
        autoEnhanceStatus,
        aspectCropRect, rotatedFrameSize, straightenFrame,
        clampCropToFrame, remapCropToFrame, sharpenImageData } = sandbox;
function ctxExpr(expr) { return vm.runInContext(expr, sandbox); }

check("defaultAdjustments are all zero",
  eq(defaultAdjustments(), { brightness: 0, contrast: 0, saturation: 0, warmth: 0, sharpness: 0 }));
check("filter preset vivid lifts saturation and contrast",
  eq(ctxExpr("FILTER_PRESETS.vivid"), { brightness: 0, contrast: 10, saturation: 25, warmth: 0, sharpness: 0 }));
check("filter preset warm warms, cool cools, mono kills saturation",
  ctxExpr("FILTER_PRESETS.warm").warmth === 25 &&
  ctxExpr("FILTER_PRESETS.cool").warmth === -25 &&
  ctxExpr("FILTER_PRESETS.mono").saturation === -100 &&
  eq(ctxExpr("FILTER_PRESETS.original"), { brightness: 0, contrast: 0, saturation: 0, warmth: 0, sharpness: 0 }));

check("adjustmentFilter is neutral at zero",
  adjustmentFilter(defaultAdjustments()) === "brightness(1.000) contrast(1.000) saturate(1.000)");
check("adjustmentFilter scales brightness and contrast, clamps saturate at 0",
  adjustmentFilter({ brightness: 50, contrast: -20, saturation: -100, warmth: 0, sharpness: 0 }) ===
    "brightness(1.500) contrast(0.800) saturate(0.000)");

check("auto enhance brightens a dark photo and adds contrast to a flat one",
  eq(autoEnhanceValues(60, 20), { brightness: 29, contrast: 15, saturation: 12 }));
check("auto enhance darkens a bright photo and calms a contrasty one",
  eq(autoEnhanceValues(180, 90), { brightness: -30, contrast: -9, saturation: 12 }));
check("auto enhance leaves a balanced photo alone except saturation",
  eq(autoEnhanceValues(118, 64), { brightness: 0, contrast: 0, saturation: 12 }));
/* v58: Auto enhance never brightens a photo that is already bright —
   lifting the whites on white packaging erases the faint print. */
check("auto enhance never gives a positive brightness at or above the target mean",
  autoEnhanceValues(118, 64).brightness === 0 &&
  autoEnhanceValues(189, 71).brightness === -30 &&
  autoEnhanceValues(255, 0).brightness === -30);
check("auto enhance still brightens a genuinely dark photo",
  autoEnhanceValues(60, 20).brightness === 29);
/* v59: the status line says the exact values Auto enhance set, or
   that the photo was already bright enough to need no lift. */
check("auto enhance status names the exact values it set",
  autoEnhanceStatus({ brightness: 29, contrast: 22, saturation: 12 }) ===
    "Auto enhance set Brightness +29, Contrast +22, Saturation +12." &&
  autoEnhanceStatus({ brightness: -30, contrast: -9, saturation: 12 }) ===
    "Auto enhance set Brightness -30, Contrast -9, Saturation +12.");
check("auto enhance status says the photo needed no lift when it did not",
  autoEnhanceStatus({ brightness: 0, contrast: 0, saturation: 12 }) ===
    "Auto enhance: the photo is already bright, so no brightness or contrast change. Saturation +12.");

check("aspectCropRect null ratio is the full frame",
  eq(aspectCropRect(1000, 800, null), { x: 0, y: 0, w: 1000, h: 800 }));
check("aspectCropRect square centers in a wide frame",
  eq(aspectCropRect(1000, 800, 1), { x: 100, y: 0, w: 800, h: 800 }));
check("aspectCropRect 4:3 fits a 1000x800 frame",
  eq(aspectCropRect(1000, 800, 4 / 3), { x: 0, y: 25, w: 1000, h: 750 }));
check("aspectCropRect 16:9 fills a 1600x900 frame",
  eq(aspectCropRect(1600, 900, 16 / 9), { x: 0, y: 0, w: 1600, h: 900 }));

check("rotatedFrameSize 0/90 without straighten matches rotatedDims",
  eq(rotatedFrameSize(100, 200, 0, 0), { w: 100, h: 200 }) &&
  eq(rotatedFrameSize(100, 200, 90, 0), { w: 200, h: 100 }));
check("rotatedFrameSize square at 45 degrees grows to the diagonal box",
  eq(rotatedFrameSize(100, 100, 0, 45), { w: 141, h: 141 }));
check("rotatedFrameSize straighten 15 widens a 200x100 frame",
  eq(rotatedFrameSize(200, 100, 0, 15), { w: 219, h: 148 }));

check("straightenFrame at 0 degrees is the full frame",
  eq(straightenFrame(200, 100, 0, 0), { x: 0, y: 0, w: 200, h: 100 }) &&
  eq(straightenFrame(100, 200, 90, 0), { x: 0, y: 0, w: 200, h: 100 }));
/* The straightened frame must sit inside the rotated image: its four
   corners must clear the rotated edges. For rot-frame dims w x h and
   angle t, the centered half-extents (x0, y0) must satisfy
   y0 <= (h cos t - w sin t)/2 + x0 tan t and
   x0 <= (w cos t - h sin t)/2 + y0 tan t. */
function straightenInside(w, h, rot, deg) {
  const f = straightenFrame(w, h, rot, deg);
  const box = rotatedFrameSize(w, h, rot, deg);
  const x0 = f.w / 2, y0 = f.h / 2;
  const dims = ctxExpr("rotatedDims(" + w + "," + h + "," + rot + ")");
  const t = Math.abs(deg) * Math.PI / 180;
  const c = Math.cos(t), s = Math.sin(t);
  const okA = y0 <= (dims.h * c - dims.w * s) / 2 + x0 * Math.tan(t) + 1;
  const okB = x0 <= (dims.w * c - dims.h * s) / 2 + y0 * Math.tan(t) + 1;
  return f.x >= 0 && f.y >= 0 && f.x + f.w <= box.w + 1 && f.y + f.h <= box.h + 1 &&
    f.w >= 1 && f.h >= 1 && okA && okB;
}
check("straightenFrame stays inside the rotated image at 5/10/15 degrees",
  straightenInside(2048, 1536, 0, 5) && straightenInside(2048, 1536, 0, 10) &&
  straightenInside(2048, 1536, 0, 15) && straightenInside(1536, 2048, 90, 15) &&
  straightenInside(2048, 512, 0, 15) && straightenInside(800, 800, 0, 15));

check("clampCropToFrame leaves an inside rect alone",
  eq(clampCropToFrame({ x: 60, y: 60, w: 100, h: 80 }, { x: 52, y: 52, w: 400, h: 300 }, 50),
     { x: 60, y: 60, w: 100, h: 80 }));
check("clampCropToFrame pulls an overhanging rect back into the frame",
  eq(clampCropToFrame({ x: 10, y: 10, w: 100, h: 80 }, { x: 52, y: 52, w: 400, h: 300 }, 50),
     { x: 52, y: 52, w: 100, h: 80 }));
/* v58: the crop keeps its relative place when the visible frame
   changes size, so undoing straighten grows it back. */
check("remapCropToFrame keeps a full-frame crop full-frame when the frame grows",
  eq(remapCropToFrame({ x: 139, y: 139, w: 614, h: 452 },
                       { x: 139, y: 139, w: 614, h: 452 },
                       { x: 0, y: 0, w: 800, h: 600 }, 50),
     { x: 0, y: 0, w: 800, h: 600 }));
check("remapCropToFrame keeps a full-frame crop full-frame when the frame shrinks",
  eq(remapCropToFrame({ x: 0, y: 0, w: 800, h: 600 },
                       { x: 0, y: 0, w: 800, h: 600 },
                       { x: 139, y: 139, w: 614, h: 452 }, 50),
     { x: 139, y: 139, w: 614, h: 452 }));
check("remapCropToFrame keeps a small centered crop centered and relative",
  eq(remapCropToFrame({ x: 200, y: 150, w: 400, h: 300 },
                       { x: 0, y: 0, w: 800, h: 600 },
                       { x: 139, y: 139, w: 614, h: 452 }, 50),
     { x: 292.5, y: 252, w: 307, h: 226 }));

function fakeImageData(w, h, fill) {
  const data = new Uint8ClampedArray(w * h * 4);
  for (let i = 0; i < w * h; i++) {
    const v = typeof fill === "function" ? fill(i) : fill;
    data[i * 4] = v; data[i * 4 + 1] = v; data[i * 4 + 2] = v; data[i * 4 + 3] = 255;
  }
  return { width: w, height: h, data: data };
}
check("sharpen leaves a flat image untouched",
  (() => {
    const img = fakeImageData(4, 4, 128);
    sharpenImageData(img, 100);
    return Array.from(img.data).every((v, i) => v === (i % 4 === 3 ? 255 : 128));
  })());
check("sharpen lifts a bright spot and darkens the pixels beside it",
  (() => {
    const img = fakeImageData(3, 3, (i) => (i === 4 ? 200 : 100));
    sharpenImageData(img, 100);
    return img.data[4 * 4] === 255 && img.data[1 * 4] === 50 && img.data[7 * 4] === 50;
  })());
check("sharpen amount 0 is a no-op and alpha is never touched",
  (() => {
    const img = fakeImageData(3, 3, (i) => (i === 4 ? 200 : 100));
    img.data[3] = 77;
    sharpenImageData(img, 0);
    return img.data[4 * 4] === 200 && img.data[3] === 77;
  })());

if (failures > 0) { console.log(failures + " FAILURES"); process.exit(1); }
console.log("all editor-math tests pass");
