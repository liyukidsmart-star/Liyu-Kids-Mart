import unittest

from app import create_app
from app.extensions import db
from app.models.product import Category, Product


class MiniAppProductApiTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('development')
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_category_filter_includes_descendant_categories(self):
        parent = Category(name='Educational Toys', slug='educational-toys', icon='🧸')
        child = Category(name='Wooden Toys', slug='wooden-toys', parent_id=None, icon='🪵')
        db.session.add_all([parent, child])
        db.session.flush()
        child.parent_id = parent.id
        product = Product(
            name='Montessori Wooden Toy',
            slug='montessori-wooden-toy',
            price=350,
            category_id=child.id,
            is_active=True,
        )
        db.session.add(product)
        db.session.commit()

        response = self.client.get(f'/api/v1/products?category_id={parent.id}')
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['success'], True)
        self.assertEqual(len(payload['data']['products']), 1)
        self.assertEqual(payload['data']['products'][0]['id'], product.id)

    def test_search_matches_multiple_terms_smartly(self):
        product = Product(
            name='Wooden Toy Set',
            slug='wooden-toy-set',
            price=250,
            category_id=None,
            is_active=True,
            short_description='A beautiful Montessori play set',
        )
        db.session.add(product)
        db.session.commit()

        response = self.client.get('/api/v1/products?q=wooden toy')
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['success'], True)
        self.assertGreaterEqual(len(payload['data']['products']), 1)
        self.assertEqual(payload['data']['products'][0]['id'], product.id)


if __name__ == '__main__':
    unittest.main()
