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


def rewrite_media_url(url: str, *, prefer_cdn: bool = True) -> str:
    url = (url or '').strip()
    if not url:
        return url

    if '/media/' not in url:
        return url

    file_id = url.split('/media/', 1)[1].strip('/')
    if not file_id:
        return url
    return media_url_for_file_id(file_id, prefer_cdn=prefer_cdn)
