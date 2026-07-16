"""
loyalty_service.py — Central business logic for the Loyalty & Rewards Ecosystem.

All business rules are fetched from the database (admin-configurable).
No values are hardcoded here.
"""
import json
import secrets
import string
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Optional

from flask import current_app
from sqlalchemy import inspect

from app.extensions import db
from app.models.loyalty import (
    LoyaltyLevel, SpendingThreshold, QuantityDiscount,
    CartIncentive, Achievement, UserAchievement, RewardTransaction,
    LoyaltySettings, CustomerStatus, RewardTransactionType,
)


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _default_settings() -> SimpleNamespace:
    return SimpleNamespace(
        points_per_100_birr=1,
        bonus_review_points=50,
        bonus_referral_points=100,
        bonus_daily_visit_points=5,
        bonus_large_order_threshold=5000,
        bonus_large_order_points=200,
        points_expiry_days=365,
        point_value_birr=0.10,
        min_redemption_points=500,
        launch_date=None,
        is_enabled=True,
        show_categories_in_mini_app=True,
        show_age_filter_in_mini_app=True,
    )


def _repair_loyalty_settings_visibility_columns(missing_columns):
    """Add missing loyalty_settings columns in-place when the schema is behind."""
    if not missing_columns:
        return

    ddl_columns = {
        'show_categories_in_mini_app': 'BOOLEAN NOT NULL DEFAULT TRUE',
        'show_age_filter_in_mini_app': 'BOOLEAN NOT NULL DEFAULT TRUE',
        'qty_discount_min_price': 'NUMERIC(10,2) NOT NULL DEFAULT 2500.00',
        'qty_discount_open_to_all': 'BOOLEAN NOT NULL DEFAULT TRUE',
    }

    try:
        with db.engine.begin() as connection:
            for column_name in missing_columns:
                ddl = ddl_columns.get(column_name)
                if ddl:
                    connection.exec_driver_sql(
                        f'ALTER TABLE {LoyaltySettings.__tablename__} ADD COLUMN {column_name} {ddl}'
                    )
        current_app.logger.warning(
            'Auto-repaired loyalty_settings schema by adding missing columns: %s',
            ', '.join(sorted(missing_columns)),
        )
    except Exception as exc:
        current_app.logger.warning(
            'Failed to auto-repair loyalty_settings schema for %s: %s',
            ', '.join(sorted(missing_columns)),
            exc,
        )


def _get_settings():
    """Fetch global loyalty settings safely, even if newer columns are missing in the database."""
    try:
        inspector = inspect(db.engine)
        columns = {column['name'] for column in inspector.get_columns(LoyaltySettings.__tablename__)}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return _default_settings()

    if not columns:
        return _default_settings()

    required_columns = {
        'show_categories_in_mini_app', 'show_age_filter_in_mini_app',
        'qty_discount_min_price', 'qty_discount_open_to_all',
    }
    missing_columns = required_columns - columns
    if missing_columns:
        _repair_loyalty_settings_visibility_columns(missing_columns)
        try:
            inspector = inspect(db.engine)
            columns = {column['name'] for column in inspector.get_columns(LoyaltySettings.__tablename__)}
        except Exception:
            return _default_settings()
        if required_columns - columns:
            return _default_settings()

    settings = LoyaltySettings.query.first()
    if not settings:
        settings = LoyaltySettings()
        db.session.add(settings)
        db.session.flush()  # flush only — do NOT commit mid-checkout
    return settings


def _get_active_levels():
    """Return all active loyalty levels ordered low → high by sort_order."""
    return LoyaltyLevel.query.filter_by(is_active=True).order_by(LoyaltyLevel.sort_order.asc()).all()


def _get_active_thresholds():
    """Return active spending thresholds ordered by min_amount asc."""
    return SpendingThreshold.query.filter_by(is_active=True).order_by(SpendingThreshold.min_amount.asc()).all()


def _get_active_quantity_discounts():
    """Return active quantity discounts ordered by min_items asc."""
    return QuantityDiscount.query.filter_by(is_active=True).order_by(QuantityDiscount.min_items.asc()).all()


def _get_active_cart_incentives():
    """Return active cart incentives ordered by min_cart_value asc."""
    return CartIncentive.query.filter_by(is_active=True).order_by(CartIncentive.min_cart_value.asc()).all()


def _generate_referral_code(length=8):
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def _coerce_utc(value):
    if not value:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_store_launch_date():
    settings = _get_settings()
    return _coerce_utc(getattr(settings, 'launch_date', None))


def is_store_launch_locked(now=None):
    launch_date = get_store_launch_date()
    if not launch_date:
        return False
    current = _coerce_utc(now or datetime.now(timezone.utc))
    return current < launch_date


