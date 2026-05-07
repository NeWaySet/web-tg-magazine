from flask import Flask, render_template, request, redirect, url_for, flash, session, abort, jsonify, current_app
from config import Config
from db import init_db_pool, close_all_connections
from models import (
    get_user_by_email, create_user, get_user_by_id, get_all_products,
    get_product_by_id, add_to_cart, get_cart_items, update_cart_item,
    remove_from_cart, place_order, get_user_orders,
    get_all_orders, get_order_details, update_order_status,
    create_product, update_product, delete_product,
    get_admin_users, get_admin_system_stats,
    ensure_order_status_schema, get_order_status_history
)
from werkzeug.security import check_password_hash, generate_password_hash
import logging
from functools import wraps
from urllib.parse import parse_qsl
from decimal import Decimal, ROUND_UP
from io import BytesIO
import urllib.request
import xml.etree.ElementTree as ET
import base64
import hashlib
import hmac
import json
import time
import uuid
import os

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
telegram_payments = {}
crypto_rate_cache = {'rate': None, 'fetched_at': 0, 'source': None}
ORDER_STATUS_LABELS = {
    'cart': 'В корзине',
    'new': 'В обработке',
    'processing': 'В обработке',
    'confirmed': 'Подтвержден',
    'delivering': 'В доставке',
    'received': 'Получен',
    'completed': 'Получен',
    'cancelled': 'Отменен'
}


def csv_config(value):
    return {item.strip() for item in (value or '').split(',') if item.strip()}


def request_client_ip():
    forwarded_for = request.headers.get('X-Forwarded-For', '')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.remote_addr or ''


def request_host_name():
    return (request.host or '').split(':')[0].strip()


def admin_network_allowed():
    allowed_ips = csv_config(current_app.config.get('ADMIN_ALLOWED_IPS', ''))
    allowed_hosts = csv_config(current_app.config.get('ADMIN_ALLOWED_HOSTS', ''))
    client_ip = request_client_ip()
    host_name = request_host_name()
    ip_allowed = not allowed_ips or client_ip in allowed_ips
    host_allowed = not allowed_hosts or host_name in allowed_hosts
    if not ip_allowed or not host_allowed:
        logger.warning(
            "Admin network denied: client_ip=%s host=%s allowed_ips=%s allowed_hosts=%s",
            client_ip,
            host_name,
            sorted(allowed_ips),
            sorted(allowed_hosts)
        )
        return False
    return True


def create_app(config_class=Config):
    app = Flask(__name__)
    # Загрузка конфигурации
    app.config.from_object(config_class)
    # Инициализация пула соединений с БД
    with app.app_context():
        if not init_db_pool():
            logger.warning("Не удалось соединиться с БД.")
        else:
            ensure_order_status_schema()
    # Регистрация маршрутов
    register_routes(app)
    # Регистрация обработчиков ошибок
    register_error_handlers(app)
    
    return app

# защита для незарегестрированных
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Пожалуйста, войдите.', 'warning')
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

# защита для админа
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not admin_network_allowed():
            abort(403)
        if 'user_id' not in session:
            flash('Пожалуйста, войдите.', 'warning')
            return redirect(url_for('login', next=request.url))
        if not session.get('is_admin', False):
            flash('Доступ запрещен. Требуются права администратора.', 'danger')
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

# сумма корзины
def get_cart_total(user_id):
    items = get_cart_items(user_id)
    return round(
        sum(item['quantity'] * item['price_at_time'] for item in items),
        2
    )


def telegram_api_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Откройте Mini App через Telegram.'}), 401
        return f(*args, **kwargs)
    return decorated_function


def market_api_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Войдите в аккаунт.'}), 401
        return f(*args, **kwargs)
    return decorated_function


def decimal_to_float(value):
    if isinstance(value, Decimal):
        return float(value)
    return float(value or 0)


def serialize_product(product):
    return {
        'id': product['id'],
        'name': product['name'],
        'description': product.get('description') or '',
        'price': decimal_to_float(product.get('price')),
        'stock': int(product.get('stock') or 0),
        'image_url': url_for('static', filename=f'images/{product["id"]}.png'),
        'fallback_image_url': url_for('static', filename='images/default.png')
    }


def serialize_cart_item(item):
    price = decimal_to_float(item.get('price_at_time'))
    quantity = int(item.get('quantity') or 0)
    return {
        'id': item['id'],
        'product_id': item['product_id'],
        'name': item['name'],
        'description': item.get('description') or '',
        'quantity': quantity,
        'price_at_time': price,
        'stock': int(item.get('stock') or 0),
        'line_total': round(quantity * price, 2),
        'image_url': url_for('static', filename=f'images/{item["product_id"]}.png'),
        'fallback_image_url': url_for('static', filename='images/default.png')
    }


