import os
import json
import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flask import (render_template, redirect, url_for, flash, request,
                   jsonify, current_app)
import httpx
from flask_login import login_required, current_user
from functools import wraps
from app.blueprints.admin import admin_bp
from app.extensions import db
from app.models.product import Product, Category, ProductImage, prime_product_image_lookup
from app.models.order import Order, OrderStatus, Coupon, DiscountType
from app.models.marketing import ProductDiscount, TelegramChannelPost, TelegramChannelPostImage
from app.services.telegram_marketing import publish_channel_post, _telegram_mini_app_link, channel_button_link_mode
from app.services.image_delivery import media_url_for_file_id
from app.models.user import User, UserRole
from app.models.delivery import Driver
from app.models.ai_conversation import AIConversation
from app.utils import allowed_file
from slugify import slugify
from slugify import slugify

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
    prime_product_image_lookup(pagination.items)
    return render_template('admin/products.html', products=pagination.items,
                           pagination=pagination, q=q)


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

ADMIN_TZ = ZoneInfo("Africa/Addis_Ababa")


def _configured_mini_app_url():
    return current_app.config.get('MINI_APP_URL') or os.environ.get('MINI_APP_URL', '').strip() or 'http://localhost:5000/mini-app'



def _configured_telegram_mini_app_link(*, tab: str = 'home', query: str = '', startapp: str = ''):
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
                image_mode = request.form.get('product_image_mode', 'primary')
                if image_mode == 'gallery':
                    image_urls = product.all_images()
                else:
                    image_urls = [product.primary_image()]
                if not caption:
                    post.caption = ''
            else:
                uploaded = request.files.getlist('images')
                if uploaded:
                    for img_file in uploaded:
                        if img_file and img_file.filename and allowed_file(img_file.filename):
                            img_url = _upload_to_telegram(img_file)
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
