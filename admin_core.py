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


async def _broadcast_send_photo(context, user_ids, file_id, caption):
    """ارسال همگانی یک عکس (با کپشن اختیاری) با همان محافظت در برابر FloodWait که در _broadcast_send است."""
    sent = 0
    use_html = True
    html_downgraded = False
    for index, uid in enumerate(user_ids):
        for attempt in range(3):
            try:
                await context.bot.send_photo(
                    chat_id=uid,
                    photo=file_id,
                    caption=(caption or None),
                    parse_mode=("HTML" if (use_html and caption) else None),
                )
                sent += 1
                break
            except BadRequest as e:
                if use_html and caption and "parse entities" in str(e).lower():
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


async def _broadcast_forward(context, user_ids, from_chat_id, message_id):
    """فوروارد عین یک پیام (مثلا پستی از کانال خود ادمین) به همه کاربران، با همان محافظت در برابر FloodWait."""
    sent = 0
    for index, uid in enumerate(user_ids):
        for attempt in range(3):
            try:
                await context.bot.forward_message(chat_id=uid, from_chat_id=from_chat_id, message_id=message_id)
                sent += 1
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
    return sent


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



async def admin_pasarguard_tools_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "servers"):
        return
    keyboard = InlineKeyboardMarkup([
        [h.ibtn("🧪 تست اتصال و دسترسی نمایندگی", callback_data="pg_diag")],
        [h.ibtn("🗂 نمایش گروه‌های قابل اعمال", callback_data="pg_groups")],
        [h.ibtn("⚙️ انتخاب گروه‌های سرویس", callback_data="pg_groups_select")],
        [h.ibtn("🎁 هدیه روز/گیگ به همه", callback_data="bulk_bonus_start", style="success")],
    ])
    await _reply(update, "🛡 ابزارهای پاسارگارد\n━━━━━━━━━━━━━━━\nاین بخش فقط عملیات خواندنی انجام می‌دهد و به دیتای کاربران دست نمی‌زند.", reply_markup=keyboard)


async def admin_pasarguard_diag_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "servers"):
        return
    try:
        await query.answer("در حال تست...")
    except Exception:
        pass
    try:
        report = await asyncio.wait_for(marzban_api.operator_diagnostics(), timeout=15)
        def line(name, item):
            if item is None:
                return f"• {name}: بررسی نشد"
            if isinstance(item, dict):
                st = item.get('status')
                extra = f" | تعداد/کلید: {item.get('count')}" if item.get('count') is not None else ""
                if isinstance(st, int) and st < 400:
                    return f"✅ {name}: OK ({st}){extra}"
                return f"⚠️ {name}: {st} — {item.get('error','دسترسی ندارد/نامشخص')}"
            return f"• {name}: {item}"
        text = "🧪 نتیجه تست پاسارگارد\n━━━━━━━━━━━━━━━\n"
        text += "✅ لاگین با یوزرنیم/پسورد انجام شد.\n" if report.get("login") else "❌ لاگین ناموفق بود.\n"
        text += "\n".join([
            line("groups/simple مخصوص نمایندگی", report.get("groups_simple")),
            line("groups کامل مالک", report.get("groups_full")),
            line("user_templates/simple", report.get("templates_simple")),
            line("system stats", report.get("system_stats")),
        ])
        text += "\n\nℹ️ اگر موردهای کامل 403 بدهند طبیعی است؛ اکانت شما نمایندگی/Operator است. ربات برای ساخت سرویس از groups/simple استفاده می‌کند."
        await query.message.reply_text(text)
    except Exception as e:
        await query.message.reply_text(f"❌ خطا در تست پاسارگارد: {e}")


async def admin_pasarguard_groups_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "servers"):
        return
    try:
        await query.answer("در حال دریافت گروه‌ها...")
    except Exception:
        pass
    try:
        groups = await asyncio.wait_for(marzban_api.get_groups(force_refresh=True), timeout=15)
        if not groups:
            await query.message.reply_text("⚠️ هیچ گروهی از پاسارگارد دریافت نشد؛ تا وقتی گروه نداشته باشید ساخت سرویس ممکن نیست.")
            return
        lines = ["🗂 گروه‌هایی که ربات هنگام خرید/تست روی سرویس اعمال می‌کند:", "━━━━━━━━━━━━━━━"]
        for g in groups:
            lines.append(f"• {g.get('name','-')} | ID: {g.get('id','-')}")
        lines.append("\n✅ طبق درخواست شما، ربات همیشه همه این گروه‌های قابل‌دسترسی را روی سرویس‌های خرید و تست اعمال می‌کند.")
        await query.message.reply_text("\n".join(lines))
    except Exception as e:
        await query.message.reply_text(f"❌ خطا در دریافت گروه‌ها: {e}")


async def admin_pg_groups_select_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "servers"):
        return
    await query.answer()
    try:
        groups = await asyncio.wait_for(marzban_api.get_groups(force_refresh=True), timeout=15)
    except Exception as e:
        await query.message.reply_text(f"❌ خطا در دریافت گروه‌ها از پنل: {e}")
        return
    current_raw = await database.get_setting("pasarguard_selected_group_ids", "all")
    current = "all" if current_raw == "all" else set(__import__('json').loads(current_raw))
    lines = ["🗂 انتخاب گروه‌های سرویس‌های جدید", "━━━━━━━━━━━━━━━", "پیش‌فرض: همه گروه‌های قابل‌دسترسی اعمال می‌شوند. اگر چند گروه خاص انتخاب کنی، خرید و تست فقط روی همان‌ها ساخته می‌شود."]
    keyboard = [[h.ibtn("✅ اعمال همه گروه‌ها", callback_data="pg_groups_set:all", style="success")]]
    for g in groups:
        gid = int(g.get('id'))
        mark = "✅" if current == "all" or gid in current else "⬜️"
        keyboard.append([h.ibtn(f"{mark} {g.get('name','-')} | {gid}", callback_data=f"pg_group_toggle:{gid}")])
    await query.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_pg_group_toggle_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "servers"):
        return
    gid = int(query.data.split(":",1)[1])
    raw = await database.get_setting("pasarguard_selected_group_ids", "all")
    if raw == "all":
        groups = await marzban_api.get_groups(force_refresh=True)
        selected = {int(g['id']) for g in groups}
    else:
        selected = set(__import__('json').loads(raw))
    selected.remove(gid) if gid in selected else selected.add(gid)
    await database.set_setting("pasarguard_selected_group_ids", __import__('json').dumps(sorted(selected)))
    await query.answer("✅ ذخیره شد")
    await admin_pg_groups_select_callback(update, context)

async def admin_pg_groups_set_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "servers"):
        return
    await database.set_setting("pasarguard_selected_group_ids", "all")
    await query.answer("✅ همه گروه‌ها فعال شد", show_alert=True)

    # دریافت گروه‌های فعال از پنل
    try:
        groups = await asyncio.wait_for(marzban_api.get_groups(force_refresh=True), timeout=15)
    except Exception as e:
        await query.message.reply_text(f"❌ خطا در دریافت گروه‌ها از پنل: {e}")
        return

    all_group_ids = [g["id"] for g in groups]
    if not all_group_ids:
        await query.message.reply_text("⚠️ هیچ گروهی در پنل یافت نشد.")
        return

    # اعمال روی همه کاربران فعال دیتابیس
    orders = await database.list_active_bot_orders()
    progress_msg = await query.message.reply_text(
        f"⏳ در حال اعمال {len(all_group_ids)} گروه روی {len(orders)} سرویس فعال...",
        parse_mode="HTML"
    )

    ok, fail = 0, 0
    import time as _time
    t_start = _time.time()

    for order in orders:
        username = order.get("marzban_username")
        if not username:
            continue
        try:
            await marzban_api.set_user_groups(username, all_group_ids)
            ok += 1
        except Exception:
            fail += 1

    elapsed = round(_time.time() - t_start, 1)
    group_names = ", ".join(g.get("name", str(g["id"])) for g in groups)
    result = (
        f"✅ <b>اعمال گروه‌ها روی همه سرویس‌ها انجام شد</b>\n\n"
        f"🗂 گروه‌های فعال‌شده: <code>{group_names}</code>\n"
        f"📊 نتیجه:\n"
        f"• موفق: <b>{ok}</b> سرویس\n"
        f"• ناموفق: <b>{fail}</b> سرویس\n"
        f"⏱ زمان انجام: {elapsed} ثانیه"
    )
    try:
        await progress_msg.edit_text(result, parse_mode="HTML")
    except Exception:
        await query.message.reply_text(result, parse_mode="HTML")

async def admin_bulk_bonus_message_start(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "orders"):
        return ConversationHandler.END
    context.user_data["admin_action"] = "bulk_bonus_days"
    msg = "🎁 <b>هدیه گروهی به خریداران فعال</b>" + chr(10) + chr(10) + "اول تعداد روز اضافه برای همه سرویس‌های خریداری‌شده فعال را وارد کنید. اگر روز نمی‌خواهید، 0 بفرستید:"
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=h.flow_cancel_keyboard())
    return h.AWAITING_ADMIN_INPUT


async def admin_bulk_bonus_start(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "orders"):
        return ConversationHandler.END
    context.user_data["admin_action"] = "bulk_bonus_days"
    await query.answer()
    await query.message.reply_text("🎁 <b>هدیه گروهی به خریداران فعال</b>\n\nاول تعداد روز اضافه برای همه سرویس‌های خریداری‌شده فعال را وارد کنید. اگر روز نمی‌خواهید، 0 بفرستید:", parse_mode="HTML", reply_markup=h.flow_cancel_keyboard())
    return h.AWAITING_ADMIN_INPUT

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
    if action == "broadcast_all" or (action and action.startswith("broadcast_package:")):
        file_id = message.photo[-1].file_id
        caption = (message.caption or "").strip()
        if action == "broadcast_all":
            user_ids = await database.list_all_user_ids()
            log_label = "پیام همگانی (عکس)"
        else:
            package_id = int(action.split(":")[1])
            user_ids = await database.list_user_ids_for_package(package_id)
            log_label = f"پیام به کاربران پلن #{package_id} (عکس)"
        sent, html_downgraded = await _broadcast_send_photo(context, user_ids, file_id, caption)
        await database.log_action("admin", update.effective_user.id, f"{log_label} به {sent} کاربر" + (" (HTML نامعتبر بود)" if html_downgraded else ""))
        msg = f"✅ عکس به {sent} کاربر ارسال شد."
        if html_downgraded:
            msg += "\n⚠️ توجه: قالب‌بندی HTML کپشن شما معتبر نبود، برای همین کپشن به صورت متن ساده ارسال شد."
        await message.reply_text(msg)
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


