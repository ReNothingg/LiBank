function toast(message, type = '') {
    const cont = document.getElementById('toastContainer');
    if (!cont) return;
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = message;
    cont.appendChild(el);
    setTimeout(() => {
        el.style.opacity = '0';
        el.style.transform = 'translateY(8px)';
        setTimeout(() => cont.removeChild(el), 150);
    }, 2500);
}

function fmtMoney(cents) {
    try {
        const v = (cents || 0) / 100;
        return new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 2 }).format(v);
    } catch {
        return `${(cents / 100).toFixed(2)} ₽`;
    }
}

function fmtDate(isoString) {
    try {
        return new Date(isoString).toLocaleString('ru-RU', {
            year: 'numeric', month: 'long', day: 'numeric',
            hour: '2-digit', minute: '2-digit', second: '2-digit'
        });
    } catch {
        return isoString;
    }
}

async function api(path, opts = {}) {
    const defaultHeaders = { 'Content-Type': 'application/json' };
    const res = await fetch(path, {
        credentials: 'same-origin',
        ...opts,
        headers: { ...defaultHeaders, ...opts.headers },
    });

    if (res.headers.get('content-disposition')?.includes('attachment')) {
        if (!res.ok) throw new Error(`Ошибка ${res.status}`);
        return res.blob();
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) throw new Error(data.error || `Ошибка ${res.status}`);
    return data;
}

function openModal(id) {
    const m = document.getElementById(id);
    if (m) m.classList.add('show');
}
function closeModal(id) {
    const m = document.getElementById(id);
    if (m) m.classList.remove('show');
}

// ----------------- Login page -----------------
async function initLoginPage() {
    const loginForm = document.getElementById('loginForm');
    const registerBtn = document.getElementById('registerBtn');
    const loginByIdBtn = document.getElementById('loginByIdBtn');
    const registerForm = document.getElementById('registerForm');

    loginForm?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const fd = new FormData(loginForm);
        const username = fd.get('username');
        const password = fd.get('password');
        try {
            await api('/api/login', { method: 'POST', body: JSON.stringify({ username, password }) });
            window.location.href = '/account';
        } catch (err) {
            toast(err.message, 'error');
        }
    });

    registerBtn?.addEventListener('click', () => {
        openModal('registerModal');
    });

    registerForm?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const fd = new FormData(registerForm);
        const data = Object.fromEntries(fd.entries());

        if (data.password !== data.password_confirm) {
            toast('Пароли не совпадают', 'error');
            return;
        }

        try {
            await api('/api/register', { method: 'POST', body: JSON.stringify(data) });
            toast('Аккаунт создан, добро пожаловать!', 'success');
            setTimeout(() => window.location.href = '/account', 500);
        } catch (err) {
            toast(err.message, 'error');
        }
    });

    loginByIdBtn?.addEventListener('click', async () => {
        const id = document.getElementById('loginByIdInput').value.trim();
        if (!id) return;
        try {
            await api('/api/login_by_id', { method: 'POST', body: JSON.stringify({ user_id: id }) });
            window.location.href = '/account';
        } catch (err) {
            toast(err.message, 'error');
        }
    });

    // Close modals
    document.querySelectorAll('[data-close]').forEach(btn => {
        btn.addEventListener('click', () => closeModal(btn.getAttribute('data-close')));
    });
    document.getElementById('registerModal')?.addEventListener('click', (e) => {
        if (e.target.id === 'registerModal') closeModal('registerModal');
    });
}

// ----------------- Profile Page -----------------
async function initProfilePage() {
    const profileForm = document.getElementById('profileForm');
    const passwordForm = document.getElementById('passwordForm');

    try {
        const { user } = await api('/api/me');
        profileForm.elements.first_name.value = user.first_name;
        profileForm.elements.last_name.value = user.last_name;
        profileForm.elements.patronymic.value = user.patronymic;
        profileForm.elements.birth_date.value = user.birth_date;
    } catch (err) {
        toast(err.message, 'error');
        window.location.href = '/';
    }

    profileForm?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const fd = new FormData(profileForm);
        const data = Object.fromEntries(fd.entries());
        try {
            const res = await api('/api/me', { method: 'PUT', body: JSON.stringify(data) });
            toast(res.message || 'Профиль обновлён', 'success');
        } catch (err) {
            toast(err.message, 'error');
        }
    });

    passwordForm?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const fd = new FormData(passwordForm);
        const data = Object.fromEntries(fd.entries());
        if (data.new_password !== data.new_password_confirm) {
            toast('Новые пароли не совпадают', 'error');
            return;
        }
        try {
            const res = await api('/api/me/password', { method: 'PUT', body: JSON.stringify(data) });
            toast(res.message || 'Пароль изменён', 'success');
            passwordForm.reset();
        } catch (err) {
            toast(err.message, 'error');
        }
    });
}

// ----------------- Account page -----------------
let currentFilter = 'all';
let searchQuery = '';
let scanning = false;
let stream = null;
let scanRaf = null;

