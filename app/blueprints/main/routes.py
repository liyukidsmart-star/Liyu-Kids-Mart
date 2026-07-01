import os

import httpx
from flask import abort, make_response, redirect, render_template

from app.blueprints.main import main_bp
from app.models.order import Order
from app.models.product import Category, Product, prime_product_image_lookup
from app.services.image_delivery import image_cdn_base_url, media_url_for_file_id

TELEGRAM_FILE_PATH_CACHE = {}


@main_bp.route('/')
def index():
    try:
        featured = Product.query.filter_by(is_active=True, is_featured=True).limit(8).all()
        new_arrivals = Product.query.filter_by(is_active=True, is_new_arrival=True).limit(10).all()
        best_sellers = Product.query.filter_by(is_active=True).order_by(Product.sales_count.desc()).limit(8).all()
        categories = Category.query.filter_by(is_active=True, parent_id=None).order_by(Category.sort_order).all()
        prime_product_image_lookup(list(featured) + list(new_arrivals) + list(best_sellers))
        return render_template(
            'main/index.html',
            featured=featured,
            new_arrivals=new_arrivals,
            best_sellers=best_sellers,
            categories=categories,
            reviews=[],
        )
    except Exception as e:
        return f"<h1>Database Error</h1><p>{str(e)}</p><p>If you haven't initialized your database tables yet, please go to <a href='/init-db'>/init-db</a></p>", 500


@main_bp.route('/init-db')
def init_db():
    from app.extensions import db
    from app.models.user import User, UserRole
    try:
        db.create_all()
        admin = User.query.filter_by(email='admin@liyukidsmart.com').first()
        if not admin:
            admin = User(
                email='admin@liyukidsmart.com',
                full_name='Admin',
                role=UserRole.admin,
                is_active=True,
                is_verified=True,
            )
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()

        return "<h1>Success!</h1><p>Database tables and admin user (admin@liyukidsmart.com / admin123) created successfully! You can now <a href='/'>return to the homepage</a>.</p>", 200
    except Exception as e:
        return f"<h1>Error Creating Tables</h1><p>{str(e)}</p>", 500


@main_bp.route('/about')
def about():
    return render_template('main/about.html')


@main_bp.route('/contact')
def contact():
    return render_template('main/contact.html')


@main_bp.route('/track/<order_number>')
def track_order(order_number):
    order = Order.query.filter_by(order_number=order_number).first()
    return render_template('main/track.html', order=order, order_number=order_number)


def _telegram_file_path(file_id):
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    if not token:
        return None

    cached = TELEGRAM_FILE_PATH_CACHE.get(file_id)
    if cached:
        return cached

    try:
        resp = httpx.get(
            f'https://api.telegram.org/bot{token}/getFile',
            params={'file_id': file_id},
            timeout=10,
        )
        data = resp.json()
        if not data.get('ok'):
            return None
        file_path = data['result']['file_path']
        TELEGRAM_FILE_PATH_CACHE[file_id] = file_path
        return file_path
    except Exception:
        return None


# TELEGRAM MEDIA PROXY
# Images uploaded to Telegram are stored by file_id.
# This endpoint resolves file_id -> file_path once, caches it in-process,
# and redirects browsers directly to Telegram's CDN.
@main_bp.route('/media/<path:file_id>')
def telegram_media(file_id):
    cdn_base = image_cdn_base_url()
    if cdn_base:
        return redirect(media_url_for_file_id(file_id), code=302)

    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    if not token:
        abort(503, 'Media service not configured')

    file_path = _telegram_file_path(file_id)
    if not file_path:
        abort(404, 'File not found on Telegram')

    cdn_url = f'https://api.telegram.org/file/bot{token}/{file_path}'
    response = redirect(cdn_url, code=302)
    response.headers['Cache-Control'] = 'public, max-age=86400, stale-while-revalidate=604800'
    return response