async def admin_receive_forward_input(update, context):
    """پیام فوروارد‌شده (مثلا از کانال خود ادمین) را در حالت پیام همگانی می‌گیرد و عین آن را برای همه کاربران فوروارد می‌کند.
    اگر اکشن جاری یک پیام همگانی نباشد، پیام فوروارد‌شده را به هندلرهای قبلی (متن/عکس/گیف) واگذار می‌کند تا رفتار قبلی تغییر نکند."""
    message = update.effective_message
    action = context.user_data.get("admin_action")
    is_broadcast_action = action == "broadcast_all" or (action and action.startswith("broadcast_package:"))
    if message is None or not is_broadcast_action:
        if message is not None and message.photo:
            return await admin_receive_photo_input(update, context)
        if message is not None and message.animation:
            return await admin_receive_animation_input(update, context)
        return await admin_receive_input(update, context)

    # توجه: از نسخه ۲۱ کتابخانه python-telegram-bot، فیلدهای قدیمی forward_from_chat و
    # forward_from_message_id به‌طور کامل حذف شده‌اند و فقط forward_origin موجود است. برای
    # فوروارد به کاربران، به‌جای تلاش برای استخراج چت/پیام مبدأ اصلی (که همیشه در دسترس ربات
    # نیست، مثلا اگر ربات عضو آن کانال نباشد)، همان پیامی که همین الان ادمین در چت خصوصی با
    # ربات فوروارد کرده است را دوباره فوروارد می‌کنیم؛ تلگرام در این حالت برچسب «فوروارد شده از»
    # را از منبع اصلی (کانال) حفظ می‌کند، نه از ادمین.
    if message.forward_origin is None:
        await message.reply_text("❌ این پیام قابل فوروارد نیست. لطفا پیامی را مستقیم از یک کانال/گروه فوروارد کنید یا متن/عکس را مستقیم بفرستید.")
        return h.AWAITING_ADMIN_INPUT
    from_chat_id = message.chat_id
    message_id = message.message_id
    if action == "broadcast_all":
        user_ids = await database.list_all_user_ids()
        log_label = "پیام همگانی (فوروارد)"
    else:
        package_id = int(action.split(":")[1])
        user_ids = await database.list_user_ids_for_package(package_id)
        log_label = f"پیام به کاربران پلن #{package_id} (فوروارد)"
    sent = await _broadcast_forward(context, user_ids, from_chat_id, message_id)
    await database.log_action("admin", update.effective_user.id, f"{log_label} به {sent} کاربر")
    await message.reply_text(f"✅ پیام فوروارد‌شده به {sent} کاربر ارسال شد.")
    context.user_data.pop("admin_action", None)
    return ConversationHandler.END


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
    keyboard.append([h.ibtn("🎨 قالب نمایش پلن‌ها", callback_data="admin_pkg_template_menu")])
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


# ---------------- قالب نمایش پلن‌ها ----------------

_SAMPLE_PACKAGE_FOR_PREVIEW = {"name": "نمونه", "gb": 150, "days": 60, "price": 444000}


async def _pkg_template_menu_view():
    """فهرست دسته‌های پلن (مثلا هفتگی/ماهانه) برای انتخاب قالب نمایش جداگانه هر دسته.
    هر دسته قالب مستقل خودش را دارد؛ تغییر قالب یک دسته روی بقیه دسته‌ها تاثیری ندارد."""
    categories = await database.list_package_categories()
    lines = [
        "🎨 <b>قالب نمایش پلن‌ها</b>",
        "━━━━━━━━━━━",
        "<b>هر دسته (مثلا هفتگی، ماهانه و ...) قالب نمایش مستقل خودش را دارد.</b>",
        "یکی از دسته‌های زیر را برای تنظیم قالب آن انتخاب کنید:",
    ]
    keyboard = []
    for c in categories:
        keyboard.append([h.ibtn(f"🗂 {c['name']}", callback_data=f"admin_pkg_template_cat:{c['id']}")])
    uncategorized_count = await database.count_packages_by_category(None, active_only=False)
    if uncategorized_count > 0 or not categories:
        keyboard.append([h.ibtn("📦 سایر پلن‌ها (بدون دسته)", callback_data="admin_pkg_template_cat:none")])
    keyboard.append([h.ibtn("🔙 بازگشت به پلن‌ها", callback_data="admin_packages")])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


async def _pkg_template_cat_view(category_id):
    """فهرست قالب‌های آماده مخصوص یک دسته خاص + گزینه قالب دلخواه، با علامت روی قالب فعلیِ همان دسته."""
    cat_token = "none" if category_id is None else str(category_id)
    if category_id is None:
        cat_name = "سایر پلن‌ها (بدون دسته)"
    else:
        cat = await database.get_package_category(category_id)
        cat_name = cat["name"] if cat else "دسته"
    current = await h.get_package_label_template(category_id)
    lines = [
        f"🎨 <b>قالب نمایش پلن‌های «{cat_name}»</b>",
        "━━━━━━━━━━━",
        "<b>پیش‌نمایش با یک پلن نمونه (150 گیگ/2 ماهه/444,000 تومان):</b>",
        "",
    ]
    keyboard = []
    for idx, tpl in enumerate(h.PACKAGE_LABEL_TEMPLATES):
        preview = h.render_package_label(tpl, _SAMPLE_PACKAGE_FOR_PREVIEW)
        mark = " ✅" if tpl == current else ""
        lines.append(f"{idx + 1}. {preview}{mark}")
        keyboard.append([h.ibtn(f"انتخاب قالب {idx + 1}", callback_data=f"admin_pkg_template_set:{cat_token}:{idx}")])
    keyboard.append([h.ibtn("✏️ قالب دلخواه همین دسته", callback_data=f"admin_pkg_template_custom:{cat_token}")])
    if current != h.DEFAULT_PACKAGE_LABEL_TEMPLATE:
        keyboard.append([h.ibtn("♻️ بازگشت به قالب پیش‌فرض (اولیه)", callback_data=f"admin_pkg_template_reset:{cat_token}")])
    keyboard.append([h.ibtn("🔙 بازگشت به دسته‌ها", callback_data="admin_pkg_template_menu")])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


async def admin_pkg_template_menu_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "packages"):
        return
    text, keyboard = await _pkg_template_menu_view()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def admin_pkg_template_menu_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "packages"):
        return
    await query.answer()
    text, keyboard = await _pkg_template_menu_view()
    await h.safe_edit_or_send(query, text, reply_markup=keyboard)


async def admin_pkg_template_cat_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "packages"):
        return
    await query.answer()
    raw_cat = query.data.split(":", 1)[1]
    category_id = None if raw_cat == "none" else int(raw_cat)
    text, keyboard = await _pkg_template_cat_view(category_id)
    await h.safe_edit_or_send(query, text, reply_markup=keyboard)


async def admin_pkg_template_set_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "packages"):
        return
    # admin_pkg_template_set:<cat>:<idx>  (<cat> عدد شناسه‌ی دسته یا "none")
    parts = query.data.split(":")
    raw_cat, raw_idx = parts[1], parts[2]
    idx = int(raw_idx)
    if idx < 0 or idx >= len(h.PACKAGE_LABEL_TEMPLATES):
        await query.answer("نامعتبر", show_alert=True)
        return
    category_id = None if raw_cat == "none" else int(raw_cat)
    template = h.PACKAGE_LABEL_TEMPLATES[idx]
    await database.set_setting(h.pkg_template_setting_key(category_id), template)
    await database.log_action("admin", query.from_user.id, f"انتخاب قالب نمایش پلن دسته {raw_cat} -> #{idx + 1}")
    await query.answer("✅ ذخیره شد.")
    text, keyboard = await _pkg_template_cat_view(category_id)
    await h.safe_edit_or_send(query, text, reply_markup=keyboard)


async def admin_pkg_template_reset_callback(update, context):
    """قالب یک دسته را دقیقا به همان قالب پیش‌فرض/اولیهٔ ربات برمی‌گرداند (بدون حذف قالب‌های بقیه دسته‌ها)."""
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "packages"):
        return
    raw_cat = query.data.split(":", 1)[1]
    category_id = None if raw_cat == "none" else int(raw_cat)
    await database.set_setting(h.pkg_template_setting_key(category_id), h.DEFAULT_PACKAGE_LABEL_TEMPLATE)
    await database.log_action("admin", query.from_user.id, f"قالب نمایش پلن دسته {raw_cat} به پیش‌فرض بازگشت")
    await query.answer("✅ به قالب پیش‌فرض برگشت.")
    text, keyboard = await _pkg_template_cat_view(category_id)
    await h.safe_edit_or_send(query, text, reply_markup=keyboard)


async def admin_pkg_template_custom_start(update, context):
    """شروع دریافت قالب دلخواه مخصوص یک دسته‌ی خاص (نه سراسری برای همه دسته‌ها)."""
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "packages"):
        return ConversationHandler.END
    raw_cat = query.data.split(":", 1)[1]
    context.user_data["admin_action"] = f"admin_set_package_template:{raw_cat}"
    await query.answer()
    await query.message.reply_text(
        "✏️ <b>قالب دلخواه نمایش پلن‌های این دسته را ارسال کنید.</b>\n"
        "متغیرهای قابل استفاده: {gb} — {duration} — {price} — {name} — {days}\n"
        "مثال:\n🚀 {gb} گیگ | {duration} | {price} تومان",
        parse_mode="HTML",
        reply_markup=h.flow_cancel_keyboard(),
    )
    return h.AWAITING_ADMIN_INPUT


