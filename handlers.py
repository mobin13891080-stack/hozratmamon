# -*- coding: utf-8 -*-
# handlers.py
# تمام هندلرهای ربات: منوی اصلی (کیبورد ثابت پایین صفحه)، خرید سرویس،
# تست رایگان، حساب من، پشتیبانی، و پنل مدیریت کامل ادمین (God Mode)

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ConversationHandler

import config
import database
import marzban_api
import qr_util

# شناسه‌های وضعیت مکالمه
(SELECT_RECEIPT,) = range(100, 101)
AWAITING_ADMIN_INPUT = 200


# ==================== متن دکمه‌های منو (کیبورد ثابت پایین صفحه) ====================

BTN_BUY = "🛒 خرید سرویس"
BTN_TRIAL = "🎁 تست رایگان"
BTN_ACCOUNT = "👤 سرویس‌های من"
BTN_SUPPORT = "📞 پشتیبانی"
BTN_ADMIN_PANEL = "⚙️ پنل مدیریت"

BTN_ADMIN_PACKAGES = "📦 مدیریت پلن‌ها"
BTN_ADMIN_CARD = "💳 شماره کارت"
BTN_ADMIN_FORCEJOIN = "🔒 عضویت اجباری"
BTN_ADMIN_TRIAL = "🎁 تنظیمات تست رایگان"
BTN_ADMIN_ADMINS = "👮 مدیریت ادمین‌ها"
BTN_ADMIN_WELCOME = "📝 متن خوش‌آمدگویی"
BTN_ADMIN_WELCOME_PHOTO = "🖼 عکس خوش‌آمدگویی"
BTN_ADMIN_SUPPORT_TEXT = "✏️ متن پشتیبانی"
BTN_ADMIN_STATS = "📊 آمار"
BTN_ADMIN_TURN_OFF = "🔴 خاموش کردن ربات"
BTN_ADMIN_TURN_ON = "🟢 روشن کردن ربات"
BTN_BACK_MAIN = "🔙 بازگشت به منوی اصلی"


# ==================== ساخت کیبوردها ====================

def main_menu_keyboard(is_admin_user: bool) -> ReplyKeyboardMarkup:
    rows = [
        [BTN_BUY, BTN_TRIAL],
        [BTN_ACCOUNT, BTN_SUPPORT],
    ]
    if is_admin_user:
        rows.append([BTN_ADMIN_PANEL])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def admin_menu_keyboard(bot_enabled: bool) -> ReplyKeyboardMarkup:
    toggle_label = BTN_ADMIN_TURN_OFF if bot_enabled else BTN_ADMIN_TURN_ON
    rows = [
        [BTN_ADMIN_PACKAGES, BTN_ADMIN_CARD],
        [BTN_ADMIN_FORCEJOIN, BTN_ADMIN_TRIAL],
        [BTN_ADMIN_ADMINS, BTN_ADMIN_STATS],
        [BTN_ADMIN_WELCOME, BTN_ADMIN_WELCOME_PHOTO],
        [BTN_ADMIN_SUPPORT_TEXT],
        [toggle_label],
        [BTN_BACK_MAIN],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


# ==================== ابزارهای کمکی ====================

async def require_admin(query, user_id) -> bool:
    if await database.is_admin(user_id):
        return True
    await query.answer("شما دسترسی ادمین ندارید.", show_alert=True)
    return False


async def ensure_bot_active(update, context, user_id) -> bool:
    """اگر ربات توسط ادمین خاموش شده باشد، به کاربران عادی پیام می‌دهد و False برمی‌گرداند.
    ادمین‌ها همیشه دسترسی کامل دارند تا بتوانند ربات را دوباره روشن کنند."""
    if await database.is_admin(user_id):
        return True
    enabled = await database.get_setting("bot_enabled", "1")
    if enabled == "1":
        return True
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🚧 ربات موقتاً خاموش است. لطفا بعداً دوباره تلاش کنید.",
    )
    return False


# ==================== منوی اصلی ====================

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


async def check_membership(context, channel, user_id) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False


