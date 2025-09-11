import os
import sqlite3
import re  # Для валидации пароля
import csv  # Для экспорта в CSV
from flask import (
    Flask, request, session, jsonify,
    render_template, send_file, Blueprint, g, redirect, url_for, abort, Response
)
from werkzeug.security import generate_password_hash, check_password_hash
from io import BytesIO, StringIO
import qrcode
from datetime import datetime
from decimal import Decimal, InvalidOperation

# -------------------------
# App & Config
# -------------------------
app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev_secret_change_me')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['DEBUG_LOGIN_BY_ID'] = True  # для тестового входа по ID
DB_PATH = os.path.join(os.path.dirname(__file__), 'bank.sqlite3')

# Blueprints для масштабирования
api = Blueprint('api', __name__, url_prefix='/api')
web = Blueprint('web', __name__)

# -------------------------
# DB Helpers
# -------------------------
def get_db():
    db = getattr(g, '_db', None)
    if db is None:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        g._db = db
    return db

@app.teardown_appcontext
def close_db(exception):
    db = getattr(g, '_db', None)
    if db is not None:
        db.close()

def now_iso():
    return datetime.utcnow().isoformat()

def to_cents(amount_str: str) -> int:
    try:
        s = (amount_str or "").strip().replace(',', '.').replace(' ', '')
        d = Decimal(s).quantize(Decimal('0.01'))
        cents = int(d * 100)
        if cents <= 0:
            raise ValueError("Сумма должна быть положительной")
        return cents
    except (InvalidOperation, ValueError):
        raise ValueError("Некорректная сумма")

