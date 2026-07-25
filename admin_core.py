# -*- coding: utf-8 -*-
# admin_core.py - پنل مدیریت: تنظیمات، پلن‌ها، ادمین‌ها، و دسترساز ورودی متنی پنل مدیریت

import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler

import config
import database
import marzban_api
import handlers as h
import admin_money


import asyncio

from telegram.error import RetryAfter, Forbidden, BadRequest


async def _broadcast_send(context, user_ids, text):
    """ارسال پیام همگانی با محافظت در برابر محدودیت نرخ تلگرام. کتابخانه python-telegram-bot
    از قبل با AIORateLimiter نرخ کلی درخواست‌ها را محدود می‌کند (نگاه کنید به bot.py)، اما اینجا
    یک لایه محافظتی اضافه هم می‌گذاریم: مکث کوتاه بین دسته‌های پیام و رعایت دقیق مدت‌زمان
    RetryAfter که خود تلگرام اعلام می‌کند، تا هیچ پیامی به خاطر FloodWait بی‌صدا گم نشود.
    متن در حالت HTML (مطابق سایر پیام‌های ادمین) ارسال می‌شود؛ اگر متن ادمین شامل کاراکترهایی مانند
    < یا & باشد که HTML معتبر نیست و تلگرام خطای entity parsing بدهد، برای همیشه به ارسال بدون
    قالب‌بندی (متن خام) برمی‌گرددتا پیام برای هیچ کاربری گم نشود."""
    sent = 0
    use_html = True
    html_downgraded = False
    for index, uid in enumerate(user_ids):
        for attempt in range(3):
            try:
                await context.bot.send_message(chat_id=uid, text=text, parse_mode="HTML" if use_html else None)
                sent += 1
                break
            except BadRequest as e:
                if use_html and "parse entities" in str(e).lower():
                    # تصمیم آگاهانه: اگر متن ادمین HTML معتبر نباشد، ترجیح می‌دهیم پیام برای
                    # همه‌ی کاربران (حتی آنهایی که قبلاً فرستاده شده) به صورت متن خام برسد تا
                    # پیام گم نشود، نه اینکه برای بقیه کاربران بی‌صدا شکست بخورد.
                    use_html = False
                    html_downgraded = True
                    continue
                break
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 0.5)
                continue
            except Forbidden:
                break
            except Exception:
                break
        if (index + 1) % 20 == 0:
            await asyncio.sleep(1.0)
    return sent, html_downgraded


async def _reply(update, text, reply_markup=None):
    if update.message is not None:
        return await update.message.reply_text(text, reply_markup=reply_markup)
    if update.callback_query is not None and update.callback_query.message is not None:
        return await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
    return None


async def admin_panel_show(update, context):
    if not await database.is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ شما دسترسی ادمین ندارید.")
        return
    perms = await h.admin_perms_for(update.effective_user.id)
    enabled = await database.get_setting("bot_enabled", "1") == "1"
    outage = await database.get_setting("national_outage_mode", "0") == "1"
    role = await database.get_admin_role(update.effective_user.id) or "full"
    status_text = "🟢 روشن" if enabled else "🔴 خاموش"
    outage_text = "🚨 فعال (حذف خودکار سرویس‌ها متوقف است)" if outage else "✅ غیرفعال"
    text = (
        "⚙️ پنل مدیریت\n━━━━━━━━━━━━━━━\n"
        f"سطح دسترسی: {role}\nوضعیت ربات: {status_text}\nحالت اضطراری نت ملی: {outage_text}\n\nاز دکمه‌های زیر استفاده کنید 👇"
    )
    await update.message.reply_text(text, reply_markup=h.admin_menu_keyboard(perms, enabled, outage))


async def admin_toggle_bot(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "bot_toggle"):
        return
    current = await database.get_setting("bot_enabled", "1")
    new_value = "0" if current == "1" else "1"
    await database.set_setting("bot_enabled", new_value)
    enabled = new_value == "1"
    outage = await database.get_setting("national_outage_mode", "0") == "1"
    await database.log_action("admin", update.effective_user.id, "تقییر وضعیت ربات به " + ("روشن" if enabled else "خاموش"))
    msg = "🟢 ربات روشن شد و برای همه فعال است." if enabled else "🔴 ربات خاموش شد. فقط ادمین‌ها دسترسی دارند."
    perms = await h.admin_perms_for(update.effective_user.id)
    await update.message.reply_text("✅ " + msg, reply_markup=h.admin_menu_keyboard(perms, enabled, outage))


