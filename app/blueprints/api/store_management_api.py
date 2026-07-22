"""
Store Management Mini App API — Liyu Kids Mart
All API endpoints for the store management Telegram Mini App.
Protected by MANAGER_TG_IDS env variable (comma-separated Telegram IDs).
"""
import os
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from flask import request
from app.blueprints.api import api_bp
from zoneinfo import ZoneInfo
from sqlalchemy import func, distinct, and_
from app.models.product import Category
from app.models.ai_conversation import ActivityLog
from app.extensions import db
from app.models.order import Order, OrderStatus, OrderItem
from app.models.product import Product
from app.models.delivery import Driver, Delivery, DeliveryStatus
from app.models.user import User, UserRole
from app.utils import success_response, error_response
from app.models.loyalty import LoyaltySettings
from app.services.loyalty_service import apply_order_status_change, get_store_launch_state

DEFAULT_MANAGER_TG_IDS = ['401413271', '661528493', '403612118']
MANAGER_TG_IDS = list(dict.fromkeys(
    DEFAULT_MANAGER_TG_IDS + [m.strip() for m in os.getenv('MANAGER_TG_IDS', '').split(',') if m.strip()]
))


def _is_authorized_manager(telegram_id):
    """Check if the telegram_id is a manager or admin."""
    if not telegram_id:
        return False
    tid = str(telegram_id)
    # Check env var list
    if tid in MANAGER_TG_IDS:
        return True
    # Check DB for admin/manager role
    user = User.query.filter_by(telegram_id=tid).first()
    if user and user.role.value in ('admin', 'manager'):
        return True
    return False


def _get_manager_from_request():
    """Extract manager telegram_id from request and validate."""
    telegram_id = (
        request.args.get('manager_id') or
        request.args.get('telegram_id') or
        (request.get_json(silent=True) or {}).get('manager_id') or
        (request.get_json(silent=True) or {}).get('telegram_id')
    )
    return telegram_id if _is_authorized_manager(telegram_id) else None


def _active_orders_since(start_dt):
    return [
        o for o in Order.query.filter(Order.created_at >= start_dt).all()
        if o.status not in (OrderStatus.cancelled, OrderStatus.returned)
    ]

def _active_pos_sales_since(start_dt):
    from app.models.inventory import POSSale, POSSaleStatus
    return [
        s for s in POSSale.query.filter(POSSale.created_at >= start_dt).all()
        if s.status == POSSaleStatus.completed
    ]

# ─────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────

@api_bp.route('/store/dashboard', methods=['GET'])
def store_dashboard():
    """Return daily stats for the store management dashboard."""
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)
    bi_weekly_start = today_start - timedelta(days=14)
    monthly_start = today_start - timedelta(days=30)

    # Online Orders
    today_orders = _active_orders_since(today_start)
    week_orders = _active_orders_since(week_start)
    bi_weekly_orders = _active_orders_since(bi_weekly_start)
    monthly_orders = _active_orders_since(monthly_start)
    all_pending = Order.query.filter(Order.status == OrderStatus.pending).count()
    all_confirmed = Order.query.filter(Order.status == OrderStatus.confirmed).count()
    
    # POS Sales
    today_pos = _active_pos_sales_since(today_start)
    week_pos = _active_pos_sales_since(week_start)
    bi_weekly_pos = _active_pos_sales_since(bi_weekly_start)
    monthly_pos = _active_pos_sales_since(monthly_start)

    today_revenue = sum(float(o.total) for o in today_orders) + sum(float(p.total) for p in today_pos)
    week_revenue = sum(float(o.total) for o in week_orders) + sum(float(p.total) for p in week_pos)
    bi_weekly_revenue = sum(float(o.total) for o in bi_weekly_orders) + sum(float(p.total) for p in bi_weekly_pos)
    monthly_revenue = sum(float(o.total) for o in monthly_orders) + sum(float(p.total) for p in monthly_pos)

    # Low stock products
    low_stock = Product.query.filter(Product.is_active == True, Product.stock_qty <= 5).order_by(Product.stock_qty.asc()).limit(5).all()

    # Recent orders
    recent = Order.query.order_by(Order.created_at.desc()).limit(5).all()

    return success_response({
        'today': {
            'orders': len(today_orders),
            'revenue': today_revenue,
        },
        'week': {
            'orders': len(week_orders),
            'revenue': week_revenue,
        },
        'bi_weekly': {
            'orders': len(bi_weekly_orders),
            'revenue': bi_weekly_revenue,
        },
        'monthly': {
            'orders': len(monthly_orders),
            'revenue': monthly_revenue,
        },
        'pending_orders': all_pending,
        'confirmed_orders': all_confirmed,
        'low_stock_products': [
            {'id': p.id, 'name': p.name, 'stock': p.stock_qty, 'image': p.primary_image()}
            for p in low_stock
        ],
        'recent_orders': [
            {
                'id': o.id,
                'order_number': o.order_number,
                'customer': o.user.full_name if o.user else 'Guest',
                'total': float(o.total),
                'status': o.status.value,
                'status_label': o.status_label(),
                'created_at': o.created_at.strftime('%b %d, %H:%M') if o.created_at else '',
            }
            for o in recent
        ],
    })


# ─────────────────────────────────────────────────────────────
# Orders
# ─────────────────────────────────────────────────────────────

