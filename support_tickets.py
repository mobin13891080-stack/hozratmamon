# -*- coding: utf-8 -*-
# support_tickets.py - بخش پشتیبانی و سیستم تیکت (مشابه اسکرین‌شاتی که فرستادید)

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler

import config
import database
import handlers as h

logger = logging.getLogger(__name__)


async def support_show(update, context):
    support_text = await database.get_setting("support_text", "🆘 پشتیبانی")
    support_username = await database.get_setting("support_username", "@YourSupportUsername")
    text = f"{support_text}\n\n👤 {support_username}"
    contact_url = "https://t.me/" + support_username.lstrip("@") if not support_username.startswith("http") else support_username
    keyboard = InlineKeyboardMarkup([
        [h.ibtn("🎫 ایجاد تیکت", callback_data="ticket_new")],
        [h.ibtn("📋 تیکت‌های من", callback_data="ticket_list")],
        [h.ibtn("💬 تماس مستقیم", url=contact_url)],
    ])
    await update.message.reply_text(text, reply_markup=keyboard)


async def ticket_new_start(update, context):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("❓ <b>مشکل یا سوال خود را در یک پیام بنویسید:</b>", parse_mode="HTML", reply_markup=h.flow_cancel_keyboard())
    return h.TICKET_USER_MESSAGE


async def receive_ticket_new_message(update, context):
    if await h.redirect_if_menu_button(update, context):
        return ConversationHandler.END
    message = update.effective_message
    if message is None or not (message.text and message.text.strip()):
        return h.TICKET_USER_MESSAGE
    user = update.effective_user
    message_text = message.text.strip()
    subject = message_text[:40]
    ticket_id = await database.create_ticket(user.id, subject)
    await database.add_ticket_message(ticket_id, user.id, False, message_text)
    await update.message.reply_text(f"✅ تیکت شما با شماره #{ticket_id} ثبت شد. به‌زودی پاسخ داده می‌شود.")
    keyboard = InlineKeyboardMarkup([
        [h.ibtn("✍️ پاسخ", callback_data=f"ticket_reply_start:{ticket_id}")],
        [h.ibtn("✅ بستن تیکت", callback_data=f"ticket_close:{ticket_id}")],
    ])
    admin_ids = {config.ADMIN_ID} | {a["user_id"] for a in await database.list_admins()}
    for admin_id in admin_ids:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"🎫 تیکت جدید #{ticket_id}\nکاربر: {user.mention_html()} (آیدی: {user.id})\nپیام: {message_text}",
                parse_mode="HTML", reply_markup=keyboard,
            )
        except Exception:
            pass
    return ConversationHandler.END


async def ticket_cancel(update, context):
    await update.message.reply_text("عملیات لغو شد.")
    return ConversationHandler.END


async def ticket_list_callback(update, context):
    query = update.callback_query
    await query.answer()
    tickets = await database.list_tickets_by_user(query.from_user.id)
    if not tickets:
        await query.message.reply_text("شما هیچ تیکتی ثبت نکرده‌اید.")
        return
    status_icon = {"open": "🟡", "answered": "🟢", "closed": "⚪️"}
    keyboard = [[h.ibtn(
        f"{status_icon.get(t['status'], '⚪️')} تیکت #{t['id']} — {t['subject']}", callback_data=f"ticket_view:{t['id']}",
    )] for t in tickets]
    await query.message.reply_text("📋 تیکت‌های شما:", reply_markup=InlineKeyboardMarkup(keyboard))


async def ticket_view_callback(update, context):
    query = update.callback_query
    await query.answer()
    try:
        ticket_id = int(query.data.split(":")[1])
        ticket = await database.get_ticket(ticket_id)
        if not ticket:
            await query.message.reply_text("تیکت پیدا نشد.")
            return
        if ticket["user_id"] != query.from_user.id and not await database.is_admin(query.from_user.id):
            await query.message.reply_text("شما به این تیکت دسترسی ندارید.")
            return
        messages = await database.list_ticket_messages(ticket_id)
        lines = [f"🎫 تیکت #{ticket_id} — وضعیت: {ticket['status']}\n"]
        for m in messages:
            sender = "👮 پشتیبانی" if m["is_admin"] else "👤 شما"
            lines.append(f"{sender}: {m['message']}")
        await query.message.reply_text("\n".join(lines))
    except Exception:
        logger.exception("خطا در ticket_view_callback")
        try:
            await query.message.reply_text("⚠️ خطایی رخ داد، لطفا دوباره تلاش کنید.")
        except Exception:
            pass


