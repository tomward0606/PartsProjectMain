function addToBasket(partNumber) {
  fetch(`/add_to_basket/${encodeURIComponent(partNumber)}`, {
    method: 'GET',
    headers: { 'X-Requested-With': 'XMLHttpRequest' }
  }).then(response => {
    if (response.status === 204) {
      showToast();
    }
  });
}

function showToast() {
  const toast = document.getElementById('toast');
  toast.style.display = 'block';
  setTimeout(hideToast, 2500);
}

function hideToast() {
  const toast = document.getElementById('toast');
  toast.style.display = 'none';
}

(function () {
  function escapeHtml(str) {
    return (str || "").replace(/[&<>"']/g, function (m) {
      return ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#039;"
      })[m];
    });
  }

  async function fetchPartsIntoTable(opts) {
    const {
      searchInput,
      categorySelect,
      tbody,
      addUrlTemplate,
      submitted
    } = opts;

    const q = (searchInput && searchInput.value ? searchInput.value : "").trim();
    const category = (categorySelect && categorySelect.value ? categorySelect.value : "").trim();

    const params = new URLSearchParams();
    params.set("q", q);
    params.set("category", category);

    const res = await fetch(`/stocktake/parts_search?${params.toString()}`, {
      headers: { "X-Requested-With": "XMLHttpRequest" }
    });

    if (!res.ok) return;
    const data = await res.json();
    if (!data || !data.ok) return;

    const parts = data.parts || [];
    if (!tbody) return;

    if (parts.length === 0) {
      tbody.innerHTML = `<tr><td colspan="3" class="text-muted py-4 text-center">No results.</td></tr>`;
      return;
    }

    tbody.innerHTML = parts.map(function (p) {
      const pn = p.part_number || "";
      const desc = p.description || "";
      const addUrl = addUrlTemplate.replace("__PN__", encodeURIComponent(pn));

      const action = submitted
        ? `<button class="btn btn-sm btn-secondary" disabled>Locked</button>`
        : `<a class="btn btn-sm btn-dark" href="${addUrl}">Add</a>`;

      return `
        <tr>
          <td><strong>${escapeHtml(pn)}</strong></td>
          <td class="text-muted">${escapeHtml(desc)}</td>
          <td class="text-end">${action}</td>
        </tr>
      `;
    }).join("");
  }

  function hookLiveSearch(config) {
    const {
      formId,
      searchId,
      categoryId,
      tbodyId,
      buttonId,
      addUrlTemplate,
      submitted
    } = config;

    const form = document.getElementById(formId);
    const searchInput = document.getElementById(searchId);
    const categorySelect = document.getElementById(categoryId);
    const tbody = document.getElementById(tbodyId);
    const btn = document.getElementById(buttonId);

    // Only attach if page contains the elements
    if (!searchInput || !categorySelect || !tbody) return;

    if (btn) btn.addEventListener("click", function () {
      fetchPartsIntoTable({ searchInput, categorySelect, tbody, addUrlTemplate, submitted });
    });

    if (form) {
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        fetchPartsIntoTable({ searchInput, categorySelect, tbody, addUrlTemplate, submitted });
      });
    }

    // Initial load to match current state
    fetchPartsIntoTable({ searchInput, categorySelect, tbody, addUrlTemplate, submitted });
  }

  // Engineer stocktake page
  (function initEngineerLiveSearch() {
    const form = document.getElementById("stocktakeFilterForm");
    if (!form) return;

    const addUrlTemplate = form.getAttribute("data-add-url-template") || "";
    const submitted = form.getAttribute("data-submitted") === "1";

    hookLiveSearch({
      formId: "stocktakeFilterForm",
      searchId: "partsSearch",
      categoryId: "categorySelect",
      tbodyId: "partsTbody",
      buttonId: "searchBtn",
      addUrlTemplate,
      submitted
    });
  })();

  // Leader edit page
  (function initLeaderLiveSearch() {
    const form = document.getElementById("leaderFilterForm");
    if (!form) return;

    const addUrlTemplate = form.getAttribute("data-add-url-template") || "";

    hookLiveSearch({
      formId: "leaderFilterForm",
      searchId: "leaderPartsSearch",
      categoryId: "leaderCategorySelect",
      tbodyId: "leaderPartsTbody",
      buttonId: "leaderSearchBtn",
      addUrlTemplate,
      submitted: false
    });
  })();
})();



