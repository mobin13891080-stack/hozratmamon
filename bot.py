# -*- coding: utf-8 -*-
# bot.py
# نقطه شروع ربات - ثبت همه هندلرها و اجرای ربات با Webhook (برای Render)

import asyncio
import datetime
import logging
import re
import time
import traceback
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    BaseUpdateProcessor,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest
from telegram.ext import AIORateLimiter

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route
import uvicorn

import config
import database
import marzban_api
import handlers
import admin_core
import support_tickets
import admin_users
import admin_orders
import admin_money
import admin_stats_logs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class _TokenMaskingFilter(logging.Filter):
    """جلوگیری از لیک Bot Token در لاگ‌های httpx که URL های تلگرام را کامل چاپ می‌کنند."""
    def __init__(self):
        super().__init__()
        self._token = None

    def _get_token(self):
        if self._token is None:
            try:
                import config as _cfg
                self._token = _cfg.BOT_TOKEN or ""
            except Exception:
                self._token = ""
        return self._token

    def filter(self, record):
        tok = self._get_token()
        if tok and tok in (record.getMessage()):
            record.msg = str(record.msg).replace(tok, "***TOKEN***")
            record.args = ()
        return True


_token_filter = _TokenMaskingFilter()
logging.getLogger("httpx").addFilter(_token_filter)
logging.getLogger("telegram").addFilter(_token_filter)


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
        handlers.BTN_ADMIN_CUSTOMBUILDER: admin_core.admin_custombuilder_settings_show,
        handlers.BTN_ADMIN_DISCOUNTS: admin_money.admin_discounts_show,
        handlers.BTN_ADMIN_SERVERS: admin_money.admin_servers_show,
        handlers.BTN_ADMIN_PASARGUARD: admin_core.admin_pasarguard_tools_show,
        handlers.BTN_ADMIN_BULK_BONUS: admin_core.admin_bulk_bonus_message_start,
        handlers.BTN_ADMIN_FINANCE: admin_money.admin_finance_show,
        handlers.BTN_ADMIN_NOTIFY: admin_money.admin_notify_show,
        handlers.BTN_ADMIN_TICKETS: support_tickets.admin_tickets_show,
        handlers.BTN_ADMIN_STATS: admin_stats_logs.admin_stats_show,
        handlers.BTN_ADMIN_ADMINS: admin_core.admin_admins_show,
        handlers.BTN_ADMIN_LOGS: admin_stats_logs.admin_logs_show,
        handlers.BTN_ADMIN_CARD: admin_core.admin_card_settings_show,
        handlers.BTN_ADMIN_FORCEJOIN: admin_core.admin_forcejoin_settings_show,
        handlers.BTN_ADMIN_TRIAL: admin_core.admin_trial_settings_show,
         handlers.BTN_ADMIN_REFERRAL: admin_core.admin_referral_settings_show,
        handlers.BTN_ADMIN_GUIDE: admin_core.admin_post_approval_guide_show,
        handlers.BTN_ADMIN_RULES: admin_core.admin_rules_settings_show,
        handlers.BTN_ADMIN_SUPPORT_SETTINGS: admin_core.admin_support_settings_show,
        handlers.BTN_ADMIN_BUY_PROMPT: admin_core.admin_buy_prompt_menu_show,
        handlers.BTN_ADMIN_PKG_TEMPLATE: admin_core.admin_pkg_template_menu_show,
        handlers.BTN_ADMIN_TURN_OFF: admin_core.admin_toggle_bot,
        handlers.BTN_ADMIN_TURN_ON: admin_core.admin_toggle_bot,
        handlers.BTN_ADMIN_NATIONAL_OUTAGE_ON: admin_core.admin_toggle_national_outage,
        handlers.BTN_ADMIN_NATIONAL_OUTAGE_OFF: admin_core.admin_toggle_national_outage,
        handlers.BTN_BACK_MAIN: handlers.back_to_main_from_admin,
    handlers.BTN_CONNECTION_GUIDE: handlers.connection_guide_show,
    handlers.BTN_ADMIN_CONNECTION_GUIDE_SETTINGS: admin_core.admin_connection_guide_settings_show,
    handlers.BTN_ADMIN_SERVICE_TEMPLATE: admin_core.admin_service_template_show,
    handlers.BTN_ADMIN_TRUST_CHANNEL: admin_core.admin_trust_channel_settings_show,
        handlers.BTN_WALLET: handlers.wallet_show,
         handlers.BTN_REFERRAL: handlers.referral_show,
        handlers.BTN_HELP: handlers.help_show,
    })


# کالبک‌دیتایی که از پیش‌بررسی سراسری معاف هستند (دکمه تأیید عضویت/قوانین و دکمه عضو کانال تریگ جوین)
_ACCESS_PRECHECK_EXEMPT_CALLBACKS = {"check_join", "rules_ack"}