function parseInvoiceId(text) {
    if (!text) return null;
    const s = String(text).trim();
    const pay = s.match(/^PAY:(\d+)$/i);
    if (pay) return parseInt(pay[1], 10);
    const urlMatch = s.match(/(?:\/pay\/|\/invoice\/|[\?\&]invoice=)(\d+)/i);
    if (urlMatch) return parseInt(urlMatch[1], 10);
    const onlyDigits = s.match(/^\d+$/);
    if (onlyDigits) return parseInt(onlyDigits[0], 10);
    return null;
}

async function loadMe() {
    const data = await api('/api/me');
    const user = data.user;
    document.getElementById('balanceValue').textContent = fmtMoney(user.balance_cents);
    document.getElementById('fullNameLabel').textContent = user.full_name;
    document.getElementById('usernameLabel').textContent = '@' + user.username;
}

async function loadTransactions() {
    const params = new URLSearchParams();
    if (currentFilter === 'debit' || currentFilter === 'credit') params.set('type', currentFilter);
    if (searchQuery) params.set('q', searchQuery);
    const data = await api('/api/transactions?' + params.toString());
    const list = document.getElementById('txList');
    list.innerHTML = '';
    document.getElementById('emptyState').hidden = data.items.length > 0;

    for (const t of data.items) {
        const li = document.createElement('li');
        li.className = 'tx-item';
        li.dataset.txId = t.id;
        li.style.cursor = 'pointer';

        const left = document.createElement('div');
        left.className = 'tx-left';
        const title = document.createElement('div');
        title.className = 'tx-title';
        title.textContent = t.description || (t.type === 'debit' ? 'Списание' : 'Поступление');
        const sub = document.createElement('div');
        sub.className = 'tx-sub';
        const who = t.counterparty_username ? '@' + t.counterparty_username : (t.counterparty_id ? `ID:${t.counterparty_id}` : '');
        sub.textContent = `${who || '—'} • ${new Date(t.created_at).toLocaleString('ru-RU')}`;
        left.appendChild(title); left.appendChild(sub);

        const amount = document.createElement('div');
        amount.className = 'amount ' + (t.type === 'debit' ? 'negative' : 'positive');
        amount.textContent = (t.type === 'debit' ? '−' : '+') + fmtMoney(t.amount_cents);

        li.appendChild(left); li.appendChild(amount);
        list.appendChild(li);
    }

    document.querySelectorAll('.tx-item').forEach(item => {
        item.addEventListener('click', () => showTransactionDetails(item.dataset.txId));
    });
}

async function showTransactionDetails(txId) {
    try {
        const { transaction: tx } = await api(`/api/transactions/${txId}`);
        const content = document.getElementById('txDetailContent');

        const sign = tx.type === 'debit' ? '−' : '+';
        const amountClass = tx.type === 'debit' ? 'negative' : 'positive';
        const who = tx.counterparty_username ? `@${tx.counterparty_username}` : (tx.counterparty_id ? `ID:${tx.counterparty_id}` : '—');

        content.innerHTML = `
            <div class="input-group">
                <label>Сумма</label>
                <div class="amount ${amountClass}" style="font-size: 24px;">${sign}${fmtMoney(tx.amount_cents)}</div>
            </div>
            <div class="input-group">
                <label>Описание</label>
                <p style="margin: 0;">${tx.description || 'Без описания'}</p>
            </div>
            <div class="input-group">
                <label>Контрагент</label>
                <p style="margin: 0;">${who}</p>
            </div>
            <div class="input-group">
                <label>Дата и время</label>
                <p style="margin: 0;">${fmtDate(tx.created_at)}</p>
            </div>
             <div class="input-group">
                <label>ID транзакции</label>
                <p style="margin: 0;"><code>${tx.id}</code></p>
            </div>
        `;
        openModal('txDetailModal');
    } catch (err) {
        toast(err.message, 'error');
    }
}

function bindFilters() {
    document.querySelectorAll('.chip').forEach(chip => {
        chip.addEventListener('click', () => {
            document.querySelector('.chip.active')?.classList.remove('active');
            chip.classList.add('active');
            currentFilter = chip.dataset.filter;
            loadTransactions().catch(err => toast(err.message, 'error'));
        });
    });
    const search = document.getElementById('searchInput');
    let t = null;
    search.addEventListener('input', () => {
        searchQuery = search.value.trim();
        clearTimeout(t);
        t = setTimeout(() => loadTransactions().catch(err => toast(err.message, 'error')), 250);
    });
}

