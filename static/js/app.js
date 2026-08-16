let currentPinId = null;
let knownRetailerProfiles = [];
const AMAZON_DOMAINS = [
  "amazon.com", "amazon.co.uk", "amazon.ca", "amazon.de", "amazon.fr",
  "amazon.es", "amazon.it", "amazon.in", "amazon.co.jp", "amazon.com.au",
  "amazon.com.br", "amazon.com.mx", "amazon.nl", "amazon.se", "amazon.sg",
  "amazon.com.tr", "amazon.ae", "amazon.sa", "amazon.pl", "amazon.com.be",
];

function getDomain(url) {
  try {
    return new URL(url).hostname.toLowerCase().replace(/^www\./, "");
  } catch (e) {
    return null;
  }
}

function isAmazonDomain(domain) {
  return AMAZON_DOMAINS.some(d => domain === d || domain.endsWith("." + d));
}

function matchingRetailerProfile(domain) {
  const matches = knownRetailerProfiles.filter(p => domain.includes(p.domain_pattern));
  if (!matches.length) return null;
  return matches.reduce((a, b) => (b.domain_pattern.length > a.domain_pattern.length ? b : a));
}

function updateOverrideVisibility() {
  const url = document.getElementById("amazon-url").value.trim();
  const domain = getDomain(url);
  const row = document.getElementById("override-link-row");
  if (!domain || isAmazonDomain(domain)) {
    row.style.display = "none";
    return;
  }
  const profile = matchingRetailerProfile(domain);
  // Show the manual-paste field unless a profile exists AND has a template
  // configured -- a profile with no template still needs a link pasted in
  // every time, same as an unconfigured domain.
  row.style.display = (profile && profile.link_template) ? "none" : "block";
}

function showError(msg) {
  const box = document.getElementById("generate-error");
  box.textContent = msg;
  box.classList.add("show");
}
function clearError() {
  const box = document.getElementById("generate-error");
  box.classList.remove("show");
  box.textContent = "";
}

async function forgePin() {
  clearError();
  const url = document.getElementById("amazon-url").value.trim();
  if (!url) {
    showError("Paste a product URL first.");
    return;
  }
  const affiliateOverride = document.getElementById("affiliate-override").value.trim();
  const manualTitle = document.getElementById("manual-title").value.trim();
  const manualPrice = document.getElementById("manual-price").value.trim();
  const manualImage = document.getElementById("manual-image").value.trim();

  const btn = document.getElementById("forge-btn");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Forging…';

  try {
    const resp = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        amazon_url: url,
        affiliate_link_override: affiliateOverride || undefined,
        manual_title: manualTitle || undefined,
        manual_price: manualPrice || undefined,
        manual_image_url: manualImage || undefined,
      }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      showError(data.error || "Something went wrong generating that pin.");
      // Auto-open manual entry so the person doesn't have to hunt for it
      document.getElementById("manual-entry-details").open = true;
      return;
    }
    renderBench(data);
    // Clear manual fields on success so they don't linger for the next pin
    document.getElementById("manual-title").value = "";
    document.getElementById("manual-price").value = "";
    document.getElementById("manual-image").value = "";
    document.getElementById("manual-entry-details").open = false;
  } catch (e) {
    showError("Network error reaching PinForge. Is the server running?");
  } finally {
    btn.disabled = false;
    btn.textContent = "Forge Pin";
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("amazon-url").addEventListener("input", updateOverrideVisibility);
});


