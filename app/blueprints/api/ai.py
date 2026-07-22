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


def _get_conversation_history(session_id, limit=20):
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
# Age detection – strict per-mention windows
# ---------------------------------------------------------------------------

def _detect_age_from_text(text):
    """
    Returns a dict with:
      - 'windows': list of (min_months, max_months) tuples
      - 'min': overall minimum months
      - 'max': overall maximum months
      - 'active': bool – True if any age was mentioned
    """
    windows = []
    text_lower = text.lower()

    # "X-Y year" range first (e.g. "2-4 years")
    for m in re.finditer(r'(\d+)\s*[-–]\s*(\d+)\s*(?:year|yr)', text_lower):
        y1, y2 = int(m.group(1)), int(m.group(2))
        windows.append((y1 * 12, y2 * 12))

    # Single "X year old / X yr old / X-year-old"
    for m in re.finditer(r'(\d+)\s*[-\s]?(?:year|yr)s?\s*(?:old)?', text_lower):
        years = int(m.group(1))
        # Tight ±6-month window around exact age
        windows.append((max(0, years * 12 - 6), years * 12 + 6))

    # "X months old / X-month-old"
    for m in re.finditer(r'(\d+)\s*[-\s]?(?:month|mo)s?\s*(?:old)?', text_lower):
        months = int(m.group(1))
        # ±3-month window
        windows.append((max(0, months - 3), months + 3))

    # Amharic number words (treated as years)
    amharic_numbers = {
        'አንድ': 1, 'ሁለት': 2, 'ሶስት': 3, 'አራት': 4, 'አምስት': 5,
        'ስድስት': 6, 'ሰባት': 7, 'ስምንት': 8, 'ዘጠኝ': 9, 'አስር': 10,
    }
    for word, num in amharic_numbers.items():
        if word in text:
            windows.append((max(0, num * 12 - 6), num * 12 + 6))

    if not windows:
        return {'windows': [], 'min': None, 'max': None, 'active': False}

    overall_min = min(w[0] for w in windows)
    overall_max = max(w[1] for w in windows)
    return {'windows': windows, 'min': overall_min, 'max': overall_max, 'active': True}


# ---------------------------------------------------------------------------
# Price detection – handles "4k", "4000 birr", "budget of X", etc.
# ---------------------------------------------------------------------------

def _detect_price_constraints(text):
    """
    Returns (min_price, max_price) or (None, None).
    Handles:
      - "under / below / less than X [birr/ETB]"
      - "X birr" with "under/cheap" nearby
      - "Xk" shorthand  (e.g. 4k → 4000)
      - "budget of X"
      - "between X and Y"
      - "above / more than X"
    """
    t = text.lower()

    # Normalise shorthand like 4k → 4000, 1.5k → 1500
    def expand_k(s):
        return re.sub(
            r'(\d+(?:\.\d+)?)\s*k\b',
            lambda m: str(int(float(m.group(1)) * 1000)),
            s
        )

    t = expand_k(t)

    min_price, max_price = None, None

    # "between X and Y"
    m = re.search(r'between\s+(\d+)\s+and\s+(\d+)', t)
    if m:
        min_price = int(m.group(1))
        max_price = int(m.group(2))
        return min_price, max_price

    # "under / below / less than / max / at most / budget of / no more than X"
    m = re.search(
        r'(?:under|below|less than|max(?:imum)?|at most|budget of|no more than|within)\s+(\d+)',
        t
    )
    if m:
        max_price = int(m.group(1))
    else:
        # "X birr/ETB/br" near budget keyword
        m = re.search(r'(\d+)\s*(?:birr|etb|br)', t)
        if m and any(kw in t for kw in ('under', 'below', 'cheap', 'affordable', 'budget')):
            max_price = int(m.group(1))

    # "above / over / more than / at least X"
    m2 = re.search(r'(?:above|over|more than|at least|minimum)\s+(\d+)', t)
    if m2:
        min_price = int(m2.group(1))

    return min_price, max_price


# ---------------------------------------------------------------------------
# Special-needs / context intent detection
# ---------------------------------------------------------------------------

