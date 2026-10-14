import asyncio
import logging
import os

import httpx
from flask import current_app, jsonify, render_template, request

from app.blueprints.telegram_webhook import telegram_bp
from app.extensions import db
from app.models.user import User, UserRole
from telegram_bot.bot import process_webhook_update

logger = logging.getLogger(__name__)


def _normalize_base_url(value):
    value = (value or '').strip().rstrip('/')
    if not value:
        return ''
    if value.startswith('http://') or value.startswith('https://'):
        return value
    return f'https://{value}'


def _build_webhook_target():
    """Build the public Telegram webhook URL for this deployment."""
    configured = _normalize_base_url(current_app.config.get('TELEGRAM_WEBHOOK_URL'))
    if configured:
        if configured.endswith('/telegram/webhook'):
            return configured
        return f'{configured}/telegram/webhook'

    vercel_url = _normalize_base_url(os.getenv('VERCEL_URL', ''))
    if vercel_url:
        return f'{vercel_url}/telegram/webhook'

    return ''


def _set_telegram_webhook():
    token = current_app.config.get('TELEGRAM_BOT_TOKEN', '').strip()
    webhook_target = _build_webhook_target()
    if not token or not webhook_target:
        return {'ok': False, 'message': 'Bot token or webhook URL not configured'}, 400

    url = f'https://api.telegram.org/bot{token}/setWebhook'
    resp = httpx.post(url, json={'url': webhook_target}, timeout=20)
    return resp.json(), resp.status_code


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

    # --- Intercept approval callback_queries ---
    callback_query = data.get('callback_query')
    if callback_query:
        cb_data = (callback_query.get('data') or '').strip()
        if cb_data.startswith('approve_post_') or cb_data.startswith('reject_post_'):
            return _handle_post_approval_callback(callback_query)

    try:
        asyncio.run(process_webhook_update(data))
    except Exception as exc:
        logger.exception('Telegram webhook processing failed')
        return jsonify({'ok': False, 'message': str(exc)}), 500

    return jsonify({'ok': True})


def _handle_post_approval_callback(callback_query: dict):
    """Handle approve_post_<id> / reject_post_<id> inline keyboard callbacks."""
    import httpx
    from app.models.marketing import TelegramChannelPost

    callback_id = callback_query.get('id', '')
    cb_data = (callback_query.get('data') or '').strip()
    from_user = callback_query.get('from', {})
    manager_tg_id = str(from_user.get('id', ''))
    manager_name = from_user.get('first_name', 'Manager')

    token = current_app.config.get('TELEGRAM_BOT_TOKEN', '').strip()

    def answer(text='', alert=False):
        try:
            httpx.post(
                f'https://api.telegram.org/bot{token}/answerCallbackQuery',
                json={'callback_query_id': callback_id, 'text': text, 'show_alert': alert},
                timeout=10,
            )
        except Exception:
            pass

    action = 'approve' if cb_data.startswith('approve_post_') else 'reject'
    try:
        post_id = int(cb_data.split('_post_')[1])
    except (ValueError, IndexError):
        answer('Invalid post ID.', alert=True)
        return jsonify({'ok': False})

    post = db.session.get(TelegramChannelPost, post_id)
    if not post:
        answer('Post not found.', alert=True)
        return jsonify({'ok': False})

    if post.status not in ('pending_approval', 'draft', 'failed'):
        answer(f'Post is already "{post.status}" — no action taken.', alert=True)
        return jsonify({'ok': True})

    if action == 'reject':
        post.status = 'failed'
        post.error_message = f'Rejected by {manager_name} (TG ID: {manager_tg_id})'
        db.session.commit()
        answer('❌ Post rejected and will NOT be published to the channel.', alert=True)
        logger.info('Grouped post #%s rejected by %s', post_id, manager_tg_id)
        return jsonify({'ok': True})

    # APPROVE → publish to channel
    try:
        from app.services.telegram_marketing import publish_channel_post, channel_button_link_mode
        from app.blueprints.admin.routes import _post_image_urls

        product = post.product
        image_urls = _post_image_urls(post)
        if post.post_type == 'product' and not image_urls and product:
            image_urls = [product.primary_image()]

        result = asyncio.run(publish_channel_post(
            post,
            images=image_urls or None,
            product=product,
            button_text=post.button_text or '🌐 Open Mini App',
            button_url=post.button_url or '',
        ))

        if result.get('ok'):
            from datetime import datetime, timezone
            post.status = 'sent'
            post.sent_at = datetime.now(timezone.utc)
            post.error_message = None
            message_ids = result.get('message_ids')
            if message_ids:
                import json as _json
                post.sent_message_id = _json.dumps([m for m in message_ids if m])
            else:
                post.sent_message_id = str((result.get('result') or {}).get('message_id') or '')
            db.session.commit()
            answer(f'✅ Approved by {manager_name}! Post is now live on the channel.', alert=True)
            logger.info('Grouped post #%s approved and published by %s', post_id, manager_tg_id)
        else:
            err = result.get('error') or result.get('description') or 'Unknown error'
            post.status = 'failed'
            post.error_message = f'Approved by {manager_name} but publish failed: {err}'
            db.session.commit()
            answer(f'⚠️ Approved but publish failed: {err}', alert=True)
    except Exception as exc:
        logger.exception('Error publishing approved grouped post #%s', post_id)
        answer(f'Error during publish: {exc}', alert=True)
        return jsonify({'ok': False})

    return jsonify({'ok': True})




