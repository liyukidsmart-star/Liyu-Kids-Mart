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


# ─────────────────────────────────────────────────────────────
# SMART PRICE ADJUSTMENT
# ─────────────────────────────────────────────────────────────

@admin_bp.route('/products/smart-pricing', methods=['GET', 'POST'])
@admin_required
def smart_pricing():
    """Admin page to manage per-product smart price adjustment percentages."""
    if request.method == 'POST':
        action = request.form.get('action', '')

        if action == 'update_product':
            pid = int(request.form.get('product_id', 0))
            product = db.session.get(Product, pid)
            if product:
                product.smart_price_enabled = 'smart_price_enabled' in request.form
                pct_raw = request.form.get('smart_price_adjustment_pct', '0')
                product.smart_price_adjustment_pct = float(pct_raw) if pct_raw.strip() else 0.0
                db.session.commit()
                flash(f'✅ Smart pricing updated for "{product.name}".', 'success')

        elif action == 'bulk_enable':
            # Enable smart pricing at a given % for all products above a price threshold
            min_price = float(request.form.get('bulk_min_price', 0) or 0)
            bulk_pct = float(request.form.get('bulk_pct', 0) or 0)
            if bulk_pct > 0:
                products_to_update = Product.query.filter(
                    Product.is_active == True,
                    Product.price >= min_price,
                ).all()
                for p in products_to_update:
                    p.smart_price_enabled = True
                    p.smart_price_adjustment_pct = bulk_pct
                db.session.commit()
                flash(f'✅ Enabled {bulk_pct}% smart pricing on {len(products_to_update)} products priced {min_price:,.0f}+ Birr.', 'success')
            else:
                flash('Please enter a valid percentage greater than 0.', 'warning')

        elif action == 'disable_all':
            Product.query.update({'smart_price_enabled': False, 'smart_price_adjustment_pct': 0.0})
            db.session.commit()
            flash('Smart pricing disabled for all products.', 'info')

        return redirect(url_for('admin.smart_pricing'))

    # GET — list all active products with pricing info
    q = request.args.get('q', '').strip()
    show_enabled_only = request.args.get('enabled_only', '') == '1'
    query = Product.query.filter_by(is_active=True)
    if q:
        query = query.filter(Product.name.ilike(f'%{q}%'))
    if show_enabled_only:
        query = query.filter_by(smart_price_enabled=True)

    products_list = query.order_by(Product.name.asc()).all()
    enabled_count = Product.query.filter_by(is_active=True, smart_price_enabled=True).count()

    from app.services.loyalty_service import _get_settings
    settings = _get_settings()
    qty_min_price = float(getattr(settings, 'qty_discount_min_price', 2500))

    return render_template(
        'admin/smart_pricing.html',
        products=products_list,
        enabled_count=enabled_count,
        q=q,
        show_enabled_only=show_enabled_only,
        qty_min_price=qty_min_price,
    )


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
        from app.models.inventory import StockTransaction, POSSaleItem
        from app.models.ai_conversation import ProductRecommendation
        from app.models.marketing import TelegramChannelPost
        from app.models.order import OrderItem
        
        # Clean up related records to prevent IntegrityError
        StockTransaction.query.filter_by(product_id=product.id).delete()
        ProductRecommendation.query.filter_by(product_id=product.id).delete()
        
        POSSaleItem.query.filter_by(product_id=product.id).update({'product_id': None})
        TelegramChannelPost.query.filter_by(product_id=product.id).update({'product_id': None})
        OrderItem.query.filter_by(product_id=product.id).update({'product_id': None})
        
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

    # Fetch all distinct carts that still have items (not checked out)
    # Group by user_id + session_id. We do this per-row to avoid NULL grouping issues.
    # Fetch top 15 distinct carts (without database-specific string aggregation)
    raw_carts = db.session.query(
        Cart.user_id,
        Cart.session_id,
        func.sum(Cart.quantity).label('total_items'),
        func.max(Cart.added_at).label('last_active'),
        func.sum(Cart.quantity * db.cast(Product.price, db.Numeric)).label('total_value')
    ).join(Product, Cart.product_id == Product.id).group_by(
        Cart.user_id, Cart.session_id
    ).order_by(func.max(Cart.added_at).desc()).limit(100).all()

    active_carts_data = []
    active_user_ids = set()
    
    for uid, sid, qty, last_active, total_value in raw_carts:
        if uid:
            active_user_ids.add(uid)
        user = db.session.get(User, uid) if uid else None
        name = user.full_name if user else "Anonymous Visitor"
        telegram_username = user.telegram_username if user and user.telegram_username else None
        phone = user.phone if user and user.phone else None
        
        # Fetch detailed products for this specific cart safely
        if uid:
            cart_items = Cart.query.filter_by(user_id=uid).all()
        else:
            cart_items = Cart.query.filter_by(session_id=sid).all()
            
        products_detailed = []
        for item in cart_items:
            if item.product:
                products_detailed.append({
                    'name': item.product.name,
                    'price': float(item.product.price),
                    'qty': item.quantity,
                    'image': item.product.primary_image()
                })
        
        products_list = ", ".join(set([p['name'] for p in products_detailed]))
        
        if telegram_username:
            identifier = f"@{telegram_username}"
        elif user:
            identifier = f"User #{user.id}"
        else:
            identifier = f"Session …{sid[-6:] if sid else '???'}"
            
        if isinstance(last_active, str):
            try:
                from datetime import datetime as _dt
                last_active = _dt.fromisoformat(last_active.replace('Z', '+00:00'))
            except Exception:
                last_active = None
        last_str = last_active.strftime('%b %d, %H:%M') if last_active else '—'
        
        active_carts_data.append({
            'status': 'Live',
            'name': name,
            'identifier': identifier,
            'telegram_username': telegram_username,
            'telegram_id': user.telegram_id if user else None,
            'phone': phone,
            'total_items': int(qty or 0),
            'total': float(total_value or 0),
            'last_active': last_str,
            'products_list': products_list,
            'products_detailed': products_detailed
        })

    # ── Historical Abandoned Intents (ActivityLog) ──────────────────
    historical_logs = db.session.query(
        ActivityLog.user_id,
        ActivityLog.ip_address.label('session_id'),
        func.max(ActivityLog.created_at).label('last_active')
    ).filter(
        ActivityLog.action == 'add_to_cart'
    ).group_by(
        ActivityLog.user_id, ActivityLog.ip_address
    ).order_by(
        func.max(ActivityLog.created_at).desc()
    ).limit(50).all()

    uids = [h.user_id for h in historical_logs if h.user_id and h.user_id not in active_user_ids]
    sess_ids = [h.session_id for h in historical_logs if h.session_id]
    
    if uids or sess_ids:
        from sqlalchemy import or_
        logs_q = ActivityLog.query.filter(ActivityLog.action == 'add_to_cart')
        if uids and sess_ids:
            logs_q = logs_q.filter(or_(ActivityLog.user_id.in_(uids), ActivityLog.ip_address.in_(sess_ids)))
        elif uids:
            logs_q = logs_q.filter(ActivityLog.user_id.in_(uids))
        elif sess_ids:
            logs_q = logs_q.filter(ActivityLog.ip_address.in_(sess_ids))
            
        all_logs = logs_q.order_by(ActivityLog.created_at.desc()).all()
        
        # Preload users and products
        users_map = {u.id: u for u in User.query.filter(User.id.in_(uids)).all()}
        product_ids = list(set(log.entity_id for log in all_logs if log.entity_id))
        products_map = {p.id: p for p in Product.query.filter(Product.id.in_(product_ids)).all()}
        
        # Group logs by user/session
        logs_by_entity = {}
        for log in all_logs:
            key = f"user_{log.user_id}" if log.user_id else f"session_{log.ip_address}"
            if key not in logs_by_entity:
                logs_by_entity[key] = []
            if len(logs_by_entity[key]) < 20: # Limit to 20 products per cart
                logs_by_entity[key].append(log)
                
        for h in historical_logs:
            uid = h.user_id
            sess_id = h.session_id
            last_active = h.last_active
            
            if uid in active_user_ids:
                continue
                
            key = f"user_{uid}" if uid else f"session_{sess_id}"
            user_logs = logs_by_entity.get(key, [])
            if not user_logs:
                continue
                
            hist_products = {}
            for log in user_logs:
                if log.entity_id and log.entity_id not in hist_products:
                    prod = products_map.get(log.entity_id)
                    if prod:
                        qty_raw = log.get_meta().get('qty', 1) if log.meta else 1
                        try:
                            qty = int(qty_raw)
                        except:
                            qty = 1
                        hist_products[prod.id] = {
                            'name': prod.name,
                            'price': float(prod.price),
                            'qty': qty,
                            'image': prod.primary_image()
                        }
            
            if not hist_products:
                continue
                
            products_detailed = list(hist_products.values())
            products_list = ", ".join([p['name'] for p in products_detailed])
            total_val = sum([p['price'] * p['qty'] for p in products_detailed])
            total_items = sum([p['qty'] for p in products_detailed])
            
            user = users_map.get(uid)
            if user:
                name = user.full_name
                telegram_username = user.telegram_username
                phone = user.phone
                if telegram_username:
                    identifier = f"@{telegram_username}"
                else:
                    identifier = f"User #{user.id}"
            else:
                name = "Anonymous User"
                telegram_username = None
                phone = None
                identifier = f"Session: {sess_id[:8]}..." if sess_id else "Guest"
                
            if isinstance(last_active, str):
                try:
                    from datetime import datetime as _dt
                    last_active = _dt.fromisoformat(last_active.replace('Z', '+00:00'))
                except Exception:
                    last_active = None
            last_str = last_active.strftime('%b %d, %H:%M') if last_active else '—'
            
            active_carts_data.append({
                'status': 'Abandoned',
                'name': name,
                'identifier': identifier,
                'telegram_username': telegram_username,
                'telegram_id': user.telegram_id if user else None,
                'phone': phone,
                'total_items': int(total_items),
                'total': float(total_val),
                'last_active': last_str,
                'products_list': products_list,
                'products_detailed': products_detailed
            })
        
    # Sort the combined list by last_active
    def _parse_time(d):
        try:
            return datetime.strptime(d['last_active'], '%b %d, %H:%M')
        except:
            return datetime.min
    active_carts_data.sort(key=_parse_time, reverse=True)
    # limit to top 150 total
    active_carts_data = active_carts_data[:150]

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


