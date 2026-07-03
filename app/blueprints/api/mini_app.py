"""
Mini App dedicated API endpoints for Liyu Kids Mart Telegram Mini App.
Handles in-app checkout, orders, wishlist, and receipt uploads for Telegram users.
"""
import json
import os
from flask import request
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity
from app.blueprints.api import api_bp
from app.extensions import db
from app.models.product import Product
from app.models.order import (Cart, Order, OrderItem, Address,
                               OrderStatus, PaymentMethod)
from app.models.user import User, UserRole
from app.utils import success_response, error_response, generate_order_number
from app.services.order_notifications import notify_store_managers


def _resolve_mini_app_user():
    """Resolve user from JWT or telegram_id in request body."""
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


@api_bp.route('/mini-app/checkout', methods=['POST'])
def mini_app_checkout():
    """
    Place an order directly from the Telegram Mini App.
    Accepts cart items array from the Mini App's localStorage cart
    and delivery details, creates the order in the database.
    """
    user = _resolve_mini_app_user()
    data = request.get_json(silent=True) or {}

    # Cart items passed from mini app frontend
    cart_items_data = data.get('cart_items', [])
    delivery = data.get('delivery', {})
    payment_method_str = data.get('payment_method', 'cod')
    telegram_id = data.get('telegram_id')
    payment_receipt_url = data.get('payment_receipt_url', '')

    if not cart_items_data:
        return error_response('Cart is empty', 400)

    if not delivery.get('phone') or not delivery.get('specific_location'):
        return error_response('Phone number and delivery location are required', 400)

    # Auto-create user if we have telegram_id but no user yet
    if not user and telegram_id:
        full_name = data.get('full_name', 'Customer')
        username = data.get('telegram_username', '')
        user = User.query.filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            user = User(
                telegram_id=str(telegram_id),
                telegram_username=username,
                full_name=full_name
            )
            db.session.add(user)
            db.session.flush()

    if not user:
        return error_response('Unable to identify user', 401)

    # Validate products and build totals
    order_items = []
    subtotal = 0.0

    for ci in cart_items_data:
        product_id = ci.get('id') or ci.get('product_id')
        qty = int(ci.get('qty', ci.get('quantity', 1)))
        product = db.session.get(Product, product_id)
        if not product or not product.is_active:
            continue
        if product.stock_qty < qty:
            qty = product.stock_qty
        if qty <= 0:
            continue
        item_total = float(product.current_price()) * qty
        subtotal += item_total
        order_items.append({
            'product': product,
            'qty': qty,
            'unit_price': float(product.current_price()),
            'item_total': item_total
        })

    if not order_items:
        return error_response('No valid items in cart', 400)

    d_fee = delivery.get('delivery_fee')
    delivery_fee = float(d_fee) if d_fee is not None and str(d_fee).strip() != '' else 80.0

    # ── Calculate Loyalty Discount (tier-gated) ──────────────────
    from app.services.loyalty_service import calculate_loyalty_discount, process_order_rewards
    total_items = sum(oi['qty'] for oi in order_items)
    user._cart_item_count = total_items
    discount_info = calculate_loyalty_discount(user, subtotal)
    discount_amount = round(discount_info.get('total_discount_amount', 0.0), 2)
    total = round(subtotal - discount_amount + delivery_fee, 2)

    # Map payment method
    pm_map = {
        'cod': PaymentMethod.cod,
        'telebirr': PaymentMethod.telebirr,
        'chapa': PaymentMethod.chapa,
    }
    payment_method = pm_map.get(payment_method_str, PaymentMethod.cod)

    # Create address (with lat/lng from map pin)
    addr = Address(
        user_id=user.id,
        recipient_name=delivery.get('name', user.full_name or 'Customer'),
        phone=delivery.get('phone', ''),
        city=delivery.get('city', 'Addis Ababa'),
        sub_city=delivery.get('sub_city', ''),
        woreda=delivery.get('woreda', ''),
        specific_location=delivery.get('specific_location', ''),
        lat=delivery.get('lat'),
        lng=delivery.get('lng'),
    )
    db.session.add(addr)
    db.session.flush()

    # Build notes — include receipt URL for TeleBirr
    notes = data.get('notes', 'Placed via Telegram Mini App')
    if payment_receipt_url and payment_method_str == 'telebirr':
        notes = f"{notes} | TeleBirr Receipt: {payment_receipt_url}"

    # Create order
    order_number = generate_order_number()
    order = Order(
        user_id=user.id,
        order_number=order_number,
        status=OrderStatus.pending,
        subtotal=subtotal,
        delivery_fee=delivery_fee,
        discount_amount=discount_amount,
        total=total,
        payment_method=payment_method,
        payment_status='pending',
        notes=notes,
        address_id=addr.id,
    )
    db.session.add(order)
    db.session.flush()

    # Create order items and update stock
    for oi in order_items:
        product = oi['product']
        order_item = OrderItem(
            order_id=order.id,
            product_id=product.id,
            quantity=oi['qty'],
            unit_price=oi['unit_price'],
            total_price=oi['item_total'],
            product_snapshot=json.dumps({
                'name': product.name,
                'price': oi['unit_price'],
                'image': product.primary_image(),
            })
        )
        db.session.add(order_item)
        product.stock_qty = max(0, product.stock_qty - oi['qty'])
        product.sales_count = (product.sales_count or 0) + oi['qty']

    # Also clear any server-side cart items for this user
    Cart.query.filter_by(user_id=user.id).delete()

    # Process loyalty rewards (update user's tier, points, savings)
    try:
        process_order_rewards(user, order, savings_amount=discount_amount)
    except Exception:
        pass

    import logging
    _logger = logging.getLogger(__name__)

    # ── Commit everything to the database ────────────────────────
    try:
        _logger.info(f'[checkout] Committing order {order_number} for user_id={user.id} telegram_id={user.telegram_id}')
        db.session.commit()
        _logger.info(f'[checkout] Order {order_number} committed successfully. id={order.id}')
    except Exception as db_exc:
        _logger.error(f'[checkout] DB COMMIT FAILED for order {order_number}: {db_exc}', exc_info=True)
        db.session.rollback()
        return error_response(f'Failed to save order: {str(db_exc)}', 500)

    # ── Notify store managers ─────────────────────────────────────
    try:
        notify_store_managers(order, order_items, addr, payment_method_str, discount_amount, payment_receipt_url)
    except Exception as exc:
        _logger.error(f'[order_notify] Failed to notify managers: {exc}', exc_info=True)

    return success_response({
        'order_number': order_number,
        'total': total,
        'delivery_fee': delivery_fee,
        'subtotal': subtotal,
        'discount_amount': discount_amount,
        'items_count': len(order_items),
        'payment_method': payment_method_str,
        'message': f'Order #{order_number} placed successfully! We will confirm shortly.'
    }, 'Order placed successfully')


