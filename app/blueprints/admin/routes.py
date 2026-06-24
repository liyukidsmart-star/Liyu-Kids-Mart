import os
from flask import (render_template, redirect, url_for, flash, request,
                   jsonify, current_app)
from flask_login import login_required, current_user
from functools import wraps
from app.blueprints.admin import admin_bp
from app.extensions import db
from app.models.product import Product, Category, ProductImage
from app.models.order import Order, OrderStatus, Coupon, DiscountType
from app.models.user import User, UserRole
from app.models.delivery import Driver
from app.models.ai_conversation import AIConversation
from app.utils import allowed_file
from slugify import slugify
from slugify import slugify

def _upload_to_telegram(file_obj):
    """Upload an image to Telegram via sendPhoto to a dedicated media channel.

    Returns a /media/<file_id> URL.  The proxy endpoint in main/routes.py
    resolves this at request time into a 302 redirect → Telegram CDN.
    Image bytes never pass through our server — zero egress cost.

    Falls back to Supabase if TELEGRAM_BOT_TOKEN or TELEGRAM_MEDIA_CHAT_ID
    is not configured.
    """
    import httpx
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.environ.get('TELEGRAM_MEDIA_CHAT_ID', '').strip()

    if not token or not chat_id:
        current_app.logger.warning(
            'TELEGRAM_BOT_TOKEN or TELEGRAM_MEDIA_CHAT_ID not set — '
            'falling back to Supabase'
        )
        return _upload_file_to_supabase(file_obj)

    try:
        file_content = file_obj.read()
        file_obj.seek(0)
        content_type = getattr(file_obj, 'content_type', 'image/jpeg') or 'image/jpeg'
        orig_name = getattr(file_obj, 'filename', 'photo.jpg') or 'photo.jpg'
        ext = orig_name.rsplit('.', 1)[-1].lower() if '.' in orig_name else 'jpg'
        safe_name = f'product.{ext}'

        resp = httpx.post(
            f'https://api.telegram.org/bot{token}/sendPhoto',
            data={'chat_id': chat_id, 'disable_notification': 'true'},
            files={'photo': (safe_name, file_content, content_type)},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get('ok'):
            raise ValueError(f"Telegram API error: {data.get('description')}")

        # Pick the largest photo variant (best quality)
        photos = data['result']['photo']
        best = max(photos, key=lambda p: p.get('file_size', 0))
        file_id = best['file_id']

        # Return our proxy URL — clean, no token exposed
        app_url = os.environ.get('APP_URL', '').rstrip('/')
        return f'{app_url}/media/{file_id}'

    except Exception as e:
        current_app.logger.warning(f'Telegram upload failed: {e} — falling back to Supabase')
        file_obj.seek(0)
        return _upload_file_to_supabase(file_obj)


def _upload_file_to_supabase(file_obj, filename=None):
    """Fallback: upload to Supabase Storage and return public URL."""
    supabase_url = os.environ.get('SUPABASE_URL')
    supabase_key = (
        os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or
        os.environ.get('SUPABASE_KEY') or
        os.environ.get('SUPABASE_ANON_KEY')
    )
    if not supabase_url or not supabase_key:
        current_app.logger.warning('Supabase credentials not configured — skipping fallback upload')
        return None
    if filename is None:
        orig = getattr(file_obj, 'filename', 'upload.jpg') or 'upload.jpg'
        filename = orig
    try:
        from supabase import create_client, Client
        supabase: Client = create_client(supabase_url, supabase_key)
        bucket_name = 'uploads'
        file_content = file_obj.read()
        file_obj.seek(0)
        supabase.storage.from_(bucket_name).upload(
            file=file_content,
            path=filename,
            file_options={'content-type': file_obj.content_type, 'upsert': 'true'}
        )
        return supabase.storage.from_(bucket_name).get_public_url(filename)
    except Exception as e:
        current_app.logger.error(f'Supabase upload failed: {e}')
        return None

def _try_broadcast(product):
    """Fire-and-forget broadcast of a new product to all Telegram bot users."""
    try:
        from telegram_bot.broadcaster import broadcast_new_product
        broadcast_new_product({
            'name': product.name,
            'name_am': product.name_am,
            'slug': product.slug,
            'short_description': product.short_description or '',
            'short_description_am': product.short_description_am or '',
            'description': product.description or '',
            'description_am': product.description_am or '',
            'price': float(product.price),
            'compare_price': float(product.compare_price) if product.compare_price else None,
            'age_label': product.age_label(),
            'primary_image': product.primary_image(),
        })
    except Exception as e:
        current_app.logger.warning(f'Broadcast failed: {e}')


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.role.value not in ('admin', 'manager'):
            flash('Access denied.', 'danger')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated


# ── DASHBOARD ──
@admin_bp.route('/')
@admin_required
def dashboard():
    stats = {
        'total_revenue': db.session.query(db.func.sum(Order.total)).filter(
            Order.status == OrderStatus.delivered).scalar() or 0,
        'total_orders': Order.query.count(),
        'today_orders': Order.query.filter(
            db.func.date(Order.created_at) == db.func.current_date()).count(),
        'pending_orders': Order.query.filter_by(status=OrderStatus.pending).count(),
        'total_products': Product.query.filter_by(is_active=True).count(),
        'total_customers': User.query.filter_by(role=UserRole.customer).count(),
    }
    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(10).all()
    low_stock = Product.query.filter(
        Product.is_active == True,  # noqa
        Product.stock_qty <= 5
    ).order_by(Product.stock_qty.asc()).limit(8).all()
    return render_template('admin/dashboard.html', stats=stats,
                           recent_orders=recent_orders, low_stock=low_stock)


# ── PRODUCTS ──
@admin_bp.route('/products')
@admin_required
def products():
    q = request.args.get('q', '').strip()
    query = Product.query
    if q:
        query = query.filter(Product.name.ilike(f'%{q}%'))
    pagination = query.order_by(Product.created_at.desc()).paginate(
        page=request.args.get('page', 1, int), per_page=20, error_out=False)
    return render_template('admin/products.html', products=pagination.items,
                           pagination=pagination, q=q)


@admin_bp.route('/products/create', methods=['GET', 'POST'])
@admin_required
def create_product():
    categories = Category.query.filter_by(is_active=True).all()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Product name is required.', 'danger')
            return render_template('admin/product_form.html', product=None, categories=categories)
        slug = slugify(name)
        # Make slug unique
        base_slug, n = slug, 1
        while Product.query.filter_by(slug=slug).first():
            slug = f'{base_slug}-{n}'; n += 1

        def _safe_float(val, default=0.0):
            return float(val) if str(val).strip() else default
        def _safe_int(val, default=0):
            return int(val) if str(val).strip() else default

        product = Product(
            name=name, slug=slug,
            name_am=request.form.get('name_am', '').strip(),
            price=_safe_float(request.form.get('price')),
            compare_price=_safe_float(request.form.get('compare_price'), None) if request.form.get('compare_price', '').strip() else None,
            stock_qty=_safe_int(request.form.get('stock_qty')),
            category_id=_safe_int(request.form.get('category_id'), 0) or None,
            age_min_months=_safe_int(request.form.get('age_min_months'), 0),
            age_max_months=_safe_int(request.form.get('age_max_months'), 144),
            short_description=request.form.get('short_description', ''),
            short_description_am=request.form.get('short_description_am', ''),
            description=request.form.get('description', ''),
            description_am=request.form.get('description_am', ''),
            is_active='is_active' in request.form,
            is_featured='is_featured' in request.form,
            is_new_arrival='is_new_arrival' in request.form,
        )
        db.session.add(product)
        db.session.flush()

        # Handle image uploads — primary destination is Telegra.ph (Telegram's free CDN)
        images = request.files.getlist('images')
        upload_folder = current_app.config.get('UPLOAD_FOLDER', 'static/uploads')
        try:
            for i, img_file in enumerate(images):
                if img_file and img_file.filename and allowed_file(img_file.filename):
                    # 1st choice: Telegram media channel (free, unlimited, zero egress cost)
                    img_url = _upload_to_telegram(img_file)

                    if not img_url:
                        # Last-resort fallback: local disk (dev only)
                        try:
                            os.makedirs(upload_folder, exist_ok=True)
                            ext = img_file.filename.rsplit('.', 1)[1].lower()
                            fname = f'product_{product.id}_{i}.{ext}'
                            img_file.seek(0)
                            img_file.save(os.path.join(upload_folder, fname))
                            img_url = f'/static/uploads/{fname}'
                        except OSError:
                            current_app.logger.error('All upload methods failed for image %s', i)
                            continue

                    img = ProductImage(
                        product_id=product.id,
                        image_url=img_url,
                        is_primary=(i == 0),
                        sort_order=i,
                    )
                    db.session.add(img)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f'Image upload block failed: {e}')
            flash('Image upload failed. Product was saved without images.', 'warning')
            db.session.add(product)
            db.session.commit()

        # Broadcast to Telegram if product is active (published)
        if product.is_active:
            _try_broadcast(product)
            flash(f'✅ Product "{name}" created and announced on Telegram!', 'success')
        else:
            flash(f'✅ Product "{name}" created (draft — not announced yet).', 'success')
        return redirect(url_for('admin.products'))
    return render_template('admin/product_form.html', product=None, categories=categories)


