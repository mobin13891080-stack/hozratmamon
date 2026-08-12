# -*- coding: utf-8 -*-
# admin_stats_logs.py - آمار ربات و لاگ‌ها

import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import database
import handlers as h


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
        [h.ibtn("👮 فعالیت ادمین‌ها", callback_data="logs_filter:admin")],
        [h.ibtn("❌ خطاهای ربات", callback_data="logs_filter:error")],
        [h.ibtn("🔌 لاگ مرزبان", callback_data="logs_filter:marzban_api")],
        [h.ibtn("🗑 لاگ حذفی کاربرا", callback_data="logs_filter:user_deletion")],
        [h.ibtn("📜 همه لاگ‌ها", callback_data="logs_filter:all")],
    ])
    await update.message.reply_text("📜 نوع لاگ را انتخاب کنید:", reply_markup=keyboard)


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
    keyboard = InlineKeyboardMarkup([[h.ibtn("🧹 پاک کردن همین نوع لاگ", callback_data=f"logs_clear:{log_type}", style="danger")]])
    await query.message.reply_text(text, reply_markup=keyboard)


async def logs_clear_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "logs"):
        return
    log_type = query.data.split(":", 1)[1]
    count = await database.clear_logs(None if log_type == "all" else log_type)
    await query.answer("✅ پاک شد", show_alert=True)
    await query.message.reply_text(f"🧹 {count} لاگ از نوع {log_type} پاک شد.")
