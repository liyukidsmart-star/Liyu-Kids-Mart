import urllib.request
import json

url = "https://liyu-kids-mart.vercel.app/api/v1/mini-app/bootstrap"
try:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        
        products = data.get('data', {}).get('featured', [])
        print(f"Fetched {len(products)} featured products.")
        for p in products[:3]:
            print(f"ID: {p['id']}, Name: {p['name']}")
            print(f"Primary Image: {p['primary_image']}")
            print()
            
except Exception as e:
    print("Error:", e)
