import enum
from datetime import datetime, timezone
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db
from app.models.loyalty import CustomerStatus


class UserRole(enum.Enum):
    customer = 'customer'
    admin = 'admin'
    manager = 'manager'
    driver = 'driver'


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    telegram_id = db.Column(db.String(64), unique=True, nullable=True, index=True)
    telegram_username = db.Column(db.String(128), nullable=True)
    email = db.Column(db.String(255), unique=True, nullable=True, index=True)
    phone = db.Column(db.String(20), nullable=True)
    full_name = db.Column(db.String(255), nullable=False, default='')
    role = db.Column(db.Enum(UserRole), default=UserRole.customer, nullable=False)
    password_hash = db.Column(db.String(512), nullable=True)
    avatar_url = db.Column(db.String(512), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_verified = db.Column(db.Boolean, default=False, nullable=False)
    child_ages = db.Column(db.Text, nullable=True)  # JSON: [2, 4, 6]
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))
    last_login = db.Column(db.DateTime, nullable=True)

    # ── Loyalty & Rewards ──────────────────────────────────────────────────────
    loyalty_level_id = db.Column(db.Integer, db.ForeignKey('loyalty_levels.id'), nullable=True, index=True)
    customer_status = db.Column(db.Enum(CustomerStatus), default=CustomerStatus.new, nullable=False)
    loyalty_score = db.Column(db.Integer, default=0, nullable=False)   # computed score for gamification
    reward_points = db.Column(db.Integer, default=0, nullable=False)   # current redeemable points
    lifetime_points_earned = db.Column(db.Integer, default=0, nullable=False)
    # Financial tracking (cumulative, never reset)
    total_money_spent = db.Column(db.Numeric(14, 2), default=0, nullable=False)
    lifetime_savings = db.Column(db.Numeric(14, 2), default=0, nullable=False)
    total_orders = db.Column(db.Integer, default=0, nullable=False)
    total_items_purchased = db.Column(db.Integer, default=0, nullable=False)
    # Timestamps
    first_purchase_date = db.Column(db.DateTime, nullable=True)
    last_purchase_date = db.Column(db.DateTime, nullable=True)
    # Referral
    referral_code = db.Column(db.String(32), nullable=True, unique=True, index=True)
    referred_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    # Relationships
    orders = db.relationship('Order', back_populates='user', lazy='dynamic')
    cart_items = db.relationship('Cart', back_populates='user', lazy='dynamic', cascade='all, delete-orphan')
    wishlist = db.relationship('Wishlist', back_populates='user', lazy='dynamic', cascade='all, delete-orphan')
    reviews = db.relationship('Review', back_populates='user', lazy='dynamic')
    ai_conversations = db.relationship('AIConversation', back_populates='user', lazy='dynamic',
                                       cascade='all, delete-orphan')
    notifications = db.relationship('Notification', back_populates='user', lazy='dynamic',
                                    cascade='all, delete-orphan')
    addresses = db.relationship('Address', back_populates='user', lazy='dynamic', cascade='all, delete-orphan')
    loyalty_level = db.relationship('LoyaltyLevel', back_populates='users', foreign_keys=[loyalty_level_id])
    achievements = db.relationship('UserAchievement', back_populates='user', lazy='dynamic',
                                   cascade='all, delete-orphan')
    reward_transactions = db.relationship('RewardTransaction', back_populates='user', lazy='dynamic',
                                          cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    def get_child_ages(self):
        import json
        if self.child_ages:
            try:
                return json.loads(self.child_ages)
            except Exception:
                return []
        return []

    def set_child_ages(self, ages_list):
        import json
        self.child_ages = json.dumps(ages_list)

    def get_loyalty_level_dict(self):
        """Returns the user's current loyalty level as dict, or None."""
        if self.loyalty_level:
            return self.loyalty_level.to_dict()
        return None

    def to_dict(self):
        return {
            'id': self.id,
            'telegram_id': self.telegram_id,
            'telegram_username': self.telegram_username,
            'email': self.email,
            'phone': self.phone,
            'full_name': self.full_name,
            'role': self.role.value,
            'avatar_url': self.avatar_url,
            'is_active': self.is_active,
            'child_ages': self.get_child_ages(),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            # Loyalty fields
            'customer_status': self.customer_status.value,
            'loyalty_level': self.get_loyalty_level_dict(),
            'loyalty_score': self.loyalty_score,
            'reward_points': self.reward_points,
            'total_money_spent': float(self.total_money_spent),
            'lifetime_savings': float(self.lifetime_savings),
            'total_orders': self.total_orders,
            'total_items_purchased': self.total_items_purchased,
            'first_purchase_date': self.first_purchase_date.isoformat() if self.first_purchase_date else None,
            'last_purchase_date': self.last_purchase_date.isoformat() if self.last_purchase_date else None,
            'referral_code': self.referral_code,
        }

    def __repr__(self):
        return f'<User {self.full_name} ({self.role.value})>'
