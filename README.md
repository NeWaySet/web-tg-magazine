# Web TG Magazine

Единый интернет-магазин техники для браузера и Telegram Mini Apps.

Проект работает как обычный сайт и как Telegram Mini App на одной кодовой базе. В браузере используется авторизация по email и паролю, в Telegram - авторизация через `Telegram.WebApp.initData`.

## Возможности

- Каталог товаров с карточками, ценами и остатками.
- Корзина и оформление заказа.
- Обязательная оплата перед созданием заказа.
- QR-код для оплаты через криптовалюту или СБП.
- Пересчет суммы заказа из рублей в USDT.
- Единая версия интерфейса для браузера и Telegram Mini Apps.
- История статусов заказа:
  - `В обработке`
  - `Подтвержден`
  - `В доставке`
  - `Получен`
- Админ-панель для управления товарами и заказами.
- Ограничение админ-панели по IP/host.
- HTTPS-ready конфигурация для nginx.
- Шифрование чувствительных данных перед записью в PostgreSQL.

## Технологии

- Python 3
- Flask
- Gunicorn
- PostgreSQL
- pg8000
- nginx
- Telegram Web Apps SDK
- qrcode
- cryptography

## Структура

```text
web_market_deploy/
├── app.py                  # Flask-приложение и маршруты
├── models.py               # Работа с БД, корзиной, заказами, миграциями
├── db.py                   # Подключение к PostgreSQL
├── config.py               # Настройки из .env
├── security.py             # Шифрование и хэширование чувствительных данных
├── create_database.sql     # Начальная схема БД
├── requirements.txt        # Python-зависимости
├── static/                 # CSS, JS, изображения
├── templates/              # HTML-шаблоны
└── deploy/                 # systemd/nginx/env примеры для сервера
```

## Переменные окружения

Создайте файл `.env` на сервере или локально:

```env
DATABASE_HOST=localhost
DATABASE_NAME=nuzdin_db
DATABASE_USER=web_market_user
DATABASE_PASSWORD=replace-with-db-password
DATABASE_PORT=5432

SECRET_KEY=replace-with-long-random-secret
FLASK_DEBUG=False
TELEGRAM_BOT_TOKEN=replace-with-botfather-token

DATA_ENCRYPTION_KEY=replace-with-fernet-key
DATA_HASH_KEY=replace-with-random-hmac-key

CRYPTO_ASSET=USDT
CRYPTO_NETWORK=TRC20
CRYPTO_ADDRESS=replace-with-wallet-address
CRYPTO_USDT_RUB_RATE=100
CRYPTO_QR_PAYLOAD_TEMPLATE=

SBP_QR_PAYLOAD_TEMPLATE=

ADMIN_ALLOWED_IPS=
ADMIN_ALLOWED_HOSTS=132.243.230.19
```

Сгенерировать ключи:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
openssl rand -hex 32
```

Первую строку вставьте в `DATA_ENCRYPTION_KEY`, вторую - в `DATA_HASH_KEY`.

Важно: `DATA_ENCRYPTION_KEY` и `DATA_HASH_KEY` нельзя терять или менять после запуска. Иначе старые зашифрованные email, адреса и заказы нельзя будет корректно прочитать.

## Шифрование данных

Проект использует шифрование на уровне приложения. Данные шифруются до записи в PostgreSQL.

Шифруются:

- email пользователя: `users.email_encrypted`
- адрес доставки: `orders.delivery_address_encrypted`
- состав оформленного заказа: `orders.order_snapshot_encrypted`
- платежный снимок заказа: `orders.payment_snapshot_encrypted`

Для поиска пользователя по email используется не открытый email, а HMAC-хэш:

```text
users.email_lookup_hash
```

Поле `users.email` после миграции хранит только техническую маску вида `enc:...`, а не реальный email.

## Локальный запуск

```bash
python -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
cp .env.example .env
python app.py
```

На Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\pip.exe install --upgrade pip
.\.venv\Scripts\pip.exe install -r requirements.txt
copy .env.example .env
.\.venv\Scripts\python.exe app.py
```

