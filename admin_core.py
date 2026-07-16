# -*- coding: utf-8 -*-
# admin_core.py - پنل مدیریت: تنطیمات، پلن‌ها، ادمین‌ها، و دسترساز ورودی متنی پنل مدیریت

import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler

import config
import database
import marzban_api
import handlers as h


async def admin_panel_show(update, context):
    if not await database.is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ شما دسترسی ادمین ندارید.")
        return
    perms = await h.admin_perms_for(update.effective_user.id)
    enabled = await database.get_setting("bot_enabled", "1") == "1"
    role = await database.get_admin_role(update.effective_user.id) or "full"
    status_text = "🟢 روشن" if enabled else "🔴 خاموش"
    text = (
        "⚙️ پنل مدیریت\n━━━━━━━━━━━━━━━\n"
        f"سطح دسترسی: {role}\nوضعیت ربات: {status_text}\n\nاز دکمه‌های زیر استفاده کنید 👇"
    )
    await update.message.reply_text(text, reply_markup=h.admin_menu_keyboard(perms, enabled))


async def admin_toggle_bot(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "bot_toggle"):
        return
    current = await database.get_setting("bot_enabled", "1")
    new_value = "0" if current == "1" else "1"
    await database.set_setting("bot_enabled", new_value)
    enabled = new_value == "1"
    await database.log_action("admin", update.effective_user.id, "تغییر وضعیت ربات به " + ("روشن" if enabled else "خاموش"))
    msg = "🟢 ربات روشن شد و برای همه فعال است." if enabled else "🔴 ربات خاموش شد. فقط ادمین‌ها دسترسی دارند."
    perms = await h.admin_perms_for(update.effective_user.id)
    await update.message.reply_text("✅ " + msg, reply_markup=h.admin_menu_keyboard(perms, enabled))


# ---------------- تنطیمات عمومی (یک پیام = یک مقدار) ----------------

async def admin_generic_setting_start(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "settings"):
        return ConversationHandler.END
    action = query.data
    if action not in h.ASYNC_SETTING_ACTIONS:
        await query.answer("نامعتبر", show_alert=True)
        return ConversationHandler.END
    _, prompt = h.ASYNC_SETTING_ACTIONS[action]
    context.user_data["admin_action"] = action
    await query.answer()
    await query.message.reply_text(prompt + " (لغو: /cancel)")
    return h.AWAITING_ADMIN_INPUT


async def admin_welcome_start(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "settings"):
        return ConversationHandler.END
    context.user_data["admin_action"] = "admin_set_welcome_text"
    await update.message.reply_text("متن جدید خوش‌آمدگویی را وارد کنید: (لغو: /cancel)")
    return h.AWAITING_ADMIN_INPUT


async def admin_welcome_photo_start(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "settings"):
        return ConversationHandler.END
    context.user_data["admin_action"] = "admin_set_welcome_photo"
    await update.message.reply_text("🖼️ عکس جدید را ارسال کنید، یا 0 را بفرستید تا عکس حذف شود. (لغو: /cancel)")
    return h.AWAITING_ADMIN_INPUT


async def admin_receive_photo_input(update, context):
    action = context.user_data.get("admin_action")
    if action == "admin_set_welcome_photo":
        file_id = update.message.photo[-1].file_id
        await database.set_setting("welcome_photo_id", file_id)
        await update.message.reply_text("✅ عکس خوش‌آمدگویی ثبت شد.")
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END
    await update.message.reply_text("لطفا متن وارد کنید، نه عکس.")
    return h.AWAITING_ADMIN_INPUT


# ---------------- پلن‌ها ----------------

async def _packages_view():
    packages = await database.list_packages()
    keyboard = []
    for p in packages:
        icon = "🟢" if p["active"] else "🔴"
        keyboard.append([InlineKeyboardButton(
            f"{icon} {p['name']} — {p['gb']}گیگ/{p['days']}روز — {p['price']:,}ت",
            callback_data=f"admin_pkg_edit:{p['id']}",
        )])
    keyboard.append([InlineKeyboardButton("➕ افزودن پلن جدید", callback_data="admin_pkg_add")])
    text = "📦 پلن‌های موجود\n━━━━━━━━━━━━━━━\nبرای ویرایش، روی پلن بزنید 👇"
    if not packages:
        text = "📦 هنوز پلنی ثبت نشده."
    return text, InlineKeyboardMarkup(keyboard)


