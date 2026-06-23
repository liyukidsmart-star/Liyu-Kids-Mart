import json
from flask import render_template, redirect, url_for, flash, request, session
from flask_login import login_user, logout_user, login_required, current_user
from app.blueprints.auth import auth_bp
from app.extensions import db
from app.models.user import User, UserRole


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            if not user.is_active:
                flash('Your account is deactivated. Please contact support.', 'danger')
                return redirect(url_for('auth.login'))
            login_user(user, remember=True)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('main.index'))
        flash('Invalid email or password.', 'danger')
    return render_template('auth/login.html')


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').strip().lower() or None
        phone = request.form.get('phone', '').strip() or None
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        child_ages = [int(a) for a in request.form.getlist('child_ages')]

        if not full_name:
            flash('Name is required.', 'danger')
            return render_template('auth/register.html')
        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'danger')
            return render_template('auth/register.html')
        if password != confirm:
            flash('Passwords do not match.', 'danger')
            return render_template('auth/register.html')
        if email and User.query.filter_by(email=email).first():
            flash('An account with this email already exists.', 'danger')
            return render_template('auth/register.html')

        user = User(full_name=full_name, email=email, phone=phone,
                    role=UserRole.customer, is_active=True, is_verified=False)
        user.set_password(password)
        user.set_child_ages(child_ages)
        db.session.add(user)
        db.session.commit()
        login_user(user, remember=True)
        flash(f'Welcome, {full_name}! Your account has been created.', 'success')
        return redirect(url_for('main.index'))
    return render_template('auth/register.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('main.index'))


@auth_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        current_user.full_name = request.form.get('full_name', current_user.full_name).strip()
        current_user.phone = request.form.get('phone', '').strip() or None
        child_ages = [int(a) for a in request.form.getlist('child_ages')]
        current_user.set_child_ages(child_ages)
        db.session.commit()
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('auth.profile'))
    return render_template('auth/profile.html')


@auth_bp.route('/telegram-login', methods=['POST'])
def telegram_login():
    """Handle Telegram Mini App authentication."""
    data = request.get_json() or {}
    telegram_id = str(data.get('telegram_id', ''))
    if not telegram_id:
        return {'success': False, 'message': 'No telegram_id'}, 400
    user = User.query.filter_by(telegram_id=telegram_id).first()
    if not user:
        user = User(
            telegram_id=telegram_id,
            telegram_username=data.get('telegram_username', ''),
            full_name=data.get('full_name', 'Telegram User'),
            role=UserRole.customer, is_active=True,
        )
        db.session.add(user)
        db.session.commit()
    login_user(user, remember=True)
    return {'success': True, 'redirect': url_for('main.index')}