async def admin_toggle_national_outage(update, context):
    """روشن/خاموش کردن حالت اضطراری نت ملی. وقتی فعال باشد، حذف خودکار سرویس‌های
    منقضی (که به‌صورت دوره‌ای هر ۳۰ دقیقه اجرا می‌شود) کاملاً متوقف می‌شود و اصلاً
    سراغ پنل مرزبان نمی‌رود؛ تا وقتی که نت ملی/دسترسی به پنل برگردد و ادمین دوباره
    این حالت را خاموش کند، هیچ سرویسی (نه تست، نه خریداری‌شده) به‌خاطر گذشت زمان یا
    اتمام حجم حذف نمی‌شود و ربات هم بابت قطع بودن پنل، خطای الکی به ادمین گزارش نمی‌کند."""
    if not await h.require_perm_msg(update, update.effective_user.id, "bot_toggle"):
        return
    current = await database.get_setting("national_outage_mode", "0")
    new_value = "0" if current == "1" else "1"
    await database.set_setting("national_outage_mode", new_value)
    outage = new_value == "1"
    await database.log_action(
        "admin", update.effective_user.id,
        "تغییر حالت اضطراری نت ملی به " + ("فعال (حذف خودکار سرویس‌ها متوقف شد)" if outage else "غیرفعال (حذف خودکار سرویس‌ها دوباره فعال شد)"),
    )
    msg = (
        "🚨 حالت اضطراری نت ملی فعال شد.\nاز الان تا وقتی دوباره این دکمه را بزنید، هیچ سرویسی (تست یا خریداری‌شده) به‌صورت خودکار حذف نمی‌شود و ربات سراغ پنل مرزبان برای بررسی انقضا نمی‌رود."
        if outage else
        "✅ حالت اضطراری نت ملی غیرفعال شد.\nحذف خودکار سرویس‌های منقضی دوباره طبق روال معمول انجام می‌شود."
    )
    perms = await h.admin_perms_for(update.effective_user.id)
    enabled = await database.get_setting("bot_enabled", "1") == "1"
    await update.message.reply_text(msg, reply_markup=h.admin_menu_keyboard(perms, enabled, outage))


# ---------------- تنظیمات عمومی (یک پیام = یک مقدار) ----------------

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
    await query.message.reply_text(prompt, reply_markup=h.flow_cancel_keyboard())
    return h.AWAITING_ADMIN_INPUT


async def admin_welcome_start(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "settings"):
        return ConversationHandler.END
    context.user_data["admin_action"] = "admin_set_welcome_text"
    await update.message.reply_text("✏️ <b>متن جدید خوش-امدگویی را وارد کنید:</b>", parse_mode="HTML", reply_markup=h.flow_cancel_keyboard())
    return h.AWAITING_ADMIN_INPUT


async def admin_welcome_photo_start(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "settings"):
        return ConversationHandler.END
    context.user_data["admin_action"] = "admin_set_welcome_photo"
    await update.message.reply_text("🖼️ عکس جدید را ارسال کنید، یا 0 را بفرستید تا عکس حذف شود.", reply_markup=h.flow_cancel_keyboard())
    return h.AWAITING_ADMIN_INPUT


async def admin_welcome_gif_start(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "settings"):
        return ConversationHandler.END
    context.user_data["admin_action"] = "admin_set_welcome_gif"
    await update.message.reply_text("🎞 گیف جدید را ارسال کنید، یا 0 را بفرستید تا گیف حذف شود.", reply_markup=h.flow_cancel_keyboard())
    return h.AWAITING_ADMIN_INPUT


async def admin_receive_photo_input(update, context):
    message = update.effective_message
    if message is None or not message.photo:
        return h.AWAITING_ADMIN_INPUT
    action = context.user_data.get("admin_action")
    if action == "admin_set_welcome_photo":
        file_id = message.photo[-1].file_id
        await database.set_setting("welcome_photo_id", file_id)
        await message.reply_text("✅ عکس خوش-امدگویی ثبت شد.")
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END
    await message.reply_text("لطفا متن وارد کنید، نه عکس.")
    return h.AWAITING_ADMIN_INPUT


async def admin_receive_animation_input(update, context):
    message = update.effective_message
    if message is None or not message.animation:
        return h.AWAITING_ADMIN_INPUT
    action = context.user_data.get("admin_action")
    if action == "admin_set_welcome_gif":
        file_id = message.animation.file_id
        await database.set_setting("welcome_gif_id", file_id)
        await message.reply_text("✅ گیف خوش-امدگویی ثبت شد.")
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END
    await message.reply_text("لطفا متن وارد کنید، نه گیف.")
    return h.AWAITING_ADMIN_INPUT


# ---------------- پلن‌ها ----------------

async def _packages_view():
    packages = await database.list_packages()
    categories = await database.list_package_categories()
    cat_names = {c["id"]: c["name"] for c in categories}
    keyboard = []
    for p in packages:
        icon = "🟢" if p["active"] else "🔴"
        cat_id = p.get("category_id")
        cat_label = cat_names.get(cat_id, "بدون دسته") if cat_id else "بدون دسته"
        keyboard.append([h.ibtn(
            f"{icon} {p['name']} — {p['gb']}گیگ/{p['days']}روز — {p['price']:,}ت — 🗂{cat_label}",
            callback_data=f"admin_pkg_edit:{p['id']}",
        )])
    keyboard.append([h.ibtn("➕ افزودن پلن جدید", callback_data="admin_pkg_add")])
    keyboard.append([h.ibtn("🗂 مدیریت دسته‌بندی‌ها", callback_data="admin_categories")])
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
    await h.safe_edit_or_send(query, text, reply_markup=keyboard)


