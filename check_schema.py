import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from dotenv import load_dotenv
load_dotenv()
from app import create_app
from app.extensions import db

app = create_app()
with app.app_context():
    from sqlalchemy import inspect as sa_inspect
    inspector = sa_inspect(db.engine)
    
    print("=== Orders table columns in DB ===")
    for c in inspector.get_columns('orders'):
        print(f"  {c['name']} : {c['type']}")
    
    print()
    print("=== Addresses table columns ===")
    for c in inspector.get_columns('addresses'):
        print(f"  {c['name']} : {c['type']}")
    
    print()
    print("=== Users table columns ===")
    for c in inspector.get_columns('users'):
        print(f"  {c['name']} : {c['type']}")
