# -*- coding: utf-8 -*-
# handlers.py
# تمام هندلرهای ربات: منوی اصلی، خرید سرویس، تست رایگان، حساب من، و پنل مدیریت

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler

import config
import database
import marzban_api
import qr_util

# شناسه‌های وضعیت مکالمه
(SELECT_RECEIPT,) = range(100, 101)
AWAITING_ADMIN_INPUT = 200


# ==================== منوی اصلی ====================

def main_menu_keyboard(is_admin_user: bool) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("\U0001F6D2 خرید سرویس", callback_data="menu_buy")],
        [InlineKeyboardButton("\U0001F381 تست رایگان", callback_data="menu_trial")],
        [InlineKeyboardButton("\U0001F464 سرویس‌های من", callback_data="menu_account")],
    ]
    if is_admin_user:
        keyboard.append([InlineKeyboardButton("\u2699\uFE0F پنل مدیریت", callback_data="menu_admin")])
    return InlineKeyboardMarkup(keyboard)


async def send_main_menu(chat_id, context, user_id):
    welcome_text = await database.get_setting("welcome_text", "خوش آمدید")
    is_admin_user = await database.is_admin(user_id)
    await context.bot.send_message(chat_id=chat_id, text=welcome_text, reply_markup=main_menu_keyboard(is_admin_user))


async def check_membership(context, channel, user_id) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False


