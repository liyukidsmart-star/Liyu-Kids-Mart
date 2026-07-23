import asyncio
import httpx
from app import create_app
from app.extensions import db
import os
from app.models.marketing import TelegramChannelPost
from app.services.telegram_marketing import _product_reply_markup

async def fix_buttons():
    app = create_app()
    with app.app_context():
        token = os.environ.get('TELEGRAM_BOT_TOKEN')
        posts = TelegramChannelPost.query.filter(TelegramChannelPost.sent_message_id.isnot(None)).all()
        print(f"Found {len(posts)} posts to fix.")
        
        async with httpx.AsyncClient() as client:
            for post in posts:
                print(f"Fixing post {post.id} (message_id {post.sent_message_id})...")
                
                # Regenerate reply_markup
                if post.post_type == 'product':
                    if getattr(post, 'grouped_products', None) and post.grouped_products.count() > 0:
                        products = post.grouped_products.all()
                        if not products:
                            continue
                        reply_markup = _product_reply_markup(products[0], post_id=post.id)
                    elif post.product:
                        reply_markup = _product_reply_markup(post.product, post_id=post.id)
                    else:
                        continue
                else:
                    # Ignore non-product posts
                    continue

                # Edit message reply markup
                url = f"https://api.telegram.org/bot{token}/editMessageReplyMarkup"
                payload = {
                    'chat_id': post.channel_chat_id,
                    'message_id': int(post.sent_message_id),
                    'reply_markup': reply_markup
                }
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    print(f"Success for post {post.id}")
                else:
                    print(f"Failed for post {post.id}: {resp.text}")

if __name__ == '__main__':
    asyncio.run(fix_buttons())
