from flask import Blueprint
admin_bp = Blueprint('admin', __name__, template_folder='templates')
from app.blueprints.admin import routes  # noqa
from app.blueprints.admin import loyalty_routes  # noqa
from app.blueprints.admin import inventory_routes  # noqa
