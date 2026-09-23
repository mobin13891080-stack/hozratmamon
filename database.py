# -*- coding: utf-8 -*-
# database.py
# تمام توابع مربوط به دیتاباز Turso (libSQL)
# شامل: تنظیمات، ادمین‌ها (با سطح دسترسی)، کاربران، پلن‌ها، سفارش‌ها،
# کد تخفیف، سرورها، لاگ‌ها و سیستم تیکت پشتیبانی

import asyncio
import datetime
import logging
import time
import hashlib
import hmac
import secrets
import threading

import libsql_client

import config

logger = logging.getLogger(__name__)

_client = None
_client_lock = asyncio.Lock()
_client_init_lock = threading.Lock()

# کش‌های سبک برای کاهش رفت‌وبرگشت‌های غیرضروری به Turso.
# این مقادیر باید قبل از اولین get_setting/is_user_blocked/_get_admin_ids_cached تعریف شوند.
_SETTINGS_CACHE_MAX = 256
_SETTING_TTL = 5.0
_BLOCKED_CACHE_MAX = 4096
_BLOCKED_TTL = 10.0
_RULES_TTL = 10.0
_ADMIN_TTL = 10.0

_settings_cache = {}
_blocked_cache = {}
_rules_cache = {}
_admin_ids_cache = None
_admin_ids_cache_time = 0.0

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
    """گرفتن یک کلاینت مشترک؛ ساخت آن نیز thread-safe است.

    قبلاً دو coroutine همزمان می‌توانستند هر دو _client را None ببینند، دو کلاینت بسازند و
    یکی را روی دیگری overwrite کنند. در کنار reset همزمان، این race می‌توانست زنجیره‌ای از
    خطاهای دیتابیس ایجاد کند.
    """
    global _client
    if _client is None:
        with _client_init_lock:
            if _client is None:
                _client = libsql_client.create_client(
                    url=config.TURSO_DATABASE_URL,
                    auth_token=config.TURSO_AUTH_TOKEN,
                )
    return _client


async def _reset_client(bad_client=None):
    """کلاینت Turso را فقط یک‌بار و به‌صورت سریال reset می‌کند.
    این قفل جلوی حالتی را می‌گیرد که چند درخواست هم‌زمان، کلاینت مشترک را پشت‌سرهم
    ببندند و باعث cascade failure و فریز ظاهری همه دکمه‌ها شوند.
    """
    global _client
    async with _client_lock:
        if bad_client is not None and _client is not bad_client:
            return
        old = _client
        _client = None
        if old is not None:
            try:
                await old.close()
            except Exception:
                pass


async def execute(sql: str, params=None, retries: int = 3):
    """اجرای مقاوم کوئری Turso با retry محدود و reset نسل‌دار کلاینت.

    هدف این لایه این است که یک خطای لحظه‌ای شبکه/HTTP باعث قفل شدن handlerها نشود. تعداد retry
    عمداً محدود است تا خرابی واقعی دیتابیس صف بی‌نهایت نسازد. ساخت کلاینت نیز thread-safe شده است.
    """
    last_exc = None
    for attempt in range(retries + 1):
        client = None
        try:
            client = get_client()
            if params is None:
                return await client.execute(sql)
            return await client.execute(sql, params)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            last_exc = e
            if client is not None:
                try:
                    await _reset_client(client)
                except Exception:
                    pass
            if attempt < retries:
                # backoff کوتاه و افزایشی؛ بدون sleep طولانی روی مسیر کاربر
                await asyncio.sleep(0.2 * (attempt + 1))
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
    «ستاک» اگر ستون از قبل افزوده شده باشد"""
    try:
        await client.execute(statement)
    except Exception:
        pass


async def init_db():
    """ساخت جدول‌ها در صورت نبودن، انجام مهاجرت‌های ساده و مقداردهی اولیه تنظیمات"""
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
            """CREATE TABLE IF NOT EXISTS revenue_resets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reset_at TEXT,
                actor_id INTEGER,
                note TEXT
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
            """CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL,
                referred_id INTEGER NOT NULL UNIQUE,
                referral_code TEXT NOT NULL,
                reward_amount INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                qualified INTEGER NOT NULL DEFAULT 0,
                qualified_order_id INTEGER,
                created_at TEXT NOT NULL,
                qualified_at TEXT
            )""",
        ]
    )

    # ایجاد ایندکس‌ها برای سریع‌تر کردن کوئری‌های پرتکرار (بدون این‌ها هر کوئری full table scan بود)
    await _try_execute(client, "CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id)")
    await _try_execute(client, "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")
    await _try_execute(client, "CREATE INDEX IF NOT EXISTS idx_orders_marzban_username ON orders(marzban_username)")
    await _try_execute(client, "CREATE INDEX IF NOT EXISTS idx_orders_status_type ON orders(status, order_type)")
    await _try_execute(client, "CREATE INDEX IF NOT EXISTS idx_logs_type ON logs(log_type)")
    await _try_execute(client, "CREATE INDEX IF NOT EXISTS idx_logs_created ON logs(created_at DESC)")
    await _try_execute(client, "CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets(user_id)")
    await _try_execute(client, "CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status)")
    await _try_execute(client, "CREATE INDEX IF NOT EXISTS idx_ticket_msgs_ticket ON ticket_messages(ticket_id)")
    await _try_execute(client, "CREATE INDEX IF NOT EXISTS idx_wallet_tx_user ON wallet_transactions(user_id)")
    await _try_execute(client, "CREATE INDEX IF NOT EXISTS idx_wallet_tx_kind_status ON wallet_transactions(kind, status)")
    await _try_execute(client, "CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id)")
    await _try_execute(client, "CREATE INDEX IF NOT EXISTS idx_referrals_referred ON referrals(referred_id)")
    await _try_execute(client, "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
    await _try_execute(client, "CREATE INDEX IF NOT EXISTS idx_referral_penalty_events_status ON referral_penalty_events(status)")
    await _try_execute(client, "CREATE INDEX IF NOT EXISTS idx_packages_active_price ON packages(active, price)")

    # مهاجرت ستون‌های جدید برای دیتاباز‌های قدیمی‌تر (خطای «ستاک» اگر قبلاً افزوده شده نادیده گرفته می‌شود)
    await _try_execute(client, "ALTER TABLE users ADD COLUMN is_blocked INTEGER DEFAULT 0")
    await _try_execute(client, "ALTER TABLE admins ADD COLUMN role TEXT DEFAULT 'full'")
    await _try_execute(client, "ALTER TABLE orders ADD COLUMN price INTEGER")
    await _try_execute(client, "ALTER TABLE orders ADD COLUMN discount_code TEXT")
    await _try_execute(client, "ALTER TABLE users ADD COLUMN wallet_balance INTEGER DEFAULT 0")
    await _try_execute(client, "ALTER TABLE orders ADD COLUMN order_type TEXT DEFAULT 'purchase'")
    await _try_execute(client, "ALTER TABLE orders ADD COLUMN custom_gb REAL")
    await _try_execute(client, "ALTER TABLE orders ADD COLUMN custom_days INTEGER")
    await _try_execute(client, "ALTER TABLE wallet_transactions ADD COLUMN related_order_id INTEGER")
    await _try_execute(client, "ALTER TABLE packages ADD COLUMN category_id INTEGER")
    await _try_execute(client, "ALTER TABLE orders ADD COLUMN desired_username TEXT")
    await _try_execute(client, "ALTER TABLE orders ADD COLUMN reject_reason TEXT")
    await _try_execute(client, "ALTER TABLE users ADD COLUMN trial_generation INTEGER DEFAULT 0")
    await _try_execute(client, "ALTER TABLE users ADD COLUMN referral_code TEXT")
    await _try_execute(client, "ALTER TABLE users ADD COLUMN pending_referrer_id INTEGER")
    await _try_execute(client, "ALTER TABLE users ADD COLUMN referral_start_locked INTEGER DEFAULT 0")
    await _try_execute(client, "CREATE TABLE IF NOT EXISTS referral_penalty_states (referral_id INTEGER NOT NULL, channel TEXT NOT NULL, is_left INTEGER NOT NULL DEFAULT 0, last_checked_at TEXT, left_at TEXT, PRIMARY KEY (referral_id, channel))")
    await _try_execute(client, "CREATE TABLE IF NOT EXISTS referral_penalty_events (id INTEGER PRIMARY KEY AUTOINCREMENT, referral_id INTEGER NOT NULL, channel TEXT NOT NULL, left_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', applied_at TEXT, claimed_at TEXT, UNIQUE(referral_id, channel, left_at))")
    await _try_execute(client, "ALTER TABLE referral_penalty_events ADD COLUMN claimed_at TEXT")
    # موجودی داخلی پاداش رفرال؛ فقط برای منطق داخلی است و هرگز جداگانه به کاربر نمایش داده نمی‌شود.
    await _try_execute(client, "ALTER TABLE users ADD COLUMN referral_available_balance INTEGER DEFAULT 0")
    # موجودی رفرال قفل‌شده داخل موجودی کل کاربر لحاظ می‌شود، اما تا آزادسازی قابل خرج نیست.
    await _try_execute(client, "ALTER TABLE users ADD COLUMN referral_locked_balance INTEGER DEFAULT 0")
    await _try_execute(client, "ALTER TABLE users ADD COLUMN referral_penalty_debt INTEGER DEFAULT 0")
    await _try_execute(client, "ALTER TABLE referrals ADD COLUMN released_amount INTEGER DEFAULT 0")
    await _try_execute(client, "ALTER TABLE referrals ADD COLUMN is_test INTEGER DEFAULT 0")
    await _try_execute(client, "ALTER TABLE referrals ADD COLUMN test_user_id INTEGER")
    await _try_execute(client, "ALTER TABLE referrals ADD COLUMN test_owner_id INTEGER")
    await _try_execute(client, "ALTER TABLE referrals ADD COLUMN left_at TEXT")
    await _try_execute(client, "ALTER TABLE referrals ADD COLUMN left_channel TEXT")
    await _try_execute(client, "ALTER TABLE referrals ADD COLUMN penalty_status TEXT DEFAULT 'none'")
    await _try_execute(client, "ALTER TABLE referrals ADD COLUMN penalty_amount INTEGER DEFAULT 0")
    await _try_execute(client, "ALTER TABLE referrals ADD COLUMN penalty_gb REAL DEFAULT 0")
    await _try_execute(client, "ALTER TABLE referrals ADD COLUMN penalty_days INTEGER DEFAULT 0")
    await _try_execute(client, "ALTER TABLE referrals ADD COLUMN penalty_order_id INTEGER")
    await _try_execute(client, "ALTER TABLE referrals ADD COLUMN penalty_applied_at TEXT")
    await _try_execute(client, "ALTER TABLE referrals ADD COLUMN penalty_checked_at TEXT")
    await _try_execute(client, "ALTER TABLE orders ADD COLUMN requested_price INTEGER")
    await _try_execute(client, "ALTER TABLE orders ADD COLUMN referral_funds_used INTEGER DEFAULT 0")
    await _try_execute(client, "ALTER TABLE orders ADD COLUMN payment_source TEXT DEFAULT 'unknown'")
    await _try_execute(client, "ALTER TABLE orders ADD COLUMN referral_base_data_limit INTEGER DEFAULT 0")
    await _try_execute(client, "ALTER TABLE orders ADD COLUMN referral_base_expire INTEGER DEFAULT 0")
    await _try_execute(client, "ALTER TABLE orders ADD COLUMN referral_funded_data_limit INTEGER DEFAULT 0")
    await _try_execute(client, "ALTER TABLE orders ADD COLUMN referral_funded_days REAL DEFAULT 0")

    # ایندکس‌های وابسته به ستون‌های مهاجرتی را بعد از ALTER TABLE نیز بساز؛
    # در نسخه‌های قبلی تلاش برای ساخت این دو ایندکس قبل از ایجاد ستون‌ها silently fail می‌کرد.
    await _try_execute(client, "CREATE INDEX IF NOT EXISTS idx_orders_status_type ON orders(status, order_type)")
    await _try_execute(client, "CREATE INDEX IF NOT EXISTS idx_referral_penalty_events_status ON referral_penalty_events(status)")
    await _try_execute(client, "CREATE INDEX IF NOT EXISTS idx_orders_user_status ON orders(user_id, status)")
    await _try_execute(client, "CREATE INDEX IF NOT EXISTS idx_wallet_tx_user_status ON wallet_transactions(user_id, status)")
    await _try_execute(client, "CREATE INDEX IF NOT EXISTS idx_referral_penalty_states_left ON referral_penalty_states(is_left, last_checked_at)")

    await _try_execute(
        client,
        "CREATE TABLE IF NOT EXISTS package_categories ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name TEXT NOT NULL, "
        "sort_order INTEGER DEFAULT 0, "
        "created_at TEXT)",
    )

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
            "✨ به ربات فروش سرویس VPN خوش امدید! ✨\n"
            "━━━━━━━\n"
            "🚀 سریع  |  🔒 امن  |  ⚡️ بدون قطعی\n"
            "━━━━━━━\n"
            "از منوی زیر یکی از گزینه ها رو انتخاب کن 👇"
        ),
        "welcome_photo_id": "",
        "support_text": (
            "🆘 پشتیبانی\n\nبرای هرگونه سوال به پشتیبانی پیام دهید:"
        ),
        "support_username": "@YourSupportUsername",
        "bot_enabled": "1",
        "national_outage_mode": "0",
        "rules_text": (
            "1- به اطلاعیه هایی که داخل کانال گذاشته می شود حتما توجه کنید.\n"
            "2- در صورتی که اطلاعیه ای در مورد قطعی در کانال گذاشته نشده به اکانت پشتیبانی پیام دهید\n"
            "3- سرویس ها را از طریق پیامک ارسال نکنید، برای ارسال پیامک می توانید از طریق ایمیل ارسال کنید."
        ),
        "contact_links": "",
        "custom_builder_enabled": "0",
        "custom_builder_min_gb": "5",
        "custom_builder_max_gb": "200",
        "custom_builder_price_per_gb": "0",
        "custom_builder_price_per_day": "0",
        "custom_builder_charge_days": "1",
        "trial_unit": "days",
        "trial_reset_generation": "0",
        "payment_amount_mode": "fixed",
        "payment_random_min": "100",
        "payment_random_max": "1500",
        "post_approval_guide_enabled": "0",
        "post_approval_guide_text": "📚 آموزش اتصال پس از خرید\n\nلینک اشتراک را در اپلیکیشن خود وارد کنید. در صورت نیاز با پشتیبانی در ارتباط باشید.",
        "post_approval_guide_url": "",
        "pasarguard_selected_group_ids": "all",
        "referral_enabled": "0",
        "referral_condition_enabled": "1",
        "referral_reward": "5000",
        "referral_min_gb": "10",
        "referral_spend_enabled": "0",
        "referral_spend_min_gb": "10",
        "referral_penalty_enabled": "0",
        "referral_penalty_extra_money": "0",
        "referral_penalty_gb": "0",
        "referral_penalty_days": "0",
        "referral_penalty_text": (
            "⚠️ <b>جریمه خروج کاربر دعوت‌شده</b>\n━━━━━━━━━━━━━━\n\n"
            "👤 <b>کاربر دعوت‌شده:</b>\n<b>{name}</b>\n"
            "🆔 <b>آیدی عددی:</b> <code>{user_id}</code>\n"
            "🔗 <b>یوزرنیم:</b> {username}\n\n"
            "📢 <b>کانال ترک‌شده:</b> <code>{channel}</code>\n\n"
            "{penalty_details}\n\n"
            "🔒 موجودی و سهم خریداری‌شده با پول واقعی این کاربر در این جریمه دستکاری نشده است."
        ),
        "referral_success_text": (
            "🎉 <b>دعوت موفق!</b>\n━━━━━━━━━━━━━━\n"
            "👤 نام: {name}\n"
            "🆔 آیدی: <code>{user_id}</code>\n\n"
            "💰 پاداش: <b>{amount:,} تومان</b>\n"
            "{status_text}"
        ),
        "referral_page_text": (
            "👥 <b>دعوت دوستان و کسب درآمد</b> 💸\n━━━━━━━━━━━━━━\n"
            "دوستانت رو دعوت کن و به‌ازای هر دعوت موفق، <b>{reward:,} تومان</b> پاداش بگیر! 🎁\n\n"
            "🔗 <b>لینک اختصاصی شما:</b>\n<code>{link}</code>\n\n"
            "🔑 <b>کد اختصاصی:</b> <code>{code}</code>\n\n"
            "👤 تعداد دعوت: <b>{total}</b>\n"
            "✅ دعوت‌های موفق: <b>{successful}</b>\n"
            "🔓 مبلغ آزاد شده: <b>{unlocked:,} تومان</b>\n"
            "🔒 مبلغ در انتظار: <b>{locked:,} تومان</b>\n\n"
            "{condition_text}"
        ),
    }
    for key, value in defaults.items():
        current = await get_setting(key)
        if current is None:
            await set_setting(key, value)
    # V10 migration: invalidate and remove every previously stored referral code.
    # Historical referral relationships/rewards are preserved; only old link payloads are erased.
    if await get_setting("referral_v10_numeric_reset_done", "0") != "1":
        await _try_execute(client, "UPDATE users SET referral_code = NULL")
        await _try_execute(client, "UPDATE referrals SET referral_code = ''")
        await set_setting("referral_v10_numeric_reset_done", "1")
    # V11 anti-abuse migration: every user that already existed before V11 is considered to have
    # already performed their first bot start. New users keep the default 0 and get exactly one
    # chance to attribute a referral on their first /start.
    if await get_setting("referral_v11_start_lock_migrated", "0") != "1":
        await _try_execute(client, "UPDATE users SET referral_start_locked = 1 WHERE COALESCE(referral_start_locked, 0) = 0")
        # Any pending attribution created by an older version is also invalidated.
        # Historical completed referrals/rewards remain untouched.
        await _try_execute(client, "UPDATE users SET pending_referrer_id = NULL")
        await set_setting("referral_v11_start_lock_migrated", "1")
    # سازگاری داده‌های نسخه قبل: رفرال‌های قبلاً آزادشده، کل پاداش آزادشده دارند.
    await _try_execute(client, "UPDATE referrals SET released_amount = reward_amount WHERE status = 'unlocked' AND COALESCE(released_amount, 0) = 0")


