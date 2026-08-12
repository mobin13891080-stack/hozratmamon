# -*- coding: utf-8 -*-
# admin_stats_logs.py - آمار ربات و لاگ‌ها

import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import database
import handlers as h
from handlers import safe_answer


async def admin_stats_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "stats"):
        return
    total_users = await database.count_users()
    active_users = await database.count_active_users()
    today_iso = datetime.datetime.utcnow().date().isoformat()
    new_today = await database.count_new_users_since(today_iso)
    orders = await database.order_stats()
    revenue = await database.revenue_total()
    packages = await database.list_packages()
    active_packages = len([p for p in packages if p["active"]])

    text = (
        "📊 آمار ربات\n━━━━━━━━━━━━━━━\n"
        f"👥 کل کاربران: {total_users}\n"
        f"✅ کاربران فعال (دارای خرید موفق): {active_users}\n"
        f"🆕 ثبت‌نام امروز: {new_today}\n\n"
        "🧾 وضعیت سفارش‌ها:\n"
        f"⏳ در انتظار: {orders.get('pending', 0)}\n"
        f"✅ تایید‌شده (فعال): {orders.get('approved', 0)}\n"
        f"⌛ منقضی‌شده: {orders.get('expired', 0)}\n"
        f"❌ رد‌شده: {orders.get('rejected', 0)}\n"
        f"🚫 لغو‌شده: {orders.get('cancelled', 0)}\n\n"
        f"💰 درآمد کل: {revenue:,} تومان\n"
        f"📦 پلن‌های فعال: {active_packages} از {len(packages)}"
    )
    await update.message.reply_text(text)


async def admin_logs_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "logs"):
        return
    keyboard = InlineKeyboardMarkup([
        [h.ibtn("👮 فعالیت ادمین‌ها", callback_data="logs_filter:admin"),
         h.ibtn("♻️", callback_data="logs_reset_confirm:admin", style="danger")],
        [h.ibtn("❌ خطاهای ربات", callback_data="logs_filter:error"),
         h.ibtn("♻️", callback_data="logs_reset_confirm:error", style="danger")],
        [h.ibtn("🔌 لاگ مرزبان", callback_data="logs_filter:marzban_api"),
         h.ibtn("♻️", callback_data="logs_reset_confirm:marzban_api", style="danger")],
        [h.ibtn("🗑 لاگ حذفی کاربرا", callback_data="logs_filter:user_deletion"),
         h.ibtn("♻️", callback_data="logs_reset_confirm:user_deletion", style="danger")],
        [h.ibtn("📜 همه لاگ‌ها", callback_data="logs_filter:all"),
         h.ibtn("♻️ همه", callback_data="logs_reset_confirm:all", style="danger")],
    ])
    await update.message.reply_text(
        "📜 نوع لاگ را انتخاب کنید (با زدن ♻️ همان نوع لاگ ریست می‌شود):",
        reply_markup=keyboard,
    )


async def logs_reset_confirm_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "logs"):
        return
    log_type = query.data.split(":")[1]
    await safe_answer(query)
    label = "همهٔ لاگ‌ها" if log_type == "all" else f"لاگ‌های نوع «{log_type}»"
    keyboard = InlineKeyboardMarkup([
        [h.ibtn("✅ بله، حذف کن", callback_data=f"logs_reset_do:{log_type}", style="danger"),
         h.ibtn("❌ انصراف", callback_data="admin_logs_menu")],
    ])
    await h.safe_edit_or_send(
        query,
        f"⚠️ آیا مطمئنید {label} برای همیشه حذف شود؟ (این کار فقط خود لاگ‌ها را می‌پاکد و هیچ تاثیری روی کاربران/سفارش‌ها ندارد.)",
        reply_markup=keyboard,
    )


async def logs_reset_do_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "logs"):
        return
    log_type = query.data.split(":")[1]
    affected = await database.clear_logs(log_type)
    await safe_answer(query, "✅ حذف شد.")
    await query.message.reply_text(f"✅ {affected} لاگ حذف شد.")


async def logs_filter_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "logs"):
        return
    log_type = query.data.split(":")[1]
    await query.answer()
    logs = await database.list_logs(None if log_type == "all" else log_type, limit=25)
    if not logs:
        await query.message.reply_text("لاگی یافت نشد.")
        return
    lines = [f"📜 لاگ‌ها ({log_type})\n━━━━━━━━━━━━━━━"]
    for entry in logs:
        if entry.get("log_type") == "user_deletion":
            # متن لاگ خودش شامل تاریخ/ساعت دقیق به وقت تهران است، برای همین از نمایش تاریخ خام UTC دوباره صرف‌نظر می‌شود
            lines.append(f"• {entry['message']}\n")
        else:
            lines.append(f"• [{entry['created_at']}] {entry.get('actor_id') or '-'}: {entry['message']}")
    text = "\n".join(lines)
    if len(text) > 3500:
        text = text[:3500] + "\n..."
    await query.message.reply_text(text)



async def admin_logs_menu_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "logs"):
        return
    await safe_answer(query)
    keyboard = InlineKeyboardMarkup([
        [h.ibtn("👮 فعالیت ادمین‌ها", callback_data="logs_filter:admin"),
         h.ibtn("♻️", callback_data="logs_reset_confirm:admin", style="danger")],
        [h.ibtn("❌ خطاهای ربات", callback_data="logs_filter:error"),
         h.ibtn("♻️", callback_data="logs_reset_confirm:error", style="danger")],
        [h.ibtn("🔌 لاگ مرزبان", callback_data="logs_filter:marzban_api"),
         h.ibtn("♻️", callback_data="logs_reset_confirm:marzban_api", style="danger")],
        [h.ibtn("🗑 لاگ حذفی کاربرا", callback_data="logs_filter:user_deletion"),
         h.ibtn("♻️", callback_data="logs_reset_confirm:user_deletion", style="danger")],
        [h.ibtn("📜 همه لاگ‌ها", callback_data="logs_filter:all"),
         h.ibtn("♻️ همه", callback_data="logs_reset_confirm:all", style="danger")],
    ])
    await h.safe_edit_or_send(query, "📜 نوع لاگ را انتخاب کنید:", reply_markup=keyboard)
