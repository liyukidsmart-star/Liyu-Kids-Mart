import html
import logging
import os
from typing import Iterable, Optional
from urllib.parse import quote_plus

import httpx
from flask import current_app, has_app_context

from app.models.product import ProductImage

logger = logging.getLogger(__name__)

DEFAULT_MINI_APP_URL = os.getenv('MINI_APP_URL', 'http://localhost:5000/telegram/mini-app')
DEFAULT_APP_URL = os.getenv('APP_URL', 'http://localhost:5000')
ASK_LIYU_LABEL = '??? ????'
BUY_NOW_LABEL = '??? ???'


def _config_value(name: str, default: str = '') -> str:
    if has_app_context():
        value = current_app.config.get(name, '')
        if value:
            return str(value).strip()
    return os.getenv(name, default).strip()


def _token() -> str:
    return _config_value('TELEGRAM_BOT_TOKEN')


def _bot_username() -> str:
    # Keep the real bot handle as the fallback so channel buttons keep working
    # even if the environment variable is missing or stale.
    username = _config_value('TELEGRAM_BOT_USERNAME', 'Liyu_Kids_Mart_Bot') or 'Liyu_Kids_Mart_Bot'
    return username.lstrip('@')


def _mini_app_url() -> str:
    return _config_value('MINI_APP_URL', DEFAULT_MINI_APP_URL) or DEFAULT_MINI_APP_URL


def _telegram_mini_app_link(startapp: str = '') -> str:
    username = _bot_username()
    base = f'https://t.me/{username}?startapp'
    if startapp:
        return f'{base}={quote_plus(startapp)}'
    return base


def _channel_id(override: Optional[str] = None) -> str:
    if override:
        return str(override).strip()
    return (
        _config_value('TELEGRAM_CHANNEL_CHAT_ID')
        or _config_value('TELEGRAM_MAIN_CHANNEL_ID')
        or _config_value('TELEGRAM_CHANNEL_ID')
    )


def _truncate(text: str, limit: int) -> str:
    text = (text or '').strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + '?'


def _absolute_url(url: str) -> str:
    if not url:
        return ''
    if url.startswith('http://') or url.startswith('https://'):
        return url
    if url.startswith('/media/') or url.startswith('/static/'):
        return f"{DEFAULT_APP_URL.rstrip('/')}{url}"
    return url


def _telegram_image_input(url: str) -> str:
    if not url:
        return ''
    if url.startswith('/media/'):
        return url.split('/media/', 1)[-1]
    if url.startswith('/static/'):
        return f"{DEFAULT_APP_URL.rstrip('/')}{url}"
    return url


def _button_markup(button_text: str, button_url: str) -> dict:
    return {
        'inline_keyboard': [[{
            'text': button_text,
            'url': button_url,
        }]]
    }


def _product_reply_markup(product) -> dict:
    product_id = getattr(product, 'id', None)
    ask_url = _telegram_mini_app_link(f'product:{product_id}') if product_id else _telegram_mini_app_link()
    buy_url = _telegram_mini_app_link()
    return {
        'inline_keyboard': [[
            {'text': ASK_LIYU_LABEL, 'url': ask_url},
            {'text': BUY_NOW_LABEL, 'url': buy_url},
        ]]
    }


def _escape(text: str) -> str:
    return html.escape(text or '')


def _build_product_caption(product, custom_caption: str = '') -> str:
    name = _escape(getattr(product, 'name_am', None) or getattr(product, 'name', '') or '')
    description = _escape(
        getattr(product, 'description_am', None)
        or getattr(product, 'short_description_am', None)
        or getattr(product, 'short_description', '')
        or getattr(product, 'description', '')
        or ''
    )
    current_price = float(getattr(product, 'current_price', lambda: product.price)())
    compare_price = getattr(product, 'compare_at_price', lambda: None)()
    age_label = _escape(getattr(product, 'age_label', lambda: '')())
    custom_caption = _escape(custom_caption.strip())

    parts = [
        'NEW ITEM ARRIVED!',
        '',
        f'Product: {name}',
    ]
    if age_label:
        parts.append(f'Age: {age_label}')
    if compare_price and float(compare_price) > current_price:
        parts.append(f'Price: {current_price:,.0f} ETB <s>{float(compare_price):,.0f} ETB</s>')
    else:
        parts.append(f'Price: {current_price:,.0f} ETB')
    if description:
        parts.extend(['', description])
    if custom_caption:
        parts.extend(['', custom_caption])

    parts.extend([
        '',
        '----------------------',
        'Address: Bole Bulbula, 93 Mazoriya, Addis Ababa',
        'Phone: 0947967117',
        '',
        'Need more info? Use Ask Liyu or Buy Now.',
    ])
    return '\n'.join(parts).strip()


def _build_announcement_caption(title: str, caption: str) -> str:
    title = _escape(title)
    caption = _escape(caption)
    parts = []
    if title:
        parts.append(f'<b>{title}</b>')
    if caption:
        parts.append(caption)
    return '\n\n'.join(parts).strip()