def get_store_launch_state() -> dict:
    settings = _get_settings()
    launch_date = get_store_launch_date()
    return {
        'launch_date': launch_date.isoformat() if launch_date else None,
        'launch_locked': bool(launch_date and datetime.now(timezone.utc) < launch_date),
        'show_categories_in_mini_app': getattr(settings, 'show_categories_in_mini_app', True),
        'show_age_filter_in_mini_app': getattr(settings, 'show_age_filter_in_mini_app', True),
    }


# Tier Resolution
# ─────────────────────────────────────────────────────────────

def resolve_loyalty_level(total_spent: float, total_orders: int) -> Optional[LoyaltyLevel]:
    """
    Given lifetime spending and total orders, return the highest
    LoyaltyLevel the user qualifies for, or None if they don't meet
    the minimum threshold of any level.
    """
    levels = _get_active_levels()
    matched = None
    for level in levels:
        if total_spent >= float(level.min_spending) and total_orders >= level.min_orders:
            matched = level
    return matched


# ─────────────────────────────────────────────────────────────
# Discount Calculation
# ─────────────────────────────────────────────────────────────

def calculate_loyalty_discount(user, cart_subtotal: float, cart_items=None, qty_items: int = 0) -> dict:
    """
    Calculate the complete discount breakdown for a cart.

    Quantity discount eligibility:
      - Controlled by settings.qty_discount_open_to_all (admin-toggleable)
      - When True: ALL customers (including new/no-tier) qualify
      - When False: Bronze (sort_order>=1) and above only
      - Only items with current_price() >= settings.qty_discount_min_price count
        toward the eligible item total

    Returns a dict with:
      loyalty_discount_pct    — loyalty tier % (e.g. 4.0)
      loyalty_discount_amount — Birr saved from tier
      spending_discount_pct   — per-order spending threshold %
      spending_discount_amount— Birr saved from purchase size
      qty_discount_amount     — Birr saved from quantity rules (0 if not eligible)
      incentive_discount_amount — Birr saved from cart incentives (0 if not eligible)
      total_discount_amount   — sum of all discounts
      total_discount_pct      — effective %
      savings_breakdown       — human-readable list of savings
      qty_allowed             — bool: customer eligible for quantity discounts
      incentive_allowed       — bool: customer eligible for cart incentives
      tier_sort_order         — int: user's tier sort_order (0 = no tier)
      qty_eligible_items      — int: number of items that counted toward qty discount
    """
    loyalty_disc_pct = 0.0
    loyalty_disc_amt = 0.0
    spending_disc_pct = 0.0
    spending_disc_amt = 0.0
    qty_disc_amt = 0.0
    incentive_disc_amt = 0.0
    savings_breakdown = []

    settings = _get_settings()
    qty_min_price = float(getattr(settings, 'qty_discount_min_price', 2500))
    open_to_all = bool(getattr(settings, 'qty_discount_open_to_all', True))

    # Determine tier level
    tier_sort_order = 0
    if user and user.loyalty_level and user.loyalty_level.is_active:
        tier_sort_order = int(user.loyalty_level.sort_order or 0)

    # Access flags based on tier and settings
    # Quantity discounts: open to all when flag is True, otherwise Bronze+ only
    qty_allowed = open_to_all or (tier_sort_order >= 1)
    incentive_allowed = tier_sort_order >= 3  # Gold (sort_order=3) and above

    # 1. Loyalty tier discount
    if user and user.loyalty_level:
        loyalty_disc_pct = float(user.loyalty_level.discount_percentage)
        loyalty_disc_amt = round(cart_subtotal * loyalty_disc_pct / 100, 2)
        if loyalty_disc_amt > 0:
            savings_breakdown.append({
                'label': f'{user.loyalty_level.name} Loyalty Discount',
                'amount': loyalty_disc_amt,
                'pct': loyalty_disc_pct,
            })

    # 2. Per-order spending threshold discount (only applied if better than loyalty)
    thresholds = _get_active_thresholds()
    best_threshold = None
    for t in reversed(thresholds):   # highest threshold first
        if cart_subtotal >= float(t.min_amount):
            best_threshold = t
            break

    if best_threshold:
        potential_spending_disc_pct = float(best_threshold.discount_percentage)
        potential_spending_disc_amt = round(cart_subtotal * potential_spending_disc_pct / 100, 2)

        # Take the better of the two (do not stack)
        if potential_spending_disc_amt > loyalty_disc_amt:
            spending_disc_pct = potential_spending_disc_pct
            spending_disc_amt = potential_spending_disc_amt
            # Clear loyalty discount since spending is better
            loyalty_disc_pct = 0.0
            loyalty_disc_amt = 0.0
            savings_breakdown = [{
                'label': f'Large Purchase Discount ({best_threshold.label or f"{best_threshold.min_amount:.0f}+ Birr"})',
                'amount': spending_disc_amt,
                'pct': spending_disc_pct,
            }]

    # 3. Quantity discount — now available to all customers (controlled by admin setting)
    # Count only items meeting the minimum price threshold
    qty_eligible_items = 0
    if qty_allowed:
        # Priority: explicit qty_items arg > _qty_eligible_item_count attr > _cart_item_count attr
        if qty_items and qty_items > 0:
            qty_eligible_items = qty_items
        elif hasattr(user, '_qty_eligible_item_count') and user is not None:
            qty_eligible_items = user._qty_eligible_item_count
        elif hasattr(user, '_cart_item_count') and user is not None:
            # Fallback: use total item count (caller didn't filter)
            qty_eligible_items = user._cart_item_count

        qty_discounts = _get_active_quantity_discounts()
        for qd in reversed(qty_discounts):
            if qty_eligible_items >= qd.min_items:
                qty_disc_amt = round(float(qd.discount_amount), 2)
                if qty_disc_amt > 0:
                    savings_breakdown.append({
                        'label': qd.label or f'{qd.min_items} Eligible Items Discount',
                        'amount': qty_disc_amt,
                        'pct': None,
                    })
                break

    # 4. Cart Incentive discount — Gold+ only
    if incentive_allowed:
        cart_incentives = _get_active_cart_incentives()
        best_incentive = None
        for inc in reversed(cart_incentives):
            if cart_subtotal >= float(inc.min_cart_value):
                best_incentive = inc
                break
        if best_incentive:
            incentive_disc_amt = round(float(best_incentive.discount_offered), 2)
            if incentive_disc_amt > 0:
                savings_breakdown.append({
                    'label': best_incentive.popup_text or 'Cart Reward',
                    'amount': incentive_disc_amt,
                    'pct': None,
                })

    # Ensure discounts don't exceed cart value
    total_disc = min(loyalty_disc_amt + spending_disc_amt + qty_disc_amt + incentive_disc_amt, cart_subtotal)
    total_pct = round(total_disc / cart_subtotal * 100, 2) if cart_subtotal > 0 else 0.0

    return {
        'loyalty_discount_pct': loyalty_disc_pct,
        'loyalty_discount_amount': loyalty_disc_amt,
        'spending_discount_pct': spending_disc_pct,
        'spending_discount_amount': spending_disc_amt,
        'qty_discount_amount': qty_disc_amt,
        'incentive_discount_amount': incentive_disc_amt,
        'total_discount_amount': total_disc,
        'total_discount_pct': total_pct,
        'savings_breakdown': savings_breakdown,
        'qty_allowed': qty_allowed,
        'incentive_allowed': incentive_allowed,
        'tier_sort_order': tier_sort_order,
        'qty_eligible_items': qty_eligible_items,
        'qty_min_price': qty_min_price,
    }