async def show_join_gate(chat_id, context, channel):
    channel_display = channel if channel.startswith("http") else "https://t.me/" + channel.lstrip("@")
    keyboard = [
        [InlineKeyboardButton("📢 عضویت در کانال", url=channel_display)],
        [InlineKeyboardButton("✅ عضو شدم", callback_data="check_join")],
    ]
    await context.bot.send_message(
        chat_id=chat_id,
        text="برای استفاده از ربات، ابتدا باید در کانال زیر عضو شوید:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def start(update, context):
    """هندلر دستور /start"""
    user = update.effective_user
    await database.upsert_user(user.id, user.username or "")

    if not await ensure_bot_active(update, context, user.id):
        return

    force_join = await database.get_setting("force_join_enabled", "0")
    if force_join == "1":
        channel = await database.get_setting("force_join_channel", "")
        if channel:
            is_member = await check_membership(context, channel, user.id)
            if not is_member:
                await show_join_gate(update.effective_chat.id, context, channel)
                return
    await send_main_menu(update.effective_chat.id, context, user.id)


async def check_join_callback(update, context):
    query = update.callback_query
    channel = await database.get_setting("force_join_channel", "")
    is_member = await check_membership(context, channel, query.from_user.id)
    if is_member:
        await query.answer("عضویت شما تایید شد ✅")
        await query.message.delete()
        await send_main_menu(query.message.chat_id, context, query.from_user.id)
    else:
        await query.answer("هنوز در کانال عضو نشده‌اید.", show_alert=True)


# ==================== مسیریاب اصلی دکمه‌های کیبورد ====================

async def main_menu_router(update, context):
    """تمام پیام‌های متنی خارج از مکالمه‌ها را بر اساس متن دکمه فشرده‌شده مسیریابی می‌کند."""
    text = (update.message.text or "").strip()
    handler_fn = MAIN_MENU_ROUTES.get(text)
    if not handler_fn:
        return
    if not await ensure_bot_active(update, context, update.effective_user.id):
        return
    await handler_fn(update, context)


async def back_to_main_from_admin(update, context):
    await send_main_menu(update.effective_chat.id, context, update.effective_user.id)


# ==================== خرید سرویس ====================

async def buy_show_packages(update, context):
    packages = await database.list_packages(active_only=True)
    if not packages:
        await update.message.reply_text("در حال حاضر پلنی برای فروش وجود ندارد.")
        return
    keyboard = [
        [InlineKeyboardButton(
            f"{p['name']} — {p['gb']} گیگ / {p['days']} روز — {p['price']:,} تومان",
            callback_data=f"pkg:{p['id']}",
        )]
        for p in packages
    ]
    await update.message.reply_text(
        "🛒 لطفا یکی از پلن‌های زیر را انتخاب کنید:",
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
    card_number = await database.get_setting("card_number", "-")
    card_holder = await database.get_setting("card_holder", "-")
    await query.answer()
    await query.edit_message_text(
        f"پلن انتخابی: {package['name']} ({package['gb']} گیگ / {package['days']} روز)\n"
        f"قیمت: {package['price']:,} تومان\n\n"
        f"💳 شماره کارت: {card_number}\n👤 به نام: {card_holder}\n\n"
        "لطفا بعد از واریز، تصویر رسید پرداخت را همینجا ارسال کنید. (برای لغو: /cancel)"
    )
    return SELECT_RECEIPT


async def receive_receipt(update, context):
    package_id = context.user_data.get("selected_package_id")
    if not package_id:
        await update.message.reply_text("خطا در فرایند سفارش. لطفا دوباره از منو شروع کنید.")
        return ConversationHandler.END

    photo = update.message.photo[-1]
    order_id = await database.create_order(update.effective_user.id, package_id, photo.file_id)
    package = await database.get_package(package_id)

    await update.message.reply_text("✅ رسید شما ثبت شد و برای بررسی به ادمین ارسال شد. لطفا منتظر تایید بمانید.")

    caption = (
        f"🧾 سفارش جدید #{order_id}\n"
        f"کاربر: {update.effective_user.mention_html()} (آیدی: {update.effective_user.id})\n"
        f"پلن: {package['name']} — {package['gb']} گیگ / {package['days']} روز — {package['price']:,} تومان"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تایید", callback_data=f"order_approve:{order_id}"),
            InlineKeyboardButton("❌ رد", callback_data=f"order_reject:{order_id}"),
        ]
    ])

    admin_ids = {config.ADMIN_ID} | {a["user_id"] for a in await database.list_admins()}
    for admin_id in admin_ids:
        try:
            await context.bot.send_photo(
                chat_id=admin_id, photo=photo.file_id, caption=caption,
                parse_mode="HTML", reply_markup=keyboard,
            )
        except Exception:
            pass

    context.user_data.pop("selected_package_id", None)
    return ConversationHandler.END


