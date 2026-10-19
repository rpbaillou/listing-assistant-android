/* Listing Assistant v2.8 - front end. Plain steps, no framework. */
let draft = null;
let selectedFiles = [];
let saveTimer = null;

const $ = (id) => document.getElementById(id);

function show(stepId) {
  document.querySelectorAll("main .step").forEach((s) => s.classList.add("hidden"));
  $(stepId).classList.remove("hidden");
  window.scrollTo(0, 0);
}

function showError(msg) {
  const el = $("analyze-error");
  el.textContent = msg;
  el.classList.remove("hidden");
}
function hideError() { $("analyze-error").classList.add("hidden"); }

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const text = await r.text();
  let data = {};
  let isJson = false;
  try { data = text ? JSON.parse(text) : {}; isJson = true; }
  catch (e) { isJson = false; }
  if (!r.ok) {
    if (isJson && data.error) throw new Error(data.error);
    // Non-JSON error body (e.g. Cloudflare's 502 page when the tunnel times
    // out on a huge upload): say what to do, not the status code.
    if (!isJson) throw new Error("The request took too long or the connection dropped. Try fewer photos.");
    throw new Error("Request failed (" + r.status + ")");
  }
  return data;
}

/* ---------- step 1: photos ---------- */
/* v2.8: shrink each photo on the phone before upload (max 1600px long edge,
   JPEG q85) so 10 photos stay well under the tunnel timeout. Files the
   browser cannot decode (e.g. HEIC) are skipped with a plain-words note. */
const MAX_EDGE = 1600;
let previewUrls = [];

function decodeFile(file) {
  if (window.createImageBitmap) {
    return createImageBitmap(file).catch(() => null);
  }
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
    img.onerror = () => { URL.revokeObjectURL(url); resolve(null); };
    img.src = url;
  });
}

async function downscaleFile(file, idx) {
  const bmp = await decodeFile(file);
  if (!bmp) return { skipped: true };
  const scale = Math.min(1, MAX_EDGE / Math.max(bmp.width, bmp.height));
  const w = Math.max(1, Math.round(bmp.width * scale));
  const h = Math.max(1, Math.round(bmp.height * scale));
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  canvas.getContext("2d").drawImage(bmp, 0, 0, w, h);
  if (bmp.close) bmp.close();
  const blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", 0.85));
  if (!blob) return { skipped: true };
  return { blob: blob, name: "photo" + (idx + 1) + ".jpg" };
}

function clearPreviews() {
  previewUrls.forEach((u) => URL.revokeObjectURL(u));
  previewUrls = [];
  $("thumbs").innerHTML = "";
  $("skip-note").classList.add("hidden");
}

$("photo-input").addEventListener("change", async (e) => {
  const picked = Array.from(e.target.files || []).slice(0, 10);
  clearPreviews();
  const box = $("thumbs");
  selectedFiles = [];
  let skipped = 0;
  for (let i = 0; i < picked.length; i++) {
    const r = await downscaleFile(picked[i], i);
    if (r.skipped) {
      skipped++;
      const ph = document.createElement("div");
      ph.className = "thumb-skip";
      ph.textContent = "can't preview";
      box.appendChild(ph);
      continue;
    }
    selectedFiles.push(r);
    const img = document.createElement("img");
    const u = URL.createObjectURL(r.blob);
    previewUrls.push(u);
    img.src = u;
    box.appendChild(img);
  }
  if (skipped > 0) {
    const n = $("skip-note");
    n.textContent = skipped + (skipped === 1 ? " photo" : " photos") +
      " couldn't be read and " + (skipped === 1 ? "was" : "were") + " skipped.";
    n.classList.remove("hidden");
  }
  $("btn-analyze").disabled = selectedFiles.length === 0;
  hideError();
});

$("btn-analyze").addEventListener("click", async () => {
  hideError();
  $("spinner").classList.remove("hidden");
  $("btn-analyze").disabled = true;
  try {
    const fd = new FormData();
    selectedFiles.forEach((f) => fd.append("photos", f.blob, f.name));
    const { draft: d } = await api("/api/analyze", { method: "POST", body: fd });
    loadDraftIntoForm(d);
    show("step-draft");
  } catch (err) {
    showError(err.message);
  } finally {
    $("spinner").classList.add("hidden");
    $("btn-analyze").disabled = selectedFiles.length === 0;
  }
});