# ── ANALYTICS API ENDPOINTS ──────────────────────────────────────────────────

def _analytics_date_range(period=None):
    """Return (start, end) UTC datetimes for a given period string."""
    local_tz = ZoneInfo('Africa/Addis_Ababa')
    now_local = datetime.now(local_tz)
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = datetime.now(timezone.utc)

    if period == 'today':
        start = today_start.astimezone(timezone.utc)
    elif period == 'yesterday':
        start = (today_start - timedelta(days=1)).astimezone(timezone.utc)
        end = today_start.astimezone(timezone.utc)
    elif period == '7d':
        start = (today_start - timedelta(days=7)).astimezone(timezone.utc)
    elif period == '30d':
        start = (today_start - timedelta(days=30)).astimezone(timezone.utc)
    elif period == '90d':
        start = (today_start - timedelta(days=90)).astimezone(timezone.utc)
    elif period == '1y':
        start = (today_start - timedelta(days=365)).astimezone(timezone.utc)
    else:
        start = (today_start - timedelta(days=30)).astimezone(timezone.utc)
    return start, end


def _prev_period_start(start, end):
    """Return the start of the equivalent previous period."""
    delta = end - start
    return start - delta


@admin_bp.route('/analytics/kpis')
@admin_required
def analytics_kpis():
    """JSON: KPI cards with period comparison."""
    period = request.args.get('period', '30d')
    start, end = _analytics_date_range(period)
    prev_start = _prev_period_start(start, end)

    def pct_change(curr, prev):
        if not prev:
            return None
        return round((curr - prev) / prev * 100, 1)

    # Revenue
    revenue = db.session.query(func.sum(Order.total)).filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).scalar() or 0
    prev_revenue = db.session.query(func.sum(Order.total)).filter(
        Order.created_at >= prev_start, Order.created_at < start,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).scalar() or 0

    # Orders
    orders = Order.query.filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).count()
    prev_orders = Order.query.filter(
        Order.created_at >= prev_start, Order.created_at < start,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).count()

    # AOV
    aov = float(revenue) / orders if orders else 0
    prev_aov = float(prev_revenue) / prev_orders if prev_orders else 0

    # Customers
    total_customers = User.query.filter_by(role=UserRole.customer).count()
    new_customers = User.query.filter(
        User.role == UserRole.customer,
        User.created_at >= start, User.created_at < end
    ).count()
    prev_new_customers = User.query.filter(
        User.role == UserRole.customer,
        User.created_at >= prev_start, User.created_at < start
    ).count()

    # Returning customers: those who had orders before the period and also in period
    returning_subq = db.session.query(Order.user_id).filter(
        Order.created_at < start,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).distinct().subquery()
    returning_customers = db.session.query(func.count(distinct(Order.user_id))).filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned]),
        Order.user_id.in_(db.session.query(returning_subq))
    ).scalar() or 0

    # Mini app visits
    visits = db.session.query(func.count(ActivityLog.id)).filter(
        ActivityLog.action == 'mini_app_visit',
        ActivityLog.created_at >= start, ActivityLog.created_at < end
    ).scalar() or 0
    prev_visits = db.session.query(func.count(ActivityLog.id)).filter(
        ActivityLog.action == 'mini_app_visit',
        ActivityLog.created_at >= prev_start, ActivityLog.created_at < start
    ).scalar() or 0

    # Cart adds
    cart_adds = db.session.query(func.count(ActivityLog.id)).filter(
        ActivityLog.action == 'add_to_cart',
        ActivityLog.created_at >= start, ActivityLog.created_at < end
    ).scalar() or 0

    # Product views
    product_views = db.session.query(func.count(ActivityLog.id)).filter(
        ActivityLog.action == 'view_product',
        ActivityLog.created_at >= start, ActivityLog.created_at < end
    ).scalar() or 0

    # Avg items per order
    avg_items_row = db.session.query(func.avg(Order.total_items)).filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned]),
        Order.total_items > 0
    ).scalar()
    avg_items = round(float(avg_items_row), 2) if avg_items_row else 0

    # Avg delivery fee
    avg_del_row = db.session.query(func.avg(Order.delivery_fee)).filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).scalar()
    avg_delivery_fee = round(float(avg_del_row), 0) if avg_del_row else 0

    # Cart abandonment: cart adds that didn't lead to an order in the period
    cart_abandonment_rate = 0
    if cart_adds > 0:
        cart_abandonment_rate = round((1 - min(orders / cart_adds, 1)) * 100, 1)

    # Repeat purchase rate
    customers_with_orders = db.session.query(func.count(distinct(Order.user_id))).filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).scalar() or 0
    repeat_rate = round(returning_customers / customers_with_orders * 100, 1) if customers_with_orders else 0

    # CLV: total revenue / total customers with orders
    clv = round(float(revenue) / customers_with_orders, 0) if customers_with_orders else 0

    return jsonify({
        'period': period,
        'revenue': {'value': float(revenue), 'prev': float(prev_revenue), 'change': pct_change(float(revenue), float(prev_revenue))},
        'orders': {'value': orders, 'prev': prev_orders, 'change': pct_change(orders, prev_orders)},
        'aov': {'value': round(aov, 0), 'prev': round(prev_aov, 0), 'change': pct_change(aov, prev_aov)},
        'total_customers': {'value': total_customers},
        'new_customers': {'value': new_customers, 'prev': prev_new_customers, 'change': pct_change(new_customers, prev_new_customers)},
        'returning_customers': {'value': returning_customers},
        'visits': {'value': visits, 'prev': prev_visits, 'change': pct_change(visits, prev_visits)},
        'product_views': {'value': product_views},
        'cart_adds': {'value': cart_adds},
        'cart_abandonment_rate': {'value': cart_abandonment_rate},
        'avg_items': {'value': avg_items},
        'avg_delivery_fee': {'value': avg_delivery_fee},
        'repeat_rate': {'value': repeat_rate},
        'clv': {'value': clv},
    })


