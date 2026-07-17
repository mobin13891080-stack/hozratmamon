# -*- coding: utf-8 -*-
# database.py
# تمام توابع مربوط به دیتاباز Turso (libSQL)
# شامل: تنطیمات، ادمین‌ها (با سطح دسترسی)، کاربران، پلن‌ها، سفارش‌ها،
# کد تخفیف، سرورها، لاگ‌ها و سیستم تیکت پشتیبانی

import asyncio
import datetime

import libsql_client

import config

_client = None

ADMIN_ROLES = ("full", "sales", "support")

ROLE_PERMISSIONS = {
    "full": {
        "users", "orders", "packages", "servers", "finance", "discounts",
        "settings", "notify", "stats", "admins", "logs", "tickets", "bot_toggle",
    },
    "sales": {"orders", "packages", "discounts", "finance", "stats", "notify"},
    "support": {"tickets", "users", "stats"},
}


def get_client():
    """گرفتن یک نمونه از کلاینت دیتاباز (فقط یک بار ساخته می‌شود)"""
    global _client
    if _client is None:
        _client = libsql_client.create_client(
            url=config.TURSO_DATABASE_URL,
            auth_token=config.TURSO_AUTH_TOKEN,
        )
    return _client


async def _reset_client():
    """بستن و ساخت مجدد کلاینت دیتاباز بعد از خطای شبکه/موقت"""
    global _client
    old = _client
    _client = None
    if old is not None:
        try:
            await old.close()
        except Exception:
            pass


async def execute(sql: str, params=None, retries: int = 2):
    """اجرای امن یک کوئری روی Turso با تلاش مجدد در صورت خطای موقت شبکه/دیتابیز.
    این تابع جای execute(...) مستقیم را می‌گیرد تا کرش‌های موقت
    (مثل KeyError: 'result' یا قطعی شبکه) کل ربات را از کار نیندازد"""
    last_exc = None
    for attempt in range(retries + 1):
        try:
            client = get_client()
            if params is None:
                return await client.execute(sql)
            return await client.execute(sql, params)
        except Exception as e:
            last_exc = e
            await _reset_client()
            if attempt < retries:
                await asyncio.sleep(0.35 * (attempt + 1))
                continue
    raise last_exc


def _now() -> str:
    return datetime.datetime.utcnow().isoformat()


def _today() -> str:
    return datetime.datetime.utcnow().date().isoformat()


def _rows_as_dicts(result_set) -> list:
    """تبدیل نتیجه کوئری به لیستی از دیکشنری‌ها"""
    return [dict(zip(result_set.columns, row)) for row in result_set.rows]


async def _try_execute(client, statement: str):
    """اجرای یک دستور مهاجرتی (مانند ALTER TABLE) و نادیده گرفتن خطای
    «ستاک» اگر ستون از قبل وجود دارد"""
    try:
        await client.execute(statement)
    except Exception:
        pass


