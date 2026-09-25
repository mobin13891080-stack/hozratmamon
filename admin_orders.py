# -*- coding: utf-8 -*-
# admin_orders.py - مدیریت سفارش‌ها و مدیریت اکانت‌های مرزبان (گرافیکی)

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler

import database
import marzban_api
import handlers as h

PAGE_SIZE = 8
STATUS_ICON = {"pending": "⏳", "approved": "✅", "rejected": "❌", "cancelled": "🚫", "expired": "⌛"}
STATUS_TITLE = {"all": "📚 همه", "pending": "⏳ در انتظار", "approved": "✅ تایید‌شده", "rejected": "❌ رد‌شده", "cancelled": "🚫 لغو‌شده", "expired": "⌛ منقضی‌شده"}


async def _orders_page(offset, status_filter):
    status = None if status_filter == "all" else status_filter
    orders = await database.list_orders(offset=offset, limit=PAGE_SIZE, status=status)
    total = await database.count_orders(status=status)
    try:
        stats = await database.order_stats()
    except Exception:
        stats = {}
    total_all = sum(stats.values()) if stats else total
    keyboard = [[h.ibtn(
        f"{STATUS_ICON.get(o['status'], '')} #{o['id']} — کاربر {o['user_id']} — {o.get('price') or 0:,}ت" + (" 🧾" if o.get("receipt_file_id") else ""),
        callback_data=f"order_view:{o['id']}:{status_filter}",
    )] for o in orders]
    nav_row = []
    if offset > 0:
        nav_row.append(h.ibtn("⬅️", callback_data=f"order_filter:{status_filter}:{max(offset - PAGE_SIZE, 0)}"))
    if offset + PAGE_SIZE < total:
        nav_row.append(h.ibtn("➡️", callback_data=f"order_filter:{status_filter}:{offset + PAGE_SIZE}"))
    if nav_row:
        keyboard.append(nav_row)
    keyboard.append([
        h.ibtn(f"⏳ در انتظار ({stats.get('pending', 0)})", callback_data="order_filter:pending:0"),
        h.ibtn(f"✅ تایید‌شده ({stats.get('approved', 0)})", callback_data="order_filter:approved:0"),
    ])
    keyboard.append([
        h.ibtn(f"❌ رد‌شده ({stats.get('rejected', 0)})", callback_data="order_filter:rejected:0"),
        h.ibtn(f"🚫 لغو‌شده ({stats.get('cancelled', 0)})", callback_data="order_filter:cancelled:0"),
    ])
    keyboard.append([h.ibtn(f"⌛ منقضی‌شده ({stats.get('expired', 0)})", callback_data="order_filter:expired:0")])
    keyboard.append([h.ibtn(f"📚 همه ({total_all})", callback_data="order_filter:all:0")])
    title = STATUS_TITLE.get(status_filter, status_filter)
    text = (
        "🧾 <b>مدیریت سفارش‌ها</b>\n━━━━━━━━━━━━━━━\n"
        f"🔎 فیلتر فعلی: <b>{title}</b>\n"
        f"📊 تعداد در این فیلتر: <b>{total}</b>\n"
        "👇 برای مشاهده جزئیات کامل + رسید پرداخت، روی هرکدام بزنید. (🧾 یعنی رسید عکسی دارد)"
    )
    if not orders:
        text += "\n\nℹ️ هیچ سفارشی با این فیلتر یافت نشد."
    return text, InlineKeyboardMarkup(keyboard)


async def admin_orders_show(update, context):
    if not await h.require_perm_msg(update, update.effective_user.id, "orders"):
        return
    text, keyboard = await _orders_page(0, "pending")
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")


