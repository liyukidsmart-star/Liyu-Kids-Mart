import traceback
import sys

try:
    from app import create_app
    app = create_app()
    with app.test_request_context('/admin/customers?tab=overview'):
        from app.blueprints.admin.routes import customers
        customers()
        print("SUCCESS")
except Exception as e:
    traceback.print_exc()