@admin_bp.route('/products/<int:product_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_product(product_id):
    product = db.session.get(Product, product_id)
    if not product:
        flash('Product not found.', 'danger')
        return redirect(url_for('admin.products'))
    categories = Category.query.filter_by(is_active=True).all()
    if request.method == 'POST':
        def _safe_float(val, default=0.0):
            return float(val) if str(val).strip() else default
        def _safe_int(val, default=0):
            return int(val) if str(val).strip() else default

        product.name = request.form.get('name', product.name).strip()
        product.name_am = request.form.get('name_am', product.name_am or '').strip()
        product.price = _safe_float(request.form.get('price', product.price))
        product.compare_price = _safe_float(request.form.get('compare_price', product.compare_price), None) if request.form.get('compare_price', '').strip() else None
        product.stock_qty = _safe_int(request.form.get('stock_qty', product.stock_qty))
        product.category_id = _safe_int(request.form.get('category_id'), 0) or None
        product.age_min_months = _safe_int(request.form.get('age_min_months'), 0)
        product.age_max_months = _safe_int(request.form.get('age_max_months'), 144)
        product.short_description = request.form.get('short_description', '')
        product.short_description_am = request.form.get('short_description_am', '')
        product.description = request.form.get('description', '')
        product.description_am = request.form.get('description_am', '')
        product.is_active = 'is_active' in request.form
        product.is_featured = 'is_featured' in request.form
        product.is_new_arrival = 'is_new_arrival' in request.form

        images = request.files.getlist('images')
        upload_folder = current_app.config.get('UPLOAD_FOLDER', 'static/uploads')

        try:
            existing_count = product.images.count()
            for i, img_file in enumerate(images):
                if img_file and img_file.filename and allowed_file(img_file.filename):
                    # 1st choice: Telegram media channel (free, unlimited, zero egress cost)
                    img_url = _upload_to_telegram(img_file)

                    if not img_url:
                        # Last-resort fallback: local disk (dev only)
                        try:
                            os.makedirs(upload_folder, exist_ok=True)
                            ext = img_file.filename.rsplit('.', 1)[1].lower()
                            fname = f'product_{product.id}_{existing_count + i}.{ext}'
                            img_file.seek(0)
                            img_file.save(os.path.join(upload_folder, fname))
                            img_url = f'/static/uploads/{fname}'
                        except OSError:
                            current_app.logger.error('All upload methods failed for image %s', i)
                            continue

                    img = ProductImage(
                        product_id=product.id,
                        image_url=img_url,
                        is_primary=(existing_count == 0 and i == 0),
                        sort_order=existing_count + i,
                    )
                    db.session.add(img)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f'Image upload block failed: {e}')
            flash('Image upload failed. Product edits may have been lost — please try again.', 'warning')

        # If admin clicked the "Broadcast" button, announce this product
        if request.form.get('broadcast_telegram'):
            _try_broadcast(product)
            flash(f'✅ "{product.name}" updated and announced on Telegram! 📢', 'success')
        else:
            flash(f'✅ Product "{product.name}" updated!', 'success')
        return redirect(url_for('admin.products'))
    return render_template('admin/product_form.html', product=product, categories=categories)


