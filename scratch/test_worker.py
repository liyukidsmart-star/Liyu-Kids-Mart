import urllib.request

url = "https://liyu-kids-mart.liyukidsmart.workers.dev/media/AgACAgQAAyEGAAMBBKoeyQADjmpDh9WtVEQIBeRZvBrZsF25BzZVAAJuDWsbUtchUpqhQ5euKFUJAQADAgADeQADPAQ"
try:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        print("Status code:", response.getcode())
        print("Content type:", response.headers.get('Content-Type'))
        print("Length:", len(response.read()))
except Exception as e:
    print("Error:", e)