# ---------------- متن بالای لیست پلن‌ها ----------------

async def _buy_prompt_menu_view():
    current = await database.get_setting("buy_prompt_text", h.DEFAULT_BUY_PROMPT_TEXT)
    lines = [
        "💬 <b>متن بالای لیست پلن‌ها</b>",
        "━━━━━━━━━━━",
        f"<b>متن فعلی:</b> {current}",
        "",
        "<b>یکی از متن‌های جذاب زیر را انتخاب کنید:</b>",
        "",
    ]
    keyboard = []
    for idx, preset in enumerate(h.BUY_PROMPT_TEXT_PRESETS):
        mark = " ✅" if preset == current else ""
        lines.append(f"{idx + 1}. {preset}{mark}")
        keyboard.append([h.ibtn(f"انتخاب متن {idx + 1}", callback_data=f"admin_buy_prompt_set:{idx}")])
    keyboard.append([h.ibtn("✏️ متن دلخواه", callback_data="admin_set_buy_prompt_text")])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


async def admin_buy_prompt_menu_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "settings"):
        return
    text, keyboard = await _buy_prompt_menu_view()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def admin_buy_prompt_set_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "settings"):
        return
    idx = int(query.data.split(":")[1])
    if idx < 0 or idx >= len(h.BUY_PROMPT_TEXT_PRESETS):
        await query.answer("نامعتبر", show_alert=True)
        return
    await database.set_setting("buy_prompt_text", h.BUY_PROMPT_TEXT_PRESETS[idx])
    await database.log_action("admin", query.from_user.id, f"انتخاب متن پیشنهادی انتخاب پلن #{idx + 1}")
    await query.answer("✅ ذخیره شد.")
    text, keyboard = await _buy_prompt_menu_view()
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
    lines = [
        "👮 مدیریت ادمین‌ها\n━━━━━━━━━━━━━━━",
        f"👑 ادمین اصلی: {config.ADMIN_ID}",
        "\nسطح‌ها:",
        "• full: دسترسی کامل",
        "• sales: فروش/مالی/سفارش‌ها",
        "• support: پشتیبانی/کاربران",
        "",
        "ادمین‌های فرعی:",
    ]
    keyboard = []
    if not admins:
        lines.append("هنوز ادمین فرعی ثبت نشده.")
    for a in admins:
        role = a.get('role') or 'full'
        lines.append(f"• {a['user_id']} — سطح: {role} — افزودن: {a.get('added_at','-')}")
        keyboard.append([h.ibtn(f"🗑 حذف {a['user_id']}", callback_data=f"admin_admins_remove:{a['user_id']}", style="danger")])
    keyboard.append([h.ibtn("➕ افزودن ادمین", callback_data="admin_admins_add", style="success")])
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
        lines.append(f"👤 {a['user_id']} — سطح: {a['role']}")
        keyboard.append([h.ibtn(f"🗑 حذف {a['user_id']}", callback_data=f"admin_admins_remove:{a['user_id']}")])
    keyboard.append([h.ibtn("➕ افزودن ادمین", callback_data="admin_admins_add")])
    await h.safe_edit_or_send(query, "\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_cancel(update, context):
    for key in ("admin_action", "new_package", "new_discount", "new_server", "reply_ticket_id",
                 "bulk_bonus_days", "wallet_target_user", "guide_btn_num"):
        context.user_data.pop(key, None)
    await update.message.reply_text("✅ <b>عملیات لغو شد.</b>", parse_mode="HTML")
    return ConversationHandler.END


async def admin_cancel_callback(update, context):
    query = update.callback_query
    for key in ("admin_action", "new_package", "new_discount", "new_server", "reply_ticket_id",
                 "bulk_bonus_days", "wallet_target_user", "guide_btn_num"):
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



async def admin_trial_unit_toggle(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "settings"):
        return
    current = await database.get_setting("trial_unit", "days")
    new_value = "hours" if current == "days" else "days"
    await database.set_setting("trial_unit", new_value)
    await database.log_action("admin", query.from_user.id, "تغییر واحد تست رایگان به " + ("ساعتی" if new_value == "hours" else "روزانه"))
    await query.answer("✅ تغییر کرد")
    await admin_trial_settings_show(update, context)


async def admin_trial_reset_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "settings"):
        return
    count = await database.reset_trial_usage(query.from_user.id)
    await query.answer("✅ تست‌ها ریست شد", show_alert=True)
    await query.message.reply_text(f"✅ ریست تست انجام شد. از الان همه کاربرانی که قبلاً تست گرفته بودند، یک‌بار دیگر مجاز به دریافت تست هستند.\n👥 تعداد کاربران دارای سابقه تست: {count}")


async def admin_payment_mode_toggle(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "settings"):
        return
    current = await database.get_setting("payment_amount_mode", "fixed")
    new_value = "randomized" if current == "fixed" else "fixed"
    await database.set_setting("payment_amount_mode", new_value)
    await database.log_action("admin", query.from_user.id, "تغییر حالت مبلغ پرداخت به " + new_value)
    await query.answer("✅ حالت پرداخت تغییر کرد")
    await admin_card_settings_show(update, context)


async def admin_post_approval_guide_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "settings"):
        return
    enabled = await database.get_setting("post_approval_guide_enabled", "0") == "1"
    url = await database.get_setting("post_approval_guide_url", "")
    text_value = await database.get_setting("post_approval_guide_text", "-")
    text = (
        "📚 آموزش بعد از تایید رسید\n━━━━━━━━━━━━━━━\n"
        f"وضعیت: {'🟢 روشن' if enabled else '🔴 خاموش'}\n"
        f"نوع دکمه: {'باز کردن لینک' if url else 'نمایش متن داخل ربات'}\n"
        f"لینک فعلی: {url or '-'}\n\n"
        f"متن فعلی:\n{text_value}"
    )
    keyboard = InlineKeyboardMarkup([
        [h.ibtn("🔄 روشن/خاموش", callback_data="admin_guide_toggle")],
        [h.ibtn("✏️ ویرایش متن آموزش", callback_data="admin_set_guide_text")],
        [h.ibtn("🔗 تنظیم لینک دکمه", callback_data="admin_set_guide_url")],
    ])
    await _reply(update, text, reply_markup=keyboard)


async def admin_guide_toggle(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "settings"):
        return
    current = await database.get_setting("post_approval_guide_enabled", "0")
    await database.set_setting("post_approval_guide_enabled", "0" if current == "1" else "1")
    await database.log_action("admin", query.from_user.id, "تغییر وضعیت آموزش بعد از تایید رسید")
    await query.answer("✅ انجام شد")
    await query.message.reply_text("✅ وضعیت دکمه آموزش تغییر کرد. از این به بعد در پیام سرویس و بخش سرویس‌های من طبق این تنظیم نمایش داده می‌شود.")

# ---------------- زیرمنوهای تنظیمات (کارت ، عضویت اجباری، تست رایگان، قوانین، و مدیریت) ----------------

