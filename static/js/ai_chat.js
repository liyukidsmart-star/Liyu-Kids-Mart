/* ── LIYU AI CHAT — Smart Shopping Assistant ── */
let aiSessionId = localStorage.getItem('lkm_ai_session') || null;
let aiOpen = false;
let pendingAddedProducts = [];
let lastSuggestedProducts = []; // Store all products returned by last suggestion
let suggestionsShown = 3;       // How many are currently visible

const aiPanel   = document.getElementById('aiChatPanel');
const aiToggle  = document.getElementById('aiChatToggle');
const aiIcon    = document.getElementById('aiToggleIcon');
const aiMessages = document.getElementById('aiMessages');
const aiInput   = document.getElementById('aiInput');

// ── Toggle panel ──
if (aiToggle) {
  aiToggle.addEventListener('click', () => {
    aiOpen = !aiOpen;
    aiPanel.classList.toggle('open', aiOpen);
    aiIcon.className = aiOpen ? 'fas fa-times' : 'fas fa-robot';
    if (aiOpen) { aiInput?.focus(); scrollToBottom(); }
  });
}
document.getElementById('aiCloseBtn')?.addEventListener('click', () => {
  aiOpen = false;
  aiPanel?.classList.remove('open');
  aiIcon.className = 'fas fa-robot';
});

// ── Strip markdown ──
function stripMarkdown(text) {
  return text
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/\*(.+?)\*/g, '$1')
    .replace(/__(.+?)__/g, '$1')
    .replace(/_(.+?)_/g, '$1');
}

// ── Send a message ──
async function aiSend(overrideMsg) {
  const msg = overrideMsg || aiInput.value.trim();
  if (!msg) return;
  if (!overrideMsg) aiInput.value = '';
  pendingAddedProducts = [];
  appendMessage('user', msg);
  showTyping();
  try {
    const res = await fetch('/api/v1/ai/chat', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg, session_id: aiSessionId, channel: 'web' })
    });
    const data = await res.json();
    removeTyping();
    if (data.success) {
      aiSessionId = data.data.session_id;
      localStorage.setItem('lkm_ai_session', aiSessionId);
      appendMessage('assistant', stripMarkdown(data.data.message));

      const products = data.data.products || [];
      lastSuggestedProducts = products;
      suggestionsShown = 3;

      if (products.length > 0) {
        appendProductSuggestions(products, 3, data.data.all_candidate_ids || []);
      }

      // After cart confirmation, show checkout prompt
      if (data.data.show_checkout_prompt) {
        setTimeout(() => appendCheckoutPrompt(data.data.cart_summary), 800);
      }
    } else {
      appendMessage('assistant', "Sorry, I'm having a bit of trouble right now. Give me a moment and try again!");
    }
  } catch (e) {
    removeTyping();
    appendMessage('assistant', "Oops, something went wrong on my end. Please try again in a moment.");
  }
}

function aiSendSuggestion(text) {
  aiInput.value = '';
  aiSend(text);
}

aiInput?.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); aiSend(); }
});

// ── Append plain message bubble ──
function appendMessage(role, content) {
  const wrapper = document.createElement('div');
  wrapper.className = `ai-message ${role}`;
  const bubble = document.createElement('div');
  bubble.className = 'ai-bubble';

  // Location link → beautiful map button
  const locationRegex = /https:\/\/(?:share\.google|maps\.app\.goo\.gl)\/[^\s\)\"\'<>]+/g;
  let html = stripMarkdown(content).replace(/\n/g, '<br/>');
  html = html.replace(locationRegex, (url) => {
    return `<a href="${url}" target="_blank" class="ai-map-btn">
      <span>📍</span> Open Location on Google Maps
    </a>`;
  });
  bubble.innerHTML = html;
  wrapper.appendChild(bubble);
  aiMessages.appendChild(wrapper);
  scrollToBottom();
}

