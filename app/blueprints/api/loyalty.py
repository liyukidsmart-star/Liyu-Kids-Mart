"""
loyalty.py — API endpoints for the Loyalty & Rewards system.
Used by the Telegram Mini App to fetch loyalty profile,
cart incentives, and rewards data.
"""
from flask import request
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity

from app.blueprints.api import api_bp
from app.extensions import db
from app.models.user import User
from app.models.loyalty import LoyaltyLevel, Achievement, LoyaltySettings
from app.services.loyalty_service import (
    get_customer_loyalty_profile,
    get_cart_incentive_context,
    calculate_loyalty_discount,
    seed_default_loyalty_data,
)
from app.utils import success_response, error_response


def _get_user_from_request():
    """Try JWT first, then telegram_id param."""
    try:
        verify_jwt_in_request(optional=True)
        uid = get_jwt_identity()
        if uid:
            return db.session.get(User, uid)
    except Exception:
        pass
    telegram_id = (
        request.args.get('telegram_id') or
        (request.get_json(silent=True) or {}).get('telegram_id')
    )
    if telegram_id:
        return User.query.filter_by(telegram_id=str(telegram_id)).first()
    return None


# ─────────────────────────────────────────────────────────────
# GET /api/v1/loyalty/profile
# ─────────────────────────────────────────────────────────────

@api_bp.route('/loyalty/profile', methods=['GET'])
def loyalty_profile():
    """Return the full loyalty profile for the current user."""
    user = _get_user_from_request()
    if not user:
        return error_response('Authentication required', 401)
    profile = get_customer_loyalty_profile(user)
    return success_response(profile)


# ─────────────────────────────────────────────────────────────
# GET /api/v1/loyalty/cart-incentive?subtotal=XXXX
# ─────────────────────────────────────────────────────────────

@api_bp.route('/loyalty/cart-incentive', methods=['GET'])
def loyalty_cart_incentive():
    """
    Return the next cart incentive and progress for the given subtotal.
    Called live as the user adds items to the cart.
    """
    try:
        subtotal = float(request.args.get('subtotal', 0))
    except (TypeError, ValueError):
        subtotal = 0.0
    ctx = get_cart_incentive_context(subtotal)
    return success_response(ctx)


# ─────────────────────────────────────────────────────────────
# GET /api/v1/loyalty/levels
# ─────────────────────────────────────────────────────────────

@api_bp.route('/loyalty/levels', methods=['GET'])
def loyalty_levels():
    """Return all active loyalty levels for display in the Mini App."""
    levels = LoyaltyLevel.query.filter_by(is_active=True).order_by(LoyaltyLevel.sort_order).all()
    return success_response({'levels': [l.to_dict() for l in levels]})


# ─────────────────────────────────────────────────────────────
# GET /api/v1/loyalty/achievements
# ─────────────────────────────────────────────────────────────

@api_bp.route('/loyalty/achievements', methods=['GET'])
def loyalty_achievements():
    """Return achievements. If telegram_id provided, marks which are unlocked."""
    user = _get_user_from_request()
    achievements = Achievement.query.filter_by(is_active=True).order_by(Achievement.sort_order).all()

    unlocked_ids = set()
    if user:
        unlocked_ids = {ua.achievement_id for ua in user.achievements.all()}

    result = []
    for a in achievements:
        d = a.to_dict()
        d['is_unlocked'] = a.id in unlocked_ids
        result.append(d)

    return success_response({'achievements': result})


# ─────────────────────────────────────────────────────────────
# GET /api/v1/loyalty/settings
# ─────────────────────────────────────────────────────────────

@api_bp.route('/loyalty/settings', methods=['GET'])
def loyalty_settings_api():
    """Return public loyalty settings for frontend display."""
    settings = LoyaltySettings.query.first()
    if not settings:
        seed_default_loyalty_data()
        settings = LoyaltySettings.query.first()
    return success_response(settings.to_dict() if settings else {})


# ─────────────────────────────────────────────────────────────
# GET /api/v1/loyalty/discount?subtotal=X&telegram_id=Y&items=N
# ─────────────────────────────────────────────────────────────

@api_bp.route('/loyalty/discount', methods=['GET'])
def loyalty_discount():
    """
    Calculate the discount for a given cart subtotal.
    Used by the cart page to show real-time savings.
    """
    user = _get_user_from_request()
    try:
        subtotal = float(request.args.get('subtotal', 0))
        items = int(request.args.get('items', 0))
    except (TypeError, ValueError):
        subtotal = 0.0
        items = 0

    if user:
        user._cart_item_count = items

    discount_info = calculate_loyalty_discount(user, subtotal)
    return success_response(discount_info)
