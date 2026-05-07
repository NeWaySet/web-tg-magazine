from db import get_db_connection, release_db_connection
from werkzeug.security import generate_password_hash, check_password_hash
from security import (
    decrypt_json, decrypt_value, email_lookup_hash, email_lookup_hashes, encrypt_json, encrypt_value,
    masked_email_value, normalize_email
)
import logging
logger = logging.getLogger(__name__)
ORDER_STATUSES = ['cart', 'processing', 'confirmed', 'delivering', 'received', 'new', 'completed', 'cancelled']


def row_to_dict(cursor, row):
    if not row:
        return None
    columns = [desc[0] for desc in cursor.description]
    return dict(zip(columns, row))


def decrypt_user_row(user):
    if not user:
        return user
    if user.get('email_encrypted'):
        user['email'] = decrypt_value(user['email_encrypted']) or user.get('email')
    return user


def decrypt_order_row(order):
    if not order:
        return order
    order['delivery_address'] = decrypt_value(order.get('delivery_address_encrypted')) or ''
    order['payment_snapshot'] = decrypt_json(order.get('payment_snapshot_encrypted'), {}) or {}
    order['order_snapshot'] = decrypt_json(order.get('order_snapshot_encrypted'), {}) or {}
    return order


def ensure_order_status_schema():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return False
        cursor = connection.cursor()
        cursor.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS chk_status")
        cursor.execute("""
            ALTER TABLE orders
            ADD CONSTRAINT chk_status
            CHECK (status IN ('cart', 'processing', 'confirmed', 'delivering', 'received', 'new', 'completed', 'cancelled'))
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS order_status_history (
                id SERIAL PRIMARY KEY,
                order_id INTEGER NOT NULL,
                old_status VARCHAR(50),
                new_status VARCHAR(50) NOT NULL,
                changed_by INTEGER,
                note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_order_status_history_order
                    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
                CONSTRAINT fk_order_status_history_user
                    FOREIGN KEY (changed_by) REFERENCES users(id) ON DELETE SET NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_status_history_order_id ON order_status_history(order_id)")
        cursor.execute("""
            INSERT INTO order_status_history (order_id, old_status, new_status, note, created_at)
            SELECT o.id, NULL, o.status, 'initial', o.created_at
            FROM orders o
            WHERE o.status != 'cart'
              AND NOT EXISTS (
                  SELECT 1 FROM order_status_history h WHERE h.order_id = o.id
              )
        """)
        connection.commit()
        return True
    except Exception as e:
        logger.error(f"Order status schema migration error: {e}")
        if connection:
            connection.rollback()
        return False
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def ensure_encryption_schema():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return False
        cursor = connection.cursor()
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_encrypted TEXT")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_lookup_hash VARCHAR(64)")
        cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_address_encrypted TEXT")
        cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_snapshot_encrypted TEXT")
        cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS order_snapshot_encrypted TEXT")

        cursor.execute("SELECT id, email, email_encrypted, email_lookup_hash FROM users")
        for user_id, email, email_encrypted, lookup_hash in cursor.fetchall():
            plain_email = decrypt_value(email_encrypted) if email_encrypted else None
            if not plain_email and email and not str(email).startswith('enc:'):
                plain_email = email
            if not plain_email:
                continue
            normalized_email = normalize_email(plain_email)
            new_lookup_hash = lookup_hash or email_lookup_hash(normalized_email)
            new_encrypted_email = email_encrypted or encrypt_value(normalized_email)
            cursor.execute(
                """
                UPDATE users
                SET email = %s, email_encrypted = %s, email_lookup_hash = %s
                WHERE id = %s
                """,
                (masked_email_value(new_lookup_hash), new_encrypted_email, new_lookup_hash, user_id)
            )

        cursor.execute("""
            SELECT id
            FROM orders
            WHERE status != 'cart' AND order_snapshot_encrypted IS NULL
        """)
        order_ids = [row[0] for row in cursor.fetchall()]
        for order_id in order_ids:
            cursor.execute("""
                SELECT oi.id, oi.product_id, oi.quantity, oi.price_at_time,
                       p.name, p.description
                FROM order_items oi
                JOIN products p ON oi.product_id = p.id
                WHERE oi.order_id = %s
                ORDER BY oi.id ASC
            """, (order_id,))
            item_columns = [desc[0] for desc in cursor.description]
            snapshot_items = [dict(zip(item_columns, item)) for item in cursor.fetchall()]
            if not snapshot_items:
                continue
            for item in snapshot_items:
                item['quantity'] = int(item.get('quantity') or 0)
                item['price_at_time'] = float(item.get('price_at_time') or 0)
            snapshot_total = round(sum(
                item['quantity'] * item['price_at_time']
                for item in snapshot_items
            ), 2)
            cursor.execute(
                "UPDATE orders SET order_snapshot_encrypted = %s WHERE id = %s",
                (encrypt_json({'items': snapshot_items, 'total': snapshot_total}), order_id)
            )
            cursor.execute("DELETE FROM order_items WHERE order_id = %s", (order_id,))

        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lookup_hash ON users(email_lookup_hash)")
        connection.commit()
        return True
    except Exception as e:
        logger.error(f"Encryption schema migration error: {e}")
        if connection:
            connection.rollback()
        return False
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)

#ПОЛУЧЕНИЕ ПО ид
def get_user_by_id(user_id):
    connection = None
    cursor = None  
    try:
        connection = get_db_connection()
        if not connection:
            return None
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        return decrypt_user_row(row_to_dict(cursor, row))
    except Exception as e:
        logger.error(f"Ошибка получения пользователя по ID {user_id}: {e}")
        return None
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)

# получение по емил
def get_user_by_email(email):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return None
        cursor = connection.cursor()
        lookup_hashes = email_lookup_hashes(email)
        placeholders = ', '.join(['%s'] * len(lookup_hashes))
        cursor.execute(f"SELECT * FROM users WHERE email_lookup_hash IN ({placeholders})", tuple(lookup_hashes))
        row = cursor.fetchone()
        if not row:
            cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
            row = cursor.fetchone()
        return decrypt_user_row(row_to_dict(cursor, row))
    except Exception as e:
        logger.error(f"Ошибка получения пользователя по email {email}: {e}")
        return None 
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def get_admin_users(limit=100, offset=0):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return []
        cursor = connection.cursor()
        cursor.execute("""
            SELECT u.id, u.email, u.email_encrypted, u.email_lookup_hash, u.is_admin, u.registered_at,
                   COUNT(DISTINCT o.id) FILTER (WHERE o.status != 'cart') AS orders_count,
                   COALESCE(SUM(oi.quantity * oi.price_at_time) FILTER (WHERE o.status != 'cart'), 0) AS total_spent
            FROM users u
            LEFT JOIN orders o ON o.user_id = u.id
            LEFT JOIN order_items oi ON oi.order_id = o.id
            GROUP BY u.id, u.email, u.email_encrypted, u.email_lookup_hash, u.is_admin, u.registered_at
            ORDER BY u.registered_at DESC
            LIMIT %s OFFSET %s
        """, (limit, offset))
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        users = [decrypt_user_row(dict(zip(columns, row))) for row in rows]
        cursor.execute("""
            SELECT user_id, order_snapshot_encrypted
            FROM orders
            WHERE status != 'cart' AND order_snapshot_encrypted IS NOT NULL
        """)
        encrypted_totals = {}
        for user_id, snapshot_encrypted in cursor.fetchall():
            snapshot = decrypt_json(snapshot_encrypted, {}) or {}
            encrypted_totals[user_id] = encrypted_totals.get(user_id, 0) + float(snapshot.get('total') or 0)
        for user in users:
            user['total_spent'] = float(user.get('total_spent') or 0) + encrypted_totals.get(user['id'], 0)
        return users
    except Exception as e:
        logger.error(f"Admin users query error: {e}")
        return []
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def get_admin_system_stats():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return {}
        cursor = connection.cursor()
        cursor.execute("""
            SELECT
                (SELECT COUNT(*) FROM users) AS users_count,
                (SELECT COUNT(*) FROM products) AS products_count,
                (SELECT COUNT(*) FROM orders WHERE status != 'cart') AS orders_count,
                (SELECT COUNT(*) FROM orders WHERE status = 'cart') AS carts_count,
                (SELECT COUNT(*) FROM products WHERE stock <= 5) AS low_stock_count
        """)
        row = cursor.fetchone()
        columns = [desc[0] for desc in cursor.description]
        stats = dict(zip(columns, row)) if row else {}

        cursor.execute("""
            SELECT order_snapshot_encrypted
            FROM orders
            WHERE status != 'cart' AND order_snapshot_encrypted IS NOT NULL
        """)
        encrypted_revenue = 0
        for snapshot_row in cursor.fetchall():
            snapshot = decrypt_json(snapshot_row[0], {}) or {}
            encrypted_revenue += float(snapshot.get('total') or 0)

        cursor.execute("""
            SELECT COALESCE(SUM(oi.quantity * oi.price_at_time), 0)
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            WHERE o.status != 'cart' AND o.order_snapshot_encrypted IS NULL
        """)
        legacy_revenue_row = cursor.fetchone()
        stats['revenue'] = encrypted_revenue + float(legacy_revenue_row[0] if legacy_revenue_row else 0)

        cursor.execute("""
            SELECT id, name, price, stock
            FROM products
            WHERE stock <= 5
            ORDER BY stock ASC, name ASC
            LIMIT 20
        """)
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        stats['low_stock_products'] = [dict(zip(columns, row)) for row in rows]
        return stats
    except Exception as e:
        logger.error(f"Admin system stats query error: {e}")
        return {}
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)

# для создния нового пользователя
def create_user(email, password_hash, is_admin=False):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return None
        cursor = connection.cursor()
        normalized_email = normalize_email(email)
        lookup_hash = email_lookup_hash(normalized_email)
        cursor.execute(
            """
            INSERT INTO users (email, email_encrypted, email_lookup_hash, password_hash, is_admin)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (masked_email_value(lookup_hash), encrypt_value(normalized_email), lookup_hash, password_hash, is_admin)
        )
        connection.commit()
        row = cursor.fetchone()
        return row[0] if row else None
    except Exception as e:
        logger.error(f"Ошибка создания пользователя {email}: {e}")
        if connection:
            connection.rollback()
        return None
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)

