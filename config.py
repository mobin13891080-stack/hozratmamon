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

# اگر لینک توسو به‌صورت https:// داده شده باشد، خودکار به libsql:// تبدیل می‌شود
_turso_url = os.environ.get("TURSO_DATABASE_URL", "")
if _turso_url.startswith("https://"):
    _turso_url = "libsql://" + _turso_url[len("https://"):]
TURSO_DATABASE_URL = _turso_url
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")

# آدرس عمومی رندر (بعد از اولین دیپلوی به دست می‌آید)، مثلا: https://myvpnbot.onrender.com
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").rstrip("/")

# Render خودش مقدار PORT را می‌دهد， لازم نیست دستی تنظیم شود
PORT = int(os.environ.get("PORT", "10000"))
