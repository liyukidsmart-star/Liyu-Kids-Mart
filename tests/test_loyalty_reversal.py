import unittest

from app import create_app
from app.extensions import db
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.services.loyalty_service import process_order_rewards, apply_order_status_change


class LoyaltyReversalTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('development')
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()

        from app.services.loyalty_service import seed_default_loyalty_data
        seed_default_loyalty_data()

        self.user = User(full_name='Test User', email='test@example.com')
        self.user.set_password('secret')
        db.session.add(self.user)
        db.session.flush()

        self.order = Order(
            order_number='T-1001',
            user_id=self.user.id,
            subtotal=500,
            delivery_fee=0,
            total=500,
            status=OrderStatus.confirmed,
        )
        db.session.add(self.order)
        db.session.flush()

        process_order_rewards(self.user, self.order, savings_amount=0)
        db.session.flush()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_cancelled_order_reduces_loyalty_metrics(self):
        self.assertEqual(self.user.total_orders, 1)
        self.assertGreaterEqual(self.user.loyalty_score, 0)

        result = apply_order_status_change(self.user, self.order, OrderStatus.cancelled, OrderStatus.confirmed)

        self.assertTrue(result['reversed'])
        self.assertEqual(self.user.total_orders, 0)
        self.assertEqual(float(self.user.total_money_spent or 0), 0.0)
        self.assertEqual(self.user.loyalty_level_id, None)


if __name__ == '__main__':
    unittest.main()
