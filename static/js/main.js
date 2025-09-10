let notyf = null;
function ensureNotyf() {
    if (!notyf) {
        notyf = new Notyf({
            duration: 2500,
            ripple: true,
            position: { x: 'center', y: 'top' },
            types: [
                { type: 'success', background: '#26d07c', icon: false },
                { type: 'error', background: '#ff5a5f', icon: false }
            ]
        });
    }
}
function toastSuccess(msg) { ensureNotyf(); notyf.success(msg); }
function toastError(msg) { ensureNotyf(); notyf.error(msg); }

async function apiFetch(url, opts = {}) {
    const options = Object.assign({ headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin' }, opts);
    const res = await fetch(url, options);
    let data = null;
    try { data = await res.json(); } catch { }
    if (!res.ok) {
        return data || { ok: false, error: 'Ошибка запроса' };
    }
    return data;
}
function formatAmount(cents) {
    const rub = (cents / 100).toFixed(2);
    const parts = rub.split('.');
    const integer = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
    return `${integer},${parts[1]} ₽`;
}
function el(html) {
    const div = document.createElement('div');
    div.innerHTML = html.trim();
    return div.firstChild;
}
function openModal(id) {
    document.getElementById(id).classList.add('show');
}
function closeModal(id) {
    document.getElementById(id).classList.remove('show');
}

// Страница: Вход
function initLogin() {
    const form = document.getElementById('loginForm');
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const payload = {
            username: form.username.value.trim(),
            password: form.password.value
        };
        const res = await apiFetch('/api/login', { method: 'POST', body: JSON.stringify(payload) });
        if (res.ok) {
            toastSuccess('Добро пожаловать!');
            setTimeout(() => window.location.href = '/account', 300);
        } else {
            toastError(res.error || 'Не удалось войти');
        }
    });
}

// Страница: Регистрация
function initRegister() {
    const form = document.getElementById('registerForm');
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const u = form.username.value.trim();
        const p1 = form.password.value;
        const p2 = form.password2.value;
        if (p1 !== p2) {
            toastError('Пароли не совпадают');
            return;
        }
        const res = await apiFetch('/api/register', { method: 'POST', body: JSON.stringify({ username: u, password: p1 }) });
        if (res.ok) {
            toastSuccess('Аккаунт создан');
            setTimeout(() => window.location.href = '/account', 300);
        } else {
            toastError(res.error || 'Ошибка регистрации');
        }
    });
}

// Страница: Аккаунт
let html5Qr = null;
let lastScanned = null;
let selectedFilter = 'all';
let latestTxIds = new Set();

async function refreshMe() {
    const res = await apiFetch('/api/me');
    if (res.ok) {
        document.getElementById('username').textContent = '@' + res.user.username;
        const balance = document.getElementById('balance');
        // Анимация баланса (небольшой пульс)
        balance.classList.add('animate__animated', 'animate__pulse');
        balance.addEventListener('animationend', () => {
            balance.classList.remove('animate__animated', 'animate__pulse');
        }, { once: true });
        balance.textContent = res.user.balance_display;
    }
}

function txItemHtml(tx) {
    const sign = tx.direction === 'in' ? '+' : '−';
    const icoClass = tx.direction === 'in' ? 'in' : 'out';
    const title = tx.direction === 'in' ? `От @${tx.counterparty}` : `К @${tx.counterparty}`;
    return `
    <div class="tx-item animate__animated animate__fadeInUp" data-id="${tx.id}">
      <div class="tx-left">
        <div class="tx-ico ${icoClass}">
          ${tx.direction === 'in' ? '⬇️' : '⬆️'}
        </div>
        <div class="tx-meta">
          <div class="tx-title">${title}</div>
          <div class="tx-sub">${new Date(tx.created_at).toLocaleString('ru-RU')} · ${tx.description || '—'}</div>
        </div>
      </div>
      <div class="tx-amount ${icoClass}">${sign} ${tx.amount_display}</div>
    </div>
  `;
}

async function refreshTx() {
    const res = await apiFetch(`/api/transactions?type=${selectedFilter}`);
    const list = document.getElementById('txList');
    if (!res.ok) {
        list.innerHTML = `<div class="muted">Не удалось загрузить историю</div>`;
        return;
    }
    const txs = res.transactions;
    // Анимации при обновлении: удаляем те, которых нет
    const existing = Array.from(list.querySelectorAll('.tx-item'));
    const resIds = new Set(txs.map(t => t.id));
    for (const node of existing) {
        const id = +node.dataset.id;
        if (!resIds.has(id)) {
            node.classList.add('fadeOut');
            setTimeout(() => node.remove(), 240);
        }
    }
    // Добавляем/перестраиваем
    const frag = document.createDocumentFragment();
    txs.forEach(tx => {
        const node = el(txItemHtml(tx));
        frag.appendChild(node);
    });
    list.innerHTML = '';
    list.appendChild(frag);
}

