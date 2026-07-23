import urllib.request
import urllib.error

url = "https://liyu-kids-mart.vercel.app/media/AgACAgQAAyEGAAMBBKoeyQADjmpDh9WtVEQIBeRZvBrZsF25BzZVAAJuDWsbUtchUpqhQ5euKFUJAQADAgADeQADPAQ"
try:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        print("Status code:", response.getcode())
except urllib.error.HTTPError as e:
    print(f"Error {e.code}: {e.reason}")
    print("Body:", e.read().decode())
except Exception as e:
    print("Error:", e)
