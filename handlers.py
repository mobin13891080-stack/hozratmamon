# -*- coding: utf-8 -*-
# handlers.py - هندلرهای اصلی: منو، خرید، تست، حساب من، پشتیبانی/تیکت، پلن‌ها، تنظیمات، ادمین‌ها

import asyncio
import datetime
import html
import json
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ConversationHandler

import config
import database
import marzban_api
import qr_util

# ---------------- دکمه‌های رنگی (ویژگی Style در Bot API 9.4) ----------------
# style یکی از 'primary' (آبی) / 'success' (سبز) / 'danger' (قرمز) است.

_DANGER_HINTS = ("❌", "🗑", "🔴", "🚫", "حذف", " رد", "رد ", "لغو", "مسدود", "خاموش", "بستن", "حذف")
_SUCCESS_HINTS = ("✅", "➕", "🟢", "تایید", "فعال کردن", "افزودن", "افزون", "عضو شدم", "روشن کردن")


def _auto_style(text):
    for hint in _DANGER_HINTS:
        if hint in text:
            return "danger"
    for hint in _SUCCESS_HINTS:
        if hint in text:
            return "success"
    return "primary"


def ibtn(text, callback_data=None, url=None, style=None):
    """ساخت دکمه شیشه‌ای (inline) رنگی. اگر style ندهید، رنگ بر اساس متن دکمه تشخیص داده می‌شود."""
    kwargs = {}
    if callback_data is not None:
        kwargs["callback_data"] = callback_data
    if url is not None:
        kwargs["url"] = url
    chosen_style = style or _auto_style(text)
    return InlineKeyboardButton(text, api_kwargs={"style": chosen_style}, **kwargs)


def kbtn(text, style="primary"):
    """ساخت دکمه رنگی منوی ثابت (ReplyKeyboard). اگر style=None باشد، دکمه بدون رنگ (پیش‌فرض تلگرام) می‌ماند."""
    if style is None:
        return KeyboardButton(text)
    return KeyboardButton(text, api_kwargs={"style": style})


def flow_cancel_keyboard():
    """دکمه بازگشت عمومی برای جایگزینی /cancel در هر مرحله ورودی‌متن"""
    return InlineKeyboardMarkup([[ibtn("🔙 بازگشت", callback_data="flow_cancel", style="danger")]])



async def safe_edit_or_send(query, text, reply_markup=None, parse_mode="HTML"):
    """ویرایش امن پیام فعلی (منو) به‌جای فرستادن پیام جدید و شلوق کردن چت.
    اگر محتوای جدید دقیقا مثل قبلی بود (خطای «not modified»)، پیام تکراری فرستاده نمی‌شود.
    فقط اگر ویرایش واقعا ممکن نبود (پیام حذف‌شده/قدیمی)، پیام قبلی حذف و پیام تازه فرستاده می‌شود
    تا هیچ‌وقت دو منو همزمان روی هم نماند."""
    try:
        await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        return
    except Exception as e:
        if "not modified" in str(e).lower():
            return
    try:
        await query.message.delete()
    except Exception:
        pass
    await query.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)


async def send_chunked(message, text, parse_mode="HTML", max_len=3500):
    """ارسال متن طولانی به‌صورت چند پیام کوتاه‌تر برای رد شدن از محدودیت تلگرام
    (خطای «Message is too long»)"""
    remaining = text
    if len(remaining) <= max_len:
        await message.reply_text(remaining, parse_mode=parse_mode)
        return
    while remaining:
        if len(remaining) <= max_len:
            await message.reply_text(remaining, parse_mode=parse_mode)
            break
        cut = remaining.rfind("\n\n", 0, max_len)
        if cut <= 0:
            cut = max_len
        await message.reply_text(remaining[:cut], parse_mode=parse_mode)
        remaining = remaining[cut:].lstrip("\n")


async def show_error_with_back(update, context, error_text):
    """بستن منوی قبلی (چه پیام متنی چه callback) و نمایش خطا با دکمه بازگشت،
    به‌جای باقی ماندن منوی قبلی و فرستادن پیام خطای جدید و بی‌ربط."""
    keyboard = InlineKeyboardMarkup([[ibtn("🏠 بازگشت به منوی اصلی", callback_data="err_back_main", style="primary")]])
    query = getattr(update, "callback_query", None)
    chat = None
    if query is not None and query.message is not None:
        chat = query.message.chat_id
        try:
            await query.message.delete()
        except Exception:
            pass
    elif update.effective_chat is not None:
        chat = update.effective_chat.id
    if chat is None:
        return
    await context.bot.send_message(chat_id=chat, text=error_text, parse_mode="HTML", reply_markup=keyboard)
CHOOSE_DISCOUNT, DISCOUNT_INPUT, SELECT_RECEIPT = range(100, 103)
AWAITING_ADMIN_INPUT = 200
TICKET_USER_MESSAGE, TICKET_ADMIN_REPLY = range(300, 302)
WALLET_TOPUP_AMOUNT, WALLET_TOPUP_RECEIPT = range(400, 402)
CUSTOM_GB_INPUT, CUSTOM_DAYS_INPUT = range(500, 502)
USERNAME_INPUT = 502
REJECT_REASON_INPUT = 600

BTN_BUY = "🛍️ خرید سرویس"
BTN_TRIAL = "🎁 تست رایگان"
BTN_ACCOUNT = "👤 سرویس‌های من"
BTN_SUPPORT = "🆘 پشتیبانی"
BTN_WALLET = "💰 كيف پول + شارژ"
BTN_HELP = "❓ راهنما"
BTN_ADMIN_PANEL = "⚙️ پنل مدیریت"

BTN_ADMIN_USERS = "👥 مدیریت کاربران"
BTN_ADMIN_ORDERS = "🧾 مدیریت سفارش‌ها"
BTN_ADMIN_PACKAGES = "📦 مدیریت پلن‌ها"
BTN_ADMIN_DISCOUNTS = "🎟️ کدهای تخفیف"
BTN_ADMIN_CUSTOMBUILDER = "🚀 کانفیگ اختصاصی VIP"
BTN_ADMIN_SERVERS = "🖥️ مدیریت سرورها"
BTN_ADMIN_FINANCE = "💰 مدیریت مالی"
BTN_ADMIN_NOTIFY = "📢 اطلاع-رسانی"
BTN_ADMIN_TICKETS = "🎧 تیکت‌های پشتیبانی"
BTN_ADMIN_STATS = "📊 آمار ربات"
BTN_ADMIN_ADMINS = "👮 مدیریت ادمین‌ها"
BTN_ADMIN_LOGS = "📜 لاگ‌ها"
BTN_ADMIN_CARD = "💳 شماره کارت"
BTN_ADMIN_FORCEJOIN = "🔒 عضویت اجباری"
BTN_ADMIN_TRIAL = "🎁 تنظیمات تست رایگان"
BTN_ADMIN_RULES = "📜 قوانین و لینک‌ها"
BTN_ADMIN_WELCOME = "📝 متن خوش‌آمدگویی"
BTN_ADMIN_WELCOME_PHOTO = "🖼️ عکس خوش‌آمدگویی"
BTN_ADMIN_WELCOME_GIF = "🎞 گیف خوش‌آمدگویی"
BTN_ADMIN_SUPPORT_SETTINGS = "🆘 تنظیمات پشتیبانی"
BTN_ADMIN_TURN_OFF = "🔴 خاموش کردن ربات"
BTN_ADMIN_TURN_ON = "🟢 روشن کردن ربات"
BTN_BACK_MAIN = "🔙 بازگشت به منوی اصلی"

TRUST_CHANNEL = "@Jetovpn_etemad"


def _mask_user_id(user_id):
    """ماسک کردن ایدی تلگرام برای نمایش عمومی در کانال اعتماد، مثلا 65*278"""
    s = str(user_id)
    if len(s) <= 5:
        return s[:2] + "*" * max(len(s) - 2, 1)
    return s[:2] + "*" * (len(s) - 5) + s[-3:]

BUTTON_PERMISSION = {
    BTN_ADMIN_USERS: "users", BTN_ADMIN_ORDERS: "orders", BTN_ADMIN_PACKAGES: "packages",
    BTN_ADMIN_DISCOUNTS: "discounts", BTN_ADMIN_CUSTOMBUILDER: "packages", BTN_ADMIN_SERVERS: "servers", BTN_ADMIN_FINANCE: "finance",
    BTN_ADMIN_NOTIFY: "notify", BTN_ADMIN_TICKETS: "tickets", BTN_ADMIN_STATS: "stats",
    BTN_ADMIN_ADMINS: "admins", BTN_ADMIN_LOGS: "logs", BTN_ADMIN_CARD: "settings",
    BTN_ADMIN_FORCEJOIN: "settings", BTN_ADMIN_TRIAL: "settings", BTN_ADMIN_RULES: "settings",
    BTN_ADMIN_WELCOME: "settings", BTN_ADMIN_WELCOME_PHOTO: "settings", BTN_ADMIN_WELCOME_GIF: "settings", BTN_ADMIN_SUPPORT_SETTINGS: "settings",
}

ADMIN_MENU_ROWS = [
    [BTN_ADMIN_USERS, BTN_ADMIN_ORDERS],
    [BTN_ADMIN_PACKAGES, BTN_ADMIN_DISCOUNTS],
    [BTN_ADMIN_CUSTOMBUILDER],
    [BTN_ADMIN_SERVERS, BTN_ADMIN_FINANCE],
    [BTN_ADMIN_NOTIFY, BTN_ADMIN_TICKETS],
    [BTN_ADMIN_STATS, BTN_ADMIN_ADMINS],
    [BTN_ADMIN_LOGS],
    [BTN_ADMIN_CARD, BTN_ADMIN_FORCEJOIN],
    [BTN_ADMIN_TRIAL, BTN_ADMIN_RULES],
    [BTN_ADMIN_WELCOME, BTN_ADMIN_WELCOME_PHOTO],
    [BTN_ADMIN_WELCOME_GIF],
    [BTN_ADMIN_SUPPORT_SETTINGS],
]

