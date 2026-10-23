/* Listing Assistant v2.8 - front end. Plain steps, no framework. */
let draft = null;
let selectedFiles = [];
let saveTimer = null;
let saveDirty = false; /* v5: true while edits are waiting for the debounced save */
let maxPhotos = 10; /* v8: overwritten by /api/config (eBay allows 24 free) */

const $ = (id) => document.getElementById(id);

function show(stepId) {
  /* v5: never switch sections with unsaved edits pending — flush first so
     leaving the draft (My drafts, back button, sold-price pop-up) cannot
     lose what he just typed. */
  flushSave();
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

/* v5: every chosen photo gets an X button so a wrong pick can be removed
   before identifying. Each entry carries its own preview URL so removal
   revokes exactly the right one and never disturbs the rest. */
let skippedCount = 0;

function clearPreviews() {
  selectedFiles.forEach((e) => { if (e.url) URL.revokeObjectURL(e.url); });
  selectedFiles = [];
  skippedCount = 0;
  $("thumbs").innerHTML = "";
  $("skip-note").classList.add("hidden");
  updateAnalyzeButton();
}

function updateAnalyzeButton() {
  $("btn-analyze").disabled = selectedFiles.length === 0;
}

function renderThumbs() {
  const box = $("thumbs");
  box.innerHTML = "";
  selectedFiles.forEach((entry, i) => {
    const wrap = document.createElement("div");
    wrap.className = "thumb-wrap";
    const img = document.createElement("img");
    img.src = entry.url;
    img.alt = "Chosen photo " + (i + 1);
    const x = document.createElement("button");
    x.type = "button";
    x.className = "thumb-x";
    x.textContent = "×";
    x.setAttribute("aria-label", "Remove this photo");
    x.addEventListener("click", () => removePhoto(i));
    wrap.appendChild(img);
    wrap.appendChild(x);
    box.appendChild(wrap);
  });
  for (let s = 0; s < skippedCount; s++) {
    const ph = document.createElement("div");
    ph.className = "thumb-skip";
    ph.textContent = "can't preview";
    box.appendChild(ph);
  }
  updateAnalyzeButton();
}

function removePhoto(i) {
  const entry = selectedFiles[i];
  if (!entry) return;
  if (entry.url) URL.revokeObjectURL(entry.url);
  selectedFiles.splice(i, 1);
  renderThumbs();
  hideError();
}

/* Shared photo intake: "Choose photos" replaces the set, "Take photo"
   appends one more (so he can snap several angles in a row). */
async function handlePickedFiles(fileList, replace) {
  const room = replace ? maxPhotos : Math.max(0, maxPhotos - selectedFiles.length);
  const picked = Array.from(fileList || []).slice(0, room);
  if (replace) clearPreviews();
  skippedCount = 0;
  for (let i = 0; i < picked.length; i++) {
    const r = await downscaleFile(picked[i], selectedFiles.length + i);
    if (r.skipped) { skippedCount++; continue; }
    const u = URL.createObjectURL(r.blob);
    selectedFiles.push({ blob: r.blob, name: r.name, url: u });
  }
  renderThumbs();
  if (skippedCount > 0) {
    const n = $("skip-note");
    n.textContent = skippedCount + (skippedCount === 1 ? " photo" : " photos") +
      " couldn't be read and " + (skippedCount === 1 ? "was" : "were") + " skipped.";
    n.classList.remove("hidden");
  }
  hideError();
}

$("photo-input").addEventListener("change", (e) => { handlePickedFiles(e.target.files, true); });
$("camera-input").addEventListener("change", (e) => {
  handlePickedFiles(e.target.files, false);
  e.target.value = ""; /* reset so another snap fires change again */
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
  saveDirty = false; /* v5: a fresh load is the clean baseline */
  clearTimeout(saveTimer);
  saveTimer = null;
  $("f-title").value = d.title || "";
  $("f-category").value = d.category || "";
  $("f-description").value = d.description || "";
  $("f-condition").value = d.condition || "";
  $("f-condition-details").value = d.condition_details || "";
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
    condition: $("f-condition").value,
    condition_details: $("f-condition-details").value.trim(),
    price: $("f-price").value.trim(),
    attributes: attrs,
    /* v9: best offer lives in step 4 (p- ids) but joins the same autosave —
       the elements always exist in the DOM, visible or not. */
    best_offer: $("p-best-offer").checked,
    best_offer_auto_decline: $("p-bo-decline").value.trim(),
    best_offer_auto_accept: $("p-bo-accept").value.trim(),
  };
}

/* v6: plain-words label for a condition value (reads the step-2 dropdown). */
function conditionLabel(value) {
  if (!value) return "";
  const o = $("f-condition").querySelector('option[value="' + value + '"]');
  return o ? o.textContent : value;
}

function scheduleSave() {
  saveDirty = true;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveDraft, 1200);
}

