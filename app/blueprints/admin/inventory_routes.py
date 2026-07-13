"""
Admin Inventory & POS Routes — Liyu Kids Mart
Handles stock management, barcode printing, and POS terminal views.
"""
import json
import random
from datetime import datetime, timezone, timedelta
from flask import render_template, redirect, url_for, flash, request, jsonify, current_app
from flask_login import login_required, current_user
from functools import wraps

from app.blueprints.admin import admin_bp
from app.extensions import db
from app.models.product import Product, Category, prime_product_image_lookup
from app.models.inventory import (
    StockTransaction, StockTransactionType, POSSale, POSSaleItem, POSSaleStatus
)


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.role.value not in ('admin', 'manager'):
            flash('Access denied.', 'danger')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated


def _generate_pos_number():
    """Generate a unique POS sale number."""
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime('%Y%m%d')
    rand = random.randint(1000, 9999)
    return f'POS-{ts}-{rand}'


# ─────────────────────────────────────────────────────────────
# INVENTORY — Stock Management Page
# ─────────────────────────────────────────────────────────────

@admin_bp.route('/inventory')
@admin_required
def inventory_stock():
    """Main inventory / stock management page."""
    q = request.args.get('q', '').strip()
    category_id = request.args.get('category_id', '', type=str)
    stock_filter = request.args.get('stock_filter', '')  # low, out, all

    query = Product.query

    if q:
        query = query.filter(Product.name.ilike(f'%{q}%'))
    if category_id and category_id.isdigit():
        query = query.filter(Product.category_id == int(category_id))
    if stock_filter == 'low':
        query = query.filter(Product.stock_qty > 0, Product.stock_qty <= 10)
    elif stock_filter == 'out':
        query = query.filter(Product.stock_qty <= 0)

    page = request.args.get('page', 1, type=int)
    pagination = query.order_by(Product.stock_qty.asc()).paginate(page=page, per_page=20, error_out=False)

    prime_product_image_lookup(pagination.items)

    # Stats
    total_products = Product.query.count()
    out_of_stock = Product.query.filter(Product.stock_qty <= 0).count()
    low_stock = Product.query.filter(Product.stock_qty > 0, Product.stock_qty <= 10).count()
    total_stock_value = db.session.query(
        db.func.sum(Product.price * Product.stock_qty)
    ).filter(Product.is_active == True).scalar() or 0  # noqa

    categories = Category.query.filter_by(is_active=True).order_by(Category.name).all()

    # Recent transactions
    recent_transactions = StockTransaction.query.order_by(
        StockTransaction.created_at.desc()
    ).limit(15).all()

    return render_template(
        'admin/inventory/stock.html',
        products=pagination.items,
        pagination=pagination,
        categories=categories,
        q=q,
        category_id=category_id,
        stock_filter=stock_filter,
        total_products=total_products,
        out_of_stock=out_of_stock,
        low_stock=low_stock,
        total_stock_value=float(total_stock_value),
        recent_transactions=recent_transactions,
    )


@admin_bp.route('/inventory/adjust-stock', methods=['POST'])
@admin_required
def inventory_adjust_stock():
    """Adjust the stock of a product (add/subtract)."""
    data = request.get_json(silent=True) or {}
    product_id = data.get('product_id')
    change = data.get('change')
    notes = data.get('notes', '').strip()

    if not product_id or change is None:
        return jsonify({'success': False, 'message': 'product_id and change are required'}), 400

    product = db.session.get(Product, product_id)
    if not product:
        return jsonify({'success': False, 'message': 'Product not found'}), 404

    change = int(change)
    before = product.stock_qty
    product.stock_qty = max(0, before + change)
    after = product.stock_qty

    txn = StockTransaction(
        product_id=product.id,
        transaction_type=StockTransactionType.adjustment,
        quantity_change=change,
        quantity_before=before,
        quantity_after=after,
        notes=notes or f'Manual adjustment by {current_user.full_name}',
        created_by_id=current_user.id,
    )
    db.session.add(txn)
    db.session.commit()

    return jsonify({'success': True, 'new_stock': product.stock_qty, 'product_name': product.name})