async def show_join_gate(chat_id, context, channel):
    channel_display = channel if channel.startswith("http") else "https://t.me/" + channel.lstrip("@")
    keyboard = [
        [InlineKeyboardButton("\U0001F4E2 عضویت در کانال", url=channel_display)],
        [InlineKeyboardButton("\u2705 عضو شدم", callback_data="check_join")],
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
        await query.answer("عضویت شما تایید شد \u2705")
        await query.message.delete()
        await send_main_menu(query.message.chat_id, context, query.from_user.id)
    else:
        await query.answer("هنوز در کانال عضو نشده‌اید.", show_alert=True)


async def back_main_callback(update, context):
    query = update.callback_query
    await query.answer()
    is_admin_user = await database.is_admin(query.from_user.id)
    welcome_text = await database.get_setting("welcome_text", "خوش آمدید")
    await query.edit_message_text(welcome_text, reply_markup=main_menu_keyboard(is_admin_user))


# ==================== خرید سرویس ====================

async def menu_buy_callback(update, context):
    query = update.callback_query
    await query.answer()
    packages = await database.list_packages(active_only=True)
    if not packages:
        await query.answer("در حال حاضر پلنی برای فروش وجود ندارد.", show_alert=True)
        return
    keyboard = [
        [InlineKeyboardButton(
            f"{p['name']} — {p['gb']} گیگ / {p['days']} روز — {p['price']:,} تومان",
            callback_data=f"pkg:{p['id']}",
        )]
        for p in packages
    ]
    keyboard.append([InlineKeyboardButton("\U0001F519 بازگشت", callback_data="back_main")])
    await query.edit_message_text("لطفا یکی از پلن‌های زیر را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard))


async def select_package(update, context):
    query = update.callback_query
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
        f"\U0001F4B3 شماره کارت: {card_number}\n\U0001F464 به نام: {card_holder}\n\n"
        "لطفا بعد از واریز، تصویر رسید پرداخت را همینجا ارسال کنید. (برای لفو: /cancel)"
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

    await update.message.reply_text("\u2705 رسید شما ثبت شد و برای بررسی به ادمین ارسال شد. لطفا منتظر تایید بمانید.")

    caption = (
        f"\U0001F9FE سفارش جدید #{order_id}\n"
        f"کاربر: {update.effective_user.mention_html()} (آیدی: {update.effective_user.id})\n"
        f"پلن: {package['name']} — {package['gb']} گیگ / {package['days']} روز — {package['price']:,} تومان"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\u2705 تایید", callback_data=f"order_approve:{order_id}"),
            InlineKeyboardButton("\u274C رد", callback_data=f"order_reject:{order_id}"),
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
    await update.message.reply_text("خرید لفو شد.")
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
            f"\u2705 سرویسِ «{package['name']}» آماده شد — {package['gb']} گیگ \u00b7 {package['days']} روز\n\n"
            f"\U0001F517 لینکِ اشتراک:\n{link}"
        )
        await context.bot.send_photo(chat_id=order["user_id"], photo=qr_bytes, caption=caption)
        old_caption = query.message.caption or ""
        await query.edit_message_caption(caption=old_caption + "\n\n\u2705 تأیید شد.")
    except Exception as error:
        old_caption = query.message.caption or ""
        await query.edit_message_caption(caption=old_caption + f"\n\n\u274C خطا در ساخت سرویس:\n{error}")
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
    await query.edit_message_caption(caption=old_caption + "\n\n\u274C رد شد.")
    try:
        await context.bot.send_message(
            chat_id=order["user_id"],
            text="\u274C متأسفانه رسید پرداخت شما تأیید نشد. لطفا با ادمین در ارتباط باشید.",
        )
    except Exception:
        pass


# ==================== تست رایگان ====================

async def menu_trial_callback(update, context):
    query = update.callback_query
    user_id = query.from_user.id
    trial_enabled = await database.get_setting("trial_enabled", "0")
    if trial_enabled != "1":
        await query.answer("در حال حاضر تست رایگان فعال نیست.", show_alert=True)
        return
    if await database.has_used_trial(user_id):
        await query.answer("شما قبلاً از تست رایگان استفاده کرده‌اید.", show_alert=True)
        return
    await query.answer("در حال ساخت سرویس...")
    gb = float(await database.get_setting("trial_gb", "1"))
    days = int(await database.get_setting("trial_days", "1"))
    try:
        user_data = await marzban_api.create_user(gb, days)
        link = marzban_api.extract_subscription_link(user_data)
        await database.mark_trial_used(user_id)
        qr_bytes = qr_util.make_qr_bytes(link)
        caption = (
            f"\u2705 سرویسِ «تست رایگان» آماده شد — {gb} گیگ \u00b7 {days} روز\n\n"
            f"\U0001F517 لینکِ اشتراک:\n{link}"
        )
        await context.bot.send_photo(chat_id=user_id, photo=qr_bytes, caption=caption)
    except Exception as error:
        await context.bot.send_message(
            chat_id=user_id,
            text="متأسفانه در حال حاضر امکان ساخت سرویس وجود ندارد. لطفا بعداً تلاش کنید.",
        )
        try:
            await context.bot.send_message(chat_id=config.ADMIN_ID, text=f"خطا در ساخت تست رایگان برای {user_id}:\n{error}")
        except Exception:
            pass


# ==================== حساب من ====================

async def menu_account_callback(update, context):
    query = update.callback_query
    await query.answer()
    orders = await database.list_orders_by_user(query.from_user.id)
    approved = [o for o in orders if o["status"] == "approved"]
    if not approved:
        await query.message.reply_text("شما هنوز هیچ سرویس فعالی ندارید.")
        return
    lines = ["\U0001F4CB سرویس‌های شما:\n"]
    for o in approved:
        lines.append(f"\u2022 {o['subscription_link']}")
    await query.message.reply_text("\n".join(lines))


# ==================== پنل مدیریت ====================

def admin_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001F4E6 مدیریت پلن‌ها", callback_data="admin_packages")],
        [InlineKeyboardButton("\U0001F4B3 شماره کارت", callback_data="admin_card")],
        [InlineKeyboardButton("\U0001F512 عضویت اجباری", callback_data="admin_forcejoin")],
        [InlineKeyboardButton("\U0001F381 تست رایگان", callback_data="admin_trial")],
        [InlineKeyboardButton("\U0001F465 مدیریت ادمین‌ها", callback_data="admin_admins")],
        [InlineKeyboardButton("\U0001F4DD متن خوش‌آمدگویی", callback_data="admin_welcome")],
        [InlineKeyboardButton("\U0001F4CA آمار", callback_data="admin_stats")],
        [InlineKeyboardButton("\U0001F519 بازگشت", callback_data="back_main")],
    ])


async def require_admin(query, user_id) -> bool:
    if await database.is_admin(user_id):
        return True
    await query.answer("شما دسترسی ادمین ندارید.", show_alert=True)
    return False


async def menu_admin_callback(update, context):
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return
    await query.answer()
    await query.edit_message_text("\u2699\uFE0F پنل مدیریت:", reply_markup=admin_menu_keyboard())


# ---- مدیریت پلن‌ها ----

