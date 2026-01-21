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

    let t = null;
    function schedule() {
      clearTimeout(t);
      t = setTimeout(function () {
        fetchPartsIntoTable({ searchInput, categorySelect, tbody, addUrlTemplate, submitted });
      }, 150);
    }

    searchInput.addEventListener("input", schedule);
    categorySelect.addEventListener("change", function () {
      fetchPartsIntoTable({ searchInput, categorySelect, tbody, addUrlTemplate, submitted });
    });

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
