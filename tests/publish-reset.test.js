/* v61 node tests: after a listing goes live, the photos on step 1
   are cleared so going back to start opens an empty grid ready for
   the next set. Run: node tests/publish-reset.test.js (exit 0 =
   pass). Same vm harness pattern as v50-photo-resume.test.js: a
   stub DOM with STATEFUL elements and a small in-memory server
   behind fetch (auto draft, photo append, reanalyze, resume
   record, publish, batch-publish). */
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
    set() { return true; },
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

/* Small in-memory server behind fetch. drafts holds the server-side
   truth; resumeId is the remembered draft; publishMode drives the
   single-publish answer ("ok", "ship", "pick", "scope"); batchWall
   is the draft id the batch run must stop on with a ship-from wall. */
const server = {
  drafts: {},
  resumeId: null,
  seq: 0,
  publishMode: "ok",
  batchWall: null,
};
const fetchCalls = [];

function blankDraft(id) {
  return { id: id, title: "", description: "", category: "", photos: [],
           price: "", condition: "", condition_details: "",
           best_offer: false, best_offer_auto_decline: "",
           best_offer_auto_accept: "", attributes: {},
           identification_notes: "" };
}

function route(url, opts) {
  const method = (opts && opts.method) || "GET";
  const u = String(url);
  if (method === "POST" && u === "/api/auto-draft") {
    const id = "auto" + (++server.seq);
    server.drafts[id] = blankDraft(id);
    return { status: 200, body: { draft: server.drafts[id] } };
  }
  let m = u.match(/^\/api\/drafts\/([^/]+)\/photos$/);
  if (method === "POST" && m) {
    const d = server.drafts[m[1]];
    if (!d) return { status: 404, body: { error: "Draft not found." } };
    const n = (opts.body && opts.body.getAll)
      ? opts.body.getAll("photos").length : 0;
    for (let i = 0; i < n; i++) d.photos.push(d.id + "/" + (d.photos.length + 1) + ".jpg");
    return { status: 200, body: { draft: d, added: n, dropped: 0 } };
  }
  m = u.match(/^\/api\/drafts\/([^/]+)\/reanalyze$/);
  if (method === "POST" && m) {
    const d = server.drafts[m[1]];
    if (!d || !d.photos.length) return { status: 404, body: { error: "Draft not found or has no photos." } };
    d.title = "Test item"; d.description = "A thing."; d.category = "Dolls";
    d.identification_notes = "AI saw the photos.";
    return { status: 200, body: { draft: d } };
  }
  m = u.match(/^\/api\/drafts\/([^/]+)$/);
  if (m && method === "GET") {
    const d = server.drafts[m[1]];
    if (!d) return { status: 404, body: { error: "Draft not found." } };
    return { status: 200, body: { draft: d } };
  }
  if (m && method === "PUT") {
    const d = server.drafts[m[1]];
    if (!d) return { status: 404, body: { error: "Draft not found." } };
    return { status: 200, body: { draft: d } };
  }
  if (m && method === "DELETE") {
    delete server.drafts[m[1]];
    if (server.resumeId === m[1]) server.resumeId = null;
    return { status: 200, body: { ok: true } };
  }
  if (method === "POST" && u === "/api/analyze") {
    const n = (opts.body && opts.body.getAll)
      ? opts.body.getAll("photos").length : 0;
    const id = "new" + (++server.seq);
    const d = blankDraft(id);
    for (let i = 0; i < n; i++) d.photos.push(id + "/" + (i + 1) + ".jpg");
    d.title = "Test item"; d.description = "A thing."; d.category = "Dolls";
    server.drafts[id] = d;
    return { status: 200, body: { draft: d } };
  }
  if (method === "POST" && u === "/api/quick-post") {
    const n = (opts.body && opts.body.getAll)
      ? opts.body.getAll("photos").length : 0;
    const id = "new" + (++server.seq);
    const d = blankDraft(id);
    for (let i = 0; i < n; i++) d.photos.push(id + "/" + (i + 1) + ".jpg");
    d.title = "Test item"; d.description = "A thing."; d.category = "Dolls";
    server.drafts[id] = d;
    return { status: 200, body: { draft: d } };
  }
  if (method === "GET" && u === "/api/resume-draft") {
    const d = server.drafts[server.resumeId];
    if (!d) return { status: 200, body: { draft_id: null } };
    return { status: 200, body: { draft_id: d.id, title: d.title,
                                  photo_count: d.photos.length, updated: "" } };
  }
  if (method === "POST" && u === "/api/resume-draft") {
    const parsed = JSON.parse(opts.body || "{}");
    server.resumeId = parsed.draft_id || null;
    return { status: 200, body: { draft_id: server.resumeId } };
  }
  if (method === "POST" && u === "/api/ebay/publish") {
    if (server.publishMode === "ship") return { status: 200, body: { needs_ship_from: true } };
    if (server.publishMode === "pick") return { status: 200, body: { needs_policy_pick: true, policies: [] } };
    if (server.publishMode === "scope") return { status: 200, body: { needs_scope_reconnect: true, error: "The app needs one new permission to read your shipping policies." } };
    return { status: 200, body: { listing_id: "98765", listing_url: "https://www.ebay.com/itm/98765" } };
  }
  if (method === "POST" && u === "/api/ebay/batch-publish") {
    const parsed = JSON.parse(opts.body || "{}");
    const ids = parsed.draft_ids || [];
    const results = [];
    for (const rawId of ids) {
      const id = String(rawId || "");
      const d = server.drafts[id];
      const title = (d && d.title) || "(untitled)";
      if (server.batchWall === id) {
        results.push({ draft_id: id, title: title, ok: false,
                       needs_ship_from: true, error: "No shop location." });
        break;
      }
      if (!d) {
        results.push({ draft_id: id, title: title, ok: false, error: "Draft not found." });
        continue;
      }
      if (d.ebay_listing_id) {
        results.push({ draft_id: d.id, title: title, ok: false, error: "Already live on eBay." });
        continue;
      }
      d.ebay_listing_id = "L-" + id;
      d.ebay_listing_url = "https://www.ebay.com/itm/L-" + id;
      results.push({ draft_id: d.id, title: title, ok: true,
                     listing_id: d.ebay_listing_id, listing_url: d.ebay_listing_url });
    }
    return { status: 200, body: { results: results } };
  }
  if (method === "GET" && u === "/api/ebay/status") {
    return { status: 200, body: { ebay_configured: true, connected: true,
                                  policy_defaults: {}, seller_name: "test",
                                  runame: "test" } };
  }
  if (method === "GET" && u === "/api/preset") {
    return { status: 200, body: { preset: null } };
  }
  if (method === "GET" && u === "/api/ebay/shipping-services") {
    return { status: 200, body: { carriers: {} } };
  }
  if (method === "GET" && u === "/api/ebay/fulfillment-policies") {
    return { status: 200, body: { policies: [] } };
  }
  return { status: 200, body: {} };
}