def serialize_order(order):
    items = order.get('items', [])
    total = round(sum(
        int(item.get('quantity') or 0) * decimal_to_float(item.get('price_at_time'))
        for item in items
    ), 2)
    created_at = order.get('created_at')
    history = order.get('status_history')
    if history is None:
        history = get_order_status_history(order['id'])
    return {
        'id': order['id'],
        'status': order.get('status'),
        'status_label': ORDER_STATUS_LABELS.get(order.get('status'), order.get('status')),
        'created_at': created_at.isoformat() if hasattr(created_at, 'isoformat') else created_at,
        'status_history': [
            {
                'id': item.get('id'),
                'old_status': item.get('old_status'),
                'old_status_label': ORDER_STATUS_LABELS.get(item.get('old_status'), item.get('old_status')),
                'new_status': item.get('new_status'),
                'new_status_label': ORDER_STATUS_LABELS.get(item.get('new_status'), item.get('new_status')),
                'changed_by': item.get('changed_by'),
                'changed_by_email': item.get('changed_by_email'),
                'note': item.get('note'),
                'created_at': item.get('created_at').isoformat() if hasattr(item.get('created_at'), 'isoformat') else item.get('created_at')
            }
            for item in history
        ],
        'items': [
            {
                'id': item['id'],
                'product_id': item['product_id'],
                'name': item['name'],
                'quantity': int(item.get('quantity') or 0),
                'price_at_time': decimal_to_float(item.get('price_at_time')),
            }
            for item in items
        ],
        'total': total
    }


def cart_total_from_items(items):
    return round(sum(
        int(item.get('quantity') or 0) * decimal_to_float(item.get('price_at_time'))
        for item in items
    ), 2)


def cart_signature(items):
    signature_source = '|'.join(
        f'{item["id"]}:{item["product_id"]}:{int(item.get("quantity") or 0)}:{decimal_to_float(item.get("price_at_time")):.2f}'
        for item in sorted(items, key=lambda cart_item: cart_item['id'])
    )
    return hashlib.sha256(signature_source.encode('utf-8')).hexdigest()


def payment_comment(payment_id):
    return f'Web Market {payment_id[:8]}'


def configured_crypto_rate():
    raw_rate = str(Config.CRYPTO_USDT_RUB_RATE or '').replace(',', '.').strip()
    if not raw_rate:
        return None
    try:
        rate = Decimal(raw_rate)
        return rate if rate > 0 else None
    except Exception:
        return None


def fetch_crypto_rate():
    configured_rate = configured_crypto_rate()
    if configured_rate:
        return configured_rate, 'manual'

    now = time.time()
    cached_rate = crypto_rate_cache.get('rate')
    if cached_rate and now - crypto_rate_cache.get('fetched_at', 0) < Config.CRYPTO_RATE_CACHE_SECONDS:
        return cached_rate, crypto_rate_cache.get('source') or 'cache'

    try:
        request_obj = urllib.request.Request(
            Config.CRYPTO_RATE_URL,
            headers={'User-Agent': 'WebMarket/1.0'}
        )
        with urllib.request.urlopen(request_obj, timeout=5) as response:
            xml_data = response.read()
        root = ET.fromstring(xml_data)
        for valute in root.findall('Valute'):
            char_code = valute.findtext('CharCode')
            if char_code == 'USD':
                nominal = Decimal((valute.findtext('Nominal') or '1').replace(',', '.'))
                value = Decimal((valute.findtext('Value') or '0').replace(',', '.'))
                if nominal <= 0 or value <= 0:
                    break
                rate = value / nominal
                crypto_rate_cache.update({'rate': rate, 'fetched_at': now, 'source': 'cbr_usd'})
                return rate, 'cbr_usd'
    except Exception as e:
        logger.error(f"Crypto rate fetch error: {e}")

    if cached_rate:
        return cached_rate, crypto_rate_cache.get('source') or 'cache'
    raise ValueError('Не удалось получить курс USDT/RUB. Задайте CRYPTO_USDT_RUB_RATE в .env.')


def crypto_payment_info(amount):
    rate, source = fetch_crypto_rate()
    rub_amount = Decimal(str(decimal_to_float(amount)))
    crypto_amount = (rub_amount / rate).quantize(Decimal('0.000001'), rounding=ROUND_UP)
    return {
        'asset': Config.CRYPTO_ASSET,
        'network': Config.CRYPTO_NETWORK,
        'address': Config.CRYPTO_ADDRESS,
        'rate': float(rate),
        'rate_display': f'{rate.quantize(Decimal("0.01"))}',
        'rate_source': source,
        'amount': float(crypto_amount),
        'amount_display': f'{crypto_amount.normalize():f}'
    }


def fill_payment_template(template, amount, payment_id, method, crypto_info=None):
    crypto_info = crypto_info or {}
    amount = decimal_to_float(amount)
    return template.format(
        amount=f'{amount:.2f}',
        amount_kop=int(round(amount * 100)),
        payment_id=payment_id,
        order_id=payment_id[:8],
        comment=payment_comment(payment_id),
        crypto_address=Config.CRYPTO_ADDRESS,
        crypto_asset=Config.CRYPTO_ASSET,
        crypto_network=Config.CRYPTO_NETWORK,
        crypto_amount=crypto_info.get('amount_display', ''),
        crypto_rate=crypto_info.get('rate_display', ''),
        method=method
    )