@api_bp.route('/store/orders', methods=['GET'])
def store_orders():
    """Paginated order list with optional status filter."""
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    status_filter = request.args.get('status')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    search = request.args.get('search', '').strip()

    query = Order.query

    if status_filter and status_filter != 'all':
        try:
            query = query.filter(Order.status == OrderStatus[status_filter])
        except KeyError:
            pass

    if search:
        query = query.join(User, Order.user_id == User.id, isouter=True).filter(
            db.or_(
                Order.order_number.ilike(f'%{search}%'),
                User.full_name.ilike(f'%{search}%'),
            )
        )

    total_count = query.count()
    orders = query.order_by(Order.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()

    return success_response({
        'orders': [
            {
                'id': o.id,
                'order_number': o.order_number,
                'customer': o.user.full_name if o.user else 'Guest',
                'customer_phone': o.address.phone if o.address else '',
                'delivery_scope': o.address.delivery_scope if o.address else 'addis',
                'region': o.address.region if o.address else '',
                'city_town': o.address.city_town if o.address else '',
                'total': float(o.total),
                'subtotal': float(o.subtotal),
                'discount_amount': float(o.discount_amount or 0),
                'spending_discount_amount': float(o.spending_discount_amount or 0),
                'qty_discount_amount_saved': float(o.qty_discount_amount_saved or 0),
                'delivery_fee': float(o.delivery_fee or 0),
                'status': o.status.value,
                'status_label': o.status_label(),
                'payment_method': o.payment_method.value if o.payment_method else 'cod',
                'items_count': len(o.items),
                'created_at': o.created_at.isoformat() if o.created_at else '',
                'created_label': o.created_at.strftime('%b %d, %H:%M') if o.created_at else '',
            }
            for o in orders
        ],
        'total': total_count,
        'page': page,
        'per_page': per_page,
    })


@api_bp.route('/store/orders/<int:order_id>', methods=['GET'])
def store_order_detail(order_id):
    """Full order detail for the store management portal."""
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    order = db.session.get(Order, order_id)
    if not order:
        return error_response('Order not found', 404)

    items = [
        {
            'id': oi.id,
            'product_name': oi.product_name(),
            'product_image': oi.product_image(),
            'quantity': oi.quantity,
            'unit_price': float(oi.unit_price),
            'total_price': float(oi.total_price),
        }
        for oi in order.items
    ]

    delivery_info = None
    if order.delivery:
        d = order.delivery
        delivery_info = {
            'id': d.id,
            'status': d.status.value,
            'driver_name': d.driver.user.full_name if d.driver and d.driver.user else 'Unassigned',
            'driver_phone': d.driver.user.phone if d.driver and d.driver.user else '',
        }

    return success_response({
        'id': order.id,
        'order_number': order.order_number,
        'customer': order.user.full_name if order.user else 'Guest',
        'customer_phone': order.address.phone if order.address else '',
        'customer_telegram': order.user.telegram_id if order.user else '',
        'location': order.address.specific_location if order.address else '',
        'region': order.address.region if order.address else '',
        'city_town': order.address.city_town if order.address else '',
        'delivery_scope': order.address.delivery_scope if order.address else 'addis',
        'lat': order.address.lat if order.address else None,
        'lng': order.address.lng if order.address else None,
        'items': items,
        'subtotal': float(order.subtotal),
        'discount_amount': float(order.discount_amount or 0),
        'spending_discount_amount': float(order.spending_discount_amount or 0),
        'qty_discount_amount_saved': float(order.qty_discount_amount_saved or 0),
        'delivery_fee': float(order.delivery_fee or 0),
        'total': float(order.total),
        'status': order.status.value,
        'status_label': order.status_label(),
        'payment_method': order.payment_method.value if order.payment_method else 'cod',
        'payment_status': order.payment_status or 'pending',
        'notes': order.notes or '',
        'created_at': order.created_at.isoformat() if order.created_at else '',
        'delivery': delivery_info,
    })


@api_bp.route('/store/orders/<int:order_id>/status', methods=['POST'])
def store_update_order_status(order_id):
    """Update an order's status."""
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    order = db.session.get(Order, order_id)
    if not order:
        return error_response('Order not found', 404)

    data = request.get_json(silent=True) or {}
    new_status_str = data.get('status')
    if not new_status_str:
        return error_response('status field required', 400)

    try:
        new_status = OrderStatus[new_status_str]
    except KeyError:
        return error_response(f'Invalid status: {new_status_str}', 400)

    previous_status = order.status
    order.status = new_status
    order.updated_at = datetime.now(timezone.utc)
    reversal_result = apply_order_status_change(order.user, order, new_status, previous_status)

    db.session.commit()

    # Notify customer
    try:
        _notify_customer_status(order)
    except Exception:
        pass

    payload = {'status': new_status.value, 'status_label': order.status_label()}
    if reversal_result is not None:
        payload['reversal'] = reversal_result
    return success_response(payload)


def _notify_customer_status(order):
    """Send order status update to customer."""
    import httpx as _httpx
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    if not token or not order.user or not order.user.telegram_id:
        return
    status_emoji = {
        'confirmed': '✅', 'packed': '📦',
        'out_for_delivery': '🚚', 'delivered': '🎉', 'cancelled': '❌',
    }
    emoji = status_emoji.get(order.status.value, '📋')
    msg = (
        f"{emoji} *Order #{order.order_number} Update*\n\n"
        f"Your order status has been updated to: *{order.status_label()}*\n\n"
        f"Total: ETB {float(order.total):,.0f}\n"
        f"Thank you for shopping at Liyu Kids Mart! 💚"
    )
    try:
        _httpx.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': order.user.telegram_id, 'text': msg, 'parse_mode': 'Markdown'},
            timeout=5
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────


@api_bp.route('/store/settings', methods=['GET'])
def store_settings():
    """Return store-wide settings for the manager portal."""
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    settings = LoyaltySettings.query.first()
    if not settings:
        settings = LoyaltySettings()
        db.session.add(settings)
        db.session.flush()

    payload = settings.to_dict()
    payload.update(get_store_launch_state())
    return success_response(payload)


@api_bp.route('/store/launch-date', methods=['POST'])
def store_set_launch_date():
    """Set the global launch date used to gate ordering."""
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    data = request.get_json(silent=True) or {}
    raw_launch = data.get('launch_date')
    if not raw_launch:
        return error_response('launch_date is required', 400)

    if str(raw_launch).endswith('Z'):
        raw_launch = str(raw_launch)[:-1] + '+00:00'
    try:
        launch_date = datetime.fromisoformat(str(raw_launch))
    except ValueError:
        try:
            launch_date = datetime.strptime(str(raw_launch), '%Y-%m-%dT%H:%M')
        except ValueError:
            return error_response('Invalid launch_date format', 400)
    if launch_date.tzinfo is None:
        launch_date = launch_date.replace(tzinfo=timezone.utc)
    launch_date = launch_date.astimezone(timezone.utc)

    settings = LoyaltySettings.query.first()
    if not settings:
        settings = LoyaltySettings()
        db.session.add(settings)
    settings.launch_date = launch_date
    db.session.commit()

    payload = settings.to_dict()
    payload.update(get_store_launch_state())
    return success_response(payload, 'Launch date updated')


@api_bp.route('/store/launch-date', methods=['DELETE'])
def store_clear_launch_date():
    """Clear the global launch date and reopen ordering."""
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    settings = LoyaltySettings.query.first()
    if not settings:
        settings = LoyaltySettings()
        db.session.add(settings)
    settings.launch_date = None
    db.session.commit()

    payload = settings.to_dict()
    payload.update(get_store_launch_state())
    return success_response(payload, 'Launch date cleared')


# Products (Store View)
# ─────────────────────────────────────────────────────────────

@api_bp.route('/store/products/price-adjustment', methods=['POST'])
def store_bulk_price_adjustment():
    """Apply a universal price change to all active products."""
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    data = request.get_json(silent=True) or {}
    mode = (data.get('mode') or 'percentage').strip().lower()
    if mode not in ('percentage', 'fixed', 'set'):
        return error_response('Unsupported price adjustment mode', 400)

    raw_value = data.get('value')
    if raw_value is None:
        return error_response('value field required', 400)

    try:
        value = Decimal(str(raw_value))
    except Exception:
        return error_response('value must be numeric', 400)

    products = Product.query.filter_by(is_active=True).all()
    if not products:
        return success_response({'updated_count': 0, 'mode': mode, 'value': float(value)}, 'No active products to update')

    for product in products:
        current_price = Decimal(str(product.price or 0))
        if mode == 'percentage':
            updated_price = current_price * (Decimal('1') + (value / Decimal('100')))
        elif mode == 'fixed':
            updated_price = current_price + value
        else:
            updated_price = value
        product.price = updated_price.quantize(Decimal('0.01'))

    db.session.commit()
    return success_response(
        {'updated_count': len(products), 'mode': mode, 'value': float(value)},
        'Prices updated successfully'
    )


@api_bp.route('/store/products', methods=['GET'])
def store_products():
    """Product list with stock levels for inventory management."""
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 30))
    low_stock_only = request.args.get('low_stock') == '1'
    search = request.args.get('search', '').strip()

    query = Product.query.filter_by(is_active=True)
    if low_stock_only:
        query = query.filter(Product.stock_qty <= 5)
    if search:
        query = query.filter(Product.name.ilike(f'%{search}%'))

    total_count = query.count()
    products = query.order_by(Product.stock_qty.asc()).offset((page - 1) * per_page).limit(per_page).all()

    return success_response({
        'products': [
            {
                'id': p.id,
                'name': p.name,
                'price': float(p.current_price()),
                'stock': p.stock_qty,
                'sales_count': p.sales_count or 0,
                'image': p.primary_image(),
                'is_active': p.is_active,
                'low_stock': p.stock_qty <= 5,
            }
            for p in products
        ],
        'total': total_count,
        'page': page,
    })



