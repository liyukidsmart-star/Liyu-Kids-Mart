"""
Admin routes for the Loyalty Management section.
All CRUD for loyalty levels, spending thresholds, quantity discounts,
cart incentives, achievements, loyalty settings, and customer profiles.
"""
import json
from datetime import datetime, timezone
from flask import render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from functools import wraps

from app.blueprints.admin import admin_bp
from app.extensions import db
from app.models.loyalty import (
    LoyaltyLevel, SpendingThreshold, QuantityDiscount, CartIncentive,
    Achievement, UserAchievement, RewardTransaction, LoyaltySettings,
    RewardTransactionType,
)
from app.models.user import User, UserRole
from app.models.order import Order, OrderStatus


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.role.value not in ('admin', 'manager'):
            flash('Access denied.', 'danger')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────────────────────────
# LOYALTY MANAGEMENT — Overview
# ─────────────────────────────────────────────────────────────

@admin_bp.route('/loyalty')
@admin_required
def loyalty_overview():
    levels = LoyaltyLevel.query.order_by(LoyaltyLevel.sort_order).all()
    settings = LoyaltySettings.query.first()
    total_customers = User.query.filter_by(role=UserRole.customer).count()

    # Tier distribution
    tier_dist = []
    for level in levels:
        count = User.query.filter_by(loyalty_level_id=level.id).count()
        tier_dist.append({'level': level, 'count': count})
    no_tier = User.query.filter_by(role=UserRole.customer, loyalty_level_id=None).count()

    # Top stats
    top_spenders = (
        User.query.filter_by(role=UserRole.customer)
        .order_by(User.total_money_spent.desc())
        .limit(5).all()
    )
    total_savings_given = db.session.query(
        db.func.sum(User.lifetime_savings)
    ).filter_by(role=UserRole.customer).scalar() or 0

    return render_template(
        'admin/loyalty/overview.html',
        levels=levels, settings=settings,
        total_customers=total_customers, tier_dist=tier_dist,
        no_tier=no_tier, top_spenders=top_spenders,
        total_savings_given=total_savings_given,
    )


# ─────────────────────────────────────────────────────────────
# LOYALTY LEVELS
# ─────────────────────────────────────────────────────────────

@admin_bp.route('/loyalty/levels')
@admin_required
def loyalty_levels():
    levels = LoyaltyLevel.query.order_by(LoyaltyLevel.sort_order).all()
    return render_template('admin/loyalty/levels.html', levels=levels)


@admin_bp.route('/loyalty/levels/create', methods=['GET', 'POST'])
@admin_required
def loyalty_level_create():
    if request.method == 'POST':
        rights = request.form.getlist('access_rights')
        level = LoyaltyLevel(
            name=request.form.get('name', '').strip(),
            name_am=request.form.get('name_am', '').strip(),
            sort_order=int(request.form.get('sort_order', 0) or 0),
            min_spending=float(request.form.get('min_spending', 0) or 0),
            min_orders=int(request.form.get('min_orders', 0) or 0),
            discount_percentage=float(request.form.get('discount_percentage', 0) or 0),
            badge_icon=request.form.get('badge_icon', '🏅').strip() or '🏅',
            badge_url=request.form.get('badge_url', '').strip() or None,
            color_hex=request.form.get('color_hex', '#CD7F32').strip() or '#CD7F32',
            description=request.form.get('description', '').strip(),
            access_rights=json.dumps(rights),
            is_active='is_active' in request.form,
        )
        db.session.add(level)
        db.session.commit()
        flash(f'✅ Loyalty level "{level.name}" created!', 'success')
        return redirect(url_for('admin.loyalty_levels'))
    return render_template('admin/loyalty/level_form.html', level=None)


