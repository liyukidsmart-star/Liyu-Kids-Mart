import enum
import json
from datetime import datetime, timezone
from app.extensions import db


class OrderStatus(enum.Enum):
    pending = 'pending'
    confirmed = 'confirmed'
    packed = 'packed'
    out_for_delivery = 'out_for_delivery'
    delivered = 'delivered'
    cancelled = 'cancelled'
    returned = 'returned'


class PaymentMethod(enum.Enum):
    cod = 'cod'
    chapa = 'chapa'
    telebirr = 'telebirr'


class PaymentStatus(enum.Enum):
    pending = 'pending'
    completed = 'completed'
    failed = 'failed'
    refunded = 'refunded'


class DiscountType(enum.Enum):
    percentage = 'percentage'
    fixed = 'fixed'


class Address(db.Model):
    __tablename__ = 'addresses'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    label = db.Column(db.String(50), default='Home')
    recipient_name = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    city = db.Column(db.String(100), default='Addis Ababa')
    sub_city = db.Column(db.String(100), nullable=True)
    # Regional delivery details used when the customer is outside Addis Ababa.
    region = db.Column(db.String(100), nullable=True)
    city_town = db.Column(db.String(100), nullable=True)
    delivery_scope = db.Column(db.String(20), default='addis', nullable=False)
    woreda = db.Column(db.String(100), nullable=True)
    specific_location = db.Column(db.Text, nullable=True)
    lat = db.Column(db.Float, nullable=True)
    lng = db.Column(db.Float, nullable=True)
    is_default = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', back_populates='addresses')
    orders = db.relationship('Order', back_populates='address')

    def to_dict(self):
        return {
            'id': self.id,
            'label': self.label,
            'recipient_name': self.recipient_name,
            'phone': self.phone,
            'city': self.city,
            'sub_city': self.sub_city,
            'region': self.region,
            'city_town': self.city_town,
            'delivery_scope': self.delivery_scope,
            'woreda': self.woreda,
            'specific_location': self.specific_location,
            'lat': self.lat,
            'lng': self.lng,
            'is_default': self.is_default,
        }


class Coupon(db.Model):
    __tablename__ = 'coupons'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    description = db.Column(db.String(255), nullable=True)
    discount_type = db.Column(db.Enum(DiscountType), default=DiscountType.percentage)
    discount_value = db.Column(db.Numeric(10, 2), nullable=False)
    min_order_amount = db.Column(db.Numeric(10, 2), default=0)
    max_discount = db.Column(db.Numeric(10, 2), nullable=True)
    max_uses = db.Column(db.Integer, nullable=True)
    used_count = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    orders = db.relationship('Order', back_populates='coupon')

    def is_valid(self, order_amount):
        from datetime import datetime, timezone
        if not self.is_active:
            return False, 'Coupon is inactive'
        if self.expires_at and datetime.now(timezone.utc) > self.expires_at.replace(tzinfo=timezone.utc):
            return False, 'Coupon has expired'
        if self.max_uses and self.used_count >= self.max_uses:
            return False, 'Coupon usage limit reached'
        if order_amount < float(self.min_order_amount):
            return False, f'Minimum order amount is ETB {self.min_order_amount:,.0f}'
        return True, 'Valid'

    def calculate_discount(self, amount):
        if self.discount_type == DiscountType.percentage:
            d = amount * float(self.discount_value) / 100
            if self.max_discount:
                d = min(d, float(self.max_discount))
            return round(d, 2)
        return min(float(self.discount_value), amount)

    def to_dict(self):
        return {
            'id': self.id,
            'code': self.code,
            'description': self.description,
            'discount_type': self.discount_type.value,
            'discount_value': float(self.discount_value),
            'min_order_amount': float(self.min_order_amount),
        }