def _notify_store_managers(order, order_items, addr, payment_method_str, discount_amount, payment_receipt_url=''):
    """Send rich order notification to all store managers via Telegram."""
    import httpx as _httpx
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    app_url = os.getenv('APP_URL', 'http://localhost:5000')
    if not token:
        return

    # 1. Collect manager Telegram IDs from the database
    manager_ids = set()
    try:
        db_managers = User.query.filter(
            User.role.in_([UserRole.admin, UserRole.manager]),
            User.telegram_id.isnot(None),
            User.is_active == True,
        ).all()
        for m in db_managers:
            if m.telegram_id and m.telegram_id.strip():
                manager_ids.add(m.telegram_id.strip())
    except Exception:
        pass

    # 2. Also add IDs from environment variable as fallback
    manager_ids_raw = os.getenv('MANAGER_TG_IDS', '')
    for mid in manager_ids_raw.split(','):
        mid = mid.strip()
        if mid:
            manager_ids.add(mid)
            
    # 3. Explicitly add the requested managers
    manager_ids.add('661528493')
    manager_ids.add('401413271')

    if not manager_ids:
        return

    # Build items text
    items_lines = []
    for oi in order_items:
        p = oi['product']
        items_lines.append(
            f"  • <b>{p.name[:40]}</b>  ×{oi['qty']}  —  <b>ETB {oi['item_total']:,.0f}</b>"
        )
    items_text = '\n'.join(items_lines)

    pm_labels = {
        'cod':      '💵 Cash on Delivery',
        'telebirr': '📱 TeleBirr',
        'chapa':    '💳 Chapa',
    }
    pm_label = pm_labels.get(payment_method_str, payment_method_str.upper())
    subtotal = float(order.subtotal)
    delivery_fee = float(order.delivery_fee)
    total = float(order.total)

    store_url = f'{app_url}/telegram/store-app'

    # Google Maps link for the delivery location
    maps_link = ''
    if addr.lat and addr.lng:
        maps_link = f'\n🗺 <a href="https://maps.google.com/?q={addr.lat},{addr.lng}">View on Map</a>'

    discount_line = f'\n🎁 <b>Discount:</b>  -ETB {discount_amount:,.0f}'

    receipt_line = ''
    if payment_receipt_url and payment_method_str == 'telebirr':
        receipt_line = f'\n🧾 <a href="{payment_receipt_url}">View TeleBirr Receipt</a>'

    msg = (
        f"🛍️ <b>NEW ORDER #{order.order_number}</b>\n\n"
        f"👤 <b>Customer:</b>  {order.user.full_name or 'Customer'}\n"
        f"📞 <b>Phone:</b>  {addr.phone}\n"
        f"📍 <b>Location:</b>  {addr.specific_location or 'Not specified'}{maps_link}\n\n"
        f"📦 <b>Items:</b>\n{items_text}\n\n"
        f"💰 <b>Subtotal:</b>  ETB {subtotal:,.0f}"
        f"{discount_line}\n"
        f"🚚 <b>Delivery:</b>  ETB {delivery_fee:,.0f}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💳 <b>TOTAL:  ETB {total:,.0f}</b>\n"
        f"💳 <b>Payment:</b>  {pm_label}"
        f"{receipt_line}"
    )

    reply_markup = {
        'inline_keyboard': [[
            {'text': '🤖 Open Bot', 'url': 'https://t.me/Liyu_Kids_Mart_Bot'},
            {'text': '🌐 Open Store Portal', 'url': store_url}
        ]]
    }

    import logging
    _logger = logging.getLogger(__name__)

    for manager_id in manager_ids:
        try:
            resp = _httpx.post(
                f'https://api.telegram.org/bot{token}/sendMessage',
                json={
                    'chat_id': manager_id,
                    'text': msg,
                    'parse_mode': 'HTML',
                    'disable_web_page_preview': True,
                    'reply_markup': reply_markup,
                },
                timeout=8
            )
            resp.raise_for_status()
        except Exception as e:
            _logger.error(f'[order_notify] Failed to send to {manager_id}: {e}', exc_info=True)


