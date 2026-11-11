/* v50 node tests: typed-code normalization + create exclusions (F1/F2),
   the NOT_SPECIFIED matcher note (F3), and photo persistence +
   return-to-draft (auto draft + resume). Run: node tests/v50-photo-resume.test.js
   (exit 0 = pass). The app is a browser script, so this stubs the DOM
   with STATEFUL elements (same pattern as multi-photo.test.js) and a
   small in-memory server behind fetch: the auto draft, the photo
   append/delete/move/replace endpoints, reanalyze, the resume record,
   and the Analyze/Quick-post fallbacks. */
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
   truth; resumeId is the remembered draft; fail holds "METHOD URL"
   keys that answer 500. */
const server = {
  drafts: {},
  resumeId: null,
  seq: 0,
  fail: new Set(),
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
  const key = method + " " + u;
  if (server.fail.has(key)) return { status: 500, body: { error: "boom" } };
  if (method === "POST" && u === "/api/auto-draft") {
    const id = "auto" + (++server.seq);
    server.drafts[id] = blankDraft(id);
    return { status: 200, body: { draft: server.drafts[id] } };
  }
  // Test hook: fail every photo append (the stale-save path).
  if (server.failAppend && method === "POST" && /\/photos$/.test(u)) {
    return { status: 500, body: { error: "boom" } };
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
  m = u.match(/^\/api\/drafts\/([^/]+)\/photos\/(\d+)$/);
  if (method === "DELETE" && m) {
    const d = server.drafts[m[1]];
    if (!d) return { status: 404, body: { error: "Draft not found." } };
    d.photos.splice(Number(m[2]), 1);
    return { status: 200, body: { draft: d } };
  }
  m = u.match(/^\/api\/drafts\/([^/]+)\/photos\/(\d+)\/move$/);
  if (method === "POST" && m) {
    const d = server.drafts[m[1]];
    if (!d) return { status: 404, body: { error: "Draft not found." } };
    const to = JSON.parse(opts.body || "{}").to;
    const item = d.photos.splice(Number(m[2]), 1)[0];
    d.photos.splice(to, 0, item);
    return { status: 200, body: { draft: d } };
  }
  m = u.match(/^\/api\/drafts\/([^/]+)\/photos\/(\d+)\/replace$/);
  if (method === "POST" && m) {
    const d = server.drafts[m[1]];
    if (!d) return { status: 404, body: { error: "Draft not found." } };
    return { status: 200, body: { draft: d } };
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
  if (method === "GET" && u === "/api/ebay/shipping-services") {
    return { status: 200, body: server.shippingServices || {} };
  }
  if (method === "POST" && u === "/api/ebay/policy-for-service") {
    return { status: 200, body: server.policyForService || {} };
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

let failures = 0;
function check(name, cond) {
  if (!cond) { failures++; console.log("FAIL: " + name); }
  else { console.log("ok: " + name); }
}
const $ = (id) => sandbox.document.getElementById(id);
const hidden = (id) => $(id).classList.contains("hidden");
const ctx = (code) => vm.runInContext(code, sandbox);
const fakeFile = (name) => new File(["x"], name, { type: "image/jpeg" });
const flush = () => new Promise((r) => setTimeout(r, 20));
async function drainAutoDraft() {
  // `let` bindings are not properties of the sandbox object, so read
  // the queue through the context, not off `sandbox`.
  await ctx("autoDraftQueue.catch(() => {})");
  await flush();
}

(async () => {
  ctx("maxPhotos = 24;");
  // The load-time resume check ran against an empty server; wait for it.
  await flush();

  // ---- F2: typed codes match case-insensitively, by code or label ----
  check("canonical lowercases a typed code",
    sandbox.canonicalServiceCode("uspsparcel") === "USPSParcel" &&
    sandbox.canonicalServiceCode("fedexsmartpost") === "FedExSmartPost");
  check("canonical accepts the display label with spaces",
    sandbox.canonicalServiceCode("USPS Ground Advantage") === "USPSParcel" &&
    sandbox.canonicalServiceCode("usps ground advantage") === "USPSParcel" &&
    sandbox.canonicalServiceCode("UPS Ground Saver") === "UPSGroundSaver");
  check("canonical still maps the rejected code",
    sandbox.canonicalServiceCode("USPSGroundAdvantage") === "USPSParcel");
  check("canonical leaves an unknown code alone",
    sandbox.canonicalServiceCode("BogusCode") === "BogusCode");
  const resolved = sandbox.resolveTypedServiceCode("uspsparcel");
  check("resolve typed code reports known",
    resolved.code === "USPSParcel" && resolved.known === true);
  const unresolved = sandbox.resolveTypedServiceCode("BogusCode");
  check("resolve typed code reports unknown",
    unresolved.code === "BogusCode" && unresolved.known === false);
  const mkForm = (v, other) => ({ dom_service_1: v, dom_service_1_other: other || "" });
  check("typed unknown code errors in plain words",
    sandbox.createServiceTypedError("dom", 1, mkForm("__OTHER__", "BogusCode"))
      .includes("may not know the shipping code") &&
    sandbox.createServiceTypedError("dom", 1, mkForm("__OTHER__", "BogusCode"))
      .includes("BogusCode"));
  check("typed known code passes",
    sandbox.createServiceTypedError("dom", 1, mkForm("__OTHER__", "uspsparcel")) === "");
  check("typed held-back code errors plainly",
    sandbox.createServiceTypedError("dom", 1, mkForm("__OTHER__", "UPS Ground Saver"))
      .includes("cannot be used for a new policy yet"));
  check("dropdown held-back code errors plainly",
    sandbox.createServiceTypedError("dom", 1, mkForm("UPSGroundSaver", ""))
      .includes("cannot be used for a new policy yet"));
  check("dropdown valid code passes",
    sandbox.createServiceTypedError("dom", 1, mkForm("USPSParcel", "")) === "");

  // collectPolicyCreateForm surfaces the unknown-code error, not a POST.
  $("pc-name").value = "Test policy";
  $("pc-dom-cost-type").value = "CALCULATED";
  $("pc-dom-service-1").value = "__OTHER__";
  $("pc-dom-service-1-other").value = "BogusCode";
  $("pc-handling").value = "2";
  const collected = sandbox.collectPolicyCreateForm();
  check("form collector refuses an unknown typed code",
    collected.errors.some((e) => e.includes("may not know the shipping code")));

  // ---- F1: the create form offers everything except UPSGroundSaver ----
  server.shippingServices = {
    domestic: [{ code: "UPSGroundSaver", label: "UPS Ground Saver" },
               { code: "USPSParcel", label: "USPS Ground Advantage" },
               { code: "FedExSmartPost", label: "FedEx Ground Economy" }],
    create_domestic: [{ code: "USPSParcel", label: "USPS Ground Advantage" },
                      { code: "FedExSmartPost", label: "FedEx Ground Economy" }],
    international: [], create_international: [],
  };
  ctx("createServicesFetched = false;");
  await sandbox.openCreateForm();
  const offered = $("pc-dom-service-1").children.map((o) => o.value);
  check("create form offers Ground Advantage and Ground Economy",
    offered.includes("USPSParcel") && offered.includes("FedExSmartPost"));
  check("create form does NOT offer UPS Ground Saver",
    !offered.includes("UPSGroundSaver"));

  // Prefilling the held-back code leaves the service blank + a plain note.
  await sandbox.openCreateFormPrefilled("UPS", "UPSGroundSaver");
  check("prefill of a held-back code leaves the service blank",
    $("pc-dom-service-1").value === "");
  check("prefill of a held-back code says why in plain words",
    !hidden("policy-create-error") &&
    $("policy-create-error").textContent.includes("cannot be used for a new policy yet"));
  await sandbox.openCreateFormPrefilled("USPS", "USPSParcel");
  check("prefill of a valid code still fills the service",
    $("pc-dom-service-1").value === "USPSParcel");

  // ---- F3: a NOT_SPECIFIED policy is offered by name, not "no policy" ----
  ctx("draft = { id: 'd1' };");
  server.drafts.d1 = blankDraft("d1");
  server.policyForService = {
    policy_id: null, flat_policy_id: null, flat_name: null,
    not_specified_policy_id: "777", not_specified_name: "Dummy Unset Price",
  };
  $("ship-carrier").value = "USPS";
  $("policy-create-form").classList.add("hidden"); /* the F1 prefill above opened it */
  sandbox.populateShipService();
  $("ship-service").value = "USPSPriority";
  $("ship-service-note").classList.add("hidden");
  await sandbox.onShipServicePicked();
  check("NOT_SPECIFIED policy is offered by name",
    !$("ship-service-note").classList.contains("hidden") &&
    $("ship-service-note").textContent.includes("Dummy Unset Price") &&
    $("ship-service-note").textContent.includes("USPS Priority Mail"));
  check("NOT_SPECIFIED offer has a Use that policy button",
    $("ship-service-note").children.some((c) => c.id === "btn-use-not-specified-policy"));
  check("NOT_SPECIFIED offer does not open the create form",
    hidden("policy-create-form"));

  // ---- auto draft: photos save the moment they are picked ----
  ctx("selectedFiles = []; autoDraftId = null; autoDraftStale = false;");
  fetchCalls.length = 0;
  await sandbox.handlePickedFiles([fakeFile("a.jpg")], false);
  await drainAutoDraft();
  check("first photo creates the auto draft",
    fetchCalls.some((c) => String(c.url) === "/api/auto-draft" && c.opts.method === "POST"));
  const autoId = ctx("autoDraftId");
  check("first photo is uploaded to the auto draft",
    !!autoId && server.drafts[autoId] && server.drafts[autoId].photos.length === 1);
  check("auto draft is remembered as the resume draft",
    server.resumeId === autoId);

  await sandbox.handlePickedFiles([fakeFile("b.jpg")], false);
  await drainAutoDraft();
  check("second photo appends to the same auto draft, no second draft",
    fetchCalls.filter((c) => String(c.url) === "/api/auto-draft").length === 1 &&
    server.drafts[autoId].photos.length === 2);

  // Remove + reorder land on the server draft too.
  sandbox.removePhoto(0);
  await drainAutoDraft();
  check("remove deletes the same photo from the auto draft",
    server.drafts[autoId].photos.length === 1);
  await sandbox.handlePickedFiles([fakeFile("c.jpg")], false);
  await drainAutoDraft();
  sandbox.reorderPhoto(1, 0);
  await drainAutoDraft();
  check("drag reorder moves the photo on the auto draft",
    server.drafts[autoId].photos.length === 2);

  // Replace (Choose photos) deletes the old auto draft, starts fresh.
  fetchCalls.length = 0;
  await sandbox.handlePickedFiles([fakeFile("d.jpg")], true);
  await drainAutoDraft();
  const newAutoId = ctx("autoDraftId");
  check("Choose photos deletes the old auto draft",
    fetchCalls.some((c) => String(c.url) === "/api/drafts/" + autoId &&
      c.opts.method === "DELETE"));
  check("Choose photos starts a fresh auto draft with the new set",
    !!newAutoId && newAutoId !== autoId && server.drafts[newAutoId] &&
    server.drafts[newAutoId].photos.length === 1 && !server.drafts[autoId]);

  // ---- Analyze reuses the auto draft; Quick post reuses it too ----
  const draftsBeforeAnalyze = Object.keys(server.drafts).length;
  const reuseId = ctx("autoDraftId");
  fetchCalls.length = 0;
  await sandbox.analyzePhotos();
  check("Analyze reuses the auto draft (reanalyze), never re-uploads",
    fetchCalls.some((c) => String(c.url).includes("/reanalyze")) &&
    !fetchCalls.some((c) => String(c.url) === "/api/analyze"));
  check("Analyze fills the draft from the auto draft, no duplicate draft",
    ctx("draft") && ctx("draft").title === "Test item" &&
    ctx("draft").id === reuseId &&
    Object.keys(server.drafts).length === draftsBeforeAnalyze);
  check("Analyze clears the auto draft id",
    ctx("autoDraftId") === null);

  ctx("selectedFiles = []; autoDraftId = null; autoDraftStale = false;");
  await sandbox.handlePickedFiles([fakeFile("e.jpg")], false);
  await drainAutoDraft();
  const auto2 = ctx("autoDraftId");
  fetchCalls.length = 0;
  await sandbox.quickPostPhotos();
  check("Quick post opens the auto draft directly, never re-uploads",
    fetchCalls.some((c) => String(c.url) === "/api/drafts/" + auto2 &&
      (c.opts.method || "GET") === "GET") &&
    !fetchCalls.some((c) => String(c.url) === "/api/quick-post"));
  check("Quick post clears the auto draft id",
    ctx("autoDraftId") === null);

  // ---- stale fallback: a failed save falls back to the in-memory upload ----
  ctx("selectedFiles = []; autoDraftId = null; autoDraftStale = false;");
  server.failAppend = true;
  await sandbox.handlePickedFiles([fakeFile("f.jpg")], false);
  await drainAutoDraft();
  // v54: the failed save marks the draft stale but KEEPS its id, so the
  // next photo retries the same draft instead of starting a second one.
  check("a failed photo save marks the draft stale, keeps the same draft, and shows plain words",
    ctx("autoDraftStale") === true && !!ctx("autoDraftId") &&
    !$("analyze-error").classList.contains("hidden") &&
    $("analyze-error").textContent.includes("didn't save to the app yet"));
  server.failAppend = false;
  fetchCalls.length = 0;
  await sandbox.analyzePhotos();
  check("stale Analyze uploads the photos on screen instead",
    fetchCalls.some((c) => String(c.url) === "/api/analyze") &&
    !fetchCalls.some((c) => String(c.url).includes("/reanalyze")));

  // ---- resume: titled draft offers Continue; photo-only restores ----
  server.drafts.d9 = blankDraft("d9");
  server.drafts.d9.title = "Test item";
  server.drafts.d9.photos = ["d9/1.jpg"];
  server.resumeId = "d9";
  $("resume-card").classList.add("hidden");
  await sandbox.checkResumeDraft();
  check("titled resume draft shows the Continue card naming it",
    !hidden("resume-card") &&
    $("resume-text").textContent.includes("Test item"));
  await sandbox.openResumeDraft();
  check("Continue opens the remembered draft",
    ctx("draft") && ctx("draft").id === "d9" && hidden("resume-card"));

  server.drafts.d8 = blankDraft("d8");
  server.drafts.d8.photos = ["d8/1.jpg", "d8/2.jpg"];
  server.resumeId = "d8";
  ctx("selectedFiles = []; autoDraftId = null;");
  $("resume-card").classList.add("hidden");
  await sandbox.checkResumeDraft();
  check("photo-only resume draft restores its photos into the grid",
    ctx("selectedFiles.length") === 2 && ctx("autoDraftId") === "d8" &&
    hidden("resume-card") && $("photo-count").textContent === "2 of 24 photos");

  server.resumeId = null;
  $("resume-card").classList.add("hidden");
  await sandbox.checkResumeDraft();
  check("no resume record means no card",
    hidden("resume-card"));

  // Dismiss drops the card and clears the record.
  server.resumeId = "d9";
  await sandbox.checkResumeDraft();
  sandbox.dismissResumeCard();
  await flush();
  check("dismiss hides the card and clears the resume record",
    hidden("resume-card") && server.resumeId === null);

  // Opening a draft records it as the resume draft.
  server.drafts.d7 = blankDraft("d7");
  server.drafts.d7.title = "Another item";
  sandbox.loadDraftIntoForm(server.drafts.d7);
  await flush();
  check("opening a draft records it as the resume draft",
    server.resumeId === "d7");

  console.log(failures === 0 ? "v50-photo-resume: all pass" : "v50-photo-resume: " + failures + " FAILURES");
  process.exit(failures === 0 ? 0 : 1);
})().catch((e) => { console.log("ERROR: " + (e && e.stack || e)); process.exit(1); });