// ── Quick-view product modal ──
function showProductQuickView(p) {
  const existing = document.getElementById('aiProductModal');
  if (existing) existing.remove();

  const modal = document.createElement('div');
  modal.id = 'aiProductModal';
  modal.className = 'ai-product-modal-overlay';
  modal.innerHTML = `
    <div class="ai-product-modal">
      <button class="ai-modal-close" onclick="document.getElementById('aiProductModal').remove()">
        <i class="fas fa-times"></i>
      </button>
      <img src="${p.primary_image}" class="ai-modal-img" onerror="this.src='/static/images/placeholder.png'"/>
      <div class="ai-modal-body">
        <div class="ai-modal-name">${p.name}</div>
        <div class="ai-modal-price">ETB ${Math.round(p.price).toLocaleString()}</div>
        ${p.age_label ? `<div class="ai-modal-age">🎈 Ages: ${p.age_label}</div>` : ''}
        ${p.short_description ? `<div class="ai-modal-desc">${p.short_description}</div>` : ''}
        <div class="ai-modal-actions">
          <button class="ai-modal-cart-btn" id="modal-cart-btn-${p.id}" 
            onclick="aiAddToCartModal(${p.id}, ${JSON.stringify(p.name)}, this)">
            <i class="fas fa-cart-plus"></i> Add to Cart
          </button>
          <a href="/shop/product/${p.slug}" class="ai-modal-view-link" target="_blank">
            View Full Details
          </a>
        </div>
      </div>
    </div>
  `;
  // Close when clicking overlay
  modal.addEventListener('click', (e) => {
    if (e.target === modal) modal.remove();
  });
  document.body.appendChild(modal);
}

// ── Add to cart from quick-view modal ──
window.aiAddToCartModal = async function(productId, productName, btn) {
  btn.disabled = true;
  btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Adding...';
  try {
    const res = await fetch('/api/v1/cart/items', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product_id: productId, quantity: 1 })
    });
    const data = await res.json();
    if (data.success) {
      btn.innerHTML = '<i class="fas fa-check"></i> Added!';
      btn.classList.add('added');
      pendingAddedProducts.push(productName);
      _updateCartBadge();
      // Update card button too if visible
      const cardBtn = document.getElementById(`ai-cart-btn-${productId}`);
      if (cardBtn) { cardBtn.innerHTML = '<i class="fas fa-check"></i> Added!'; cardBtn.classList.add('added'); }
      // Close modal after brief success state
      setTimeout(() => document.getElementById('aiProductModal')?.remove(), 1200);
    } else {
      btn.innerHTML = '<i class="fas fa-exclamation-triangle"></i> ' + (data.message || 'Error');
      setTimeout(() => { btn.innerHTML = '<i class="fas fa-cart-plus"></i> Add to Cart'; btn.disabled = false; }, 2500);
    }
  } catch(e) {
    btn.innerHTML = '<i class="fas fa-exclamation-triangle"></i> Error';
    setTimeout(() => { btn.innerHTML = '<i class="fas fa-cart-plus"></i> Add to Cart'; btn.disabled = false; }, 2000);
  }
};

// ── Product suggestion cards ──
let _confirmDivRef = null;

function appendProductSuggestions(products, showCount, allCandidateIds) {
  showCount = showCount || 3;
  const toShow = products.slice(0, showCount);
  const hasMore = products.length > showCount;

  const wrapper = document.createElement('div');
  wrapper.className = 'ai-message assistant';
  wrapper.id = `ai-suggestions-wrap-${Date.now()}`;

  const inner = document.createElement('div');
  inner.className = 'ai-product-suggestions';

  const confirmId = `confirm-${Date.now()}`;

  toShow.forEach(p => {
    inner.appendChild(_buildProductCard(p, confirmId));
  });

  // "View more" button
  if (hasMore) {
    const moreBtn = document.createElement('button');
    moreBtn.className = 'ai-view-more-btn';
    moreBtn.innerHTML = `<i class="fas fa-plus-circle"></i> Show ${Math.min(3, products.length - showCount)} more options`;
    moreBtn.onclick = () => {
      const nextCount = Math.min(showCount + 3, products.length);
      moreBtn.remove();
      const extraSlice = products.slice(showCount, nextCount);
      extraSlice.forEach(p => {
        inner.insertBefore(_buildProductCard(p, confirmId), inner.querySelector('.ai-confirm-area'));
      });
      // Add another "view more" if still more remain
      if (nextCount < products.length) {
        const newMoreBtn = document.createElement('button');
        newMoreBtn.className = 'ai-view-more-btn';
        newMoreBtn.innerHTML = `<i class="fas fa-plus-circle"></i> Show ${Math.min(3, products.length - nextCount)} more options`;
        newMoreBtn.onclick = moreBtn.onclick; // reassign to next batch
        // Update the closure
        newMoreBtn.onclick = () => {
          const n = nextCount;
          const nn = Math.min(n + 3, products.length);
          newMoreBtn.remove();
          products.slice(n, nn).forEach(p => {
            inner.insertBefore(_buildProductCard(p, confirmId), inner.querySelector('.ai-confirm-area'));
          });
          scrollToBottom();
        };
        inner.insertBefore(newMoreBtn, inner.querySelector('.ai-confirm-area'));
      }
      scrollToBottom();
    };
    inner.appendChild(moreBtn);
  }

  // "Done adding" confirm button
  const confirmDiv = document.createElement('div');
  confirmDiv.className = 'ai-confirm-area';
  confirmDiv.id = confirmId;
  confirmDiv.style.display = 'none';
  confirmDiv.innerHTML = `
    <button class="ai-confirm-btn" onclick="confirmCartToLiyu(this)">
      ✅ Done! Tell Liyu what I added
    </button>
  `;
  _confirmDivRef = confirmDiv;

  inner.appendChild(confirmDiv);
  wrapper.appendChild(inner);
  aiMessages.appendChild(wrapper);
  scrollToBottom();
}

