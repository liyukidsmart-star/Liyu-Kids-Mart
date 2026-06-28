import html
import json
import logging
import os
from typing import Iterable, List, Optional

import httpx
from app.models.product import ProductImage

logger = logging.getLogger(__name__)

DEFAULT_MINI_APP_URL = os.getenv('MINI_APP_URL', 'http://localhost:5000/telegram/mini-app')
DEFAULT_APP_URL = os.getenv('APP_URL', 'http://localhost:5000')


def _token() -> str:
    return os.getenv('TELEGRAM_BOT_TOKEN', '').strip()


def _channel_id(override: Optional[str] = None) -> str:
    if override:
        return str(override).strip()
    return (
        os.getenv('TELEGRAM_CHANNEL_CHAT_ID', '').strip()
        or os.getenv('TELEGRAM_MAIN_CHANNEL_ID', '').strip()
        or os.getenv('TELEGRAM_CHANNEL_ID', '').strip()
    )


def _truncate(text: str, limit: int) -> str:
    text = (text or '').strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + '…'


def _absolute_url(url: str) -> str:
    if not url:
        return ''
    if url.startswith('http://') or url.startswith('https://'):
        return url
    if url.startswith('/media/') or url.startswith('/static/'):
        return f"{DEFAULT_APP_URL.rstrip('/')}{url}"
    return url


def _button_markup(button_text: str, button_url: str) -> dict:
    return {
        'inline_keyboard': [[{
            'text': button_text,
            'url': button_url,
        }]]
    }


def _escape(text: str) -> str:
    return html.escape(text or '')


def _build_product_caption(product, custom_caption: str = '') -> str:
    name = _escape(getattr(product, 'name', '') or '')
    name_am = _escape(getattr(product, 'name_am', '') or '')
    description = _escape(getattr(product, 'short_description_am', None) or getattr(product, 'short_description', '') or getattr(product, 'description_am', '') or getattr(product, 'description', '') or '')
    current_price = float(getattr(product, 'current_price', lambda: product.price)())
    base_price = float(getattr(product, 'price', 0) or 0)
    compare_price = float(getattr(product, 'compare_price', 0) or 0) if getattr(product, 'compare_price', None) else None
    discount_label = _escape(getattr(product, 'discount_label', lambda: '')())
    age_label = _escape(getattr(product, 'age_label', lambda: '')())

    parts = [
        f"<b>{name}</b>",
    ]
    if name_am and name_am != name:
        parts.append(name_am)
    if age_label:
        parts.append(f"Age: {age_label}")
    if compare_price and compare_price > current_price:
        parts.append(f"Price: ETB {current_price:,.0f} <s>ETB {compare_price:,.0f}</s>")
    elif current_price < base_price:
        parts.append(f"Price: ETB {current_price:,.0f} <s>ETB {base_price:,.0f}</s>")
    else:
        parts.append(f"Price: ETB {current_price:,.0f}")
    if discount_label:
        parts.append(discount_label)
    if description:
        parts.append(description)
    if custom_caption.strip():
        parts.append(_escape(custom_caption.strip()))
    return '\n'.join(part for part in parts if part)


def _build_announcement_caption(title: str, caption: str) -> str:
    title = _escape(title)
    caption = _escape(caption)
    parts = []
    if title:
        parts.append(f"<b>{title}</b>")
    if caption:
        parts.append(caption)
    return '\n\n'.join(parts).strip()


def _resolve_product_button_url(product) -> str:
    slug = getattr(product, 'slug', '') or ''
    if slug:
        return f"{DEFAULT_MINI_APP_URL}?tab=liyu&query={slug}"
    return DEFAULT_MINI_APP_URL


def _resolve_announcement_button_url(button_url: str) -> str:
    return button_url.strip() or DEFAULT_MINI_APP_URL


def _send_photo(client: httpx.AsyncClient, chat_id, photo_url: str, caption: str, reply_markup: dict) -> dict:
    return client.post(
        f"https://api.telegram.org/bot{_token()}/sendPhoto",
        json={
            'chat_id': chat_id,
            'photo': _absolute_url(photo_url),
            'caption': _truncate(caption, 1024),
            'parse_mode': 'HTML',
            'reply_markup': reply_markup,
        },
        timeout=20,
    )


async def publish_channel_post(post, *, images: Optional[Iterable[str]] = None, product=None, button_text: str = 'Open Mini App', button_url: str = '') -> dict:
    """Publish an admin-composed post to the configured Telegram channel."""
    token = _token()
    channel_id = _channel_id(getattr(post, 'channel_chat_id', None))
    if not token:
        return {'ok': False, 'error': 'TELEGRAM_BOT_TOKEN is not configured.'}
    if not channel_id:
        return {'ok': False, 'error': 'Telegram channel chat ID is not configured.'}

    reply_markup = _button_markup(button_text, button_url or DEFAULT_MINI_APP_URL)
    caption = ''
    media: List[dict] = []

    if getattr(post, 'post_type', 'announcement') == 'product' and product is not None:
        caption = _build_product_caption(product, getattr(post, 'caption', '') or '')
        if images is None:
            images = [getattr(product, 'primary_image', lambda: '')()]
        if not button_url:
            button_url = _resolve_product_button_url(product)
        reply_markup = _button_markup(button_text, button_url)
    else:
        caption = _build_announcement_caption(getattr(post, 'title', '') or '', getattr(post, 'caption', '') or '')
        if not button_url:
            button_url = _resolve_announcement_button_url(getattr(post, 'button_url', '') or '')
        reply_markup = _button_markup(button_text or getattr(post, 'button_text', '') or 'Open Mini App', button_url)

    image_urls = [img for img in (images or []) if img]
    image_urls = [_absolute_url(url) for url in image_urls]

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
                data = resp.json()
                return data

            if len(image_urls) == 1:
                resp = await _send_photo(client, channel_id, image_urls[0], caption, reply_markup)
                return resp.json()

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


def build_product_post_payload(product, *, caption: str = '', title: str = '', button_text: str = 'Open Mini App') -> dict:
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
        'button_url': _resolve_product_button_url(product),
        'product_id': product.id,
        'images': images,
    }


def build_announcement_payload(title: str, caption: str, *, button_text: str = 'Open Mini App', button_url: str = '') -> dict:
    return {
        'post_type': 'announcement',
        'title': title,
        'caption': caption,
        'button_text': button_text,
        'button_url': _resolve_announcement_button_url(button_url),
    }