@admin_bp.route('/analytics/funnel')
@admin_required
def analytics_funnel():
    """JSON: Sales funnel data."""
    period = request.args.get('period', '30d')
    start, end = _analytics_date_range(period)

    app_opens = db.session.query(func.count(ActivityLog.id)).filter(
        ActivityLog.action == 'mini_app_visit',
        ActivityLog.created_at >= start, ActivityLog.created_at < end
    ).scalar() or 0

    product_views = db.session.query(func.count(ActivityLog.id)).filter(
        ActivityLog.action == 'view_product',
        ActivityLog.created_at >= start, ActivityLog.created_at < end
    ).scalar() or 0

    cart_adds = db.session.query(func.count(ActivityLog.id)).filter(
        ActivityLog.action == 'add_to_cart',
        ActivityLog.created_at >= start, ActivityLog.created_at < end
    ).scalar() or 0

    checkouts_started = db.session.query(func.count(ActivityLog.id)).filter(
        ActivityLog.action == 'checkout_started',
        ActivityLog.created_at >= start, ActivityLog.created_at < end
    ).scalar() or 0
    # Fallback: count unique users who started checkout (orders placed)
    if checkouts_started == 0:
        checkouts_started = Order.query.filter(
            Order.created_at >= start, Order.created_at < end
        ).count()

    orders_placed = Order.query.filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).count()

    orders_delivered = Order.query.filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status == OrderStatus.delivered
    ).count()

    def safe_pct(a, b):
        if not b:
            return 0
        return round(a / b * 100, 1)

    def drop_pct(a, b):
        if not b:
            return 0
        return round((1 - a / b) * 100, 1)

    stages = [
        {'label': 'App Opened', 'count': app_opens, 'pct': 100, 'drop': 0},
        {'label': 'Viewed Product', 'count': product_views, 'pct': safe_pct(product_views, app_opens), 'drop': drop_pct(product_views, app_opens)},
        {'label': 'Added to Cart', 'count': cart_adds, 'pct': safe_pct(cart_adds, app_opens), 'drop': drop_pct(cart_adds, product_views)},
        {'label': 'Checkout Started', 'count': checkouts_started, 'pct': safe_pct(checkouts_started, app_opens), 'drop': drop_pct(checkouts_started, cart_adds)},
        {'label': 'Order Placed', 'count': orders_placed, 'pct': safe_pct(orders_placed, app_opens), 'drop': drop_pct(orders_placed, checkouts_started)},
        {'label': 'Delivered', 'count': orders_delivered, 'pct': safe_pct(orders_delivered, app_opens), 'drop': drop_pct(orders_delivered, orders_placed)},
    ]
    return jsonify({'stages': stages})