async def admin_pkg_add_start(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "packages"):
        return ConversationHandler.END
    context.user_data["new_package"] = {}
    context.user_data["admin_action"] = "pkg_add_name"
    await query.answer()
    await query.message.reply_text("📦 نام پلن جدید را وارد کنید:", reply_markup=h.flow_cancel_keyboard())
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
    category = await database.get_package_category(package["category_id"]) if package.get("category_id") else None
    cat_text = category["name"] if category else "بدون دسته"
    text = (
        f"📦 {package['name']}\nحجم: {package['gb']} گیگ\nمدت: {package['days']} روز\n"
        f"قیمت: {package['price']:,} تومان\nوضعیت: {icon}\nدسته: 🗂 {cat_text}"
    )
    keyboard = InlineKeyboardMarkup([
        [h.ibtn("✏️ نام", callback_data=f"admin_pkg_field:{package_id}:name"),
         h.ibtn("✏️ حجم", callback_data=f"admin_pkg_field:{package_id}:gb")],
        [h.ibtn("✏️ مدت", callback_data=f"admin_pkg_field:{package_id}:days"),
         h.ibtn("✏️ قیمت", callback_data=f"admin_pkg_field:{package_id}:price")],
        [h.ibtn("🗂 تقییر دسته", callback_data=f"admin_pkg_setcat:{package_id}")],
        [h.ibtn("🔄 فعال/معلق", callback_data=f"admin_pkg_toggle:{package_id}")],
        [h.ibtn("🗑 حذف پلن", callback_data=f"admin_pkg_delete:{package_id}")],
        [h.ibtn("🔙 بازگشت", callback_data="admin_packages")],
    ])
    await h.safe_edit_or_send(query, text, reply_markup=keyboard)


async def admin_pkg_field_start(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "packages"):
        return ConversationHandler.END
    _, package_id, field = query.data.split(":")
    context.user_data["admin_action"] = f"pkg_edit_{field}:{package_id}"
    prompts = {"name": "نام جدید", "gb": "حجم جدید (گیگابایت)", "days": "مدت جدید (روز)", "price": "قیمت جدید (تومان)"}
    await query.answer()
    await query.message.reply_text(f"{prompts.get(field, field)} را وارد کنید:", reply_markup=h.flow_cancel_keyboard())
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
    await h.safe_edit_or_send(query, text, reply_markup=keyboard)


# ---------------- دسته‌بندی پلن‌ها ----------------

async def _categories_view():
    categories = await database.list_package_categories()
    keyboard = []
    for i, c in enumerate(categories):
        count = await database.count_packages_by_category(c["id"], active_only=False)
        keyboard.append([h.ibtn(f"🗂 {c['name']} ({count} پلن)", callback_data=f"admin_cat_rename_prompt:{c['id']}")])
        nav = []
        if i > 0:
            nav.append(h.ibtn("⬆️", callback_data=f"admin_cat_move:{c['id']}:up"))
        if i < len(categories) - 1:
            nav.append(h.ibtn("⬇️", callback_data=f"admin_cat_move:{c['id']}:down"))
        nav.append(h.ibtn("🗑 حذف", callback_data=f"admin_cat_delete:{c['id']}"))
        keyboard.append(nav)
    keyboard.append([h.ibtn("➕ افزودن دسته جدید", callback_data="admin_cat_add")])
    keyboard.append([h.ibtn("🔙 بازگشت", callback_data="admin_packages")])
    text = (
        "🗂 دسته‌بندی پلن‌ها\n━━━━━━━━━━━━━━━\n"
        "روی نام دسته بزنید تا نامش را ویرایش کنید. با ➕ دسته جدید (مثلاً هفتگی، ماهانه، فصلی) بسازید و از بخش ویرایش هر پلن، دسته‌اش را مشخص کنید."
    )
    if not categories:
        text += "\n\nهنوز دسته‌ای ثبت نشده."
    return text, InlineKeyboardMarkup(keyboard)


async def admin_categories_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "packages"):
        return
    await query.answer()
    text, keyboard = await _categories_view()
    await h.safe_edit_or_send(query, text, reply_markup=keyboard)


async def admin_cat_add_start(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "packages"):
        return ConversationHandler.END
    context.user_data["admin_action"] = "admin_cat_add_name"
    await query.answer()
    await query.message.reply_text("🗂 نام دسته جدید را وارد کنید (مثلاً هفتگی، ماهانه، فصلی):", reply_markup=h.flow_cancel_keyboard())
    return h.AWAITING_ADMIN_INPUT