async def init_db():
    """ساخت جدول‌ها در صورت نبودن، انجام مهاجرت‌های ساده و مقداردهی اولیه تنطیمات"""
    client = get_client()
    await client.batch(
        [
            """CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                added_by INTEGER,
                added_at TEXT,
                role TEXT DEFAULT 'full'
            )""",
            """CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                joined_at TEXT,
                used_trial INTEGER DEFAULT 0,
                is_blocked INTEGER DEFAULT 0,
                rules_accepted INTEGER DEFAULT 0
            )""",
            """CREATE TABLE IF NOT EXISTS packages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                gb REAL,
                days INTEGER,
                price INTEGER,
                active INTEGER DEFAULT 1
            )""",
            """CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                package_id INTEGER,
                status TEXT DEFAULT 'pending',
                receipt_file_id TEXT,
                marzban_username TEXT,
                subscription_link TEXT,
                created_at TEXT,
                price INTEGER,
                discount_code TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS discount_codes (
                code TEXT PRIMARY KEY,
                kind TEXT,
                value REAL,
                max_uses INTEGER DEFAULT 0,
                used_count INTEGER DEFAULT 0,
                expires_at TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                address TEXT,
                enabled INTEGER DEFAULT 1,
                added_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                log_type TEXT,
                actor_id INTEGER,
                message TEXT,
                created_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                subject TEXT,
                status TEXT DEFAULT 'open',
                created_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS ticket_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER,
                sender_id INTEGER,
                is_admin INTEGER DEFAULT 0,
                message TEXT,
                created_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS wallet_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                kind TEXT,
                status TEXT DEFAULT 'done',
                receipt_file_id TEXT,
                note TEXT,
                created_at TEXT
            )""",
        ]
    )

    # مهاجرت ستون‌های جدید برای دیتاباز‌های قدیمی‌تر (خطای «ستاک» اگر قبلاً افزوده شده نادیده گرفته می‌شود)
    await _try_execute(client, "ALTER TABLE users ADD COLUMN is_blocked INTEGER DEFAULT 0")
    await _try_execute(client, "ALTER TABLE admins ADD COLUMN role TEXT DEFAULT 'full'")
    await _try_execute(client, "ALTER TABLE orders ADD COLUMN price INTEGER")
    await _try_execute(client, "ALTER TABLE orders ADD COLUMN discount_code TEXT")
    await _try_execute(client, "ALTER TABLE users ADD COLUMN wallet_balance INTEGER DEFAULT 0")
    await _try_execute(client, "ALTER TABLE orders ADD COLUMN order_type TEXT DEFAULT 'purchase'")

    defaults = {
        "card_number": "0000-0000-0000-0000",
        "card_holder": "-",
        "force_join_enabled": "0",
        "force_join_channel": "",
        "force_join_channels": "[]",
        "trial_enabled": "1",
        "trial_gb": "1",
        "trial_days": "1",
        "welcome_text": (
            "✨ به ربات فروش سرویس VPN خوش آمدید! ✨\n"
            "━━━━━━━━━━━\n"
            "🚀 سریع  |  🔒 امن  |  ⚡️ بدون قطعی\n"
            "━━━━━━━━━━━\n"
            "از منوی زیر یکی از گزینه‌ها رو انتخاب کن 👇"
        ),
        "welcome_photo_id": "",
        "support_text": (
            "🆘 پشتیبانی\n\nبرای هرگونه سوال به پشتیبانی پیام دهید:"
        ),
        "support_username": "@YourSupportUsername",
        "bot_enabled": "1",
        "rules_text": (
            "1- به اطلاعیه هایی که داخل کانال گذاشته می شود حتما توجه کنید.\n"
            "2- در صورتی که اطلاعیه ای در مورد قطعی در کانال گذاشته نشده به اکانت پشتیبانی پیام دهید\n"
            "3- سرویس ها را از طریق پیامک ارسال نکنید، برای ارسال پیامک می توانید از طریق ایمیل ارسال کنید."
        ),
        "contact_links": "",
    }
    for key, value in defaults.items():
        current = await get_setting(key)
        if current is None:
            await set_setting(key, value)


# ==================== لاگ فعالیت ====================

async def log_action(log_type: str, actor_id, message: str):
    try:
        client = get_client()
        await execute(
            "INSERT INTO logs (log_type, actor_id, message, created_at) VALUES (?, ?, ?, ?)",
            [log_type, actor_id, message, _now()],
        )
    except Exception:
        pass


async def list_logs(log_type: str = None, limit: int = 30) -> list:
    client = get_client()
    if log_type:
        rs = await execute(
            "SELECT * FROM logs WHERE log_type = ? ORDER BY id DESC LIMIT ?", [log_type, limit]
        )
    else:
        rs = await execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", [limit])
    return _rows_as_dicts(rs)


# ---------------- تنطیمات ----------------

async def get_setting(key: str, default=None):
    client = get_client()
    rs = await execute("SELECT value FROM settings WHERE key = ?", [key])
    if not rs.rows:
        return default
    return rs.rows[0][0]