async function saveDraft() {
  if (!draft) return;
  clearTimeout(saveTimer);
  saveTimer = null;
  try {
    const { draft: d } = await api("/api/drafts/" + draft.id, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectForm()),
    });
    draft = d;
    saveDirty = false;
  } catch (e) { /* autosave is best-effort; the Review step saves too */ }
}

/* v5: last-chance save when the page is hidden or closed (phone back button,
   app switch, screen off). Uses keepalive so the request survives the page
   going away. Fire-and-forget: nothing to await here. */
function flushSave() {
  if (!saveDirty || !draft) {
    clearTimeout(saveTimer);
    saveTimer = null;
    saveDirty = false;
    return;
  }
  clearTimeout(saveTimer);
  saveTimer = null;
  saveDirty = false;
  try {
    fetch("/api/drafts/" + draft.id, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectForm()),
      keepalive: true,
    }).catch(() => {});
  } catch (e) { /* best-effort */ }
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") flushSave();
});
window.addEventListener("pagehide", flushSave);

/* v5: hooks the native app uses (sold-price pop-up "Use this price"). */
window.LA_getDraftId = () => (draft ? draft.id : "");
window.LA_refreshDraft = async () => {
  if (!draft) return;
  try {
    const { draft: d } = await api("/api/drafts/" + draft.id);
    loadDraftIntoForm(d);
  } catch (e) { /* refresh is best-effort */ }
};

["f-title", "f-category", "f-description", "f-price"].forEach((id) => {
  $(id).addEventListener("input", () => { updateCount(); scheduleSave(); });
});
/* v6: condition fields join the autosave. The select fires "change"
   reliably; "input" covers the textarea. Double-firing is harmless
   (scheduleSave is debounced). */
["f-condition", "f-condition-details"].forEach((id) => {
  $(id).addEventListener("input", scheduleSave);
  $(id).addEventListener("change", scheduleSave);
});

function updateCount() {
  $("title-count").textContent = $("f-title").value.length + "/80";
}

/* v5: save first — the pop-up opens over the draft, and "Use this price"
   later refreshes the form from the server, so the server must be current. */
