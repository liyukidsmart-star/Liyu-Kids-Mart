from app.services.image_delivery import rewrite_media_url, is_placeholder_url

urls = [
    'https://liyu-kids-mart.vercel.app/media/AgACAgQAAyEGAAMBBKoeyQADA2o74KaAjw3Jozk1TRuJRv_QI2D-AAINDmsbEaDgUS03MCPJo2cYAQADAgADeAADPAQ',
    'https://liyu-kids-mart.liyukidsmart.workers.dev/media/AgACAgQAAyEGAAMBBKoeyQAD_WpFKRHF6Dq6dutaafTncexKaKfpAAK6DmsbYcIoUg-hz9Dl0NHrAQADAgADeQADPAQ',
]

for url in urls:
    rewritten = rewrite_media_url(url)
    print(f"Original: {url}")
    print(f"Rewritten: {rewritten}")
    print(f"Is placeholder (original): {is_placeholder_url(url)}")
    print(f"Is placeholder (rewritten): {is_placeholder_url(rewritten)}")
    print()
