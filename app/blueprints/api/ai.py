import os
from flask import request
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity
from app.blueprints.api import api_bp
from app.extensions import db
from app.models.ai_conversation import AIConversation
from app.models.user import User
from app.models.product import Product
from app.utils import success_response, error_response, generate_session_id


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


def _detect_age_months_from_text(text):
    import re

    age_months = []
    for m in re.finditer(r'(\d+)\s*[-\s]?(?:year|yr)', text.lower()):
        age_months.append(int(m.group(1)) * 12)
    for m in re.finditer(r'(\d+)\s*(?:month|mo)', text.lower()):
        age_months.append(int(m.group(1)))

    amharic_numbers = {
        'አንድ': 1, 'ሁለት': 2, 'ሶስት': 3, 'አራት': 4, 'አምስት': 5,
        'ስድስት': 6, 'ሰባት': 7, 'ስምንት': 8, 'ዘጠኝ': 9, 'አስር': 10,
    }
    for word, num in amharic_numbers.items():
        if word in text:
            age_months.append(num * 12)
    return age_months


_PRODUCT_KEYWORDS = [
    'montessori', 'wooden', 'puzzle', 'book', 'art', 'music', 'building',
    'blocks', 'paint', 'sensory', 'stacking', 'shape', 'color', 'number',
    'letter', 'alphabet', 'animal', 'toy', 'game', 'doll', 'instrument',
    'reading', 'drawing', 'math', 'counting', 'abacus', 'flashcard',
]


def _get_all_candidate_products(query_text, history_text='', exclude_ids=None):
    exclude_ids = set(exclude_ids or [])
    combined = (query_text + ' ' + history_text).lower()

    age_months_list = _detect_age_months_from_text(combined)
    min_age, max_age = None, None
    if age_months_list:
        min_age = max(0, min(age_months_list) - 3)
        max_age = max(age_months_list) + 12

    import re
    max_price = None
    price_match = re.search(r'(?:under|below|less than|max(?:imum)?)\s*(\d+)', combined)
    if not price_match:
        price_match = re.search(r'(\d+)\s*(?:birr|etb|br)', combined)
        if price_match and ('under' in combined or 'cheap' in combined):
            max_price = int(price_match.group(1))
    else:
        max_price = int(price_match.group(1))

    q = Product.query.filter(Product.is_active == True)
    if max_price:
        q = q.filter(Product.price <= max_price)
    if min_age is not None and max_age is not None:
        q = q.filter(Product.age_min_months <= max_age, Product.age_max_months >= min_age)

    results = []
    seen = set()
    for kw in _PRODUCT_KEYWORDS:
        if kw in combined:
            kw_q = q.filter(
                db.or_(
                    Product.name.ilike(f'%{kw}%'),
                    Product.description.ilike(f'%{kw}%'),
                )
            ).all()
            for p in kw_q:
                if p.id not in seen and p.id not in exclude_ids:
                    results.append(p)
                    seen.add(p.id)

    if not results:
        for p in q.order_by(Product.sales_count.desc()).limit(8).all():
            if p.id not in exclude_ids:
                results.append(p)

    return results[:8]