@admin_bp.route('/loyalty/levels/<int:level_id>/edit', methods=['GET', 'POST'])
@admin_required
def loyalty_level_edit(level_id):
    level = db.session.get(LoyaltyLevel, level_id)
    if not level:
        flash('Level not found.', 'danger')
        return redirect(url_for('admin.loyalty_levels'))
    if request.method == 'POST':
        rights = request.form.getlist('access_rights')
        level.name = request.form.get('name', level.name).strip()
        level.name_am = request.form.get('name_am', level.name_am or '').strip()
        level.sort_order = int(request.form.get('sort_order', level.sort_order) or 0)
        level.min_spending = float(request.form.get('min_spending', level.min_spending) or 0)
        level.min_orders = int(request.form.get('min_orders', level.min_orders) or 0)
        level.discount_percentage = float(request.form.get('discount_percentage', level.discount_percentage) or 0)
        level.badge_icon = request.form.get('badge_icon', level.badge_icon or '🏅').strip() or '🏅'
        level.badge_url = request.form.get('badge_url', '').strip() or None
        level.color_hex = request.form.get('color_hex', level.color_hex or '#CD7F32').strip()
        level.description = request.form.get('description', '').strip()
        level.access_rights = json.dumps(rights)
        level.is_active = 'is_active' in request.form
        db.session.commit()
        flash(f'✅ Level "{level.name}" updated!', 'success')
        return redirect(url_for('admin.loyalty_levels'))
    return render_template('admin/loyalty/level_form.html', level=level)


@admin_bp.route('/loyalty/levels/<int:level_id>/delete', methods=['POST'])
@admin_required
def loyalty_level_delete(level_id):
    level = db.session.get(LoyaltyLevel, level_id)
    if level:
        level.is_active = False
        db.session.commit()
        flash(f'Level "{level.name}" deactivated.', 'success')
    return redirect(url_for('admin.loyalty_levels'))


# ─────────────────────────────────────────────────────────────
# SPENDING THRESHOLDS
# ─────────────────────────────────────────────────────────────

@admin_bp.route('/loyalty/thresholds', methods=['GET', 'POST'])
@admin_required
def loyalty_thresholds():
    if request.method == 'POST':
        action = request.form.get('action', 'create')
        if action == 'delete':
            tid = int(request.form.get('id', 0))
            t = db.session.get(SpendingThreshold, tid)
            if t:
                db.session.delete(t)
                db.session.commit()
                flash('Threshold deleted.', 'success')
        elif action == 'edit':
            tid = int(request.form.get('id', 0))
            t = db.session.get(SpendingThreshold, tid)
            if t:
                t.min_amount = float(request.form.get('min_amount', t.min_amount))
                t.discount_percentage = float(request.form.get('discount_percentage', t.discount_percentage))
                t.label = request.form.get('label', t.label or '').strip() or None
                t.is_active = 'is_active' in request.form
                db.session.commit()
                flash('Threshold updated!', 'success')
        else:
            t = SpendingThreshold(
                min_amount=float(request.form.get('min_amount', 0)),
                discount_percentage=float(request.form.get('discount_percentage', 0)),
                label=request.form.get('label', '').strip() or None,
                is_active=True,
                sort_order=SpendingThreshold.query.count(),
            )
            db.session.add(t)
            db.session.commit()
            flash('✅ Threshold added!', 'success')
        return redirect(url_for('admin.loyalty_thresholds'))

    thresholds = SpendingThreshold.query.order_by(SpendingThreshold.min_amount.asc()).all()
    return render_template('admin/loyalty/thresholds.html', thresholds=thresholds)


# ─────────────────────────────────────────────────────────────
# QUANTITY DISCOUNTS
# ─────────────────────────────────────────────────────────────

