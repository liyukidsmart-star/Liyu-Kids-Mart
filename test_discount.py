from app import create_app
app = create_app()
with app.app_context():
    with app.test_request_context('/admin/loyalty/discount-analytics'):
        try:
            from app.blueprints.admin.loyalty_routes import loyalty_discount_analytics
            res = loyalty_discount_analytics()
            print("SUCCESS")
        except Exception as e:
            import traceback
            traceback.print_exc()
