import os
import io
import hmac
import time
import base64
import hashlib
import urllib.parse
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
import bcrypt
import qrcode

# ---------------------------
# Конфигурация приложения
# ---------------------------
app = Flask(__name__, static_folder='static', template_folder='templates')
app.config.update(
    SECRET_KEY='dev-secret-change-me',
    SQLALCHEMY_DATABASE_URI='sqlite:///bank.db',
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_HTTPONLY=True,
)
app.config['PAYLINK_SECRET'] = os.environ.get('PAYLINK_SECRET', 'dev-paylink-secret')

db = SQLAlchemy(app)

# ---------------------------
# Модели БД
# ---------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(128), nullable=False)
    balance_cents = db.Column(db.Integer, nullable=False, default=100000)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    amount_cents = db.Column(db.Integer, nullable=False)  # > 0
    description = db.Column(db.String(255), default='', nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


# ---------------------------
# Вспомогательные функции
# ---------------------------
def init_db():
    with app.app_context():
        db.create_all()

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def cents_to_rub_display(cents: int) -> str:
    rub = Decimal(cents) / Decimal(100)
    s = f"{rub:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")
    return f"{s} ₽"

def parse_amount_to_cents(amount_str: str) -> int:
    try:
        normalized = amount_str.strip().replace(" ", "").replace(",", ".")
        d = Decimal(normalized)
        if d <= 0:
            return -1
        cents = int((d * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        return cents
    except Exception:
        return -1

def hmac_signature(recipient_id: int, amount_cents: int, desc: str, ts: int) -> str:
    msg = f"{recipient_id}|{amount_cents}|{desc}|{ts}".encode('utf-8')
    key = app.config['PAYLINK_SECRET'].encode('utf-8')
    return hmac.new(key, msg, hashlib.sha256).hexdigest()

def generate_paylink(recipient_id: int, amount_cents: int, desc: str) -> str:
    ts = int(time.time())
    sig = hmac_signature(recipient_id, amount_cents, desc, ts)
    params = urllib.parse.urlencode({
        'rid': recipient_id,
        'amt': amount_cents,
        'desc': desc,
        'ts': ts,
        'sig': sig
    })
    return f"http://localhost:5000/paylink?{params}"

def parse_paylink(url_or_text: str):
    try:
        parsed = urllib.parse.urlparse(url_or_text)
        if parsed.scheme not in ('http', 'https') or parsed.netloc not in ('localhost:5000', '127.0.0.1:5000'):
            return None, "Некорректный домен в ссылке"
        if parsed.path != '/paylink':
            return None, "Некорректный путь в ссылке"
        q = urllib.parse.parse_qs(parsed.query)
        rid = q.get('rid', [None])[0]
        amt = q.get('amt', [None])[0]
        desc = q.get('desc', [''])[0]
        ts = q.get('ts', [None])[0]
        sig = q.get('sig', [None])[0]
        if not all([rid, amt, ts, sig]):
            return None, "Отсутствуют необходимые параметры"
        rid = int(rid)
        amt = int(amt)
        ts = int(ts)
        expected_sig = hmac_signature(rid, amt, desc, ts)
        if not hmac.compare_digest(sig, expected_sig):
            return None, "Подпись ссылки не совпала"
        return {'recipient_id': rid, 'amount_cents': amt, 'description': desc, 'timestamp': ts}, None
    except Exception:
        return None, "Не удалось разобрать ссылку"

def generate_qr_png_base64(data: str) -> str:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
    return f"data:image/png;base64,{b64}"

def login_required_json(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'ok': False, 'error': 'Требуется авторизация'}), 401
        return fn(*args, **kwargs)
    return wrapper

def current_user():
    if 'user_id' not in session:
        return None
    return User.query.get(session['user_id'])


# ---------------------------
# Маршруты фронтенда
# ---------------------------
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('account'))
    return render_template('index.html')

@app.route('/register')
def register_page():
    if 'user_id' in session:
        return redirect(url_for('account'))
    return render_template('register.html')

@app.route('/account')
def account():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    return render_template('account.html')

@app.route('/paylink')
def paylink_page():
    return render_template('paylink.html')


