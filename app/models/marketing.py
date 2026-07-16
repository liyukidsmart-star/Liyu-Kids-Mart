from datetime import datetime, timezone
from sqlalchemy import or_, and_

from app.extensions import db
from app.models.product import Product
from app.models.order import DiscountType


class ProductDiscount(db.Model):
    __tablename__ = 'product_discounts'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    scope = db.Column(db.String(20), nullable=False, default='product')
    title = db.Column(db.String(255), nullable=True)
    discount_type = db.Column(db.Enum(DiscountType), nullable=False, default=DiscountType.percentage)
    discount_value = db.Column(db.Numeric(10, 2), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    starts_at = db.Column(db.DateTime, nullable=True)
    ends_at = db.Column(db.DateTime, nullable=True)
    priority = db.Column(db.Integer, default=100)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    product = db.relationship('Product', backref=db.backref('discount_rows', lazy='dynamic', cascade='all, delete-orphan'))

    def applies_to(self, product_id):
        return (
            self.scope == 'global' and self.product_id is None
        ) or (
            self.scope == 'product' and self.product_id == product_id
        )

    def _normalize_dt(self, value):
        if not value:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def is_current(self):
        now = datetime.now(timezone.utc)
        if not self.is_active:
            return False
        starts_at = self._normalize_dt(self.starts_at)
        ends_at = self._normalize_dt(self.ends_at)
        if starts_at and starts_at > now:
            return False
        if ends_at and ends_at < now:
            return False
        return True

    def apply_to(self, base_price):
        base_price = float(base_price or 0)
        if self.discount_type == DiscountType.percentage:
            discount = base_price * float(self.discount_value) / 100
            return round(max(0, base_price - discount), 2)
        return round(max(0, base_price - float(self.discount_value)), 2)

    def label(self):
        if self.discount_type == DiscountType.percentage:
            return f'{float(self.discount_value):.0f}% OFF'
        return f'ETB {float(self.discount_value):,.0f} OFF'


class TelegramChannelPost(db.Model):
    __tablename__ = 'telegram_channel_posts'

    id = db.Column(db.Integer, primary_key=True)
    post_type = db.Column(db.String(20), nullable=False, default='announcement')
    title = db.Column(db.String(255), nullable=True)
    caption = db.Column(db.Text, nullable=True)
    button_text = db.Column(db.String(120), nullable=True)
    button_url = db.Column(db.String(512), nullable=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    channel_chat_id = db.Column(db.String(128), nullable=True)
    status = db.Column(db.String(20), nullable=False, default='draft')
    scheduled_at = db.Column(db.DateTime, nullable=True)
    sent_message_id = db.Column(db.String(128), nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    sent_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    product = db.relationship('Product')
    images = db.relationship('TelegramChannelPostImage', back_populates='post', lazy='dynamic', cascade='all, delete-orphan', order_by='TelegramChannelPostImage.sort_order')

    def is_product_post(self):
        return self.post_type == 'product'

    def is_scheduled(self):
        return self.status == 'scheduled'

    def is_due(self):
        if not self.scheduled_at:
            return False
        now = datetime.now(timezone.utc)
        scheduled_at = self.scheduled_at.replace(tzinfo=timezone.utc) if self.scheduled_at.tzinfo is None else self.scheduled_at.astimezone(timezone.utc)
        return self.status == 'scheduled' and scheduled_at <= now


class TelegramChannelPostImage(db.Model):
    __tablename__ = 'telegram_channel_post_images'

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('telegram_channel_posts.id'), nullable=False)
    image_url = db.Column(db.String(512), nullable=False)
    alt_text = db.Column(db.String(255), nullable=True)
    sort_order = db.Column(db.Integer, default=0)

    post = db.relationship('TelegramChannelPost', back_populates='images')


_original_to_dict = Product.to_dict


def _product_active_discount(self):
    now = datetime.now(timezone.utc)
    q = ProductDiscount.query.filter(ProductDiscount.is_active == True)  # noqa: E712
    q = q.filter(
        or_(
            and_(ProductDiscount.scope == 'product', ProductDiscount.product_id == self.id),
            and_(ProductDiscount.scope == 'global', ProductDiscount.product_id.is_(None)),
        )
    )
    q = q.filter(or_(ProductDiscount.starts_at.is_(None), ProductDiscount.starts_at <= now))
    q = q.filter(or_(ProductDiscount.ends_at.is_(None), ProductDiscount.ends_at >= now))
    return q.order_by(ProductDiscount.priority.asc(), ProductDiscount.created_at.desc()).first()


def _product_current_price(self):
    base_price = float(self.price or 0)
    discount = _product_active_discount(self)
    if not discount:
        return round(base_price, 2)
    return discount.apply_to(base_price)


def _product_discount_label(self):
    discount = _product_active_discount(self)
    return discount.label() if discount else ''


def _product_discount_percentage(self):
    base_price = float(self.price or 0)
    current_price = _product_current_price(self)
    if base_price <= 0 or current_price >= base_price:
        return 0
    return round((1 - current_price / base_price) * 100)


def _product_compare_price(self):
    current_price = _product_current_price(self)
    base_price = float(self.price or 0)
    if self.compare_price and float(self.compare_price) > current_price:
        return float(self.compare_price)
    if current_price < base_price:
        return base_price
    return None


def _product_to_dict(self, include_description=False, qty_discount_min_price=None):
    payload = _original_to_dict(self, include_description=include_description, qty_discount_min_price=qty_discount_min_price)
    current_price = _product_current_price(self)
    base_price = float(self.price or 0)
    compare_price = float(self.compare_price) if self.compare_price and float(self.compare_price) > current_price else None
    if compare_price is None and current_price < base_price:
        compare_price = base_price
    payload['price'] = current_price
    payload['base_price'] = base_price
    payload['compare_price'] = compare_price
    payload['discount_label'] = _product_discount_label(self)
    payload['discount_percentage'] = _product_discount_percentage(self)
    payload['current_price'] = current_price
    return payload


Product.active_discount = _product_active_discount
Product.current_price = _product_current_price
Product.discount_label = _product_discount_label
Product.discount_percentage = _product_discount_percentage
Product.compare_at_price = _product_compare_price
Product.to_dict = _product_to_dict