@telegram_bp.route('/health', methods=['GET'])
def health():
    """Quick health check for Vercel and Telegram webhook wiring."""
    return jsonify({
        'ok': True,
        'webhook_target': _build_webhook_target(),
        'has_token': bool(current_app.config.get('TELEGRAM_BOT_TOKEN')),
    })


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
    payload, status = _set_telegram_webhook()
    return jsonify(payload), status


@telegram_bp.route('/status', methods=['GET'])
def status():
    """Inspect Telegram's view of the webhook."""
    token = current_app.config.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not token:
        return jsonify({'ok': False, 'message': 'TELEGRAM_BOT_TOKEN not configured'}), 400

    url = f'https://api.telegram.org/bot{token}/getWebhookInfo'
    resp = httpx.get(url, timeout=20)
    return jsonify({
        'ok': resp.status_code == 200,
        'expected_webhook': _build_webhook_target(),
        'telegram': resp.json(),
    }), resp.status_code


# -- MINI APP --
@telegram_bp.route('/mini-app')
def mini_app():
    from app.blueprints.api.mini_app import get_mini_app_bootstrap_data
    try:
        initial_bootstrap = get_mini_app_bootstrap_data()
    except Exception as e:
        initial_bootstrap = None
    return render_template('mini_app/index.html', initial_bootstrap=initial_bootstrap)


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


@telegram_bp.route('/store-app')
def store_app():
    """Store Management Mini App for store managers and admins."""
    return render_template('mini_app/store_app.html')


@telegram_bp.route('/post-approval-callback', methods=['POST'])
def post_approval_callback():
    """
    Telegram sends inline keyboard callback_query events here.
    We handle approve_post_<id> and reject_post_<id> actions.
    """
    import asyncio
    import httpx
    from app.models.marketing import TelegramChannelPost
    from app.services.telegram_marketing import _token, publish_channel_post, _post_image_urls_for_webhook

    data = request.get_json(silent=True) or {}
    callback_query = data.get('callback_query')
    if not callback_query:
        # Telegram sends this via webhook — check standard update format too
        callback_query = data.get('callback_query')
        if not callback_query:
            return jsonify({'ok': True})

    callback_id = callback_query.get('id', '')
    callback_data = (callback_query.get('data') or '').strip()
    from_user = callback_query.get('from', {})
    manager_tg_id = str(from_user.get('id', ''))

    token = current_app.config.get('TELEGRAM_BOT_TOKEN', '').strip()

    def answer_callback(text=''):
        """Acknowledge the callback so Telegram removes the loading spinner."""
        try:
            httpx.post(
                f'https://api.telegram.org/bot{token}/answerCallbackQuery',
                json={'callback_query_id': callback_id, 'text': text, 'show_alert': bool(text)},
                timeout=10,
            )
        except Exception:
            pass

    if callback_data.startswith('approve_post_') or callback_data.startswith('reject_post_'):
        action = 'approve' if callback_data.startswith('approve_post_') else 'reject'
        try:
            post_id = int(callback_data.split('_post_')[1])
        except (ValueError, IndexError):
            answer_callback('Invalid post ID.')
            return jsonify({'ok': False})

        post = db.session.get(TelegramChannelPost, post_id)
        if not post:
            answer_callback('Post not found.')
            return jsonify({'ok': False})

        if post.status not in ('pending_approval', 'draft', 'failed'):
            answer_callback(f'Post is already {post.status} — no action taken.')
            return jsonify({'ok': True})

        if action == 'reject':
            post.status = 'failed'
            post.error_message = f'Rejected by manager (Telegram ID: {manager_tg_id})'
            db.session.commit()
            answer_callback('❌ Post rejected. It will NOT be published.')
            logger.info('Grouped post #%s rejected by manager %s', post_id, manager_tg_id)
            return jsonify({'ok': True})

        # APPROVE — publish to channel now
        try:
            from app.blueprints.admin.routes import _publish_post_to_channel
            ok, msg = _publish_post_to_channel(post)
            if ok:
                answer_callback('✅ Approved! Post is live on the channel.')
            else:
                answer_callback(f'⚠️ Approval saved but publish failed: {msg}')
        except Exception as exc:
            logger.exception('Error publishing approved post #%s', post_id)
            answer_callback(f'Error: {exc}')
            return jsonify({'ok': False})

        return jsonify({'ok': True})

    # Unrecognised callback — just ack it
    answer_callback()
    return jsonify({'ok': True})