function renderBench(data) {
  currentPinId = data.pin_id;

  document.getElementById("bench-empty").style.display = "none";
  const content = document.getElementById("bench-content");
  content.style.display = "block";

  const tmpl = document.getElementById("bench-template");
  content.innerHTML = "";
  content.appendChild(tmpl.content.cloneNode(true));

  document.getElementById("proof-img").src = data.image_url;
  document.getElementById("price-tag").textContent = data.price ? `PRICE  ${data.price}` : "PRICE  —";
  document.getElementById("pin-title-input").value = data.pin_title;
  document.getElementById("pin-desc-input").value = data.pin_description;
  document.getElementById("desc-count").textContent = data.pin_description.length;
  document.getElementById("affiliate-link").textContent = data.affiliate_link;

  const sourceTag = document.getElementById("source-tag");
  const retailer = data.retailer_label || "Amazon";
  if (data.source === undefined) {
    // Reopened from the Ledger -- we don't know the original fetch source,
    // so don't claim one. Show what's actually known instead.
    sourceTag.textContent = data.status === "published"
      ? `PUBLISHED · ${retailer.toUpperCase()}${data.pinterest_pin_id ? " · PIN " + data.pinterest_pin_id : ""}`
      : `DRAFT · ${retailer.toUpperCase()}`;
  } else if (retailer === "Amazon") {
    if (data.source === "scrape") {
      sourceTag.textContent = "⚠ AMAZON FALLBACK PAGE READ — VERIFY PRICE";
      sourceTag.classList.add("scrape");
    } else if (data.source === "manual") {
      sourceTag.textContent = "SOURCE · MANUAL ENTRY";
    } else {
      sourceTag.textContent = "SOURCE · AMAZON CREATORS API";
    }
  } else {
    sourceTag.textContent = `SOURCE · ${retailer.toUpperCase()} (SCRAPED)`;
  }

  // If this pin already has a landing page, show it as created rather
  // than re-prompting to make a new one.
  if (data.wordpress_post_url) {
    document.getElementById("landing-page-empty").style.display = "none";
    const createdEl = document.getElementById("landing-page-created");
    createdEl.style.display = "block";
    document.getElementById("landing-page-url").innerHTML =
      `✓ <a href="${data.wordpress_post_url}" target="_blank" style="color:var(--sage);">${data.wordpress_post_url}</a>`;
  }

  // If this pin was already published, say so without blocking Publish --
  // republishing (e.g. after edits, or to a different board) is valid.
  if (data.status === "published") {
    document.getElementById("published-status").innerHTML =
      `<div class="published-badge">✓ ALREADY PUBLISHED${data.pinterest_pin_id ? " — PIN " + data.pinterest_pin_id : ""} — publishing again creates a new pin</div>`;
  }

  document.getElementById("pin-desc-input").addEventListener("input", (e) => {
    document.getElementById("desc-count").textContent = e.target.value.length;
  });

  // Persist edits on blur
  document.getElementById("pin-title-input").addEventListener("blur", saveEdits);
  document.getElementById("pin-desc-input").addEventListener("blur", saveEdits);
}

async function openPin(pinId) {
  clearError();
  try {
    const resp = await fetch(`/api/pins/${pinId}`);
    const data = await resp.json();
    if (!resp.ok) {
      showError(data.error || "Could not open that pin.");
      return;
    }
    renderBench(data);
    document.querySelector(".console").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    showError("Network error opening that pin.");
  }
}

