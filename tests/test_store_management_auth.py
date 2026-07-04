import unittest

from app import create_app
from app.extensions import db
from app.blueprints.api.store_management_api import _is_authorized_manager


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


if __name__ == '__main__':
    unittest.main()
