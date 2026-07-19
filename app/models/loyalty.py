"""
loyalty.py — All loyalty & rewards database models.
Every business threshold and rule is stored in the DB so admins
can modify them from the dashboard without touching code.
"""
import enum
import json
from datetime import datetime, timezone
from app.extensions import db


# ─────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────

class CustomerStatus(enum.Enum):
    new = 'new'
    active = 'active'
    loyal = 'loyal'
    vip = 'vip'
    elite = 'elite'


class RewardTransactionType(enum.Enum):
    earn_purchase = 'earn_purchase'
    earn_review = 'earn_review'
    earn_referral = 'earn_referral'
    earn_daily_visit = 'earn_daily_visit'
    earn_large_order = 'earn_large_order'
    earn_seasonal = 'earn_seasonal'
    earn_achievement = 'earn_achievement'
    redeem = 'redeem'
    expire = 'expire'
    adjust = 'adjust'


# ─────────────────────────────────────────────────────────────
# LoyaltyLevel — Admin-configurable tiers
# ─────────────────────────────────────────────────────────────

class LoyaltyLevel(db.Model):
    """
    Configurable loyalty tier (Bronze, Silver, Gold, VIP, Elite, …)
    All thresholds and benefits are stored here. No hardcoded values.
    """
    __tablename__ = 'loyalty_levels'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False)               # e.g. "Bronze"
    name_am = db.Column(db.String(64), nullable=True)             # Amharic name
    sort_order = db.Column(db.Integer, default=0, nullable=False)  # lowest → highest
    min_spending = db.Column(db.Numeric(12, 2), default=0, nullable=False)
    min_orders = db.Column(db.Integer, default=0, nullable=False)
    discount_percentage = db.Column(db.Numeric(5, 2), default=0, nullable=False)
    badge_icon = db.Column(db.String(32), nullable=True, default='🏅')
    badge_url = db.Column(db.String(512), nullable=True)
    color_hex = db.Column(db.String(10), nullable=True, default='#CD7F32')
    description = db.Column(db.Text, nullable=True)
    # JSON list of access rights, e.g. ["see_prices", "see_premium", "purchase_premium"]
    access_rights = db.Column(db.Text, nullable=True, default='[]')
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    users = db.relationship('User', back_populates='loyalty_level', lazy='dynamic')
    products_requiring = db.relationship('Product', back_populates='min_loyalty_level',
                                         foreign_keys='Product.min_loyalty_level_id', lazy='dynamic')

    def get_access_rights(self):
        try:
            return json.loads(self.access_rights or '[]')
        except Exception:
            return []

    def set_access_rights(self, rights_list):
        self.access_rights = json.dumps(rights_list)

    def has_right(self, right):
        return right in self.get_access_rights()

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'name_am': self.name_am,
            'sort_order': self.sort_order,
            'min_spending': float(self.min_spending),
            'min_orders': self.min_orders,
            'discount_percentage': float(self.discount_percentage),
            'badge_icon': self.badge_icon,
            'badge_url': self.badge_url,
            'color_hex': self.color_hex,
            'description': self.description,
            'access_rights': self.get_access_rights(),
            'is_active': self.is_active,
        }

    def __repr__(self):
        return f'<LoyaltyLevel {self.name} (min {self.min_spending} ETB)>'


# ─────────────────────────────────────────────────────────────
# SpendingThreshold — Per-purchase discount bands
# ─────────────────────────────────────────────────────────────

class SpendingThreshold(db.Model):
    """
    Admin-defined purchase size → discount percentage.
    e.g. spend 5000 ETB → 1%, spend 10000 ETB → 1.5%
    These are per-order discounts (TODAY's purchase), not lifetime.
    """
    __tablename__ = 'spending_thresholds'

    id = db.Column(db.Integer, primary_key=True)
    min_amount = db.Column(db.Numeric(12, 2), nullable=False)
    discount_percentage = db.Column(db.Numeric(5, 2), nullable=False)
    label = db.Column(db.String(64), nullable=True)   # e.g. "5,000+ Birr"
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            'id': self.id,
            'min_amount': float(self.min_amount),
            'discount_percentage': float(self.discount_percentage),
            'label': self.label,
            'is_active': self.is_active,
        }

    def __repr__(self):
        return f'<SpendingThreshold {self.min_amount} → {self.discount_percentage}%>'


