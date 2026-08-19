import urllib.request
import urllib.error

url = "https://liyu-kids-mart.vercel.app/media/AgACAgQAAyEGAAMBBKoeyQADA2o74KaAjw3Jozk1TRuJRv_QI2D-AAINDmsbEaDgUS03MCPJo2cYAQADAgADeAADPAQ"

try:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    resp = urllib.request.urlopen(req)
    print("Status:", resp.status)
    try:
        print("Body:", resp.read().decode())
    except:
        print("Body (binary):", len(resp.read()))
except urllib.error.HTTPError as e:
    print("Status:", e.code)
    try:
        print("Body:", e.read().decode())
    except:
        print("Body (binary error)")
