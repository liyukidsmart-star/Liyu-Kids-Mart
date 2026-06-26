import asyncio
import logging

import httpx
from flask import current_app, jsonify, render_template, request

from app.blueprints.telegram_webhook import telegram_bp
from app.extensions import db
from app.models.user import User, UserRole
from telegram_bot.bot import process_webhook_update

logger = logging.getLogger(__name__)


@telegram_bp.route('/webhook', methods=['POST'])
@telegram_bp.route('/webhook/<string:secret>', methods=['POST'])
def webhook(secret=None):
    """Receive Telegram webhook updates and dispatch them through the bot handlers."""
    expected_secret = current_app.config.get('TELEGRAM_BOT_TOKEN', '')
    if secret and expected_secret and secret != expected_secret:
        return jsonify({'ok': False, 'message': 'Invalid webhook secret'}), 403

    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({'ok': False, 'message': 'Missing Telegram payload'}), 400

    logger.info(f"Telegram webhook received: {data.get('update_id')}")
    try:
        asyncio.run(process_webhook_update(data))
    except Exception as exc:
        logger.exception('Telegram webhook processing failed')
        return jsonify({'ok': False, 'message': str(exc)}), 500

    return jsonify({'ok': True})


@telegram_bp.route('/register-user', methods=['POST'])
def register_user():
    """Called by bot.py to register/update a Telegram user in the DB."""
    data = request.get_json() or {}
    telegram_id = str(data.get('telegram_id', ''))
    if not telegram_id:
        return jsonify({'success': False, 'message': 'No telegram_id'})
    user = User.query.filter_by(telegram_id=telegram_id).first()
    if not user:
        user = User(
            telegram_id=telegram_id,
            telegram_username=data.get('telegram_username', ''),
            full_name=data.get('full_name') or 'Telegram User',
            role=UserRole.customer,
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        return jsonify({'success': True, 'action': 'created', 'user_id': user.id})
    user.full_name = data.get('full_name') or user.full_name
    user.telegram_username = data.get('telegram_username') or user.telegram_username
    db.session.commit()
    return jsonify({'success': True, 'action': 'updated', 'user_id': user.id})


@telegram_bp.route('/set-webhook')
def set_webhook():
    """Manually set the Telegram webhook URL."""
    token = current_app.config.get('TELEGRAM_BOT_TOKEN')
    webhook_url = current_app.config.get('TELEGRAM_WEBHOOK_URL')
    if not token or not webhook_url:
        return jsonify({'success': False, 'message': 'Bot token or webhook URL not configured'})

    url = f'https://api.telegram.org/bot{token}/setWebhook'
    target_url = f"{webhook_url.rstrip('/')}/{token}"
    resp = httpx.post(url, json={'url': target_url})
    return jsonify(resp.json())


# -- MINI APP --
@telegram_bp.route('/mini-app')
def mini_app():
    return render_template('mini_app/index.html')


@telegram_bp.route('/mini-app/api/products')
def mini_app_products():
    """Lightweight product list for mini app (also served via /api/v1/products)."""
    from app.models.product import Product

    products = Product.query.filter_by(is_active=True).order_by(
        Product.is_featured.desc(), Product.sales_count.desc()).limit(60).all()
    return jsonify({
        'success': True,
        'data': [p.to_dict() for p in products]
    })


@telegram_bp.route('/driver-app')
def driver_app():
    """Driver Mini App for the delivery man."""
    return render_template('mini_app/driver.html')