async def set_setting(key: str, value: str):
    client = get_client()
    await execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        [key, str(value)],
    )


# ---------------- ادمین‌ها ----------------

async def is_admin(user_id: int) -> bool:
    if user_id == config.ADMIN_ID:
        return True
    client = get_client()
    rs = await execute("SELECT user_id FROM admins WHERE user_id = ?", [user_id])
    return len(rs.rows) > 0


async def get_admin_role(user_id: int) -> str:
    if user_id == config.ADMIN_ID:
        return "full"
    client = get_client()
    rs = await execute("SELECT role FROM admins WHERE user_id = ?", [user_id])
    if not rs.rows:
        return None
    return rs.rows[0][0] or "full"


async def admin_has_permission(user_id: int, permission: str) -> bool:
    if user_id == config.ADMIN_ID:
        return True
    role = await get_admin_role(user_id)
    if not role:
        return False
    return permission in ROLE_PERMISSIONS.get(role, set())


async def add_admin(user_id: int, added_by: int, role: str = "full"):
    if role not in ADMIN_ROLES:
        role = "full"
    client = get_client()
    await execute(
        "INSERT INTO admins (user_id, added_by, added_at, role) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET role = excluded.role",
        [user_id, added_by, _now(), role],
    )


async def remove_admin(user_id: int) -> bool:
    if user_id == config.ADMIN_ID:
        return False  # ادمین اصلی هرگز حذف نمی‌شود
    client = get_client()
    await execute("DELETE FROM admins WHERE user_id = ?", [user_id])
    return True


async def list_admins() -> list:
    client = get_client()
    rs = await execute("SELECT user_id, added_at, role FROM admins")
    return _rows_as_dicts(rs)


# ---------------- کاربران ----------------

async def upsert_user(user_id: int, username: str):
    client = get_client()
    rs = await execute("SELECT user_id FROM users WHERE user_id = ?", [user_id])
    if rs.rows:
        await execute("UPDATE users SET username = ? WHERE user_id = ?", [username or "", user_id])
    else:
        await execute(
            "INSERT INTO users (user_id, username, joined_at, used_trial, is_blocked) VALUES (?, ?, ?, 0, 0)",
            [user_id, username or "", _now()],
        )


async def has_used_trial(user_id: int) -> bool:
    client = get_client()
    rs = await execute("SELECT used_trial FROM users WHERE user_id = ?", [user_id])
    if not rs.rows:
        return False
    return bool(rs.rows[0][0])


async def mark_trial_used(user_id: int):
    client = get_client()
    await execute("UPDATE users SET used_trial = 1 WHERE user_id = ?", [user_id])


async def has_accepted_rules(user_id: int) -> bool:
    client = get_client()
    rs = await execute("SELECT rules_accepted FROM users WHERE user_id = ?", [user_id])
    if not rs.rows:
        return False
    return bool(rs.rows[0][0])


async def mark_rules_accepted(user_id: int):
    client = get_client()
    await execute("UPDATE users SET rules_accepted = 1 WHERE user_id = ?", [user_id])


async def is_user_blocked(user_id: int) -> bool:
    client = get_client()
    rs = await execute("SELECT is_blocked FROM users WHERE user_id = ?", [user_id])
    if not rs.rows:
        return False
    return bool(rs.rows[0][0])


async def set_user_blocked(user_id: int, blocked: bool):
    client = get_client()
    await execute("UPDATE users SET is_blocked = ? WHERE user_id = ?", [1 if blocked else 0, user_id])


async def delete_user(user_id: int):
    client = get_client()
    await execute("DELETE FROM users WHERE user_id = ?", [user_id])


async def get_user(user_id: int):
    client = get_client()
    rs = await execute("SELECT * FROM users WHERE user_id = ?", [user_id])
    rows = _rows_as_dicts(rs)
    return rows[0] if rows else None


