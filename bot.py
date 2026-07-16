# -*- coding: utf-8 -*-
# bot.py
# نقطه شروع ربات - ثبت همه هندلرها و اجرای ربات با Webhook (برای Render)

import logging
import re
import traceback

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

import config
import database
import handlers
import admin_core
import support_tickets
import admin_users
import admin_orders
import admin_money
import admin_stats_logs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _wire_main_menu_routes():
    """اتصال دکمه‌های منوی اصلی (ReplyKeyboard) به توابع مربوطه"""
    handlers.MAIN_MENU_ROUTES.update({
        handlers.BTN_BUY: handlers.buy_show_packages,
        handlers.BTN_TRIAL: handlers.trial_run,
        handlers.BTN_ACCOUNT: handlers.account_show,
        handlers.BTN_SUPPORT: support_tickets.support_show,
        handlers.BTN_ADMIN_PANEL: admin_core.admin_panel_show,
        handlers.BTN_ADMIN_USERS: admin_users.admin_users_show,
        handlers.BTN_ADMIN_ORDERS: admin_orders.admin_orders_show,
        handlers.BTN_ADMIN_PACKAGES: admin_core.admin_packages_show,
        handlers.BTN_ADMIN_DISCOUNTS: admin_money.admin_discounts_show,
        handlers.BTN_ADMIN_SERVERS: admin_money.admin_servers_show,
        handlers.BTN_ADMIN_FINANCE: admin_money.admin_finance_show,
        handlers.BTN_ADMIN_NOTIFY: admin_money.admin_notify_show,
        handlers.BTN_ADMIN_TICKETS: support_tickets.admin_tickets_show,
        handlers.BTN_ADMIN_STATS: admin_stats_logs.admin_stats_show,
        handlers.BTN_ADMIN_ADMINS: admin_core.admin_admins_show,
        handlers.BTN_ADMIN_LOGS: admin_stats_logs.admin_logs_show,
        handlers.BTN_ADMIN_CARD: admin_core.admin_card_settings_show,
        handlers.BTN_ADMIN_FORCEJOIN: admin_core.admin_forcejoin_settings_show,
        handlers.BTN_ADMIN_TRIAL: admin_core.admin_trial_settings_show,
        handlers.BTN_ADMIN_RULES: admin_core.admin_rules_settings_show,
        handlers.BTN_ADMIN_SUPPORT_SETTINGS: admin_core.admin_support_settings_show,
        handlers.BTN_ADMIN_TURN_OFF: admin_core.admin_toggle_bot,
        handlers.BTN_ADMIN_TURN_ON: admin_core.admin_toggle_bot,
        handlers.BTN_BACK_MAIN: handlers.back_to_main_from_admin,
    })


