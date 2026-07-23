import logging
import os

import httpx

from app.models.user import User, UserRole
from app.services.loyalty_service import _get_settings

logger = logging.getLogger(__name__)


def _collect_manager_ids():
    """Collect Telegram chat IDs for admins/managers from the DB and env."""
    manager_ids = set()

    try:
        db_managers = User.query.filter(
            User.role.in_([UserRole.admin, UserRole.manager]),
            User.telegram_id.isnot(None),
            User.is_active == True,  # noqa: E712
        ).all()
        for manager in db_managers:
            if manager.telegram_id and manager.telegram_id.strip():
                manager_ids.add(manager.telegram_id.strip())
    except Exception:
        logger.exception('Failed to load manager Telegram IDs from the database')

    for mid in os.getenv('MANAGER_TG_IDS', '').split(','):
        mid = mid.strip()
        if mid:
            manager_ids.add(mid)

    manager_ids.update({'661528493', '401413271', '403612118'})
    return sorted(manager_ids)


def notify_store_managers(order, order_items, addr, payment_method_str, discount_amount, payment_receipt_url=''):
    """Send a rich order notification to each manager via Telegram."""
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    app_url = os.getenv('APP_URL', 'http://localhost:5000').rstrip('/')
    if not token:
        return

    manager_ids = _collect_manager_ids()
    if not manager_ids:
        logger.warning('[order_notify] No manager Telegram IDs configured')
        return

    settings = _get_settings()
    telebirr_phone = (getattr(settings, 'telebirr_payment_phone', '') or '').strip() or '+251 91 162 3758'

    items_lines = []
    for oi in order_items:
        p = oi['product']
        items_lines.append(
            f"  • <b>{p.name[:40]}</b> ×{oi['qty']}  —  <b>ETB {oi['item_total']:,.0f}</b>"
        )
    items_text = '\n'.join(items_lines)

    is_regional = bool(getattr(addr, 'delivery_scope', '') == 'regional')
    pm_labels = {
        'cod': 'Place Order' if is_regional else 'Cash on Delivery',
        'telebirr': 'TeleBirr',
        'chapa': 'Chapa',
    }
    pm_label = pm_labels.get(payment_method_str, payment_method_str.upper())
    subtotal = float(order.subtotal)
    delivery_fee = float(order.delivery_fee)
    total = float(order.total)

    store_url = f'{app_url}/telegram/store-app?v=2'
    maps_link = ''
    if addr.lat and addr.lng and not is_regional:
        maps_link = f'\n🗺 <a href="https://maps.google.com/?q={addr.lat},{addr.lng}">View on Map</a>'

    discount_line = ''
    if discount_amount:
        discount_line = f'\n🎁 <b>Discount:</b>  -ETB {discount_amount:,.0f}'

    receipt_line = ''
    if payment_receipt_url and payment_method_str == 'telebirr':
        receipt_line = f'\n🧾 <a href="{payment_receipt_url}">View TeleBirr Receipt</a>'

    location_value = addr.specific_location or 'Not specified'
    if is_regional:
        location_value = ', '.join(
            [p for p in [getattr(addr, 'region', '') or '', getattr(addr, 'city_town', '') or ''] if p]
        ) or location_value

    msg = (
        f"🛍 <b>NEW ORDER #{order.order_number}</b>\n\n"
        f"👤 <b>Customer:</b>  {order.user.full_name or 'Customer'}\n"
        f"📞 <b>Phone:</b>  {addr.phone}\n"
        f"📍 <b>Order Type:</b>  {'Regional Order' if is_regional else 'Addis Delivery'}\n"
        f"📌 <b>Location:</b>  {location_value}{maps_link}\n\n"
        f"📦 <b>Items:</b>\n{items_text}\n\n"
        f"💰 <b>Subtotal:</b>  ETB {subtotal:,.0f}"
        f"{discount_line}\n"
        f"🛵 <b>Delivery:</b>  ETB {delivery_fee:,.0f}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 <b>TOTAL:</b>  ETB {total:,.0f}\n"
        f"💳 <b>Payment:</b>  {pm_label}"
        f"{receipt_line}"
    )

    if payment_method_str == 'telebirr' and telebirr_phone:
        msg += f"\n📲 <b>TeleBirr Phone:</b>  {telebirr_phone}"

    reply_markup = {
        'inline_keyboard': [[
            {'text': '🤖 Open Bot', 'url': 'https://t.me/Liyu_Kids_Mart_Bot'},
            {'text': '🌐 Open Store Portal', 'web_app': {'url': store_url}},
        ]]
    }

    for manager_id in manager_ids:
        try:
            resp = httpx.post(
                f'https://api.telegram.org/bot{token}/sendMessage',
                json={
                    'chat_id': manager_id,
                    'text': msg,
                    'parse_mode': 'HTML',
                    'disable_web_page_preview': True,
                    'reply_markup': reply_markup,
                },
                timeout=8,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.error('[order_notify] Failed to send to %s: %s', manager_id, exc, exc_info=True)