# ─────────────────────────────────────────────────────────────
# QuantityDiscount — Buy X items → save Y Birr
# ─────────────────────────────────────────────────────────────

class QuantityDiscount(db.Model):
    """
    Admin-configured "Buy N items, save M Birr" rule.
    """
    __tablename__ = 'quantity_discounts'

    id = db.Column(db.Integer, primary_key=True)
    min_items = db.Column(db.Integer, nullable=False)             # minimum cart item count
    discount_amount = db.Column(db.Numeric(10, 2), nullable=False)  # fixed Birr off
    label = db.Column(db.String(128), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            'id': self.id,
            'min_items': self.min_items,
            'discount_amount': float(self.discount_amount),
            'label': self.label,
            'is_active': self.is_active,
        }

    def __repr__(self):
        return f'<QuantityDiscount {self.min_items} items → {self.discount_amount} ETB off>'


# ─────────────────────────────────────────────────────────────
# CartIncentive — "Spend X more to unlock reward"
# ─────────────────────────────────────────────────────────────

class CartIncentive(db.Model):
    """
    Displayed in the Cart Progress bar.
    When cart total reaches min_cart_value, the customer gets discount_offered.
    """
    __tablename__ = 'cart_incentives'

    id = db.Column(db.Integer, primary_key=True)
    min_cart_value = db.Column(db.Numeric(12, 2), nullable=False)      # trigger amount
    discount_offered = db.Column(db.Numeric(10, 2), nullable=False)    # Birr saved
    popup_text = db.Column(db.String(255), nullable=True)              # custom message
    required_additional_amount = db.Column(db.Numeric(12, 2), nullable=True)  # hint: "add X more"
    # JSON array of product IDs to suggest when customer is near this threshold
    suggested_product_ids = db.Column(db.Text, nullable=True, default='[]')
    animation = db.Column(db.String(50), nullable=True, default='confetti')  # confetti, sparkle, bounce
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def get_suggested_product_ids(self):
        try:
            return json.loads(self.suggested_product_ids or '[]')
        except Exception:
            return []

    def to_dict(self):
        return {
            'id': self.id,
            'min_cart_value': float(self.min_cart_value),
            'discount_offered': float(self.discount_offered),
            'popup_text': self.popup_text,
            'required_additional_amount': float(self.required_additional_amount) if self.required_additional_amount else None,
            'suggested_product_ids': self.get_suggested_product_ids(),
            'animation': self.animation,
            'is_active': self.is_active,
        }

    def __repr__(self):
        return f'<CartIncentive at {self.min_cart_value} ETB → save {self.discount_offered}>'


# ─────────────────────────────────────────────────────────────
# Achievement — Gamification badges
# ─────────────────────────────────────────────────────────────

class Achievement(db.Model):
    """
    Gamification badges that customers can unlock.
    Admin creates and manages these.
    """
    __tablename__ = 'achievements'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    name_am = db.Column(db.String(100), nullable=True)
    description = db.Column(db.String(255), nullable=True)
    description_am = db.Column(db.String(255), nullable=True)
    badge_icon = db.Column(db.String(32), nullable=True, default='🏆')
    badge_url = db.Column(db.String(512), nullable=True)
    color_hex = db.Column(db.String(10), nullable=True, default='#FFD700')
    # trigger_type: orders_count, spending_total, items_count, review_count, etc.
    trigger_type = db.Column(db.String(50), nullable=False, default='orders_count')
    trigger_value = db.Column(db.Numeric(12, 2), nullable=False, default=1)
    points_awarded = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    user_achievements = db.relationship('UserAchievement', back_populates='achievement',
                                        cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'name_am': self.name_am,
            'description': self.description,
            'badge_icon': self.badge_icon,
            'badge_url': self.badge_url,
            'color_hex': self.color_hex,
            'trigger_type': self.trigger_type,
            'trigger_value': float(self.trigger_value),
            'points_awarded': self.points_awarded,
        }

    def __repr__(self):
        return f'<Achievement {self.name}>'


# ─────────────────────────────────────────────────────────────
# UserAchievement — Junction: which customers unlocked what
# ─────────────────────────────────────────────────────────────