async def cancel_purchase(update, context):
    context.user_data.pop("selected_package_id", None)
    await update.message.reply_text("خرید لغو شد.")
    return ConversationHandler.END


# ==================== تأیید / رد سفارش (توسط ادمین) ====================

async def order_approve_callback(update, context):
    query = update.callback_query
    if not await database.is_admin(query.from_user.id):
        await query.answer("شما مجاز نیستید.", show_alert=True)
        return
    order_id = int(query.data.split(":")[1])
    order = await database.get_order(order_id)
    if not order or order["status"] != "pending":
        await query.answer("این سفارش قبلاً بررسی شده.", show_alert=True)
        return
    await query.answer("در حال پردازش...")
    package = await database.get_package(order["package_id"])
    try:
        user_data = await marzban_api.create_user(package["gb"], package["days"])
        link = marzban_api.extract_subscription_link(user_data)
        await database.update_order(
            order_id, status="approved",
            marzban_username=user_data.get("username", ""), subscription_link=link,
        )
        qr_bytes = qr_util.make_qr_bytes(link)
        caption = (
            f"✅ سرویسِ «{package['name']}» آماده شد — {package['gb']} گیگ · {package['days']} روز\n\n"
            f"🔗 لینکِ اشتراک:\n{link}"
        )
        await context.bot.send_photo(chat_id=order["user_id"], photo=qr_bytes, caption=caption)
        old_caption = query.message.caption or ""
        await query.edit_message_caption(caption=old_caption + "\n\n✅ تأیید شد.")
    except Exception as error:
        old_caption = query.message.caption or ""
        await query.edit_message_caption(caption=old_caption + f"\n\n❌ خطا در ساخت سرویس:\n{error}")
        try:
            await context.bot.send_message(chat_id=config.ADMIN_ID, text=f"خطا در پردازش سفارش {order_id}:\n{error}")
        except Exception:
            pass


async def order_reject_callback(update, context):
    query = update.callback_query
    if not await database.is_admin(query.from_user.id):
        await query.answer("شما مجاز نیستید.", show_alert=True)
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
    try:
        await context.bot.send_message(
            chat_id=order["user_id"],
            text="❌ متأسفانه رسید پرداخت شما تأیید نشد. لطفا با ادمین در ارتباط باشید.",
        )
    except Exception:
        pass


# ==================== تست رایگان ====================

async def trial_run(update, context):
    user_id = update.effective_user.id
    trial_enabled = await database.get_setting("trial_enabled", "0")
    if trial_enabled != "1":
        await update.message.reply_text("در حال حاضر تست رایگان فعال نیست.")
        return
    if await database.has_used_trial(user_id):
        await update.message.reply_text("شما قبلاً از تست رایگان استفاده کرده‌اید.")
        return
    await update.message.reply_text("⏳ در حال ساخت سرویس...")
    gb = float(await database.get_setting("trial_gb", "1"))
    days = int(await database.get_setting("trial_days", "1"))
    try:
        user_data = await marzban_api.create_user(gb, days)
        link = marzban_api.extract_subscription_link(user_data)
        await database.mark_trial_used(user_id)
        qr_bytes = qr_util.make_qr_bytes(link)
        caption = (
            f"✅ سرویسِ «تست رایگان» آماده شد — {gb} گیگ · {days} روز\n\n"
            f"🔗 لینکِ اشتراک:\n{link}"
        )
        await context.bot.send_photo(chat_id=user_id, photo=qr_bytes, caption=caption)
    except Exception as error:
        await update.message.reply_text("متأسفانه در حال حاضر امکان ساخت سرویس وجود ندارد. لطفا بعداً تلاش کنید.")
        try:
            await context.bot.send_message(chat_id=config.ADMIN_ID, text=f"خطا در ساخت تست رایگان برای {user_id}:\n{error}")
        except Exception:
            pass


