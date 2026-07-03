import asyncio
import random
import os
from types import SimpleNamespace

from app.extensions import db
from app.models.product import Product, Category
from app.models.user import User, UserRole
from app.models.delivery import Driver
from app.models.order import Cart, Order, OrderItem, Address, OrderStatus
from app.services.order_notifications import notify_store_managers

_app = None


def _get_app():
    global _app
    if _app is None:
        from app import create_app
        config_name = os.getenv('APP_CONFIG') or os.getenv('FLASK_ENV') or 'production'
        _app = create_app(config_name)
    return _app


DRIVER_TG_IDS = {
    tg_id.strip()
    for tg_id in os.getenv('DRIVER_TG_IDS', '851785627,7733651914').split(',')
    if tg_id.strip()
}


def _run_in_app_context(func, *args, **kwargs):
    with _get_app().app_context():
        return func(*args, **kwargs)


async def run_in_db(func, *args, **kwargs):
    return await asyncio.to_thread(_run_in_app_context, func, *args, **kwargs)


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


# Sync DB functions to be executed in the executor
# Always returning namespaces or primitives to avoid DetachedInstanceError


def get_categories():
    cats = Category.query.filter_by(is_active=True).order_by(Category.sort_order).all()
    return [_ns(id=c.id, name=c.name, icon=c.icon) for c in cats]


def get_products(category_id=None, limit=10, offset=0):
    q = Product.query.filter_by(is_active=True)
    if category_id:
        q = q.filter_by(category_id=category_id)
    prods = q.order_by(Product.id.desc()).offset(offset).limit(limit).all()

    result = []
    for p in prods:
        result.append(_ns(
            id=p.id,
            name=p.name,
            price=float(p.current_price()),
            category_name=p.category.name if p.category else 'Products',
            category_id=p.category_id,
            category=_ns(id=p.category.id, name=p.category.name, icon=p.category.icon) if p.category else None,
            current_price=lambda p=p: float(p.current_price()),
            primary_image=lambda p=p: p.primary_image(),
            age_label=lambda p=p: p.age_label(),
            short_description=p.short_description,
        ))
    return result


def get_product_by_id(product_id):
    p = Product.query.get(product_id)
    if not p:
        return None
    return _ns(
        id=p.id,
        name=p.name,
        price=float(p.current_price()),
        age_label=p.age_label(),
        short_description=p.short_description,
        category_id=p.category_id,
        primary_image=p.primary_image(),
        current_price=lambda p=p: float(p.current_price()),
    )


def get_or_create_user(telegram_id, username, full_name):
    user = User.query.filter_by(telegram_id=str(telegram_id)).first()
    if not user:
        is_driver = str(telegram_id) in DRIVER_TG_IDS
        user = User(
            telegram_id=str(telegram_id),
            telegram_username=username,
            full_name=full_name,
            role=UserRole.driver if is_driver else UserRole.customer,
        )
        db.session.add(user)
        db.session.commit()
    elif str(telegram_id) in DRIVER_TG_IDS and user.role != UserRole.driver:
        user.role = UserRole.driver
        if not user.driver_profile:
            db.session.add(Driver(user_id=user.id, is_available=True, is_active=True))
        db.session.commit()
    return {'id': user.id, 'full_name': user.full_name}


def is_driver_user(telegram_id):
    user = User.query.filter_by(telegram_id=str(telegram_id)).first()
    if not user:
        return str(telegram_id) in DRIVER_TG_IDS
    if user.role == UserRole.driver or user.driver_profile:
        return True
    return str(telegram_id) in DRIVER_TG_IDS


def is_manager_user(telegram_id):
    """Check if telegram_id is a manager or admin in the DB."""
    user = User.query.filter_by(telegram_id=str(telegram_id)).first()
    if not user:
        return False
    return user.role.value in ('admin', 'manager')


def add_to_cart(telegram_id, product_id, quantity=1):
    user = User.query.filter_by(telegram_id=str(telegram_id)).first()
    if not user:
        return False, 'User not found'

    cart_item = Cart.query.filter_by(user_id=user.id, product_id=product_id).first()
    if cart_item:
        cart_item.quantity += quantity
    else:
        cart_item = Cart(user_id=user.id, product_id=product_id, quantity=quantity)
        db.session.add(cart_item)

    db.session.commit()
    return True, 'Added to cart'