// ------------------------------
// Qty +/- + autosave (Engineer + Leader)
// ------------------------------
(function () {
  // Only run on pages that have qty inputs
  const qtyInputs = document.querySelectorAll(".qty-input");
  if (!qtyInputs.length) return;

  function toInt(v) {
    const n = parseInt(String(v || "0"), 10);
    return Number.isFinite(n) ? n : 0;
  }

  function setStatus(statusId, text, kind) {
    if (!statusId) return;
    const el = document.getElementById(statusId);
    if (!el) return;

    el.classList.remove("text-muted", "text-success", "text-danger");
    if (kind === "ok") el.classList.add("text-success");
    else if (kind === "bad") el.classList.add("text-danger");
    else el.classList.add("text-muted");

    el.textContent = text || "";
  }

  function updateClientTotals() {
    // Sum all visible qty inputs on the page
    const inputs = document.querySelectorAll(".qty-input");
    let totalQty = 0;
    inputs.forEach(i => { totalQty += toInt(i.value); });

    // Optional UI elements (update if they exist)
    const totalEl = document.getElementById("totalQtyValue");
    if (totalEl) totalEl.textContent = String(totalQty);

    const badge = document.getElementById("itemsCountBadge");
    if (badge) badge.textContent = `${inputs.length} items`;
  }

  async function saveQty(inputEl) {
    const url = inputEl.dataset.url;
    const statusId = inputEl.dataset.statusId;
    const rowId = inputEl.dataset.rowId;
    const cardId = inputEl.dataset.cardId;

    if (!url) return;

    const raw = inputEl.value;
    if (raw === "") {
      setStatus(statusId, "Enter a quantity…", "muted");
      return;
    }

    setStatus(statusId, "Saving…", "muted");

    try {
      const body = new URLSearchParams();
      body.set("quantity", raw);

      const res = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "X-Requested-With": "XMLHttpRequest"
        },
        body
      });

      if (!res.ok) throw new Error("Request failed");
      const data = await res.json();

      if (!data.ok) {
        setStatus(statusId, data.error || "Couldn’t save.", "bad");
        return;
      }

      // If backend removes item at qty=0, remove row/card
      if (data.removed) {
        if (rowId) {
          const row = document.getElementById(rowId);
          if (row) row.remove();
        }
        if (cardId) {
          const card = document.getElementById(cardId);
          if (card) card.remove();
        }
      } else if (typeof data.quantity !== "undefined") {
        inputEl.value = data.quantity;
      }

      // Update badge from server if provided
      const badge = document.getElementById("itemsCountBadge");
      if (badge && typeof data.items_count !== "undefined") {
        badge.textContent = `${data.items_count} items`;
      }

      // Always update totals client-side (instant feedback)
      updateClientTotals();

      setStatus(statusId, "Saved ✔", "ok");
      setTimeout(() => setStatus(statusId, "", "muted"), 1200);

    } catch (e) {
      setStatus(statusId, "Save failed. Try again.", "bad");
    }
  }

  // Debounce saves so typing doesn't spam
  const timers = new WeakMap();
  function scheduleSave(inputEl) {
    clearTimeout(timers.get(inputEl));
    const t = setTimeout(() => saveQty(inputEl), 250);
    timers.set(inputEl, t);
  }

  // Wire up every qty input + its +/- buttons
  qtyInputs.forEach((inputEl) => {
    // If someone types, save
    inputEl.addEventListener("input", () => {
      updateClientTotals();
      scheduleSave(inputEl);
    });
    inputEl.addEventListener("change", () => {
      updateClientTotals();
      scheduleSave(inputEl);
    });

    // Hook +/- buttons in the same input-group
    const group = inputEl.closest(".input-group");
    if (group) {
      const minus = group.querySelector(".qty-minus");
      const plus = group.querySelector(".qty-plus");

      if (minus) {
        minus.addEventListener("click", () => {
          const next = Math.max(0, toInt(inputEl.value) - 1);
          inputEl.value = String(next);
          updateClientTotals();
          scheduleSave(inputEl);
        });
      }

      if (plus) {
        plus.addEventListener("click", () => {
          const next = toInt(inputEl.value) + 1;
          inputEl.value = String(next);
          updateClientTotals();
          scheduleSave(inputEl);
        });
      }
    }
  });

  // Initial totals on load
  updateClientTotals();
})();