async function saveEdits() {
  if (!currentPinId) return;
  const pin_title = document.getElementById("pin-title-input").value;
  const pin_description = document.getElementById("pin-desc-input").value;
  await fetch(`/api/pins/${currentPinId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pin_title, pin_description }),
  });
}

async function deletePin(pinId, isPublished) {
  const message = isPublished
    ? "This pin is published on Pinterest. Deleting it here only removes it from PinForge's records — it will NOT be taken down from Pinterest itself. Delete anyway?"
    : "Delete this draft pin? This can't be undone.";
  if (!confirm(message)) return;

  const resp = await fetch(`/api/pins/${pinId}`, { method: "DELETE" });
  if (resp.ok) {
    const row = document.getElementById(`ledger-row-${pinId}`);
    if (row) row.remove();
  } else {
    const data = await resp.json().catch(() => ({}));
    alert(data.error || "Could not delete that pin.");
  }
}

async function createLandingPage() {
  if (!currentPinId) return;
  await saveEdits();

  const btn = document.getElementById("landing-page-btn");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Publishing…';

  try {
    const resp = await fetch(`/api/pins/${currentPinId}/landing-page`, { method: "POST" });
    const data = await resp.json();
    if (!resp.ok) {
      showError(data.error || "Could not publish the product.");
      btn.disabled = false;
      btn.textContent = "Publish as product on reallygoodgadgets.store";
      return;
    }
    document.getElementById("landing-page-empty").style.display = "none";
    const createdEl = document.getElementById("landing-page-created");
    createdEl.style.display = "block";
    document.getElementById("landing-page-url").innerHTML =
      `✓ <a href="${data.wordpress_post_url}" target="_blank" style="color:var(--sage);">${data.wordpress_post_url}</a>`;
  } catch (e) {
    showError("Network error publishing the product.");
    btn.disabled = false;
    btn.textContent = "Publish as product on reallygoodgadgets.store";
  }
}

async function publishPin() {
  if (!currentPinId) return;
  await saveEdits();

  const manualBoardId = document.getElementById("board-id-override").value.trim();
  const boardId = manualBoardId || document.getElementById("board-select").value;
  if (!boardId) {
    showError("Pick a Pinterest board, or enter a board ID manually, before publishing.");
    return;
  }

  const btn = document.getElementById("publish-btn");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Publishing…';

  try {
    const resp = await fetch(`/api/pins/${currentPinId}/publish`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ board_id: boardId }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      showError(data.error || "Pinterest rejected that pin.");
      return;
    }
    const isSandbox = document.body.dataset.pinterestSandbox === "true";
    const sandboxNote = isSandbox
      ? " (Sandbox — visible only to you, not the public, until Standard access is approved)"
      : "";
    document.getElementById("published-status").innerHTML =
      `<div class="published-badge">✓ PUBLISHED — PIN ${data.pinterest_pin_id}${sandboxNote}</div>`;
    btn.textContent = "Published";
  } catch (e) {
    showError("Network error publishing to Pinterest.");
    btn.disabled = false;
    btn.textContent = "Publish";
  }
}

/* ---------------- Scout ---------------- */

async function loadScoutCategories() {
  const resp = await fetch("/api/scout/categories");
  const categories = await resp.json();
  const select = document.getElementById("watch-category");
  select.innerHTML = categories.map(c => `<option value="${c}">${c}</option>`).join("");
}

async function loadWatches() {
  const resp = await fetch("/api/scout/watches");
  const watches = await resp.json();
  const list = document.getElementById("watch-list");

  if (!watches.length) {
    list.innerHTML = '<p class="empty-ledger-small">No watches yet — add one below.</p>';
    return;
  }

  list.innerHTML = watches.map(w => {
    const filters = [];
    if (w.min_rating) filters.push(`★${w.min_rating}+`);
    if (w.min_reviews) filters.push(`${w.min_reviews}+ reviews`);
    if (w.min_price) filters.push(`$${w.min_price}+`);
    if (w.max_price) filters.push(`under $${w.max_price}`);
    const lastRun = w.last_run_at ? `last run ${w.last_run_at.slice(0, 10)}` : "never run";
    return `
      <div class="watch-chip">
        <div>
          <div>${w.label}</div>
          <div class="wc-meta">${w.search_index}${w.keywords ? " · " + w.keywords : ""}${filters.length ? " · " + filters.join(", ") : ""}</div>
          <div class="wc-last-run">${lastRun}</div>
        </div>
        <button class="wc-remove" onclick="removeWatch(${w.id})" title="Remove watch">✕</button>
      </div>`;
  }).join("");
}

async function addWatch() {
  const label = document.getElementById("watch-label").value.trim();
  const search_index = document.getElementById("watch-category").value;
  const keywords = document.getElementById("watch-keywords").value.trim();
  const min_rating = document.getElementById("watch-min-rating").value.trim();
  const min_reviews = document.getElementById("watch-min-reviews").value.trim();
  const min_price = document.getElementById("watch-min-price").value.trim();
  const max_price = document.getElementById("watch-max-price").value.trim();

  if (!label) {
    showError("Give this watch a label.");
    return;
  }

  await fetch("/api/scout/watches", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      label, search_index,
      keywords: keywords || null,
      min_rating: min_rating || null,
      min_reviews: min_reviews || null,
      min_price: min_price || null,
      max_price: max_price || null,
    }),
  });

  document.getElementById("watch-label").value = "";
  document.getElementById("watch-keywords").value = "";
  document.getElementById("watch-min-rating").value = "";
  document.getElementById("watch-min-reviews").value = "";
  document.getElementById("watch-min-price").value = "";
  document.getElementById("watch-max-price").value = "";

  loadWatches();
}

async function removeWatch(id) {
  await fetch(`/api/scout/watches/${id}`, { method: "DELETE" });
  loadWatches();
}

async function runScout() {
  const btn = document.getElementById("run-scout-btn");
  const resultEl = document.getElementById("scout-run-result");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Scouting…';
  resultEl.textContent = "";

  try {
    const resp = await fetch("/api/scout/run", { method: "POST" });
    const data = await resp.json();
    if (!resp.ok) {
      resultEl.textContent = data.error || "Scout run failed.";
      return;
    }
    const totals = data.results.reduce((acc, r) => {
      acc.kept += r.kept || 0;
      acc.found += r.found || 0;
      acc.similar += r.skipped_similar || 0;
      acc.duplicate += r.skipped_duplicate || 0;
      if (r.error) acc.errors.push(r.error);
      return acc;
    }, { kept: 0, found: 0, similar: 0, duplicate: 0, errors: [] });

    if (totals.errors.length) {
      resultEl.textContent = totals.errors[0];
    } else {
      const skippedBits = [];
      if (totals.duplicate) skippedBits.push(`${totals.duplicate} already seen`);
      if (totals.similar) skippedBits.push(`${totals.similar} similar to something you've decided on`);
      const skippedText = skippedBits.length ? ` (skipped ${skippedBits.join(", ")})` : "";
      resultEl.textContent = `Found ${totals.found}, added ${totals.kept} new candidates${skippedText}.`;
    }

    loadCandidates();
    loadWatches();
  } catch (e) {
    resultEl.textContent = "Network error running Scout.";
  } finally {
    btn.disabled = false;
    btn.textContent = "Run Scout Now";
  }
}

