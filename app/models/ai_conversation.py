import json
from datetime import datetime, timezone
from app.extensions import db


class Payment(db.Model):
    __tablename__ = 'payments'

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False, unique=True)
    method = db.Column(db.String(20), default='cod')
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    currency = db.Column(db.String(10), default='ETB')
    status = db.Column(db.String(20), default='pending')
    provider_reference = db.Column(db.String(255), nullable=True)
    provider_response = db.Column(db.Text, nullable=True)  # JSON
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    order = db.relationship('Order', back_populates='payment')

    def to_dict(self):
        return {
            'id': self.id,
            'method': self.method,
            'amount': float(self.amount),
            'currency': self.currency,
            'status': self.status,
            'provider_reference': self.provider_reference,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class AIConversation(db.Model):
    __tablename__ = 'ai_conversations'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    session_id = db.Column(db.String(128), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False)  # 'user' | 'assistant' | 'system'
    content = db.Column(db.Text, nullable=False)
    channel = db.Column(db.String(20), default='web')  # 'web' | 'telegram' | 'mini_app'
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', back_populates='ai_conversations')

    def to_dict(self):
        return {
            'id': self.id,
            'role': self.role,
            'content': self.content,
            'channel': self.channel,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class ProductRecommendation(db.Model):
    __tablename__ = 'product_recommendations'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    score = db.Column(db.Float, default=0.0)
    reason = db.Column(db.String(100), nullable=True)
    context = db.Column(db.Text, nullable=True)  # JSON
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class ActivityLog(db.Model):
    __tablename__ = 'activity_logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    action = db.Column(db.String(100), nullable=False, index=True)
    entity_type = db.Column(db.String(50), nullable=True)
    entity_id = db.Column(db.Integer, nullable=True)
    meta = db.Column(db.Text, nullable=True)  # JSON
    ip_address = db.Column(db.String(50), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def get_meta(self):
        if self.meta:
            try:
                return json.loads(self.meta)
            except Exception:
                return {}
        return {}


class Notification(db.Model):
    __tablename__ = 'notifications'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    body = db.Column(db.Text, nullable=True)
    type = db.Column(db.String(30), default='system')  # order, promo, restock, system
    is_read = db.Column(db.Boolean, default=False)
    telegram_sent = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', back_populates='notifications')

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'body': self.body,
            'type': self.type,
            'is_read': self.is_read,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
