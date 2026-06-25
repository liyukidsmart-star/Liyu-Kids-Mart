"""
Telegram broadcast service for Liyu Kids Mart.
Called when admin publishes a new product to notify all bot users.
"""
import os
import asyncio
import logging
import httpx

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
APP_URL = os.getenv('APP_URL', 'http://localhost:5000')
MINI_APP_URL = os.getenv('MINI_APP_URL', 'http://localhost:5000/telegram/mini-app')

STORE_PHONE = '0947967117'
STORE_LOCATION = 'Bole Bulbula, 93 Mazoriya, Addis Ababa'


def _build_caption(product: dict) -> str:
    """Build a beautiful Telegram caption for a new product announcement."""
    name_am = product.get('name_am') or product.get('name', '')
    short_desc_am = product.get('short_description_am') or product.get('short_description', '')
    desc_am = product.get('description_am') or product.get('description', '')
    price = product.get('price', 0)
    compare_price = product.get('compare_price')
    age_label = product.get('age_label', '')

    # Use full description and preserve line spacing
    desc_text = desc_am

    lines = [
        f"🌟 *አዲስ እቃ ገብቷል!* 🌟",
        f"",
        f"🧸 *{name_am}*",
    ]

    if age_label:
        lines.append(f"👶 *ለዕድሜ:* {age_label}")

    if compare_price and float(compare_price) > float(price):
        discount_pct = round((1 - float(price) / float(compare_price)) * 100)
        lines.append(f"💰 *ዋጋ:* {float(price):,.0f} ብር  ~~{float(compare_price):,.0f} ብር~~ — *{discount_pct}% ቅናሽ!* 🎉")
    else:
        lines.append(f"💰 *ዋጋ:* {float(price):,.0f} ብር")

    if desc_text:
        lines += ["", f"{desc_text}"]

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"📍 *አድራሻ:* {STORE_LOCATION}",
        f"📞 *ስልክ:* {STORE_PHONE}",
        "",
        "💬 *ተጨማሪ መረጃ ይፈልጋሉ?* *ልዩ* የተባለችውን የ AI ረዳታችንን ያነጋግሩ!",
    ]

    return '\n'.join(lines)


def _build_inline_keyboard(product_slug: str) -> dict:
    """Build Telegram InlineKeyboardMarkup with two buttons."""
    mini_app_store = MINI_APP_URL
    mini_app_liyu = f"{MINI_APP_URL}?tab=liyu&query={product_slug}"

    return {
        "inline_keyboard": [
            [
                {
                    "text": "🤖 ልዩን ይጠይቁ",
                    "web_app": {"url": mini_app_liyu}
                },
                {
                    "text": "🛒 አሁን ይግዙ",
                    "web_app": {"url": mini_app_store}
                }
            ]
        ]
    }


async def _get_all_bot_users_async() -> list[int]:
    """Get all unique Telegram IDs from the database."""
    from app import create_app
    from app.models.user import User
    _app = create_app('development')
    with _app.app_context():
        users = User.query.filter(
            User.telegram_id.isnot(None),
            User.is_active == True  # noqa
        ).all()
        return [int(u.telegram_id) for u in users if u.telegram_id]


async def _send_photo_to_user(client: httpx.AsyncClient, chat_id,
                               photo_url: str, caption: str, keyboard: dict) -> bool:
    """Send a product photo with caption to a single Telegram user."""
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

    # Handle different types of photo URLs
    if photo_url.startswith('/media/'):
        # Extract the Telegram file_id directly
        full_photo_url = photo_url.split('/media/')[-1]
    elif photo_url.startswith('/static/'):
        full_photo_url = f"{APP_URL}{photo_url}"
    else:
        full_photo_url = photo_url

    try:
        # Try to send photo first
        resp = await client.post(f"{api_url}/sendPhoto", json={
            "chat_id": chat_id,
            "photo": full_photo_url,
            "caption": caption,
            "parse_mode": "Markdown",
            "reply_markup": keyboard
        }, timeout=10)
        data = resp.json()
        if data.get('ok'):
            return True

        # If photo fails (e.g. local URL), fall back to text message
        resp2 = await client.post(f"{api_url}/sendMessage", json={
            "chat_id": chat_id,
            "text": caption,
            "parse_mode": "Markdown",
            "reply_markup": keyboard,
            "disable_web_page_preview": False
        }, timeout=10)
        return resp2.json().get('ok', False)
    except Exception as e:
        logger.warning(f"Failed to send to {chat_id}: {e}")
        return False


async def _broadcast_async(product: dict):
    """Async broadcast logic — sends to all bot users."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("No TELEGRAM_BOT_TOKEN set — skipping broadcast.")
        return

    telegram_ids = list(await _get_all_bot_users_async())
    
    # Also broadcast to the media channel where product photos are kept
    media_chat_id = os.environ.get('TELEGRAM_MEDIA_CHAT_ID', '').strip()
    if media_chat_id:
        try:
            telegram_ids.insert(0, int(media_chat_id))
        except ValueError:
            telegram_ids.insert(0, media_chat_id)

    if not telegram_ids:
        logger.info("No bot users to broadcast to.")
        return

    caption = _build_caption(product)
    keyboard = _build_inline_keyboard(product.get('slug', ''))
    photo_url = product.get('primary_image', '')

    logger.info(f"Broadcasting new product '{product['name']}' to {len(telegram_ids)} users…")

    async with httpx.AsyncClient() as client:
        tasks = [
            _send_photo_to_user(client, uid, photo_url, caption, keyboard)
            for uid in telegram_ids
        ]
        # Rate-limit: send in batches of 20 with 1s delay
        results = []
        batch_size = 20
        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i + batch_size]
            batch_results = await asyncio.gather(*batch, return_exceptions=True)
            results.extend(batch_results)
            if i + batch_size < len(tasks):
                await asyncio.sleep(1)

    sent = sum(1 for r in results if r is True)
    logger.info(f"Broadcast complete: {sent}/{len(telegram_ids)} delivered.")


def broadcast_new_product(product: dict):
    """
    Synchronous wrapper — call this from Flask admin routes after product creation.
    On Vercel, this must run synchronously before the HTTP response is returned, 
    otherwise the serverless function is frozen and the broadcast dies.
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_broadcast_async(product))
        loop.close()
        logger.info(f"Product broadcast completed synchronously for '{product.get('name')}'.")
    except Exception as e:
        logger.error(f"Broadcast error: {e}")