@admin_bp.route('/analytics/revenue')
@admin_required
def analytics_revenue():
    """JSON: Revenue chart data (daily, weekly, monthly breakdown)."""
    period = request.args.get('period', '30d')
    start, end = _analytics_date_range(period)
    local_tz = ZoneInfo('Africa/Addis_Ababa')

    # Daily revenue for the period
    from app.models.inventory import POSSale, POSSaleStatus
    days = []
    delta = end - start
    num_days = min(int(delta.total_seconds() / 86400), 90)
    today_start_local = datetime.now(local_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    for i in range(num_days - 1, -1, -1):
        day_local = today_start_local - timedelta(days=i)
        next_day_local = day_local + timedelta(days=1)
        day_utc = day_local.astimezone(timezone.utc)
        next_day_utc = next_day_local.astimezone(timezone.utc)
        if day_utc < start:
            continue

        rev = db.session.query(func.sum(Order.total)).filter(
            Order.created_at >= day_utc, Order.created_at < next_day_utc,
            Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
        ).scalar() or 0

        pos_rev = db.session.query(func.sum(POSSale.total)).filter(
            POSSale.created_at >= day_utc, POSSale.created_at < next_day_utc,
            POSSale.status == POSSaleStatus.completed
        ).scalar() or 0

        ord_cnt = Order.query.filter(
            Order.created_at >= day_utc, Order.created_at < next_day_utc,
            Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
        ).count()

        days.append({
            'date': day_local.strftime('%b %d'),
            'revenue': float(rev) + float(pos_rev),
            'orders': ord_cnt,
        })

    # Revenue by category
    cat_rev = db.session.query(
        Category.name,
        func.sum(OrderItem.price * OrderItem.quantity).label('rev')
    ).select_from(OrderItem
    ).join(Product, OrderItem.product_id == Product.id
    ).join(Category, Product.category_id == Category.id
    ).join(Order, OrderItem.order_id == Order.id
    ).filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).group_by(Category.name).order_by(func.sum(OrderItem.price * OrderItem.quantity).desc()).limit(8).all()

    # Revenue by product
    prod_rev = db.session.query(
        Product.name,
        func.sum(OrderItem.price * OrderItem.quantity).label('rev'),
        func.sum(OrderItem.quantity).label('qty')
    ).select_from(OrderItem
    ).join(Product, OrderItem.product_id == Product.id
    ).join(Order, OrderItem.order_id == Order.id
    ).filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).group_by(Product.name).order_by(func.sum(OrderItem.price * OrderItem.quantity).desc()).limit(10).all()

    return jsonify({
        'daily': days,
        'by_category': [{'name': r[0], 'revenue': float(r[1])} for r in cat_rev],
        'by_product': [{'name': r[0], 'revenue': float(r[1]), 'qty': int(r[2])} for r in prod_rev],
    })