# Maps contextual needs to product search keywords injected into the DB query
_CONTEXT_KEYWORD_MAP = {
    # Special needs
    'autism':        ['sensory', 'cause', 'sorting', 'montessori', 'stacking', 'calm', 'texture'],
    'autistic':      ['sensory', 'cause', 'sorting', 'montessori', 'stacking', 'calm', 'texture'],
    'adhd':          ['fidget', 'hands-on', 'tactile', 'sensory', 'building', 'active', 'movement'],
    'hyperactive':   ['active', 'movement', 'building', 'outdoor', 'kinetic'],
    'speech':        ['language', 'alphabet', 'flashcard', 'book', 'reading', 'letter', 'amharic'],
    'language':      ['alphabet', 'flashcard', 'book', 'reading', 'letter', 'amharic'],
    'fine motor':    ['lacing', 'threading', 'bead', 'puzzle', 'peg', 'montessori', 'sorting'],
    'gross motor':   ['active', 'outdoor', 'movement', 'balance', 'stacking'],
    'visual':        ['color', 'shape', 'puzzle', 'pattern', 'sorting'],

    # Screen-time / interaction
    'screen time':   ['wooden', 'offline', 'hands-on', 'puzzle', 'art', 'craft', 'building'],
    'screen':        ['wooden', 'offline', 'puzzle', 'art', 'craft', 'building'],
    'no screen':     ['wooden', 'offline', 'puzzle', 'art', 'craft', 'building'],
    'offline':       ['wooden', 'puzzle', 'art', 'craft', 'building'],

    # Social / multiplayer
    'two people':    ['game', 'interactive', 'cooperative', 'balancing', 'matching'],
    'together':      ['game', 'interactive', 'cooperative', 'balancing', 'matching'],
    'siblings':      ['game', 'interactive', 'cooperative', 'matching', 'building'],
    'multiplayer':   ['game', 'interactive', 'cooperative', 'matching'],
    'family':        ['game', 'interactive', 'building', 'art', 'matching'],
    'play with':     ['game', 'interactive', 'cooperative', 'building'],
    'cooperative':   ['game', 'interactive', 'cooperative', 'matching'],
    'interactive':   ['game', 'interactive', 'cooperative', 'balancing'],

    # Creative / imaginative
    'creative':      ['art', 'craft', 'paint', 'drawing', 'clay', 'building', 'blocks'],
    'creativity':    ['art', 'craft', 'paint', 'drawing', 'clay', 'building'],
    'imaginative':   ['doll', 'pretend', 'building', 'art', 'craft'],
    'art':           ['art', 'craft', 'paint', 'drawing', 'clay'],
    'drawing':       ['art', 'drawing', 'chalk', 'paint'],

    # Academic / learning
    'math':          ['math', 'counting', 'abacus', 'number', 'sorting', 'montessori'],
    'counting':      ['counting', 'abacus', 'number', 'math', 'montessori'],
    'reading':       ['book', 'alphabet', 'flashcard', 'letter', 'reading', 'amharic'],
    'amharic':       ['amharic', 'fidel', 'alphabet', 'book', 'letter'],
    'english':       ['alphabet', 'letter', 'book', 'flashcard', 'reading', 'english'],
    'science':       ['stem', 'science', 'experiment', 'discovery'],
    'stem':          ['stem', 'science', 'building', 'blocks', 'experiment'],

    # Physical / sensory
    'sensory':       ['sensory', 'texture', 'tactile', 'kinetic', 'sand', 'water', 'sorting'],
    'outdoor':       ['outdoor', 'active', 'movement', 'balance', 'sport'],
    'music':         ['music', 'xylophone', 'instrument', 'drum', 'piano', 'musical'],
    'musical':       ['music', 'xylophone', 'instrument', 'drum', 'piano'],
    'gift':          ['featured', 'popular', 'bestseller', 'wooden', 'educational'],
    'birthday':      ['featured', 'popular', 'bestseller', 'wooden', 'educational'],
}


def _extract_context_keywords(text):
    """
    Scan the message for special-context phrases and return expanded product search keywords.
    """
    text_lower = text.lower()
    extra_keywords = set()
    for phrase, kws in _CONTEXT_KEYWORD_MAP.items():
        if phrase in text_lower:
            extra_keywords.update(kws)
    return list(extra_keywords)