def get_cart_items(telegram_id):
    user = User.query.filter_by(telegram_id=str(telegram_id)).first()
    if not user:
        return []
    items = Cart.query.filter_by(user_id=user.id).all()
    result = []
    for item in items:
        prod = item.product
        result.append(_ns(
            id=item.id,
            product_id=item.product.id,
            product_name=item.product.name,
            quantity=item.quantity,
            price=float(item.product.current_price()),
            product=_ns(
                id=prod.id,
                name=prod.name,
                price=float(prod.current_price()),
                current_price=lambda prod=prod: float(prod.current_price()),
                primary_image=lambda prod=prod: prod.primary_image(),
            ) if prod else None,
        ))
    return result


def update_cart_item(telegram_id, cart_id, quantity_change):
    user = User.query.filter_by(telegram_id=str(telegram_id)).first()
    if not user:
        return
    cart_item = Cart.query.filter_by(id=cart_id, user_id=user.id).first()
    if cart_item:
        cart_item.quantity += quantity_change
        if cart_item.quantity <= 0:
            db.session.delete(cart_item)
        db.session.commit()


def clear_cart(telegram_id):
    user = User.query.filter_by(telegram_id=str(telegram_id)).first()
    if user:
        Cart.query.filter_by(user_id=user.id).delete()
        db.session.commit()


def place_order(telegram_id, phone, location):
    user = User.query.filter_by(telegram_id=str(telegram_id)).first()
    if not user:
        return False, 'User not found'

    cart_items = Cart.query.filter_by(user_id=user.id).all()
    if not cart_items:
        return False, 'Cart is empty'

    subtotal = sum(item.product.current_price() * item.quantity for item in cart_items)
    delivery_fee = 50
    if subtotal > 1000:
        delivery_fee = 0
    total = subtotal + delivery_fee

    order_num = f"LKM-2024-{random.randint(10000, 99999)}"

    recipient = user.full_name if user.full_name else 'Customer'
    address = Address(user_id=user.id, recipient_name=recipient, phone=phone, specific_location=location)
    db.session.add(address)
    db.session.flush()

    order = Order(
        order_number=order_num,
        user_id=user.id,
        status=OrderStatus.pending,
        subtotal=subtotal,
        delivery_fee=delivery_fee,
        total=total,
        address_id=address.id,
    )
    db.session.add(order)
    db.session.flush()

    for item in cart_items:
        order_item = OrderItem(
            order_id=order.id,
            product_id=item.product_id,
            quantity=item.quantity,
            unit_price=item.product.current_price(),
            total_price=item.product.current_price() * item.quantity,
        )
        db.session.add(order_item)

    Cart.query.filter_by(user_id=user.id).delete()
    db.session.commit()

    try:
        order_items = []
        for item in cart_items:
            if not item.product:
                continue
            order_items.append({
                'product': item.product,
                'qty': item.quantity,
                'unit_price': float(item.product.current_price()),
                'item_total': float(item.product.current_price()) * item.quantity,
            })
        notify_store_managers(order, order_items, address, 'cod', 0)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('[order_notify] Failed to notify managers from bot checkout')

    return True, order_num


def get_user_orders(telegram_id):
    user = User.query.filter_by(telegram_id=str(telegram_id)).first()
    if not user:
        return []
    orders = Order.query.filter_by(user_id=user.id).order_by(Order.id.desc()).limit(5).all()
    result = []
    for o in orders:
        result.append(_ns(
            order_number=o.order_number,
            status=_ns(value=o.status.value, name=o.status.name),
            total=float(o.total),
            created_at=o.created_at,
        ))
    return result


def get_order_by_number(order_num):
    o = Order.query.filter_by(order_number=order_num).first()
    if not o:
        return None
    return _ns(
        order_number=o.order_number,
        status=_ns(value=o.status.value, name=o.status.name),
        total=float(o.total),
        created_at=o.created_at,
    )


def cancel_order(order_id, user_id):
    order = Order.query.filter_by(id=order_id, user_id=user_id).first()
    if order and order.status == OrderStatus.pending:
        order.status = OrderStatus.cancelled
        db.session.commit()
        return True
    return False


def update_driver_location(telegram_id, lat, lng):
    user = User.query.filter_by(telegram_id=str(telegram_id)).first()
    if user and user.driver_profile:
        driver = user.driver_profile
        driver.current_lat = lat
        driver.current_lng = lng
        db.session.commit()
        return True
    return False
