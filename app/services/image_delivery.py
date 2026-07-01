import os

from flask import current_app, has_app_context

DEFAULT_IMAGE_CDN_BASE_URL = 'https://liyu-kids-mart.liyukidsmart.workers.dev'


def _config_value(name: str, default: str = '') -> str:
    if has_app_context():
        value = current_app.config.get(name, '')
        if value:
            return str(value).strip()
    return os.getenv(name, default).strip()


def image_cdn_base_url() -> str:
    return _config_value('IMAGE_CDN_BASE_URL', '').rstrip('/')


def looks_like_telegram_file_id(value: str) -> bool:
    value = (value or '').strip()
    if not value or value.startswith(('http://', 'https://', '/')):
        return False
    if ' ' in value or len(value) < 20:
        return False
    allowed = set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-')
    return all(ch in allowed for ch in value)


def media_url_for_file_id(file_id: str, *, prefer_cdn: bool = True) -> str:
    file_id = (file_id or '').strip()
    if not file_id:
        return ''

    cdn_base = image_cdn_base_url() if prefer_cdn else ''
    if prefer_cdn and not cdn_base:
        cdn_base = DEFAULT_IMAGE_CDN_BASE_URL
    if cdn_base:
        return f'{cdn_base}/media/{file_id}'

    app_url = _config_value('APP_URL', '').rstrip('/')
    if app_url:
        return f'{app_url}/media/{file_id}'

    return f'/media/{file_id}'


def is_placeholder_url(url: str) -> bool:
    """Return True if the URL is a known placeholder / missing-image sentinel."""
    if not url:
        return True
    stripped = url.strip()
    return (
        stripped.endswith('/static/images/placeholder.png')
        or stripped == '/static/images/placeholder.png'
        or 'placeholder' in stripped.lower().split('/')[-1]
    )


def rewrite_media_url(url: str, *, prefer_cdn: bool = True) -> str:
    url = (url or '').strip()
    if not url:
        return url

    # Already a placeholder — return as-is so callers can filter it
    if is_placeholder_url(url):
        return url

    # Handle any /media/<file_id> URL (covers vercel.app, workers.dev, /media/ paths, etc.)
    if '/media/' in url:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            path_after_media = parsed.path.split('/media/', 1)[1].strip('/')
            if path_after_media:
                return media_url_for_file_id(path_after_media, prefer_cdn=prefer_cdn)
        except Exception:
            pass

    if looks_like_telegram_file_id(url):
        return media_url_for_file_id(url, prefer_cdn=prefer_cdn)

    return url
