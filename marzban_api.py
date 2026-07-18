# -*- coding: utf-8 -*-
# marzban_api.py
# تمام توابع مربوط به ارتباط با API پنل مرزبان/ParsPanel
# هر درخواست مهم در جدول logs دیتاباز ثبت می‌شود (لاگ API مرزبان)

import time
import secrets
import string
import datetime
import asyncio

import httpx

import config
import database

GB = 1024 * 1024 * 1024

_cached_token = None
_cached_token_time = 0
TOKEN_TTL_SECONDS = 60 * 30

_cached_inbounds = None
_cached_inbounds_time = 0
INBOUNDS_TTL_SECONDS = 60 * 5

# یک کلاینت HTTP مشترک و پایدار برای کل عمر برنامه (به‌جای باز و بسته کردن
# یک کانکشن/TLS handshake جدید در هر درخواست) تا تاخیر ارتباط با پنل مرزبان
# به شدت کاهش پیدا کند.
_client = None
_client_lock = asyncio.Lock()


async def _get_client():
    global _client
    if _client is None or _client.is_closed:
        async with _client_lock:
            if _client is None or _client.is_closed:
                _client = httpx.AsyncClient(
                    timeout=httpx.Timeout(20.0, connect=10.0),
                    limits=httpx.Limits(max_keepalive_connections=100, max_connections=200, keepalive_expiry=60.0),
                )
    return _client


async def _log(action, ok, detail=""):
    status = "OK" if ok else "FAIL"
    await database.log_action("marzban_api", None, ("[" + status + "] " + action + " " + detail).strip())


async def get_token():
    global _cached_token, _cached_token_time
    now = time.time()
    if _cached_token and (now - _cached_token_time) < TOKEN_TTL_SECONDS:
        return _cached_token

    url = config.PANEL_URL + "/api/admin/token"
    data = {"username": config.PANEL_USERNAME, "password": config.PANEL_PASSWORD}
    client = await _get_client()
    response = await client.post(url, data=data)
    if response.status_code >= 400:
        await _log("login", False, str(response.status_code))
        raise Exception("خطا در ورود به پنل (" + str(response.status_code) + "): " + response.text)
    token = response.json()["access_token"]
    _cached_token = token
    _cached_token_time = now
    return token


async def get_inbounds(force_refresh=False):
    global _cached_inbounds, _cached_inbounds_time
    now = time.time()
    if not force_refresh and _cached_inbounds and (now - _cached_inbounds_time) < INBOUNDS_TTL_SECONDS:
        return _cached_inbounds
    token = await get_token()
    url = config.PANEL_URL + "/api/inbounds"
    headers = {"Authorization": "Bearer " + token}
    client = await _get_client()
    response = await client.get(url, headers=headers)
    if response.status_code >= 400:
        raise Exception("خطا در گرفتن inbound ها (" + str(response.status_code) + "): " + response.text)
    inbounds = response.json()
    _cached_inbounds = inbounds
    _cached_inbounds_time = now
    return inbounds


def generate_username(prefix="u"):
    suffix = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
    return prefix + suffix


async def create_user(gb, days, username=None, note=None):
    token = await get_token()
    inbounds = await get_inbounds()

    proxies = {}
    selected_inbounds = {}
    for protocol, inbound_list in inbounds.items():
        proxies[protocol] = {}
        selected_inbounds[protocol] = [inbound["tag"] for inbound in inbound_list]

    if not username:
        username = generate_username()

    data_limit_bytes = int(gb * GB) if gb and gb > 0 else 0
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
    if note:
        payload["note"] = note

    url = config.PANEL_URL + "/api/user"
    headers = {"Authorization": "Bearer " + token}
    client = await _get_client()
    try:
        response = await client.post(url, headers=headers, json=payload, timeout=25)
    except Exception:
        # اگر به هر دلیلی کانکشن مشترک قطع شده باشد یک تلاش دوباره با کلاینت تازه انجام می‌شود
        global _client
        _client = None
        client = await _get_client()
        response = await client.post(url, headers=headers, json=payload, timeout=25)
    if response.status_code >= 400:
        await _log("create_user", False, username + " " + str(response.status_code))
        raise Exception("خطا در ساخت کاربر (" + str(response.status_code) + "): " + response.text)
    await _log("create_user", True, username)
    return response.json()