@api_bp.route('/store/products/<int:product_id>/price', methods=['POST'])
def store_update_product_price(product_id):
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)
        
    product = Product.query.get_or_404(product_id)
    data = request.json or {}
    new_price = data.get('price')
    
    if new_price is None:
        return error_response('Price is required', 400)
        
    try:
        new_price = float(new_price)
        if new_price < 0:
            return error_response('Price cannot be negative', 400)
    except ValueError:
        return error_response('Invalid price format', 400)
        
    product.price = new_price
    db.session.commit()
    
    return success_response({'message': 'Price updated successfully', 'price': float(product.price)})

@api_bp.route('/store/products/<int:product_id>/stock', methods=['POST'])
def store_update_stock(product_id):
    """Quick stock update for a product."""
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    product = db.session.get(Product, product_id)
    if not product:
        return error_response('Product not found', 404)

    data = request.get_json(silent=True) or {}
    new_stock = data.get('stock')
    if new_stock is None:
        return error_response('stock field required', 400)

    product.stock_qty = max(0, int(new_stock))
    db.session.commit()
    return success_response({'id': product_id, 'stock': product.stock_qty})


# ─────────────────────────────────────────────────────────────
# Drivers
# ─────────────────────────────────────────────────────────────

@api_bp.route('/store/drivers', methods=['GET'])
def store_drivers():
    """List all active drivers with their current assignment."""
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    drivers = Driver.query.all()
    result = []
    for d in drivers:
        active_deliveries = Delivery.query.filter_by(driver_id=d.id).filter(
            Delivery.status.in_([DeliveryStatus.assigned, DeliveryStatus.picked_up, DeliveryStatus.in_transit])
        ).count()
        result.append({
            'id': d.id,
            'name': d.user.full_name if d.user else 'Driver',
            'phone': d.user.phone if d.user else '',
            'telegram_id': d.user.telegram_id if d.user else '',
            'is_available': d.is_available if hasattr(d, 'is_available') else True,
            'active_deliveries': active_deliveries,
            'total_deliveries': d.total_deliveries or 0,
            'lat': float(d.current_lat) if d.current_lat else None,
            'lng': float(d.current_lng) if d.current_lng else None,
        })

    return success_response({'drivers': result})


@api_bp.route('/store/orders/<int:order_id>/assign-driver', methods=['POST'])
def store_assign_driver(order_id):
    """Assign a driver to an order's delivery."""
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    order = db.session.get(Order, order_id)
    if not order:
        return error_response('Order not found', 404)

    data = request.get_json(silent=True) or {}
    driver_id = data.get('driver_id')
    if not driver_id:
        return error_response('driver_id required', 400)

    driver = db.session.get(Driver, driver_id)
    if not driver:
        return error_response('Driver not found', 404)

    # Create or update delivery assignment
    delivery = order.delivery
    if not delivery:
        delivery = Delivery(order_id=order.id)
        db.session.add(delivery)

    delivery.driver_id = driver.id
    delivery.status = DeliveryStatus.assigned
    order.status = OrderStatus.confirmed
    db.session.commit()

    return success_response({
        'driver_name': driver.user.full_name if driver.user else 'Driver',
        'order_status': order.status.value,
    })


# ─────────────────────────────────────────────────────────────
# Customers
# ─────────────────────────────────────────────────────────────

@api_bp.route('/store/customers', methods=['GET'])
def store_customers():
    """Top customers by spending for the store management portal."""
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    customers = User.query.filter(
        User.total_orders > 0
    ).order_by(User.total_money_spent.desc()).limit(30).all()

    return success_response({
        'customers': [
            {
                'id': u.id,
                'name': u.full_name,
                'telegram': u.telegram_username or '',
                'telegram_id': u.telegram_id or '',
                'total_orders': u.total_orders,
                'total_spent': float(u.total_money_spent or 0),
                'loyalty_level': u.loyalty_level.name if u.loyalty_level else 'None',
                'loyalty_icon': u.loyalty_level.badge_icon if u.loyalty_level else '🆕',
                'last_purchase': u.last_purchase_date.strftime('%b %d, %Y') if u.last_purchase_date else '',
            }
            for u in customers
        ]
    })


# ─────────────────────────────────────────────────────────────
# POS Terminal (Telegram Mini App)
# ─────────────────────────────────────────────────────────────

@api_bp.route('/store/pos/lookup-product', methods=['GET'])
def store_pos_lookup():
    """Lookup a product by ID or SKU for the Mini App POS."""
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    query = request.args.get('q', '').strip()
    product_id = request.args.get('id', '').strip()

    p = None
    if product_id and product_id.isdigit():
        p = db.session.get(Product, int(product_id))
    elif query:
        # If it's a scanned URL, try to extract product__<id>
        if 'startapp=product__' in query:
            try:
                pid = query.split('startapp=product__')[1].split('&')[0]
                p = db.session.get(Product, int(pid))
            except:
                pass
        
        # If not found yet, check SKU
        if not p:
            p = Product.query.filter(Product.sku.ilike(f'%{query}%')).first()
            
        # Or exact ID
        if not p and query.isdigit():
            p = db.session.get(Product, int(query))

    if not p or not p.is_active:
        return error_response('Product not found', 404)

    return success_response({
        'product': {
            'id': p.id,
            'name': p.name,
            'sku': p.sku or f'P-{p.id}',
            'price': float(p.price),
            'stock_qty': p.stock_qty,
            'image': p.primary_image()
        }
    })