async def admin_packages_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "packages"):
        return
    text, keyboard = await _packages_view()
    await update.message.reply_text(text, reply_markup=keyboard)


async def admin_packages_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "packages"):
        return
    await query.answer()
    text, keyboard = await _packages_view()
    await query.edit_message_text(text, reply_markup=keyboard)


async def admin_pkg_add_start(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "packages"):
        return ConversationHandler.END
    context.user_data["new_package"] = {}
    context.user_data["admin_action"] = "pkg_add_name"
    await query.answer()
    await query.message.reply_text("📦 نام پلن جدید را وارد کنید: (لغو: /cancel)")
    return h.AWAITING_ADMIN_INPUT


async def admin_pkg_edit_menu(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "packages"):
        return
    package_id = int(query.data.split(":")[1])
    package = await database.get_package(package_id)
    if not package:
        await query.answer("پلن پیدا نشد.", show_alert=True)
        return
    await query.answer()
    icon = "🟢 فعال" if package["active"] else "🔴 معلق"
    text = (
        f"📦 {package['name']}\nحجم: {package['gb']} گیگ\nمدت: {package['days']} روز\n"
        f"قیمت: {package['price']:,} تومان\nوضعیت: {icon}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ نام", callback_data=f"admin_pkg_field:{package_id}:name"),
         InlineKeyboardButton("✏️ حجم", callback_data=f"admin_pkg_field:{package_id}:gb")],
        [InlineKeyboardButton("✏️ مدت", callback_data=f"admin_pkg_field:{package_id}:days"),
         InlineKeyboardButton("✏️ قیمت", callback_data=f"admin_pkg_field:{package_id}:price")],
        [InlineKeyboardButton("🔄 فعال/معلق", callback_data=f"admin_pkg_toggle:{package_id}")],
        [InlineKeyboardButton("🗑 حذف پلن", callback_data=f"admin_pkg_delete:{package_id}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_packages")],
    ])
    await query.edit_message_text(text, reply_markup=keyboard)


async def admin_pkg_field_start(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "packages"):
        return ConversationHandler.END
    _, package_id, field = query.data.split(":")
    context.user_data["admin_action"] = f"pkg_edit_{field}:{package_id}"
    prompts = {"name": "نام جدید", "gb": "حجم جدید (گیگابایت)", "days": "مدت جدید (روز)", "price": "قیمت جدید (تومان)"}
    await query.answer()
    await query.message.reply_text(f"{prompts.get(field, field)} را وارد کنید: (لغو: /cancel)")
    return h.AWAITING_ADMIN_INPUT


async def admin_pkg_toggle(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "packages"):
        return
    package_id = int(query.data.split(":")[1])
    package = await database.get_package(package_id)
    if not package:
        await query.answer("پلن پیدا نشد.", show_alert=True)
        return
    await database.update_package_field(package_id, "active", 0 if package["active"] else 1)
    await query.answer("وضعیت عوض شد.")
    await admin_pkg_edit_menu(update, context)


async def admin_pkg_delete(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "packages"):
        return
    package_id = int(query.data.split(":")[1])
    await database.delete_package(package_id)
    await query.answer("پلن حذف شد.")
    await database.log_action("admin", query.from_user.id, f"حذف پلن #{package_id}")
    text, keyboard = await _packages_view()
    await query.edit_message_text(text, reply_markup=keyboard)


# ---------------- مدیریت ادمین‌ها ----------------

async def admin_admins_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "admins"):
        return
    admins = await database.list_admins()
    lines = ["👮 ادمین‌ها\n━━━━━━━━━━━━━━━", f"👑 ادمین اصلی: {config.ADMIN_ID}"]
    keyboard = []
    for a in admins:
        lines.append(f"• {a['user_id']} — سطح: {a['role']}")
        keyboard.append([InlineKeyboardButton(f"🗑 حذف {a['user_id']}", callback_data=f"admin_admins_remove:{a['user_id']}")])
    keyboard.append([InlineKeyboardButton("➕ افزودن ادمین", callback_data="admin_admins_add")])
    await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_admins_add_start(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "admins"):
        return ConversationHandler.END
    context.user_data["admin_action"] = "add_admin_id"
    await query.answer()
    await query.message.reply_text("👮 آیدی عددی تلگرام ادمین جدید را وارد کنید: (لغو: /cancel)")
    return h.AWAITING_ADMIN_INPUT


