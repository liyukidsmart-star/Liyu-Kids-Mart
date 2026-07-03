from dotenv import load_dotenv
load_dotenv()

from app import create_app
from app.extensions import db
from app.models.user import User
from app.models.product import Product
from app.models.order import Order, Address
import httpx
import logging

def test_notify():
    app = create_app()
    with app.app_context():
        # Get latest order
        order = Order.query.order_by(Order.id.desc()).first()
        if not order:
            print("No order found")
            return
            
        addr = Address.query.filter_by(id=order.address_id).first()
        if not addr:
            addr = Address(phone="0911000000", specific_location="Test")
            
        order_items = []
        for item in order.items:
            order_items.append({
                'product': item.product,
                'qty': item.quantity,
                'unit_price': float(item.unit_price),
                'item_total': float(item.unit_price) * item.quantity
            })
            
        # Commit to simulate checkout
        db.session.commit()
        
        # Now call notify
        from app.blueprints.api.mini_app import _notify_store_managers
        
        try:
            _notify_store_managers(order, order_items, addr, 'telebirr', 0, '')
            print("Successfully notified!")
        except Exception as e:
            import traceback
            traceback.print_exc()

if __name__ == '__main__':
    test_notify()