async def admin_cat_rename_prompt(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "packages"):
        return ConversationHandler.END
    category_id = int(query.data.split(":")[1])
    category = await database.get_package_category(category_id)
    if not category:
        await query.answer("این دسته دیگر موجود نیست.", show_alert=True)
        return ConversationHandler.END
    context.user_data["admin_action"] = f"admin_cat_rename:{category_id}"
    await query.answer()
    await query.message.reply_text(f"✏️ نام جدید برای دسته «{category['name']}» را وارد کنید:", reply_markup=h.flow_cancel_keyboard())
    return h.AWAITING_ADMIN_INPUT


async def admin_cat_delete_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "packages"):
        return
    category_id = int(query.data.split(":")[1])
    await database.delete_package_category(category_id)
    await database.log_action("admin", query.from_user.id, f"حذف دسته پلن #{category_id}")
    await query.answer("دسته حذف شد. پلن‌های این دسته به «بدون دسته» منتقل شدند.")
    text, keyboard = await _categories_view()
    await h.safe_edit_or_send(query, text, reply_markup=keyboard)


async def admin_cat_move_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "packages"):
        return
    _, category_id, direction = query.data.split(":")
    await database.move_package_category(int(category_id), direction)
    await query.answer()
    text, keyboard = await _categories_view()
    await h.safe_edit_or_send(query, text, reply_markup=keyboard)


async def admin_pkg_setcat_show(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "packages"):
        return
    package_id = int(query.data.split(":")[1])
    package = await database.get_package(package_id)
    if not package:
        await query.answer("پلن پیدا نشد.", show_alert=True)
        return
    categories = await database.list_package_categories()
    await query.answer()
    keyboard = [[h.ibtn(f"🗂 {c['name']}", callback_data=f"admin_pkg_setcat_apply:{package_id}:{c['id']}")] for c in categories]
    keyboard.append([h.ibtn("🚫 بدون دسته", callback_data=f"admin_pkg_setcat_apply:{package_id}:none")])
    keyboard.append([h.ibtn("🔙 بازگشت", callback_data=f"admin_pkg_edit:{package_id}")])
    text = f"🗂 دسته‌ی پلن «{package['name']}» را انتخاب کنید:"
    if not categories:
        text += "\n\n(هنوز دسته‌ای ساخته نشده — از بخش «مدیریت دسته‌بندی‌ها» دسته بسازید.)"
    await h.safe_edit_or_send(query, text, reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_pkg_setcat_apply(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "packages"):
        return
    _, package_id, category_raw = query.data.split(":")
    category_id = None if category_raw == "none" else int(category_raw)
    await database.update_package_field(int(package_id), "category_id", category_id)
    await database.log_action("admin", query.from_user.id, f"تقییر دسته پلن #{package_id}")
    await query.answer("✅ دسته پلن به‌روز شد.")
    await admin_pkg_edit_menu(update, context)


# ---------------- مدیریت ادمین‌ها ----------------

async def admin_admins_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "admins"):
        return
    admins = await database.list_admins()
    lines = ["👮 ادمین‌ها\n━━━━━━━━━━━━━━━", f"👑 ادمین اصلی: {config.ADMIN_ID}"]
    keyboard = []
    for a in admins:
        lines.append(f"• {a['user_id']} — سطح: {a['role']}")
        keyboard.append([h.ibtn(f"🗑 حذف {a['user_id']}", callback_data=f"admin_admins_remove:{a['user_id']}")])
    keyboard.append([h.ibtn("➕ افزودن ادمین", callback_data="admin_admins_add")])
    await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_admins_add_start(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "admins"):
        return ConversationHandler.END
    context.user_data["admin_action"] = "add_admin_id"
    await query.answer()
    await query.message.reply_text("👮 ایدی عددی تلگرام ادمین جدید را وارد کنید:", reply_markup=h.flow_cancel_keyboard())
    return h.AWAITING_ADMIN_INPUT


async def admin_role_set_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "admins"):
        return
    _, target_id, role = query.data.split(":")
    await database.add_admin(int(target_id), query.from_user.id, role=role)
    await database.log_action("admin", query.from_user.id, f"افزودن ادمین {target_id} با سطح {role}")
    await query.answer("✅ انجام شد.")
    await h.safe_edit_or_send(query, f"✅ کاربر {target_id} با سطح دسترسی «{role}» به ادمین اضافه شد.")


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
        keyboard.append([h.ibtn(f"🗑 حذف {a['user_id']}", callback_data=f"admin_admins_remove:{a['user_id']}")])
    keyboard.append([h.ibtn("➕ افزودن ادمین", callback_data="admin_admins_add")])
    await h.safe_edit_or_send(query, "\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_cancel(update, context):
    for key in ("admin_action", "new_package", "new_discount", "new_server", "reply_ticket_id"):
        context.user_data.pop(key, None)
    await update.message.reply_text("✅ <b>عملیات لغو شد.</b>", parse_mode="HTML")
    return ConversationHandler.END


async def admin_cancel_callback(update, context):
    query = update.callback_query
    for key in ("admin_action", "new_package", "new_discount", "new_server", "reply_ticket_id"):
        context.user_data.pop(key, None)
    try:
        await query.answer("لغو شد")
    except Exception:
        pass
    try:
        await query.message.delete()
    except Exception:
        pass
    await h.send_main_menu(query.message.chat_id, context, query.from_user.id)
    return ConversationHandler.END


# ---------------- زیرمنوهای تنظیمات (کارت ، عضویت اجباری، تست رایگان، قوانین، و مدیریت) ----------------

async def admin_card_settings_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "settings"):
        return
    card_number = await database.get_setting("card_number", "-")
    card_holder = await database.get_setting("card_holder", "-")
    text = f"💳 شماره کارت: {card_number}\n👤 به نام: {card_holder}"
    keyboard = InlineKeyboardMarkup([
        [h.ibtn("✏️ ویرایش شماره کارت", callback_data="admin_set_card_number")],
        [h.ibtn("✏️ ویرایش نام صاحب کارت", callback_data="admin_set_card_holder")],
    ])
    await _reply(update, text, reply_markup=keyboard)