async def admin_role_set_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "admins"):
        return
    _, target_id, role = query.data.split(":")
    await database.add_admin(int(target_id), query.from_user.id, role=role)
    await database.log_action("admin", query.from_user.id, f"افزودن ادمین {target_id} با سطح {role}")
    await query.answer("✅ انجام شد.")
    await query.edit_message_text(f"✅ کاربر {target_id} با سطح دسترسی «{role}» به ادمین اضافه شد.")


async def admin_admins_remove(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "admins"):
        return
    target_id = int(query.data.split(":")[1])
    removed = await database.remove_admin(target_id)
    if removed:
        await database.log_action("admin", query.from_user.id, f"حذف ادمین {target_id}")
        await query.answer("حذف شد.")
    else:
        await query.answer("این ادمین اصلی قابل حذف نیست.", show_alert=True)
        return
    admins = await database.list_admins()
    lines = ["👮 ادمین‌ها\n━━━━━━━━━━━━━━━", f"👑 ادمین اصلی: {config.ADMIN_ID}"]
    keyboard = []
    for a in admins:
        lines.append(f"• {a['user_id']} — سطح: {a['role']}")
        keyboard.append([InlineKeyboardButton(f"🗑 حذف {a['user_id']}", callback_data=f"admin_admins_remove:{a['user_id']}")])
    keyboard.append([InlineKeyboardButton("➕ افزودن ادمین", callback_data="admin_admins_add")])
    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_cancel(update, context):
    for key in ("admin_action", "new_package", "new_discount", "new_server", "reply_ticket_id"):
        context.user_data.pop(key, None)
    await update.message.reply_text("عملیات لغو شد.")
    return ConversationHandler.END


# ---------------- زیرمنوهای تنطیمات (کارت ، عضویت اجباری، تست رایگان، قوانین، پشتیبانی) ----------------

async def admin_card_settings_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "settings"):
        return
    card_number = await database.get_setting("card_number", "-")
    card_holder = await database.get_setting("card_holder", "-")
    text = f"💳 شماره کارت: {card_number}\n👤 به نام: {card_holder}"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ ویرایش شماره کارت", callback_data="admin_set_card_number")],
        [InlineKeyboardButton("✏️ ویرایش نام صاحب کارت", callback_data="admin_set_card_holder")],
    ])
    await update.message.reply_text(text, reply_markup=keyboard)


async def admin_forcejoin_settings_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "settings"):
        return
    enabled = await database.get_setting("force_join_enabled", "0") == "1"
    channel = await database.get_setting("force_join_channel", "") or "تنظیم نشده"
    status_icon = "🟢 فعال" if enabled else "🔴 معلق"
    text = f"🔒 عضویت اجباری\nوضعیت: {status_icon}\nکانال: {channel}"
    toggle_label = "🔴 معلول کردن" if enabled else "🟢 فعال کردن"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(toggle_label, callback_data="admin_forcejoin_toggle")],
        [InlineKeyboardButton("✏️ تنطیم کانال", callback_data="admin_set_forcejoin_channel")],
    ])
    await update.message.reply_text(text, reply_markup=keyboard)


async def admin_forcejoin_toggle(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "settings"):
        return
    current = await database.get_setting("force_join_enabled", "0")
    await database.set_setting("force_join_enabled", "0" if current == "1" else "1")
    await database.log_action("admin", query.from_user.id, "توگل عضویت اجباری")
    await query.answer("وضعیت عوض شد.")
    await query.message.reply_text("✅ وضعیت عضویت اجباری تعویض شد.")


async def admin_trial_settings_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "settings"):
        return
    enabled = await database.get_setting("trial_enabled", "0") == "1"
    gb = await database.get_setting("trial_gb", "1")
    days = await database.get_setting("trial_days", "1")
    status_icon = "🟢 فعال" if enabled else "🔴 معلق"
    text = f"🎁 تست رایگان\nوضعیت: {status_icon}\nحجم: {gb} گیگ\nمدت: {days} روز"
    toggle_label = "🔴 معلول کردن" if enabled else "🟢 فعال کردن"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(toggle_label, callback_data="admin_trial_toggle")],
        [InlineKeyboardButton("✏️ حجم", callback_data="admin_set_trial_gb"),
         InlineKeyboardButton("✏️ مدت", callback_data="admin_set_trial_days")],
    ])
    await update.message.reply_text(text, reply_markup=keyboard)