# ─────────────────────────────────────────────────────────────
# Cart Incentive
# ─────────────────────────────────────────────────────────────

def get_cart_incentive_context(cart_subtotal: float) -> dict:
    """
    Return the next cart incentive the customer should aim for,
    progress percentage, and messaging.
    """
    incentives = _get_active_cart_incentives()
    if not incentives:
        return {}

    # Find the next threshold the cart hasn't reached yet
    next_incentive = None
    for inc in incentives:
        if cart_subtotal < float(inc.min_cart_value):
            next_incentive = inc
            break

    # Find the current unlocked incentive
    current_incentive = None
    for inc in reversed(incentives):
        if cart_subtotal >= float(inc.min_cart_value):
            current_incentive = inc
            break

    ctx = {
        'current_savings': float(current_incentive.discount_offered) if current_incentive else 0.0,
        'next_incentive': next_incentive.to_dict() if next_incentive else None,
        'current_incentive': current_incentive.to_dict() if current_incentive else None,
        'cart_subtotal': cart_subtotal,
    }

    if next_incentive:
        target = float(next_incentive.min_cart_value)
        needed = max(0.0, target - cart_subtotal)
        progress_pct = round(min(cart_subtotal / target * 100, 100), 1) if target > 0 else 100.0
        ctx.update({
            'amount_needed': needed,
            'progress_pct': progress_pct,
            'potential_savings': float(next_incentive.discount_offered),
            'smart_message': _build_smart_message(needed, float(next_incentive.discount_offered), current_incentive),
        })
    else:
        ctx.update({
            'amount_needed': 0.0,
            'progress_pct': 100.0,
            'potential_savings': 0.0,
            'smart_message': _max_tier_message(current_incentive),
        })

    return ctx