async def admin_card_settings_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "settings"):
        return
    card_number = await database.get_setting("card_number", "-")
    card_holder = await database.get_setting("card_holder", "-")
    mode = await database.get_setting("payment_amount_mode", "fixed")
    mn = await database.get_setting("payment_random_min", "100")
    mx = await database.get_setting("payment_random_max", "1500")
    text = (
        "💳 تنظیمات کارت و مبلغ پرداخت\n━━━━━━━━━━━━━━━\n"
        f"شماره کارت: {card_number}\n"
        f"به نام: {card_holder}\n\n"
        f"حالت مبلغ: {'🎲 مبلغ اختصاصی/رندوم' if mode == 'randomized' else '✅ مبلغ ثابت معمولی'}\n"
        f"بازه مبلغ رندوم: {mn} تا {mx} تومان اضافه روی قیمت پلن"
    )
    keyboard = InlineKeyboardMarkup([
        [h.ibtn("✏️ ویرایش شماره کارت", callback_data="admin_set_card_number")],
        [h.ibtn("✏️ ویرایش نام صاحب کارت", callback_data="admin_set_card_holder")],
        [h.ibtn("🔄 سوییچ مبلغ ثابت/رندوم", callback_data="admin_payment_mode_toggle")],
        [h.ibtn("✏️ حداقل رندوم", callback_data="admin_set_payment_random_min"), h.ibtn("✏️ حداکثر رندوم", callback_data="admin_set_payment_random_max")],
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
    amount = await database.get_setting("trial_days", "1")
    unit = await database.get_setting("trial_unit", "days")
    status_icon = "🟢 فعال" if enabled else "🔴 معلق"
    unit_label = "ساعت" if unit == "hours" else "روز"
    text = (
        "🎁 تنظیمات تست رایگان\n━━━━━━━━━━━━━━━\n"
        f"وضعیت: {status_icon}\n"
        f"حجم تست: {gb} گیگ\n"
        f"مدت تست: {amount} {unit_label}\n"
        f"حالت زمان: {'⏱ ساعتی' if unit == 'hours' else '📅 روزانه'}\n\n"
        "♻️ با دکمه ریست تست، همه کاربرانی که قبلاً تست گرفته‌اند دوباره فقط یک‌بار مجاز می‌شوند."
    )
    toggle_label = "🔴 غیرفعال کردن تست" if enabled else "🟢 فعال کردن تست"
    keyboard = InlineKeyboardMarkup([
        [h.ibtn(toggle_label, callback_data="admin_trial_toggle")],
        [h.ibtn("✏️ حجم تست", callback_data="admin_set_trial_gb"), h.ibtn("✏️ مدت تست", callback_data="admin_set_trial_days")],
        [h.ibtn("🔄 سوییچ ساعت/روز", callback_data="admin_trial_unit_toggle")],
        [h.ibtn("♻️ ریست تست", callback_data="admin_trial_reset", style="danger")],
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

    # قالب دلخواه نمایش پلن مخصوص یک دسته (نیاز به اعتبارسنجی خاص)؛ اکشن به صورت
    # "admin_set_package_template:<cat>" ذخیره شده و <cat> شناسه‌ی دسته یا "none" است.
    if action and action.startswith("admin_set_package_template:"):
        raw_cat = action.split(":", 1)[1]
        category_id = None if raw_cat == "none" else int(raw_cat)
        preview = h.validate_package_label_template(text)
        if preview is None:
            await update.message.reply_text(
                "❌ قالب نامعتبر است. فقط از {gb}، {duration}، {price}، {name}، {days} و متن/ایموجی ساده استفاده کنید و دوباره ارسال کنید.",
                reply_markup=h.flow_cancel_keyboard(),
            )
            return h.AWAITING_ADMIN_INPUT
        await database.set_setting(h.pkg_template_setting_key(category_id), text)
        await database.log_action("admin", update.effective_user.id, f"قالب دلخواه نمایش پلن دسته {raw_cat} ثبت شد")
        await update.message.reply_text(f"✅ قالب این دسته ذخیره شد.\n\nپیش‌نمایش:\n{preview}")
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

    if action == "admin_set_guide_url" and text == "0":
        await database.set_setting("post_approval_guide_url", "")
        await update.message.reply_text("✅ لینک حذف شد؛ دکمه آموزش متن داخل ربات را نمایش می‌دهد.")
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

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
            order = await database.get_order_by_marzban_username(username) if hasattr(database, "get_order_by_marzban_username") else None
            if order:
                try:
                    await context.bot.send_message(chat_id=order["user_id"], text=f"🎁 خبر خوب! مدیریت برای سرویس شما «{username}» مقدار {add_gb:g} گیگابایت حجم اضافه کرد.\nاز همراهی شما ممنونیم 💜")
                except Exception:
                    pass
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
            order = await database.get_order_by_marzban_username(username) if hasattr(database, "get_order_by_marzban_username") else None
            if order:
                try:
                    await context.bot.send_message(chat_id=order["user_id"], text=f"🎉 سرویس شما «{username}» از طرف مدیریت {add_days} روز تمدید شد.\nبا خیال راحت ادامه بدهید 🚀")
                except Exception:
                    pass
        except Exception as error:
            await update.message.reply_text(f"❌ خطا: {error}")
        context.user_data.pop("admin_action", None)
        return ConversationHandler.END

    # ---- کانال اعتماد ----
    if action == "trust_set_channel":
        await database.set_setting("trust_channel_id", text.strip())
        context.user_data.pop("admin_action", None)
        await message.reply_text(f"✅ کانال اعتماد روی <code>{text.strip()}</code> تنظیم شد.", parse_mode="HTML")
        return ConversationHandler.END

    if action == "trust_buy_btn_label":
        await database.set_setting("trust_channel_buy_btn_label", text.strip())
        context.user_data["admin_action"] = "trust_buy_btn_url"
        await message.reply_text("🔗 حالا لینک دکمه خرید را وارد کنید:", reply_markup=h.flow_cancel_keyboard())
        return h.AWAITING_ADMIN_INPUT

    if action == "trust_buy_btn_url":
        await database.set_setting("trust_channel_buy_btn_url", text.strip())
        context.user_data.pop("admin_action", None)
        await message.reply_text("✅ دکمه خرید ذخیره شد.")
        return ConversationHandler.END

    # ---- راهنمای اتصال ویرایش متن/دکمه ----
    if action == "guide_edit_text":
        await database.set_setting("connection_guide_text", text)
        context.user_data.pop("admin_action", None)
        await message.reply_text("✅ متن راهنمای اتصال ذخیره شد.")
        return ConversationHandler.END

    if action in ("guide_edit_btn1_label", "guide_edit_btn2_label", "guide_edit_btn3_label"):
        btn_num = context.user_data.get("guide_btn_num", 1)
        if text.strip() == "-":
            await database.set_setting(f"connection_guide_btn{btn_num}_label", "")
            await database.set_setting(f"connection_guide_btn{btn_num}_url", "")
            context.user_data.pop("admin_action", None)
            context.user_data.pop("guide_btn_num", None)
            await message.reply_text(f"✅ دکمه {btn_num} حذف شد.")
            return ConversationHandler.END
        await database.set_setting(f"connection_guide_btn{btn_num}_label", text)
        context.user_data["admin_action"] = f"guide_edit_btn{btn_num}_url"
        await message.reply_text(f"🔗 حالا لینک دکمه {btn_num} را وارد کنید:", reply_markup=h.flow_cancel_keyboard())
        return h.AWAITING_ADMIN_INPUT

    if action in ("guide_edit_btn1_url", "guide_edit_btn2_url", "guide_edit_btn3_url"):
        btn_num = context.user_data.get("guide_btn_num", 1)
        await database.set_setting(f"connection_guide_btn{btn_num}_url", text)
        context.user_data.pop("admin_action", None)
        context.user_data.pop("guide_btn_num", None)
        await message.reply_text(f"✅ دکمه {btn_num} ذخیره شد.")
        return ConversationHandler.END

    # ---- هدیه همگانی روز/گیگ ----
    if action == "bulk_bonus_days":
        try:
            bonus_days = int(text)
            if bonus_days < 0:
                raise ValueError
        except ValueError:
            await message.reply_text("❌ لطفا فقط عدد صحیح (صفر یا بزرگتر) وارد کنید.", reply_markup=h.flow_cancel_keyboard())
            return h.AWAITING_ADMIN_INPUT
        context.user_data["bulk_bonus_days"] = bonus_days
        context.user_data["admin_action"] = "bulk_bonus_gb"
        await message.reply_text("💾 حالا مقدار گیگابایت هدیه را وارد کنید. اگر گیگ نمی‌خواهید، 0 بفرستید:", reply_markup=h.flow_cancel_keyboard())
        return h.AWAITING_ADMIN_INPUT

    if action == "bulk_bonus_gb":
        try:
            bonus_gb = float(text)
            if bonus_gb < 0:
                raise ValueError
        except ValueError:
            await message.reply_text("❌ لطفا فقط عدد (صفر یا بزرگتر) وارد کنید.", reply_markup=h.flow_cancel_keyboard())
            return h.AWAITING_ADMIN_INPUT
        bonus_days = context.user_data.get("bulk_bonus_days", 0)
        context.user_data.pop("admin_action", None)
        context.user_data.pop("bulk_bonus_days", None)
        if bonus_days == 0 and bonus_gb == 0:
            await message.reply_text("⚠️ هر دو مقدار صفر است. عملیات لغو شد.")
            return ConversationHandler.END
        orders = await database.list_active_purchase_orders()
        if not orders:
            await message.reply_text("⚠️ هیچ سرویس فعال خریداری‌شده‌ای یافت نشد.")
            return ConversationHandler.END
        ok_count = 0
        fail_count = 0
        notified = 0
        for order in orders:
            username = order.get("marzban_username")
            if not username:
                continue
            try:
                if bonus_days > 0:
                    await marzban_api.extend_user_expire(username, bonus_days)
                if bonus_gb > 0:
                    await marzban_api.increase_user_data(username, bonus_gb)
                ok_count += 1
                parts = []
                if bonus_days > 0:
                    parts.append(f"{bonus_days} روز")
                if bonus_gb > 0:
                    parts.append(f"{bonus_gb:g} گیگابایت")
                gift_text = " و ".join(parts)
                try:
                    await context.bot.send_message(
                        chat_id=order["user_id"],
                        text=f"🎁 هدیه از مدیریت!\n\nبه سرویس شما «{username}» {gift_text} اضافه شد.\nاز همراهی شما ممنونیم 💜"
                    )
                    notified += 1
                except Exception:
                    pass
            except Exception:
                fail_count += 1
        summary_parts = []
        if bonus_days > 0:
            summary_parts.append(f"{bonus_days} روز")
        if bonus_gb > 0:
            summary_parts.append(f"{bonus_gb:g} گیگ")
        gift_summary = " + ".join(summary_parts)
        await database.log_action("admin", update.effective_user.id, f"هدیه همگانی {gift_summary} به {ok_count} سرویس")
        result_msg = f"✅ هدیه همگانی ({gift_summary}) اعمال شد.\n\n📊 نتیجه:\n• موفق: {ok_count} سرویس\n• ناموفق: {fail_count} سرویس\n• اطلاع‌رسانی به کاربر: {notified} نفر"
        await message.reply_text(result_msg)
        return ConversationHandler.END

    context.user_data.pop("admin_action", None)
    await message.reply_text("❓ متوجه نشدم. لطفا دوباره وارد کنید یا انصراف بدهید.", reply_markup=h.flow_cancel_keyboard())
    return ConversationHandler.END


# ─────────────────────────────────────────────
# بخش راهنمای اتصال کاربران
# ─────────────────────────────────────────────

async def admin_connection_guide_settings_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "settings"):
        return
    enabled = await database.get_setting("connection_guide_enabled", "0") == "1"
    title = await database.get_setting("connection_guide_title", "📲 آموزش اتصال")
    text = await database.get_setting("connection_guide_text", "تنظیم نشده")
    btn1 = await database.get_setting("connection_guide_btn1_label", "")
    url1 = await database.get_setting("connection_guide_btn1_url", "")
    btn2 = await database.get_setting("connection_guide_btn2_label", "")
    url2 = await database.get_setting("connection_guide_btn2_url", "")
    btn3 = await database.get_setting("connection_guide_btn3_label", "")
    url3 = await database.get_setting("connection_guide_btn3_url", "")
    status = "✅ فعال" if enabled else "❌ غیرفعال"
    preview = text[:120] + "..." if len(text) > 120 else text
    info = (
        f"📲 <b>راهنمای کاربران (آموزش اتصال)</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"وضعیت: <b>{status}</b>\n"
        f"عنوان: {title}\n"
        f"متن (پیش‌نمایش): {preview}\n"
        f"دکمه ۱: {btn1 or '—'} | {url1 or '—'}\n"
        f"دکمه ۲: {btn2 or '—'} | {url2 or '—'}\n"
        f"دکمه ۳: {btn3 or '—'} | {url3 or '—'}\n"
    )
    keyboard = InlineKeyboardMarkup([
        [h.ibtn("✅ فعال‌سازی" if not enabled else "❌ غیرفعال‌سازی",
                callback_data="guide_toggle", style="success" if not enabled else "danger")],
        [h.ibtn("✏️ ویرایش متن راهنما", callback_data="guide_edit_text")],
        [h.ibtn("🔘 دکمه ۱", callback_data="guide_edit_btn1"),
         h.ibtn("🔘 دکمه ۲", callback_data="guide_edit_btn2"),
         h.ibtn("🔘 دکمه ۳", callback_data="guide_edit_btn3")],
        [h.ibtn("👁️ پیش‌نمایش", callback_data="guide_preview")],
    ])
    await update.message.reply_text(info, parse_mode="HTML", reply_markup=keyboard)


async def admin_connection_guide_toggle(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "settings"):
        return
    current = await database.get_setting("connection_guide_enabled", "0")
    new_val = "0" if current == "1" else "1"
    await database.set_setting("connection_guide_enabled", new_val)
    label = "فعال" if new_val == "1" else "غیرفعال"
    await query.answer(f"راهنمای اتصال {label} شد", show_alert=True)
    try:
        await query.message.delete()
    except Exception:
        pass


async def admin_connection_guide_edit_text_start(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "settings"):
        return ConversationHandler.END
    context.user_data["admin_action"] = "guide_edit_text"
    await query.answer()
    await query.message.reply_text(
        "✏️ متن راهنمای اتصال را وارد کنید (HTML پشتیبانی می‌شود):\n"
        "مثال: 📲 <b>آموزش اتصال</b>\n\nبرنامه X-UI را نصب کنید...",
        reply_markup=h.flow_cancel_keyboard()
    )
    return h.AWAITING_ADMIN_INPUT


async def admin_connection_guide_edit_btn_start(update, context, btn_num):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "settings"):
        return ConversationHandler.END
    context.user_data["admin_action"] = f"guide_edit_btn{btn_num}_label"
    context.user_data["guide_btn_num"] = btn_num
    await query.answer()
    await query.message.reply_text(
        f"🔘 عنوان دکمه {btn_num} را وارد کنید (یا «-» برای حذف):",
        reply_markup=h.flow_cancel_keyboard()
    )
    return h.AWAITING_ADMIN_INPUT


async def admin_connection_guide_edit_btn1_start(update, context):
    return await admin_connection_guide_edit_btn_start(update, context, 1)

async def admin_connection_guide_edit_btn2_start(update, context):
    return await admin_connection_guide_edit_btn_start(update, context, 2)

async def admin_connection_guide_edit_btn3_start(update, context):
    return await admin_connection_guide_edit_btn_start(update, context, 3)


async def admin_connection_guide_preview(update, context):
    query = update.callback_query
    await query.answer()
    text = await database.get_setting("connection_guide_text", "📲 راهنما تنظیم نشده")
    btn1 = await database.get_setting("connection_guide_btn1_label", "")
    url1 = await database.get_setting("connection_guide_btn1_url", "")
    btn2 = await database.get_setting("connection_guide_btn2_label", "")
    url2 = await database.get_setting("connection_guide_btn2_url", "")
    btn3 = await database.get_setting("connection_guide_btn3_label", "")
    url3 = await database.get_setting("connection_guide_btn3_url", "")
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    rows = []
    for label, url in [(btn1, url1), (btn2, url2), (btn3, url3)]:
        if label and url:
            rows.append([InlineKeyboardButton(label, url=url)])
    km = InlineKeyboardMarkup(rows) if rows else None
    await query.message.reply_text("👁️ پیش‌نمایش راهنما:\n" + text, parse_mode="HTML", reply_markup=km)


# ─────────────────────────────────────────────
# بخش قالب‌های سرویس کاربران (20 قالب)
# ─────────────────────────────────────────────

SERVICE_TEMPLATES = [
  {
    "name": "🌟 کلاسیک (پیش‌فرض)",
    "template": "{status_icon} <b>{status_label}</b>\n━━━━━━━━━━━━━━━\n👤 <b>نام سرویس:</b> <code>{username}</code>\n📦 <b>محصول:</b> {product_name}\n{price_line}🆔 <b>شناسهٔ سفارش:</b> #{order_id}\n━━━━━━━━━━━━━━━\n📊 <b>وضعیت مصرف ترافیک:</b>\n{usage_bar}\n📥 مصرف‌شده: <b>{used_str}</b>  •  📦 کل: <b>{limit_str}</b>\n🛟 باقی‌مانده: <b>{remaining_str}</b>{usage_note}\n━━━━━━━━━━━━━━━\n{time_section}📅 <b>تاریخ اتمام:</b> {expire_str}{countdown}{expiry_note}\n━━━━━━━━━━━━━━━\n💡 <b>نکتهٔ امنیتی:</b> اگر حس می‌کنی شخص دیگه‌ای هم از سرویست استفاده می‌کنه، با دکمه‌ی «⚙️ تعویض لینک» دسترسی همه رو قطع کن و یک لینک تازه بگیر.\n\n🙏 از خرید شما ممنونیم، امیدواریم از سرعت و کیفیت سرویس لذت ببرید."
  },
  {
    "name": "🔥 مدرن و مینیمال",
    "template": "╔══ {status_icon} {status_label} ══╗\n\n🆔 سرویس: <code>{username}</code>\n📦 پلن: {product_name}\n{price_line}\n📊 ترافیک:\n{usage_bar}\n‣ مصرف: {used_str} از {limit_str}\n‣ باقی: <b>{remaining_str}</b>{usage_note}\n\n⏱ زمان:\n{time_section}‣ انقضا: {expire_str}{countdown}{expiry_note}\n\n╚══════════════╝\n🔐 تعویض لینک = قطع دسترسی دیگران"
  },
  {
    "name": "💎 لوکس",
    "template": "✦ ✦ ✦ گزارش سرویس شما ✦ ✦ ✦\n\n{status_icon} وضعیت: <b>{status_label}</b>\n━━━━━━━━━━━━━━━\n🎯 نام: <code>{username}</code>\n💎 پلن: {product_name}\n{price_line}🔢 سفارش: #{order_id}\n━━━━━━━━━━━━━━━\n📡 <b>مصرف اینترنت</b>\n{usage_bar}\n📤 {used_str}  ←  📥 {limit_str}\n💾 باقیمانده: <b>{remaining_str}</b>{usage_note}\n━━━━━━━━━━━━━━━\n🕐 <b>زمان</b>\n{time_section}📆 {expire_str}{countdown}{expiry_note}\n━━━━━━━━━━━━━━━\n✨ ممنون از اعتمادت 💜"
  },
  {
    "name": "⚡ سرعت محور",
    "template": "⚡ <b>وضعیت لحظه‌ای سرویس</b>\n\n{status_icon} {status_label} | <code>{username}</code>\n\n📊 {usage_bar} {used_str}/{limit_str}\n💾 باقی: <b>{remaining_str}</b>{usage_note}\n📅 {expire_str}{countdown}{expiry_note}\n{time_section}\n#{order_id} | {product_name}"
  },
  {
    "name": "🌙 تاریک و حرفه‌ای",
    "template": "▓▓ ACCOUNT DASHBOARD ▓▓\n\n▸ ID: <code>{username}</code>\n▸ Plan: {product_name}\n▸ Status: {status_icon} {status_label}\n{price_line}\n▓ TRAFFIC ▓\n{usage_bar}\nUsed: {used_str} | Total: {limit_str}\nLeft: <b>{remaining_str}</b>{usage_note}\n\n▓ TIME ▓\n{time_section}Exp: {expire_str}{countdown}{expiry_note}\n\n▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓\nOrder #{order_id}"
  },
  {
    "name": "🎮 گیمری",
    "template": "🎮 ━━ PLAYER STATS ━━ 🎮\n\n👾 Player: <code>{username}</code>\n🏅 Class: {product_name}\n⚡ Status: {status_icon} {status_label}\n\n🔋 DATA POWER\n{usage_bar}\n[ {used_str} used / {remaining_str} left / {limit_str} total ]{usage_note}\n\n⏰ TIME LEFT\n{time_section}💀 Expires: {expire_str}{countdown}{expiry_note}\n\n🎖️ Order #{order_id} | {price_line}\n━━━━━━━━━━━━━━━━"
  },
  {
    "name": "🌈 رنگارنگ",
    "template": "🟢🔵🟣 سرویس شما 🟣🔵🟢\n\n{status_icon} <b>{status_label}</b>\n━━━━━━━━━━━━━\n🔷 نام: <code>{username}</code>\n🔶 پلن: {product_name}\n🔷 سفارش: #{order_id}\n{price_line}\n🟩 مصرف: {used_str}\n🟥 باقی: <b>{remaining_str}</b> از {limit_str}{usage_note}\n{usage_bar}\n\n🕒 انقضا: {expire_str}{countdown}\n{time_section}{expiry_note}\n💜 خوش‌اومدی!"
  },
  {
    "name": "📱 تلگرافی",
    "template": "<b>📱 اطلاعات سرویس</b>\n\n<code>━━━━━━━━━━━━━━━━</code>\n<b>👤 یوزرنیم:</b> <code>{username}</code>\n<b>📦 پلن:</b> {product_name}\n<b>⚡ وضعیت:</b> {status_icon} {status_label}\n{price_line}<code>━━━━━━━━━━━━━━━━</code>\n<b>📊 ترافیک باقی‌مانده:</b>\n<code>{usage_bar}</code>\n<b>{remaining_str}</b> از {limit_str} ({used_str} مصرف){usage_note}\n<code>━━━━━━━━━━━━━━━━</code>\n{time_section}<b>📅 انقضا:</b> {expire_str}{countdown}{expiry_note}\n<code>━━━━━━━━━━━━━━━━</code>\n<i>💌 سفارش #{order_id}</i>"
  },
  {
    "name": "🏖️ کژوال",
    "template": "سلام! اینجا اطلاعات سرویسته 😊\n\n{status_icon} الان <b>{status_label}</b>\nاسم سرویست: <code>{username}</code>\nپلنت: {product_name}\n{price_line}\n📊 از ترافیکت:\n{usage_bar}\n{used_str} رفته، {remaining_str} مونده از {limit_str}{usage_note}\n{time_section}\n⏳ تا {expire_str} وقت داری{countdown}{expiry_note}\n\nسفارش #{order_id} - ممنون که هستی! 💜"
  },
  {
    "name": "🏢 سازمانی",
    "template": "┌─────────────────────┐\n│    گزارش سرویس VPN   │\n└─────────────────────┘\nشناسه: <code>{username}</code>\nوضعیت: {status_icon} {status_label}\nپلن: {product_name}\nشماره سفارش: #{order_id}\n{price_line}\n├─────────────────────┤\n│        ترافیک        │\n├─────────────────────┤\n{usage_bar}\nمصرفی: {used_str} | کل: {limit_str}\nموجود: <b>{remaining_str}</b>{usage_note}\n├─────────────────────┤\n│         زمان         │\n├─────────────────────┤\n{time_section}پایان: {expire_str}{countdown}{expiry_note}\n└─────────────────────┘"
  },
  {
    "name": "🌊 موجی",
    "template": "〰️〰️〰️〰️〰️〰️〰️\n\n{status_icon} <b>{status_label}</b>\n\n〰️ <code>{username}</code> 〰️\nپلن: {product_name}\n{price_line}\n〰️〰️〰️〰️〰️〰️〰️\n📶 <b>اینترنت:</b>\n{usage_bar}\n{remaining_str} مانده از {limit_str}{usage_note}\n\n📅 <b>انقضا:</b> {expire_str}{countdown}{expiry_note}\n{time_section}\n〰️〰️〰️〰️〰️〰️〰️\n🌊 سفارش #{order_id}"
  },
  {
    "name": "🎯 دقیق",
    "template": "🎯 <b>گزارش دقیق سرویس</b>\n━━━━━━━━━━━━━━━\n🔹 یوزرنیم: <code>{username}</code>\n🔹 پلن: {product_name}\n🔹 وضعیت: {status_icon} {status_label}\n🔹 سفارش: #{order_id}\n{price_line}━━━━━━━━━━━━━━━\n📊 <b>ترافیک مصرفی</b>\n{usage_bar}\n🔸 مصرف‌شده: {used_str}\n🔸 باقیمانده: <b>{remaining_str}</b>\n🔸 کل: {limit_str}{usage_note}\n━━━━━━━━━━━━━━━\n⏳ <b>زمان‌بندی</b>\n{time_section}🔸 تاریخ پایان: {expire_str}{countdown}{expiry_note}\n━━━━━━━━━━━━━━━"
  },
  {
    "name": "🚀 فضایی",
    "template": "🚀 ══ SPACE VPN ══ 🛸\n\n🛰️ Terminal: <code>{username}</code>\n🌌 Mission: {product_name}\n⚡ System: {status_icon} {status_label}\n{price_line}\n🔋 FUEL GAUGE (Traffic)\n{usage_bar}\nConsumed: {used_str} | Reserve: <b>{remaining_str}</b> | Capacity: {limit_str}{usage_note}\n\n⏱️ MISSION CLOCK\n{time_section}T-Minus: {expire_str}{countdown}{expiry_note}\n\n🛸 Mission #{order_id} | Godspeed! 🚀"
  },
  {
    "name": "🌺 ایرانی",
    "template": "🌺 سرویس شما 🌺\n\n{status_icon} <b>{status_label}</b>\n━━━━━━━━━━━━━━━\n📌 نام: <code>{username}</code>\n📌 نوع سرویس: {product_name}\n📌 کد سفارش: #{order_id}\n{price_line}━━━━━━━━━━━━━━━\n🌐 <b>مصرف اینترنت</b>\n{usage_bar}\n✅ مصرف شده: {used_str}\n✅ باقی‌مانده: <b>{remaining_str}</b>\n✅ کل حجم: {limit_str}{usage_note}\n━━━━━━━━━━━━━━━\n📅 <b>تاریخ پایان سرویس</b>\n{expire_str}{countdown}{expiry_note}\n{time_section}\n🌺 از انتخاب شما سپاسگزاریم 🌺"
  },
  {
    "name": "🔮 رمزی",
    "template": "🔮 ═══ اطلاعات مخفی ═══ 🔮\n\n🗝️ کلید: <code>{username}</code>\n📜 دسترسی: {product_name}\n🔮 وضعیت: {status_icon} {status_label}\n{price_line}\n⚗️ ═══ ظرفیت ═══ ⚗️\n{usage_bar}\n🧪 مصرف: {used_str} | 💊 باقی: <b>{remaining_str}</b> | 🏺 کل: {limit_str}{usage_note}\n\n⏳ ═══ زمان ═══ ⏳\n{time_section}🕰️ {expire_str}{countdown}{expiry_note}\n\n🔮 طلسم #{order_id} ═══ 🔮"
  },
  {
    "name": "🎸 موزیکال",
    "template": "🎵 ♪ ♫ VPN DJ ♫ ♪ 🎵\n\n🎸 Track: <code>{username}</code>\n🎼 Album: {product_name}\n🎚️ Status: {status_icon} {status_label}\n{price_line}\n🎛️ ━━━ EQUALIZER ━━━ 🎛️\n{usage_bar}\n🎵 Played: {used_str} | 🎶 Left: <b>{remaining_str}</b> | 🎙️ Total: {limit_str}{usage_note}\n\n⏱️ Time on Stage:\n{time_section}🎤 Ends: {expire_str}{countdown}{expiry_note}\n\n🎵 Ticket #{order_id} | Rock on! 🤘"
  },
  {
    "name": "🏔️ طبیعت‌گرا",
    "template": "🌲 🏔️ سرویس طبیعی 🏔️ 🌲\n\n🌿 نام: <code>{username}</code>\n🍃 پلن: {product_name}\n☀️ وضعیت: {status_icon} {status_label}\n{price_line}\n🌊 ~~~~ آب مصرفی ~~~~ 🌊\n{usage_bar}\n💧 مصرف: {used_str} | 🌊 ذخیره: <b>{remaining_str}</b> | ⛲ کل: {limit_str}{usage_note}\n\n🌅 ~~~~ فصل ~~~~ 🌅\n{time_section}🍂 پایان: {expire_str}{countdown}{expiry_note}\n\n🌲 سفارش #{order_id} | طبیعت پاک 🌿"
  },
  {
    "name": "🤖 رباتیک",
    "template": "🤖 ROBOT REPORT v2.0 🤖\n\n[USER]: <code>{username}</code>\n[PLAN]: {product_name}\n[STATUS]: {status_icon} {status_label}\n{price_line}[ORDER]: #{order_id}\n\n[BANDWIDTH REPORT]\n{usage_bar}\n[USED]: {used_str} / [TOTAL]: {limit_str}\n[AVAILABLE]: <b>{remaining_str}</b>{usage_note}\n\n[TIME REPORT]\n{time_section}[EXPIRY]: {expire_str}{countdown}{expiry_note}\n\n>>> REPORT COMPLETE <<<"
  },
  {
    "name": "🎪 فستیوال",
    "template": "🎪🎡🎢 فستیوال سرویس 🎢🎡🎪\n\n🎠 سرویس: <code>{username}</code>\n🎭 نوع: {product_name}\n🎊 وضعیت: {status_icon} {status_label}\n{price_line}\n🎆 ════ جشن ترافیک ════ 🎆\n{usage_bar}\n🎈 مصرف: {used_str} | 🎉 باقی: <b>{remaining_str}</b> | 🎁 کل: {limit_str}{usage_note}\n\n🎑 ════ تقویم ════ 🎑\n{time_section}🎋 پایان: {expire_str}{countdown}{expiry_note}\n\n🎪 بلیت #{order_id} | خوش بگذره! 🎊"
  },
  {
    "name": "🛡️ امنیتی",
    "template": "🛡️ ═══ SECURITY PANEL ═══ 🛡️\n\n🔒 ID: <code>{username}</code>\n🔑 Level: {product_name}\n🟢 Status: {status_icon} {status_label}\n{price_line}\n🛡️ ═══ BANDWIDTH ═══ 🛡️\n{usage_bar}\n▶ Used: {used_str} | ▶ Free: <b>{remaining_str}</b> | ▶ Max: {limit_str}{usage_note}\n\n🕐 ═══ VALIDITY ═══ 🕐\n{time_section}⚠️ Expiry: {expire_str}{countdown}{expiry_note}\n\n🔐 Ticket #{order_id} | Stay Safe! 🛡️"
  }
]


async def admin_service_template_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "settings"):
        return
    current_idx = int(await database.get_setting("service_template_idx", "0"))
    current_name = SERVICE_TEMPLATES[current_idx]["name"] if current_idx < len(SERVICE_TEMPLATES) else SERVICE_TEMPLATES[0]["name"]
    text = (
        f"🎨 <b>قالب نمایش سرویس کاربران</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"قالب فعلی: <b>{current_name}</b> (شماره {current_idx+1} از {len(SERVICE_TEMPLATES)})\n\n"
        f"با دکمه‌های زیر بین قالب‌ها جابجا شوید و پیش‌نمایش ببینید."
    )
    keyboard = _service_template_keyboard(current_idx)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


def _service_template_keyboard(idx):
    total = len(SERVICE_TEMPLATES)
    rows = [
        [h.ibtn(f"◀️ قبلی", callback_data=f"svc_tpl_nav:{(idx-1)%total}"),
         h.ibtn(f"{idx+1}/{total}", callback_data="noop"),
         h.ibtn(f"بعدی ▶️", callback_data=f"svc_tpl_nav:{(idx+1)%total}")],
        [h.ibtn(f"👁 پیش‌نمایش: {SERVICE_TEMPLATES[idx]['name']}", callback_data=f"svc_tpl_preview:{idx}")],
        [h.ibtn(f"✅ انتخاب این قالب", callback_data=f"svc_tpl_select:{idx}", style="success")],
        [h.ibtn(f"🔄 برگشت به پیش‌فرض", callback_data="svc_tpl_select:0")],
    ]
    return InlineKeyboardMarkup(rows)


async def admin_service_template_nav(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "settings"):
        return
    idx = int(query.data.split(":")[1])
    await query.answer()
    tpl = SERVICE_TEMPLATES[idx]
    text = (
        f"🎨 <b>قالب‌های سرویس</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"قالب {idx+1}/{len(SERVICE_TEMPLATES)}: <b>{tpl['name']}</b>"
    )
    try:
        await query.message.edit_text(text, parse_mode="HTML", reply_markup=_service_template_keyboard(idx))
    except Exception:
        pass


async def admin_service_template_preview(update, context):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split(":")[1])
    tpl = SERVICE_TEMPLATES[idx]
    preview = tpl["template"].format(
        status_icon="✅", status_label="فعال و متصل به اینترنت",
        username="u7ut41q5r", product_name="🚀 پرسرعت",
        price_line="💰 مبلغ پرداخت‌شده: ۱۹۰۰۰ تومان\n",
        order_id=285,
        usage_bar="🟩🟩🟩🟩🟩⬜️⬜️⬜️⬜️⬜️⬜️⬜️",
        used_str="2.50 GB", limit_str="5.00 GB",
        remaining_str="2.50 GB (50%)", usage_note="",
        time_section="⏳ <b>زمان باقی‌مانده:</b>\n🟩🟩🟩🟩🟩🟩⬜️⬜️⬜️⬜️⬜️⬜️  50%\n",
        expire_str="2026-09-11", countdown=" (29 روز دیگر)",
        expiry_note="",
    )
    await query.message.reply_text(f"👁 پیش‌نمایش <b>{tpl['name']}</b>:\n\n{preview}", parse_mode="HTML")


