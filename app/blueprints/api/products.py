import re
from math import ceil
from datetime import datetime, timezone

from flask import request, current_app
from sqlalchemy.orm import selectinload

from app.blueprints.api import api_bp
from app.extensions import db
from app.models.product import Category, Product, ProductImage, prime_product_image_lookup
from app.models import marketing as _marketing  # noqa: F401 - ensure price helpers are attached
from app.services.loyalty_service import _get_settings
from app.models.marketing import TelegramChannelPost, TelegramChannelPostImage
from app.data.product_images_backfill import PRODUCT_IMAGE_CATALOG
from app.services.image_delivery import rewrite_media_url
from app.utils import error_response, success_response


def _collect_category_ids(category_id):
    if not category_id:
        return set()

    category_ids = {int(category_id)}
    stack = [int(category_id)]
    while stack:
        current_id = stack.pop()
        child_ids = [row.id for row in Category.query.filter_by(parent_id=current_id, is_active=True).all()]
        for child_id in child_ids:
            if child_id not in category_ids:
                category_ids.add(child_id)
                stack.append(child_id)
    return category_ids


def _normalize_search_query(value):
    return re.sub(r'\s+', ' ', (value or '').strip().casefold())


def _tokenize_search_query(value):
    return [token for token in re.split(r'[^a-z0-9]+', _normalize_search_query(value)) if len(token) >= 2]


def _search_score(product, q_str):
    normalized_query = _normalize_search_query(q_str)
    if not normalized_query:
        return 0

    haystacks = [
        product.name or '',
        product.short_description or '',
        product.description or '',
        product.name_am or '',
        product.short_description_am or '',
        product.description_am or '',
        (product.category.name if product.category else '') or '',
    ]
    tokens = _tokenize_search_query(q_str)
    if not tokens:
        return 0

    score = 0
    for field in haystacks:
        text = (field or '').casefold()
        if not text:
            continue
        if normalized_query in text:
            score += 120
        elif all(token in text for token in tokens):
            score += 80
        else:
            matches = sum(1 for token in tokens if token in text)
            if matches:
                score += matches * 20

    if any(token in (product.name or '').casefold() for token in tokens):
        score += 40
    if any(token in (product.category.name or '').casefold() for token in tokens) if product.category else False:
        score += 30
    return score


def _matches_search_query(product, q_str):
    return _search_score(product, q_str) >= 20


def _filter_products(products, category_id=None, featured=None, new_arrival=None, min_price=0, max_price=99999, q_str=''):
    filtered = []
    category_ids = _collect_category_ids(category_id) if category_id else set()
    for product in products:
        if not product.is_active:
            continue
        if category_ids and product.category_id not in category_ids:
            continue
        if featured is True and not product.is_featured:
            continue
        if new_arrival is True and not product.is_new_arrival:
            continue
        if q_str and not _matches_search_query(product, q_str):
            continue
        price = float(product.current_price())
        if price < float(min_price) or price > float(max_price):
            continue
        filtered.append(product)
    return filtered