def _match_products_from_reply(reply_text, candidates):
    reply_lower = reply_text.lower()
    mentioned = []
    others = []

    for p in candidates:
        words = [w for w in p.name.lower().split() if len(w) > 3]
        score = sum(1 for w in words if w in reply_lower)
        if score >= max(1, len(words) // 2):
            mentioned.append((score, p))
        else:
            others.append(p)

    mentioned.sort(key=lambda x: -x[0])
    return [p for _, p in mentioned], others


SYSTEM_PROMPT = """You are Liyu, a warm and knowledgeable shopping guide at Liyu Kids Mart, Addis Ababa's best educational toy store. You feel like a real person - not an AI.

Personality and tone:
- Speak naturally, warmly, and conversationally - like a trusted friend helping a parent.
- Match the customer's language exactly. If they write in Amharic, respond fully in Amharic. If English, respond in English. Never mix languages unless they do.
- Use emojis occasionally but naturally. Never overdo it.
- Keep responses short and helpful - under 100 words unless the customer asks for detail.
- Never say you are an AI, a bot, or "Grok-powered". You are Liyu, a person.
- Never use asterisks (*), underscores (_), or markdown formatting.

When recommending products:
- Describe them in 1-2 warm sentences. Never list prices in your message - the product cards show all that.
- Tell the customer the cards are below so they can add what they like.
- When the user says "I added X", celebrate warmly and ask if they need more or are ready to checkout.

Store policies:
- Delivery: FREE delivery for orders over 1000 ETB. Otherwise, delivery is 80 ETB.
- Payment methods: We accept Telebirr, CBE (Commercial Bank of Ethiopia), and Cash on Delivery.
- Location: We are located in Bole Bulbula, 93 Mazoriya.
- Store hours: We are open Monday to Saturday, 9:00 AM to 6:00 PM.

Critical rules:
- Only recommend products from the [SYSTEM CONTEXT - PRODUCTS] list. Never invent names or prices.
- Never mention items in cart unless [SYSTEM CONTEXT - CART] explicitly lists them.
- Never use markdown formatting."""


def _build_gemini_prompt(user_message, history, cart_items, candidates):
    parts = [SYSTEM_PROMPT]

    if history:
        history_lines = []
        for item in history[-6:]:
            label = 'User' if item.role == 'user' else 'Assistant'
            history_lines.append(f'{label}: {item.content}')
        parts.append('Conversation history:\n' + '\n'.join(history_lines))

    if cart_items:
        cart_lines = [
            f"- {item.quantity}x {item.product.name} (ETB {float(item.product.price):,.0f})"
            for item in cart_items
        ]
        parts.append('SYSTEM CONTEXT - CART:\n' + '\n'.join(cart_lines))
    else:
        parts.append('SYSTEM CONTEXT - CART: Cart is EMPTY. Do NOT mention any cart items.')

    if candidates:
        product_lines = []
        for p in candidates:
            stock_note = f'In stock ({p.stock_qty} left)' if p.stock_qty > 0 else 'Out of stock'
            product_lines.append(f'- {p.name} | ETB {float(p.price):,.0f} | Age: {p.age_label()} | {stock_note}')
        parts.append('SYSTEM CONTEXT - PRODUCTS:\n' + '\n'.join(product_lines))

    parts.append('User message:\n' + user_message)
    return '\n\n'.join(parts)

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
            max_output_tokens=350,
        ),
    )
    text = (getattr(response, 'text', '') or '').strip()
    if not text:
        raise RuntimeError('Gemini returned an empty response')
    return text


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
    candidates = _get_all_candidate_products(user_message, recent_history_text, exclude_ids=cart_product_ids)

    prompt = _build_gemini_prompt(user_message, history, cart_items, candidates)
    try:
        assistant_reply = _call_gemini(prompt)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return error_response(f"AI Service Error: {str(e)}")

    mentioned, _others = _match_products_from_reply(assistant_reply, candidates)

    non_product_patterns = [
        'location', 'address', 'where are you', 'map', 'direction',
        'hello', 'hi ', 'selam', 'thank', 'bye', 'checkout', 'check out',
        'order placed', 'payment', 'cart', 'total price', 'cost', 'basket',
    ]
    is_non_product = any(kw in user_message.lower() for kw in non_product_patterns)

    if is_non_product and not mentioned:
        final_products = []
    else:
        pool = mentioned + [p for p in candidates if p.id not in {x.id for x in mentioned}]
        final_products = pool[:6]

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
        subtotal = sum(float(item.product.price) * item.quantity for item in cart_items if item.product)
        delivery_fee = 0 if subtotal >= 1000 else 80
        cart_summary = {
            'items': [
                {'name': i.product.name, 'price': float(i.product.price), 'qty': i.quantity}
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
        q = q.filter(Product.age_min_months <= max_age + 12, Product.age_max_months >= min_age - 6)
    products = q.order_by(Product.sales_count.desc()).limit(8).all()
    return success_response({
        'products': [p.to_dict() for p in products],
        'reason': 'age_based' if child_ages else 'popular',
    })