/* =========================
   Stocktake: mobile tabs + remember filters + catalogue qty controls
   (safe to paste at the bottom of script.js)
   ========================= */
(function () {
  if (window.__stocktakeMobileCatalogueInit) return;
  window.__stocktakeMobileCatalogueInit = true;

  const body = document.body;
  const engineer = body?.dataset?.engineer || "";
  const submitted = (body?.dataset?.submitted || "0") === "1";
  const setUrlTemplate = body?.dataset?.setUrlTemplate || ""; // .../__PN__

  // ----------------------------
  // Mobile tabs (Catalogue/Basket)
  // ----------------------------
  const tabCat = document.getElementById("tabCatalogue");
  const tabBas = document.getElementById("tabBasket");
  const paneCat = document.getElementById("paneCatalogue");
  const paneBas = document.getElementById("paneBasket");

  function setActiveTab(which) {
    if (!tabCat || !tabBas || !paneCat || !paneBas) return;
    const isCat = which === "catalogue";

    paneCat.classList.toggle("active", isCat);
    paneBas.classList.toggle("active", !isCat);
    tabCat.classList.toggle("active", isCat);
    tabBas.classList.toggle("active", !isCat);

    try { localStorage.setItem("stocktake_last_tab", which); } catch (e) {}
  }

  if (tabCat && tabBas && paneCat && paneBas) {
    tabCat.addEventListener("click", () => setActiveTab("catalogue"));
    tabBas.addEventListener("click", () => setActiveTab("basket"));

    const last = (() => {
      try { return localStorage.getItem("stocktake_last_tab"); } catch (e) { return null; }
    })();
    setActiveTab(last || "catalogue");
  }

  // ----------------------------
  // Remember filter inputs (search + category)
  // So rotate/reload doesn't feel like a reset
  // ----------------------------
  function safeJSONParse(s) {
    try { return JSON.parse(s); } catch (e) { return null; }
  }

  const filterKey = "stocktake_filters_" + engineer;
  const savedFilters = safeJSONParse((() => {
    try { return localStorage.getItem(filterKey); } catch (e) { return null; }
  })());

  const partsSearch = document.getElementById("partsSearch");
  const categorySelect = document.getElementById("categorySelect");
  const partsSearchM = document.getElementById("partsSearchM");
  const categorySelectM = document.getElementById("categorySelectM");

  // Only auto-fill if the field is currently empty
  if (savedFilters) {
    if (partsSearch && !partsSearch.value) partsSearch.value = savedFilters.search || "";
    if (categorySelect && !categorySelect.value) categorySelect.value = savedFilters.category || "";
    if (partsSearchM && !partsSearchM.value) partsSearchM.value = savedFilters.search || "";
    if (categorySelectM && !categorySelectM.value) categorySelectM.value = savedFilters.category || "";
  }

  function persistFilters() {
    const search = (partsSearch && partsSearch.value) || (partsSearchM && partsSearchM.value) || "";
    const category = (categorySelect && categorySelect.value) || (categorySelectM && categorySelectM.value) || "";
    try { localStorage.setItem(filterKey, JSON.stringify({ search, category })); } catch (e) {}
  }

  [partsSearch, categorySelect, partsSearchM, categorySelectM].forEach(el => {
    if (!el) return;
    el.addEventListener("input", persistFilters);
    el.addEventListener("change", persistFilters);
  });

  // ----------------------------
  // Helpers for updating badges + basket UI
  // ----------------------------
  function setBadges(itemsCount) {
    const b1 = document.getElementById("itemsCountBadge");
    const b2 = document.getElementById("itemsCountBadgeM");
    const b3 = document.getElementById("itemsCountBadgeM2");
    if (b1) b1.textContent = `${itemsCount} items`;
    if (b2) b2.textContent = `${itemsCount}`;
    if (b3) b3.textContent = `${itemsCount} items`;
  }

  function upsertBasketRow(partNumber, desc, qty) {
    // Desktop table body
    const tbody = document.getElementById("basketBody");
    if (!tbody) return;

    const id = "row-" + partNumber.replaceAll(" ", "_");
    let row = document.getElementById(id);

    if (qty <= 0) {
      if (row) row.remove();
      return;
    }

    if (!row) {
      row = document.createElement("tr");
      row.id = id;
      row.setAttribute("data-part", partNumber);
      row.setAttribute("data-desc", desc || "");
      row.innerHTML = `
        <td>
          <div class="mono"><strong>${partNumber}</strong></div>
          <div class="small text-muted">${desc || ""}</div>
          <div class="small text-muted qty-status" id="status-${partNumber.replaceAll(" ", "_")}"></div>
        </td>
        <td>
          <div class="input-group input-group-sm">
            <button class="btn btn-outline-secondary qty-btn qty-minus" type="button">−</button>
            <input type="number" min="0" value="${qty}"
                   class="form-control form-control-sm qty-input text-center"
                   data-status-id="status-${partNumber.replaceAll(" ", "_")}"
                   data-row-id="${id}"
                   data-url="/stocktake/${encodeURIComponent(engineer)}/update/${encodeURIComponent(partNumber)}">
            <button class="btn btn-outline-secondary qty-btn qty-plus" type="button">+</button>
          </div>
        </td>
        <td class="text-end">
          <a class="btn btn-sm btn-outline-danger"
             href="/stocktake/${encodeURIComponent(engineer)}/remove/${encodeURIComponent(partNumber)}">
            Remove
          </a>
        </td>
      `;
      tbody.prepend(row);
    } else {
      const input = row.querySelector(".qty-input");
      if (input) input.value = String(qty);
    }
  }

  function upsertBasketCard(partNumber, desc, qty) {
    const cards = document.getElementById("basketCards");
    if (!cards) return;

    const empty = document.getElementById("basketEmpty");
    if (empty && qty > 0) empty.remove();

    const id = "card-" + partNumber.replaceAll(" ", "_");
    let card = document.getElementById(id);

    if (qty <= 0) {
      if (card) card.remove();
      return;
    }

    if (!card) {
      card = document.createElement("div");
      card.className = "bg-white border rounded-4 p-3 mb-2";
      card.id = id;
      card.setAttribute("data-part", partNumber);
      card.setAttribute("data-desc", desc || "");
      card.innerHTML = `
        <div class="d-flex justify-content-between align-items-start gap-2">
          <div>
            <div class="mono fw-semibold">${partNumber}</div>
            <div class="small text-muted">${desc || ""}</div>
            <div class="small text-muted qty-status" id="mstatus-${partNumber.replaceAll(" ", "_")}"></div>
          </div>
          <a class="btn btn-sm btn-outline-danger"
             href="/stocktake/${encodeURIComponent(engineer)}/remove/${encodeURIComponent(partNumber)}">
            Remove
          </a>
        </div>

        <div class="mt-3">
          <label class="form-label small mb-1">Quantity</label>
          <div class="input-group input-group-sm">
            <button class="btn btn-outline-secondary qty-btn qty-minus" type="button">−</button>
            <input type="number" min="0" value="${qty}"
                   class="form-control form-control-sm qty-input text-center"
                   data-status-id="mstatus-${partNumber.replaceAll(" ", "_")}"
                   data-card-id="${id}"
                   data-url="/stocktake/${encodeURIComponent(engineer)}/update/${encodeURIComponent(partNumber)}">
            <button class="btn btn-outline-secondary qty-btn qty-plus" type="button">+</button>
          </div>
        </div>
      `;
      cards.prepend(card);
    } else {
      const input = card.querySelector(".qty-input");
      if (input) input.value = String(qty);
    }
  }

  // ----------------------------
  // Catalogue +/- controls (AJAX set qty)
  // ----------------------------
  if (!submitted && engineer && setUrlTemplate) {
    async function setQty(partNumber, qty, rowEl) {
      const status = rowEl.querySelector(".cat-status");
      if (status) status.textContent = "Saving…";

      const url = setUrlTemplate.replace("__PN__", encodeURIComponent(partNumber));
      const body = new URLSearchParams();
      body.set("quantity", String(qty));

      try {
        const res = await fetch(url, {
          method: "POST",
          headers: {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest"
          },
          body
        });

        if (!res.ok) throw new Error("bad response");
        const data = await res.json();

        if (!data.ok) {
          if (status) status.textContent = data.error || "Save failed";
          return;
        }

        // Confirm qty from server
        const input = rowEl.querySelector(".cat-qty");
        if (input) input.value = String(data.quantity);

        // Update badges
        if (typeof data.items_count !== "undefined") setBadges(data.items_count);

        // Update basket UI (so it feels instant)
        const desc = rowEl.getAttribute("data-desc") || "";
        upsertBasketRow(partNumber, desc, data.quantity);
        upsertBasketCard(partNumber, desc, data.quantity);

        if (status) {
          status.textContent = "Saved ✔";
          setTimeout(() => { status.textContent = ""; }, 700);
        }
      } catch (e) {
        if (status) status.textContent = "Save failed";
      }
    }

    function attachCatalogueHandlers() {
      document.querySelectorAll("tr[data-part]").forEach(row => {
        // Prevent double-binding if script runs twice
        if (row.dataset.bound === "1") return;
        row.dataset.bound = "1";

        const partNumber = row.getAttribute("data-part");
        const input = row.querySelector(".cat-qty");
        const plus = row.querySelector(".cat-plus");
        const minus = row.querySelector(".cat-minus");
        if (!partNumber || !input || !plus || !minus) return;

        plus.addEventListener("click", () => {
          const next = (parseInt(input.value || "0", 10) || 0) + 1;
          input.value = String(next);
          setQty(partNumber, next, row);
        });

        minus.addEventListener("click", () => {
          const next = Math.max(0, (parseInt(input.value || "0", 10) || 0) - 1);
          input.value = String(next);
          setQty(partNumber, next, row);
        });

        input.addEventListener("change", () => {
          const next = Math.max(0, (parseInt(input.value || "0", 10) || 0));
          input.value = String(next);
          setQty(partNumber, next, row);
        });
      });
    }

    attachCatalogueHandlers();
  }

})();