def extract_subscription_link(user_data):
    sub_url = user_data.get("subscription_url", "")
    if not sub_url:
        raise Exception("پنل لینک Subscription را برنگرداند: " + str(user_data))
    if sub_url.startswith("http"):
        return sub_url
    return config.PANEL_URL + sub_url


async def get_user(username):
    token = await get_token()
    url = config.PANEL_URL + "/api/user/" + username
    headers = {"Authorization": "Bearer " + token}
    client = await _get_client()
    response = await client.get(url, headers=headers, timeout=15)
    if response.status_code >= 400:
        await _log("get_user", False, username + " " + str(response.status_code))
        raise Exception("خطا در گرفتن اطلاعات کاربر (" + str(response.status_code) + "): " + response.text)
    return response.json()


async def modify_user(username, **fields):
    token = await get_token()
    url = config.PANEL_URL + "/api/user/" + username
    headers = {"Authorization": "Bearer " + token}
    client = await _get_client()
    response = await client.put(url, headers=headers, json=fields, timeout=20)
    if response.status_code >= 400:
        await _log("modify_user", False, username + " " + str(response.status_code))
        raise Exception("خطا در ویرایش کاربر (" + str(response.status_code) + "): " + response.text)
    await _log("modify_user", True, username + " " + str(list(fields.keys())))
    return response.json()


async def reset_user_data_usage(username):
    token = await get_token()
    url = config.PANEL_URL + "/api/user/" + username + "/reset"
    headers = {"Authorization": "Bearer " + token}
    client = await _get_client()
    response = await client.post(url, headers=headers, timeout=20)
    if response.status_code >= 400:
        await _log("reset_usage", False, username + " " + str(response.status_code))
        raise Exception("خطا در ریست مصرف (" + str(response.status_code) + "): " + response.text)
    await _log("reset_usage", True, username)
    return response.json()


async def remove_user(username):
    token = await get_token()
    url = config.PANEL_URL + "/api/user/" + username
    headers = {"Authorization": "Bearer " + token}
    client = await _get_client()
    response = await client.delete(url, headers=headers, timeout=20)
    if response.status_code >= 400:
        await _log("remove_user", False, username + " " + str(response.status_code))
        raise Exception("خطا در حذف کاربر (" + str(response.status_code) + "): " + response.text)
    await _log("remove_user", True, username)


async def extend_user_expire(username, add_days):
    current = await get_user(username)
    current_expire = current.get("expire") or 0
    base = current_expire if current_expire and current_expire > int(time.time()) else int(time.time())
    new_expire = base + int(add_days) * 86400
    return await modify_user(username, expire=new_expire)


async def increase_user_data(username, add_gb):
    current = await get_user(username)
    current_limit = current.get("data_limit") or 0
    new_limit = current_limit + int(add_gb * GB)
    return await modify_user(username, data_limit=new_limit)


async def set_user_status(username, active):
    return await modify_user(username, status="active" if active else "disabled")


async def revoke_sub(username):
    token = await get_token()
    url = config.PANEL_URL + "/api/user/" + username + "/revoke_sub"
    headers = {"Authorization": "Bearer " + token}
    client = await _get_client()
    response = await client.post(url, headers=headers, timeout=20)
    if response.status_code >= 400:
        await _log("revoke_sub", False, username + " " + str(response.status_code))
        raise Exception("خطا در تغییر لینک اشتراک (" + str(response.status_code) + "): " + response.text)
    await _log("revoke_sub", True, username)
    return response.json()


def format_bytes(num_bytes):
    if not num_bytes:
        return "0 GB"
    return "{:.2f} GB".format(num_bytes / GB)


def format_expire(expire_timestamp):
    if not expire_timestamp:
        return "بی محدودیت"
    dt = datetime.datetime.utcfromtimestamp(expire_timestamp)
    return dt.strftime("%Y-%m-%d")
