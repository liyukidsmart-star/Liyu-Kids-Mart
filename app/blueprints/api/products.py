from flask import request
from app.blueprints.api import api_bp
from app.extensions import db
from app.models.product import Product, Category
from app.utils import success_response, error_response, paginate_query


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
    telegram_id = request.args.get('telegram_id')

    q = Product.query.filter_by(is_active=True)
    if category_id:
        q = q.filter_by(category_id=category_id)
    if featured == 'True' or featured == 'true' or featured == '1':
        q = q.filter_by(is_featured=True)
    if new_arrival == 'True' or new_arrival == 'true' or new_arrival == '1':
        q = q.filter_by(is_new_arrival=True)
    q = q.filter(Product.price >= min_price, Product.price <= max_price)
    if sort == 'price_asc':
        q = q.order_by(Product.price.asc())
    elif sort == 'price_desc':
        q = q.order_by(Product.price.desc())
    elif sort == 'bestselling':
        q = q.order_by(Product.sales_count.desc())
    else:
        q = q.order_by(Product.created_at.desc())

    pagination = paginate_query(q, page, per_page)
    return success_response({
        'products': [p.to_dict() for p in pagination.items],
        'total': pagination.total,
        'pages': pagination.pages,
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