def _send_photo(client: httpx.AsyncClient, chat_id, photo_url: str, caption: str, reply_markup: dict) -> dict:
    return client.post(
        f"https://api.telegram.org/bot{_token()}/sendPhoto",
        json={
            'chat_id': chat_id,
            'photo': _telegram_image_input(photo_url),
            'caption': _truncate(caption, 1024),
            'parse_mode': 'HTML',
            'reply_markup': reply_markup,
        },
        timeout=20,
    )


def _looks_like_media_fetch_error(data: dict) -> bool:
    description = (data.get('description') or '').lower()
    return (
        'wrong type of the web page content' in description
        or 'failed to get http url content' in description
        or 'unsupported url protocol' in description
    )


async def _send_text_fallback(client: httpx.AsyncClient, token: str, channel_id: str, caption: str, reply_markup: dict) -> dict:
    resp = await client.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            'chat_id': channel_id,
            'text': _truncate(caption or 'Open the mini app using the button below.', 4096),
            'parse_mode': 'HTML',
            'reply_markup': reply_markup,
            'disable_web_page_preview': False,
        },
        timeout=20,
    )
    return resp.json()


async def publish_channel_post(post, *, images: Optional[Iterable[str]] = None, product=None, button_text: str = 'Open Mini App', button_url: str = '') -> dict:
    """Publish an admin-composed post to the configured Telegram channel."""
    token = _token()
    channel_id = _channel_id(getattr(post, 'channel_chat_id', None))
    if not token:
        return {'ok': False, 'error': 'TELEGRAM_BOT_TOKEN is not configured.'}
    if not channel_id:
        return {'ok': False, 'error': 'Telegram channel chat ID is not configured.'}

    caption = ''
    if getattr(post, 'post_type', 'announcement') == 'product' and product is not None:
        caption = _build_product_caption(product, getattr(post, 'caption', '') or '')
        if images is None:
            images = [getattr(product, 'primary_image', lambda: '')()]
        reply_markup = _product_reply_markup(product)
    else:
        caption = _build_announcement_caption(getattr(post, 'title', '') or '', getattr(post, 'caption', '') or '')
        reply_markup = _button_markup(button_text or getattr(post, 'button_text', '') or 'Open Mini App', button_url or _telegram_mini_app_link())

    image_urls = [img for img in (images or []) if img]
    image_urls = [_telegram_image_input(url) for url in image_urls]

    async with httpx.AsyncClient() as client:
        try:
            if not image_urls:
                resp = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={
                        'chat_id': channel_id,
                        'text': _truncate(caption or getattr(post, 'caption', '') or '', 4096),
                        'parse_mode': 'HTML',
                        'reply_markup': reply_markup,
                        'disable_web_page_preview': False,
                    },
                    timeout=20,
                )
                return resp.json()

            if len(image_urls) == 1:
                resp = await _send_photo(client, channel_id, image_urls[0], caption, reply_markup)
                data = resp.json()
                if data.get('ok') or not _looks_like_media_fetch_error(data):
                    return data
                return await _send_text_fallback(client, token, channel_id, caption, reply_markup)

            media = []
            for idx, url in enumerate(image_urls[:10]):
                item = {
                    'type': 'photo',
                    'media': url,
                }
                if idx == 0:
                    item['caption'] = _truncate(caption, 1024)
                    item['parse_mode'] = 'HTML'
                media.append(item)

            media_resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMediaGroup",
                json={'chat_id': channel_id, 'media': media},
                timeout=30,
            )
            media_data = media_resp.json()
            if not media_data.get('ok'):
                if _looks_like_media_fetch_error(media_data):
                    return await _send_text_fallback(client, token, channel_id, caption, reply_markup)
                return media_data

            message_ids = []
            for item in media_data.get('result', []):
                try:
                    message_ids.append(item.get('message_id'))
                except Exception:
                    pass

            followup_text = _truncate(caption or getattr(post, 'caption', '') or '', 900) or 'Open the mini app using the button below.'
            message_resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    'chat_id': channel_id,
                    'text': followup_text,
                    'parse_mode': 'HTML',
                    'reply_markup': reply_markup,
                    'disable_web_page_preview': False,
                },
                timeout=20,
            )
            message_data = message_resp.json()
            if message_data.get('ok'):
                try:
                    message_ids.append(message_data.get('result', {}).get('message_id'))
                except Exception:
                    pass
                message_data['message_ids'] = message_ids
            return message_data
        except Exception as exc:
            logger.exception('Telegram channel post failed: %s', exc)
            return {'ok': False, 'error': str(exc)}


def build_product_post_payload(product, *, caption: str = '', title: str = '', button_text: str = '??? ???') -> dict:
    images = []
    try:
        images = [img.image_url for img in product.images.order_by(ProductImage.sort_order.asc()).all()]
    except Exception:
        images = []
    return {
        'post_type': 'product',
        'title': title or product.name,
        'caption': caption or '',
        'button_text': button_text,
        'button_url': _telegram_mini_app_link(f'product:{product.id}'),
        'product_id': product.id,
        'images': images,
    }


def build_announcement_payload(title: str, caption: str, *, button_text: str = 'Open Mini App', button_url: str = '') -> dict:
    return {
        'post_type': 'announcement',
        'title': title,
        'caption': caption,
        'button_text': button_text,
        'button_url': button_url or _telegram_mini_app_link(),
    }
