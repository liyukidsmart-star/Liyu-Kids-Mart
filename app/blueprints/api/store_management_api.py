"""
Store Management Mini App API — Liyu Kids Mart
All API endpoints for the store management Telegram Mini App.
Protected by MANAGER_TG_IDS env variable (comma-separated Telegram IDs).
"""
import os
from datetime import datetime, timezone, timedelta
from flask import request
from app.blueprints.api import api_bp
from app.extensions import db
from app.models.order import Order, OrderStatus, OrderItem
from app.models.product import Product
from app.models.delivery import Driver, Delivery, DeliveryStatus
from app.models.user import User, UserRole
from app.utils import success_response, error_response
from app.models.loyalty import LoyaltySettings
from app.services.loyalty_service import apply_order_status_change

MANAGER_TG_IDS = [m.strip() for m in os.getenv('MANAGER_TG_IDS', '401413271').split(',') if m.strip()]


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

    # Today's stats
    today_orders = _active_orders_since(today_start)
    week_orders = _active_orders_since(week_start)
    bi_weekly_orders = _active_orders_since(bi_weekly_start)
    monthly_orders = _active_orders_since(monthly_start)
    all_pending = Order.query.filter(Order.status == OrderStatus.pending).count()
    all_confirmed = Order.query.filter(Order.status == OrderStatus.confirmed).count()

    today_revenue = sum(float(o.total) for o in today_orders)
    week_revenue = sum(float(o.total) for o in week_orders)
    bi_weekly_revenue = sum(float(o.total) for o in bi_weekly_orders)
    monthly_revenue = sum(float(o.total) for o in monthly_orders)

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
                'total': float(o.total),
                'subtotal': float(o.subtotal),
                'discount_amount': float(o.discount_amount or 0),
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
        'lat': order.address.lat if order.address else None,
        'lng': order.address.lng if order.address else None,
        'items': items,
        'subtotal': float(order.subtotal),
        'discount_amount': float(order.discount_amount or 0),
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
    reversal_result = apply_order_status_change(order.user, order, new_status, previous_status)
    order.status = new_status
    order.updated_at = datetime.now(timezone.utc)

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
