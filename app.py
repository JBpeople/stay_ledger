import os
import sqlite3
import threading
import time
from datetime import date, datetime
from functools import wraps

import requests
from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "ledger.db")
DEFAULT_PASSWORD = "P@ssw0rd"
DEFAULT_TELEGRAM_POLL_INTERVAL = 5
ALLOWED_CATEGORIES = [
    "餐饮",
    "交通",
    "购物",
    "住房",
    "水电燃气",
    "通讯",
    "医疗",
    "教育",
    "娱乐",
    "旅行",
    "工资",
    "奖金",
    "理财收益",
    "其他",
]

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-in-production")

telegram_thread = None
telegram_lock = threading.Lock()

def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL CHECK(type IN ('income', 'expense')),
            amount REAL NOT NULL CHECK(amount >= 0),
            category TEXT NOT NULL,
            note TEXT,
            happened_on TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS app_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    db.commit()


def get_config(key: str, default: str = "") -> str:
    db = get_db()
    row = db.execute("SELECT value FROM app_config WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_config(key: str, value: str) -> None:
    db = get_db()
    db.execute(
        """
        INSERT INTO app_config(key, value)
        VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    db.commit()


def add_transaction_record(
    tx_type: str, amount: float, category: str, note: str, happened_on: str
) -> None:
    db = get_db()
    db.execute(
        """
        INSERT INTO transactions(type, amount, category, note, happened_on, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (tx_type, amount, category, note, happened_on, datetime.now().isoformat()),
    )
    db.commit()


def is_password_valid(raw_password: str) -> bool:
    env_password = os.getenv("APP_PASSWORD", DEFAULT_PASSWORD)
    env_password_hash = os.getenv("APP_PASSWORD_HASH")
    if env_password_hash:
        return check_password_hash(env_password_hash, raw_password)
    return raw_password == env_password


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped_view


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if is_password_valid(password):
            session["authenticated"] = True
            return redirect(url_for("index"))
        flash("密码错误，请重试。")
    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/", methods=["GET"])
@login_required
def index():
    db = get_db()

    rows = db.execute(
        """
        SELECT id, type, amount, category, note, happened_on, created_at
        FROM transactions
        ORDER BY date(happened_on) DESC, id DESC
        LIMIT 100
        """,
    ).fetchall()

    totals = db.execute(
        """
        SELECT
          COALESCE(SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END), 0) AS income_total,
          COALESCE(SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END), 0) AS expense_total
        FROM transactions
        """
    ).fetchone()
    income_total = float(totals["income_total"] or 0)
    expense_total = float(totals["expense_total"] or 0)
    balance = income_total - expense_total

    return render_template(
        "index.html",
        rows=rows,
        income_total=income_total,
        expense_total=expense_total,
        balance=balance,
        today=date.today().isoformat(),
        categories=ALLOWED_CATEGORIES,
    )


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        enabled = "1" if request.form.get("telegram_enabled") == "on" else "0"
        bot_token = request.form.get("telegram_bot_token", "").strip()
        allowed_chat_id = request.form.get("telegram_allowed_chat_id", "").strip()

        set_config("telegram_enabled", enabled)
        set_config("telegram_bot_token", bot_token)
        set_config("telegram_allowed_chat_id", allowed_chat_id)
        flash("配置已保存。")
        return redirect(url_for("settings"))

    return render_template(
        "settings.html",
        telegram_enabled=get_config("telegram_enabled", "0") == "1",
        telegram_bot_token=get_config("telegram_bot_token", ""),
        telegram_allowed_chat_id=get_config("telegram_allowed_chat_id", ""),
        categories=ALLOWED_CATEGORIES,
    )


@app.route("/monthly-report", methods=["GET"])
@login_required
def monthly_report():
    db = get_db()
    current_month = date.today().strftime("%Y-%m")
    selected_month = request.args.get("month", current_month).strip()
    try:
        datetime.strptime(selected_month, "%Y-%m")
    except ValueError:
        selected_month = current_month

    month_rows = db.execute(
        """
        SELECT DISTINCT substr(happened_on, 1, 7) AS month
        FROM transactions
        WHERE length(happened_on) >= 7
        ORDER BY month DESC
        """
    ).fetchall()
    available_months = [r["month"] for r in month_rows if r["month"]]
    if current_month not in available_months:
        available_months.insert(0, current_month)
    if selected_month not in available_months:
        available_months.insert(0, selected_month)

    totals = db.execute(
        """
        SELECT
          COALESCE(SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END), 0) AS income_total,
          COALESCE(SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END), 0) AS expense_total
        FROM transactions
        WHERE substr(happened_on, 1, 7) = ?
        """,
        (selected_month,),
    ).fetchone()
    income_total = float(totals["income_total"] or 0)
    expense_total = float(totals["expense_total"] or 0)
    balance = income_total - expense_total

    category_rows = db.execute(
        """
        SELECT category, SUM(amount) AS total
        FROM transactions
        WHERE type = 'expense' AND substr(happened_on, 1, 7) = ?
        GROUP BY category
        ORDER BY total DESC, category ASC
        """,
        (selected_month,),
    ).fetchall()
    category_expenses = []
    for row in category_rows:
        total = float(row["total"] or 0)
        ratio = (total / expense_total * 100.0) if expense_total > 0 else 0.0
        category_expenses.append(
            {
                "category": row["category"],
                "total": total,
                "ratio": ratio,
            }
        )

    income_category_rows = db.execute(
        """
        SELECT category, SUM(amount) AS total
        FROM transactions
        WHERE type = 'income' AND substr(happened_on, 1, 7) = ?
        GROUP BY category
        ORDER BY total DESC, category ASC
        """,
        (selected_month,),
    ).fetchall()
    category_incomes = []
    for row in income_category_rows:
        total = float(row["total"] or 0)
        ratio = (total / income_total * 100.0) if income_total > 0 else 0.0
        category_incomes.append(
            {
                "category": row["category"],
                "total": total,
                "ratio": ratio,
            }
        )

    expense_rows = db.execute(
        """
        SELECT id, happened_on, category, amount, note
        FROM transactions
        WHERE type = 'expense' AND substr(happened_on, 1, 7) = ?
        ORDER BY date(happened_on) DESC, id DESC
        """,
        (selected_month,),
    ).fetchall()
    income_rows = db.execute(
        """
        SELECT id, happened_on, category, amount, note
        FROM transactions
        WHERE type = 'income' AND substr(happened_on, 1, 7) = ?
        ORDER BY date(happened_on) DESC, id DESC
        """,
        (selected_month,),
    ).fetchall()

    return render_template(
        "monthly_report.html",
        selected_month=selected_month,
        available_months=available_months,
        income_total=income_total,
        expense_total=expense_total,
        balance=balance,
        category_expenses=category_expenses,
        category_incomes=category_incomes,
        expense_rows=expense_rows,
        income_rows=income_rows,
    )


@app.post("/transactions")
@login_required
def add_transaction():
    tx_type = request.form.get("type", "").strip()
    category = request.form.get("category", "").strip()
    note = request.form.get("note", "").strip()
    happened_on = request.form.get("happened_on", "").strip()
    amount_raw = request.form.get("amount", "").strip()

    try:
        amount = float(amount_raw)
    except ValueError:
        flash("金额必须是数字。")
        return redirect(url_for("index"))

    if tx_type not in {"income", "expense"}:
        flash("收支类型不合法。")
        return redirect(url_for("index"))
    if amount < 0:
        flash("金额不能小于 0。")
        return redirect(url_for("index"))
    if not category:
        flash("分类不能为空。")
        return redirect(url_for("index"))
    if category not in ALLOWED_CATEGORIES:
        flash("分类不在可选范围内。")
        return redirect(url_for("index"))
    try:
        datetime.strptime(happened_on, "%Y-%m-%d")
    except ValueError:
        flash("日期格式错误。")
        return redirect(url_for("index"))

    add_transaction_record(tx_type, amount, category, note, happened_on)
    return redirect(url_for("index"))


@app.post("/transactions/<int:tx_id>/delete")
@login_required
def delete_transaction(tx_id: int):
    db = get_db()
    db.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
    db.commit()
    return redirect(url_for("index"))


@app.route("/transactions/<int:tx_id>/edit", methods=["GET", "POST"])
@login_required
def edit_transaction(tx_id: int):
    db = get_db()
    row = db.execute(
        """
        SELECT id, type, amount, category, note, happened_on
        FROM transactions
        WHERE id = ?
        """,
        (tx_id,),
    ).fetchone()
    if not row:
        flash("记录不存在。")
        return redirect(url_for("index"))

    if request.method == "POST":
        tx_type = request.form.get("type", "").strip()
        category = request.form.get("category", "").strip()
        note = request.form.get("note", "").strip()
        happened_on = request.form.get("happened_on", "").strip()
        amount_raw = request.form.get("amount", "").strip()

        try:
            amount = float(amount_raw)
        except ValueError:
            flash("金额必须是数字。")
            return redirect(url_for("edit_transaction", tx_id=tx_id))

        if tx_type not in {"income", "expense"}:
            flash("收支类型不合法。")
            return redirect(url_for("edit_transaction", tx_id=tx_id))
        if amount < 0:
            flash("金额不能小于 0。")
            return redirect(url_for("edit_transaction", tx_id=tx_id))
        if category not in ALLOWED_CATEGORIES:
            flash("分类不在可选范围内。")
            return redirect(url_for("edit_transaction", tx_id=tx_id))
        try:
            datetime.strptime(happened_on, "%Y-%m-%d")
        except ValueError:
            flash("日期格式错误。")
            return redirect(url_for("edit_transaction", tx_id=tx_id))

        db.execute(
            """
            UPDATE transactions
            SET type = ?, amount = ?, category = ?, note = ?, happened_on = ?
            WHERE id = ?
            """,
            (tx_type, amount, category, note, happened_on, tx_id),
        )
        db.commit()
        return redirect(url_for("index"))

    return render_template(
        "edit_transaction.html",
        tx=row,
        categories=ALLOWED_CATEGORIES,
    )


@app.cli.command("hash-password")
def hash_password():
    password = input("Password: ")
    print(generate_password_hash(password))


def parse_telegram_transaction(text: str):
    line = (text or "").strip()
    if not line:
        return None, "消息为空，请发送记账命令。"

    normalized = line
    if normalized.startswith("/"):
        normalized = normalized[1:]
    parts = normalized.split()
    if not parts:
        return None, "消息为空，请发送记账命令。"

    cmd = parts[0].lower()
    if cmd in {"help", "start"}:
        return "help", ""
    if len(parts) < 3:
        return None, "格式错误。示例：/expense 32.5 餐饮 午饭"

    type_map = {
        "expense": "expense",
        "income": "income",
        "add_expense": "expense",
        "add_income": "income",
        "支出": "expense",
        "收入": "income",
    }

    if cmd == "add":
        if len(parts) < 4:
            return None, "格式错误。示例：/add 支出 32.5 餐饮 午饭"
        cmd = parts[1].lower()
        parts = parts[1:]

    tx_type = type_map.get(cmd)
    if not tx_type:
        return None, "不支持的命令。请使用 /expense 或 /income。"

    try:
        amount = float(parts[1])
    except ValueError:
        return None, "金额必须是数字。"
    if amount < 0:
        return None, "金额不能小于 0。"

    category = parts[2]
    if category not in ALLOWED_CATEGORIES:
        return None, f"分类无效。可用分类：{', '.join(ALLOWED_CATEGORIES)}"
    note = " ".join(parts[3:]).strip() if len(parts) > 3 else ""

    return {
        "type": tx_type,
        "amount": amount,
        "category": category,
        "note": note,
        "happened_on": date.today().isoformat(),
    }, ""


def telegram_api_get(token: str, method: str, params: dict):
    url = f"https://api.telegram.org/bot{token}/{method}"
    return requests.get(url, params=params, timeout=30)


def telegram_api_post(token: str, method: str, payload: dict):
    url = f"https://api.telegram.org/bot{token}/{method}"
    return requests.post(url, json=payload, timeout=30)


def send_telegram_message(token: str, chat_id: int, text: str):
    try:
        telegram_api_post(token, "sendMessage", {"chat_id": chat_id, "text": text})
    except requests.RequestException:
        return


def handle_telegram_message(token: str, message: dict):
    text = message.get("text", "")
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    if chat_id is None:
        return

    normalized_text = text.strip().lower()
    if normalized_text in {"/myid", "myid"}:
        send_telegram_message(token, chat_id, f"当前 chat id: {chat_id}")
        return

    allowed_chat_id = get_config("telegram_allowed_chat_id", "").strip()
    if allowed_chat_id and str(chat_id) != allowed_chat_id:
        send_telegram_message(
            token,
            chat_id,
            f"未授权聊天。当前 chat id: {chat_id}，请在系统配置中绑定后再试。",
        )
        return

    parsed, err = parse_telegram_transaction(text)
    if parsed == "help":
        send_telegram_message(
            token,
            chat_id,
            "记账命令:\n/expense 金额 分类 备注\n/income 金额 分类 备注\n示例: /expense 32.5 餐饮 午饭",
        )
        return
    if err:
        send_telegram_message(token, chat_id, err)
        return

    add_transaction_record(
        parsed["type"],
        parsed["amount"],
        parsed["category"],
        parsed["note"],
        parsed["happened_on"],
    )
    tx_text = "收入" if parsed["type"] == "income" else "支出"
    send_telegram_message(
        token,
        chat_id,
        f"已记账: {tx_text} ¥{parsed['amount']:.2f} / {parsed['category']}",
    )


def telegram_poll_loop():
    with app.app_context():
        while True:
            enabled = get_config("telegram_enabled", "0") == "1"
            token = get_config("telegram_bot_token", "").strip()
            if not enabled or not token:
                time.sleep(3)
                continue

            try:
                poll_interval = int(get_config("telegram_poll_interval", str(DEFAULT_TELEGRAM_POLL_INTERVAL)))
            except ValueError:
                poll_interval = DEFAULT_TELEGRAM_POLL_INTERVAL
            poll_interval = max(2, poll_interval)

            try:
                last_update_id = int(get_config("telegram_last_update_id", "0"))
            except ValueError:
                last_update_id = 0

            try:
                resp = telegram_api_get(
                    token,
                    "getUpdates",
                    {
                        "timeout": 20,
                        "offset": last_update_id + 1,
                        "allowed_updates": ["message"],
                    },
                )
                data = resp.json()
            except (requests.RequestException, ValueError):
                time.sleep(poll_interval)
                continue

            if not data.get("ok"):
                time.sleep(poll_interval)
                continue

            for update in data.get("result", []):
                update_id = update.get("update_id")
                message = update.get("message")
                if isinstance(update_id, int):
                    set_config("telegram_last_update_id", str(update_id))
                if message:
                    handle_telegram_message(token, message)


def start_telegram_poller():
    global telegram_thread
    with telegram_lock:
        if telegram_thread and telegram_thread.is_alive():
            return
        telegram_thread = threading.Thread(target=telegram_poll_loop, daemon=True, name="telegram-poller")
        telegram_thread.start()


with app.app_context():
    init_db()
    if not get_config("telegram_poll_interval", ""):
        set_config("telegram_poll_interval", str(DEFAULT_TELEGRAM_POLL_INTERVAL))
start_telegram_poller()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
