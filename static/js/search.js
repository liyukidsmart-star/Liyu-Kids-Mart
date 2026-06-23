/* ── LIYU KIDS MART — SEARCH JS ── */

let searchTimer = null;

async function lkmSearchInput(query, dropdownId = 'searchDropdown') {
  const dropdown = document.getElementById(dropdownId);
  if (!dropdown) return;
  if (!query || query.length < 2) {
    dropdown.classList.remove('show');
    dropdown.innerHTML = '';
    return;
  }
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    try {
      const res = await fetch(`/api/v1/products/search?q=${encodeURIComponent(query)}&limit=6`);
      const data = await res.json();
      if (data.success && data.data.products.length > 0) {
        dropdown.innerHTML = data.data.products.map(p => `
          <a href="/shop/product/${p.slug}" class="search-result-item">
            <img src="${p.primary_image}" alt="${p.name}" class="search-result-img"
                 onerror="this.src='/static/images/placeholder.png'"/>
            <div class="flex-grow-1">
              <div class="search-result-name">${p.name}</div>
              <div class="search-result-price">ETB ${Math.round(p.price).toLocaleString()}</div>
            </div>
          </a>
        `).join('') + `<a href="/shop/search?q=${encodeURIComponent(query)}" class="search-result-item fw-600 text-green">
          <i class="fas fa-search me-2"></i>See all results for "${query}"
        </a>`;
        dropdown.classList.add('show');
      } else {
        dropdown.innerHTML = `<div class="search-result-item text-muted">No results for "${query}"</div>`;
        dropdown.classList.add('show');
      }
    } catch (e) {
      dropdown.classList.remove('show');
    }
  }, 280);
}

// Wire up navbar search inputs
['globalSearch', 'mobileSearch'].forEach(id => {
  const el = document.getElementById(id);
  if (!el) return;
  const dropId = id === 'globalSearch' ? 'searchDropdown' : 'mobileSearchDropdown';
  el.addEventListener('input', e => lkmSearchInput(e.target.value, dropId));
  el.addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      const q = e.target.value.trim();
      if (q) window.location.href = `/shop/search?q=${encodeURIComponent(q)}`;
    }
  });
});

// Close dropdowns on outside click
document.addEventListener('click', e => {
  if (!e.target.closest('.search-wrapper')) {
    document.querySelectorAll('.search-dropdown').forEach(d => d.classList.remove('show'));
  }
});