async def admin_service_template_select(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "settings"):
        return
    idx = int(query.data.split(":")[1])
    await database.set_setting("service_template_idx", str(idx))
    await database.log_action("admin", query.from_user.id, f"تغییر قالب سرویس به: {SERVICE_TEMPLATES[idx]['name']}")
    await query.answer(f"✅ قالب {SERVICE_TEMPLATES[idx]['name']} فعال شد", show_alert=True)
    try:
        await query.message.edit_reply_markup(reply_markup=_service_template_keyboard(idx))
    except Exception:
        pass


# ─────────────────────────────────────────────
# بخش مدیریت کانال اعتماد
# ─────────────────────────────────────────────

TRUST_CHANNEL_TEMPLATES = [
  {
    "name": "🛍️ کلاسیک (پیش‌فرض)",
    "template": "🛍️ <b>خرید جدید!</b>\n━━━━━━━━━━━\n👤 <b>مشتری:</b> {buyer_name}\n🆔 <b>Telegram ID:</b> <code>{buyer_id_masked}</code>\n👤 <b>نام سرویس:</b> {service_username}\n📦 <b>بسته:</b> {gb} گیگابایت | {days} روز\n💰 <b>مبلغ:</b> {price} تومان\n📅 <b>انقضا:</b> {expire_date}\n⏰ <b>زمان:</b> {now_str}\n\n✅ <b>سرویس مشتری با موفقیت و در سریع‌ترین زمان ممکن فعال شد.</b>\n💎 به جمع صدها مشتری راضی ما بپیوندید و همین حالا سرویس اختصاصی خودتان را تهیه کنید! 🚀"
  },
  {
    "name": "🔥 مدرن",
    "template": "🔥 <b>مشتری جدید</b>\n┄┄┄┄┄┄┄┄┄┄┄\n👤 {buyer_name} | <code>{buyer_id_masked}</code>\n🛡️ سرویس: <b>{service_username}</b>\n📦 {gb}GB / {days} روز\n💵 {price} تومان\n📅 تا {expire_date}\n🕐 {now_str}\n\n🚀 <i>فعال شد!</i>"
  },
  {
    "name": "💎 لوکس",
    "template": "✦ ✦ ✦ <b>خرید موفق</b> ✦ ✦ ✦\n\n💎 مشتری: <b>{buyer_name}</b>\n🆔 آیدی: <code>{buyer_id_masked}</code>\n🔑 سرویس: <code>{service_username}</code>\n📦 پلن: {gb} گیگ | {days} روز\n💰 مبلغ: {price} تومان\n📅 انقضا: {expire_date}\n⏱ زمان: {now_str}\n\n✨ ممنون از اعتماد شما 💜"
  },
  {
    "name": "⚡ فشرده",
    "template": "⚡ <b>خرید</b> | {buyer_name} | <code>{buyer_id_masked}</code>\n📦 {gb}GB/{days}d | 💰{price}T | 📅{expire_date}\n🔑 <code>{service_username}</code> | 🕐{now_str}"
  },
  {
    "name": "🌟 ستاره‌ای",
    "template": "⭐⭐⭐ <b>خرید جدید</b> ⭐⭐⭐\n\n🌟 مشتری: {buyer_name}\n🌟 آیدی: <code>{buyer_id_masked}</code>\n🌟 سرویس: <code>{service_username}</code>\n🌟 بسته: {gb}GB | {days} روز\n🌟 مبلغ: {price} تومان\n🌟 انقضا: {expire_date}\n🌟 زمان: {now_str}\n\n🎉 خوش اومدی به خانواده ما!"
  },
  {
    "name": "🎯 سازمانی",
    "template": "📋 <b>گزارش خرید</b>\n━━━━━━━━━━━━━━━\n👤 نام: {buyer_name}\n🆔 شناسه: <code>{buyer_id_masked}</code>\n🔑 یوزرنیم: <code>{service_username}</code>\n📊 حجم: {gb} GB | مدت: {days} روز\n💳 مبلغ: {price} تومان\n📅 انقضا: {expire_date}\n🕐 تاریخ: {now_str}\n━━━━━━━━━━━━━━━\n✅ وضعیت: فعال"
  },
  {
    "name": "🎮 گیمری",
    "template": "🎮 <b>NEW SUBSCRIBER!</b>\n━━━━━━━━━━━━━━━\n🎯 Player: {buyer_name}\n🆔 ID: <code>{buyer_id_masked}</code>\n⚔️ Account: <code>{service_username}</code>\n🏆 Pack: {gb}GB | {days} Days\n💰 Price: {price} T\n📅 Exp: {expire_date}\n🕐 Time: {now_str}\n━━━━━━━━━━━━━━━\n🚀 GG! Account Activated!"
  },
  {
    "name": "🌈 رنگارنگ",
    "template": "🟢🟡🔵 <b>خرید موفق</b> 🔵🟡🟢\n\n🔷 مشتری: {buyer_name}\n🔶 آیدی: <code>{buyer_id_masked}</code>\n🔷 سرویس: <code>{service_username}</code>\n🔶 {gb}GB | {days} روز | {price} تومان\n🔷 انقضا: {expire_date}\n🔶 زمان: {now_str}\n\n💜 ممنون از انتخاب شما!"
  },
  {
    "name": "🤖 رباتیک",
    "template": "🤖 <b>NEW ORDER CONFIRMED</b>\n[USER]: {buyer_name} | <code>{buyer_id_masked}</code>\n[SERVICE]: <code>{service_username}</code>\n[PLAN]: {gb}GB | {days}d | {price}T\n[EXPIRY]: {expire_date}\n[TIME]: {now_str}\n[STATUS]: ✅ ACTIVE"
  },
  {
    "name": "🎪 جشنی",
    "template": "🎉🎊 <b>خرید جدید!</b> 🎊🎉\n\n🎈 مشتری: <b>{buyer_name}</b>\n🎁 آیدی: <code>{buyer_id_masked}</code>\n🎯 سرویس: <code>{service_username}</code>\n🎪 بسته: {gb}GB | {days} روز\n💵 {price} تومان\n📅 تا {expire_date}\n⏰ {now_str}\n\n🥳 خوش اومدی! سرویست فعاله!"
  },
  {
    "name": "🔮 مرموز",
    "template": "🔮 <b>عضو جدید</b> 🔮\n═══════════════\n🗝️ {buyer_name} | <code>{buyer_id_masked}</code>\n📜 <code>{service_username}</code>\n⚗️ {gb}GB · {days}d · {price}T\n⏳ {expire_date} | {now_str}\n═══════════════\n✨ فعال شد!"
  },
  {
    "name": "🏆 قهرمانی",
    "template": "🏆 <b>مشتری جدید پیوست!</b>\n\n🥇 نام: {buyer_name}\n🆔 <code>{buyer_id_masked}</code>\n🎖️ سرویس: <code>{service_username}</code>\n📦 {gb}GB | {days} روز\n💰 {price} تومان\n📅 {expire_date} | ⏰ {now_str}\n\n🚀 به تیم قهرمانان خوش اومدی!"
  },
  {
    "name": "🌙 شبانه",
    "template": "🌙 <b>خرید شب</b>\n‣ مشتری: {buyer_name} (<code>{buyer_id_masked}</code>)\n‣ سرویس: <code>{service_username}</code>\n‣ {gb}GB | {days} روز | {price}T\n‣ انقضا: {expire_date}\n‣ {now_str}\n🌟 فعال شد!"
  },
  {
    "name": "🚀 فضایی",
    "template": "🚀 <b>LAUNCH CONFIRMED</b> 🛸\n\n👨‍🚀 Pilot: {buyer_name}\n🆔 <code>{buyer_id_masked}</code>\n🛰️ Station: <code>{service_username}</code>\n⚡ {gb}GB | {days}d | {price}T\n🌍 Land: {expire_date}\n🕐 Launch: {now_str}\n\n🚀 Mission Active! Godspeed!"
  },
  {
    "name": "💼 تجاری",
    "template": "💼 <b>فاکتور خرید</b>\n━━━━━━━━━━━━━━━\nمشتری: {buyer_name}\nشناسه: <code>{buyer_id_masked}</code>\nسرویس: <code>{service_username}</code>\n━━━━━━━━━━━━━━━\nحجم: {gb} گیگابایت\nمدت: {days} روز\nمبلغ: {price} تومان\nانقضا: {expire_date}\nزمان: {now_str}\n━━━━━━━━━━━━━━━\n✅ پرداخت تایید شد"
  },
  {
    "name": "🌺 گرم",
    "template": "🌺 سلام {buyer_name} عزیز!\n\nسرویست آماده‌ست 🎉\n🔑 <code>{service_username}</code>\n📦 {gb}GB | {days} روز\n💰 {price} تومان\n📅 تا {expire_date}\n⏰ {now_str}\n\n🙏 ممنون از اعتمادت!\n🆔 <code>{buyer_id_masked}</code>"
  },
  {
    "name": "📊 آماری",
    "template": "📊 <b>گزارش فروش</b>\n\n👤 {buyer_name} | <code>{buyer_id_masked}</code>\n📌 <code>{service_username}</code>\n📈 {gb}GB · {days}d · {price}T\n🗓 {expire_date} | 🕐 {now_str}\n━━━━━━━━━━━\n✅ ثبت شد"
  },
  {
    "name": "🏖️ کژوال",
    "template": "😎 یه مشتری جدید داریم!\n\n{buyer_name} ({buyer_id_masked}) خرید کرد\nسرویسش: <code>{service_username}</code>\n{gb}گیگ برای {days} روز به قیمت {price} تومن\nتا {expire_date} وقت داره\nثبت شد ساعت {now_str} 🕐"
  },
  {
    "name": "🎸 موزیکال",
    "template": "🎵 <b>New Hit!</b> 🎶\n\n🎤 Fan: {buyer_name} | <code>{buyer_id_masked}</code>\n🎸 Track: <code>{service_username}</code>\n🎼 {gb}GB | {days}d | {price}T\n📅 Until: {expire_date}\n🕐 {now_str}\n\n🎵 Playing! Enjoy the music!"
  },
  {
    "name": "🛡️ امنیتی",
    "template": "🛡️ <b>ACCESS GRANTED</b>\n═══════════════\n👤 {buyer_name}\n🔑 ID: <code>{buyer_id_masked}</code>\n🛡️ Account: <code>{service_username}</code>\n📊 {gb}GB | {days}d | {price}T\n⏰ Valid until: {expire_date}\n🕐 Activated: {now_str}\n═══════════════\n🔒 CONNECTION SECURED"
  }
]