@admin_bp.route('/loyalty/quantity-discounts', methods=['GET', 'POST'])
@admin_required
def loyalty_qty_discounts():
    if request.method == 'POST':
        action = request.form.get('action', 'create')
        if action == 'delete':
            qid = int(request.form.get('id', 0))
            q = db.session.get(QuantityDiscount, qid)
            if q:
                db.session.delete(q)
                db.session.commit()
                flash('Quantity discount deleted.', 'success')
        elif action == 'edit':
            qid = int(request.form.get('id', 0))
            q = db.session.get(QuantityDiscount, qid)
            if q:
                q.min_items = int(request.form.get('min_items', q.min_items))
                q.discount_amount = float(request.form.get('discount_amount', q.discount_amount))
                q.label = request.form.get('label', q.label or '').strip() or None
                q.is_active = 'is_active' in request.form
                db.session.commit()
                flash('Quantity discount updated!', 'success')
        else:
            q = QuantityDiscount(
                min_items=int(request.form.get('min_items', 1)),
                discount_amount=float(request.form.get('discount_amount', 0)),
                label=request.form.get('label', '').strip() or None,
                is_active=True,
            )
            db.session.add(q)
            db.session.commit()
            flash('✅ Quantity discount added!', 'success')
        return redirect(url_for('admin.loyalty_qty_discounts'))

    discounts = QuantityDiscount.query.order_by(QuantityDiscount.min_items.asc()).all()
    from app.services.loyalty_service import _get_settings
    settings = _get_settings()
    return render_template('admin/loyalty/qty_discounts.html', discounts=discounts, settings=settings)


# ─────────────────────────────────────────────────────────────
# CART INCENTIVES
# ─────────────────────────────────────────────────────────────

@admin_bp.route('/loyalty/cart-incentives', methods=['GET', 'POST'])
@admin_required
def loyalty_cart_incentives():
    if request.method == 'POST':
        action = request.form.get('action', 'create')
        if action == 'delete':
            cid = int(request.form.get('id', 0))
            c = db.session.get(CartIncentive, cid)
            if c:
                db.session.delete(c)
                db.session.commit()
                flash('Cart incentive deleted.', 'success')
        elif action == 'edit':
            cid = int(request.form.get('id', 0))
            c = db.session.get(CartIncentive, cid)
            if c:
                c.min_cart_value = float(request.form.get('min_cart_value', c.min_cart_value))
                c.discount_offered = float(request.form.get('discount_offered', c.discount_offered))
                c.popup_text = request.form.get('popup_text', c.popup_text or '').strip() or None
                c.animation = request.form.get('animation', c.animation or 'confetti')
                c.is_active = 'is_active' in request.form
                db.session.commit()
                flash('Cart incentive updated!', 'success')
        else:
            c = CartIncentive(
                min_cart_value=float(request.form.get('min_cart_value', 0)),
                discount_offered=float(request.form.get('discount_offered', 0)),
                popup_text=request.form.get('popup_text', '').strip() or None,
                animation=request.form.get('animation', 'confetti'),
                is_active=True,
                sort_order=CartIncentive.query.count(),
            )
            db.session.add(c)
            db.session.commit()
            flash('✅ Cart incentive added!', 'success')
        return redirect(url_for('admin.loyalty_cart_incentives'))

    incentives = CartIncentive.query.order_by(CartIncentive.min_cart_value.asc()).all()
    return render_template('admin/loyalty/cart_incentives.html', incentives=incentives)


# ─────────────────────────────────────────────────────────────
# ACHIEVEMENTS
# ─────────────────────────────────────────────────────────────

@admin_bp.route('/loyalty/achievements', methods=['GET', 'POST'])
@admin_required
def loyalty_achievements():
    if request.method == 'POST':
        action = request.form.get('action', 'create')
        if action == 'delete':
            aid = int(request.form.get('id', 0))
            a = db.session.get(Achievement, aid)
            if a:
                a.is_active = False
                db.session.commit()
                flash('Achievement deactivated.', 'success')
        else:
            aid = int(request.form.get('id', 0))
            a = db.session.get(Achievement, aid) if aid else Achievement()
            a.name = request.form.get('name', '').strip()
            a.name_am = request.form.get('name_am', '').strip() or None
            a.description = request.form.get('description', '').strip() or None
            a.badge_icon = request.form.get('badge_icon', '🏆').strip() or '🏆'
            a.color_hex = request.form.get('color_hex', '#FFD700').strip()
            a.trigger_type = request.form.get('trigger_type', 'orders_count')
            a.trigger_value = float(request.form.get('trigger_value', 1) or 1)
            a.points_awarded = int(request.form.get('points_awarded', 0) or 0)
            a.is_active = 'is_active' in request.form
            if not aid:
                db.session.add(a)
            db.session.commit()
            flash('✅ Achievement saved!', 'success')
        return redirect(url_for('admin.loyalty_achievements'))

    achievements = Achievement.query.order_by(Achievement.sort_order, Achievement.id).all()
    return render_template('admin/loyalty/achievements.html', achievements=achievements)