async function startScan() {
    const containerId = 'qrReader';
    if (!window.Html5Qrcode) return;
    if (html5Qr) {
        try { await html5Qr.stop(); } catch { }
        html5Qr = null;
    }
    html5Qr = new Html5Qrcode(containerId, { verbose: false });
    const config = { fps: 10, qrbox: { width: 240, height: 240 } };
    try {
        await html5Qr.start({ facingMode: 'environment' }, config, onQrScan, onQrError);
    } catch (e) {
        document.getElementById(containerId).innerHTML = `<div class="muted small">Не удалось получить доступ к камере. Вставьте ссылку вручную ниже.</div>`;
    }
}
async function stopScan() {
    if (html5Qr) {
        try { await html5Qr.stop(); } catch { }
        try { html5Qr.clear(); } catch { }
        html5Qr = null;
    }
}
async function onQrScan(text) {
    if (text === lastScanned) return;
    lastScanned = text;
    await previewInvoice(text);
}
function onQrError(err) {
    // Игнорируем шум
}

async function previewInvoice(paylink) {
    const preview = document.getElementById('invoicePreview');
    const confirmBtn = document.getElementById('confirmPayBtn');
    confirmBtn.disabled = true;
    preview.classList.add('hidden');
    try {
        const res = await apiFetch('/api/pay/preview', { method: 'POST', body: JSON.stringify({ paylink }) });
        if (!res.ok) {
            toastError(res.error || 'Некорректный QR/ссылка');
            return;
        }
        const inv = res.invoice;
        preview.innerHTML = `
      <div><b>Кому:</b> @${inv.recipient_username}</div>
      <div><b>Сумма:</b> ${inv.amount_display}</div>
      <div><b>Назначение:</b> ${inv.description || '—'}</div>
    `;
        preview.classList.remove('hidden');
        confirmBtn.disabled = false;
        confirmBtn.onclick = async () => {
            confirmBtn.disabled = true;
            const payRes = await apiFetch('/api/pay', { method: 'POST', body: JSON.stringify({ paylink }) });
            if (payRes.ok) {
                toastSuccess('Оплата успешна');
                await refreshMe();
                await refreshTx();
                closeModal('scanModal');
            } else {
                toastError(payRes.error || 'Оплата не удалась');
            }
            confirmBtn.disabled = false;
        };
    } catch (e) {
        toastError('Ошибка при обработке ссылки');
    }
}

function initAccount() {
    // Кнопки
    document.getElementById('logoutBtn').addEventListener('click', async () => {
        const res = await apiFetch('/api/logout', { method: 'POST' });
        window.location.href = '/';
    });

    document.getElementById('scanBtn').addEventListener('click', () => {
        openModal('scanModal');
        startScan();
    });
    document.getElementById('createBtn').addEventListener('click', () => {
        openModal('createModal');
    });

    // Фильтры
    document.querySelectorAll('.chip').forEach(chip => {
        chip.addEventListener('click', () => {
            document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
            chip.classList.add('active');
            selectedFilter = chip.dataset.filter || 'all';
            refreshTx();
        });
    });

    // Закрытие модалок
    document.querySelectorAll('[data-close-modal]').forEach(btn => {
        btn.addEventListener('click', () => {
            const modal = btn.closest('.modal');
            modal.classList.remove('show');
            if (modal.id === 'scanModal') stopScan();
        });
    });
    document.getElementById('scanModal').addEventListener('click', (e) => {
        if (e.target.id === 'scanModal') {
            closeModal('scanModal'); stopScan();
        }
    });
    document.getElementById('createModal').addEventListener('click', (e) => {
        if (e.target.id === 'createModal') closeModal('createModal');
    });

    // Ввод ссылки вручную
    document.getElementById('manualPreviewBtn').addEventListener('click', async () => {
        const link = document.getElementById('manualPaylink').value.trim();
        if (!link) return;
        await previewInvoice(link);
    });

    // Создание QR
    const createForm = document.getElementById('createForm');
    createForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const amount = createForm.amount.value.trim();
        const description = createForm.description.value.trim();
        const btn = createForm.querySelector('button[type="submit"]');
        btn.disabled = true;
        const res = await apiFetch('/api/qr/create', { method: 'POST', body: JSON.stringify({ amount, description }) });
        btn.disabled = false;
        if (res.ok) {
            const qrBlock = document.getElementById('qrResult');
            document.getElementById('qrImage').src = res.qr_png_base64;
            const linkInput = document.getElementById('qrLink');
            linkInput.value = res.paylink;
            qrBlock.classList.remove('hidden');
            // Копирование
            document.getElementById('copyLinkBtn').onclick = async () => {
                try { await navigator.clipboard.writeText(res.paylink); toastSuccess('Ссылка скопирована'); } catch { toastError('Не удалось скопировать'); }
            };
            // Поделиться
            document.getElementById('shareBtn').onclick = async () => {
                if (navigator.share) {
                    try { await navigator.share({ title: 'Счёт на оплату', text: description || 'Счёт', url: res.paylink }); } catch { }
                } else {
                    toastError('Поделиться не поддерживается, скопируйте ссылку');
                }
            };
        } else {
            toastError(res.error || 'Не удалось создать QR');
        }
    });

    refreshMe();
    refreshTx();
}

document.addEventListener('DOMContentLoaded', () => {
    const page = document.body.dataset.page;
    if (page === 'login') initLogin();
    if (page === 'register') initRegister();
    if (page === 'account') initAccount();
});