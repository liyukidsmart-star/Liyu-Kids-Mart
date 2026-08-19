import urllib.request
import urllib.error

url = "https://liyu-kids-mart.liyukidsmart.workers.dev/media/AgACAgQAAyEGAAMBBKoeyQADA2o74KaAjw3Jozk1TRuJRv_QI2D-AAINDmsbEaDgUS03MCPJo2cYAQADAgADeAADPAQ"

try:
    resp = urllib.request.urlopen(url)
    print("Status:", resp.status)
    print("Body:", resp.read().decode())
except urllib.error.HTTPError as e:
    print("Status:", e.code)
    print("Body:", e.read().decode())
