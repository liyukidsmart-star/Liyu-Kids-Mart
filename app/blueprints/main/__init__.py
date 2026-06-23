from flask import Blueprint
main_bp = Blueprint('main', __name__, template_folder='templates')
from app.blueprints.main import routes  # noqa