ASYNC_SETTING_ACTIONS = {
    "admin_set_card_number": ("card_number", "شماره کارت جدید را وارد کنید:"),
    "admin_set_card_holder": ("card_holder", "نام صاحب کارت را وارد کنید:"),
    "admin_set_trial_gb": ("trial_gb", "حجم تست رایگان را به گیگابایت وارد کنید:"),
    "admin_set_trial_days": ("trial_days", "تعداد روز اعتبار تست رایگان را وارد کنید:"),
    "admin_set_support_text": ("support_text", "متن جدید بخش پشتیبانی را وارد کنید:"),
    "admin_set_support_username": ("support_username", "آیدی پشتیبانی را وارد کنید (مثلا @sup):"),
    "admin_set_rules_text": ("rules_text", "متن جدید قوانین را وارد کنید:"),
    "admin_set_contact_links": ("contact_links", "لینک‌های ارتباطی را وارد کنید:"),
    "admin_set_cb_min_gb": ("custom_builder_min_gb", "حداقل گیگابایت کانفیگ اختصاصی را وارد کنید:"),
    "admin_set_cb_max_gb": ("custom_builder_max_gb", "حداکثر گیگابایت کانفیگ اختصاصی را وارد کنید:"),
    "admin_set_cb_price_gb": ("custom_builder_price_per_gb", "قیمت هر گیگابایت کانفیگ اختصاصی را وارد کنید (تومان):"),
    "admin_set_cb_price_day": ("custom_builder_price_per_day", "قیمت هر روز کانفیگ اختصاصی را وارد کنید (تومان):"),
}


def main_menu_keyboard(is_admin_user):
    rows = [[kbtn(BTN_BUY, style="success"), kbtn(BTN_TRIAL, style="danger")], [kbtn(BTN_ACCOUNT, style=None), kbtn(BTN_WALLET, style=None)], [kbtn(BTN_SUPPORT, style=None), kbtn(BTN_HELP, style=None)]]
    if is_admin_user:
        rows.append([kbtn(BTN_ADMIN_PANEL, style=None)])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def admin_menu_keyboard(perms, bot_enabled):
    rows = []
    for row in ADMIN_MENU_ROWS:
        filtered = [b for b in row if BUTTON_PERMISSION.get(b) in perms]
        if filtered:
            rows.append([kbtn(b) for b in filtered])
    if "bot_toggle" in perms:
        rows.append([kbtn(BTN_ADMIN_TURN_OFF if bot_enabled else BTN_ADMIN_TURN_ON, style="danger" if bot_enabled else "success")])
    rows.append([kbtn(BTN_BACK_MAIN)])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


async def admin_perms_for(user_id):
    if user_id == config.ADMIN_ID:
        return database.ROLE_PERMISSIONS["full"]
    role = await database.get_admin_role(user_id)
    if not role:
        return set()
    return database.ROLE_PERMISSIONS.get(role, set())


async def require_perm(query, user_id, permission):
    if await database.admin_has_permission(user_id, permission):
        return True
    await query.answer("⛔ شما دسترسی به این بخش را ندارید.", show_alert=True)
    return False


async def require_perm_msg(update, user_id, permission):
    if await database.admin_has_permission(user_id, permission):
        return True
    await update.message.reply_text("⛔ شما دسترسی به این بخش را ندارید.")
    return False


async def ensure_bot_active(update, context, user_id):
    if await database.is_admin(user_id):
        return True
    if await database.is_user_blocked(user_id):
        await context.bot.send_message(chat_id=update.effective_chat.id, text="⛔️ حساب شما توسط مدیریت مسدود شده است.")
        return False
    enabled = await database.get_setting("bot_enabled", "1")
    if enabled != "1":
        await context.bot.send_message(chat_id=update.effective_chat.id, text="🚧 ربات موقتاً خاموش است. لطفا بعداً دوباره تلاش کنید.")
        return False
    return True


async def send_main_menu(chat_id, context, user_id, account_name=None):
    welcome_text = await database.get_setting("welcome_text", "خوش امدید")
    if "mmbr" in welcome_text:
        if account_name is None:
            try:
                chat = await context.bot.get_chat(user_id)
                account_name = chat.first_name or chat.full_name or chat.username or str(user_id)
            except Exception:
                account_name = str(user_id)
        welcome_text = welcome_text.replace("mmbr", account_name)
    photo_id = await database.get_setting("welcome_photo_id", "")
    gif_id = await database.get_setting("welcome_gif_id", "")
    is_admin_user = await database.is_admin(user_id)
    keyboard = main_menu_keyboard(is_admin_user)
    if gif_id:
        try:
            await context.bot.send_animation(chat_id=chat_id, animation=gif_id, caption=welcome_text, reply_markup=keyboard)
            return
        except Exception:
            pass
    if photo_id:
        try:
            await context.bot.send_photo(chat_id=chat_id, photo=photo_id, caption=welcome_text, reply_markup=keyboard)
            return
        except Exception:
            pass
    await context.bot.send_message(chat_id=chat_id, text=welcome_text, reply_markup=keyboard)


async def get_force_join_channels():
    raw = await database.get_setting("force_join_channels", "")
    if not raw:
        # مهاجرت از حالت قدیمی تک‌کاناله
        legacy = await database.get_setting("force_join_channel", "")
        return [legacy] if legacy else []
    try:
        channels = json.loads(raw)
        if isinstance(channels, list):
            return [c for c in channels if c]
    except Exception:
        pass
    return []


async def set_force_join_channels(channels):
    await database.set_setting("force_join_channels", json.dumps(channels, ensure_ascii=False))


async def check_membership(context, channel, user_id):
    try:
        member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
        is_member = member.status in ("member", "administrator", "creator")
        if not is_member:
            try:
                await database.log_action(
                    "forcejoin_debug", user_id,
                    f"کاربر {user_id} عضو کانال {channel} نیست (status={member.status})",
                )
            except Exception:
                pass
        return is_member
    except Exception as error:
        try:
            await database.log_action(
                "forcejoin_error", user_id,
                f"خطا در بررسی عضویت کانال {channel} برای کاربر {user_id}: {type(error).__name__}: {error} | نکته: ربات باید ادمین/مالک همین کانال باشد تا بتواند وضعیت عضویت اعضا را ببیند",
            )
        except Exception:
            pass
        return False


async def get_missing_channels(context, user_id):
    """لیست کانال‌هایی که کاربر هنوز عضو نشده را برمی‌گرداند (اگر جوین اجباری فعال باشد)."""
    if await database.get_setting("force_join_enabled", "0") != "1":
        return []
    channels = await get_force_join_channels()
    if not channels:
        return []
    missing = []
    for channel in channels:
        if not await check_membership(context, channel, user_id):
            missing.append(channel)
    return missing


async def show_join_gate(chat_id, context, channels):
    keyboard = []
    for channel in channels:
        channel_display = channel if channel.startswith("http") else "https://t.me/" + channel.lstrip("@")
        keyboard.append([ibtn(f"📢 عضویت در {channel}", url=channel_display, style="primary")])
    keyboard.append([ibtn("✅ عضو شدم", callback_data="check_join", style="success")])
    await context.bot.send_message(chat_id=chat_id, text="برای استفاده از ربات، ابتدا در کانال‌های زیر عضو شوید:", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_rules_gate(chat_id, context):
    rules_text = await database.get_setting("rules_text", "")
    text = "♨️ <b>قوانین استفاده از خدمات ما</b>\n\n" + (rules_text or "-")
    keyboard = InlineKeyboardMarkup([[ibtn("تایید✅️", callback_data="rules_ack", style="success")]])
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=keyboard)


async def _proceed_after_rules(chat_id, context, user_id):
    if not await database.is_admin(user_id):
        missing = await get_missing_channels(context, user_id)
        if missing:
            await show_join_gate(chat_id, context, missing)
            return
    await send_main_menu(chat_id, context, user_id)


async def rules_ack_callback(update, context):
    query = update.callback_query
    await query.answer("✅ تایید شد")
    await database.mark_rules_accepted(query.from_user.id)
    try:
        await query.message.delete()
    except Exception:
        pass
    await _proceed_after_rules(query.message.chat_id, context, query.from_user.id)


async def start(update, context):
    user = update.effective_user
    await database.upsert_user(user.id, user.username or "")
    if not await ensure_bot_active(update, context, user.id):
        return
    if await database.is_admin(user.id):
        await send_main_menu(update.effective_chat.id, context, user.id)
        return
    if await database.has_accepted_rules(user.id):
        await _proceed_after_rules(update.effective_chat.id, context, user.id)
        return
    await show_rules_gate(update.effective_chat.id, context)


async def check_join_callback(update, context):
    query = update.callback_query
    missing = await get_missing_channels(context, query.from_user.id)
    if not missing:
        await query.answer("عضویت شما تایید شد ✅")
        try:
            await query.message.delete()
        except Exception:
            pass
        await send_main_menu(query.message.chat_id, context, query.from_user.id)
    else:
        await query.answer("هنوز در همه کانال‌ها عضو نشده‌اید.", show_alert=True)


async def back_to_main_from_admin(update, context):
    await send_main_menu(update.effective_chat.id, context, update.effective_user.id)


async def err_back_main_callback(update, context):
    """دکمه بازگشت به منوی اصلی زیر پیام خطا."""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    try:
        await query.message.delete()
    except Exception:
        pass
    await send_main_menu(update.effective_chat.id, context, query.from_user.id)


async def main_menu_router(update, context):
    message = update.effective_message
    if not message:
        return
    text = (message.text or "").strip()
    handler_fn = MAIN_MENU_ROUTES.get(text)
    if not handler_fn:
        return
    user_id = update.effective_user.id
    if not await ensure_bot_active(update, context, user_id):
        return
    if not await database.is_admin(user_id):
        missing = await get_missing_channels(context, user_id)
        if missing:
            await show_join_gate(update.effective_chat.id, context, missing)
            return
    await handler_fn(update, context)


async def redirect_if_menu_button(update, context):
    """اگر متن ورودی یکی از دکمه‌های منو باشد (یعنی کاربر در حین ورود اطلاعات، دکمه
    دیگری از منو را لمس کرده)، به آن مسیردهی می‌کند و True برمی‌گرداند،‌ ورنه مقدار ورودی را
    به عنوان داده ثبت می‌کند."""
    message = update.effective_message
    text = (message.text or "").strip() if message else ""
    handler_fn = MAIN_MENU_ROUTES.get(text)
    if not handler_fn:
        return False
    context.user_data.clear()
    await handler_fn(update, context)
    return True