# ─────────────────────────────────────────────────────────────
# LOYALTY SETTINGS (global points config)
# ─────────────────────────────────────────────────────────────

@admin_bp.route('/loyalty/settings', methods=['GET', 'POST'])
@admin_required
def loyalty_settings():
    settings = LoyaltySettings.query.first()
    if not settings:
        settings = LoyaltySettings()
        db.session.add(settings)
        db.session.commit()

    if request.method == 'POST':
        settings.points_per_100_birr = int(request.form.get('points_per_100_birr', 1) or 1)
        settings.bonus_review_points = int(request.form.get('bonus_review_points', 50) or 50)
        settings.bonus_referral_points = int(request.form.get('bonus_referral_points', 100) or 100)
        settings.bonus_daily_visit_points = int(request.form.get('bonus_daily_visit_points', 5) or 5)
        settings.bonus_large_order_threshold = float(request.form.get('bonus_large_order_threshold', 5000) or 5000)
        settings.bonus_large_order_points = int(request.form.get('bonus_large_order_points', 200) or 200)
        settings.points_expiry_days = int(request.form.get('points_expiry_days', 365) or 365)
        settings.point_value_birr = float(request.form.get('point_value_birr', 0.10) or 0.10)
        settings.min_redemption_points = int(request.form.get('min_redemption_points', 500) or 500)
        settings.is_enabled = 'is_enabled' in request.form
        # Quantity discount eligibility settings
        settings.qty_discount_min_price = float(request.form.get('qty_discount_min_price', 2500) or 2500)
        settings.qty_discount_open_to_all = 'qty_discount_open_to_all' in request.form
        db.session.commit()
        flash('✅ Loyalty settings updated!', 'success')
        return redirect(url_for('admin.loyalty_settings'))

    return render_template('admin/loyalty/settings.html', settings=settings)


# ─────────────────────────────────────────────────────────────
# LOYALTY ANALYTICS
# ─────────────────────────────────────────────────────────────

@admin_bp.route('/loyalty/analytics')
@admin_required
def loyalty_analytics():
    levels = LoyaltyLevel.query.order_by(LoyaltyLevel.sort_order).all()
    tier_dist = []
    for level in levels:
        count = User.query.filter_by(loyalty_level_id=level.id).count()
        tier_dist.append({'level': level.to_dict(), 'count': count})

    # Top spenders
    top_spenders = (
        User.query.filter_by(role=UserRole.customer)
        .order_by(User.total_money_spent.desc())
        .limit(10).all()
    )

    # Top savers
    top_savers = (
        User.query.filter_by(role=UserRole.customer)
        .order_by(User.lifetime_savings.desc())
        .limit(10).all()
    )

    # Stats
    stats = {
        'total_customers': User.query.filter_by(role=UserRole.customer).count(),
        'total_savings_given': float(
            db.session.query(db.func.sum(User.lifetime_savings))
            .filter_by(role=UserRole.customer).scalar() or 0
        ),
        'avg_order_value': float(
            db.session.query(db.func.avg(Order.total))
            .filter(Order.status == OrderStatus.delivered).scalar() or 0
        ),
        'total_points_issued': int(
            db.session.query(db.func.sum(User.lifetime_points_earned))
            .filter_by(role=UserRole.customer).scalar() or 0
        ),
        'customers_with_level': User.query.filter(
            User.role == UserRole.customer,
            User.loyalty_level_id.isnot(None)
        ).count(),
    }

    return render_template(
        'admin/loyalty/analytics.html',
        tier_dist=tier_dist, top_spenders=top_spenders,
        top_savers=top_savers, stats=stats,
    )


# ─────────────────────────────────────────────────────────────
# DISCOUNT ANALYTICS
# ─────────────────────────────────────────────────────────────

