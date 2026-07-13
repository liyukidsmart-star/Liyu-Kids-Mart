"""
Inventory & POS Models — Liyu Kids Mart
Stock transaction history and Point-of-Sale sale records.
"""
import enum
import json
from datetime import datetime, timezone
from app.extensions import db


class StockTransactionType(enum.Enum):
    restock = 'restock'            # Manual stock addition
    sale = 'sale'                  # Online order (stock auto-decremented)
    pos_sale = 'pos_sale'          # In-store POS sale
    adjustment = 'adjustment'     # Manual correction
    return_ = 'return'            # Returned item, stock restored


class StockTransaction(db.Model):
    """Audit log of every stock level change for a product."""
    __tablename__ = 'stock_transactions'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False, index=True)
    transaction_type = db.Column(db.Enum(StockTransactionType), nullable=False)
    quantity_change = db.Column(db.Integer, nullable=False)  # positive = add, negative = remove
    quantity_before = db.Column(db.Integer, nullable=False)
    quantity_after = db.Column(db.Integer, nullable=False)
    reference_id = db.Column(db.String(64), nullable=True)   # order_number or pos_sale_number
    notes = db.Column(db.String(255), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    product = db.relationship('Product', backref=db.backref('stock_transactions', lazy='dynamic'))
    created_by = db.relationship('User', foreign_keys=[created_by_id])

    def to_dict(self):
        return {
            'id': self.id,
            'product_id': self.product_id,
            'product_name': self.product.name if self.product else '',
            'transaction_type': self.transaction_type.value,
            'quantity_change': self.quantity_change,
            'quantity_before': self.quantity_before,
            'quantity_after': self.quantity_after,
            'reference_id': self.reference_id,
            'notes': self.notes,
            'created_by': self.created_by.full_name if self.created_by else 'System',
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class POSSaleStatus(enum.Enum):
    completed = 'completed'
    refunded = 'refunded'
    voided = 'voided'


class POSSale(db.Model):
    """A single in-store Point-of-Sale transaction."""
    __tablename__ = 'pos_sales'

    id = db.Column(db.Integer, primary_key=True)
    sale_number = db.Column(db.String(30), unique=True, nullable=False, index=True)
    cashier_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    status = db.Column(db.Enum(POSSaleStatus), default=POSSaleStatus.completed, nullable=False)

    subtotal = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    discount_percentage = db.Column(db.Numeric(5, 2), default=0)  # On-store % discount
    discount_amount = db.Column(db.Numeric(10, 2), default=0)
    total = db.Column(db.Numeric(10, 2), nullable=False, default=0)

    payment_method = db.Column(db.String(50), nullable=True)       # Optional - cash/card/telebirr etc.
    notes = db.Column(db.Text, nullable=True)

    items_snapshot = db.Column(db.Text, nullable=True)             # JSON list of items at time of sale
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    cashier = db.relationship('User', foreign_keys=[cashier_id])
    items = db.relationship('POSSaleItem', back_populates='sale', cascade='all, delete-orphan')

    def get_items_snapshot(self):
        if self.items_snapshot:
            try:
                return json.loads(self.items_snapshot)
            except Exception:
                return []
        return []

    def to_dict(self):
        return {
            'id': self.id,
            'sale_number': self.sale_number,
            'cashier': self.cashier.full_name if self.cashier else 'Unknown',
            'status': self.status.value,
            'subtotal': float(self.subtotal),
            'discount_percentage': float(self.discount_percentage),
            'discount_amount': float(self.discount_amount),
            'total': float(self.total),
            'payment_method': self.payment_method or '',
            'notes': self.notes or '',
            'items': [i.to_dict() for i in self.items],
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'created_label': self.created_at.strftime('%b %d, %H:%M') if self.created_at else '',
        }


class POSSaleItem(db.Model):
    """Line items within a POS Sale."""
    __tablename__ = 'pos_sale_items'

    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('pos_sales.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)
    total_price = db.Column(db.Numeric(10, 2), nullable=False)
    product_snapshot = db.Column(db.Text, nullable=True)  # JSON name/image snapshot

    sale = db.relationship('POSSale', back_populates='items')
    product = db.relationship('Product')

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