# ==================== خرید سرویس ====================

def _format_duration(days):
    if days and days % 30 == 0:
        months = days // 30
        return f"{months} ماهه"
    return f"{days} روزه"


def _format_package_label(p):
    duration = _format_duration(p["days"])
    gb = p["gb"]
    gb_str = str(int(gb)) if float(gb).is_integer() else str(gb)
    return f"🚀 {gb_str} گیگ | {duration} کاربر ∞ — {p['price']:,} تومان"

async def _buy_categories_view():
    categories = await database.list_package_categories()
    custom_enabled = await database.get_setting("custom_builder_enabled", "0") == "1"
    uncategorized_count = await database.count_packages_by_category(None, active_only=True)
    keyboard = []
    for c in categories:
        count = await database.count_packages_by_category(c["id"], active_only=True)
        if count == 0:
            continue
        keyboard.append([ibtn(f"🗂 {c['name']}", callback_data=f"buy_cat:{c['id']}", style="primary")])
    if uncategorized_count > 0:
        keyboard.append([ibtn("📦 سایر پلن‌ها", callback_data="buy_cat:uncat", style="primary")])
    if custom_enabled:
        keyboard.append([ibtn("🚀 کانفیگ خودتو بساز (ویژه VIP)", callback_data="custom_builder_start", style="success")])
    keyboard.append([ibtn("🔙 بازگشت به منوی اصلی", callback_data="buy_back_main", style="danger")])
    if len(keyboard) <= 1:
        text = "⚠️ <b>در حال حاضر پلنی برای فروش وجود ندارد.</b>"
    else:
        text = "🛍️ <b>خب، یکی از گزینه‌های زیر رو انتخاب کنید:</b>"
    return text, InlineKeyboardMarkup(keyboard)


async def buy_show_packages(update, context):
    text, keyboard = await _buy_categories_view()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def buy_categories_back_callback(update, context):
    query = update.callback_query
    await query.answer()
    text, keyboard = await _buy_categories_view()
    await safe_edit_or_send(query, text, reply_markup=keyboard)


async def buy_back_main_callback(update, context):
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass
    await send_main_menu(query.message.chat_id, context, query.from_user.id)


async def buy_category_callback(update, context):
    query = update.callback_query
    if not await ensure_bot_active(update, context, query.from_user.id):
        await query.answer()
        return
    await query.answer()
    raw_id = query.data.split(":", 1)[1]
    category_id = None if raw_id == "uncat" else int(raw_id)
    packages = await database.list_packages_by_category(category_id, active_only=True)
    if category_id is None:
        category_name = "سایر پلن‌ها"
    else:
        category = await database.get_package_category(category_id)
        category_name = category["name"] if category else "پلن‌ها"
    keyboard = [[ibtn(_format_package_label(p), callback_data=f"pkg:{p['id']}", style="primary")] for p in packages]
    keyboard.append([ibtn("🔙 بازگشت", callback_data="buy_categories_back", style="danger")])
    if packages:
        text = f"🛍️ <b>{category_name}</b>\n━━━━━━━━━━━━━━━\nیکی از پلن‌های زیر را انتخاب کنید:"
    else:
        text = f"⚠️ <b>در دسته «{category_name}» در حال حاضر پلنی موجود نیست.</b>"
    await safe_edit_or_send(query, text, reply_markup=InlineKeyboardMarkup(keyboard))


async def select_package(update, context):
    query = update.callback_query
    if not await ensure_bot_active(update, context, query.from_user.id):
        await query.answer()
        return ConversationHandler.END
    package_id = int(query.data.split(":")[1])
    package = await database.get_package(package_id)
    if not package or not package["active"]:
        await query.answer("این پلن دیگر موجود نیست.", show_alert=True)
        return ConversationHandler.END
    context.user_data["selected_package_id"] = package_id
    context.user_data["discount_code"] = None
    context.user_data["custom_builder"] = False
    context.user_data.pop("custom_gb", None)
    context.user_data.pop("custom_days", None)
    await query.answer()
    await _show_payment_choice(query, context, package["price"], package=package, show_discount_option=True)
    return CHOOSE_DISCOUNT


