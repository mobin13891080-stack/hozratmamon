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
_token_lock = asyncio.Lock()

_cached_groups = None
_cached_groups_time = 0
GROUPS_TTL_SECONDS = 60 * 5

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


async def _request(method, url, retries: int = 2, **kwargs):
    """درخواست مقاوم به پنل با retry فقط برای خطاهای گذرا.

    علاوه بر قطع connection، پاسخ‌های 502/503/504 هم transient محسوب می‌شوند؛ این دقیقاً همان
    الگوی 503 است که در snapshot دیتابیس دیده شد. خطاهای منطقی مثل 400/401/403 بی‌جهت retry نمی‌شوند.
    """
    global _client
    last_exc = None
    for attempt in range(retries + 1):
        client = None
        try:
            client = await _get_client()
            response = await client.request(method, url, **kwargs)
            if response.status_code not in (502, 503, 504) or attempt >= retries:
                return response
            await asyncio.sleep(0.25 * (attempt + 1))
            continue
        except asyncio.CancelledError:
            raise
        except Exception as e:
            last_exc = e
            # فقط reference فعلی را کنار می‌گذاریم؛ _get_client در درخواست بعدی یک client تازه می‌سازد.
            _client = None
            if attempt < retries:
                await asyncio.sleep(0.25 * (attempt + 1))
                continue
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("درخواست پنل بدون نتیجه پایان یافت")


async def _log(action, ok, detail=""):
    status = "OK" if ok else "FAIL"
    await database.log_action("marzban_api", None, ("[" + status + "] " + action + " " + detail).strip())


async def get_token():
    global _cached_token, _cached_token_time
    now = time.time()
    if _cached_token and (now - _cached_token_time) < TOKEN_TTL_SECONDS:
        return _cached_token

    # قفل دور گرفتن توکن تا در صورت درخواست همزمان چندین کاربر، دوباره بی‌مورد fetch از پنل انجام نشود
    async with _token_lock:
        now = time.time()
        if _cached_token and (now - _cached_token_time) < TOKEN_TTL_SECONDS:
            return _cached_token
        url = config.PANEL_URL + "/api/admin/token"
        data = {"username": config.PANEL_USERNAME, "password": config.PANEL_PASSWORD}
        response = await _request("POST", url, data=data)
        if response.status_code >= 400:
            await _log("login", False, str(response.status_code))
            raise Exception("خطا در ورود به پنل (" + str(response.status_code) + "): " + response.text)
        token = response.json()["access_token"]
        _cached_token = token
        _cached_token_time = now
        return token


async def get_groups(force_refresh=False):
    """گروه‌های پنل پاسارگارد را برمی‌گرداند (به‌جای inbound های خام مرزبان قدیمی).
    از endpoint سبک /api/groups/simple استفاده می‌کنیم (همونی که دروپوداون خود پنل
    در فرم "افزودن کاربر" ازش استفاده می‌کند)، چون ادمین‌های فرعی (مانند mobinfast)
    معمولا فقط دسترسی "خواندن ساده/read_simple" دارند، نه دسترسی کامل مدیریت
    گروه/read که endpoint قدیمی /api/groups نیاز دارد (همین تفاوت باعث خطای 403 می‌شد)."""
    global _cached_groups, _cached_groups_time
    now = time.time()
    if not force_refresh and _cached_groups and (now - _cached_groups_time) < GROUPS_TTL_SECONDS:
        return _cached_groups
    token = await get_token()
    url = config.PANEL_URL + "/api/groups/simple"
    headers = {"Authorization": "Bearer " + token}
    response = await _request("GET", url, headers=headers, params={"limit": 200})
    if response.status_code >= 400:
        raise Exception("خطا در گرفتن گروه‌های پنل (" + str(response.status_code) + "): " + response.text)
    data = response.json()
    groups = data.get("groups", []) if isinstance(data, dict) else data
    _cached_groups = groups
    _cached_groups_time = now
    return groups