def _sort_products(products, sort, order=''):
    sort = (sort or '').strip().lower()
    order = (order or '').strip().lower()

    if sort in ('price', 'price_asc') or (sort == 'asc' and order != 'desc'):
        return sorted(products, key=lambda p: (float(p.current_price()), -(p.sales_count or 0), p.id))

    if sort in ('price_desc',) or (sort == 'price' and order == 'desc') or (sort == 'desc' and order != 'asc'):
        return sorted(products, key=lambda p: (float(p.current_price()), (p.sales_count or 0), -p.id), reverse=True)

    if sort in ('bestselling', 'sales_count', 'popular'):
        if order == 'asc':
            return sorted(products, key=lambda p: ((p.sales_count or 0), p.created_at or datetime.min.replace(tzinfo=timezone.utc)))
        return sorted(
            products,
            key=lambda p: ((p.sales_count or 0), (p.created_at or datetime.min.replace(tzinfo=timezone.utc))),
            reverse=True,
        )

    if sort in ('created_at', 'new', 'newest'):
        if order == 'asc':
            return sorted(products, key=lambda p: p.created_at or datetime.min.replace(tzinfo=timezone.utc))
        return sorted(products, key=lambda p: p.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    return sorted(products, key=lambda p: p.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def _default_products_query(*, category_id=None, featured=False, new_arrival=False):
    query = Product.query.filter_by(is_active=True).options(selectinload(Product.category), selectinload(Product.min_loyalty_level))
    if category_id:
        category_ids = _collect_category_ids(category_id)
        if category_ids:
            query = query.filter(Product.category_id.in_(category_ids))
    if featured:
        query = query.filter(Product.is_featured == True)  # noqa: E712
    if new_arrival:
        query = query.filter(Product.is_new_arrival == True)  # noqa: E712
    return query


@api_bp.route('/products')
def get_products():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 12, type=int)
    category_id = request.args.get('category_id', type=int)
    featured = request.args.get('featured', '')
    new_arrival = request.args.get('new_arrival', '')
    sort = request.args.get('sort', 'newest')
    order = request.args.get('order', '')
    min_price = request.args.get('min_price', 0, type=float)
    max_price = request.args.get('max_price', 99999, type=float)
    q_str = request.args.get('q', '').strip().lower()

    settings = _get_settings()
    qty_min_price = float(getattr(settings, 'qty_discount_min_price', 2500))

    if q_str:
        all_products = (
            Product.query.filter_by(is_active=True)
            .options(selectinload(Product.category), selectinload(Product.min_loyalty_level))
            .all()
        )
        filtered = _filter_products(
            all_products,
            category_id=category_id,
            featured=featured in ('True', 'true', '1'),
            new_arrival=new_arrival in ('True', 'true', '1'),
            min_price=min_price,
            max_price=max_price,
            q_str=q_str,
        )
        sorted_products = sorted(filtered, key=lambda product: (-_search_score(product, q_str), -(product.sales_count or 0), -(product.view_count or 0), product.id))
        total = len(sorted_products)
        pages = max(1, ceil(total / per_page)) if per_page else 1
        start = max(0, (page - 1) * per_page)
        end = start + per_page
        page_items = sorted_products[start:end]
        prime_product_image_lookup(page_items)
        return success_response({
            'products': [p.to_card_dict(qty_discount_min_price=qty_min_price) for p in page_items],
            'total': total,
            'pages': pages,
            'page': page,
        })

    query = _default_products_query(
        category_id=category_id,
        featured=featured in ('True', 'true', '1'),
        new_arrival=new_arrival in ('True', 'true', '1'),
    )

    if sort in ('price', 'price_asc', 'price_desc') or (sort == 'asc' and order != 'desc') or (sort == 'desc' and order != 'asc'):
        all_products = query.all()
        filtered = _filter_products(
            all_products,
            category_id=category_id,
            featured=featured in ('True', 'true', '1'),
            new_arrival=new_arrival in ('True', 'true', '1'),
            min_price=min_price,
            max_price=max_price,
            q_str='',
        )
        sorted_products = _sort_products(filtered, sort, order)
        total = len(sorted_products)
        pages = max(1, ceil(total / per_page)) if per_page else 1
        start = max(0, (page - 1) * per_page)
        end = start + per_page
        page_items = sorted_products[start:end]
    else:
        if sort in ('bestselling', 'sales_count', 'popular'):
            query = query.order_by(Product.sales_count.desc(), Product.created_at.desc(), Product.id.desc())
        else:
            query = query.order_by(Product.created_at.desc(), Product.id.desc())
        total = query.count()
        pages = max(1, ceil(total / per_page)) if per_page else 1
        start = max(0, (page - 1) * per_page)
        page_items = query.offset(start).limit(per_page).all()

    prime_product_image_lookup(page_items)

    return success_response({
        'products': [p.to_card_dict(qty_discount_min_price=qty_min_price) for p in page_items],
        'total': total,
        'pages': pages,
        'page': page,
    })


@api_bp.route('/products/search')
def search_products():
    q_str = request.args.get('q', '').strip()
    limit = request.args.get('limit', 8, type=int)
    if not q_str:
        return success_response({'products': []})

    products = (
        Product.query.filter_by(is_active=True)
        .options(selectinload(Product.category), selectinload(Product.min_loyalty_level))
        .all()
    )
    results = [product for product in products if _matches_search_query(product, q_str)]
    results.sort(key=lambda product: (-_search_score(product, q_str), -(product.sales_count or 0), -(product.view_count or 0), product.id))
    results = results[:limit]
    prime_product_image_lookup(results)
    settings = _get_settings()
    qty_min_price = float(getattr(settings, 'qty_discount_min_price', 2500))
    return success_response({'products': [p.to_card_dict(qty_discount_min_price=qty_min_price) for p in results]})


@api_bp.route('/products/featured')
def featured_products():
    products = Product.query.filter_by(is_featured=True, is_active=True).options(selectinload(Product.category), selectinload(Product.min_loyalty_level)).limit(8).all()
    prime_product_image_lookup(products)
    settings = _get_settings()
    qty_min_price = float(getattr(settings, 'qty_discount_min_price', 2500))
    return success_response({'products': [p.to_card_dict(qty_discount_min_price=qty_min_price) for p in products]})


@api_bp.route('/products/trending')
def trending_products():
    products = Product.query.filter_by(is_active=True).options(selectinload(Product.category), selectinload(Product.min_loyalty_level)).order_by(
        Product.sales_count.desc()).limit(8).all()
    prime_product_image_lookup(products)
    settings = _get_settings()
    qty_min_price = float(getattr(settings, 'qty_discount_min_price', 2500))
    return success_response({'products': [p.to_card_dict(qty_discount_min_price=qty_min_price) for p in products]})


@api_bp.route('/products/<int:product_id>')
def get_product(product_id):
    product = db.session.get(Product, product_id)
    if not product or not product.is_active:
        return error_response('Product not found', 404)
    settings = _get_settings()
    qty_min_price = float(getattr(settings, 'qty_discount_min_price', 2500))
    return success_response(product.to_dict(include_description=True, qty_discount_min_price=qty_min_price))


@api_bp.route('/products/<int:product_id>/recommendations')
def product_recommendations(product_id):
    product = db.session.get(Product, product_id)
    if not product:
        return error_response('Product not found', 404)
    similar = Product.query.filter(
        Product.category_id == product.category_id,
        Product.id != product.id,
        Product.is_active == True
    ).options(selectinload(Product.category), selectinload(Product.min_loyalty_level)).order_by(Product.sales_count.desc()).limit(6).all()
    prime_product_image_lookup(similar)
    settings = _get_settings()
    qty_min_price = float(getattr(settings, 'qty_discount_min_price', 2500))
    return success_response({'products': [p.to_card_dict(qty_discount_min_price=qty_min_price) for p in similar]})


@api_bp.route('/products/repair-images', methods=['POST'])
def repair_product_images():
    """Repair missing or placeholder product thumbnails in the live database."""
    confirm = (request.args.get('confirm') or '').strip().lower()
    if confirm not in ('1', 'true', 'yes'):
        return error_response('Confirmation required', 400)

    products = Product.query.filter_by(is_active=True).all()
    created = 0
    updated = 0
    skipped = 0
    skipped_ids = []

    for product in products:
        rows = product.images.order_by(ProductImage.sort_order.asc(), ProductImage.id.asc()).all()
        real_rows = [row for row in rows if (rewrite_media_url(row.image_url) and 'placeholder.png' not in rewrite_media_url(row.image_url))]
        if real_rows:
            continue

        candidates = []
        posts = TelegramChannelPost.query.filter_by(product_id=product.id).order_by(TelegramChannelPost.created_at.desc()).all()
        for post in posts:
            for img in post.images.order_by(TelegramChannelPostImage.sort_order.asc()).all():
                url = rewrite_media_url(img.image_url)
                if url and 'placeholder.png' not in url and url not in candidates:
                    candidates.append(url)

        for url in PRODUCT_IMAGE_CATALOG.get(product.id, []):
            if url and 'placeholder.png' not in url and url not in candidates:
                candidates.append(url)

        if not candidates:
            skipped += 1
            skipped_ids.append(product.id)
            continue

        if rows:
            for idx, row in enumerate(rows):
                new_url = candidates[min(idx, len(candidates) - 1)]
                if row.image_url != new_url:
                    row.image_url = new_url
                    updated += 1
        else:
            for idx, url in enumerate(candidates):
                db.session.add(ProductImage(
                    product_id=product.id,
                    image_url=url,
                    is_primary=(idx == 0),
                    sort_order=idx,
                ))
                created += 1

    db.session.commit()
    current_app.logger.warning('Product image repair finished: created=%s updated=%s skipped=%s', created, updated, skipped)
    return success_response({'created': created, 'updated': updated, 'skipped': skipped, 'skipped_ids': skipped_ids})


@api_bp.route('/categories')
def get_categories():
    settings = _get_settings()
    if getattr(settings, 'show_categories_in_mini_app', True) is False:
        return success_response([])
    cats = Category.query.filter_by(is_active=True, parent_id=None).order_by(Category.sort_order).all()
    return success_response([c.to_dict() for c in cats])


@api_bp.route('/channel-posts/<int:post_id>')
def get_channel_post_products(post_id):
    """Return grouped products for a channel post (used by the mini app buy modal)."""
    post = db.session.get(TelegramChannelPost, post_id)
    if not post:
        return error_response('Channel post not found', 404)

    settings = _get_settings()
    qty_min_price = float(getattr(settings, 'qty_discount_min_price', 2500))

    # Prefer grouped_products; fall back to legacy single product
    products = post.grouped_products.all()
    if not products and post.product:
        products = [post.product]

    prime_product_image_lookup(products)

    items = []
    for p in products:
        d = p.to_card_dict(qty_discount_min_price=qty_min_price)
        
        # Build images array
        from app.models.product import ProductImage
        img_urls = [img.image_url for img in p.images.order_by(ProductImage.sort_order.asc()).all()]
        if not img_urls and p.primary_image():
            img_urls = [p.primary_image()]
        
        d['images'] = img_urls
        # Include full descriptions for the modal
        d['description'] = p.description
        d['description_am'] = p.description_am
        d['short_description'] = p.short_description
        d['short_description_am'] = p.short_description_am
        items.append(d)

    return success_response({
        'post_id': post.id,
        'title': post.title or '',
        'caption': post.caption or '',
        'post_type': post.post_type,
        'products': items,
    })

