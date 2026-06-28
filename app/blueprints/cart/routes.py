from flask import render_template, request, jsonify, redirect, url_for, flash, session
from flask_login import current_user
from app.blueprints.cart import cart_bp
from app.extensions import db
from app.models.product import Product
from app.models.order import (Cart as CartModel, Order, OrderItem, OrderStatus,
                              PaymentMethod, Address, Coupon)
from app.blueprints.api.delivery import calculate_distance, STORE_LAT, STORE_LNG
import math
from app.utils import success_response, error_response, generate_order_number, get_or_create_session_id

FREE_DELIVERY_THRESHOLD = 1000
DELIVERY_FEE = 80

def _get_cart_items():
    if current_user.is_authenticated:
        return CartModel.query.filter_by(user_id=current_user.id).all()
    session_id = get_or_create_session_id()
    return CartModel.query.filter_by(session_id=session_id).all()

def _ensure_session():
    return get_or_create_session_id()


def _calc_totals(items, coupon=None):
    subtotal = sum((i.product.current_price() if i.product else 0) * i.quantity for i in items)
    delivery_fee = 0 if subtotal >= FREE_DELIVERY_THRESHOLD else DELIVERY_FEE
    discount = 0
    if coupon and coupon.is_active:
        from app.models.order import DiscountType
        if subtotal >= (coupon.min_order_amount or 0):
            if coupon.discount_type == DiscountType.percentage:
                discount = subtotal * coupon.discount_value / 100
                if coupon.max_discount:
                    discount = min(discount, coupon.max_discount)
            else:
                discount = coupon.discount_value
    total = max(0, subtotal + delivery_fee - discount)
    return subtotal, delivery_fee, discount, total


@cart_bp.route('/')
def view():
    items = _get_cart_items()
    coupon_code = session.get('coupon_code')
    coupon = Coupon.query.filter_by(code=coupon_code, is_active=True).first() if coupon_code else None
    subtotal, delivery_fee, discount, total = _calc_totals(items, coupon)
    return render_template('cart/cart.html', items=items, subtotal=subtotal,
                           delivery_fee=delivery_fee, discount=discount, total=total,
                           coupon_code=coupon_code)


@cart_bp.route('/add', methods=['POST'])
def add():
    data = request.get_json() or {}
    product_id = data.get('product_id')
    quantity = int(data.get('quantity', 1))
    product = db.session.get(Product, product_id)
    if not product or not product.is_active:
        return error_response('Product not found')
    if product.stock_qty < quantity:
        return error_response(f'Only {product.stock_qty} in stock')

    session_id = _ensure_session()
    if current_user.is_authenticated:
        item = CartModel.query.filter_by(user_id=current_user.id, product_id=product_id).first()
        if item:
            item.quantity = min(item.quantity + quantity, product.stock_qty)
        else:
            item = CartModel(user_id=current_user.id, product_id=product_id, quantity=quantity)
            db.session.add(item)
    else:
        item = CartModel.query.filter_by(session_id=session_id, product_id=product_id).first()
        if item:
            item.quantity = min(item.quantity + quantity, product.stock_qty)
        else:
            item = CartModel(session_id=session_id, product_id=product_id, quantity=quantity)
            db.session.add(item)
    db.session.commit()

    cart_count = len(_get_cart_items())
    return success_response({'cart_count': cart_count}, 'Added to cart')


@cart_bp.route('/update', methods=['POST'])
def update():
    data = request.get_json() or {}
    item_id = data.get('item_id')
    quantity = int(data.get('quantity', 1))
    item = db.session.get(CartModel, item_id)
    if not item:
        return error_response('Cart item not found', 404)
    if quantity <= 0:
        db.session.delete(item)
    else:
        item.quantity = quantity
    db.session.commit()
    return success_response(message='Cart updated')


@cart_bp.route('/remove', methods=['POST'])
def remove():
    data = request.get_json() or {}
    item_id = data.get('item_id')
    item = db.session.get(CartModel, item_id)
    if item:
        db.session.delete(item)
        db.session.commit()
    return success_response(message='Removed')


@cart_bp.route('/count')
def count():
    items = _get_cart_items()
    return jsonify({'count': len(items)})