def init_db():
    db = get_db()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL,
      first_name TEXT,
      last_name TEXT,
      patronymic TEXT,
      birth_date TEXT,
      balance_cents INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS transactions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      type TEXT CHECK(type IN ('debit','credit')) NOT NULL,
      amount_cents INTEGER NOT NULL,
      description TEXT,
      counterparty_id INTEGER,
      invoice_id INTEGER,
      created_at TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS invoices (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      creator_id INTEGER NOT NULL,
      amount_cents INTEGER NOT NULL,
      description TEXT,
      status TEXT CHECK(status IN ('pending','paid','cancelled')) NOT NULL DEFAULT 'pending',
      created_at TEXT NOT NULL,
      paid_by INTEGER,
      paid_at TEXT,
      FOREIGN KEY(creator_id) REFERENCES users(id)
    );

    CREATE INDEX IF NOT EXISTS idx_trans_user ON transactions(user_id);
    CREATE INDEX IF NOT EXISTS idx_inv_status ON invoices(status);
    """)
    count = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()['c']
    if count == 0:
        users = [
            ('alice', generate_password_hash('Pass1234'), 'Алиса', 'Иванова', None, '1990-05-15', 500000, now_iso()),
            ('bob', generate_password_hash('Qwerty987'), 'Борис', 'Петров', 'Сергеевич', '1988-11-20', 250000, now_iso()),
        ]
        db.executemany(
            "INSERT INTO users (username, password_hash, first_name, last_name, patronymic, birth_date, balance_cents, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            users
        )
        db.commit()

@app.before_request
def ensure_db():
    if not os.path.exists(DB_PATH):
        open(DB_PATH, 'a').close()
    init_db()

def require_login():
    uid = session.get('user_id')
    if not uid:
        abort(401, description="Требуется вход")
    return uid

def get_user_by_id(user_id):
    db = get_db()
    return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

def get_user_by_username(username):
    db = get_db()
    return db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

# NEW: Password validation helper
def is_password_strong(password):
    if len(password) < 8: return False, "Пароль должен быть не менее 8 символов"
    if not re.search(r"[A-Z]", password): return False, "Пароль должен содержать хотя бы одну заглавную букву"
    if not re.search(r"[a-z]", password): return False, "Пароль должен содержать хотя бы одну строчную букву"
    if not re.search(r"[0-9]", password): return False, "Пароль должен содержать хотя бы одну цифру"
    return True, ""

def serialize_user(row, full=False):
    full_name = ' '.join(filter(None, [row["last_name"], row["first_name"], row["patronymic"]]))
    data = {
        "id": row["id"],
        "username": row["username"],
        "full_name": full_name or row["username"],
        "balance_cents": row["balance_cents"],
    }
    if full: # Add more details for profile page
        data.update({
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "patronymic": row["patronymic"] or "",
            "birth_date": row["birth_date"],
        })
    return data

def serialize_transaction(row, full=False):
    data = {
        "id": row["id"],
        "type": row["type"],
        "amount_cents": row["amount_cents"],
        "description": row["description"] or "",
        "counterparty_id": row["counterparty_id"],
        "counterparty_username": row["counterparty_username"],
        "created_at": row["created_at"]
    }
    if full: # Add extra details for detail view
        data.update({
            "invoice_id": row["invoice_id"],
            "user_id": row["user_id"],
        })
    return data

def serialize_invoice(row, include_creator=False):
    data = {
        "id": row["id"],
        "creator_id": row["creator_id"],
        "amount_cents": row["amount_cents"],
        "description": row["description"] or "",
        "status": row["status"],
        "created_at": row["created_at"],
        "paid_by": row["paid_by"],
        "paid_at": row["paid_at"]
    }
    if include_creator:
        db = get_db()
        u = db.execute("SELECT username FROM users WHERE id = ?", (row["creator_id"],)).fetchone()
        data["creator_username"] = u["username"] if u else None
    return data

def transfer_funds(payer_id: int, recipient_id: int, amount_cents: int, description: str, invoice_id: int = None):
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        payer = db.execute("SELECT id, balance_cents FROM users WHERE id = ?", (payer_id,)).fetchone()
        recipient = db.execute("SELECT id, balance_cents FROM users WHERE id = ?", (recipient_id,)).fetchone()
        if not payer or not recipient:
            raise ValueError("Пользователь не найден")

        if payer["balance_cents"] < amount_cents:
            raise ValueError("Недостаточно средств")

        new_payer_balance = payer["balance_cents"] - amount_cents
        new_recipient_balance = recipient["balance_cents"] + amount_cents
        db.execute("UPDATE users SET balance_cents = ? WHERE id = ?", (new_payer_balance, payer_id))
        db.execute("UPDATE users SET balance_cents = ? WHERE id = ?", (new_recipient_balance, recipient_id))

        ts = now_iso()
        db.execute("""
            INSERT INTO transactions (user_id, type, amount_cents, description, counterparty_id, invoice_id, created_at)
            VALUES (?, 'debit', ?, ?, ?, ?, ?)
        """, (payer_id, amount_cents, description, recipient_id, invoice_id, ts))
        db.execute("""
            INSERT INTO transactions (user_id, type, amount_cents, description, counterparty_id, invoice_id, created_at)
            VALUES (?, 'credit', ?, ?, ?, ?, ?)
        """, (recipient_id, amount_cents, description, payer_id, invoice_id, ts))
        db.commit()
    except Exception as e:
        db.execute("ROLLBACK")
        raise e

# -------------------------
# WEB (Frontend pages)
# -------------------------
@web.route('/')
def index():
    if session.get('user_id'):
        return redirect(url_for('web.account'))
    return render_template('index.html')

@web.route('/account')
def account():
    if not session.get('user_id'):
        return redirect(url_for('web.index'))
    return render_template('account.html')

# NEW: Profile page route
@web.route('/profile')
def profile():
    if not session.get('user_id'):
        return redirect(url_for('web.index'))
    return render_template('profile.html')


app.register_blueprint(web)

# -------------------------
# API (JSON)
# -------------------------
@api.route('/register', methods=['POST'])
def api_register():
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()
    password_confirm = (data.get('password_confirm') or '').strip()
    first_name = (data.get('first_name') or '').strip()
    last_name = (data.get('last_name') or '').strip()
    patronymic = (data.get('patronymic') or '').strip()
    birth_date = (data.get('birth_date') or '').strip()

    if not all([username, password, password_confirm, first_name, last_name, birth_date]):
        return jsonify(ok=False, error="Все обязательные поля должны быть заполнены"), 400
    if password != password_confirm:
        return jsonify(ok=False, error="Пароли не совпадают"), 400

    # NEW: Strong password check
    is_strong, message = is_password_strong(password)
    if not is_strong:
        return jsonify(ok=False, error=message), 400

    if len(username) < 3:
        return jsonify(ok=False, error="Слишком короткий логин"), 400
    try:
        datetime.strptime(birth_date, '%Y-%m-%d')
    except ValueError:
        return jsonify(ok=False, error="Некорректный формат даты рождения"), 400

    db = get_db()
    try:
        db.execute(
            """INSERT INTO users (username, password_hash, first_name, last_name, patronymic, birth_date, balance_cents, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (username, generate_password_hash(password), first_name, last_name, patronymic, birth_date, 10000, now_iso()) # Welcome bonus 100 RUB
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify(ok=False, error="Пользователь с таким логином уже существует"), 409

    user = get_user_by_username(username)
    session['user_id'] = user["id"]
    return jsonify(ok=True, user=serialize_user(user))

@api.route('/login', methods=['POST'])
def api_login():
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()
    if not username or not password:
        return jsonify(ok=False, error="Укажите логин и пароль"), 400
    user = get_user_by_username(username)
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify(ok=False, error="Неверный логин или пароль"), 401
    session['user_id'] = user["id"]
    return jsonify(ok=True, user=serialize_user(user))

@api.route('/login_by_id', methods=['POST'])
def api_login_by_id():
    if not app.config.get('DEBUG_LOGIN_BY_ID'):
        abort(404)
    data = request.get_json(force=True, silent=True) or {}
    try:
        user_id = int(data.get('user_id'))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Некорректный ID"), 400
    user = get_user_by_id(user_id)
    if not user:
        return jsonify(ok=False, error="Пользователь не найден"), 404
    session['user_id'] = user["id"]
    return jsonify(ok=True, user=serialize_user(user))

@api.route('/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify(ok=True)

@api.route('/me', methods=['GET'])
def api_me():
    uid = require_login()
    user = get_user_by_id(uid)
    return jsonify(ok=True, user=serialize_user(user, full=True)) # Use full serialization

# NEW: Update profile endpoint
@api.route('/me', methods=['PUT'])
def api_update_me():
    uid = require_login()
    data = request.get_json(force=True, silent=True) or {}
    first_name = (data.get('first_name') or '').strip()
    last_name = (data.get('last_name') or '').strip()
    patronymic = (data.get('patronymic') or '').strip()
    birth_date = (data.get('birth_date') or '').strip()

    if not all([first_name, last_name, birth_date]):
        return jsonify(ok=False, error="Имя, фамилия и дата рождения обязательны"), 400
    try:
        datetime.strptime(birth_date, '%Y-%m-%d')
    except ValueError:
        return jsonify(ok=False, error="Некорректный формат даты рождения"), 400

    db = get_db()
    db.execute("""
        UPDATE users SET first_name=?, last_name=?, patronymic=?, birth_date=?
        WHERE id = ?
    """, (first_name, last_name, patronymic, birth_date, uid))
    db.commit()
    return jsonify(ok=True, message="Профиль обновлён")

# NEW: Change password endpoint
@api.route('/me/password', methods=['PUT'])
def api_change_password():
    uid = require_login()
    data = request.get_json(force=True, silent=True) or {}
    current_password = data.get('current_password')
    new_password = data.get('new_password')
    new_password_confirm = data.get('new_password_confirm')

    if not all([current_password, new_password, new_password_confirm]):
        return jsonify(ok=False, error="Все поля обязательны"), 400

    user = get_user_by_id(uid)
    if not check_password_hash(user['password_hash'], current_password):
        return jsonify(ok=False, error="Текущий пароль неверен"), 403

    if new_password != new_password_confirm:
        return jsonify(ok=False, error="Новые пароли не совпадают"), 400

    is_strong, message = is_password_strong(new_password)
    if not is_strong:
        return jsonify(ok=False, error=message), 400

    db = get_db()
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_password), uid))
    db.commit()
    return jsonify(ok=True, message="Пароль успешно изменён")