async def admin_trust_channel_settings_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "settings"):
        return
    channel = await database.get_setting("trust_channel_id", "")
    enabled = await database.get_setting("trust_channel_enabled", "1") == "1"
    tpl_idx = int(await database.get_setting("trust_channel_template_idx", "0"))
    tpl_name = TRUST_CHANNEL_TEMPLATES[tpl_idx]["name"] if tpl_idx < len(TRUST_CHANNEL_TEMPLATES) else "-"
    buy_btn_enabled = await database.get_setting("trust_channel_buy_btn", "0") == "1"
    buy_btn_label = await database.get_setting("trust_channel_buy_btn_label", "🟢 تست و خرید")
    buy_btn_url = await database.get_setting("trust_channel_buy_btn_url", "")
    status = "✅ فعال" if enabled else "❌ غیرفعال"
    info = (
        f"📣 <b>مدیریت کانال اعتماد</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"وضعیت: <b>{status}</b>\n"
        f"کانال: <code>{channel or 'تنظیم نشده'}</code>\n"
        f"قالب: <b>{tpl_name}</b> (شماره {tpl_idx+1})\n"
        f"دکمه خرید: {'✅ فعال' if buy_btn_enabled else '❌ غیرفعال'}\n"
        f"لیبل دکمه: {buy_btn_label}\n"
        f"لینک دکمه: {buy_btn_url or 'تنظیم نشده'}\n"
    )
    keyboard = InlineKeyboardMarkup([
        [h.ibtn("✅ فعال‌سازی" if not enabled else "❌ غیرفعال‌سازی",
                callback_data="trust_toggle", style="success" if not enabled else "danger")],
        [h.ibtn("📝 تنظیم آیدی کانال", callback_data="trust_set_channel")],
        [h.ibtn("🎨 انتخاب قالب پیام", callback_data="trust_tpl_nav:0")],
        [h.ibtn("🔘 دکمه خرید زیر پیام: " + ("✅" if buy_btn_enabled else "❌"),
                callback_data="trust_buy_btn_toggle")],
        [h.ibtn("✏️ ویرایش لیبل دکمه", callback_data="trust_buy_btn_label"),
         h.ibtn("🔗 ویرایش لینک", callback_data="trust_buy_btn_url")],
        [h.ibtn("👁️ پیش‌نمایش پیام", callback_data="trust_preview")],
    ])
    await update.message.reply_text(info, parse_mode="HTML", reply_markup=keyboard)


