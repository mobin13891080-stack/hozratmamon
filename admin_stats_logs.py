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
        f"🚫 لفو‌شده: {orders.get('cancelled', 0)}\n\n"
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
        lines.append(f"• [{entry['created_at']}] {entry.get('actor_id') or '-'}: {entry['message']}")
    text = "\n".join(lines)
    if len(text) > 3500:
        text = text[:3500] + "\n..."
    await query.message.reply_text(text)