async def admin_packages_callback(update, context):
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return
    await query.answer()
    packages = await database.list_packages()
    keyboard = []
    for p in packages:
        status_icon = "\U0001F7E2" if p["active"] else "\U0001F534"
        keyboard.append([InlineKeyboardButton(
            f"{status_icon} {p['name']} — {p['gb']}گیگ/{p['days']}روز — {p['price']:,}ت",
            callback_data=f"admin_pkg_edit:{p['id']}",
        )])
    keyboard.append([InlineKeyboardButton("\u2795 افزودن پلن جدید", callback_data="admin_pkg_add")])
    keyboard.append([InlineKeyboardButton("\U0001F519 بازگشت", callback_data="menu_admin")])
    await query.edit_message_text("\U0001F4E6 پلن‌های موجود (برای ویرایش روی هرکدام بزنید):", reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_pkg_add_start(update, context):
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return ConversationHandler.END
    context.user_data["admin_action"] = "pkg_add_name"
    context.user_data["new_package"] = {}
    await query.answer()
    await query.message.reply_text("نام پلن جدید را وارد کنید: (برای لفو: /cancel)")
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
    status_text = "فعال \U0001F7E2" if package["active"] else "فیرفعال \U0001F534"
    keyboard = [
        [InlineKeyboardButton("\u270F\uFE0F نام", callback_data=f"admin_pkg_field:{package_id}:name")],
        [InlineKeyboardButton("\u270F\uFE0F حجم (گیگ)", callback_data=f"admin_pkg_field:{package_id}:gb")],
        [InlineKeyboardButton("\u270F\uFE0F روز اعتبار", callback_data=f"admin_pkg_field:{package_id}:days")],
        [InlineKeyboardButton("\u270F\uFE0F قیمت", callback_data=f"admin_pkg_field:{package_id}:price")],
        [InlineKeyboardButton(f"وضعیت: {status_text} (تعویض)", callback_data=f"admin_pkg_toggle:{package_id}")],
        [InlineKeyboardButton("\U0001F5D1 حذف پلن", callback_data=f"admin_pkg_delete:{package_id}")],
        [InlineKeyboardButton("\U0001F519 بازگشت", callback_data="admin_packages")],
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
    await query.message.reply_text(f"مقدار جدید برای «{field_names.get(field, field)}» را وارد کنید: (/cancel برای لفو)")
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


# ---- شماره کارت / عضویت اجباری / تست / متن خوش‌آمدگویی ----

async def admin_card_callback(update, context):
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return
    await query.answer()
    card_number = await database.get_setting("card_number", "-")
    card_holder = await database.get_setting("card_holder", "-")
    keyboard = [
        [InlineKeyboardButton("\u270F\uFE0F تعییر شماره کارت", callback_data="admin_set_card_number")],
        [InlineKeyboardButton("\u270F\uFE0F تعییر نام صاحب کارت", callback_data="admin_set_card_holder")],
        [InlineKeyboardButton("\U0001F519 بازگشت", callback_data="menu_admin")],
    ]
    await query.edit_message_text(
        f"\U0001F4B3 شماره کارت فعلی: {card_number}\n\U0001F464 به نام: {card_holder}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


ASYNC_SETTING_ACTIONS = {
    "admin_set_card_number": ("card_number", "شماره کارت جدید را وارد کنید:"),
    "admin_set_card_holder": ("card_holder", "نام صاحب کارت را وارد کنید:"),
    "admin_forcejoin_setchannel": ("force_join_channel", "ایدی عددی یا یوزرنیم کانال را وارد کنید (مثلا @mychannel):"),
    "admin_trial_setgb": ("trial_gb", "حجم تست رایگان را به گیگابایت وارد کنید:"),
    "admin_trial_setdays": ("trial_days", "تعداد روز اعتبار تست رایگان را وارد کنید:"),
    "admin_welcome": ("welcome_text", "متن خوش‌آمدگویی جدید را وارد کنید:"),
}


async def admin_generic_setting_start(update, context):
    """شروع دریافت مقدار جدید برای یک تنظیم ساده (بر اساس callback_data)"""
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return ConversationHandler.END
    setting_key, prompt = ASYNC_SETTING_ACTIONS[query.data]
    context.user_data["admin_action"] = f"setting_{setting_key}"
    await query.answer()
    await query.message.reply_text(prompt + " (/cancel برای لفو)")
    return AWAITING_ADMIN_INPUT


async def admin_forcejoin_callback(update, context):
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return
    await query.answer()
    enabled = await database.get_setting("force_join_enabled", "0") == "1"
    channel = await database.get_setting("force_join_channel", "-")
    keyboard = [
        [InlineKeyboardButton(f"وضعیت: {'فعال \U0001F7E2' if enabled else 'فیرفعال \U0001F534'} (تعویض)", callback_data="admin_forcejoin_toggle")],
        [InlineKeyboardButton("\u270F\uFE0F تعییر کانال", callback_data="admin_forcejoin_setchannel")],
        [InlineKeyboardButton("\U0001F519 بازگشت", callback_data="menu_admin")],
    ]
    await query.edit_message_text(f"\U0001F512 عضویت اجباری\nکانال فعلی: {channel}", reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_forcejoin_toggle(update, context):
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return
    current = await database.get_setting("force_join_enabled", "0")
    await database.set_setting("force_join_enabled", "0" if current == "1" else "1")
    await admin_forcejoin_callback(update, context)


async def admin_trial_callback(update, context):
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return
    await query.answer()
    enabled = await database.get_setting("trial_enabled", "0") == "1"
    gb = await database.get_setting("trial_gb", "1")
    days = await database.get_setting("trial_days", "1")
    keyboard = [
        [InlineKeyboardButton(f"وضعیت: {'فعال \U0001F7E2' if enabled else 'فیرفعال \U0001F534'} (تعویض)", callback_data="admin_trial_toggle")],
        [InlineKeyboardButton("\u270F\uFE0F تعییر حجم", callback_data="admin_trial_setgb")],
        [InlineKeyboardButton("\u270F\uFE0F تعییر روز", callback_data="admin_trial_setdays")],
        [InlineKeyboardButton("\U0001F519 بازگشت", callback_data="menu_admin")],
    ]
    await query.edit_message_text(f"\U0001F381 تست رایگان\nحجم: {gb} گیگ | روز: {days}", reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_trial_toggle(update, context):
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return
    current = await database.get_setting("trial_enabled", "0")
    await database.set_setting("trial_enabled", "0" if current == "1" else "1")
    await admin_trial_callback(update, context)


# ---- مدیریت ادمین‌ها ----

async def admin_admins_callback(update, context):
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return
    await query.answer()
    admins = await database.list_admins()
    lines = [f"\U0001F451 ادمین اصلی: {config.ADMIN_ID}"]
    keyboard = []
    for a in admins:
        lines.append(f"\U0001F464 ادمین: {a['user_id']}")
        keyboard.append([InlineKeyboardButton(f"\U0001F5D1 حذف {a['user_id']}", callback_data=f"admin_admins_remove:{a['user_id']}")])
    keyboard.append([InlineKeyboardButton("\u2795 افزودن ادمین جدید", callback_data="admin_admins_add")])
    keyboard.append([InlineKeyboardButton("\U0001F519 بازگشت", callback_data="menu_admin")])
    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_admins_add_start(update, context):
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return ConversationHandler.END
    context.user_data["admin_action"] = "add_admin_id"
    await query.answer()
    await query.message.reply_text(
        "آیدی عددی کاربری که می‌خواهید ادمین شود را وارد کنید.\n"
        "(کاربر باید قبلاً یک بار /start ربات را زده باشد) (/cancel برای لفو)"
    )
    return AWAITING_ADMIN_INPUT


async def admin_admins_remove(update, context):
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return
    target_id = int(query.data.split(":")[1])
    removed = await database.remove_admin(target_id)
    if removed:
        await query.answer("ادمین حذف شد.")
    else:
        await query.answer("این کاربر ادمین اصلی است و قابل حذف نیست.", show_alert=True)
    await admin_admins_callback(update, context)


async def admin_stats_callback(update, context):
    query = update.callback_query
    if not await require_admin(query, query.from_user.id):
        return
    await query.answer()
    total_users = await database.count_users()
    stats = await database.order_stats()
    text = (
        f"\U0001F4CA آمار ربات\n\n"
        f"\U0001F465 تعداد کاربران: {total_users}\n"
        f"\U0001F9FE سفارش‌های در انتظار: {stats.get('pending', 0)}\n"
        f"\u2705 سفارش‌های تأییدشده: {stats.get('approved', 0)}\n"
        f"\u274C سفارش‌های ردشده: {stats.get('rejected', 0)}"
    )
    keyboard = [[InlineKeyboardButton("\U0001F519 بازگشت", callback_data="menu_admin")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


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
        await update.message.reply_text(f"\u2705 پلن «{pkg['name']}» اضافه شد.")
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
        await update.message.reply_text("\u2705 به‌روزرسانی شد.")
        context.user_data.pop("admin_action", None)
        context.user_data.pop("admin_target_id", None)
        return ConversationHandler.END

    if action and action.startswith("setting_"):
        setting_key = action.replace("setting_", "")
        await database.set_setting(setting_key, text)
        await update.message.reply_text("\u2705 ذخیره شد.")
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

    if action == "add_admin_id":
        try:
            new_admin_id = int(text)
        except ValueError:
            await update.message.reply_text("لطفا فقط آیدی عددی وارد کنید.")
            return AWAITING_ADMIN_INPUT
        await database.add_admin(new_admin_id, update.effective_user.id)
        await update.message.reply_text(f"\u2705 کاربر {new_admin_id} به ادمین‌ها اضافه شد.")
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

    await update.message.reply_text("متوجه نشدم. لطفا از منو استفاده کنید. /cancel")
    return ConversationHandler.END


async def admin_cancel(update, context):
    context.user_data.pop("admin_action", None)
    context.user_data.pop("new_package", None)
    context.user_data.pop("admin_target_id", None)
    await update.message.reply_text("عملیات لفو شد.")
    return ConversationHandler.END