async def search_users(query: str) -> list:
    client = get_client()
    query = query.strip().lstrip("@")
    if query.isdigit():
        rs = await execute("SELECT * FROM users WHERE user_id = ?", [int(query)])
    else:
        rs = await execute("SELECT * FROM users WHERE username LIKE ?", [f"%{query}%"])
    return _rows_as_dicts(rs)


async def list_users(offset: int = 0, limit: int = 10) -> list:
    client = get_client()
    rs = await execute(
        "SELECT * FROM users ORDER BY joined_at DESC LIMIT ? OFFSET ?", [limit, offset]
    )
    return _rows_as_dicts(rs)


async def count_users() -> int:
    client = get_client()
    rs = await execute("SELECT COUNT(*) FROM users")
    return rs.rows[0][0]


async def count_active_users() -> int:
    """کاربرانی که حداقل یک خرید واقعی تاییدشده یا منقضی‌شده دارند (تست رایگان حساب نمی‌شود)"""
    client = get_client()
    rs = await execute(
        "SELECT COUNT(DISTINCT user_id) FROM orders WHERE status IN ('approved', 'expired') AND order_type = 'purchase'"
    )
    return rs.rows[0][0]


async def count_new_users_since(iso_date: str) -> int:
    client = get_client()
    rs = await execute(
        "SELECT COUNT(*) FROM users WHERE datetime(joined_at) >= datetime(?)", [iso_date]
    )
    return rs.rows[0][0]


async def list_all_user_ids() -> list:
    client = get_client()
    rs = await execute("SELECT user_id FROM users")
    return [row[0] for row in rs.rows]


async def list_user_ids_for_package(package_id: int) -> list:
    client = get_client()
    rs = await execute(
        "SELECT DISTINCT user_id FROM orders WHERE package_id = ? AND status = 'approved'",
        [package_id],
    )
    return [row[0] for row in rs.rows]


# ---------------- پلن‌ها ----------------

async def list_packages(active_only: bool = False) -> list:
    client = get_client()
    if active_only:
        rs = await execute("SELECT * FROM packages WHERE active = 1 ORDER BY price ASC")
    else:
        rs = await execute("SELECT * FROM packages ORDER BY id ASC")
    return _rows_as_dicts(rs)


async def get_package(package_id: int):
    client = get_client()
    rs = await execute("SELECT * FROM packages WHERE id = ?", [package_id])
    rows = _rows_as_dicts(rs)
    return rows[0] if rows else None


async def add_package(name: str, gb: float, days: int, price: int) -> int:
    client = get_client()
    rs = await execute(
        "INSERT INTO packages (name, gb, days, price, active) VALUES (?, ?, ?, ?, 1)",
        [name, gb, days, price],
    )
    return rs.last_insert_rowid


async def update_package_field(package_id: int, field: str, value):
    allowed_fields = {"name", "gb", "days", "price", "active"}
    if field not in allowed_fields:
        raise ValueError("فیلد نامعتبر")
    client = get_client()
    await execute(f"UPDATE packages SET {field} = ? WHERE id = ?", [value, package_id])


async def delete_package(package_id: int):
    client = get_client()
    await execute("DELETE FROM packages WHERE id = ?", [package_id])


# ---------------- سفارش‌ها ----------------

async def create_order(user_id: int, package_id, receipt_file_id, price: int = None, discount_code: str = None, order_type: str = "purchase") -> int:
    client = get_client()
    rs = await execute(
        "INSERT INTO orders (user_id, package_id, status, receipt_file_id, created_at, price, discount_code, order_type) "
        "VALUES (?, ?, 'pending', ?, ?, ?, ?, ?)",
        [user_id, package_id, receipt_file_id, _now(), price, discount_code, order_type],
    )
    return rs.last_insert_rowid