def _order_detail_text(order, package_label, is_trial):
    lines = [
        f"🧾 <b>سفارش #{order['id']}</b>" + (" 🎁" if is_trial else ""),
        f"👤 <b>کاربر:</b> <code>{order['user_id']}</code>",
        f"📦 <b>پلن:</b> {package_label}",
        f"💵 <b>مبلغ:</b> {order.get('price') or 0:,} تومان",
    ]
    if order.get("discount_code"):
        lines.append(f"🎟️ <b>کد تخفیف:</b> {order['discount_code']}")
    lines.append(f"📅 <b>تاریخ ثبت:</b> {order['created_at']}")
    status = order["status"]
    status_line = f"{STATUS_ICON.get(status, '')} <b>وضعیت:</b> {STATUS_TITLE.get(status, status)}"
    lines.append(status_line)
    if status == "rejected" and order.get("reject_reason"):
        lines.append(f"📝 <b>دلیل رد:</b> {order['reject_reason']}")
    if status == "approved" and order.get("marzban_username"):
        lines.append(f"🔧 <b>نام سرویس:</b> <code>{order['marzban_username']}</code>")
    if order.get("receipt_file_id"):
        lines.append("🧾 <b>رسید پرداخت:</b> عکس رسید بالا ⬆️ نمایش داده شده است.")
    else:
        lines.append("🧾 <b>رسید پرداخت:</b> بدون رسید (پرداخت با کیف‌پول یا تست رایگان)")
    return "\n".join(lines)


