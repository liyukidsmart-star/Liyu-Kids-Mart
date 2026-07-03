import os
from app import create_app

# On Vercel (serverless), FLASK_ENV is typically not set. Default to production.
config_name = os.getenv('FLASK_ENV', os.getenv('APP_ENV', 'production'))
app = create_app(config_name)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
