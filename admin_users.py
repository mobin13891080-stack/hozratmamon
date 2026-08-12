# -*- coding: utf-8 -*-
# admin_users.py - مدیریت کامل کاربران: لیست، جستجو، مسدود/فعال، حذف، پیام خصوصی، همگانی

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler

import database
import handlers as h
from handlers import safe_answer

PAGE_SIZE = 8


def _user_line(u):
    blocked = " 🚫" if u.get("is_blocked") else ""
    return f"👤 {u['user_id']} @{u['username'] or '-'}{blocked}"


async def _users_page(offset):
    users = await database.list_users(offset=offset, limit=PAGE_SIZE)
    total = await database.count_users()
    keyboard = [[h.ibtn(_user_line(u), callback_data=f"user_view:{u['user_id']}")] for u in users]
    nav_row = []
    if offset > 0:
        nav_row.append(h.ibtn("⬅️ قبلی", callback_data=f"user_page:{max(offset - PAGE_SIZE, 0)}"))
    if offset + PAGE_SIZE < total:
        nav_row.append(h.ibtn("بعدی ➡️", callback_data=f"user_page:{offset + PAGE_SIZE}"))
    if nav_row:
        keyboard.append(nav_row)
    keyboard.append([h.ibtn("🔍 جستجوی کاربر", callback_data="user_search_start")])
    keyboard.append([h.ibtn("📢 پیام همگانی", callback_data="broadcast_start")])
    text = f"👥 کاربران (کل: {total})\n━━━━━━━━━━━━━━━"
    return text, InlineKeyboardMarkup(keyboard)


async def admin_users_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "users"):
        return
    text, keyboard = await _users_page(0)
    await update.message.reply_text(text, reply_markup=keyboard)


async def user_search_start(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "users"):
        return ConversationHandler.END
    context.user_data["admin_action"] = "user_search"
    await query.answer()
    await query.message.reply_text("🔍 <b>ایدی عددی تلگرام یا نام کاربری (@username) را وارد کنید:</b>", parse_mode="HTML", reply_markup=h.flow_cancel_keyboard())
    return h.AWAITING_ADMIN_INPUT


async def broadcast_start(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "notify"):
        return ConversationHandler.END
    context.user_data["admin_action"] = "broadcast_all"
    await query.answer()
    await query.message.reply_text(
        "📢 <b>متن پیام همگانی را وارد کنید:</b>\n\nیا یک عکس با کپشن بفرستید، یا پیامی را از کانال خودتون فوروارد کنید تا عین آن برای همه کاربران فوروارد شود.",
        parse_mode="HTML",
        reply_markup=h.flow_cancel_keyboard(),
    )
    return h.AWAITING_ADMIN_INPUT


async def notify_pkg_choose(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "notify"):
        return ConversationHandler.END
    package_id = int(query.data.split(":")[1])
    context.user_data["admin_action"] = f"broadcast_package:{package_id}"
    await query.answer()
    await query.message.reply_text(
        "🎯 <b>متن پیام برای کاربران این پلن را وارد کنید:</b>\n\nیا یک عکس با کپشن بفرستید، یا پیامی را فوروارد کنید تا عین آن برای این کاربران فوروارد شود.",
        parse_mode="HTML",
        reply_markup=h.flow_cancel_keyboard(),
    )
    return h.AWAITING_ADMIN_INPUT


async def _user_detail_view(user_id):
    user = await database.get_user(user_id)
    if not user:
        return None, None
    orders = await database.list_orders_by_user(user_id)
    approved = [o for o in orders if o["status"] == "approved"]
    blocked_text = "🚫 مسدود" if user["is_blocked"] else "✅ فعال"
    trial_text = "استفاده شده" if user["used_trial"] else "استفاده نشده"
    lines = [
        f"👤 کاربر {user['user_id']} (@{user['username'] or '-'})",
        f"📅 عضویت: {user['joined_at']}",
        f"🎁 تست رایگان: {trial_text}",
        f"وضعیت: {blocked_text}",
        f"📭 تعداد سفارش فعال: {len(approved)}",
        "",
        "🧾 سوابق خرید:",
    ]
    for o in orders[:10]:
        lines.append(f"• #{o['id']} — {o['status']} — {o.get('price') or 0:,} تومان")

    keyboard = []
    for o in approved:
        if o.get("marzban_username"):
            keyboard.append([h.ibtn(f"🔧 مدیریت اکانت سفارش #{o['id']}", callback_data=f"mz_view:{o['marzban_username']}")])
    block_label = "✅ فعال‌سازی" if user["is_blocked"] else "🚫 مسدود سازی"
    keyboard.append([h.ibtn(block_label, callback_data=f"user_toggle_block:{user_id}")])
    keyboard.append([h.ibtn("📩 پیام خصوصی", callback_data=f"user_dm_start:{user_id}")])
    keyboard.append([h.ibtn("🗑 حذف کاربر", callback_data=f"user_delete:{user_id}")])
    keyboard.append([h.ibtn("🔙 بازگشت به لیست", callback_data="user_page:0")])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


async def user_action_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "users"):
        return ConversationHandler.END
    data = query.data

    if data.startswith("user_page:"):
        offset = int(data.split(":")[1])
        await query.answer()
        text, keyboard = await _users_page(offset)
        await h.safe_edit_or_send(query, text, reply_markup=keyboard)
        return

    if data.startswith("user_view:"):
        target_id = int(data.split(":")[1])
        text, keyboard = await _user_detail_view(target_id)
        await query.answer()
        if not text:
            await query.message.reply_text("کاربر پیدا نشد.")
            return
        await query.message.reply_text(text, reply_markup=keyboard)
        return

    if data.startswith("user_toggle_block:"):
        target_id = int(data.split(":")[1])
        user = await database.get_user(target_id)
        if not user:
            await query.answer("کاربر پیدا نشد.", show_alert=True)
            return
        await database.set_user_blocked(target_id, not user["is_blocked"])
        await database.log_action("admin", query.from_user.id, f"توگل مسدودسازی کاربر {target_id}")
        await query.answer("وضعیت عوض شد.")
        text, keyboard = await _user_detail_view(target_id)
        await h.safe_edit_or_send(query, text, reply_markup=keyboard)
        return

    if data.startswith("user_delete:"):
        target_id = int(data.split(":")[1])
        await database.delete_user(target_id)
        await database.log_action("admin", query.from_user.id, f"حذف کاربر {target_id}")
        await query.answer("کاربر حذف شد.")
        await query.message.reply_text("✅ کاربر از دیتاباز ربات حذف شد.")
        return


async def user_dm_start(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "users"):
        return ConversationHandler.END
    target_id = int(query.data.split(":")[1])
    context.user_data["admin_action"] = f"user_dm:{target_id}"
    await query.answer()
    await query.message.reply_text(f"📩 <b>متن پیام خصوصی به {target_id} را وارد کنید:</b>", parse_mode="HTML", reply_markup=h.flow_cancel_keyboard())
    return h.AWAITING_ADMIN_INPUT