async def admin_trust_toggle(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "settings"):
        return
    current = await database.get_setting("trust_channel_enabled", "1")
    new_val = "0" if current == "1" else "1"
    await database.set_setting("trust_channel_enabled", new_val)
    label = "فعال" if new_val == "1" else "غیرفعال"
    await query.answer(f"کانال اعتماد {label} شد", show_alert=True)
    try:
        await query.message.delete()
    except Exception:
        pass


async def admin_trust_buy_btn_toggle(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "settings"):
        return
    current = await database.get_setting("trust_channel_buy_btn", "0")
    new_val = "0" if current == "1" else "1"
    await database.set_setting("trust_channel_buy_btn", new_val)
    await query.answer("✅ تغییر کرد", show_alert=False)
    try:
        await query.message.delete()
    except Exception:
        pass


async def admin_trust_set_channel_start(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "settings"):
        return ConversationHandler.END
    context.user_data["admin_action"] = "trust_set_channel"
    await query.answer()
    await query.message.reply_text(
        "📣 آیدی کانال اعتماد را وارد کنید.\nمثال: @mychannel یا -1001234567890",
        reply_markup=h.flow_cancel_keyboard()
    )
    return h.AWAITING_ADMIN_INPUT


async def admin_trust_buy_btn_label_start(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "settings"):
        return ConversationHandler.END
    context.user_data["admin_action"] = "trust_buy_btn_label"
    await query.answer()
    await query.message.reply_text(
        "✏️ متن دکمه خرید را وارد کنید:\nمثال: 🟢 تست و خرید",
        reply_markup=h.flow_cancel_keyboard()
    )
    return h.AWAITING_ADMIN_INPUT


