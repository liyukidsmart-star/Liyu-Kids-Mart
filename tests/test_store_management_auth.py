import unittest

from app import create_app
from app.extensions import db
from app.blueprints.api.store_management_api import _is_authorized_manager
from app.models.product import Product


class StoreManagementAuthTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('development')
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_hardcoded_manager_telegram_id_is_authorized(self):
        self.assertTrue(_is_authorized_manager('661528493'))

    def test_bulk_price_adjustment_updates_all_active_products(self):
        product_a = Product(name='Alpha', slug='alpha', price=100, stock_qty=10, is_active=True)
        product_b = Product(name='Beta', slug='beta', price=200, stock_qty=5, is_active=True)
        product_c = Product(name='Gamma', slug='gamma', price=300, stock_qty=1, is_active=False)
        db.session.add_all([product_a, product_b, product_c])
        db.session.commit()

        client = self.app.test_client()
        response = client.post(
            '/api/v1/store/products/price-adjustment',
            json={'manager_id': '661528493', 'mode': 'percentage', 'value': 10},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['data']['updated_count'], 2)

        db.session.refresh(product_a)
        db.session.refresh(product_b)
        db.session.refresh(product_c)
        self.assertEqual(float(product_a.price), 110.0)
        self.assertEqual(float(product_b.price), 220.0)
        self.assertEqual(float(product_c.price), 300.0)


if __name__ == '__main__':
    unittest.main()
