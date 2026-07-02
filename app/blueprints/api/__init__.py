from flask import Blueprint

api_bp = Blueprint('api', __name__)

from app.blueprints.api import products, orders, cart, users, ai, delivery, mini_app, driver_app, loyalty  # noqa
