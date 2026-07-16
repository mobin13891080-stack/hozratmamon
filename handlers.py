# -*- coding: utf-8 -*-
# handlers.py - هندلرهای اصلی: منو، خرید، تست، حساب من، پشتیبانی/تیکت، پلن‌ها، تنطیمات، ادمین‌ها

import datetime
import json

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
    """ساخت دکمه رنگی منوی ثابت (ReplyKeyboard)."""
    return KeyboardButton(text, api_kwargs={"style": style})

CHOOSE_DISCOUNT, DISCOUNT_INPUT, SELECT_RECEIPT = range(100, 103)
AWAITING_ADMIN_INPUT = 200
TICKET_USER_MESSAGE, TICKET_ADMIN_REPLY = range(300, 302)

BTN_BUY = "🛍️ خرید سرویس"
BTN_TRIAL = "🎁 تست رایگان"
BTN_ACCOUNT = "👤 سرویس‌های من"
BTN_SUPPORT = "🆘 پشتیبانی"
BTN_ADMIN_PANEL = "⚙️ پنل مدیریت"

BTN_ADMIN_USERS = "👥 مدیریت کاربران"
BTN_ADMIN_ORDERS = "🧾 مدیریت سفارش‌ها"
BTN_ADMIN_PACKAGES = "📦 مدیریت پلن‌ها"
BTN_ADMIN_DISCOUNTS = "🎟️ کدهای تخفیف"
BTN_ADMIN_SERVERS = "🖥️ مدیریت سرورها"
BTN_ADMIN_FINANCE = "💰 مدیریت مالی"
BTN_ADMIN_NOTIFY = "📢 اطلاع‌رسانی"
BTN_ADMIN_TICKETS = "🎧 تیکت‌های پشتیبانی"
BTN_ADMIN_STATS = "📊 آمار ربات"
BTN_ADMIN_ADMINS = "👮 مدیریت ادمین‌ها"
BTN_ADMIN_LOGS = "📜 لاگ‌ها"
BTN_ADMIN_CARD = "💳 شماره کارت"
BTN_ADMIN_FORCEJOIN = "🔒 عضویت اجباری"
BTN_ADMIN_TRIAL = "🎁 تنطیمات تست رایگان"
BTN_ADMIN_RULES = "📜 قوانین و لینک‌ها"
BTN_ADMIN_WELCOME = "📝 متن خوش‌آمدگویی"
BTN_ADMIN_WELCOME_PHOTO = "🖼️ عکس خوش‌آمدگویی"
BTN_ADMIN_SUPPORT_SETTINGS = "🆘 تنطیمات پشتیبانی"
BTN_ADMIN_TURN_OFF = "🔴 خاموش کردن ربات"
BTN_ADMIN_TURN_ON = "🟢 روشن کردن ربات"
BTN_BACK_MAIN = "🔙 بازگشت به منوی اصلی"

BUTTON_PERMISSION = {
    BTN_ADMIN_USERS: "users", BTN_ADMIN_ORDERS: "orders", BTN_ADMIN_PACKAGES: "packages",
    BTN_ADMIN_DISCOUNTS: "discounts", BTN_ADMIN_SERVERS: "servers", BTN_ADMIN_FINANCE: "finance",
    BTN_ADMIN_NOTIFY: "notify", BTN_ADMIN_TICKETS: "tickets", BTN_ADMIN_STATS: "stats",
    BTN_ADMIN_ADMINS: "admins", BTN_ADMIN_LOGS: "logs", BTN_ADMIN_CARD: "settings",
    BTN_ADMIN_FORCEJOIN: "settings", BTN_ADMIN_TRIAL: "settings", BTN_ADMIN_RULES: "settings",
    BTN_ADMIN_WELCOME: "settings", BTN_ADMIN_WELCOME_PHOTO: "settings", BTN_ADMIN_SUPPORT_SETTINGS: "settings",
}

ADMIN_MENU_ROWS = [
    [BTN_ADMIN_USERS, BTN_ADMIN_ORDERS],
    [BTN_ADMIN_PACKAGES, BTN_ADMIN_DISCOUNTS],
    [BTN_ADMIN_SERVERS, BTN_ADMIN_FINANCE],
    [BTN_ADMIN_NOTIFY, BTN_ADMIN_TICKETS],
    [BTN_ADMIN_STATS, BTN_ADMIN_ADMINS],
    [BTN_ADMIN_LOGS],
    [BTN_ADMIN_CARD, BTN_ADMIN_FORCEJOIN],
    [BTN_ADMIN_TRIAL, BTN_ADMIN_RULES],
    [BTN_ADMIN_WELCOME, BTN_ADMIN_WELCOME_PHOTO],
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
}


def main_menu_keyboard(is_admin_user):
    rows = [[kbtn(BTN_BUY), kbtn(BTN_TRIAL)], [kbtn(BTN_ACCOUNT), kbtn(BTN_SUPPORT)]]
    if is_admin_user:
        rows.append([kbtn(BTN_ADMIN_PANEL)])
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


