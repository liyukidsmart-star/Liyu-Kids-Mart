/* ── LIYU KIDS MART — CART JS ── */
// cart.js — loaded on every page for cart badge and mini interactions
// The full cart page logic is inline in cart.html

document.addEventListener('DOMContentLoaded', () => {
  updateCartBadge();
});

// Exported for cart.html inline usage
window.cartRemove = async function(itemId) {
  await fetch('/cart/remove', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ item_id: itemId })
  });
  const el = document.getElementById('cartItem' + itemId);
  if (el) el.remove();
  updateCartBadge();
};