async def order_action_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "orders"):
        return
    data = query.data

    if data.startswith("order_filter:"):
        _, status_filter, offset = data.split(":")
        await query.answer()
        text, keyboard = await _orders_page(int(offset), status_filter)
        await h.safe_edit_or_send(query, text, reply_markup=keyboard)
        return

    if data.startswith("order_byuser:"):
        _, user_id_str, offset_str = data.split(":")
        user_id = int(user_id_str)
        offset = int(offset_str)
        await query.answer()
        all_orders = await database.list_orders_by_user(user_id)
        total = len(all_orders)
        page_orders = all_orders[offset:offset + PAGE_SIZE]
        keyboard = [[h.ibtn(
            f"{STATUS_ICON.get(o['status'], '')} #{o['id']} — {o.get('price') or 0:,}ت — {o['created_at']}" + (" 🧾" if o.get("receipt_file_id") else ""),
            callback_data=f"order_view:{o['id']}:byuser:{user_id}",
        )] for o in page_orders]
        nav_row = []
        if offset > 0:
            nav_row.append(h.ibtn("⬅️", callback_data=f"order_byuser:{user_id}:{max(offset - PAGE_SIZE, 0)}"))
        if offset + PAGE_SIZE < total:
            nav_row.append(h.ibtn("➡️", callback_data=f"order_byuser:{user_id}:{offset + PAGE_SIZE}"))
        if nav_row:
            keyboard.append(nav_row)
        keyboard.append([h.ibtn("🔙 بازگشت به لیست کل سفارش‌ها", callback_data="order_filter:all:0")])
        text = f"👤 <b>تمام سفارش‌های کاربر</b> <code>{user_id}</code>\n━━━━━━━━━━━━━━━\n📊 تعداد کل: <b>{total}</b>"
        if not page_orders:
            text += "\n\nℹ️ هیچ سفارشی برای این کاربر ثبت نشده."
        await h.safe_edit_or_send(query, text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("order_view:"):
        await query.answer()
        _, order_id, status_filter = data.split(":", 2)
        order = await database.get_order(int(order_id))
        if not order:
            await query.message.reply_text("سفارش پیدا نشد.")
            return
        is_custom = order.get("custom_gb") is not None
        package = None if is_custom else await database.get_package(order["package_id"])
        is_trial = order.get("order_type") == "trial"
        if is_custom:
            gb_val = order["custom_gb"]
            gb_str = str(int(gb_val)) if float(gb_val).is_integer() else str(gb_val)
            package_label = f"🚀 کانفیگ اختصاصی VIP — {gb_str}گیگ/{order['custom_days']}روز"
        elif is_trial:
            package_label = "🎁 تست رایگان"
        else:
            package_label = package['name'] if package else '-'
        text = _order_detail_text(order, package_label, is_trial)
        keyboard = []
        if order["status"] == "pending":
            keyboard.append([
                h.ibtn("✅ تایید", callback_data=f"order_approve:{order['id']}"),
                h.ibtn("❌ رد", callback_data=f"order_reject_choice:{order['id']}"),
            ])
        if order["status"] == "approved":
            if order.get("marzban_username"):
                keyboard.append([h.ibtn("🔧 مدیریت اکانت", callback_data=f"mz_view:{order['marzban_username']}")])
            keyboard.append([h.ibtn("🚫 لغو سفارش", callback_data=f"order_cancel:{order['id']}")])
        keyboard.append([h.ibtn("👤 سایر سفارش‌های این کاربر", callback_data=f"order_byuser:{order['user_id']}:0")])
        if status_filter.startswith("byuser:"):
            back_cb = f"order_byuser:{status_filter.split(':', 1)[1]}:0"
        else:
            back_cb = f"order_filter:{status_filter}:0"
        keyboard.append([h.ibtn("🔙 بازگشت", callback_data=back_cb)])
        reply_markup = InlineKeyboardMarkup(keyboard)
        if order.get("receipt_file_id"):
            try:
                await query.message.reply_photo(photo=order["receipt_file_id"], caption=text, parse_mode="HTML", reply_markup=reply_markup)
                return
            except Exception:
                pass
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
        return

    if data.startswith("order_cancel:"):
        order_id = int(data.split(":")[1])
        order = await database.get_order(order_id)
        removed_note = ""
        if order and order.get("marzban_username"):
            try:
                await marzban_api.remove_user(order["marzban_username"])
                removed_note = " و اکانت مرزبان آن نیز حذف شد"
            except Exception as error:
                removed_note = f" (⚠️ حذف اکانت مرزبان ناموفق بود: {error})"
        await database.update_order(order_id, status="cancelled")
        await database.log_action("admin", query.from_user.id, f"لغو سفارش #{order_id}{removed_note}")
        await query.answer("سفارش لغو شد.")
        await query.message.reply_text(f"✅ سفارش لغو شد{removed_note}.")
        return


# ---------------- مدیریت اکانت مرزبان ----------------

async def _mz_view_text_kb(username):
    try:
        user_data = await marzban_api.get_user(username)
    except Exception as error:
        return f"❌ خطا در دریافت اطلاعات اکانت:\n{error}", None

    used = marzban_api.format_bytes(user_data.get("used_traffic"))
    limit = marzban_api.format_bytes(user_data.get("data_limit")) if user_data.get("data_limit") else "بی محدودیت"
    expire = marzban_api.format_expire(user_data.get("expire"))
    status = user_data.get("status", "-")
    status_icon = "🟢" if status == "active" else "🔴"
    text = (
        f"🔧 اکانت مرزبان: {username}\n━━━━━━━━━━━━━━━\n"
        f"📊 مصرف: {used} / {limit}\n📅 انقضا: {expire}\nوضعیت: {status_icon} {status}"
    )
    keyboard = InlineKeyboardMarkup([
        [h.ibtn("➕ افزایش حجم", callback_data=f"mz_incgb:{username}"),
         h.ibtn("📅 افزایش مدت", callback_data=f"mz_extend:{username}")],
        [h.ibtn("🔄 ریست مصرف", callback_data=f"mz_reset:{username}"),
         h.ibtn("⛔️ فعال/معلق", callback_data=f"mz_toggle:{username}")],
        [h.ibtn("🔗 لینک ساب", callback_data=f"mz_getlink:{username}")],
        [h.ibtn("🔔 اعلان تمدید", callback_data=f"mz_notify_renew:{username}"),
         h.ibtn("🔔 اتمام حجم", callback_data=f"mz_notify_data:{username}")],
        [h.ibtn("🗑 حذف اکانت", callback_data=f"mz_delete:{username}")],
    ])
    return text, keyboard


async def mz_prompt_start(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "orders"):
        return ConversationHandler.END
    action, username = query.data.split(":", 1)
    context.user_data["admin_action"] = f"{action}:{username}"
    await query.answer()
    if action == "mz_incgb":
        await query.message.reply_text(f"💾 <b>مقدار گیگابایتی که می‌خواهید به {username} افزوده شود وارد کنید:</b>", parse_mode="HTML", reply_markup=h.flow_cancel_keyboard())
    else:
        await query.message.reply_text(f"⏳ <b>تعداد روزی که می‌خواهید به {username} افزوده شود وارد کنید:</b>", parse_mode="HTML", reply_markup=h.flow_cancel_keyboard())
    return h.AWAITING_ADMIN_INPUT


async def mz_action_callback(update, context):
    query = update.callback_query
    if not await h.require_perm(query, query.from_user.id, "orders"):
        return
    action, username = query.data.split(":", 1)

    if action == "mz_view":
        await query.answer()
        text, keyboard = await _mz_view_text_kb(username)
        await query.message.reply_text(text, reply_markup=keyboard)
        return

    if action == "mz_reset":
        try:
            await marzban_api.reset_user_data_usage(username)
            await query.answer("مصرف ریست شد.")
            await database.log_action("admin", query.from_user.id, f"ریست مصرف {username}")
        except Exception as error:
            await query.answer(f"خطا: {error}", show_alert=True)
            return
        text, keyboard = await _mz_view_text_kb(username)
        await query.message.reply_text(text, reply_markup=keyboard)
        return

    if action == "mz_delete":
        try:
            await marzban_api.remove_user(username)
            await query.answer("اکانت حذف شد.")
            await database.log_action("admin", query.from_user.id, f"حذف اکانت مرزبان {username}")
            await query.message.reply_text(f"✅ اکانت {username} از پنل حذف شد.")
        except Exception as error:
            await query.answer(f"خطا: {error}", show_alert=True)
        return

    if action == "mz_getlink":
        try:
            user_data = await marzban_api.get_user(username)
            link = marzban_api.extract_subscription_link(user_data)
            await query.answer()
            await query.message.reply_text(f"🔗 لینک اشتراک {username}:\n{link}")
        except Exception as error:
            await query.answer(f"خطا: {error}", show_alert=True)
        return

    if action == "mz_toggle":
        try:
            user_data = await marzban_api.get_user(username)
            new_active = user_data.get("status") != "active"
            await marzban_api.set_user_status(username, new_active)
            await database.log_action("admin", query.from_user.id, f"توگل وضعیت {username}")
            await query.answer("وضعیت عوض شد.")
            text, keyboard = await _mz_view_text_kb(username)
            await query.message.reply_text(text, reply_markup=keyboard)
        except Exception as error:
            await query.answer(f"خطا: {error}", show_alert=True)
        return

    if action in ("mz_notify_renew", "mz_notify_data", "mz_notify_expire"):
        order = await database.get_order_by_marzban_username(username) if hasattr(database, "get_order_by_marzban_username") else None
        target_user = order["user_id"] if order else None
        templates = {
            "mz_notify_renew": "⏰ یادآوری: اعتبار سرویس شما رو به اتمام است. برای تمدید از منوی خرید اقدام کنید.",
            "mz_notify_data": "📊 حجم سرویس شما رو به اتمام است. برای افزایش حجم با پشتیبانی در ارتباط باشید.",
            "mz_notify_expire": "⏳ اعتبار سرویس شما به زودی به پایان می‌رسد.",
        }
        if not target_user:
            await query.answer("کاربر مرتبط پیدا نشد.", show_alert=True)
            return
        try:
            await context.bot.send_message(chat_id=target_user, text=templates[action])
            await query.answer("اعلان ارسال شد.")
        except Exception as error:
            await query.answer(f"خطا: {error}", show_alert=True)
        return