@admin_bp.route('/loyalty/discount-analytics')
@admin_required
def loyalty_discount_analytics():
    from datetime import timedelta
    from sqlalchemy import func, cast, Date

    # ---- Aggregate KPIs ----
    delivered_orders = Order.query.filter(Order.status == OrderStatus.delivered)

    total_orders_with_spending = delivered_orders.filter(
        Order.spending_discount_amount > 0
    ).count()
    total_orders_with_qty = delivered_orders.filter(
        Order.qty_discount_amount_saved > 0
    ).count()
    total_spending_disc_birr = float(
        db.session.query(func.sum(Order.spending_discount_amount))
        .filter(Order.status == OrderStatus.delivered).scalar() or 0
    )
    total_qty_disc_birr = float(
        db.session.query(func.sum(Order.qty_discount_amount_saved))
        .filter(Order.status == OrderStatus.delivered).scalar() or 0
    )
    total_disc_birr = float(
        db.session.query(func.sum(Order.discount_amount))
        .filter(Order.status == OrderStatus.delivered).scalar() or 0
    )
    avg_order_before = float(
        db.session.query(func.avg(Order.subtotal))
        .filter(Order.status == OrderStatus.delivered).scalar() or 0
    )
    avg_order_after = float(
        db.session.query(func.avg(Order.total))
        .filter(Order.status == OrderStatus.delivered).scalar() or 0
    )
    total_delivered = delivered_orders.count()

    # ---- Daily chart data (last 30 days) ----
    thirty_days_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    daily_rows = (
        db.session.query(
            cast(Order.created_at, Date).label('day'),
            func.count(Order.id).label('orders'),
            func.sum(Order.discount_amount).label('disc_total'),
            func.sum(Order.spending_discount_amount).label('spending_disc'),
            func.sum(Order.qty_discount_amount_saved).label('qty_disc'),
        )
        .filter(
            Order.status == OrderStatus.delivered,
            Order.created_at >= thirty_days_ago,
        )
        .group_by(cast(Order.created_at, Date))
        .order_by(cast(Order.created_at, Date).asc())
        .all()
    )
    chart_labels = [str(row.day) for row in daily_rows]
    chart_spending = [float(row.spending_disc or 0) for row in daily_rows]
    chart_qty = [float(row.qty_disc or 0) for row in daily_rows]
    chart_orders = [int(row.orders or 0) for row in daily_rows]

    # ---- Most common spending threshold reached ----
    from app.models.loyalty import SpendingThreshold
    thresholds = SpendingThreshold.query.filter_by(is_active=True).order_by(
        SpendingThreshold.min_amount.desc()
    ).all()
    most_common_threshold = None
    for t in thresholds:
        cnt = delivered_orders.filter(
            Order.spending_discount_amount > 0,
            Order.subtotal >= float(t.min_amount)
        ).count()
        if cnt > 0:
            most_common_threshold = {'label': t.label or f'{t.min_amount:.0f}+ Birr', 'count': cnt}
            break

    # ---- Revenue from smart-price-adjusted products ----
    from app.models.product import Product as _Product
    from app.models.order import OrderItem as _OrderItem
    smart_revenue = float(
        db.session.query(func.sum(_OrderItem.total_price))
        .join(_Product, _OrderItem.product_id == _Product.id)
        .filter(_Product.smart_price_enabled == True)
        .scalar() or 0
    )
    smart_product_count = _Product.query.filter_by(
        smart_price_enabled=True, is_active=True
    ).count()

    # ---- Recent discounted orders ----
    recent_orders = (
        Order.query
        .filter(
            Order.status == OrderStatus.delivered,
            Order.discount_amount > 0,
        )
        .order_by(Order.created_at.desc())
        .limit(20)
        .all()
    )

    stats = {
        'total_orders_with_spending': total_orders_with_spending,
        'total_orders_with_qty': total_orders_with_qty,
        'total_spending_disc_birr': total_spending_disc_birr,
        'total_qty_disc_birr': total_qty_disc_birr,
        'total_disc_birr': total_disc_birr,
        'avg_order_before': avg_order_before,
        'avg_order_after': avg_order_after,
        'total_delivered': total_delivered,
        'smart_revenue': smart_revenue,
        'smart_product_count': smart_product_count,
        'most_common_threshold': most_common_threshold,
    }

    return render_template(
        'admin/loyalty/discount_analytics.html',
        stats=stats,
        chart_labels=chart_labels,
        chart_spending=chart_spending,
        chart_qty=chart_qty,
        chart_orders=chart_orders,
        recent_orders=recent_orders,
    )