@cart_bp.route('/apply-coupon', methods=['POST'])
def apply_coupon():
    data = request.get_json() or {}
    code = (data.get('code') or '').strip().upper()
    coupon = Coupon.query.filter_by(code=code, is_active=True).first()
    if not coupon:
        return jsonify({'success': False, 'message': 'Invalid or expired coupon code'})
    if coupon.max_uses and coupon.used_count >= coupon.max_uses:
        return jsonify({'success': False, 'message': 'Coupon has reached its usage limit'})
    session['coupon_code'] = code
    return jsonify({'success': True, 'message': f'Coupon "{code}" applied successfully!'})


@cart_bp.route('/checkout', methods=['GET', 'POST'])
def checkout():
    items = _get_cart_items()
    if not items:
        flash('Your cart is empty.', 'info')
        return redirect(url_for('cart.view'))

    coupon_code = session.get('coupon_code')
    coupon = Coupon.query.filter_by(code=coupon_code, is_active=True).first() if coupon_code else None
    subtotal, delivery_fee, discount, total = _calc_totals(items, coupon)

    if request.method == 'POST':
        # Get coordinates
        lat_str = request.form.get('lat')
        lng_str = request.form.get('lng')
        
        if not lat_str or not lng_str:
            flash('Please select a delivery location on the map.', 'danger')
            return redirect(url_for('cart.checkout'))
            
        try:
            lat = float(lat_str)
            lng = float(lng_str)
        except ValueError:
            flash('Invalid location coordinates.', 'danger')
            return redirect(url_for('cart.checkout'))

        # Recalculate delivery fee securely
        distance = calculate_distance(STORE_LAT, STORE_LNG, lat, lng)
        # Pricing: 100 ETB for first 5km, then 40 ETB per additional km
        if distance <= 5:
            delivery_fee = 100
        else:
            extra_km = distance - 5
            delivery_fee = 100 + (extra_km * 40)
            
        # Update total with new delivery fee
        total = max(0, subtotal + delivery_fee - discount)

        # Create address
        addr = Address(
            user_id=current_user.id if current_user.is_authenticated else None,
            recipient_name=request.form.get('recipient_name', ''),
            phone=request.form.get('phone', ''),
            city=request.form.get('city', 'Addis Ababa'),
            sub_city=request.form.get('sub_city', ''),
            woreda=request.form.get('woreda', ''),
            specific_location=request.form.get('specific_location', ''),
            lat=lat,
            lng=lng,
        )
        db.session.add(addr)
        db.session.flush()

        # Create order
        order = Order(
            user_id=current_user.id if current_user.is_authenticated else None,
            order_number=generate_order_number(),
            status=OrderStatus.pending,
            subtotal=subtotal,
            delivery_fee=delivery_fee,
            discount_amount=discount,
            total=total,
            payment_method=PaymentMethod.cod,
            payment_status='pending',
            notes=request.form.get('notes', ''),
            address_id=addr.id,
            coupon_id=coupon.id if coupon else None,
        )
        db.session.add(order)
        db.session.flush()

        # Add order items and clear cart
        for item in items:
            if item.product:
                import json
                oi = OrderItem(
                    order_id=order.id,
                    product_id=item.product_id,
                    quantity=item.quantity,
                    unit_price=item.product.current_price(),
                    total_price=item.product.current_price() * item.quantity,
                    product_snapshot=json.dumps({
                        'name': item.product.name,
                        'price': float(item.product.current_price()),
                        'image': item.product.primary_image(),
                    }),
                )
                db.session.add(oi)
                item.product.stock_qty = max(0, item.product.stock_qty - item.quantity)
                item.product.sales_count = (item.product.sales_count or 0) + item.quantity
            db.session.delete(item)

        if coupon:
            coupon.used_count += 1
        session.pop('coupon_code', None)
        db.session.commit()

        flash(f'🎉 Order #{order.order_number} placed! We\'ll confirm shortly.', 'success')
        return redirect(url_for('orders.detail', order_number=order.order_number))

    return render_template('cart/checkout.html', items=items, subtotal=subtotal,
                           delivery_fee=delivery_fee, discount=discount, total=total)