@admin_bp.route('/products/<int:product_id>/delete', methods=['POST'])
@admin_required
def delete_product(product_id):
    product = db.session.get(Product, product_id)
    if product:
        product.is_active = False
        db.session.commit()
        flash(f'Product "{product.name}" deactivated.', 'success')
    return redirect(url_for('admin.products'))

@admin_bp.route('/products/<int:product_id>/hard-delete', methods=['POST'])
@admin_required
def hard_delete_product(product_id):
    product = db.session.get(Product, product_id)
    if product:
        product.cart_items.delete()
        product.wishlist_items.delete()
        db.session.delete(product)
        db.session.commit()
        flash(f'Product "{product.name}" permanently deleted.', 'success')
    return redirect(url_for('admin.products'))


# ── CATEGORIES ──
@admin_bp.route('/categories', methods=['GET', 'POST'])
@admin_required
def categories():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if name:
            slug = slugify(name)
            base, n = slug, 1
            while Category.query.filter_by(slug=slug).first():
                slug = f'{base}-{n}'; n += 1
            cat = Category(name=name, slug=slug,
                           icon=request.form.get('icon', '📦'),
                           description=request.form.get('description', ''),
                           is_active=True)
            db.session.add(cat)
            db.session.commit()
            flash(f'Category "{name}" created!', 'success')
        return redirect(url_for('admin.categories'))
    cats = Category.query.order_by(Category.sort_order, Category.name).all()
    return render_template('admin/categories.html', categories=cats)