async def custom_builder_start(update, context):
    query = update.callback_query
    if not await ensure_bot_active(update, context, query.from_user.id):
        await query.answer()
        return ConversationHandler.END
    if await database.get_setting("custom_builder_enabled", "0") != "1":
        await query.answer("این قابلیت فعال نیست.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    min_gb = float(await database.get_setting("custom_builder_min_gb", "5"))
    max_gb = float(await database.get_setting("custom_builder_max_gb", "200"))
    context.user_data["selected_package_id"] = None
    context.user_data["custom_builder"] = True
    context.user_data["discount_code"] = None
    await query.edit_message_text(
        f"🚀 <b>کانفیگ خودتو بساز (ویژه VIP)</b>\n━━━━━━━━━━━━━━━\n"
        f"💾 مقدار گیگابایت مورد نظر را وارد کنید (بین {min_gb:g} تا {max_gb:g} گیگ):",
        parse_mode="HTML",
        reply_markup=flow_cancel_keyboard(),
    )
    return CUSTOM_GB_INPUT


async def receive_custom_gb(update, context):
    if await redirect_if_menu_button(update, context):
        return ConversationHandler.END
    text = (update.effective_message.text or "").strip()
    try:
        gb = float(text)
        if gb <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ لطفا فقط یک عدد معتبر وارد کنید.")
        return CUSTOM_GB_INPUT
    min_gb = float(await database.get_setting("custom_builder_min_gb", "5"))
    max_gb = float(await database.get_setting("custom_builder_max_gb", "200"))
    if gb < min_gb or gb > max_gb:
        await update.message.reply_text(f"❌ مقدار گیگابایت باید بین {min_gb:g} تا {max_gb:g} باشد. دوباره وارد کنید:")
        return CUSTOM_GB_INPUT
    context.user_data["custom_gb"] = gb
    charge_days = await database.get_setting("custom_builder_charge_days", "1") == "1"
    note = "" if charge_days else "\n💡 روزهای سرویس رایگان است و هزینه‌ای از شما گرفته نمی‌شود."
    await update.message.reply_text(f"⏳ <b>تعداد روز اعتبار سرویس را وارد کنید:</b>{note}", parse_mode="HTML", reply_markup=flow_cancel_keyboard())
    return CUSTOM_DAYS_INPUT


async def receive_custom_days(update, context):
    if await redirect_if_menu_button(update, context):
        return ConversationHandler.END
    text = (update.effective_message.text or "").strip()
    try:
        days = int(float(text))
        if days <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ لطفا فقط یک عدد صحیح معتبر وارد کنید.")
        return CUSTOM_DAYS_INPUT
    gb = context.user_data.get("custom_gb")
    context.user_data["custom_days"] = days
    price_per_gb = float(await database.get_setting("custom_builder_price_per_gb", "0"))
    price_per_day = float(await database.get_setting("custom_builder_price_per_day", "0"))
    charge_days = await database.get_setting("custom_builder_charge_days", "1") == "1"
    final_price = int(gb * price_per_gb + (days * price_per_day if charge_days else 0))
    context.user_data["discount_code"] = None
    synthetic_package = {"name": "🚀 کانفیگ اختصاصی VIP", "gb": gb, "days": days}
    await _show_payment_choice(update, context, final_price, package=synthetic_package, show_discount_option=True)
    return CHOOSE_DISCOUNT


async def _resolve_order_package(context):
    """برمی‌گرداند (package, custom_gb, custom_days). برای سفارش‌های عادی package از دیتاباز خوانده می‌شود.
    برای کانفیگ اختصاصی VIP، package مقدار None است و custom_gb/custom_days برگردانده می‌شوند."""
    if context.user_data.get("custom_builder"):
        gb = context.user_data.get("custom_gb")
        days = context.user_data.get("custom_days")
        if gb is None or days is None:
            return None, None, None
        return None, gb, days
    package_id = context.user_data.get("selected_package_id")
    if not package_id:
        return None, None, None
    package = await database.get_package(package_id)
    return package, None, None


async def _show_payment_choice(update_or_query, context, final_price, package=None, show_discount_option=True):
    """نمایش سه گزینه پرداخت: کارت به کارت / کد تخفیف / کیف پول (اگر موجودی کافی باشد)"""
    context.user_data["final_price"] = final_price
    user_id = update_or_query.from_user.id if hasattr(update_or_query, "from_user") else update_or_query.effective_user.id
    wallet_balance = await database.get_wallet_balance(user_id)
    lines = ["🧾 <b>نهایی‌سازی خرید</b>", "━━━━━━━━━━━"]
    if package:
        lines.append(f"📦 <b>پلن:</b> {package['name']}")
        lines.append(f"💾 <b>حجم:</b> {package['gb']} گیگابایت  |  ⏳ <b>مدت:</b> {package['days']} روز")
    lines.append(f"💵 <b>مبلغ قابل پرداخت:</b> {final_price:,} تومان")
    wallet_line = f"💰 <b>موجودی کیف پول شما:</b> {wallet_balance:,} تومان"
    if wallet_balance >= final_price:
        wallet_line += " ✅ کافی است"
    lines.append(wallet_line)
    lines.append("\n👇 <b>روش پرداخت را انتخاب کنید:</b>")
    text = "\n".join(lines)
    keyboard_rows = [[ibtn("💳 پرداخت کارت به کارت", callback_data="pay_card", style="primary")]]
    if show_discount_option:
        keyboard_rows.append([ibtn("🎟️ کد تخفیف دارم", callback_data="discount_have", style="primary")])
    wallet_style = "success" if wallet_balance >= final_price else "primary"
    keyboard_rows.append([ibtn("💰 پرداخت از کیف پول", callback_data="pay_wallet_direct", style=wallet_style)])
    keyboard_rows.append([ibtn("🔙 انصراف", callback_data="flow_cancel", style="danger")])
    keyboard = InlineKeyboardMarkup(keyboard_rows)
    if hasattr(update_or_query, "edit_message_text"):
        await update_or_query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await update_or_query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _show_card_payment(update_or_query, context, final_price):
    context.user_data["final_price"] = final_price
    card_number = await database.get_setting("card_number", "-")
    card_holder = await database.get_setting("card_holder", "-")
    text = (
        "💳 <b>پرداخت کارت به کارت</b>\n"
        "━━━━━━━━━━━\n"
        f"💵 <b>مبلغ قابل پرداخت:</b> {final_price:,} تومان\n\n"
        f"💳 <b>شماره کارت:</b> <code>{card_number}</code>\n👤 <b>به نام:</b> {card_holder}\n\n"
        "📸 <b>لطفا بعد از واریز، عکس رسید پرداخت را همینجا ارسال کنید.</b>"
    )
    copy_btn = InlineKeyboardButton("📋 کپی شماره کارت", api_kwargs={"copy_text": {"text": card_number}})
    keyboard = InlineKeyboardMarkup([[copy_btn], [ibtn("🔙 انصراف", callback_data="flow_cancel", style="danger")]])
    if hasattr(update_or_query, "edit_message_text"):
        await update_or_query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await update_or_query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


def _validate_desired_username(text):
    """فقط حروف انگلیسی و عدد مجاز است، بدون @، فاصله، خط تیره یا اندرلاین
    و باید با یک حرف انگلیسی شروع شود. طول 3 تا 32 کاراکتر."""
    text = (text or "").strip()
    if not (3 <= len(text) <= 32):
        return False
    import re as _re
    return bool(_re.match(r"^[A-Za-z][A-Za-z0-9]{2,31}$", text))


async def _prompt_desired_username(update_or_query):
    """قبل از اینکه مشتری وارید کند یا از کیف پول پرداخت کند، اول یک نام کاربری دلخواه می‌گیریم."""
    text = (
        "📝 یک نام کاربری دلخواه ارسال کنید\n"
        "⚠️ نام کاربری باید بدون کاراکترهای اضافه مانند @ ، فاصله ، خط تیره باشد.\n"
        "⚠️ نام کاربری باید انگلیسی باشد.\n"
        "👇👇\n"
        "✅ نام کاربری های صحیح : mmd11 | mahdi | ws1ksdf\n"
        "👆👆\n"
        "❌ نام کاربری های نادرست : ali_ | tele@ | _mahdi | محسن | ws1_ksdf\n\n"
        "✨️اگر نخواستید نام کاربری تعیین کنید می‌تونید از گزینه زیر دکمه ساخت نام کاربری رندوم استفاده کنید👇✨️"
    )
    keyboard = InlineKeyboardMarkup([
        [ibtn("🎲 انتخاب نام کاربری رندوم", callback_data="username_random", style="primary")],
        [ibtn("🔙 انصراف", callback_data="flow_cancel", style="danger")],
    ])
    if hasattr(update_or_query, "edit_message_text"):
        await update_or_query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update_or_query.message.reply_text(text, reply_markup=keyboard)


async def pay_card_callback(update, context):
    query = update.callback_query
    await query.answer()
    final_price = context.user_data.get("final_price")
    package_id = context.user_data.get("selected_package_id")
    is_custom = bool(context.user_data.get("custom_builder"))
    if final_price is None or (not package_id and not is_custom):
        await query.edit_message_text("⚠️ <b>خطا. لطفا دوباره از منو شروع کنید.</b>", parse_mode="HTML")
        return ConversationHandler.END
    context.user_data["post_username_action"] = "card"
    await _prompt_desired_username(query)
    return USERNAME_INPUT


async def pay_wallet_direct_callback(update, context):
    query = update.callback_query
    await query.answer()
    final_price = context.user_data.get("final_price")
    package_id = context.user_data.get("selected_package_id")
    is_custom = bool(context.user_data.get("custom_builder"))
    if final_price is None or (not package_id and not is_custom):
        await query.edit_message_text("⚠️ <b>خطا. لطفا دوباره از منو شروع کنید.</b>", parse_mode="HTML")
        return ConversationHandler.END
    context.user_data["post_username_action"] = "wallet"
    await _prompt_desired_username(query)
    return USERNAME_INPUT


async def receive_desired_username(update, context):
    if await redirect_if_menu_button(update, context):
        return ConversationHandler.END
    text = (update.effective_message.text or "").strip()
    if not _validate_desired_username(text):
        await update.message.reply_text(
            "❌ <b>نام کاربری نامعتبر است.</b>\n"
            "• فقط حروف و اعداد انگلیسی (بدون @، فاصله، خط تیره، اندرلاین)\n"
            "• باید با حرف شروع شود و بین 3 تا 32 کاراکتر باشد.\n\n"
            "✅ مثلا: <code>mmd11</code> | <code>mahdi</code> | <code>ws1ksdf</code>\n"
            "دوباره یک نام کاربری بفرستید:",
            parse_mode="HTML",
        )
        return USERNAME_INPUT
    username_lower = text.lower()
    username_taken = False
    try:
        await marzban_api.get_user(username_lower)
        username_taken = True
    except Exception as error:
        if "404" not in str(error):
            try:
                await database.log_action("error", None, f"خطای بررسی نام کاربری {username_lower}: {error}")
            except Exception:
                pass
    if username_taken:
        await update.message.reply_text(
            "❌ <b>نام کاربری تکراری است. لطفا نام کاربری دیگری انتخاب کنید.</b>",
            parse_mode="HTML",
        )
        return USERNAME_INPUT
    context.user_data["desired_username"] = username_lower
    next_action = context.user_data.get("post_username_action")
    if next_action == "wallet":
        return await _process_wallet_payment(update, context)
    final_price = context.user_data.get("final_price", 0)
    await _show_card_payment(update, context, final_price)
    return SELECT_RECEIPT


async def username_random_callback(update, context):
    """مشتری دکمه ساخت نام کاربری رندوم را زده و ترجیح می‌دهد ربات خودش یک نام کاربری بسازد."""
    query = update.callback_query
    await query.answer("🎲 نام کاربری رندوم انتخاب شد")
    context.user_data["desired_username"] = None
    next_action = context.user_data.get("post_username_action")
    if next_action == "wallet":
        return await _process_wallet_payment(update, context)
    final_price = context.user_data.get("final_price", 0)
    await _show_card_payment(query, context, final_price)
    return SELECT_RECEIPT


async def flow_cancel_callback(update, context):
    query = update.callback_query
    try:
        await query.answer("لغو شد")
    except Exception:
        pass
    context.user_data.pop("selected_package_id", None)
    context.user_data.pop("final_price", None)
    context.user_data.pop("discount_code", None)
    context.user_data.pop("wallet_topup_amount", None)
    context.user_data.pop("reply_ticket_id", None)
    context.user_data.pop("custom_builder", None)
    context.user_data.pop("custom_gb", None)
    context.user_data.pop("custom_days", None)
    try:
        await query.message.delete()
    except Exception:
        pass
    await send_main_menu(query.message.chat_id, context, query.from_user.id)
    return ConversationHandler.END


async def discount_have_callback(update, context):
    query = update.callback_query
    await query.answer()
    keyboard = InlineKeyboardMarkup([[ibtn("🔙 انصراف", callback_data="flow_cancel", style="danger")]])
    await query.edit_message_text("🎟️ <b>کد تخفیف را وارد کنید:</b>", parse_mode="HTML", reply_markup=keyboard)
    return DISCOUNT_INPUT



async def receive_discount_code(update, context):
    if await redirect_if_menu_button(update, context):
        return ConversationHandler.END
    code = (update.effective_message.text or "").strip().upper()
    package, custom_gb, custom_days = await _resolve_order_package(context)
    if not package and not (custom_gb and custom_days):
        await update.message.reply_text("⚠️ <b>این پلن دیگر موجود نیست. لطفا دوباره از منو شروع کنید.</b>", parse_mode="HTML")
        return ConversationHandler.END
    discount = await database.get_discount_code(code)
    if not discount or not discount["active"]:
        await update.message.reply_text("❌ کد نامعتبر. دوباره وارد کنید یا /skip بزنید.")
        return DISCOUNT_INPUT
    if discount["expires_at"]:
        try:
            if datetime.datetime.utcnow() > datetime.datetime.fromisoformat(discount["expires_at"]):
                await update.message.reply_text("❌ کد منقضی شده. /skip بزنید.")
                return DISCOUNT_INPUT
        except Exception:
            pass
    if discount["max_uses"] and discount["used_count"] >= discount["max_uses"]:
        await update.message.reply_text("❌ این کد تمام شده. /skip بزنید.")
        return DISCOUNT_INPUT
    price = package["price"] if package else context.user_data.get("final_price", 0)
    if discount["kind"] == "percent":
        final_price = int(price - (price * discount["value"] / 100))
    else:
        final_price = int(max(price - discount["value"], 0))
    context.user_data["discount_code"] = code
    await update.message.reply_text(f"✅ <b>کد اعمال شد!</b> قیمت جدید: {final_price:,} تومان", parse_mode="HTML")
    display_package = package or {"name": "🚀 کانفیگ اختصاصی VIP", "gb": custom_gb, "days": custom_days}
    await _show_payment_choice(update, context, final_price, package=display_package, show_discount_option=False)
    return CHOOSE_DISCOUNT


async def skip_discount_command(update, context):
    package, custom_gb, custom_days = await _resolve_order_package(context)
    if not package and not (custom_gb and custom_days):
        await update.message.reply_text("⚠️ <b>این پلن دیگر موجود نیست. لطفا دوباره از منو شروع کنید.</b>", parse_mode="HTML")
        return ConversationHandler.END
    context.user_data["discount_code"] = None
    price = package["price"] if package else context.user_data.get("final_price", 0)
    display_package = package or {"name": "🚀 کانفیگ اختصاصی VIP", "gb": custom_gb, "days": custom_days}
    await _show_payment_choice(update, context, price, package=display_package, show_discount_option=True)
    return CHOOSE_DISCOUNT

async def receive_receipt(update, context):
    package, custom_gb, custom_days = await _resolve_order_package(context)
    package_id = context.user_data.get("selected_package_id")
    if not package and not (custom_gb and custom_days):
        await update.message.reply_text("⚠️ <b>خطا.</b> لطفا دوباره از منو شروع کنید.", parse_mode="HTML")
        return ConversationHandler.END
    final_price = context.user_data.get("final_price")
    discount_code = context.user_data.get("discount_code")
    photo = update.message.photo[-1]
    order_id = await database.create_order(
        update.effective_user.id, package_id, photo.file_id, price=final_price, discount_code=discount_code,
        custom_gb=custom_gb, custom_days=custom_days,
    )
    if not package:
        package = {"name": "🚀 کانفیگ اختصاصی VIP", "gb": custom_gb, "days": custom_days}
    await update.message.reply_text(
        "📤 <b>رسید شما با موفقیت ثبت شد!</b>\n"
        "✨ همین الان برای بررسی برای تیم ما ارسال شد.\n"
        "⏱️ <b>پرداخت های ما در سریع‌ترین زمان ممکن بررسی و تایید می‌شوند.</b>",
        parse_mode="HTML",
    )
    caption = (
        f"🧾 <b>سفارش جدید #{order_id}</b>\n"
        "━━━━━━━━━━━\n"
        f"👤 <b>کاربر:</b> {update.effective_user.mention_html()} (<code>{update.effective_user.id}</code>)\n"
        f"📦 <b>پلن:</b> {package['name']} — {package['gb']}گیگ/{package['days']}روز\n"
        f"💰 <b>مبلغ:</b> {final_price:,} تومان" + (f" (🎟️کد: {discount_code})" if discount_code else "")
    )
    keyboard = InlineKeyboardMarkup([[
        ibtn("✅ تایید", callback_data=f"order_approve:{order_id}", style="success"),
        ibtn("❌ رد", callback_data=f"order_reject_choice:{order_id}", style="danger"),
    ]])
    admin_ids = {config.ADMIN_ID} | {a["user_id"] for a in await database.list_admins()}
    for admin_id in admin_ids:
        try:
            await context.bot.send_photo(chat_id=admin_id, photo=photo.file_id, caption=caption, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            pass
    context.user_data.pop("selected_package_id", None)
    context.user_data.pop("final_price", None)
    context.user_data.pop("discount_code", None)
    context.user_data.pop("custom_builder", None)
    context.user_data.pop("custom_gb", None)
    context.user_data.pop("custom_days", None)
    return ConversationHandler.END


async def cancel_purchase(update, context):
    context.user_data.pop("selected_package_id", None)
    context.user_data.pop("final_price", None)
    context.user_data.pop("discount_code", None)
    context.user_data.pop("custom_builder", None)
    context.user_data.pop("custom_gb", None)
    context.user_data.pop("custom_days", None)
    await update.message.reply_text("🚫 <b>خرید لغو شد.</b>", parse_mode="HTML")
    await send_main_menu(update.effective_chat.id, context, update.effective_user.id)
    return ConversationHandler.END


async def _post_trust_channel(context, buyer_user, service_username, package, final_price, expire_ts):
    try:
        buyer_name = buyer_user.full_name if hasattr(buyer_user, "full_name") else str(buyer_user.get("id"))
        buyer_id = buyer_user.id if hasattr(buyer_user, "id") else buyer_user.get("id")
        if expire_ts:
            expire_date = datetime.datetime.fromtimestamp(expire_ts).strftime("%Y-%m-%d")
        else:
            expire_date = (datetime.datetime.now() + datetime.timedelta(days=package["days"])).strftime("%Y-%m-%d")
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        text = (
            "🛍️ <b>خرید جدید!</b>\n"
            "━━━━━━━━━━━\n"
            f"👤 <b>مشتری:</b> {html.escape(str(buyer_name))}\n"
            f"🆔 <b>Telegram ID:</b> <code>{_mask_user_id(buyer_id)}</code>\n"
            f"👤 <b>نام سرویس:</b> {html.escape(str(service_username))}\n"
            f"📦 <b>بسته:</b> {package['gb']} گیگابایت | {package['days']} روز\n"
            f"💰 <b>مبلغ:</b> {final_price:,} تومان\n"
            f"📅 <b>انقضا:</b> {expire_date}\n"
            f"⏰ <b>زمان:</b> {now_str}\n\n"
            "✅ <b>سرویس مشتری با موفقیت و در سریع‌ترین زمان ممکن فعال شد.</b>\n"
            "💎 به جمع صدها مشتری راضی ما بپیوندید و همین حالا سرویس اختصاصی خودتان را تهیه کنید! 🚀"
        )
        await context.bot.send_message(chat_id=TRUST_CHANNEL, text=text, parse_mode="HTML")
    except Exception as error:
        try:
            await database.log_action("error", None, f"خطا در ارسال به کانال اعتماد: {error}")
        except Exception:
            pass


async def _fulfill_order(order, context):
    if order.get("custom_gb") is not None:
        package = {"name": "🚀 کانفیگ اختصاصی VIP", "gb": order["custom_gb"], "days": order["custom_days"], "price": order.get("price") or 0}
    else:
        package = await database.get_package(order["package_id"])
    buyer_id = order["user_id"]
    try:
        buyer_chat_for_note = await context.bot.get_chat(buyer_id)
        note_id = f"@{buyer_chat_for_note.username}" if buyer_chat_for_note.username else str(buyer_id)
    except Exception:
        note_id = str(buyer_id)
    note = f"این سرویس خریداری‌شده مربوط به ایدی {note_id} است"
    desired_username = order.get("desired_username") or None
    user_data = await marzban_api.create_user(package["gb"], package["days"], username=desired_username, note=note)
    link = marzban_api.extract_subscription_link(user_data)
    service_username = user_data.get("username", "")
    await database.update_order(order["id"], status="approved", marzban_username=service_username, subscription_link=link)
    if order.get("discount_code"):
        await database.increment_discount_usage(order["discount_code"])
    qr_bytes = qr_util.make_qr_bytes(link)
    caption = (
        "✅ <b>سرویس با موفقیت ایجاد شد</b>\n"
        "━━━━━━━━━━━\n"
        f"👤 <b>نام کاربری سرویس:</b> <code>{service_username}</code>\n"
        f"📡 <b>نوع:</b> {package['name']}\n"
        f"⏳ <b>مدت زمان:</b> {package['days']} روز\n"
        f"🗜 <b>حجم سرویس:</b> {package['gb']} گیگ\n"
        "👤 <b>تعداد کاربر:</b> نامحدود\n\n"
        f"🔗 <b>لینک اتصال:</b>\n<code>{link}</code>\n\n"
        "👌 از همکاری شما متشکریم! 🙏"
    )
    await context.bot.send_photo(chat_id=order["user_id"], photo=qr_bytes, caption=caption, parse_mode="HTML")
    try:
        buyer_chat = await context.bot.get_chat(order["user_id"])
    except Exception:
        buyer_chat = type("Obj", (), {"id": order["user_id"], "full_name": str(order["user_id"])})()
    await _post_trust_channel(context, buyer_chat, user_data.get("username", ""), package, order.get("price") or package["price"], user_data.get("expire"))


async def order_approve_callback(update, context):
    query = update.callback_query
    if not await require_perm(query, query.from_user.id, "orders"):
        return
    order_id = int(query.data.split(":")[1])
    order = await database.get_order(order_id)
    if not order or order["status"] != "pending":
        await query.answer("این سفارش قبلاً بررسی شده.", show_alert=True)
        return
    await query.answer("⚙️ در حال پردازش...")
    try:
        await _fulfill_order(order, context)
        old_caption = query.message.caption or ""
        await query.edit_message_caption(caption=old_caption + "\n\n✅ <b>تایید شد.</b>", parse_mode="HTML")
        await database.log_action("admin", query.from_user.id, f"تایید سفارش #{order_id}")
    except Exception as error:
        old_caption = query.message.caption or ""
        await query.edit_message_caption(caption=old_caption + f"\n\n❌ <b>خطا در ساخت سرویس:</b>\n{html.escape(str(error))}", parse_mode="HTML")
        try:
            await context.bot.send_message(chat_id=config.ADMIN_ID, text=f"⚠️ خطا در پردازش سفارش {order_id}:\n{html.escape(str(error))}", parse_mode="HTML")
        except Exception:
            pass


async def order_reject_choice_callback(update, context):
    """وقتی ادمین دکمه «❌ رد» را می‌زند، اول می‌پرسیم با دلیل یا بدون دلیل رد کند."""
    query = update.callback_query
    if not await require_perm(query, query.from_user.id, "orders"):
        return
    order_id = int(query.data.split(":")[1])
    order = await database.get_order(order_id)
    if not order or order["status"] != "pending":
        await query.answer("این سفارش قبلاً بررسی شده.", show_alert=True)
        return
    await query.answer()
    keyboard = InlineKeyboardMarkup([[
        ibtn("❌ رد بدون دلیل", callback_data=f"order_reject_now:{order_id}", style="danger"),
        ibtn("📝 رد با ذکر دلیل", callback_data=f"order_reject_reason:{order_id}", style="primary"),
    ]])
    try:
        await query.edit_message_reply_markup(reply_markup=keyboard)
    except Exception:
        pass


async def _do_reject_order(query, context, order_id, order, reason=None):
    await database.update_order(order_id, status="rejected", reject_reason=reason)
    suffix = "\n\n❌ <b>رد شد.</b>"
    if reason:
        suffix += f"\n📝 <b>دلیل:</b> {html.escape(reason)}"
    old_caption = query.message.caption or ""
    await query.edit_message_caption(caption=old_caption + suffix, parse_mode="HTML")
    await database.log_action("admin", query.from_user.id, f"رد سفارش #{order_id}")
    customer_text = "❌ <b>متاسفانه رسید پرداخت شما تایید نشد.</b>"
    if reason:
        customer_text += f"\n📝 <b>دلیل رد:</b> {html.escape(reason)}"
    customer_text += "\nبا پشتیبانی در ارتباط باشید."
    try:
        await context.bot.send_message(chat_id=order["user_id"], text=customer_text, parse_mode="HTML")
    except Exception:
        pass


async def order_reject_now_callback(update, context):
    query = update.callback_query
    if not await require_perm(query, query.from_user.id, "orders"):
        return
    order_id = int(query.data.split(":")[1])
    order = await database.get_order(order_id)
    if not order or order["status"] != "pending":
        await query.answer("این سفارش قبلاً بررسی شده.", show_alert=True)
        return
    await query.answer("رد شد.")
    await _do_reject_order(query, context, order_id, order, reason=None)


async def order_reject_reason_start(update, context):
    query = update.callback_query
    if not await require_perm(query, query.from_user.id, "orders"):
        return ConversationHandler.END
    order_id = int(query.data.split(":")[1])
    order = await database.get_order(order_id)
    if not order or order["status"] != "pending":
        await query.answer("این سفارش قبلاً بررسی شده.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    context.user_data["reject_order_id"] = order_id
    await query.message.reply_text(
        "📝 لطفا دلیل رد کردن را بنویسید (برای مشتری ارسال می‌شود):",
        reply_markup=InlineKeyboardMarkup([[ibtn("🔙 انصراف", callback_data="reject_reason_cancel", style="danger")]]),
    )
    return REJECT_REASON_INPUT


async def receive_reject_reason(update, context):
    order_id = context.user_data.get("reject_order_id")
    reason = (update.effective_message.text or "").strip()
    if not order_id or not reason:
        await update.message.reply_text("❌ متن خالی معتبر نیست. دوباره دلیل رد را بنویسید:")
        return REJECT_REASON_INPUT
    order = await database.get_order(order_id)
    if not order or order["status"] != "pending":
        await update.message.reply_text("این سفارش قبلاً بررسی شده.")
        context.user_data.pop("reject_order_id", None)
        return ConversationHandler.END
    await database.update_order(order_id, status="rejected", reject_reason=reason)
    await database.log_action("admin", update.effective_user.id, f"رد سفارش #{order_id} با دلیل")
    try:
        await context.bot.send_message(
            chat_id=order["user_id"],
            text=f"❌ <b>متاسفانه رسید پرداخت شما تایید نشد.</b>\n📝 <b>دلیل رد:</b> {html.escape(reason)}\nبا پشتیبانی در ارتباط باشید.",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await update.message.reply_text("✅ سفارش رد شد و دلیل برای مشتری ارسال شد.")
    context.user_data.pop("reject_order_id", None)
    return ConversationHandler.END


async def reject_reason_cancel_callback(update, context):
    query = update.callback_query
    await query.answer("لغو شد")
    context.user_data.pop("reject_order_id", None)
    try:
        await query.message.delete()
    except Exception:
        pass
    return ConversationHandler.END


async def order_reject_callback(update, context):
    query = update.callback_query
    if not await require_perm(query, query.from_user.id, "orders"):
        return
    order_id = int(query.data.split(":")[1])
    order = await database.get_order(order_id)
    if not order or order["status"] != "pending":
        await query.answer("این سفارش قبلاً بررسی شده.", show_alert=True)
        return
    await query.answer("رد شد.")
    await _do_reject_order(query, context, order_id, order, reason=None)


async def trial_run(update, context):
    user_id = update.effective_user.id
    if await database.get_setting("trial_enabled", "0") != "1":
        await update.message.reply_text("⚠️ <b>در حال حاضر تست رایگان فعال نیست.</b>", parse_mode="HTML")
        return
    if await database.has_used_trial(user_id):
        await update.message.reply_text("ℹ️ <b>شما قبلاً از تست رایگان استفاده کرده‌اید.</b>", parse_mode="HTML")
        return
    await update.message.reply_text("⏳ <b>در حال ساخت سرویس...</b>", parse_mode="HTML")
    try:
        gb = float(await database.get_setting("trial_gb", "1"))
    except (TypeError, ValueError):
        gb = 1.0
    try:
        days = int(float(await database.get_setting("trial_days", "1")))
    except (TypeError, ValueError):
        days = 1
    try:
        try:
            trial_chat_for_note = await context.bot.get_chat(user_id)
            note_id = f"@{trial_chat_for_note.username}" if trial_chat_for_note.username else str(user_id)
        except Exception:
            note_id = str(user_id)
        note = f"این سرویس تست مربوط به ایدی {note_id} است"
        user_data = await marzban_api.create_user(gb, days, note=note)
        link = marzban_api.extract_subscription_link(user_data)
        service_username = user_data.get("username", "")
        order_id = await database.create_order(user_id, None, None, price=0, order_type="trial")
        await database.update_order(order_id, status="approved", marzban_username=service_username, subscription_link=link)
        await database.mark_trial_used(user_id)
        qr_bytes = qr_util.make_qr_bytes(link)
        caption = (
            "✅ <b>سرویس با موفقیت ایجاد شد</b>\n"
            "━━━━━━━━━━━\n"
            f"👤 <b>نام کاربری سرویس:</b> <code>{service_username}</code>\n"
            "📡 <b>نوع:</b> 🎁 تست رایگان\n"
            f"⏳ <b>مدت زمان:</b> {days} روز\n"
            f"🗜 <b>حجم سرویس:</b> {gb} گیگ\n"
            "👤 <b>تعداد کاربر:</b> نامحدود\n\n"
            f"🔗 <b>لینک اتصال:</b>\n<code>{link}</code>"
        )
        await context.bot.send_photo(chat_id=user_id, photo=qr_bytes, caption=caption, parse_mode="HTML")
    except Exception as error:
        await update.message.reply_text("❌ <b>متاسفانه امکان ساخت سرویس وجود ندارد. بعداً تلاش کنید.</b>", parse_mode="HTML")
        try:
            await context.bot.send_message(chat_id=config.ADMIN_ID, text=f"⚠️ خطا در ساخت تست رایگان برای {user_id}:\n{html.escape(str(error))}", parse_mode="HTML")
        except Exception:
            pass


async def help_show(update, context):
    support_username = await database.get_setting("support_username", "")
    support_line = f"\n🆘 <b>پشتیبانی:</b> @{support_username.lstrip('@')}\n" if support_username else ""
    text = (
        "❓ <b>راهنمای کامل استفاده از ربات</b>\n"
        "━━━━━━━━━━━\n\n"
        "🛒 <b>خرید اشتراک</b>\n"
        "از دکمه «خرید اشتراک» یکی از پلن‌ها را انتخاب کنید، اگر کد تخفیف دارید وارد کنید یا رد کنید، سپس مبلغ را کارت به کارت کنید یا از 💰 <b>کیف پول</b> تون پرداخت کنید.\n"
        "بعد از کارت به کارت، فقط کافیست عکس رسید واریز را ارسال کنید.\n"
        "⚡️ <b>پرداخت های ما در سریع‌ترین زمان ممکن بررسی و تایید می‌شوند</b> و بلافاصله سرویس شما فعال می‌شود.\n\n"
        "🎁 <b>تست رایگان</b>\n"
        "یک‌بار و فقط یک‌بار می‌توانید از دکمه تست رایگان استفاده کنید تا کیفیت سرویس را بسنجید.\n\n"
        "📱 <b>حساب کاربری</b>\n"
        "در این بخش لینک های اشتراک فعال خودتان را می بینید.\n\n"
        "💰 <b>کیف پول</b>\n"
        "می‌توانید از قبل شارژ کنید تا در لحظه خرید، بدون نیاز به ارسال رسید، سریع پرداخت کنید.\n\n"
        f"{support_line}"
        "\n✨ هر سوالی داشتید از پشتیبانی بپرسید. 💜"
    )
    await update.message.reply_text(text, parse_mode="HTML")

def _account_list_text_and_keyboard(approved):
    text = (
        "📱 <b>سرویس‌های من</b>\n"
        "━━━━━━━━━━━\n"
        "در ادامه لیست سرویس‌های خریداری‌شده و تست رایگان فعال شما آمده است.\n"
        "👇 برای مشاهده جزئیات، لینک اشتراک و مدیریت هر سرویس، روی نام آن ضربه بزنید."
    )
    rows = []
    for o in approved:
        username = o.get("marzban_username") or "?"
        if o.get("order_type") == "trial":
            label = f"🎁 تست رایگان — {username}"
        else:
            label = f"✨ {username} ✨"
        rows.append([ibtn(label, callback_data=f"acc_detail:{o['id']}")])
    return text, InlineKeyboardMarkup(rows)


async def account_show(update, context):
    orders = await database.list_orders_by_user(update.effective_user.id)
    approved = [o for o in orders if o["status"] == "approved" and o.get("marzban_username")]
    if not approved:
        text = (
            "👤 <b>سرویس‌های من</b>\n"
            "━━━━━━━━━━━\n"
            "ℹ️ <b>شما هنوز هیچ سرویس فعالی ندارید.</b>\n"
            "🛒 از منوی اصلی یک پلن بخرید یا تست رایگان را امتحان کنید."
        )
        await update.message.reply_text(text, parse_mode="HTML")
        return
    text, keyboard = _account_list_text_and_keyboard(approved)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def account_list_callback(update, context):
    query = update.callback_query
    await query.answer()
    orders = await database.list_orders_by_user(query.from_user.id)
    approved = [o for o in orders if o["status"] == "approved" and o.get("marzban_username")]
    if not approved:
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.message.reply_text("ℹ️ <b>شما هیچ سرویس فعالی ندارید.</b>", parse_mode="HTML")
        return
    text, keyboard = _account_list_text_and_keyboard(approved)
    await safe_edit_or_send(query, text, reply_markup=keyboard)


async def _build_account_detail(order):
    is_trial = order.get("order_type") == "trial"
    package = {} if is_trial else (await database.get_package(order["package_id"]) or {})
    username = order["marzban_username"]
    order_id = order["id"]
    try:
        info = await marzban_api.get_user(username)
    except Exception as e:
        text = (
            f"⚠️ <b>خطا در دریافت اطلاعات زنده سرویس «{username}»</b>\n"
            f"جزئیات: <code>{e}</code>"
        )
        keyboard = InlineKeyboardMarkup([
            [ibtn("♻️ بروزرسانی اطلاعات", callback_data=f"acc_refresh:{order_id}")],
            [ibtn("🏠 بازگشت به لیست سرویس‌ها", callback_data="acc_back_list")],
        ])
        return text, keyboard

    is_active = info.get("status") == "active"
    status_icon, status_label = ("✅", "فعال") if is_active else ("❌", "فعال")
    used = info.get("used_traffic") or 0
    limit = info.get("data_limit") or 0
    used_str = marzban_api.format_bytes(used)
    limit_str = marzban_api.format_bytes(limit) if limit else "بی‌محدودیت"
    if limit:
        remaining = max(0, limit - used)
        percent = max(0, min(100, round(remaining / limit * 100)))
        remaining_str = f"{marzban_api.format_bytes(remaining)} ({percent}%)"
    else:
        remaining_str = "بی‌محدودیت"

    expire_ts = info.get("expire")
    expire_str = marzban_api.format_expire(expire_ts)
    countdown = ""
    if expire_ts:
        remaining_seconds = expire_ts - int(time.time())
        if remaining_seconds > 0:
            days = remaining_seconds // 86400
            hours = (remaining_seconds % 86400) // 3600
            minutes = (remaining_seconds % 3600) // 60
            if days > 0:
                countdown = f" ({days} روز دیگر)"
            elif hours > 0:
                countdown = f" ({hours} ساعت دیگر)"
            elif minutes > 0:
                countdown = f" ({minutes} دقیقه دیگر)"
        else:
            countdown = " (منقضی شده)"

    online_at = info.get("online_at")
    last_connection = "متصل نشده ❌" if not online_at else str(online_at).replace("T", " ").split(".")[0]
    sub_updated_at = info.get("sub_updated_at")
    sub_updated_str = "-" if not sub_updated_at else str(sub_updated_at).replace("T", " ").split(".")[0]
    user_agent = info.get("sub_last_user_agent") or "-"

    product_name = "🎁 تست رایگان" if is_trial else package.get("name", "سرویس")
    text = (
        f"{status_icon} <b>وضعیت سرویس:</b> {status_label}\n"
        f"👤 <b>نام سرویس:</b> <code>{username}</code>\n"
        "───────────\n"
        f"📦 <b>نام محصول:</b> {product_name}\n"
        f"🔋 <b>ترافیک کل:</b> {limit_str}\n"
        f"📥 <b>حجم مصرفی:</b> {used_str}\n"
        f"🛛 <b>حجم باقی‌مانده:</b> {remaining_str}\n"
        "───────────\n"
        f"📅 <b>تاریخ اتمام:</b> {expire_str}{countdown}\n"
        f"📶 <b>آخرین زمان اتصال:</b> {last_connection}\n"
        f"🔄 <b>آخرین آپدیت لینک اشتراک:</b> {sub_updated_str}\n"
        f"# <b>کلاینت متصل‌شده:</b> <code>{user_agent}</code>\n\n"
        "💡 برای قطع دسترسی دیگران، از دکمه «تعویض لینک» استفاده کنید."
    )
    toggle_label = "⛔️ خاموش کردن اکانت" if is_active else "✅ روشن کردن اکانت"
    action_rows = [
        [ibtn("♻️ بروزرسانی اطلاعات", callback_data=f"acc_refresh:{order_id}")],
        [ibtn("🔗 لینک اشتراک", callback_data=f"acc_link:{order_id}"), ibtn("📗 دریافت کانفیگ", callback_data=f"acc_config:{order_id}")],
    ]
    if is_trial:
        action_rows.append([ibtn("⚙️ تعویض لینک", callback_data=f"acc_revoke:{order_id}")])
    else:
        action_rows.append([ibtn("💊 تمدید سرویس", callback_data=f"pkg:{package.get('id', 0)}", style="success"), ibtn("⚙️ تعویض لینک", callback_data=f"acc_revoke:{order_id}")])
    action_rows.append([ibtn(toggle_label, callback_data=f"acc_toggle:{order_id}", style="danger" if is_active else "success")])
    action_rows.append([ibtn("🏠 بازگشت به لیست سرویس‌ها", callback_data="acc_back_list")])
    keyboard = InlineKeyboardMarkup(action_rows)
    return text, keyboard


async def _load_order_for_user(query):
    order_id = int(query.data.split(":")[1])
    order = await database.get_order(order_id)
    if not order or order["user_id"] != query.from_user.id or not order.get("marzban_username"):
        await query.answer("⚠️ دسترسی مجاز نیست.", show_alert=True)
        return None
    return order


async def account_detail_callback(update, context):
    query = update.callback_query
    order = await _load_order_for_user(query)
    if not order:
        return
    await query.answer()
    text, keyboard = await _build_account_detail(order)
    await safe_edit_or_send(query, text, reply_markup=keyboard)


async def account_refresh_callback(update, context):
    query = update.callback_query
    order = await _load_order_for_user(query)
    if not order:
        return
    await query.answer("🔄 بروزرسانی شد")
    text, keyboard = await _build_account_detail(order)
    await safe_edit_or_send(query, text, reply_markup=keyboard)


async def account_link_callback(update, context):
    query = update.callback_query
    order = await _load_order_for_user(query)
    if not order:
        return
    await query.answer()
    link = order.get("subscription_link") or "-"
    try:
        await query.message.reply_text(f"🔗 <b>لینک اشتراک شما:</b>\n<code>{link}</code>", parse_mode="HTML")
    except Exception:
        await query.message.reply_text(f"🔗 لینک: {link}")


async def account_config_callback(update, context):
    query = update.callback_query
    order = await _load_order_for_user(query)
    if not order:
        return
    await query.answer()
    username = order["marzban_username"]
    try:
        info = await marzban_api.get_user(username)
        links = info.get("links") or []
        if links:
            parts = ["📗 <b>کانفیگ‌های سرویس شما:</b>"]
            for i, link in enumerate(links, 1):
                parts.append(f"{i}. <code>{link}</code>")
            text = "\n\n".join(parts)
        else:
            sub_link = order.get("subscription_link") or "-"
            text = f"🔗 <b>لینک کانفیگ شما:</b>\n<code>{sub_link}</code>"
        await send_chunked(query.message, text, parse_mode="HTML")
    except Exception as e:
        await query.message.reply_text(f"⚠️ <b>خطا در دریافت کانفیگ:</b> {e}", parse_mode="HTML")


async def account_revoke_callback(update, context):
    query = update.callback_query
    order = await _load_order_for_user(query)
    if not order:
        return
    await query.answer("⏳ در حال تعویض لینک...")
    username = order["marzban_username"]
    try:
        result = await marzban_api.revoke_sub(username)
        new_link = marzban_api.extract_subscription_link(result)
        if new_link:
            await database.update_order(order["id"], subscription_link=new_link)
    except Exception as e:
        await query.message.reply_text(f"⚠️ <b>خطا در تعویض لینک:</b> {e}", parse_mode="HTML")
        return
    order = await database.get_order(order["id"])
    text, keyboard = await _build_account_detail(order)
    text = "✅ <b>لینک اشتراک با موفقیت تعویض شد و دسترسی دستگاه‌های قبلی قطع شد.</b>\n\n" + text
    await safe_edit_or_send(query, text, reply_markup=keyboard)


async def account_toggle_callback(update, context):
    query = update.callback_query
    order = await _load_order_for_user(query)
    if not order:
        return
    await query.answer("⏳ در حال بروزرسانی وضعیت...")
    username = order["marzban_username"]
    try:
        info = await marzban_api.get_user(username)
        is_active = info.get("status") == "active"
        await marzban_api.set_user_status(username, not is_active)
    except Exception as e:
        await query.message.reply_text(f"⚠️ <b>خطا در تعویض وضعیت:</b> {e}", parse_mode="HTML")
        return
    text, keyboard = await _build_account_detail(order)
    await safe_edit_or_send(query, text, reply_markup=keyboard)


MAIN_MENU_ROUTES = {}

# ==================== کیف پول ====================

def _gregorian_to_jalali(gy, gm, gd):
    """تبدیل تاریخ میلادی به شمسی (الگوریتم عمومی بدون وابستگی خارجی)."""
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621
    gy2 = gy + 1 if gm > 2 else gy
    days = (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400) - 80 + gd + g_d_m[gm - 1]
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + days // 31
        jd = 1 + (days % 31)
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + ((days - 186) % 30)
    return jy, jm, jd


_PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"


def _to_persian_digits(text):
    return "".join(_PERSIAN_DIGITS[int(ch)] if ch.isdigit() else ch for ch in str(text))


def _format_jalali_datetime(iso_text):
    """تاریخ ذخیره شده (میلادی، ISO) را به قالب شمسی فارسی تبدیل می‌کند."""
    if not iso_text:
        return "-"
    try:
        cleaned = str(iso_text).replace("T", " ").split(".")[0]
        dt = datetime.datetime.strptime(cleaned[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            dt = datetime.datetime.strptime(str(iso_text)[:10], "%Y-%m-%d")
        except Exception:
            return str(iso_text)
    jy, jm, jd = _gregorian_to_jalali(dt.year, dt.month, dt.day)
    result = f"{jy:04d}/{jm:02d}/{jd:02d} {dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"
    return _to_persian_digits(result)


async def wallet_show(update, context):
    user_id = update.effective_user.id
    balance = await database.get_wallet_balance(user_id)
    user_row = await database.get_user(user_id)
    display_name = update.effective_user.full_name or (update.effective_user.username or str(user_id))
    joined_at = user_row.get("joined_at") if user_row else None
    joined_str = _format_jalali_datetime(joined_at)
    orders = await database.list_orders_by_user(user_id)
    purchased_count = sum(1 for o in orders if o.get("status") == "approved" and o.get("order_type") == "purchase")
    paid_invoices_count = sum(1 for o in orders if o.get("status") == "approved" and (o.get("price") or 0) > 0)
    text = (
        "💰 <b>کیف پول من</b>\n"
        "━━━━━━━━━━━\n"
        f"💵 موجودی فعلی: <b>{balance:,} تومان</b>\n\n"
        "🤖 اطلاعات حساب کاربری شما:\n\n"
        f"🪪 ایدی عددی: <b>{user_id}</b>\n"
        f"👤 نام: {html.escape(display_name)}\n"
        f"⌚️ زمان ثبت‌نام: {joined_str}\n"
        f"🕒 تعداد سرویس‌های خریداری‌شده: {purchased_count} عدد\n"
        f"📑 تعداد فاکتورهای پرداخت‌شده: {paid_invoices_count} عدد\n\n"
        "می‌توانید از موجودی کیف پول برای خرید سرویس هم استفاده کنید."
    )
    keyboard = InlineKeyboardMarkup([
        [ibtn("💳 شارژ کیف پول", callback_data="wallet_topup_start", style="success")],
        [ibtn("📜 تاریخچه تراکنش‌ها", callback_data="wallet_history", style="primary")],
    ])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def wallet_history_callback(update, context):
    query = update.callback_query
    await query.answer()
    txs = await database.list_wallet_transactions(query.from_user.id, limit=15)
    if not txs:
        await query.message.reply_text("📜 هنوز تراکنشی در کیف پول شما ثبت نشده.")
        return
    kind_labels = {
        "topup": "⛳ درخواست شارژ (در انتظار)",
        "topup_approved": "✅ شارژ تایید‌شده",
        "topup_rejected": "❌ شارژ رد‌شده",
        "purchase": "🛒️ خرید از کیف پول",
        "admin_credit": "➕ افزایش توسط ادمین",
        "admin_debit": "➖ کاهش توسط ادمین",
    }
    lines = ["📜 <b>تاریخچه تراکنش‌های کیف پول</b>\n━━━━━━━━━━━"]
    for tx in txs:
        label = kind_labels.get(tx["kind"], tx["kind"])
        sign = "+" if tx["amount"] and tx["amount"] > 0 else ""
        lines.append(f"• {label}: {sign}{tx['amount']:,} تومان")
    await query.message.reply_text("\n".join(lines), parse_mode="HTML")


async def wallet_topup_start(update, context):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("💳 <b>مبلغی که می‌خواهید به کیف پول افزوده شود را به تومان وارد کنید:</b>", parse_mode="HTML", reply_markup=flow_cancel_keyboard())
    return WALLET_TOPUP_AMOUNT


async def receive_wallet_topup_amount(update, context):
    if await redirect_if_menu_button(update, context):
        return ConversationHandler.END
    text = (update.effective_message.text or "").strip().replace(",", "")
    try:
        amount = int(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ لطفا فقط یک عدد مثبت بزرگتر از صفر وارد کنید.")
        return WALLET_TOPUP_AMOUNT
    context.user_data["wallet_topup_amount"] = amount
    card_number = await database.get_setting("card_number", "-")
    card_holder = await database.get_setting("card_holder", "-")
    await update.message.reply_text(
        f"💵 مبلغ: <b>{amount:,} تومان</b>\n\n"
        f"💳 شماره کارت: <code>{card_number}</code>\n👤 به نام: {card_holder}\n\n"
        "📸 <b>لطفا بعد از واریز، عکس رسید را همینجا ارسال کنید.</b>",
        parse_mode="HTML",
    )
    return WALLET_TOPUP_RECEIPT


async def receive_wallet_topup_receipt(update, context):
    amount = context.user_data.get("wallet_topup_amount")
    if not amount:
        await update.message.reply_text("⚠️ خطا رخ داد. لطفا دوباره از منو شروع کنید.")
        return ConversationHandler.END
    photo = update.message.photo[-1]
    tx_id = await database.create_wallet_topup(update.effective_user.id, amount, photo.file_id)
    await update.message.reply_text(
        "✅ <b>رسید شارژ کیف پول ثبت شد!</b>\n⏳ به محض تایید ادمین، موجودی به کیف پولتان افزوده می‌شود.",
        parse_mode="HTML",
    )
    caption = (
        "💳 <b>درخواست شارژ کیف پول</b> #" + str(tx_id) + "\n"
        "━━━━━━━━━━━\n"
        f"👤 کاربر: {update.effective_user.mention_html()}\n"
        f"🆔 آیدی: <code>{update.effective_user.id}</code>\n"
        f"💵 مبلغ: {amount:,} تومان"
    )
    keyboard = InlineKeyboardMarkup([[
        ibtn("✅ تایید", callback_data=f"wallet_topup_approve:{tx_id}", style="success"),
        ibtn("❌ رد", callback_data=f"wallet_topup_reject:{tx_id}", style="danger"),
    ]])
    admin_ids = {config.ADMIN_ID} | {a["user_id"] for a in await database.list_admins()}
    for admin_id in admin_ids:
        try:
            await context.bot.send_photo(chat_id=admin_id, photo=photo.file_id, caption=caption, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            pass
    context.user_data.pop("wallet_topup_amount", None)
    return ConversationHandler.END


async def wallet_topup_cancel(update, context):
    context.user_data.pop("wallet_topup_amount", None)
    await update.message.reply_text("شارژ کیف پول لغو شد.")
    return ConversationHandler.END


async def wallet_topup_approve_callback(update, context):
    query = update.callback_query
    if not await require_perm(query, query.from_user.id, "finance"):
        return
    tx_id = int(query.data.split(":")[1])
    tx = await database.get_wallet_transaction(tx_id)
    if not tx or tx["status"] != "pending":
        await query.answer("این درخواست قبلاً بررسی شده.", show_alert=True)
        return
    await query.answer("در حال انجام...")
    await database.update_wallet_transaction(tx_id, status="approved", kind="topup_approved")
    new_balance = await database.adjust_wallet_balance(tx["user_id"], tx["amount"])
    old_caption = query.message.caption or ""
    await query.edit_message_caption(caption=old_caption + "\n\n✅ تایید شد.", parse_mode="HTML")
    await database.log_action("admin", query.from_user.id, f"تایید شارژ کیف پول #{tx_id}")
    try:
        await context.bot.send_message(
            chat_id=tx["user_id"],
            text=f"✅ شارژ کیف پول شما به مبلغ {tx['amount']:,} تومان تایید شد. 🎉\n💰 موجودی جدید: {new_balance:,} تومان",
        )
    except Exception:
        pass


async def wallet_topup_reject_callback(update, context):
    query = update.callback_query
    if not await require_perm(query, query.from_user.id, "finance"):
        return
    tx_id = int(query.data.split(":")[1])
    tx = await database.get_wallet_transaction(tx_id)
    if not tx or tx["status"] != "pending":
        await query.answer("این درخواست قبلاً بررسی شده.", show_alert=True)
        return
    await database.update_wallet_transaction(tx_id, status="rejected", kind="topup_rejected")
    await query.answer("رد شد.")
    old_caption = query.message.caption or ""
    await query.edit_message_caption(caption=old_caption + "\n\n❌ رد شد.", parse_mode="HTML")
    await database.log_action("admin", query.from_user.id, f"رد شارژ کیف پول #{tx_id}")
    try:
        await context.bot.send_message(chat_id=tx["user_id"], text="❌ متاسفانه رسید شارژ کیف پول شما تایید نشد. با پشتیبانی در ارتباط باشید.")
    except Exception:
        pass

async def _process_wallet_payment(update, context):
    query = update.callback_query
    user_id = query.from_user.id if query else update.effective_user.id

    async def _reply(text, **kwargs):
        if query:
            await query.message.reply_text(text, **kwargs)
        else:
            await update.message.reply_text(text, **kwargs)

    package, custom_gb, custom_days = await _resolve_order_package(context)
    package_id = context.user_data.get("selected_package_id")
    is_custom = custom_gb is not None and custom_days is not None
    if not package and not is_custom:
        if query:
            await query.answer("⚠️ خطا. لطفا دوباره از منو شروع کنید.", show_alert=True)
        return ConversationHandler.END
    final_price = context.user_data.get("final_price")
    if final_price is None:
        final_price = package["price"] if package else 0
    discount_code = context.user_data.get("discount_code")
    desired_username = context.user_data.get("desired_username")
    balance = await database.get_wallet_balance(user_id)
    if balance < final_price:
        if query:
            await query.answer()
        await _reply(
            f"⚠️ <b>موجودی کیف پول کافی نیست.</b>\n💰 موجودی فعلی: {balance:,} تومان\n🧾 مبلغ مورد نیاز: {final_price:,} تومان\n\nلطفا ابتدا کیف پولتان را از منوی «کیف پول» شارژ کنید.",
            parse_mode="HTML",
        )
        return ConversationHandler.END
    if query:
        await query.answer("⏳ در حال پردازش...")
    else:
        await _reply("⏳ <b>در حال پردازش و ساخت سرویس...</b>", parse_mode="HTML")
    order_id = await database.create_order(
        user_id, package_id, None, price=final_price, discount_code=discount_code,
        order_type="purchase", custom_gb=custom_gb, custom_days=custom_days, desired_username=desired_username,
    )
    await database.adjust_wallet_balance(user_id, -final_price)
    await database.add_wallet_transaction(user_id, -final_price, "purchase", related_order_id=order_id)
    order = await database.get_order(order_id)
    try:
        await _fulfill_order(order, context)
        await _reply("✅ <b>خرید با موفقیت از کیف پول انجام شد و سرویس فعال گردید!</b>", parse_mode="HTML")
        await database.log_action("purchase", user_id, f"خرید با کیف پول #{order_id}")
    except Exception as error:
        await database.adjust_wallet_balance(user_id, final_price)
        await database.add_wallet_transaction(user_id, final_price, "admin_credit", related_order_id=order_id)
        await database.update_order(order_id, status="rejected")
        await _reply(
            f"❌ <b>متاسفانه در ساخت سرویس خطایی رخ داد، مبلغ به کیف پول شما برگشت.</b>\nخطا: <code>{html.escape(str(error))}</code>",
            parse_mode="HTML",
        )
        try:
            await context.bot.send_message(chat_id=config.ADMIN_ID, text=f"⚠️ خطا در پردازش سفارش کیف پول {order_id}:\n{html.escape(str(error))}", parse_mode="HTML")
        except Exception:
            pass
    context.user_data.pop("selected_package_id", None)
    context.user_data.pop("final_price", None)
    context.user_data.pop("discount_code", None)
    context.user_data.pop("custom_builder", None)
    context.user_data.pop("custom_gb", None)
    context.user_data.pop("custom_days", None)
    context.user_data.pop("desired_username", None)
    context.user_data.pop("post_username_action", None)
    return ConversationHandler.END


async def pay_with_wallet_callback(update, context):
    return await _process_wallet_payment(update, context)