@api.route('/transactions', methods=['GET'])
def api_transactions():
    uid = require_login()
    q = (request.args.get('q') or '').strip()
    ttype = (request.args.get('type') or '').strip()
    limit = 50
    offset = 0

    db = get_db()
    sql = """
    SELECT t.*, u.username as counterparty_username
    FROM transactions t
    LEFT JOIN users u ON u.id = t.counterparty_id
    WHERE t.user_id = ?
    """
    params = [uid]

    if ttype in ('debit', 'credit'):
        sql += " AND t.type = ?"
        params.append(ttype)
    if q:
        sql += " AND (t.description LIKE ? OR u.username LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like])

    sql += " ORDER BY t.created_at DESC, t.id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = db.execute(sql, params).fetchall()
    return jsonify(ok=True, items=[serialize_transaction(r) for r in rows])

# NEW: Get single transaction details
@api.route('/transactions/<int:tx_id>', methods=['GET'])
def api_transaction_details(tx_id):
    uid = require_login()
    db = get_db()
    row = db.execute("""
        SELECT t.*, u.username as counterparty_username
        FROM transactions t
        LEFT JOIN users u ON u.id = t.counterparty_id
        WHERE t.id = ? AND t.user_id = ?
    """, (tx_id, uid)).fetchone()
    if not row:
        return jsonify(ok=False, error="Транзакция не найдена"), 404
    return jsonify(ok=True, transaction=serialize_transaction(row, full=True))

# NEW: Export transactions to CSV
@api.route('/transactions/export', methods=['GET'])
def api_export_transactions():
    uid = require_login()
    db = get_db()
    rows = db.execute("""
        SELECT t.id, t.created_at, t.type, t.amount_cents, t.description, u.username as counterparty_username
        FROM transactions t
        LEFT JOIN users u ON u.id = t.counterparty_id
        WHERE t.user_id = ?
        ORDER BY t.created_at DESC
    """, (uid,)).fetchall()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Дата', 'Тип', 'Сумма (коп.)', 'Описание', 'Контрагент'])
    for row in rows:
        writer.writerow([row['id'], row['created_at'], row['type'], row['amount_cents'], row['description'], row['counterparty_username']])

    output.seek(0)
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename=transactions_{datetime.now().strftime('%Y%m%d')}.csv"}
    )

