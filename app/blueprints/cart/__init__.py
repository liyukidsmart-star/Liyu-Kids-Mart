from flask import Blueprint
cart_bp = Blueprint('cart', __name__, template_folder='templates')
from app.blueprints.cart import routes  # noqa
