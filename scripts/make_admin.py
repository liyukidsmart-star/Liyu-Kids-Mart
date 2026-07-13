from app import create_app
from app.extensions import db
from app.models.user import User, UserRole

app = create_app()
with app.app_context():
    u = User.query.filter_by(telegram_id='403612118').first()
    if not u:
        u = User(telegram_id='403612118', role=UserRole.admin, full_name='Admin 403612118')
        db.session.add(u)
        print("Created new user with telegram_id 403612118 as admin.")
    else:
        u.role = UserRole.admin
        print("Updated existing user with telegram_id 403612118 to admin.")
    db.session.commit()
    print("Done.")