async def ticket_close_callback(update, context):
    query = update.callback_query
    await query.answer()
    try:
        ticket_id = int(query.data.split(":")[1])
        ticket = await database.get_ticket(ticket_id)
        if not ticket:
            await query.message.reply_text("تیکت پیدا نشد.")
            return
        if not await database.is_admin(query.from_user.id) and ticket["user_id"] != query.from_user.id:
            await query.message.reply_text("⛔ دسترسی ندارید.")
            return
        await database.set_ticket_status(ticket_id, "closed")
        await query.message.reply_text("✅ تیکت بسته شد.")
        try:
            await context.bot.send_message(chat_id=ticket["user_id"], text=f"🎫 تیکت #{ticket_id} بسته شد.")
        except Exception:
            pass
    except Exception:
        logger.exception("خطا در ticket_close_callback")
        try:
            await query.message.reply_text("⚠️ خطایی رخ داد، لطفا دوباره تلاش کنید.")
        except Exception:
            pass


async def ticket_reply_start(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "tickets"):
        return ConversationHandler.END
    ticket_id = int(query.data.split(":")[1])
    context.user_data["reply_ticket_id"] = ticket_id
    await query.answer()
    await query.message.reply_text("💬 <b>متن پاسخ را وارد کنید:</b>", parse_mode="HTML", reply_markup=h.flow_cancel_keyboard())
    return h.TICKET_ADMIN_REPLY


async def receive_ticket_admin_reply(update, context):
    if await h.redirect_if_menu_button(update, context):
        return ConversationHandler.END
    message = update.effective_message
    if message is None or not (message.text and message.text.strip()):
        return h.TICKET_ADMIN_REPLY
    ticket_id = context.user_data.get("reply_ticket_id")
    ticket = await database.get_ticket(ticket_id)
    if not ticket:
        await message.reply_text("تیکت پیدا نشد.")
        return ConversationHandler.END
    text = message.text.strip()
    await database.add_ticket_message(ticket_id, update.effective_user.id, True, text)
    await database.set_ticket_status(ticket_id, "answered")
    try:
        await context.bot.send_message(chat_id=ticket["user_id"], text=f"📩 پاسخ پشتیبانی (تیکت #{ticket_id}):\n{text}")
    except Exception:
        pass
    await message.reply_text("✅ پاسخ ارسال شد.")
    await database.log_action("admin", update.effective_user.id, f"پاسخ به تیکت #{ticket_id}")
    context.user_data.pop("reply_ticket_id", None)
    return ConversationHandler.END


async def admin_tickets_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "tickets"):
        return
    tickets = await database.list_open_tickets()
    if not tickets:
        await update.message.reply_text("🎧 هیچ تیکت بازی وجود ندارد.")
        return
    keyboard = [[h.ibtn(
        f"🎫 #{t['id']} — کاربر {t['user_id']}", callback_data=f"admin_ticket_view:{t['id']}",
    )] for t in tickets]
    await update.message.reply_text("🎧 تیکت‌های باز:", reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_ticket_view_callback(update, context):
    query = update.callback_query
    await query.answer()
    try:
        if not await h.require_perm(query, query.from_user.id, "tickets"):
            return
        ticket_id = int(query.data.split(":")[1])
        ticket = await database.get_ticket(ticket_id)
        if not ticket:
            await query.message.reply_text("تیکت پیدا نشد.")
            return
        messages = await database.list_ticket_messages(ticket_id)
        lines = [f"🎫 تیکت #{ticket_id} — کاربر {ticket['user_id']} — وضعیت: {ticket['status']}\n"]
        for m in messages:
            sender = "👮 پشتیبانی" if m["is_admin"] else "👤 کاربر"
            lines.append(f"{sender}: {m['message']}")
        keyboard = InlineKeyboardMarkup([
            [h.ibtn("✍️ پاسخ", callback_data=f"ticket_reply_start:{ticket_id}")],
            [h.ibtn("✅ بستن تیکت", callback_data=f"ticket_close:{ticket_id}")],
        ])
        await query.message.reply_text("\n".join(lines), reply_markup=keyboard)
    except Exception:
        logger.exception("خطا در admin_ticket_view_callback")
        try:
            await query.message.reply_text("⚠️ خطایی رخ داد، لطفا دوباره تلاش کنید.")
        except Exception:
            pass