@api_bp.route('/store/pos/visual-search', methods=['POST'])
def store_pos_visual_search():
    """Visual-search a product from a camera capture (CLIP + Pinecone)."""
    from app.services import visual_search as vs

    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    image_file = request.files.get('image')
    if not image_file:
        return error_response('No image provided', 400)

    if not vs.is_configured():
        # Graceful fallback so the POS screen never hard-crashes
        p = Product.query.filter_by(is_active=True).order_by(Product.id.desc()).first()
        if not p:
            return error_response('Visual search not configured and no products found', 503)
        return success_response({
            'product': _pos_product_dict(p),
            'confidence': None,
            'method': 'fallback_latest',
            'note': 'Visual search not yet configured. Add HF_TOKEN, PINECONE_API_KEY and PINECONE_INDEX to env vars.'
        })

    try:
        image_bytes = image_file.read()
        content_type = image_file.content_type or 'image/jpeg'

        matches = vs.query_image_bytes(image_bytes, content_type, top_k=1)
        if not matches:
            return error_response('No visual match found in index', 404)

        best = matches[0]
        confidence = best['score']

        if confidence < vs.CONFIDENCE_THRESHOLD:
            return error_response(
                f'Best match confidence {confidence:.0%} is below threshold — no reliable match found', 404
            )

        p = db.session.get(Product, best['product_id'])
        if not p:
            # Try by SKU as fallback
            p = Product.query.filter_by(sku=best['sku']).first()
        if not p or not p.is_active:
            return error_response('Matched product not found in catalogue', 404)

        return success_response({
            'product': _pos_product_dict(p),
            'confidence': round(confidence, 4),
            'method': 'clip_pinecone',
        })

    except Exception as exc:
        import logging
        logging.getLogger(__name__).error('Visual search error: %s', exc, exc_info=True)
        return error_response(f'Visual search error: {exc}', 500)


@api_bp.route('/store/pos/visual-search-config', methods=['GET'])
def store_pos_visual_search_config():
    """Return HF token and config status securely to the manager frontend."""
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)
        
    from app.services import visual_search as vs
    return success_response({
        'configured': vs.is_configured(),
        'hf_token': vs._hf_token(),
    })


@api_bp.route('/store/pos/products-to-index', methods=['GET'])
def store_pos_products_to_index():
    """Return a batch of products to index in the frontend."""
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)
        
    offset = int(request.args.get('offset', 0))
    limit = int(request.args.get('limit', 10))
    
    total = Product.query.filter_by(is_active=True).count()
    products = Product.query.filter_by(is_active=True).order_by(Product.id.asc()).offset(offset).limit(limit).all()
    
    return success_response({
        'total': total,
        'products': [
            {
                'id': p.id,
                'sku': p.sku or str(p.id),
                'image': p.primary_image()
            }
            for p in products
        ]
    })


@api_bp.route('/store/pos/proxy-image', methods=['GET'])
def store_pos_proxy_image():
    """Proxy image fetch to bypass CORS for the frontend."""
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)
        
    url = request.args.get('url')
    if not url:
        return error_response('No URL provided', 400)
        
    import urllib.request
    import io
    from flask import send_file
    import os
    
    # Resolve relative Telegram proxy URLs if necessary
    if url.startswith('/media/'):
        file_id = url.split('/media/')[1]
        from app.blueprints.main.routes import _telegram_file_path
        token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        file_path = _telegram_file_path(file_id)
        if file_path:
            url = f'https://api.telegram.org/file/bot{token}/{file_path}'
        else:
            return error_response('File not found on Telegram', 404)
    elif url.startswith('/') and not url.startswith('//'):
        url = request.host_url.rstrip('/') + url

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'LiyuKidsMart/1.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            content = response.read()
            content_type = response.headers.get('Content-Type', 'image/jpeg')
            
        return send_file(
            io.BytesIO(content),
            mimetype=content_type
        )
    except Exception as e:
        import logging
        logging.error(f"Proxy fetch failed for {url}: {e}")
        return error_response(f'Proxy fetch failed: {str(e)}', 500)


@api_bp.route('/store/pos/upsert-embedding', methods=['POST'])
def store_pos_upsert_embedding():
    """Save an embedding calculated by the frontend into Pinecone."""
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)
        
    data = request.json or {}
    product_id = data.get('product_id')
    sku = data.get('sku')
    embedding = data.get('embedding')
    
    if not product_id or not embedding:
        return error_response('Missing required fields', 400)
        
    from app.services import visual_search as vs
    try:
        vs.upsert_product(int(product_id), str(sku), embedding)
        return success_response({})
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error('Upsert embedding error: %s', exc, exc_info=True)
        return error_response(f'Upsert error: {exc}', 500)


@api_bp.route('/store/pos/index-product', methods=['POST'])
def store_pos_index_product():
    """Fully server-side: fetch image, embed via HuggingFace, upsert to Pinecone."""
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    data = request.json or {}
    product_id = data.get('product_id')
    sku = data.get('sku')
    image_url = data.get('image_url')

    if not product_id or not image_url:
        return error_response('Missing product_id or image_url', 400)

    from app.services import visual_search as vs
    import logging
    log = logging.getLogger(__name__)

    if not vs.is_configured():
        return error_response('Visual search not configured (check HF_TOKEN, PINECONE_API_KEY, PINECONE_INDEX)', 503)

    try:
        embedding = vs.embed_image_url(image_url)
        vs.upsert_product(int(product_id), str(sku or product_id), embedding)
        return success_response({'indexed': True})
    except vs.HFEmbeddingUnavailableError as exc:
        log.warning('index-product skipped for product %s: %s', product_id, exc)
        return success_response({'indexed': False, 'skipped': True, 'reason': str(exc)})
    except Exception as exc:
        log.error('index-product error for product %s: %s', product_id, exc, exc_info=True)
        return error_response(str(exc), 500)


def _pos_product_dict(p: Product) -> dict:
    return {
        'id': p.id,
        'name': p.name,
        'sku': p.sku or f'P-{p.id}',
        'price': float(p.price),
        'stock_qty': p.stock_qty,
        'image': p.primary_image(),
    }


@api_bp.route('/store/pos/checkout', methods=['POST'])
def store_pos_checkout():
    """Process a POS checkout from the Mini App."""

    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    data = request.get_json(silent=True) or {}
    items = data.get('items', [])
    discount_percentage = float(data.get('discount_percentage', 0))
    payment_method = data.get('payment_method', 'cash')
    notes = data.get('notes', '')

    if not items:
        return error_response('Cart is empty', 400)

    try:
        from app.models.inventory import POSSale, POSSaleItem, StockTransaction, StockTransactionType
        from app.models.user import User
        
        manager = User.query.filter_by(telegram_id=str(manager_id)).first()
        cashier_name = manager.full_name if manager else 'Manager'
        cashier_id = manager.id if manager else None

        import time
        sale = POSSale(
            sale_number=f"POS-{time.strftime('%Y%m%d')}-{int(time.time()*1000)%10000}",
            cashier_id=cashier_id,
            discount_percentage=discount_percentage,
            payment_method=payment_method,
            notes=notes
        )

        subtotal = 0.0
        for item_data in items:
            pid = item_data.get('product_id')
            qty = int(item_data.get('quantity', 1))
            
            p = db.session.get(Product, pid)
            if not p:
                return error_response(f'Product {pid} not found', 404)
            if p.stock_qty < qty:
                return error_response(f'Insufficient stock for {p.name} (Available: {p.stock_qty})', 400)

            # Deduct stock
            p.stock_qty -= qty

            # Log transaction
            txn = StockTransaction(
                product_id=p.id,
                transaction_type=StockTransactionType.pos_sale,
                quantity_change=-qty,
                quantity_before=p.stock_qty + qty,
                quantity_after=p.stock_qty,
                reference_id=sale.sale_number,
                notes=f'POS Sale checkout by {cashier_name}'
            )
            db.session.add(txn)

            # Add sale item
            price = float(item_data.get('unit_price', p.price))
            subtotal += (price * qty)

            sale_item = POSSaleItem(
                product_id=p.id,
                product_name=p.name,
                product_image=p.primary_image(),
                quantity=qty,
                unit_price=price,
                total_price=(price * qty)
            )
            sale.items.append(sale_item)

        sale.subtotal = subtotal
        sale.discount_amount = subtotal * (discount_percentage / 100.0)
        sale.total = subtotal - sale.discount_amount

        db.session.add(sale)
        db.session.commit()

        return success_response({
            'message': f'Sale {sale.sale_number} completed successfully!',
            'sale_number': sale.sale_number,
            'total': sale.total,
            'items_count': len(items)
        })

    except Exception as e:
        db.session.rollback()
        import logging
        logging.error(f'[store_pos_checkout] {e}', exc_info=True)
        return error_response(str(e), 500)