def get_quantity_incentive_context(qty_eligible_items: int) -> dict:
    """
    Return the next quantity discount tier the customer should aim for,
    based on the count of eligible items (priced at or above qty_discount_min_price).
    """
    settings = _get_settings()
    qty_min_price = float(getattr(settings, 'qty_discount_min_price', 2500))
    open_to_all = bool(getattr(settings, 'qty_discount_open_to_all', True))

    qty_discounts = _get_active_quantity_discounts()
    if not qty_discounts:
        return {'enabled': False}

    # Current tier achieved
    current_tier = None
    for qd in reversed(qty_discounts):
        if qty_eligible_items >= qd.min_items:
            current_tier = qd
            break

    # Next tier not yet achieved
    next_tier = None
    for qd in qty_discounts:
        if qty_eligible_items < qd.min_items:
            next_tier = qd
            break

    ctx = {
        'enabled': True,
        'open_to_all': open_to_all,
        'qty_min_price': qty_min_price,
        'qty_eligible_items': qty_eligible_items,
        'current_tier': current_tier.to_dict() if current_tier else None,
        'next_tier': next_tier.to_dict() if next_tier else None,
        'current_savings': float(current_tier.discount_amount) if current_tier else 0.0,
    }

    if next_tier:
        items_needed = next_tier.min_items - qty_eligible_items
        ctx['items_needed'] = items_needed
        ctx['smart_message'] = _build_qty_smart_message(
            items_needed, float(next_tier.discount_amount), qty_eligible_items, qty_min_price
        )
    else:
        ctx['items_needed'] = 0
        ctx['smart_message'] = (
            f'🏆 Maximum multi-buy reward unlocked! You saved {float(current_tier.discount_amount):,.0f} Birr!'
            if current_tier else '🛒 Add eligible items to unlock multi-buy savings!'
        )

    return ctx


def _build_smart_message(needed: float, savings: float, current_inc) -> str:
    if needed <= 0:
        return f'🎁 Amazing! You saved {savings:,.0f} Birr today!'
    if needed <= 500:
        return f'🔥 So close! Add {needed:,.0f} Birr more → save {savings:,.0f} Birr!'
    return f'🎉 Add {needed:,.0f} Birr more to unlock {savings:,.0f} Birr off your order.'


def _max_tier_message(inc) -> str:
    if inc:
        return f'🏆 Congratulations! You unlocked the maximum reward — saving {inc.discount_offered:,.0f} Birr!'
    return '🛒 Keep shopping to unlock spending rewards!'


def _build_qty_smart_message(items_needed: int, savings: float, current_count: int, min_price: float) -> str:
    """Build a contextual message about quantity discount progress."""
    item_word = 'item' if items_needed == 1 else 'items'
    if items_needed == 1:
        return f'🛍️ Add just 1 more eligible item (priced {min_price:,.0f}+ Birr) → unlock {savings:,.0f} Birr off!'
    if items_needed <= 3:
        return f'🛍️ Add {items_needed} more eligible {item_word} → unlock {savings:,.0f} Birr off!'
    return f'🛒 Add {items_needed} more qualifying {item_word} (each priced {min_price:,.0f}+ Birr) to unlock {savings:,.0f} Birr savings.'


# ─────────────────────────────────────────────────────────────
# Points Calculation
# ─────────────────────────────────────────────────────────────

def calculate_points_for_order(order_total: float) -> int:
    """Calculate how many reward points a purchase earns."""
    settings = _get_settings()
    if not settings.is_enabled:
        return 0
    return int((order_total / 100) * settings.points_per_100_birr)


def award_points(user, points: int, transaction_type: RewardTransactionType,
                 description: str, order=None, expires_at=None):
    """Add points to user and write a ledger entry."""
    if points == 0:
        return
    user.reward_points = (user.reward_points or 0) + points
    user.lifetime_points_earned = (user.lifetime_points_earned or 0) + max(0, points)
    txn = RewardTransaction(
        user_id=user.id,
        order_id=order.id if order else None,
        transaction_type=transaction_type,
        points=points,
        balance_after=user.reward_points,
        description=description,
        expires_at=expires_at,
    )
    db.session.add(txn)


def apply_order_status_change(user, order, new_status, previous_status=None):
    """Apply loyalty side effects for order status changes."""
    from app.models.order import OrderStatus as _OrderStatus

    previous_status = previous_status or getattr(order, 'status', None)
    if new_status in (_OrderStatus.cancelled, _OrderStatus.returned) and previous_status not in (_OrderStatus.cancelled, _OrderStatus.returned):
        try:
            return reverse_order_rewards(user, order)
        except Exception:
            return {'reversed': False, 'reason': 'reversal_failed'}
    return {'reversed': False, 'reason': 'no_reversal_needed'}


