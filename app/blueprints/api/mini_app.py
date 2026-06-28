"""
Mini App dedicated API endpoints for Liyu Kids Mart Telegram Mini App.
Handles in-app checkout, orders, and wishlist for Telegram users.
"""
import json
from flask import request
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity
from app.blueprints.api import api_bp
from app.extensions import db
from app.models.product import Product
from app.models.order import (Cart, Order, OrderItem, Address,
                               OrderStatus, PaymentMethod)
from app.models.user import User
from app.utils import success_response, error_response, generate_order_number


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

    delivery_fee = float(delivery.get('delivery_fee', 0)) if delivery.get('delivery_fee') else 80.0
    total = subtotal + delivery_fee

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

    # Create order
    order_number = generate_order_number()
    order = Order(
        user_id=user.id,
        order_number=order_number,
        status=OrderStatus.pending,
        subtotal=subtotal,
        delivery_fee=delivery_fee,
        discount_amount=0,
        total=total,
        payment_method=payment_method,
        payment_status='pending',
        notes=data.get('notes', 'Placed via Telegram Mini App'),
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

    db.session.commit()

    return success_response({
        'order_number': order_number,
        'total': total,
        'delivery_fee': delivery_fee,
        'subtotal': subtotal,
        'items_count': len(order_items),
        'payment_method': payment_method_str,
        'message': f'Order #{order_number} placed successfully! We will confirm shortly.'
    }, 'Order placed successfully')


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
