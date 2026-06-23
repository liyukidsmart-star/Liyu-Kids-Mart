from functools import wraps
from flask import redirect, url_for, flash, request, jsonify
from flask_login import current_user


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login', next=request.url))
        if current_user.role.value not in ('admin', 'manager'):
            flash('Access denied. Admins only.', 'danger')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated


def driver_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login', next=request.url))
        if current_user.role.value not in ('driver', 'admin'):
            flash('Access denied. Drivers only.', 'danger')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated


def api_admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity
        from app.models.user import User
        from app.extensions import db
        try:
            verify_jwt_in_request()
            uid = get_jwt_identity()
            user = db.session.get(User, uid)
            if not user or user.role.value not in ('admin', 'manager'):
                return jsonify({'success': False, 'message': 'Admin access required'}), 403
        except Exception:
            return jsonify({'success': False, 'message': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated


def login_required_api(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        from flask_jwt_extended import verify_jwt_in_request
        try:
            verify_jwt_in_request(optional=True)
        except Exception:
            return jsonify({'success': False, 'message': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated
