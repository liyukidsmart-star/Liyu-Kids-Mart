from datetime import datetime, timezone
from app.extensions import db
from app.services.image_delivery import rewrite_media_url


class Category(db.Model):
    __tablename__ = 'categories'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(120), unique=True, nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    icon = db.Column(db.String(10), nullable=True, default='📦')
    icon_url = db.Column(db.String(512), nullable=True)
    parent_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=True)
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Self-referential relationship
    children = db.relationship('Category', backref=db.backref('parent', remote_side=[id]), lazy='dynamic')
    products = db.relationship('Product', back_populates='category', lazy='dynamic')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'description': self.description,
            'icon': self.icon,
            'icon_url': self.icon_url,
            'product_count': self.products.filter_by(is_active=True).count(),
        }

    def __repr__(self):
        return f'<Category {self.name}>'


class Product(db.Model):
    __tablename__ = 'products'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    name_am = db.Column(db.String(255), nullable=True)
    slug = db.Column(db.String(300), unique=True, nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    description_am = db.Column(db.Text, nullable=True)
    short_description = db.Column(db.String(500), nullable=True)
    short_description_am = db.Column(db.String(500), nullable=True)
    price = db.Column(db.Numeric(10, 2), nullable=False)
    compare_price = db.Column(db.Numeric(10, 2), nullable=True)
    sku = db.Column(db.String(100), nullable=True, unique=True)
    stock_qty = db.Column(db.Integer, default=0, nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=True)
    age_min_months = db.Column(db.Integer, default=0)   # age in months
    age_max_months = db.Column(db.Integer, default=144)  # 12 years default
    weight_kg = db.Column(db.Numeric(6, 3), nullable=True)
    is_featured = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    is_new_arrival = db.Column(db.Boolean, default=False)
    view_count = db.Column(db.Integer, default=0)
    sales_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    category = db.relationship('Category', back_populates='products')
    images = db.relationship('ProductImage', back_populates='product', lazy='dynamic',
                             cascade='all, delete-orphan', order_by='ProductImage.sort_order')
    tags = db.relationship('ProductTag', back_populates='product', lazy='dynamic', cascade='all, delete-orphan')
    order_items = db.relationship('OrderItem', back_populates='product', lazy='dynamic')
    cart_items = db.relationship('Cart', back_populates='product', lazy='dynamic')
    wishlist_items = db.relationship('Wishlist', back_populates='product', lazy='dynamic')
    reviews = db.relationship('Review', back_populates='product', lazy='dynamic', cascade='all, delete-orphan')
    embedding = db.relationship('ProductEmbedding', back_populates='product', uselist=False,
                                cascade='all, delete-orphan')

    def primary_image(self):
        img = self.images.filter_by(is_primary=True).first()
        if not img:
            img = self.images.first()
        return rewrite_media_url(img.image_url) if img else '/static/images/placeholder.png'

    def all_images(self):
        return [rewrite_media_url(i.image_url) for i in self.images.order_by(ProductImage.sort_order.asc())]

    def avg_rating(self):
        approved = self.reviews.filter_by(approved=True).all()
        if not approved:
            return 0
        return round(sum(r.rating for r in approved) / len(approved), 1)

    def review_count(self):
        return self.reviews.filter_by(approved=True).count()

    def age_label(self):
        def fmt(m):
            if m == 0:
                return '0m'
            if m < 12:
                return f'{m}m'
            y = m // 12
            rem = m % 12
            return f'{y}yr' + (f' {rem}m' if rem else '')
        return f'{fmt(self.age_min_months)} – {fmt(self.age_max_months)}'

    def to_dict(self, include_description=False):
        d = {
            'id': self.id,
            'name': self.name,
            'name_am': self.name_am,
            'slug': self.slug,
            'short_description': self.short_description,
            'short_description_am': self.short_description_am,
            'price': float(self.price),
            'compare_price': float(self.compare_price) if self.compare_price else None,
            'stock_qty': self.stock_qty,
            'category_id': self.category_id,
            'category_name': self.category.name if self.category else None,
            'age_min_months': self.age_min_months,
            'age_max_months': self.age_max_months,
            'age_label': self.age_label(),
            'is_featured': self.is_featured,
            'is_new_arrival': self.is_new_arrival,
            'is_active': self.is_active,
            'view_count': self.view_count,
            'sales_count': self.sales_count,
            'primary_image': self.primary_image(),
            'images': self.all_images(),
            'tags': [t.tag for t in self.tags],
            'avg_rating': self.avg_rating(),
            'review_count': self.review_count(),
        }
        if include_description:
            d['description'] = self.description
            d['description_am'] = self.description_am
        return d

    def __repr__(self):
        return f'<Product {self.name}>'


class ProductImage(db.Model):
    __tablename__ = 'product_images'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    image_url = db.Column(db.String(512), nullable=False)
    alt_text = db.Column(db.String(255), nullable=True)
    is_primary = db.Column(db.Boolean, default=False)
    sort_order = db.Column(db.Integer, default=0)

    product = db.relationship('Product', back_populates='images')

    def to_dict(self):
        return {
            'id': self.id,
            'image_url': self.image_url,
            'alt_text': self.alt_text,
            'is_primary': self.is_primary,
        }


class ProductTag(db.Model):
    __tablename__ = 'product_tags'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    tag = db.Column(db.String(100), nullable=False)

    product = db.relationship('Product', back_populates='tags')


class ProductEmbedding(db.Model):
    __tablename__ = 'product_embeddings'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False, unique=True)
    embedding_json = db.Column(db.Text, nullable=True)  # JSON array of floats
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    product = db.relationship('Product', back_populates='embedding')
