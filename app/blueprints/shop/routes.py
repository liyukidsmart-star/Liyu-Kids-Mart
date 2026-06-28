from flask import render_template, request, jsonify, abort
from datetime import datetime, timezone
from flask_login import login_required, current_user
from sqlalchemy import or_
from app.blueprints.shop import shop_bp
from app.extensions import db
from app.models.product import Product, Category
from app.models.order import Wishlist


def _get_categories():
    return Category.query.filter_by(is_active=True, parent_id=None).order_by(Category.sort_order).all()


def _product_matches(product, request_args):
    if not product.is_active:
        return False
    if request_args.get('featured') and not product.is_featured:
        return False
    if request_args.get('new_arrival') and not product.is_new_arrival:
        return False
    age = request_args.get('age')
    if age:
        parts = age.split('-')
        if len(parts) == 2:
            age_min = int(parts[0]) * 12
            age_max = int(parts[1]) * 12
            if not (product.age_min_months <= age_max and product.age_max_months >= age_min):
                return False
    return True


def _sort_products(products, sort):
    if sort == 'price_asc':
        return sorted(products, key=lambda p: (float(p.current_price()), p.id))
    if sort == 'price_desc':
        return sorted(products, key=lambda p: (float(p.current_price()), p.id), reverse=True)
    if sort == 'bestselling':
        return sorted(products, key=lambda p: ((p.sales_count or 0), p.id), reverse=True)
    return sorted(products, key=lambda p: p.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def _filter_and_sort_products(request_args, category_id=None):
    sort = request_args.get('sort', 'newest')
    products = Product.query.filter_by(is_active=True).all()
    if category_id:
        products = [p for p in products if p.category_id == category_id]
    products = [p for p in products if _product_matches(p, request_args)]
    products = _sort_products(products, sort)
    return products, sort



class _Pagination:
    def __init__(self, items, page, pages, total):
        self.items = items
        self.page = page
        self.pages = pages
        self.total = total
        self.has_prev = page > 1
        self.has_next = page < pages
        self.prev_num = max(1, page - 1)
        self.next_num = min(pages, page + 1)

    def iter_pages(self):
        return range(1, self.pages + 1)


def _paginate(items, page, per_page):
    total = len(items)
    start = max(0, (page - 1) * per_page)
    end = start + per_page
    pages = max(1, (total + per_page - 1) // per_page) if per_page else 1
    return items[start:end], total, pages


@shop_bp.route('/')
def listing():
    page = request.args.get('page', 1, type=int)
    products, sort = _filter_and_sort_products(request.args)
    page_items, total, pages = _paginate(products, page, 20)
    return render_template('shop/listing.html',
                           products=page_items,
                           pagination=_Pagination(page_items, page, pages, total),
                           categories=_get_categories(),
                           page_title='Shop All Products',
                           current_category=None,
                           current_sort=sort)


@shop_bp.route('/category/<slug>')
def category(slug):
    cat = Category.query.filter_by(slug=slug).first_or_404()
    page = request.args.get('page', 1, type=int)
    products, sort = _filter_and_sort_products(request.args, category_id=cat.id)
    page_items, total, pages = _paginate(products, page, 20)
    return render_template('shop/listing.html',
                           products=page_items,
                           pagination=_Pagination(page_items, page, pages, total),
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