# ==================== لاگ فعالیت ====================

async def log_action(log_type: str, actor_id, message: str):
    try:
        await execute(
            "INSERT INTO logs (log_type, actor_id, message, created_at) VALUES (?, ?, ?, ?)",
            [log_type, actor_id, message, _now()],
        )
    except Exception:
        pass


async def list_logs(log_type: str = None, limit: int = 30) -> list:
    if log_type:
        rs = await execute("SELECT * FROM logs WHERE log_type = ? ORDER BY id DESC LIMIT ?", [log_type, limit])
    else:
        rs = await execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", [limit])
    return _rows_as_dicts(rs)


async def get_setting(key: str, default=None):
    now = time.time()
    cached = _settings_cache.get(key)
    if cached is not None and (now - cached[1]) < _SETTING_TTL:
        return cached[0] if cached[0] is not None else default
    rs = await execute("SELECT value FROM settings WHERE key = ?", [key])
    value = rs.rows[0][0] if rs.rows else None
    _settings_cache[key] = (value, now)
    # هماهنگ با الگوی پاک‌سازی کش کاربران مسدود (کاملا بی‌خطر، فقط برای یکدست بودن کد):
    # تعداد کلیدهای تنظیمات ذاتاً ثابت و کم است، اما اگر روزی بیشتر شد، این پاک‌سازی مانع رشد بی‌نهایت می‌شود.
    if len(_settings_cache) > _SETTINGS_CACHE_MAX:
        expired = [k for k, (_, ts) in _settings_cache.items() if (now - ts) >= _SETTING_TTL]
        for k in expired:
            _settings_cache.pop(k, None)
    return value if value is not None else default


async def set_setting(key: str, value: str):
    await execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        [key, str(value)],
    )
    _settings_cache[key] = (str(value), time.time())


async def get_settings_many(keys, defaults=None) -> dict:
    """Read several settings in one DB query, while reusing the short TTL cache."""
    keys = [str(k) for k in keys if k]
    defaults = defaults or {}
    now = time.time()
    result = {}
    missing = []
    for key in keys:
        cached = _settings_cache.get(key)
        if cached is not None and (now - cached[1]) < _SETTING_TTL:
            result[key] = cached[0]
        else:
            missing.append(key)
    if missing:
        placeholders = ",".join("?" for _ in missing)
        rs = await execute(
            f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
            missing,
        )
        found = {row[0]: row[1] for row in rs.rows}
        for key in missing:
            value = found.get(key)
            _settings_cache[key] = (value, now)
            result[key] = value
    for key in keys:
        if result.get(key) is None:
            result[key] = defaults.get(key)
    return result


# ---------------- ادمین‌ها ----------------

async def _get_admin_ids_cached() -> set:
    global _admin_ids_cache, _admin_ids_cache_time
    now = time.time()
    if _admin_ids_cache is not None and (now - _admin_ids_cache_time) < _ADMIN_TTL:
        return _admin_ids_cache
    rs = await execute("SELECT user_id FROM admins")
    _admin_ids_cache = {row[0] for row in rs.rows}
    _admin_ids_cache_time = now
    return _admin_ids_cache


def _invalidate_admin_cache():
    global _admin_ids_cache, _admin_ids_cache_time
    _admin_ids_cache = None
    _admin_ids_cache_time = 0.0


async def is_admin(user_id: int) -> bool:
    if user_id == config.ADMIN_ID:
        return True
    admin_ids = await _get_admin_ids_cached()
    return user_id in admin_ids


async def get_admin_role(user_id: int) -> str:
    if user_id == config.ADMIN_ID:
        return "full"
    now = time.time()
    cached = _admin_role_cache.get(user_id)
    if cached is not None and (now - cached[1]) < _ADMIN_ROLE_TTL:
        return cached[0]
    rs = await execute("SELECT role FROM admins WHERE user_id = ?", [user_id])
    role = (rs.rows[0][0] or "full") if rs.rows else None
    _admin_role_cache[user_id] = (role, now)
    return role


