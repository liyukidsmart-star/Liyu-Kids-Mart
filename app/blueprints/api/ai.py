import os
import json
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
    msgs = AIConversation.query.filter_by(session_id=session_id).filter(
        AIConversation.role.in_(['user', 'assistant'])
    ).order_by(AIConversation.created_at.desc()).limit(limit).all()
    return list(reversed(msgs))


def _save_message(session_id, role, content, user_id=None, channel='web'):
    msg = AIConversation(session_id=session_id, role=role, content=content,
                         user_id=user_id, channel=channel)
    db.session.add(msg)
    db.session.commit()


def _call_gemini(messages):
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key='AIzaSyAbOqNG7pf1bDHf2KA3TSkO2jwiG8o7sD0')
        
        system_instruction = ""
        gemini_history = []
        for m in messages:
            if m['role'] == 'system':
                system_instruction += m['content'] + "\n"
            elif m['role'] == 'assistant':
                gemini_history.append(types.Content(role="model", parts=[types.Part.from_text(text=m['content'])]))
            elif m['role'] == 'user':
                gemini_history.append(types.Content(role="user", parts=[types.Part.from_text(text=m['content'])]))
                
        last_user_msg = gemini_history.pop().parts[0].text
        
        # Start a chat session
        chat = client.chats.create(
            model='gemini-3.1-flash-lite',
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
            ),
            history=gemini_history
        )
        response = chat.send_message(last_user_msg)
        return response.text
    except Exception as e:
        return f"I'm having trouble connecting right now. Please try again in a moment! ({str(e)[:50]})"


SYSTEM_PROMPT = """You are Liyu, a warm and knowledgeable shopping guide at Liyu Kids Mart, Addis Ababa's best educational toy store. You feel like a real person — not an AI.

Personality & Tone:
- Speak naturally, warmly, and conversationally — like a trusted friend helping a parent.
- Match the customer's language EXACTLY. If they write in Amharic, respond fully in Amharic. If English, respond in English. Never mix languages unless they do.
- Use emojis occasionally but naturally. Never overdo it.
- Keep responses SHORT and helpful — under 100 words unless the customer asks for detail.
- Never say you are an AI, a bot, or "Grok-powered". You are Liyu, a person.
- NEVER use asterisks (*), underscores (_), or any markdown formatting.

When recommending products:
- Describe them in 1-2 warm sentences. Never list prices in your message — the product cards show all that.
- Tell the customer the cards are below so they can add what they like.
- When user says "I added X", celebrate warmly and ask if they need more or are ready to checkout.

STORE POLICIES:
- Delivery: FREE delivery for orders over 1000 ETB. Otherwise, delivery is 80 ETB.
- Payment Methods: We accept Telebirr, CBE (Commercial Bank of Ethiopia), and Cash on Delivery.
- Location: We are located in Bole Bulbula, 93 Mazoriya.
- Store Hours: We are open Monday to Saturday, 9:00 AM to 6:00 PM.

CRITICAL RULES:
- ONLY recommend products from the [SYSTEM CONTEXT - PRODUCTS] list. Never invent names or prices.
- NEVER mention items in cart unless [SYSTEM CONTEXT - CART] explicitly lists them.
- NEVER use markdown formatting."""


# Common toy keywords to detect intent
_PRODUCT_KEYWORDS = [
    'montessori', 'wooden', 'puzzle', 'book', 'art', 'music', 'building',
    'blocks', 'paint', 'sensory', 'stacking', 'shape', 'color', 'number',
    'letter', 'alphabet', 'animal', 'toy', 'game', 'doll', 'instrument',
    'reading', 'drawing', 'math', 'counting', 'abacus', 'flashcard'
]


def _detect_age_months_from_text(text):
    """Extract age in months from any text (handles years and months)."""
    import re
    age_months = []
    # e.g. "2 year old", "2-year-old", "2 years"
    for m in re.finditer(r'(\d+)\s*[-\s]?(?:year|yr)', text.lower()):
        age_months.append(int(m.group(1)) * 12)
    # e.g. "18 months", "6 month"
    for m in re.finditer(r'(\d+)\s*(?:month|mo)', text.lower()):
        age_months.append(int(m.group(1)))
    # Amharic digit patterns like "2 አመት" or "ሁለት"
    amharic_numbers = {'አንድ':1,'ሁለት':2,'ሶስት':3,'አራት':4,'አምስት':5,'ስድስት':6,'ሰባት':7,'ስምንት':8,'ዘጠኝ':9,'አስር':10}
    for word, num in amharic_numbers.items():
        if word in text:
            age_months.append(num * 12)
    return age_months