@admin_bp.route('/analytics/products')
@admin_required
def analytics_products():
    """JSON: Product analytics."""
    period = request.args.get('period', '30d')
    start, end = _analytics_date_range(period)

    prods = db.session.query(
        Product.id,
        Product.name,
        Product.stock_qty,
        Product.price,
        Product.view_count,
        Product.sales_count,
        func.sum(OrderItem.quantity).label('period_qty'),
        func.sum(OrderItem.price * OrderItem.quantity).label('period_rev'),
        func.count(distinct(OrderItem.order_id)).label('period_orders'),
    ).outerjoin(
        OrderItem, and_(
            OrderItem.product_id == Product.id,
        )
    ).outerjoin(
        Order, and_(
            Order.id == OrderItem.order_id,
            Order.created_at >= start, Order.created_at < end,
            Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
        )
    ).filter(Product.is_active == True
    ).group_by(Product.id, Product.name, Product.stock_qty, Product.price,
               Product.view_count, Product.sales_count
    ).order_by(func.sum(OrderItem.price * OrderItem.quantity).desc().nullslast()
    ).limit(50).all()

    # Cart adds per product
    cart_by_product = {r[0]: r[1] for r in db.session.query(
        ActivityLog.entity_id, func.count(ActivityLog.id)
    ).filter(
        ActivityLog.action == 'add_to_cart',
        ActivityLog.created_at >= start, ActivityLog.created_at < end,
        ActivityLog.entity_id.isnot(None)
    ).group_by(ActivityLog.entity_id).all()}

    # Wishlist count per product
    from app.models.order import Wishlist
    wish_by_product = {r[0]: r[1] for r in db.session.query(
        Wishlist.product_id, func.count(Wishlist.id)
    ).group_by(Wishlist.product_id).all()}

    result = []
    for p in prods:
        period_qty = int(p.period_qty or 0)
        period_rev = float(p.period_rev or 0)
        period_orders = int(p.period_orders or 0)
        views = int(p.view_count or 0)
        cart_cnt = cart_by_product.get(p.id, 0)
        conv = round(period_orders / views * 100, 1) if views else 0
        result.append({
            'id': p.id,
            'name': p.name,
            'price': float(p.price),
            'stock': p.stock_qty,
            'views': views,
            'cart_adds': cart_cnt,
            'orders': period_orders,
            'qty_sold': period_qty,
            'revenue': period_rev,
            'conversion': conv,
            'wishlist': wish_by_product.get(p.id, 0),
            'low_stock': p.stock_qty < 5,
        })
    return jsonify({'products': result})