# ==================== سرویس‌های من ====================

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


# ==================== پشتیبانی ====================

async def support_show(update, context):
    support_text = await database.get_setting(
        "support_text", "📞 برای ارتباط با پشتیبانی به ادمین پیام دهید."
    )
    await update.message.reply_text(support_text)


# ==================== پنل مدیریت (God Mode) ====================

async def admin_panel_show(update, context):
    if not await database.is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ شما دسترسی ادمین ندارید.")
        return
    enabled = await database.get_setting("bot_enabled", "1") == "1"
    status_text = "🟢 روشن" if enabled else "🔴 خاموش"
    text = (
        "⚙️ پنل مدیریت\n"
        "━━━━━━━━━━━━━━━\n"
        f"وضعیت فعلی ربات: {status_text}\n"
        "از دکمه‌های زیر برای مدیریت کامل ربات استفاده کنید 👇"
    )
    await update.message.reply_text(text, reply_markup=admin_menu_keyboard(enabled))


async def admin_toggle_bot(update, context):
    if not await database.is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ شما دسترسی ادمین ندارید.")
        return
    current = await database.get_setting("bot_enabled", "1")
    new_value = "0" if current == "1" else "1"
    await database.set_setting("bot_enabled", new_value)
    enabled = new_value == "1"
    status_text = "🟢 ربات روشن شد و برای همه کاربران فعال است." if enabled else "🔴 ربات خاموش شد. فقط ادمین‌ها به آن دسترسی دارند."
    await update.message.reply_text(f"✅ {status_text}", reply_markup=admin_menu_keyboard(enabled))


# ---- مدیریت پلن‌ها ----

async def _packages_view():
    packages = await database.list_packages()
    lines = ["📦 پلن‌های موجود", "━━━━━━━━━━━━━━━"]
    if not packages:
        lines.append("هیچ پلنی ثبت نشده است.")
    keyboard = []
    for p in packages:
        status_icon = "🟢" if p["active"] else "🔴"
        keyboard.append([InlineKeyboardButton(
            f"{status_icon} {p['name']} — {p['gb']}گیگ/{p['days']}روز — {p['price']:,}ت",
            callback_data=f"admin_pkg_edit:{p['id']}",
        )])
    keyboard.append([InlineKeyboardButton("➕ افزودن پلن جدید", callback_data="admin_pkg_add")])
    text = "\n".join(lines) + "\n\nبرای ویرایش، روی یک پلن بزنید 👇"
    return text, InlineKeyboardMarkup(keyboard)


async def admin_packages_show(update, context):
    if not await database.is_admin(update.effective_user.id):
        return
    text, keyboard = await _packages_view()
    await update.message.reply_text(text, reply_markup=keyboard)


async def admin_packages_callback(update, context):
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return
    await query.answer()
    text, keyboard = await _packages_view()
    await query.edit_message_text(text, reply_markup=keyboard)


async def admin_pkg_add_start(update, context):
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return ConversationHandler.END
    context.user_data["admin_action"] = "pkg_add_name"
    context.user_data["new_package"] = {}
    await query.answer()
    await query.message.reply_text("نام پلن جدید را وارد کنید: (برای لغو: /cancel)")
    return AWAITING_ADMIN_INPUT