async def send_main_menu(chat_id, context, user_id):
    welcome_text = await database.get_setting("welcome_text", "خوش آمدید")
    photo_id = await database.get_setting("welcome_photo_id", "")
    is_admin_user = await database.is_admin(user_id)
    keyboard = main_menu_keyboard(is_admin_user)
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
        return member.status in ("member", "administrator", "creator")
    except Exception:
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


async def start(update, context):
    user = update.effective_user
    await database.upsert_user(user.id, user.username or "")
    if not await ensure_bot_active(update, context, user.id):
        return
    if not await database.is_admin(user.id):
        missing = await get_missing_channels(context, user.id)
        if missing:
            await show_join_gate(update.effective_chat.id, context, missing)
            return
    await send_main_menu(update.effective_chat.id, context, user.id)


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

async def buy_show_packages(update, context):
    packages = await database.list_packages(active_only=True)
    if not packages:
        await update.message.reply_text("در حال حاضر پلنی برای فروش وجود ندارد.")
        return
    keyboard = [[ibtn(
        f"📦 {p['name']} — {p['gb']}گیگ/{p['days']}روز — {p['price']:,}ت", callback_data=f"pkg:{p['id']}", style="primary"
    )] for p in packages]
    await update.message.reply_text("🛍️ یکی از پلن‌های زیر را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard))


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
    await query.answer()
    keyboard = InlineKeyboardMarkup([
        [ibtn("🎟️ کد تخفیف دارم", callback_data="discount_have", style="primary")],
        [ibtn("✅ ادامه بدون تخفیف", callback_data="discount_skip", style="success")],
    ])
    await query.edit_message_text(
        f"📦 پلن: {package['name']} ({package['gb']}گیگ/{package['days']}روز)\n💵 قیمت: {package['price']:,} تومان\n\nآیا کد تخفیف دارید؟",
        reply_markup=keyboard,
    )
    return CHOOSE_DISCOUNT


async def _show_payment_step(update_or_query, context, final_price):
    context.user_data["final_price"] = final_price
    card_number = await database.get_setting("card_number", "-")
    card_holder = await database.get_setting("card_holder", "-")
    text = (
        f"💵 مبلغ قابل پرداخت: {final_price:,} تومان\n\n"
        f"💳 شماره کارت: {card_number}\n👤 به نام: {card_holder}\n\n"
        "لطفا بعد از واریز، تصویر رسید پرداخت را همینجا ارسال کنید. (لغو: /cancel)"
    )
    if hasattr(update_or_query, "edit_message_text"):
        await update_or_query.edit_message_text(text)
    else:
        await update_or_query.message.reply_text(text)


async def discount_skip_callback(update, context):
    query = update.callback_query
    await query.answer()
    package = await database.get_package(context.user_data.get("selected_package_id"))
    context.user_data["discount_code"] = None
    await _show_payment_step(query, context, package["price"])
    return SELECT_RECEIPT


async def discount_have_callback(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("کد تخفیف ��ا وارد کنید: (برای رد کردن /skip بزنید)")
    return DISCOUNT_INPUT


async def receive_discount_code(update, context):
    if await redirect_if_menu_button(update, context):
        return ConversationHandler.END
    code = (update.effective_message.text or "").strip().upper()
    package = await database.get_package(context.user_data.get("selected_package_id"))
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
    price = package["price"]
    if discount["kind"] == "percent":
        final_price = int(price - (price * discount["value"] / 100))
    else:
        final_price = int(max(price - discount["value"], 0))
    context.user_data["discount_code"] = code
    await update.message.reply_text(f"✅ کد اعمال شد! قیمت جدید: {final_price:,} تومان")
    await _show_payment_step(update, context, final_price)
    return SELECT_RECEIPT


async def skip_discount_command(update, context):
    package = await database.get_package(context.user_data.get("selected_package_id"))
    context.user_data["discount_code"] = None
    await _show_payment_step(update, context, package["price"])
    return SELECT_RECEIPT


async def receive_receipt(update, context):
    package_id = context.user_data.get("selected_package_id")
    if not package_id:
        await update.message.reply_text("خطا. لطفا دوباره از منو شروع کنید.")
        return ConversationHandler.END
    final_price = context.user_data.get("final_price")
    discount_code = context.user_data.get("discount_code")
    photo = update.message.photo[-1]
    order_id = await database.create_order(update.effective_user.id, package_id, photo.file_id, price=final_price, discount_code=discount_code)
    package = await database.get_package(package_id)
    await update.message.reply_text("✅ رسید شما ثبت شد و برای بررسی به ادمین ارسال شد.")
    caption = (
        f"🧾 سفارش جدید #{order_id}\nکاربر: {update.effective_user.mention_html()} (آیدی: {update.effective_user.id})\n"
        f"پلن: {package['name']} — {package['gb']}گیگ/{package['days']}روز\nمبلغ: {final_price:,} تومان" + (f" (کد: {discount_code})" if discount_code else "")
    )
    keyboard = InlineKeyboardMarkup([[
        ibtn("✅ تایید", callback_data=f"order_approve:{order_id}", style="success"),
        ibtn("❌ رد", callback_data=f"order_reject:{order_id}", style="danger"),
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
    return ConversationHandler.END


async def cancel_purchase(update, context):
    context.user_data.pop("selected_package_id", None)
    context.user_data.pop("final_price", None)
    context.user_data.pop("discount_code", None)
    await update.message.reply_text("خرید لغو شد.")
    return ConversationHandler.END


async def _fulfill_order(order, context):
    package = await database.get_package(order["package_id"])
    user_data = await marzban_api.create_user(package["gb"], package["days"])
    link = marzban_api.extract_subscription_link(user_data)
    await database.update_order(order["id"], status="approved", marzban_username=user_data.get("username", ""), subscription_link=link)
    if order.get("discount_code"):
        await database.increment_discount_usage(order["discount_code"])
    qr_bytes = qr_util.make_qr_bytes(link)
    caption = f"✅ سرویسِ «{package['name']}» آماده شد — {package['gb']} گیگ · {package['days']} روز\n\n🔗 لینکِ اشتراک:\n{link}"
    await context.bot.send_photo(chat_id=order["user_id"], photo=qr_bytes, caption=caption)


async def order_approve_callback(update, context):
    query = update.callback_query
    if not await require_perm(query, query.from_user.id, "orders"):
        return
    order_id = int(query.data.split(":")[1])
    order = await database.get_order(order_id)
    if not order or order["status"] != "pending":
        await query.answer("این سفارش قبلاً بررسی شده.", show_alert=True)
        return
    await query.answer("در حال پردازش...")
    try:
        await _fulfill_order(order, context)
        old_caption = query.message.caption or ""
        await query.edit_message_caption(caption=old_caption + "\n\n✅ تایید شد.")
        await database.log_action("admin", query.from_user.id, f"تایید سفارش #{order_id}")
    except Exception as error:
        old_caption = query.message.caption or ""
        await query.edit_message_caption(caption=old_caption + f"\n\n❌ خطا در ساخت سرویس:\n{error}")
        try:
            await context.bot.send_message(chat_id=config.ADMIN_ID, text=f"خطا در پردازش سفارش {order_id}:\n{error}")
        except Exception:
            pass


async def order_reject_callback(update, context):
    query = update.callback_query
    if not await require_perm(query, query.from_user.id, "orders"):
        return
    order_id = int(query.data.split(":")[1])
    order = await database.get_order(order_id)
    if not order or order["status"] != "pending":
        await query.answer("این سفارش قبلاً بررسی شده.", show_alert=True)
        return
    await database.update_order(order_id, status="rejected")
    await query.answer("رد شد.")
    old_caption = query.message.caption or ""
    await query.edit_message_caption(caption=old_caption + "\n\n❌ رد شد.")
    await database.log_action("admin", query.from_user.id, f"رد سفارش #{order_id}")
    try:
        await context.bot.send_message(chat_id=order["user_id"], text="❌ متاسفانه رسید پرداخت شما تایید نشد. با پشتیبانی در ارتباط باشید.")
    except Exception:
        pass


async def trial_run(update, context):
    user_id = update.effective_user.id
    if await database.get_setting("trial_enabled", "0") != "1":
        await update.message.reply_text("در حال حاضر تست رایگان فعال نیست.")
        return
    if await database.has_used_trial(user_id):
        await update.message.reply_text("شما قبلاً از تست رایگان استفاده کرده‌اید.")
        return
    await update.message.reply_text("⏳ در حال ساخت سرویس...")
    try:
        gb = float(await database.get_setting("trial_gb", "1"))
    except (TypeError, ValueError):
        gb = 1.0
    try:
        days = int(float(await database.get_setting("trial_days", "1")))
    except (TypeError, ValueError):
        days = 1
    try:
        user_data = await marzban_api.create_user(gb, days)
        link = marzban_api.extract_subscription_link(user_data)
        await database.mark_trial_used(user_id)
        qr_bytes = qr_util.make_qr_bytes(link)
        caption = f"✅ سرویسِ «تست رایگان» آماده شد — {gb} گیگ · {days} روز\n\n🔗 لینکِ اشتراک:\n{link}"
        await context.bot.send_photo(chat_id=user_id, photo=qr_bytes, caption=caption)
    except Exception as error:
        await update.message.reply_text("متاسفانه امکان ساخت سرویس وجود ندارد. بعداً تلاش کنید.")
        try:
            await context.bot.send_message(chat_id=config.ADMIN_ID, text=f"خطا در ساخت تست رایگان برای {user_id}:\n{error}")
        except Exception:
            pass


async def account_show(update, context):
    orders = await database.list_orders_by_user(update.effective_user.id)
    approved = [o for o in orders if o["status"] == "approved"]
    if not approved:
        await update.message.reply_text("شما هنوز هیچ سرویس فعالی ندارید.")
        return
    lines = ["📋 سرویس‌های شما:\n"]
    for o in approved:
        lines.append(f"• {o['subscription_link']}")
    await update.message.reply_text("\n".join(lines))


MAIN_MENU_ROUTES = {}
