#!/usr/bin/env python3
"""
Seed database with sample Ethiopian educational toy products.
Run: python scripts/seed_db.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models.user import User, UserRole
from app.models.product import Product, Category, ProductImage
from app.models.order import Review
from slugify import slugify

app = create_app('development')


def seed():
    with app.app_context():
        db.create_all()
        print("✅ Tables created")

        # ── CATEGORIES ──
        categories_data = [
            ('Montessori Materials', '🎯', 'Authentic Montessori educational materials for hands-on learning'),
            ('Wooden Toys', '🪵', 'Natural wooden toys for safe, open-ended play'),
            ('Puzzles & Games', '🧩', 'Brain-boosting puzzles, board games, and matching activities'),
            ('Art & Creativity', '🎨', 'Art supplies, craft kits, and creative tools'),
            ('Books & Learning', '📚', 'Educational books, flashcards, and learning aids'),
            ('Building & Construction', '🏗️', 'Building blocks, LEGO-compatible sets, and construction toys'),
            ('Music & Sound', '🎵', 'Musical instruments and sound toys for early music education'),
            ('Outdoor & Active Play', '🏃', 'Outdoor toys for active, energetic play'),
        ]
        categories = {}
        for name, icon, desc in categories_data:
            slug = slugify(name)
            existing = Category.query.filter_by(slug=slug).first()
            if not existing:
                cat = Category(name=name, slug=slug, icon=icon, description=desc, is_active=True)
                db.session.add(cat)
                db.session.flush()
                categories[name] = cat
                print(f"  ✅ Category: {name}")
            else:
                categories[name] = existing

        db.session.commit()

        # ── ADMIN USER ──
        if not User.query.filter_by(email='admin@liyukids.com').first():
            admin = User(
                full_name='Liyu Admin',
                email='admin@liyukids.com',
                phone='+251911000000',
                role=UserRole.admin,
                is_active=True,
                is_verified=True,
            )
            admin.set_password('admin1234')
            db.session.add(admin)
            print("  ✅ Admin user: admin@liyukids.com / admin1234")

        # ── SAMPLE PRODUCTS ──
        products_data = [
            {
                'name': 'Montessori Pink Tower (10 Cubes)',
                'category': 'Montessori Materials',
                'price': 850, 'compare_price': 1100,
                'stock': 15, 'age_min': 18, 'age_max': 60,
                'short_desc': 'Classic Montessori sensorial material for spatial reasoning and fine motor development.',
                'desc': 'The Pink Tower is one of the most iconic Montessori sensorial materials. It consists of 10 wooden cubes ranging from 1cm to 10cm. Children learn to distinguish sizes, build concentration, and develop fine motor skills. Made from premium quality wood with child-safe paint. Each cube differs in three dimensions.',
                'featured': True, 'new_arrival': False, 'tags': ['montessori','sensorial','wooden'],
            },
            {
                'name': 'Ethiopian Alphabet Puzzle Set',
                'category': 'Puzzles & Games',
                'price': 650, 'compare_price': 850,
                'stock': 30, 'age_min': 36, 'age_max': 84,
                'short_desc': 'Learn the Ge\'ez script with this beautifully crafted wooden puzzle set.',
                'desc': 'Introduce your child to the beautiful Ethiopian Fidel (Ge\'ez alphabet) with this tactile wooden puzzle. Each piece features a different character with matching pictures. Perfect for Ethiopian parents who want to teach their children Amharic literacy from an early age.',
                'featured': True, 'new_arrival': True, 'tags': ['amharic','alphabet','ethiopian','puzzle'],
            },
            {
                'name': 'Rainbow Wooden Stacking Rings',
                'category': 'Wooden Toys',
                'price': 450, 'compare_price': None,
                'stock': 25, 'age_min': 6, 'age_max': 36,
                'short_desc': 'Beautiful rainbow rings for color recognition and fine motor development.',
                'desc': 'These gorgeous hand-crafted wooden stacking rings are perfect for babies and toddlers. The vibrant, non-toxic colors help develop color recognition while the stacking activity builds fine motor skills and hand-eye coordination. Made from smooth, sanded wood.',
                'featured': True, 'new_arrival': True, 'tags': ['baby','rings','rainbow','wooden'],
            },
            {
                'name': 'Animal Sound Farm Puzzle',
                'category': 'Puzzles & Games',
                'price': 380, 'compare_price': 500,
                'stock': 20, 'age_min': 12, 'age_max': 48,
                'short_desc': 'Press each animal piece to hear its sound! Perfect for language development.',
                'desc': 'This interactive farm puzzle teaches children about animals, their names, and sounds. Each piece triggers a corresponding animal sound when pressed into place. Features 8 large, easy-to-grasp pieces perfect for little hands.',
                'featured': False, 'new_arrival': True, 'tags': ['puzzle','sound','farm','animals'],
            },
            {
                'name': 'Montessori Sandpaper Letters (Amharic)',
                'category': 'Montessori Materials',
                'price': 1200, 'compare_price': 1500,
                'stock': 10, 'age_min': 36, 'age_max': 84,
                'short_desc': 'Tactile letter learning for Amharic pre-readers using genuine Montessori method.',
                'desc': 'These handcrafted sandpaper letters are adapted for Amharic using the authentic Montessori three-period lesson method. Children trace the rough Fidel characters with their fingers, building muscle memory before writing. Set includes the most common 50 Fidel characters.',
                'featured': True, 'new_arrival': False, 'tags': ['montessori','amharic','literacy','sandpaper'],
            },
            {
                'name': 'Wooden Building Blocks Set (100 pcs)',
                'category': 'Building & Construction',
                'price': 950, 'compare_price': 1200,
                'stock': 18, 'age_min': 24, 'age_max': 144,
                'short_desc': '100-piece natural wood blocks for endless creative building play.',
                'desc': 'This classic 100-piece wooden block set includes cylinders, arches, rectangles, squares, and triangles in natural wood finish. Blocks are smooth, splinter-free, and perfectly sized for little hands. Promotes spatial reasoning, creativity, and cooperative play.',
                'featured': True, 'new_arrival': False, 'tags': ['blocks','building','wooden','construction'],
            },
            {
                'name': 'Watercolor Art Set for Kids',
                'category': 'Art & Creativity',
                'price': 320, 'compare_price': None,
                'stock': 40, 'age_min': 36, 'age_max': 144,
                'short_desc': 'Non-toxic watercolor set with 24 vibrant colors and quality brushes.',
                'desc': 'Encourage artistic expression with this premium watercolor set. Includes 24 vibrant, non-toxic colors, 3 quality brushes, a palette, and 20 sheets of watercolor paper. All pigments are EU-certified child-safe. Perfect for budding young artists aged 3+.',
                'featured': False, 'new_arrival': True, 'tags': ['art','watercolor','painting','creativity'],
            },
            {
                'name': 'Xylophone Rainbow Glockenspiel',
                'category': 'Music & Sound',
                'price': 580, 'compare_price': 750,
                'stock': 12, 'age_min': 18, 'age_max': 72,
                'short_desc': 'Beautiful 8-key rainbow xylophone for early music education.',
                'desc': 'This beautiful 8-note xylophone features rainbow-colored bars that help children associate colors with musical notes. Perfect for developing musical ear, coordination, and creativity. Includes two mallets and a simple song booklet.',
                'featured': False, 'new_arrival': False, 'tags': ['music','xylophone','rainbow','instrument'],
            },
            {
                'name': 'Montessori Bead Chain — Hundred',
                'category': 'Montessori Materials',
                'price': 750, 'compare_price': 900,
                'stock': 8, 'age_min': 48, 'age_max': 108,
                'short_desc': 'Classic Montessori bead chain for counting, skip counting, and number patterns.',
                'desc': 'The Hundred Chain consists of 100 golden beads arranged in groups of 10. It is used in Montessori elementary math to teach skip counting, multiples, and number patterns. A tactile and visual way to explore mathematics.',
                'featured': False, 'new_arrival': False, 'tags': ['montessori','math','counting','beads'],
            },
            {
                'name': 'Ethiopian Animals Flashcard Set',
                'category': 'Books & Learning',
                'price': 290, 'compare_price': None,
                'stock': 50, 'age_min': 6, 'age_max': 72,
                'short_desc': 'Beautiful bilingual (Amharic/English) flashcards featuring Ethiopian animals.',
                'desc': 'This unique 50-card set features Ethiopian animals like the Gelada Monkey, Ethiopian Wolf, Walia Ibex, and Shoebill Stork. Each card shows the animal with its name in both Amharic and English, plus a fun fact. Perfect for bilingual Ethiopian families.',
                'featured': True, 'new_arrival': True, 'tags': ['flashcards','amharic','english','animals','bilingual'],
            },
        ]

        for pdata in products_data:
            slug = slugify(pdata['name'])
            if Product.query.filter_by(slug=slug).first():
                print(f"  ⏭️  Skip (exists): {pdata['name']}")
                continue
            cat = categories.get(pdata['category'])
            product = Product(
                name=pdata['name'],
                slug=slug,
                short_description=pdata['short_desc'],
                description=pdata['desc'],
                price=pdata['price'],
                compare_price=pdata.get('compare_price'),
                stock_qty=pdata['stock'],
                category_id=cat.id if cat else None,
                age_min_months=pdata['age_min'],
                age_max_months=pdata['age_max'],
                is_featured=pdata.get('featured', False),
                is_new_arrival=pdata.get('new_arrival', False),
                is_active=True,
                sales_count=__import__('random').randint(5, 200),
            )
            db.session.add(product)
            db.session.flush()

            # Placeholder image
            img = ProductImage(
                product_id=product.id,
                image_url=f'/static/images/placeholder.png',
                is_primary=True, sort_order=0
            )
            db.session.add(img)
            print(f"  ✅ Product: {pdata['name']} — ETB {pdata['price']}")

        db.session.commit()
        print("\n✅ Seed complete!")
        print(f"   {Category.query.count()} categories")
        print(f"   {Product.query.count()} products")
        print(f"   {User.query.count()} users")
        print("\n🌐 Start the server: python run.py")
        print("🔑 Admin login: admin@liyukids.com / admin1234")
        print("🤖 Start Telegram bot: python telegram_bot/bot.py")


if __name__ == '__main__':
    seed()