async function showInvoiceConfirm(invoiceId) {
    try {
        const { invoice } = await api(`/api/invoices/${invoiceId}`);
        if (invoice.status !== 'pending') {
            toast('Счёт уже оплачен или отменён', 'error');
            return;
        }
        const who = invoice.creator_username ? '@' + invoice.creator_username : `ID:${invoice.creator_id}`;
        if (!confirm(`Оплатить ${fmtMoney(invoice.amount_cents)} пользователю ${who}?`)) return;

        await api('/api/pay', { method: 'POST', body: JSON.stringify({ invoice_id: invoiceId }) });
        toast('Оплата успешна', 'success');
        closeModal('scanModal');
        await Promise.all([loadMe(), loadTransactions()]);
    } catch (err) {
        toast(err.message, 'error');
    }
}

// ---- Scanner ----
async function startScanner() {
    if (scanning) return;
    scanning = true;

    const video = document.getElementById('video');
    const canvas = document.getElementById('canvas');
    const ctx = canvas.getContext('2d');

    try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } } });
        video.srcObject = stream;
        await video.play();

        const tick = () => {
            if (!scanning) return;
            if (video.readyState === video.HAVE_ENOUGH_DATA) {
                canvas.height = video.videoHeight;
                canvas.width = video.videoWidth;
                ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
                const code = window.jsQR(imageData.data, imageData.width, imageData.height, { inversionAttempts: "dontInvert" });
                if (code) {
                    handleDecoded(code.data);
                    return;
                }
            }
            requestAnimationFrame(tick);
        };
        tick();
    } catch (err) {
        toast('Нет доступа к камере: ' + err.message, 'error');
        document.getElementById('scanHint').textContent = 'Сканер недоступен, используйте ввод вручную';
    }
}

function stopScanner() {
    scanning = false;
    if (stream) stream.getTracks().forEach(t => t.stop());
    stream = null;
}

function handleDecoded(text) {
    stopScanner();
    const id = parseInvoiceId(text);
    if (!id) {
        toast('Не удалось распознать счёт', 'error');
        setTimeout(startScanner, 1000);
        return;
    }
    showInvoiceConfirm(id);
}

// ---- Invoice & Transfer Forms ----
function bindForms() {
    const invoiceForm = document.getElementById('invoiceForm');
    invoiceForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const fd = new FormData(invoiceForm);
        try {
            const { invoice } = await api('/api/invoices', {
                method: 'POST',
                body: JSON.stringify(Object.fromEntries(fd.entries()))
            });
            document.getElementById('qrImage').src = invoice.qr_url;
            document.getElementById('payloadText').textContent = invoice.payload;
            document.getElementById('invoiceResult').hidden = false;
            toast('Счёт создан', 'success');
        } catch (err) {
            toast(err.message, 'error');
        }
    });

    document.getElementById('copyPayloadBtn').addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(document.getElementById('payloadText').textContent);
            toast('Скопировано', 'success');
        } catch {
            toast('Не удалось скопировать', 'error');
        }
    });

    const transferForm = document.getElementById('transferForm');
    transferForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const fd = new FormData(transferForm);
        let recipient = fd.get('recipient_username').trim();
        if (recipient.startsWith('@')) recipient = recipient.substring(1);

        const data = {
            recipient_username: recipient,
            amount: fd.get('amount'),
            description: fd.get('description'),
        };

        try {
            await api('/api/transfer', { method: 'POST', body: JSON.stringify(data) });
            toast('Перевод успешен', 'success');
            closeModal('transferModal');
            await Promise.all([loadMe(), loadTransactions()]);
        } catch (err) {
            toast(err.message, 'error');
        }
    });
}

// ---- Bind UI ----
async function initAccountPage() {
    document.getElementById('logoutBtn').addEventListener('click', async () => {
        try { await api('/api/logout', { method: 'POST' }); } finally { window.location.href = '/'; }
    });

    await Promise.all([loadMe(), loadTransactions()]);
    bindFilters();
    bindForms();

    document.getElementById('scanBtn').addEventListener('click', () => { openModal('scanModal'); startScanner(); });
    document.getElementById('createInvoiceBtn').addEventListener('click', () => {
        document.getElementById('invoiceForm').reset();
        document.getElementById('invoiceResult').hidden = true;
        openModal('invoiceModal');
    });
    document.getElementById('transferBtn').addEventListener('click', () => {
        document.getElementById('transferForm').reset();
        openModal('transferModal');
    });

    document.getElementById('manualPayBtn').addEventListener('click', () => {
        const id = parseInvoiceId(document.getElementById('manualInput').value);
        if (!id) { toast('Введите корректный счёт (PAY:ID или URL)', 'error'); return; }
        showInvoiceConfirm(id);
    });

    document.querySelectorAll('.modal').forEach(modal => {
        modal.addEventListener('click', (e) => {
            if (e.target.classList.contains('modal') || e.target.closest('[data-close]')) {
                closeModal(modal.id);
                if (modal.id === 'scanModal') stopScanner();
            }
        });
    });
}

document.addEventListener('DOMContentLoaded', () => {
    const page = document.body.dataset.page;
    if (page === 'login') initLoginPage();
    if (page === 'account') initAccountPage();
    if (page === 'profile') initProfilePage();
});
