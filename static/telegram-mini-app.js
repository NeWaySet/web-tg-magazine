(function () {
    const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
    const state = {
        page: 1,
        hasNext: false,
        products: [],
        cart: { items: [], total: 0 },
        orders: [],
        paymentMethods: [],
        currentPayment: null,
        user: null,
        authenticated: false,
        activeView: 'catalog',
        search: '',
        loading: false
    };

    const els = {
        appShell: document.querySelector('.app-shell'),
        authPanel: document.getElementById('authPanel'),
        authStatus: document.getElementById('authStatus'),
        retryAuthButton: document.getElementById('retryAuthButton'),
        userChip: document.getElementById('userChip'),
        userName: document.getElementById('userName'),
        productGrid: document.getElementById('productGrid'),
        loadMoreButton: document.getElementById('loadMoreButton'),
        searchInput: document.getElementById('searchInput'),
        cartList: document.getElementById('cartList'),
        cartTotal: document.getElementById('cartTotal'),
        cartBadge: document.getElementById('cartBadge'),
        checkoutBar: document.getElementById('checkoutBar'),
        ordersList: document.getElementById('ordersList'),
        sheet: document.getElementById('productSheet'),
        sheetBackdrop: document.getElementById('sheetBackdrop'),
        toast: document.getElementById('toast')
    };

    function formatPrice(value) {
        return new Intl.NumberFormat('ru-RU', {
            style: 'currency',
            currency: 'RUB',
            maximumFractionDigits: 0
        }).format(value || 0);
    }

    function escapeHtml(value) {
        return String(value || '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#039;');
    }

    function notify(message) {
        els.toast.textContent = message;
        els.toast.classList.add('is-visible');
        window.clearTimeout(notify.timer);
        notify.timer = window.setTimeout(() => {
            els.toast.classList.remove('is-visible');
        }, 2300);
    }

    function haptic(type) {
        if (!tg || !tg.HapticFeedback) {
            return;
        }
        if (type === 'error') {
            tg.HapticFeedback.notificationOccurred('error');
        } else if (type === 'success') {
            tg.HapticFeedback.notificationOccurred('success');
        } else {
            tg.HapticFeedback.impactOccurred('light');
        }
    }

    async function api(path, options) {
        const response = await fetch(path, {
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            ...options
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            const error = new Error(data.error || 'Ошибка запроса.');
            error.status = response.status;
            error.data = data;
            throw error;
        }
        return data;
    }

    function updateCartBadge() {
        const count = state.cart.items.reduce((sum, item) => sum + Number(item.quantity || 0), 0);
        els.cartBadge.textContent = count ? String(count) : '';
        els.cartBadge.classList.toggle('is-empty', count === 0);
    }

    function renderAuthState(message) {
        els.appShell.classList.toggle('is-auth-blocked', !state.authenticated);
        els.authPanel.hidden = state.authenticated;
        els.authPanel.classList.toggle('is-visible', !state.authenticated);
        els.userChip.hidden = !state.authenticated;

        if (state.authenticated && state.user) {
            const name = state.user.name || state.user.username || 'Telegram';
            els.userName.textContent = name;
            els.userChip.querySelector('.user-avatar').textContent = name.trim().charAt(0).toUpperCase() || 'T';
        } else {
            els.authStatus.textContent = message || 'Mini App привязывает корзину, оплату и заказы к вашему Telegram-аккаунту.';
        }
    }

    async function authenticate() {
        try {
            const data = await api('/api/telegram/auth', {
                method: 'POST',
                body: JSON.stringify({ initData: tg ? tg.initData : '' })
            });
            state.user = data.user;
            state.authenticated = true;
            renderAuthState();
            return true;
        } catch (error) {
            state.user = null;
            state.authenticated = false;
            renderAuthState(error.message || 'Не удалось войти через Telegram.');
            notify(error.message);
            haptic('error');
            return false;
        }
    }

    function filteredProducts() {
        const query = state.search.trim().toLowerCase();
        if (!query) {
            return state.products;
        }
        return state.products.filter((product) => {
            return `${product.name} ${product.description}`.toLowerCase().includes(query);
        });
    }

    function renderProducts() {
        const products = filteredProducts();
        if (!products.length) {
            els.productGrid.innerHTML = '<div class="empty-state"><strong>Ничего не найдено</strong><span>Попробуйте другой запрос</span></div>';
            els.loadMoreButton.hidden = true;
            return;
        }
        els.productGrid.innerHTML = products.map((product) => `
            <article class="product-card">
                <img class="product-card__image" src="${product.image_url}" alt="${escapeHtml(product.name)}" onerror="this.onerror=null;this.src='${product.fallback_image_url}'">
                <div class="product-card__body">
                    <h2 class="product-card__title">${escapeHtml(product.name)}</h2>
                    <div class="product-card__meta">
                        <span class="price">${formatPrice(product.price)}</span>
                        <span class="stock">${product.stock} шт.</span>
                    </div>
                    <button class="compact-button" type="button" data-action="open-product" data-product-id="${product.id}">Подробнее</button>
                </div>
            </article>
        `).join('');
        els.loadMoreButton.hidden = !state.hasNext || Boolean(state.search.trim());
    }

    function renderCart() {
        updateCartBadge();
        els.cartTotal.textContent = formatPrice(state.cart.total);
        els.checkoutBar.classList.toggle('is-visible', state.cart.items.length > 0);
        if (!state.cart.items.length) {
            els.cartList.innerHTML = '<div class="empty-state"><strong>Корзина пуста</strong><span>Добавьте технику из каталога</span></div>';
            return;
        }
        els.cartList.innerHTML = state.cart.items.map((item) => `
            <article class="cart-item">
                <img class="cart-item__image" src="${item.image_url}" alt="${escapeHtml(item.name)}" onerror="this.onerror=null;this.src='${item.fallback_image_url}'">
                <div class="cart-item__body">
                    <h2 class="cart-item__title">${escapeHtml(item.name)}</h2>
                    <span class="stock">${formatPrice(item.price_at_time)} · ${item.stock} шт.</span>
                    <div class="cart-item__footer">
                        <strong class="price">${formatPrice(item.line_total)}</strong>
                        <div class="qty-control">
                            <button type="button" data-action="cart-dec" data-item-id="${item.id}" aria-label="Уменьшить">−</button>
                            <output>${item.quantity}</output>
                            <button type="button" data-action="cart-inc" data-item-id="${item.id}" aria-label="Увеличить">+</button>
                        </div>
                    </div>
                </div>
            </article>
        `).join('');
    }

    function renderOrders() {
        if (!state.orders.length) {
            els.ordersList.innerHTML = '<div class="empty-state"><strong>Заказов пока нет</strong><span>Оформленные покупки появятся здесь</span></div>';
            return;
        }
        els.ordersList.innerHTML = state.orders.map((order) => {
            const names = order.items.map((item) => `${escapeHtml(item.name)} × ${item.quantity}`).join(', ');
            const date = order.created_at ? new Date(order.created_at).toLocaleDateString('ru-RU') : '';
            return `
                <article class="order-card">
                    <div class="order-card__head">
                        <h3>Заказ #${order.id}</h3>
                        <span class="status status--${escapeHtml(order.status)}">${escapeHtml(order.status_label || order.status)}</span>
                    </div>
                    <p>${names}</p>
                    <div class="order-card__total">
                        <time>${date}</time>
                        <strong>${formatPrice(order.total)}</strong>
                    </div>
                </article>
            `;
        }).join('');
    }

    function switchView(view) {
        state.activeView = view;
        document.querySelectorAll('.view').forEach((el) => {
            el.classList.toggle('is-active', el.id === `view-${view}`);
        });
        document.querySelectorAll('.tab-button').forEach((button) => {
            button.classList.toggle('is-active', button.dataset.view === view);
        });
        if (view === 'cart') {
            loadCart();
        }
        if (view === 'orders') {
            loadOrders();
        }
    }

    async function loadProducts(reset) {
        if (state.loading) {
            return;
        }
        state.loading = true;
        els.loadMoreButton.disabled = true;
        try {
            if (reset) {
                state.page = 1;
                state.products = [];
            }
            const data = await api(`/api/telegram/products?page=${state.page}&per_page=20`);
            state.products = reset ? data.products : state.products.concat(data.products);
            state.hasNext = data.has_next;
            state.page += 1;
            renderProducts();
        } catch (error) {
            notify(error.message);
            haptic('error');
        } finally {
            state.loading = false;
            els.loadMoreButton.disabled = false;
        }
    }

    async function loadCart() {
        try {
            state.cart = await api('/api/telegram/cart');
            renderCart();
        } catch (error) {
            notify(error.message);
            haptic('error');
        }
    }

    async function loadOrders() {
        try {
            const data = await api('/api/telegram/orders');
            state.orders = data.orders || [];
            renderOrders();
        } catch (error) {
            notify(error.message);
            haptic('error');
        }
    }

    async function loadPaymentMethods() {
        const data = await api('/api/telegram/payments/methods');
        state.paymentMethods = data.methods || [];
    }

    async function logout() {
        try {
            await api('/api/market/auth/logout', {
                method: 'POST',
                body: '{}'
            });
        } catch (error) {
            notify(error.message);
        }
        state.user = null;
        state.authenticated = false;
        state.cart = { items: [], total: 0 };
        state.orders = [];
        state.currentPayment = null;
        renderAuthState('Вы вышли. Нажмите повторный вход, чтобы снова авторизоваться через Telegram.');
        renderCart();
        renderOrders();
        switchView('catalog');
    }

    async function refreshAll() {
        if (!state.authenticated) {
            const ok = await authenticate();
            if (!ok) return;
        }
        await loadProducts(true);
        await loadCart();
        if (state.activeView === 'orders') {
            await loadOrders();
        }
        notify('Данные обновлены');
        haptic('success');
    }

    async function openProduct(productId) {
        try {
            const data = await api(`/api/telegram/products/${productId}`);
            const product = data.product;
            els.sheet.innerHTML = `
                <img class="sheet-image" src="${product.image_url}" alt="${escapeHtml(product.name)}" onerror="this.onerror=null;this.src='${product.fallback_image_url}'">
                <div class="sheet-title-row">
                    <h2>${escapeHtml(product.name)}</h2>
                    <button class="icon-button" type="button" data-action="close-sheet" aria-label="Закрыть" title="Закрыть">×</button>
                </div>
                <p class="price">${formatPrice(product.price)}</p>
                <p class="stock">В наличии ${product.stock} шт.</p>
                <p class="sheet-description">${escapeHtml(product.description)}</p>
                <div class="sheet-actions">
                    <input class="quantity-input" id="sheetQuantity" type="number" min="1" max="${product.stock}" value="1" inputmode="numeric">
                    <button class="primary-button" type="button" data-action="add-product" data-product-id="${product.id}" ${product.stock <= 0 ? 'disabled' : ''}>В корзину</button>
                </div>
            `;
            els.sheetBackdrop.hidden = false;
            els.sheet.classList.add('is-open');
            els.sheet.setAttribute('aria-hidden', 'false');
            haptic();
        } catch (error) {
            notify(error.message);
            haptic('error');
        }
    }

    function closeSheet() {
        els.sheet.classList.remove('is-open');
        els.sheet.setAttribute('aria-hidden', 'true');
        window.setTimeout(() => {
            els.sheetBackdrop.hidden = true;
            els.sheet.innerHTML = '';
        }, 180);
    }

    async function addProduct(productId, quantity) {
        try {
            state.cart = await api('/api/telegram/cart', {
                method: 'POST',
                body: JSON.stringify({ product_id: Number(productId), quantity: Number(quantity || 1) })
            });
            state.currentPayment = null;
            renderCart();
            closeSheet();
            notify('Добавлено в корзину');
            haptic('success');
        } catch (error) {
            notify(error.message);
            haptic('error');
        }
    }

    async function updateCartItem(itemId, quantity) {
        try {
            state.cart = await api(`/api/telegram/cart/${itemId}`, {
                method: 'PATCH',
                body: JSON.stringify({ quantity })
            });
            state.currentPayment = null;
            renderCart();
            haptic();
        } catch (error) {
            notify(error.message);
            haptic('error');
        }
    }

    async function openPaymentSheet() {
        if (!state.cart.items.length) {
            notify('Корзина пуста');
            return;
        }
        try {
            if (!state.paymentMethods.length) {
                await loadPaymentMethods();
            }
            state.currentPayment = null;
            const methods = state.paymentMethods.map((method) => `
                <button class="payment-method" type="button" data-action="create-payment" data-method="${method.id}" ${method.configured ? '' : 'disabled'}>
                    <span>${escapeHtml(method.title)}</span>
                    <small>${method.configured ? 'QR будет создан на сумму заказа' : 'Нужно настроить в .env'}</small>
                </button>
            `).join('');
            els.sheet.innerHTML = `
                <div class="sheet-title-row">
                    <h2>Оплата заказа</h2>
                    <button class="icon-button" type="button" data-action="close-sheet" aria-label="Закрыть" title="Закрыть">×</button>
                </div>
                <p class="sheet-description">Перед оформлением нужно оплатить заказ. После оплаты нажмите кнопку подтверждения.</p>
                <div class="payment-total">
                    <span>К оплате</span>
                    <strong>${formatPrice(state.cart.total)}</strong>
                </div>
                <div class="payment-methods">${methods}</div>
            `;
            els.sheetBackdrop.hidden = false;
            els.sheet.classList.add('is-open');
            els.sheet.setAttribute('aria-hidden', 'false');
        } catch (error) {
            notify(error.message);
            haptic('error');
        }
    }

    async function createPayment(method) {
        try {
            const data = await api('/api/telegram/payments', {
                method: 'POST',
                body: JSON.stringify({ method })
            });
            const payment = data.payment;
            state.currentPayment = payment;
            els.sheet.innerHTML = `
                <div class="sheet-title-row">
                    <h2>Сканируйте QR</h2>
                    <button class="icon-button" type="button" data-action="close-sheet" aria-label="Закрыть" title="Закрыть">×</button>
                </div>
                <div class="payment-total">
                    <span>Сумма</span>
                    <strong>${formatPrice(payment.amount)}</strong>
                </div>
                <img class="payment-qr" src="${payment.qr_data_url}" alt="QR-код оплаты">
                <p class="payment-comment">Комментарий: <strong>${escapeHtml(payment.comment)}</strong></p>
                <textarea class="payment-payload" readonly>${escapeHtml(payment.payload)}</textarea>
                <button class="primary-button payment-submit" type="button" data-action="confirm-payment">Я оплатил, оформить заказ</button>
                <button class="secondary-button payment-submit" type="button" data-action="payment-back">Выбрать другой способ</button>
            `;
            haptic('success');
        } catch (error) {
            notify(error.message);
            haptic('error');
        }
    }

    async function confirmPaymentAndCheckout() {
        if (!state.currentPayment) {
            notify('Сначала создайте платеж');
            return;
        }
        try {
            await api(`/api/telegram/payments/${state.currentPayment.id}/confirm`, {
                method: 'POST',
                body: '{}'
            });
            await checkout(state.currentPayment.id);
        } catch (error) {
            notify(error.message);
            haptic('error');
        }
    }

    async function checkout(paymentId) {
        try {
            const data = await api('/api/telegram/orders', {
                method: 'POST',
                body: JSON.stringify({ payment_id: paymentId })
            });
            state.cart = data.cart || { items: [], total: 0 };
            state.currentPayment = null;
            renderCart();
            closeSheet();
            await loadOrders();
            switchView('orders');
            notify(`Заказ #${data.order.id} оформлен`);
            haptic('success');
        } catch (error) {
            if (error.status === 409 && error.data && error.data.cart) {
                state.cart = error.data.cart;
                renderCart();
            }
            notify(error.message);
            haptic('error');
        }
    }

    function bindEvents() {
        document.addEventListener('click', (event) => {
            const target = event.target.closest('[data-action], [data-view]');
            if (!target) {
                return;
            }
            if (target.dataset.view) {
                if (!state.authenticated) {
                    renderAuthState('Сначала войдите через Telegram.');
                    return;
                }
                switchView(target.dataset.view);
                return;
            }
            const action = target.dataset.action;
            if (action === 'refresh') {
                refreshAll();
            }
            if (action === 'logout') {
                logout();
            }
            if (action === 'open-product') {
                openProduct(target.dataset.productId);
            }
            if (action === 'close-sheet') {
                closeSheet();
            }
            if (action === 'add-product') {
                const quantity = document.getElementById('sheetQuantity')?.value || 1;
                addProduct(target.dataset.productId, quantity);
            }
            if (action === 'cart-dec' || action === 'cart-inc') {
                const item = state.cart.items.find((cartItem) => cartItem.id === Number(target.dataset.itemId));
                if (!item) {
                    return;
                }
                const nextQuantity = action === 'cart-inc' ? item.quantity + 1 : item.quantity - 1;
                updateCartItem(item.id, nextQuantity);
            }
            if (action === 'checkout') {
                openPaymentSheet();
            }
            if (action === 'create-payment') {
                createPayment(target.dataset.method);
            }
            if (action === 'confirm-payment') {
                confirmPaymentAndCheckout();
            }
            if (action === 'payment-back') {
                openPaymentSheet();
            }
        });

        els.sheetBackdrop.addEventListener('click', closeSheet);
        els.loadMoreButton.addEventListener('click', () => loadProducts(false));
        els.retryAuthButton.addEventListener('click', async () => {
            const ok = await authenticate();
            if (ok) {
                await Promise.all([loadProducts(true), loadCart()]);
            }
        });
        els.searchInput.addEventListener('input', (event) => {
            state.search = event.target.value;
            renderProducts();
        });
    }

    async function init() {
        if (tg) {
            tg.ready();
            tg.expand();
        }
        bindEvents();
        renderAuthState();
        const ok = await authenticate();
        if (ok) {
            await Promise.all([loadProducts(true), loadCart()]);
        }
    }

    init();
})();
