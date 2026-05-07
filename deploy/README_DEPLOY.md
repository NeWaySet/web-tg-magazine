# Web Market deploy

Пакет рассчитан на Ubuntu/VPS. Основной путь приложения: `/var/www/web_market`.

## 1. Перенос архива

С локальной машины:

```powershell
scp C:\Users\HONOR\Documents\leha_prod\web_market_deploy.zip root@78.17.67.131:/var/www/
```

На сервере:

```bash
sudo apt update
sudo apt install unzip python3-venv python3-pip postgresql postgresql-contrib nginx -y
cd /var/www
sudo unzip -o web_market_deploy.zip -d web_market
sudo chown -R www-data:www-data /var/www/web_market
```

## 2. База данных

```bash
sudo -u postgres psql
```

```sql
CREATE DATABASE nuzdin_db;
CREATE USER web_market_user WITH PASSWORD 'replace-with-strong-db-password';
GRANT ALL PRIVILEGES ON DATABASE nuzdin_db TO web_market_user;
\q
```

```bash
cd /var/www/web_market
sudo -u postgres psql -d nuzdin_db -f create_database.sql
```

## 3. Python env

```bash
cd /var/www/web_market
sudo python3 -m venv .venv
sudo .venv/bin/pip install --upgrade pip
sudo .venv/bin/pip install -r requirements.txt
```

## 4. Production .env

```bash
sudo cp deploy/.env.production.example .env
sudo nano .env
```

Заполните:

```env
DATABASE_USER=web_market_user
DATABASE_PASSWORD=replace-with-strong-db-password
SECRET_KEY=replace-with-long-random-secret
TELEGRAM_BOT_TOKEN=replace-with-botfather-token
ADMIN_ALLOWED_HOSTS=YOUR_DOMAIN_OR_78.17.67.131
```

## 5. systemd

```bash
sudo cp deploy/web-market.service /etc/systemd/system/web-market.service
sudo systemctl daemon-reload
sudo systemctl enable web-market
sudo systemctl start web-market
sudo systemctl status web-market
```

## 6. Nginx HTTP

Для домена:

```bash
sudo cp deploy/nginx-domain.conf /etc/nginx/sites-available/web_market
sudo nano /etc/nginx/sites-available/web_market
sudo ln -sf /etc/nginx/sites-available/web_market /etc/nginx/sites-enabled/web_market
sudo nginx -t
sudo systemctl reload nginx
```

Для IP `78.17.67.131`:

```bash
sudo mkdir -p /var/www/certbot
sudo cp deploy/nginx-ip-http.conf /etc/nginx/sites-available/web_market
sudo ln -sf /etc/nginx/sites-available/web_market /etc/nginx/sites-enabled/web_market
sudo nginx -t
sudo systemctl reload nginx
```

## 7. HTTPS

Лучше использовать домен. Тогда:

```bash
sudo snap install core
sudo snap refresh core
sudo snap install --classic certbot
sudo ln -sf /snap/bin/certbot /usr/bin/certbot
sudo certbot --nginx -d YOUR_DOMAIN -d www.YOUR_DOMAIN
```

Если нужен HTTPS прямо на IP, Let’s Encrypt поддерживает IP certificates, но они short-lived. Для `78.17.67.131`:

```bash
sudo snap install core
sudo snap refresh core
sudo snap install --classic certbot
sudo ln -sf /snap/bin/certbot /usr/bin/certbot
sudo certbot certonly --preferred-profile shortlived --webroot --webroot-path /var/www/certbot --ip-address 78.17.67.131
sudo cp deploy/nginx-ip-https.conf /etc/nginx/sites-available/web_market
sudo nginx -t
sudo systemctl reload nginx
sudo certbot renew --dry-run
```

## 8. URLs

Сайт:

```text
https://YOUR_DOMAIN/market
https://78.17.67.131/market
```

Админка:

```text
https://YOUR_DOMAIN/admin_panel/12000
https://78.17.67.131/admin_panel/12000
```

Telegram BotFather Mini App URL:

```text
https://YOUR_DOMAIN/market
```

или при IP-сертификате:

```text
https://78.17.67.131/market
```
