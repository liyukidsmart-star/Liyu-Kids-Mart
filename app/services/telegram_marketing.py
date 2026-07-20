import html
import logging
import os
import json
from functools import lru_cache
from typing import Iterable, Optional
from urllib.parse import quote_plus, urlencode

import httpx
from flask import current_app, has_app_context

from app.models.product import ProductImage

logger = logging.getLogger(__name__)

DEFAULT_MINI_APP_URL = os.getenv('MINI_APP_URL', 'http://localhost:5000/mini-app')
DEFAULT_APP_URL = os.getenv('APP_URL', 'http://localhost:5000')
ASK_LIYU_LABEL = 'ልዩን ይጠይቱ'
BUY_NOW_LABEL = 'አሁን ይግዙ'


def _config_value(name: str, default: str = '') -> str:
    if has_app_context():
        value = current_app.config.get(name, '')
        if value:
            return str(value).strip()
    return os.getenv(name, default).strip()


def _token() -> str:
    return _config_value('TELEGRAM_BOT_TOKEN')



@lru_cache(maxsize=1)
def _bot_username() -> str:
    # Resolve the live bot username from the token first so channel deep links
    # stay correct even if the environment variable is stale or missing.
    token = _token()
    if token:
        try:
            resp = httpx.get(f'https://api.telegram.org/bot{token}/getMe', timeout=10)
            data = resp.json()
            username = ((data.get('result') or {}).get('username') or '').strip()
            if username:
                return username.lstrip('@')
        except Exception:
            logger.warning('Could not resolve Telegram bot username from getMe; falling back to TELEGRAM_BOT_USERNAME')

    username = _config_value('TELEGRAM_BOT_USERNAME', 'Liyu_Kids_Mart_Bot') or 'Liyu_Kids_Mart_Bot'
    return username.lstrip('@')


@lru_cache(maxsize=1)
def _bot_has_main_web_app() -> bool:
    token = _token()
    if not token:
        return False
    try:
        resp = httpx.get(f'https://api.telegram.org/bot{token}/getMe', timeout=10)
        data = resp.json()
        return bool((data.get('result') or {}).get('has_main_web_app'))
    except Exception:
        logger.warning('Could not read has_main_web_app from getMe', exc_info=True)
        return False


def _mini_app_short_name() -> str:
    return _config_value('TELEGRAM_MINI_APP_SHORT_NAME').strip().lstrip('/')


def _can_use_tme_mini_app_links() -> bool:
    return bool(_mini_app_short_name()) or _bot_has_main_web_app()


def _clear_bot_profile_cache() -> None:
    _bot_username.cache_clear()
    _bot_has_main_web_app.cache_clear()


def ensure_bot_main_mini_app() -> bool:
    """Try to register MINI_APP_URL as the bot menu web app (needed for t.me?startapp= links)."""
    token = _token()
    mini_url = _absolute_url(_mini_app_url())
    if not token or not mini_url.startswith('https://'):
        return False
    try:
        resp = httpx.post(
            f'https://api.telegram.org/bot{token}/setChatMenuButton',
            json={
                'menu_button': {
                    'type': 'web_app',
                    'text': 'Open Mini App',
                    'web_app': {'url': mini_url},
                }
            },
            timeout=15,
        )
        data = resp.json()
        if data.get('ok'):
            _clear_bot_profile_cache()
            return _bot_has_main_web_app()
        logger.warning('setChatMenuButton failed: %s', data.get('description'))
    except Exception:
        logger.warning('Could not configure Telegram menu button web app', exc_info=True)
    return False

def _mini_app_url() -> str:
    return _config_value('MINI_APP_URL', DEFAULT_MINI_APP_URL) or DEFAULT_MINI_APP_URL


def _mini_app_web_url(*, tab: str = '', query: str = '', startapp: str = '') -> str:
    base = _absolute_url(_mini_app_url())
    params = {}
    if tab:
        params['tab'] = tab.strip()
    if query:
        params['query'] = query.strip()
    if startapp:
        params['startapp'] = startapp.strip()
    if not params:
        return base
    sep = '&' if '?' in base else '?'
    return f"{base}{sep}{urlencode(params)}"


def _encode_startapp(*, tab: str = '', query: str = '', startapp: str = '') -> str:
    if startapp:
        return startapp.strip()[:512]
    tab = (tab or '').strip()
    query = (query or '').strip()
    if tab and query:
        return f'{tab}__{quote_plus(query)}'[:512]
    return (tab or 'home')[:512]