async def admin_trial_toggle(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "settings"):
        return
    current = await database.get_setting("trial_enabled", "0")
    await database.set_setting("trial_enabled", "0" if current == "1" else "1")
    await database.log_action("admin", query.from_user.id, "توگل وضعیت تست رایگان")
    await query.answer("وضعیت عوض شد.")
    await query.message.reply_text("✅ وضعیت تست رایگان تعویض شد.")


async def admin_rules_settings_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "settings"):
        return
    rules_text = await database.get_setting("rules_text", "-")
    contact_links = await database.get_setting("contact_links", "-")
    text = f"📜 قوانین:\n{rules_text}\n\n🔗 لینک‌های ارتباطی:\n{contact_links}"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ ویرایش قوانین", callback_data="admin_set_rules_text")],
        [InlineKeyboardButton("✏️ ویرایش لینک‌ها", callback_data="admin_set_contact_links")],
    ])
    await update.message.reply_text(text, reply_markup=keyboard)


async def admin_support_settings_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "settings"):
        return
    support_text = await database.get_setting("support_text", "-")
    support_username = await database.get_setting("support_username", "-")
    text = f"🆘 متن پشتیبانی:\n{support_text}\n\n👤 ایدی پشتیبانی: {support_username}"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ ویرایش متن", callback_data="admin_set_support_text")],
        [InlineKeyboardButton("✏️ ویرایش ایدی", callback_data="admin_set_support_username")],
    ])
    await update.message.reply_text(text, reply_markup=keyboard)


# ---------------- دسترساز ورودی متنی اصلی پنل مدیریت ----------------