async def admin_forcejoin_settings_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "settings"):
        return
    enabled = await database.get_setting("force_join_enabled", "0") == "1"
    channels = await h.get_force_join_channels()
    status_icon = "🟢 فعال" if enabled else "🔴 معلق"
    channels_text = "\n".join(f"• {c}" for c in channels) if channels else "هیچ کانالی تنزیم نشده"
    text = f"🔒 عضویت اجباری\nوضعیت: {status_icon}\n\nکانال‌ها:\n{channels_text}"
    toggle_label = "🔴 نیرفعال کردن" if enabled else "🟢 فعال کردن"
    keyboard = [[h.ibtn(toggle_label, callback_data="admin_forcejoin_toggle")]]
    for idx, c in enumerate(channels):
        keyboard.append([h.ibtn(f"🗑 حذف {c}", callback_data=f"forcejoin_remove:{idx}")])
    keyboard.append([h.ibtn("➕ افزودن کانال جدید", callback_data="admin_forcejoin_add")])
    await _reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_forcejoin_add_start(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "settings"):
        return ConversationHandler.END
    context.user_data["admin_action"] = "admin_add_forcejoin_channel"
    await query.answer()
    await query.message.reply_text("📢 <b>ایدی/یوزرنیم کانال جدید را وارد کنید (مثلا @mychannel):</b>", parse_mode="HTML", reply_markup=h.flow_cancel_keyboard())
    return h.AWAITING_ADMIN_INPUT


async def forcejoin_remove_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "settings"):
        return
    idx = int(query.data.split(":")[1])
    channels = await h.get_force_join_channels()
    if 0 <= idx < len(channels):
        removed = channels.pop(idx)
        await h.set_force_join_channels(channels)
        await database.log_action("admin", query.from_user.id, f"حذف کانال جوین اجباری {removed}")
        await query.answer("حذف شد.")
    else:
        await query.answer("یافت نشد.", show_alert=True)
    await admin_forcejoin_settings_show(update, context)


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
    toggle_label = "🔴 نیرفعال کردن" if enabled else "🟢 فعال کردن"
    keyboard = InlineKeyboardMarkup([
        [h.ibtn(toggle_label, callback_data="admin_trial_toggle")],
        [h.ibtn("✏️ حجم", callback_data="admin_set_trial_gb"),
         h.ibtn("✏️ مدت", callback_data="admin_set_trial_days")],
    ])
    await _reply(update, text, reply_markup=keyboard)


async def admin_trial_toggle(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "settings"):
        return
    current = await database.get_setting("trial_enabled", "0")
    await database.set_setting("trial_enabled", "0" if current == "1" else "1")
    await database.log_action("admin", query.from_user.id, "توگل وضعیت تست رایگان")
    await query.answer("وضعیت عوض شد.")
    await query.message.reply_text("✅ وضعیت تست رایگان تعویض شد.")


async def admin_custombuilder_settings_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "packages"):
        return
    enabled = await database.get_setting("custom_builder_enabled", "0") == "1"
    min_gb = await database.get_setting("custom_builder_min_gb", "5")
    max_gb = await database.get_setting("custom_builder_max_gb", "200")
    price_gb = await database.get_setting("custom_builder_price_per_gb", "0")
    price_day = await database.get_setting("custom_builder_price_per_day", "0")
    charge_days = await database.get_setting("custom_builder_charge_days", "1") == "1"
    status_icon = "🟢 فعال" if enabled else "🔴 معلق"
    charge_icon = "🟢 فعال (هزینه روز هم گرفته می‌شود)" if charge_days else "🔴 فقط هزینه گیگ گرفته می‌شود"
    text = (
        "🚀 کانفیگ اختصاصی (ویژه VIP)\n"
        f"وضعیت: {status_icon}\n"
        f"بازهٔ حجم: {min_gb} تا {max_gb} گیگ\n"
        f"قیمت هر گیگ: {price_gb} تومان\n"
        f"قیمت هر روز: {price_day} تومان\n"
        f"دریافت هزینهٔ روز: {charge_icon}"
    )
    toggle_label = "🔴 قیرفعال کردن" if enabled else "🟢 فعال کردن"
    charge_toggle_label = "🔴 قیرفعال کردن هزینهٔ روز" if charge_days else "🟢 فعال کردن هزینهٔ روز"
    keyboard = InlineKeyboardMarkup([
        [h.ibtn(toggle_label, callback_data="admin_custombuilder_toggle")],
        [h.ibtn("✏️ حداقل گیگ", callback_data="admin_set_cb_min_gb"), h.ibtn("✏️ حداکثر گیگ", callback_data="admin_set_cb_max_gb")],
        [h.ibtn("✏️ قیمت هر گیگ", callback_data="admin_set_cb_price_gb"), h.ibtn("✏️ قیمت هر روز", callback_data="admin_set_cb_price_day")],
        [h.ibtn(charge_toggle_label, callback_data="admin_custombuilder_charge_toggle")],
    ])
    await _reply(update, text, reply_markup=keyboard)