def _telegram_mini_app_link(*, tab: str = '', query: str = '', startapp: str = '') -> str:
    """Build a channel-safe mini app link.

    t.me startapp links only work when BotFather has a Main Mini App or /newapp short name.
    Otherwise fall back to the HTTPS mini app URL (works as a normal url button in channels).
    """
    username = _bot_username()
    app_short = _mini_app_short_name()
    payload = _encode_startapp(tab=tab, query=query, startapp=startapp)

    if app_short:
        base = f'https://t.me/{username}/{app_short}'
        return f'{base}?startapp={quote_plus(payload)}' if payload else base

    if _bot_has_main_web_app():
        base = f'https://t.me/{username}'
        return f'{base}?startapp={quote_plus(payload)}' if payload else f'{base}?startapp=home'

    return _mini_app_web_url(tab=tab, query=query, startapp=startapp or payload)


def channel_button_link_mode() -> str:
    return 'tme' if _can_use_tme_mini_app_links() else 'https'


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
    if '/media/' in url:
        return url.split('/media/', 1)[-1]
    if url.startswith('/static/'):
        return f"{DEFAULT_APP_URL.rstrip('/')}{url}"
    return url


def _channel_button(text: str, *, tab: str = '', query: str = '', startapp: str = '', url: str = '') -> dict:
    """Inline keyboard button safe for Telegram channels (url link, not web_app)."""
    if url.startswith('https://t.me/') and _can_use_tme_mini_app_links():
        link = url
    elif url.startswith('https://') and not url.startswith('https://t.me/'):
        link = url
    else:
        link = _telegram_mini_app_link(tab=tab, query=query, startapp=startapp)
    return {'text': text, 'url': link}


def _button_markup(button_text: str, button_url: str = '', *, tab: str = 'home') -> dict:
    return {
        'inline_keyboard': [[_channel_button(
            button_text,
            tab=tab,
            url=button_url,
        )]]
    }