def build_application() -> Application:
    # تایم‌اوت بالاتر برای جلوگیری از خطای TimedOut هنگام شروع سرد (cold start) روی هاست رایگان
    # connection_pool_size بالا رفته تا وقتی همزمان هزاران پیام می‌رسند، ربات هنگ نکند/مسدود نماند
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
        connection_pool_size=256,
    )
    updates_request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
        connection_pool_size=32,
    )
    application = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .request(request)
        .get_updates_request(updates_request)
        .concurrent_updates(256)
        .build()
    )

    _wire_main_menu_routes()

    # ---- مکالمه خرید سرویس (انتخاب پلن -> تخفیف -> ارسال رسید) ----
    purchase_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(handlers.select_package, pattern=r"^pkg:\d+$")],
        states={
            handlers.CHOOSE_DISCOUNT: [
                CallbackQueryHandler(handlers.discount_have_callback, pattern="^discount_have$"),
                CallbackQueryHandler(handlers.discount_skip_callback, pattern="^discount_skip$"),
            ],
            handlers.DISCOUNT_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.receive_discount_code),
                CommandHandler("skip", handlers.skip_discount_command),
            ],
            handlers.SELECT_RECEIPT: [
                MessageHandler(filters.PHOTO, handlers.receive_receipt),
            ],
        },
        fallbacks=[CommandHandler("cancel", handlers.cancel_purchase)],
        name="purchase_conv",
        persistent=False,
    )

    # ---- مکالمه پنل مدیریت (دریافت مقادیر جدید از ادمین) ----
    admin_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_core.admin_generic_setting_start, pattern=r"^admin_set_\w+$"),
            CallbackQueryHandler(admin_core.admin_pkg_add_start, pattern="^admin_pkg_add$"),
            CallbackQueryHandler(admin_core.admin_pkg_field_start, pattern=r"^admin_pkg_field:\d+:\w+$"),
            CallbackQueryHandler(admin_core.admin_admins_add_start, pattern="^admin_admins_add$"),
            CallbackQueryHandler(admin_users.user_search_start, pattern="^user_search_start$"),
            CallbackQueryHandler(admin_users.broadcast_start, pattern="^broadcast_start$"),
            CallbackQueryHandler(admin_users.notify_pkg_choose, pattern=r"^notify_pkg:\d+$"),
            CallbackQueryHandler(admin_users.user_dm_start, pattern=r"^user_dm_start:-?\d+$"),
            CallbackQueryHandler(admin_orders.mz_prompt_start, pattern=r"^mz_(incgb|extend):"),
            CallbackQueryHandler(admin_money.discount_add_start, pattern="^discount_add_start$"),
            CallbackQueryHandler(admin_money.server_add_start, pattern="^server_add_start$"),
            CallbackQueryHandler(admin_core.admin_forcejoin_add_start, pattern="^admin_forcejoin_add$"),
            MessageHandler(filters.Regex(rf"^{re.escape(handlers.BTN_ADMIN_WELCOME)}$"), admin_core.admin_welcome_start),
            MessageHandler(filters.Regex(rf"^{re.escape(handlers.BTN_ADMIN_WELCOME_PHOTO)}$"), admin_core.admin_welcome_photo_start),
        ],
        states={
            handlers.AWAITING_ADMIN_INPUT: [
                MessageHandler(filters.PHOTO, admin_core.admin_receive_photo_input),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_core.admin_receive_input),
            ],
        },
        fallbacks=[CommandHandler("cancel", admin_core.admin_cancel)],
        name="admin_conv",
        persistent=False,
    )

    # ---- مکالمه تیکت پشتیبانی ----
    ticket_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(support_tickets.ticket_new_start, pattern="^ticket_new$"),
            CallbackQueryHandler(support_tickets.ticket_reply_start, pattern=r"^ticket_reply_start:\d+$"),
        ],
        states={
            handlers.TICKET_USER_MESSAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, support_tickets.receive_ticket_new_message),
            ],
            handlers.TICKET_ADMIN_REPLY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, support_tickets.receive_ticket_admin_reply),
            ],
        },
        fallbacks=[CommandHandler("cancel", support_tickets.ticket_cancel)],
        name="ticket_conv",
        persistent=False,
    )

    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(purchase_conv)
    application.add_handler(admin_conv)
    application.add_handler(ticket_conv)

    application.add_handler(CallbackQueryHandler(handlers.check_join_callback, pattern="^check_join$"))

    # پلن‌ها
    application.add_handler(CallbackQueryHandler(admin_core.admin_packages_callback, pattern="^admin_packages$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_pkg_edit_menu, pattern=r"^admin_pkg_edit:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_pkg_toggle, pattern=r"^admin_pkg_toggle:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_pkg_delete, pattern=r"^admin_pkg_delete:\d+$"))

    # تنطیمات
    application.add_handler(CallbackQueryHandler(admin_core.admin_forcejoin_toggle, pattern="^admin_forcejoin_toggle$"))
    application.add_handler(CallbackQueryHandler(admin_core.forcejoin_remove_callback, pattern=r"^forcejoin_remove:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_trial_toggle, pattern="^admin_trial_toggle$"))

    # ادمین‌ها
    application.add_handler(CallbackQueryHandler(admin_core.admin_role_set_callback, pattern=r"^admin_role_set:-?\d+:\w+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_admins_remove, pattern=r"^admin_admins_remove:-?\d+$"))

    # سفارش‌ها (تایید/رد اولیه خرید + مدیریت کامل سفارش‌ها)
    application.add_handler(CallbackQueryHandler(handlers.order_approve_callback, pattern=r"^order_approve:\d+$"))
    application.add_handler(CallbackQueryHandler(handlers.order_reject_callback, pattern=r"^order_reject:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_orders.order_action_callback, pattern=r"^order_(filter|view|cancel):"))

    # مدیریت مرزبان
    application.add_handler(CallbackQueryHandler(admin_orders.mz_action_callback, pattern=r"^mz_(view|reset|delete|getlink|toggle|notify_renew|notify_data|notify_expire):"))

    # کاربران
    application.add_handler(CallbackQueryHandler(admin_users.user_action_callback, pattern=r"^user_(page|view|toggle_block|delete):"))

    # تخفیف و سرورها
    application.add_handler(CallbackQueryHandler(admin_money.discount_delete_callback, pattern=r"^discount_delete:"))
    application.add_handler(CallbackQueryHandler(admin_money.server_action_callback, pattern=r"^server_(delete|toggle|check):\d+$"))

    # لاگ‌ها
    application.add_handler(CallbackQueryHandler(admin_stats_logs.logs_filter_callback, pattern=r"^logs_filter:\w+$"))

    # تیکت‌ها
    application.add_handler(CallbackQueryHandler(support_tickets.ticket_list_callback, pattern="^ticket_list$"))
    application.add_handler(CallbackQueryHandler(support_tickets.ticket_view_callback, pattern=r"^ticket_view:\d+$"))
    application.add_handler(CallbackQueryHandler(support_tickets.ticket_close_callback, pattern=r"^ticket_close:\d+$"))
    application.add_handler(CallbackQueryHandler(support_tickets.admin_ticket_view_callback, pattern=r"^admin_ticket_view:\d+$"))

    # مسیریاب اصلی منوی متنی ثابت (ReplyKeyboard) - باید بعد از همه مکالمه‌ها ثبت شود
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.main_menu_router))

    application.add_error_handler(_error_handler)

    return application


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("خطای ناهنجار در پردازش اپدیت", exc_info=context.error)
    tb = "".join(traceback.format_exception(None, context.error, context.error.__traceback__))
    try:
        await database.log_action("error", None, f"{context.error}\n{tb[-1500:]}")
    except Exception:
        logger.exception("ثبت لاگ خطا هم ناموفق بود")
    try:
        if config.ADMIN_ID:
            await context.bot.send_message(
                chat_id=config.ADMIN_ID,
                text=f"⚠️ خطایی در ربات رخ داد:\n{str(context.error)[:1000]}",
            )
    except Exception:
        logger.exception("ارسال اعلان خطا به ادمین ناموفق بود")
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ خطایی رخ داد. لطفا دوباره تلاش کنید یا با /start شروع کنید.")
        except Exception:
            pass


async def _post_init(application: Application) -> None:
    await database.init_db()
    logger.info("دیتابیس با موفقیت آماده شد.")


def main() -> None:
    if not config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN تنظیم نشده است! لطفا این متفیر محیطی را در تنظیمات Render اضافه کنید.")

    application = build_application()
    application.post_init = _post_init

    if config.WEBHOOK_URL:
        logger.info(f"ربات در حالت Webhook روی پورت {config.PORT} اجرا می‌شود...")
        application.run_webhook(
            listen="0.0.0.0",
            port=config.PORT,
            url_path="webhook",
            webhook_url=f"{config.WEBHOOK_URL}/webhook",
        )
    else:
        logger.warning("WEBHOOK_URL تنظیم نشده; ربات با حالت polling (فقط برای تست محلی) اجرا می‌شود.")
        application.run_polling()


if __name__ == "__main__":
    main()