def _normalize_query_words(text):
    return re.findall(r'[\w\u1200-\u137f]+', text.lower())


def _product_name_match_score(product, text_lower):
    for name in (product.name, product.name_am):
        if not name:
            continue
        name_lower = name.lower().strip()
        if len(name_lower) > 8 and name_lower in text_lower:
            return 100

        words = [w for w in re.findall(r'[\w\u1200-\u137f]+', name_lower) if len(w) > 2]
        if not words:
            continue

        hit_count = sum(1 for w in words if w in text_lower)
        if hit_count >= max(1, len(words) - 1):
            return 50 + hit_count

    return 0


# ---------------------------------------------------------------------------
# Product name lookup in message
# ---------------------------------------------------------------------------

def _find_product_by_name_in_message(text):
    """
    If the customer explicitly mentions a product name that exists in our catalog,
    return those products (up to 3), ordered by match quality.
    """
    text_lower = text.lower()
    all_products = Product.query.filter_by(is_active=True).all()
    matches = []
    for p in all_products:
        score = _product_name_match_score(p, text_lower)
        if score:
            matches.append((score, p))

    matches.sort(key=lambda x: -x[0])
    return [p for _, p in matches[:3]]


# ---------------------------------------------------------------------------
# Core product keyword list (base)
# ---------------------------------------------------------------------------

_BASE_PRODUCT_KEYWORDS = [
    'montessori', 'wooden', 'puzzle', 'book', 'art', 'music', 'building',
    'blocks', 'paint', 'sensory', 'stacking', 'shape', 'color', 'number',
    'letter', 'alphabet', 'animal', 'toy', 'game', 'doll', 'instrument',
    'reading', 'drawing', 'math', 'counting', 'abacus', 'flashcard',
    'sorting', 'lacing', 'threading', 'bead', 'clay', 'craft', 'foam',
    'educational', 'learning', 'stem', 'science', 'outdoor', 'balance',
    'matching', 'cooperative', 'interactive', 'xylophone', 'fidget',
    'tactile', 'texture', 'amharic', 'english', 'fidel',
]

_AMHARIC_PRODUCT_HINTS = [
    'ምርት', 'ምርቶቹ', 'መጫወቻ', 'መጫወቻዎቹ', 'መጽሐፍ', 'ሞንቴሶሪ',
    'እንጨት', 'ፓዝሎ', 'ሙዚካ', 'ብሎክ', 'ቀለም', 'አሳይ', 'አለን',
]


def _is_amharic_text(text):
    return any('ሀ' <= ch <= '፿' for ch in text)


def _is_product_request(text):
    lowered = text.lower()
    hints = [
        'buy', 'find', 'search', 'looking for', 'show me', 'recommend', 'suggest',
        'toy', 'product', 'montessori', 'wooden', 'puzzle', 'blocks', 'book',
        'art', 'musical', 'educational', 'what do you have', 'do you sell',
        'age', 'years old', 'months old', 'gift', 'birthday', 'child', 'kid',
        'toddler', 'baby', 'infant', 'best for', 'good for', 'appropriate',
        'autism', 'adhd', 'screen time', 'interactive', 'sensory', 'creative',
        'affordable', 'budget', 'under', 'below',
    ]
    if any(h in lowered for h in hints):
        return True
    if _is_amharic_text(text) and any(h in text for h in _AMHARIC_PRODUCT_HINTS):
        return True
    return False


# ---------------------------------------------------------------------------
# Diversity enforcement – ensure candidates span different categories/types
# ---------------------------------------------------------------------------

def _diversify_candidates(products, target=9):
    """
    Reorder products so that the top results are from different categories.
    Ensures the AI has diverse options to pick 3 from.
    """
    if not products:
        return products

    by_category = {}
    for p in products:
        cat = p.category_id or 0
        by_category.setdefault(cat, []).append(p)

    result = []
    seen_cats = {}
    # Round-robin across categories
    queues = list(by_category.values())
    i = 0
    while len(result) < target and any(queues):
        q = queues[i % len(queues)]
        if q:
            result.append(q.pop(0))
        i += 1
        if not any(queues):
            break

    return result[:target]


