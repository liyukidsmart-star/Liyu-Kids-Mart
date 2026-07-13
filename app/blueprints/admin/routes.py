import os
import json
import asyncio
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from flask import (render_template, redirect, url_for, flash, request,
                   jsonify, current_app)
import httpx
from flask_login import login_required, current_user
from functools import wraps
from app.blueprints.admin import admin_bp
from app.extensions import db
from app.models.inventory import POSSale, POSSaleStatus
from app.models.product import Product, Category, ProductImage, prime_product_image_lookup
from app.models.order import Order, OrderStatus, Coupon, DiscountType, OrderItem
from app.models.loyalty import LoyaltySettings
from app.services.loyalty_service import _get_settings
from app.models.marketing import ProductDiscount, TelegramChannelPost, TelegramChannelPostImage
from app.services.telegram_marketing import publish_channel_post, _telegram_mini_app_link, channel_button_link_mode
from app.services.image_delivery import media_url_for_file_id
from app.models.user import User, UserRole
from app.models.delivery import Driver
from app.models.ai_conversation import AIConversation, ActivityLog
from app.utils import allowed_file
from slugify import slugify
from sqlalchemy import func, distinct, and_, or_, case

def _upload_to_telegram(file_obj):
    """Upload an image to Telegram via sendPhoto to a dedicated media channel.

    Returns a /media/<file_id> URL.  The proxy endpoint in main/routes.py
    resolves this at request time into a cached 302 redirect to Telegram CDN.
    Image bytes never pass through our server, so there is zero egress cost.

    Falls back to Supabase if Telegram upload is unavailable.
    """
    import httpx
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.environ.get('TELEGRAM_MEDIA_CHAT_ID', '').strip()

    if not token or not chat_id:
        current_app.logger.warning(
            'TELEGRAM_MEDIA_CHAT_ID is not set - using Supabase fallback for product images'
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

        photos = data['result']['photo']
        best = max(photos, key=lambda p: p.get('file_size', 0))
        file_id = best['file_id']

        return media_url_for_file_id(file_id)

    except Exception as e:
        current_app.logger.warning(f'Telegram upload failed: {e} - falling back to Supabase')
        file_obj.seek(0)
        return _upload_file_to_supabase(file_obj)


def _upload_video_to_telegram(file_obj):
    """Upload a video to Telegram via sendVideo to a dedicated media channel."""
    import httpx
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.environ.get('TELEGRAM_MEDIA_CHAT_ID', '').strip()

    if not token or not chat_id:
        current_app.logger.warning('TELEGRAM_MEDIA_CHAT_ID is not set for video upload')
        return None

    try:
        file_content = file_obj.read()
        file_obj.seek(0)
        content_type = getattr(file_obj, 'content_type', 'video/mp4') or 'video/mp4'
        orig_name = getattr(file_obj, 'filename', 'video.mp4') or 'video.mp4'
        ext = orig_name.rsplit('.', 1)[-1].lower() if '.' in orig_name else 'mp4'
        safe_name = f'product.{ext}'

        resp = httpx.post(
            f'https://api.telegram.org/bot{token}/sendVideo',
            data={'chat_id': chat_id, 'disable_notification': 'true'},
            files={'video': (safe_name, file_content, content_type)},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get('ok'):
            raise ValueError(f"Telegram API error: {data.get('description')}")

        video_info = data['result']['video']
        file_id = video_info['file_id']
        return media_url_for_file_id(file_id)

    except Exception as e:
        current_app.logger.error(f'Telegram video upload failed: {e}')
        return None


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
        payload = product.to_dict(include_description=True)
        payload['age_label'] = product.age_label()
        payload['primary_image'] = product.primary_image()
        broadcast_new_product(payload)
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
    online_rev = db.session.query(db.func.sum(Order.total)).filter(Order.status == OrderStatus.delivered).scalar() or 0
    pos_rev = db.session.query(db.func.sum(POSSale.total)).filter(POSSale.status == POSSaleStatus.completed).scalar() or 0
    stats = {
        'total_revenue': float(online_rev) + float(pos_rev),
        'total_orders': Order.query.count() + POSSale.query.count(),
        'today_orders': Order.query.filter(db.func.date(Order.created_at) == db.func.current_date()).count() + POSSale.query.filter(db.func.date(POSSale.created_at) == db.func.current_date()).count(),
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


@admin_bp.route('/products')
@admin_required
def products():
    q = request.args.get('q', '').strip()
    query = Product.query
    if q:
        query = query.filter(Product.name.ilike(f'%{q}%'))
    page = request.args.get('page', 1, type=int)
    pagination = query.order_by(Product.created_at.desc()).paginate(page=page, per_page=15)
    return render_template('admin/products.html', products=pagination.items, pagination=pagination, q=q)

@admin_bp.route('/api/upload-url', methods=['POST'])
@admin_required
def get_upload_url():
    """Generates a Supabase signed URL for direct client-side uploads to bypass Vercel limits."""
    import time, os, binascii
    filename = request.json.get('filename') if request.json else None
    if not filename:
        return jsonify({'error': 'Filename required'}), 400
        
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'bin'
    safe_name = f'custom_{int(time.time())}_{binascii.hexlify(os.urandom(4)).decode()}.{ext}'
    
    supabase_url = os.environ.get('SUPABASE_URL')
    supabase_key = (
        os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or
        os.environ.get('SUPABASE_KEY') or
        os.environ.get('SUPABASE_ANON_KEY')
    )
    if not supabase_url or not supabase_key:
        return jsonify({'error': 'Supabase not configured'}), 500
        
    try:
        from supabase import create_client, Client
        supabase: Client = create_client(supabase_url, supabase_key)
        res = supabase.storage.from_('uploads').create_signed_upload_url(safe_name)
        public_url = supabase.storage.from_('uploads').get_public_url(safe_name)
        
        signed_url = res.get('signedURL') or res.get('signedUrl') or res.get('url')
        if not signed_url:
            signed_url = res.get('signedURL')
            
        return jsonify({
            'signed_url': signed_url,
            'public_url': public_url,
            'path': safe_name
        })
    except Exception as e:
        current_app.logger.error(f'Supabase signed URL error: {e}')
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/products/create', methods=['GET', 'POST'])
@admin_required
def create_product():
    categories = Category.query.filter_by(is_active=True).all()
    from app.models.loyalty import LoyaltyLevel
    loyalty_levels = LoyaltyLevel.query.order_by(LoyaltyLevel.sort_order).all()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Product name is required.', 'danger')
            return render_template('admin/product_form.html', product=None, categories=categories, loyalty_levels=loyalty_levels)
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
            is_premium='is_premium' in request.form,
            price_hidden='price_hidden' in request.form,
            min_loyalty_level_id=_safe_int(request.form.get('min_loyalty_level_id'), 0) or None,
        )
        db.session.add(product)
        db.session.flush()

        # Handle image uploads — primary destination is Telegra.ph (Telegram's free CDN)
        images = request.files.getlist('images')
        upload_folder = current_app.config.get('UPLOAD_FOLDER', 'static/uploads')
        try:
            skipped_images = 0
            for i, img_file in enumerate(images):
                if img_file and img_file.filename and allowed_file(img_file.filename):
                    # 1st choice: Telegram media channel (free, unlimited, zero egress cost)
                    img_url = _upload_to_telegram(img_file)

                    if not img_url:
                        allow_local = current_app.debug or os.environ.get('ALLOW_LOCAL_IMAGE_FALLBACK', '').strip().lower() in ('1', 'true', 'yes')
                        if allow_local:
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
                        else:
                            current_app.logger.warning('Skipping image %s because Telegram/Supabase upload failed and local fallback is disabled.', i)
                            skipped_images += 1
                            continue

                    img = ProductImage(
                        product_id=product.id,
                        image_url=img_url,
                        is_primary=(i == 0),
                        sort_order=i,
                    )
                    db.session.add(img)
            db.session.commit()
            if skipped_images:
                flash(f'Skipped {skipped_images} image(s) because Telegram media storage is not configured.', 'warning')
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
    return render_template('admin/product_form.html', product=None, categories=categories, loyalty_levels=loyalty_levels)


@admin_bp.route('/products/<int:product_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_product(product_id):
    product = db.session.get(Product, product_id)
    if not product:
        flash('Product not found.', 'danger')
        return redirect(url_for('admin.products'))
    categories = Category.query.filter_by(is_active=True).all()
    from app.models.loyalty import LoyaltyLevel
    loyalty_levels = LoyaltyLevel.query.order_by(LoyaltyLevel.sort_order).all()
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
        product.is_premium = 'is_premium' in request.form
        product.price_hidden = 'price_hidden' in request.form
        product.min_loyalty_level_id = _safe_int(request.form.get('min_loyalty_level_id'), 0) or None

        images = request.files.getlist('images')
        upload_folder = current_app.config.get('UPLOAD_FOLDER', 'static/uploads')

        try:
            existing_count = product.images.count()
            skipped_images = 0
            for i, img_file in enumerate(images):
                if img_file and img_file.filename and allowed_file(img_file.filename):
                    # 1st choice: Telegram media channel (free, unlimited, zero egress cost)
                    img_url = _upload_to_telegram(img_file)

                    if not img_url:
                        allow_local = current_app.debug or os.environ.get('ALLOW_LOCAL_IMAGE_FALLBACK', '').strip().lower() in ('1', 'true', 'yes')
                        if allow_local:
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
                        else:
                            current_app.logger.warning('Skipping image %s because Telegram/Supabase upload failed and local fallback is disabled.', i)
                            skipped_images += 1
                            continue

                    img = ProductImage(
                        product_id=product.id,
                        image_url=img_url,
                        is_primary=(existing_count == 0 and i == 0),
                        sort_order=existing_count + i,
                    )
                    db.session.add(img)
            db.session.commit()
            if skipped_images:
                flash(f'Skipped {skipped_images} image(s) because Telegram media storage is not configured.', 'warning')
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
    return render_template('admin/product_form.html', product=product, categories=categories, loyalty_levels=loyalty_levels)


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
    settings = _get_settings()

    if request.method == 'POST':
        if request.form.get('save_mini_app_visibility') == '1':
            try:
                db_settings = LoyaltySettings.query.first()
                if not db_settings:
                    db_settings = LoyaltySettings()
                    db.session.add(db_settings)
                
                db_settings.show_categories_in_mini_app = 'show_categories_in_mini_app' in request.form
                db_settings.show_age_filter_in_mini_app = 'show_age_filter_in_mini_app' in request.form
                db.session.commit()
                flash('Mini app visibility updated!', 'success')
            except Exception as e:
                db.session.rollback()
                flash(f'Database error: {e}. The visibility toggle is enabled, but the database schema might be missing the required columns. Please run migrations first.', 'warning')
            return redirect(url_for('admin.categories'))

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
    return render_template('admin/categories.html', categories=cats, settings=settings)


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


# ── CUSTOMERS INTELLIGENCE HUB ──
@admin_bp.route('/customers')
@admin_required
def customers():
    q = request.args.get('q', '').strip()
    tab = request.args.get('tab', 'overview')
    
    # Use Addis Ababa local time for daily/weekly boundaries
    local_tz = ZoneInfo('Africa/Addis_Ababa')
    now_local = datetime.now(local_tz)
    today_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    
    today_start = today_start_local.astimezone(timezone.utc)
    week_start = (today_start_local - timedelta(days=7)).astimezone(timezone.utc)
    month_start = (today_start_local - timedelta(days=30)).astimezone(timezone.utc)
    now = datetime.now(timezone.utc)

    # ── Overview KPIs ──────────────────────────────────────────────
    total_customers = User.query.filter_by(role=UserRole.customer).count()
    new_today = User.query.filter(
        User.role == UserRole.customer,
        User.created_at >= today_start
    ).count()
    new_this_week = User.query.filter(
        User.role == UserRole.customer,
        User.created_at >= week_start
    ).count()
    new_this_month = User.query.filter(
        User.role == UserRole.customer,
        User.created_at >= month_start
    ).count()
    telegram_users = User.query.filter(
        User.role == UserRole.customer,
        User.telegram_id.isnot(None)
    ).count()

    # Mini App visits today (unique sessions)
    visits_today = db.session.query(func.count(distinct(ActivityLog.user_id))).filter(
        ActivityLog.action == 'mini_app_visit',
        ActivityLog.created_at >= today_start
    ).scalar() or 0
    visits_week = db.session.query(func.count(distinct(ActivityLog.user_id))).filter(
        ActivityLog.action == 'mini_app_visit',
        ActivityLog.created_at >= week_start
    ).scalar() or 0

    # Cart adds this week
    cart_adds_week = db.session.query(func.count(ActivityLog.id)).filter(
        ActivityLog.action == 'add_to_cart',
        ActivityLog.created_at >= week_start
    ).scalar() or 0

    # Buy Now clicks from Telegram channel (this week)
    buy_now_clicks_week = db.session.query(func.count(ActivityLog.id)).filter(
        ActivityLog.action == 'telegram_buy_now_click',
        ActivityLog.created_at >= week_start
    ).scalar() or 0

    # AI conversations this week
    ai_convos_week = db.session.query(
        func.count(distinct(AIConversation.session_id))
    ).filter(
        AIConversation.created_at >= week_start
    ).scalar() or 0

    new_users_30 = User.query.filter(
        User.role == UserRole.customer,
        User.created_at >= month_start
    ).count()
    returning_users_30 = db.session.query(func.count(distinct(ActivityLog.user_id))).join(User).filter(
        User.role == UserRole.customer,
        User.created_at < month_start,
        ActivityLog.created_at >= month_start
    ).scalar() or 0

    # ── Daily visits for past 14 days chart ───────────────────────
    daily_visits = []
    for i in range(13, -1, -1):
        day_local = today_start_local - timedelta(days=i)
        next_day_local = day_local + timedelta(days=1)
        day_utc = day_local.astimezone(timezone.utc)
        next_day_utc = next_day_local.astimezone(timezone.utc)
        
        visitors = db.session.query(func.count(distinct(
            db.case((ActivityLog.user_id.isnot(None), db.cast(ActivityLog.user_id, db.String)), else_=ActivityLog.ip_address)
        ))).filter(
            ActivityLog.action == 'mini_app_visit',
            ActivityLog.created_at >= day_utc,
            ActivityLog.created_at < next_day_utc
        ).scalar() or 0
        
        visits = db.session.query(func.count(ActivityLog.id)).filter(
            ActivityLog.action == 'mini_app_visit',
            ActivityLog.created_at >= day_utc,
            ActivityLog.created_at < next_day_utc
        ).scalar() or 0
        
        returning = db.session.query(func.count(distinct(ActivityLog.user_id))).join(User).filter(
            User.role == UserRole.customer,
            User.created_at < day_utc,
            ActivityLog.action == 'mini_app_visit',
            ActivityLog.created_at >= day_utc,
            ActivityLog.created_at < next_day_utc
        ).scalar() or 0
        
        daily_visits.append({
            'date': day_local.strftime('%b %d'), 
            'visitors': visitors,
            'visits': visits,
            'returning': returning
        })

    # ── Weekly signups for past 8 weeks chart ─────────────────────
    weekly_signups = []
    for i in range(7, -1, -1):
        wk_start_local = today_start_local - timedelta(weeks=i+1)
        wk_end_local = today_start_local - timedelta(weeks=i)
        wk_start_utc = wk_start_local.astimezone(timezone.utc)
        wk_end_utc = wk_end_local.astimezone(timezone.utc)
        
        cnt = User.query.filter(
            User.role == UserRole.customer,
            User.created_at >= wk_start_utc,
            User.created_at < wk_end_utc
        ).count()
        weekly_signups.append({'week': wk_start_local.strftime('W%U %b'), 'count': cnt})

    # ── Active Carts (Abandonment) ──────────────────────
    from app.models.order import Cart
    active_carts_query = db.session.query(
        Cart.user_id,
        Cart.session_id,
        func.sum(Cart.quantity).label('total_items'),
        func.max(Cart.added_at).label('last_active'),
    ).join(Product).group_by(Cart.user_id, Cart.session_id).order_by(func.max(Cart.added_at).desc()).limit(8).all()

    active_carts_data = []
    for uid, sid, qty, last_active in active_carts_query:
        user = db.session.get(User, uid) if uid else None
        cart_items = Cart.query.filter_by(user_id=uid, session_id=sid).all()
        total_price = sum(i.quantity * float(i.product.price) for i in cart_items if i.product)
        
        name = user.full_name if user else "Anonymous Visitor"
        identifier = f"@{user.telegram_username}" if (user and user.telegram_username) else (f"User #{user.id}" if user else f"Session {sid[:6] if sid else '?'}")
        
        active_carts_data.append({
            'name': name,
            'identifier': identifier,
            'items': qty,
            'total': total_price,
            'last_active': last_active.strftime('%b %d, %H:%M')
        })

    # ── Buy Now clicks by product ─────────────────────────────────
    top_buy_now = db.session.query(
        ActivityLog.entity_id,
        func.count(ActivityLog.id).label('cnt')
    ).filter(
        ActivityLog.action == 'telegram_buy_now_click',
        ActivityLog.entity_id.isnot(None),
        ActivityLog.created_at >= month_start
    ).group_by(ActivityLog.entity_id).order_by(func.count(ActivityLog.id).desc()).limit(5).all()

    top_buy_now_data = []
    for pid, cnt in top_buy_now:
        p = db.session.get(Product, pid)
        if p:
            top_buy_now_data.append({'name': p.name, 'count': cnt})

    # ── AI suggestions breakdown ──────────────────────────────────
    ai_suggestions = db.session.query(
        ActivityLog.entity_id,
        func.count(ActivityLog.id).label('cnt')
    ).filter(
        ActivityLog.action == 'ai_suggested_product',
        ActivityLog.entity_id.isnot(None),
        ActivityLog.created_at >= month_start
    ).group_by(ActivityLog.entity_id).order_by(func.count(ActivityLog.id).desc()).limit(5).all()

    ai_suggestions_data = []
    for pid, cnt in ai_suggestions:
        p = db.session.get(Product, pid)
        if p:
            ai_suggestions_data.append({'name': p.name, 'count': cnt})

    # ── Recent activity feed ───────────────────────────────────────
    recent_activity = ActivityLog.query.order_by(
        ActivityLog.created_at.desc()
    ).limit(20).all()

    # ── Customer list (search) ────────────────────────────────────
    cust_query = User.query.filter_by(role=UserRole.customer)
    if q:
        cust_query = cust_query.filter(or_(
            User.full_name.ilike(f'%{q}%'),
            User.email.ilike(f'%{q}%'),
            User.phone.ilike(f'%{q}%'),
            User.telegram_username.ilike(f'%{q}%'),
        ))
    pagination = cust_query.order_by(User.created_at.desc()).paginate(
        page=request.args.get('page', 1, int), per_page=25, error_out=False
    )

    # ── AI conversations recent ───────────────────────────────────
    recent_ai = db.session.query(
        AIConversation.session_id,
        AIConversation.user_id,
        func.count(AIConversation.id).label('msg_count'),
        func.max(AIConversation.created_at).label('last_msg'),
    ).group_by(AIConversation.session_id, AIConversation.user_id
    ).order_by(func.max(AIConversation.created_at).desc()).limit(15).all()

    return render_template('admin/customers.html',
        tab=tab, q=q,
        now=now,
        # KPIs
        total_customers=total_customers,
        new_today=new_today,
        new_this_week=new_this_week,
        new_this_month=new_this_month,
        telegram_users=telegram_users,
        visits_today=visits_today,
        visits_week=visits_week,
        cart_adds_week=cart_adds_week,
        buy_now_clicks_week=buy_now_clicks_week,
        ai_convos_week=ai_convos_week,
        # Charts data
        new_users_30=new_users_30,
        returning_users_30=returning_users_30,
        daily_visits=daily_visits,
        weekly_signups=weekly_signups,
        active_carts=active_carts_data,
        top_buy_now=top_buy_now_data,
        ai_suggestions=ai_suggestions_data,
        recent_activity=recent_activity,
        recent_ai=recent_ai,
        # Customer list
        customers=pagination.items,
        pagination=pagination,
    )


@admin_bp.route('/customers/<int:user_id>')
@admin_required
def customer_detail(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash('Customer not found', 'danger')
        return redirect(url_for('admin.customers'))

    # Activity timeline
    activity = ActivityLog.query.filter_by(user_id=user_id).order_by(
        ActivityLog.created_at.desc()
    ).limit(50).all()

    # Orders
    orders = Order.query.filter_by(user_id=user_id).order_by(
        Order.created_at.desc()
    ).limit(20).all()

    # AI conversations grouped by session
    ai_sessions = db.session.query(
        AIConversation.session_id,
        func.count(AIConversation.id).label('msg_count'),
        func.min(AIConversation.created_at).label('started'),
        func.max(AIConversation.created_at).label('last_msg'),
    ).filter(
        AIConversation.user_id == user_id
    ).group_by(AIConversation.session_id
    ).order_by(func.max(AIConversation.created_at).desc()).limit(10).all()

    # Messages for each session (latest 3 sessions full)
    ai_messages_by_session = {}
    for sess in ai_sessions[:3]:
        msgs = AIConversation.query.filter_by(
            session_id=sess.session_id
        ).order_by(AIConversation.created_at.asc()).all()
        ai_messages_by_session[sess.session_id] = msgs

    return render_template('admin/customer_detail.html',
        customer=user,
        activity=activity,
        orders=orders,
        ai_sessions=ai_sessions,
        ai_messages_by_session=ai_messages_by_session,
    )


@admin_bp.route('/customers/live-activity')
@admin_required
def customers_live_activity():
    """Returns the latest 20 activities for the live feed."""
    recent = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(20).all()
    local_tz = ZoneInfo('Africa/Addis_Ababa')
    
    html = []
    for act in recent:
        action = act.action
        # Get localized time
        dt = act.created_at
        if dt:
            dt_local = dt.replace(tzinfo=timezone.utc).astimezone(local_tz)
            time_str = dt_local.strftime('%b %d, %H:%M')
        else:
            time_str = '—'
            
        # Determine dot class/icon
        dot_cls = 'other'
        icon = '<i class="fas fa-circle"></i>'
        if action == 'mini_app_visit':
            dot_cls, icon = 'visit', '<i class="fas fa-mobile-alt"></i>'
        elif action == 'add_to_cart':
            dot_cls, icon = 'cart', '<i class="fas fa-shopping-cart"></i>'
        elif action == 'telegram_buy_now_click':
            dot_cls, icon = 'buy', '<i class="fab fa-telegram"></i>'
        elif action == 'ai_suggested_product':
            dot_cls, icon = 'ai', '<i class="fas fa-robot"></i>'
        elif action == 'view_product':
            dot_cls, icon = 'view', '<i class="fas fa-eye"></i>'
            
        # Determine title
        title = action.replace('_', ' ').title()
        if action == 'mini_app_visit': title = 'Mini App Visited'
        elif action == 'add_to_cart': title = f"Added to Cart{f' — Product #{act.entity_id}' if act.entity_id else ''}"
        elif action == 'telegram_buy_now_click': title = f"Telegram Buy Now Click{f' — Product #{act.entity_id}' if act.entity_id else ''}"
        elif action == 'ai_suggested_product': title = f"AI Suggested Product #{act.entity_id}"
        elif action == 'view_product': title = f"Viewed Product #{act.entity_id}"
        
        meta_str = f"User #{act.user_id}" if act.user_id else "Anonymous"
        if act.ip_address: meta_str += f" · {act.ip_address}"
        
        html.append(f'''
        <li class="activity-item">
          <div class="act-dot {dot_cls}">{icon}</div>
          <div class="act-body">
            <div class="act-title">{title}</div>
            <div class="act-meta">{meta_str}</div>
          </div>
          <div class="act-time">{time_str}</div>
        </li>
        ''')
        
    return ''.join(html)

@admin_bp.route('/customers/weekly-report')
@admin_required
def customers_weekly_report():
    """JSON endpoint for weekly report data."""
    local_tz = ZoneInfo('Africa/Addis_Ababa')
    now_local = datetime.now(local_tz)
    today_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    weeks = []
    for i in range(7, -1, -1):
        wk_start_local = today_start_local - timedelta(weeks=i+1)
        wk_end_local = today_start_local - timedelta(weeks=i)
        
        wk_start = wk_start_local.astimezone(timezone.utc)
        wk_end = wk_end_local.astimezone(timezone.utc)
        
        new_users = User.query.filter(
            User.role == UserRole.customer,
            User.created_at >= wk_start,
            User.created_at < wk_end
        ).count()
        visits = db.session.query(func.count(distinct(
            db.case((ActivityLog.user_id.isnot(None), db.cast(ActivityLog.user_id, db.String)), else_=ActivityLog.ip_address)
        ))).filter(
            ActivityLog.action == 'mini_app_visit',
            ActivityLog.created_at >= wk_start,
            ActivityLog.created_at < wk_end
        ).scalar() or 0
        
        from app.models.inventory import POSSale, POSSaleStatus
        orders_cnt = Order.query.filter(
            Order.created_at >= wk_start,
            Order.created_at < wk_end,
            Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
        ).count()
        pos_cnt = POSSale.query.filter(
            POSSale.created_at >= wk_start,
            POSSale.created_at < wk_end,
            POSSale.status == POSSaleStatus.completed
        ).count()
        total_orders_cnt = orders_cnt + pos_cnt

        revenue = db.session.query(func.sum(Order.total)).filter(
            Order.created_at >= wk_start,
            Order.created_at < wk_end,
            Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
        ).scalar() or 0
        revenue_pos = db.session.query(func.sum(POSSale.total)).filter(
            POSSale.created_at >= wk_start,
            POSSale.created_at < wk_end,
            POSSale.status == POSSaleStatus.completed
        ).scalar() or 0
        total_revenue = float(revenue) + float(revenue_pos)

        weeks.append({
            'week': wk_start_local.strftime('%b %d'),
            'new_users': new_users,
            'visits': visits,
            'orders': total_orders_cnt,
            'revenue': total_revenue,
        })
    return jsonify({'weeks': weeks})


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

ADMIN_TZ = ZoneInfo("Africa/Addis_Ababa")


def _configured_mini_app_url():
    return current_app.config.get('MINI_APP_URL') or os.environ.get('MINI_APP_URL', '').strip() or 'http://localhost:5000/mini-app'



def _configured_telegram_mini_app_link(*, tab: str = 'home', query: str = '', startapp: str = '', product_id=None):
    if product_id:
        startapp = f'product__{product_id}'
        tab = 'shop'
    return _telegram_mini_app_link(tab=tab, query=query, startapp=startapp)

def _configured_channel_id():
    return (
        current_app.config.get('TELEGRAM_CHANNEL_CHAT_ID')
        or current_app.config.get('TELEGRAM_MAIN_CHANNEL_ID')
        or os.environ.get('TELEGRAM_CHANNEL_CHAT_ID', '').strip()
        or os.environ.get('TELEGRAM_MAIN_CHANNEL_ID', '').strip()
        or os.environ.get('TELEGRAM_CHANNEL_ID', '').strip()
        or ''
    )


def _admin_now_utc():
    return datetime.now(timezone.utc)


def _parse_admin_datetime(raw):
    if not raw:
        return None
    try:
        local_dt = datetime.strptime(raw, '%Y-%m-%dT%H:%M')
    except Exception:
        return None
    return local_dt.replace(tzinfo=ADMIN_TZ).astimezone(timezone.utc)


def _display_admin_datetime(value):
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ADMIN_TZ)


def _parse_message_ids(raw):
    if not raw:
        return []
    try:
        if raw.startswith('['):
            data = json.loads(raw)
            return [str(mid) for mid in data if mid]
    except Exception:
        pass
    return [str(raw)]


def _post_image_urls(post):
    return [img.image_url for img in post.images.order_by(TelegramChannelPostImage.sort_order.asc()).all()]


def _delete_telegram_post(post):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = (post.channel_chat_id or os.environ.get('TELEGRAM_CHANNEL_CHAT_ID') or os.environ.get('TELEGRAM_MAIN_CHANNEL_ID') or os.environ.get('TELEGRAM_CHANNEL_ID') or '').strip()
    message_ids = _parse_message_ids(post.sent_message_id)
    if not token or not chat_id or not message_ids:
        return True, 'Nothing to delete'

    errors = []
    for mid in message_ids:
        try:
            resp = httpx.post(
                f'https://api.telegram.org/bot{token}/deleteMessage',
                json={'chat_id': chat_id, 'message_id': int(mid)},
                timeout=15,
            )
            data = resp.json()
            if not data.get('ok'):
                errors.append(data.get('description') or f'Could not delete message {mid}')
        except Exception as exc:
            errors.append(str(exc))
    if errors:
        return False, '; '.join(errors)
    return True, 'Deleted from Telegram'


def _save_post_images(post, image_urls):
    for idx, img_url in enumerate(image_urls):
        db.session.add(TelegramChannelPostImage(
            post_id=post.id,
            image_url=img_url,
            sort_order=idx,
        ))


def _publish_post(post, product=None):
    if post.post_type == 'product' and product is None and post.product:
        product = post.product
    image_urls = _post_image_urls(post)
    if post.post_type == 'product' and not image_urls and product is not None:
        image_urls = [product.primary_image()]

    button_text = post.button_text or '🌐 Open Mini App'
    button_url = post.button_url or _configured_telegram_mini_app_link(tab='home')
    result = asyncio.run(publish_channel_post(
        post,
        images=image_urls if image_urls else None,
        product=product,
        button_text=button_text,
        button_url=button_url,
    ))
    if result.get('ok'):
        post.status = 'sent'
        post.sent_at = _admin_now_utc()
        post.error_message = None
        message_ids = result.get('message_ids')
        if message_ids:
            post.sent_message_id = json.dumps([mid for mid in message_ids if mid])
        else:
            post.sent_message_id = str(result.get('result', {}).get('message_id') or '')
        db.session.commit()
        if channel_button_link_mode() == 'https':
            return True, (
                'Channel post published. Buttons open your mini app via HTTPS '
                '(works in channels). For native Telegram mini app links, enable '
                'Configure Mini App in @BotFather and add TELEGRAM_MINI_APP_SHORT_NAME in Vercel.'
            )
        return True, 'Channel post published successfully.'
    post.status = 'failed'
    post.error_message = result.get('error') or result.get('description') or 'Telegram returned an error'
    db.session.commit()
    return False, post.error_message


def _process_due_channel_posts():
    due_posts = TelegramChannelPost.query.filter_by(status='scheduled').order_by(TelegramChannelPost.scheduled_at.asc()).all()
    processed = 0
    for post in due_posts:
        try:
            if post.is_due():
                ok, _msg = _publish_post(post)
                if ok:
                    processed += 1
        except Exception as exc:
            post.status = 'failed'
            post.error_message = str(exc)
            db.session.commit()
    return processed


@admin_bp.route('/channel-posts/process-due', methods=['GET', 'POST'])
def process_due_channel_posts():
    secret = (request.args.get('secret') or request.headers.get('X-Cron-Secret') or '').strip()
    expected = os.environ.get('CHANNEL_POSTS_CRON_SECRET', '').strip()
    if not expected or secret != expected:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    processed = _process_due_channel_posts()
    return jsonify({'success': True, 'processed': processed})


@admin_bp.route('/channel-posts', methods=['GET', 'POST'])
@admin_required
def channel_posts():
    products = Product.query.filter_by(is_active=True).order_by(Product.created_at.desc()).limit(300).all()
    processed = _process_due_channel_posts()
    recent_posts = TelegramChannelPost.query.order_by(TelegramChannelPost.created_at.desc()).limit(25).all()

    if request.method == 'POST':
        def _safe_int(val, default=0):
            try:
                return int(val) if str(val).strip() else default
            except Exception:
                return default

        post_type = request.form.get('post_type', 'announcement').strip() or 'announcement'
        title = request.form.get('title', '').strip()
        caption = request.form.get('caption', '').strip()
        button_text = request.form.get('button_text', 'Open Mini App').strip() or 'Open Mini App'
        scheduled_at = _parse_admin_datetime(request.form.get('scheduled_at', '').strip())
        send_now = 'send_now' in request.form or not scheduled_at or scheduled_at <= _admin_now_utc()
        status = 'sent' if send_now else 'scheduled'

        post = TelegramChannelPost(
            post_type=post_type,
            title=title,
            caption=caption,
            button_text=button_text,
            button_url=_configured_telegram_mini_app_link(tab='home'),
            status=status,
            scheduled_at=scheduled_at,
            channel_chat_id=_configured_channel_id(),
        )

        product = None
        image_urls = []
        try:
            if post_type == 'product':
                product_id = _safe_int(request.form.get('product_id'))
                product = db.session.get(Product, product_id)
                if not product:
                    flash('Select a valid product for the channel post.', 'danger')
                    return render_template('admin/channel_posts.html', products=products, recent_posts=recent_posts, processed=processed, configured_tz=ADMIN_TZ)
                post.product_id = product.id
                post.title = title or product.name
                custom_photo_url = request.form.get('custom_photo_url', '').strip()
                custom_video_url = request.form.get('custom_video_url', '').strip()

                custom_photo = request.files.get('custom_photo')
                custom_video = request.files.get('custom_video')
                
                if custom_photo_url:
                    image_urls.append(custom_photo_url)
                elif custom_photo and custom_photo.filename and allowed_file(custom_photo.filename):
                    img_url = _upload_to_telegram(custom_photo)
                    if img_url: image_urls.append(img_url)
                
                if custom_video_url:
                    image_urls.append(custom_video_url)
                elif custom_video and custom_video.filename:
                    vid_url = _upload_video_to_telegram(custom_video)
                    if vid_url: image_urls.append(vid_url)
                
                if not image_urls:
                    image_urls = [product.primary_image()]
                
                post.button_url = _configured_telegram_mini_app_link(product_id=product.id)
                
                if not caption:
                    post.caption = ''
            else:
                uploaded = request.files.getlist('images')
                if uploaded:
                    upload_folder = current_app.config.get('UPLOAD_FOLDER', 'static/uploads')
                    allow_local = current_app.debug or os.environ.get('ALLOW_LOCAL_IMAGE_FALLBACK', '').strip().lower() in ('1', 'true', 'yes')
                    for img_file in uploaded:
                        if img_file and img_file.filename and allowed_file(img_file.filename):
                            img_url = _upload_to_telegram(img_file)
                            if not img_url and allow_local:
                                try:
                                    os.makedirs(upload_folder, exist_ok=True)
                                    ext = img_file.filename.rsplit('.', 1)[-1].lower()
                                    import uuid
                                    fname = f'announcement_{uuid.uuid4().hex[:8]}.{ext}'
                                    img_file.seek(0)
                                    img_file.save(os.path.join(upload_folder, fname))
                                    img_url = f'/static/uploads/{fname}'
                                except OSError as e:
                                    current_app.logger.error('Local upload failed for announcement image: %s', e)
                            if img_url:
                                image_urls.append(img_url)
                if not title:
                    flash('Announcement title is required.', 'danger')
                    return render_template('admin/channel_posts.html', products=products, recent_posts=recent_posts, processed=processed, configured_tz=ADMIN_TZ)

            db.session.add(post)
            db.session.flush()
            if image_urls:
                _save_post_images(post, image_urls)
            db.session.commit()

            if send_now:
                ok, msg = _publish_post(post, product=product)
                flash(msg, 'success' if ok else 'danger')
            else:
                flash(f'Post scheduled for {scheduled_at.astimezone(ADMIN_TZ).strftime("%b %d, %Y %H:%M")}.', 'success')
            return redirect(url_for('admin.channel_posts'))
        except Exception as exc:
            db.session.rollback()
            current_app.logger.warning(f'Channel post failed: {exc}')
            flash(f'Could not save channel post: {exc}', 'danger')
            return render_template('admin/channel_posts.html', products=products, recent_posts=recent_posts, processed=processed, configured_tz=ADMIN_TZ)

    return render_template('admin/channel_posts.html', products=products, recent_posts=recent_posts, processed=processed, configured_tz=ADMIN_TZ)


@admin_bp.route('/channel-posts/<int:post_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_channel_post(post_id):
    post = db.session.get(TelegramChannelPost, post_id)
    if not post:
        flash('Channel post not found.', 'danger')
        return redirect(url_for('admin.channel_posts'))
    products = Product.query.filter_by(is_active=True).order_by(Product.created_at.desc()).limit(300).all()

    if request.method == 'POST':
        def _safe_int(val, default=0):
            try:
                return int(val) if str(val).strip() else default
            except Exception:
                return default

        post.post_type = request.form.get('post_type', post.post_type).strip() or post.post_type
        post.title = request.form.get('title', post.title or '').strip()
        post.caption = request.form.get('caption', post.caption or '').strip()
        post.button_text = request.form.get('button_text', post.button_text or 'Open Mini App').strip() or 'Open Mini App'
        post.button_url = _configured_telegram_mini_app_link(tab='home')
        post.scheduled_at = _parse_admin_datetime(request.form.get('scheduled_at', '').strip())
        republish_now = 'republish_now' in request.form
        schedule_later = post.scheduled_at and post.scheduled_at > _admin_now_utc() and not republish_now

        try:
            if post.post_type == 'product':
                product_id = _safe_int(request.form.get('product_id'))
                product = db.session.get(Product, product_id)
                if not product:
                    flash('Select a valid product.', 'danger')
                    return render_template('admin/channel_post_edit.html', post=post, products=products, configured_tz=ADMIN_TZ)
                post.product_id = product.id
                image_mode = request.form.get('product_image_mode', 'primary')
                if request.files.getlist('images'):
                    post.images.delete()
                    db.session.flush()
                if post.images.count() == 0:
                    image_urls = product.all_images() if image_mode == 'gallery' else [product.primary_image()]
                    _save_post_images(post, image_urls)
                if not post.caption:
                    post.caption = ''
            else:
                uploaded = request.files.getlist('images')
                if uploaded:
                    post.images.delete()
                    db.session.flush()
                    image_urls = []
                    for img_file in uploaded:
                        if img_file and img_file.filename and allowed_file(img_file.filename):
                            img_url = _upload_to_telegram(img_file)
                            if img_url:
                                image_urls.append(img_url)
                    _save_post_images(post, image_urls)
                if not post.title:
                    flash('Announcement title is required.', 'danger')
                    return render_template('admin/channel_post_edit.html', post=post, products=products, configured_tz=ADMIN_TZ)

            if schedule_later:
                post.status = 'scheduled'
                post.error_message = None
                db.session.commit()
                flash('Post updated and kept scheduled.', 'success')
                return redirect(url_for('admin.channel_posts'))

            if post.status == 'sent' and not republish_now:
                db.session.commit()
                flash('Post details were updated in the admin portal. Check the republish box to push a replacement to Telegram.', 'success')
                return redirect(url_for('admin.channel_posts'))

            if post.status == 'sent' and republish_now:
                _delete_telegram_post(post)

            db.session.commit()
            ok, msg = _publish_post(post, product=post.product if post.post_type == 'product' else None)
            flash(msg, 'success' if ok else 'danger')
            return redirect(url_for('admin.channel_posts'))
        except Exception as exc:
            db.session.rollback()
            flash(f'Could not update post: {exc}', 'danger')
            return render_template('admin/channel_post_edit.html', post=post, products=products, configured_tz=ADMIN_TZ)

    return render_template('admin/channel_post_edit.html', post=post, products=products, configured_tz=ADMIN_TZ)


@admin_bp.route('/channel-posts/<int:post_id>/delete', methods=['POST'])
@admin_required
def delete_channel_post(post_id):
    post = db.session.get(TelegramChannelPost, post_id)
    if not post:
        flash('Channel post not found.', 'danger')
        return redirect(url_for('admin.channel_posts'))
    if post.status == 'sent':
        ok, msg = _delete_telegram_post(post)
        if not ok:
            flash(f'Telegram delete had issues: {msg}', 'warning')
    db.session.delete(post)
    db.session.commit()
    flash('Channel post deleted.', 'success')
    return redirect(url_for('admin.channel_posts'))


@admin_bp.route('/channel-posts/<int:post_id>/send-now', methods=['POST'])
@admin_required
def send_channel_post_now(post_id):
    post = db.session.get(TelegramChannelPost, post_id)
    if not post:
        flash('Channel post not found.', 'danger')
        return redirect(url_for('admin.channel_posts'))
    post.scheduled_at = None
    post.status = 'draft'
    db.session.commit()
    ok, msg = _publish_post(post, product=post.product if post.post_type == 'product' else None)
    flash(msg, 'success' if ok else 'danger')
    return redirect(url_for('admin.channel_posts'))


@admin_bp.route('/discounts', methods=['GET', 'POST'])
@admin_required
def discounts():
    products = Product.query.filter_by(is_active=True).order_by(Product.name.asc()).all()
    discounts_q = ProductDiscount.query.order_by(ProductDiscount.created_at.desc()).all()
    if request.method == 'POST':
        def _safe_int(val, default=0):
            try:
                return int(val) if str(val).strip() else default
            except Exception:
                return default

        def _safe_float(val, default=0.0):
            try:
                return float(val) if str(val).strip() else default
            except Exception:
                return default

        scope = request.form.get('scope', 'product').strip() or 'product'
        product_id = _safe_int(request.form.get('product_id')) if scope == 'product' else None
        if scope == 'product' and not product_id:
            flash('Please select a product for the discount.', 'danger')
            return render_template('admin/discounts.html', discounts=discounts_q, products=products, discount=None, edit_mode=False, configured_tz=ADMIN_TZ)

        discount_type = request.form.get('discount_type', 'percentage').strip() or 'percentage'
        value = _safe_float(request.form.get('discount_value'))
        title = request.form.get('title', '').strip()
        starts_at = _parse_admin_datetime(request.form.get('starts_at', '').strip())
        ends_at = _parse_admin_datetime(request.form.get('ends_at', '').strip())
        priority = _safe_int(request.form.get('priority'), 100)

        existing = ProductDiscount.query.filter_by(scope=scope, product_id=product_id, is_active=True).all()
        for row in existing:
            row.is_active = False

        discount = ProductDiscount(
            product_id=product_id,
            scope=scope,
            title=title,
            discount_type=DiscountType[discount_type],
            discount_value=value,
            starts_at=starts_at,
            ends_at=ends_at,
            priority=priority,
            is_active=True,
        )
        db.session.add(discount)
        db.session.commit()
        flash('Discount saved successfully.', 'success')
        return redirect(url_for('admin.discounts'))

    return render_template('admin/discounts.html', discounts=discounts_q, products=products, discount=None, edit_mode=False, configured_tz=ADMIN_TZ)


@admin_bp.route('/discounts/<int:discount_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_discount(discount_id):
    discount = db.session.get(ProductDiscount, discount_id)
    if not discount:
        flash('Discount not found.', 'danger')
        return redirect(url_for('admin.discounts'))
    products = Product.query.filter_by(is_active=True).order_by(Product.name.asc()).all()
    if request.method == 'POST':
        def _safe_int(val, default=0):
            try:
                return int(val) if str(val).strip() else default
            except Exception:
                return default

        def _safe_float(val, default=0.0):
            try:
                return float(val) if str(val).strip() else default
            except Exception:
                return default

        discount.scope = request.form.get('scope', discount.scope).strip() or discount.scope
        discount.product_id = _safe_int(request.form.get('product_id')) if discount.scope == 'product' else None
        if discount.scope == 'product' and not discount.product_id:
            flash('Please select a product for the discount.', 'danger')
            return render_template('admin/discounts.html', discounts=ProductDiscount.query.order_by(ProductDiscount.created_at.desc()).all(), products=products, discount=discount, edit_mode=True, configured_tz=ADMIN_TZ)
        discount.title = request.form.get('title', '').strip()
        discount.discount_type = DiscountType[request.form.get('discount_type', discount.discount_type.value)]
        discount.discount_value = _safe_float(request.form.get('discount_value'), float(discount.discount_value))
        discount.starts_at = _parse_admin_datetime(request.form.get('starts_at', '').strip())
        discount.ends_at = _parse_admin_datetime(request.form.get('ends_at', '').strip())
        discount.priority = _safe_int(request.form.get('priority'), discount.priority)
        discount.is_active = 'is_active' in request.form
        db.session.commit()
        flash('Discount updated.', 'success')
        return redirect(url_for('admin.discounts'))
    return render_template('admin/discounts.html', discounts=ProductDiscount.query.order_by(ProductDiscount.created_at.desc()).all(), products=products, discount=discount, edit_mode=True, configured_tz=ADMIN_TZ)


@admin_bp.route('/discounts/<int:discount_id>/toggle', methods=['POST'])
@admin_required
def toggle_discount(discount_id):
    discount = db.session.get(ProductDiscount, discount_id)
    if discount:
        discount.is_active = not discount.is_active
        db.session.commit()
        flash('Discount updated.', 'success')
    return redirect(url_for('admin.discounts'))


@admin_bp.route('/discounts/<int:discount_id>/delete', methods=['POST'])
@admin_required
def delete_discount(discount_id):
    discount = db.session.get(ProductDiscount, discount_id)
    if discount:
        db.session.delete(discount)
        db.session.commit()
        flash('Discount removed.', 'success')
    return redirect(url_for('admin.discounts'))


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