# ─────────────────────────────────────────────────────────────
# CUSTOMER LOYALTY PROFILE
# ─────────────────────────────────────────────────────────────

@admin_bp.route('/loyalty/customers')
@admin_required
def loyalty_customers():
    q = request.args.get('q', '').strip()
    status_filter = request.args.get('status', '')
    level_filter = request.args.get('level_id', '')

    query = User.query.filter_by(role=UserRole.customer)
    if q:
        from sqlalchemy import or_
        query = query.filter(or_(
            User.full_name.ilike(f'%{q}%'),
            User.telegram_username.ilike(f'%{q}%'),
            User.phone.ilike(f'%{q}%'),
        ))
    if status_filter:
        query = query.filter(User.customer_status == status_filter)
    if level_filter:
        query = query.filter(User.loyalty_level_id == int(level_filter))

    pagination = query.order_by(User.total_money_spent.desc()).paginate(
        page=request.args.get('page', 1, int), per_page=30, error_out=False
    )
    levels = LoyaltyLevel.query.order_by(LoyaltyLevel.sort_order).all()

    return render_template(
        'admin/loyalty/customers.html',
        customers=pagination.items, pagination=pagination,
        levels=levels, q=q, status_filter=status_filter, level_filter=level_filter,
    )


@admin_bp.route('/loyalty/customers/<int:user_id>')
@admin_required
def loyalty_customer_profile(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash('Customer not found.', 'danger')
        return redirect(url_for('admin.loyalty_customers'))

    levels = LoyaltyLevel.query.order_by(LoyaltyLevel.sort_order).all()
    orders = (
        Order.query.filter_by(user_id=user.id)
        .order_by(Order.created_at.desc()).all()
    )
    transactions = (
        RewardTransaction.query.filter_by(user_id=user.id)
        .order_by(RewardTransaction.created_at.desc()).limit(50).all()
    )
    unlocked_achievements = (
        UserAchievement.query.filter_by(user_id=user.id)
        .join(Achievement).all()
    )

    # Progress to next level
    current_level = user.loyalty_level
    next_level = None
    for lvl in levels:
        if not current_level or lvl.sort_order > current_level.sort_order:
            next_level = lvl
            break

    spending = float(user.total_money_spent or 0)
    progress = {}
    if next_level:
        target = float(next_level.min_spending)
        needed = max(0.0, target - spending)
        pct = round(min(spending / target * 100, 100), 1) if target > 0 else 100.0
        progress = {
            'next_level': next_level,
            'needed_spending': needed,
            'needed_orders': max(0, next_level.min_orders - (user.total_orders or 0)),
            'progress_pct': pct,
        }

    return render_template(
        'admin/loyalty/customer_profile.html',
        user=user, orders=orders, transactions=transactions,
        unlocked_achievements=unlocked_achievements,
        levels=levels, progress=progress,
    )


@admin_bp.route('/loyalty/customers/<int:user_id>/adjust-points', methods=['POST'])
@admin_required
def loyalty_adjust_points(user_id):
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({'success': False, 'message': 'User not found'}), 404
    points = int(request.form.get('points', 0))
    reason = request.form.get('reason', 'Manual admin adjustment').strip()
    user.reward_points = max(0, (user.reward_points or 0) + points)
    txn = RewardTransaction(
        user_id=user.id,
        transaction_type=RewardTransactionType.adjust,
        points=points,
        balance_after=user.reward_points,
        description=f'Admin: {reason}',
    )
    db.session.add(txn)
    db.session.commit()
    flash(f'Points adjusted by {points:+d} for {user.full_name}.', 'success')
    return redirect(url_for('admin.loyalty_customer_profile', user_id=user.id))