# изменение пользователя
def update_user(user_id, **kwargs):
    if not kwargs:
        return False
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return False
        fields = []
        values = []
        for key, value in kwargs.items():
            if key == 'email':
                normalized_email = normalize_email(value)
                lookup_hash = email_lookup_hash(normalized_email)
                fields.extend(['email = %s', 'email_encrypted = %s', 'email_lookup_hash = %s'])
                values.extend([masked_email_value(lookup_hash), encrypt_value(normalized_email), lookup_hash])
            elif key in ['password_hash', 'is_admin']:
                fields.append(f"{key} = %s")
                values.append(value)
        
        if not fields:
            return False
        values.append(user_id)
        query = f"UPDATE users SET {', '.join(fields)} WHERE id = %s RETURNING id"
        cursor = connection.cursor()
        cursor.execute(query, values)
        connection.commit()
        row = cursor.fetchone()
        return row is not None
    except Exception as e:
        logger.error(f"Ошибка обновления пользователя {user_id}: {e}")
        if connection:
            connection.rollback()
        return False 
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)



# поучение всех продуктов для каталога
def get_all_products(limit=20, offset=0):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return []
            
        cursor = connection.cursor()
        cursor.execute(
            "SELECT * FROM products ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (limit, offset)
        )
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        
        return [dict(zip(columns, row)) for row in rows]
    except Exception as e:
        logger.error(f"Ошибка получения товаров: {e}")
        return [] 
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)