/* ---------- step 2: edit draft ---------- */
function loadDraftIntoForm(d) {
  draft = d;
  $("f-title").value = d.title || "";
  $("f-category").value = d.category || "";
  $("f-description").value = d.description || "";
  $("f-price").value = d.price || "";
  updateCount();
  const box = $("draft-photos");
  box.innerHTML = "";
  (d.photos || []).forEach((p) => {
    const img = document.createElement("img");
    img.src = "/photos/" + p;
    box.appendChild(img);
  });
  renderAttrs(d.attributes || {});
  const notes = $("ai-notes");
  if (d.identification_notes) {
    notes.textContent = "AI notes: " + d.identification_notes;
    notes.classList.remove("hidden");
  } else notes.classList.add("hidden");
  $("btn-photos-zip").href = "/api/drafts/" + d.id + "/photos.zip";
}

function renderAttrs(attrs) {
  const box = $("attrs");
  box.innerHTML = "";
  Object.entries(attrs).forEach(([k, v]) => box.appendChild(attrRow(k, v)));
  if (Object.keys(attrs).length === 0) box.appendChild(attrRow("", ""));
}

function attrRow(k, v) {
  const row = document.createElement("div");
  row.className = "attr-row";
  row.innerHTML =
    '<input type="text" class="ak" placeholder="Name (Brand)" value="">' +
    '<input type="text" class="av" placeholder="Value" value="">' +
    '<button type="button" title="Remove">×</button>';
  row.querySelector(".ak").value = k;
  row.querySelector(".av").value = v;
  row.querySelector("button").addEventListener("click", () => {
    row.remove();
    scheduleSave();
  });
  row.querySelectorAll("input").forEach((i) =>
    i.addEventListener("input", scheduleSave));
  return row;
}

$("btn-add-attr").addEventListener("click", () => {
  $("attrs").appendChild(attrRow("", ""));
});

function collectForm() {
  const attrs = {};
  document.querySelectorAll("#attrs .attr-row").forEach((row) => {
    const k = row.querySelector(".ak").value.trim();
    const v = row.querySelector(".av").value.trim();
    if (k) attrs[k] = v;
  });
  return {
    title: $("f-title").value.trim(),
    category: $("f-category").value.trim(),
    description: $("f-description").value.trim(),
    price: $("f-price").value.trim(),
    attributes: attrs,
  };
}

function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveDraft, 1200);
}

async function saveDraft() {
  if (!draft) return;
  try {
    const { draft: d } = await api("/api/drafts/" + draft.id, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectForm()),
    });
    draft = d;
  } catch (e) { /* autosave is best-effort; the Review step saves too */ }
}

["f-title", "f-category", "f-description", "f-price"].forEach((id) => {
  $(id).addEventListener("input", () => { updateCount(); scheduleSave(); });
});

function updateCount() {
  $("title-count").textContent = $("f-title").value.length + "/80";
}

$("btn-solds").addEventListener("click", () => {
  const q = encodeURIComponent($("f-title").value.trim() || "vintage item");
  window.open(
    "https://www.ebay.com/sch/i.html?_nkw=" + q + "&LH_Sold=1&LH_Complete=1",
    "_blank"
  );
});

$("btn-reanalyze").addEventListener("click", async () => {
  if (!draft || !confirm("Ask the AI to look at the photos again? Your typed price is kept.")) return;
  try {
    const { draft: d } = await api("/api/drafts/" + draft.id + "/reanalyze", { method: "POST" });
    loadDraftIntoForm(d);
  } catch (err) { alert(err.message); }
});

$("btn-to-review").addEventListener("click", async () => {
  await saveDraft();
  renderReview();
  show("step-review");
});

/* ---------- step 3: review ---------- */
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function renderReview() {
  const d = Object.assign({}, draft, collectForm());
  let attrs = "";
  Object.entries(d.attributes || {}).forEach(([k, v]) => {
    attrs += "<dt>" + esc(k) + "</dt><dd>" + esc(v) + "</dd>";
  });
  $("review-card").innerHTML =
    "<h3>" + esc(d.title || "(no title)") + "</h3>" +
    "<p><b>Price:</b> " + (d.price ? "$" + esc(d.price) : "(not set)") + "</p>" +
    "<p><b>Category:</b> " + esc(d.category || "(not set)") + "</p>" +
    "<p>" + esc(d.description).replace(/\n/g, "<br>") + "</p>" +
    (attrs ? "<dl>" + attrs + "</dl>" : "") +
    "<p><b>Photos:</b> " + (d.photos || []).length + "</p>";
}

$("btn-back-edit").addEventListener("click", () => show("step-draft"));
$("btn-to-handoff").addEventListener("click", () => { renderEbayStep(); show("step-handoff"); });

