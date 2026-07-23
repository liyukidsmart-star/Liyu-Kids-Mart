from app import create_app
from app.extensions import db
from app.models.product import Product, prime_product_image_lookup, _catalog_image_lookup

app = create_app()
with app.app_context():
    catalog = _catalog_image_lookup()
    print("Catalog cache keys count:", len(catalog))
    products = Product.query.limit(5).all()
    print("Found products:", len(products))
    prime_product_image_lookup(products)
    
    for p in products:
        print(f"Product {p.id}: {p.name}")
        print(f"  Primary Image: {p.primary_image()}")
        print(f"  All Images: {p.all_images()}")
        print(f"  In Catalog: {p.id in catalog}")