# ---------------------------
# API
# ---------------------------
@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.get_json(force=True)
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    if len(username) < 3:
        return jsonify({'ok': False, 'error': 'Логин должен быть от 3 символов'}), 400
    if len(password) < 6:
        return jsonify({'ok': False, 'error': 'Пароль должен быть от 6 символов'}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({'ok': False, 'error': 'Пользователь с таким логином уже существует'}), 400
    user = User(username=username, password_hash=hash_password(password))
    db.session.add(user)
    db.session.commit()
    session['user_id'] = user.id
    return jsonify({'ok': True})

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json(force=True)
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    user = User.query.filter_by(username=username).first()
    if not user or not check_password(password, user.password_hash):
        return jsonify({'ok': False, 'error': 'Неверный логин или пароль'}), 400
    session['user_id'] = user.id
    return jsonify({'ok': True})

@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/me', methods=['GET'])
@login_required_json
def api_me():
    user = current_user()
    return jsonify({
        'ok': True,
        'user': {
            'id': user.id,
            'username': user.username,
            'balance_cents': user.balance_cents,
            'balance_display': cents_to_rub_display(user.balance_cents),
        }
    })

@app.route('/api/transactions', methods=['GET'])
@login_required_json
def api_transactions():
    user = current_user()
    ttype = request.args.get('type', 'all')
    q = Transaction.query
    if ttype == 'in':
        q = q.filter(Transaction.receiver_id == user.id)
    elif ttype == 'out':
        q = q.filter(Transaction.sender_id == user.id)
    else:
        q = q.filter((Transaction.sender_id == user.id) | (Transaction.receiver_id == user.id))
    q = q.order_by(Transaction.created_at.desc(), Transaction.id.desc()).limit(100)
    rows = []
    for tx in q.all():
        direction = 'out' if tx.sender_id == user.id else 'in'
        counterparty_id = tx.receiver_id if direction == 'out' else tx.sender_id
        counterparty_name = None
        if counterparty_id:
            cp = User.query.get(counterparty_id)
            counterparty_name = cp.username if cp else 'Пользователь'
        else:
            counterparty_name = 'Система'
        rows.append({
            'id': tx.id,
            'direction': direction,
            'amount_cents': tx.amount_cents,
            'amount_display': cents_to_rub_display(tx.amount_cents),
            'description': tx.description,
            'created_at': tx.created_at.isoformat(),
            'counterparty': counterparty_name
        })
    return jsonify({'ok': True, 'transactions': rows})

@app.route('/api/qr/create', methods=['POST'])
@login_required_json
def api_qr_create():
    user = current_user()
    data = request.get_json(force=True)
    amount_str = (data.get('amount') or '').strip()
    description = (data.get('description') or '').strip()
    cents = parse_amount_to_cents(amount_str)
    if cents <= 0:
        return jsonify({'ok': False, 'error': 'Некорректная сумма'}), 400
    if len(description) > 200:
        return jsonify({'ok': False, 'error': 'Описание слишком длинное'}), 400
    paylink = generate_paylink(user.id, cents, description)
    qr_b64 = generate_qr_png_base64(paylink)
    return jsonify({'ok': True, 'paylink': paylink, 'qr_png_base64': qr_b64})

@app.route('/api/pay/preview', methods=['POST'])
@login_required_json
def api_pay_preview():
    user = current_user()
    data = request.get_json(force=True)
    paylink = data.get('paylink') or ''
    parsed, err = parse_paylink(paylink)
    if err:
        return jsonify({'ok': False, 'error': err}), 400
    rid = parsed['recipient_id']
    amount_cents = parsed['amount_cents']
    desc = parsed['description']
    receiver = User.query.get(rid)
    if not receiver:
        return jsonify({'ok': False, 'error': 'Получатель не найден'}), 400
    if receiver.id == user.id:
        return jsonify({'ok': False, 'error': 'Нельзя оплатить счёт самому себе'}), 400
    return jsonify({
        'ok': True,
        'invoice': {
            'recipient_id': receiver.id,
            'recipient_username': receiver.username,
            'amount_cents': amount_cents,
            'amount_display': cents_to_rub_display(amount_cents),
            'description': desc
        }
    })

@app.route('/api/pay', methods=['POST'])
@login_required_json
def api_pay():
    user = current_user()
    data = request.get_json(force=True)
    paylink = data.get('paylink') or ''
    parsed, err = parse_paylink(paylink)
    if err:
        return jsonify({'ok': False, 'error': err}), 400

    rid = parsed['recipient_id']
    amount_cents = parsed['amount_cents']
    desc = parsed['description']
    receiver = User.query.get(rid)
    if not receiver:
        return jsonify({'ok': False, 'error': 'Получатель не найден'}), 400
    if receiver.id == user.id:
        return jsonify({'ok': False, 'error': 'Нельзя оплатить счёт самому себе'}), 400
    if amount_cents <= 0:
        return jsonify({'ok': False, 'error': 'Некорректная сумма'}), 400
    payer = user
    if payer.balance_cents < amount_cents:
        return jsonify({'ok': False, 'error': 'Недостаточно средств'}), 400

    payer.balance_cents -= amount_cents
    receiver.balance_cents += amount_cents
    tx = Transaction(
        sender_id=payer.id,
        receiver_id=receiver.id,
        amount_cents=amount_cents,
        description=desc
    )
    db.session.add(tx)
    db.session.commit()

    return jsonify({
        'ok': True,
        'balance_cents': payer.balance_cents,
        'balance_display': cents_to_rub_display(payer.balance_cents),
        'transaction': {
            'id': tx.id,
            'direction': 'out',
            'amount_cents': tx.amount_cents,
            'amount_display': cents_to_rub_display(tx.amount_cents),
            'description': tx.description,
            'created_at': tx.created_at.isoformat(),
            'counterparty': receiver.username
        }
    })

# ---------------------------
# Точка входа
# ---------------------------
if __name__ == '__main__':
    init_db()
    app.run(host='127.0.0.1', port=5000, debug=True)