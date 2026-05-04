#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

try:
    from kwork import Kwork
    KWORK_API_AVAILABLE = True
except ImportError:
    KWORK_API_AVAILABLE = False

from dotenv import load_dotenv
load_dotenv()

import os
import asyncio
import logging
import datetime as dt

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from prometheus_client import start_http_server

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def _load_router(module_path: str, name: str = "router"):
    try:
        mod = __import__(module_path, fromlist=[name])
        return getattr(mod, name)
    except Exception as e:
        log.warning("Skip router %s: %s", module_path, e)
        return None


async def _weekly_report_loop(bot: Bot, admin_chat_id: int) -> None:
    """Каждое воскресенье в 10:00 МСК шлёт отчёт MarketAnalytics."""
    from bot.market_analytics import MarketAnalytics
    from bot.database import init_db
    ma = MarketAnalytics()

    while True:
        now = dt.datetime.now(dt.timezone.utc)
        moscow_tz = dt.timezone(dt.timedelta(hours=3))
        now_msk = now.astimezone(moscow_tz)

        days_until_sunday = (6 - now_msk.weekday()) % 7
        if days_until_sunday == 0 and now_msk.hour >= 10:
            days_until_sunday = 7

        target = now_msk.replace(hour=10, minute=0, second=0, microsecond=0) + dt.timedelta(days=days_until_sunday)
        wait_secs = (target - now_msk).total_seconds()
        if wait_secs > 0:
            log.info("Next market report in %.0f hours", wait_secs / 3600)
            await asyncio.sleep(wait_secs)

        try:
            await init_db()
            report = await ma.weekly_report(admin_chat_id)
            await bot.send_message(admin_chat_id, report, parse_mode="HTML")
            log.info("Weekly market report sent to admin")
        except Exception as e:
            log.error("Weekly report failed: %s", e)

        await asyncio.sleep(3600)


async def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN/BOT_TOKEN is not set")

    from bot.database import init_db
    await init_db()

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    for mp in (
        "bot.handlers.handlers_onboarding",
        "bot.handlers.handlers_start",
        "bot.handlers.handlers_settings",
        "bot.handlers.handlers_profile",
        # "bot.handlers.handlers_pay",
        "bot.handlers.handlers_admin",
        "bot.handlers.handlers_exchanges",
        "bot.handlers.handlers_cards",
        "bot.handlers.handlers_reset",
        "bot.handlers.handlers_user_stats",
    ):
        r = _load_router(mp)
        if r:
            dp.include_router(r)

    reminder_task = None
    try:
        from bot.subscription_reminder import run_reminder_loop
        reminder_task = asyncio.create_task(run_reminder_loop(bot))
        log.info("Subscription reminder task started")
    except Exception as e:
        log.warning("Failed to start subscription reminder: %s", e)

    admin_chat = int(os.getenv("ADMIN_CHAT_ID", "0"))
    market_task = None
    if admin_chat:
        market_task = asyncio.create_task(_weekly_report_loop(bot, admin_chat))
        log.info("Market analytics weekly scheduler started")

    try:
        await dp.start_polling(bot, skip_updates=False)
    finally:
        for t in (reminder_task, market_task):
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
        await bot.session.close()


if __name__ == "__main__":
    METRICS_PORT = int(os.getenv("METRICS_PORT", "8003"))
    METRICS_ADDR = os.getenv("METRICS_ADDR", "0.0.0.0")
    start_http_server(METRICS_PORT, addr=METRICS_ADDR)

    asyncio.run(main())
