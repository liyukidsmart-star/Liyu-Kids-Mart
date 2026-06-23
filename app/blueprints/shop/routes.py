from flask import render_template, request, jsonify, abort
from flask_login import login_required, current_user
from sqlalchemy import or_, func
from app.blueprints.shop import shop_bp
from app.extensions import db
from app.models.product import Product, Category
from app.models.order import Wishlist
from app.utils import paginate_query


def _get_categories():
    return Category.query.filter_by(is_active=True, parent_id=None).order_by(Category.sort_order).all()


def _build_product_query(request_args):
    q = Product.query.filter_by(is_active=True)
    sort = request_args.get('sort', 'newest')
    if sort == 'price_asc':
        q = q.order_by(Product.price.asc())
    elif sort == 'price_desc':
        q = q.order_by(Product.price.desc())
    elif sort == 'bestselling':
        q = q.order_by(Product.sales_count.desc())
    else:
        q = q.order_by(Product.created_at.desc())

    if request_args.get('featured'):
        q = q.filter_by(is_featured=True)
    if request_args.get('new_arrival'):
        q = q.filter_by(is_new_arrival=True)
    age = request_args.get('age')
    if age:
        parts = age.split('-')
        if len(parts) == 2:
            age_min = int(parts[0]) * 12
            age_max = int(parts[1]) * 12
            q = q.filter(Product.age_min_months <= age_max, Product.age_max_months >= age_min)
    return q, sort


@shop_bp.route('/')
def listing():
    q, sort = _build_product_query(request.args)
    pagination = q.paginate(page=request.args.get('page', 1, int), per_page=20, error_out=False)
    return render_template('shop/listing.html',
                           products=pagination.items,
                           pagination=pagination,
                           categories=_get_categories(),
                           page_title='Shop All Products',
                           current_category=None,
                           current_sort=sort)


@shop_bp.route('/category/<slug>')
def category(slug):
    cat = Category.query.filter_by(slug=slug).first_or_404()
    q = Product.query.filter_by(is_active=True, category_id=cat.id)
    sort = request.args.get('sort', 'newest')
    q = q.order_by(Product.created_at.desc() if sort == 'newest' else
                   (Product.price.asc() if sort == 'price_asc' else
                    (Product.price.desc() if sort == 'price_desc' else Product.sales_count.desc())))
    pagination = q.paginate(page=request.args.get('page', 1, int), per_page=20, error_out=False)
    return render_template('shop/listing.html',
                           products=pagination.items,
                           pagination=pagination,
                           categories=_get_categories(),
                           page_title=f'{cat.icon or ""} {cat.name}',
                           current_category=slug,
                           current_sort=sort)


@shop_bp.route('/product/<slug>')
def detail(slug):
    product = Product.query.filter_by(slug=slug, is_active=True).first_or_404()
    product.view_count = (product.view_count or 0) + 1
    db.session.commit()

    in_wishlist = False
    if current_user.is_authenticated:
        in_wishlist = Wishlist.query.filter_by(
            user_id=current_user.id, product_id=product.id).first() is not None

    # Similar products: same category, exclude this one
    similar = (Product.query.filter_by(is_active=True, category_id=product.category_id)
               .filter(Product.id != product.id).limit(4).all())

    return render_template('shop/detail.html',
                           product=product,
                           similar=similar,
                           in_wishlist=in_wishlist)


@shop_bp.route('/search')
def search():
    query_str = request.args.get('q', '').strip()
    products = []
    total = 0
    if query_str:
        like = f'%{query_str}%'
        products = (Product.query.filter(
            Product.is_active == True,  # noqa
            or_(Product.name.ilike(like),
                Product.description.ilike(like),
                Product.short_description.ilike(like))
        ).limit(40).all())
        total = len(products)
    return render_template('shop/search.html', products=products, query=query_str, total=total)


@shop_bp.route('/wishlist')
@login_required
def wishlist():
    items = Wishlist.query.filter_by(user_id=current_user.id).order_by(Wishlist.added_at.desc()).all()
    return render_template('shop/wishlist.html', items=items)


@shop_bp.route('/wishlist/toggle', methods=['POST'])
@login_required
def wishlist_toggle():
    data = request.get_json() or {}
    product_id = data.get('product_id')
    if not product_id:
        return jsonify({'success': False, 'message': 'No product_id'})
    existing = Wishlist.query.filter_by(user_id=current_user.id, product_id=product_id).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        return jsonify({'success': True, 'action': 'removed'})
    wl = Wishlist(user_id=current_user.id, product_id=product_id)
    db.session.add(wl)
    db.session.commit()
    return jsonify({'success': True, 'action': 'added'})