async def admin_pkg_edit_menu(update, context):
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return
    await query.answer()
    package_id = int(query.data.split(":")[1])
    package = await database.get_package(package_id)
    if not package:
        await query.answer("این پلن پیدا نشد.", show_alert=True)
        return
    status_text = "فعال 🟢" if package["active"] else "غیرفعال 🔴"
    keyboard = [
        [InlineKeyboardButton("✏️ نام", callback_data=f"admin_pkg_field:{package_id}:name")],
        [InlineKeyboardButton("✏️ حجم (گیگ)", callback_data=f"admin_pkg_field:{package_id}:gb")],
        [InlineKeyboardButton("✏️ روز اعتبار", callback_data=f"admin_pkg_field:{package_id}:days")],
        [InlineKeyboardButton("✏️ قیمت", callback_data=f"admin_pkg_field:{package_id}:price")],
        [InlineKeyboardButton(f"وضعیت: {status_text} (تعویض)", callback_data=f"admin_pkg_toggle:{package_id}")],
        [InlineKeyboardButton("🗑 حذف پلن", callback_data=f"admin_pkg_delete:{package_id}")],
        [InlineKeyboardButton("🔙 بازگشت به لیست پلن‌ها", callback_data="admin_packages")],
    ]
    await query.edit_message_text(
        f"ویرایش پلن «{package['name']}»:\n"
        f"حجم: {package['gb']} گیگ | روز: {package['days']} | قیمت: {package['price']:,} تومان",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def admin_pkg_field_start(update, context):
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return ConversationHandler.END
    _, package_id, field = query.data.split(":")
    context.user_data["admin_action"] = f"pkg_edit_{field}"
    context.user_data["admin_target_id"] = int(package_id)
    await query.answer()
    field_names = {"name": "نام", "gb": "حجم (گیگ)", "days": "روز اعتبار", "price": "قیمت (تومان)"}
    await query.message.reply_text(f"مقدار جدید برای «{field_names.get(field, field)}» را وارد کنید: (/cancel برای لغو)")
    return AWAITING_ADMIN_INPUT


async def admin_pkg_toggle(update, context):
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return
    package_id = int(query.data.split(":")[1])
    package = await database.get_package(package_id)
    if package:
        await database.update_package_field(package_id, "active", 0 if package["active"] else 1)
    await admin_pkg_edit_menu(update, context)


async def admin_pkg_delete(update, context):
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return
    package_id = int(query.data.split(":")[1])
    await database.delete_package(package_id)
    await query.answer("پلن حذف شد.")
    await admin_packages_callback(update, context)


# ---- شماره کارت ----

async def _card_view():
    card_number = await database.get_setting("card_number", "-")
    card_holder = await database.get_setting("card_holder", "-")
    text = (
        "💳 اطلاعات کارت\n━━━━━━━━━━━━━━━\n"
        f"شماره: {card_number}\nبه‌نام: {card_holder}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ تغییر شماره کارت", callback_data="admin_set_card_number")],
        [InlineKeyboardButton("✏️ تغییر نام صاحب کارت", callback_data="admin_set_card_holder")],
    ])
    return text, keyboard


async def admin_card_show(update, context):
    if not await database.is_admin(update.effective_user.id):
        return
    text, keyboard = await _card_view()
    await update.message.reply_text(text, reply_markup=keyboard)


ASYNC_SETTING_ACTIONS = {
    "admin_set_card_number": ("card_number", "شماره کارت جدید را وارد کنید:"),
    "admin_set_card_holder": ("card_holder", "نام صاحب کارت را وارد کنید:"),
    "admin_forcejoin_setchannel": ("force_join_channel", "ایدی عددی یا یوزرنیم کانال را وارد کنید (مثلا @mychannel):"),
    "admin_trial_setgb": ("trial_gb", "حجم تست رایگان را به گیگابایت وارد کنید:"),
    "admin_trial_setdays": ("trial_days", "تعداد روز اعتبار تست رایگان را وارد کنید:"),
}


async def admin_generic_setting_start(update, context):
    """شروع دریافت مقدار جدید برای یک تنظیم ساده (بر اساس callback_data)"""
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return ConversationHandler.END
    setting_key, prompt = ASYNC_SETTING_ACTIONS[query.data]
    context.user_data["admin_action"] = f"setting_{setting_key}"
    await query.answer()
    await query.message.reply_text(prompt + " (/cancel برای لغو)")
    return AWAITING_ADMIN_INPUT