$("btn-solds").addEventListener("click", async () => {
  await saveDraft();
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
    "<p><b>Condition:</b> " + esc(conditionLabel(d.condition) || "(not set)") + "</p>" +
    (d.condition_details ? "<p><b>Condition details:</b> " + esc(d.condition_details).replace(/\n/g, "<br>") + "</p>" : "") +
    /* v9: show best offer status in the review so it's confirmed before step 4. */
    (d.best_offer ? "<p><b>Best Offer:</b> on" +
      (d.best_offer_auto_decline ? " (auto-decline under $" + esc(d.best_offer_auto_decline) + ")" : "") +
      (d.best_offer_auto_accept ? " (auto-accept $" + esc(d.best_offer_auto_accept) + "+)" : "") +
      "</p>" : "") +
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
  const cond = conditionLabel(d.condition);
  if (cond) t += "\n\nCondition: " + cond;
  if (d.condition_details) t += "\nCondition details: " + d.condition_details;
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
  flushSave(); /* v5: save any last-second edits before abandoning the form */
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
/* v5: Back from My drafts goes to the very beginning (choose photos).
   The "My drafts" button is always in the header, so this is the only way
   back to the start once you are inside a draft. Pending edits are flushed
   by show() before the switch. */
$("btn-close-drafts").addEventListener("click", () => show("step-photos"));

/* ---------- init ---------- */
api("/api/config").then((c) => {
  if (!c.gemini_configured) $("key-warning").classList.remove("hidden");
  if (c.max_photos) {
    maxPhotos = c.max_photos;
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
  const pick = document.createElement("option");
  pick.value = ""; pick.textContent = "— Pick one —";
  sel.appendChild(pick);
  (ebayStatus.conditions || []).forEach((c) => {
    const o = document.createElement("option");
    o.value = c.value; o.textContent = c.label;
    sel.appendChild(o);
  });
  // v6: carry the draft's condition over; gentle reminder (not a block) if unset
  const cur = collectForm();
  sel.value = cur.condition || "";
  $("condition-reminder").classList.toggle("hidden", !!cur.condition);
  $("p-price").value = $("f-price").value || "";
  /* v9: best offer settings carry over from the draft. */
  $("p-best-offer").checked = !!draft.best_offer;
  $("p-bo-decline").value = draft.best_offer_auto_decline || "";
  $("p-bo-accept").value = draft.best_offer_auto_accept || "";
  $("best-offer-fields").classList.toggle("hidden", !$("p-best-offer").checked);
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
        loadCategoryAspects(); /* v8: fetch what eBay requires for this category */
      });
      box.appendChild(b);
    });
  } catch (e) {
    box.innerHTML = "<p class='error'>" + esc(e.message) + "</p>";
  }
});

/* v8: also check when a category number is typed in by hand. */
$("p-category").addEventListener("change", loadCategoryAspects);

/* v9: best offer toggle shows/hides the auto-price fields; all three join
   the draft autosave (checkbox fires "change", prices fire "input"). */
$("p-best-offer").addEventListener("change", () => {
  $("best-offer-fields").classList.toggle("hidden", !$("p-best-offer").checked);
  scheduleSave();
});
["p-bo-decline", "p-bo-accept"].forEach((id) => {
  $(id).addEventListener("input", scheduleSave);
});

/* v9: client-side mirror of the server's best-offer rules. Returns a
   plain-words error or "". The server validates again at publish. */
function parseMoney(s) {
  s = String(s || "").trim().replace(/[$,]/g, "");
  if (!s) return null;
  const v = Math.round(parseFloat(s) * 100) / 100;
  return (isNaN(v) || v <= 0) ? null : v;
}
function checkBestOffer(on, declineRaw, acceptRaw, price) {
  if (!on) return "";
  const decline = parseMoney(declineRaw);
  const accept = parseMoney(acceptRaw);
  if (declineRaw.trim() && decline === null)
    return "The auto-decline price isn't a valid amount. Clear it or type a number like 25.00.";
  if (acceptRaw.trim() && accept === null)
    return "The auto-accept price isn't a valid amount. Clear it or type a number like 40.00.";
  if (decline !== null && decline >= price)
    return "The auto-decline price has to be below your listing price.";
  if (accept !== null && accept >= price)
    return "The auto-accept price has to be below your listing price.";
  if (decline !== null && accept !== null && accept <= decline)
    return "The auto-accept price has to be higher than the auto-decline price.";
  return "";
}

/* ---------- v8: required item specifics ---------- */
let categoryAspects = { required: [], recommended: [] };

function draftAttrValue(name) {
  /* Case-insensitive lookup in the draft's AI-guessed attributes so eBay's
     exact aspect names get pre-filled where they match. */
  const attrs = ((draft && draft.attributes) || {});
  const lower = String(name).toLowerCase();
  for (const k of Object.keys(attrs)) {
    if (String(k).toLowerCase() === lower) return attrs[k];
  }
  return "";
}

