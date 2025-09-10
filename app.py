import os
import io
import json
import base64
import secrets
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, jsonify, request, session, send_from_directory, abort, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
import qrcode
from qrcode.constants import ERROR_CORRECT_Q

# Все файлы в корне
app = Flask(__name__, static_folder=".", static_url_path="", template_folder=".")
app.secret_key = os.environ.get("FLASK_SECRET", "dev-secret-change-me")
app.config["JSON_SORT_KEYS"] = False

DB_FILE = "db.json"
CURRENCY = "RUB"

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def round2(x: float) -> float:
    return round(float(x) + 1e-12, 2)

def db_exists() -> bool:
    return os.path.exists(DB_FILE)

def load_db():
    if not db_exists():
        return None
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def new_user(db: dict, username: str, password: str, name: str | None = None, balance: float = 0.0):
    username_raw = (username or "").strip()
    username_l = username_raw.lower()
    if not username_l or not password:
        raise ValueError("Пустой логин или пароль")
    if " " in username_raw:
        raise ValueError("Имя пользователя не должно содержать пробелы")
    db.setdefault("usernames", {})
    db.setdefault("users", {})
    if username_l in db["usernames"]:
        raise ValueError("Пользователь с таким именем уже существует")

    uid = "U" + secrets.token_hex(4).upper()
    user = {
        "id": uid,
        "username": username_raw,
        "name": name or username_raw,
        "password_hash": generate_password_hash(password, method="pbkdf2:sha256", salt_length=16),
        "balance": round2(balance),
        "currency": CURRENCY,
        "transactions": []
    }
    db["users"][uid] = user
    db["usernames"][username_l] = uid
    return user

def get_user_by_username(db: dict, username: str):
    if not db:
        return None
    username_l = (username or "").strip().lower()
    uid = db.get("usernames", {}).get(username_l)
    if not uid:
        return None
    return db["users"].get(uid)

def add_tx(user: dict, tx_type: str, signed_amount: float, description: str, counterparty=None):
    user["balance"] = round2(user.get("balance", 0.0) + signed_amount)
    tx = {
        "id": secrets.token_hex(6),
        "timestamp": now_iso(),
        "type": tx_type,  # in | out | payment | topup | transfer
        "amount": round2(abs(signed_amount)),
        "signed_amount": round2(signed_amount),
        "currency": user.get("currency", CURRENCY),
        "description": description,
        "counterparty": counterparty,
        "balance_after": user["balance"],
    }
    user.setdefault("transactions", [])
    user["transactions"].insert(0, tx)
    return tx

def require_db(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not db_exists():
            return jsonify({"ok": False, "error": "База не инициализирована"}), 400
        return fn(*args, **kwargs)
    return wrapper

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not db_exists():
            return jsonify({"ok": False, "error": "База не инициализирована"}), 400
        db = load_db()
        uid = session.get("user_id")
        if not uid or uid not in db["users"]:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper

# Маршруты страниц
@app.get("/")
def index():
    return send_from_directory(".", "login.html")

@app.get("/app")
def app_page():
    if not session.get("user_id"):
        return redirect(url_for("index"))
    return send_from_directory(".", "app.html")

# Статические ресурсы (если потребуются)
@app.route("/<path:path>")
def static_proxy(path):
    if os.path.exists(path):
        return send_from_directory(".", path)
    abort(404)

# API: статус/инициализация/аутентификация
@app.get("/api/status")
def api_status():
    if not db_exists():
        return jsonify({"ok": True, "db_exists": False, "user_count": 0})
    db = load_db()
    return jsonify({"ok": True, "db_exists": True, "user_count": len(db.get("users", {}))})

@app.post("/api/setup_db")
def api_setup_db():
    if db_exists():
        return jsonify({"ok": False, "error": "База уже создана"}), 409
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    name = str(data.get("name", "")).strip() or username
    if not username or not password:
        return jsonify({"ok": False, "error": "Укажите имя пользователя и пароль"}), 400
    db = {"users": {}, "usernames": {}, "invoices": {}}
    try:
        admin = new_user(db, username=username, password=password, name=name, balance=10000.0)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    # Добавим 1-2 стартовые транзакции администратору
    add_tx(admin, "topup", +2000.00, "Пополнение (старт)", None)
    add_tx(admin, "out", -350.50, "Кофе", "Coffee Bar")
    save_db(db)
    return jsonify({"ok": True})

@app.post("/api/login")
@require_db
def api_login():
    db = load_db()
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    user = get_user_by_username(db, username)
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"ok": False, "error": "Неверное имя пользователя или пароль"}), 401
    session["user_id"] = user["id"]
    return jsonify({"ok": True})