def _product_reply_markup(product, post_id: int = None) -> dict:
    product_id = getattr(product, 'id', None)
    product_name = getattr(product, 'name_am', None) or getattr(product, 'name', '') or ''
    query = product_name or (f'product:{product_id}' if product_id else '')
    
    if post_id:
        shop_tab_startapp = f'post__{post_id}'
    else:
        shop_tab_startapp = f'product__{product_id}' if product_id else ''
        
    return {
        'inline_keyboard': [[
            _channel_button(f'💬 {ASK_LIYU_LABEL}', tab='ai', query=query),
            _channel_button(f'🛒 {BUY_NOW_LABEL}', tab='shop', startapp=shop_tab_startapp),
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
        '✨ <b>አዲስ እታ ገብቷል!</b> ✨',
        '',
        f'🧸 <b>{name}</b>',
    ]
    if age_label:
        parts.append(f'👶 <b>ለዕድሜ:</b> {age_label}')
    if compare_price and float(compare_price) > current_price:
        discount_pct = round((1 - current_price / float(compare_price)) * 100)
        parts.append(
            f'💰 <b>ዋጋ:</b> {current_price:,.0f} ብር '
            f'<s>{float(compare_price):,.0f} ብር</s> · 🎉 {discount_pct}% ቅናሽ!'
        )
    else:
        parts.append(f'💰 <b>ዋጋ:</b> {current_price:,.0f} ብር')
    if description:
        parts.extend(['', description])
    if custom_caption:
        parts.extend(['', custom_caption])

    parts.extend([
        '',
        '━━━━━━━━━━━━━━━━━━━━━━',
        '📍 <b>አድራሻ:</b> Bole Bulbula, 93 Mazoriya, Addis Ababa',
        '📞 <b>ስልክ:</b> 0947967117',
        '',
        '👇 ከታች ያሉትን ቁልፎች ይጫኑ · ልዩን ይጠይቱ ወይም አሁን ይግዙ!',
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


def _send_photo(client: httpx.AsyncClient, chat_id, photo_url: str, caption: str, reply_markup: dict):
    if photo_url.startswith('/static/'):
        local_path = os.path.join(current_app.root_path, photo_url.lstrip('/'))
        if os.path.exists(local_path):
            with open(local_path, 'rb') as f:
                file_bytes = f.read()
            filename = os.path.basename(local_path)
            data_payload = {
                'chat_id': chat_id,
                'caption': _truncate(caption, 1024),
                'parse_mode': 'HTML',
            }
            if reply_markup:
                data_payload['reply_markup'] = json.dumps(reply_markup)
            return client.post(
                f"https://api.telegram.org/bot{_token()}/sendPhoto",
                data=data_payload,
                files={'photo': (filename, file_bytes)},
                timeout=30,
            )

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


def _send_video(client: httpx.AsyncClient, chat_id, video_url: str, caption: str, reply_markup: dict):
    if video_url.startswith('/static/'):
        local_path = os.path.join(current_app.root_path, video_url.lstrip('/'))
        if os.path.exists(local_path):
            with open(local_path, 'rb') as f:
                file_bytes = f.read()
            filename = os.path.basename(local_path)
            data_payload = {
                'chat_id': chat_id,
                'caption': _truncate(caption, 1024),
                'parse_mode': 'HTML',
            }
            if reply_markup:
                data_payload['reply_markup'] = json.dumps(reply_markup)
            return client.post(
                f"https://api.telegram.org/bot{_token()}/sendVideo",
                data=data_payload,
                files={'video': (filename, file_bytes)},
                timeout=60,
            )

    return client.post(
        f"https://api.telegram.org/bot{_token()}/sendVideo",
        json={
            'chat_id': chat_id,
            'video': _telegram_image_input(video_url),
            'caption': _truncate(caption, 1024),
            'parse_mode': 'HTML',
            'reply_markup': reply_markup,
        },
        timeout=60,
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

    ensure_bot_main_mini_app()

    caption = ''
    if getattr(post, 'post_type', 'announcement') == 'product' and product is not None:
        caption = _build_product_caption(product, getattr(post, 'caption', '') or '')
        if images is None:
            images = [getattr(product, 'primary_image', lambda: '')()]
        
        is_grouped = False
        try:
            if getattr(post, 'grouped_products', None):
                is_grouped = post.grouped_products.count() > 1
        except Exception:
            pass

        reply_markup = _product_reply_markup(product, post_id=post.id if is_grouped else None)
    else:
        caption = _build_announcement_caption(getattr(post, 'title', '') or '', getattr(post, 'caption', '') or '')
        reply_markup = _button_markup(
            button_text or getattr(post, 'button_text', '') or '🌐 Open Mini App',
            button_url or _telegram_mini_app_link(tab='home'),
            tab='home',
        )

    link_mode = channel_button_link_mode()
    sample_url = (reply_markup.get('inline_keyboard') or [[{}]])[0][0].get('url', '')
    logger.info('Publishing channel post with %s mini app links: %s', link_mode, sample_url)

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
                is_video = any(image_urls[0].lower().endswith(ext) for ext in ['.mp4', '.mov', '.avi'])
                if is_video:
                    resp = await _send_video(client, channel_id, image_urls[0], caption, reply_markup)
                else:
                    resp = await _send_photo(client, channel_id, image_urls[0], caption, reply_markup)
                data = resp.json()
                if data.get('ok') or not _looks_like_media_fetch_error(data):
                    return data
                return await _send_text_fallback(client, token, channel_id, caption, reply_markup)

            media = []
            files = {}
            for idx, url in enumerate(image_urls[:10]):
                is_video = any(url.lower().endswith(ext) for ext in ['.mp4', '.mov', '.avi'])
                item = {
                    'type': 'video' if is_video else 'photo',
                }
                
                if url.startswith('/static/'):
                    local_path = os.path.join(current_app.root_path, url.lstrip('/'))
                    if os.path.exists(local_path):
                        with open(local_path, 'rb') as f:
                            file_bytes = f.read()
                        filename = os.path.basename(local_path)
                        attach_name = f"media{idx}"
                        item['media'] = f"attach://{attach_name}"
                        files[attach_name] = (filename, file_bytes)
                    else:
                        item['media'] = _telegram_image_input(url)
                else:
                    item['media'] = _telegram_image_input(url)

                if idx == 0:
                    item['caption'] = _truncate(caption, 1024)
                    item['parse_mode'] = 'HTML'
                media.append(item)

            if files:
                data_payload = {'chat_id': channel_id, 'media': json.dumps(media)}
                media_resp = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMediaGroup",
                    data=data_payload,
                    files=files,
                    timeout=30,
                )
            else:
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

            followup_text = '👇 ከታች ያሉትን ቁልፎች ይጫኑ · ልዩን ይጠይቱ ወይም አሁን ይግዙ!'
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
        'button_url': _telegram_mini_app_link(tab='shop'),
        'product_id': product.id,
        'images': images,
    }


def build_announcement_payload(title: str, caption: str, *, button_text: str = 'Open Mini App', button_url: str = '') -> dict:
    return {
        'post_type': 'announcement',
        'title': title,
        'caption': caption,
        'button_text': button_text,
        'button_url': button_url or _telegram_mini_app_link(tab='home'),
    }


def _get_manager_telegram_ids() -> list:
    """Return telegram_ids of all active admins and managers."""
    try:
        from app.models.user import User, UserRole
        users = User.query.filter(
            User.telegram_id.isnot(None),
            User.is_active == True,  # noqa: E712
            User.role.in_([UserRole.admin, UserRole.manager]),
        ).all()
        return [u.telegram_id for u in users if u.telegram_id]
    except Exception as exc:
        logger.warning('Could not fetch manager Telegram IDs: %s', exc)
        return []


async def send_grouped_post_approval_preview(post, product=None) -> dict:
    """Send the grouped post as a preview to all admins/managers for approval.

    The preview message contains:
    - A thumbnail of the cover image
    - The post caption
    - Three inline buttons:
        * ✅ Approve  (callback: approve_post_<id>)
        * ❌ Reject   (callback: reject_post_<id>)
        * 🛒 Test link (the live "አሁን ይግዙ" deep link so they can verify the modal)
    """
    token = _token()
    if not token:
        return {'ok': False, 'error': 'No bot token'}

    manager_ids = _get_manager_telegram_ids()
    if not manager_ids:
        logger.warning('No admin/manager Telegram IDs found – skipping approval preview.')
        return {'ok': False, 'error': 'No managers configured with a Telegram ID'}

    post_id = getattr(post, 'id', None)
    buy_url = getattr(post, 'button_url', '') or _telegram_mini_app_link(startapp=f'post__{post_id}')

    # Build grouped product summary
    try:
        grouped = post.grouped_products.all()
    except Exception:
        grouped = [product] if product else []

    product_lines = ''
    for p in grouped:
        price = float(p.current_price())
        name = getattr(p, 'name_am', None) or getattr(p, 'name', '') or ''
        product_lines += f'\n  • {html.escape(name)} — ETB {price:,.0f}'

    caption_text = (
        f'<b>📦 Grouped Post Preview — Post #{post_id}</b>\n\n'
        f'<b>Title:</b> {html.escape(getattr(post, "title", "") or "")}\n'
        f'<b>Caption:</b> {html.escape(getattr(post, "caption", "") or "")}\n\n'
        f'<b>Products ({len(grouped)}):</b>{product_lines}\n\n'
        f'👆 Test "አሁን ይግዙ" button below to verify the modal, '
        f'then approve or reject.'
    )

    reply_markup = {
        'inline_keyboard': [
            [
                {'text': '✅ Approve & Post to Channel', 'callback_data': f'approve_post_{post_id}'},
                {'text': '❌ Reject', 'callback_data': f'reject_post_{post_id}'},
            ],
            [
                _channel_button(f'🛒 Test — አሁን ይግዙ', url=buy_url),
            ],
        ]
    }

    image_urls = []
    try:
        from app.models.marketing import TelegramChannelPostImage
        image_urls = [img.image_url for img in post.images.order_by(
            TelegramChannelPostImage.sort_order.asc()).all() if img.image_url]
    except Exception:
        pass
    cover_url = _telegram_image_input(image_urls[0]) if image_urls else ''

    results = []
    async with httpx.AsyncClient() as client:
        for chat_id in manager_ids:
            try:
                if cover_url:
                    resp = await client.post(
                        f'https://api.telegram.org/bot{token}/sendPhoto',
                        json={
                            'chat_id': chat_id,
                            'photo': cover_url,
                            'caption': _truncate(caption_text, 1024),
                            'parse_mode': 'HTML',
                            'reply_markup': reply_markup,
                        },
                        timeout=20,
                    )
                else:
                    resp = await client.post(
                        f'https://api.telegram.org/bot{token}/sendMessage',
                        json={
                            'chat_id': chat_id,
                            'text': _truncate(caption_text, 4096),
                            'parse_mode': 'HTML',
                            'reply_markup': reply_markup,
                            'disable_web_page_preview': True,
                        },
                        timeout=20,
                    )
                results.append(resp.json())
            except Exception as exc:
                logger.warning('Failed to send approval preview to %s: %s', chat_id, exc)
                results.append({'ok': False, 'error': str(exc)})

    success = any(r.get('ok') for r in results)
    return {'ok': success, 'results': results}