const registry = {};
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
    querySelector() { return makeEl(); },
    querySelectorAll() { return []; },
    addEventListener() {},
    body: makeEl(),
  },
  window: {
    addEventListener() {},
    location: { search: "" },
    history: {},
    innerWidth: 400,
    scrollTo() {},
    createImageBitmap: async () => ({ width: 120, height: 90, close() {} }),
  },
  createImageBitmap: async () => ({ width: 120, height: 90, close() {} }),
  fetch: async (url, opts = {}) => {
    fetchCalls.push({ url, opts });
    const u = String(url);
    if (u.startsWith("data:") || u.startsWith("/photos/")) {
      return {
        ok: true, status: 200,
        text: async () => "{}",
        blob: async () => new Blob(["fake-jpeg"], { type: "image/jpeg" }),
      };
    }
    const r = route(u, opts);
    return {
      ok: r.status >= 200 && r.status < 300, status: r.status,
      text: async () => JSON.stringify(r.body),
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

const unhandled = [];
process.on("unhandledRejection", (e) => { unhandled.push(e); });

let failures = 0;
function check(name, cond, extra) {
  if (!cond) { failures++; console.log("FAIL: " + name + (extra ? " — " + extra : "")); }
  else { console.log("ok: " + name); }
}
const $ = (id) => sandbox.document.getElementById(id);
const hidden = (id) => $(id).classList.contains("hidden");
const ctx = (code) => vm.runInContext(code, sandbox);
const fakeFile = (name) => new File(["x"], name, { type: "image/jpeg" });
const flush = () => new Promise((r) => setTimeout(r, 20));
async function drainAutoDraft() {
  await ctx("autoDraftQueue.catch(() => {})");
  await flush();
}
async function pickPhotos(names) {
  ctx("selectedFiles = []; autoDraftId = null; autoDraftStale = false; step1DraftId = null;");
  for (const n of names) {
    await sandbox.handlePickedFiles([fakeFile(n)], false);
    await drainAutoDraft();
  }
}
async function analyzeToDraft() {
  await sandbox.analyzePhotos();
  await flush();
  return ctx("draft");
}

(async () => {
  await flush(); /* the load-time resume check ran against an empty server */
  server.publishMode = "ok";
  server.batchWall = null;

  // ---- A: a listing that goes live clears the step-1 set ----
  await pickPhotos(["a.jpg", "b.jpg"]);
  const listed = await analyzeToDraft();
  check("Analyze builds the draft from the step-1 photos",
    !!listed && listed.title === "Test item" &&
    ctx("step1DraftId") === listed.id && ctx("selectedFiles.length") === 2 &&
    server.resumeId === listed.id);
  const outcome = await sandbox.postPublish({ draft_id: listed.id });
  await flush();
  check("publish succeeds and reports listed",
    outcome === "listed");
  check("the It is live note stays visible with the item number",
    !hidden("publish-ok") &&
    $("publish-ok").innerHTML.includes("98765") &&
    $("publish-ok").innerHTML.includes("It is live!"));
  check("the listing page still holds the just-listed draft",
    !!ctx("draft") && ctx("draft").id === listed.id &&
    ctx("draft").ebay_listing_id === "98765");
  check("publish clears the step-1 photos and previews",
    ctx("selectedFiles.length") === 0 && $("thumbs").innerHTML === "" &&
    $("btn-analyze").disabled === true);
  check("publish clears the auto draft id, the step-1 draft id, and the resume record",
    ctx("autoDraftId") === null && ctx("step1DraftId") === null &&
    server.resumeId === null);
  check("the listed draft stays in My drafts with its photos",
    !!server.drafts[listed.id] && server.drafts[listed.id].photos.length === 2);

  // A new set after the reset starts a brand-new draft, never the listed one.
  await sandbox.handlePickedFiles([fakeFile("z.jpg")], false);
  await drainAutoDraft();
  const freshId = ctx("autoDraftId");
  check("new photos after the reset start a brand-new draft",
    !!freshId && freshId !== listed.id && server.drafts[freshId] &&
    server.drafts[freshId].photos.length === 1);
  check("the listed draft's photos are untouched by the new set",
    server.drafts[listed.id].photos.length === 2);

  // ---- B: no reset on the paths that still need the seller ----
  for (const mode of ["ship", "pick", "scope"]) {
    server.publishMode = mode;
    $("publish-ok").classList.add("hidden"); /* fresh listing page per run */
    await pickPhotos(["c.jpg", "d.jpg"]);
    const d = await analyzeToDraft();
    const out = await sandbox.postPublish({ draft_id: d.id });
    await flush();
    check("publish " + mode + " reports its follow-up, not listed",
      out === "needs-address" || out === "needs-pick" || out === "needs-scope");
    check("publish " + mode + " leaves the step-1 photos alone",
      ctx("selectedFiles.length") === 2 && ctx("step1DraftId") === d.id &&
      server.resumeId === d.id && hidden("publish-ok"));
  }
  server.publishMode = "ok";

  // ---- C: listing a different draft leaves the step-1 set alone ----
  await pickPhotos(["e.jpg", "f.jpg"]);
  const draftA = await analyzeToDraft();
  const other = blankDraft("otherB");
  other.title = "Other item";
  other.photos = ["otherB/1.jpg"];
  server.drafts.otherB = other;
  sandbox.loadDraftIntoForm(other);
  await flush();
  const outC = await sandbox.postPublish({ draft_id: "otherB" });
  await flush();
  check("listing the other draft still reports listed",
    outC === "listed");
  check("listing a different draft leaves the step-1 set alone",
    ctx("selectedFiles.length") === 2 && ctx("step1DraftId") === draftA.id &&
    server.resumeId === "otherB");
  check("the other draft went live, the step-1 draft did not",
    ctx("draft").ebay_listing_id === "98765" &&
    server.drafts[draftA.id].ebay_listing_id === undefined);

  // ---- D: batch publish clears step 1 only when its draft went live ----
  await pickPhotos(["g.jpg"]);
  const batchAuto = ctx("autoDraftId");
  ctx("batchIncluded = [{ id: '" + batchAuto + "', title: '' }];");
  $("b-category").value = "123";
  const sigD = await sandbox.runBatch(null, null);
  await flush();
  check("batch publishes the auto draft",
    sigD && server.drafts[batchAuto] && !!server.drafts[batchAuto].ebay_listing_id);
  check("batch clears the step-1 set when its draft went live",
    ctx("selectedFiles.length") === 0 && ctx("autoDraftId") === null &&
    server.resumeId === null);

  // A batch that stops on a wall for the step-1 draft clears nothing.
  await pickPhotos(["h.jpg"]);
  const wallAuto = ctx("autoDraftId");
  server.batchWall = wallAuto;
  ctx("batchIncluded = [{ id: '" + wallAuto + "', title: '' }];");
  const sigW = await sandbox.runBatch(null, null);
  await flush();
  check("batch reports the ship-from wall",
    sigW && sigW.needsAddress === true);
  check("batch stopped on a wall leaves the step-1 photos alone",
    ctx("selectedFiles.length") === 1 && ctx("autoDraftId") === wallAuto &&
    server.resumeId === wallAuto);
  server.batchWall = null;

  // A batch that only publishes other drafts leaves the step-1 set alone.
  const otherC = blankDraft("otherC");
  otherC.title = "Third item";
  otherC.photos = ["otherC/1.jpg"];
  server.drafts.otherC = otherC;
  ctx("batchIncluded = [{ id: 'otherC', title: 'Third item' }];");
  const sigO = await sandbox.runBatch(null, null);
  await flush();
  check("batch publishes the other draft",
    sigO && server.drafts.otherC.ebay_listing_id === "L-otherC");
  check("batch of other drafts leaves the step-1 photos alone",
    ctx("selectedFiles.length") === 1 && ctx("autoDraftId") === wallAuto);

  check("no unhandled rejection anywhere in the run",
    unhandled.length === 0,
    unhandled.map((e) => String((e && e.stack) || e)).join(" | "));

  console.log(failures === 0 ? "publish-reset: all pass" : "publish-reset: " + failures + " FAILURES");
  process.exit(failures === 0 ? 0 : 1);
})().catch((e) => { console.log("ERROR: " + (e && e.stack || e)); process.exit(1); });
