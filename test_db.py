from app import create_app
from app.extensions import db
from app.models.order import Order, OrderStatus
from sqlalchemy import func, cast, Date
from datetime import datetime, timezone, timedelta
app = create_app()
with app.app_context():
    print("Testing aggregate...")
    thirty_days_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    try:
        res = db.session.query(
            cast(Order.created_at, Date).label('day'),
            func.count(Order.id).label('orders'),
            func.sum(Order.discount_amount).label('disc_total')
        ).filter(
            Order.status == OrderStatus.delivered,
            Order.created_at >= thirty_days_ago
        ).group_by(cast(Order.created_at, Date)).all()
        print("Aggregate success:", len(res))
    except Exception as e:
        print("Error:", e)