async def admin_custombuilder_toggle(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "packages"):
        return
    current = await database.get_setting("custom_builder_enabled", "0")
    await database.set_setting("custom_builder_enabled", "0" if current == "1" else "1")
    await database.log_action("admin", query.from_user.id, "دوگل وضعیت کانفیگ اختصاصی VIP")
    await query.answer("وضعیت عوض شد.")
    await admin_custombuilder_settings_show(update, context)


async def admin_custombuilder_charge_toggle(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "packages"):
        return
    current = await database.get_setting("custom_builder_charge_days", "1")
    await database.set_setting("custom_builder_charge_days", "0" if current == "1" else "1")
    await database.log_action("admin", query.from_user.id, "دوگل دریافت هزینه روز کانفیگ اختصاصی")
    await query.answer("وضعیت عوض شد.")
    await admin_custombuilder_settings_show(update, context)


async def admin_rules_settings_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "settings"):
        return
    rules_text = await database.get_setting("rules_text", "-")
    contact_links = await database.get_setting("contact_links", "-")
    text = f"📜 قوانین:\n{rules_text}\n\n🔗 لینک‌های ارتباطی:\n{contact_links}"
    keyboard = InlineKeyboardMarkup([
        [h.ibtn("✏️ ویرایش قوانین", callback_data="admin_set_rules_text")],
        [h.ibtn("✏️ ویرایش لینک‌ها", callback_data="admin_set_contact_links")],
    ])
    await _reply(update, text, reply_markup=keyboard)


async def admin_support_settings_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "settings"):
        return
    support_text = await database.get_setting("support_text", "-")
    support_username = await database.get_setting("support_username", "-")
    text = f"🆘 متن پشتیبانی:\n{support_text}\n\n👤 ایدی پشتیبانی: {support_username}"
    keyboard = InlineKeyboardMarkup([
        [h.ibtn("✏️ ویرایش متن", callback_data="admin_set_support_text")],
        [h.ibtn("✏️ ویرایش ایدی", callback_data="admin_set_support_username")],
    ])
    await _reply(update, text, reply_markup=keyboard)


# ---------------- دسترساز ورودی متنی اصلی پنل مدیریت ----------------

_NUMERIC_INT_ACTIONS = {"admin_set_trial_days", "admin_set_cb_price_gb", "admin_set_cb_price_day"}
_NUMERIC_FLOAT_ACTIONS = {"admin_set_trial_gb", "admin_set_cb_min_gb", "admin_set_cb_max_gb"}