# получение техники по ид
def get_product_by_id(product_id):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return None 
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM products WHERE id = %s", (product_id,))
        row = cursor.fetchone()
        if row:
            columns = [desc[0] for desc in cursor.description]
            return dict(zip(columns, row))
        return None
    except Exception as e:
        logger.error(f"Ошибка получения товара по ID {product_id}: {e}")
        return None
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)

# создание продукта
def create_product(name, description, price, stock):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return None
        cursor = connection.cursor()
        cursor.execute(
            "INSERT INTO products (name, description, price, stock) VALUES (%s, %s, %s, %s) RETURNING id",
            (name, description, price, stock)
        )
        connection.commit()
        row = cursor.fetchone()
        return row[0] if row else None
    except Exception as e:
        logger.error(f"Ошибка создания товара '{name}': {e}")
        if connection:
            connection.rollback()
        return None
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)

# изменение продукта
def update_product(product_id, **kwargs):
    if not kwargs:
        return False
    connection = None
    cursor = None
    
    try:
        connection = get_db_connection()
        if not connection:
            return False
        fields = []
        values = []
        for key, value in kwargs.items():
            if key in ['name', 'description', 'price', 'stock']:
                fields.append(f"{key} = %s")
                values.append(value)
        if not fields:
            return False
        
        values.append(product_id)
        query = f"UPDATE products SET {', '.join(fields)} WHERE id = %s RETURNING id"
        cursor = connection.cursor()
        cursor.execute(query, values)
        connection.commit()
        row = cursor.fetchone()
        return row is not None
    except Exception as e:
        logger.error(f"Ошибка обновления товара {product_id}: {e}")
        if connection:
            connection.rollback()
        return False
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)

