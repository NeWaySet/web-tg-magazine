import os
from dotenv import load_dotenv

load_dotenv()


# конфигурация +  подключение к бд  и логирование 
class Config:
    DATABASE_HOST = os.getenv('DATABASE_HOST', 'localhost')
    DATABASE_NAME = os.getenv('DATABASE_NAME', 'nuzdin_db')
    DATABASE_USER = os.getenv('DATABASE_USER', 'postgres')
    DATABASE_PASSWORD = os.getenv('DATABASE_PASSWORD', '')
    DATABASE_PORT = os.getenv('DATABASE_PORT', '5432')
    
    DATABASE_URL = os.getenv(
        'DATABASE_URL',
        f"postgresql://{DATABASE_USER}:{DATABASE_PASSWORD}@{DATABASE_HOST}:{DATABASE_PORT}/{DATABASE_NAME}"
    )
    
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key')
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
    SBP_QR_PAYLOAD_TEMPLATE = os.getenv('SBP_QR_PAYLOAD_TEMPLATE', '')
    CRYPTO_ADDRESS = os.getenv('CRYPTO_ADDRESS', '')
    CRYPTO_ASSET = os.getenv('CRYPTO_ASSET', 'USDT')
    CRYPTO_NETWORK = os.getenv('CRYPTO_NETWORK', 'TRC20')
    CRYPTO_QR_PAYLOAD_TEMPLATE = os.getenv('CRYPTO_QR_PAYLOAD_TEMPLATE', '')
    CRYPTO_USDT_RUB_RATE = os.getenv('CRYPTO_USDT_RUB_RATE', '')
    CRYPTO_RATE_URL = os.getenv('CRYPTO_RATE_URL', 'https://www.cbr.ru/scripts/XML_daily.asp')
    CRYPTO_RATE_CACHE_SECONDS = int(os.getenv('CRYPTO_RATE_CACHE_SECONDS', '3600'))
    ADMIN_ALLOWED_IPS = os.getenv('ADMIN_ALLOWED_IPS', '')
    ADMIN_ALLOWED_HOSTS = os.getenv('ADMIN_ALLOWED_HOSTS', '')
    
    DEBUG = os.getenv('FLASK_DEBUG', 'True').lower() == 'true'