async def admin_receive_input(update, context):
    message = update.effective_message
    if message is None or not (message.text and message.text.strip()):
        return h.AWAITING_ADMIN_INPUT
    action = context.user_data.get("admin_action")
    if not action:
        return ConversationHandler.END
    if await h.redirect_if_menu_button(update, context):
        return ConversationHandler.END
    text = message.text.strip()

    if action in _NUMERIC_INT_ACTIONS:
        try:
            int(float(text))
        except ValueError:
            await message.reply_text("❌ لطفا فقط عدد صحیح وارد کنید (مثلا 3).")
            return h.AWAITING_ADMIN_INPUT
    if action in _NUMERIC_FLOAT_ACTIONS:
        try:
            float(text)
        except ValueError:
            await message.reply_text("❌ لطفا عدد وارد کنید (مثلا 1.5).")
            return h.AWAITING_ADMIN_INPUT

    # تنظیمات عمومی (یک مقدار متنی)
    if action in h.ASYNC_SETTING_ACTIONS:
        key, _ = h.ASYNC_SETTING_ACTIONS[action]
        await database.set_setting(key, text)
        await database.log_action("admin", update.effective_user.id, f"تنظیم {key} به-روز شد")
        await update.message.reply_text("✅ با موفقیت ذخیره شد.")
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

    if action == "admin_set_welcome_text":
        await database.set_setting("welcome_text", text)
        await update.message.reply_text("✅ متن خوش-امدگویی به-روز شد.")
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

    if action == "admin_set_welcome_photo":
        if text == "0":
            await database.set_setting("welcome_photo_id", "")
            await update.message.reply_text("✅ عکس خوش-امدگویی حذف شد.")
            context.user_data.pop("admin_action", None)
            return ConversationHandler.END
        await update.message.reply_text("لطفا یک عکس ارسال کنید یا 0 را بفرستید.")
        return h.AWAITING_ADMIN_INPUT

    if action == "admin_set_welcome_gif":
        if text == "0":
            await database.set_setting("welcome_gif_id", "")
            await update.message.reply_text("✅ گیف خوش-امدگویی حذف شد.")
            context.user_data.pop("admin_action", None)
            return ConversationHandler.END
        await update.message.reply_text("لطفا یک گیف ارسال کنید یا 0 را بفرستید.")
        return h.AWAITING_ADMIN_INPUT

    if action == "admin_add_forcejoin_channel":
        new_channel = text.strip()
        if not (new_channel.startswith("@") or new_channel.startswith("https://") or new_channel.startswith("-100")):
            await message.reply_text("لطفا یوزرنیم کانال (مثلا @mychannel)، لینک دعوت یا ایدی عددی (-100...) وارد کنید.")
            return h.AWAITING_ADMIN_INPUT
        channels = await h.get_force_join_channels()
        if new_channel in channels:
            await message.reply_text("این کانال قبلا اضافه شده است.")
            context.user_data.pop("admin_action", None)
            return ConversationHandler.END
        access_warning = ""
        if not new_channel.startswith("https://"):
            try:
                bot_member = await context.bot.get_chat_member(new_channel, context.bot.id)
                if bot_member.status not in ("administrator", "creator"):
                    access_warning = (
                        "\n\n⚠️ هشدار: ربات در این کانال ادمین نیست. برای اینکه عضویت کاربران به درستی بررسی شود، ربات را ادمین کانال کنید ورنه اینکه جوین اجباری کار نخواهد کرد!"
                    )
            except Exception as exc:
                await database.log_action("error", None, f"عدم دسترسی ربات به کانال {new_channel}: {exc}")
                access_warning = (
                    "\n\n⚠️ هشدار: ربات نتوانست به این کانال دسترسی پیدا کند (احتمالا ربات عضو کانال نیست یا ایدی اشتباه است). جوین اجباری برای این کانال کار نخواهد کرد تا رفع مشکل."
                )
        channels.append(new_channel)
        await h.set_force_join_channels(channels)
        await database.log_action("admin", update.effective_user.id, f"افزودن کانال جوین اجباری {new_channel}")
        await message.reply_text(f"✅ کانال {new_channel} اضافه شد.{access_warning}")
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

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
        new_id = await database.add_package(new_package.get("name", "پلن"), new_package.get("gb", 0), new_package.get("days", 0), price)
        await database.log_action("admin", update.effective_user.id, f"افزودن پلن {new_package.get('name')}")
        context.user_data.pop("new_package", None)
        context.user_data.pop("admin_action", None)
        categories = await database.list_package_categories()
        if categories:
            keyboard = [[h.ibtn(f"🗂 {c['name']}", callback_data=f"admin_pkg_setcat_apply:{new_id}:{c['id']}")] for c in categories]
            keyboard.append([h.ibtn("🚫 بدون دسته", callback_data=f"admin_pkg_setcat_apply:{new_id}:none")])
            await update.message.reply_text(
                "✅ پلن جدید اضافه شد.\n🗂 دسته‌ی این پلن را انتخاب کنید:",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            await update.message.reply_text("✅ پلن جدید اضافه شد.")
        return ConversationHandler.END

    # ---- دسته‌بندی پلن‌ها ----
    if action == "admin_cat_add_name":
        await database.add_package_category(text)
        await database.log_action("admin", update.effective_user.id, f"افزودن دسته پلن {text}")
        await update.message.reply_text(f"✅ دسته «{text}» اضافه شد.")
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END
    if action.startswith("admin_cat_rename:"):
        category_id = int(action.split(":")[1])
        await database.rename_package_category(category_id, text)
        await database.log_action("admin", update.effective_user.id, f"ویرایش نام دسته #{category_id}")
        await update.message.reply_text(f"✅ نام دسته به «{text}» تغییر یافت.")
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
            await update.message.reply_text("لطفا فقط عدد (ایدی عددی) وارد کنید.")
            return h.AWAITING_ADMIN_INPUT
        keyboard = InlineKeyboardMarkup([
            [h.ibtn("👑 دسترسی کامل", callback_data=f"admin_role_set:{new_admin_id}:full")],
            [h.ibtn("💰 فقط فروش", callback_data=f"admin_role_set:{new_admin_id}:sales")],
            [h.ibtn("🎧 فقط پشتیبانی", callback_data=f"admin_role_set:{new_admin_id}:support")],
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
        await update.message.reply_text("ادرس سرور را وارد کنید (مثلا https://1.2.3.4):")
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
        keyboard = [[h.ibtn(
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
        sent, html_downgraded = await _broadcast_send(context, user_ids, text)
        await database.log_action("admin", update.effective_user.id, f"پیام همگانی به {sent} کاربر" + (" (HTML نامعتبر بود، به صورت متن خام ارسال شد)" if html_downgraded else ""))
        msg = f"✅ پیامم به {sent} کاربر ارسال شد."
        if html_downgraded:
            msg += "\n⚠️ توجه: قالب‌بندی HTML متن شما معتبر نبود، برای همین پیام برای همه‌ی کاربران به صورت متن ساده (بدون بولد/ایتالیک) ارسال شد."
        await update.message.reply_text(msg)
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

    if action.startswith("broadcast_package:"):
        package_id = int(action.split(":")[1])
        user_ids = await database.list_user_ids_for_package(package_id)
        sent, html_downgraded = await _broadcast_send(context, user_ids, text)
        await database.log_action("admin", update.effective_user.id, f"پیام به کاربران پلن #{package_id} ({sent} نفر)" + (" (HTML نامعتبر بود)" if html_downgraded else ""))
        msg = f"✅ پیام به {sent} کاربر این پلن ارسال شد."
        if html_downgraded:
            msg += "\n⚠️ توجه: قالب‌بندی HTML متن شما معتبر نبود، برای همین پیام برای همه‌ی کاربران به صورت متن ساده ارسال شد."
        await update.message.reply_text(msg)
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

    # ---- مدیریت کیف پول کاربران ----
    if action == "admin_wallet_search":
        context.user_data.pop("admin_action", None)
        target_user = None
        if text.lstrip("-").isdigit():
            target_user = await database.get_user(int(text))
        if not target_user:
            found = await database.search_users(text.lstrip("@"))
            target_user = found[0] if found else None
        if not target_user:
            await update.message.reply_text("❌ کاربری یافت نشد.")
            return ConversationHandler.END
        card_text, card_kb = await admin_money._wallet_user_card(target_user)
        await update.message.reply_text(card_text, parse_mode="HTML", reply_markup=card_kb)
        return ConversationHandler.END

    if action == "admin_wallet_credit_amount":
        target_id = context.user_data.get("wallet_target_user")
        try:
            amount = int(text.strip().replace(",", ""))
            if amount <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ لطفا فقط یک عدد مثبت بزرگتر از صفر وارد کنید.")
            return h.AWAITING_ADMIN_INPUT
        new_balance = await database.adjust_wallet_balance(target_id, amount)
        await database.record_wallet_transaction(target_id, amount, "admin_credit", note=f"توسط ادمین {update.effective_user.id}")
        await database.log_action("admin", update.effective_user.id, f"افزایش کیف پول {target_id} +{amount}")
        await update.message.reply_text(f"✅ {amount:,} تومان به کیف پول افزوده شد. موجودی جدید: {new_balance:,} تومان")
        try:
            await context.bot.send_message(chat_id=target_id, text=f"🎉 مبلغ {amount:,} تومان به کیف پول شما افزوده شد توسط مدیریت.\n💰 موجودی جدید: {new_balance:,} تومان")
        except Exception:
            pass
        context.user_data.pop("admin_action", None)
        context.user_data.pop("wallet_target_user", None)
        return ConversationHandler.END

    if action == "admin_wallet_debit_amount":
        target_id = context.user_data.get("wallet_target_user")
        try:
            amount = int(text.strip().replace(",", ""))
            if amount <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ لطفا فقط یک عدد مثبت بزرگتر از صفر وارد کنید.")
            return h.AWAITING_ADMIN_INPUT
        spent = await database.try_spend_wallet_balance(target_id, amount)
        if not spent:
            current_balance = await database.get_wallet_balance(target_id)
            await update.message.reply_text(f"❌ موجودی فعلی کاربر ({current_balance:,} تومان) کمتر از مقدار وارد شده است.")
            return ConversationHandler.END
        new_balance = await database.get_wallet_balance(target_id)
        await database.record_wallet_transaction(target_id, -amount, "admin_debit", note=f"توسط ادمین {update.effective_user.id}")
        await database.log_action("admin", update.effective_user.id, f"کاهش کیف پول {target_id} -{amount}")
        await update.message.reply_text(f"✅ {amount:,} تومان از کیف پول کاسته شد. موجودی جدید: {new_balance:,} تومان")
        try:
            await context.bot.send_message(chat_id=target_id, text=f"⚠️ مبلغ {amount:,} تومان از کیف پول شما کاسته شد توسط مدیریت.\n💰 موجودی جدید: {new_balance:,} تومان")
        except Exception:
            pass
        context.user_data.pop("admin_action", None)
        context.user_data.pop("wallet_target_user", None)
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
    await message.reply_text("❓ متوجه نشدم. لطفا دوباره وارد کنید یا انصراف بدهید.", reply_markup=h.flow_cancel_keyboard())
    return ConversationHandler.END
