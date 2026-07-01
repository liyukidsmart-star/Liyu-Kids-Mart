#!/usr/bin/env python3
"""Normalize stored image URLs to the canonical Cloudflare media URL.

This script rewrites any Telegram-backed image URL stored as:
- /media/<file_id>
- https://liyu-kids-mart.vercel.app/media/<file_id>
- a raw Telegram file_id

Run with:
  python scripts/normalize_image_urls.py --apply

By default it performs a dry run.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models.marketing import TelegramChannelPostImage
from app.models.product import ProductImage
from app.services.image_delivery import rewrite_media_url


@dataclass
class RewriteResult:
    table: str
    row_id: int
    old_url: str
    new_url: str


def _normalize_rows(model, table_name: str, *, apply: bool = False):
    results: list[RewriteResult] = []
    rows = db.session.query(model).all()
    for row in rows:
        old_url = (row.image_url or '').strip()
        new_url = rewrite_media_url(old_url)
        if not new_url or new_url == old_url:
            continue
        results.append(RewriteResult(table_name, row.id, old_url, new_url))
        if apply:
            row.image_url = new_url
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Write the rewritten URLs back to the database.')
    args = parser.parse_args()

    app = create_app(os.getenv('FLASK_ENV', 'production'))
    with app.app_context():
        changes = []
        changes.extend(_normalize_rows(ProductImage, 'product_images', apply=args.apply))
        changes.extend(_normalize_rows(TelegramChannelPostImage, 'telegram_channel_post_images', apply=args.apply))

        print(f'Found {len(changes)} image URL(s) to rewrite.')
        for item in changes[:200]:
            print(f'{item.table}:{item.row_id} -> {item.new_url}')
        if len(changes) > 200:
            print(f'... and {len(changes) - 200} more')

        if args.apply and changes:
            db.session.commit()
            print('Changes committed.')
        elif args.apply:
            print('No changes needed.')
        else:
            print('Dry run only. Re-run with --apply to save changes.')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