@app.post("/api/logout")
def api_logout():
    session.pop("user_id", None)
    return jsonify({"ok": True})

# API: профиль и базовые данные
@app.get("/api/me")
@login_required
def api_me():
    db = load_db()
    uid = session["user_id"]
    user = db["users"][uid]
    return jsonify({
        "ok": True,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "name": user["name"],
            "balance": user["balance"],
            "currency": user.get("currency", CURRENCY),
        }
    })

@app.post("/api/change_profile")
@login_required
def api_change_profile():
    db = load_db()
    uid = session["user_id"]
    user = db["users"][uid]
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify({"ok": False, "error": "Имя не может быть пустым"}), 400
    user["name"] = name
    save_db(db)
    return jsonify({"ok": True})

@app.post("/api/change_password")
@login_required
def api_change_password():
    db = load_db()
    uid = session["user_id"]
    user = db["users"][uid]
    data = request.get_json(silent=True) or {}
    old = str(data.get("old_password", "")).strip()
    new = str(data.get("new_password", "")).strip()
    if not check_password_hash(user["password_hash"], old):
        return jsonify({"ok": False, "error": "Старый пароль неверен"}), 400
    if len(new) < 4:
        return jsonify({"ok": False, "error": "Новый пароль слишком короткий"}), 400
    user["password_hash"] = generate_password_hash(new, method="pbkdf2:sha256", salt_length=16)
    save_db(db)
    return jsonify({"ok": True})

# API: транзакции/баланс
@app.get("/api/transactions")
@login_required
def api_transactions():
    db = load_db()
    uid = session["user_id"]
    user = db["users"][uid]
    txs = user.get("transactions", [])
    return jsonify({"ok": True, "transactions": txs})

@app.post("/api/topup")
@login_required
def api_topup():
    db = load_db()
    uid = session["user_id"]
    user = db["users"][uid]
    data = request.get_json(silent=True) or {}
    amount = float(data.get("amount", 0))
    if amount <= 0:
        return jsonify({"ok": False, "error": "Некорректная сумма"}), 400
    add_tx(user, "topup", +round2(amount), "Пополнение (тест)", None)
    save_db(db)
    return jsonify({"ok": True, "balance": user["balance"]})

@app.post("/api/transfer")
@login_required
def api_transfer():
    db = load_db()
    uid = session["user_id"]
    from_user = db["users"][uid]
    data = request.get_json(silent=True) or {}
    to_username = str(data.get("to_username", "")).strip()
    amount = float(data.get("amount", 0))
    description = str(data.get("description", "")).strip()[:140]
    if amount <= 0:
        return jsonify({"ok": False, "error": "Некорректная сумма"}), 400
    to_user = get_user_by_username(db, to_username)
    if not to_user:
        return jsonify({"ok": False, "error": "Получатель не найден"}), 404
    if to_user["id"] == from_user["id"]:
        return jsonify({"ok": False, "error": "Нельзя переводить самому себе"}), 400
    if from_user["balance"] < amount:
        return jsonify({"ok": False, "error": "Недостаточно средств"}), 400
    add_tx(from_user, "transfer", -amount, description or f"Перевод {to_user['username']}", counterparty=to_user["name"])
    add_tx(to_user, "in", +amount, description or f"Перевод от {from_user['username']}", counterparty=from_user["name"])
    save_db(db)
    return jsonify({"ok": True, "balances": {
        "from": {"id": from_user["id"], "balance": from_user["balance"]},
        "to": {"id": to_user["id"], "balance": to_user["balance"]},
    }})

