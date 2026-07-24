from datetime import datetime, timezone
import math
from flask import request
from app.blueprints.api import api_bp
from app.extensions import db
from app.models.delivery import Driver, Delivery, DeliveryStatus
from app.models.order import Order, OrderStatus
from app.utils import success_response, error_response


@api_bp.route('/delivery/<int:order_id>/track', methods=['GET'])
def track_delivery(order_id):
    order = db.session.get(Order, order_id)
    if not order:
        return error_response('Order not found', 404)
    delivery = order.delivery
    return success_response({
        'order_number': order.order_number,
        'order_status': order.status.value,
        'delivery': delivery.to_dict() if delivery else None,
    })


@api_bp.route('/delivery/<int:delivery_id>/status', methods=['PUT'])
def update_delivery_status(delivery_id):
    data = request.get_json() or {}
    delivery = db.session.get(Delivery, delivery_id)
    if not delivery:
        return error_response('Delivery not found', 404)
    status = data.get('status')
    try:
        delivery.status = DeliveryStatus[status]
    except KeyError:
        return error_response('Invalid status')
    if status == 'picked_up':
        delivery.picked_up_at = datetime.now(timezone.utc)
        delivery.order.status = OrderStatus.out_for_delivery
    elif status == 'delivered':
        delivery.delivered_at = datetime.now(timezone.utc)
        delivery.order.status = OrderStatus.delivered
        if delivery.driver:
            delivery.driver.total_deliveries += 1
    db.session.commit()
    return success_response(delivery.to_dict(), 'Status updated')


@api_bp.route('/delivery/location/legacy', methods=['POST'])
def update_driver_location_legacy():
    data = request.get_json() or {}
    driver_id = data.get('driver_id')
    driver = db.session.get(Driver, driver_id)
    if not driver:
        return error_response('Driver not found', 404)
    driver.current_lat = data.get('lat')
    driver.current_lng = data.get('lng')
    db.session.commit()
    return success_response(message='Location updated')


STORE_LAT = 8.956133150795546
STORE_LNG = 38.78781484232836


def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371  # Radius of the earth in km
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = math.sin(dLat/2) * math.sin(dLat/2) + \
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.sin(dLon/2) * math.sin(dLon/2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    d = R * c # Distance in km
    return d


@api_bp.route('/delivery/calculate', methods=['POST'])
def calculate_price():
    data = request.get_json() or {}
    lat = data.get('lat')
    lng = data.get('lng')
    distance_km = data.get('distance_km')

    if distance_km is not None:
        try:
            distance = float(distance_km)
        except ValueError:
            return error_response('Invalid distance')
    else:
        if lat is None or lng is None:
            return error_response('Latitude and longitude are required')
        try:
            lat = float(lat)
            lng = float(lng)
        except ValueError:
            return error_response('Invalid coordinates')
        distance = calculate_distance(STORE_LAT, STORE_LNG, lat, lng)

    # Pricing: 200 ETB for first 5km, then 40 ETB per additional km
    if distance <= 5:
        price = 200
    else:
        extra_km = distance - 5
        price = 200 + (extra_km * 40)

    return success_response({
        'distance_km': round(distance, 2),
        'price': round(price, 2),
        'store_lat': STORE_LAT,
        'store_lng': STORE_LNG
    })
