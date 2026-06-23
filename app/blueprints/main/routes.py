from flask import render_template, request
from app.blueprints.main import main_bp
from app.models.product import Product, Category
from app.models.order import Order


@main_bp.route('/')
def index():
    try:
        featured = Product.query.filter_by(is_active=True, is_featured=True).limit(8).all()
        new_arrivals = Product.query.filter_by(is_active=True, is_new_arrival=True).limit(10).all()
        best_sellers = Product.query.filter_by(is_active=True).order_by(Product.sales_count.desc()).limit(8).all()
        categories = Category.query.filter_by(is_active=True, parent_id=None).order_by(Category.sort_order).all()
        return render_template('main/index.html',
                               featured=featured,
                               new_arrivals=new_arrivals,
                               best_sellers=best_sellers,
                               categories=categories,
                               reviews=[])
    except Exception as e:
        return f"<h1>Database Error</h1><p>{str(e)}</p><p>If you haven't initialized your database tables yet, please go to <a href='/init-db'>/init-db</a></p>", 500

@main_bp.route('/init-db')
def init_db():
    from app.extensions import db
    try:
        db.create_all()
        return "<h1>Success!</h1><p>Database tables created successfully! You can now <a href='/'>return to the homepage</a>.</p>", 200
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