class Order(db.Model):
    __tablename__ = 'orders'

    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(30), unique=True, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    status = db.Column(db.Enum(OrderStatus), default=OrderStatus.pending, nullable=False)
    subtotal = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    delivery_fee = db.Column(db.Numeric(10, 2), default=50)
    discount_amount = db.Column(db.Numeric(10, 2), default=0)
    total = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    payment_method = db.Column(db.Enum(PaymentMethod), default=PaymentMethod.cod)
    payment_status = db.Column(db.String(20), default='pending')
    notes = db.Column(db.Text, nullable=True)
    address_id = db.Column(db.Integer, db.ForeignKey('addresses.id'), nullable=True)
    coupon_id = db.Column(db.Integer, db.ForeignKey('coupons.id'), nullable=True)
    # Snapshot for address in case it changes later
    delivery_address_snapshot = db.Column(db.Text, nullable=True)
    # ── Loyalty snapshots ────────────────────────────────────
    savings_amount = db.Column(db.Numeric(10, 2), default=0)        # Birr saved on this order
    reward_earned = db.Column(db.Integer, default=0)                # points awarded
    loyalty_level_id_after = db.Column(db.Integer, db.ForeignKey('loyalty_levels.id'), nullable=True)
    lifetime_total_after = db.Column(db.Numeric(14, 2), nullable=True)  # running lifetime total
    total_items = db.Column(db.Integer, default=0)                  # number of items in this order
    # ── Per-discount-type amounts (for analytics) ─────────────────
    spending_discount_amount = db.Column(db.Numeric(10, 2), default=0)  # Birr saved from spending threshold
    qty_discount_amount_saved = db.Column(db.Numeric(10, 2), default=0) # Birr saved from quantity rules
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    user = db.relationship('User', back_populates='orders')
    items = db.relationship('OrderItem', back_populates='order', cascade='all, delete-orphan')
    address = db.relationship('Address', back_populates='orders')
    coupon = db.relationship('Coupon', back_populates='orders')
    payment = db.relationship('Payment', back_populates='order', uselist=False, cascade='all, delete-orphan')
    delivery = db.relationship('Delivery', back_populates='order', uselist=False, cascade='all, delete-orphan')
    reward_transactions = db.relationship('RewardTransaction', back_populates='order', lazy='dynamic')

    STATUS_LABELS = {
        'pending': 'Pending',
        'confirmed': 'Confirmed',
        'packed': 'Packed',
        'out_for_delivery': 'Out for Delivery',
        'delivered': 'Delivered',
        'cancelled': 'Cancelled',
        'returned': 'Returned',
    }

    STATUS_COLORS = {
        'pending': 'warning',
        'confirmed': 'info',
        'packed': 'primary',
        'out_for_delivery': 'orange',
        'delivered': 'success',
        'cancelled': 'danger',
        'returned': 'secondary',
    }

    @property
    def delivery_scope_value(self):
        if self.address and self.address.delivery_scope:
            return self.address.delivery_scope
        return 'addis'

    @property
    def is_regional(self):
        return self.delivery_scope_value == 'regional'

    def status_label(self):
        return self.STATUS_LABELS.get(self.status.value, self.status.value)

    def status_color(self):
        return self.STATUS_COLORS.get(self.status.value, 'secondary')

    def to_dict(self, include_items=False):
        d = {
            'id': self.id,
            'order_number': self.order_number,
            'user_id': self.user_id,
            'customer_name': self.user.full_name if self.user else '',
            'status': self.status.value,
            'status_label': self.status_label(),
            'subtotal': float(self.subtotal),
            'delivery_fee': float(self.delivery_fee),
            'discount_amount': float(self.discount_amount),
            'total': float(self.total),
            'payment_method': self.payment_method.value,
            'payment_status': self.payment_status if isinstance(self.payment_status, str) else (self.payment_status.value if self.payment_status else 'pending'),
            'delivery_scope': self.delivery_scope_value,
            'is_regional': self.is_regional,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'items_count': len(self.items),
        }
        if include_items:
            d['items'] = [i.to_dict() for i in self.items]
            if self.address:
                d['address'] = self.address.to_dict()
        return d

    def __repr__(self):
        return f'<Order {self.order_number}>'


class OrderItem(db.Model):
    __tablename__ = 'order_items'

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)
    total_price = db.Column(db.Numeric(10, 2), nullable=False)
    product_snapshot = db.Column(db.Text, nullable=True)  # JSON snapshot

    order = db.relationship('Order', back_populates='items')
    product = db.relationship('Product', back_populates='order_items')

    def get_snapshot(self):
        if self.product_snapshot:
            try:
                return json.loads(self.product_snapshot)
            except Exception:
                return {}
        return {}

    def product_name(self):
        snap = self.get_snapshot()
        return snap.get('name', self.product.name if self.product else 'Unknown')

    def product_image(self):
        snap = self.get_snapshot()
        return snap.get('image', self.product.primary_image() if self.product else '')

    def to_dict(self):
        return {
            'id': self.id,
            'product_id': self.product_id,
            'product_name': self.product_name(),
            'product_image': self.product_image(),
            'quantity': self.quantity,
            'unit_price': float(self.unit_price),
            'total_price': float(self.total_price),
        }


class Cart(db.Model):
    __tablename__ = 'cart_items'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    session_id = db.Column(db.String(128), nullable=True, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    quantity = db.Column(db.Integer, default=1, nullable=False)
    added_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', back_populates='cart_items')
    product = db.relationship('Product', back_populates='cart_items')

    def to_dict(self):
        return {
            'id': self.id,
            'product_id': self.product_id,
            'product_name': self.product.name if self.product else '',
            'product_image': self.product.primary_image() if self.product else '',
            'product_slug': self.product.slug if self.product else '',
            'unit_price': float(self.product.price) if self.product else 0,
            'quantity': self.quantity,
            'total_price': float(self.product.price) * self.quantity if self.product else 0,
        }


class Wishlist(db.Model):
    __tablename__ = 'wishlist'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    added_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', back_populates='wishlist')
    product = db.relationship('Product', back_populates='wishlist_items')

    __table_args__ = (db.UniqueConstraint('user_id', 'product_id', name='uq_wishlist'),)


class Review(db.Model):
    __tablename__ = 'reviews'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False)  # 1-5
    body = db.Column(db.Text, nullable=True)
    approved = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    product = db.relationship('Product', back_populates='reviews')
    user = db.relationship('User', back_populates='reviews')

    def to_dict(self):
        return {
            'id': self.id,
            'rating': self.rating,
            'body': self.body,
            'reviewer': self.user.full_name if self.user else 'Anonymous',
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