# удаление продукта
def delete_product(product_id):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return False  
        cursor = connection.cursor()
        cursor.execute("DELETE FROM products WHERE id = %s RETURNING id", (product_id,))
        connection.commit()
        row = cursor.fetchone()
        return row is not None
        
    except Exception as e:
        logger.error(f"Ошибка удаления товара {product_id}: {e}")
        if connection:
            connection.rollback()
        return False
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


# создание корзины
def get_or_create_cart(user_id):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return None
        cursor = connection.cursor()
        cursor.execute(
            "SELECT id FROM orders WHERE user_id = %s AND status = 'cart' ORDER BY created_at DESC LIMIT 1",
            (user_id,)
        )
        row = cursor.fetchone()
        
        if row:
            return row[0]
        cursor.execute(
            "INSERT INTO orders (user_id, status) VALUES (%s, 'cart') RETURNING id",
            (user_id,)
        )
        connection.commit() 
        row = cursor.fetchone()
        return row[0] if row else None
    except Exception as e:
        logger.error(f"Ошибка получения/создания корзины для пользователя {user_id}: {e}")
        if connection:
            connection.rollback()
        return None
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)

# добавление товаров в корзину
def add_to_cart(user_id, product_id, quantity=1):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return None
        # проверка
        cursor = connection.cursor()
        cursor.execute("SELECT stock, price FROM products WHERE id = %s", (product_id,))
        row = cursor.fetchone()
        
        if not row:
            raise Exception("Товар не найден")
        stock, price = row
        if stock < quantity:
            raise Exception(f"Недостаточно товара на складе. Доступно: {stock}")
        
        # получение
        cart_id = get_or_create_cart(user_id)
        if not cart_id:
            raise Exception("Не удалось получить корзину")
        cursor.execute(
            "SELECT quantity FROM order_items WHERE order_id = %s AND product_id = %s",
            (cart_id, product_id)
        )
        existing_row = cursor.fetchone()
        if existing_row:
            new_quantity = existing_row[0] + quantity
            cursor.execute(
                "UPDATE order_items SET quantity = %s WHERE order_id = %s AND product_id = %s RETURNING quantity",
                (new_quantity, cart_id, product_id)
            )
        else:
            # Добавляем новый товар
            cursor.execute(
                "INSERT INTO order_items (order_id, product_id, quantity, price_at_time) VALUES (%s, %s, %s, %s) RETURNING quantity",
                (cart_id, product_id, quantity, price)
            )
        
        connection.commit()
        row = cursor.fetchone()
        return row[0] if row else None
    except Exception as e:
        logger.error(f"Ошибка добавления товара в корзину: {e}")
        if connection:
            connection.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)

# полуение товаров из карзины пользователя
def get_cart_items(user_id):
    connection = None
    cursor = None
    
    try:
        connection = get_db_connection()
        if not connection:
            return []
            
        cursor = connection.cursor()
        cart_id = get_or_create_cart(user_id)
        if not cart_id:
            return []
        cursor.execute("""
            SELECT oi.id, oi.product_id, oi.quantity, oi.price_at_time,
                   p.name, p.description, p.stock
            FROM order_items oi
            JOIN products p ON oi.product_id = p.id
            WHERE oi.order_id = %s
        """, (cart_id,))
        
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in rows]
        
    except Exception as e:
        logger.error(f"Ошибка получения товаров корзины: {e}")
        return []
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)

# обновление кол-ва товара в орзине
def update_cart_item(order_item_id, quantity):
    connection = None
    cursor = None
    
    try:
        connection = get_db_connection()
        if not connection:
            return False
        cursor = connection.cursor()
        if quantity <= 0:
            cursor.execute("DELETE FROM order_items WHERE id = %s RETURNING id", (order_item_id,))
        else:
            cursor.execute(
                "UPDATE order_items SET quantity = %s WHERE id = %s RETURNING id",
                (quantity, order_item_id)
            )
        connection.commit()
        return cursor.fetchone() is not None
    except Exception as e:
        logger.error(f"Ошибка обновления элемента корзины {order_item_id}: {e}")
        if connection:
            connection.rollback()
        return False
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)

