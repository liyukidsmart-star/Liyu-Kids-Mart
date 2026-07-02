import csv

with open('scripts/data/product_images_backfill.csv', 'r') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

product_ids = sorted(set(int(r['product_id']) for r in rows))
print(f'Total rows: {len(rows)}')
print(f'Unique product_ids in CSV: {len(product_ids)}')
print(f'First 5 product_ids: {product_ids[:5]}')
print(f'Last 5 product_ids: {product_ids[-5:]}')
print(f'Min: {product_ids[0]}, Max: {product_ids[-1]}')

# Count rows per product
from collections import Counter
counts = Counter(int(r['product_id']) for r in rows)
print('\nProducts with multiple images:')
for pid, cnt in sorted(counts.items()):
    if cnt > 1:
        print(f'  product_id={pid}: {cnt} images')
