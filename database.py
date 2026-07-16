# -*- coding: utf-8 -*-
# database.py
# تمام توابع مربوط به دیتابیس Turso (libSQL) - تنظیمات، ادمین‌ها، کاربران، پلن‌ها، سفارش‌ها

import datetime

import libsql_client

import config

_client = None


def get_client():
    """گرفتن یک نمونه از کلاینت دیتابیس (فقط یک بار ساخته می‌شود)"""
    global _client
    if _client is None:
        _client = libsql_client.create_client(
            url=config.TURSO_DATABASE_URL,
            auth_token=config.TURSO_AUTH_TOKEN,
        )
    return _client


def _now() -> str:
    return datetime.datetime.utcnow().isoformat()


def _rows_as_dicts(result_set) -> list:
    """تبدیل نتیجه کوئری به لیستی از دیکشنری‌ها"""
    return [dict(zip(result_set.columns, row)) for row in result_set.rows]


async def init_db():
    """ساخت جدول‌ها در صورت نبودن و مقداردهی اولیه تنظیمات پیش‌فرض"""
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
                added_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                joined_at TEXT,
                used_trial INTEGER DEFAULT 0
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
                created_at TEXT
            )""",
        ]
    )

    defaults = {
        "card_number": "0000-0000-0000-0000",
        "card_holder": "-",
        "force_join_enabled": "0",
        "force_join_channel": "",
        "trial_enabled": "1",
        "trial_gb": "1",
        "trial_days": "1",
        "welcome_text": (
            "✨ به ربات فروش سرویس VPN خوش آمدید! ✨\n"
            "━━━━━━━━━━━━━━━\n"
            "🚀 سریع  |  🔒 امن  |  ⚡️ بدون قطعی\n"
            "━━━━━━━━━━━━━━━\n"
            "از منوی زیر یکی از گزینه‌ها رو انتخاب کن 👇"
        ),
        "welcome_photo_id": "",
        "support_text": (
            "📞 پشتیبانی\n━━━━━━━━━━━━━━━\n"
            "برای ارتباط با پشتیبانی به آیدی زیر پیام دهید:\n@YourSupportUsername"
        ),
        "bot_enabled": "1",
    }
    for key, value in defaults.items():
        current = await get_setting(key)
        if current is None:
            await set_setting(key, value)


# ---------------- تنظیمات ----------------

async def get_setting(key: str, default=None):
    client = get_client()
    rs = await client.execute("SELECT value FROM settings WHERE key = ?", [key])
    if not rs.rows:
        return default
    return rs.rows[0][0]


async def set_setting(key: str, value: str):
    client = get_client()
    await client.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        [key, str(value)],
    )


# ---------------- ادمین‌ها ----------------

async def is_admin(user_id: int) -> bool:
    if user_id == config.ADMIN_ID:
        return True
    client = get_client()
    rs = await client.execute("SELECT user_id FROM admins WHERE user_id = ?", [user_id])
    return len(rs.rows) > 0


async def add_admin(user_id: int, added_by: int):
    client = get_client()
    await client.execute(
        "INSERT OR IGNORE INTO admins (user_id, added_by, added_at) VALUES (?, ?, ?)",
        [user_id, added_by, _now()],
    )


async def remove_admin(user_id: int) -> bool:
    if user_id == config.ADMIN_ID:
        return False  # ادمین اصلی هرگز حذف نمی‌شود
    client = get_client()
    await client.execute("DELETE FROM admins WHERE user_id = ?", [user_id])
    return True


async def list_admins() -> list:
    client = get_client()
    rs = await client.execute("SELECT user_id, added_at FROM admins")
    return _rows_as_dicts(rs)


# ---------------- کاربران ----------------

async def upsert_user(user_id: int, username: str):
    client = get_client()
    rs = await client.execute("SELECT user_id FROM users WHERE user_id = ?", [user_id])
    if rs.rows:
        await client.execute("UPDATE users SET username = ? WHERE user_id = ?", [username or "", user_id])
    else:
        await client.execute(
            "INSERT INTO users (user_id, username, joined_at, used_trial) VALUES (?, ?, ?, 0)",
            [user_id, username or "", _now()],
        )


async def has_used_trial(user_id: int) -> bool:
    client = get_client()
    rs = await client.execute("SELECT used_trial FROM users WHERE user_id = ?", [user_id])
    if not rs.rows:
        return False
    return bool(rs.rows[0][0])


async def mark_trial_used(user_id: int):
    client = get_client()
    await client.execute("UPDATE users SET used_trial = 1 WHERE user_id = ?", [user_id])


async def count_users() -> int:
    client = get_client()
    rs = await client.execute("SELECT COUNT(*) FROM users")
    return rs.rows[0][0]


# ---------------- پلن‌ها ----------------

async def list_packages(active_only: bool = False) -> list:
    client = get_client()
    if active_only:
        rs = await client.execute("SELECT * FROM packages WHERE active = 1 ORDER BY price ASC")
    else:
        rs = await client.execute("SELECT * FROM packages ORDER BY id ASC")
    return _rows_as_dicts(rs)


async def get_package(package_id: int):
    client = get_client()
    rs = await client.execute("SELECT * FROM packages WHERE id = ?", [package_id])
    rows = _rows_as_dicts(rs)
    return rows[0] if rows else None


async def add_package(name: str, gb: float, days: int, price: int) -> int:
    client = get_client()
    rs = await client.execute(
        "INSERT INTO packages (name, gb, days, price, active) VALUES (?, ?, ?, ?, 1)",
        [name, gb, days, price],
    )
    return rs.last_insert_rowid


async def update_package_field(package_id: int, field: str, value):
    allowed_fields = {"name", "gb", "days", "price", "active"}
    if field not in allowed_fields:
        raise ValueError("فیلد نامعتبر")
    client = get_client()
    await client.execute(f"UPDATE packages SET {field} = ? WHERE id = ?", [value, package_id])


async def delete_package(package_id: int):
    client = get_client()
    await client.execute("DELETE FROM packages WHERE id = ?", [package_id])


# ---------------- سفارش‌ها ----------------

async def create_order(user_id: int, package_id: int, receipt_file_id: str) -> int:
    client = get_client()
    rs = await client.execute(
        "INSERT INTO orders (user_id, package_id, status, receipt_file_id, created_at) "
        "VALUES (?, ?, 'pending', ?, ?)",
        [user_id, package_id, receipt_file_id, _now()],
    )
    return rs.last_insert_rowid


async def get_order(order_id: int):
    client = get_client()
    rs = await client.execute("SELECT * FROM orders WHERE id = ?", [order_id])
    rows = _rows_as_dicts(rs)
    return rows[0] if rows else None


async def update_order(order_id: int, **fields):
    if not fields:
        return
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [order_id]
    client = get_client()
    await client.execute(f"UPDATE orders SET {columns} WHERE id = ?", values)


async def list_orders_by_user(user_id: int) -> list:
    client = get_client()
    rs = await client.execute(
        "SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC", [user_id]
    )
    return _rows_as_dicts(rs)


async def order_stats() -> dict:
    client = get_client()
    rs = await client.execute("SELECT status, COUNT(*) FROM orders GROUP BY status")
    result = {"pending": 0, "approved": 0, "rejected": 0}
    for row in rs.rows:
        status, count = row[0], row[1]
        result[status] = count
    return result