@api_bp.route('/store/sales/history', methods=['GET'])
def store_sales_history():
    manager_id = _get_manager_from_request()
    if not manager_id: return error_response('Unauthorized', 403)
    
    from app.models.inventory import POSSale
    
    # Get last 50 online orders
    orders = Order.query.order_by(Order.created_at.desc()).limit(50).all()
    
    # Get last 50 POS sales
    pos_sales = POSSale.query.order_by(POSSale.created_at.desc()).limit(50).all()
    
    history = []
    for o in orders:
        history.append({
            'id': str(o.id),
            'reference': o.order_number,
            'type': 'online',
            'status': o.status.value,
            'total': float(o.total),
            'items_count': len(o.items),
            'created_at': o.created_at.isoformat(),
            'payment_method': o.payment_method
        })
        
    for p in pos_sales:
        history.append({
            'id': str(p.id),
            'reference': p.sale_number,
            'type': 'pos',
            'status': p.status.value,
            'total': float(p.total),
            'items_count': len(p.items),
            'created_at': p.created_at.isoformat(),
            'payment_method': p.payment_method or 'Cash'
        })
        
    # Sort interleaved by created_at desc
    history.sort(key=lambda x: x['created_at'], reverse=True)
    
    return success_response({'history': history[:50]})

@api_bp.route('/store/sales/cancel', methods=['POST'])
def store_sales_cancel():
    manager_id = _get_manager_from_request()
    if not manager_id: return error_response('Unauthorized', 403)
    
    data = request.json or {}
    sale_type = data.get('type')
    sale_id = data.get('id')
    
    if not sale_type or not sale_id:
        return error_response('Missing type or id', 400)
        
    if sale_type == 'online':
        order = Order.query.get(sale_id)
        if not order: return error_response('Order not found', 404)
        if order.status in (OrderStatus.cancelled, OrderStatus.returned):
            return error_response('Already cancelled', 400)
            
        order.status = OrderStatus.cancelled
        # Online orders auto-restock via signals or separate job usually, but we must do it manually if we don't have signals
        for item in order.items:
            prod = item.product
            if prod:
                prod.stock_qty += item.quantity
                prod.sold_count = max(0, prod.sold_count - item.quantity)
        db.session.commit()
        return success_response({'message': 'Online order cancelled'})
        
    elif sale_type == 'pos':
        from app.models.inventory import POSSale, POSSaleStatus, StockTransaction, StockTransactionType
        sale = POSSale.query.get(sale_id)
        if not sale: return error_response('POS Sale not found', 404)
        if sale.status != POSSaleStatus.completed:
            return error_response(f'Cannot cancel sale with status {sale.status.value}', 400)
            
        sale.status = POSSaleStatus.refunded
        
        # Restore stock
        for item in sale.items:
            prod = Product.query.get(item.product_id)
            if prod:
                qty = item.quantity
                # create transaction
                txn = StockTransaction(
                    product_id=prod.id,
                    transaction_type=StockTransactionType.return_,
                    quantity_change=qty,
                    quantity_before=prod.stock_qty,
                    quantity_after=prod.stock_qty + qty,
                    reference_id=f"Refund-{sale.sale_number}",
                    notes="POS Sale Cancelled"
                )
                prod.stock_qty += qty
                prod.sold_count = max(0, prod.sold_count - qty)
                db.session.add(txn)
                
        db.session.commit()
        return success_response({'message': 'POS sale cancelled and stock restored'})
        
    return error_response('Invalid sale type', 400)


# ─────────────────────────────────────────────────────────────
# Analytics (Customer Intelligence)
# ─────────────────────────────────────────────────────────────

def _analytics_date_range(period=None):
    """Return (start, end) UTC datetimes for a given period string."""
    local_tz = ZoneInfo('Africa/Addis_Ababa')
    now_local = datetime.now(local_tz)
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = datetime.now(timezone.utc)

    if period == 'today':
        start = today_start.astimezone(timezone.utc)
    elif period == 'yesterday':
        start = (today_start - timedelta(days=1)).astimezone(timezone.utc)
        end = today_start.astimezone(timezone.utc)
    elif period == '7d':
        start = (today_start - timedelta(days=7)).astimezone(timezone.utc)
    elif period == '30d':
        start = (today_start - timedelta(days=30)).astimezone(timezone.utc)
    elif period == '90d':
        start = (today_start - timedelta(days=90)).astimezone(timezone.utc)
    elif period == '1y':
        start = (today_start - timedelta(days=365)).astimezone(timezone.utc)
    else:
        start = (today_start - timedelta(days=30)).astimezone(timezone.utc)
    return start, end


def _prev_period_start(start, end):
    """Return the start of the equivalent previous period."""
    delta = end - start
    return start - delta


