(function () {
    const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
    const isTelegram = Boolean(tg && (tg.initData || tg.initDataUnsafe?.user));

    const state = {
        page: 1,
        hasNext: false,
        products: [],
        cart: { items: [], total: 0 },
        orders: [],
        user: null,
        authenticated: false,
        authMode: 'login',
        search: '',
        paymentMethods: [],
        currentPayment: null
    };

    const els = {
        runtimeLabel: document.getElementById('runtimeLabel'),
        accountChip: document.getElementById('accountChip'),
        accountAvatar: document.getElementById('accountAvatar'),
        accountName: document.getElementById('accountName'),
        authButton: document.getElementById('authButton'),
        productGrid: document.getElementById('productGrid'),
        loadMoreButton: document.getElementById('loadMoreButton'),
        searchInput: document.getElementById('searchInput'),
        cartList: document.getElementById('cartList'),
        cartTotal: document.getElementById('cartTotal'),
        cartCounter: document.getElementById('cartCounter'),
        mobileCartCounter: document.getElementById('mobileCartCounter'),
        checkoutPanel: document.getElementById('checkoutPanel'),
        ordersList: document.getElementById('ordersList'),
        modalBackdrop: document.getElementById('modalBackdrop'),
        authModal: document.getElementById('authModal'),
        authTitle: document.getElementById('authTitle'),
        authHint: document.getElementById('authHint'),
        authForm: document.getElementById('authForm'),
        authSubmit: document.getElementById('authSubmit'),
        toggleAuthButton: document.getElementById('toggleAuthButton'),
        productModal: document.getElementById('productModal'),
        paymentModal: document.getElementById('paymentModal'),
        toast: document.getElementById('toast')
    };

    function formatPrice(value) {
        return new Intl.NumberFormat('ru-RU', {
            style: 'currency',
            currency: 'RUB',
            maximumFractionDigits: 0
        }).format(value || 0);
    }

    function formatCrypto(value) {
        return new Intl.NumberFormat('ru-RU', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 6
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
        notify.timer = window.setTimeout(() => els.toast.classList.remove('is-visible'), 2400);
    }

    function haptic(type) {
        if (!tg || !tg.HapticFeedback) return;
        if (type === 'success') tg.HapticFeedback.notificationOccurred('success');
        else if (type === 'error') tg.HapticFeedback.notificationOccurred('error');
        else tg.HapticFeedback.impactOccurred('light');
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

    function openModal(modal) {
        els.modalBackdrop.hidden = false;
        modal.classList.add('is-open');
        modal.setAttribute('aria-hidden', 'false');
    }

    function closeModals() {
        [els.authModal, els.productModal, els.paymentModal].forEach((modal) => {
            modal.classList.remove('is-open');
            modal.setAttribute('aria-hidden', 'true');
        });
        els.modalBackdrop.hidden = true;
    }

    function renderAuth() {
        const name = state.user?.name || state.user?.email || 'Аккаунт';
        els.accountChip.hidden = !state.authenticated;
        els.authButton.hidden = state.authenticated && isTelegram;
        els.authButton.textContent = state.authenticated ? 'Выйти' : 'Войти';
        els.accountName.textContent = name;
        els.accountAvatar.textContent = name.trim().charAt(0).toUpperCase() || 'U';
        els.runtimeLabel.textContent = isTelegram ? 'Открыто в Telegram Mini App' : 'Веб-магазин техники';
    }

    function requireAuth() {
        if (state.authenticated) return true;
        if (isTelegram) {
            notify('Не удалось войти через Telegram. Откройте Mini App заново.');
        } else {
            openAuth('login');
        }
        return false;
    }

    function openAuth(mode) {
        state.authMode = mode || state.authMode;
        const passwordInput = els.authForm.querySelector('input[name="password"]');
        els.authTitle.textContent = state.authMode === 'login' ? 'Вход' : 'Регистрация';
        els.authSubmit.textContent = state.authMode === 'login' ? 'Войти' : 'Создать аккаунт';
        els.toggleAuthButton.textContent = state.authMode === 'login' ? 'Создать аккаунт' : 'Уже есть аккаунт';
        if (passwordInput) {
            if (state.authMode === 'register') {
                passwordInput.setAttribute('minlength', '6');
            } else {
                passwordInput.removeAttribute('minlength');
            }
        }
        els.authHint.textContent = isTelegram
            ? 'В Telegram вход выполняется автоматически через initData.'
            : 'В браузере войдите по email и паролю. Этот же магазин откроется и в Telegram.';
        openModal(els.authModal);
    }

    function cartCount() {
        return state.cart.items.reduce((sum, item) => sum + Number(item.quantity || 0), 0);
    }

    function updateCounters() {
        const count = cartCount();
        els.cartCounter.textContent = count;
        els.mobileCartCounter.textContent = count;
    }

    function filteredProducts() {
        const query = state.search.trim().toLowerCase();
        if (!query) return state.products;
        return state.products.filter((product) => `${product.name} ${product.description}`.toLowerCase().includes(query));
    }

    function renderProducts() {
        const products = filteredProducts();
        if (!products.length) {
            els.productGrid.innerHTML = '<div class="empty-state">Товары не найдены</div>';
            els.loadMoreButton.hidden = true;
            return;
        }
        els.productGrid.innerHTML = products.map((product) => `
            <article class="product-card">
                <img class="product-image" src="${product.image_url}" alt="${escapeHtml(product.name)}" onerror="this.onerror=null;this.src='${product.fallback_image_url}'">
                <div class="product-body">
                    <h2 class="product-title">${escapeHtml(product.name)}</h2>
                    <p class="product-description">${escapeHtml(product.description).slice(0, 110)}</p>
                    <div class="product-meta">
                        <span class="price">${formatPrice(product.price)}</span>
                        <span class="stock">${product.stock} шт.</span>
                    </div>
                    <button class="primary-button" style="width:100%;margin-top:12px;" type="button" data-action="open-product" data-product-id="${product.id}">Подробнее</button>
                </div>
            </article>
        `).join('');
        els.loadMoreButton.hidden = !state.hasNext || Boolean(state.search.trim());
    }

    function renderCart() {
        updateCounters();
        els.cartTotal.textContent = formatPrice(state.cart.total);
        els.checkoutPanel.hidden = !state.cart.items.length;
        if (!state.authenticated) {
            els.cartList.innerHTML = '<div class="empty-state">Войдите, чтобы пользоваться корзиной</div>';
            return;
        }
        if (!state.cart.items.length) {
            els.cartList.innerHTML = '<div class="empty-state">Корзина пуста</div>';
            return;
        }
        els.cartList.innerHTML = state.cart.items.map((item) => `
            <article class="cart-item">
                <img class="cart-image" src="${item.image_url}" alt="${escapeHtml(item.name)}" onerror="this.onerror=null;this.src='${item.fallback_image_url}'">
                <div>
                    <h3>${escapeHtml(item.name)}</h3>
                    <p class="muted">${formatPrice(item.price_at_time)} · доступно ${item.stock} шт.</p>
                    <div class="cart-footer">
                        <strong class="price">${formatPrice(item.line_total)}</strong>
                        <div class="qty-control">
                            <button type="button" data-action="cart-dec" data-item-id="${item.id}">−</button>
                            <output>${item.quantity}</output>
                            <button type="button" data-action="cart-inc" data-item-id="${item.id}">+</button>
                        </div>
                    </div>
                </div>
            </article>
        `).join('');
    }

    function renderOrders() {
        if (!state.authenticated) {
            els.ordersList.innerHTML = '<div class="empty-state">Войдите, чтобы увидеть заказы</div>';
            return;
        }
        if (!state.orders.length) {
            els.ordersList.innerHTML = '<div class="empty-state">Заказов пока нет</div>';
            return;
        }
        els.ordersList.innerHTML = state.orders.map((order) => {
            const names = order.items.map((item) => `${escapeHtml(item.name)} × ${item.quantity}`).join(', ');
            const date = order.created_at ? new Date(order.created_at).toLocaleString('ru-RU') : '';
            const history = (order.status_history || []).map((item) => {
                const historyDate = item.created_at ? new Date(item.created_at).toLocaleString('ru-RU') : '';
                return `
                    <li>
                        <span class="history-dot status-dot--${escapeHtml(item.new_status)}"></span>
                        <div>
                            <strong>${escapeHtml(item.new_status_label || item.new_status)}</strong>
                            <small>${historyDate}</small>
                        </div>
                    </li>
                `;
            }).join('');
            return `
                <article class="order-card">
                    <div class="order-head">
                        <h3>Заказ #${order.id}</h3>
                        <span class="status status--${escapeHtml(order.status)}">${escapeHtml(order.status_label || order.status)}</span>
                    </div>
                    <p>${names}</p>
                    <div class="order-total">
                        <time class="muted">${date}</time>
                        <strong>${formatPrice(order.total)}</strong>
                    </div>
                    <div class="status-history">
                        <h4>История статуса</h4>
                        <ul>${history || '<li><span class="history-dot"></span><div><strong>История пока пуста</strong></div></li>'}</ul>
                    </div>
                </article>
            `;
        }).join('');
    }

    function switchView(view) {
        if ((view === 'cart' || view === 'orders') && !requireAuth()) return;
        document.querySelectorAll('.view').forEach((element) => {
            element.classList.toggle('is-active', element.id === `view-${view}`);
        });
        document.querySelectorAll('[data-view]').forEach((button) => {
            button.classList.toggle('is-active', button.dataset.view === view);
        });
        if (view === 'cart') loadCart();
        if (view === 'orders') loadOrders();
    }

    async function loadProducts(reset) {
        if (reset) {
            state.page = 1;
            state.products = [];
        }
        els.loadMoreButton.disabled = true;
        try {
            const data = await api(`/api/market/products?page=${state.page}&per_page=24`);
            state.products = reset ? data.products : state.products.concat(data.products);
            state.hasNext = data.has_next;
            state.page += 1;
            renderProducts();
        } catch (error) {
            notify(error.message);
        } finally {
            els.loadMoreButton.disabled = false;
        }
    }

    async function loadCart() {
        if (!state.authenticated) {
            renderCart();
            return;
        }
        try {
            state.cart = await api('/api/market/cart');
            renderCart();
        } catch (error) {
            notify(error.message);
        }
    }

    async function loadOrders() {
        if (!state.authenticated) {
            renderOrders();
            return;
        }
        try {
            const data = await api('/api/market/orders');
            state.orders = data.orders || [];
            renderOrders();
        } catch (error) {
            notify(error.message);
        }
    }

    async function checkSession() {
        const data = await api('/api/market/auth/me');
        state.authenticated = Boolean(data.authenticated);
        state.user = data.user;
        renderAuth();
    }

    async function telegramLogin() {
        if (!isTelegram) return false;
        try {
            if (tg) {
                tg.ready();
                tg.expand();
            }
            const data = await api('/api/market/auth/telegram', {
                method: 'POST',
                body: JSON.stringify({ initData: tg.initData || '' })
            });
            state.user = data.user;
            state.authenticated = true;
            renderAuth();
            return true;
        } catch (error) {
            state.authenticated = false;
            state.user = null;
            renderAuth();
            notify(error.message);
            return false;
        }
    }

    async function submitAuth(event) {
        event.preventDefault();
        const formData = new FormData(els.authForm);
        const path = state.authMode === 'login' ? '/api/market/auth/login' : '/api/market/auth/register';
        try {
            const data = await api(path, {
                method: 'POST',
                body: JSON.stringify({
                    email: formData.get('email'),
                    password: formData.get('password')
                })
            });
            state.authenticated = Boolean(data.authenticated);
            state.user = data.user;
            closeModals();
            renderAuth();
            await loadCart();
            notify('Вы вошли');
        } catch (error) {
            notify(error.message);
        }
    }

    async function logout() {
        await api('/api/market/auth/logout', { method: 'POST', body: '{}' });
        state.authenticated = false;
        state.user = null;
        state.cart = { items: [], total: 0 };
        state.orders = [];
        renderAuth();
        renderCart();
        renderOrders();
        switchView('catalog');
    }

    async function openProduct(productId) {
        try {
            const data = await api(`/api/market/products/${productId}`);
            const product = data.product;
            els.productModal.innerHTML = `
                <div class="modal-head">
                    <h2>${escapeHtml(product.name)}</h2>
                    <button class="icon-button" type="button" data-action="close-modal">×</button>
                </div>
                <img class="product-detail-image" src="${product.image_url}" alt="${escapeHtml(product.name)}" onerror="this.onerror=null;this.src='${product.fallback_image_url}'">
                <p class="price">${formatPrice(product.price)}</p>
                <p class="stock">В наличии ${product.stock} шт.</p>
                <p class="muted">${escapeHtml(product.description)}</p>
                <div class="product-actions">
                    <input class="form-control quantity-input" id="productQuantity" type="number" min="1" max="${product.stock}" value="1">
                    <button class="primary-button" type="button" data-action="add-product" data-product-id="${product.id}" ${product.stock <= 0 ? 'disabled' : ''}>Добавить в корзину</button>
                </div>
            `;
            openModal(els.productModal);
        } catch (error) {
            notify(error.message);
        }
    }

    async function addProduct(productId) {
        if (!requireAuth()) return;
        const quantity = Number(document.getElementById('productQuantity')?.value || 1);
        try {
            state.cart = await api('/api/market/cart', {
                method: 'POST',
                body: JSON.stringify({ product_id: Number(productId), quantity })
            });
            closeModals();
            renderCart();
            notify('Добавлено в корзину');
            haptic('success');
        } catch (error) {
            notify(error.message);
            haptic('error');
        }
    }

    async function updateCartItem(itemId, quantity) {
        try {
            state.cart = await api(`/api/market/cart/${itemId}`, {
                method: 'PATCH',
                body: JSON.stringify({ quantity })
            });
            renderCart();
        } catch (error) {
            notify(error.message);
        }
    }

    async function openPayment() {
        if (!requireAuth()) return;
        if (!state.cart.items.length) {
            notify('Корзина пуста');
            return;
        }
        try {
            if (!state.paymentMethods.length) {
                const data = await api('/api/market/payments/methods');
                state.paymentMethods = data.methods || [];
            }
            els.paymentModal.innerHTML = `
                <div class="modal-head">
                    <h2>Оплата заказа</h2>
                    <button class="icon-button" type="button" data-action="close-modal">×</button>
                </div>
                <p class="muted">Сначала оплатите заказ через QR. После оплаты нажмите подтверждение.</p>
                <p class="price">${formatPrice(state.cart.total)}</p>
                <div class="payment-methods">
                    ${state.paymentMethods.map((method) => `
                        <button class="payment-method" type="button" data-action="create-payment" data-method="${method.id}" ${method.configured ? '' : 'disabled'}>
                            <span>${escapeHtml(method.title)}</span>
                            <small>${method.configured ? 'Создать QR' : 'Не настроено в .env'}</small>
                        </button>
                    `).join('')}
                </div>
            `;
            openModal(els.paymentModal);
        } catch (error) {
            notify(error.message);
        }
    }

    async function createPayment(method) {
        try {
            const data = await api('/api/market/payments', {
                method: 'POST',
                body: JSON.stringify({ method })
            });
            state.currentPayment = data.payment;
            const crypto = state.currentPayment.crypto;
            const underQrCost = crypto ? `
                <div class="payment-cost-under-qr">
                    <span>К оплате</span>
                    <strong>${formatCrypto(crypto.amount)} ${escapeHtml(crypto.asset)}</strong>
                    <small>${formatPrice(state.currentPayment.amount)} · 1 ${escapeHtml(crypto.asset)} = ${formatPrice(crypto.rate)}</small>
                    <small>${escapeHtml(crypto.network)} · ${escapeHtml(crypto.rate_source || 'rate')}</small>
                </div>
            ` : `
                <div class="payment-cost-under-qr">
                    <span>К оплате</span>
                    <strong>${formatPrice(state.currentPayment.amount)}</strong>
                </div>
            `;
            els.paymentModal.innerHTML = `
                <div class="modal-head">
                    <h2>QR для оплаты</h2>
                    <button class="icon-button" type="button" data-action="close-modal">×</button>
                </div>
                <p class="price">${formatPrice(state.currentPayment.amount)}</p>
                <img class="payment-qr" src="${state.currentPayment.qr_data_url}" alt="QR-код оплаты">
                ${underQrCost}
                <p class="muted">Комментарий: <strong>${escapeHtml(state.currentPayment.comment)}</strong></p>
                <textarea class="payment-payload" readonly>${escapeHtml(state.currentPayment.payload)}</textarea>
                <button class="primary-button" style="width:100%;margin-top:10px;" type="button" data-action="confirm-payment">Я оплатил, оформить заказ</button>
                <button class="secondary-button" style="width:100%;margin-top:10px;" type="button" data-action="checkout">Выбрать другой способ</button>
            `;
        } catch (error) {
            notify(error.message);
        }
    }

    async function confirmPayment() {
        if (!state.currentPayment) return;
        try {
            await api(`/api/market/payments/${state.currentPayment.id}/confirm`, { method: 'POST', body: '{}' });
            const data = await api('/api/market/orders', {
                method: 'POST',
                body: JSON.stringify({ payment_id: state.currentPayment.id })
            });
            state.cart = data.cart || { items: [], total: 0 };
            state.currentPayment = null;
            closeModals();
            renderCart();
            await loadOrders();
            switchView('orders');
            notify(`Заказ #${data.order.id} оформлен`);
            haptic('success');
        } catch (error) {
            notify(error.message);
            haptic('error');
        }
    }

    function bindEvents() {
        document.addEventListener('click', async (event) => {
            const target = event.target.closest('[data-action], [data-view]');
            if (!target) return;

            if (target.dataset.view) {
                switchView(target.dataset.view);
                return;
            }

            const action = target.dataset.action;
            if (action === 'refresh') loadProducts(true);
            if (action === 'open-auth') {
                if (state.authenticated && !isTelegram) await logout();
                else if (!state.authenticated) openAuth('login');
            }
            if (action === 'close-auth' || action === 'close-modal') closeModals();
            if (action === 'toggle-auth') openAuth(state.authMode === 'login' ? 'register' : 'login');
            if (action === 'open-product') openProduct(target.dataset.productId);
            if (action === 'add-product') addProduct(target.dataset.productId);
            if (action === 'cart-dec' || action === 'cart-inc') {
                const item = state.cart.items.find((cartItem) => cartItem.id === Number(target.dataset.itemId));
                if (!item) return;
                updateCartItem(item.id, action === 'cart-inc' ? item.quantity + 1 : item.quantity - 1);
            }
            if (action === 'checkout') openPayment();
            if (action === 'create-payment') createPayment(target.dataset.method);
            if (action === 'confirm-payment') confirmPayment();
        });

        els.modalBackdrop.addEventListener('click', closeModals);
        els.authForm.addEventListener('submit', submitAuth);
        els.loadMoreButton.addEventListener('click', () => loadProducts(false));
        els.searchInput.addEventListener('input', (event) => {
            state.search = event.target.value;
            renderProducts();
        });
    }

    async function init() {
        bindEvents();
        renderAuth();
        if (isTelegram) {
            await telegramLogin();
        } else {
            await checkSession();
        }
        await Promise.all([loadProducts(true), loadCart()]);
    }

    init();
})();
