import urllib.request
import urllib.error

class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

url = "https://liyu-kids-mart.vercel.app/media/AgACAgQAAyEGAAMBBKoeyQADA2o74KaAjw3Jozk1TRuJRv_QI2D-AAINDmsbEaDgUS03MCPJo2cYAQADAgADeAADPAQ"

opener = urllib.request.build_opener(NoRedirectHandler())

try:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    resp = opener.open(req)
    print("Status:", resp.status)
    print("Headers:", dict(resp.headers))
    try:
        print("Body:", resp.read().decode())
    except:
        print("Body (binary):", len(resp.read()))
except urllib.error.HTTPError as e:
    print("Status:", e.code)
    print("Headers:", dict(e.headers))
    try:
        print("Body:", e.read().decode())
    except:
        print("Body (binary error)")