def _get_all_candidate_products(query_text, history_text='', exclude_ids=None):
    """
    Fetch a WIDE pool of candidate products based on age + keywords + price
    """
    exclude_ids = set(exclude_ids or [])
    combined = (query_text + ' ' + history_text).lower()
    
    # 1. Age extraction
    age_months_list = _detect_age_months_from_text(combined)
    min_age, max_age = None, None
    if age_months_list:
        min_age = max(0, min(age_months_list) - 3)
        max_age = max(age_months_list) + 12
    
    # 2. Price extraction
    import re
    max_price = None
    price_match = re.search(r'(?:under|below|less than|max(?:imum)?)\s*(\d+)', combined)
    if not price_match:
        price_match = re.search(r'(\d+)\s*(?:birr|etb|br)', combined)
        if price_match and ('under' in combined or 'cheap' in combined):
            max_price = int(price_match.group(1))
    else:
        max_price = int(price_match.group(1))

    # 3. Build base query
    q = Product.query.filter(Product.is_active == True)
    if max_price:
        q = q.filter(Product.price <= max_price)
    if min_age is not None and max_age is not None:
        q = q.filter(Product.age_min_months <= max_age, Product.age_max_months >= min_age)
    
    results = []
    seen = set()

    # 4. Keyword matches
    for kw in _PRODUCT_KEYWORDS:
        if kw in combined:
            kw_q = q.filter(db.or_(
                Product.name.ilike(f'%{kw}%'),
                Product.description.ilike(f'%{kw}%')
            )).all()
            for p in kw_q:
                if p.id not in seen and p.id not in exclude_ids:
                    results.append(p)
                    seen.add(p.id)
    
    # 5. Fallback
    if not results:
        all_p = q.order_by(Product.sales_count.desc()).limit(8).all()
        for p in all_p:
            if p.id not in exclude_ids:
                results.append(p)
    
    return results[:8]