# ---- عضویت اجباری ----

async def _forcejoin_view():
    enabled = await database.get_setting("force_join_enabled", "0") == "1"
    channel = await database.get_setting("force_join_channel", "-")
    status_label = "فعال 🟢" if enabled else "غیرفعال 🔴"
    text = f"🔒 عضویت اجباری\n━━━━━━━━━━━━━━━\nوضعیت: {status_label}\nکانال: {channel}"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"تعویض وضعیت (فعلی: {status_label})", callback_data="admin_forcejoin_toggle")],
        [InlineKeyboardButton("✏️ تغییر کانال", callback_data="admin_forcejoin_setchannel")],
    ])
    return text, keyboard


async def admin_forcejoin_show(update, context):
    if not await database.is_admin(update.effective_user.id):
        return
    text, keyboard = await _forcejoin_view()
    await update.message.reply_text(text, reply_markup=keyboard)


async def admin_forcejoin_toggle(update, context):
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return
    current = await database.get_setting("force_join_enabled", "0")
    await database.set_setting("force_join_enabled", "0" if current == "1" else "1")
    await query.answer("وضعیت تغییر کرد.")
    text, keyboard = await _forcejoin_view()
    await query.edit_message_text(text, reply_markup=keyboard)


# ---- تست رایگان ----

async def _trial_view():
    enabled = await database.get_setting("trial_enabled", "0") == "1"
    gb = await database.get_setting("trial_gb", "1")
    days = await database.get_setting("trial_days", "1")
    status_label = "فعال 🟢" if enabled else "غیرفعال 🔴"
    text = f"🎁 تست رایگان\n━━━━━━━━━━━━━━━\nوضعیت: {status_label}\nحجم: {gb} گیگ | روز: {days}"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"تعویض وضعیت (فعلی: {status_label})", callback_data="admin_trial_toggle")],
        [InlineKeyboardButton("✏️ تغییر حجم", callback_data="admin_trial_setgb")],
        [InlineKeyboardButton("✏️ تغییر روز", callback_data="admin_trial_setdays")],
    ])
    return text, keyboard


async def admin_trial_show(update, context):
    if not await database.is_admin(update.effective_user.id):
        return
    text, keyboard = await _trial_view()
    await update.message.reply_text(text, reply_markup=keyboard)


async def admin_trial_toggle(update, context):
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return
    current = await database.get_setting("trial_enabled", "0")
    await database.set_setting("trial_enabled", "0" if current == "1" else "1")
    await query.answer("وضعیت تغییر کرد.")
    text, keyboard = await _trial_view()
    await query.edit_message_text(text, reply_markup=keyboard)


# ---- مدیریت ادمین‌ها ----

async def _admins_view():
    admins = await database.list_admins()
    lines = ["👮 مدیریت ادمین‌ها", "━━━━━━━━━━━━━━━", f"👑 ادمین اصلی: {config.ADMIN_ID}"]
    keyboard = []
    for a in admins:
        lines.append(f"👤 ادمین: {a['user_id']}")
        keyboard.append([InlineKeyboardButton(f"🗑 حذف {a['user_id']}", callback_data=f"admin_admins_remove:{a['user_id']}")])
    keyboard.append([InlineKeyboardButton("➕ افزودن ادمین جدید", callback_data="admin_admins_add")])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


async def admin_admins_show(update, context):
    if not await database.is_admin(update.effective_user.id):
        return
    text, keyboard = await _admins_view()
    await update.message.reply_text(text, reply_markup=keyboard)


async def admin_admins_add_start(update, context):
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return ConversationHandler.END
    context.user_data["admin_action"] = "add_admin_id"
    await query.answer()
    await query.message.reply_text(
        "آیدی عددی کاربری که می‌خواهید ادمین شود را وارد کنید.\n"
        "(کاربر باید قبلاً یک بار /start ربات را زده باشد) (/cancel برای لغو)"
    )
    return AWAITING_ADMIN_INPUT


