/* v40: node tests for draft photo Delete + Move left/right in
   static/app.js. Same vm-harness pattern as multi-photo.test.js, but
   elements record their listeners so clicks can be fired: Delete calls
   the DELETE endpoint and shows "Photo removed." with an updated count;
   Move calls the move endpoint and re-renders in the new order; edge
   arrows are hidden (first photo has no left, last has no right);
   step-1 arrows reorder selectedFiles. Run: node tests/photo-arrange.test.js
   (exit 0 = pass). */
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
    _handlers: {},
    classList: makeClassList(),
    addEventListener(type, fn) {
      (el._handlers[type] = el._handlers[type] || []).push(fn);
    },
    removeEventListener() {},
    setAttribute(k, v) { el.dataset[k] = v; },
    getAttribute(k) { return el.dataset[k]; },
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

function noopCtx() {
  return new Proxy({}, {
    get(t, p) {
      if (typeof p === "string") return () => {};
      return undefined;
    },
    set(t, p, v) { t[p] = v; return true; },
  });
}

function fakeCanvas() {
  return {
    width: 0,
    height: 0,
    getContext() { return noopCtx(); },
    toBlob(cb) { cb(new Blob(["fake-jpeg"], { type: "image/jpeg" })); },
  };
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
    createElement(tag) {
      if (tag === "canvas") return fakeCanvas();
      return makeEl();
    },
    querySelectorAll() { return []; },
    addEventListener() {},
    body: makeEl(),
  },
  window: {
    addEventListener() {},
    location: { search: "" },
    history: {},
    innerWidth: 400,
    createImageBitmap: async () => ({ width: 120, height: 90, close() {} }),
  },
  createImageBitmap: async () => ({ width: 120, height: 90, close() {} }),
  fetch: async (url, opts = {}) => {
    fetchCalls.push({ url, opts });
    const next = fetchQueue.length ? fetchQueue.shift() : {};
    return {
      ok: true, status: 200,
      text: async () => JSON.stringify(next),
      blob: async () => new Blob(["fake-jpeg"], { type: "image/jpeg" }),
    };
  },
  URL: {
    createObjectURL: () => "blob:fake-" + Math.random(),
    revokeObjectURL() {},
  },
  URLSearchParams,
  File,
  Blob,
  FormData,
  console,
  setTimeout,
  clearTimeout,
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
const $ = (id) => sandbox.document.getElementById(id);
const hidden = (id) => $(id).classList.contains("hidden");
const ctx = (code) => vm.runInContext(code, sandbox);
const buttons = (el) => el.children.filter((c) => c.textContent !== "");
async function fire(el, type) {
  for (const h of (el._handlers[type] || [])) await h();
}
function findButton(wrap, label) {
  const all = [];
  (function walk(el) {
    el.children.forEach((c) => { all.push(c); walk(c); });
  })(wrap);
  return all.find((c) => c.textContent === label);
}

(async () => {
  ctx("maxPhotos = 24;");
  // Listing-page render: Delete + edge-hidden arrows.
  sandbox.renderDraftPhotos({ photos: ["d1/1.jpg", "d1/2.jpg", "d1/3.jpg"] });
  const wraps = $("draft-photos").children;
  check("draft render shows all three photos", wraps.length === 3);
  const rowTexts = (w) => {
    const row = w.children.find((c) => c.className === "thumb-move-row");
    return row ? row.children.map((c) => c.textContent) : [];
  };
  check("first photo has no left arrow, only right",
    JSON.stringify(rowTexts(wraps[0])) === JSON.stringify(["▶"]));
  check("middle photo has both arrows",
    JSON.stringify(rowTexts(wraps[1])) === JSON.stringify(["◀", "▶"]));
  check("last photo has no right arrow, only left",
    JSON.stringify(rowTexts(wraps[2])) === JSON.stringify(["◀"]));
  check("every draft photo gets a Delete button",
    wraps.every((w) => !!findButton(w, "×")));
  check("single photo gets no move row at all",
    (() => {
      sandbox.renderDraftPhotos({ photos: ["d1/1.jpg"] });
      return $("draft-photos").children[0].children.every(
        (c) => c.className !== "thumb-move-row");
    })());

  // Delete: calls DELETE, re-renders, shows note + updated count.
  ctx("draft = { id: 'd9', photos: ['d9/1.jpg', 'd9/2.jpg'] };");
  sandbox.renderDraftPhotos(ctx("draft"));
  fetchCalls.length = 0;
  fetchQueue = [{ draft: { id: "d9", photos: ["d9/1.jpg"] } }];
  await fire(findButton($("draft-photos").children[1], "×"), "click");
  check("delete calls the DELETE endpoint",
    fetchCalls.length === 1
    && fetchCalls[0].url === "/api/drafts/d9/photos/1"
    && fetchCalls[0].opts.method === "DELETE");
  check("delete re-renders with one photo left",
    $("draft-photos").children.length === 1);
  check("delete shows a Photo removed note",
    !hidden("draft-photo-note")
    && $("draft-photo-note").textContent === "Photo removed.");
  check("count updates after delete",
    $("draft-photo-count").textContent === "1 of 24 photos");

  // Move: calls the move endpoint and re-renders in the new order.
  ctx("draft = { id: 'd9', photos: ['d9/1.jpg', 'd9/2.jpg', 'd9/3.jpg'] };");
  sandbox.renderDraftPhotos(ctx("draft"));
  fetchCalls.length = 0;
  fetchQueue = [{ draft: { id: "d9", photos: ["d9/2.jpg", "d9/1.jpg", "d9/3.jpg"] } }];
  await fire(findButton($("draft-photos").children[0], "▶"), "click");
  check("move calls the move endpoint with direction",
    fetchCalls.length === 1
    && fetchCalls[0].url === "/api/drafts/d9/photos/0/move"
    && fetchCalls[0].opts.method === "POST"
    && JSON.parse(fetchCalls[0].opts.body).direction === "right");
  check("move re-renders in the new order",
    $("draft-photos").children.length === 3
    && $("draft-photos").children[0].children[0].children[0].src === "/photos/d9/2.jpg");

  // Step-1 arrows reorder selectedFiles (client-side only).
  ctx("selectedFiles = [{ url: 'blob:a' }, { url: 'blob:b' }, { url: 'blob:c' }];");
  sandbox.renderThumbs();
  const thumbs = $("thumbs").children;
  check("step-1 first thumb has only a right arrow",
    JSON.stringify(thumbs[0].children[1].children.map((c) => c.textContent))
    === JSON.stringify(["▶"]));
  fire(findButton(thumbs[0], "▶"), "click");
  check("step-1 move right swaps the first two picks",
    ctx("selectedFiles").map((e) => e.url).join(",") === "blob:b,blob:a,blob:c");
  fire(findButton($("thumbs").children[2], "◀"), "click");
  check("step-1 move left swaps the last two picks",
    ctx("selectedFiles").map((e) => e.url).join(",") === "blob:b,blob:c,blob:a");

  if (failures) { console.log(failures + " FAILURES"); process.exit(1); }
  console.log("all photo-arrange checks pass");
})();
