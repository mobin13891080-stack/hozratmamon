# -*- coding: utf-8 -*-
# qr_util.py
# ساخت تصویر QR Code از روی لینک Subscription

import io
import qrcode


def make_qr_bytes(data: str) -> io.BytesIO:
    """ساخت تصویر QR و برگرداندن آن به‌صورت بایت برای ارسال در تلگرام"""
    img = qrcode.make(data)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    buffer.name = "subscription_qr.png"
    return buffer