async def get_active_group_ids(selected_ids=None):
    """Group ids used for new users.
    If selected_ids is None/'all'/empty, all accessible operator groups are applied.
    This keeps the default safe behavior: Turkey/Netherlands/France/etc all apply unless admin narrows them.
    """
    groups = await get_groups()
    available = [g["id"] for g in groups]
    if not selected_ids or selected_ids == "all":
        return available
    selected_set = {int(x) for x in selected_ids}
    return [gid for gid in available if int(gid) in selected_set]


def generate_username(prefix="u"):
    suffix = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
    return prefix + suffix


def _normalize_user_expire(user_data):
    """پنل پاسارگارد فیلد expire را گاهی به‌صورت رشته تاریخ ISO برمی‌گرداند
    (مثلا "2026-09-10T20:00:00+03:30") به‌جای عدد timestamp یکپارچه که کد قدیمی (نوشته
    شده برای مرزبان) انتظار دارد. اینجا اون را همیشه به عدد timestamp یکدست
    تبدیل می‌کنیم تا بقیه کد (تمدید، فرمت تاریخ، مقایسه با زمان فعلی) بدون خطای str/int کار کند."""
    if isinstance(user_data, dict) and isinstance(user_data.get("expire"), str) and user_data["expire"]:
        try:
            dt_value = datetime.datetime.fromisoformat(user_data["expire"])
            user_data["expire"] = int(dt_value.timestamp())
        except (ValueError, TypeError):
            pass
    return user_data


async def create_user(gb, days, username=None, note=None, group_ids=None):
    token = await get_token()
    group_ids = await get_active_group_ids(group_ids)
    if not group_ids:
        raise Exception("هیچ گروه فعالی در پنل پیدا نشد؛ باید حداقل یک Group در پنل پاسارگارد فعال باشد.")

    if not username:
        username = generate_username()

    data_limit_bytes = int(gb * GB) if gb and gb > 0 else 0
    expire_timestamp = int(time.time()) + int(days) * 86400 if days and days > 0 else 0

    payload = {
        "username": username,
        "group_ids": group_ids,
        "expire": expire_timestamp,
        "data_limit": data_limit_bytes,
        "data_limit_reset_strategy": "no_reset",
        "status": "active",
    }
    if note:
        payload["note"] = note

    url = config.PANEL_URL + "/api/user"
    headers = {"Authorization": "Bearer " + token}
    response = await _request("POST", url, headers=headers, json=payload, timeout=25)
    if response.status_code >= 400:
        await _log("create_user", False, username + " " + str(response.status_code))
        raise Exception("خطا در ساخت کاربر (" + str(response.status_code) + "): " + response.text)
    await _log("create_user", True, username)
    return _normalize_user_expire(response.json())


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
    response = await _request("GET", url, headers=headers, timeout=15)
    if response.status_code >= 400:
        await _log("get_user", False, username + " " + str(response.status_code))
        raise Exception("خطا در گرفتن اطلاعات کاربر (" + str(response.status_code) + "): " + response.text)
    return _normalize_user_expire(response.json())


async def modify_user(username, **fields):
    token = await get_token()
    url = config.PANEL_URL + "/api/user/" + username
    headers = {"Authorization": "Bearer " + token}
    response = await _request("PUT", url, headers=headers, json=fields, timeout=20)
    if response.status_code >= 400:
        await _log("modify_user", False, username + " " + str(response.status_code))
        raise Exception("خطا در ویرایش کاربر (" + str(response.status_code) + "): " + response.text)
    await _log("modify_user", True, username + " " + str(list(fields.keys())))
    return _normalize_user_expire(response.json())


async def reset_user_data_usage(username):
    token = await get_token()
    url = config.PANEL_URL + "/api/user/" + username + "/reset"
    headers = {"Authorization": "Bearer " + token}
    response = await _request("POST", url, headers=headers, timeout=20)
    if response.status_code >= 400:
        await _log("reset_usage", False, username + " " + str(response.status_code))
        raise Exception("خطا در ریست مصرف (" + str(response.status_code) + "): " + response.text)
    await _log("reset_usage", True, username)
    return _normalize_user_expire(response.json())