function aspectInput(name, values, required, prefill) {
  const wrap = document.createElement("div");
  wrap.className = "req-aspect";
  const label = document.createElement("label");
  label.textContent = name + (required ? " *" : "");
  const input = document.createElement("input");
  input.type = "text";
  input.dataset.aspectName = name;
  input.value = prefill || "";
  input.placeholder = required ? "Required by eBay" : "Optional";
  if (values && values.length) {
    const dl = document.createElement("datalist");
    dl.id = "dl-" + Math.random().toString(36).slice(2);
    values.slice(0, 30).forEach((v) => {
      const o = document.createElement("option");
      o.value = v;
      dl.appendChild(o);
    });
    wrap.appendChild(dl);
    input.setAttribute("list", dl.id);
  }
  wrap.appendChild(label);
  wrap.appendChild(input);
  return wrap;
}

async function loadCategoryAspects() {
  const box = $("required-aspects");
  const cid = $("p-category").value.trim();
  box.innerHTML = "";
  categoryAspects = { required: [], recommended: [] };
  if (!cid) return;
  box.innerHTML = "<p class='hint'>Checking what details eBay wants for this category…</p>";
  let data;
  try {
    data = await api("/api/ebay/category-aspects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category_id: cid }),
    });
  } catch (e) {
    data = null;
  }
  if (!data || data.unavailable) {
    box.innerHTML = "<p class='hint'>Could not check eBay's required details — listing with what you have.</p>";
    return;
  }
  categoryAspects = data;
  box.innerHTML = "";
  const req = data.required || [];
  const rec = (data.recommended || []).slice(0, 8);
  if (!req.length && !rec.length) {
    box.innerHTML = "<p class='hint'>eBay has no special required details for this category.</p>";
    return;
  }
  if (req.length) {
    const h = document.createElement("h4");
    h.textContent = "Details eBay requires for this category";
    box.appendChild(h);
    req.forEach((a) => box.appendChild(aspectInput(a.name, a.values, true, draftAttrValue(a.name))));
  }
  if (rec.length) {
    const det = document.createElement("details");
    const sum = document.createElement("summary");
    sum.textContent = "More details eBay suggests (optional)";
    det.appendChild(sum);
    rec.forEach((a) => det.appendChild(aspectInput(a.name, a.values, false, draftAttrValue(a.name))));
    box.appendChild(det);
  }
}

function collectAspects() {
  /* Values from the step-4 aspect fields, keyed by eBay's exact aspect name. */
  const out = {};
  document.querySelectorAll("#required-aspects input[data-aspect-name]").forEach((i) => {
    const v = i.value.trim();
    if (v) out[i.dataset.aspectName] = v;
  });
  return out;
}

function validateAspects() {
  const missing = [];
  const vals = collectAspects();
  (categoryAspects.required || []).forEach((a) => {
    const v = (vals[a.name] || "").trim();
    if (!v || v.toLowerCase() === "unknown") missing.push(a.name);
  });
  return missing;
}

$("btn-publish").addEventListener("click", async () => {
  hidePublishMsg();
  if (!draft) return;
  /* v8: eBay's required item specifics must be filled before listing. */
  const missing = validateAspects();
  if (missing.length) {
    const el = $("publish-error");
    el.textContent = "eBay requires " + missing.join(", ") +
      " for this category. Fill them in under 'Details eBay requires' above, then try again.";
    el.classList.remove("hidden");
    return;
  }
  /* v9: best offer sanity checks, plain words, before the confirm dialog. */
  const boErr = checkBestOffer($("p-best-offer").checked,
                               $("p-bo-decline").value,
                               $("p-bo-accept").value,
                               parseFloat($("p-price").value));
  if (boErr) {
    const el = $("publish-error");
    el.textContent = boErr;
    el.classList.remove("hidden");
    return;
  }
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
      condition_details: $("f-condition-details").value.trim(),
      category_id: $("p-category").value.trim(),
      fulfillment_policy_id: $("p-fulfillment").value.trim(),
      payment_policy_id: $("p-payment").value.trim(),
      return_policy_id: $("p-return").value.trim(),
      /* v8: step-4 aspect fields overlay the draft's attributes server-side. */
      attributes: Object.assign({}, draft.attributes || {}, collectAspects()),
      /* v9: best offer settings. */
      best_offer: $("p-best-offer").checked,
      best_offer_auto_decline: $("p-bo-decline").value.trim(),
      best_offer_auto_accept: $("p-bo-accept").value.trim(),
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