/* =========================
   Stocktake cards UI (no table, phone-first)
   Paste at bottom of static/script.js
   ========================= */
(function () {
  const body = document.body;
  if (!body) return;

  const engineer = body.dataset.engineer || "";
  const submitted = (body.dataset.submitted || "0") === "1";
  const setUrlTemplate = body.dataset.setUrlTemplate || ""; // contains __PN__
  if (!engineer || !setUrlTemplate) return;
  if (submitted) return; // locked

  // --- remember filters so rotate/reload doesn't feel like you "lost everything"
  const searchEl = document.getElementById("stSearch");
  const catEl = document.getElementById("stCategory");
  const filterKey = "st_filters_" + engineer;

  function loadFilters() {
    try {
      const raw = localStorage.getItem(filterKey);
      if (!raw) return;
      const saved = JSON.parse(raw);
      if (searchEl && !searchEl.value) searchEl.value = saved.search || "";
      if (catEl && !catEl.value) catEl.value = saved.category || "";
    } catch (e) {}
  }

  function saveFilters() {
    try {
      const payload = {
        search: searchEl ? searchEl.value : "",
        category: catEl ? catEl.value : ""
      };
      localStorage.setItem(filterKey, JSON.stringify(payload));
    } catch (e) {}
  }

  loadFilters();
  if (searchEl) searchEl.addEventListener("input", saveFilters);
  if (catEl) catEl.addEventListener("change", saveFilters);

  // --- toast helpers
  const toast = document.getElementById("stToast");
  const toastMsg = document.getElementById("stToastMsg");
  const toastClose = document.getElementById("stToastClose");
  let toastTimer = null;

  function showToast(msg) {
    if (!toast || !toastMsg) return;
    toastMsg.textContent = msg || "Saved";
    toast.style.display = "block";
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toast.style.display = "none"), 1200);
  }

  if (toastClose && toast) {
    toastClose.addEventListener("click", () => (toast.style.display = "none"));
  }

  // --- network call
  async function setQty(partNumber, qty) {
    const url = setUrlTemplate.replace("__PN__", encodeURIComponent(partNumber));
    const body = new URLSearchParams();
    body.set("quantity", String(qty));

    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest"
      },
      body
    });

    if (!res.ok) throw new Error("Bad response");
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Save failed");
    return data; // { quantity, items_count, ... }
  }

  // --- bind all cards
  const cards = document.querySelectorAll(".stocktake-part-card");
  cards.forEach((card) => {
    const part = card.getAttribute("data-part");
    if (!part) return;

    const minusBtn = card.querySelector(".st-minus");
    const plusBtn = card.querySelector(".st-plus");
    const qtyInput = card.querySelector(".st-qty");
    const status = card.querySelector(".st-status");
    const badge = card.querySelector(".st-qty-badge");

    if (!minusBtn || !plusBtn || !qtyInput) return;

    let busy = false;

    async function applyQty(next) {
      if (busy) return;
      busy = true;

      next = Math.max(0, parseInt(next || "0", 10) || 0);
      qtyInput.value = String(next);
      if (status) status.textContent = "Saving…";

      try {
        const data = await setQty(part, next);
        qtyInput.value = String(data.quantity);
        if (badge) badge.textContent = String(data.quantity);
        if (status) status.textContent = "";
        showToast(`Saved ${part} = ${data.quantity}`);
      } catch (e) {
        if (status) status.textContent = "Save failed";
      } finally {
        busy = false;
      }
    }

    plusBtn.addEventListener("click", () => {
      const cur = parseInt(qtyInput.value || "0", 10) || 0;
      applyQty(cur + 1);
    });

    minusBtn.addEventListener("click", () => {
      const cur = parseInt(qtyInput.value || "0", 10) || 0;
      applyQty(Math.max(0, cur - 1));
    });

    qtyInput.addEventListener("change", () => {
      applyQty(qtyInput.value);
    });
  });
})();


