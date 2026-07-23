import sys
import os
sys.path.insert(0, os.path.abspath('.'))

from app import create_app

app = create_app()
endpoints = [
    '/telegram/store-app',
    '/api/v1/store/dashboard?manager_id=401413271',
    '/api/v1/store/orders?manager_id=401413271',
    '/api/v1/store/products?manager_id=401413271',
    '/api/v1/store/drivers?manager_id=401413271',
    '/api/v1/store/settings?manager_id=401413271',
    '/api/v1/store/sales/history?manager_id=401413271',
    '/api/v1/store/analytics/kpis?manager_id=401413271',
    '/api/v1/store/analytics/revenue?manager_id=401413271',
    '/api/v1/store/analytics/funnel?manager_id=401413271',
    '/api/v1/store/analytics/products?manager_id=401413271',
    '/api/v1/store/analytics/segments?manager_id=401413271',
    '/api/v1/store/analytics/cohort?manager_id=401413271',
    '/api/v1/store/analytics/geographic?manager_id=401413271',
    '/api/v1/store/analytics/insights?manager_id=401413271',
    '/api/v1/store/customers?manager_id=401413271',
]

with app.test_client() as client:
    all_ok = True
    for ep in endpoints:
        res = client.get(ep)
        status = res.status_code
        is_json = res.is_json
        print(f"Endpoint: {ep:<55} | Status: {status} | Is JSON: {is_json}")
        if status != 200:
            all_ok = False
            print(f"   --> Error Body: {res.get_data(as_text=True)[:150]}")
            
    if all_ok:
        print("\nALL STORE MANAGEMENT PORTAL ENDPOINTS RETURNED STATUS 200 OK!")
    else:
        print("\nSOME ENDPOINTS FAILED!")