def reverse_order_rewards(user, order):
    """Reverse loyalty stats for a cancelled or returned order."""
    if not user or not order:
        return {'reversed': False, 'reason': 'missing_user_or_order'}

    existing_reversal = (
        RewardTransaction.query
        .filter_by(user_id=user.id, order_id=order.id, transaction_type=RewardTransactionType.adjust)
        .filter(RewardTransaction.points <= 0)
        .first()
    )
    if existing_reversal:
        return {'reversed': False, 'reason': 'already_reversed'}

    from app.models.order import Order as _Order, OrderStatus as _OrderStatus

    order_total = float(order.total or 0)
    savings_amount = float(getattr(order, 'savings_amount', 0) or 0)
    points_to_reverse = int(getattr(order, 'reward_earned', 0) or 0)
    items_to_reverse = int(getattr(order, 'total_items', 0) or 0)
    if items_to_reverse <= 0 and getattr(order, 'items', None):
        items_to_reverse = sum(i.quantity for i in order.items)

    user.total_money_spent = max(0.0, float(user.total_money_spent or 0) - order_total)
    user.total_orders = max(0, int(user.total_orders or 0) - 1)
    user.total_items_purchased = max(0, int(user.total_items_purchased or 0) - items_to_reverse)
    user.lifetime_savings = max(0.0, float(user.lifetime_savings or 0) - savings_amount)
    user.reward_points = max(0, int(user.reward_points or 0) - points_to_reverse)
    user.lifetime_points_earned = max(0, int(user.lifetime_points_earned or 0) - points_to_reverse)
    user.loyalty_score = max(0, int(user.loyalty_score or 0) - points_to_reverse)

    new_level = resolve_loyalty_level(float(user.total_money_spent or 0), int(user.total_orders or 0))
    user.loyalty_level_id = new_level.id if new_level else None

    if user.total_orders > 0:
        if new_level:
            if 'elite' in new_level.name.lower() or user.total_orders >= 100:
                user.customer_status = CustomerStatus.elite
            elif 'vip' in new_level.name.lower() or user.total_orders >= 50:
                user.customer_status = CustomerStatus.vip
            elif user.total_orders >= 5:
                user.customer_status = CustomerStatus.loyal
            else:
                user.customer_status = CustomerStatus.active
        else:
            user.customer_status = CustomerStatus.active
    else:
        user.customer_status = CustomerStatus.new

    # Flush session to ensure order status change is visible to the query below
    db.session.flush()

    remaining_orders = (
        _Order.query
        .filter(_Order.user_id == user.id)
        .filter(~_Order.status.in_([_OrderStatus.cancelled, _OrderStatus.returned]))
        .order_by(_Order.created_at.desc())
        .all()
    )
    if remaining_orders:
        user.first_purchase_date = remaining_orders[-1].created_at
        user.last_purchase_date = remaining_orders[0].created_at
    else:
        user.last_purchase_date = None
        user.first_purchase_date = None

    txn = RewardTransaction(
        user_id=user.id,
        order_id=order.id,
        transaction_type=RewardTransactionType.adjust,
        points=-points_to_reverse,
        balance_after=user.reward_points,
        description=f'Reversal for order #{order.order_number}',
    )
    db.session.add(txn)
    db.session.flush()

    return {
        'reversed': True,
        'points_reversed': points_to_reverse,
        'order_total_reversed': order_total,
        'savings_reversed': savings_amount,
        'new_level': new_level.to_dict() if new_level else None,
    }


# Order Post-Processing (call after order is confirmed)
# ─────────────────────────────────────────────────────────────