/* =========================
   Stocktake view toggle: All Parts vs My Stocktake
   (filters cards to qty>0, feels like a separate page)
   Paste at bottom of static/script.js
   ========================= */
(function () {
  const body = document.body;
  if (!body) return;

  const engineer = body.dataset.engineer || "";
  if (!engineer) return;

  const allBtn = document.getElementById("viewAllBtn");
  const mineBtn = document.getElementById("viewMineBtn");
  const mineEmpty = document.getElementById("mineEmpty");
  const mineCountBadge = document.getElementById("mineCountBadge");

  const cards = Array.from(document.querySelectorAll(".stocktake-part-card"));
  if (!allBtn || !mineBtn || !cards.length) return;

  const viewKey = "st_view_" + engineer;

  function getCardQty(card) {
    const input = card.querySelector(".st-qty");
    if (input) return Math.max(0, parseInt(input.value || "0", 10) || 0);
    // if locked, try badge
    const badge = card.querySelector(".st-qty-badge");
    if (badge) return Math.max(0, parseInt(badge.textContent || "0", 10) || 0);
    return 0;
  }

  function countSelectedCards() {
    let count = 0;
    for (const card of cards) {
      if (getCardQty(card) > 0) count++;
    }
    return count;
  }

  function setActiveView(view) {
    const isMine = view === "mine";
    allBtn.classList.toggle("active", !isMine);
    mineBtn.classList.toggle("active", isMine);

    // Filter visibility
    let visibleCount = 0;
    for (const card of cards) {
      const qty = getCardQty(card);
      const show = !isMine || qty > 0;
      card.parentElement.style.display = show ? "" : "none"; // parent col-12/col-md-6
      if (show) visibleCount++;
    }

    const selectedCount = countSelectedCards();
    if (mineCountBadge) mineCountBadge.textContent = String(selectedCount);

    // Empty state only for "My Stocktake"
    if (mineEmpty) {
      mineEmpty.style.display = (isMine && selectedCount === 0) ? "" : "none";
    }

    try { localStorage.setItem(viewKey, view); } catch (e) {}
  }

  // Click handlers
  allBtn.addEventListener("click", () => setActiveView("all"));
  mineBtn.addEventListener("click", () => setActiveView("mine"));

  // Observe qty changes and re-apply filter automatically (so it feels live)
  function hookQtyInputs() {
    cards.forEach(card => {
      const qtyInput = card.querySelector(".st-qty");
      if (!qtyInput) return;

      // Avoid double-binding
      if (qtyInput.dataset.bound === "1") return;
      qtyInput.dataset.bound = "1";

      qtyInput.addEventListener("change", () => {
        const currentView = (() => {
          try { return localStorage.getItem(viewKey) || "all"; } catch (e) { return "all"; }
        })();
        setActiveView(currentView);
      });
    });
  }

  hookQtyInputs();

  // Also re-apply view after plus/minus clicks, because those update input values
  document.addEventListener("click", (e) => {
    const t = e.target;
    if (!t) return;
    if (t.classList && (t.classList.contains("st-plus") || t.classList.contains("st-minus"))) {
      const currentView = (() => {
        try { return localStorage.getItem(viewKey) || "all"; } catch (e) { return "all"; }
      })();
      // small delay so the input value updates first
      setTimeout(() => setActiveView(currentView), 50);
    }
  });

  // Init view
  const startView = (() => {
    try { return localStorage.getItem(viewKey) || "all"; } catch (e) { return "all"; }
  })();
  setActiveView(startView);

})();


