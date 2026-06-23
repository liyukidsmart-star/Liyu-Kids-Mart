/* ── LIYU KIDS MART — MAIN JS ── */

// ── CART BADGE ──
async function updateCartBadge() {
  try {
    const res = await fetch('/cart/count');
    const data = await res.json();
    const badge = document.getElementById('cartBadge');
    if (badge) {
      badge.textContent = data.count || 0;
      badge.style.display = data.count > 0 ? 'flex' : 'none';
    }
  } catch (e) {}
}
updateCartBadge();

// ── ADD TO CART ──
async function addToCart(productId, qty = 1) {
  try {
    const res = await fetch('/cart/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product_id: productId, quantity: qty })
    });
    const data = await res.json();
    if (data.success) {
      const badge = document.getElementById('cartBadge');
      if (badge) {
        badge.textContent = data.data?.cart_count || 0;
        badge.style.display = 'flex';
        badge.classList.add('cart-badge-pulse');
        setTimeout(() => badge.classList.remove('cart-badge-pulse'), 400);
      }
      showToast('✅ Added to cart!', 'success');
    } else {
      showToast('❌ ' + data.message, 'danger');
    }
  } catch (e) {
    showToast('Something went wrong. Please try again.', 'danger');
  }
}

// ── TOAST NOTIFICATIONS ──
function showToast(message, type = 'success', duration = 3000) {
  const container = document.getElementById('toastContainer');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `lkm-toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.animation = 'toastIn .3s ease reverse';
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

// ── NAVBAR SCROLL ──
window.addEventListener('scroll', () => {
  const nav = document.getElementById('mainNav');
  const backToTop = document.getElementById('backToTop');
  if (nav) nav.classList.toggle('scrolled', window.scrollY > 50);
  if (backToTop) backToTop.classList.toggle('show', window.scrollY > 300);
});

// ── BACK TO TOP ──
const backToTopBtn = document.getElementById('backToTop');
if (backToTopBtn) backToTopBtn.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));

// ── AUTO-HIDE FLASH ALERTS ──
document.querySelectorAll('.lkm-alert').forEach(alert => {
  setTimeout(() => {
    if (alert.parentNode) {
      alert.classList.remove('show');
      setTimeout(() => alert.remove(), 300);
    }
  }, 5000);
});

// ── LAZY IMAGE LOADING FALLBACK ──
document.querySelectorAll('img[loading="lazy"]').forEach(img => {
  img.addEventListener('error', function () {
    this.src = '/static/images/placeholder.png';
  });
});
