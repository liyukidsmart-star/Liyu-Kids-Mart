from flask import Blueprint
shop_bp = Blueprint('shop', __name__, template_folder='templates')
from app.blueprints.shop import routes  # noqa