/* =========================
   Stocktake filters: submit only on button press
   ========================= */


/* =========================
   Stocktake Leader engineer edit: cards + auto filters + My Stocktake view
   Paste at bottom of static/script.js
   ========================= */
(function () {
  const body = document.body;
  if (!body) return;

  const stocktakeId = body.dataset.stocktakeId;
  const setUrlTemplate = body.dataset.leaderSetUrlTemplate; // .../__PN__
  if (!stocktakeId || !setUrlTemplate) return; // only run on leader edit page

  const form = document.getElementById("leadStocktakeFilters");
  const search = document.getElementById("leadSearch");
  const category = document.getElementById("leadCategory");

  const allBtn = document.getElementById("leadViewAllBtn");
  const mineBtn = document.getElementById("leadViewMineBtn");
  const mineEmpty = document.getElementById("leadMineEmpty");
  const mineCountBadge = document.getElementById("leadMineCountBadge");

  const cards = Array.from(document.querySelectorAll(".leader-part-card"));

  // ---- toast
  const toast = document.getElementById("leadToast");
  const toastMsg = document.getElementById("leadToastMsg");
  const toastClose = document.getElementById("leadToastClose");
  let toastTimer = null;

  function showToast(msg) {
    if (!toast || !toastMsg) return;
    toastMsg.textContent = msg || "Saved";
    toast.style.display = "block";
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toast.style.display = "none"), 1200);
  }
  if (toastClose && toast) toastClose.addEventListener("click", () => (toast.style.display = "none"));

  // ---- filters now submit only when the Search button is pressed

  // ---- view toggle (client-side filter to qty>0)
  const viewKey = "lead_view_" + stocktakeId;

  function getQty(card) {
    const input = card.querySelector(".lead-qty");
    if (!input) return 0;
    return Math.max(0, parseInt(input.value || "0", 10) || 0);
  }

  function countSelected() {
    let n = 0;
    cards.forEach(c => { if (getQty(c) > 0) n++; });
    return n;
  }

  function setView(view) {
    const isMine = view === "mine";
    if (allBtn) allBtn.classList.toggle("active", !isMine);
    if (mineBtn) mineBtn.classList.toggle("active", isMine);

    cards.forEach(card => {
      const qty = getQty(card);
      const show = !isMine || qty > 0;
      card.parentElement.style.display = show ? "" : "none";
    });

    const selectedCount = countSelected();
    if (mineCountBadge) mineCountBadge.textContent = String(selectedCount);
    if (mineEmpty) mineEmpty.style.display = (isMine && selectedCount === 0) ? "" : "none";

    try { localStorage.setItem(viewKey, view); } catch (e) {}
  }

  if (allBtn) allBtn.addEventListener("click", () => setView("all"));
  if (mineBtn) mineBtn.addEventListener("click", () => setView("mine"));

  const startView = (() => {
    try { return localStorage.getItem(viewKey) || "mine"; } catch (e) { return "mine"; }
  })();
  setView(startView);

  // ---- AJAX set qty
  async function setQty(partNumber, qty) {
    const url = setUrlTemplate.replace("__PN__", encodeURIComponent(partNumber));
    const body = new URLSearchParams();
    body.set("quantity", String(qty));

    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded", "X-Requested-With": "XMLHttpRequest" },
      body
    });

    if (!res.ok) throw new Error("Bad response");
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Save failed");
    return data; // quantity, items_count
  }

  // ---- bind controls
  cards.forEach(card => {
    const part = card.getAttribute("data-part");
    if (!part) return;

    const minusBtn = card.querySelector(".lead-minus");
    const plusBtn = card.querySelector(".lead-plus");
    const qtyInput = card.querySelector(".lead-qty");
    const status = card.querySelector(".lead-status");
    const badge = card.querySelector(".lead-qty-badge");

    if (!minusBtn || !plusBtn || !qtyInput) return;

    let busy = false;

    async function applyQty(next) {
      if (busy) return;
      busy = true;

      next = Math.max(0, parseInt(next || "0", 10) || 0);
      qtyInput.value = String(next);
      if (status) status.textContent = "Saving…";

      try {
        const data = await setQty(part, next);
        qtyInput.value = String(data.quantity);
        if (badge) badge.textContent = String(data.quantity);
        if (status) status.textContent = "";
        showToast(`Saved ${part} = ${data.quantity}`);

        // keep My Stocktake view in sync if active
        const currentView = (() => {
          try { return localStorage.getItem(viewKey) || "all"; } catch (e) { return "all"; }
        })();
        setTimeout(() => setView(currentView), 10);

      } catch (e) {
        if (status) status.textContent = "Save failed";
      } finally {
        busy = false;
      }
    }

    plusBtn.addEventListener("click", () => {
      const cur = parseInt(qtyInput.value || "0", 10) || 0;
      applyQty(cur + 1);
    });

    minusBtn.addEventListener("click", () => {
      const cur = parseInt(qtyInput.value || "0", 10) || 0;
      applyQty(Math.max(0, cur - 1));
    });

    qtyInput.addEventListener("change", () => applyQty(qtyInput.value));
  });

})();