async def admin_admins_remove(update, context):
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return
    target_id = int(query.data.split(":")[1])
    removed = await database.remove_admin(target_id)
    await query.answer("ادمین حذف شد." if removed else "این کاربر ادمین اصلی است و قابل حذف نیست.", show_alert=not removed)
    text, keyboard = await _admins_view()
    await query.edit_message_text(text, reply_markup=keyboard)


# ---- آمار ----

async def admin_stats_show(update, context):
    if not await database.is_admin(update.effective_user.id):
        return
    total_users = await database.count_users()
    stats = await database.order_stats()
    text = (
        "📊 آمار ربات\n━━━━━━━━━━━━━━━\n"
        f"👥 تعداد کاربران: {total_users}\n"
        f"🧾 سفارش‌های در انتظار: {stats.get('pending', 0)}\n"
        f"✅ سفارش‌های تأییدشده: {stats.get('approved', 0)}\n"
        f"❌ سفارش‌های ردشده: {stats.get('rejected', 0)}"
    )
    await update.message.reply_text(text)


# ---- متن خوش‌آمدگویی / عکس خوش‌آمدگویی / متن پشتیبانی (ورودی مستقیم از منو) ----

async def admin_welcome_start(update, context):
    if not await database.is_admin(update.effective_user.id):
        return ConversationHandler.END
    context.user_data["admin_action"] = "setting_welcome_text"
    await update.message.reply_text("📝 متن خوش‌آمدگویی جدید را وارد کنید: (/cancel برای لغو)")
    return AWAITING_ADMIN_INPUT


async def admin_welcome_photo_start(update, context):
    if not await database.is_admin(update.effective_user.id):
        return ConversationHandler.END
    context.user_data["admin_action"] = "setting_welcome_photo"
    await update.message.reply_text(
        "🖼 لطفا تصویر خوش‌آمدگویی جدید را ارسال کنید.\n"
        "برای حذف تصویر فعلی، عدد 0 را ارسال کنید. (/cancel برای لغو)"
    )
    return AWAITING_ADMIN_INPUT


async def admin_support_text_start(update, context):
    if not await database.is_admin(update.effective_user.id):
        return ConversationHandler.END
    context.user_data["admin_action"] = "setting_support_text"
    await update.message.reply_text("✏️ متن پشتیبانی جدید را وارد کنید: (/cancel برای لغو)")
    return AWAITING_ADMIN_INPUT


# ---- دریافت متنی از ادمین (کل مراحل ورودی پنل مدیریت) ----