# удаоление товара
def remove_from_cart(order_item_id):
    return update_cart_item(order_item_id, 0)

# оформление заказа
def place_order(user_id, delivery_address=None, payment_snapshot=None):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return None    
        cursor = connection.cursor()
        cart_id = get_or_create_cart(user_id)
        if not cart_id:
            return None
        cursor.execute(
            "SELECT COUNT(*) FROM order_items WHERE order_id = %s",
            (cart_id,)
        )
        if cursor.fetchone()[0] == 0:
            raise Exception("Корзина пуста")
        

        cursor.execute("""
            UPDATE order_items oi
            SET price_at_time = p.price
            FROM products p
            WHERE oi.product_id = p.id AND oi.order_id = %s
        """, (cart_id,))
        cursor.execute("""
            SELECT oi.id, oi.product_id, oi.quantity, oi.price_at_time,
                   p.name, p.description
            FROM order_items oi
            JOIN products p ON oi.product_id = p.id
            WHERE oi.order_id = %s
            ORDER BY oi.id ASC
        """, (cart_id,))
        item_columns = [desc[0] for desc in cursor.description]
        snapshot_items = [dict(zip(item_columns, item)) for item in cursor.fetchall()]
        for item in snapshot_items:
            item['quantity'] = int(item.get('quantity') or 0)
            item['price_at_time'] = float(item.get('price_at_time') or 0)
        snapshot_total = round(sum(
            item['quantity'] * item['price_at_time']
            for item in snapshot_items
        ), 2)
        order_snapshot = {
            'items': snapshot_items,
            'total': snapshot_total
        }
        cursor.execute("""
            UPDATE products p
            SET stock = p.stock - oi.quantity
            FROM order_items oi
            WHERE oi.order_id = %s AND oi.product_id = p.id
        """, (cart_id,))
        cursor.execute("""
            UPDATE orders
            SET status = 'processing',
                delivery_address_encrypted = %s,
                payment_snapshot_encrypted = %s,
                order_snapshot_encrypted = %s
            WHERE id = %s
            RETURNING id
        """, (
            encrypt_value((delivery_address or '').strip()),
            encrypt_json(payment_snapshot or {}),
            encrypt_json(order_snapshot),
            cart_id
        ))
        row = cursor.fetchone()
        if not row:
            connection.rollback()
            return None
        cursor.execute("DELETE FROM order_items WHERE order_id = %s", (cart_id,))
        cursor.execute("""
            INSERT INTO order_status_history (order_id, old_status, new_status, changed_by, note)
            VALUES (%s, 'cart', 'processing', %s, 'order_created')
        """, (cart_id, user_id))
        connection.commit()
        return row[0] if row else None
        
    except Exception as e:
        logger.error(f"Ошибка оформления заказа для пользователя {user_id}: {e}")
        if connection:
            connection.rollback()
        return None
        
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)

# получение заазов пользователя
def get_user_orders(user_id):
    connection = None
    cursor = None
    
    try:
        connection = get_db_connection()
        if not connection:
            return []
            
        cursor = connection.cursor()
        cursor.execute("""
            SELECT id, user_id, status, created_at,
                   delivery_address_encrypted, payment_snapshot_encrypted, order_snapshot_encrypted
            FROM orders
            WHERE user_id = %s AND status != 'cart'
            ORDER BY created_at DESC
        """, (user_id,))
        
        orders = cursor.fetchall()
        order_columns = [desc[0] for desc in cursor.description]
        result = []
        for order_row in orders:
            order = decrypt_order_row(dict(zip(order_columns, order_row)))
            snapshot = order.get('order_snapshot') or {}
            if snapshot.get('items') is not None:
                order['items'] = snapshot.get('items') or []
            else:
                cursor.execute("""
                    SELECT oi.id, oi.product_id, oi.quantity, oi.price_at_time, p.name
                    FROM order_items oi
                    JOIN products p ON oi.product_id = p.id
                    WHERE oi.order_id = %s
                """, (order['id'],))

                items = cursor.fetchall()
                item_columns = [desc[0] for desc in cursor.description]
                order['items'] = [dict(zip(item_columns, item)) for item in items]
            result.append(order)
        return result 
    except Exception as e:
        logger.error(f"Ошибка получения заказов пользователя {user_id}: {e}")
        return []
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)