async function loadCandidates() {
  const resp = await fetch("/api/scout/candidates?status=pending");
  const candidates = await resp.json();
  const list = document.getElementById("candidate-list");

  if (!candidates.length) {
    list.innerHTML = '<p class="empty-ledger">No candidates yet — add a watch and run Scout.</p>';
    return;
  }

  const tmpl = document.getElementById("candidate-row-template");
  list.innerHTML = "";
  candidates.forEach(c => {
    const row = tmpl.content.cloneNode(true);
    row.querySelector(".candidate-thumb").src = c.image_url || "";
    row.querySelector(".candidate-title").textContent = c.title;
    const meta = [c.watch_label, c.price, c.rating ? `★${c.rating}` : null, c.review_count ? `${c.review_count} reviews` : null]
      .filter(Boolean).join(" · ");
    row.querySelector(".candidate-meta").textContent = meta;

    row.querySelector(".approve").addEventListener("click", async (e) => {
      e.target.disabled = true;
      e.target.textContent = "Forging…";
      const resp = await fetch(`/api/scout/candidates/${c.id}/approve`, { method: "POST" });
      const data = await resp.json();
      if (resp.ok) {
        renderBench(data);
        loadCandidates();
        window.scrollTo({ top: 0, behavior: "smooth" });
      } else {
        showError(data.error || "Could not forge that candidate.");
        e.target.disabled = false;
        e.target.textContent = "✓ Forge";
      }
    });

    row.querySelector(".reject").addEventListener("click", async () => {
      await fetch(`/api/scout/candidates/${c.id}/reject`, { method: "POST" });
      loadCandidates();
    });

    list.appendChild(row);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  loadScoutCategories();
  loadWatches();
  loadCandidates();
  loadRetailers();
  loadAnalytics();
});

/* ---------------- Analytics ---------------- */

async function loadAnalytics() {
  const resp = await fetch("/api/analytics");
  const data = await resp.json();

  document.getElementById("analytics-stats").innerHTML = `
    <div class="stat-card">
      <div class="stat-value">${data.total_clicks}</div>
      <div class="stat-label">Total clicks</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">${data.recent_clicks}</div>
      <div class="stat-label">Clicks — last ${data.recent_days} days</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">${data.published_pins}</div>
      <div class="stat-label">Published pins</div>
    </div>
  `;

  const topPinsEl = document.getElementById("analytics-top-pins");
  if (!data.top_pins.length) {
    topPinsEl.innerHTML = '<p class="empty-ledger-small">No clicks recorded yet.</p>';
  } else {
    topPinsEl.innerHTML = data.top_pins.map(p => `
      <div class="analytics-row">
        <img src="/${p.generated_image_path}" alt="">
        <div>
          <div class="ar-title">${p.pin_title}</div>
          <div class="ar-meta">${p.retailer_label || "Amazon"}${p.product_price ? " · " + p.product_price : ""}</div>
        </div>
        <div class="ar-count">${p.click_count}</div>
      </div>
    `).join("");
  }

  const byRetailerEl = document.getElementById("analytics-by-retailer");
  if (!data.by_retailer.length) {
    byRetailerEl.innerHTML = '<p class="empty-ledger-small">No clicks recorded yet.</p>';
  } else {
    byRetailerEl.innerHTML = data.by_retailer.map(r => `
      <div class="retailer-row">
        <span>${r.retailer_label || "Amazon"}</span>
        <span class="rr-count">${r.click_count}</span>
      </div>
    `).join("");
  }
}

/* ---------------- Retailer profiles ---------------- */

async function loadRetailers() {
  const resp = await fetch("/api/retailers");
  knownRetailerProfiles = await resp.json();
  const list = document.getElementById("retailer-list");

  if (!knownRetailerProfiles.length) {
    list.innerHTML = '<p class="empty-ledger-small">No retailer profiles yet — Amazon works without one. Add a profile below for any other retailer.</p>';
    return;
  }

  list.innerHTML = knownRetailerProfiles.map(p => `
    <div class="watch-chip">
      <div>
        <div>${p.label}</div>
        <div class="wc-meta">${p.domain_pattern}${p.link_template ? " · templated" : " · manual paste required"}</div>
      </div>
      <button class="wc-remove" onclick="removeRetailer(${p.id})" title="Remove profile">✕</button>
    </div>`).join("");

  updateOverrideVisibility();
}

async function addRetailer() {
  const label = document.getElementById("retailer-label").value.trim();
  const domain_pattern = document.getElementById("retailer-domain").value.trim().toLowerCase().replace(/^www\./, "");
  const link_template = document.getElementById("retailer-template").value.trim();

  if (!label || !domain_pattern) {
    showError("A label and domain are required for a retailer profile.");
    return;
  }

  await fetch("/api/retailers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label, domain_pattern, link_template: link_template || null }),
  });

  document.getElementById("retailer-label").value = "";
  document.getElementById("retailer-domain").value = "";
  document.getElementById("retailer-template").value = "";
  loadRetailers();
}

async function removeRetailer(id) {
  await fetch(`/api/retailers/${id}`, { method: "DELETE" });
  loadRetailers();
}
