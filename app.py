import os
import io
import json
import base64
import secrets
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, jsonify, request, session, send_from_directory, abort
import qrcode
from qrcode.constants import ERROR_CORRECT_Q

# Все в корневой папке
app = Flask(__name__, static_folder=".", static_url_path="", template_folder=".")
app.secret_key = os.environ.get("FLASK_SECRET", "dev-secret-change-me")
app.config["JSON_SORT_KEYS"] = False

DB_FILE = "db.json"
CURRENCY = "RUB"

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def round2(x: float) -> float:
    return round(float(x) + 1e-12, 2)

def ensure_db():
    if not os.path.exists(DB_FILE):
        db = {
            "users": {
                "1001": {
                    "id": "1001",
                    "name": "Анна Петрова",
                    "balance": 12500.50,
                    "currency": CURRENCY,
                    "transactions": []
                },
                "1002": {
                    "id": "1002",
                    "name": "Иван Иванов",
                    "balance": 6400.00,
                    "currency": CURRENCY,
                    "transactions": []
                }
            },
            "invoices": {}  # invoice_id -> {...}
        }
        # Немного стартовых транзакций
        add_tx(db["users"]["1001"], tx_type="topup", signed_amount=+2000.00,
               description="Пополнение", counterparty=None)
        add_tx(db["users"]["1001"], tx_type="out", signed_amount=-350.50,
               description="Кофе", counterparty="Coffee Bar")
        add_tx(db["users"]["1002"], tx_type="topup", signed_amount=+1500.00,
               description="Пополнение", counterparty=None)
        save_db(db)
    return

def load_db():
    ensure_db()
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def add_tx(user: dict, tx_type: str, signed_amount: float, description: str, counterparty=None):
    user["balance"] = round2(user.get("balance", 0.0) + signed_amount)
    tx = {
        "id": secrets.token_hex(6),
        "timestamp": now_iso(),
        "type": tx_type,  # in | out | payment | topup
        "amount": round2(abs(signed_amount)),
        "signed_amount": round2(signed_amount),  # для удобства UI
        "currency": user.get("currency", CURRENCY),
        "description": description,
        "counterparty": counterparty,
        "balance_after": user["balance"],
    }
    user.setdefault("transactions", [])
    user["transactions"].insert(0, tx)
    return tx

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        db = load_db()
        uid = session.get("user_id")
        if not uid or uid not in db["users"]:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper

@app.route("/")
def index():
    # Отдаем index.html из корневой папки
    return send_from_directory(".", "index.html")

@app.post("/api/login")
def api_login():
    db = load_db()
    data = request.get_json(silent=True) or {}
    user_id = str(data.get("user_id", "")).strip()
    if not user_id or user_id not in db["users"]:
        return jsonify({"ok": False, "error": "Пользователь не найден"}), 404
    session["user_id"] = user_id
    user = db["users"][user_id]
    return jsonify({
        "ok": True,
        "user": {
            "id": user["id"],
            "name": user["name"],
            "balance": user["balance"],
            "currency": user.get("currency", CURRENCY),
        }
    })

@app.post("/api/logout")
def api_logout():
    session.pop("user_id", None)
    return jsonify({"ok": True})

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
            "name": user["name"],
            "balance": user["balance"],
            "currency": user.get("currency", CURRENCY),
        }
    })

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
        "to_user": uid,  # получатель (кто создает счет)
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

    # Текст для QR (короткий URI-схемы)
    qr_text = f"BANKPAY://invoice?i={invoice_id}"
    # Генерируем PNG base64
    qr = qrcode.QRCode(
        version=1, error_correction=ERROR_CORRECT_Q, box_size=8, border=1
    )
    qr.add_data(qr_text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    data_url = f"data:image/png;base64,{b64}"

    return jsonify({
        "ok": True,
        "invoice": invoice,
        "qr_text": qr_text,
        "qr_png": data_url
    })

@app.post("/api/pay_invoice")
@login_required
def api_pay_invoice():
    db = load_db()
    payer_id = session["user_id"]
    payer = db["users"][payer_id]

    data = request.get_json(silent=True) or {}
    qr_text = str(data.get("qr_text", "")).strip()

    # Поддержим либо полный текст, либо просто invoice_id
    invoice_id = None
    if qr_text.startswith("BANKPAY://invoice?"):
        # очень простый парс
        parts = qr_text.split("i=", 1)
        if len(parts) == 2:
            invoice_id = parts[1].split("&", 1)[0].strip()
    else:
        # возможно, передали напрямую id
        if qr_text.startswith("INV-"):
            invoice_id = qr_text

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

    # Списываем у плательщика
    add_tx(payer, "payment", -amount,
           f"Оплата счета {invoice_id}" + (f" — {invoice['description']}" if invoice["description"] else ""),
           counterparty=receiver["name"])
    # Зачисляем получателю
    add_tx(receiver, "in", +amount,
           f"Поступление по счету {invoice_id}" + (f" — {invoice['description']}" if invoice["description"] else ""),
           counterparty=payer["name"])

    invoice["status"] = "paid"
    invoice["paid_at"] = now_iso()
    invoice["paid_by"] = payer_id

    save_db(db)
    return jsonify({
        "ok": True,
        "invoice": invoice,
        "balances": {
            "payer": {"id": payer_id, "balance": payer["balance"]},
            "receiver": {"id": receiver_id, "balance": receiver["balance"]},
        }
    })

@app.get("/api/invoice/<invoice_id>")
@login_required
def api_invoice(invoice_id):
    db = load_db()
    inv = db["invoices"].get(invoice_id)
    if not inv:
        return jsonify({"ok": False, "error": "Счет не найден"}), 404
    return jsonify({"ok": True, "invoice": inv})

# Для статических ресурсов из корня (css/js, если будут отдельными файлами)
@app.route("/<path:path>")
def static_proxy(path):
    if os.path.exists(path):
        return send_from_directory(".", path)
    abort(404)

if __name__ == "__main__":
    # DEBUG режим для разработки
    app.run(host="0.0.0.0", port=5000, debug=True)