async def list_active_bot_orders() -> list:
    """همه سفارش‌های تایید‌شده (خریداری‌شده یا تست رایگان) که خودِ ربات آن‌ها را در پنل مرزبان
    ساخته و ثبت کرده (یعنی marzban_username دارند). این تابع صرفاً برای پایش خودکار انقضا استفاده
    می‌شود و هرگز شامل کاربرهایی که ادمین مستقیماً و دستی در پنل مرزبان ساخته نمی‌شود، چون آن‌ها
    اصلاً سفارشی در این جدول ندارند."""
    client = get_client()
    rs = await execute(
        "SELECT * FROM orders WHERE status = 'approved' AND marzban_username IS NOT NULL AND marzban_username != ''"
    )
    return _rows_as_dicts(rs)


async def mark_order_expired(order_id: int):
    client = get_client()
    await execute("UPDATE orders SET status = 'expired' WHERE id = ?", [order_id])


async def get_order(order_id: int):
    client = get_client()
    rs = await execute("SELECT * FROM orders WHERE id = ?", [order_id])
    rows = _rows_as_dicts(rs)
    return rows[0] if rows else None


async def update_order(order_id: int, **fields):
    if not fields:
        return
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [order_id]
    client = get_client()
    await execute(f"UPDATE orders SET {columns} WHERE id = ?", values)


async def list_orders_by_user(user_id: int) -> list:
    client = get_client()
    rs = await execute(
        "SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC", [user_id]
    )
    return _rows_as_dicts(rs)


async def list_orders(offset: int = 0, limit: int = 10, status: str = None) -> list:
    client = get_client()
    if status:
        rs = await execute(
            "SELECT * FROM orders WHERE status = ? ORDER BY id DESC LIMIT ? OFFSET ?",
            [status, limit, offset],
        )
    else:
        rs = await execute(
            "SELECT * FROM orders ORDER BY id DESC LIMIT ? OFFSET ?", [limit, offset]
        )
    return _rows_as_dicts(rs)


async def count_orders(status: str = None) -> int:
    client = get_client()
    if status:
        rs = await execute("SELECT COUNT(*) FROM orders WHERE status = ?", [status])
    else:
        rs = await execute("SELECT COUNT(*) FROM orders")
    return rs.rows[0][0]


async def order_stats() -> dict:
    client = get_client()
    rs = await execute("SELECT status, COUNT(*) FROM orders GROUP BY status")
    result = {"pending": 0, "approved": 0, "rejected": 0, "cancelled": 0, "expired": 0}
    for row in rs.rows:
        status, count = row[0], row[1]
        result[status] = count
    return result


async def revenue_total() -> int:
    client = get_client()
    rs = await execute(
        "SELECT COALESCE(SUM(price), 0) FROM orders WHERE status IN ('approved', 'expired')"
    )
    return rs.rows[0][0] or 0


async def revenue_since(iso_date: str) -> int:
    client = get_client()
    rs = await execute(
        "SELECT COALESCE(SUM(price), 0) FROM orders WHERE status IN ('approved', 'expired') AND datetime(created_at) >= datetime(?)",
        [iso_date],
    )
    return rs.rows[0][0] or 0


async def count_fulfilled_orders() -> int:
    """تعداد خریدهای واقعی که تا به حال با موفقیت فعال شده‌اند (چه هنوز فعال باشند چه منقضی شده باشند) - تست رایگان را شامل نمی‌شود"""
    client = get_client()
    rs = await execute("SELECT COUNT(*) FROM orders WHERE status IN ('approved', 'expired') AND order_type = 'purchase'")
    return rs.rows[0][0]


# ---------------- کدهای تخفیف ----------------

async def create_discount_code(code: str, kind: str, value: float, max_uses: int, expires_at: str):
    client = get_client()
    await execute(
        "INSERT INTO discount_codes (code, kind, value, max_uses, used_count, expires_at, active, created_at) "
        "VALUES (?, ?, ?, ?, 0, ?, 1, ?) "
        "ON CONFLICT(code) DO UPDATE SET kind=excluded.kind, value=excluded.value, "
        "max_uses=excluded.max_uses, expires_at=excluded.expires_at, active=1",
        [code, kind, value, max_uses, expires_at, _now()],
    )