@admin_bp.route('/analytics/segments')
@admin_required
def analytics_segments():
    """JSON: Customer segmentation."""
    now_utc = datetime.now(timezone.utc)
    local_tz = ZoneInfo('Africa/Addis_Ababa')
    today_start = datetime.now(local_tz).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    d30 = today_start - timedelta(days=30)
    d90 = today_start - timedelta(days=90)
    d180 = today_start - timedelta(days=180)

    customers = User.query.filter_by(role=UserRole.customer).all()

    segments = {
        'VIP': [],
        'High Value': [],
        'Frequent Buyer': [],
        'Returning': [],
        'First Time Buyer': [],
        'Window Shopper': [],
        'Inactive': [],
        'Lost Customer': [],
    }

    for c in customers:
        spent = float(c.total_money_spent or 0)
        orders = int(c.total_orders or 0)
        last_purchase = c.last_purchase_date

        if spent >= 10000 and orders >= 5:
            segments['VIP'].append(c)
        elif spent >= 5000 or orders >= 3:
            segments['High Value'].append(c)
        elif orders >= 2 and last_purchase and last_purchase.replace(tzinfo=timezone.utc) >= d30:
            segments['Frequent Buyer'].append(c)
        elif orders >= 2:
            segments['Returning'].append(c)
        elif orders == 1 and last_purchase and last_purchase.replace(tzinfo=timezone.utc) >= d90:
            segments['First Time Buyer'].append(c)
        elif orders == 0:
            # Check if they visited app or viewed products
            activity_cnt = ActivityLog.query.filter_by(user_id=c.id).count()
            if activity_cnt > 0:
                segments['Window Shopper'].append(c)
        elif orders >= 1 and last_purchase and last_purchase.replace(tzinfo=timezone.utc) < d180:
            segments['Lost Customer'].append(c)
        elif last_purchase and last_purchase.replace(tzinfo=timezone.utc) < d90:
            segments['Inactive'].append(c)
        else:
            segments['Inactive'].append(c)

    result = []
    for seg_name, seg_customers in segments.items():
        if not seg_customers:
            result.append({'segment': seg_name, 'count': 0, 'revenue': 0, 'avg_spend': 0})
            continue
        total_rev = sum(float(c.total_money_spent or 0) for c in seg_customers)
        avg_spend = total_rev / len(seg_customers)
        result.append({
            'segment': seg_name,
            'count': len(seg_customers),
            'revenue': round(total_rev, 0),
            'avg_spend': round(avg_spend, 0),
        })

    return jsonify({'segments': result})


@admin_bp.route('/analytics/cohort')
@admin_required
def analytics_cohort():
    """JSON: Monthly cohort retention."""
    local_tz = ZoneInfo('Africa/Addis_Ababa')
    now_local = datetime.now(local_tz)
    months = []
    for i in range(5, -1, -1):
        # month start
        month_offset = now_local.month - i
        year_offset = now_local.year
        while month_offset <= 0:
            month_offset += 12
            year_offset -= 1
        months.append((year_offset, month_offset))

    cohort_data = []
    for cohort_year, cohort_month in months:
        # Users who joined in this month
        if cohort_month == 12:
            next_month_start = datetime(cohort_year + 1, 1, 1, tzinfo=local_tz).astimezone(timezone.utc)
        else:
            next_month_start = datetime(cohort_year, cohort_month + 1, 1, tzinfo=local_tz).astimezone(timezone.utc)
        cohort_start = datetime(cohort_year, cohort_month, 1, tzinfo=local_tz).astimezone(timezone.utc)

        cohort_users = User.query.filter(
            User.role == UserRole.customer,
            User.created_at >= cohort_start,
            User.created_at < next_month_start
        ).with_entities(User.id).all()
        cohort_ids = [u.id for u in cohort_users]
        cohort_size = len(cohort_ids)

        retention = []
        if cohort_ids:
            for offset in range(0, 5):
                # Calculate month for retention check
                ret_month = cohort_month + offset
                ret_year = cohort_year
                while ret_month > 12:
                    ret_month -= 12
                    ret_year += 1
                if ret_month == 12:
                    ret_end_month = 1
                    ret_end_year = ret_year + 1
                else:
                    ret_end_month = ret_month + 1
                    ret_end_year = ret_year

                ret_start = datetime(ret_year, ret_month, 1, tzinfo=local_tz).astimezone(timezone.utc)
                ret_end = datetime(ret_end_year, ret_end_month, 1, tzinfo=local_tz).astimezone(timezone.utc)

                active = db.session.query(func.count(distinct(Order.user_id))).filter(
                    Order.user_id.in_(cohort_ids),
                    Order.created_at >= ret_start,
                    Order.created_at < ret_end,
                    Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
                ).scalar() or 0

                pct = round(active / cohort_size * 100, 0) if cohort_size else 0
                retention.append(pct)

        cohort_data.append({
            'label': f"{cohort_year}-{cohort_month:02d}",
            'size': cohort_size,
            'retention': retention,
        })

    return jsonify({'cohorts': cohort_data})