def process_order_rewards(user, order, savings_amount: float = 0.0):
    """
    Called after a successful order is placed.
    - Updates lifetime spending, orders, items, savings
    - Upgrades loyalty tier if needed
    - Awards points
    - Checks achievement unlocks
    - Generates referral code if missing
    - Updates customer status
    """
    now = datetime.now(timezone.utc)
    settings = _get_settings()

    # Aggregate stats
    order_total = float(order.total)
    item_count = sum(i.quantity for i in order.items)
    user.total_money_spent = float(user.total_money_spent or 0) + order_total
    user.total_orders = (user.total_orders or 0) + 1
    user.total_items_purchased = (user.total_items_purchased or 0) + item_count
    user.lifetime_savings = float(user.lifetime_savings or 0) + savings_amount
    user.last_purchase_date = now
    if not user.first_purchase_date:
        user.first_purchase_date = now

    # Generate referral code if not set
    if not user.referral_code:
        from app.models.user import User as _User
        code = _generate_referral_code()
        while _User.query.filter_by(referral_code=code).first():
            code = _generate_referral_code()
        user.referral_code = code

    # Tier upgrade
    new_level = resolve_loyalty_level(float(user.total_money_spent), user.total_orders)
    old_level_id = user.loyalty_level_id
    if new_level:
        user.loyalty_level_id = new_level.id

    # Update customer status based on new level / orders
    if new_level:
        rights = new_level.get_access_rights()
        if 'elite' in new_level.name.lower() or user.total_orders >= 100:
            user.customer_status = CustomerStatus.elite
        elif 'vip' in new_level.name.lower() or user.total_orders >= 50:
            user.customer_status = CustomerStatus.vip
        elif user.total_orders >= 5:
            user.customer_status = CustomerStatus.loyal
        else:
            user.customer_status = CustomerStatus.active
    elif user.total_orders > 0:
        user.customer_status = CustomerStatus.active

    # Award points for this order
    points = calculate_points_for_order(order_total)
    # Large order bonus
    if settings.is_enabled and order_total >= float(settings.bonus_large_order_threshold):
        points += settings.bonus_large_order_points

    if points > 0 and settings.is_enabled:
        award_points(
            user=user, points=points,
            transaction_type=RewardTransactionType.earn_purchase,
            description=f'Purchase reward — Order #{order.order_number}',
            order=order,
        )
        user.loyalty_score = (user.loyalty_score or 0) + points

    # Update order snapshot
    order.savings_amount = savings_amount
    order.reward_earned = points
    order.loyalty_level_id_after = user.loyalty_level_id
    order.lifetime_total_after = user.total_money_spent
    order.total_items = item_count

    # Check achievements
    newly_unlocked = _check_and_award_achievements(user)

    db.session.flush()

    return {
        'tier_changed': new_level and new_level.id != old_level_id,
        'new_level': new_level.to_dict() if new_level else None,
        'points_earned': points,
        'new_achievements': [ua.to_dict() for ua in newly_unlocked],
        'lifetime_savings': float(user.lifetime_savings),
    }


# ─────────────────────────────────────────────────────────────
# Achievements
# ─────────────────────────────────────────────────────────────

def _check_and_award_achievements(user) -> list:
    """Check all achievements and award any newly unlocked ones."""
    achievements = Achievement.query.filter_by(is_active=True).all()
    already_unlocked = {ua.achievement_id for ua in user.achievements.all()}
    newly_unlocked = []

    for ach in achievements:
        if ach.id in already_unlocked:
            continue
        if _achievement_met(user, ach):
            ua = UserAchievement(user_id=user.id, achievement_id=ach.id)
            db.session.add(ua)
            # Award points for achievement
            if ach.points_awarded > 0:
                award_points(
                    user=user, points=ach.points_awarded,
                    transaction_type=RewardTransactionType.earn_achievement,
                    description=f'Achievement unlocked: {ach.name}',
                )
            newly_unlocked.append(ua)

    return newly_unlocked


def _achievement_met(user, achievement: Achievement) -> bool:
    t = achievement.trigger_type
    v = float(achievement.trigger_value)
    if t == 'orders_count':
        return (user.total_orders or 0) >= v
    if t == 'spending_total':
        return float(user.total_money_spent or 0) >= v
    if t == 'items_count':
        return (user.total_items_purchased or 0) >= v
    if t == 'review_count':
        return user.reviews.count() >= v
    if t == 'reward_points':
        return (user.reward_points or 0) >= v
    return False


# ─────────────────────────────────────────────────────────────
# Customer Profile — for Mini App rewards dashboard
# ─────────────────────────────────────────────────────────────