async def get_discount_code(code: str):
    client = get_client()
    rs = await execute("SELECT * FROM discount_codes WHERE code = ?", [code])
    rows = _rows_as_dicts(rs)
    return rows[0] if rows else None


async def list_discount_codes() -> list:
    client = get_client()
    rs = await execute("SELECT * FROM discount_codes ORDER BY created_at DESC")
    return _rows_as_dicts(rs)


async def delete_discount_code(code: str):
    client = get_client()
    await execute("DELETE FROM discount_codes WHERE code = ?", [code])


async def increment_discount_usage(code: str):
    client = get_client()
    await execute(
        "UPDATE discount_codes SET used_count = used_count + 1 WHERE code = ?", [code]
    )


# ---------------- سرورها (دفترچه مدیریتی داخلی ربات) ----------------

async def add_server(name: str, address: str) -> int:
    client = get_client()
    rs = await execute(
        "INSERT INTO servers (name, address, enabled, added_at) VALUES (?, ?, 1, ?)",
        [name, address, _now()],
    )
    return rs.last_insert_rowid


async def list_servers() -> list:
    client = get_client()
    rs = await execute("SELECT * FROM servers ORDER BY id ASC")
    return _rows_as_dicts(rs)


async def get_server(server_id: int):
    client = get_client()
    rs = await execute("SELECT * FROM servers WHERE id = ?", [server_id])
    rows = _rows_as_dicts(rs)
    return rows[0] if rows else None


async def remove_server(server_id: int):
    client = get_client()
    await execute("DELETE FROM servers WHERE id = ?", [server_id])


async def toggle_server(server_id: int):
    server = await get_server(server_id)
    if not server:
        return
    client = get_client()
    await execute(
        "UPDATE servers SET enabled = ? WHERE id = ?",
        [0 if server["enabled"] else 1, server_id],
    )


# ---------------- تیکت‌ها ----------------

async def create_ticket(user_id: int, subject: str) -> int:
    client = get_client()
    rs = await execute(
        "INSERT INTO tickets (user_id, subject, status, created_at) VALUES (?, ?, 'open', ?)",
        [user_id, subject, _now()],
    )
    return rs.last_insert_rowid


async def add_ticket_message(ticket_id: int, sender_id: int, is_admin: bool, message: str):
    client = get_client()
    await execute(
        "INSERT INTO ticket_messages (ticket_id, sender_id, is_admin, message, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [ticket_id, sender_id, 1 if is_admin else 0, message, _now()],
    )


async def get_ticket(ticket_id: int):
    client = get_client()
    rs = await execute("SELECT * FROM tickets WHERE id = ?", [ticket_id])
    rows = _rows_as_dicts(rs)
    return rows[0] if rows else None


async def list_tickets_by_user(user_id: int) -> list:
    client = get_client()
    rs = await execute(
        "SELECT * FROM tickets WHERE user_id = ? ORDER BY id DESC", [user_id]
    )
    return _rows_as_dicts(rs)


async def list_open_tickets(offset: int = 0, limit: int = 10) -> list:
    client = get_client()
    rs = await execute(
        "SELECT * FROM tickets WHERE status = 'open' ORDER BY id DESC LIMIT ? OFFSET ?",
        [limit, offset],
    )
    return _rows_as_dicts(rs)


async def list_ticket_messages(ticket_id: int) -> list:
    client = get_client()
    rs = await execute(
        "SELECT * FROM ticket_messages WHERE ticket_id = ? ORDER BY id ASC", [ticket_id]
    )
    return _rows_as_dicts(rs)


async def set_ticket_status(ticket_id: int, status: str):
    client = get_client()
    await execute("UPDATE tickets SET status = ? WHERE id = ?", [status, ticket_id])


# ---------------- کمکی برای مدیریت اکانت مرزبان ----------------

