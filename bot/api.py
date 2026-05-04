#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

try:
    from kwork import Kwork
    KWORK_API_AVAILABLE = True
except ImportError:
    KWORK_API_AVAILABLE = False

import asyncio
import logging
import os
import json
import time
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, Response, Request, HTTPException
from prometheus_client import Counter, Histogram, CONTENT_TYPE_LATEST, generate_latest

from bot.database import _get_db, add_bonus_days

log = logging.getLogger("bot.api")

APP_VERSION = "2.0-sqlite"
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
_ACTIVATION_RETRIES = 3
_ACTIVATION_RETRY_DELAY = 2.0

app = FastAPI(title="Kwork Bot API", version=APP_VERSION)
REQ_TOTAL = Counter("api_requests_total", "Total API requests", ["endpoint"])
PAYMENT_REQUESTS_TOTAL = Counter("payment_requests_total", "Total payment webhooks received")
PAYMENT_PROCESSING_SECONDS = Histogram("payment_processing_seconds", "Time spent processing payment webhooks")


async def _notify_admin(text: str) -> None:
    if not BOT_TOKEN or not ADMIN_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json={"chat_id": ADMIN_CHAT_ID, "text": text})
    except Exception as exc:
        log.error("Failed to notify admin: %s", exc)


async def _ensure_payments_table() -> None:
    db = await _get_db()
    await db.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            amount REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'RUB',
            provider TEXT NOT NULL DEFAULT 'mock',
            status TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            raw TEXT
        )
    """)
    await db.commit()


@app.get("/healthz")
async def healthz() -> Dict[str, Any]:
    REQ_TOTAL.labels("/healthz").inc()
    ok_db = False
    try:
        db = await _get_db()
        await db.execute("SELECT 1")
        ok_db = True
    except Exception:
        ok_db = False
    return {"database": ok_db, "version": APP_VERSION}


@app.get("/metrics")
async def metrics():
    REQ_TOTAL.labels("/metrics").inc()
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/payments/webhook")
@app.post("/webhook")
async def payments_webhook(req: Request):
    _t0 = time.monotonic()
    REQ_TOTAL.labels("/payments/webhook").inc()
    PAYMENT_REQUESTS_TOTAL.inc()
    payload: Dict[str, Any] = await req.json()

    provider = str(payload.get("provider") or "mock")
    status = str(payload.get("status") or "unknown")
    amount_raw = payload.get("amount")
    try:
        amount = float(amount_raw)
    except Exception:
        raise HTTPException(400, f"amount must be numeric, got: {amount_raw!r}")
    currency = str(payload.get("currency") or "RUB")
    telegram_id = int(payload.get("telegram_id") or 0)

    if not telegram_id:
        raise HTTPException(400, "telegram_id is required")
    if amount <= 0:
        raise HTTPException(400, "amount must be > 0")

    try:
        await _ensure_payments_table()
        db = await _get_db()

        import uuid
        payment_id = str(uuid.uuid4())
        raw_json = json.dumps(payload, ensure_ascii=False)

        await db.execute(
            "INSERT INTO payments(id, user_id, amount, currency, provider, status, raw) VALUES(?,?,?,?,?,?,?)",
            (payment_id, telegram_id, amount, currency, provider, status, raw_json),
        )
        await db.commit()

        if status == "paid":
            activation_error: Optional[Exception] = None
            for attempt in range(1, _ACTIVATION_RETRIES + 1):
                try:
                    await add_bonus_days(telegram_id, 30)
                    log.info("Pro activated for user %s (attempt %s)", telegram_id, attempt)
                    activation_error = None
                    break
                except Exception as exc:
                    activation_error = exc
                    log.error("Pro activation error for user %s, attempt %s: %s", telegram_id, attempt, exc)
                    if attempt < _ACTIVATION_RETRIES:
                        await asyncio.sleep(_ACTIVATION_RETRY_DELAY * attempt)

            if activation_error is not None:
                try:
                    fail_id = str(uuid.uuid4())
                    await db.execute(
                        "INSERT INTO payments(id, user_id, amount, currency, provider, status, raw) VALUES(?,?,?,?,?,?,?)",
                        (fail_id, telegram_id, amount, currency, provider, "failed_activation", raw_json),
                    )
                    await db.commit()
                except Exception as exc2:
                    log.error("Failed to record failed_activation for user %s: %s", telegram_id, exc2)

                await _notify_admin(
                    f"❌ ОШИБКА АКТИВАЦИИ PRO\n"
                    f"Пользователь: {telegram_id}\n"
                    f"Сумма: {amount} {currency}\n"
                    f"Ошибка: {activation_error}\n"
                    f"Требуется ручная активация: /gift_pro {telegram_id}"
                )

        PAYMENT_PROCESSING_SECONDS.observe(time.monotonic() - _t0)
        return {"ok": True}

    except HTTPException:
        raise
    except Exception as e:
        log.error("payments_webhook error: %s", repr(e), exc_info=True)
        raise HTTPException(status_code=500, detail="internal_error")


@app.get("/me")
async def me(telegram_id: int):
    REQ_TOTAL.labels("/me").inc()
    try:
        db = await _get_db()
        cursor = await db.execute("SELECT premium_until FROM users WHERE telegram_id=?", (telegram_id,))
        row = await cursor.fetchone()
        if not row:
            return {"found": False, "premium_until": None, "is_pro": False, "version": APP_VERSION}
        pu = row["premium_until"]
        is_pro = False
        if pu:
            try:
                from datetime import datetime, timezone
                is_pro = datetime.fromisoformat(pu).replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
            except Exception:
                pass
        return {"found": True, "premium_until": pu, "is_pro": is_pro, "version": APP_VERSION}
    except Exception as e:
        log.error("/me error: %s", e)
        raise HTTPException(status_code=500, detail="internal_error")
