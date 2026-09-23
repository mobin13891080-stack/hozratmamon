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


# ==================== پنل دیباگ ====================

async def admin_debug_show(update, context):
    """پنل دیباگ: نمایش باگ‌های فعلی یا رفع خودکار آن‌ها"""
    if not await h.require_perm_msg(update, update.effective_user.id, "logs"):
        return
    keyboard = InlineKeyboardMarkup([
        [h.ibtn("🔍 نمایش باگ‌های فعلی", callback_data="debug_action:show_bugs")],
        [h.ibtn("🔧 رفع خودکار باگ‌ها", callback_data="debug_action:fix_bugs")],
    ])
    await update.message.reply_text(
        "🐛 <b>پنل دیباگ</b>\n\n"
        "گزینه اول: لیست باگ‌های فعلی را به صورت فایل txt دریافت کنید.\n"
        "گزینه دوم: تمام باگ‌های قابل رفع را به صورت خودکار برطرف کند.",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def debug_action_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "logs"):
        return
    action = query.data.split(":")[1]
    await query.answer()

    if action == "show_bugs":
        await _debug_show_bugs(query, context)
    elif action == "fix_bugs":
        await _debug_fix_bugs(query, context)


async def _debug_show_bugs(query, context):
    """جمع‌آوری و ارسال گزارش باگ‌های فعلی به صورت فایل txt"""
    import io
    import datetime
    import database as db

    lines = []
    lines.append("=" * 60)
    lines.append(f"گزارش باگ‌های ربات — {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("=" * 60)
    lines.append("")

    bugs_found = 0

    # ۱. سفارش‌های pending قدیمی (بیش از ۲۴ ساعت)
    try:
        rs = await db.execute(
            "SELECT id, user_id, created_at FROM orders WHERE status='pending' "
            "AND created_at < datetime('now', '-1 day') ORDER BY id DESC LIMIT 20"
        )
        old_pending = rs.rows if rs.rows else []
        if old_pending:
            bugs_found += 1
            lines.append(f"🔴 باگ {bugs_found}: سفارش‌های pending بیش از ۲۴ ساعت ({len(old_pending)} مورد)")
            for row in old_pending:
                lines.append(f"   - سفارش #{row[0]} | کاربر {row[1]} | تاریخ: {row[2]}")
            lines.append("")
    except Exception as e:
        lines.append(f"⚠️ خطا در بررسی pending orders: {e}")
        lines.append("")

    # ۲. سفارش‌های processing گیر کرده
    try:
        rs = await db.execute(
            "SELECT id, user_id, created_at FROM orders WHERE status='processing' "
            "AND created_at < datetime('now', '-30 minutes') ORDER BY id DESC LIMIT 20"
        )
        stuck = rs.rows if rs.rows else []
        if stuck:
            bugs_found += 1
            lines.append(f"🔴 باگ {bugs_found}: سفارش‌های processing گیر کرده ({len(stuck)} مورد)")
            for row in stuck:
                lines.append(f"   - سفارش #{row[0]} | کاربر {row[1]} | از: {row[2]}")
            lines.append("")
    except Exception as e:
        lines.append(f"⚠️ خطا در بررسی processing stuck: {e}")
        lines.append("")

    # ۳. خطاهای اخیر در لاگ
    try:
        error_logs = await db.list_logs("error", limit=15)
        if error_logs:
            bugs_found += 1
            lines.append(f"🟡 باگ {bugs_found}: {len(error_logs)} خطای اخیر در لاگ")
            for entry in error_logs[:5]:
                msg = str(entry.get("message", ""))[:120]
                lines.append(f"   [{entry.get('created_at','')}] {msg}")
            if len(error_logs) > 5:
                lines.append(f"   ... و {len(error_logs)-5} خطای دیگر")
            lines.append("")
    except Exception as e:
        lines.append(f"⚠️ خطا در بررسی error logs: {e}")
        lines.append("")

    # ۴. تراکنش‌های کیف پول منفی/ناسازگار
    try:
        rs = await db.execute(
            "SELECT user_id, SUM(amount) as bal FROM wallet_transactions "
            "GROUP BY user_id HAVING bal < 0 LIMIT 10"
        )
        negative = rs.rows if rs.rows else []
        if negative:
            bugs_found += 1
            lines.append(f"🔴 باگ {bugs_found}: کاربران با موجودی منفی ({len(negative)} نفر)")
            for row in negative:
                lines.append(f"   - کاربر {row[0]}: موجودی {row[1]:,} تومان")
            lines.append("")
    except Exception as e:
        lines.append(f"⚠️ خطا در بررسی wallet: {e}")
        lines.append("")

    # ۵. کاربران بلاک‌شده که سرویس فعال دارند
    try:
        rs = await db.execute(
            "SELECT u.user_id FROM users u "
            "JOIN orders o ON o.user_id = u.user_id "
            "WHERE u.is_blocked = 1 AND o.status = 'approved' LIMIT 10"
        )
        blocked_with_service = rs.rows if rs.rows else []
        if blocked_with_service:
            bugs_found += 1
            lines.append(f"🟡 باگ {bugs_found}: کاربران بلاک‌شده با سرویس فعال ({len(blocked_with_service)} نفر)")
            for row in blocked_with_service:
                lines.append(f"   - کاربر {row[0]}")
            lines.append("")
    except Exception as e:
        lines.append(f"⚠️ خطا: {e}")
        lines.append("")

    lines.append("=" * 60)
    if bugs_found == 0:
        lines.append("✅ هیچ باگ شناخته‌شده‌ای پیدا نشد!")
    else:
        lines.append(f"جمع کل مشکلات شناسایی‌شده: {bugs_found}")
    lines.append("=" * 60)

    report = "\n".join(lines)
    file_obj = io.BytesIO(report.encode("utf-8"))
    file_obj.name = f"bug_report_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M')}.txt"

    await query.message.reply_document(
        document=file_obj,
        caption=f"🐛 گزارش باگ — {bugs_found} مشکل شناسایی‌شد" if bugs_found else "✅ ربات سالم است",
    )


async def _debug_fix_bugs(query, context):
    """رفع خودکار باگ‌های قابل‌رفع"""
    import database as db

    fixed = []
    errors = []

    # ۱. سفارش‌های processing گیر کرده → برگشت به pending
    try:
        rs = await db.execute(
            "UPDATE orders SET status='pending' "
            "WHERE status='processing' AND created_at < datetime('now', '-30 minutes') RETURNING id"
        )
        if rs and rs.rows:
            fixed.append(f"✅ {len(rs.rows)} سفارش processing گیر‌کرده → pending برگشت داده شد")
        else:
            # fallback بدون RETURNING
            rs2 = await db.execute(
                "SELECT id FROM orders WHERE status='processing' AND created_at < datetime('now', '-30 minutes')"
            )
            count = len(rs2.rows) if rs2.rows else 0
            if count:
                await db.execute(
                    "UPDATE orders SET status='pending' WHERE status='processing' AND created_at < datetime('now', '-30 minutes')"
                )
                fixed.append(f"✅ {count} سفارش processing گیر‌کرده → pending برگشت داده شد")
    except Exception as e:
        errors.append(f"❌ خطا در رفع processing stuck: {e}")

    # ۲. پاک کردن لاگ‌های قدیمی‌تر از ۳۰ روز
    try:
        rs = await db.execute("DELETE FROM logs WHERE created_at < datetime('now', '-30 days')")
        fixed.append("✅ لاگ‌های قدیمی‌تر از ۳۰ روز پاک شدند")
    except Exception as e:
        errors.append(f"❌ خطا در پاک‌سازی لاگ: {e}")

    lines = ["🔧 <b>نتیجه رفع خودکار باگ‌ها</b>\n"]
    lines += fixed if fixed else ["⚠️ چیزی برای رفع پیدا نشد"]
    if errors:
        lines.append("")
        lines += errors

    await query.message.reply_text("\n".join(lines), parse_mode="HTML")