# ---------------------------------------------------------------------------
# Candidate product retrieval – strict age + context + diversity
# ---------------------------------------------------------------------------

def _get_all_candidate_products(query_text, history_text='', exclude_ids=None):
    """
    Retrieve up to 9 diverse, age-appropriate, budget-matching products.

    Strategy:
    1. Detect age (strict window), price range, context keywords
    2. Build an age-filtered + price-filtered base query
    3. Score/match by keyword relevance
    4. If no age-matched products exist, return empty (do NOT fall back to wrong-age products)
    5. Diversify results across categories
    """
    exclude_ids = set(exclude_ids or [])
    combined = (query_text + ' ' + history_text).lower()
    combined_original = f'{query_text} {history_text}'

    # --- Age ---
    age_info = _detect_age_from_text(combined_original)

    # --- Price ---
    min_price, max_price = _detect_price_constraints(combined_original)

    # --- Context keywords ---
    context_kws = _extract_context_keywords(combined_original)
    all_keywords = list(set(_BASE_PRODUCT_KEYWORDS + context_kws))

    # --- Base query with age + price filters ---
    q = Product.query.filter(Product.is_active == True)
    if max_price:
        q = q.filter(Product.price <= max_price)
    if min_price:
        q = q.filter(Product.price >= min_price)

    age_filter_applied = False
    if age_info['active']:
        q = q.filter(
            Product.age_min_months <= age_info['max'],
            Product.age_max_months >= age_info['min'],
        )
        age_filter_applied = True

    results = []
    seen = set()

    # --- Explicit product name matches from the full text ---
    for p in _find_product_by_name_in_message(combined_original):
        if p.id not in seen and p.id not in exclude_ids:
            results.append(p)
            seen.add(p.id)

    # --- Keyword search within age/price-filtered space ---
    search_space = combined

    for kw in all_keywords:
        if kw in search_space:
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
            ).order_by(Product.sales_count.desc()).limit(12).all()
            for p in kw_q:
                if p.id not in seen and p.id not in exclude_ids:
                    results.append(p)
                    seen.add(p.id)

    # --- Amharic product name fallback (still age-filtered) ---
    if not results and _is_amharic_text(combined_original):
        am_q = q.filter(
            db.or_(
                Product.name_am.isnot(None),
                Product.description_am.isnot(None),
            )
        ).order_by(Product.sales_count.desc()).limit(12).all()
        for p in am_q:
            if p.id not in exclude_ids:
                results.append(p)

    # --- Age-only fallback (age specified, no keyword match) ---
    if not results and age_filter_applied:
        for p in q.order_by(Product.sales_count.desc()).limit(12).all():
            if p.id not in exclude_ids:
                results.append(p)

    # IMPORTANT: if age was specified but NOTHING matched, return empty.
    # This signals the AI that no products exist for that age — it should be
    # honest with the customer rather than recommend wrong-age products.
    if age_filter_applied and not results:
        return []

    # --- General fallback (no age, no keyword match, no context) ---
    if not results:
        base_q = Product.query.filter(Product.is_active == True)
        if max_price:
            base_q = base_q.filter(Product.price <= max_price)
        if min_price:
            base_q = base_q.filter(Product.price >= min_price)
        for p in base_q.order_by(Product.sales_count.desc()).limit(12).all():
            if p.id not in exclude_ids:
                results.append(p)

    # Diversify across categories so AI has a varied selection
    return _diversify_candidates(results, target=9)


# ---------------------------------------------------------------------------
# Extract products mentioned in AI reply (only these become cards)
# ---------------------------------------------------------------------------