async def admin_trust_buy_btn_url_start(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "settings"):
        return ConversationHandler.END
    context.user_data["admin_action"] = "trust_buy_btn_url"
    await query.answer()
    await query.message.reply_text(
        "🔗 لینک دکمه خرید را وارد کنید:\nمثال: https://t.me/yourbot",
        reply_markup=h.flow_cancel_keyboard()
    )
    return h.AWAITING_ADMIN_INPUT


async def admin_trust_tpl_nav(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "settings"):
        return
    idx = int(query.data.split(":")[1])
    total = len(TRUST_CHANNEL_TEMPLATES)
    await query.answer()
    tpl = TRUST_CHANNEL_TEMPLATES[idx]
    text = (
        f"🎨 <b>قالب‌های کانال اعتماد</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"قالب {idx+1}/{total}: <b>{tpl['name']}</b>"
    )
    keyboard = InlineKeyboardMarkup([
        [h.ibtn("◀️ قبلی", callback_data=f"trust_tpl_nav:{(idx-1)%total}"),
         h.ibtn(f"{idx+1}/{total}", callback_data="noop"),
         h.ibtn("بعدی ▶️", callback_data=f"trust_tpl_nav:{(idx+1)%total}")],
        [h.ibtn(f"👁 پیش‌نمایش", callback_data=f"trust_tpl_preview:{idx}")],
        [h.ibtn(f"✅ انتخاب این قالب", callback_data=f"trust_tpl_select:{idx}", style="success")],
        [h.ibtn(f"🔄 برگشت به پیش‌فرض", callback_data="trust_tpl_select:0")],
    ])
    try:
        await query.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def admin_trust_tpl_preview(update, context):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split(":")[1])
    tpl = TRUST_CHANNEL_TEMPLATES[idx]
    preview = tpl["template"].format(
        buyer_name="محمد", buyer_id_masked="61***968",
        service_username="u75lhgr5d", gb=30, days=60,
        price="399,000", expire_date="2026-10-09",
        now_str="2026-08-13 15:30",
    )
    await query.message.reply_text(f"👁 پیش‌نمایش <b>{tpl['name']}</b>:\n\n{preview}", parse_mode="HTML")


async def admin_trust_tpl_select(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "settings"):
        return
    idx = int(query.data.split(":")[1])
    await database.set_setting("trust_channel_template_idx", str(idx))
    await query.answer(f"✅ قالب {TRUST_CHANNEL_TEMPLATES[idx]['name']} انتخاب شد", show_alert=True)
    try:
        await query.message.delete()
    except Exception:
        pass


async def admin_trust_preview(update, context):
    query = update.callback_query
    await query.answer()
    idx = int(await database.get_setting("trust_channel_template_idx", "0"))
    idx = max(0, min(idx, len(TRUST_CHANNEL_TEMPLATES)-1))
    tpl = TRUST_CHANNEL_TEMPLATES[idx]
    preview = tpl["template"].format(
        buyer_name="محمد", buyer_id_masked="61***968",
        service_username="u75lhgr5d", gb=30, days=60,
        price="399,000", expire_date="2026-10-09",
        now_str="2026-08-13 15:30",
    )
    buy_btn_enabled = await database.get_setting("trust_channel_buy_btn", "0") == "1"
    buy_btn_label = await database.get_setting("trust_channel_buy_btn_label", "🟢 تست و خرید")
    buy_btn_url = await database.get_setting("trust_channel_buy_btn_url", "")
    keyboard = None
    if buy_btn_enabled and buy_btn_url:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(buy_btn_label, url=buy_btn_url)]])
    await query.message.reply_text(f"👁 پیش‌نمایش نهایی:\n\n{preview}", parse_mode="HTML", reply_markup=keyboard)