@api.route('/invoices', methods=['POST'])
def api_create_invoice():
    uid = require_login()
    data = request.get_json(force=True, silent=True) or {}
    try:
        amount_cents = to_cents(data.get('amount'))
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    description = (data.get('description') or '').strip()

    db = get_db()
    cur = db.execute("""
        INSERT INTO invoices (creator_id, amount_cents, description, status, created_at)
        VALUES (?, ?, ?, 'pending', ?)
    """, (uid, amount_cents, description, now_iso()))
    db.commit()
    invoice_id = cur.lastrowid
    payload = f"PAY:{invoice_id}"
    qr_url = url_for('api.qr_png', invoice_id=invoice_id)
    return jsonify(ok=True, invoice={
        "id": invoice_id, "amount_cents": amount_cents, "description": description,
        "status": "pending", "qr_url": qr_url, "payload": payload
    })

@api.route('/invoices/<int:invoice_id>', methods=['GET'])
def api_get_invoice(invoice_id):
    require_login()
    db = get_db()
    inv = db.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not inv:
        return jsonify(ok=False, error="Счёт не найден"), 404
    return jsonify(ok=True, invoice=serialize_invoice(inv, include_creator=True))

@api.route('/pay', methods=['POST'])
def api_pay_invoice():
    uid = require_login()
    data = request.get_json(force=True, silent=True) or {}
    try:
        invoice_id = int(data.get('invoice_id'))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Некорректный номер счёта"), 400

    db = get_db()
    inv = db.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not inv: return jsonify(ok=False, error="Счёт не найден"), 404
    if inv["status"] != "pending": return jsonify(ok=False, error="Счёт уже оплачен или отменён"), 400
    if inv["creator_id"] == uid: return jsonify(ok=False, error="Нельзя оплатить собственный счёт"), 400

    description = inv["description"] or f"Оплата счёта #{invoice_id}"
    try:
        transfer_funds(payer_id=uid, recipient_id=inv["creator_id"], amount_cents=inv["amount_cents"], description=description, invoice_id=invoice_id)
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400

    db.execute("UPDATE invoices SET status='paid', paid_by=?, paid_at=? WHERE id=?", (uid, now_iso(), invoice_id))
    db.commit()
    payer = get_user_by_id(uid)
    return jsonify(ok=True, message="Оплата успешна", balance_cents=payer["balance_cents"])

@api.route('/transfer', methods=['POST'])
def api_transfer():
    uid = require_login()
    data = request.get_json(force=True, silent=True) or {}
    recipient_username = (data.get('recipient_username') or '').strip()
    description = (data.get('description') or '').strip()
    try:
        amount_cents = to_cents(data.get('amount'))
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400

    if not recipient_username:
        return jsonify(ok=False, error="Укажите получателя"), 400

    recipient = get_user_by_username(recipient_username)
    if not recipient:
        return jsonify(ok=False, error="Получатель не найден"), 404
    if recipient['id'] == uid:
        return jsonify(ok=False, error="Нельзя перевести средства самому себе"), 400

    final_description = description or f"Перевод пользователю @{recipient_username}"
    try:
        transfer_funds(payer_id=uid, recipient_id=recipient['id'], amount_cents=amount_cents, description=final_description)
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400

    payer = get_user_by_id(uid)
    return jsonify(ok=True, message="Перевод успешен", balance_cents=payer["balance_cents"])

@api.route('/qr/<int:invoice_id>.png', methods=['GET'])
def qr_png(invoice_id):
    payload = f"PAY:{invoice_id}"
    img = qrcode.make(payload)
    buf = BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', download_name=f'invoice_{invoice_id}.png')

app.register_blueprint(api)

# -------------------------
# Error handlers
# -------------------------
@app.errorhandler(401)
def err_401(e):
    if request.path.startswith('/api/'):
        return jsonify(ok=False, error=getattr(e, 'description', "Unauthorized")), 401
    return redirect(url_for('web.index'))

@app.errorhandler(404)
def err_404(e):
    return jsonify(ok=False, error="Не найдено"), 404 if request.path.startswith('/api/') else ("Страница не найдена", 404)

@app.errorhandler(400)
def err_400(e):
    return jsonify(ok=False, error=getattr(e, 'description', "Некорректный запрос")), 400 if request.path.startswith('/api/') else ("Некорректный запрос", 400)

# -------------------------
# Run
# -------------------------
if __name__ == '__main__':
    # Создаем базу данных и таблицы, если их нет, перед первым запросом
    with app.app_context():
        # Важно: Alembic теперь управляет схемой. Эту строку можно убрать после первой миграции.
        # db.create_all()
        pass
    app.run(debug=True)