def _invalidate_admin_role_cache(user_id: int = None):
    if user_id is None:
        _admin_role_cache.clear()
    else:
        _admin_role_cache.pop(user_id, None)


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
    await execute(
        "INSERT INTO admins (user_id, added_by, added_at, role) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET role = excluded.role",
        [user_id, added_by, _now(), role],
    )
    _invalidate_admin_cache()
    _invalidate_admin_role_cache(user_id)


async def remove_admin(user_id: int) -> bool:
    if user_id == config.ADMIN_ID:
        return False  # ادمین اصلی هرگز حذف نمی‌شود
    await execute("DELETE FROM admins WHERE user_id = ?", [user_id])
    _invalidate_admin_cache()
    _invalidate_admin_role_cache(user_id)
    return True


async def list_admins() -> list:
    rs = await execute("SELECT user_id, added_at, role FROM admins")
    return _rows_as_dicts(rs)


# ---------------- کاربران ----------------

async def upsert_user(user_id: int, username: str):
    """Create/update a user with one atomic DB round-trip."""
    await execute(
        "INSERT INTO users (user_id, username, joined_at, used_trial, is_blocked) "
        "VALUES (?, ?, ?, 0, 0) "
        "ON CONFLICT(user_id) DO UPDATE SET username = excluded.username",
        [user_id, username or "", _now()],
    )
    _rules_cache.pop(user_id, None)


async def current_trial_generation() -> int:
    try:
        return int(await get_setting("trial_reset_generation", "0") or 0)
    except Exception:
        return 0


async def has_used_trial(user_id: int) -> bool:
    rs = await execute("SELECT used_trial, COALESCE(trial_generation, 0) FROM users WHERE user_id = ?", [user_id])
    if not rs.rows:
        return False
    used_trial, generation = rs.rows[0][0], rs.rows[0][1]
    return bool(used_trial) and int(generation or 0) >= await current_trial_generation()


async def mark_trial_used(user_id: int):
    await execute("UPDATE users SET used_trial = 1, trial_generation = ? WHERE user_id = ?", [await current_trial_generation(), user_id])


async def claim_trial_slot(user_id: int) -> bool:
    """اتمیک: فقط اگر هنوز از تست استفاده نشده، آن را رزرو می‌کند.
    جلوگیری از race condition: اگر کاربر دوبار سریع دکمه تست بزند،
    فقط یکی موفق می‌شود و دو سرویس ساخته نمی‌شود."""
    gen = await current_trial_generation()
    try:
        rs = await execute(
            "UPDATE users SET used_trial = 1, trial_generation = ? "
            "WHERE user_id = ? AND (used_trial = 0 OR COALESCE(trial_generation, -1) < ?) RETURNING user_id",
            [gen, user_id, gen],
        )
        return bool(rs.rows)
    except Exception:
        # fallback برای دیتابیس‌هایی که RETURNING پشتیبانی نمی‌کنند
        before = await execute("SELECT used_trial, COALESCE(trial_generation,-1) FROM users WHERE user_id=?", [user_id])
        if not before.rows:
            return False
        used, prev_gen = before.rows[0][0], before.rows[0][1]
        if bool(used) and int(prev_gen or -1) >= gen:
            return False  # قبلاً استفاده شده
        await execute(
            "UPDATE users SET used_trial = 1, trial_generation = ? "
            "WHERE user_id = ? AND (used_trial = 0 OR COALESCE(trial_generation, -1) < ?)",
            [gen, user_id, gen],
        )
        after = await execute("SELECT used_trial, COALESCE(trial_generation,-1) FROM users WHERE user_id=?", [user_id])
        if not after.rows:
            return False
        return bool(after.rows[0][0]) and int(after.rows[0][1] or -1) >= gen


async def reset_trial_usage(actor_id: int = None) -> int:
    current = await current_trial_generation()
    new_generation = current + 1
    await set_setting("trial_reset_generation", str(new_generation))
    rs = await execute("SELECT COUNT(*) FROM users WHERE used_trial = 1")
    count = rs.rows[0][0] if rs.rows else 0
    await log_action("admin", actor_id, f"ریست تست رایگان برای نسل #{new_generation}؛ {count} کاربر دوباره مجاز شدند")
    return count


async def has_accepted_rules(user_id: int) -> bool:
    now = time.time()
    cached = _rules_cache.get(user_id)
    if cached is not None and (now - cached[1]) < _RULES_TTL:
        return cached[0]
    rs = await execute("SELECT rules_accepted FROM users WHERE user_id = ?", [user_id])
    accepted = bool(rs.rows[0][0]) if rs.rows else False
    _rules_cache[user_id] = (accepted, now)
    if len(_rules_cache) > _BLOCKED_CACHE_MAX:
        expired = [uid for uid, (_, ts) in _rules_cache.items() if (now - ts) >= _RULES_TTL]
        for uid in expired:
            _rules_cache.pop(uid, None)
    return accepted


async def mark_rules_accepted(user_id: int):
    await execute("UPDATE users SET rules_accepted = 1 WHERE user_id = ?", [user_id])
    _rules_cache[user_id] = (True, time.time())


async def is_user_blocked(user_id: int) -> bool:
    now = time.time()
    cached = _blocked_cache.get(user_id)
    if cached is not None and (now - cached[1]) < _BLOCKED_TTL:
        return cached[0]
    rs = await execute("SELECT is_blocked FROM users WHERE user_id = ?", [user_id])
    blocked = bool(rs.rows[0][0]) if rs.rows else False
    _blocked_cache[user_id] = (blocked, now)
    # رفع نشتی حافظه: بدون این پاک‌سازی، با رشد تعداد کاربران، این دیکشنری برای همیشه در حافظه می‌موند
    # و حافظه بی‌نهایت رشد می‌کند (memory leak). فقط وقتی از حد آستانه رد کرد،
    # ورودی‌های منقضی را پاک می‌کنیم تا حافظه محدود بماند (رفتار عادی روی کارکرد/دقت بدون تاثیر).
    if len(_blocked_cache) > _BLOCKED_CACHE_MAX:
        expired = [uid for uid, (_, ts) in _blocked_cache.items() if (now - ts) >= _BLOCKED_TTL]
        for uid in expired:
            _blocked_cache.pop(uid, None)
    return blocked


async def set_user_blocked(user_id: int, blocked: bool):
    await execute("UPDATE users SET is_blocked = ? WHERE user_id = ?", [1 if blocked else 0, user_id])
    _blocked_cache[user_id] = (bool(blocked), time.time())


async def delete_user(user_id: int):
    await execute("DELETE FROM users WHERE user_id = ?", [user_id])


async def get_user(user_id: int):
    rs = await execute("SELECT * FROM users WHERE user_id = ?", [user_id])
    rows = _rows_as_dicts(rs)
    return rows[0] if rows else None


async def search_users(query: str) -> list:
    query = query.strip().lstrip("@")
    if query.isdigit():
        rs = await execute("SELECT * FROM users WHERE user_id = ?", [int(query)])
    else:
        rs = await execute("SELECT * FROM users WHERE username LIKE ?", [f"%{query}%"])
    return _rows_as_dicts(rs)


async def list_users(offset: int = 0, limit: int = 10) -> list:
    rs = await execute(
        "SELECT * FROM users ORDER BY joined_at DESC LIMIT ? OFFSET ?", [limit, offset]
    )
    return _rows_as_dicts(rs)


async def count_users() -> int:
    rs = await execute("SELECT COUNT(*) FROM users")
    return rs.rows[0][0]


async def count_active_users() -> int:
    """کاربرانی که حداقل یک خرید واقعی تایید‌شده یا منقضی‌شده دارند (تست رایگان حساب نمی‌شود)"""
    rs = await execute(
        "SELECT COUNT(DISTINCT user_id) FROM orders WHERE status IN ('approved', 'expired') AND order_type = 'purchase'"
    )
    return rs.rows[0][0]


async def count_new_users_since(iso_date: str) -> int:
    rs = await execute(
        "SELECT COUNT(*) FROM users WHERE datetime(joined_at) >= datetime(?)", [iso_date]
    )
    return rs.rows[0][0]


async def list_all_user_ids() -> list:
    rs = await execute("SELECT user_id FROM users")
    return [row[0] for row in rs.rows]


async def list_user_ids_for_package(package_id: int) -> list:
    rs = await execute(
        "SELECT DISTINCT user_id FROM orders WHERE package_id = ? AND status = 'approved'",
        [package_id],
    )
    return [row[0] for row in rs.rows]


# ---------------- پلن‌ها ----------------

async def list_packages(active_only: bool = False) -> list:
    if active_only:
        rs = await execute("SELECT * FROM packages WHERE active = 1 ORDER BY price ASC")
    else:
        rs = await execute("SELECT * FROM packages ORDER BY id ASC")
    return _rows_as_dicts(rs)


_PACKAGE_TTL = 4.0
_package_cache = {}

# کش نقش ادمین‌ها — هر callback بدون این کش یک DB query جداگانه می‌زد
_ADMIN_ROLE_TTL = 15.0
_admin_role_cache: dict = {}


async def get_package(package_id: int):
    now = time.time()
    cached = _package_cache.get(package_id)
    if cached is not None and (now - cached[1]) < _PACKAGE_TTL:
        return cached[0]
    rs = await execute("SELECT * FROM packages WHERE id = ?", [package_id])
    rows = _rows_as_dicts(rs)
    result = rows[0] if rows else None
    _package_cache[package_id] = (result, now)
    return result


def _invalidate_package_cache(package_id: int = None):
    if package_id is None:
        _package_cache.clear()
    else:
        _package_cache.pop(package_id, None)


async def add_package(name: str, gb: float, days: int, price: int, category_id: int = None) -> int:
    rs = await execute(
        "INSERT INTO packages (name, gb, days, price, active, category_id) VALUES (?, ?, ?, ?, 1, ?)",
        [name, gb, days, price, category_id],
    )
    return rs.last_insert_rowid


async def update_package_field(package_id: int, field: str, value):
    allowed_fields = {"name", "gb", "days", "price", "active", "category_id"}
    if field not in allowed_fields:
        raise ValueError("فیلد نامعتبر")
    await execute(f"UPDATE packages SET {field} = ? WHERE id = ?", [value, package_id])
    _invalidate_package_cache(package_id)


async def delete_package(package_id: int):
    await execute("DELETE FROM packages WHERE id = ?", [package_id])
    _invalidate_package_cache(package_id)


# ---------------- دسته‌بندی پلن‌ها ----------------

async def list_package_categories() -> list:
    rs = await execute("SELECT * FROM package_categories ORDER BY sort_order ASC, id ASC")
    return _rows_as_dicts(rs)


async def get_package_category(category_id: int):
    rs = await execute("SELECT * FROM package_categories WHERE id = ?", [category_id])
    rows = _rows_as_dicts(rs)
    return rows[0] if rows else None


async def add_package_category(name: str) -> int:
    rs = await execute("SELECT COALESCE(MAX(sort_order), -1) FROM package_categories")
    next_order = rs.rows[0][0] + 1
    rs = await execute(
        "INSERT INTO package_categories (name, sort_order, created_at) VALUES (?, ?, ?)",
        [name, next_order, _now()],
    )
    return rs.last_insert_rowid


