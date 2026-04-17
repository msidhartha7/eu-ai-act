(function () {
  const body = document.body;
  const openButton = document.querySelector("[data-open-sidebar]");
  const closeButtons = document.querySelectorAll("[data-close-sidebar]");
  const searchInput = document.getElementById("site-search");
  const resultsContainer = document.getElementById("search-results");

  function openSidebar() {
    body.classList.add("sidebar-open");
  }

  function closeSidebar() {
    body.classList.remove("sidebar-open");
  }

  if (openButton) {
    openButton.addEventListener("click", openSidebar);
  }

  closeButtons.forEach((button) => {
    button.addEventListener("click", closeSidebar);
  });

  let searchIndexPromise = null;

  function loadSearchIndex() {
    if (!searchIndexPromise) {
      const siteBase = document.documentElement.dataset.siteBase || "";
      searchIndexPromise = fetch(`${siteBase}/search-index.json`)
        .then((response) => response.json())
        .catch(() => []);
    }
    return searchIndexPromise;
  }

  function hideResults() {
    if (resultsContainer) {
      resultsContainer.hidden = true;
      resultsContainer.innerHTML = "";
    }
  }

  function renderResults(items) {
    if (!resultsContainer) {
      return;
    }
    if (!items.length) {
      resultsContainer.hidden = false;
      resultsContainer.innerHTML = '<div class="search-result"><span class="search-result-title">No matching results</span></div>';
      return;
    }

    resultsContainer.hidden = false;
    resultsContainer.innerHTML = items
      .map(
        (item) => `
          <a class="search-result" href="${item.url}">
            <span class="search-result-type">${item.type_label} ${item.display_number}</span>
            <span class="search-result-title">${item.title}</span>
            <span class="search-result-excerpt">${item.excerpt || ""}</span>
          </a>
        `,
      )
      .join("");
  }

  function scoreItem(item, query) {
    let score = 0;
    if (item.title.toLowerCase().includes(query)) score += 4;
    if (`${item.type_label} ${item.display_number}`.toLowerCase().includes(query)) score += 3;
    if ((item.excerpt || "").toLowerCase().includes(query)) score += 2;
    if ((item.search_text || "").includes(query)) score += 1;
    return score;
  }

  if (searchInput && resultsContainer) {
    searchInput.addEventListener("input", async (event) => {
      const query = event.target.value.trim().toLowerCase();
      if (!query) {
        hideResults();
        return;
      }

      const index = await loadSearchIndex();
      const matches = index
        .map((item) => ({ item, score: scoreItem(item, query) }))
        .filter((entry) => entry.score > 0)
        .sort((left, right) => right.score - left.score || left.item.title.localeCompare(right.item.title))
        .slice(0, 12)
        .map((entry) => entry.item);

      renderResults(matches);
    });

    document.addEventListener("click", (event) => {
      if (!resultsContainer.contains(event.target) && event.target !== searchInput) {
        hideResults();
      }
    });

    searchInput.addEventListener("focus", () => {
      if (searchInput.value.trim()) {
        searchInput.dispatchEvent(new Event("input"));
      }
    });
  }
})();
