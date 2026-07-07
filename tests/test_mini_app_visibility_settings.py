import unittest

from app import create_app
from app.extensions import db
from app.models.loyalty import LoyaltySettings
from app.models.product import Category


class MiniAppVisibilitySettingsTestCase(unittest.TestCase):
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

    def _get_settings(self):
        settings = LoyaltySettings.query.first()
        if not settings:
            settings = LoyaltySettings()
            db.session.add(settings)
            db.session.commit()
        return settings

    def test_shop_init_reports_visibility_flags(self):
        settings = self._get_settings()
        settings.show_categories_in_mini_app = False
        settings.show_age_filter_in_mini_app = False
        db.session.commit()

        response = self.client.get('/api/v1/shop/init')
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload['data']['show_categories_in_mini_app'])
        self.assertFalse(payload['data']['show_age_filter_in_mini_app'])

    def test_categories_api_hides_categories_when_disabled(self):
        settings = self._get_settings()
        settings.show_categories_in_mini_app = False
        db.session.commit()

        db.session.add(Category(name='Wooden Toys', slug='wooden-toys', icon='🪵', is_active=True))
        db.session.commit()

        response = self.client.get('/api/v1/categories')
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['data'], [])


if __name__ == '__main__':
    unittest.main()
