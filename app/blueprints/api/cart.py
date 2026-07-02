from flask import request, session
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity
from app.blueprints.api import api_bp
from app.extensions import db
from app.models.order import Cart
from app.models.product import Product
from app.models.user import User
from app.utils import success_response, error_response, get_or_create_session_id


from flask_login import current_user

def _resolve_user():
    """Return user from JWT, telegram_id, flask_login, or session."""
    if current_user and current_user.is_authenticated:
        return current_user, None
    try:
        verify_jwt_in_request(optional=True)
        uid = get_jwt_identity()
        if uid:
            return db.session.get(User, uid), None
    except Exception:
        pass
    data = request.get_json(silent=True) or {}
    telegram_id = request.args.get('telegram_id') or data.get('telegram_id')
    if telegram_id:
        u = User.query.filter_by(telegram_id=str(telegram_id)).first()
        return u, None
    # Use cart_session_id from request body if provided (from JS localStorage)
    body_session = data.get('cart_session_id')
    if body_session:
        return None, body_session
    return None, get_or_create_session_id()


def _cart_query(user, session_id):
    if user:
        return Cart.query.filter_by(user_id=user.id)
    return Cart.query.filter_by(session_id=session_id, user_id=None)


@api_bp.route('/cart', methods=['GET'])
def get_cart():
    user, session_id = _resolve_user()
    items = _cart_query(user, session_id).all()
    subtotal = sum(float(i.product.current_price()) * i.quantity for i in items if i.product)
    total_item_count = sum(i.quantity for i in items)
    delivery_fee = 0 if subtotal >= 1000 else 50

    # Loyalty discount calculation
    from app.services.loyalty_service import calculate_loyalty_discount, get_cart_incentive_context
    if user:
        user._cart_item_count = total_item_count
    discount_info = calculate_loyalty_discount(user, subtotal)
    discount_amount = discount_info.get('total_discount_amount', 0.0)
    discounted_total = max(0.0, subtotal - discount_amount)

    # Cart incentive progress bar data
    incentive_ctx = get_cart_incentive_context(subtotal)

    return success_response({
        'items': [i.to_dict() for i in items],
        'subtotal': subtotal,
        'delivery_fee': delivery_fee,
        'discount_amount': discount_amount,
        'discount_info': discount_info,
        'discounted_total': discounted_total,
        'total': discounted_total + delivery_fee,
        'count': total_item_count,
        'cart_incentive': incentive_ctx,
    })


@api_bp.route('/cart/items', methods=['POST'])
def add_to_cart():
    data = request.get_json(silent=True) or request.form
    product_id = int(data.get('product_id', 0))
    quantity = int(data.get('quantity', 1))
    user, session_id = _resolve_user()

    product = db.session.get(Product, product_id)
    if not product or not product.is_active:
        return error_response('Product not found', 404)
    if product.stock_qty < quantity:
        return error_response('Insufficient stock')

    if user:
        item = Cart.query.filter_by(user_id=user.id, product_id=product_id).first()
        if item:
            item.quantity += quantity
        else:
            item = Cart(user_id=user.id, product_id=product_id, quantity=quantity)
            db.session.add(item)
    else:
        item = Cart.query.filter_by(session_id=session_id, product_id=product_id, user_id=None).first()
        if item:
            item.quantity += quantity
        else:
            item = Cart(session_id=session_id, product_id=product_id, quantity=quantity)
            db.session.add(item)

    db.session.commit()
    count = _cart_query(user, session_id).with_entities(db.func.sum(Cart.quantity)).scalar() or 0
    return success_response({'cart_count': int(count)}, 'Added to cart')


@api_bp.route('/cart/items/<int:item_id>', methods=['PUT'])
def update_cart_item(item_id):
    data = request.get_json(silent=True) or {}
    quantity = int(data.get('quantity', 1))
    item = db.session.get(Cart, item_id)
    if not item:
        return error_response('Item not found', 404)
    if quantity <= 0:
        db.session.delete(item)
    else:
        item.quantity = quantity
    db.session.commit()
    return success_response(message='Updated')


@api_bp.route('/cart/items/<int:item_id>', methods=['DELETE'])
def remove_cart_item(item_id):
    user, session_id = _resolve_user()
    data = request.get_json(silent=True) or {}
    telegram_id = request.args.get('telegram_id') or data.get('telegram_id')
    item = db.session.get(Cart, item_id)
    if not item:
        return error_response('Item not found', 404)
    db.session.delete(item)
    db.session.commit()
    return success_response(message='Removed')


@api_bp.route('/cart', methods=['DELETE'])
def clear_cart():
    user, session_id = _resolve_user()
    items = _cart_query(user, session_id).all()
    for item in items:
        db.session.delete(item)
    db.session.commit()
    return success_response(message='Cart cleared')
