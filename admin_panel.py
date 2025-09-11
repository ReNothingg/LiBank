import os
import sqlite3
from decimal import Decimal, InvalidOperation
from functools import wraps
from flask import (
    Blueprint, render_template, request, session, redirect, url_for,
    current_app, g, abort, flash
)
from werkzeug.security import generate_password_hash
from datetime import datetime

admin_bp = Blueprint('admin', __name__, template_folder='templates', static_folder='static')


def get_db():
    db = getattr(g, '_admin_db', None)
    if db is None:
        db = sqlite3.connect(current_app.config['DB_PATH'])
        db.row_factory = sqlite3.Row
        g._admin_db = db
    return db


@admin_bp.teardown_app_request
def close_db(exception):
    db = getattr(g, '_admin_db', None)
    if db is not None:
        db.close()


def to_cents(amount_str: str) -> int:
    try:
        s = (amount_str or "").strip().replace(',', '.').replace(' ', '')
        d = Decimal(s).quantize(Decimal('0.01'))
        cents = int(d * 100)
        return cents
    except (InvalidOperation, ValueError):
        raise ValueError('Некорректная сумма')


def require_admin(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get('is_admin'):
            return redirect(url_for('admin.admin_login'))
        return f(*args, **kwargs)
    return wrapped


@admin_bp.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'GET':
        return render_template('admin/login.html')

    username = (request.form.get('username') or '').strip()
    p1 = request.form.get('pass1') or ''
    p2 = request.form.get('pass2') or ''
    p3 = request.form.get('pass3') or ''

    env1 = os.environ.get('ADMIN_PASS1')
    env2 = os.environ.get('ADMIN_PASS2')
    env3 = os.environ.get('ADMIN_PASS3')

    if not all([env1, env2, env3]):
        flash('Админ-пароли не заданы в окружении', 'error')
        return render_template('admin/login.html')

    if not username:
        flash('Укажите username для входа', 'error')
        return render_template('admin/login.html')

    if p1 == env1 and p2 == env2 and p3 == env3:
        session['is_admin'] = True
        session['admin_username'] = username
        session['admin_login_time'] = datetime.utcnow().isoformat()
        return redirect(url_for('admin.admin_index'))

    flash('Неверные админ-пароли', 'error')
    return render_template('admin/login.html')


@admin_bp.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    session.pop('admin_username', None)
    session.pop('admin_login_time', None)
    return redirect(url_for('admin.admin_login'))


@admin_bp.route('/admin/')
@require_admin
def admin_index():
    db = get_db()
    rows = db.execute("SELECT id, username, first_name, last_name, balance_cents, created_at FROM users ORDER BY id DESC").fetchall()
    return render_template('admin/dashboard.html', users=rows)


@admin_bp.route('/admin/user/<int:user_id>', methods=['GET', 'POST'])
@require_admin
def admin_user_edit(user_id):
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        abort(404)

    if request.method == 'GET':
        return render_template('admin/user.html', user=user)

    username = (request.form.get('username') or '').strip()
    first_name = (request.form.get('first_name') or '').strip()
    last_name = (request.form.get('last_name') or '').strip()
    patronymic = (request.form.get('patronymic') or '').strip()
    birth_date = (request.form.get('birth_date') or '').strip()
    balance = (request.form.get('balance') or '').strip()
    new_password = request.form.get('new_password') or ''

    if username:
        try:
            db.execute("UPDATE users SET username=? WHERE id=?", (username, user_id))
        except sqlite3.IntegrityError:
            flash('Имя пользователя уже занято', 'error')
            return render_template('admin/user.html', user=user)

    db.execute("UPDATE users SET first_name=?, last_name=?, patronymic=?, birth_date=? WHERE id=?",
               (first_name, last_name, patronymic, birth_date, user_id))

    if balance:
        try:
            cents = to_cents(balance)
            db.execute('UPDATE users SET balance_cents=? WHERE id=?', (cents, user_id))
        except ValueError:
            flash('Некорректная сумма баланса', 'error')

    if new_password:
        db.execute('UPDATE users SET password_hash=? WHERE id=?', (generate_password_hash(new_password), user_id))

    db.commit()
    flash('Пользователь обновлен', 'success')
    return redirect(url_for('admin.admin_user_edit', user_id=user_id))


@admin_bp.route('/admin/user/<int:user_id>/transactions', methods=['GET', 'POST'])
@require_admin
def admin_user_transactions(user_id):
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        abort(404)

    if request.method == 'POST':
        ttype = request.form.get('type')
        amount = request.form.get('amount')
        description = request.form.get('description') or ''
        try:
            cents = to_cents(amount)
        except ValueError:
            flash('Некорректная сумма', 'error')
            return redirect(url_for('admin.admin_user_transactions', user_id=user_id))

        if ttype == 'credit':
            new_balance = user['balance_cents'] + cents
            db.execute('UPDATE users SET balance_cents=? WHERE id=?', (new_balance, user_id))
            db.execute("INSERT INTO transactions (user_id, type, amount_cents, description, created_at) VALUES (?, 'credit', ?, ?, ?)",
                       (user_id, cents, description, datetime.utcnow().isoformat()))
        elif ttype == 'debit':
            if user['balance_cents'] < cents:
                flash('Недостаточно средств для списания', 'error')
                return redirect(url_for('admin.admin_user_transactions', user_id=user_id))
            new_balance = user['balance_cents'] - cents
            db.execute('UPDATE users SET balance_cents=? WHERE id=?', (new_balance, user_id))
            db.execute("INSERT INTO transactions (user_id, type, amount_cents, description, created_at) VALUES (?, 'debit', ?, ?, ?)",
                       (user_id, cents, description, datetime.utcnow().isoformat()))
        else:
            flash('Неизвестный тип транзакции', 'error')
            return redirect(url_for('admin.admin_user_transactions', user_id=user_id))

        db.commit()
        flash('Транзакция создана', 'success')
        return redirect(url_for('admin.admin_user_transactions', user_id=user_id))

    rows = db.execute("SELECT * FROM transactions WHERE user_id = ? ORDER BY created_at DESC", (user_id,)).fetchall()
    return render_template('admin/transactions.html', user=user, transactions=rows)


@admin_bp.route('/admin/user/<int:user_id>/transactions/<int:tx_id>/delete', methods=['POST'])
@require_admin
def admin_delete_transaction(user_id, tx_id):
    db = get_db()
    tx = db.execute('SELECT * FROM transactions WHERE id=? AND user_id=?', (tx_id, user_id)).fetchone()
    if not tx:
        abort(404)

    if tx['type'] == 'credit':
        new_balance = db.execute('SELECT balance_cents FROM users WHERE id=?', (user_id,)).fetchone()['balance_cents'] - tx['amount_cents']
    else:
        new_balance = db.execute('SELECT balance_cents FROM users WHERE id=?', (user_id,)).fetchone()['balance_cents'] + tx['amount_cents']

    db.execute('UPDATE users SET balance_cents=? WHERE id=?', (new_balance, user_id))
    db.execute('DELETE FROM transactions WHERE id=?', (tx_id,))
    db.commit()
    flash('Транзакция удалена и баланс откорректирован', 'success')
    return redirect(url_for('admin.admin_user_transactions', user_id=user_id))