async def _global_access_precheck(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """گیت سراسری دسترسی.

    برای callback و پیام‌های عادی قبل از ورود به سرویس‌ها اجرا می‌شود. وضعیت جوین
    همیشه تازه از Telegram گرفته می‌شود؛ بنابراین لفت بعد از تأیید، در اولین درخواست
    بعدی دیگر دسترسی نمی‌دهد. ادمین‌ها از این گیت مستثنا هستند.
    """
    query = getattr(update, "callback_query", None)
    message = getattr(update, "effective_message", None)
    user = getattr(update, "effective_user", None)
    if not user:
        return
    user_id = user.id

    if query and query.data in {"check_join", "rules_ack"}:
        return
    # start مسیر اختصاصی خودش را دارد و نباید اینجا دوباره API تلگرام صدا زده شود.
    if message and (getattr(message, "text", None) or "").strip().startswith("/start"):
        return

    async def _checks():
        is_admin, is_blocked, settings, rules_accepted = await asyncio.gather(
            database.is_admin(user_id),
            database.is_user_blocked(user_id),
            database.get_settings_many(["bot_enabled"], {"bot_enabled": "1"}),
            database.has_accepted_rules(user_id),
        )
        if is_admin:
            return
        if is_blocked:
            if query:
                try: await query.answer("⛔️ دسترسی شما به ربات مسدود شده است.", show_alert=True)
                except Exception: pass
            raise ApplicationHandlerStop
        if (settings.get("bot_enabled") or "1") != "1":
            if query:
                try: await query.answer("🪧 ربات موقتاً خاموش است. لطفاً بعداً دوباره تلاش کنید.", show_alert=True)
                except Exception: pass
            raise ApplicationHandlerStop

        if not rules_accepted:
            await handlers.show_rules_gate(update.effective_chat.id, context)
            raise ApplicationHandlerStop

        missing = await handlers.get_missing_channels(context, user_id)
        if missing:
            if query:
                try: await query.answer("❌ عضویت شما کامل نیست؛ کانال‌های باقی‌مانده را ببینید.", show_alert=True)
                except Exception: pass
                chat_id = query.message.chat_id
            else:
                chat_id = update.effective_chat.id
            await handlers.show_join_gate(chat_id, context, missing)
            raise ApplicationHandlerStop

    try:
        await asyncio.wait_for(_checks(), timeout=5.5)
    except ApplicationHandlerStop:
        raise
    except asyncio.TimeoutError:
        # fail-open در timeout عمداً حذف شد؛ جوین اجباری نباید با کندی شبکه دور زده شود.
        if query:
            try: await query.answer("⏳ بررسی عضویت کمی طول کشید؛ لطفاً دوباره امتحان کن.", show_alert=True)
            except Exception: pass
        raise ApplicationHandlerStop
    except Exception:
        logger.exception("خطا در پیش‌بررسی سراسری دسترسی")
        # خطای ناشناخته هم نباید گیت امنیتی را دور بزند.
        if query:
            try: await query.answer("⚠️ بررسی عضویت ناموفق بود؛ لطفاً دوباره امتحان کن.", show_alert=True)
            except Exception: pass
        raise ApplicationHandlerStop


class _PerChatSerializingUpdateProcessor(BaseUpdateProcessor):
    """دلیل وجود این کلاس - ریشه اصلی و قطعی باگ «فریز شدن دکمه»:
    وقتی .concurrent_updates(N) روی Application فعال است، کتابخانه python-telegram-bot
    آپدیت‌های مختلف را کاملا موازی (به‌صورت Task های جداگانه asyncio) پردازش می‌کند، بدون هیچ
    تضمینی که دو آپدیت مربوط به همان یک کاربر/چت به‌صورت پشت‌سرهم (سریال) پردازش شوند
    (این محدودیت رسمی خود کتابخانه است؛ نگاه کنید به درخواست ویژگی هنوز بازِ شماره ۳۵۰۹ در
    ریپازیتوری python-telegram-bot: «Customizing concurrent handling of updates»).
    نتیجه عملی: اگر یک مشتری روی دکمه‌ی «مثلا ۲۵ گیگ» دوبار پشت‌سرهم بزند (که خیلی طبیعی‌ست،
    مخصوصا با اینترنت ضعیف یا وقتی چیزی فورا روی صفحه دیده نمی‌شود)، یا اگر وبهوک تلگرام به هر
    دلیلی همان آپدیت را دوباره بفرستد، هر دو کلیک ممکن است هم‌زمان و به‌صورت رقابتی روی
    context.user_data و روی وضعیت داخلی ConversationHandler اجرا شوند. این رقابت (race condition)
    می‌تواند باعث شود وضعیت مکالمه برای آن کاربر خراب/نامعتبر شود، و کلیک بعدی او (مثلا برای رفتن
    به مرحله «شماره کارت») دیگر با هیچ هندلری مچ نشود — یعنی دقیقا همان چیزی که مشتری‌ها به‌عنوان
    «دکمه فریز شده، به مرحله شماره کارت نمی‌رود» گزارش می‌دادند. این باگ فقط برای برخی مشتریان و
    فقط گاهی رخ می‌دهد چون کاملا به تایمینگ (timing) بستگی دارد، دقیقا همانطور که کاربر توصیف کرد.

    راه‌حل قطعی: به‌جای اعتماد به رفتار داخلی و تضمین‌نشده‌ی کتابخانه، اینجا خودمان تضمین می‌کنیم
    که آپدیت‌های مربوط به یک chat_id مشخص همیشه دقیقا یکی‌یکی (سریال) پردازش شوند، درحالی‌که
    آپدیت‌های مربوط به چت‌های مختلف همچنان کاملا موازی (تا سقف max_concurrent_updates) پردازش
    می‌شوند. این یعنی هیچ‌وقت، برای هیچ کاربری (گذشته/حال/آینده)، دو آپدیت از یک کاربر با هم
    رقابت نمی‌کنند، پس این کلاس از ریشه امکان بروز این باگ خاص را از بین می‌برد."""

    def __init__(self, max_concurrent_updates: int = 256) -> None:
        super().__init__(max_concurrent_updates=max_concurrent_updates)
        self._chat_locks: dict = {}
        self._chat_locks_guard = asyncio.Lock()

    async def _lock_for(self, key) -> asyncio.Lock:
        async with self._chat_locks_guard:
            lock = self._chat_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._chat_locks[key] = lock
            return lock

    async def do_process_update(self, update, coroutine) -> None:
        key = None
        if isinstance(update, Update):
            if update.effective_chat is not None:
                key = ("chat", update.effective_chat.id)
            elif update.effective_user is not None:
                key = ("user", update.effective_user.id)
        if key is None:
            # آپدیت‌هایی که کاربر/چت مشخصی ندارند (مثلا poll آماری) - نیازی به سریال‌سازی ندارند
            await coroutine
            return
        lock = await self._lock_for(key)
        async with lock:
            await coroutine
        # پاکسازی حافظه‌ی قفل‌های بی‌استفاده تا دیکشناری _chat_locks با رشد تعداد کاربران بی‌نهایت بزرگ نشود (رفع memory leak).
        # اگر همین الان کاربر/چت دیگری منتظر همین قفل نباشد (کاملا ازاد است)، از دیکشنری حذفش می‌کنیم
        # تا دفعه‌ی بعدی همان کاربر/چت یک قفل تازه بسازد (هیچ تاداخلی در سریال‌سازی ایجاد نمی‌شود).
        if not lock.locked():
            async with self._chat_locks_guard:
                current = self._chat_locks.get(key)
                if current is lock and not lock.locked():
                    del self._chat_locks[key]

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass


async def _instant_callback_ack(update, context):
    """Very early best-effort callback acknowledgement.
    This is intentionally lightweight and non-blocking. It prevents Telegram inline
    buttons from spinning while slower database/API handlers continue.
    """
    query = getattr(update, "callback_query", None)
    if not query:
        return
    try:
        await query.answer()
    except Exception:
        pass


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
        .concurrent_updates(_PerChatSerializingUpdateProcessor(max_concurrent_updates=256))
        # AIORateLimiter خودش سرعت ارسال پیام به تلگرام را مدیریت می‌کند تا زیر بار هزاران مشتری همزمان
        # ربات به خطای 429 Too Many Requests از تلگرام نخورد و پیامی گم نشود
        .rate_limiter(AIORateLimiter(overall_max_rate=30, overall_time_period=1, group_max_rate=20, group_time_period=1, max_retries=2))
        .build()
    )

    _wire_main_menu_routes()

    # ---- مکالمه خرید سرویس (انتخاب پلن -> تخفیف -> ارسال رسید) ----
    purchase_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handlers.select_package, pattern=r"^pkg:\d+$"),
            CallbackQueryHandler(handlers.custom_builder_start, pattern="^custom_builder_start$"),
        ],
        states={
            handlers.CUSTOM_GB_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE, handlers.receive_custom_gb),
                CallbackQueryHandler(handlers.flow_cancel_callback, pattern="^flow_cancel$"),
            ],
            handlers.CUSTOM_DAYS_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE, handlers.receive_custom_days),
                CallbackQueryHandler(handlers.flow_cancel_callback, pattern="^flow_cancel$"),
            ],
            handlers.CHOOSE_DISCOUNT: [
                CallbackQueryHandler(handlers.pay_card_callback, pattern="^pay_card$"),
                CallbackQueryHandler(handlers.discount_have_callback, pattern="^discount_have$"),
                CallbackQueryHandler(handlers.pay_wallet_direct_callback, pattern="^pay_wallet_direct$"),
                CallbackQueryHandler(handlers.flow_cancel_callback, pattern="^flow_cancel$"),
            ],
            handlers.DISCOUNT_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE, handlers.receive_discount_code),
                CommandHandler("skip", handlers.skip_discount_command),
                CallbackQueryHandler(handlers.flow_cancel_callback, pattern="^flow_cancel$"),
            ],
            handlers.SELECT_RECEIPT: [
                MessageHandler(filters.PHOTO & ~filters.UpdateType.EDITED_MESSAGE, handlers.receive_receipt),
                CallbackQueryHandler(handlers.pay_with_wallet_callback, pattern="^pay_wallet$"),
                CallbackQueryHandler(handlers.flow_cancel_callback, pattern="^flow_cancel$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", handlers.cancel_purchase),
            CallbackQueryHandler(handlers.flow_cancel_callback, pattern="^flow_cancel$"),
            # رفع باگ قطعی دیگر: بدون این fallback، پیام متنی در مراحلی که فقط منتظر دکمه شیشه‌ای/عکس
            # هستند (CHOOSE_DISCOUNT، SELECT_RECEIPT) هیچ هندلری را مچ نمی‌کرد و state داخلی ConversationHandler
            # برای همیشه «گیر» می‌ماند - باعث می‌شد هر کلیک بعدی روی دکمه «انتخاب پلن» از هیچ هندلری
            # پاسخ نگیرد و دکمه برای همیشه فریز بماند. این fallback همین حفره‌ی خلا را می‌بندد.
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE, handlers.purchase_conv_text_fallback),
        ],
        name="purchase_conv",
        persistent=False,
    )

    # ---- مکالمه پنل مدیریت (دریافت مقادیر جدید از ادمین) ----
    admin_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_core.admin_generic_setting_start, pattern=r"^admin_set_\w+$"),
            CallbackQueryHandler(admin_core.admin_pkg_add_start, pattern="^admin_pkg_add$"),
            CallbackQueryHandler(admin_core.admin_pkg_field_start, pattern=r"^admin_pkg_field:\d+:\w+$"),
            CallbackQueryHandler(admin_core.admin_cat_add_start, pattern="^admin_cat_add$"),
            CallbackQueryHandler(admin_core.admin_cat_rename_prompt, pattern=r"^admin_cat_rename_prompt:\d+$"),
            CallbackQueryHandler(admin_core.admin_admins_add_start, pattern="^admin_admins_add$"),
            CallbackQueryHandler(admin_users.user_search_start, pattern="^user_search_start$"),
            CallbackQueryHandler(admin_users.broadcast_start, pattern="^broadcast_start$"),
            CallbackQueryHandler(admin_users.notify_pkg_choose, pattern=r"^notify_pkg:\d+$"),
            CallbackQueryHandler(admin_users.user_dm_start, pattern=r"^user_dm_start:-?\d+$"),
            CallbackQueryHandler(admin_orders.mz_prompt_start, pattern=r"^mz_(incgb|extend):"),
            CallbackQueryHandler(admin_money.discount_add_start, pattern="^discount_add_start$"),
            CallbackQueryHandler(admin_money.server_add_start, pattern="^server_add_start$"),
            CallbackQueryHandler(admin_core.admin_forcejoin_add_start, pattern="^admin_forcejoin_add$"),
            CallbackQueryHandler(admin_money.admin_wallet_manage_start, pattern="^admin_wallet_manage_start$"),
            CallbackQueryHandler(admin_core.admin_bulk_bonus_start, pattern="^bulk_bonus_start$"),
            CallbackQueryHandler(admin_core.admin_connection_guide_edit_text_start, pattern="^guide_edit_text$"),
            CallbackQueryHandler(admin_core.admin_connection_guide_edit_btn1_start, pattern="^guide_edit_btn1$"),
            CallbackQueryHandler(admin_core.admin_connection_guide_edit_btn2_start, pattern="^guide_edit_btn2$"),
            CallbackQueryHandler(admin_core.admin_connection_guide_edit_btn3_start, pattern="^guide_edit_btn3$"),
            CallbackQueryHandler(admin_core.admin_trust_set_channel_start, pattern="^trust_set_channel$"),
            CallbackQueryHandler(admin_core.admin_trust_buy_btn_label_start, pattern="^trust_buy_btn_label$"),
            CallbackQueryHandler(admin_core.admin_trust_buy_btn_url_start, pattern="^trust_buy_btn_url$"),
            CallbackQueryHandler(admin_money.admin_wallet_credit_callback, pattern=r"^admin_wallet_credit:-?\d+$"),
            CallbackQueryHandler(admin_money.admin_wallet_debit_callback, pattern=r"^admin_wallet_debit:-?\d+$"),
            # نکته حیاتی رفع باگ «قالب دلخواه»: این دکمه حتما باید عین همین admin_conv باشد (نه یک هندلر مستقل خارج مکالمه)،
            # ورنه مقدار برگشتی AWAITING_ADMIN_INPUT که برمی‌گرداند نادیده گرفته می‌شد و پیام بعدی ادمین هرگز دریافت نمی‌شد.
            CallbackQueryHandler(admin_core.admin_pkg_template_custom_start, pattern=r"^admin_pkg_template_custom:(?:\d+|none)$"),
            MessageHandler(filters.Regex(rf"^{re.escape(handlers.BTN_ADMIN_WELCOME)}$"), admin_core.admin_welcome_start),
            MessageHandler(filters.Regex(rf"^{re.escape(handlers.BTN_ADMIN_WELCOME_PHOTO)}$"), admin_core.admin_welcome_photo_start),
            MessageHandler(filters.Regex(rf"^{re.escape(handlers.BTN_ADMIN_WELCOME_GIF)}$"), admin_core.admin_welcome_gif_start),
            MessageHandler(filters.Regex(rf"^{re.escape(handlers.BTN_ADMIN_BULK_BONUS)}$"), admin_core.admin_bulk_bonus_message_start),
        ],
        states={
            handlers.AWAITING_ADMIN_INPUT: [
                MessageHandler(filters.ALL & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE, admin_core.admin_receive_forward_input),
                CallbackQueryHandler(admin_core.admin_cancel_callback, pattern="^flow_cancel$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", admin_core.admin_cancel),
            CallbackQueryHandler(admin_core.admin_cancel_callback, pattern="^flow_cancel$"),
        ],
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
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE, support_tickets.receive_ticket_new_message),
                CallbackQueryHandler(handlers.flow_cancel_callback, pattern="^flow_cancel$"),
            ],
            handlers.TICKET_ADMIN_REPLY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE, support_tickets.receive_ticket_admin_reply),
                CallbackQueryHandler(handlers.flow_cancel_callback, pattern="^flow_cancel$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", support_tickets.ticket_cancel),
            CallbackQueryHandler(handlers.flow_cancel_callback, pattern="^flow_cancel$"),
        ],
        name="ticket_conv",
        persistent=False,
    )

    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CallbackQueryHandler(_instant_callback_ack), group=-2)

    # پیش‌بررسی سراسری (group=-1: قبل از هر هندلر دیگری اجرا می‌شود، برای هر کلیک روی دکمه‌های
    # شیشه‌ای/اینلاین). این باگ را رفع می‌کند که کاربرانی که قبل از فعال‌شدن جوین اجباری با ربات
    # کار را شروع کرده بودند، از قوانین/جوین اجباری معاف می‌ماندند - چون فقط دستور /start و منوی
    # اصلی این وضعیت را بررسی می‌کردند، نه کلیک‌های بعدی روی دکمه‌های شیشه‌ای.
    application.add_handler(CallbackQueryHandler(_global_access_precheck), group=-1)
    # تمام پیام‌های کاربر نیز قبل از ورود به ConversationHandler جوین اجباری را چک می‌کنند.
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE, _global_access_precheck), group=-1)

    application.add_handler(purchase_conv)
    application.add_handler(admin_conv)
    application.add_handler(ticket_conv)
    wallet_topup_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(handlers.wallet_topup_start, pattern="^wallet_topup_start$")],
        states={
            handlers.WALLET_TOPUP_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE, handlers.receive_wallet_topup_amount),
                CallbackQueryHandler(handlers.flow_cancel_callback, pattern="^flow_cancel$"),
            ],
            handlers.WALLET_TOPUP_RECEIPT: [
                MessageHandler(filters.PHOTO & ~filters.UpdateType.EDITED_MESSAGE, handlers.receive_wallet_topup_receipt),
                CallbackQueryHandler(handlers.flow_cancel_callback, pattern="^flow_cancel$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", handlers.wallet_topup_cancel),
            CallbackQueryHandler(handlers.flow_cancel_callback, pattern="^flow_cancel$"),
        ],
        name="wallet_topup_conv",
        persistent=False,
    )
    application.add_handler(wallet_topup_conv)


    application.add_handler(CallbackQueryHandler(handlers.check_join_callback, pattern="^check_join$"))
    application.add_handler(CallbackQueryHandler(handlers.rules_ack_callback, pattern="^rules_ack$"))
    application.add_handler(CallbackQueryHandler(handlers.show_connection_guide_callback, pattern="^show_connection_guide$"))
    application.add_handler(CallbackQueryHandler(handlers.wallet_history_callback, pattern="^wallet_history$"))
    application.add_handler(CallbackQueryHandler(handlers.wallet_topup_approve_callback, pattern=r"^wallet_topup_approve:\d+$"))
    application.add_handler(CallbackQueryHandler(handlers.wallet_topup_reject_callback, pattern=r"^wallet_topup_reject:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_money.admin_wallet_history_callback, pattern=r"^admin_wallet_history:-?\d+$"))
    application.add_handler(CallbackQueryHandler(admin_money.admin_revenue_reset_callback, pattern="^admin_revenue_reset$"))
    application.add_handler(CallbackQueryHandler(handlers.err_back_main_callback, pattern="^err_back_main$"))
    application.add_handler(CallbackQueryHandler(handlers.account_link_callback, pattern=r"^acc_link:\d+$"))
    application.add_handler(CallbackQueryHandler(handlers.account_list_callback, pattern="^acc_back_list$"))
    application.add_handler(CallbackQueryHandler(handlers.account_detail_callback, pattern=r"^acc_detail:\d+$"))
    application.add_handler(CallbackQueryHandler(handlers.account_refresh_callback, pattern=r"^acc_refresh:\d+$"))
    application.add_handler(CallbackQueryHandler(handlers.account_config_callback, pattern=r"^acc_config:\d+$"))
    application.add_handler(CallbackQueryHandler(handlers.account_revoke_callback, pattern=r"^acc_revoke:\d+$"))
    application.add_handler(CallbackQueryHandler(handlers.account_toggle_callback, pattern=r"^acc_toggle:\d+$"))

    # پلن‌ها
    application.add_handler(CallbackQueryHandler(admin_core.admin_packages_callback, pattern="^admin_packages$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_pkg_template_menu_callback, pattern="^admin_pkg_template_menu$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_pkg_template_cat_callback, pattern=r"^admin_pkg_template_cat:(?:\d+|none)$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_pkg_template_set_callback, pattern=r"^admin_pkg_template_set:(?:\d+|none):\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_pkg_template_reset_callback, pattern=r"^admin_pkg_template_reset:(?:\d+|none)$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_buy_prompt_set_callback, pattern=r"^admin_buy_prompt_set:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_pkg_edit_menu, pattern=r"^admin_pkg_edit:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_pkg_toggle, pattern=r"^admin_pkg_toggle:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_pkg_delete, pattern=r"^admin_pkg_delete:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_categories_callback, pattern="^admin_categories$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_cat_delete_callback, pattern=r"^admin_cat_delete:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_cat_move_callback, pattern=r"^admin_cat_move:\d+:(up|down)$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_pkg_setcat_show, pattern=r"^admin_pkg_setcat:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_pkg_setcat_apply, pattern=r"^admin_pkg_setcat_apply:\d+:(none|\d+)$"))
    application.add_handler(CallbackQueryHandler(handlers.buy_category_callback, pattern=r"^buy_cat:(uncat|\d+)$"))
    application.add_handler(CallbackQueryHandler(handlers.buy_categories_back_callback, pattern="^buy_categories_back$"))
    application.add_handler(CallbackQueryHandler(handlers.buy_back_main_callback, pattern="^buy_back_main$"))

    # تنظیمات
    application.add_handler(CallbackQueryHandler(admin_core.admin_forcejoin_toggle, pattern="^admin_forcejoin_toggle$"))
    application.add_handler(CallbackQueryHandler(admin_core.forcejoin_remove_callback, pattern=r"^forcejoin_remove:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_trial_toggle, pattern="^admin_trial_toggle$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_referral_toggle, pattern="^admin_referral_toggle$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_referral_condition_toggle, pattern="^admin_referral_condition_toggle$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_referral_min_plan_show, pattern="^admin_referral_min_plan$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_referral_spend_toggle, pattern="^admin_referral_spend_toggle$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_referral_spend_min_plan_show, pattern="^admin_referral_spend_min_plan$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_referral_spend_min_plan_set, pattern=r"^admin_referral_spend_min_plan_set:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_referral_min_plan_set, pattern=r"^admin_referral_min_plan_set:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_referral_settings_back, pattern="^admin_referral_settings_back$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_referral_penalty_toggle, pattern="^admin_referral_penalty_toggle$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_referral_stats, pattern=r"^admin_referral_stats:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_referral_rows, pattern=r"^admin_referral_rows:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_referral_user, pattern=r"^admin_referral_user:-?\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_referral_user_reset, pattern=r"^admin_referral_user_reset:-?\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_referral_detail, pattern=r"^admin_referral_detail:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_referral_reset, pattern=r"^admin_referral_reset:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_trial_unit_toggle, pattern="^admin_trial_unit_toggle$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_trial_reset_callback, pattern="^admin_trial_reset$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_payment_mode_toggle, pattern="^admin_payment_mode_toggle$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_guide_toggle, pattern="^admin_guide_toggle$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_custombuilder_toggle, pattern="^admin_custombuilder_toggle$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_custombuilder_charge_toggle, pattern="^admin_custombuilder_charge_toggle$"))

    # ادمین‌ها
    application.add_handler(CallbackQueryHandler(admin_core.admin_role_set_callback, pattern=r"^admin_role_set:-?\d+:\w+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_admins_remove, pattern=r"^admin_admins_remove:-?\d+$"))

    # سفارش‌ها (تایید/رد اولیه خرید + مدیریت کامل سفارش‌ها)
    application.add_handler(CallbackQueryHandler(handlers.order_approve_callback, pattern=r"^order_approve:\d+$"))
    application.add_handler(CallbackQueryHandler(handlers.order_reject_choice_callback, pattern=r"^order_reject_choice:\d+$"))
    application.add_handler(CallbackQueryHandler(handlers.order_reject_now_callback, pattern=r"^order_reject_now:\d+$"))

    reject_reason_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handlers.order_reject_reason_start, pattern=r"^order_reject_reason:\d+$"),
        ],
        states={
            handlers.REJECT_REASON_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE, handlers.receive_reject_reason),
                CallbackQueryHandler(handlers.reject_reason_cancel_callback, pattern="^reject_reason_cancel$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", handlers.reject_reason_cancel_callback),
            CallbackQueryHandler(handlers.reject_reason_cancel_callback, pattern="^reject_reason_cancel$"),
        ],
        name="reject_reason_conv",
        persistent=False,
    )
    application.add_handler(reject_reason_conv)
    application.add_handler(CallbackQueryHandler(admin_orders.order_action_callback, pattern=r"^order_(filter|view|cancel):"))

    # مدیریت مرزبان
    application.add_handler(CallbackQueryHandler(admin_orders.mz_action_callback, pattern=r"^mz_(view|reset|delete|getlink|toggle|notify_renew|notify_data|notify_expire):"))

    # کاربران
    application.add_handler(CallbackQueryHandler(admin_users.user_action_callback, pattern=r"^user_(page|view|toggle_block|delete):"))

    # تخفیف و سرورها
    application.add_handler(CallbackQueryHandler(admin_money.discount_delete_callback, pattern=r"^discount_delete:"))
    application.add_handler(CallbackQueryHandler(admin_money.server_action_callback, pattern=r"^server_(delete|toggle|check):\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_pasarguard_diag_callback, pattern="^pg_diag$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_pasarguard_groups_callback, pattern="^pg_groups$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_pg_groups_select_callback, pattern="^pg_groups_select$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_pg_group_toggle_callback, pattern=r"^pg_group_toggle:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_pg_groups_set_callback, pattern="^pg_groups_set:all$"))

    # لاگ‌ها
    application.add_handler(CallbackQueryHandler(admin_stats_logs.logs_filter_callback, pattern=r"^logs_filter:\w+$"))
    application.add_handler(CallbackQueryHandler(admin_stats_logs.logs_clear_callback, pattern=r"^logs_clear:\w+$"))

    # تیکت‌ها
    application.add_handler(CallbackQueryHandler(support_tickets.ticket_list_callback, pattern="^ticket_list$"))
    application.add_handler(CallbackQueryHandler(support_tickets.ticket_view_callback, pattern=r"^ticket_view:\d+$"))
    application.add_handler(CallbackQueryHandler(support_tickets.ticket_close_callback, pattern=r"^ticket_close:\d+$"))
    application.add_handler(CallbackQueryHandler(support_tickets.admin_ticket_view_callback, pattern=r"^admin_ticket_view:\d+$"))

    # مسیریاب اصلی منوی متنی ثابت (ReplyKeyboard) - باید بعد از همه مکالمه‌ها ثبت شوح
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE, handlers.main_menu_router))

    # دکمه شیشه‌ای منقضی/بی‌جواب (مثلا بعد از ری‌استارت رندر، حافظه مکالمه‌های در جریان از بین رفته):
    # اگر کاربری روی یک دکمه قدیمی بزند که دیگه هیچ هندلری مچش نمی‌کند، به جای اینکه دکمه برای همیشه
    # در حال بارگیری بماند و کاربر متوجه نشود چه اتفاقی افتاده، یک پیام راهنما می‌دهیم تا دوباره /start بزند.
    async def _stale_callback_catch_all(update, context):
        query = update.callback_query
        try:
            await query.answer("⚠️ این دکمه منقضی شده (احتمالا ربات بین این لحظه به‌روز شده). لطفا /start را بزنید و دوباره تلاش کنید.", show_alert=True)
        except Exception:
            pass

    # راهنمای کاربران
    application.add_handler(CallbackQueryHandler(admin_core.admin_connection_guide_toggle, pattern="^guide_toggle$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_connection_guide_preview, pattern="^guide_preview$"))
    # قالب سرویس کاربران
    application.add_handler(CallbackQueryHandler(admin_core.admin_service_template_nav, pattern=r"^svc_tpl_nav:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_service_template_preview, pattern=r"^svc_tpl_preview:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_service_template_select, pattern=r"^svc_tpl_select:\d+$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: None, pattern="^noop$"))

    # کانال اعتماد
    application.add_handler(CallbackQueryHandler(admin_core.admin_trust_toggle, pattern="^trust_toggle$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_trust_buy_btn_toggle, pattern="^trust_buy_btn_toggle$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_trust_tpl_nav, pattern=r"^trust_tpl_nav:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_trust_tpl_preview, pattern=r"^trust_tpl_preview:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_trust_tpl_select, pattern=r"^trust_tpl_select:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_core.admin_trust_preview, pattern="^trust_preview$"))
    application.add_handler(CallbackQueryHandler(_stale_callback_catch_all))

    application.add_error_handler(_error_handler)

    if application.job_queue is not None:
        application.job_queue.run_repeating(_cleanup_expired_services, interval=120, first=30)
        application.job_queue.run_repeating(handlers.scan_referral_penalties, interval=30, first=20)
    else:
        logger.warning("JobQueue فعال نیست (پکیج job-queue نصب نشده)؛ حذف خودکار سرویس‌های منقضی فعال نیست.")

    return application


async def _cleanup_expired_services(context: ContextTypes.DEFAULT_TYPE) -> None:
    """بررسی دوره‌ای سرویس‌های ساخته‌شده توسط خود ربات (خریداری‌شده یا تست رایگان) و حذف خودکار
    آن‌هایی که در پنل مرزبان منقضی/تمام‌شده‌اند (هم از دیتابیس وضعیت expired که یعنی حذف از
    «سرویس‌های من») و هم از پنل مرزبان.
    نکته امنیتی مهم: این تابع فقط و فقط روی نام‌کاربری‌هایی کار می‌کند که در جدول orders خود ربات
    ثبت شده‌اند (یعنی سرویس‌هایی که خود ربات ساخته). کاربرانی که ادمین مستقیماً و دستی در پنل
    مرزبان می‌سازد هرگز در این جدول ثبت نمی‌شوند، پس هرگز توسط این تابع بررسی یا دوباره نخواهند شد.

    نکته: اگر حالت اضطراری نت ملی (تنظیم national_outage_mode) فعال باشد، این تابع کاملاً بدون تماس با پنل
    مرزبان برمی‌گردد؛ هیچ سرویسی حذف نمی‌شود تا وقتی ادمین خودش این حالت را خاموش کند."""
    try:
        outage_mode = await database.get_setting("national_outage_mode", "0") == "1"
    except Exception:
        outage_mode = False
    if outage_mode:
        return

    try:
        orders = await database.list_active_bot_orders()
    except Exception:
        logger.exception("خطا در گرفتن لیست سرویس‌های فعال ربات برای بررسی انقضا")
        return

    for order in orders:
        username = order.get("marzban_username")
        if not username:
            continue
        try:
            info = await asyncio.wait_for(marzban_api.get_user(username), timeout=12.0)
        except asyncio.TimeoutError:
            # timeout پنل — نادیده بگیر، دفعه بعد دوباره بررسی می‌شود
            continue
        except Exception as error:
            err_str = str(error)
            if "404" in err_str:
                await database.mark_order_expired(order["id"])
                await database.log_action(
                    "auto_expire", order["user_id"],
                    f"سفارش #{order['id']} ({username}) در پنل مرزبان یافت نشد؛ به عنوان منقضی‌شده علامت خورد.",
                )
            elif "504" in err_str or "502" in err_str or "503" in err_str or "Gateway" in err_str:
                # خطای موقت gateway پنل — لاگ مختصر، بدون HTML صفحه خطا
                await database.log_action("error", None, f"پنل موقتاً در دسترس نیست (gateway error) — بررسی {username} به بعد موکول شد")
            else:
                # خطاهای دیگر: فقط ۲۰۰ کاراکتر اول (جلوگیری از پر شدن لاگ با HTML)
                short_err = err_str[:200]
                await database.log_action("error", None, f"خطا در بررسی وضعیت انقضای {username}: {short_err}")
            continue

        expire_ts = marzban_api.parse_expire_timestamp(info.get("expire")) if hasattr(marzban_api, "parse_expire_timestamp") else (info.get("expire") or 0)
        data_limit = info.get("data_limit") or 0
        used_traffic = info.get("used_traffic") or 0
        now_ts = int(time.time())
        status_value = str(info.get("status") or "").lower()
        time_expired = bool(expire_ts and expire_ts > 0 and expire_ts < now_ts)
        data_exceeded = bool(data_limit and data_limit > 0 and used_traffic >= data_limit)
        # "disabled" به تنهایی کافی نیست - ادمین ممکنه دستی خاموشش کرده باشه
        # فقط "expired" یا "limited" واقعی (که پنل خودش تشخیص داده) یا تموم شدن حجم/زمان
        is_expired = (
            status_value in ("expired", "limited")
            or time_expired
            or data_exceeded
        )
        if not is_expired:
            continue

        is_trial_order = order.get("order_type") == "trial"
        gb_remaining = None
        days_remaining = None
        if data_limit and data_limit > 0:
            remaining_bytes = data_limit - used_traffic
            gb_remaining = round(max(remaining_bytes, 0) / (1024 ** 3), 2)
        if expire_ts and expire_ts > 0:
            days_remaining = round(max(expire_ts - now_ts, 0) / 86400, 1)

        try:
            await marzban_api.remove_user(username)
        except Exception as error:
            if "404" not in str(error):
                await database.log_action("error", None, f"خطا در حذف خودکار سرویس منقضی {username}: {error}")
                continue

        await database.mark_order_expired(order["id"])
        kind_label = "تست رایگان" if is_trial_order else "خریداری‌شده"
        await database.log_action(
            "auto_expire", order["user_id"],
            f"سرویس {kind_label} «{username}» (سفارش #{order['id']}) منقضی شد و به‌صورت خودکار از پنل مرزبان و دیتابیس حذف شد.",
        )
        try:
            user_msg = (
                "🗑 سرویس " + kind_label + " شما منقضی شد و از پنل حذف گردید.\n"
                "👤 نام سرویس: " + str(username) + "\n"
                "اگر نیاز به سرویس جدید دارید، از منوی خرید اقدام کنید. 💜"
            )
            await context.bot.send_message(chat_id=order["user_id"], text=user_msg)
        except Exception as send_error:
            await database.log_action("error", order.get("user_id"), f"ارسال پیام حذف خودکار به کاربر ناموفق بود: {send_error}")
        if not is_trial_order:
            try:
                tehran_now = datetime.datetime.now(ZoneInfo("Asia/Tehran")).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                tehran_now = "-"
            display_name = "-"
            try:
                chat = await context.bot.get_chat(order["user_id"])
                name_parts = [p for p in [getattr(chat, "first_name", None), getattr(chat, "last_name", None)] if p]
                display_name = " ".join(name_parts) if name_parts else (f"@{chat.username}" if getattr(chat, "username", None) else "-")
            except Exception:
                pass
            gb_text = f"{gb_remaining} گیگابایت" if gb_remaining is not None else "نامحدود/نامشخص"
            days_text = f"{days_remaining} روز" if days_remaining is not None else "نامحدود/نامشخص"
            deletion_details = (
                f"🗑 حذف خودکار سرویس خریداری‌شده\n"
                f"👤 کاربر: {display_name} | آیدی عددی: {order['user_id']}\n"
                f"🛡 یوزرنیم مرزبان: {username}\n"
                f"📄 شماره سفارش: #{order['id']}\n"
                f"📊 حجم باقی‌مانده تا لحظه حذف: {gb_text}\n"
                f"⏳ زمان باقی‌مانده تا لحظه حذف: {days_text}\n"
                f"🕒 تاریخ و ساعت حذف (به وقت تهران): {tehran_now}"
            )
            await database.log_action("user_deletion", order["user_id"], deletion_details)
        try:
            await context.bot.send_message(
                chat_id=order["user_id"],
                text=(
                    f"⌛ <b>سرویس {kind_label} شما («{username}») به پایان رسید و به‌صورت خودکار حذف شد.</b>\n"
                    "برای ادامه استفاده، از منوی «🛍️ خرید سرویس» یک پلن جدید تهیه کنید."
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.error and "Query is too old and response timeout expired" in str(context.error):
        # Telegram sometimes sends/keeps expired callback query ids after restarts or double taps.
        # This is not a real bot failure; don't pollute logs or freeze the UI.
        return
    # مهم ترین خط برای رفع قطعی و همیشگی «فریز شدن دکمه»: اگر هر هندلری دیگری در
    # کل ربات (فعلی یا هر هندلری که در آینده نوشته می‌شود) قبل از پاسخ دادن به callback_query
    # (یعنی query.answer()) خطا بخورد، این خط در تلگرام مشتری به‌صورت دکمه شیشه‌ایی که تا
    # ابد در حال «در حال بارگذاری» می‌ماند دیده می‌شود (همان مورد گزارش‌شده توسط مشتریان).
    # این یک شبکه ایمنی سراسری است و برای همه هندلرهای دکمه‌ای، حالا و آینده، کار می‌کند —
    # یعنی حتی اگر یک هندلر تازه توسط تیم اضافه شود و توسطش answer() فراموش شود، دکمه باز هم
    # برای کاربر فریز نمی‌ماند.
    if isinstance(update, Update) and update.callback_query is not None:
        try:
            await update.callback_query.answer()
        except Exception:
            pass
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
    if isinstance(update, Update):
        try:
            await handlers.show_error_with_back(
                update,
                context,
                "⚠️ <b>خطایی رخ داد.</b>\nلطفا دوباره تلاش کنید یا با /start شروع کنید.",
            )
        except Exception:
            try:
                if update.effective_message:
                    await update.effective_message.reply_text("⚠️ خطایی رخ داد. لطفا دوباره تلاش کنید یا با /start شروع کنید.")
            except Exception:
                pass


async def _post_init(application: Application) -> None:
    await database.init_db()
    logger.info("دیتابیس با موفقیت آماده شد.")


async def _run_webhook_server(application: Application) -> None:
    """اجرای ربات با یک وب‌سرور اختصاصی (starlette + uvicorn) به‌جای وب‌سرور داخلی PTB.
    علت: روش قدیمی application.run_webhook() فقط مسیر /webhook را مدیریت می‌کرد و هیچ مسیر دیگری
    را پاسخ نمی‌داد؛ به همین دلیل وقتی UptimeRobot به ادرس اصلی سایت (GET /) سر می‌زد، خطای 404
    می‌گرفت و ربات را "خاموش" تشخیص می‌داد و Render بعد از مدتی بی‌فعالیت ربات را می‌خواباند. اینجا
    هم ادرس "/" و هم "/health" با پاسخ 200 OK راه‌اندازی می‌شوند تا UptimeRobot بتواند ربات را همیشه
    بیدار نگه دارد."""
    webhook_path = "/webhook"

    async def telegram_webhook(request: Request) -> Response:
        secret = config.WEBHOOK_SECRET
        if secret:
            header_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
            if header_token != secret:
                return Response(status_code=401)
        try:
            data = await request.json()
        except Exception:
            return Response(status_code=400)
        update = Update.de_json(data=data, bot=application.bot)
        await application.update_queue.put(update)
        return Response()

    async def health_check(_: Request) -> PlainTextResponse:
        # همین مسیر را در UptimeRobot ثبت کنید (مسیر = https://hozratmamon.onrender.com/ یا .../health)
        return PlainTextResponse("OK")

    starlette_app = Starlette(
        routes=[
            Route(webhook_path, telegram_webhook, methods=["POST"]),
            Route("/", health_check, methods=["GET"]),
            Route("/health", health_check, methods=["GET"]),
        ]
    )
    webserver = uvicorn.Server(
        config=uvicorn.Config(
            app=starlette_app,
            host="0.0.0.0",
            port=config.PORT,
            log_level="info",
            use_colors=False,
        )
    )

    async with application:
        await database.init_db()
        logger.info("دیتابیس با موفقیت آماده شد.")
        await application.bot.set_webhook(
            url=f"{config.WEBHOOK_URL}{webhook_path}",
            secret_token=config.WEBHOOK_SECRET or None,
        )
        await application.start()
        try:
            logger.info(f"ربات در حالت Webhook روی پورت {config.PORT} اجرا می‌شود (با مسیر سلامت برای UptimeRobot روی ادرس اصلی سایت)...")
            await webserver.serve()
        finally:
            await application.stop()


def main() -> None:
    if not config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN تنزیم نشده است! لطفا این متفیر محیطی را در تنظیمات Render اضافه کنید.")

    application = build_application()

    if config.WEBHOOK_URL:
        asyncio.run(_run_webhook_server(application))
    else:
        application.post_init = _post_init
        logger.warning("WEBHOOK_URL تنظیم نشده; ربات با حالت polling (فقط برای تست محلی) اجرا می‌شود.")
        application.run_polling()
if __name__ == "__main__":
    main()
