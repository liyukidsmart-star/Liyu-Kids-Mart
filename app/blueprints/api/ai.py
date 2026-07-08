import os
import re
import logging
from flask import request
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity
from app.blueprints.api import api_bp
from app.extensions import db
from app.models.ai_conversation import AIConversation
from app.models.user import User
from app.models.product import Product
from app.utils import success_response, error_response, generate_session_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _get_ai_user():
    try:
        verify_jwt_in_request(optional=True)
        uid = get_jwt_identity()
        if uid:
            return db.session.get(User, uid)
    except Exception:
        pass
    data = request.get_json(silent=True) or {}
    telegram_id = data.get('telegram_id')
    if telegram_id:
        return User.query.filter_by(telegram_id=str(telegram_id)).first()
    return None


def _get_conversation_history(session_id, limit=10):
    msgs = (
        AIConversation.query.filter_by(session_id=session_id)
        .filter(AIConversation.role.in_(['user', 'assistant']))
        .order_by(AIConversation.created_at.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(msgs))


def _save_message(session_id, role, content, user_id=None, channel='web'):
    msg = AIConversation(
        session_id=session_id,
        role=role,
        content=content,
        user_id=user_id,
        channel=channel,
    )
    db.session.add(msg)
    db.session.commit()


# ---------------------------------------------------------------------------
# Age detection
# ---------------------------------------------------------------------------

def _detect_age_months_from_text(text):
    """
    Extract age mentions from text.
    Returns list of (min_months, max_months) tuples representing strict age windows.
    """
    results = []
    text_lower = text.lower()

    # "X year old" / "X-year-old" / "X yr old"
    for m in re.finditer(r'(\d+)\s*[-\s]?(?:year|yr)s?\s*(?:old)?', text_lower):
        years = int(m.group(1))
        # Tight window: exact age year, allow ±6 months either side max
        min_m = max(0, years * 12 - 6)
        max_m = years * 12 + 6
        results.append((min_m, max_m))

    # "X-Y year old range"
    for m in re.finditer(r'(\d+)\s*[-–]\s*(\d+)\s*(?:year|yr)', text_lower):
        y1, y2 = int(m.group(1)), int(m.group(2))
        results.append((y1 * 12, y2 * 12))

    # "X months old"
    for m in re.finditer(r'(\d+)\s*(?:month|mo)s?\s*(?:old)?', text_lower):
        months = int(m.group(1))
        results.append((max(0, months - 3), months + 3))

    # Amharic number words → years
    amharic_numbers = {
        'አንድ': 1, 'ሁለት': 2, 'ሶስት': 3, 'አራት': 4, 'አምስት': 5,
        'ስድስት': 6, 'ሰባት': 7, 'ስምንት': 8, 'ዘጠኝ': 9, 'አስር': 10,
    }
    for word, num in amharic_numbers.items():
        if word in text:
            min_m = max(0, num * 12 - 6)
            results.append((min_m, num * 12 + 6))

    return results


# ---------------------------------------------------------------------------
# Product name detection
# ---------------------------------------------------------------------------

def _find_product_by_name_in_message(text):
    """
    If the customer explicitly mentions a product name that exists in our catalog,
    return those products (up to 3).
    Matches against product name, name_am, slug words.
    """
    text_lower = text.lower()
    all_products = Product.query.filter_by(is_active=True).all()
    matches = []
    for p in all_products:
        name_lower = p.name.lower()
        # Check if at least 2 consecutive significant words from product name appear in text
        words = [w for w in name_lower.split() if len(w) > 3]
        if not words:
            continue
        match_count = sum(1 for w in words if w in text_lower)
        # Strong match: most of the name words appear, or the full name substring
        if name_lower in text_lower or (len(words) >= 1 and match_count >= max(1, len(words) - 1)):
            matches.append((match_count, p))

    matches.sort(key=lambda x: -x[0])
    return [p for _, p in matches[:3]]


# ---------------------------------------------------------------------------
# Product keyword hints
# ---------------------------------------------------------------------------

_PRODUCT_KEYWORDS = [
    'montessori', 'wooden', 'puzzle', 'book', 'art', 'music', 'building',
    'blocks', 'paint', 'sensory', 'stacking', 'shape', 'color', 'number',
    'letter', 'alphabet', 'animal', 'toy', 'game', 'doll', 'instrument',
    'reading', 'drawing', 'math', 'counting', 'abacus', 'flashcard',
    'sorting', 'lacing', 'threading', 'bead', 'clay', 'craft', 'foam',
    'educational', 'learning', 'stem', 'science',
]

_AMHARIC_PRODUCT_HINTS = [
    'ምርት', 'ምርቶቹ', 'መጫወቻ', 'መጫወቻዎቹ', 'መጽሐፍ', 'መጽሐፍ', 'ሞንቴሶሪ',
    'እንጨት', 'ፓዝሎ', 'ሙዚካ', 'ብሎክ', 'ብሎክት', 'ቡቅ', 'ፒደል', 'ቀለም',
    'ስታኪጉን', 'ዳይይ', 'አሳይዑ', 'አሳይ', 'አለን',
]


def _is_amharic_text(text):
    return any('ሀ' <= ch <= '፿' for ch in text)


def _is_product_request(text):
    lowered = text.lower()
    english_hints = [
        'buy', 'find', 'search', 'looking for', 'show me', 'recommend', 'suggest',
        'toy', 'product', 'montessori', 'wooden', 'puzzle', 'blocks', 'book',
        'art', 'musical', 'educational', 'what do you have', 'do you sell',
        'age', 'years old', 'months old', 'gift', 'birthday', 'child', 'kid',
        'toddler', 'baby', 'infant', 'best for', 'good for', 'appropriate',
    ]
    if any(hint in lowered for hint in english_hints):
        return True
    if _is_amharic_text(text) and any(hint in text for hint in _AMHARIC_PRODUCT_HINTS):
        return True
    return False


# ---------------------------------------------------------------------------
# Candidate product retrieval – strict age filtering
# ---------------------------------------------------------------------------

def _get_all_candidate_products(query_text, history_text='', exclude_ids=None):
    """
    Retrieve candidate products strictly filtered by:
    1. Age range (tight ±6 month window around mentioned age)
    2. Product keyword relevance
    3. Price constraints if mentioned
    Returns at most 10 products.
    """
    exclude_ids = set(exclude_ids or [])
    combined = (query_text + ' ' + history_text).lower()
    combined_original = f'{query_text} {history_text}'

    # --- Age filtering (strict) ---
    age_windows = _detect_age_months_from_text(combined_original)
    age_filter_active = False
    min_age_filter, max_age_filter = None, None
    if age_windows:
        # Merge all windows into one envelope
        all_mins = [w[0] for w in age_windows]
        all_maxs = [w[1] for w in age_windows]
        min_age_filter = min(all_mins)
        max_age_filter = max(all_maxs)
        age_filter_active = True

    # --- Price filtering ---
    max_price = None
    price_match = re.search(r'(?:under|below|less than|max(?:imum)?)\s*(\d+)', combined)
    if not price_match:
        price_match = re.search(r'(\d+)\s*(?:birr|etb|br)', combined)
        if price_match and ('under' in combined or 'cheap' in combined):
            max_price = int(price_match.group(1))
    else:
        max_price = int(price_match.group(1))

    # --- Build base query ---
    q = Product.query.filter(Product.is_active == True)
    if max_price:
        q = q.filter(Product.price <= max_price)
    if age_filter_active:
        # Product age range must OVERLAP the requested age window
        # i.e. product_min <= our_max AND product_max >= our_min
        q = q.filter(
            Product.age_min_months <= max_age_filter,
            Product.age_max_months >= min_age_filter,
        )

    # --- Keyword matching ---
    search_space = [combined, combined_original.lower()]
    results = []
    seen = set()

    for kw in _PRODUCT_KEYWORDS:
        if any(kw in space for space in search_space):
            kw_q = q.filter(
                db.or_(
                    Product.name.ilike(f'%{kw}%'),
                    Product.name_am.ilike(f'%{kw}%'),
                    Product.description.ilike(f'%{kw}%'),
                    Product.description_am.ilike(f'%{kw}%'),
                    Product.short_description.ilike(f'%{kw}%'),
                    Product.short_description_am.ilike(f'%{kw}%'),
                    Product.slug.ilike(f'%{kw}%'),
                )
            ).order_by(Product.sales_count.desc()).limit(10).all()
            for p in kw_q:
                if p.id not in seen and p.id not in exclude_ids:
                    results.append(p)
                    seen.add(p.id)

    # --- Amharic fallback ---
    if not results and _is_amharic_text(combined_original):
        am_q = q.filter(
            db.or_(
                Product.name_am.isnot(None),
                Product.description_am.isnot(None),
                Product.short_description_am.isnot(None),
            )
        ).order_by(Product.sales_count.desc()).limit(10).all()
        for p in am_q:
            if p.id not in exclude_ids:
                results.append(p)

    # --- Age-filtered fallback (no keyword match, but age specified) ---
    if not results and age_filter_active:
        for p in q.order_by(Product.sales_count.desc()).limit(10).all():
            if p.id not in exclude_ids:
                results.append(p)

    # --- General fallback (no age, no keyword) ---
    if not results:
        base_q = Product.query.filter(Product.is_active == True)
        if max_price:
            base_q = base_q.filter(Product.price <= max_price)
        for p in base_q.order_by(Product.sales_count.desc()).limit(10).all():
            if p.id not in exclude_ids:
                results.append(p)

    return results[:10]


# ---------------------------------------------------------------------------
# Extract product names mentioned in the AI's reply (smart matching)
# ---------------------------------------------------------------------------

def _extract_mentioned_products(reply_text, candidates):
    """
    Find which candidate products the AI actually named in its reply.
    Uses multi-strategy matching:
    1. Exact substring (most reliable)
    2. Word-overlap scoring with a high threshold
    Returns list of Product objects in mention order, max 3.
    """
    reply_lower = reply_text.lower()
    mentioned = []
    seen_ids = set()

    for p in candidates:
        name_lower = p.name.lower()

        # Strategy 1: product name appears as substring
        if name_lower in reply_lower:
            mentioned.append((10, p))
            seen_ids.add(p.id)
            continue

        # Strategy 2: significant word overlap (strict – need 2/3+ of words)
        words = [w for w in name_lower.split() if len(w) > 3]
        if not words:
            continue
        hit_count = sum(1 for w in words if w in reply_lower)
        threshold = max(2, len(words) * 2 // 3)
        if hit_count >= threshold and p.id not in seen_ids:
            mentioned.append((hit_count, p))
            seen_ids.add(p.id)

    # Sort by match quality, return top 3
    mentioned.sort(key=lambda x: -x[0])
    return [p for _, p in mentioned[:3]]


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are Liyu, a warm and knowledgeable shopping guide at Liyu Kids Mart, Addis Ababa's best educational toy store. You feel like a real person - not an AI.

Personality and tone:
- Speak naturally, warmly, and conversationally - like a trusted friend helping a parent.
- Match the customer's language exactly. If they write in Amharic, respond fully in Amharic. If English, respond in English. Never mix languages unless they do.
- Use emojis occasionally but naturally. Never overdo it.
- Keep responses short and helpful - under 120 words unless the customer asks for detail.
- Never say you are an AI, a bot, or "Grok-powered". You are Liyu, a person.
- Never use asterisks (*), underscores (_), or markdown formatting.

CRITICAL PRODUCT RECOMMENDATION RULES:
- When a customer asks for a recommendation or product suggestion, you MUST recommend EXACTLY 3 products from the [SYSTEM CONTEXT - PRODUCTS] list.
- Name each of the 3 products EXACTLY as they appear in the product list (no paraphrasing, no inventing names).
- For each product you mention, include the age suitability (e.g. "great for 3-year-olds" or "perfect for ages 2-4").
- Give a short warm 1-sentence description of why each product is great.
- After naming the 3 products, say "The product cards are below - tap one to add it to your cart!"
- If a customer mentions a specific product by name, identify it from the product list, confirm it, and give them a brief warm description of that exact product including its age range and benefits.
- NEVER recommend a product that is not in [SYSTEM CONTEXT - PRODUCTS].
- NEVER invent or paraphrase product names.

Age rule:
- ONLY recommend products that are appropriate for the age the customer mentioned.
- If a customer says "3 year old", only recommend products where the age range includes 3 years.
- Never recommend baby toys (0-12 months) for a 4-year-old, or big-kid toys (8+) for a toddler.

Store policies:
- Delivery: FREE delivery for orders over 1000 ETB. Otherwise, delivery is 80 ETB.
- Payment methods: We accept Telebirr, CBE (Commercial Bank of Ethiopia), and Cash on Delivery.
- Location: We are located in Bole Bulbula, 93 Mazoriya.
- Store hours: We are open Monday to Saturday, 9:00 AM to 6:00 PM.

Critical rules:
- Only recommend products from the [SYSTEM CONTEXT - PRODUCTS] list. Never invent names or prices.
- Never mention items in cart unless [SYSTEM CONTEXT - CART] explicitly lists them.
- Never use markdown formatting."""


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_gemini_prompt(user_message, history, cart_items, candidates, named_products=None):
    parts = [SYSTEM_PROMPT]

    if history:
        history_lines = []
        for item in history[-6:]:
            label = 'User' if item.role == 'user' else 'Assistant'
            history_lines.append(f'{label}: {item.content}')
        parts.append('Conversation history:\n' + '\n'.join(history_lines))

    if cart_items:
        cart_lines = [
            f"- {item.quantity}x {item.product.name} (ETB {float(item.product.current_price()):,.0f})"
            for item in cart_items
        ]
        parts.append('SYSTEM CONTEXT - CART:\n' + '\n'.join(cart_lines))
    else:
        parts.append('SYSTEM CONTEXT - CART: Cart is EMPTY. Do NOT mention any cart items.')

    if named_products:
        # Customer explicitly asked about specific products – put them first
        named_lines = []
        for p in named_products:
            stock_note = f'In stock ({p.stock_qty} left)' if p.stock_qty > 0 else 'Out of stock'
            age_lbl = p.age_label()
            desc = (p.short_description or p.description or '')[:120]
            named_lines.append(
                f'- {p.name} | ETB {float(p.current_price()):,.0f} | Age: {age_lbl} | {stock_note} | {desc}'
            )
        parts.append(
            'SYSTEM CONTEXT - CUSTOMER ASKED ABOUT THESE PRODUCTS (describe them warmly):\n'
            + '\n'.join(named_lines)
        )

    if candidates:
        product_lines = []
        for p in candidates:
            stock_note = f'In stock ({p.stock_qty} left)' if p.stock_qty > 0 else 'Out of stock'
            product_lines.append(
                f'- {p.name} | ETB {float(p.current_price()):,.0f} | Age: {p.age_label()} | {stock_note}'
            )
        parts.append('SYSTEM CONTEXT - PRODUCTS (choose EXACTLY 3 to recommend):\n' + '\n'.join(product_lines))
    else:
        parts.append('SYSTEM CONTEXT - PRODUCTS: No matching products found for this request.')

    parts.append('User message:\n' + user_message)
    return '\n\n'.join(parts)


# ---------------------------------------------------------------------------
# Gemini call
# ---------------------------------------------------------------------------

def _call_gemini(prompt):
    gemini_key = os.getenv('GEMINI_API_KEY', '').strip()
    if not gemini_key:
        raise RuntimeError('GEMINI_API_KEY is not configured')

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=gemini_key)
    response = client.models.generate_content(
        model=os.getenv('GEMINI_MODEL', 'gemini-3.1-flash-lite'),
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.7,
            max_output_tokens=400,
        ),
    )
    text = (getattr(response, 'text', '') or '').strip()
    if not text:
        raise RuntimeError('Gemini returned an empty response')
    return text


# ---------------------------------------------------------------------------
# Non-product message detector
# ---------------------------------------------------------------------------

_NON_PRODUCT_PATTERNS = [
    'location', 'address', 'where are you', 'map', 'direction',
    'hello', 'hi ', 'selam', 'thank', 'bye', 'checkout', 'check out',
    'order placed', 'payment', 'cart', 'total price', 'cost', 'basket',
]


def _is_non_product_message(text):
    lowered = text.lower()
    return any(kw in lowered for kw in _NON_PRODUCT_PATTERNS)


# ---------------------------------------------------------------------------
# Main chat route
# ---------------------------------------------------------------------------

@api_bp.route('/ai/chat', methods=['POST'])
def ai_chat():
    data = request.get_json()
    if not data or not data.get('message'):
        return error_response('Message required')

    user_message = data['message'].strip()
    session_id = data.get('session_id') or generate_session_id()
    channel = data.get('channel', 'web')
    user = _get_ai_user()
    user_id = user.id if user else None

    history = _get_conversation_history(session_id)
    recent_history_text = ' '.join(h.content for h in history[-6:])

    from app.blueprints.api.cart import _resolve_user, _cart_query
    cart_user, cart_session_id = _resolve_user()
    cart_items = _cart_query(cart_user, cart_session_id).all()
    cart_product_ids = [item.product_id for item in cart_items]

    # Check if customer named a specific product
    named_products = _find_product_by_name_in_message(user_message)

    # Get age/keyword-filtered candidates
    candidates = _get_all_candidate_products(user_message, recent_history_text, exclude_ids=cart_product_ids)

    # Merge named products into candidates (front of list)
    if named_products:
        named_ids = {p.id for p in named_products}
        candidates = named_products + [p for p in candidates if p.id not in named_ids]
        candidates = candidates[:10]

    is_product_req = _is_product_request(user_message)
    is_non_product = _is_non_product_message(user_message)

    # Build prompt and call Gemini
    prompt = _build_gemini_prompt(user_message, history, cart_items, candidates, named_products=named_products or None)
    try:
        assistant_reply = _call_gemini(prompt)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return error_response(f"AI Service Error: {str(e)}")

    # Determine which products to show as cards
    # RULE: only show what the AI actually mentioned in its reply, up to 3
    if is_non_product and not named_products and not is_product_req:
        final_products = []
    else:
        # Extract exactly the products the AI mentioned by name
        mentioned = _extract_mentioned_products(assistant_reply, candidates)
        if mentioned:
            final_products = mentioned[:3]
        elif named_products:
            # AI probably described them — show named products
            final_products = named_products[:3]
        elif is_product_req and candidates:
            # Fallback: show top 3 best-matching candidates
            final_products = candidates[:3]
        else:
            final_products = []

    _save_message(session_id, 'user', user_message, user_id, channel)
    _save_message(session_id, 'assistant', assistant_reply, user_id, channel)

    msg_lower = user_message.lower()
    is_cart_confirm = (
        ('i added' in msg_lower or 'added' in msg_lower[:15])
        and ('cart' in msg_lower or 'basket' in msg_lower)
    )
    is_checkout_intent = any(kw in msg_lower for kw in ['checkout', 'check out', 'pay now'])
    show_checkout = is_cart_confirm or is_checkout_intent

    cart_summary = None
    if show_checkout and cart_items:
        subtotal = sum(float(item.product.current_price()) * item.quantity for item in cart_items if item.product)
        delivery_fee = 0 if subtotal >= 1000 else 80
        cart_summary = {
            'items': [
                {'name': i.product.name, 'price': float(i.product.current_price()), 'qty': i.quantity}
                for i in cart_items if i.product
            ],
            'subtotal': subtotal,
            'delivery_fee': delivery_fee,
            'total': subtotal + delivery_fee,
        }

    return success_response({
        'message': assistant_reply,
        'session_id': session_id,
        'products': [p.to_dict() for p in final_products],
        'show_checkout_prompt': show_checkout and bool(cart_items),
        'cart_summary': cart_summary,
    })


# ---------------------------------------------------------------------------
# History routes
# ---------------------------------------------------------------------------

@api_bp.route('/ai/history', methods=['GET'])
def ai_history():
    session_id = request.args.get('session_id', '')
    if not session_id:
        return success_response([])
    history = _get_conversation_history(session_id, limit=20)
    return success_response([h.to_dict() for h in history])


@api_bp.route('/ai/history', methods=['DELETE'])
def clear_ai_history():
    session_id = request.args.get('session_id', '')
    if session_id:
        AIConversation.query.filter_by(session_id=session_id).delete()
        db.session.commit()
    return success_response(message='History cleared')


# ---------------------------------------------------------------------------
# Recommendations route
# ---------------------------------------------------------------------------

@api_bp.route('/ai/recommendations', methods=['GET'])
def ai_recommendations():
    user = _get_ai_user()
    if not user:
        products = Product.query.filter_by(is_active=True).order_by(Product.sales_count.desc()).limit(8).all()
        return success_response({'products': [p.to_dict() for p in products], 'reason': 'trending'})

    child_ages = user.get_child_ages()
    q = Product.query.filter_by(is_active=True)
    if child_ages:
        age_months = [a * 12 for a in child_ages]
        min_age = min(age_months)
        max_age = max(age_months)
        # Strict: product must overlap the child's age range
        q = q.filter(
            Product.age_min_months <= max_age + 6,
            Product.age_max_months >= min_age - 6,
        )
    products = q.order_by(Product.sales_count.desc()).limit(8).all()
    return success_response({
        'products': [p.to_dict() for p in products],
        'reason': 'age_based' if child_ages else 'popular',
    })
