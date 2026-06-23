from flask import Blueprint
telegram_bp = Blueprint('telegram', __name__, template_folder='templates')
from app.blueprints.telegram_webhook import routes  # noqa
