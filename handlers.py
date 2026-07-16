# -*- coding: utf-8 -*-
# handlers.py - هندلرهای اصلی: منو، خرید، تست، حساب من، پشتیبانی/تیکت، پلن‌ها، تنطیمات، ادمین‌ها

import datetime
import html
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
WALLET_TOPUP_AMOUNT, WALLET_TOPUP_RECEIPT = range(400, 402)

BTN_BUY = "🛍️ خرید سرویس"
BTN_TRIAL = "🎁 تست رایگان"
BTN_ACCOUNT = "👤 سرویس‌های من"
BTN_SUPPORT = "🆘 پشتیبانی"
BTN_WALLET = "💰 کیف پول"
BTN_HELP = "❓ راهنما"
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
    rows = [[kbtn(BTN_BUY), kbtn(BTN_TRIAL)], [kbtn(BTN_ACCOUNT), kbtn(BTN_WALLET)], [kbtn(BTN_SUPPORT), kbtn(BTN_HELP)]]
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
        # دلیل این خطا معمولا ایناست که ربات در کانال ادمین نیست. در لاگ ثبت می‌شود تا ادمین متوجه شود
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

async def buy_show_packages(update, context):
    packages = await database.list_packages(active_only=True)
    if not packages:
        await update.message.reply_text("⚠️ در حال حاضر پلنی برای فروش وجود ندارد.")
        return
    keyboard = [[ibtn(
        f"📦 {p['name']} — {p['gb']}گیگ/{p['days']}روز — {p['price']:,}ت", callback_data=f"pkg:{p['id']}", style="primary"
    )] for p in packages]
    await update.message.reply_text(
        "🛍️ <b>یکی از پلن‌های زیر را انتخاب کنید:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


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
    text = (
        "📦 <b>پلن:</b> " + package["name"] + "\n"
        "━━━━━━━━━━━━━━━\n"
        f"💾 <b>حجم:</b> {package['gb']} گیگابایت\n"
        f"⏳ <b>مدت:</b> {package['days']} روز\n"
        f"💵 <b>قیمت:</b> {package['price']:,} تومان\n\n"
        "🎟️ آیا کد تخفیف دارید؟"
    )
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    return CHOOSE_DISCOUNT


async def _show_payment_step(update_or_query, context, final_price):
    context.user_data["final_price"] = final_price
    card_number = await database.get_setting("card_number", "-")
    card_holder = await database.get_setting("card_holder", "-")
    wallet_balance = await database.get_wallet_balance(
        update_or_query.from_user.id if hasattr(update_or_query, "from_user") else update_or_query.effective_user.id
    )
    text = (
        "🧾 <b>نهایی سازی خرید</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"💵 <b>مبلف قابل پرداخت:</b> {final_price:,} تومان\n\n"
        f"💳 <b>شماره کارت:</b> <code>{card_number}</code>\n👤 <b>به نام:</b> {card_holder}\n\n"
        "📸 لطفا بعد از واریز، تصویر رسید پرداخت را همینجا ارسال کنید. (لقو: /cancel)"
    )
    keyboard = None
    if wallet_balance >= final_price:
        text += f"\n\n💰 موجودی کیف پول شما: {wallet_balance:,} تومان (کافی است!)"
        keyboard = InlineKeyboardMarkup([[ibtn("💰 پرداخت از کیف پول", callback_data="pay_wallet", style="success")]])
    if hasattr(update_or_query, "edit_message_text"):
        await update_or_query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await update_or_query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


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
        await update.message.reply_text("⚠️ <b>خطا.</b> لطفا دوباره از منو شروع کنید.", parse_mode="HTML")
        return ConversationHandler.END
    final_price = context.user_data.get("final_price")
    discount_code = context.user_data.get("discount_code")
    photo = update.message.photo[-1]
    order_id = await database.create_order(update.effective_user.id, package_id, photo.file_id, price=final_price, discount_code=discount_code)
    package = await database.get_package(package_id)
    await update.message.reply_text(
        "📤 <b>رسید شما با موفقیت ثبت شد!</b>\n"
        "✨ همین الان برای بررسی برای تیم ما ارسال شد.\n"
        "⏱️ <b>پرداخت های ما در سریع‌ترین زمان ممکن بررسی و تایید می‌شوند.</b>",
        parse_mode="HTML",
    )
    caption = (
        f"🧾 <b>سفارش جدید #{order_id}</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"👤 <b>کاربر:</b> {update.effective_user.mention_html()} (<code>{update.effective_user.id}</code>)\n"
        f"📦 <b>پلن:</b> {package['name']} — {package['gb']}گیگ/{package['days']}روز\n"
        f"💰 <b>مبلف:</b> {final_price:,} تومان" + (f" (🎟️کد: {discount_code})" if discount_code else "")
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
    await update.message.reply_text("🚫 <b>خرید لفو شد.</b>", parse_mode="HTML")
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
            "━━━━━━━━━━━━━━━\n"
            f"👤 <b>مشتری:</b> {html.escape(str(buyer_name))}\n"
            f"🆔 <b>Telegram ID:</b> <code>{_mask_user_id(buyer_id)}</code>\n"
            f"👤 <b>نام سرویس:</b> {html.escape(str(service_username))}\n"
            f"📦 <b>بسته:</b> {package['gb']} گیگابایت | {package['days']} روز\n"
            f"💰 <b>مبلف:</b> {final_price:,} تومان\n"
            f"📅 <b>انقضا:</b> {expire_date}\n"
            f"⏰ <b>زمان:</b> {now_str}\n\n"
            "✨ همین الان به تیم ما بپیوند و سرویس بگیرید! 🚀"
        )
        await context.bot.send_message(chat_id=TRUST_CHANNEL, text=text, parse_mode="HTML")
    except Exception as error:
        try:
            await database.log_action("error", None, f"خطا در ارسال به کانال اعتماد: {error}")
        except Exception:
            pass


async def _fulfill_order(order, context):
    package = await database.get_package(order["package_id"])
    user_data = await marzban_api.create_user(package["gb"], package["days"])
    link = marzban_api.extract_subscription_link(user_data)
    await database.update_order(order["id"], status="approved", marzban_username=user_data.get("username", ""), subscription_link=link)
    if order.get("discount_code"):
        await database.increment_discount_usage(order["discount_code"])
    qr_bytes = qr_util.make_qr_bytes(link)
    caption = (
        f"🎉 <b>سرویس “{package['name']}” با موفقیت فعال شد!</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"💾 <b>حجم:</b> {package['gb']} گیگ\n"
        f"⏳ <b>مدت:</b> {package['days']} روز\n\n"
        f"🔗 <b>لینک اشتراک:</b>\n<code>{link}</code>\n\n"
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
    await query.edit_message_caption(caption=old_caption + "\n\n❌ <b>رد شد.</b>", parse_mode="HTML")
    await database.log_action("admin", query.from_user.id, f"رد سفارش #{order_id}")
    try:
        await context.bot.send_message(chat_id=order["user_id"], text="❌ <b>متاسفانه رسید پرداخت شما تایید نشد.</b>\nبا پشتیبانی در ارتباط باشید.", parse_mode="HTML")
    except Exception:
        pass


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
        user_data = await marzban_api.create_user(gb, days)
        link = marzban_api.extract_subscription_link(user_data)
        await database.mark_trial_used(user_id)
        qr_bytes = qr_util.make_qr_bytes(link)
        caption = (
            f"🎁 <b>سرویس “تست رایگان” آماده شد!</b>\n"
            "━━━━━━━━━━━━━━━\n"
            f"💾 <b>حجم:</b> {gb} گیگ\n"
            f"⏳ <b>مدت:</b> {days} روز\n\n"
            f"🔗 <b>لینک اشتراک:</b>\n<code>{link}</code>"
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
    support_line = f"\n🆘 <b>پشتیبانی:</b> @{support_username}\n" if support_username else ""
    text = (
        "❓ <b>راهنمای کامل استفاده از ربات</b>\n"
        "━━━━━━━━━━━━━━━\n\n"
        "🛒 <b>خرید اشتراک</b>\n"
        "از دکمه «خرید اشتراک» یکی از پلن‌ها را انتخاب کنید، اگر کد تخفیف دارید وارد کنید یا رد کنید، سپس مبلف را کارت به کارت کنید یا از 💰 <b>کیف پول</b> تون پرداخت کنید.\n"
        "بعد از کارت به کارت، فقط کافیست عکس رسید واریز را ارسال کنید.\n"
        "⚡️ <b>پرداخت های ما در سریع‌ترین زمان ممکن بررسی و تایید می‌شوند</b> و بلافاصله سرویس شما فعال می‌شود.\n\n"
        "🎁 <b>تست رایگان</b>\n"
        "یک‌بار و فقط یک‌بار می‌توانید از دکمه تست رایگان استفاده کنید تا کیفیت سرویس را بسنجید.\n\n"
        "📱 <b>حساب کاربری</b>\n"
        "در این بخش لینک های اشتراک فعال خودتان را می‌بینید.\n\n"
        "💰 <b>کیف پول</b>\n"
        "می‌توانید از قبل شارج کنید تا در لحظه خرید، بدون نیاز به ارسال رسید، سریع پرداخت کنید.\n\n"
        f"{support_line}"
        "\n✨ هر سوالی داشتید از پشتیبانی بپرسید. 💜"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def account_show(update, context):
    orders = await database.list_orders_by_user(update.effective_user.id)
    approved = [o for o in orders if o["status"] == "approved"]
    if not approved:
        await update.message.reply_text("ℹ️ <b>شما هنوز هیچ سرویس فعالی ندارید.</b>", parse_mode="HTML")
        return
    lines = ["📋 <b>سرویس‌های شما:</b>\n"]
    for o in approved:
        lines.append(f"• <code>{o['subscription_link']}</code>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

MAIN_MENU_ROUTES = {}

# ==================== کیف پول ====================

async def wallet_show(update, context):
    user_id = update.effective_user.id
    balance = await database.get_wallet_balance(user_id)
    text = (
        "💰 <b>کیف پول من</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"💵 موجودی فعلی: <b>{balance:,} تومان</b>\n\n"
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
        "purchase": "🛍️ خرید از کیف پول",
        "admin_credit": "➕ افزایش توسط ادمین",
        "admin_debit": "➖ کاهش توسط ادمین",
    }
    lines = ["📜 <b>تاریخچه تراکنش‌های کیف پول</b>\n━━━━━━━━━━━━━━━"]
    for tx in txs:
        label = kind_labels.get(tx["kind"], tx["kind"])
        sign = "+" if tx["amount"] and tx["amount"] > 0 else ""
        lines.append(f"• {label}: {sign}{tx['amount']:,} تومان")
    await query.message.reply_text("\n".join(lines), parse_mode="HTML")


async def wallet_topup_start(update, context):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("💳 مبلغی که می‌خواهید به کیف پول اضافه شود را به تومان وارد کنید: (لغو: /cancel)")
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
        "📸 لطفا بعد از واریز، عکس رسید را همینجا ارسال کنید. (لغو: /cancel)",
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
        "━━━━━━━━━━━━━━━\n"
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


async def pay_with_wallet_callback(update, context):
    query = update.callback_query
    user_id = query.from_user.id
    final_price = context.user_data.get("final_price")
    package_id = context.user_data.get("selected_package_id")
    if not final_price or not package_id:
        await query.answer("⚠️ خطا. لطفا دوباره از منو شروع کنید.", show_alert=True)
        return ConversationHandler.END
    balance = await database.get_wallet_balance(user_id)
    if balance < final_price:
        await query.answer("❌ موجودی کیف پول شما کافی نیست.", show_alert=True)
        return SELECT_RECEIPT
    await query.answer("✅ در حال پردازش...")
    discount_code = context.user_data.get("discount_code")
    order_id = await database.create_order(user_id, package_id, "wallet", price=final_price, discount_code=discount_code)
    await database.adjust_wallet_balance(user_id, -final_price)
    await database.record_wallet_transaction(user_id, -final_price, "purchase", note=f"خرید سفارش #{order_id}")
    order = await database.get_order(order_id)
    try:
        await _fulfill_order(order, context)
        if discount_code:
            await database.increment_discount_usage(discount_code)
        await query.edit_message_text("✅ پرداخت از کیف پول انجام شد و سرویس شما فعال شد. 🎉")
        await database.log_action("admin", user_id, f"پرداخت کیف پول برای سفارش #{order_id}")
    except Exception as error:
        await database.adjust_wallet_balance(user_id, final_price)
        await database.record_wallet_transaction(user_id, final_price, "admin_credit", note="بازگشت به دلیل خطا در ساخت سرویس")
        await query.edit_message_text("❌ خطا در فعال‌سازی سرویس. مبلغ به کیف پول شما بازگشت.")
        try:
            await context.bot.send_message(chat_id=config.ADMIN_ID, text=f"خطا در ساخت سرویس (پرداخت کیف پول) سفارش {order_id}: {html.escape(str(error))}")
        except Exception:
            pass
    context.user_data.pop("selected_package_id", None)
    context.user_data.pop("final_price", None)
    context.user_data.pop("discount_code", None)
    return ConversationHandler.END