@api_bp.route('/mini-app/upload-receipt', methods=['POST'])
def mini_app_upload_receipt():
    """
    Upload a TeleBirr payment receipt image.
    The image is forwarded to the Telegram media channel and the file_id is returned.
    """
    telegram_id = request.form.get('telegram_id') or (request.get_json(silent=True) or {}).get('telegram_id')
    if 'receipt' not in request.files:
        return error_response('No receipt file provided', 400)

    file = request.files['receipt']
    if not file or not file.filename:
        return error_response('Invalid file', 400)

    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    media_channel_id = os.getenv('TELEGRAM_MEDIA_CHAT_ID', '')

    if not token or not media_channel_id:
        return error_response('Media service not configured', 503)

    import httpx as _httpx
    try:
        file_bytes = file.read()
        filename = file.filename or 'receipt.jpg'
        resp = _httpx.post(
            f'https://api.telegram.org/bot{token}/sendPhoto',
            data={'chat_id': media_channel_id, 'caption': f'TeleBirr receipt from tg:{telegram_id}'},
            files={'photo': (filename, file_bytes, file.content_type or 'image/jpeg')},
            timeout=30,
        )
        data = resp.json()
        if data.get('ok'):
            # Extract the largest photo file_id
            photos = data['result'].get('photo', [])
            if photos:
                file_id = photos[-1]['file_id']
                from app.services.image_delivery import media_url_for_file_id
                receipt_url = media_url_for_file_id(file_id)
                return success_response({'receipt_url': receipt_url})
        return error_response('Failed to upload receipt', 500)
    except Exception as e:
        return error_response(f'Upload error: {str(e)}', 500)