# получение инфы о заказах
def get_order_details(order_id):
    connection = None
    cursor = None
    
    try:
        connection = get_db_connection()
        if not connection:
            return None
            
        cursor = connection.cursor()
        cursor.execute("""
            SELECT id, user_id, status, created_at,
                   delivery_address_encrypted, payment_snapshot_encrypted, order_snapshot_encrypted
            FROM orders
            WHERE id = %s
        """, (order_id,))
        
        row = cursor.fetchone()
        if not row:
            return None
        
        columns = [desc[0] for desc in cursor.description]
        order = decrypt_order_row(dict(zip(columns, row)))
        snapshot = order.get('order_snapshot') or {}
        if snapshot.get('items') is not None:
            order['items'] = snapshot.get('items') or []
        else:
            cursor.execute("""
                SELECT oi.id, oi.product_id, oi.quantity, oi.price_at_time, p.name
                FROM order_items oi
                JOIN products p ON oi.product_id = p.id
                WHERE oi.order_id = %s
            """, (order_id,))
            items = cursor.fetchall()
            item_columns = [desc[0] for desc in cursor.description]
            order['items'] = [dict(zip(item_columns, item)) for item in items]
        return order
        
    except Exception as e:
        logger.error(f"Ошибка получения деталей заказа {order_id}: {e}")
        return None
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def get_order_status_history(order_id):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return []
        cursor = connection.cursor()
        cursor.execute("""
            SELECT h.id, h.order_id, h.old_status, h.new_status, h.changed_by,
                   h.note, h.created_at, u.email AS changed_by_email,
                   u.email_encrypted AS changed_by_email_encrypted
            FROM order_status_history h
            LEFT JOIN users u ON u.id = h.changed_by
            WHERE h.order_id = %s
            ORDER BY h.created_at ASC, h.id ASC
        """, (order_id,))
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        result = []
        for row in rows:
            item = dict(zip(columns, row))
            if item.get('changed_by_email_encrypted'):
                item['changed_by_email'] = decrypt_value(item['changed_by_email_encrypted'])
            result.append(item)
        return result
    except Exception as e:
        logger.error(f"Order status history query error for order {order_id}: {e}")
        return []
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)

# получение всех заказов
def get_all_orders(include_cart=False):
    connection = None
    cursor = None
    
    try:
        connection = get_db_connection()
        if not connection:
            return []
        cursor = connection.cursor()
        
        if include_cart:
            cursor.execute("""
                SELECT o.id, o.user_id, o.status, o.created_at, u.email, u.email_encrypted
                FROM orders o
                JOIN users u ON o.user_id = u.id
                ORDER BY o.created_at DESC
            """)
        else:
            cursor.execute("""
                SELECT o.id, o.user_id, o.status, o.created_at, u.email, u.email_encrypted
                FROM orders o
                JOIN users u ON o.user_id = u.id
                WHERE o.status != 'cart'
                ORDER BY o.created_at DESC
            """)
        
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        result = []
        for row in rows:
            order = dict(zip(columns, row))
            if order.get('email_encrypted'):
                order['email'] = decrypt_value(order['email_encrypted'])
            result.append(order)
        return result
    except Exception as e:
        logger.error(f"Ошибка получения всех заказов: {e}")
        return []
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)

# обновление статуса заказа
def update_order_status(order_id, new_status, changed_by=None, note=None):
    valid_statuses = ORDER_STATUSES
    
    if new_status not in valid_statuses:
        logger.error(f"Недопустимый статус заказа: {new_status}")
        return False
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return False
        cursor = connection.cursor()
        cursor.execute("SELECT status FROM orders WHERE id = %s", (order_id,))
        row = cursor.fetchone()
        if not row:
            return False
        old_status = row[0]
        if old_status == new_status:
            return True
        cursor.execute(
            "UPDATE orders SET status = %s WHERE id = %s RETURNING id",
            (new_status, order_id)
        )
        updated_row = cursor.fetchone()
        if not updated_row:
            connection.rollback()
            return False
        cursor.execute("""
            INSERT INTO order_status_history (order_id, old_status, new_status, changed_by, note)
            VALUES (%s, %s, %s, %s, %s)
        """, (order_id, old_status, new_status, changed_by, note))
        connection.commit()
        return True
        
    except Exception as e:
        logger.error(f"Ошибка обновления статуса заказа {order_id}: {e}")
        if connection:
            connection.rollback()
        return False
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)