async def remove_user(username):
    token = await get_token()
    url = config.PANEL_URL + "/api/user/" + username
    headers = {"Authorization": "Bearer " + token}
    response = await _request("DELETE", url, headers=headers, timeout=20)
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
    response = await _request("POST", url, headers=headers, timeout=20)
    if response.status_code >= 400:
        await _log("revoke_sub", False, username + " " + str(response.status_code))
        raise Exception("خطا در تغییر لینک اشتراک (" + str(response.status_code) + "): " + response.text)
    await _log("revoke_sub", True, username)
    return _normalize_user_expire(response.json())


def format_bytes(num_bytes):
    if not num_bytes:
        return "0 GB"
    return "{:.2f} GB".format(num_bytes / GB)


def format_expire(expire_timestamp):
    if not expire_timestamp:
        return "بی محدودیت"
    dt = datetime.datetime.utcfromtimestamp(expire_timestamp)
    return dt.strftime("%Y-%m-%d")


async def panel_health():
    started = time.time()
    token = await get_token()
    groups = await get_groups(force_refresh=True)
    return {"ok": True, "latency_ms": int((time.time()-started)*1000), "groups_count": len(groups)}


async def get_subscription_configs(subscription_link: str) -> list:
    """Fetch and parse individual configs from the user's subscription link.
    PasarGuard subscription output can be plain text or base64 text depending on
    template/client. This function is read-only and never touches user data.
    """
    if not subscription_link or subscription_link == "-":
        return []
    import base64
    response = await _request("GET", subscription_link, timeout=20)
    if response.status_code >= 400:
        raise Exception("خطا در دریافت خروجی سابسکریپشن (" + str(response.status_code) + "): " + response.text[:300])
    raw = response.text.strip()
    candidates = [raw]
    # Some subscriptions are base64 encoded. Try safe decode too.
    try:
        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.b64decode(padded).decode("utf-8", errors="ignore").strip()
        if decoded and decoded != raw:
            candidates.insert(0, decoded)
    except Exception:
        pass
    prefixes = ("vmess://", "vless://", "trojan://", "ss://", "hysteria2://", "hy2://", "tuic://", "wg://")
    links = []
    for text in candidates:
        for line in text.replace("\r", "\n").split("\n"):
            item = line.strip()
            if item.startswith(prefixes) and item not in links:
                links.append(item)
    return links


async def operator_diagnostics():
    """Read-only PasarGuard compatibility report for reseller/operator accounts."""
    report = {"login": False, "groups_simple": None, "groups_full": None, "templates_simple": None, "system_stats": None, "errors": []}
    token = await get_token()
    report["login"] = True
    headers = {"Authorization": "Bearer " + token}
    checks = [
        ("groups_simple", "/api/groups/simple"),
        ("groups_full", "/api/groups"),
        ("templates_simple", "/api/user_templates/simple"),
        ("system_stats", "/api/system"),
    ]
    for key, path in checks:
        try:
            resp = await _request("GET", config.PANEL_URL + path, headers=headers, timeout=12)
            report[key] = {"status": resp.status_code}
            if resp.status_code < 400:
                try:
                    data = resp.json()
                    report[key]["count"] = len(data) if isinstance(data, list) else (len(data.keys()) if isinstance(data, dict) else 0)
                except Exception:
                    report[key]["count"] = None
            else:
                report[key]["error"] = resp.text[:200]
        except Exception as e:
            report[key] = {"status": "error", "error": str(e)[:200]}
    return report


async def set_user_groups(username, group_ids):
    return await modify_user(username, group_ids=group_ids)


def parse_expire_timestamp(value):
    if not value:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except Exception:
            try:
                return int(datetime.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp())
            except Exception:
                return 0
    return 0