/* ---------- step 4: handoff ---------- */
async function copyText(text, okMsg) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (_e) {}
    ta.remove();
  }
  const el = $("copy-ok");
  el.textContent = okMsg || "Copied. Paste it into eBay.";
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 2500);
}

function fullListingText() {
  const d = Object.assign({}, draft, collectForm());
  let t = d.title + "\n\n" + d.description;
  const keys = Object.keys(d.attributes || {});
  if (keys.length) {
    t += "\n\nItem specifics:\n";
    keys.forEach((k) => { t += k + ": " + d.attributes[k] + "\n"; });
  }
  if (d.price) t += "\nPrice: $" + d.price;
  return t;
}

$("btn-copy-title").addEventListener("click", () => copyText($("f-title").value, "Title copied. Paste it into eBay."));
$("btn-copy-desc").addEventListener("click", () => copyText($("f-description").value, "Description copied. Paste it into eBay."));
$("btn-copy-all").addEventListener("click", () => copyText(fullListingText(), "Everything copied. Paste it into eBay."));

$("btn-new").addEventListener("click", () => {
  draft = null;
  selectedFiles = [];
  $("photo-input").value = "";
  clearPreviews();
  $("btn-analyze").disabled = true;
  hideError();
  show("step-photos");
});

/* ---------- drafts panel ---------- */
$("btn-drafts").addEventListener("click", async () => {
  const list = $("drafts-list");
  list.innerHTML = "<p class='hint'>Loading…</p>";
  show("drafts-panel");
  try {
    const { drafts } = await api("/api/drafts");
    if (!drafts.length) {
      list.innerHTML = "<p class='hint'>No drafts yet. Start one from step 1.</p>";
      return;
    }
    list.innerHTML = "";
    drafts.forEach((d) => {
      const row = document.createElement("div");
      row.className = "draft-item";
      row.innerHTML =
        (d.thumb ? '<img src="/photos/' + d.thumb + '">' : "") +
        '<div class="t"><b>' + esc(d.title || "(untitled)") + "</b><span>" +
        esc(d.updated || "") + (d.listed ? " · <b>Listed on eBay</b>" : "") + "</span></div>" +
        '<button type="button" class="secondary">Open</button>' +
        '<button type="button" class="del">Delete</button>';
      row.querySelector(".secondary").addEventListener("click", async () => {
        const { draft: full } = await api("/api/drafts/" + d.id);
        loadDraftIntoForm(full);
        show("step-draft");
      });
      row.querySelector(".del").addEventListener("click", async () => {
        if (!confirm("Delete this draft and its photos?")) return;
        await api("/api/drafts/" + d.id, { method: "DELETE" });
        $("btn-drafts").click();
      });
      list.appendChild(row);
    });
  } catch (e) {
    list.innerHTML = "<p class='error'>" + esc(e.message) + "</p>";
  }
});
$("btn-close-drafts").addEventListener("click", () => show(draft ? "step-draft" : "step-photos"));

/* ---------- init ---------- */
api("/api/config").then((c) => {
  if (!c.gemini_configured) $("key-warning").classList.remove("hidden");
  if (c.max_photos) {
    document.querySelector("#step-photos .hint").textContent =
      "Choose up to " + c.max_photos + " photos of one item from your photo library.";
  }
}).catch(() => {});

/* ---------- v2: eBay one-tap listing ---------- */
let ebayStatus = null;

