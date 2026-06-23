import os
import uuid
import random
import json
from datetime import datetime, timezone
from flask import request, jsonify, current_app, session
from werkzeug.utils import secure_filename


def generate_order_number():
    year = datetime.now(timezone.utc).year
    random_part = random.randint(10000, 99999)
    return f'LKM-{year}-{random_part}'


def generate_session_id():
    return str(uuid.uuid4())


def allowed_file(filename):
    allowed = current_app.config.get('ALLOWED_EXTENSIONS', {'png', 'jpg', 'jpeg', 'gif', 'webp'})
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed


def save_uploaded_file(file, subfolder='products'):
    if not file or not allowed_file(file.filename):
        return None
    filename = secure_filename(file.filename)
    ext = filename.rsplit('.', 1)[1].lower()
    unique_name = f'{uuid.uuid4().hex}.{ext}'
    upload_dir = os.path.join(current_app.root_path, '..', current_app.config['UPLOAD_FOLDER'], subfolder)
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, unique_name)
    file.save(file_path)
    return f'/static/uploads/{subfolder}/{unique_name}'


def paginate_query(query, page=1, per_page=12):
    return query.paginate(page=page, per_page=per_page, error_out=False)


def format_price(amount):
    return f'ETB {float(amount):,.0f}'


def success_response(data=None, message='Success', status_code=200):
    return jsonify({'success': True, 'data': data, 'message': message}), status_code


def error_response(message='Error', status_code=400, errors=None):
    return jsonify({'success': False, 'message': message, 'errors': errors}), status_code


def get_or_create_session_id():
    if 'session_id' not in session:
        session['session_id'] = generate_session_id()
    return session['session_id']


def get_cart_count_for_session(session_id, user_id=None):
    from app.models.order import Cart
    from app.extensions import db
    q = Cart.query
    if user_id:
        q = q.filter_by(user_id=user_id)
    elif session_id:
        q = q.filter_by(session_id=session_id, user_id=None)
    else:
        return 0
    items = q.all()
    return sum(i.quantity for i in items)


def log_activity(user_id, action, entity_type=None, entity_id=None, meta=None):
    try:
        from app.models.ai_conversation import ActivityLog
        from app.extensions import db
        log = ActivityLog(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            meta=json.dumps(meta) if meta else None,
            ip_address=request.remote_addr if request else None,
            user_agent=request.user_agent.string[:255] if request and request.user_agent else None,
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        pass  # Never crash the app over logging


def months_to_age_label(months):
    if months is None:
        return ''
    if months < 12:
        return f'{months} months'
    years = months // 12
    rem = months % 12
    if rem:
        return f'{years} yr {rem} mo'
    return f'{years} year{"s" if years != 1 else ""}'
