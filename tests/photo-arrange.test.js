/* v44: node tests for draft photo Delete + drag to reorder in
   static/app.js. Same vm-harness pattern as multi-photo.test.js, but
   elements record their listeners so clicks and pointer drags can be
   fired: Delete calls the DELETE endpoint and shows "Photo removed."
   with an updated count; a drag calls the move endpoint with {"to": n}
   and re-renders in the new order; a tap never moves anything; the
   v40 ◀ ▶ buttons are gone, so no move row exists anywhere; step-1
   drags reorder selectedFiles, and the pure drag helpers
   (moveArrayItem, dragFinalIndex) are pinned directly. Run:
   node tests/photo-arrange.test.js (exit 0 = pass). */
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
    closest() { return null; },
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
async function fire(el, type, evt) {
  for (const h of (el._handlers[type] || [])) await h(evt || {});
}
const tick = () => new Promise((r) => setTimeout(r, 0));
function findButton(wrap, label) {
  const all = [];
  (function walk(el) {
    el.children.forEach((c) => { all.push(c); walk(c); });
  })(wrap);
  return all.find((c) => c.textContent === label);
}

(async () => {
  ctx("maxPhotos = 24;");
  // Listing-page render: Delete on every photo, no move row anywhere.
  sandbox.renderDraftPhotos({ photos: ["d1/1.jpg", "d1/2.jpg", "d1/3.jpg"] });
  const wraps = $("draft-photos").children;
  check("draft render shows all three photos", wraps.length === 3);
  check("no move row on any photo (v44: drag replaces the arrows)",
    wraps.every((w) => w.children.every((c) => c.className !== "thumb-move-row")));
  check("every draft photo gets a Delete button",
    wraps.every((w) => !!findButton(w, "×")));
  check("every draft photo gets an Edit button",
    wraps.every((w) => !!findButton(w, "Edit")));
  check("single photo gets no move row either",
    (() => {
      sandbox.renderDraftPhotos({ photos: ["d1/1.jpg"] });
      return $("draft-photos").children[0].children.every(
        (c) => c.className !== "thumb-move-row");
    })());

  // Pure drag helpers, pinned directly.
  check("moveArrayItem moves first to last",
    (() => {
      const a = ["a", "b", "c", "d"];
      sandbox.moveArrayItem(a, 0, 3);
      return a.join(",") === "b,c,d,a";
    })());
  check("moveArrayItem moves last to first",
    (() => {
      const a = ["a", "b", "c", "d"];
      sandbox.moveArrayItem(a, 3, 0);
      return a.join(",") === "d,a,b,c";
    })());
  check("moveArrayItem same index is a no-op",
    (() => {
      const a = ["a", "b", "c"];
      return sandbox.moveArrayItem(a, 1, 1) && a.join(",") === "a,b,c";
    })());
  check("moveArrayItem bad from leaves the array alone",
    (() => {
      const a = ["a", "b"];
      return !sandbox.moveArrayItem(a, 5, 0) && a.join(",") === "a,b";
    })());
  const rects3 = [
    { left: 0, top: 0, width: 128, height: 128 },
    { left: 136, top: 0, width: 128, height: 128 },
    { left: 272, top: 0, width: 128, height: 128 },
  ];
  check("dragFinalIndex first to end (right half of last)",
    sandbox.dragFinalIndex(rects3, 0, 380, 64) === 2);
  check("dragFinalIndex first stays first (left half of first)",
    sandbox.dragFinalIndex(rects3, 0, 10, 64) === 0);
  check("dragFinalIndex last to first",
    sandbox.dragFinalIndex(rects3, 2, 10, 64) === 0);
  check("dragFinalIndex over itself is a no-op (left half)",
    sandbox.dragFinalIndex(rects3, 1, 150, 64) === 1);
  check("dragFinalIndex over itself is a no-op (right half)",
    sandbox.dragFinalIndex(rects3, 1, 250, 64) === 1);
  check("dragFinalIndex middle to end across a row gap",
    sandbox.dragFinalIndex(rects3, 1, 400, 64) === 2);
  check("dragFinalIndex reaches the second row",
    sandbox.dragFinalIndex([
      { left: 0, top: 0, width: 128, height: 128 },
      { left: 136, top: 0, width: 128, height: 128 },
      { left: 0, top: 136, width: 128, height: 128 },
      { left: 136, top: 136, width: 128, height: 128 },
    ], 0, 250, 200) === 3);

  // Helpers for a pointer drag on a thumb frame.
  const frameOf = (wrap) => wrap.children[0];
  const setRects = (ws) => {
    ws.forEach((w, i) => {
      const left = (i % 3) * 136;
      const top = Math.floor(i / 3) * 136;
      w.getBoundingClientRect = () => ({ left, top, width: 128, height: 128 });
    });
  };
  const pointerEvt = (frame, x, y) => ({
    target: frame, clientX: x, clientY: y, pointerId: 1, button: 0,
    cancelable: true, preventDefault() {},
  });
  async function drag(frame, x0, y0, x1, y1) {
    await fire(frame, "pointerdown", pointerEvt(frame, x0, y0));
    await fire(frame, "pointermove", pointerEvt(frame, x1, y1));
    await fire(frame, "pointerup", pointerEvt(frame, x1, y1));
    await tick(); await tick();
  }

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

  // Drag: calls the move endpoint with {"to": n}, re-renders in the
  // new order. First photo dragged to the end of three.
  ctx("draft = { id: 'd9', photos: ['d9/1.jpg', 'd9/2.jpg', 'd9/3.jpg'] };");
  sandbox.renderDraftPhotos(ctx("draft"));
  setRects($("draft-photos").children);
  fetchCalls.length = 0;
  fetchQueue = [{ draft: { id: "d9", photos: ["d9/2.jpg", "d9/3.jpg", "d9/1.jpg"] } }];
  await drag(frameOf($("draft-photos").children[0]), 64, 64, 380, 64);
  check("drag posts to the move endpoint with to",
    fetchCalls.length === 1
    && fetchCalls[0].url === "/api/drafts/d9/photos/0/move"
    && fetchCalls[0].opts.method === "POST"
    && JSON.parse(fetchCalls[0].opts.body).to === 2);
  check("drag re-renders in the new order",
    $("draft-photos").children.length === 3
    && $("draft-photos").children[0].children[0].children[0].src === "/photos/d9/2.jpg");

  // A tap (down + up, no move) never reorders.
  ctx("draft = { id: 'd9', photos: ['d9/1.jpg', 'd9/2.jpg', 'd9/3.jpg'] };");
  sandbox.renderDraftPhotos(ctx("draft"));
  setRects($("draft-photos").children);
  fetchCalls.length = 0;
  const tapFrame = frameOf($("draft-photos").children[0]);
  await fire(tapFrame, "pointerdown", pointerEvt(tapFrame, 64, 64));
  await fire(tapFrame, "pointerup", pointerEvt(tapFrame, 64, 64));
  await tick(); await tick();
  check("a tap without a move posts nothing",
    fetchCalls.length === 0);

  // A drag that starts on the X button never starts.
  ctx("draft = { id: 'd9', photos: ['d9/1.jpg', 'd9/2.jpg', 'd9/3.jpg'] };");
  sandbox.renderDraftPhotos(ctx("draft"));
  setRects($("draft-photos").children);
  fetchCalls.length = 0;
  const xBtn = findButton($("draft-photos").children[0], "×");
  const xEvt = pointerEvt(xBtn, 120, 10);
  xEvt.target = { closest: () => xBtn };
  const xFrame = frameOf($("draft-photos").children[0]);
  await fire(xFrame, "pointerdown", xEvt);
  await fire(xFrame, "pointermove", pointerEvt(xFrame, 380, 64));
  await fire(xFrame, "pointerup", pointerEvt(xFrame, 380, 64));
  await tick(); await tick();
  check("a drag starting on the X button posts nothing",
    fetchCalls.length === 0);

  // Step-1 drag reorders selectedFiles (client-side only).
  ctx("selectedFiles = [{ url: 'blob:a' }, { url: 'blob:b' }, { url: 'blob:c' }];");
  sandbox.renderThumbs();
  const thumbs = $("thumbs").children;
  check("step-1 thumbs have no move row",
    thumbs.every((w) => w.children.every((c) => c.className !== "thumb-move-row")));
  setRects(thumbs);
  await drag(frameOf(thumbs[0]), 64, 64, 380, 64);
  check("step-1 drag moves the first pick to the end",
    ctx("selectedFiles").map((e) => e.url).join(",") === "blob:b,blob:c,blob:a");
  setRects($("thumbs").children);
  await drag(frameOf($("thumbs").children[2]), 336, 64, 10, 64);
  check("step-1 drag moves the last pick to the front",
    ctx("selectedFiles").map((e) => e.url).join(",") === "blob:a,blob:b,blob:c");

  if (failures) { console.log(failures + " FAILURES"); process.exit(1); }
  console.log("all photo-arrange checks pass");
})();