class UserAchievement(db.Model):
    __tablename__ = 'user_achievements'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    achievement_id = db.Column(db.Integer, db.ForeignKey('achievements.id'), nullable=False)
    unlocked_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', back_populates='achievements')
    achievement = db.relationship('Achievement', back_populates='user_achievements')

    __table_args__ = (
        db.UniqueConstraint('user_id', 'achievement_id', name='uq_user_achievement'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'achievement': self.achievement.to_dict() if self.achievement else None,
            'unlocked_at': self.unlocked_at.isoformat() if self.unlocked_at else None,
        }


# ─────────────────────────────────────────────────────────────
# RewardTransaction — Points ledger
# ─────────────────────────────────────────────────────────────

class RewardTransaction(db.Model):
    """
    Immutable ledger of every reward point event for a user.
    """
    __tablename__ = 'reward_transactions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=True)
    transaction_type = db.Column(db.Enum(RewardTransactionType), nullable=False)
    points = db.Column(db.Integer, nullable=False)   # positive = earned, negative = spent/expired
    balance_after = db.Column(db.Integer, nullable=False, default=0)
    description = db.Column(db.String(255), nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    user = db.relationship('User', back_populates='reward_transactions')
    order = db.relationship('Order', back_populates='reward_transactions')

    def to_dict(self):
        return {
            'id': self.id,
            'transaction_type': self.transaction_type.value,
            'points': self.points,
            'balance_after': self.balance_after,
            'description': self.description,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


# ─────────────────────────────────────────────────────────────
# LoyaltySettings — Global configurable settings (singleton)
# ─────────────────────────────────────────────────────────────

class LoyaltySettings(db.Model):
    """
    A single-row configuration table for global loyalty settings.
    Admin controls all point multipliers and policies here.
    """
    __tablename__ = 'loyalty_settings'

    id = db.Column(db.Integer, primary_key=True)
    # Points earning rates (points per 100 ETB spent)
    points_per_100_birr = db.Column(db.Integer, default=1, nullable=False)
    # Bonus multipliers
    bonus_review_points = db.Column(db.Integer, default=50)
    bonus_referral_points = db.Column(db.Integer, default=100)
    bonus_daily_visit_points = db.Column(db.Integer, default=5)
    bonus_large_order_threshold = db.Column(db.Numeric(12, 2), default=5000)
    bonus_large_order_points = db.Column(db.Integer, default=200)
    # Points expiry in days (0 = never expire)
    points_expiry_days = db.Column(db.Integer, default=365)
    # Points redemption rate: 1 point = X ETB
    point_value_birr = db.Column(db.Numeric(6, 4), default=0.10)
    # Min points to redeem
    min_redemption_points = db.Column(db.Integer, default=500)
    # Global store launch gate; when set in the future, ordering is blocked.
    launch_date = db.Column(db.DateTime, nullable=True)
    # Whether loyalty system is enabled
    is_enabled = db.Column(db.Boolean, default=True, nullable=False)
    # Mini-app visibility controls for category and age-range navigation
    show_categories_in_mini_app = db.Column(db.Boolean, default=True, nullable=False)
    show_age_filter_in_mini_app = db.Column(db.Boolean, default=True, nullable=False)
    # TeleBirr contact details shown on the regional checkout payment screen
    telebirr_payment_phone = db.Column(db.String(32), default='', nullable=False)
    # ── Quantity Discount Eligibility ───────────────────────────
    # Minimum product price (current_price) to count toward quantity discounts
    qty_discount_min_price = db.Column(db.Numeric(10, 2), default=2500.00, nullable=False)
    # When True, all customers (including new/no-tier) can earn quantity discounts
    qty_discount_open_to_all = db.Column(db.Boolean, default=True, nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            'points_per_100_birr': self.points_per_100_birr,
            'bonus_review_points': self.bonus_review_points,
            'bonus_referral_points': self.bonus_referral_points,
            'bonus_daily_visit_points': self.bonus_daily_visit_points,
            'bonus_large_order_threshold': float(self.bonus_large_order_threshold),
            'bonus_large_order_points': self.bonus_large_order_points,
            'points_expiry_days': self.points_expiry_days,
            'point_value_birr': float(self.point_value_birr),
            'min_redemption_points': self.min_redemption_points,
            'launch_date': self.launch_date.isoformat() if self.launch_date else None,
            'is_enabled': self.is_enabled,
            'show_categories_in_mini_app': self.show_categories_in_mini_app,
            'show_age_filter_in_mini_app': self.show_age_filter_in_mini_app,
            'telebirr_payment_phone': self.telebirr_payment_phone,
            'qty_discount_min_price': float(self.qty_discount_min_price),
            'qty_discount_open_to_all': self.qty_discount_open_to_all,
        }