@api_bp.route('/store/analytics/kpis')
def analytics_kpis():
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    """JSON: KPI cards with period comparison."""
    period = request.args.get('period', '30d')
    start, end = _analytics_date_range(period)
    prev_start = _prev_period_start(start, end)

    def pct_change(curr, prev):
        if not prev:
            return None
        return round((curr - prev) / prev * 100, 1)

    # Revenue
    revenue = db.session.query(func.sum(Order.total)).filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).scalar() or 0
    prev_revenue = db.session.query(func.sum(Order.total)).filter(
        Order.created_at >= prev_start, Order.created_at < start,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).scalar() or 0

    # Orders
    orders = Order.query.filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).count()
    prev_orders = Order.query.filter(
        Order.created_at >= prev_start, Order.created_at < start,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).count()

    # AOV
    aov = float(revenue) / orders if orders else 0
    prev_aov = float(prev_revenue) / prev_orders if prev_orders else 0

    # Customers
    total_customers = User.query.filter_by(role=UserRole.customer).count()
    new_customers = User.query.filter(
        User.role == UserRole.customer,
        User.created_at >= start, User.created_at < end
    ).count()
    prev_new_customers = User.query.filter(
        User.role == UserRole.customer,
        User.created_at >= prev_start, User.created_at < start
    ).count()

    # Returning customers: those who had orders before the period and also in period
    returning_subq = db.session.query(Order.user_id).filter(
        Order.created_at < start,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).distinct().subquery()
    returning_customers = db.session.query(func.count(distinct(Order.user_id))).filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned]),
        Order.user_id.in_(db.session.query(returning_subq))
    ).scalar() or 0

    # Mini app visits
    visits = db.session.query(func.count(ActivityLog.id)).filter(
        ActivityLog.action == 'mini_app_visit',
        ActivityLog.created_at >= start, ActivityLog.created_at < end
    ).scalar() or 0
    prev_visits = db.session.query(func.count(ActivityLog.id)).filter(
        ActivityLog.action == 'mini_app_visit',
        ActivityLog.created_at >= prev_start, ActivityLog.created_at < start
    ).scalar() or 0

    # Cart adds
    cart_adds = db.session.query(func.count(ActivityLog.id)).filter(
        ActivityLog.action == 'add_to_cart',
        ActivityLog.created_at >= start, ActivityLog.created_at < end
    ).scalar() or 0

    # Product views
    product_views = db.session.query(func.count(ActivityLog.id)).filter(
        ActivityLog.action == 'view_product',
        ActivityLog.created_at >= start, ActivityLog.created_at < end
    ).scalar() or 0

    # Avg items per order
    avg_items_row = db.session.query(func.avg(Order.total_items)).filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned]),
        Order.total_items > 0
    ).scalar()
    avg_items = round(float(avg_items_row), 2) if avg_items_row else 0

    # Avg delivery fee
    avg_del_row = db.session.query(func.avg(Order.delivery_fee)).filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).scalar()
    avg_delivery_fee = round(float(avg_del_row), 0) if avg_del_row else 0

    # Cart abandonment: cart adds that didn't lead to an order in the period
    cart_abandonment_rate = 0
    if cart_adds > 0:
        cart_abandonment_rate = round((1 - min(orders / cart_adds, 1)) * 100, 1)

    # Repeat purchase rate
    customers_with_orders = db.session.query(func.count(distinct(Order.user_id))).filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).scalar() or 0
    repeat_rate = round(returning_customers / customers_with_orders * 100, 1) if customers_with_orders else 0

    # CLV: total revenue / total customers with orders
    clv = round(float(revenue) / customers_with_orders, 0) if customers_with_orders else 0

    return success_response({
        'period': period,
        'revenue': {'value': float(revenue), 'prev': float(prev_revenue), 'change': pct_change(float(revenue), float(prev_revenue))},
        'orders': {'value': orders, 'prev': prev_orders, 'change': pct_change(orders, prev_orders)},
        'aov': {'value': round(aov, 0), 'prev': round(prev_aov, 0), 'change': pct_change(aov, prev_aov)},
        'total_customers': {'value': total_customers},
        'new_customers': {'value': new_customers, 'prev': prev_new_customers, 'change': pct_change(new_customers, prev_new_customers)},
        'returning_customers': {'value': returning_customers},
        'visits': {'value': visits, 'prev': prev_visits, 'change': pct_change(visits, prev_visits)},
        'product_views': {'value': product_views},
        'cart_adds': {'value': cart_adds},
        'cart_abandonment_rate': {'value': cart_abandonment_rate},
        'avg_items': {'value': avg_items},
        'avg_delivery_fee': {'value': avg_delivery_fee},
        'repeat_rate': {'value': repeat_rate},
        'clv': {'value': clv},
    })


@api_bp.route('/store/analytics/funnel')
def analytics_funnel():
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    """JSON: Sales funnel data."""
    period = request.args.get('period', '30d')
    start, end = _analytics_date_range(period)

    app_opens = db.session.query(func.count(ActivityLog.id)).filter(
        ActivityLog.action == 'mini_app_visit',
        ActivityLog.created_at >= start, ActivityLog.created_at < end
    ).scalar() or 0

    product_views = db.session.query(func.count(ActivityLog.id)).filter(
        ActivityLog.action == 'view_product',
        ActivityLog.created_at >= start, ActivityLog.created_at < end
    ).scalar() or 0

    cart_adds = db.session.query(func.count(ActivityLog.id)).filter(
        ActivityLog.action == 'add_to_cart',
        ActivityLog.created_at >= start, ActivityLog.created_at < end
    ).scalar() or 0

    checkouts_started = db.session.query(func.count(ActivityLog.id)).filter(
        ActivityLog.action == 'checkout_started',
        ActivityLog.created_at >= start, ActivityLog.created_at < end
    ).scalar() or 0
    # Fallback: count unique users who started checkout (orders placed)
    if checkouts_started == 0:
        checkouts_started = Order.query.filter(
            Order.created_at >= start, Order.created_at < end
        ).count()

    orders_placed = Order.query.filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).count()

    orders_delivered = Order.query.filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status == OrderStatus.delivered
    ).count()

    def safe_pct(a, b):
        if not b:
            return 0
        return round(a / b * 100, 1)

    def drop_pct(a, b):
        if not b:
            return 0
        return round((1 - a / b) * 100, 1)

    stages = [
        {'label': 'App Opened', 'count': app_opens, 'pct': 100, 'drop': 0},
        {'label': 'Viewed Product', 'count': product_views, 'pct': safe_pct(product_views, app_opens), 'drop': drop_pct(product_views, app_opens)},
        {'label': 'Added to Cart', 'count': cart_adds, 'pct': safe_pct(cart_adds, app_opens), 'drop': drop_pct(cart_adds, product_views)},
        {'label': 'Checkout Started', 'count': checkouts_started, 'pct': safe_pct(checkouts_started, app_opens), 'drop': drop_pct(checkouts_started, cart_adds)},
        {'label': 'Order Placed', 'count': orders_placed, 'pct': safe_pct(orders_placed, app_opens), 'drop': drop_pct(orders_placed, checkouts_started)},
        {'label': 'Delivered', 'count': orders_delivered, 'pct': safe_pct(orders_delivered, app_opens), 'drop': drop_pct(orders_delivered, orders_placed)},
    ]
    return success_response({'stages': stages})


@api_bp.route('/store/analytics/revenue')
def analytics_revenue():
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    """JSON: Revenue chart data (daily, weekly, monthly breakdown)."""
    period = request.args.get('period', '30d')
    start, end = _analytics_date_range(period)
    local_tz = ZoneInfo('Africa/Addis_Ababa')

    # Daily revenue for the period
    from app.models.inventory import POSSale, POSSaleStatus
    days = []
    delta = end - start
    num_days = min(int(delta.total_seconds() / 86400), 90)
    today_start_local = datetime.now(local_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    for i in range(num_days - 1, -1, -1):
        day_local = today_start_local - timedelta(days=i)
        next_day_local = day_local + timedelta(days=1)
        day_utc = day_local.astimezone(timezone.utc)
        next_day_utc = next_day_local.astimezone(timezone.utc)
        if day_utc < start:
            continue

        rev = db.session.query(func.sum(Order.total)).filter(
            Order.created_at >= day_utc, Order.created_at < next_day_utc,
            Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
        ).scalar() or 0

        pos_rev = db.session.query(func.sum(POSSale.total)).filter(
            POSSale.created_at >= day_utc, POSSale.created_at < next_day_utc,
            POSSale.status == POSSaleStatus.completed
        ).scalar() or 0

        ord_cnt = Order.query.filter(
            Order.created_at >= day_utc, Order.created_at < next_day_utc,
            Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
        ).count()

        days.append({
            'date': day_local.strftime('%b %d'),
            'revenue': float(rev) + float(pos_rev),
            'orders': ord_cnt,
        })

    # Revenue by category
    cat_rev = db.session.query(
        Category.name,
        func.sum(OrderItem.price * OrderItem.quantity).label('rev')
    ).select_from(OrderItem
    ).join(Product, OrderItem.product_id == Product.id
    ).join(Category, Product.category_id == Category.id
    ).join(Order, OrderItem.order_id == Order.id
    ).filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).group_by(Category.name).order_by(func.sum(OrderItem.price * OrderItem.quantity).desc()).limit(8).all()

    # Revenue by product
    prod_rev = db.session.query(
        Product.name,
        func.sum(OrderItem.price * OrderItem.quantity).label('rev'),
        func.sum(OrderItem.quantity).label('qty')
    ).select_from(OrderItem
    ).join(Product, OrderItem.product_id == Product.id
    ).join(Order, OrderItem.order_id == Order.id
    ).filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).group_by(Product.name).order_by(func.sum(OrderItem.price * OrderItem.quantity).desc()).limit(10).all()

    return success_response({
        'daily': days,
        'by_category': [{'name': r[0], 'revenue': float(r[1])} for r in cat_rev],
        'by_product': [{'name': r[0], 'revenue': float(r[1]), 'qty': int(r[2])} for r in prod_rev],
    })


