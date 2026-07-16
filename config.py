# -*- coding: utf-8 -*-
# config.py
# تمام مقادیر حساس از متفیرهای محیطی (Render > Environment) خوانده می‌شوند
# هیچ مقدار واقعی و حساس در این فایل نوشته نمی‌شود

import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

PANEL_URL = os.environ.get("PANEL_URL", "").rstrip("/")
PANEL_USERNAME = os.environ.get("PANEL_USERNAME", "")
PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "")

# لینک توسو باید همان آدرس https:// باشد (نه libsql://)
# چون آدرس libsql:// باعث می‌شود کتابخانه از پروتکل WebSocket استفاده کند که
# روی هاست رندر با خطای WSServerHandshakeError(400) قطع می‌شود.
# آدرس https:// باعث می‌شود کتابخانه از HTTP (پایدارتر و سازگارتر) استفاده کند.
_turso_url = os.environ.get("TURSO_DATABASE_URL", "")
if _turso_url.startswith("libsql://"):
    _turso_url = "https://" + _turso_url[len("libsql://"):]
TURSO_DATABASE_URL = _turso_url
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")

# آدرس عمومی رندر (بعد از اولین دیپلوی به دست می‌آید)، مثلا: https://myvpnbot.onrender.com
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").rstrip("/")

# توکن امنیتی وبهوک - برای اینکه فقط تلگرام بتواند به آدرس وبهوک درخواست بفرستد
# اگر متغیر محیطی WEBHOOK_SECRET تنظیم نشود، از یک مقدار ثابت مشتق از توکن بات ساخته می‌شود
import hashlib as _hashlib
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "") or _hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:32]

# Render خودش مقدار PORT را می‌دهد， لازم نیست دستی تنظیم شود
PORT = int(os.environ.get("PORT", "10000"))