def build_payment_payload(method, amount, payment_id):
    if method == 'sbp':
        if not Config.SBP_QR_PAYLOAD_TEMPLATE:
            raise ValueError('СБП QR не настроен: задайте SBP_QR_PAYLOAD_TEMPLATE в .env.')
        return fill_payment_template(Config.SBP_QR_PAYLOAD_TEMPLATE, amount, payment_id, method), None

    if method == 'crypto':
        crypto_info = crypto_payment_info(amount)
        if Config.CRYPTO_QR_PAYLOAD_TEMPLATE:
            return fill_payment_template(Config.CRYPTO_QR_PAYLOAD_TEMPLATE, amount, payment_id, method, crypto_info), crypto_info
        if not Config.CRYPTO_ADDRESS:
            raise ValueError('Crypto QR не настроен: задайте CRYPTO_ADDRESS в .env.')
        return '\n'.join([
            f'{Config.CRYPTO_ASSET} {Config.CRYPTO_NETWORK}',
            f'Address: {Config.CRYPTO_ADDRESS}',
            f'Amount {Config.CRYPTO_ASSET}: {crypto_info["amount_display"]}',
            f'Rate: 1 {Config.CRYPTO_ASSET} = {crypto_info["rate_display"]} RUB',
            f'Amount RUB: {decimal_to_float(amount):.2f}',
            f'Payment ID: {payment_id}',
            f'Comment: {payment_comment(payment_id)}'
        ]), crypto_info

    raise ValueError('Выберите оплату через СБП или криптовалюту.')


def make_qr_data_url(payload):
    import qrcode
    from qrcode.image.svg import SvgPathImage

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=3,
        image_factory=SvgPathImage
    )
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image()
    stream = BytesIO()
    image.save(stream)
    encoded = base64.b64encode(stream.getvalue()).decode('ascii')
    return f'data:image/svg+xml;base64,{encoded}'


def payment_methods_payload():
    return [
        {
            'id': 'crypto',
            'title': f'{Config.CRYPTO_ASSET} {Config.CRYPTO_NETWORK}',
            'configured': bool(Config.CRYPTO_ADDRESS or Config.CRYPTO_QR_PAYLOAD_TEMPLATE)
        },
        {
            'id': 'sbp',
            'title': 'СБП',
            'configured': bool(Config.SBP_QR_PAYLOAD_TEMPLATE)
        }
    ]


def get_confirmed_payment(payment_id, user_id, items):
    payment = telegram_payments.get(payment_id)
    if not payment:
        return None
    if payment.get('user_id') != user_id:
        return None
    if payment.get('status') != 'confirmed':
        return None
    if payment.get('cart_signature') != cart_signature(items):
        return None
    if payment.get('amount') != cart_total_from_items(items):
        return None
    return payment


def validate_telegram_init_data(init_data, bot_token):
    parsed_data = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed_data.pop('hash', None)
    if not received_hash:
        return False
    data_check_string = '\n'.join(
        f'{key}={value}' for key, value in sorted(parsed_data.items())
    )
    secret_key = hmac.new(
        b'WebAppData',
        bot_token.encode('utf-8'),
        hashlib.sha256
    ).digest()
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(calculated_hash, received_hash)


def parse_telegram_user(init_data):
    parsed_data = dict(parse_qsl(init_data or '', keep_blank_values=True))
    raw_user = parsed_data.get('user')
    if not raw_user:
        return None
    try:
        return json.loads(raw_user)
    except json.JSONDecodeError:
        return None


def telegram_display_name(telegram_user):
    first_name = telegram_user.get('first_name') or ''
    last_name = telegram_user.get('last_name') or ''
    username = telegram_user.get('username')
    full_name = f'{first_name} {last_name}'.strip()
    if full_name:
        return full_name
    if username:
        return f'@{username}'
    return 'Telegram user'


def ensure_telegram_user(telegram_user):
    telegram_id = telegram_user.get('id')
    if not telegram_id:
        return None
    email = f'telegram_{telegram_id}@telegram.local'
    user = get_user_by_email(email)
    if user:
        return user
    password_hash = generate_password_hash(f'telegram:{telegram_id}:{Config.SECRET_KEY}')
    user_id = create_user(email, password_hash, is_admin=False)
    return get_user_by_id(user_id) if user_id else None