function _buildProductCard(p, confirmId) {
  const card = document.createElement('div');
  card.className = 'ai-product-card';
  const hasDiscount = p.compare_price && p.compare_price > p.price;
  card.innerHTML = `
    <div class="ai-product-card-inner" onclick="showProductQuickView(${JSON.stringify(p).replace(/"/g, '&quot;')})">
      <img src="${p.primary_image}" class="ai-product-thumb" onerror="this.src='/static/images/placeholder.png'"/>
      <div class="ai-product-details">
        <div class="ai-product-name">${p.name}</div>
        <div class="ai-product-price-row">
          <span class="ai-product-price">ETB ${Math.round(p.price).toLocaleString()}</span>
          ${hasDiscount ? `<span class="ai-product-compare">ETB ${Math.round(p.compare_price).toLocaleString()}</span>` : ''}
        </div>
        ${p.age_label ? `<div class="ai-product-age">🎈 ${p.age_label}</div>` : ''}
        ${p.short_description ? `<div class="ai-product-preview">${p.short_description.slice(0, 60)}${p.short_description.length > 60 ? '…' : ''}</div>` : ''}
      </div>
    </div>
    <button class="ai-cart-btn" id="ai-cart-btn-${p.id}"
      data-product-id="${p.id}"
      data-product-name="${p.name.replace(/"/g, '&quot;')}"
      data-confirm-id="${confirmId}"
      onclick="event.stopPropagation(); aiAddToCartFromBtn(this)">
      <i class="fas fa-cart-plus"></i> Add to Cart
    </button>
  `;
  return card;
}

// ── Add to cart from product card button ──
window.aiAddToCartFromBtn = async function(btn) {
  const productId = parseInt(btn.dataset.productId);
  const productName = btn.dataset.productName;
  const confirmId = btn.dataset.confirmId;

  btn.disabled = true;
  btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';

  try {
    const res = await fetch('/api/v1/cart/items', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product_id: productId, quantity: 1 })
    });
    const data = await res.json();

    if (data.success) {
      btn.innerHTML = '<i class="fas fa-check"></i> Added!';
      btn.classList.add('added');
      pendingAddedProducts.push(productName);
      _updateCartBadge();

      // Show the confirm button
      const confirmDiv = document.getElementById(confirmId);
      if (confirmDiv) confirmDiv.style.display = 'block';
    } else {
      btn.innerHTML = '<i class="fas fa-exclamation-triangle"></i> ' + (data.message || 'Error');
      btn.classList.add('error');
      setTimeout(() => {
        btn.innerHTML = '<i class="fas fa-cart-plus"></i> Add to Cart';
        btn.classList.remove('error');
        btn.disabled = false;
      }, 2500);
    }
  } catch(e) {
    btn.innerHTML = '<i class="fas fa-exclamation-triangle"></i> Error';
    setTimeout(() => {
      btn.innerHTML = '<i class="fas fa-cart-plus"></i> Add to Cart';
      btn.disabled = false;
    }, 2000);
  }
};