@admin_bp.route('/inventory/set-stock', methods=['POST'])
@admin_required
def inventory_set_stock():
    """Directly set a product's stock quantity."""
    data = request.get_json(silent=True) or {}
    product_id = data.get('product_id')
    new_qty = data.get('qty')

    if product_id is None or new_qty is None:
        return jsonify({'success': False, 'message': 'product_id and qty required'}), 400

    product = db.session.get(Product, product_id)
    if not product:
        return jsonify({'success': False, 'message': 'Product not found'}), 404

    before = product.stock_qty
    product.stock_qty = max(0, int(new_qty))
    after = product.stock_qty

    txn = StockTransaction(
        product_id=product.id,
        transaction_type=StockTransactionType.restock if after > before else StockTransactionType.adjustment,
        quantity_change=after - before,
        quantity_before=before,
        quantity_after=after,
        notes=f'Stock set to {after} by {current_user.full_name}',
        created_by_id=current_user.id,
    )
    db.session.add(txn)
    db.session.commit()

    return jsonify({'success': True, 'new_stock': product.stock_qty})


# ─────────────────────────────────────────────────────────────
# BARCODE / QR PRINTING
# ─────────────────────────────────────────────────────────────

@admin_bp.route('/inventory/print-barcodes')
@admin_required
def inventory_print_barcodes():
    """Barcode / QR code label printing page."""
    product_id = request.args.get('product_id', type=int)
    products = Product.query.filter_by(is_active=True).order_by(Product.name).all()

    selected_product = None
    if product_id:
        selected_product = db.session.get(Product, product_id)

    prime_product_image_lookup(products)

    # Build the mini-app QR URL base
    mini_app_url = current_app.config.get('MINI_APP_URL', '')
    bot_username = current_app.config.get('TELEGRAM_BOT_USERNAME', 'Liyu_Kids_Mart_Bot')
    short_name = current_app.config.get('TELEGRAM_MINI_APP_SHORT_NAME', '')

    return render_template(
        'admin/inventory/print_barcodes.html',
        products=products,
        selected_product=selected_product,
        mini_app_url=mini_app_url,
        bot_username=bot_username,
        short_name=short_name,
    )


# ─────────────────────────────────────────────────────────────
# POS TERMINAL
# ─────────────────────────────────────────────────────────────

@admin_bp.route('/inventory/pos')
@admin_required
def inventory_pos():
    """POS Terminal view for in-store barcode scanning checkout."""
    # Today's POS sales
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_sales = POSSale.query.filter(POSSale.created_at >= today_start).all()
    today_revenue = sum(float(s.total) for s in today_sales if s.status == POSSaleStatus.completed)
    today_count = len([s for s in today_sales if s.status == POSSaleStatus.completed])

    recent_sales = POSSale.query.order_by(POSSale.created_at.desc()).limit(10).all()

    return render_template(
        'admin/inventory/pos.html',
        today_revenue=today_revenue,
        today_count=today_count,
        recent_sales=recent_sales,
    )


@admin_bp.route('/inventory/pos/lookup-product', methods=['GET'])
@admin_required
def pos_lookup_product():
    """Quick product lookup by id or SKU (for POS barcode scan)."""
    product_id = request.args.get('id', type=int)
    sku = request.args.get('sku', '').strip()

    product = None
    if product_id:
        product = db.session.get(Product, product_id)
    elif sku:
        product = Product.query.filter_by(sku=sku).first()
        if not product:
            # Try numeric ID embedded in the QR URL
            if sku.isdigit():
                product = db.session.get(Product, int(sku))

    if not product or not product.is_active:
        return jsonify({'success': False, 'message': 'Product not found'}), 404

    return jsonify({
        'success': True,
        'product': {
            'id': product.id,
            'name': product.name,
            'price': float(product.price),
            'stock': product.stock_qty,
            'image': product.primary_image(),
            'sku': product.sku or str(product.id),
            'category': product.category.name if product.category else '',
        }
    })


