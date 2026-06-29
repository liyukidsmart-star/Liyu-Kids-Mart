import os
from datetime import timedelta
from dotenv import load_dotenv
from sqlalchemy.pool import NullPool

load_dotenv(override=True)


class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'jwt-secret-change-me')
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=30)
    JWT_TOKEN_LOCATION = ['headers', 'cookies']
    JWT_COOKIE_SECURE = False
    JWT_COOKIE_CSRF_PROTECT = False

    MAIL_SERVER = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.getenv('MAIL_PORT', 587))
    MAIL_USE_TLS = os.getenv('MAIL_USE_TLS', 'True') == 'True'
    MAIL_USERNAME = os.getenv('MAIL_USERNAME', '')
    MAIL_PASSWORD = os.getenv('MAIL_PASSWORD', '')
    MAIL_DEFAULT_SENDER = os.getenv('MAIL_USERNAME', 'noreply@liyukids.com')

    UPLOAD_FOLDER = os.getenv('UPLOAD_FOLDER', 'static/uploads')
    MAX_CONTENT_LENGTH = int(os.getenv('MAX_CONTENT_LENGTH', 16 * 1024 * 1024))
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

    GROK_API_KEY = os.getenv('GROK_API_KEY', '')
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
    TELEGRAM_WEBHOOK_URL = os.getenv('TELEGRAM_WEBHOOK_URL', '')
    ADMIN_TELEGRAM_CHAT_ID = os.getenv('ADMIN_TELEGRAM_CHAT_ID', '')
    TELEGRAM_MEDIA_CHAT_ID = os.getenv('TELEGRAM_MEDIA_CHAT_ID', '')
    TELEGRAM_MAIN_CHANNEL_ID = os.getenv('TELEGRAM_MAIN_CHANNEL_ID', '')
    TELEGRAM_CHANNEL_CHAT_ID = os.getenv('TELEGRAM_CHANNEL_CHAT_ID', '')

    APP_URL = os.getenv('APP_URL', 'http://localhost:5000')
    MINI_APP_URL = os.getenv('MINI_APP_URL', 'http://localhost:5000/mini-app')

    WTF_CSRF_ENABLED = False  # Disable for API compatibility; re-enable per-form if needed

    CORS_ORIGINS = ['*']


    db_url = os.getenv('DATABASE_URL', 'sqlite:///liyu_kids.db')
    if db_url and db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
        
    SQLALCHEMY_DATABASE_URI = db_url

    # For serverless environments like Vercel, we MUST use NullPool.
    # Otherwise, sleeping serverless instances hold onto database connections
    # and quickly exhaust the Supabase connection limit (15).
    SQLALCHEMY_ENGINE_OPTIONS = {
        'poolclass': NullPool,
        'pool_pre_ping': True
    }

class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_ECHO = False

class ProductionConfig(Config):
    DEBUG = False
    JWT_COOKIE_SECURE = True
    WTF_CSRF_ENABLED = True


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig,
}