@api_bp.route('/store/analytics/products')
def analytics_products():
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    """JSON: Product analytics."""
    period = request.args.get('period', '30d')
    start, end = _analytics_date_range(period)

    prods = db.session.query(
        Product.id,
        Product.name,
        Product.stock_qty,
        Product.price,
        Product.view_count,
        Product.sales_count,
        func.sum(OrderItem.quantity).label('period_qty'),
        func.sum(OrderItem.price * OrderItem.quantity).label('period_rev'),
        func.count(distinct(OrderItem.order_id)).label('period_orders'),
    ).outerjoin(
        OrderItem, and_(
            OrderItem.product_id == Product.id,
        )
    ).outerjoin(
        Order, and_(
            Order.id == OrderItem.order_id,
            Order.created_at >= start, Order.created_at < end,
            Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
        )
    ).filter(Product.is_active == True
    ).group_by(Product.id, Product.name, Product.stock_qty, Product.price,
               Product.view_count, Product.sales_count
    ).order_by(func.sum(OrderItem.price * OrderItem.quantity).desc().nullslast()
    ).limit(50).all()

    # Cart adds per product
    cart_by_product = {r[0]: r[1] for r in db.session.query(
        ActivityLog.entity_id, func.count(ActivityLog.id)
    ).filter(
        ActivityLog.action == 'add_to_cart',
        ActivityLog.created_at >= start, ActivityLog.created_at < end,
        ActivityLog.entity_id.isnot(None)
    ).group_by(ActivityLog.entity_id).all()}

    # Wishlist count per product
    from app.models.order import Wishlist
    wish_by_product = {r[0]: r[1] for r in db.session.query(
        Wishlist.product_id, func.count(Wishlist.id)
    ).group_by(Wishlist.product_id).all()}

    result = []
    for p in prods:
        period_qty = int(p.period_qty or 0)
        period_rev = float(p.period_rev or 0)
        period_orders = int(p.period_orders or 0)
        views = int(p.view_count or 0)
        cart_cnt = cart_by_product.get(p.id, 0)
        conv = round(period_orders / views * 100, 1) if views else 0
        result.append({
            'id': p.id,
            'name': p.name,
            'price': float(p.price),
            'stock': p.stock_qty,
            'views': views,
            'cart_adds': cart_cnt,
            'orders': period_orders,
            'qty_sold': period_qty,
            'revenue': period_rev,
            'conversion': conv,
            'wishlist': wish_by_product.get(p.id, 0),
            'low_stock': p.stock_qty < 5,
        })
    return success_response({'products': result})


@api_bp.route('/store/analytics/segments')
def analytics_segments():
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    """JSON: Customer segmentation."""
    now_utc = datetime.now(timezone.utc)
    local_tz = ZoneInfo('Africa/Addis_Ababa')
    today_start = datetime.now(local_tz).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    d30 = today_start - timedelta(days=30)
    d90 = today_start - timedelta(days=90)
    d180 = today_start - timedelta(days=180)

    customers = User.query.filter_by(role=UserRole.customer).all()

    segments = {
        'VIP': [],
        'High Value': [],
        'Frequent Buyer': [],
        'Returning': [],
        'First Time Buyer': [],
        'Window Shopper': [],
        'Inactive': [],
        'Lost Customer': [],
    }

    for c in customers:
        spent = float(c.total_money_spent or 0)
        orders = int(c.total_orders or 0)
        last_purchase = c.last_purchase_date

        if spent >= 10000 and orders >= 5:
            segments['VIP'].append(c)
        elif spent >= 5000 or orders >= 3:
            segments['High Value'].append(c)
        elif orders >= 2 and last_purchase and last_purchase.replace(tzinfo=timezone.utc) >= d30:
            segments['Frequent Buyer'].append(c)
        elif orders >= 2:
            segments['Returning'].append(c)
        elif orders == 1 and last_purchase and last_purchase.replace(tzinfo=timezone.utc) >= d90:
            segments['First Time Buyer'].append(c)
        elif orders == 0:
            # Check if they visited app or viewed products
            activity_cnt = ActivityLog.query.filter_by(user_id=c.id).count()
            if activity_cnt > 0:
                segments['Window Shopper'].append(c)
        elif orders >= 1 and last_purchase and last_purchase.replace(tzinfo=timezone.utc) < d180:
            segments['Lost Customer'].append(c)
        elif last_purchase and last_purchase.replace(tzinfo=timezone.utc) < d90:
            segments['Inactive'].append(c)
        else:
            segments['Inactive'].append(c)

    result = []
    for seg_name, seg_customers in segments.items():
        if not seg_customers:
            result.append({'segment': seg_name, 'count': 0, 'revenue': 0, 'avg_spend': 0})
            continue
        total_rev = sum(float(c.total_money_spent or 0) for c in seg_customers)
        avg_spend = total_rev / len(seg_customers)
        result.append({
            'segment': seg_name,
            'count': len(seg_customers),
            'revenue': round(total_rev, 0),
            'avg_spend': round(avg_spend, 0),
        })

    return success_response({'segments': result})


