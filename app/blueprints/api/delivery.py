from datetime import datetime, timezone
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


@api_bp.route('/delivery/location', methods=['POST'])
def update_driver_location():
    data = request.get_json() or {}
    driver_id = data.get('driver_id')
    driver = db.session.get(Driver, driver_id)
    if not driver:
        return error_response('Driver not found', 404)
    driver.current_lat = data.get('lat')
    driver.current_lng = data.get('lng')
    db.session.commit()
    return success_response(message='Location updated')