async function renderEbayStep() {
  const box = $("ebay-status");
  $("publish-panel").classList.add("hidden");
  $("publish-error").classList.add("hidden");
  $("publish-ok").classList.add("hidden");
  try {
    ebayStatus = await api("/api/ebay/status");
  } catch (e) {
    box.innerHTML = "<p class='hint' style='margin:0'>Could not check eBay status.</p>";
    return;
  }
  if (!ebayStatus.ebay_configured) {
    box.innerHTML = "<p style='margin:0'><b>eBay is not set up yet.</b></p>" +
      "<p class='hint'>Add your eBay App ID and Cert ID to the .env file " +
      "(setup guide: Connect eBay), then restart the app. The manual copy buttons below still work.</p>";
    return;
  }
  if (!ebayStatus.connected) {
    box.innerHTML = "<p style='margin:0'><b>eBay is not connected.</b></p>" +
      "<p class='hint'>Tap the button below in this browser, sign in to eBay, " +
      "and approve access. You only do this once.</p>" +
      "<button id='btn-ebay-connect' class='primary' type='button'>Connect eBay</button>";
    $("btn-ebay-connect").addEventListener("click", () => {
      window.location.href = "/api/ebay/connect";
    });
    return;
  }
  if (draft && draft.ebay_listing_id) {
    box.innerHTML = "<p style='margin:0'><b>This draft is already live on eBay.</b></p>" +
      "<p class='hint'><a href='" + draft.ebay_listing_url + "' target='_blank'>View your listing</a>" +
      " · item " + esc(draft.ebay_listing_id) + "</p>";
    return;
  }
  const missing = [];
  if (!ebayStatus.policies_configured) missing.push("shop policy numbers");
  if (missing.length) {
    box.innerHTML = "<p style='margin:0'><b>eBay is connected.</b> Still needed: " +
      esc(missing.join(" and ")) + ". See the setup guide (Connect eBay).</p>";
    return;
  }
  box.innerHTML = "<p style='margin:0'><b>eBay connected.</b> Ready to list.</p>";
  // prefill the publish panel
  const sel = $("p-condition");
  sel.innerHTML = "";
  (ebayStatus.conditions || []).forEach((c) => {
    const o = document.createElement("option");
    o.value = c.value; o.textContent = c.label;
    sel.appendChild(o);
  });
  $("p-price").value = $("f-price").value || "";
  $("p-category").value = "";
  $("cat-suggestions").classList.add("hidden");
  const pd = ebayStatus.policy_defaults || {};
  $("p-fulfillment").value = pd.fulfillment || "";
  $("p-payment").value = pd.payment || "";
  $("p-return").value = pd.return || "";
  $("publish-panel").classList.remove("hidden");
}

$("btn-cat-suggest").addEventListener("click", async () => {
  const box = $("cat-suggestions");
  box.classList.remove("hidden");
  box.innerHTML = "<p class='hint'>Asking eBay…</p>";
  try {
    const { suggestions } = await api("/api/ebay/category-suggest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ q: $("f-title").value.trim() }),
    });
    if (!suggestions.length) {
      box.innerHTML = "<p class='hint'>No suggestions. Try a simpler title.</p>";
      return;
    }
    box.innerHTML = "";
    suggestions.forEach((s) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "ghost sug";
      b.textContent = s.name + " (" + s.id + ")";
      b.addEventListener("click", () => {
        $("p-category").value = s.id;
        box.classList.add("hidden");
      });
      box.appendChild(b);
    });
  } catch (e) {
    box.innerHTML = "<p class='error'>" + esc(e.message) + "</p>";
  }
});

$("btn-publish").addEventListener("click", async () => {
  hidePublishMsg();
  if (!draft) return;
  if (!confirm("Create a REAL live eBay listing for this item now?")) return;
  $("publish-spinner").classList.remove("hidden");
  $("btn-publish").disabled = true;
  try {
    const body = {
      draft_id: draft.id,
      title: $("f-title").value.trim(),
      description: $("f-description").value.trim(),
      price: $("p-price").value.trim(),
      condition: $("p-condition").value,
      category_id: $("p-category").value.trim(),
      fulfillment_policy_id: $("p-fulfillment").value.trim(),
      payment_policy_id: $("p-payment").value.trim(),
      return_policy_id: $("p-return").value.trim(),
    };
    const r = await api("/api/ebay/publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    draft.ebay_listing_id = r.listing_id;
    draft.ebay_listing_url = r.listing_url;
    const ok = $("publish-ok");
    ok.innerHTML = "It is live! <a href='" + r.listing_url + "' target='_blank'>View your eBay listing</a>" +
      "<br>Item number: " + esc(r.listing_id);
    ok.classList.remove("hidden");
    renderEbayStep();
  } catch (e) {
    const el = $("publish-error");
    el.textContent = e.message;
    el.classList.remove("hidden");
  } finally {
    $("publish-spinner").classList.add("hidden");
    $("btn-publish").disabled = false;
  }
});

function hidePublishMsg() {
  $("publish-error").classList.add("hidden");
  $("publish-ok").classList.add("hidden");
}

/* ---------- v2: eBay connect banner ---------- */
(function () {
  const q = new URLSearchParams(window.location.search);
  const flag = q.get("ebay");
  if (!flag) return;
  const el = $("ebay-banner");
  if (flag === "connected") {
    el.textContent = "eBay is connected. You can now list straight from step 4.";
  } else {
    el.textContent = "eBay connection did not finish. Try Connect eBay again in this browser.";
    el.className = "banner";
    el.style.margin = "0.8rem 1rem 0 1rem";
  }
  el.classList.remove("hidden");
  window.history.replaceState({}, "", window.location.pathname);
})();