# маршруты
def register_routes(app):
    def save_product_image(image_file, product_id):
        if not image_file or not image_file.filename:
            return None
        if not image_file.filename.lower().endswith('.png'):
            return False
        images_dir = os.path.join(app.root_path, 'static', 'images')
        os.makedirs(images_dir, exist_ok=True)
        image_path = os.path.join(images_dir, f'{product_id}.png')
        image_file.save(image_path)
        return True

    @app.route('/')
    def index():
        # главная стр
        return redirect(url_for('market'))

    @app.route('/market')
    def market():
        return render_template('market.html')

    @app.route('/telegram-mini-app')
    def telegram_mini_app():
        return redirect(url_for('market'))

    @app.route('/tg')
    def telegram_mini_app_shortcut():
        return redirect(url_for('market'))

    @app.route('/api/market/auth/me')
    def market_auth_me():
        if 'user_id' not in session:
            return jsonify({'authenticated': False, 'user': None})
        return jsonify({
            'authenticated': True,
            'user': {
                'id': session['user_id'],
                'email': session.get('email'),
                'name': session.get('telegram_name') or session.get('email'),
                'username': session.get('telegram_username'),
                'telegram_id': session.get('telegram_id'),
                'is_admin': bool(session.get('is_admin', False)),
                'provider': session.get('auth_provider', 'web')
            }
        })

    @app.route('/api/market/auth/login', methods=['POST'])
    def market_auth_login():
        payload = request.get_json(silent=True) or {}
        email = (payload.get('email') or '').strip()
        password = payload.get('password') or ''
        if not email or not password:
            return jsonify({'error': 'Email и пароль обязательны.'}), 400
        user = get_user_by_email(email)
        if not user or not check_password_hash(user['password_hash'], password):
            return jsonify({'error': 'Неверный email или пароль.'}), 401
        session['user_id'] = user['id']
        session['email'] = user['email']
        session['is_admin'] = bool(user.get('is_admin', False))
        session['auth_provider'] = 'web'
        session.pop('telegram_id', None)
        session.pop('telegram_name', None)
        session.pop('telegram_username', None)
        return market_auth_me()

    @app.route('/api/market/auth/register', methods=['POST'])
    def market_auth_register():
        payload = request.get_json(silent=True) or {}
        email = (payload.get('email') or '').strip()
        password = payload.get('password') or ''
        if not email or not password:
            return jsonify({'error': 'Email и пароль обязательны.'}), 400
        if len(password) < 6:
            return jsonify({'error': 'Пароль должен быть не менее 6 символов.'}), 400
        if get_user_by_email(email):
            return jsonify({'error': 'Пользователь с таким email уже существует.'}), 409
        user_id = create_user(email, generate_password_hash(password), is_admin=False)
        if not user_id:
            return jsonify({'error': 'Не удалось создать пользователя.'}), 500
        user = get_user_by_id(user_id)
        session['user_id'] = user['id']
        session['email'] = user['email']
        session['is_admin'] = False
        session['auth_provider'] = 'web'
        return market_auth_me()

    @app.route('/api/market/auth/logout', methods=['POST'])
    def market_auth_logout():
        session.clear()
        return jsonify({'authenticated': False})

    @app.route('/api/market/auth/telegram', methods=['POST'])
    @app.route('/api/telegram/auth', methods=['POST'])
    def telegram_auth():
        payload = request.get_json(silent=True) or {}
        init_data = payload.get('initData', '')
        bot_token = app.config.get('TELEGRAM_BOT_TOKEN')

        if bot_token and init_data:
            if not validate_telegram_init_data(init_data, bot_token):
                return jsonify({'error': 'Не удалось проверить подпись Telegram.'}), 401
            telegram_user = parse_telegram_user(init_data)
        elif app.config.get('DEBUG'):
            telegram_user = parse_telegram_user(init_data) or {
                'id': payload.get('devUserId') or 100001,
                'first_name': 'Dev',
                'last_name': 'Telegram'
            }
        else:
            return jsonify({'error': 'На сервере не задан TELEGRAM_BOT_TOKEN.'}), 500

        if not telegram_user or not telegram_user.get('id'):
            return jsonify({'error': 'Telegram не передал данные пользователя.'}), 400

        user = ensure_telegram_user(telegram_user)
        if not user:
            return jsonify({'error': 'Не удалось создать пользователя.'}), 500

        session['user_id'] = user['id']
        session['email'] = telegram_display_name(telegram_user)
        session['is_admin'] = bool(user.get('is_admin', False))
        session['telegram_id'] = telegram_user['id']
        session['telegram_name'] = telegram_display_name(telegram_user)
        session['telegram_username'] = telegram_user.get('username')
        session['auth_provider'] = 'telegram'

        items = get_cart_items(user['id'])
        return jsonify({
            'user': {
                'id': user['id'],
                'telegram_id': telegram_user['id'],
                'name': telegram_display_name(telegram_user),
                'username': telegram_user.get('username')
            },
            'cart_count': sum(int(item.get('quantity') or 0) for item in items)
        })

    @app.route('/api/telegram/me')
    @telegram_api_required
    def telegram_me():
        return jsonify({
            'user': {
                'id': session['user_id'],
                'telegram_id': session.get('telegram_id'),
                'name': session.get('telegram_name') or session.get('email'),
                'username': session.get('telegram_username')
            }
        })

    @app.route('/api/market/products')
    @app.route('/api/telegram/products')
    def telegram_products():
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        per_page = min(max(per_page, 1), 50)
        offset = (page - 1) * per_page
        products = get_all_products(limit=per_page, offset=offset)
        return jsonify({
            'products': [serialize_product(product) for product in products],
            'page': page,
            'per_page': per_page,
            'has_next': len(products) == per_page
        })

    @app.route('/api/market/products/<int:product_id>')
    @app.route('/api/telegram/products/<int:product_id>')
    def telegram_product_detail(product_id):
        product = get_product_by_id(product_id)
        if not product:
            return jsonify({'error': 'Товар не найден.'}), 404
        return jsonify({'product': serialize_product(product)})

    @app.route('/api/market/cart')
    @app.route('/api/telegram/cart')
    @market_api_required
    def telegram_cart():
        items = [serialize_cart_item(item) for item in get_cart_items(session['user_id'])]
        total = round(sum(item['line_total'] for item in items), 2)
        return jsonify({'items': items, 'total': total})

    @app.route('/api/market/payments/methods')
    @app.route('/api/telegram/payments/methods')
    @market_api_required
    def telegram_payment_methods():
        return jsonify({'methods': payment_methods_payload()})

    @app.route('/api/market/payments', methods=['POST'])
    @app.route('/api/telegram/payments', methods=['POST'])
    @market_api_required
    def telegram_create_payment():
        payload = request.get_json(silent=True) or {}
        method = payload.get('method')
        items = get_cart_items(session['user_id'])
        if not items:
            return jsonify({'error': 'Корзина пуста.'}), 400

        amount = cart_total_from_items(items)
        payment_id = uuid.uuid4().hex
        try:
            qr_payload, crypto_info = build_payment_payload(method, amount, payment_id)
            qr_data_url = make_qr_data_url(qr_payload)
        except ValueError as e:
            return jsonify({'error': str(e), 'methods': payment_methods_payload()}), 400

        telegram_payments[payment_id] = {
            'id': payment_id,
            'user_id': session['user_id'],
            'method': method,
            'amount': amount,
            'crypto': crypto_info,
            'cart_signature': cart_signature(items),
            'status': 'pending',
            'created_at': time.time()
        }

        return jsonify({
            'payment': {
                'id': payment_id,
                'method': method,
                'amount': amount,
                'crypto': crypto_info,
                'comment': payment_comment(payment_id),
                'payload': qr_payload,
                'qr_data_url': qr_data_url,
                'status': 'pending'
            }
        })

    @app.route('/api/market/payments/<payment_id>/confirm', methods=['POST'])
    @app.route('/api/telegram/payments/<payment_id>/confirm', methods=['POST'])
    @market_api_required
    def telegram_confirm_payment(payment_id):
        payment = telegram_payments.get(payment_id)
        if not payment or payment.get('user_id') != session['user_id']:
            return jsonify({'error': 'Платеж не найден.'}), 404
        payment['status'] = 'confirmed'
        payment['confirmed_at'] = time.time()
        return jsonify({'payment': {'id': payment_id, 'status': 'confirmed'}})

    @app.route('/api/market/cart', methods=['POST'])
    @app.route('/api/telegram/cart', methods=['POST'])
    @market_api_required
    def telegram_add_to_cart():
        payload = request.get_json(silent=True) or {}
        product_id = payload.get('product_id')
        quantity = int(payload.get('quantity') or 1)
        if quantity <= 0:
            return jsonify({'error': 'Количество должно быть больше 0.'}), 400
        product = get_product_by_id(product_id)
        if not product:
            return jsonify({'error': 'Товар не найден.'}), 404
        stock = int(product.get('stock') or 0)
        if quantity > stock:
            return jsonify({'error': f'На складе доступно: {stock} шт.'}), 400
        try:
            add_to_cart(session['user_id'], product_id, quantity)
        except Exception:
            return jsonify({'error': 'Не удалось добавить товар в корзину.'}), 400
        return telegram_cart()

    @app.route('/api/market/cart/<int:item_id>', methods=['PATCH'])
    @app.route('/api/telegram/cart/<int:item_id>', methods=['PATCH'])
    @market_api_required
    def telegram_update_cart_item(item_id):
        payload = request.get_json(silent=True) or {}
        quantity = int(payload.get('quantity') or 0)
        current_items = get_cart_items(session['user_id'])
        current_item = next((item for item in current_items if item['id'] == item_id), None)
        if not current_item:
            return jsonify({'error': 'Позиция корзины не найдена.'}), 404
        if quantity > 0:
            if quantity > int(current_item.get('stock') or 0):
                return jsonify({'error': f'На складе доступно: {current_item["stock"]} шт.'}), 400
        if not update_cart_item(item_id, quantity):
            return jsonify({'error': 'Не удалось обновить корзину.'}), 400
        return telegram_cart()

    @app.route('/api/market/cart/<int:item_id>', methods=['DELETE'])
    @app.route('/api/telegram/cart/<int:item_id>', methods=['DELETE'])
    @market_api_required
    def telegram_remove_cart_item(item_id):
        current_items = get_cart_items(session['user_id'])
        if not any(item['id'] == item_id for item in current_items):
            return jsonify({'error': 'Позиция корзины не найдена.'}), 404
        if not remove_from_cart(item_id):
            return jsonify({'error': 'Не удалось удалить товар.'}), 400
        return telegram_cart()

    @app.route('/api/market/orders')
    @app.route('/api/telegram/orders')
    @market_api_required
    def telegram_orders():
        orders = get_user_orders(session['user_id'])
        return jsonify({'orders': [serialize_order(order) for order in orders]})

    @app.route('/api/market/orders', methods=['POST'])
    @app.route('/api/telegram/orders', methods=['POST'])
    @market_api_required
    def telegram_place_order():
        payload = request.get_json(silent=True) or {}
        items = get_cart_items(session['user_id'])
        if not items:
            return jsonify({'error': 'Корзина пуста.'}), 400

        changed = False
        for item in list(items):
            stock = int(item.get('stock') or 0)
            if stock <= 0:
                remove_from_cart(item['id'])
                changed = True
            elif int(item.get('quantity') or 0) > stock:
                update_cart_item(item['id'], stock)
                changed = True

        if changed:
            cart_response = telegram_cart().get_json()
            return jsonify({
                'error': 'Корзина обновлена по наличию на складе.',
                'cart': cart_response
            }), 409

        payment_id = payload.get('payment_id')
        payment = get_confirmed_payment(payment_id, session['user_id'], items)
        if not payment:
            return jsonify({'error': 'Сначала оплатите заказ через QR-код.'}), 402

        order_id = place_order(session['user_id'])
        if not order_id:
            return jsonify({'error': 'Не удалось оформить заказ.'}), 400
        payment['status'] = 'used'
        payment['order_id'] = order_id
        order = get_order_details(order_id)
        return jsonify({'order': serialize_order(order), 'cart': {'items': [], 'total': 0}})

    @app.route('/catalog')
    def catalog():
        return redirect(url_for('market'))
        # каталог
        # Получаем параметры пагинации
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        # Ограничиваем per_page
        if per_page > 100:
            per_page = 100
        offset = (page - 1) * per_page
        # Получаем товары
        products = get_all_products(limit=per_page, offset=offset)
        # Проверяем, есть ли еще товары для следующей страницы
        has_next = len(products) == per_page
        return render_template(
            'catalog.html',
            products=products,
            page=page,
            per_page=per_page,
            has_next=has_next,
            has_prev=page > 1
        )
    
    @app.route('/register', methods=['GET', 'POST'])
    def register():
        return redirect(url_for('market'))
        # рег пользователя
        if request.method == 'POST':
            email = request.form.get('email', '').strip()
            password = request.form.get('password', '')
            confirm_password = request.form.get('confirm_password', '')
            # Валидация
            if not email or not password:
                flash('Email и пароль обязательны.', 'danger')
                return render_template('register.html')
            if len(password) < 6:
                flash('Пароль должен быть не менее 6 символов.', 'danger')
                return render_template('register.html')
            if password != confirm_password:
                flash('Пароли не совпадают.', 'danger')
                return render_template('register.html')
            # Проверка униальности email
            existing_user = get_user_by_email(email)
            if existing_user:
                flash('Пользователь с таким email уже существует.', 'danger')
                return render_template('register.html')
            # Создание пользователя
            from werkzeug.security import generate_password_hash
            password_hash = generate_password_hash(password)
            user_id = create_user(email, password_hash, is_admin=False)
            # автовход
            if user_id:
                session['user_id'] = user_id
                session['email'] = email
                session['is_admin'] = False
                flash('Регистрация успешна!', 'success')
                next_page = request.args.get('next')
                return redirect(next_page or url_for('index'))
            else:
                flash('Ошибка при регистрации. Попробуйте позже.', 'danger')
        return render_template('register.html')
    
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        return redirect(url_for('market'))
        # вход
        if request.method == 'POST':
            email = request.form.get('email', '').strip()
            password = request.form.get('password', '')
            if not email or not password:
                flash('Email и пароль обязательны.', 'danger')
                return render_template('login.html')
            user = get_user_by_email(email)
            if user and check_password_hash(user['password_hash'], password):
                session['user_id'] = user['id']
                session['email'] = user['email']
                session['is_admin'] = user['is_admin']
                flash('С возвращением!', 'success')
                next_page = request.args.get('next')
                return redirect(next_page or url_for('index'))
            else:
                flash('Неверный email или пароль.', 'danger')
        return render_template('login.html')
    
    @app.route('/logout')
    def logout():
        # выход
        session.clear()
        flash('Вы вышли из системы.', 'info')
        return redirect(url_for('market'))
    
    @app.route('/product/<int:product_id>')
    def product_detail(product_id):
        return redirect(url_for('market'))
        from models import get_product_by_id
        product = get_product_by_id(product_id)
        if not product:
            abort(404)
        return render_template('product_detail.html', product=product)
    
    @app.route('/add-to-cart/<int:product_id>', methods=['POST'])
    @login_required
    def add_to_cart_route(product_id):
        return redirect(url_for('market'))
        # добавление в корзину
        quantity = request.form.get('quantity', 1, type=int)
        if quantity <= 0:
            flash('Количество должно быть больше 0.', 'danger')
            return redirect(request.referrer or url_for('catalog'))
        product = get_product_by_id(product_id)
        if not product:
            flash('Товар не найден.', 'warning')
            return redirect(request.referrer or url_for('catalog'))
        if product.get('stock', 0) is not None and quantity > product['stock']:
            flash(f'Недостаточно товара на складе. Доступно: {product["stock"]}', 'warning')
            return redirect(request.referrer or url_for('catalog'))
        try:
            result = add_to_cart(session['user_id'], product_id, quantity)
            if result:
                flash(f'Товар добавлен в корзину (количество: {result})', 'success')
            else:
                flash('Ошибка при добавлении товара в корзину.', 'danger')
        except Exception as e:
            flash(str(e), 'danger')
        
        return redirect(request.referrer or url_for('catalog'))
    
    @app.route('/cart')
    @login_required
    def cart():
        return redirect(url_for('market'))
        # корзина
        items = get_cart_items(session['user_id'])
        total = get_cart_total(session['user_id'])
        return render_template('cart.html', items=items, total=total)
    
    @app.route('/update-cart-item/<int:item_id>', methods=['POST'])
    @login_required
    def update_cart_item_route(item_id):
        return redirect(url_for('market'))
        # обновление кол-ва
        quantity = request.form.get('quantity', 0, type=int)
        success = update_cart_item(item_id, quantity)
        if success:
            if quantity <= 0:
                flash('Товар удален из корзины.', 'success')
            else:
                flash('Количество товара обновлено.', 'success')
        else:
            flash('Ошибка при обновлении товара.', 'danger')
        
        return redirect(url_for('cart'))
    
    @app.route('/remove-from-cart/<int:item_id>', methods=['POST'])
    @login_required
    def remove_from_cart_route(item_id):
        return redirect(url_for('market'))
        # удаление товара
        success = remove_from_cart(item_id)
        if success:
            flash('Товар удален из корзины.', 'success')
        else:
            flash('Ошибка при удалении товара.', 'danger')
        return redirect(url_for('cart'))
    
    @app.route('/place-order', methods=['POST'])
    @login_required
    def place_order_route():
        return redirect(url_for('market'))
        # оформление заказа
        items = get_cart_items(session['user_id'])
        if not items:
            flash('Корзина пуста. Добавьте товары перед оформлением заказа.', 'warning')
            return redirect(url_for('cart'))
        changed = False
        for item in list(items):
            if item['stock'] <= 0:
                remove_from_cart(item['id'])
                changed = True
            elif item['quantity'] > item['stock']:
                update_cart_item(item['id'], item['stock'])
                changed = True

        if changed:
            flash('Некоторые товары были исключены/количество скорректировано из-за наличия на складе.', 'warning')
            items = get_cart_items(session['user_id'])
            if not items:
                flash('После проверки наличия корзина пуста.', 'warning')
                return redirect(url_for('cart'))
        try:
            order_id = place_order(session['user_id'])
            if order_id:
                flash(f'Заказ #{order_id} успешно оформлен!', 'success')
                return redirect(url_for('order_detail', order_id=order_id))
            else:
                flash('Ошибка при оформлении заказа.', 'danger')
        except Exception as e:
            flash(str(e), 'danger')
        return redirect(url_for('cart'))
    
    @app.route('/my-orders')
    @login_required
    def my_orders():
        return redirect(url_for('market'))
        # кабинет
        orders = get_user_orders(session['user_id'])
        for order in orders:
            items_list = order['items'] if 'items' in order else []
            order['total'] = round(sum(
                item['quantity'] * item['price_at_time'] 
                for item in items_list
            ), 2)
        
        return render_template('my_orders.html', orders=orders)

    @app.route('/order/<int:order_id>')
    @login_required
    def order_detail(order_id):
        return redirect(url_for('market'))
        order = get_order_details(order_id)
        if not order:
            abort(404)
        if not session.get('is_admin', False) and order.get('user_id') != session.get('user_id'):
            abort(403)

        total = round(sum(
            item['quantity'] * item['price_at_time']
            for item in order.get('items', [])
        ), 2)
        return render_template('order_detail.html', order=order, total=total)

    @app.route('/admin_panel/12000')
    @admin_required
    def admin_dashboard():
        # админка
        stats = get_admin_system_stats()
        return render_template(
            'admin_dashboard.html',
            stats=stats,
            admin_client_ip=request_client_ip(),
            admin_host=request_host_name()
        )

    @app.route('/admin_panel/12000/system')
    @admin_required
    def admin_system():
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        per_page = min(max(per_page, 1), 100)
        offset = (page - 1) * per_page
        users = get_admin_users(limit=per_page, offset=offset)
        stats = get_admin_system_stats()
        return render_template(
            'admin_system.html',
            users=users,
            stats=stats,
            page=page,
            per_page=per_page,
            has_prev=page > 1,
            has_next=len(users) == per_page,
            admin_client_ip=request_client_ip(),
            admin_host=request_host_name()
        )

    @app.route('/admin_panel/12000/orders')
    @admin_required
    def admin_orders():
        # упрвлен заказами
        orders = get_all_orders(include_cart=False)
        for order in orders:
            order['status_label'] = ORDER_STATUS_LABELS.get(order.get('status'), order.get('status'))
            details = get_order_details(order['id'])
            items_list = details['items'] if details and 'items' in details else []
            order['items'] = items_list
            order['total'] = round(sum(
                item['quantity'] * item['price_at_time']
                for item in items_list
            ), 2)
        return render_template('admin_orders.html', orders=orders)

    @app.route('/admin_panel/12000/order/<int:order_id>/update-status', methods=['POST'])
    @admin_required
    def admin_update_order_status(order_id):
        # обнавление статуса
        new_status = request.form.get('new_status')
        success = update_order_status(order_id, new_status, changed_by=session.get('user_id'), note='admin_update')
        if success:
            flash('Статус заказа обновлен.', 'success')
        else:
            flash('Не удалось обновить статус заказа.', 'danger')
        return redirect(url_for('admin_orders'))

    @app.route('/admin_panel/12000/products', methods=['GET', 'POST'])
    @admin_required
    def admin_products():
        # упр товарами
        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()
            price = request.form.get('price')
            stock = request.form.get('stock')
            image_file = request.files.get('image')
            try:
                price = float(price) if price is not None else 0
            except (TypeError, ValueError):
                price = 0

            price = round(price, 2)
            try:
                stock = int(stock) if stock is not None else 0
            except (TypeError, ValueError):
                stock = 0
            product_id = create_product(name, description, price, stock)
            if product_id:
                image_saved = save_product_image(image_file, product_id)
                if image_saved is False:
                    flash('Товар добавлен. Картинка не загружена: используйте PNG.', 'warning')
                elif image_saved:
                    flash('Товар и картинка успешно добавлены.', 'success')
                else:
                    flash('Товар добавлен.', 'success')
            else:
                flash('Не удалось добавить товар.', 'danger')
            return redirect(url_for('admin_products'))

        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        if per_page > 100:
            per_page = 100
        offset = (page - 1) * per_page
        products = get_all_products(limit=per_page, offset=offset)
        has_next = len(products) == per_page
        return render_template(
            'admin_products.html',
            products=products,
            page=page,
            per_page=per_page,
            has_next=has_next,
            has_prev=page > 1
        )

    @app.route('/admin_panel/12000/product/<int:product_id>/edit', methods=['GET', 'POST'])
    @admin_required
    def admin_edit_product(product_id):
        # редактирование товара
        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()
            price = request.form.get('price')
            stock = request.form.get('stock')
            image_file = request.files.get('image')
            try:
                price = float(price) if price is not None else 0
            except (TypeError, ValueError):
                price = 0
            price = round(price, 2)
            try:
                stock = int(stock) if stock is not None else 0
            except (TypeError, ValueError):
                stock = 0
            success = update_product(
                product_id,
                name=name,
                description=description,
                price=price,
                stock=stock
            )
            if success:
                image_saved = save_product_image(image_file, product_id)
                if image_saved is False:
                    flash('Товар обновлен. Картинка не загружена: используйте PNG.', 'warning')
                elif image_saved:
                    flash('Товар и картинка обновлены.', 'success')
                else:
                    flash('Товар обновлен.', 'success')
            else:
                flash('Не удалось обновить товар.', 'danger')
            return redirect(url_for('admin_products'))
        product = get_product_by_id(product_id)
        if not product:
            abort(404)
        return render_template('admin_product_edit.html', product=product)

    @app.route('/admin_panel/12000/product/<int:product_id>/delete', methods=['POST'])
    @admin_required
    def admin_delete_product(product_id):
        # удаление товара через админа
        success = delete_product(product_id)
        if success:
            flash('Товар удален.', 'success')
        else:
            flash('Не удалось удалить товар.', 'danger')
        return redirect(url_for('admin_products'))

    @app.route('/admin')
    def legacy_admin_dashboard():
        return redirect(url_for('admin_dashboard'))

    @app.route('/admin/system')
    def legacy_admin_system():
        return redirect(url_for('admin_system'))

    @app.route('/admin/orders')
    def legacy_admin_orders():
        return redirect(url_for('admin_orders'))

    @app.route('/admin/products')
    def legacy_admin_products():
        return redirect(url_for('admin_products'))

    @app.route('/admin/product/<int:product_id>/edit')
    def legacy_admin_edit_product(product_id):
        return redirect(url_for('admin_edit_product', product_id=product_id))

    @app.route('/admin/order/<int:order_id>/update-status', methods=['POST'])
    @admin_required
    def legacy_admin_update_order_status(order_id):
        return admin_update_order_status(order_id)

    @app.route('/admin/product/<int:product_id>/delete', methods=['POST'])
    @admin_required
    def legacy_admin_delete_product(product_id):
        return admin_delete_product(product_id)
    
    # проверка
    @app.route('/health')
    def health_check():
        return {'status': 'ok', 'message': 'Приложение работает'}, 200

# ошибки
def register_error_handlers(app):
    # ошибка 400
    @app.errorhandler(404)
    def not_found_error(error):
        logger.warning(f"Страница не найдена: {error}")
        return render_template('404.html'), 404

    @app.errorhandler(403)
    def forbidden_error(error):
        logger.warning(f"Доступ запрещен: {error}")
        return render_template('403.html'), 403
    
    # ошибак 500
    @app.errorhandler(500)
    def internal_error(error):
        logger.exception("Внутренняя ошибка сервера")
        return render_template('500.html', message='Внутренняя ошибка сервера. Повторите попытку позже.'), 500
    
    # обработка искл
    @app.errorhandler(Exception)
    def handle_exception(error):
        logger.error(f"Необработанное исключение: {error}")
        return render_template('500.html', message='Произошла непредвиденная ошибка. Повторите попытку позже.'), 500

# запуск
app = create_app()
import atexit
@atexit.register
def shutdown():
    close_all_connections()
    logger.info("Приложение остановлено")
if __name__ == '__main__':
    logger.info("Запуск Flask-приложения на http://localhost:5000")
    app.run(host='0.0.0.0', port=5000, debug=app.config['DEBUG'])
