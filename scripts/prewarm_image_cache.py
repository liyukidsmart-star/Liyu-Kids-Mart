#!/usr/bin/env python3
"""Pre-warm Cloudflare cache for Telegram-backed product images.

Run with production environment variables set:
- DATABASE_URL points to the live Supabase/Postgres database
- IMAGE_CDN_BASE_URL points to the Cloudflare Worker URL
- TELEGRAM_BOT_TOKEN is set in the Worker secret already
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models.product import ProductImage
from app.services.image_delivery import looks_like_telegram_file_id


def _file_id_from_url(url: str) -> str:
    if not url:
        return ''
    url = url.strip()
    if '/media/' in url:
        return url.split('/media/', 1)[1].strip('/')
    if looks_like_telegram_file_id(url):
        return url
    return ''


def _warm_one(client: httpx.Client, base_url: str, file_id: str):
    target = f'{base_url.rstrip("/")}/media/{file_id}'
    try:
        resp = client.get(target, timeout=30)
        return file_id, resp.status_code, resp.text[:160]
    except Exception as exc:
        return file_id, None, str(exc)


def main():
    image_cdn = os.getenv('IMAGE_CDN_BASE_URL', '').strip().rstrip('/')
    if not image_cdn:
        raise SystemExit('IMAGE_CDN_BASE_URL is required')

    app = create_app(os.getenv('FLASK_ENV', 'production'))
    with app.app_context():
        rows = db.session.query(ProductImage.image_url).all()

    file_ids = []
    seen = set()
    for (url,) in rows:
        file_id = _file_id_from_url(url)
        if file_id and file_id not in seen:
            seen.add(file_id)
            file_ids.append(file_id)

    print(f'Found {len(file_ids)} Telegram-backed image(s) to warm.')
    if not file_ids:
        return

    headers = {'User-Agent': 'LiyuKidsMart-CacheWarm/1.0'}
    with httpx.Client(headers=headers) as client:
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(_warm_one, client, image_cdn, fid) for fid in file_ids]
            ok = 0
            for fut in as_completed(futures):
                fid, status, info = fut.result()
                if status and 200 <= status < 400:
                    ok += 1
                    print(f'WARM {fid[:12]}... {status}')
                else:
                    print(f'FAIL {fid[:12]}... {status} {info}')
    print(f'Done. Warmed {ok}/{len(file_ids)} image(s).')


if __name__ == '__main__':
    main()
