import urllib.request
import urllib.error

# Test health
print("=== Health Check ===")
try:
    req = urllib.request.Request(
        "https://liyu-kids-mart.liyukidsmart.workers.dev/health",
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    )
    resp = urllib.request.urlopen(req)
    print("Status:", resp.status)
    print("Body:", resp.read().decode())
except urllib.error.HTTPError as e:
    print("Status:", e.code)
    print("Body:", e.read().decode())

print()
print("=== Media Test ===")
# Test a real file_id
file_id = "AgACAgQAAyEGAAMBBKoeyQADA2o74KaAjw3Jozk1TRuJRv_QI2D-AAINDmsbEaDgUS03MCPJo2cYAQADAgADeAADPAQ"
url = f"https://liyu-kids-mart.liyukidsmart.workers.dev/media/{file_id}"

class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

opener = urllib.request.build_opener(NoRedirectHandler())
try:
    req = urllib.request.Request(
        url,
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'}
    )
    resp = opener.open(req)
    print("Status:", resp.status)
    print("Content-Type:", resp.headers.get('content-type'))
    print("X-Cache:", resp.headers.get('x-cache'))
    data = resp.read()
    print(f"Image size: {len(data)} bytes")
except urllib.error.HTTPError as e:
    print("Status:", e.code)
    try:
        print("Body:", e.read().decode())
    except:
        print("(binary)")