def get_customer_loyalty_profile(user) -> dict:
    """
    Return a complete loyalty profile for the Mini App dashboard.
    """
    levels = _get_active_levels()
    current_level = user.loyalty_level
    next_level = None
    for lvl in levels:
        if not current_level or lvl.sort_order > current_level.sort_order:
            next_level = lvl
            break

    spending = float(user.total_money_spent or 0)

    # Progress to next level
    progress = {}
    if next_level:
        target_spending = float(next_level.min_spending)
        needed_spending = max(0.0, target_spending - spending)
        needed_orders = max(0, next_level.min_orders - (user.total_orders or 0))
        pct = round(min(spending / target_spending * 100, 100), 1) if target_spending > 0 else 100.0
        progress = {
            'next_level': next_level.to_dict(),
            'needed_spending': needed_spending,
            'needed_orders': needed_orders,
            'progress_pct': pct,
            'spending_gap_label': f'{needed_spending:,.0f} Birr',
        }

    # Unlocked & locked achievements
    unlocked_ids = {ua.achievement_id for ua in user.achievements.all()}
    all_achievements = Achievement.query.filter_by(is_active=True).order_by(Achievement.sort_order).all()
    unlocked = [a.to_dict() for a in all_achievements if a.id in unlocked_ids]
    locked = [a.to_dict() for a in all_achievements if a.id not in unlocked_ids]

    # Recent reward transactions
    recent_txns = (
        RewardTransaction.query
        .filter_by(user_id=user.id)
        .order_by(RewardTransaction.created_at.desc())
        .limit(20)
        .all()
    )

    # Spending thresholds (for non-loyal / new customers based on cart subtotal)
    thresholds = _get_active_thresholds()
    spending_thresholds_ctx = [t.to_dict() for t in thresholds]

    # Quantity discounts
    qty_discounts = _get_active_quantity_discounts()
    qty_discounts_ctx = [qd.to_dict() for qd in qty_discounts]

    return {
        'user_id': user.id,
        'full_name': user.full_name,
        'telegram_username': user.telegram_username,
        'customer_status': user.customer_status.value,
        # Flat level field for Mini App convenience
        'level': current_level.to_dict() if current_level else None,
        'next_level': next_level.to_dict() if next_level else None,
        'current_level': current_level.to_dict() if current_level else None,
        'reward_points': user.reward_points or 0,
        'lifetime_points_earned': user.lifetime_points_earned or 0,
        'total_money_spent': spending,
        'lifetime_savings': float(user.lifetime_savings or 0),
        'total_orders': user.total_orders or 0,
        'total_items_purchased': user.total_items_purchased or 0,
        'first_purchase_date': user.first_purchase_date.isoformat() if user.first_purchase_date else None,
        'last_purchase_date': user.last_purchase_date.isoformat() if user.last_purchase_date else None,
        'referral_code': user.referral_code,
        'progress': progress,
        'spending_thresholds': spending_thresholds_ctx,
        'qty_discounts': qty_discounts_ctx,
        'unlocked_achievements': unlocked,
        'locked_achievements': locked,
        'recent_transactions': [t.to_dict() for t in recent_txns],
        'all_levels': [l.to_dict() for l in levels],
    }


# ─────────────────────────────────────────────────────────────
# Product Visibility Check
# ─────────────────────────────────────────────────────────────

def can_user_see_product(user, product) -> dict:
    """
    Returns dict: {can_see: bool, can_see_price: bool, can_purchase: bool,
                   lock_message: str}
    """
    # Non-premium products always visible
    if not product.is_premium and not product.min_loyalty_level_id:
        return {
            'can_see': True,
            'can_see_price': not product.price_hidden,
            'can_purchase': True,
            'lock_message': None,
        }

    user_level_order = user.loyalty_level.sort_order if (user and user.loyalty_level) else -1
    required_level = product.min_loyalty_level

    if required_level and user_level_order < required_level.sort_order:
        return {
            'can_see': True,  # we show the product as "locked" to entice
            'can_see_price': False,
            'can_purchase': False,
            'lock_message': (
                f'Unlock this product by reaching {required_level.badge_icon} '
                f'{required_level.name} status. '
                f'Shop for {required_level.min_spending:,.0f} Birr to qualify!'
            ),
        }

    return {
        'can_see': True,
        'can_see_price': not product.price_hidden,
        'can_purchase': True,
        'lock_message': None,
    }


# ─────────────────────────────────────────────────────────────
# Seed Defaults (called once on first boot if tables are empty)
# ─────────────────────────────────────────────────────────────

