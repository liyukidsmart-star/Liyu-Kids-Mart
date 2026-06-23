from datetime import datetime, timezone
from flask import request
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity
from app.blueprints.api import api_bp
from app.extensions import db
from app.models.user import User, UserRole
from app.utils import success_response, error_response


@api_bp.route('/auth/register', methods=['POST'])
def api_register():
    data = request.get_json()
    if not data:
        return error_response('No data')
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    full_name = data.get('full_name', '').strip()
    if not full_name or not password:
        return error_response('Name and password required')
    if email and User.query.filter_by(email=email).first():
        return error_response('Email already registered')
    user = User(full_name=full_name, email=email or None,
                phone=data.get('phone', ''), role=UserRole.customer)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    token = create_access_token(identity=user.id)
    return success_response({'user': user.to_dict(), 'token': token}, 'Registered', 201)


@api_bp.route('/auth/login', methods=['POST'])
def api_login():
    data = request.get_json()
    if not data:
        return error_response('No data')
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return error_response('Invalid credentials', 401)
    user.last_login = datetime.now(timezone.utc)
    db.session.commit()
    token = create_access_token(identity=user.id)
    return success_response({'user': user.to_dict(), 'token': token})


@api_bp.route('/auth/telegram', methods=['POST'])
def telegram_auth():
    """Register or login via Telegram user data."""
    data = request.get_json()
    if not data or not data.get('telegram_id'):
        return error_response('telegram_id required')
    telegram_id = str(data['telegram_id'])
    user = User.query.filter_by(telegram_id=telegram_id).first()
    is_new = False
    if not user:
        user = User(
            telegram_id=telegram_id,
            telegram_username=data.get('telegram_username', ''),
            full_name=data.get('full_name', data.get('first_name', 'Telegram User')),
            role=UserRole.customer, is_active=True,
        )
        db.session.add(user)
        is_new = True
    else:
        user.telegram_username = data.get('telegram_username', user.telegram_username)
    user.last_login = datetime.now(timezone.utc)
    db.session.commit()
    token = create_access_token(identity=user.id)
    result = user.to_dict()
    result['is_new'] = is_new
    return success_response({'user': result, 'token': token})


@api_bp.route('/auth/me', methods=['GET'])
@jwt_required()
def get_me():
    uid = get_jwt_identity()
    user = db.session.get(User, uid)
    if not user:
        return error_response('User not found', 404)
    return success_response(user.to_dict())


@api_bp.route('/auth/me', methods=['PUT'])
@jwt_required()
def update_me():
    uid = get_jwt_identity()
    user = db.session.get(User, uid)
    if not user:
        return error_response('User not found', 404)
    data = request.get_json() or {}
    if 'full_name' in data:
        user.full_name = data['full_name'].strip()
    if 'phone' in data:
        user.phone = data['phone'].strip()
    if 'child_ages' in data:
        user.set_child_ages(data['child_ages'])
    db.session.commit()
    return success_response(user.to_dict(), 'Profile updated')