async def admin_receive_input(update, context):
    action = context.user_data.get("admin_action")
    text = update.message.text.strip()

    if action == "pkg_add_name":
        context.user_data["new_package"]["name"] = text
        context.user_data["admin_action"] = "pkg_add_gb"
        await update.message.reply_text("حجم پلن را به گیگابایت وارد کنید:")
        return AWAITING_ADMIN_INPUT

    if action == "pkg_add_gb":
        try:
            gb = float(text)
        except ValueError:
            await update.message.reply_text("لطفا فقط عدد وارد کنید.")
            return AWAITING_ADMIN_INPUT
        context.user_data["new_package"]["gb"] = gb
        context.user_data["admin_action"] = "pkg_add_days"
        await update.message.reply_text("تعداد روز اعتبار را وارد کنید:")
        return AWAITING_ADMIN_INPUT

    if action == "pkg_add_days":
        try:
            days = int(text)
        except ValueError:
            await update.message.reply_text("لطفا فقط عدد صحیح وارد کنید.")
            return AWAITING_ADMIN_INPUT
        context.user_data["new_package"]["days"] = days
        context.user_data["admin_action"] = "pkg_add_price"
        await update.message.reply_text("قیمت پلن را به تومان وارد کنید:")
        return AWAITING_ADMIN_INPUT

    if action == "pkg_add_price":
        try:
            price = int(text)
        except ValueError:
            await update.message.reply_text("لطفا فقط عدد صحیح وارد کنید.")
            return AWAITING_ADMIN_INPUT
        pkg = context.user_data["new_package"]
        await database.add_package(pkg["name"], pkg["gb"], pkg["days"], price)
        await update.message.reply_text(f"✅ پلن «{pkg['name']}» اضافه شد.")
        context.user_data.pop("new_package", None)
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

    if action and action.startswith("pkg_edit_"):
        field = action.replace("pkg_edit_", "")
        package_id = context.user_data.get("admin_target_id")
        value = text
        if field == "gb":
            try:
                value = float(text)
            except ValueError:
                await update.message.reply_text("لطفا فقط عدد وارد کنید.")
                return AWAITING_ADMIN_INPUT
        elif field in ("days", "price"):
            try:
                value = int(text)
            except ValueError:
                await update.message.reply_text("لطفا فقط عدد صحیح وارد کنید.")
                return AWAITING_ADMIN_INPUT
        await database.update_package_field(package_id, field, value)
        await update.message.reply_text("✅ به‌روزرسانی شد.")
        context.user_data.pop("admin_action", None)
        context.user_data.pop("admin_target_id", None)
        return ConversationHandler.END

    if action == "setting_welcome_photo":
        if text == "0":
            await database.set_setting("welcome_photo_id", "")
            await update.message.reply_text("✅ تصویر خوش‌آمدگویی حذف شد.")
            context.user_data.pop("admin_action", None)
            return ConversationHandler.END
        await update.message.reply_text("لطفا یک تصویر ارسال کنید یا برای حذف تصویر فعلی عدد 0 را بفرستید.")
        return AWAITING_ADMIN_INPUT

    if action and action.startswith("setting_"):
        setting_key = action.replace("setting_", "")
        await database.set_setting(setting_key, text)
        await update.message.reply_text("✅ ذخیره شد.")
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

    if action == "add_admin_id":
        try:
            new_admin_id = int(text)
        except ValueError:
            await update.message.reply_text("لطفا فقط آیدی عددی وارد کنید.")
            return AWAITING_ADMIN_INPUT
        await database.add_admin(new_admin_id, update.effective_user.id)
        await update.message.reply_text(f"✅ کاربر {new_admin_id} به ادمین‌ها اضافه شد.")
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

    await update.message.reply_text("متوجه نشدم. لطفا از منو استفاده کنید. /cancel")
    return ConversationHandler.END


async def admin_receive_photo_input(update, context):
    action = context.user_data.get("admin_action")
    if action == "setting_welcome_photo":
        photo = update.message.photo[-1]
        await database.set_setting("welcome_photo_id", photo.file_id)
        await update.message.reply_text("✅ تصویر خوش‌آمدگویی ذخیره شد.")
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END
    await update.message.reply_text("در این مرحله ارسال تصویر امکان ندارد. لطفا متن وارد کنید یا /cancel بزنید.")
    return AWAITING_ADMIN_INPUT


async def admin_cancel(update, context):
    context.user_data.pop("admin_action", None)
    context.user_data.pop("new_package", None)
    context.user_data.pop("admin_target_id", None)
    await update.message.reply_text("عملیات لغو شد.")
    return ConversationHandler.END


# ==================== جدول مسیریابی دکمه‌های متنی (باید در پایین فایل باشد) ====================

MAIN_MENU_ROUTES = {
    BTN_BUY: buy_show_packages,
    BTN_TRIAL: trial_run,
    BTN_ACCOUNT: account_show,
    BTN_SUPPORT: support_show,
    BTN_ADMIN_PANEL: admin_panel_show,
    BTN_ADMIN_PACKAGES: admin_packages_show,
    BTN_ADMIN_CARD: admin_card_show,
    BTN_ADMIN_FORCEJOIN: admin_forcejoin_show,
    BTN_ADMIN_TRIAL: admin_trial_show,
    BTN_ADMIN_ADMINS: admin_admins_show,
    BTN_ADMIN_STATS: admin_stats_show,
    BTN_ADMIN_TURN_OFF: admin_toggle_bot,
    BTN_ADMIN_TURN_ON: admin_toggle_bot,
    BTN_BACK_MAIN: back_to_main_from_admin,
}