async def rename_package_category(category_id: int, name: str):
    await execute("UPDATE package_categories SET name = ? WHERE id = ?", [name, category_id])


async def delete_package_category(category_id: int):
    await execute("UPDATE packages SET category_id = NULL WHERE category_id = ?", [category_id])
    await execute("DELETE FROM package_categories WHERE id = ?", [category_id])


async def move_package_category(category_id: int, direction: str):
    """جابجایی ترتیب نمایش یک دسته با دسته‌ی مجاور (direction: 'up' یا 'down')"""
    categories = await list_package_categories()
    ids = [c["id"] for c in categories]
    if category_id not in ids:
        return
    idx = ids.index(category_id)
    swap_idx = idx - 1 if direction == "up" else idx + 1
    if swap_idx < 0 or swap_idx >= len(categories):
        return
    a, b = categories[idx], categories[swap_idx]
    await execute("UPDATE package_categories SET sort_order = ? WHERE id = ?", [b["sort_order"], a["id"]])
    await execute("UPDATE package_categories SET sort_order = ? WHERE id = ?", [a["sort_order"], b["id"]])


async def list_packages_by_category(category_id, active_only: bool = True) -> list:
    """category_id=None یعنی پلن‌های بدون دسته (سایر)"""
    if category_id is None:
        query = "SELECT * FROM packages WHERE category_id IS NULL"
        params = None
    else:
        query = "SELECT * FROM packages WHERE category_id = ?"
        params = [category_id]
    if active_only:
        query += " AND active = 1"
    query += " ORDER BY price ASC"
    rs = await execute(query, params)
    return _rows_as_dicts(rs)


async def count_packages_by_category(category_id, active_only: bool = True) -> int:
    if category_id is None:
        query = "SELECT COUNT(*) FROM packages WHERE category_id IS NULL"
        params = None
    else:
        query = "SELECT COUNT(*) FROM packages WHERE category_id = ?"
        params = [category_id]
    if active_only:
        query += " AND active = 1"
    rs = await execute(query, params)
    return rs.rows[0][0]


# ---------------- سفارش‌ها ----------------

async def create_order(user_id: int, package_id, receipt_file_id, price: int = None, discount_code: str = None, order_type: str = "purchase", custom_gb: float = None, custom_days: int = None, desired_username: str = None, requested_price: int = None) -> int:
    if requested_price is None:
        requested_price = price
    rs = await execute(
        "INSERT INTO orders (user_id, package_id, status, receipt_file_id, created_at, price, discount_code, order_type, custom_gb, custom_days, desired_username, requested_price) "
        "VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [user_id, package_id, receipt_file_id, _now(), price, discount_code, order_type, custom_gb, custom_days, desired_username, requested_price],
    )
    return rs.last_insert_rowid


async def list_active_bot_orders() -> list:
    """همه سفارش‌های تایید‌شده (خریداری‌شده یا تست رایگان) که خود ربات آن‌ها را در پنل مرزبان
    ساخته و ثبت کرده (یعنی marzban_username دارند). این تابع صرفاً برای پایش خودکار
    انقضا استفاده می‌شود و هرگز شامل کاربرهایی که ادمین مستقیماً و دستی در پنل مرزبان ساخته نمی‌شود، چون آن‌ها
    اصلاً سفارشی در این جدول ندارند."""
    rs = await execute(
        "SELECT * FROM orders WHERE status = 'approved' AND marzban_username IS NOT NULL AND marzban_username != ''"
    )
    return _rows_as_dicts(rs)


async def mark_order_expired(order_id: int):
    await execute("UPDATE orders SET status = 'expired' WHERE id = ?", [order_id])


async def get_order(order_id: int):
    rs = await execute("SELECT * FROM orders WHERE id = ?", [order_id])
    rows = _rows_as_dicts(rs)
    return rows[0] if rows else None


async def update_order(order_id: int, **fields):
    if not fields:
        return
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [order_id]
    await execute(f"UPDATE orders SET {columns} WHERE id = ?", values)


async def list_orders_by_user(user_id: int) -> list:
    rs = await execute(
        "SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC", [user_id]
    )
    return _rows_as_dicts(rs)


async def list_orders(offset: int = 0, limit: int = 10, status: str = None) -> list:
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
    if status:
        rs = await execute("SELECT COUNT(*) FROM orders WHERE status = ?", [status])
    else:
        rs = await execute("SELECT COUNT(*) FROM orders")
    return rs.rows[0][0]


async def order_stats() -> dict:
    rs = await execute("SELECT status, COUNT(*) FROM orders GROUP BY status")
    result = {"pending": 0, "approved": 0, "rejected": 0, "cancelled": 0, "expired": 0}
    for row in rs.rows:
        status, count = row[0], row[1]
        result[status] = count
    return result


async def revenue_total() -> int:
    rs = await execute(
        "SELECT COALESCE(SUM(price), 0) FROM orders WHERE status IN ('approved', 'expired')"
    )
    return rs.rows[0][0] or 0


async def revenue_since(iso_date: str) -> int:
    rs = await execute(
        "SELECT COALESCE(SUM(price), 0) FROM orders WHERE status IN ('approved', 'expired') AND datetime(created_at) >= datetime(?)",
        [iso_date],
    )
    return rs.rows[0][0] or 0


async def count_fulfilled_orders() -> int:
    """تعداد خریدهای واقعی که تا به حال با موفقیت فعال شده‌اند (چه هنوز فعال باشند چه منقضی شده باشند) - تست رایگان را شامل نمی‌شود"""
    rs = await execute("SELECT COUNT(*) FROM orders WHERE status IN ('approved', 'expired') AND order_type = 'purchase'")
    return rs.rows[0][0]


# ---------------- کدهای تخفیف ----------------

async def create_discount_code(code: str, kind: str, value: float, max_uses: int, expires_at: str):
    await execute(
        "INSERT INTO discount_codes (code, kind, value, max_uses, used_count, expires_at, active, created_at) "
        "VALUES (?, ?, ?, ?, 0, ?, 1, ?) "
        "ON CONFLICT(code) DO UPDATE SET kind=excluded.kind, value=excluded.value, "
        "max_uses=excluded.max_uses, expires_at=excluded.expires_at, active=1",
        [code, kind, value, max_uses, expires_at, _now()],
    )


async def get_discount_code(code: str):
    rs = await execute("SELECT * FROM discount_codes WHERE code = ?", [code])
    rows = _rows_as_dicts(rs)
    return rows[0] if rows else None


async def list_discount_codes() -> list:
    rs = await execute("SELECT * FROM discount_codes ORDER BY created_at DESC")
    return _rows_as_dicts(rs)


async def delete_discount_code(code: str):
    await execute("DELETE FROM discount_codes WHERE code = ?", [code])


async def increment_discount_usage(code: str) -> bool:
    """افزایش اتمی و امن شمارنده‌ی استفاده از کد تخفیف؛ سقف max_uses هم در همان کوئری چک می‌شود
    تا در صورت درخواست همزمان چند کاربر، کد بیشتر از max_uses استفاده ثبت نشود."""
    rs = await execute(
        "UPDATE discount_codes SET used_count = used_count + 1 "
        "WHERE code = ? AND (max_uses IS NULL OR max_uses = 0 OR used_count < max_uses) "
        "RETURNING used_count",
        [code],
    )
    if len(rs.rows) > 0:
        return True
    existing = await get_discount_code(code)
    if not existing:
        return False
    if existing.get("max_uses") and existing.get("used_count", 0) >= existing["max_uses"]:
        return False
    await execute("UPDATE discount_codes SET used_count = used_count + 1 WHERE code = ?", [code])
    return True


# ---------------- سرورها (دفترچه مدیریتی داخلی ربات) ----------------

async def add_server(name: str, address: str) -> int:
    rs = await execute(
        "INSERT INTO servers (name, address, enabled, added_at) VALUES (?, ?, 1, ?)",
        [name, address, _now()],
    )
    return rs.last_insert_rowid


async def list_servers() -> list:
    rs = await execute("SELECT * FROM servers ORDER BY id ASC")
    return _rows_as_dicts(rs)


async def get_server(server_id: int):
    rs = await execute("SELECT * FROM servers WHERE id = ?", [server_id])
    rows = _rows_as_dicts(rs)
    return rows[0] if rows else None


async def remove_server(server_id: int):
    await execute("DELETE FROM servers WHERE id = ?", [server_id])


async def toggle_server(server_id: int):
    server = await get_server(server_id)
    if not server:
        return
    await execute(
        "UPDATE servers SET enabled = ? WHERE id = ?",
        [0 if server["enabled"] else 1, server_id],
    )


async def count_total_active_users() -> int:
    # توجه: جدول orders هیچ ستون server_id ندارد و در جریان خرید هم سروری برای هر
    # سفارش انتخاب/ثبت نمی‌شود، بنابراین امکان شمارش دقیق «کاربران هر سرور» به
    # صورت جداگانه وجود ندارد. این تابع فقط تعداد کل کاربران با سفارش تاییدشده در
    # کل سیستم را برمی‌گرداند (یک عدد سراسری، نه مخصوص یک سرور).
    rs = await execute("SELECT COUNT(DISTINCT user_id) AS c FROM orders WHERE status = 'approved'")
    rows = _rows_as_dicts(rs)
    return int(rows[0]["c"]) if rows and rows[0]["c"] is not None else 0


# ---------------- تیکت‌ها ----------------

async def create_ticket(user_id: int, subject: str) -> int:
    rs = await execute(
        "INSERT INTO tickets (user_id, subject, status, created_at) VALUES (?, ?, 'open', ?)",
        [user_id, subject, _now()],
    )
    return rs.last_insert_rowid


async def add_ticket_message(ticket_id: int, sender_id: int, is_admin: bool, message: str):
    await execute(
        "INSERT INTO ticket_messages (ticket_id, sender_id, is_admin, message, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [ticket_id, sender_id, 1 if is_admin else 0, message, _now()],
    )


async def get_ticket(ticket_id: int):
    rs = await execute("SELECT * FROM tickets WHERE id = ?", [ticket_id])
    rows = _rows_as_dicts(rs)
    return rows[0] if rows else None


async def list_tickets_by_user(user_id: int) -> list:
    rs = await execute(
        "SELECT * FROM tickets WHERE user_id = ? ORDER BY id DESC", [user_id]
    )
    return _rows_as_dicts(rs)


async def list_open_tickets(offset: int = 0, limit: int = 10) -> list:
    rs = await execute(
        "SELECT * FROM tickets WHERE status = 'open' ORDER BY id DESC LIMIT ? OFFSET ?",
        [limit, offset],
    )
    return _rows_as_dicts(rs)


async def list_ticket_messages(ticket_id: int) -> list:
    rs = await execute(
        "SELECT * FROM ticket_messages WHERE ticket_id = ? ORDER BY id ASC", [ticket_id]
    )
    return _rows_as_dicts(rs)


async def set_ticket_status(ticket_id: int, status: str):
    await execute("UPDATE tickets SET status = ? WHERE id = ?", [status, ticket_id])


# ---------------- کمکی برای مدیریت اکانت مرزبان ----------------