// ── Confirm button — tell Liyu what was added ──
window.confirmCartToLiyu = function(btn) {
  btn.disabled = true;
  btn.closest('.ai-confirm-area').style.display = 'none';

  const added = pendingAddedProducts.slice();
  pendingAddedProducts = [];

  let msg;
  if (added.length === 0) {
    msg = "I've finished looking at the suggestions.";
  } else if (added.length === 1) {
    msg = `I added "${added[0]}" to my cart!`;
  } else {
    const last = added.pop();
    msg = `I added ${added.map(n => `"${n}"`).join(', ')} and "${last}" to my cart!`;
  }
  aiSend(msg);
};

// ── Checkout prompt after cart confirm ──
function appendCheckoutPrompt(cartSummary) {
  const wrapper = document.createElement('div');
  wrapper.className = 'ai-message assistant';

  const actionsDiv = document.createElement('div');
  actionsDiv.className = 'ai-checkout-actions';

  if (cartSummary) {
    const summary = document.createElement('div');
    summary.className = 'ai-cart-summary';
    summary.innerHTML = `
      <div class="ai-summary-title">🛒 Your Cart</div>
      ${cartSummary.items.map(i => `<div class="ai-summary-item"><span>${i.name}</span><span>ETB ${Math.round(i.price * i.qty).toLocaleString()}</span></div>`).join('')}
      <div class="ai-summary-total">
        <span>Total</span>
        <span>ETB ${Math.round(cartSummary.total).toLocaleString()}</span>
      </div>
      ${cartSummary.delivery_fee === 0 ? '<div class="ai-summary-free-delivery">🎉 Free delivery included!</div>' : `<div class="ai-summary-delivery">+ ETB ${cartSummary.delivery_fee} delivery</div>`}
    `;
    actionsDiv.appendChild(summary);
  }

  const btnsRow = document.createElement('div');
  btnsRow.className = 'ai-checkout-btn-row';
  btnsRow.innerHTML = `
    <a href="/cart/checkout" class="ai-checkout-cta-btn">
      <i class="fas fa-lock"></i> Proceed to Checkout
    </a>
    <button class="ai-keep-shopping-btn" onclick="this.closest('.ai-message').remove(); aiInput.focus()">
      <i class="fas fa-search"></i> Keep Shopping
    </button>
  `;
  actionsDiv.appendChild(btnsRow);
  wrapper.appendChild(actionsDiv);
  aiMessages.appendChild(wrapper);
  scrollToBottom();
}

// ── Update cart badge ──
function _updateCartBadge() {
  const badge = document.getElementById('cartBadge');
  if (badge) {
    const current = parseInt(badge.textContent || '0');
    badge.textContent = current + 1;
    badge.style.display = 'flex';
    badge.classList.add('cart-badge-pulse');
    setTimeout(() => badge.classList.remove('cart-badge-pulse'), 400);
  }
  const cc = document.getElementById('cart-count');
  if (cc) cc.innerText = parseInt(cc.innerText || 0) + 1;
}

// ── Typing indicator ──
function showTyping() {
  const w = document.createElement('div');
  w.className = 'ai-message assistant typing';
  w.id = 'aiTyping';
  w.innerHTML = `
    <div class="ai-bubble typing-bubble">
      <span></span>
      <span></span>
      <span></span>
    </div>
  `;
  aiMessages.appendChild(w);
  scrollToBottom();
}
function removeTyping() { document.getElementById('aiTyping')?.remove(); }

// ── Utils ──
function scrollToBottom() {
  requestAnimationFrame(() => {
    aiMessages.scrollTop = aiMessages.scrollHeight;
  });
}

window.aiClearChat = function() {
  aiMessages.innerHTML = `
    <div class="ai-message assistant">
      <div class="ai-bubble">
        Hi there! 👋 I'm Liyu, your personal shopping guide here at Liyu Kids Mart.<br/><br/>
        Tell me about your child and I'll help you find the perfect toy!
      </div>
    </div>
    <div class="ai-suggestions">
      <button class="ai-chip" onclick="aiSendSuggestion('Toy for a 2 year old')">Toy for a 2 year old</button>
      <button class="ai-chip" onclick="aiSendSuggestion('Montessori materials')">Montessori materials</button>
      <button class="ai-chip" onclick="aiSendSuggestion('Under 500 birr')">Under 500 birr</button>
      <button class="ai-chip" onclick="aiSendSuggestion('Gift ideas for 5 year old')">Gift ideas</button>
      <button class="ai-chip" onclick="aiSendSuggestion('What\\'s in my cart')">View my cart</button>
    </div>
  `;
};