async def get_order_by_marzban_username(username: str):
    client = get_client()
    rs = await execute(
        "SELECT * FROM orders WHERE marzban_username = ? ORDER BY id DESC LIMIT 1", [username]
    )
    rows = _rows_as_dicts(rs)
    return rows[0] if rows else None


# ---------------- کیف پول ----------------

async def get_wallet_balance(user_id: int) -> int:
    client = get_client()
    rs = await execute("SELECT wallet_balance FROM users WHERE user_id = ?", [user_id])
    if not rs.rows or rs.rows[0][0] is None:
        return 0
    return int(rs.rows[0][0])


async def adjust_wallet_balance(user_id: int, delta: int) -> int:
    """تنظیم موجودی کیف پول (مقدار منفی = کاهش). اگر کاربر در جدول نباشد اول ساخته می‌شود"""
    client = get_client()
    rs = await execute("SELECT user_id FROM users WHERE user_id = ?", [user_id])
    if not rs.rows:
        await execute(
            "INSERT INTO users (user_id, username, joined_at, used_trial, is_blocked, wallet_balance) VALUES (?, '', ?, 0, 0, 0)",
            [user_id, _now()],
        )
    await execute(
        "UPDATE users SET wallet_balance = COALESCE(wallet_balance, 0) + ? WHERE user_id = ?",
        [delta, user_id],
    )
    return await get_wallet_balance(user_id)


async def record_wallet_transaction(user_id: int, amount: int, kind: str, status: str = "done", note: str = None, receipt_file_id: str = None) -> int:
    client = get_client()
    rs = await execute(
        "INSERT INTO wallet_transactions (user_id, amount, kind, status, receipt_file_id, note, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [user_id, amount, kind, status, receipt_file_id, note, _now()],
    )
    return rs.last_insert_rowid


async def create_wallet_topup(user_id: int, amount: int, receipt_file_id: str) -> int:
    return await record_wallet_transaction(user_id, amount, "topup", status="pending", receipt_file_id=receipt_file_id)


async def get_wallet_transaction(tx_id: int):
    client = get_client()
    rs = await execute("SELECT * FROM wallet_transactions WHERE id = ?", [tx_id])
    rows = _rows_as_dicts(rs)
    return rows[0] if rows else None


async def update_wallet_transaction(tx_id: int, **fields):
    if not fields:
        return
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [tx_id]
    client = get_client()
    await execute(f"UPDATE wallet_transactions SET {columns} WHERE id = ?", values)


async def list_wallet_transactions(user_id: int, limit: int = 20) -> list:
    client = get_client()
    rs = await execute(
        "SELECT * FROM wallet_transactions WHERE user_id = ? ORDER BY id DESC LIMIT ?", [user_id, limit]
    )
    return _rows_as_dicts(rs)


async def list_pending_wallet_topups(limit: int = 20) -> list:
    client = get_client()
    rs = await execute(
        "SELECT * FROM wallet_transactions WHERE kind = 'topup' AND status = 'pending' ORDER BY id DESC LIMIT ?", [limit]
    )
    return _rows_as_dicts(rs)


async def try_spend_wallet_balance(user_id: int, amount: int) -> bool:
    """کاهش اتمی و امن موجودی کیف پول (جهت جلوگیری از خرج شدن دوباره در اثر کلیک همزمان/رقابتی).
    فقط وقتی موجودی کافی باشد کاهش انجام می‌شود و True برمی‌گردد؛ در غیر این صورت هیچ تغییری
    اعمال نمی‌شود و False برمی‌گردد."""
    if amount <= 0:
        return True
    client = get_client()
    rs = await execute(
        "UPDATE users SET wallet_balance = wallet_balance - ? "
        "WHERE user_id = ? AND COALESCE(wallet_balance, 0) >= ? "
        "RETURNING wallet_balance",
        [amount, user_id, amount],
    )
    return len(rs.rows) > 0
