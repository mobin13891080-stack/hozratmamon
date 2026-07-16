# -*- coding: utf-8 -*-
# bot.py
# نقطه شروع ربات - ثبت همه هندلرها و اجرای ربات با Webhook (برای Render)

import logging

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

import config
import database
import handlers

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def build_application() -> Application:
    # تایم‌اوت بالاتر برای جلوگیری از خطای TimedOut هنگام شروع سرد (cold start) روی هاست رایگان
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )
    application = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .request(request)
        .get_updates_request(request)
        .build()
    )

    # ---- مکالمه خرید سرویس (انتخاب پلن -> ارسال رسید) ----
    purchase_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(handlers.select_package, pattern=r"^pkg:\d+$")],
        states={
            handlers.SELECT_RECEIPT: [
                MessageHandler(filters.PHOTO, handlers.receive_receipt),
                CommandHandler("cancel", handlers.cancel_purchase),
            ],
        },
        fallbacks=[CommandHandler("cancel", handlers.cancel_purchase)],
        name="purchase_conv",
        persistent=False,
    )

    # ---- مکالمه پنل مدیریت (دریافت مقادیر جدید از ادمین) ----
    admin_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handlers.admin_pkg_add_start, pattern="^admin_pkg_add$"),
            CallbackQueryHandler(handlers.admin_pkg_field_start, pattern=r"^admin_pkg_field:\d+:\w+$"),
            CallbackQueryHandler(handlers.admin_generic_setting_start, pattern="^admin_set_card_number$"),
            CallbackQueryHandler(handlers.admin_generic_setting_start, pattern="^admin_set_card_holder$"),
            CallbackQueryHandler(handlers.admin_generic_setting_start, pattern="^admin_forcejoin_setchannel$"),
            CallbackQueryHandler(handlers.admin_generic_setting_start, pattern="^admin_trial_setgb$"),
            CallbackQueryHandler(handlers.admin_generic_setting_start, pattern="^admin_trial_setdays$"),
            CallbackQueryHandler(handlers.admin_generic_setting_start, pattern="^admin_welcome$"),
            CallbackQueryHandler(handlers.admin_admins_add_start, pattern="^admin_admins_add$"),
        ],
        states={
            handlers.AWAITING_ADMIN_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.admin_receive_input),
                CommandHandler("cancel", handlers.admin_cancel),
            ],
        },
        fallbacks=[CommandHandler("cancel", handlers.admin_cancel)],
        name="admin_conv",
        persistent=False,
    )

    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(purchase_conv)
    application.add_handler(admin_conv)

    application.add_handler(CallbackQueryHandler(handlers.check_join_callback, pattern="^check_join$"))
    application.add_handler(CallbackQueryHandler(handlers.menu_buy_callback, pattern="^menu_buy$"))
    application.add_handler(CallbackQueryHandler(handlers.menu_trial_callback, pattern="^menu_trial$"))
    application.add_handler(CallbackQueryHandler(handlers.menu_account_callback, pattern="^menu_account$"))
    application.add_handler(CallbackQueryHandler(handlers.menu_admin_callback, pattern="^menu_admin$"))
    application.add_handler(CallbackQueryHandler(handlers.back_main_callback, pattern="^back_main$"))

    application.add_handler(CallbackQueryHandler(handlers.admin_packages_callback, pattern="^admin_packages$"))
    application.add_handler(CallbackQueryHandler(handlers.admin_pkg_edit_menu, pattern=r"^admin_pkg_edit:\d+$"))
    application.add_handler(CallbackQueryHandler(handlers.admin_pkg_toggle, pattern=r"^admin_pkg_toggle:\d+$"))
    application.add_handler(CallbackQueryHandler(handlers.admin_pkg_delete, pattern=r"^admin_pkg_delete:\d+$"))

    application.add_handler(CallbackQueryHandler(handlers.admin_card_callback, pattern="^admin_card$"))
    application.add_handler(CallbackQueryHandler(handlers.admin_forcejoin_callback, pattern="^admin_forcejoin$"))
    application.add_handler(CallbackQueryHandler(handlers.admin_forcejoin_toggle, pattern="^admin_forcejoin_toggle$"))
    application.add_handler(CallbackQueryHandler(handlers.admin_trial_callback, pattern="^admin_trial$"))
    application.add_handler(CallbackQueryHandler(handlers.admin_trial_toggle, pattern="^admin_trial_toggle$"))
    application.add_handler(CallbackQueryHandler(handlers.admin_admins_callback, pattern="^admin_admins$"))
    application.add_handler(CallbackQueryHandler(handlers.admin_admins_remove, pattern=r"^admin_admins_remove:-?\d+$"))
    application.add_handler(CallbackQueryHandler(handlers.admin_stats_callback, pattern="^admin_stats$"))

    application.add_handler(CallbackQueryHandler(handlers.order_approve_callback, pattern=r"^order_approve:\d+$"))
    application.add_handler(CallbackQueryHandler(handlers.order_reject_callback, pattern=r"^order_reject:\d+$"))

    return application


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
        logger.warning("WEBHOOK_URL تنظیم نشده؛ ربات با حالت polling (فقط برای تست محلی) اجرا می‌شود.")
        application.run_polling()


if __name__ == "__main__":
    main()