async def get_order_by_marzban_username(username: str):
    rs = await execute(
        "SELECT * FROM orders WHERE marzban_username = ? ORDER BY id DESC LIMIT 1", [username]
    )
    rows = _rows_as_dicts(rs)
    return rows[0] if rows else None


# ---------------- کیف پول ----------------


# ==================== سیستم رفرال ====================

def _referral_secret() -> bytes:
    return hashlib.sha256((getattr(config, "BOT_TOKEN", "") or "").encode("utf-8") + b"|JETO-REFERRAL-V5").digest()


def make_referral_code(user_id: int) -> str:
    """V10: shortest Telegram deep-link payload: numeric Telegram user ID only."""
    uid = int(user_id)
    if uid <= 0:
        raise ValueError("invalid user id")
    return str(uid)


def resolve_referral_code(code: str):
    """V10 accepts ONLY the numeric Telegram user ID referral format.

    All V5/V8/V9 signed referral formats are intentionally invalid from V10 onward.
    """
    if not code or not isinstance(code, str):
        return None
    code = code.strip()
    if not code.isdigit():
        return None
    try:
        uid = int(code)
    except (TypeError, ValueError):
        return None
    return uid if uid > 0 else None


async def get_or_create_referral_code(user_id: int) -> str:
    """Return the V10 numeric referral payload and migrate any old stored value."""
    uid = int(user_id)
    code = str(uid)
    row = await get_user(uid)
    if row and str(row.get("referral_code") or "") == code:
        return code
    await execute("UPDATE users SET referral_code = ? WHERE user_id = ?", [code, uid])
    return code

async def claim_first_referral_start(user_id: int) -> bool:
    """Atomically consume the user's one-time referral attribution window.

    V11 rule: only the user's very first /start may carry referral attribution.
    A previous plain /start, an invalid/old payload, or any other first interaction
    permanently closes the attribution window. Concurrent /start requests are safe.
    """
    try:
        rs = await execute(
            "UPDATE users SET referral_start_locked = 1 "
            "WHERE user_id = ? AND COALESCE(referral_start_locked, 0) = 0 RETURNING user_id",
            [user_id],
        )
        return bool(rs.rows)
    except Exception:
        rs = await execute(
            "SELECT COALESCE(referral_start_locked, 0) FROM users WHERE user_id = ?", [user_id]
        )
        if not rs.rows or int(rs.rows[0][0] or 0):
            return False
        await execute(
            "UPDATE users SET referral_start_locked = 1 WHERE user_id = ? AND COALESCE(referral_start_locked, 0) = 0",
            [user_id],
        )
        verify = await execute(
            "SELECT COALESCE(referral_start_locked, 0) FROM users WHERE user_id = ?", [user_id]
        )
        return bool(verify.rows and int(verify.rows[0][0] or 0) == 1)


async def set_pending_referrer(user_id: int, referrer_id: int):
    if user_id == referrer_id:
        return False
    row = await get_user(user_id)
    referrer = await get_user(referrer_id)
    if not row or not referrer or await is_user_blocked(referrer_id):
        return False
    # هر کاربر فقط یک معرف دارد؛ شروع مجدد با لینک‌های دیگر معرف را عوض نمی‌کند.
    if row.get("pending_referrer_id") or await get_referral_by_referred(user_id):
        return False
    rs = await execute(
        "UPDATE users SET pending_referrer_id = ? "
        "WHERE user_id = ? AND pending_referrer_id IS NULL AND referral_start_locked = 1",
        [referrer_id, user_id],
    )
    return bool(getattr(rs, "rows", None))


async def get_pending_referrer(user_id: int):
    row = await get_user(user_id)
    return int(row["pending_referrer_id"]) if row and row.get("pending_referrer_id") else None


async def get_referral_by_referred(referred_id: int):
    rs = await execute("SELECT * FROM referrals WHERE referred_id = ? AND COALESCE(is_test,0)=0 LIMIT 1", [referred_id])
    rows = _rows_as_dicts(rs)
    return rows[0] if rows else None


async def get_referral_by_id(referral_id: int):
    rs = await execute("SELECT * FROM referrals WHERE id = ? LIMIT 1", [referral_id])
    rows = _rows_as_dicts(rs)
    return rows[0] if rows else None


async def finalize_pending_referral(referred_id: int):
    """پس از عبور کامل از قوانین/جوین اجباری، رفرال را اتمیک و فقط یک‌بار ثبت می‌کند."""
    pending = await get_pending_referrer(referred_id)
    if not pending or pending == referred_id:
        return None

    existing = await get_referral_by_referred(referred_id)
    if existing:
        await execute("UPDATE users SET pending_referrer_id = NULL WHERE user_id = ?", [referred_id])
        return existing

    referrer = await get_user(pending)
    if not referrer:
        await execute("UPDATE users SET pending_referrer_id = NULL WHERE user_id = ?", [referred_id])
        return None

    enabled = await get_setting("referral_enabled", "0")
    if enabled != "1":
        return None

    reward = max(0, int(float(await get_setting("referral_reward", "5000") or 0)))
    condition = await get_setting("referral_condition_enabled", "1") == "1"
    status = "locked" if condition else "unlocked"

    code = await get_or_create_referral_code(pending)
    try:
        rs = await execute(
            "INSERT INTO referrals (referrer_id, referred_id, referral_code, reward_amount, status, qualified, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?)",
            [pending, referred_id, code, reward, status, _now()],
        )
    except Exception:
        # UNIQUE(referred_id) جلوی ثبت دوباره را می‌گیرد؛ در صورت رقابت هم داده خراب نمی‌شود.
        existing = await get_referral_by_referred(referred_id)
        if existing:
            await execute("UPDATE users SET pending_referrer_id = NULL WHERE user_id = ?", [referred_id])
            return existing
        raise
    await execute("UPDATE users SET pending_referrer_id = NULL WHERE user_id = ?", [referred_id])
    referral = await get_referral_by_id(rs.last_insert_rowid)
    if referral:
        # پاداش در هر دو حالت به موجودی کل اضافه می‌شود؛ در حالت شرط‌دار،
        # بخش قفل‌شده فقط از نظر خرج‌کردن محدود است و با شارژ واقعی/تحقق شرط آزاد می‌شود.
        reward_amount = int(referral["reward_amount"] or 0)
        if reward_amount > 0:
            await adjust_wallet_balance(referral["referrer_id"], reward_amount)
            if referral["status"] == "unlocked":
                await execute(
                    "UPDATE users SET referral_available_balance = COALESCE(referral_available_balance, 0) + ? WHERE user_id = ?",
                    [reward_amount, referral["referrer_id"]],
                )
            else:
                await execute(
                    "UPDATE users SET referral_locked_balance = COALESCE(referral_locked_balance, 0) + ? WHERE user_id = ?",
                    [reward_amount, referral["referrer_id"]],
                )
            await record_wallet_transaction(
                referral["referrer_id"], reward_amount, "referral_reward",
                note=f"پاداش دعوت موفق #{referral['id']}" + (" (قفل‌شده)" if referral["status"] != "unlocked" else ""),
            )
    return referral


async def list_referrals_by_referrer(referrer_id: int):
    rs = await execute("SELECT * FROM referrals WHERE referrer_id = ? ORDER BY id DESC", [referrer_id])
    return _rows_as_dicts(rs)


async def referral_summary(referrer_id: int):
    rs = await execute(
        "SELECT COUNT(*) AS total, "
        "COUNT(*) AS successful, "
        "COALESCE(SUM(CASE WHEN qualified = 1 THEN 1 ELSE 0 END), 0) AS qualified, "
        "COALESCE(SUM(COALESCE(released_amount, CASE WHEN status = 'unlocked' THEN reward_amount ELSE 0 END)), 0) AS unlocked, "
        "COALESCE(SUM(MAX(reward_amount - COALESCE(released_amount, 0), 0)), 0) AS locked "
        "FROM referrals WHERE referrer_id = ? AND COALESCE(is_test,0)=0",
        [referrer_id],
    )
    row = _rows_as_dicts(rs)[0]
    return {k: int(row[k] or 0) for k in row}


async def unlock_referral_reward(referral_id: int, order_id: int):
    """کل مبلغ باقی‌مانده یک رفرال را آزاد می‌کند؛ رفرال ممکن است قبلاً بخشی از پاداشش را با شارژ واقعی آزاد کرده باشد."""
    referral = await get_referral_by_id(referral_id)
    if not referral:
        return False
    remaining = max(0, int(referral.get("reward_amount") or 0) - int(referral.get("released_amount") or 0))
    if remaining <= 0:
        return False
    try:
        rs = await execute(
            "UPDATE referrals SET status = 'unlocked', qualified = 1, qualified_order_id = ?, qualified_at = ?, released_amount = reward_amount "
            "WHERE id = ? AND COALESCE(released_amount,0) < reward_amount RETURNING id, referrer_id, reward_amount, released_amount",
            [order_id, _now(), referral_id],
        )
        rows = _rows_as_dicts(rs)
        if not rows:
            return False
        changed = rows[0]
        credit = remaining
    except Exception:
        current = await get_referral_by_id(referral_id)
        if not current:
            return False
        remaining = max(0, int(current.get("reward_amount") or 0) - int(current.get("released_amount") or 0))
        if remaining <= 0:
            return False
        await execute(
            "UPDATE referrals SET status = 'unlocked', qualified = 1, qualified_order_id = ?, qualified_at = ?, released_amount = reward_amount "
            "WHERE id = ? AND COALESCE(released_amount,0) < reward_amount",
            [order_id, _now(), referral_id],
        )
        changed = await get_referral_by_id(referral_id)
        if not changed or int(changed.get("released_amount") or 0) < int(changed.get("reward_amount") or 0):
            return False
        credit = remaining
    # مبلغ قبلاً هنگام ثبت دعوت وارد موجودی کل شده؛ اینجا فقط «قفل» را به «قابل‌مصرف» تبدیل می‌کنیم.
    await execute(
        "UPDATE users SET referral_locked_balance = MAX(COALESCE(referral_locked_balance,0) - ?, 0), "
        "referral_available_balance = COALESCE(referral_available_balance,0) + ? WHERE user_id = ?",
        [credit, credit, changed["referrer_id"]],
    )
    await record_wallet_transaction(
        changed["referrer_id"], credit, "referral_reward_unlock",
        note=f"آزادسازی پاداش رفرال #{referral_id}", related_order_id=order_id,
    )
    return True