@api_bp.route('/store/analytics/cohort')
def analytics_cohort():
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    """JSON: Monthly cohort retention."""
    local_tz = ZoneInfo('Africa/Addis_Ababa')
    now_local = datetime.now(local_tz)
    months = []
    for i in range(5, -1, -1):
        # month start
        month_offset = now_local.month - i
        year_offset = now_local.year
        while month_offset <= 0:
            month_offset += 12
            year_offset -= 1
        months.append((year_offset, month_offset))

    cohort_data = []
    for cohort_year, cohort_month in months:
        # Users who joined in this month
        if cohort_month == 12:
            next_month_start = datetime(cohort_year + 1, 1, 1, tzinfo=local_tz).astimezone(timezone.utc)
        else:
            next_month_start = datetime(cohort_year, cohort_month + 1, 1, tzinfo=local_tz).astimezone(timezone.utc)
        cohort_start = datetime(cohort_year, cohort_month, 1, tzinfo=local_tz).astimezone(timezone.utc)

        cohort_users = User.query.filter(
            User.role == UserRole.customer,
            User.created_at >= cohort_start,
            User.created_at < next_month_start
        ).with_entities(User.id).all()
        cohort_ids = [u.id for u in cohort_users]
        cohort_size = len(cohort_ids)

        retention = []
        if cohort_ids:
            for offset in range(0, 5):
                # Calculate month for retention check
                ret_month = cohort_month + offset
                ret_year = cohort_year
                while ret_month > 12:
                    ret_month -= 12
                    ret_year += 1
                if ret_month == 12:
                    ret_end_month = 1
                    ret_end_year = ret_year + 1
                else:
                    ret_end_month = ret_month + 1
                    ret_end_year = ret_year

                ret_start = datetime(ret_year, ret_month, 1, tzinfo=local_tz).astimezone(timezone.utc)
                ret_end = datetime(ret_end_year, ret_end_month, 1, tzinfo=local_tz).astimezone(timezone.utc)

                active = db.session.query(func.count(distinct(Order.user_id))).filter(
                    Order.user_id.in_(cohort_ids),
                    Order.created_at >= ret_start,
                    Order.created_at < ret_end,
                    Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
                ).scalar() or 0

                pct = round(active / cohort_size * 100, 0) if cohort_size else 0
                retention.append(pct)

        cohort_data.append({
            'label': f"{cohort_year}-{cohort_month:02d}",
            'size': cohort_size,
            'retention': retention,
        })

    return success_response({'cohorts': cohort_data})


@api_bp.route('/store/analytics/geographic')
def analytics_geographic():
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    """JSON: Orders by city/region."""
    period = request.args.get('period', '30d')
    start, end = _analytics_date_range(period)

    from app.models.order import Address
    city_data = db.session.query(
        Address.city,
        func.count(Order.id).label('orders'),
        func.sum(Order.total).label('revenue'),
        func.count(distinct(Order.user_id)).label('customers')
    ).join(Order, Order.address_id == Address.id
    ).filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).group_by(Address.city).order_by(func.count(Order.id).desc()).limit(20).all()

    region_data = db.session.query(
        Address.region,
        func.count(Order.id).label('orders'),
        func.sum(Order.total).label('revenue')
    ).join(Order, Order.address_id == Address.id
    ).filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned]),
        Address.region.isnot(None)
    ).group_by(Address.region).order_by(func.count(Order.id).desc()).limit(15).all()

    return success_response({
        'cities': [{'city': r[0] or 'Unknown', 'orders': r[1], 'revenue': float(r[2] or 0), 'customers': r[3]} for r in city_data],
        'regions': [{'region': r[0] or 'Unknown', 'orders': r[1], 'revenue': float(r[2] or 0)} for r in region_data],
    })


@api_bp.route('/store/analytics/insights')
def analytics_insights():
    manager_id = _get_manager_from_request()
    if not manager_id:
        return error_response('Unauthorized', 403)

    """JSON: Auto-generated AI insights."""
    period = '30d'
    start, end = _analytics_date_range(period)
    prev_start = _prev_period_start(start, end)

    insights = []
    warnings = []
    recommendations = []

    # Revenue trend
    rev_curr = float(db.session.query(func.sum(Order.total)).filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).scalar() or 0)
    rev_prev = float(db.session.query(func.sum(Order.total)).filter(
        Order.created_at >= prev_start, Order.created_at < start,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).scalar() or 0)
    if rev_prev > 0:
        rev_change = round((rev_curr - rev_prev) / rev_prev * 100, 1)
        if rev_change > 0:
            insights.append(f"Revenue increased by {rev_change}% compared to the previous period.")
        elif rev_change < -10:
            warnings.append(f"Revenue dropped by {abs(rev_change)}% compared to previous period. Investigate now.")

    # Top growing category
    cat_curr = db.session.query(
        Category.name, func.sum(OrderItem.quantity).label('qty')
    ).join(Product, OrderItem.product_id == Product.id
    ).join(Category, Product.category_id == Category.id
    ).join(Order, OrderItem.order_id == Order.id
    ).filter(Order.created_at >= start, Order.created_at < end,
             Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).group_by(Category.name).order_by(func.sum(OrderItem.quantity).desc()).first()
    if cat_curr:
        insights.append(f"'{cat_curr.name}' is your top-selling category this period.")

    # Cart abandonment
    cart_adds = db.session.query(func.count(ActivityLog.id)).filter(
        ActivityLog.action == 'add_to_cart',
        ActivityLog.created_at >= start
    ).scalar() or 0
    orders_placed = Order.query.filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).count()
    if cart_adds > 0:
        aband_rate = (1 - min(orders_placed / cart_adds, 1)) * 100
        if aband_rate > 60:
            warnings.append(f"Cart abandonment rate is {aband_rate:.0f}%. Consider follow-up messages after cart additions.")

    # Low stock products
    low_stock = Product.query.filter(
        Product.is_active == True,
        Product.stock_qty > 0,
        Product.stock_qty <= 5
    ).count()
    out_of_stock = Product.query.filter(
        Product.is_active == True,
        Product.stock_qty == 0
    ).count()
    if low_stock > 0:
        warnings.append(f"{low_stock} product(s) have low stock (≤5 units). Restock soon.")
    if out_of_stock > 0:
        warnings.append(f"{out_of_stock} product(s) are out of stock and may be losing sales.")

    # Best performing product
    best_prod = db.session.query(
        Product.name, func.sum(OrderItem.quantity).label('qty')
    ).join(OrderItem, Product.id == OrderItem.product_id
    ).join(Order, Order.id == OrderItem.order_id
    ).filter(Order.created_at >= start, Order.created_at < end,
             Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).group_by(Product.name).order_by(func.sum(OrderItem.quantity).desc()).first()
    if best_prod:
        recommendations.append(f"Feature '{best_prod.name}' in the Telegram channel — it's your top seller this period.")

    # New customers trend
    new_cust = User.query.filter(
        User.role == UserRole.customer, User.created_at >= start
    ).count()
    recommendations.append(f"{new_cust} new customers joined this period. Send a welcome discount to boost first purchase.")

    # Returning customer rate
    all_buying_cust = db.session.query(func.count(distinct(Order.user_id))).filter(
        Order.created_at >= start,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).scalar() or 0
    ret_cust = db.session.query(func.count(distinct(Order.user_id))).filter(
        Order.created_at >= start,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned]),
        Order.user_id.in_(
            db.session.query(Order.user_id).filter(Order.created_at < start).distinct()
        )
    ).scalar() or 0
    if all_buying_cust > 0:
        ret_rate = round(ret_cust / all_buying_cust * 100, 1)
        if ret_rate < 20:
            recommendations.append(f"Returning customer rate is {ret_rate}%. Launch a loyalty promotion to bring customers back.")

    return success_response({
        'insights': insights,
        'warnings': warnings,
        'recommendations': recommendations,
        'generated_at': datetime.now(timezone.utc).isoformat(),
    })


