"""
Driver Mini App API — Liyu Kids Mart
Endpoints for the delivery driver's Telegram Mini App.
"""
import os
import math
import json
import httpx
from datetime import datetime, timezone
from flask import request
from app.blueprints.api import api_bp
from app.extensions import db
from app.models.delivery import Driver, Delivery, DeliveryStatus
from app.models.order import Order, OrderStatus, OrderItem
from app.models.user import User
from app.utils import success_response, error_response

DRIVER_TELEGRAM_ID = '851785627'
STORE_LAT = 8.956133150795546
STORE_LNG = 38.78781484232836


# ── Helpers ──────────────────────────────────────────────

def _haversine(lat1, lng1, lat2, lng2):
    R = 6371
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (math.sin(d_lat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(d_lng / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _nearest_neighbor_route(start_lat, start_lng, stops):
    """Greedy nearest-neighbor TSP to order delivery stops."""
    remaining = list(stops)
    route = []
    cur_lat, cur_lng = start_lat, start_lng
    while remaining:
        closest = min(remaining, key=lambda s: _haversine(cur_lat, cur_lng, s['lat'], s['lng']))
        route.append(closest)
        cur_lat, cur_lng = closest['lat'], closest['lng']
        remaining.remove(closest)
    return route


async def _send_telegram_message(telegram_id, text):
    """Push a Telegram message to a specific user."""
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    if not token or not telegram_id:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f'https://api.telegram.org/bot{token}/sendMessage',
                json={'chat_id': telegram_id, 'text': text, 'parse_mode': 'Markdown'},
                timeout=5
            )
    except Exception:
        pass


def _notify_customer(order, status_msg):
    """Synchronous Telegram notification via httpx."""
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    if not token or not order.user:
        return
    telegram_id = order.user.telegram_id
    if not telegram_id:
        return
    try:
        httpx.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={
                'chat_id': telegram_id,
                'text': status_msg,
                'parse_mode': 'Markdown'
            },
            timeout=5
        )
    except Exception:
        pass


def _get_driver():
    """Get driver by Telegram ID in request body or query param."""
    telegram_id = (request.get_json(silent=True) or {}).get('telegram_id') or request.args.get('telegram_id')
    if not telegram_id:
        return None
    user = User.query.filter_by(telegram_id=str(telegram_id)).first()
    if not user:
        return None
    return Driver.query.filter_by(user_id=user.id).first()


# ── Driver App Auth Check ─────────────────────────────────

@api_bp.route('/driver/check-auth', methods=['POST'])
def driver_check_auth():
    """Check if the telegram_id belongs to the designated driver."""
    data = request.get_json(silent=True) or {}
    telegram_id = str(data.get('telegram_id', ''))
    if telegram_id == DRIVER_TELEGRAM_ID:
        # Auto-create driver profile if needed
        user = User.query.filter_by(telegram_id=telegram_id).first()
        if user:
            driver = Driver.query.filter_by(user_id=user.id).first()
            if not driver:
                driver = Driver(user_id=user.id, is_available=True, is_active=True)
                db.session.add(driver)
                db.session.commit()
        return success_response({'authorized': True, 'is_driver': True})
    return success_response({'authorized': False, 'is_driver': False})


# ── Live Location Update ──────────────────────────────────

@api_bp.route('/driver/location', methods=['POST'])
def update_driver_location():
    """Update driver's GPS location (called every 5s from driver app)."""
    data = request.get_json(silent=True) or {}
    telegram_id = str(data.get('telegram_id', ''))
    lat = data.get('lat')
    lng = data.get('lng')

    if not lat or not lng:
        return error_response('Missing coordinates', 400)

    user = User.query.filter_by(telegram_id=telegram_id).first()
    if not user:
        return error_response('User not found', 404)

    driver = Driver.query.filter_by(user_id=user.id).first()
    if not driver:
        return error_response('Driver profile not found', 404)

    driver.current_lat = float(lat)
    driver.current_lng = float(lng)
    db.session.commit()
    return success_response({'updated': True})


# ── Driver Live Location (for customers) ─────────────────

@api_bp.route('/driver/live-location', methods=['GET'])
def get_driver_live_location():
    """Get driver's current GPS coordinates for customer tracking."""
    user = User.query.filter_by(telegram_id=DRIVER_TELEGRAM_ID).first()
    if not user:
        return success_response({'lat': None, 'lng': None})
    driver = Driver.query.filter_by(user_id=user.id).first()
    if not driver:
        return success_response({'lat': None, 'lng': None})
    return success_response({
        'lat': driver.current_lat,
        'lng': driver.current_lng,
        'updated_at': datetime.now(timezone.utc).isoformat()
    })


# ── Active Orders for Driver ──────────────────────────────

@api_bp.route('/driver/orders', methods=['GET'])
def driver_active_orders():
    """Return all pending/confirmed/packed orders for the driver to pick up."""
    active_statuses = [
        OrderStatus.pending, OrderStatus.confirmed,
        OrderStatus.packed, OrderStatus.out_for_delivery
    ]
    orders = Order.query.filter(Order.status.in_(active_statuses)).order_by(
        Order.created_at.asc()
    ).all()

    result = []
    for order in orders:
        addr = order.address
        if not addr:
            continue
        lat = addr.lat if hasattr(addr, 'lat') and addr.lat else None
        lng = addr.lng if hasattr(addr, 'lng') and addr.lng else None
        if not lat or not lng:
            continue

        items_summary = []
        for item in order.items:
            snap = {}
            if item.product_snapshot:
                try:
                    snap = json.loads(item.product_snapshot)
                except Exception:
                    pass
            items_summary.append({
                'name': snap.get('name', item.product.name if item.product else 'Item'),
                'qty': item.quantity,
                'price': float(item.unit_price),
                'total': float(item.total_price),
            })

        result.append({
            'order_id': order.id,
            'order_number': order.order_number,
            'status': order.status.value,
            'total': float(order.total),
            'delivery_fee': float(order.delivery_fee or 0),
            'payment_method': order.payment_method.value if order.payment_method else 'cod',
            'recipient_name': addr.recipient_name or '',
            'phone': addr.phone or '',
            'specific_location': addr.specific_location or '',
            'sub_city': addr.sub_city or '',
            'lat': lat,
            'lng': lng,
            'items': items_summary,
            'created_at': order.created_at.strftime('%H:%M, %b %d') if order.created_at else '',
        })

    return success_response({'orders': result})


# ── Optimal Route ─────────────────────────────────────────

@api_bp.route('/driver/route', methods=['POST'])
def driver_optimal_route():
    """
    Calculate optimal delivery route using nearest-neighbor algorithm.
    Body: { driver_lat, driver_lng, order_ids: [list of selected order ids] }
    Returns ordered list of stops.
    """
    data = request.get_json(silent=True) or {}
    driver_lat = data.get('driver_lat', STORE_LAT)
    driver_lng = data.get('driver_lng', STORE_LNG)
    order_ids = data.get('order_ids', [])

    if not order_ids:
        return error_response('No orders selected', 400)

    orders = Order.query.filter(Order.id.in_(order_ids)).all()
    stops = []
    for order in orders:
        addr = order.address
        if not addr or not hasattr(addr, 'lat') or not addr.lat:
            continue
        stops.append({
            'order_id': order.id,
            'order_number': order.order_number,
            'lat': addr.lat,
            'lng': addr.lng,
            'recipient_name': addr.recipient_name or '',
            'phone': addr.phone or '',
            'total': float(order.total),
        })

    if not stops:
        return error_response('No deliverable orders found', 400)

    optimized = _nearest_neighbor_route(float(driver_lat), float(driver_lng), stops)
    return success_response({'route': optimized, 'total_stops': len(optimized)})


# ── Update Order Status ───────────────────────────────────

@api_bp.route('/driver/orders/<int:order_id>/status', methods=['PUT'])
def driver_update_order_status(order_id):
    """
    Update order status from driver app.
    Statuses: confirmed, packed, out_for_delivery, delivered
    """
    data = request.get_json(silent=True) or {}
    new_status_str = data.get('status', '')

    status_map = {
        'confirmed': OrderStatus.confirmed,
        'packed': OrderStatus.packed,
        'out_for_delivery': OrderStatus.out_for_delivery,
        'delivered': OrderStatus.delivered,
    }

    new_status = status_map.get(new_status_str)
    if not new_status:
        return error_response('Invalid status', 400)

    order = db.session.get(Order, order_id)
    if not order:
        return error_response('Order not found', 404)

    old_status = order.status
    order.status = new_status
    db.session.commit()

    # Send notification to customer
    msg_map = {
        'confirmed': f"✅ *Order Confirmed!*\n\nYour order *#{order.order_number}* has been confirmed and is being prepared. 🛍️",
        'packed': f"📦 *Order Packed!*\n\nYour order *#{order.order_number}* is packed and ready for pickup by our delivery driver!",
        'out_for_delivery': (
            f"🛵 *On the Way!*\n\nYour order *#{order.order_number}* is on its way to you!\n\n"
            f"📍 Track your driver's live location in the Liyu Kids Mart Mini App."
        ),
        'delivered': (
            f"🎉 *Order Delivered!*\n\nYour order *#{order.order_number}* has been delivered.\n\n"
            f"Thank you for shopping at Liyu Kids Mart! 💚"
        ),
    }

    notification = msg_map.get(new_status_str)
    if notification:
        _notify_customer(order, notification)

    return success_response({
        'order_id': order_id,
        'old_status': old_status.value,
        'new_status': new_status.value,
        'notified': bool(notification)
    }, 'Status updated successfully')


# ── Customer Order Tracking ───────────────────────────────

@api_bp.route('/track/<order_number>', methods=['GET'])
def track_order_public(order_number):
    """Public endpoint for customers to track their order status + driver location."""
    order = Order.query.filter_by(order_number=order_number.upper()).first()
    if not order:
        return error_response('Order not found', 404)

    driver_lat = None
    driver_lng = None
    if order.status == OrderStatus.out_for_delivery:
        # Return driver's live location
        drv_user = User.query.filter_by(telegram_id=DRIVER_TELEGRAM_ID).first()
        if drv_user:
            drv = Driver.query.filter_by(user_id=drv_user.id).first()
            if drv:
                driver_lat = drv.current_lat
                driver_lng = drv.current_lng

    addr = order.address
    return success_response({
        'order_number': order.order_number,
        'status': order.status.value,
        'total': float(order.total),
        'delivery_lat': addr.lat if addr and hasattr(addr, 'lat') else None,
        'delivery_lng': addr.lng if addr and hasattr(addr, 'lng') else None,
        'recipient_name': addr.recipient_name if addr else '',
        'driver_lat': driver_lat,
        'driver_lng': driver_lng,
        'created_at': order.created_at.strftime('%b %d, %Y %H:%M') if order.created_at else '',
    })