async def release_referral_by_real_topup(user_id: int, amount: int):
    """به ازای هر تومان شارژ واقعی، همان مقدار از پاداش قفل‌شده آزاد می‌شود.
    این عملیات شرط خرید دوست را دور نمی‌زند؛ فقط بخشی از پاداش قفل‌شده را قابل مصرف می‌کند."""
    amount = max(0, int(amount or 0))
    if amount <= 0:
        return 0
    remaining_to_release = amount
    total_released = 0
    rs = await execute(
        "SELECT id, reward_amount, COALESCE(released_amount,0) AS released_amount FROM referrals "
        "WHERE referrer_id = ? AND COALESCE(is_test,0)=0 AND COALESCE(left_at,'')='' AND COALESCE(released_amount,0) < reward_amount "
        "ORDER BY id ASC", [user_id]
    )
    for row in _rows_as_dicts(rs):
        if remaining_to_release <= 0:
            break
        available = max(0, int(row["reward_amount"] or 0) - int(row["released_amount"] or 0))
        if available <= 0:
            continue
        credit = min(available, remaining_to_release)
        old_released = int(row["released_amount"] or 0)
        new_released = old_released + credit
        new_status = "unlocked" if new_released >= int(row["reward_amount"] or 0) else "partial"
        try:
            changed_rs = await execute(
                "UPDATE referrals SET released_amount = ?, status = ? "
                "WHERE id = ? AND COALESCE(released_amount,0) = ? RETURNING id",
                [new_released, new_status, row["id"], old_released],
            )
            changed = bool(changed_rs.rows)
        except Exception:
            await execute(
                "UPDATE referrals SET released_amount = ?, status = ? "
                "WHERE id = ? AND COALESCE(released_amount,0) = ?",
                [new_released, new_status, row["id"], old_released],
            )
            check = await get_referral_by_id(row["id"])
            changed = bool(check and int(check.get("released_amount") or 0) == new_released)
        if not changed:
            continue
        total_released += credit
        remaining_to_release -= credit
    if total_released:
        # شارژ واقعی خودش قبلاً جداگانه به wallet_balance اضافه شده است؛
        # این مقدار فقط از قفل رفرال به بخش قابل‌مصرف منتقل می‌شود و دوباره به موجودی کل اضافه نمی‌گردد.
        await execute(
            "UPDATE users SET referral_locked_balance = MAX(COALESCE(referral_locked_balance,0) - ?, 0), "
            "referral_available_balance = COALESCE(referral_available_balance,0) + ? WHERE user_id = ?",
            [total_released, total_released, user_id],
        )
        await record_wallet_transaction(user_id, total_released, "referral_topup_release", note="آزادسازی پاداش رفرال به اندازه شارژ واقعی کیف پول")
    return total_released


async def qualify_referral_for_order(order_id: int):
    """خرید واجدشرایط دعوت واقعی را ثبت می‌کند و در صورت تکمیل شرط، پاداش‌ها را آزاد می‌کند."""
    order = await get_order(order_id)
    if not order or order.get("status") != "approved" or order.get("order_type") != "purchase":
        return None
    package = await get_package(order.get("package_id")) if order.get("package_id") else None
    gb = order.get("custom_gb")
    if gb is None and package:
        gb = package.get("gb")
    try:
        gb = float(gb or 0)
        min_gb = float(await get_setting("referral_min_gb", "10") or 10)
    except (TypeError, ValueError):
        return None
    if gb < min_gb:
        return None

    referral = await get_referral_by_referred(order["user_id"])
    # دعوتی که قبلاً از جوین اجباری لفت داده، حتی اگر دوباره برگردد، دیگر
    # نمی‌تواند همان رفرال را دوباره واجدشرایط یا پاداش‌دار کند.
    if not referral or referral.get("qualified") or referral.get("left_at"):
        return None

    await execute(
        "UPDATE referrals SET qualified = 1, qualified_order_id = ?, qualified_at = ? "
        "WHERE id = ? AND COALESCE(is_test,0)=0 AND COALESCE(qualified,0)=0 AND COALESCE(released_amount,0) < reward_amount",
        [order_id, _now(), referral["id"]],
    )
    scope = "COALESCE(is_test,0)=0 AND COALESCE(left_at,'')='' AND referrer_id=?"
    rs = await execute(
        f"SELECT COUNT(*) AS c FROM referrals WHERE {scope} AND COALESCE(released_amount,0) < reward_amount AND qualified=0",
        [referral["referrer_id"]],
    )
    locked_count = int(_rows_as_dicts(rs)[0]["c"] or 0)
    if locked_count > 0:
        return False

    rs = await execute(
        f"SELECT id FROM referrals WHERE {scope} AND qualified=1 AND COALESCE(released_amount,0)<reward_amount",
        [referral["referrer_id"]],
    )
    unlocked = 0
    for row in _rows_as_dicts(rs):
        if await unlock_referral_reward(row["id"], order_id):
            unlocked += 1
    return unlocked > 0


async def release_all_locked_referrals():
    """وقتی شرط خاموش شد، فقط بخش باقی‌مانده پاداش‌های قفل‌شده آزاد می‌شود."""
    rs = await execute("SELECT id, referrer_id FROM referrals WHERE COALESCE(is_test,0)=0 AND COALESCE(left_at,'')='' AND COALESCE(released_amount,0) < reward_amount")
    released = 0
    for row in _rows_as_dicts(rs):
        if await unlock_referral_reward(row["id"], 0):
            released += 1
    return released


async def get_referral_stats_for_admin():
    rs = await execute(
        "SELECT COUNT(*) AS total, "
        "COALESCE(SUM(CASE WHEN qualified=1 THEN 1 ELSE 0 END),0) AS qualified, "
        "COALESCE(SUM(reward_amount),0) AS rewards, "
        "COALESCE(SUM(COALESCE(released_amount,0)),0) AS released, "
        "COALESCE(SUM(MAX(reward_amount-COALESCE(released_amount,0),0)),0) AS locked, "
        "COALESCE(SUM(CASE WHEN COALESCE(left_at,'')!='' THEN 1 ELSE 0 END),0) AS left_count, "
        "COALESCE(SUM(CASE WHEN COALESCE(penalty_status,'none')='done' THEN 1 ELSE 0 END),0) AS penalties, "
        "COALESCE(SUM(CASE WHEN COALESCE(penalty_status,'none')='done' THEN COALESCE(penalty_amount,0) ELSE 0 END),0) AS penalty_money "
        "FROM referrals WHERE COALESCE(is_test,0)=0"
    )
    row=_rows_as_dicts(rs)[0]
    # بدهی جریمه‌ها در users است؛ فقط برای آمار، نه برای محاسبه موجودی.
    debt_rs=await execute("SELECT COALESCE(SUM(referral_penalty_debt),0) FROM users")
    row['debts']=int(debt_rs.rows[0][0] or 0) if debt_rs.rows else 0
    return {k:int(row[k] or 0) for k in row}

async def get_referral_admin_rows(limit: int = 10, offset: int = 0):
    rs = await execute(
        "SELECT r.*, u.username AS referred_username, "
        "COALESCE(u2.username, '') AS referrer_username "
        "FROM referrals r LEFT JOIN users u ON u.user_id = r.referred_id "
        "LEFT JOIN users u2 ON u2.user_id = r.referrer_id "
        "WHERE COALESCE(r.is_test,0)=0 ORDER BY r.id DESC LIMIT ? OFFSET ?", [int(limit), int(offset)]
    )
    return _rows_as_dicts(rs)


async def get_referral_admin_detail(referral_id: int):
    rs = await execute(
        "SELECT r.*, u.username AS referred_username, COALESCE(u2.username,'') AS referrer_username "
        "FROM referrals r LEFT JOIN users u ON u.user_id=r.referred_id "
        "LEFT JOIN users u2 ON u2.user_id=r.referrer_id WHERE r.id=? AND COALESCE(r.is_test,0)=0 LIMIT 1", [referral_id]
    )
    rows = _rows_as_dicts(rs)
    return rows[0] if rows else None


async def list_referral_penalty_candidates(limit: int = 50):
    # V11 scans every active referral; transition state is tracked per channel, so a
    # user can be penalized again after leaving, rejoining, and leaving again.
    rs = await execute(
        "SELECT * FROM referrals WHERE COALESCE(is_test,0)=0 ORDER BY id ASC LIMIT ?",
        [int(limit)],
    )
    return _rows_as_dicts(rs)


async def get_referral_penalty_event(referral_id: int, channel: str):
    rs = await execute(
        "SELECT * FROM referral_penalty_events WHERE referral_id=? AND channel=? AND status='pending' ORDER BY id DESC LIMIT 1",
        [int(referral_id), str(channel)[:300]],
    )
    rows = _rows_as_dicts(rs)
    return rows[0] if rows else None


async def claim_referral_leave(referral_id: int, channel: str):
    """V11: claim only a NEW member->left transition for this referral/channel.
    Repeated scans while the user remains outside cannot duplicate a penalty; after
    the user rejoins, the next real leave creates a new pending penalty event.
    """
    channel = str(channel)[:300]
    now = _now()
    # First ensure a state row exists and read the previous state.
    rs = await execute(
        "SELECT is_left FROM referral_penalty_states WHERE referral_id=? AND channel=? LIMIT 1",
        [int(referral_id), channel],
    )
    was_left = bool(rs.rows and int(rs.rows[0][0] or 0))
    if was_left:
        return None
    try:
        await execute(
            "INSERT INTO referral_penalty_states(referral_id,channel,is_left,last_checked_at,left_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(referral_id,channel) DO UPDATE SET is_left=1,last_checked_at=excluded.last_checked_at,left_at=excluded.left_at "
            "WHERE referral_penalty_states.is_left=0",
            [int(referral_id), channel, 1, now, now],
        )
        verify = await execute(
            "SELECT is_left,left_at FROM referral_penalty_states WHERE referral_id=? AND channel=? LIMIT 1",
            [int(referral_id), channel],
        )
        if not verify.rows or int(verify.rows[0][0] or 0) != 1:
            return None
        left_at = str(verify.rows[0][1] or now)
        # Only one event may exist for this exact transition.
        try:
            ev = await execute(
                "INSERT INTO referral_penalty_events(referral_id,channel,left_at,status) VALUES(?,?,?,'pending') RETURNING id",
                [int(referral_id), channel, left_at],
            )
        except Exception:
            ev = None
        if ev is not None and getattr(ev, 'rows', None):
            base = await get_referral_by_id(referral_id)
            if base:
                base['penalty_event_id'] = int(ev.rows[0][0])
                base['left_at'] = left_at
                base['left_channel'] = channel
                base['penalty_status'] = 'pending'
                return base
        return None
    except Exception:
        return None


async def mark_referral_penalty_checked(referral_id: int):
    # Kept for compatibility; V11 state tracking supersedes the old one-shot marker.
    await execute("UPDATE referrals SET penalty_checked_at=? WHERE id=?", [_now(), int(referral_id)])


async def list_pending_referral_penalty_events(limit: int = 100):
    rs = await execute(
        "SELECT e.*, r.referrer_id, r.referred_id, r.reward_amount FROM referral_penalty_events e "
        "JOIN referrals r ON r.id=e.referral_id WHERE e.status='pending' ORDER BY e.id ASC LIMIT ?",
        [int(limit)],
    )
    return _rows_as_dicts(rs)


async def claim_referral_penalty_event(event_id: int) -> bool:
    """Claim a pending event so only one worker applies its external side effects."""
    try:
        rs = await execute(
            "UPDATE referral_penalty_events SET status='processing', claimed_at=? "
            "WHERE id=? AND status='pending' RETURNING id",
            [_now(), int(event_id)],
        )
        return bool(rs.rows)
    except Exception:
        return False


async def mark_referral_penalty_pending_done(event_id: int):
    await execute(
        "UPDATE referral_penalty_events SET status='done', applied_at=? WHERE id=? AND status='pending'",
        [_now(), int(event_id)],
    )


async def mark_referral_penalty_retry(event_id: int):
    await execute(
        "UPDATE referral_penalty_events SET status='pending' WHERE id=?", [int(event_id)]
    )


