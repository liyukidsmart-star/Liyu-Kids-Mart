import json
from flask import request
from flask_jwt_extended import jwt_required, get_jwt_identity, verify_jwt_in_request
from app.blueprints.api import api_bp
from app.extensions import db
from app.models.order import Order, OrderItem, Cart, Address, OrderStatus
from app.models.product import Product
from app.models.ai_conversation import Payment
from app.models.user import User
from app.services.loyalty_service import apply_order_status_change
from app.utils import success_response, error_response, generate_order_number


def _get_user_from_request():
    """Try JWT first, then telegram_id param."""
    try:
        verify_jwt_in_request(optional=True)
        uid = get_jwt_identity()
        if uid:
            return db.session.get(User, uid)
    except Exception:
        pass
    telegram_id = request.args.get('telegram_id') or (request.get_json(silent=True) or {}).get('telegram_id')
    if telegram_id:
        return User.query.filter_by(telegram_id=str(telegram_id)).first()
    return None


@api_bp.route('/orders', methods=['GET'])
def get_orders():
    user = _get_user_from_request()
    if not user:
        return error_response('Authentication required', 401)
    orders = Order.query.filter_by(user_id=user.id).order_by(Order.created_at.desc()).limit(20).all()
    return success_response({'orders': [o.to_dict(include_items=True) for o in orders]})


@api_bp.route('/orders/<order_number>', methods=['GET'])
def get_order(order_number):
    order = Order.query.filter_by(order_number=order_number.upper()).first()
    if not order:
        return error_response('Order not found', 404)
    return success_response(order.to_dict(include_items=True))


@api_bp.route('/orders', methods=['POST'])
def create_order():
    user = _get_user_from_request()
    if not user:
        return error_response('Authentication required', 401)
    data = request.get_json()
    if not data:
        return error_response('No data provided')

    # Get cart
    cart_items = Cart.query.filter_by(user_id=user.id).all()
    if not cart_items:
        return error_response('Cart is empty')

    subtotal = sum(float(i.product.current_price()) * i.quantity for i in cart_items if i.product)
    total_item_count = sum(i.quantity for i in cart_items)

    # ── Calculate Loyalty Discount ──
    from app.services.loyalty_service import calculate_loyalty_discount, process_order_rewards
    user._cart_item_count = total_item_count
    discount_info = calculate_loyalty_discount(user, subtotal)
    discount_amount = discount_info.get('total_discount_amount', 0.0)
    
    discounted_subtotal = max(0.0, subtotal - discount_amount)
    delivery_fee = 0 if discounted_subtotal >= 1000 else 50
    total = discounted_subtotal + delivery_fee

    addr_data = data.get('address', {})
    addr = Address(
        user_id=user.id,
        recipient_name=addr_data.get('recipient_name', user.full_name),
        phone=addr_data.get('phone', user.phone or ''),
        city=addr_data.get('city', 'Addis Ababa'),
        sub_city=addr_data.get('sub_city', ''),
        woreda=addr_data.get('woreda', ''),
        specific_location=addr_data.get('specific_location', ''),
    )
    db.session.add(addr)
    db.session.flush()

    order = Order(
        order_number=generate_order_number(),
        user_id=user.id,
        subtotal=subtotal, delivery_fee=delivery_fee, total=total,
        notes=data.get('notes', ''),
        address_id=addr.id,
        delivery_address_snapshot=json.dumps(addr.to_dict())
    )
    db.session.add(order)
    db.session.flush()

    for item in cart_items:
        if not item.product:
            continue
        oi = OrderItem(
            order_id=order.id, product_id=item.product_id,
            quantity=item.quantity, unit_price=item.product.current_price(),
            total_price=float(item.product.current_price()) * item.quantity,
            product_snapshot=json.dumps({'name': item.product.name, 'image': item.product.primary_image()})
        )
        db.session.add(oi)
        item.product.stock_qty = max(0, item.product.stock_qty - item.quantity)
        item.product.sales_count = (item.product.sales_count or 0) + item.quantity
        db.session.delete(item)

    # ── Process Loyalty Rewards ──
    loyalty_result = process_order_rewards(user, order, savings_amount=discount_amount)

    payment = Payment(order_id=order.id, method='cod', amount=total, status='pending')
    db.session.add(payment)
    db.session.commit()
    
    res_data = order.to_dict(include_items=True)
    res_data['loyalty_result'] = loyalty_result
    return success_response(res_data, 'Order placed successfully', 201)


@api_bp.route('/orders/<order_number>/cancel', methods=['POST'])
def cancel_order(order_number):
    user = _get_user_from_request()
    order = Order.query.filter_by(order_number=order_number.upper()).first()
    if not order:
        return error_response('Order not found', 404)
    if order.status.value not in ('pending', 'confirmed'):
        return error_response('Order cannot be cancelled')
    for item in order.items:
        if item.product:
            item.product.stock_qty += item.quantity
    previous_status = order.status
    order.status = OrderStatus.cancelled
    reversal_result = apply_order_status_change(user, order, OrderStatus.cancelled, previous_status)
    db.session.commit()
    return success_response(message='Order cancelled')


@api_bp.route('/orders/<order_number>/track', methods=['GET'])
def track_order(order_number):
    order = Order.query.filter_by(order_number=order_number.upper()).first()
    if not order:
        return error_response('Order not found', 404)
    tracking = {
        'order_number': order.order_number,
        'status': order.status.value,
        'status_label': order.status_label(),
        'created_at': order.created_at.isoformat(),
        'delivery': order.delivery.to_dict() if order.delivery else None,
    }
    return success_response(tracking)