@api_bp.route('/mini-app/wishlist', methods=['GET'])
def mini_app_wishlist():
    """Get wishlist product IDs for a telegram user."""
    telegram_id = request.args.get('telegram_id')
    if not telegram_id:
        return success_response({'product_ids': []})
    user = User.query.filter_by(telegram_id=str(telegram_id)).first()
    if not user:
        return success_response({'product_ids': []})
    from app.models.order import Wishlist
    items = Wishlist.query.filter_by(user_id=user.id).all()
    return success_response({'product_ids': [i.product_id for i in items]})


@api_bp.route('/mini-app/wishlist/toggle', methods=['POST'])
def mini_app_wishlist_toggle():
    """Toggle a product in/out of wishlist for a telegram user."""
    data = request.get_json(silent=True) or {}
    telegram_id = data.get('telegram_id')
    product_id = data.get('product_id')

    if not telegram_id or not product_id:
        return error_response('telegram_id and product_id required', 400)

    user = User.query.filter_by(telegram_id=str(telegram_id)).first()
    if not user:
        # Auto create
        user = User(telegram_id=str(telegram_id), full_name='Customer')
        db.session.add(user)
        db.session.flush()

    from app.models.order import Wishlist
    existing = Wishlist.query.filter_by(user_id=user.id, product_id=product_id).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        return success_response({'wishlisted': False})
    else:
        w = Wishlist(user_id=user.id, product_id=product_id)
        db.session.add(w)
        db.session.commit()
        return success_response({'wishlisted': True})


@api_bp.route('/orders/my', methods=['GET'])
def my_orders_api():
    """Get orders for the current user (JWT or telegram_id query param)."""
    user = _resolve_mini_app_user()
    telegram_id = request.args.get('telegram_id')
    if not user and telegram_id:
        user = User.query.filter_by(telegram_id=str(telegram_id)).first()
    if not user:
        return success_response({'orders': []})

    orders = Order.query.filter_by(user_id=user.id).order_by(
        Order.created_at.desc()
    ).limit(20).all()

    return success_response({
        'orders': [
            {
                'id': o.id,
                'order_number': o.order_number,
                'status': o.status.value,
                'status_label': o.status_label(),
                'total': float(o.total),
                'items_count': len(o.items),
                'created_at': o.created_at.strftime('%b %d, %Y') if o.created_at else '',
            }
            for o in orders
        ]
    })


@api_bp.route('/debug/db', methods=['GET'])
def debug_db():
    """Diagnostic: show what database the app is actually connected to."""
    import logging
    _logger = logging.getLogger(__name__)
    try:
        db_url = str(db.engine.url)
        # Mask password
        import re
        masked = re.sub(r'(:)[^@]+(@)', r'\1***\2', db_url)
        total_orders = Order.query.count()
        total_users = User.query.count()
        recent = Order.query.order_by(Order.id.desc()).limit(3).all()
        recent_list = [
            {'id': o.id, 'order_number': o.order_number, 'created_at': str(o.created_at)}
            for o in recent
        ]
        return success_response({
            'db_url': masked,
            'total_orders': total_orders,
            'total_users': total_users,
            'recent_orders': recent_list,
            'flask_env': os.getenv('FLASK_ENV', 'NOT SET'),
            'database_url_set': bool(os.getenv('DATABASE_URL')),
        })
    except Exception as e:
        _logger.error(f'[debug_db] {e}', exc_info=True)
        return error_response(str(e), 500)


@api_bp.route('/debug/migrate', methods=['GET', 'POST'])
def debug_migrate():
    """Run database migrations programmatically on Vercel."""
    import logging
    from flask_migrate import upgrade
    from app import db
    _logger = logging.getLogger(__name__)
    try:
        # Run Alembic upgrade head
        upgrade()
        
        # Verify if tables were created
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        
        return success_response({
            'message': 'Migration successful',
            'tables': tables
        })
    except Exception as e:
        _logger.error(f'[debug_migrate] {e}', exc_info=True)
        return error_response(str(e), 500)