def seed_default_loyalty_data():
    """
    Create sensible defaults so the admin dashboard is not empty.
    All defaults are stored in the DB and fully editable by admin.
    Only runs if tables are empty.
    """
    if LoyaltyLevel.query.count() == 0:
        defaults = [
            dict(name='Bronze', name_am='ብሮንዝ', sort_order=1, min_spending=5000, min_orders=1,
                 discount_percentage=2, badge_icon='🥉', color_hex='#CD7F32',
                 access_rights=json.dumps(['see_prices', 'purchase_regular'])),
            dict(name='Silver', name_am='ብር', sort_order=2, min_spending=25000, min_orders=3,
                 discount_percentage=4, badge_icon='🥈', color_hex='#C0C0C0',
                 access_rights=json.dumps(['see_prices', 'purchase_regular'])),
            dict(name='Gold', name_am='ወርቅ', sort_order=3, min_spending=60000, min_orders=6,
                 discount_percentage=6, badge_icon='🥇', color_hex='#FFD700',
                 access_rights=json.dumps(['see_prices', 'purchase_regular', 'see_premium'])),
            dict(name='VIP', name_am='VIP', sort_order=4, min_spending=100000, min_orders=10,
                 discount_percentage=8, badge_icon='💎', color_hex='#9B59B6',
                 access_rights=json.dumps(['see_prices', 'purchase_regular', 'see_premium', 'purchase_premium'])),
            dict(name='Elite', name_am='ኤሊት', sort_order=5, min_spending=150000, min_orders=15,
                 discount_percentage=10, badge_icon='👑', color_hex='#E74C3C',
                 access_rights=json.dumps(['see_prices', 'purchase_regular', 'see_premium', 'purchase_premium', 'priority_support'])),
        ]
        for d in defaults:
            db.session.add(LoyaltyLevel(**d))

    if SpendingThreshold.query.count() == 0:
        thresholds = [
            (5000, 1.0, '5,000+ Birr'), (10000, 1.5, '10,000+ Birr'),
            (20000, 2.0, '20,000+ Birr'), (40000, 2.5, '40,000+ Birr'),
            (60000, 3.0, '60,000+ Birr'), (80000, 3.5, '80,000+ Birr'),
            (100000, 4.0, '100,000+ Birr'), (150000, 5.0, '150,000+ Birr'),
        ]
        for i, (amt, pct, lbl) in enumerate(thresholds):
            db.session.add(SpendingThreshold(min_amount=amt, discount_percentage=pct, label=lbl, sort_order=i))

    if QuantityDiscount.query.count() == 0:
        qty_defaults = [
            (2, 100, 'Buy 2+ items'), (5, 250, 'Buy 5+ items'),
            (10, 700, 'Buy 10+ items'), (20, 1500, 'Buy 20+ items'),
        ]
        for i, (items, disc, lbl) in enumerate(qty_defaults):
            db.session.add(QuantityDiscount(min_items=items, discount_amount=disc, label=lbl, sort_order=i))

    if CartIncentive.query.count() == 0:
        incentives = [
            (1000, 50, '🎉 Add {needed} Birr more and save {savings} Birr!', 'sparkle'),
            (5000, 200, '🔥 Almost there! Save {savings} Birr!', 'confetti'),
            (10000, 500, '⭐ Big spender! You save {savings} Birr!', 'confetti'),
            (25000, 1500, '🏆 Elite shopper! Save {savings} Birr!', 'confetti'),
        ]
        for i, (val, disc, txt, anim) in enumerate(incentives):
            db.session.add(CartIncentive(min_cart_value=val, discount_offered=disc,
                                         popup_text=txt, animation=anim, sort_order=i))

    if Achievement.query.count() == 0:
        ach_defaults = [
            dict(name='First Purchase', name_am='ፈጠራ ግዢ', description='Made your first order!',
                 badge_icon='🌟', color_hex='#F39C12', trigger_type='orders_count',
                 trigger_value=1, points_awarded=100, sort_order=1),
            dict(name='Getting Started', name_am='ጀምሮ', description='Completed 5 orders',
                 badge_icon='🚀', color_hex='#3498DB', trigger_type='orders_count',
                 trigger_value=5, points_awarded=250, sort_order=2),
            dict(name='Regular Shopper', name_am='መደበኛ ሸቀጠኛ', description='Completed 10 orders',
                 badge_icon='🛍️', color_hex='#27AE60', trigger_type='orders_count',
                 trigger_value=10, points_awarded=500, sort_order=3),
            dict(name='Toy Collector', name_am='አሻንጉሊት ሰብሳቢ', description='Purchased 50 items',
                 badge_icon='🧸', color_hex='#E91E63', trigger_type='items_count',
                 trigger_value=50, points_awarded=300, sort_order=4),
            dict(name='Big Spender', name_am='ትልቅ ሸቀጠኛ', description='Spent 25,000 Birr lifetime',
                 badge_icon='💰', color_hex='#FF9800', trigger_type='spending_total',
                 trigger_value=25000, points_awarded=1000, sort_order=5),
            dict(name='VIP Member', name_am='VIP አባል', description='Spent 100,000 Birr lifetime',
                 badge_icon='💎', color_hex='#9B59B6', trigger_type='spending_total',
                 trigger_value=100000, points_awarded=5000, sort_order=6),
        ]
        for d in ach_defaults:
            db.session.add(Achievement(**d))

    if LoyaltySettings.query.count() == 0:
        db.session.add(LoyaltySettings(qty_discount_open_to_all=True))

    # Runtime fixup: ensure existing settings rows have qty_discount_open_to_all = True.
    # This corrects any row created with the wrong server_default='false' in migration.
    try:
        db.session.query(LoyaltySettings).filter(
            LoyaltySettings.qty_discount_open_to_all == False  # noqa: E712
        ).update({'qty_discount_open_to_all': True})
    except Exception:
        pass

    db.session.commit()