# API: счета и QR
@app.post("/api/create_invoice")
@login_required
def api_create_invoice():
    db = load_db()
    uid = session["user_id"]
    user = db["users"][uid]
    data = request.get_json(silent=True) or {}
    amount = float(data.get("amount", 0))
    description = str(data.get("description", "")).strip()[:140]
    if amount <= 0:
        return jsonify({"ok": False, "error": "Некорректная сумма"}), 400

    invoice_id = "INV-" + secrets.token_hex(5).upper()
    invoice = {
        "id": invoice_id,
        "to_user": uid,  # получатель
        "amount": round2(amount),
        "currency": user.get("currency", CURRENCY),
        "description": description,
        "created_at": now_iso(),
        "status": "unpaid",
        "paid_at": None,
        "paid_by": None,
    }
    db["invoices"][invoice_id] = invoice
    save_db(db)

    # URI для QR
    qr_text = f"BANKPAY://invoice?i={invoice_id}"
    # Генерация PNG base64
    qr = qrcode.QRCode(version=1, error_correction=ERROR_CORRECT_Q, box_size=8, border=1)
    qr.add_data(qr_text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    data_url = f"data:image/png;base64,{b64}"

    return jsonify({"ok": True, "invoice": invoice, "qr_text": qr_text, "qr_png": data_url})

def parse_invoice_id_from_text(qr_text: str):
    t = (qr_text or "").strip()
    if not t:
        return None
    # Поддержка BANKPAY://invoice?i=..., а также http(s)://...i=... и прямого INV-...
    if t.startswith("BANKPAY://invoice?"):
        parts = t.split("i=", 1)
        if len(parts) == 2:
            return parts[1].split("&", 1)[0].strip()
    if t.startswith("http://") or t.startswith("https://"):
        if "i=" in t:
            return t.split("i=", 1)[1].split("&", 1)[0].strip()
    if t.startswith("INV-"):
        return t
    return None

@app.post("/api/pay_invoice")
@login_required
def api_pay_invoice():
    db = load_db()
    payer_id = session["user_id"]
    payer = db["users"][payer_id]
    data = request.get_json(silent=True) or {}
    qr_text = str(data.get("qr_text", "")).strip()
    invoice_id = parse_invoice_id_from_text(qr_text)
    if not invoice_id or invoice_id not in db["invoices"]:
        return jsonify({"ok": False, "error": "Счет не найден"}), 404
    invoice = db["invoices"][invoice_id]
    if invoice["status"] != "unpaid":
        return jsonify({"ok": False, "error": "Счет уже оплачен или недоступен"}), 400
    receiver_id = invoice["to_user"]
    if receiver_id not in db["users"]:
        return jsonify({"ok": False, "error": "Получатель не найден"}), 404
    if payer_id == receiver_id:
        return jsonify({"ok": False, "error": "Нельзя оплатить собственный счет"}), 400
    amount = invoice["amount"]
    if payer["balance"] < amount:
        return jsonify({"ok": False, "error": "Недостаточно средств"}), 400

    receiver = db["users"][receiver_id]
    add_tx(payer, "payment", -amount,
           f"Оплата счета {invoice_id}" + (f" — {invoice['description']}" if invoice["description"] else ""),
           counterparty=receiver["name"])
    add_tx(receiver, "in", +amount,
           f"Поступление по счету {invoice_id}" + (f" — {invoice['description']}" if invoice["description"] else ""),
           counterparty=payer["name"])

    invoice["status"] = "paid"
    invoice["paid_at"] = now_iso()
    invoice["paid_by"] = payer_id
    save_db(db)
    return jsonify({"ok": True, "invoice": invoice})

@app.get("/api/invoice/<invoice_id>")
@login_required
def api_invoice(invoice_id):
    db = load_db()
    inv = db["invoices"].get(invoice_id)
    if not inv:
        return jsonify({"ok": False, "error": "Счет не найден"}), 404
    return jsonify({"ok": True, "invoice": inv})

@app.get("/api/my_invoices")
@login_required
def api_my_invoices():
    db = load_db()
    uid = session["user_id"]
    all_inv = db.get("invoices", {})
    mine = [v for v in all_inv.values() if v.get("to_user") == uid]
    mine.sort(key=lambda x: x["created_at"], reverse=True)
    return jsonify({"ok": True, "invoices": mine})

@app.post("/api/create_demo_user")
@login_required
def api_create_demo_user():
    db = load_db()
    # подберем имя: demo, demo2, demo3 ...
    base = "demo"
    idx = 0
    while True:
        uname = base if idx == 0 else f"{base}{idx+1}"
        if not get_user_by_username(db, uname):
            break
        idx += 1
        if idx > 20:
            return jsonify({"ok": False, "error": "Невозможно создать демо-пользователя"}), 400
    password = uname  # для удобства
    try:
        user = new_user(db, uname, password, name=uname.capitalize(), balance=5000.0)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    add_tx(user, "topup", +1000.00, "Пополнение (демо)", None)
    save_db(db)
    return jsonify({"ok": True, "user": {"username": uname, "password": password}})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)