def _match_products_from_reply(reply_text, candidates):
    """
    After Gemini replies, figure out which candidate products it actually mentioned.
    Uses word-level fuzzy matching so e.g. 'Pink Tower' matches 'Montessori Pink Tower'.
    Returns: (mentioned_products, other_products) both sorted by relevance.
    """
    reply_lower = reply_text.lower()
    mentioned = []
    others = []

    for p in candidates:
        # Score: count how many significant words from the product name appear in the reply
        words = [w for w in p.name.lower().split() if len(w) > 3]
        score = sum(1 for w in words if w in reply_lower)
        if score >= max(1, len(words) // 2):
            mentioned.append((score, p))
        else:
            others.append(p)

    # Sort mentioned by score desc
    mentioned.sort(key=lambda x: -x[0])
    return [p for _, p in mentioned], others


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

    # Build conversation history
    history = _get_conversation_history(session_id)
    recent_history_text = ' '.join(h.content for h in history[-6:])

    messages = [{'role': 'system', 'content': SYSTEM_PROMPT}]

    # Inject user profile if available
    if user and user.get_child_ages():
        ages = user.get_child_ages()
        age_context = f'\n\nThis customer has children aged: {", ".join(str(a) + " years" for a in ages)}. Tailor recommendations accordingly.'
        messages[0]['content'] += age_context

    for h in history[-8:]:
        messages.append({'role': h.role, 'content': h.content})

    # Inject Cart State
    from app.models.order import Cart
    from app.blueprints.api.cart import _resolve_user, _cart_query
    cart_user, cart_session_id = _resolve_user()
    cart_items = _cart_query(cart_user, cart_session_id).all()
    if cart_items:
        cart_context = "\n\n[SYSTEM CONTEXT - CART] The user currently has these items in their cart:\n"
        for item in cart_items:
            cart_context += f"- {item.quantity}x {item.product.name} (ETB {float(item.product.price):,.0f})\n"
        messages[0]['content'] += cart_context
    else:
        messages[0]['content'] += "\n\n[SYSTEM CONTEXT - CART] Cart is EMPTY. Do NOT mention any cart items."

    # PHASE 1: Fetch wide pool of age+keyword matched candidates
    cart_product_ids = [item.product_id for item in cart_items]
    candidates = _get_all_candidate_products(user_message, recent_history_text, exclude_ids=cart_product_ids)

    # Inject the FULL candidate list into the prompt so Gemini only recommends real products
    product_context = '\n\n[SYSTEM CONTEXT - PRODUCTS] These are ALL the real products in our store that match the customer. You MUST only recommend products from this list. Do NOT invent names or prices. Just mention them naturally — the product cards appear automatically below your reply:\n'
    for p in candidates:
        stock_note = f"In stock ({p.stock_qty} left)" if p.stock_qty > 0 else "Out of stock"
        product_context += f'- {p.name} | ETB {float(p.price):,.0f} | Age: {p.age_label()} | {stock_note}\n'
    messages[0]['content'] += product_context

    messages.append({'role': 'user', 'content': user_message})

    # Call Gemini
    assistant_reply = _call_gemini(messages)

    # PHASE 2: Only show products Gemini actually mentioned by name.
    mentioned, _others = _match_products_from_reply(assistant_reply, candidates)

    # Non-product intent guard: if the user asked a purely administrative question
    # and Gemini didn't mention any specific products, we hide the cards.
    non_product_patterns = [
        'location', 'address', 'where are you', 'map', 'direction',
        'hello', 'hi ', 'selam', 'thank', 'bye', 'checkout', 'check out',
        'order placed', 'payment', 'cart', 'total price', 'cost', 'basket'
    ]
    is_non_product = any(kw in user_message.lower() for kw in non_product_patterns)

    # If it's explicitly a non-product query AND no products were mentioned by the AI, hide cards.
    if is_non_product and not mentioned:
        final_products = []
    else:
        # Show mentioned products first, then fill with the remaining candidates.
        pool = mentioned + [p for p in candidates if p.id not in {x.id for x in mentioned}]
        final_products = pool[:6]  # frontend shows first 3, offers next 3 via button

    # Save conversation
    _save_message(session_id, 'user', user_message, user_id, channel)
    _save_message(session_id, 'assistant', assistant_reply, user_id, channel)

    # Detect if this is a cart confirmation (user just confirmed they added items)
    # Must contain "I added" or "to my cart" - NOT just asking about the cart
    msg_lower = user_message.lower()
    is_cart_confirm = (
        ('i added' in msg_lower or 'added' in msg_lower[:15]) and
        ('cart' in msg_lower or 'basket' in msg_lower)
    )
    # Detect if user explicitly wants to checkout
    is_checkout_intent = any(kw in msg_lower for kw in ['checkout', 'check out', 'pay now'])
    
    show_checkout = is_cart_confirm or is_checkout_intent
    
    cart_summary = None
    if show_checkout and cart_items:
        subtotal = sum(float(item.product.price) * item.quantity for item in cart_items if item.product)
        delivery_fee = 0 if subtotal >= 1000 else 80
        cart_summary = {
            'items': [{'name': i.product.name, 'price': float(i.product.price), 'qty': i.quantity} for i in cart_items if i.product],
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
        # Return popular products for anonymous users
        products = Product.query.filter_by(is_active=True).order_by(
            Product.sales_count.desc()).limit(8).all()
        return success_response({'products': [p.to_dict() for p in products], 'reason': 'trending'})

    # Personalized: age-based + category preference
    child_ages = user.get_child_ages()
    q = Product.query.filter_by(is_active=True)
    if child_ages:
        age_months = [a * 12 for a in child_ages]
        min_age = min(age_months)
        max_age = max(age_months)
        q = q.filter(Product.age_min_months <= max_age + 12,
                     Product.age_max_months >= min_age - 6)
    products = q.order_by(Product.sales_count.desc()).limit(8).all()
    return success_response({
        'products': [p.to_dict() for p in products],
        'reason': 'age_based' if child_ages else 'popular'
    })