@admin_bp.route('/analytics/geographic')
@admin_required
def analytics_geographic():
    """JSON: Orders by city/region."""
    period = request.args.get('period', '30d')
    start, end = _analytics_date_range(period)

    from app.models.order import Address
    city_data = db.session.query(
        Address.city,
        func.count(Order.id).label('orders'),
        func.sum(Order.total).label('revenue'),
        func.count(distinct(Order.user_id)).label('customers')
    ).join(Order, Order.address_id == Address.id
    ).filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).group_by(Address.city).order_by(func.count(Order.id).desc()).limit(20).all()

    region_data = db.session.query(
        Address.region,
        func.count(Order.id).label('orders'),
        func.sum(Order.total).label('revenue')
    ).join(Order, Order.address_id == Address.id
    ).filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned]),
        Address.region.isnot(None)
    ).group_by(Address.region).order_by(func.count(Order.id).desc()).limit(15).all()

    return jsonify({
        'cities': [{'city': r[0] or 'Unknown', 'orders': r[1], 'revenue': float(r[2] or 0), 'customers': r[3]} for r in city_data],
        'regions': [{'region': r[0] or 'Unknown', 'orders': r[1], 'revenue': float(r[2] or 0)} for r in region_data],
    })


@admin_bp.route('/analytics/insights')
@admin_required
def analytics_insights():
    """JSON: Auto-generated AI insights."""
    period = '30d'
    start, end = _analytics_date_range(period)
    prev_start = _prev_period_start(start, end)

    insights = []
    warnings = []
    recommendations = []

    # Revenue trend
    rev_curr = float(db.session.query(func.sum(Order.total)).filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).scalar() or 0)
    rev_prev = float(db.session.query(func.sum(Order.total)).filter(
        Order.created_at >= prev_start, Order.created_at < start,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).scalar() or 0)
    if rev_prev > 0:
        rev_change = round((rev_curr - rev_prev) / rev_prev * 100, 1)
        if rev_change > 0:
            insights.append(f"Revenue increased by {rev_change}% compared to the previous period.")
        elif rev_change < -10:
            warnings.append(f"Revenue dropped by {abs(rev_change)}% compared to previous period. Investigate now.")

    # Top growing category
    cat_curr = db.session.query(
        Category.name, func.sum(OrderItem.quantity).label('qty')
    ).join(Product, OrderItem.product_id == Product.id
    ).join(Category, Product.category_id == Category.id
    ).join(Order, OrderItem.order_id == Order.id
    ).filter(Order.created_at >= start, Order.created_at < end,
             Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).group_by(Category.name).order_by(func.sum(OrderItem.quantity).desc()).first()
    if cat_curr:
        insights.append(f"'{cat_curr.name}' is your top-selling category this period.")

    # Cart abandonment
    cart_adds = db.session.query(func.count(ActivityLog.id)).filter(
        ActivityLog.action == 'add_to_cart',
        ActivityLog.created_at >= start
    ).scalar() or 0
    orders_placed = Order.query.filter(
        Order.created_at >= start, Order.created_at < end,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).count()
    if cart_adds > 0:
        aband_rate = (1 - min(orders_placed / cart_adds, 1)) * 100
        if aband_rate > 60:
            warnings.append(f"Cart abandonment rate is {aband_rate:.0f}%. Consider follow-up messages after cart additions.")

    # Low stock products
    low_stock = Product.query.filter(
        Product.is_active == True,
        Product.stock_qty > 0,
        Product.stock_qty <= 5
    ).count()
    out_of_stock = Product.query.filter(
        Product.is_active == True,
        Product.stock_qty == 0
    ).count()
    if low_stock > 0:
        warnings.append(f"{low_stock} product(s) have low stock (≤5 units). Restock soon.")
    if out_of_stock > 0:
        warnings.append(f"{out_of_stock} product(s) are out of stock and may be losing sales.")

    # Best performing product
    best_prod = db.session.query(
        Product.name, func.sum(OrderItem.quantity).label('qty')
    ).join(OrderItem, Product.id == OrderItem.product_id
    ).join(Order, Order.id == OrderItem.order_id
    ).filter(Order.created_at >= start, Order.created_at < end,
             Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).group_by(Product.name).order_by(func.sum(OrderItem.quantity).desc()).first()
    if best_prod:
        recommendations.append(f"Feature '{best_prod.name}' in the Telegram channel — it's your top seller this period.")

    # New customers trend
    new_cust = User.query.filter(
        User.role == UserRole.customer, User.created_at >= start
    ).count()
    recommendations.append(f"{new_cust} new customers joined this period. Send a welcome discount to boost first purchase.")

    # Returning customer rate
    all_buying_cust = db.session.query(func.count(distinct(Order.user_id))).filter(
        Order.created_at >= start,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned])
    ).scalar() or 0
    ret_cust = db.session.query(func.count(distinct(Order.user_id))).filter(
        Order.created_at >= start,
        Order.status.notin_([OrderStatus.cancelled, OrderStatus.returned]),
        Order.user_id.in_(
            db.session.query(Order.user_id).filter(Order.created_at < start).distinct()
        )
    ).scalar() or 0
    if all_buying_cust > 0:
        ret_rate = round(ret_cust / all_buying_cust * 100, 1)
        if ret_rate < 20:
            recommendations.append(f"Returning customer rate is {ret_rate}%. Launch a loyalty promotion to bring customers back.")

    return jsonify({
        'insights': insights,
        'warnings': warnings,
        'recommendations': recommendations,
        'generated_at': datetime.now(timezone.utc).isoformat(),
    })


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
                # Support new multi-select (product_ids[]) and old single-select (product_id)
                raw_ids = request.form.getlist('product_ids[]') or [request.form.get('product_id', '')]
                product_ids = [_safe_int(v) for v in raw_ids if str(v).strip() and _safe_int(v)]
                product_ids = list(dict.fromkeys(product_ids))  # deduplicate, preserve order

                if not product_ids:
                    flash('Select at least one product for the channel post.', 'danger')
                    return render_template('admin/channel_posts.html', products=products, recent_posts=recent_posts, processed=processed, configured_tz=ADMIN_TZ)

                selected_products = [db.session.get(Product, pid) for pid in product_ids]
                selected_products = [p for p in selected_products if p]

                if not selected_products:
                    flash('Could not find the selected products. Please try again.', 'danger')
                    return render_template('admin/channel_posts.html', products=products, recent_posts=recent_posts, processed=processed, configured_tz=ADMIN_TZ)

                is_grouped = len(selected_products) > 1

                # Primary product (first selected) — kept for legacy single-product compatibility
                product = selected_products[0]
                post.product_id = product.id
                post.title = title or product.name

                # Handle custom cover image / video
                custom_photo_url = request.form.get('custom_photo_url', '').strip()
                custom_video_url = request.form.get('custom_video_url', '').strip()
                custom_photo = request.files.get('custom_photo')
                custom_video = request.files.get('custom_video')

                if custom_photo_url:
                    image_urls.append(custom_photo_url)
                elif custom_photo and custom_photo.filename and allowed_file(custom_photo.filename):
                    img_url = _upload_to_telegram(custom_photo)
                    if img_url:
                        image_urls.append(img_url)

                if custom_video_url:
                    image_urls.append(custom_video_url)
                elif custom_video and custom_video.filename:
                    vid_url = _upload_video_to_telegram(custom_video)
                    if vid_url:
                        image_urls.append(vid_url)

                if not image_urls:
                    image_urls = [product.primary_image()]

                if not caption:
                    post.caption = ''

                # Button URL is set after flush (need post.id for grouped posts)
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
            db.session.flush()  # get post.id

            # Save grouped products and set button URL
            if post_type == 'product':
                for idx, p in enumerate(selected_products):
                    db.session.execute(
                        __import__('sqlalchemy').text(
                            'INSERT OR IGNORE INTO channel_post_products (post_id, product_id, sort_order) VALUES (:post_id, :product_id, :sort_order)'
                        ),
                        {'post_id': post.id, 'product_id': p.id, 'sort_order': idx}
                    )
                if is_grouped:
                    post.button_url = _configured_telegram_mini_app_link(startapp=f'post__{post.id}')
                else:
                    post.button_url = _configured_telegram_mini_app_link(product_id=product.id)

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
                # Support new multi-select (product_ids[]) and old single-select (product_id)
                raw_ids = request.form.getlist('product_ids[]') or [request.form.get('product_id', '')]
                product_ids_edit = [_safe_int(v) for v in raw_ids if str(v).strip() and _safe_int(v)]
                product_ids_edit = list(dict.fromkeys(product_ids_edit))

                if not product_ids_edit:
                    flash('Select at least one product.', 'danger')
                    return render_template('admin/channel_post_edit.html', post=post, products=products, configured_tz=ADMIN_TZ)

                selected_products_edit = [db.session.get(Product, pid) for pid in product_ids_edit]
                selected_products_edit = [p for p in selected_products_edit if p]

                if not selected_products_edit:
                    flash('Could not find the selected products.', 'danger')
                    return render_template('admin/channel_post_edit.html', post=post, products=products, configured_tz=ADMIN_TZ)

                is_grouped_edit = len(selected_products_edit) > 1
                primary_product = selected_products_edit[0]
                post.product_id = primary_product.id

                # Update grouped_products: clear old, insert new
                import sqlalchemy as sa
                db.session.execute(
                    sa.text('DELETE FROM channel_post_products WHERE post_id = :post_id'),
                    {'post_id': post.id}
                )
                db.session.flush()
                for idx, p in enumerate(selected_products_edit):
                    db.session.execute(
                        sa.text('INSERT OR IGNORE INTO channel_post_products (post_id, product_id, sort_order) VALUES (:post_id, :product_id, :sort_order)'),
                        {'post_id': post.id, 'product_id': p.id, 'sort_order': idx}
                    )

                image_mode = request.form.get('product_image_mode', 'primary')
                if request.files.getlist('images'):
                    post.images.delete()
                    db.session.flush()
                if post.images.count() == 0:
                    image_urls_edit = primary_product.all_images() if image_mode == 'gallery' else [primary_product.primary_image()]
                    _save_post_images(post, image_urls_edit)
                if not post.caption:
                    post.caption = ''

                # Update button URL based on grouping
                if is_grouped_edit:
                    post.button_url = _configured_telegram_mini_app_link(startapp=f'post__{post.id}')
                else:
                    post.button_url = _configured_telegram_mini_app_link(product_id=primary_product.id)
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
