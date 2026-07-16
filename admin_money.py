# -*- coding: utf-8 -*-
# admin_money.py - کد تخفیف، سرورها، مدیریت مالی، منوی اطلاع‌رسانی

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler

import database
import handlers as h


# ---------------- کدهای تخفیف ----------------

async def admin_discounts_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "discounts"):
        return
    codes = await database.list_discount_codes()
    lines = ["🎟️ کدهای تخفیف\n━━━━━━━━━━━━━━━"]
    keyboard = []
    for c in codes:
        unit = "%" if c["kind"] == "percent" else " تومان"
        limit_text = "بی‌نهایت" if not c["max_uses"] else f"{c['used_count']}/{c['max_uses']}"
        lines.append(f"• {c['code']} — {c['value']}{unit} — استفاده: {limit_text}")
        keyboard.append([h.ibtn(f"🗑 حذف {c['code']}", callback_data=f"discount_delete:{c['code']}")])
    keyboard.append([h.ibtn("➕ کد تخفیف جدید", callback_data="discount_add_start")])
    if not codes:
        lines.append("هنوز کدی ثبت نشده.")
    await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))


async def discount_add_start(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "discounts"):
        return ConversationHandler.END
    context.user_data["admin_action"] = "discount_add_code"
    await query.answer()
    await query.message.reply_text("🎟️ کد تخفیف جدید را وارد کنید (مثلا SUMMER20): (لغو: /cancel)")
    return h.AWAITING_ADMIN_INPUT


async def discount_delete_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "discounts"):
        return
    code = query.data.split(":", 1)[1]
    await database.delete_discount_code(code)
    await database.log_action("admin", query.from_user.id, f"حذف کد تخفیف {code}")
    await query.answer("حذف شد.")
    await query.message.reply_text(f"✅ کد {code} حذف شد.")


# ---------------- سرورها ----------------

async def admin_servers_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "servers"):
        return
    servers = await database.list_servers()
    lines = ["🖥️ سرورها\n━━━━━━━━━━━━━━━"]
    keyboard = []
    for s in servers:
        icon = "🟢" if s["enabled"] else "🔴"
        user_count = await database.count_users_on_server(s["id"]) if hasattr(database, "count_users_on_server") else 0
        lines.append(f"{icon} {s['name']} — {s['address']} — 👥 {user_count}")
        keyboard.append([
            h.ibtn("🔌 وضعیت", callback_data=f"server_check:{s['id']}"),
            h.ibtn("🔄 فعال/معلق", callback_data=f"server_toggle:{s['id']}"),
            h.ibtn("🗑", callback_data=f"server_delete:{s['id']}"),
        ])
    keyboard.append([h.ibtn("➕ افزودن سرور", callback_data="server_add_start")])
    if not servers:
        lines.append("هنوز سروری ثبت نشده.")
    await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))


async def server_add_start(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "servers"):
        return ConversationHandler.END
    context.user_data["admin_action"] = "server_add_name"
    await query.answer()
    await query.message.reply_text("🖥️ نام سرور را وارد کنید: (لغو: /cancel)")
    return h.AWAITING_ADMIN_INPUT


async def server_action_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "servers"):
        return
    data = query.data

    if data.startswith("server_delete:"):
        server_id = int(data.split(":")[1])
        await database.remove_server(server_id)
        await database.log_action("admin", query.from_user.id, f"حذف سرور #{server_id}")
        await query.answer("حذف شد.")
        await query.message.reply_text("✅ سرور حذف شد.")
        return

    if data.startswith("server_toggle:"):
        server_id = int(data.split(":")[1])
        server = await database.get_server(server_id)
        if not server:
            await query.answer("سرور پیدا نشد.", show_alert=True)
            return
        await database.toggle_server(server_id)
        await query.answer("وضعیت عوض شد.")
        await query.message.reply_text("✅ وضعیت سرور تعویض شد.")
        return

    if data.startswith("server_check:"):
        server_id = int(data.split(":")[1])
        server = await database.get_server(server_id)
        if not server:
            await query.answer("سرور پیدا نشد.", show_alert=True)
            return
        await query.answer()
        online = False
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(server["address"])
                online = response.status_code < 500
        except Exception:
            online = False
        status_text = "🟢 انلاین" if online else "🔴 امکان دسترسی نیست / افلاین"
        await query.message.reply_text(f"🔌 وضعیت {server['name']}: {status_text}\n(بررسی ساده بر اساس اتصال HTTP به ادرس سرور)")
        return


# ---------------- مالی ----------------

async def admin_finance_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "finance"):
        return
    import datetime as _dt
    total = await database.revenue_total()
    today_iso = _dt.datetime.utcnow().date().isoformat()
    week_iso = (_dt.datetime.utcnow() - _dt.timedelta(days=7)).isoformat()
    month_iso = (_dt.datetime.utcnow() - _dt.timedelta(days=30)).isoformat()
    today = await database.revenue_since(today_iso)
    week = await database.revenue_since(week_iso)
    month = await database.revenue_since(month_iso)
    approved_count = await database.count_orders(status="approved")
    text = (
        "💰 مدیریت مالی\n━━━━━━━━━━━━━━━\n"
        f"📈 درآمد کل: {total:,} تومان\n"
        f"📅 امروز: {today:,} تومان\n"
        f"📍 این هفته: {week:,} تومان\n"
        f"📆 این ماه: {month:,} تومان\n\n"
        f"🧾 تعداد تراکنش تایید‌شده: {approved_count}"
    )
    await update.message.reply_text(text)


async def admin_notify_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "notify"):
        return
    packages = await database.list_packages()
    keyboard = [[h.ibtn("📢 پیام همگانی به همه", callback_data="broadcast_start")]]
    for p in packages:
        keyboard.append([h.ibtn(f"🎯 به کاربران {p['name']}", callback_data=f"notify_pkg:{p['id']}")])
    await update.message.reply_text(
        "📢 اطلاع‌رسانی\n━━━━━━━━━━━━━━━\nیک گزینه را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
