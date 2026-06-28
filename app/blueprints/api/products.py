from math import ceil
from datetime import datetime, timezone
from flask import request
from app.blueprints.api import api_bp
from app.extensions import db
from app.models.product import Product, Category
from app.utils import success_response, error_response


def _filter_products(products, category_id=None, featured=None, new_arrival=None, min_price=0, max_price=99999):
    filtered = []
    for product in products:
        if not product.is_active:
            continue
        if category_id and product.category_id != category_id:
            continue
        if featured is True and not product.is_featured:
            continue
        if new_arrival is True and not product.is_new_arrival:
            continue
        price = float(product.current_price())
        if price < float(min_price) or price > float(max_price):
            continue
        filtered.append(product)
    return filtered


def _sort_products(products, sort):
    if sort == 'price_asc':
        return sorted(products, key=lambda p: (float(p.current_price()), -(p.sales_count or 0), p.id))
    if sort == 'price_desc':
        return sorted(products, key=lambda p: (float(p.current_price()), (p.sales_count or 0), -p.id), reverse=True)
    if sort == 'bestselling':
        return sorted(products, key=lambda p: ((p.sales_count or 0), (p.created_at or datetime.min.replace(tzinfo=timezone.utc))), reverse=True)
    return sorted(products, key=lambda p: p.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


@api_bp.route('/products')
def get_products():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 12, type=int)
    category_id = request.args.get('category_id', type=int)
    featured = request.args.get('featured', '')
    new_arrival = request.args.get('new_arrival', '')
    sort = request.args.get('sort', 'newest')
    min_price = request.args.get('min_price', 0, type=float)
    max_price = request.args.get('max_price', 99999, type=float)

    all_products = Product.query.filter_by(is_active=True).all()
    filtered = _filter_products(
        all_products,
        category_id=category_id,
        featured=featured in ('True', 'true', '1'),
        new_arrival=new_arrival in ('True', 'true', '1'),
        min_price=min_price,
        max_price=max_price,
    )
    sorted_products = _sort_products(filtered, sort)
    total = len(sorted_products)
    pages = max(1, ceil(total / per_page)) if per_page else 1
    start = max(0, (page - 1) * per_page)
    end = start + per_page
    page_items = sorted_products[start:end]

    return success_response({
        'products': [p.to_dict() for p in page_items],
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
    results = Product.query.filter(
        Product.is_active == True,
        db.or_(
            Product.name.ilike(f'%{q_str}%'),
            Product.short_description.ilike(f'%{q_str}%'),
            Product.description.ilike(f'%{q_str}%'),
        )
    ).order_by(Product.sales_count.desc()).limit(limit).all()
    return success_response({'products': [p.to_dict() for p in results]})


@api_bp.route('/products/featured')
def featured_products():
    products = Product.query.filter_by(is_featured=True, is_active=True).limit(8).all()
    return success_response({'products': [p.to_dict() for p in products]})


@api_bp.route('/products/trending')
def trending_products():
    products = Product.query.filter_by(is_active=True).order_by(
        Product.sales_count.desc()).limit(8).all()
    return success_response({'products': [p.to_dict() for p in products]})


@api_bp.route('/products/<int:product_id>')
def get_product(product_id):
    product = db.session.get(Product, product_id)
    if not product or not product.is_active:
        return error_response('Product not found', 404)
    return success_response(product.to_dict(include_description=True))


@api_bp.route('/products/<int:product_id>/recommendations')
def product_recommendations(product_id):
    product = db.session.get(Product, product_id)
    if not product:
        return error_response('Product not found', 404)
    similar = Product.query.filter(
        Product.category_id == product.category_id,
        Product.id != product.id,
        Product.is_active == True
    ).order_by(Product.sales_count.desc()).limit(6).all()
    return success_response({'products': [p.to_dict() for p in similar]})


@api_bp.route('/categories')
def get_categories():
    cats = Category.query.filter_by(is_active=True, parent_id=None).order_by(Category.sort_order).all()
    return success_response([c.to_dict() for c in cats])