@admin_bp.route('/inventory/pos/checkout', methods=['POST'])
@admin_required
def pos_checkout():
    """
    Process a POS checkout.
    Expects JSON:
    {
        items: [{product_id, quantity, unit_price}],
        discount_percentage: 0-100,
        payment_method: optional string,
        notes: optional string
    }
    """
    data = request.get_json(silent=True) or {}
    items_data = data.get('items', [])
    discount_pct = float(data.get('discount_percentage', 0))
    payment_method = data.get('payment_method', '') or ''
    notes = data.get('notes', '') or ''

    if not items_data:
        return jsonify({'success': False, 'message': 'No items in cart'}), 400

    if discount_pct < 0 or discount_pct > 100:
        return jsonify({'success': False, 'message': 'Discount must be between 0 and 100'}), 400

    # Validate and load products
    line_items = []
    for item in items_data:
        pid = item.get('product_id')
        qty = int(item.get('quantity', 1))
        if qty < 1:
            continue
        product = db.session.get(Product, pid)
        if not product or not product.is_active:
            return jsonify({'success': False, 'message': f'Product {pid} not found or inactive'}), 404
        if product.stock_qty < qty:
            return jsonify({
                'success': False,
                'message': f'Insufficient stock for "{product.name}". Available: {product.stock_qty}'
            }), 400
        line_items.append({'product': product, 'qty': qty, 'unit_price': float(product.price)})

    if not line_items:
        return jsonify({'success': False, 'message': 'No valid items to checkout'}), 400

    # Calculate totals
    subtotal = sum(li['qty'] * li['unit_price'] for li in line_items)
    discount_amount = round(subtotal * discount_pct / 100, 2)
    total = round(subtotal - discount_amount, 2)

    # Create the POS Sale
    sale_number = _generate_pos_number()
    # Avoid collision
    while POSSale.query.filter_by(sale_number=sale_number).first():
        sale_number = _generate_pos_number()

    sale = POSSale(
        sale_number=sale_number,
        cashier_id=current_user.id,
        status=POSSaleStatus.completed,
        subtotal=subtotal,
        discount_percentage=discount_pct,
        discount_amount=discount_amount,
        total=total,
        payment_method=payment_method if payment_method else None,
        notes=notes if notes else None,
    )
    db.session.add(sale)
    db.session.flush()  # get sale.id

    # Create line items & decrement stock
    items_snapshot = []
    for li in line_items:
        product = li['product']
        qty = li['qty']
        unit_price = li['unit_price']
        total_price = round(qty * unit_price, 2)

        sale_item = POSSaleItem(
            sale_id=sale.id,
            product_id=product.id,
            quantity=qty,
            unit_price=unit_price,
            total_price=total_price,
            product_snapshot=json.dumps({
                'name': product.name,
                'image': product.primary_image(),
                'sku': product.sku or str(product.id),
            }),
        )
        db.session.add(sale_item)

        # Decrement stock
        before = product.stock_qty
        product.stock_qty = max(0, before - qty)
        after = product.stock_qty

        txn = StockTransaction(
            product_id=product.id,
            transaction_type=StockTransactionType.pos_sale,
            quantity_change=-qty,
            quantity_before=before,
            quantity_after=after,
            reference_id=sale_number,
            notes=f'POS Sale {sale_number}',
            created_by_id=current_user.id,
        )
        db.session.add(txn)

        items_snapshot.append({
            'name': product.name,
            'qty': qty,
            'unit_price': unit_price,
            'total': total_price,
        })

    sale.items_snapshot = json.dumps(items_snapshot)
    db.session.commit()

    return jsonify({
        'success': True,
        'sale_number': sale_number,
        'subtotal': subtotal,
        'discount_amount': discount_amount,
        'discount_percentage': discount_pct,
        'total': total,
        'items_count': len(line_items),
        'message': f'Sale {sale_number} completed successfully!',
    })


# ─────────────────────────────────────────────────────────────
# POS SALES HISTORY
# ─────────────────────────────────────────────────────────────

@admin_bp.route('/inventory/pos/history')
@admin_required
def pos_history():
    """POS Sales history page."""
    page = request.args.get('page', 1, type=int)
    pagination = POSSale.query.order_by(POSSale.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )

    # Summary stats
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)

    today_revenue = db.session.query(db.func.sum(POSSale.total)).filter(
        POSSale.created_at >= today_start,
        POSSale.status == POSSaleStatus.completed
    ).scalar() or 0

    week_revenue = db.session.query(db.func.sum(POSSale.total)).filter(
        POSSale.created_at >= week_start,
        POSSale.status == POSSaleStatus.completed
    ).scalar() or 0

    total_sales = POSSale.query.filter_by(status=POSSaleStatus.completed).count()

    return render_template(
        'admin/inventory/pos_history.html',
        sales=pagination.items,
        pagination=pagination,
        today_revenue=float(today_revenue),
        week_revenue=float(week_revenue),
        total_sales=total_sales,
    )
