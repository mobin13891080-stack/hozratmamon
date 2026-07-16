# -*- coding: utf-8 -*-
# marzban_api.py
# تمام توابع مربوط به ارتباط با API پنل مرزبان/ParsPanel

import time
import secrets
import string

import httpx

import config

_cached_token = None
_cached_token_time = 0
TOKEN_TTL_SECONDS = 60 * 30  # نیم ساعت قبل از انقضا دوباره توکن می‌گیریم


async def get_token() -> str:
    """گرفتن توکن دسترسی از پنل (با کش کوتاه‌مدت برای کاهش درخواست‌های تکراری)"""
    global _cached_token, _cached_token_time
    now = time.time()
    if _cached_token and (now - _cached_token_time) < TOKEN_TTL_SECONDS:
        return _cached_token

    url = f"{config.PANEL_URL}/api/admin/token"
    data = {"username": config.PANEL_USERNAME, "password": config.PANEL_PASSWORD}
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, data=data)
        if response.status_code >= 400:
            raise Exception(f"خطا در ورود به پنل ({response.status_code}): {response.text}")
        token = response.json()["access_token"]
        _cached_token = token
        _cached_token_time = now
        return token


async def get_inbounds() -> dict:
    """گرفتن لیست inbound های فعال پنل برای استفاده در ساخت کاربر"""
    token = await get_token()
    url = f"{config.PANEL_URL}/api/inbounds"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(url, headers=headers)
        if response.status_code >= 400:
            raise Exception(f"خطا در گرفتن inbound ها ({response.status_code}): {response.text}")
        return response.json()


def generate_username(prefix: str = "u") -> str:
    """ساخت یک نام کاربری تصادفی و امن برای پنل (چون از مشتری یوزرنیم پرسیده نمی‌شود)"""
    suffix = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
    return f"{prefix}{suffix}"


async def create_user(gb: float, days: int, username: str = None) -> dict:
    """ساخت کاربر جدید در پنل و برگرداندن اطلاعات کامل آن (شامل لینک ساب)"""
    token = await get_token()
    inbounds = await get_inbounds()

    proxies = {}
    selected_inbounds = {}
    for protocol, inbound_list in inbounds.items():
        proxies[protocol] = {}
        selected_inbounds[protocol] = [inbound["tag"] for inbound in inbound_list]

    if not username:
        username = generate_username()

    data_limit_bytes = int(gb * 1024 * 1024 * 1024) if gb and gb > 0 else 0
    expire_timestamp = int(time.time()) + int(days) * 86400 if days and days > 0 else 0

    payload = {
        "username": username,
        "proxies": proxies,
        "inbounds": selected_inbounds,
        "expire": expire_timestamp,
        "data_limit": data_limit_bytes,
        "data_limit_reset_strategy": "no_reset",
        "status": "active",
    }

    url = f"{config.PANEL_URL}/api/user"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            raise Exception(f"خطا در ساخت کاربر ({response.status_code}): {response.text}")
        return response.json()


def extract_subscription_link(user_data: dict) -> str:
    """استخراج لینک Subscription از پاسخ پنل؛ اگر نسبی بود، آدرس پنل به آن اضافه می‌شود"""
    sub_url = user_data.get("subscription_url", "")
    if not sub_url:
        raise Exception("پنل لینک Subscription را برنگرداند: " + str(user_data))
    if sub_url.startswith("http"):
        return sub_url
    return f"{config.PANEL_URL}{sub_url}"
