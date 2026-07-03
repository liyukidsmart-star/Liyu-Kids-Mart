import logging
import httpx
import os

def test_telegram():
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    print(f"Token: {token}")
    
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    print(url)
    
    manager_id = '661528493'
    
    msg = "Test from test script"
    
    try:
        resp = httpx.post(url, json={'chat_id': manager_id, 'text': msg})
        print(resp.status_code)
        print(resp.text)
    except Exception as e:
        print("Error:", e)

if __name__ == '__main__':
    test_telegram()
