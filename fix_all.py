import io, sys
sys.stdout.reconfigure(encoding='utf-8')

path = 'app/templates/admin/loyalty/discount_analytics.html'
try:
    with io.open(path, 'r', encoding='utf-8') as f:
        content = f.read()
except UnicodeDecodeError:
    with io.open(path, 'r', encoding='cp1252') as f:
        content = f.read()

old = "url_for('admin.loyalty_spending_thresholds')"
new = "url_for('admin.loyalty_thresholds')"
if old in content:
    content = content.replace(old, new)
    print('Fixed analytics endpoint')
else:
    print('WARNING: pattern not found')

with io.open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print('Done')