# ── ORDERS ──
@admin_bp.route('/orders')
@admin_required
def orders():
    status_filter = request.args.get('status', '')
    q = Order.query
    if status_filter:
        try:
            q = q.filter_by(status=OrderStatus[status_filter])
        except KeyError:
            pass
    pagination = q.order_by(Order.created_at.desc()).paginate(
        page=request.args.get('page', 1, int), per_page=25, error_out=False)
    return render_template('admin/orders.html', orders=pagination.items,
                           pagination=pagination, status_filter=status_filter)


@admin_bp.route('/orders/<int:order_id>/status', methods=['POST'])
@admin_required
def update_order_status(order_id):
    order = db.session.get(Order, order_id)
    if not order:
        return jsonify({'success': False, 'message': 'Order not found'}), 404
    status = request.form.get('status') or (request.get_json() or {}).get('status')
    try:
        order.status = OrderStatus[status]
        db.session.commit()
        return jsonify({'success': True, 'message': f'Status updated to {status}'})
    except KeyError:
        return jsonify({'success': False, 'message': 'Invalid status'})


# ── CUSTOMERS ──
@admin_bp.route('/customers')
@admin_required
def customers():
    q = request.args.get('q', '').strip()
    query = User.query
    if q:
        from sqlalchemy import or_
        query = query.filter(or_(
            User.full_name.ilike(f'%{q}%'),
            User.email.ilike(f'%{q}%'),
            User.phone.ilike(f'%{q}%'),
        ))
    pagination = query.order_by(User.created_at.desc()).paginate(
        page=request.args.get('page', 1, int), per_page=30, error_out=False)
    return render_template('admin/customers.html', customers=pagination.items,
                           pagination=pagination, q=q)


# ── DRIVERS ──
@admin_bp.route('/drivers')
@admin_required
def drivers():
    all_drivers = Driver.query.all()
    available = [d for d in all_drivers if d.is_available]
    return render_template('admin/drivers.html', drivers=all_drivers,
                           available_count=len(available))


# ── ANALYTICS ──
@admin_bp.route('/analytics')
@admin_required
def analytics():
    top_products = Product.query.filter_by(is_active=True).order_by(
        Product.sales_count.desc()).limit(10).all()
    telegram_users = User.query.filter(User.telegram_id.isnot(None)).count()
    ai_count = AIConversation.query.count()
    return render_template('admin/analytics.html', top_products=top_products,
                           telegram_users=telegram_users, ai_count=ai_count)


# ── COUPONS ──
@admin_bp.route('/coupons', methods=['GET', 'POST'])
@admin_required
def coupons():
    if request.method == 'POST':
        code = request.form.get('code', '').strip().upper()
        if code and not Coupon.query.filter_by(code=code).first():
            dt_val = request.form.get('discount_type', 'percentage')
            coupon = Coupon(
                code=code,
                description=request.form.get('description', ''),
                discount_type=DiscountType[dt_val],
                discount_value=float(request.form.get('discount_value', 10)),
                min_order_amount=float(request.form.get('min_order_amount', 0)),
                max_uses=int(request.form.get('max_uses', 0)) or None,
                is_active=True,
            )
            db.session.add(coupon)
            db.session.commit()
            flash(f'Coupon "{code}" created!', 'success')
        else:
            flash('Coupon code already exists or is invalid.', 'danger')
        return redirect(url_for('admin.coupons'))
    all_coupons = Coupon.query.order_by(Coupon.created_at.desc()).all()
    return render_template('admin/coupons.html', coupons=all_coupons)


# ── AI CONVERSATIONS ──
@admin_bp.route('/ai-conversations')
@admin_required
def ai_conversations():
    pagination = AIConversation.query.order_by(
        AIConversation.created_at.desc()).paginate(
        page=request.args.get('page', 1, int), per_page=50, error_out=False)
    return render_template('admin/ai_conversations.html',
                           conversations=pagination.items, pagination=pagination)