async def admin_receive_input(update, context):
    action = context.user_data.get("admin_action")
    text = (update.message.text or "").strip()
    if not action:
        return ConversationHandler.END

    # تنطیمات عمومی (یک مقدار متنی)
    if action in h.ASYNC_SETTING_ACTIONS:
        key, _ = h.ASYNC_SETTING_ACTIONS[action]
        await database.set_setting(key, text)
        await database.log_action("admin", update.effective_user.id, f"تنطیم {key} به‌روز شد")
        await update.message.reply_text("✅ با موفقیت ذخیره شد.")
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

    if action == "admin_set_welcome_text":
        await database.set_setting("welcome_text", text)
        await update.message.reply_text("✅ متن خوش‌آمدگویی به‌روز شد.")
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

    if action == "admin_set_welcome_photo":
        if text == "0":
            await database.set_setting("welcome_photo_id", "")
            await update.message.reply_text("✅ عکس خوش‌آمدگویی حذف شد.")
            context.user_data.pop("admin_action", None)
            return ConversationHandler.END
        await update.message.reply_text("لطفا یک عکس ارسال کنید یا 0 را بفرستید.")
        return h.AWAITING_ADMIN_INPUT

    # ---- افزودن پلن (مراحلی) ----
    if action == "pkg_add_name":
        context.user_data["new_package"] = {"name": text}
        context.user_data["admin_action"] = "pkg_add_gb"
        await update.message.reply_text("حجم پلن را به گیگابایت وارد کنید:")
        return h.AWAITING_ADMIN_INPUT
    if action == "pkg_add_gb":
        try:
            gb = float(text)
        except ValueError:
            await update.message.reply_text("لطفا فقط عدد وارد کنید.")
            return h.AWAITING_ADMIN_INPUT
        context.user_data["new_package"]["gb"] = gb
        context.user_data["admin_action"] = "pkg_add_days"
        await update.message.reply_text("مدت اعتبار را به روز وارد کنید:")
        return h.AWAITING_ADMIN_INPUT
    if action == "pkg_add_days":
        try:
            days = int(text)
        except ValueError:
            await update.message.reply_text("لطفا فقط عدد وارد کنید.")
            return h.AWAITING_ADMIN_INPUT
        context.user_data["new_package"]["days"] = days
        context.user_data["admin_action"] = "pkg_add_price"
        await update.message.reply_text("قیمت را به تومان وارد کنید:")
        return h.AWAITING_ADMIN_INPUT
    if action == "pkg_add_price":
        try:
            price = int(text)
        except ValueError:
            await update.message.reply_text("لطفا فقط عدد وارد کنید.")
            return h.AWAITING_ADMIN_INPUT
        new_package = context.user_data.get("new_package", {})
        await database.add_package(new_package.get("name", "پلن"), new_package.get("gb", 0), new_package.get("days", 0), price)
        await database.log_action("admin", update.effective_user.id, f"افزودن پلن {new_package.get('name')}")
        await update.message.reply_text("✅ پلن جدید اضافه شد.")
        context.user_data.pop("new_package", None)
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

    # ---- ویرایش یک فیلد پلن ----
    if action.startswith("pkg_edit_"):
        field_part, package_id = action[len("pkg_edit_"):].split(":")
        try:
            if field_part in ("gb",):
                value = float(text)
            elif field_part in ("days", "price"):
                value = int(text)
            else:
                value = text
        except ValueError:
            await update.message.reply_text("لطفا مقدار معتبر وارد کنید.")
            return h.AWAITING_ADMIN_INPUT
        await database.update_package_field(int(package_id), field_part, value)
        await database.log_action("admin", update.effective_user.id, f"ویرایش پلن #{package_id} ({field_part})")
        await update.message.reply_text("✅ با موفقیت ویرایش شد.")
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

    # ---- افزودن ادمین ----
    if action == "add_admin_id":
        try:
            new_admin_id = int(text)
        except ValueError:
            await update.message.reply_text("لطفا فقط عدد (آیدی عددی) وارد کنید.")
            return h.AWAITING_ADMIN_INPUT
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("👑 دسترسی کامل", callback_data=f"admin_role_set:{new_admin_id}:full")],
            [InlineKeyboardButton("💰 فقط فروش", callback_data=f"admin_role_set:{new_admin_id}:sales")],
            [InlineKeyboardButton("🎧 فقط پشتیبانی", callback_data=f"admin_role_set:{new_admin_id}:support")],
        ])
        await update.message.reply_text("سطح دسترسی این ادمین را انتخاب کنید:", reply_markup=keyboard)
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

    # ---- کد تخفیف ----
    if action == "discount_add_code":
        context.user_data["new_discount"] = {"code": text.upper()}
        context.user_data["admin_action"] = "discount_add_kind"
        await update.message.reply_text("نوع تخفیف را وارد کنید: percent یا amount")
        return h.AWAITING_ADMIN_INPUT
    if action == "discount_add_kind":
        if text.lower() not in ("percent", "amount"):
            await update.message.reply_text("فقط percent یا amount معتبر است.")
            return h.AWAITING_ADMIN_INPUT
        context.user_data["new_discount"]["kind"] = text.lower()
        context.user_data["admin_action"] = "discount_add_value"
        await update.message.reply_text("مقدار تخفیف را وارد کنید (درصد یا تومان):")
        return h.AWAITING_ADMIN_INPUT
    if action == "discount_add_value":
        try:
            value = float(text)
        except ValueError:
            await update.message.reply_text("لطفا عدد وارد کنید.")
            return h.AWAITING_ADMIN_INPUT
        context.user_data["new_discount"]["value"] = value
        context.user_data["admin_action"] = "discount_add_maxuses"
        await update.message.reply_text("حداکثر تعداد استفاده را وارد کنید (0 = بی‌نهایت):")
        return h.AWAITING_ADMIN_INPUT
    if action == "discount_add_maxuses":
        try:
            max_uses = int(text)
        except ValueError:
            await update.message.reply_text("لطفا عدد وارد کنید.")
            return h.AWAITING_ADMIN_INPUT
        context.user_data["new_discount"]["max_uses"] = max_uses
        context.user_data["admin_action"] = "discount_add_expiredays"
        await update.message.reply_text("تعداد روز اعتبار کد را وارد کنید (0 = همیشه):")
        return h.AWAITING_ADMIN_INPUT
    if action == "discount_add_expiredays":
        try:
            expire_days = int(text)
        except ValueError:
            await update.message.reply_text("لطفا عدد وارد کنید.")
            return h.AWAITING_ADMIN_INPUT
        new_discount = context.user_data.get("new_discount", {})
        expires_at = None
        if expire_days > 0:
            expires_at = (datetime.datetime.utcnow() + datetime.timedelta(days=expire_days)).isoformat()
        await database.create_discount_code(
            new_discount.get("code"), new_discount.get("kind"), new_discount.get("value"),
            new_discount.get("max_uses", 0), expires_at,
        )
        await database.log_action("admin", update.effective_user.id, f"افزودن کد تخفیف {new_discount.get('code')}")
        await update.message.reply_text("✅ کد تخفیف اضافه شد.")
        context.user_data.pop("new_discount", None)
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

    # ---- سرورها ----
    if action == "server_add_name":
        context.user_data["new_server"] = {"name": text}
        context.user_data["admin_action"] = "server_add_address"
        await update.message.reply_text("آدرس سرور را وارد کنید (مثلا https://1.2.3.4):")
        return h.AWAITING_ADMIN_INPUT
    if action == "server_add_address":
        new_server = context.user_data.get("new_server", {})
        await database.add_server(new_server.get("name", "سرور"), text)
        await database.log_action("admin", update.effective_user.id, f"افزودن سرور {new_server.get('name')}")
        await update.message.reply_text("✅ سرور اضافه شد.")
        context.user_data.pop("new_server", None)
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

    # ---- مدیریت کاربران: جستجو، پیام خصوصی، همگانی ----
    if action == "user_search":
        results = await database.search_users(text)
        context.user_data.pop("admin_action", None)
        if not results:
            await update.message.reply_text("کاربری یافت نشد.")
            return ConversationHandler.END
        keyboard = [[InlineKeyboardButton(
            f"👤 {u['user_id']} @{u['username'] or '-'}", callback_data=f"user_view:{u['user_id']}",
        )] for u in results]
        await update.message.reply_text("🔍 نتایج جستجو:", reply_markup=InlineKeyboardMarkup(keyboard))
        return ConversationHandler.END

    if action.startswith("user_dm:"):
        target_id = int(action.split(":")[1])
        try:
            await context.bot.send_message(chat_id=target_id, text=f"📩 پیام از مدیریت:\n\n{text}")
            await update.message.reply_text("✅ ارسال شد.")
            await database.log_action("admin", update.effective_user.id, f"پیام خصوصی به {target_id}")
        except Exception:
            await update.message.reply_text("❌ ارسال نشد (امکان دارد کاربر ربات را بلاک کرده باشد).")
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

    if action == "broadcast_all":
        user_ids = await database.list_all_user_ids()
        sent = 0
        for uid in user_ids:
            try:
                await context.bot.send_message(chat_id=uid, text=text)
                sent += 1
            except Exception:
                pass
        await database.log_action("admin", update.effective_user.id, f"پیام همگانی به {sent} کاربر")
        await update.message.reply_text(f"✅ پیام به {sent} کاربر ارسال شد.")
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

    if action.startswith("broadcast_package:"):
        package_id = int(action.split(":")[1])
        user_ids = await database.list_user_ids_for_package(package_id)
        sent = 0
        for uid in user_ids:
            try:
                await context.bot.send_message(chat_id=uid, text=text)
                sent += 1
            except Exception:
                pass
        await database.log_action("admin", update.effective_user.id, f"پیام به کاربران پلن #{package_id} ({sent} نفر)")
        await update.message.reply_text(f"✅ پیام به {sent} کاربر این پلن ارسال شد.")
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

    # ---- مدیریت مرزبان: افزایش حجم/مدت ----
    if action.startswith("mz_incgb:"):
        username = action.split(":", 1)[1]
        try:
            add_gb = float(text)
        except ValueError:
            await update.message.reply_text("لطفا عدد وارد کنید.")
            return h.AWAITING_ADMIN_INPUT
        try:
            await marzban_api.increase_user_data(username, add_gb)
            await database.log_action("admin", update.effective_user.id, f"افزایش حجم {username} +{add_gb}GB")
            await update.message.reply_text("✅ حجم افزایش یافت.")
        except Exception as error:
            await update.message.reply_text(f"❌ خطا: {error}")
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

    if action.startswith("mz_extend:"):
        username = action.split(":", 1)[1]
        try:
            add_days = int(text)
        except ValueError:
            await update.message.reply_text("لطفا عدد وارد کنید.")
            return h.AWAITING_ADMIN_INPUT
        try:
            await marzban_api.extend_user_expire(username, add_days)
            await database.log_action("admin", update.effective_user.id, f"تمدید {username} +{add_days}روز")
            await update.message.reply_text("✅ مدت تمدید شد.")
        except Exception as error:
            await update.message.reply_text(f"❌ خطا: {error}")
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

    context.user_data.pop("admin_action", None)
    await update.message.reply_text("متوجه نشدم. /cancel را بزنید و دوباره امتحان کنید.")
    return ConversationHandler.END