def _extract_mentioned_products(reply_text, candidates):
    """
    Find which candidates the AI actually named in its reply.
    Multi-strategy: exact substring > word-overlap.
    Returns up to 3 in mention order.
    """
    reply_lower = reply_text.lower()
    mentioned = []
    seen_ids = set()

    for p in candidates:
        matched = False
        for name in (p.name, p.name_am):
            if not name:
                continue
            name_lower = name.lower().strip()

            # Strategy 1: exact substring
            if len(name_lower) > 4 and name_lower in reply_lower:
                mentioned.append((10, p))
                seen_ids.add(p.id)
                matched = True
                break

            # Strategy 2: high word overlap (≥ 2/3 of significant words)
            words = [w for w in re.findall(r'[\w\u1200-\u137f]+', name_lower) if len(w) > 2]
            if len(words) < 2:
                continue
            hit_count = sum(1 for w in words if w in reply_lower)
            threshold = max(2, (len(words) * 2 + 2) // 3)
            if hit_count >= threshold and p.id not in seen_ids:
                mentioned.append((hit_count, p))
                seen_ids.add(p.id)
                matched = True
                break

        if matched:
            continue

    mentioned.sort(key=lambda x: -x[0])
    return [p for _, p in mentioned[:3]]


# ---------------------------------------------------------------------------
# Build full product catalogue context for prompt
# ---------------------------------------------------------------------------

def _build_product_context_for_prompt(candidates, age_info, min_price, max_price, use_amharic_names=False):
    """
    Build the product list section of the prompt.
    Includes a note if candidates list is empty (no age match).
    """
    if not candidates:
        if age_info['active']:
            age_desc = _format_age_for_human(age_info['min'], age_info['max'])
            return (
                f'SYSTEM CONTEXT - PRODUCTS: We currently have no products specifically '
                f'for {age_desc}. Tell the customer warmly and honestly that you do not '
                f'have items for that exact age right now, and suggest they check back soon '
                f'or ask about a different age range.'
            )
        return 'SYSTEM CONTEXT - PRODUCTS: No matching products found for this request.'

    lines = []
    for p in candidates:
        display_name = p.name_am if use_amharic_names and p.name_am else p.name
        short_desc = (p.short_description_am if use_amharic_names and p.short_description_am else p.short_description or p.description or '')[:100].strip()
        stock_note = f'In stock' if p.stock_qty > 0 else 'Out of stock'
        category = p.category.name if p.category else ''
        line = (
            f'- {display_name}'
            f' | ETB {float(p.current_price()):,.0f}'
            f' | Age: {p.age_label()}'
            f' | Category: {category}'
            f' | {stock_note}'
        )
        if short_desc:
            line += f' | {short_desc}'
        lines.append(line)

    price_note = ''
    if max_price:
        price_note = f' (all are within the ETB {max_price:,} budget)'
    if min_price and max_price:
        price_note = f' (all are between ETB {min_price:,} and ETB {max_price:,})'

    header = f'SYSTEM CONTEXT - PRODUCTS{price_note} (choose EXACTLY 3 DIFFERENT types/categories to recommend):'
    return header + '\n' + '\n'.join(lines)


def _format_age_for_human(min_months, max_months):
    def fmt(m):
        if m < 12:
            return f'{m} months'
        y = m // 12
        r = m % 12
        return f'{y} year{"s" if y != 1 else ""}' + (f' {r}m' if r else '')
    if min_months == max_months:
        return fmt(min_months)
    return f'{fmt(min_months)} to {fmt(max_months)}'


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are Liyu, a warm and knowledgeable shopping guide at Liyu Kids Mart, Addis Ababa's best educational toy store. You feel like a real person - not an AI.

Personality and tone:
- Speak naturally, warmly, and conversationally - like a trusted friend helping a parent.
- Match the customer's language exactly. If they write in Amharic, respond fully in Amharic. If English, respond in English. Never mix languages unless they do.
- Use emojis occasionally but naturally. Never overdo it.
- Keep responses under 150 words unless the customer asks for more detail.
- Never say you are an AI, a bot, or mention any technology behind you. You are Liyu, a person.
- Never use asterisks (*), underscores (_), or markdown formatting.

CRITICAL PRODUCT RECOMMENDATION RULES:
1. When asked for a recommendation, you MUST recommend EXACTLY 3 products from [SYSTEM CONTEXT - PRODUCTS].
2. The 3 products MUST be DIFFERENT types - do not recommend 3 stacking rings or 3 puzzles. Aim for variety (e.g. one sensory toy, one learning toy, one creative toy).
3. Name each product EXACTLY as it appears in the product list - no paraphrasing or shortening.
4. For each product, mention the age range (e.g. "perfect for 2-4 year olds") and one reason why it is great.
5. End with: "The product cards are below - tap one to add it to your cart!"
6. If a customer asks about a specific product by name, find it in [SYSTEM CONTEXT - PRODUCTS], confirm it warmly, describe it with its age range and main benefit.
7. NEVER recommend a product not in [SYSTEM CONTEXT - PRODUCTS]. NEVER invent product names.
8. If [SYSTEM CONTEXT - PRODUCTS] says there are no matching products, tell the customer honestly and warmly.

AGE RULES (very important):
- ONLY recommend products whose age range includes the age the customer mentioned.
- If the customer says "6 months old", only recommend products labelled for 0-12 months.
- If the customer says "3 years old", only recommend products labelled for 2-4 years range.
- NEVER recommend a product for a baby to a parent of a school-age child or vice versa.
- If no products match the age, say so honestly and suggest checking back or trying a nearby age.

BUDGET RULES:
- If the customer mentioned a budget, only recommend products within that budget.
- Never suggest a product that costs more than the customer's stated budget.

DIVERSITY RULE:
- Recommend products from different categories when possible (e.g. one building toy, one music toy, one book/puzzle).

Store policies:
- Delivery: FREE delivery for orders over 1000 ETB. Otherwise, delivery is 80 ETB.
- Payment: Telebirr, CBE (Commercial Bank of Ethiopia), Cash on Delivery.
- Location: Bole Bulbula, 93 Mazoriya, Addis Ababa.
- Hours: Monday to Saturday, 9:00 AM to 6:00 PM.

Critical rules:
- Only recommend products from [SYSTEM CONTEXT - PRODUCTS]. Never invent names or prices.
- Never mention cart items unless [SYSTEM CONTEXT - CART] explicitly lists them.
- Never use markdown formatting."""


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_gemini_prompt(user_message, history, cart_items, candidates,
                         named_products=None, age_info=None, min_price=None, max_price=None,
                         conversation_context=None):
    parts = [SYSTEM_PROMPT]

    if conversation_context:
        parts.append('Full conversation history:\n' + conversation_context)
    elif history:
        history_lines = []
        for item in history:
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
        use_amharic_names = _is_amharic_text(user_message)
        named_lines = []
        for p in named_products:
            display_name = p.name_am if use_amharic_names and p.name_am else p.name
            desc = (p.short_description_am if use_amharic_names and p.short_description_am else p.short_description or p.description or '')[:150]
            stock_note = 'In stock' if p.stock_qty > 0 else 'Out of stock'
            named_lines.append(
                f'- {display_name} | ETB {float(p.current_price()):,.0f} | Age: {p.age_label()} | {stock_note} | {desc}'
            )
        parts.append(
            'SYSTEM CONTEXT - CUSTOMER ASKED ABOUT THESE PRODUCTS (describe them warmly, include age and benefit):\n'
            + '\n'.join(named_lines)
        )

    age_info = age_info or {'active': False, 'min': None, 'max': None}
    use_amharic_names = _is_amharic_text(user_message)
    product_context = _build_product_context_for_prompt(
        candidates, age_info, min_price, max_price,
        use_amharic_names=use_amharic_names,
    )
    parts.append(product_context)

    parts.append('User message:\n' + user_message)
    return '\n\n'.join(parts)


# ---------------------------------------------------------------------------
# Gemini API call
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
            max_output_tokens=450,
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
    'hello', 'hi there', 'selam', 'thank', 'bye', 'checkout', 'check out',
    'order placed', 'payment method', 'cart total', 'delivery fee',
]


def _is_non_product_message(text):
    lowered = text.lower()
    # It is non-product only if it matches a non-product pattern AND does not
    # mention any age or product keyword (to avoid false positives like "thank you,
    # now show me toys for 3 year old")
    if not any(kw in lowered for kw in _NON_PRODUCT_PATTERNS):
        return False
    # If the message also has product intent signals, it's still a product request
    product_signals = ['toy', 'age', 'year', 'month', 'suggest', 'recommend', 'show', 'find']
    return not any(sig in lowered for sig in product_signals)


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
    recent_history_text = ' '.join(h.content for h in history)
    conversation_context = '\n'.join(
        f"{('User' if item.role == 'user' else 'Assistant')}: {item.content}"
        for item in history
    )

    from app.blueprints.api.cart import _resolve_user, _cart_query
    cart_user, cart_session_id = _resolve_user()
    cart_items = _cart_query(cart_user, cart_session_id).all()
    cart_product_ids = [item.product_id for item in cart_items]

    # Parse age and price from full context
    combined_text = f'{user_message} {recent_history_text}'
    age_info = _detect_age_from_text(combined_text)
    min_price, max_price = _detect_price_constraints(combined_text)

    # Check if the customer explicitly named a product, including references from recent conversation history
    named_products = _find_product_by_name_in_message(combined_text)

    # Fetch age/price/context-filtered candidates
    candidates = _get_all_candidate_products(
        user_message, recent_history_text, exclude_ids=cart_product_ids
    )

    # Merge named products into candidates (front of list, no duplicates)
    if named_products:
        named_ids = {p.id for p in named_products}
        candidates = named_products + [p for p in candidates if p.id not in named_ids]
        candidates = candidates[:9]

    # Context awareness: treat follow-up questions as product requests when recent history contains product references
    is_product_req = _is_product_request(combined_text)
    is_non_product = _is_non_product_message(user_message)

    # Build and send prompt to Gemini
    prompt = _build_gemini_prompt(
        user_message, history, cart_items, candidates,
        named_products=named_products or None,
        age_info=age_info,
        min_price=min_price,
        max_price=max_price,
        conversation_context=conversation_context,
    )
    try:
        assistant_reply = _call_gemini(prompt)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return error_response(f"AI Service Error: {str(e)}")

    # Determine product cards: ONLY what the AI actually mentioned, max 3
    if is_non_product and not named_products and not is_product_req:
        final_products = []
    else:
        mentioned = _extract_mentioned_products(assistant_reply, candidates)
        if mentioned:
            final_products = mentioned[:3]
        elif named_products:
            # AI described specific products – show them
            final_products = named_products[:3]
        elif is_product_req and candidates:
            # Fallback: show top 3 diverse candidates
            final_products = candidates[:3]
        else:
            final_products = []

    _save_message(session_id, 'user', user_message, user_id, channel)
    _save_message(session_id, 'assistant', assistant_reply, user_id, channel)

    # Track AI-suggested products in ActivityLog for analytics
    if final_products:
        try:
            from app.models.ai_conversation import ActivityLog
            import json as _json
            for fp in final_products:
                log = ActivityLog(
                    user_id=user_id,
                    action='ai_suggested_product',
                    entity_type='product',
                    entity_id=fp.id,
                    meta=_json.dumps({'session_id': session_id, 'channel': channel}),
                )
                db.session.add(log)
            db.session.commit()
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass

    msg_lower = user_message.lower()
    is_cart_confirm = (
        ('i added' in msg_lower or 'added' in msg_lower[:15])
        and ('cart' in msg_lower or 'basket' in msg_lower)
    )
    is_checkout_intent = any(kw in msg_lower for kw in ['checkout', 'check out', 'pay now'])
    show_checkout = is_cart_confirm or is_checkout_intent

    cart_summary = None
    if show_checkout and cart_items:
        subtotal = sum(
            float(item.product.current_price()) * item.quantity
            for item in cart_items if item.product
        )
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
        products = (
            Product.query.filter_by(is_active=True)
            .order_by(Product.sales_count.desc())
            .limit(8).all()
        )
        return success_response({'products': [p.to_dict() for p in products], 'reason': 'trending'})

    child_ages = user.get_child_ages()
    q = Product.query.filter_by(is_active=True)
    if child_ages:
        age_months = [a * 12 for a in child_ages]
        min_age = min(age_months)
        max_age = max(age_months)
        # Strict overlap: ±6 months only
        q = q.filter(
            Product.age_min_months <= max_age + 6,
            Product.age_max_months >= max(0, min_age - 6),
        )
    products = q.order_by(Product.sales_count.desc()).limit(8).all()
    return success_response({
        'products': [p.to_dict() for p in products],
        'reason': 'age_based' if child_ages else 'popular',
    })
