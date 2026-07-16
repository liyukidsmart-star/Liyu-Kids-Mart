import io
import re

path = 'app/templates/admin/loyalty/discount_analytics.html'
try:
    with io.open(path, 'r', encoding='utf-8') as f:
        content = f.read()
except UnicodeDecodeError:
    with io.open(path, 'r', encoding='cp1252') as f:
        content = f.read()

target = '''        <div class="kpi-value" style="color:#6366F1; font-size:1.4rem;">{{ stats.most_common_threshold.label }}</div>
        <div class="kpi-sub">{{ stats.most_common_threshold.count }} qualifying orders</div>'''

replacement = '''        {% if stats.most_common_threshold %}
        <div class="kpi-value" style="color:#6366F1; font-size:1.4rem;">{{ stats.most_common_threshold.label }}</div>
        <div class="kpi-sub">{{ stats.most_common_threshold.count }} qualifying orders</div>
        {% else %}
        <div class="kpi-value" style="color:#6366F1; font-size:1.4rem;">-</div>
        <div class="kpi-sub">0 qualifying orders</div>
        {% endif %}'''

new_content = content.replace(target, replacement)
try:
    with io.open(path, 'w', encoding='utf-8') as f:
        f.write(new_content)
except Exception:
    pass

print("Patched!")
