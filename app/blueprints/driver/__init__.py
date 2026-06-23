from flask import Blueprint
driver_bp = Blueprint('driver', __name__, template_folder='templates')
from app.blueprints.driver import routes  # noqa