async def mark_referral_channel_rejoined(referral_id: int, channel: str):
    await execute(
        "INSERT INTO referral_penalty_states(referral_id,channel,is_left,last_checked_at,left_at) VALUES(?,?,0,?,NULL) "
        "ON CONFLICT(referral_id,channel) DO UPDATE SET is_left=0,last_checked_at=excluded.last_checked_at",
        [int(referral_id), str(channel)[:300], _now()],
    )


async def get_active_referral_funded_orders(referrer_id: int):
    rs = await execute(
        "SELECT * FROM orders WHERE user_id=? AND status='approved' AND order_type='purchase' "
        "AND COALESCE(marzban_username,'')!='' AND COALESCE(referral_funds_used,0)>0 ORDER BY id ASC", [referrer_id]
    )
    return _rows_as_dicts(rs)


async def apply_referral_cash_penalty(referrer_id: int, amount: int):
    """کسر جریمه فقط از منابع رفرالی.
    اول پاداش آزادشده و سپس پاداش قفل‌شده مصرف می‌شود؛ موجودی واقعی هرگز در این تابع لمس نمی‌شود.
    خروجی مقدار واقعی کسرشده است."""
    amount = max(0, int(amount or 0))
    if amount <= 0:
        return 0
    for _ in range(6):
        rs = await execute(
            "SELECT COALESCE(wallet_balance,0), COALESCE(referral_available_balance,0), COALESCE(referral_locked_balance,0) "
            "FROM users WHERE user_id=?", [referrer_id]
        )
        if not rs.rows:
            return 0
        wallet = max(0, int(rs.rows[0][0] or 0))
        available = max(0, int(rs.rows[0][1] or 0))
        locked = max(0, int(rs.rows[0][2] or 0))
        # منابع رفرالی نباید از کل کیف پول بیشتر شوند. این clamp فقط برای جلوگیری از
        # آسیب به موجودی واقعی در صورت ناسازگاری قدیمی دیتابیس است.
        available = min(available, wallet)
        locked = min(locked, max(0, wallet - available))
        take_available = min(amount, available)
        take_locked = min(amount - take_available, locked)
        take = take_available + take_locked
        if take <= 0:
            return 0
        new_wallet = wallet - take
        new_available = available - take_available
        new_locked = locked - take_locked
        try:
            changed = await execute(
                "UPDATE users SET wallet_balance=?, referral_available_balance=?, referral_locked_balance=? "
                "WHERE user_id=? AND COALESCE(wallet_balance,0)=? "
                "AND COALESCE(referral_available_balance,0)=? AND COALESCE(referral_locked_balance,0)=? RETURNING user_id",
                [new_wallet, new_available, new_locked, referrer_id, wallet, available, locked],
            )
            if changed.rows:
                await record_wallet_transaction(referrer_id, -take, "referral_penalty", note="جریمه خروج دعوت‌شده؛ فقط از موجودی رفرالی")
                return take
        except Exception:
            pass
        await execute(
            "UPDATE users SET wallet_balance=?, referral_available_balance=?, referral_locked_balance=? "
            "WHERE user_id=? AND COALESCE(wallet_balance,0)=? "
            "AND COALESCE(referral_available_balance,0)=? AND COALESCE(referral_locked_balance,0)=?",
            [new_wallet, new_available, new_locked, referrer_id, wallet, available, locked]
        )
        after = await execute(
            "SELECT COALESCE(wallet_balance,0), COALESCE(referral_available_balance,0), COALESCE(referral_locked_balance,0) "
            "FROM users WHERE user_id=?", [referrer_id]
        )
        if after.rows:
            vals=tuple(int(after.rows[0][i] or 0) for i in range(3))
            if vals == (new_wallet, new_available, new_locked):
                await record_wallet_transaction(referrer_id, -take, "referral_penalty", note="جریمه خروج دعوت‌شده؛ فقط از موجودی رفرالی")
                return take
    return 0


async def add_referral_penalty_debt(referrer_id: int, amount: int) -> int:
    amount = max(0, int(amount or 0))
    if amount <= 0:
        return 0
    await execute("UPDATE users SET referral_penalty_debt=COALESCE(referral_penalty_debt,0)+? WHERE user_id=?", [amount, referrer_id])
    return amount


async def get_referral_penalty_debt(user_id: int) -> int:
    rs = await execute("SELECT COALESCE(referral_penalty_debt,0) FROM users WHERE user_id=?", [user_id])
    return int(rs.rows[0][0] or 0) if rs.rows else 0


async def settle_referral_penalty_debt_from_topup(user_id: int, topup_amount: int) -> int:
    topup_amount = max(0, int(topup_amount or 0))
    if topup_amount <= 0:
        return 0
    for _ in range(5):
        rs = await execute("SELECT COALESCE(referral_penalty_debt,0) FROM users WHERE user_id=?", [user_id])
        debt = int(rs.rows[0][0] or 0) if rs.rows else 0
        if debt <= 0:
            return 0
        take = min(debt, topup_amount)
        try:
            changed = await execute(
                "UPDATE users SET wallet_balance=MAX(wallet_balance-?,0), referral_penalty_debt=referral_penalty_debt-? "
                "WHERE user_id=? AND COALESCE(referral_penalty_debt,0)=? RETURNING user_id", [take, take, user_id, debt]
            )
            if changed.rows:
                await record_wallet_transaction(user_id, -take, "referral_penalty_debt", note="تسویه بدهی جریمه رفرال از شارژ واقعی")
                return take
        except Exception:
            pass
    return 0


async def mark_referral_penalty_done(referral_id: int, money: int, gb: float, days: int, order_id: int = None):
    await execute(
        "UPDATE referrals SET penalty_status='done', penalty_amount=?, penalty_gb=?, penalty_days=?, penalty_order_id=?, penalty_applied_at=? WHERE id=?",
        [int(money or 0), float(gb or 0), int(days or 0), order_id, _now(), referral_id]
    )


async def get_referral_top_referrers(limit: int = 10):
    rs = await execute(
        "SELECT r.referrer_id, COALESCE(u.username,'') AS username, "
        "COUNT(*) AS total, COALESCE(SUM(CASE WHEN r.qualified=1 THEN 1 ELSE 0 END),0) AS qualified, "
        "COALESCE(SUM(r.reward_amount),0) AS rewards, "
        "COALESCE(SUM(COALESCE(r.released_amount,0)),0) AS released, "
        "COALESCE(SUM(CASE WHEN COALESCE(r.left_at,'')!='' THEN 1 ELSE 0 END),0) AS left_count, "
        "COALESCE(SUM(CASE WHEN COALESCE(r.penalty_status,'none')='done' THEN 1 ELSE 0 END),0) AS penalized "
        "FROM referrals r LEFT JOIN users u ON u.user_id=r.referrer_id "
        "WHERE COALESCE(r.is_test,0)=0 GROUP BY r.referrer_id, u.username "
        "ORDER BY total DESC, r.referrer_id ASC LIMIT ?", [int(limit)]
    )
    return _rows_as_dicts(rs)


async def get_referral_user_overview(referrer_id: int):
    rs = await execute(
        "SELECT COUNT(*) AS total, COALESCE(SUM(CASE WHEN qualified=1 THEN 1 ELSE 0 END),0) AS qualified, "
        "COALESCE(SUM(reward_amount),0) AS rewards, COALESCE(SUM(released_amount),0) AS released, "
        "COALESCE(SUM(CASE WHEN COALESCE(left_at,'')!='' THEN 1 ELSE 0 END),0) AS left_count, "
        "COALESCE(SUM(CASE WHEN COALESCE(penalty_status,'none')='done' THEN 1 ELSE 0 END),0) AS penalties "
        "FROM referrals WHERE referrer_id=? AND COALESCE(is_test,0)=0", [referrer_id]
    )
    row=_rows_as_dicts(rs)[0]
    return {k:int(row[k] or 0) for k in row}


async def list_referrals_for_referrer(referrer_id: int, limit: int = 100):
    rs=await execute(
        "SELECT r.*, COALESCE(u.username,'') AS referred_username FROM referrals r "
        "LEFT JOIN users u ON u.user_id=r.referred_id WHERE r.referrer_id=? AND COALESCE(r.is_test,0)=0 ORDER BY r.id DESC LIMIT ?",
        [referrer_id, int(limit)]
    )
    return _rows_as_dicts(rs)


async def reset_referrals_for_referrer(referrer_id: int):
    """ریست attribution های یک معرف؛ فقط موجودی داخلی رفرالِ باقی‌مانده را برمی‌گرداند و پول واقعی را دست نمی‌زند."""
    rs=await execute("SELECT COALESCE(referral_available_balance,0), COALESCE(referral_locked_balance,0) FROM users WHERE user_id=?", [referrer_id])
    if not rs.rows:
        return False
    available=int(rs.rows[0][0] or 0); locked=int(rs.rows[0][1] or 0); remove=max(0,available+locked)
    if remove:
        await execute("UPDATE users SET wallet_balance=MAX(COALESCE(wallet_balance,0)-?,0), referral_available_balance=0, referral_locked_balance=0 WHERE user_id=?", [remove,referrer_id])
        await record_wallet_transaction(referrer_id,-remove,"referral_reset",note="ریست موجودی باقی‌مانده رفرال توسط ادمین")
    await execute("DELETE FROM referrals WHERE referrer_id=? AND COALESCE(is_test,0)=0", [referrer_id])
    return True


async def reset_referral_record(referral_id: int):
    """ادمین می‌تواند یک attribution رفرال را حذف کند؛ فقط خود رکورد رفرال حذف می‌شود و پول قبلاً خرج‌شده دستکاری نمی‌شود."""
    row = await get_referral_by_id(referral_id)
    if not row or int(row.get("is_test") or 0):
        return False
    await execute("DELETE FROM referrals WHERE id = ? AND COALESCE(is_test,0)=0", [referral_id])
    return True


async def get_wallet_balance(user_id: int) -> int:
    rs = await execute("SELECT wallet_balance FROM users WHERE user_id = ?", [user_id])
    if not rs.rows or rs.rows[0][0] is None:
        return 0
    return int(rs.rows[0][0])


async def adjust_wallet_balance(user_id: int, delta: int) -> int:
    """تنزیم موجودی کیف پول (مقدار منفی = کاهش). اگر کاربر در جدول نباشد اول ساخته می‌شود"""
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


async def record_wallet_transaction(user_id: int, amount: int, kind: str, status: str = "done", note: str = None, receipt_file_id: str = None, related_order_id: int = None) -> int:
    rs = await execute(
        "INSERT INTO wallet_transactions (user_id, amount, kind, status, receipt_file_id, note, created_at, related_order_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [user_id, amount, kind, status, receipt_file_id, note, _now(), related_order_id],
    )
    return rs.last_insert_rowid


async def add_wallet_transaction(user_id: int, amount: int, kind: str, related_order_id: int = None, status: str = "done", note: str = None, receipt_file_id: str = None) -> int:
    """نام مستعار record_wallet_transaction با اولویت پارامتر related_order_id (برای ثبت تراکنش‌های وابسته به یک سفارش، مثل خرید از کیف پول)"""
    return await record_wallet_transaction(user_id, amount, kind, status=status, note=note, receipt_file_id=receipt_file_id, related_order_id=related_order_id)


