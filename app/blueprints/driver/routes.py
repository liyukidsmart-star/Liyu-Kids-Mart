from functools import wraps
from flask import render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from app.blueprints.driver import driver_bp
from app.extensions import db
from app.models.delivery import Driver, Delivery, DeliveryStatus
from app.models.order import Order, OrderStatus


def driver_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.role.value not in ('driver', 'admin'):
            flash('Access denied.', 'danger')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated


@driver_bp.route('/')
@driver_required
def dashboard():
    driver = Driver.query.filter_by(user_id=current_user.id).first()
    if not driver:
        flash('Driver profile not found. Please contact admin.', 'danger')
        return redirect(url_for('main.index'))
    active_deliveries = Delivery.query.filter_by(
        driver_id=driver.id
    ).filter(Delivery.status.in_([DeliveryStatus.assigned, DeliveryStatus.picked_up,
                                   DeliveryStatus.in_transit])).all()
    return render_template('driver/dashboard.html', driver=driver,
                           active_deliveries=active_deliveries)


@driver_bp.route('/deliveries')
@driver_required
def deliveries():
    driver = Driver.query.filter_by(user_id=current_user.id).first()
    if not driver:
        return redirect(url_for('driver.dashboard'))
    all_deliveries = Delivery.query.filter_by(driver_id=driver.id).order_by(
        Delivery.created_at.desc()).limit(50).all()
    return render_template('driver/deliveries.html', deliveries=all_deliveries, driver=driver)


@driver_bp.route('/deliveries/<int:delivery_id>/pickup', methods=['POST'])
@driver_required
def mark_pickup(delivery_id):
    from datetime import datetime, timezone
    delivery = db.session.get(Delivery, delivery_id)
    if delivery:
        delivery.status = DeliveryStatus.picked_up
        delivery.picked_up_at = datetime.now(timezone.utc)
        delivery.order.status = OrderStatus.out_for_delivery
        db.session.commit()
        flash('Marked as picked up!', 'success')
    return redirect(url_for('driver.dashboard'))


@driver_bp.route('/deliveries/<int:delivery_id>/deliver', methods=['POST'])
@driver_required
def mark_delivered(delivery_id):
    from datetime import datetime, timezone
    delivery = db.session.get(Delivery, delivery_id)
    if delivery:
        delivery.status = DeliveryStatus.delivered
        delivery.delivered_at = datetime.now(timezone.utc)
        delivery.order.status = OrderStatus.delivered
        if delivery.driver:
            delivery.driver.total_deliveries = (delivery.driver.total_deliveries or 0) + 1
        db.session.commit()
        flash('🎉 Delivery completed!', 'success')
    return redirect(url_for('driver.dashboard'))


@driver_bp.route('/location/update', methods=['POST'])
@driver_required
def update_location():
    driver = Driver.query.filter_by(user_id=current_user.id).first()
    if driver:
        data = request.get_json() or {}
        driver.current_lat = data.get('lat')
        driver.current_lng = data.get('lng')
        db.session.commit()
    return jsonify({'success': True})
