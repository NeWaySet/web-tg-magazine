-- Создание таблицы пользователей
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    email_encrypted TEXT,
    email_lookup_hash VARCHAR(64),
    password_hash VARCHAR(255) NOT NULL,
    is_admin BOOLEAN DEFAULT FALSE,
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Создание таблицы товаров
CREATE TABLE products (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    price DECIMAL(10, 2) NOT NULL,
    stock INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Создание таблицы заказов
CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    status VARCHAR(50) DEFAULT 'cart',
    delivery_address_encrypted TEXT,
    payment_snapshot_encrypted TEXT,
    order_snapshot_encrypted TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_status CHECK (status IN ('cart', 'processing', 'confirmed', 'delivering', 'received', 'new', 'completed', 'cancelled')),
    CONSTRAINT fk_orders_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE order_status_history (
    id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL,
    old_status VARCHAR(50),
    new_status VARCHAR(50) NOT NULL,
    changed_by INTEGER,
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_order_status_history_order FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    CONSTRAINT fk_order_status_history_user FOREIGN KEY (changed_by) REFERENCES users(id) ON DELETE SET NULL
);

-- Создание таблицы элементов заказа
CREATE TABLE order_items (
    id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    price_at_time DECIMAL(10, 2) NOT NULL,
    CONSTRAINT fk_order_items_order FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    CONSTRAINT fk_order_items_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

-- Создание индексов для ускорения поиска
CREATE INDEX idx_orders_user_id ON orders(user_id);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_order_items_order_id ON order_items(order_id);
CREATE INDEX idx_order_items_product_id ON order_items(product_id);
CREATE INDEX idx_users_email ON users(email);
CREATE UNIQUE INDEX idx_users_email_lookup_hash ON users(email_lookup_hash);
CREATE INDEX idx_products_name ON products(name);
CREATE INDEX idx_order_status_history_order_id ON order_status_history(order_id);

-- Добавление тестового пользователя-админа
-- Пароль admin 
INSERT INTO users (email, password_hash, is_admin) 
VALUES ('admin@example.com', 'scrypt:32768:8:1$12BkhQuyF7T7DffE$1897ca521f9656ba866972438766f400c9ebefda3b77ecf63539dee4447f7a7329dd3b7e82a09785fa8ffb2830df4fd3d767829c5d4f733bfc7830f99eae92a2', TRUE);

-- Добавление тестовых товаров
INSERT INTO products (name, description, price, stock) VALUES
('Ноутбук', 'Ноутбук Legion', 89999.00, 15),
('Смартфон', 'Samsung s22+', 49999.00, 30),
('Наушники', 'Airpods max', 12999.00, 50),
('Клавиатура', 'varmilo mechanical', 7999.00, 40),
('Мышь', 'мышь ardor gaming', 4999.00, 60),
('Монитор', 'samsung odysey ', 34999.00, 20),
('Веб-камера', 'HD веб-камера lg logi', 5999.00, 35),
('SSD диск', 'ssd crucial 1TB', 8999.00, 45);