async def create_wallet_topup(user_id: int, amount: int, receipt_file_id: str) -> int:
    return await record_wallet_transaction(user_id, amount, "topup", status="pending", receipt_file_id=receipt_file_id)


async def get_wallet_transaction(tx_id: int):
    rs = await execute("SELECT * FROM wallet_transactions WHERE id = ?", [tx_id])
    rows = _rows_as_dicts(rs)
    return rows[0] if rows else None


async def update_wallet_transaction(tx_id: int, **fields):
    if not fields:
        return
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [tx_id]
    await execute(f"UPDATE wallet_transactions SET {columns} WHERE id = ?", values)


async def list_wallet_transactions(user_id: int, limit: int = 20) -> list:
    rs = await execute(
        "SELECT * FROM wallet_transactions WHERE user_id = ? ORDER BY id DESC LIMIT ?", [user_id, limit]
    )
    return _rows_as_dicts(rs)


async def list_pending_wallet_topups(limit: int = 20) -> list:
    rs = await execute(
        "SELECT * FROM wallet_transactions WHERE kind = 'topup' AND status = 'pending' ORDER BY id DESC LIMIT ?", [limit]
    )
    return _rows_as_dicts(rs)



async def get_internal_referral_balance(user_id: int) -> int:
    """موجودی داخلی پاداش‌های آزادشده رفرال؛ در رابط کاربر نمایش جداگانه ندارد."""
    rs = await execute("SELECT COALESCE(referral_available_balance, 0) FROM users WHERE user_id = ?", [user_id])
    return int(rs.rows[0][0] or 0) if rs.rows else 0


async def try_spend_wallet_for_purchase(user_id: int, amount: int, package_gb: float = 0):
    """پرداخت از یک کیف پول ظاهراً واحد با سه بخش داخلی: رفرال قابل‌مصرف، پول عادی و رفرال قفل‌شده.
    ترتیب مصرف: رفرال قابل‌مصرف -> پول عادی. رفرال قفل‌شده فقط در پلن حداقل مجاز قابل استفاده است.
    شارژ واقعی هرگز قفل نیست. خروجی: (success, referral_used)."""
    if amount <= 0:
        return True, 0
    spend_limit_enabled = await get_setting("referral_spend_enabled", "0") == "1"
    try:
        min_gb = float(await get_setting("referral_spend_min_gb", "10") or 10)
    except (TypeError, ValueError):
        min_gb = 10.0

    for _ in range(5):
        rs = await execute(
            "SELECT COALESCE(wallet_balance,0), COALESCE(referral_available_balance,0), COALESCE(referral_locked_balance,0) "
            "FROM users WHERE user_id = ?", [user_id]
        )
        if not rs.rows:
            return False, 0
        wallet, referral_available, referral_locked = (int(rs.rows[0][0] or 0), int(rs.rows[0][1] or 0), int(rs.rows[0][2] or 0))
        referral_available = min(max(referral_available, 0), wallet)
        referral_locked = min(max(referral_locked, 0), max(0, wallet - referral_available))
        regular = max(0, wallet - referral_available - referral_locked)

        if wallet < amount:
            return False, 0

        from_available = min(amount, referral_available)
        remaining = amount - from_available
        from_regular = min(remaining, regular)
        remaining -= from_regular
        # پاداش قفل‌شده هرگز قابل خرج نیست؛ فقط با شرط آزادسازی/شارژ واقعی آزاد می‌شود.
        if remaining > 0:
            return False, 0

        from_locked = 0
        if from_available > 0 and spend_limit_enabled and float(package_gb or 0) < min_gb:
            # فقط وقتی واقعاً پول رفرالی در این خرید مصرف می‌شود محدودیت اعمال می‌شود.
            return False, -1

        new_available = referral_available - from_available
        new_locked = referral_locked
        try:
            upd = await execute(
                "UPDATE users SET wallet_balance = wallet_balance - ?, "
                "referral_available_balance = ?, referral_locked_balance = ? "
                "WHERE user_id = ? AND COALESCE(wallet_balance,0) = ? "
                "AND COALESCE(referral_available_balance,0) = ? AND COALESCE(referral_locked_balance,0) = ? RETURNING user_id",
                [amount, new_available, new_locked, user_id, wallet, referral_available, referral_locked],
            )
            if upd.rows:
                return True, from_available + from_locked
        except Exception:
            pass
        await execute(
            "UPDATE users SET wallet_balance = wallet_balance - ?, referral_available_balance = ?, referral_locked_balance = ? "
            "WHERE user_id = ? AND COALESCE(wallet_balance,0) = ? "
            "AND COALESCE(referral_available_balance,0) = ? AND COALESCE(referral_locked_balance,0) = ?",
            [amount, new_available, new_locked, user_id, wallet, referral_available, referral_locked],
        )
        after = await execute(
            "SELECT COALESCE(wallet_balance,0), COALESCE(referral_available_balance,0), COALESCE(referral_locked_balance,0) "
            "FROM users WHERE user_id = ?", [user_id]
        )
        if after.rows and (int(after.rows[0][0] or 0), int(after.rows[0][1] or 0), int(after.rows[0][2] or 0)) == (wallet - amount, new_available, new_locked):
            return True, from_available + from_locked
    return False, 0


async def refund_wallet_purchase(user_id: int, amount: int, referral_used: int = 0):
    """بازگرداندن مبلغ خرید؛ بخش رفرالی که خرج شده بود نیز به موجودی داخلی خود برمی‌گردد."""
    if amount <= 0:
        return
    referral_used = max(0, min(int(referral_used or 0), int(amount)))
    await adjust_wallet_balance(user_id, amount)
    if referral_used:
        await execute(
            "UPDATE users SET referral_available_balance = COALESCE(referral_available_balance,0) + ? WHERE user_id = ?",
            [referral_used, user_id],
        )

async def try_spend_wallet_balance(user_id: int, amount: int) -> bool:
    """کاهش اتمی و امن موجودی کیف پول (جهت جلوگیری از خرج شدن دوباره در اثر کلیک همزمان/رقابتی).
    فقط وقتی موجودی کافی باشد کاهش انجام می‌شود و True برمی‌گردد؛ در غیر این صورت هیچ تغییری
    اعمال نمی‌شود و False برمی‌گردد."""
    if amount <= 0:
        return True
    try:
        rs = await execute(
            "UPDATE users SET wallet_balance = wallet_balance - ? "
            "WHERE user_id = ? AND COALESCE(wallet_balance, 0) >= ? "
            "RETURNING wallet_balance",
            [amount, user_id, amount],
        )
        return len(rs.rows) > 0
    except Exception:
        logger.warning("try_spend_wallet_balance: RETURNING پشتیبانی نشد، سوییچ به مسیر جایگزین امن.")
        for _ in range(3):
            before = await get_wallet_balance(user_id)
            if before < amount:
                return False
            await execute(
                "UPDATE users SET wallet_balance = wallet_balance - ? "
                "WHERE user_id = ? AND COALESCE(wallet_balance, 0) = ?",
                [amount, user_id, before],
            )
            after = await get_wallet_balance(user_id)
            if after == before - amount:
                return True
        return False


# ---------------- ابزارهای مدیریتی نسخه ۴۸ ----------------
async def reset_revenue_counter(actor_id: int = None, note: str = "manual") -> int:
    total = await revenue_total()
    await execute("INSERT INTO revenue_resets (reset_at, actor_id, note) VALUES (?, ?, ?)", [_now(), actor_id, note])
    await log_action("admin", actor_id, f"ریست شمارنده درآمد؛ درآمد تا این لحظه {total} تومان بود")
    return total


async def last_revenue_reset():
    rs = await execute("SELECT * FROM revenue_resets ORDER BY id DESC LIMIT 1")
    rows = _rows_as_dicts(rs)
    return rows[0] if rows else None


async def revenue_since_last_reset() -> int:
    reset = await last_revenue_reset()
    if not reset:
        return await revenue_total()
    return await revenue_since(reset["reset_at"])


async def clear_logs(log_type: str = None) -> int:
    if log_type and log_type != "all":
        rs = await execute("SELECT COUNT(*) FROM logs WHERE log_type = ?", [log_type])
        count = rs.rows[0][0] if rs.rows else 0
        await execute("DELETE FROM logs WHERE log_type = ?", [log_type])
        return count
    rs = await execute("SELECT COUNT(*) FROM logs")
    count = rs.rows[0][0] if rs.rows else 0
    await execute("DELETE FROM logs")
    return count


async def list_active_purchase_orders() -> list:
    """Approved paid purchase services that have a panel username."""
    rs = await execute(
        "SELECT * FROM orders WHERE status = 'approved' AND order_type = 'purchase' AND marzban_username IS NOT NULL AND marzban_username != ''"
    )
    return _rows_as_dicts(rs)


async def claim_wallet_topup(tx_id: int) -> bool:
    """Atomically claim a pending topup for approval — prevents double-approval race condition."""
    try:
        rs = await execute(
            "UPDATE wallet_transactions SET status = 'approved', kind = 'topup_approved' "
            "WHERE id = ? AND status = 'pending' RETURNING id",
            [tx_id],
        )
        return bool(rs.rows)
    except Exception:
        # Fallback for DBs without RETURNING support
        before = await get_wallet_transaction(tx_id)
        if not before or before["status"] != "pending":
            return False
        await execute(
            "UPDATE wallet_transactions SET status = 'approved', kind = 'topup_approved' WHERE id = ? AND status = 'pending'",
            [tx_id],
        )
        after = await get_wallet_transaction(tx_id)
        return bool(after and after["status"] == "approved")


async def claim_wallet_topup_reject(tx_id: int) -> bool:
    """Atomically claim a pending topup for rejection — prevents double-reject race condition."""
    try:
        rs = await execute(
            "UPDATE wallet_transactions SET status = 'rejected', kind = 'topup_rejected' "
            "WHERE id = ? AND status = 'pending' RETURNING id",
            [tx_id],
        )
        return bool(rs.rows)
    except Exception:
        before = await get_wallet_transaction(tx_id)
        if not before or before["status"] != "pending":
            return False
        await execute(
            "UPDATE wallet_transactions SET status = 'rejected', kind = 'topup_rejected' WHERE id = ? AND status = 'pending'",
            [tx_id],
        )
        after = await get_wallet_transaction(tx_id)
        return bool(after and after["status"] == "rejected")


async def claim_order_for_approval(order_id: int) -> bool:
    """Atomically claim a pending order for approval — prevents double-approval race condition.
    Two admins pressing Approve simultaneously: only one wins, the other gets False."""
    try:
        rs = await execute(
            "UPDATE orders SET status = 'processing' "
            "WHERE id = ? AND status = 'pending' RETURNING id",
            [order_id],
        )
        return bool(rs.rows)
    except Exception:
        # Fallback for DBs without RETURNING support
        before = await get_order(order_id)
        if not before or before["status"] != "pending":
            return False
        await execute(
            "UPDATE orders SET status = 'processing' WHERE id = ? AND status = 'pending'",
            [order_id],
        )
        after = await get_order(order_id)
        return bool(after and after["status"] == "processing")