Сайт:

```text
http://127.0.0.1:5000/market
```

Админ-панель:

```text
http://127.0.0.1:5000/admin_panel/12000
```

## Подготовка PostgreSQL

```bash
sudo -u postgres psql
```

```sql
CREATE DATABASE nuzdin_db;
CREATE USER web_market_user WITH PASSWORD 'replace-with-db-password';
GRANT ALL PRIVILEGES ON DATABASE nuzdin_db TO web_market_user;
\c nuzdin_db
GRANT ALL ON SCHEMA public TO web_market_user;
ALTER SCHEMA public OWNER TO web_market_user;
\q
```

Заливка начальной схемы:

```bash
psql -h localhost -U web_market_user -d nuzdin_db -f create_database.sql
```

## Деплой на сервер

Базовый путь на сервере:

```text
/var/www/web-tg-magazine
```

Установка зависимостей:

```bash
cd /var/www/web-tg-magazine
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Запуск через systemd:

```bash
sudo cp deploy/web-market.service /etc/systemd/system/web-market.service
sudo systemctl daemon-reload
sudo systemctl enable web-market
sudo systemctl start web-market
sudo systemctl status web-market --no-pager
```

Проверка Flask/Gunicorn:

```bash
curl -I http://127.0.0.1:8000/market
```

## nginx

Пример HTTP-конфига:

```nginx
server {
    listen 80;
    server_name 132.243.230.19;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Применение:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## HTTPS

Для Telegram Mini Apps рекомендуется использовать домен и обычный порт `443`.

Для домена:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d example.com
```

Для IP-сертификата нужен свежий certbot через snap:

```bash
sudo snap install core
sudo snap refresh core
sudo snap install --classic certbot
sudo ln -sf /snap/bin/certbot /usr/bin/certbot
```

Получение IP-сертификата:

```bash
sudo mkdir -p /var/www/certbot
sudo certbot certonly --preferred-profile shortlived --webroot --webroot-path /var/www/certbot --ip-address 132.243.230.19
```

Проверка:

```bash
curl -I https://132.243.230.19/market
```

## Telegram Mini App

В BotFather укажите URL:

```text
https://132.243.230.19/market
```

Лучший вариант для продакшена:

```text
https://your-domain.ru/market
```

На сервере должен быть указан токен именно того бота, через которого открывается Mini App:

```env
TELEGRAM_BOT_TOKEN=replace-with-botfather-token
```

## Админ-панель

URL:

```text
https://132.243.230.19/admin_panel/12000
```

Стандартный тестовый админ из `create_database.sql`:

```text
admin@example.com
admin
```

В продакшене пароль нужно заменить.

## Проверки после деплоя

Проверить приложение:

```bash
sudo systemctl status web-market --no-pager
sudo journalctl -u web-market -n 80 --no-pager
curl -I http://127.0.0.1:8000/market
curl -I https://132.243.230.19/market
```

Проверить шифрование в БД:

```bash
sudo -u postgres psql -d nuzdin_db -c "SELECT id, email, email_encrypted IS NOT NULL AS email_encrypted, email_lookup_hash IS NOT NULL AS has_hash FROM users;"
sudo -u postgres psql -d nuzdin_db -c "SELECT id, status, order_snapshot_encrypted IS NOT NULL AS order_encrypted, delivery_address_encrypted IS NOT NULL AS address_encrypted FROM orders ORDER BY id DESC LIMIT 10;"
```

В `users.email` не должен отображаться реальный email пользователя.

## Git

Перед коммитом не добавляйте секреты и служебные файлы:

```text
.env
.venv/
__pycache__/
*.pyc
*.log
```

Обычный цикл обновления:

```bash
git add .
git commit -m "Update project"
git push
```

На сервере:

```bash
cd /var/www/web-tg-magazine
git pull
.venv/bin/pip install -r requirements.txt
sudo systemctl restart web-market
```

## Лицензия

См. файл `LICENSE`.
