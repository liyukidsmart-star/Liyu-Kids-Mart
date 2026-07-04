from flask import render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from app.blueprints.orders import orders_bp
from app.extensions import db
from app.models.order import Order, OrderStatus
from app.services.loyalty_service import apply_order_status_change


@orders_bp.route('/')
@login_required
def my_orders():
    status_filter = request.args.get('status', '')
    q = Order.query.filter_by(user_id=current_user.id)
    if status_filter:
        try:
            q = q.filter_by(status=OrderStatus[status_filter])
        except KeyError:
            pass
    orders = q.order_by(Order.created_at.desc()).all()
    return render_template('orders/my_orders.html', orders=orders, status_filter=status_filter)


@orders_bp.route('/<order_number>')
def detail(order_number):
    order = Order.query.filter_by(order_number=order_number).first_or_404()
    # Allow access: owner or admin
    if current_user.is_authenticated:
        if order.user_id and order.user_id != current_user.id:
            if current_user.role.value not in ('admin', 'manager'):
                abort(403)
    return render_template('orders/order_detail.html', order=order)


@orders_bp.route('/<order_number>/cancel', methods=['POST'])
@login_required
def cancel(order_number):
    order = Order.query.filter_by(order_number=order_number, user_id=current_user.id).first_or_404()
    if order.status.value not in ('pending', 'confirmed'):
        flash('This order cannot be cancelled.', 'danger')
        return redirect(url_for('orders.detail', order_number=order_number))
    previous_status = order.status
    order.status = OrderStatus.cancelled
    order.updated_at = datetime.now(timezone.utc)
    reversal_result = apply_order_status_change(current_user, order, OrderStatus.cancelled, previous_status)
    # Restore stock
    for item in order.items:
        if item.product:
            item.product.stock_qty += item.quantity
    db.session.commit()
    flash(f'Order #{order.order_number} has been cancelled.', 'success')
    return redirect(url_for('orders.my_orders'))
