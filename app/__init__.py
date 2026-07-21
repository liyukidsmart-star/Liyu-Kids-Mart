import os
from flask import Flask
from dotenv import load_dotenv
from app.config import config
from app.extensions import db, migrate, jwt, mail, cors, login_manager

load_dotenv(override=True)


def create_app(config_name='development'):
    app = Flask(__name__, template_folder='templates', static_folder='../static')
    app.config.from_object(config[config_name])

    # Init extensions
    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    mail.init_app(app)
    cors.init_app(app, resources={r"/api/*": {"origins": "*"}, r"/media/*": {"origins": "*"}})
    login_manager.init_app(app)

    # Ensure upload folder exists (catch errors on read-only serverless filesystems)
    try:
        upload_folder = os.path.join(app.root_path, '..', app.config['UPLOAD_FOLDER'])
        os.makedirs(upload_folder, exist_ok=True)
        os.makedirs(os.path.join(upload_folder, 'products'), exist_ok=True)
        os.makedirs(os.path.join(upload_folder, 'drivers'), exist_ok=True)
    except OSError:
        pass

    # Register blueprints
    from app.models import marketing as _marketing  # noqa: F401
    from app.blueprints.main import main_bp
    from app.blueprints.auth import auth_bp
    from app.blueprints.shop import shop_bp
    from app.blueprints.cart import cart_bp
    from app.blueprints.orders import orders_bp
    from app.blueprints.admin import admin_bp
    from app.blueprints.driver import driver_bp
    from app.blueprints.api import api_bp
    from app.blueprints.telegram_webhook import telegram_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(shop_bp, url_prefix='/shop')
    app.register_blueprint(cart_bp, url_prefix='/cart')
    app.register_blueprint(orders_bp, url_prefix='/orders')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(driver_bp, url_prefix='/driver')
    app.register_blueprint(api_bp, url_prefix='/api/v1')
    app.register_blueprint(telegram_bp, url_prefix='/telegram')

    # Ensure newly added tables exist in deployed environments.
    if os.getenv('AUTO_CREATE_TABLES', 'true').lower() in ('1', 'true', 'yes'):
        try:
            with app.app_context():
                # Apply Alembic migrations automatically (adds missing columns to Supabase)
                try:
                    from flask_migrate import upgrade
                    upgrade()
                except Exception as e:
                    app.logger.warning(f'Auto-migration failed: {e}')

                db.create_all()
                # Seed loyalty defaults on first boot (no-op if already seeded)
                try:
                    from app.services.loyalty_service import seed_default_loyalty_data
                    seed_default_loyalty_data()
                except Exception:
                    app.logger.exception('Loyalty seed failed')
        except Exception:
            app.logger.exception('Automatic table creation failed')

    # User loader for Flask-Login
    from app.models.user import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # JWT user lookup
    @jwt.user_identity_loader
    def user_identity_lookup(user):
        return user.id if hasattr(user, 'id') else user

    @jwt.user_lookup_loader
    def user_lookup_callback(_jwt_header, jwt_data):
        identity = jwt_data["sub"]
        return db.session.get(User, identity)

    return app
