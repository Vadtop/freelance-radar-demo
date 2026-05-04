#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Фоновый сервис напоминаний об истечении подписки/trial.
Запускается как asyncio.Task внутри telegram_bot_entrypoint.py.

Логика:
- Каждые 6 часов сканирует пользователей
- За 3 дня до истечения — одно напоминание
- За 1 день до истечения — одно напоминание
- Дата последнего напоминания хранится в user_reminders (без спама)
"""

from __future__ import annotations
import asyncio
import datetime as dt
import logging
from typing import Optional

from aiogram import Bot

from bot.database import (
    get_users_expiring_soon,
    get_reminder_state,
    set_reminder_sent,
)

log = logging.getLogger("subscription_reminder")

_CHECK_INTERVAL_HOURS = 6


def _parse_dt_val(val) -> Optional[dt.datetime]:
    if val is None:
        return None
    if isinstance(val, dt.datetime):
        return val
    if isinstance(val, str):
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return dt.datetime.strptime(val[:26], fmt).replace(tzinfo=dt.timezone.utc) if fmt.endswith("%z") else dt.datetime.strptime(val, fmt).replace(tzinfo=dt.timezone.utc)
            except ValueError:
                continue
    return None


def _effective_expiry(row: dict) -> Optional[dt.datetime]:
    """Возвращает ближайшую дату истечения (Pro или Trial)."""
    pu = _parse_dt_val(row.get("premium_until"))
    tu = _parse_dt_val(row.get("trial_until"))
    dates = [d for d in (pu, tu) if d is not None]
    if not dates:
        return None
    result = max(dates)
    if result.tzinfo is None:
        result = result.replace(tzinfo=dt.timezone.utc)
    return result


def _is_trial_user(row: dict) -> bool:
    """Пользователь на trial (без оплаченного Pro)."""
    pu = _parse_dt_val(row.get("premium_until"))
    tu = _parse_dt_val(row.get("trial_until"))
    if pu is None and tu is not None:
        return True
    if pu and pu.replace(tzinfo=dt.timezone.utc if pu.tzinfo is None else pu.tzinfo) <= dt.datetime.now(dt.timezone.utc):
        return True
    return False


def _reminder_text(days_left: int, is_trial: bool) -> str:
    if is_trial:
        return (
            f"⏰ <b>Твой бесплатный Pro-период заканчивается через {days_left} дн.!</b>\n\n"
            "Чтобы не прерывать мониторинг заказов — перейди на Pro:\n"
            "→ /buy_pro — всего <b>299 ₽/мес</b>\n\n"
            "Это меньше одного заказа, который ты найдёшь с нашей помощью 💪"
        )
    return (
        f"⏰ <b>Твой Pro истекает через {days_left} дн.!</b>\n\n"
        "Продли сейчас чтобы не прерывать мониторинг:\n"
        "→ /buy_pro — <b>299 ₽/мес</b>"
    )


async def _send_safe(bot: Bot, telegram_id: int, text: str) -> bool:
    """Отправляет сообщение, возвращает True при успехе."""
    try:
        await bot.send_message(telegram_id, text, parse_mode="HTML")
        return True
    except Exception as e:
        log.warning("reminder send failed tg_id=%s: %s", telegram_id, e)
        return False


async def _check_and_notify(bot: Bot) -> None:
    """Один прогон: проверяем всех нужных пользователей."""
    now = dt.datetime.now(dt.timezone.utc)

    # Пользователи у которых истекает через 2-4 дня → напоминание «за 3 дня»
    users_3d = await get_users_expiring_soon(days_min=2, days_max=4)
    for row in users_3d:
        user_id     = row["id"]
        telegram_id = row["telegram_id"]
        expiry      = _effective_expiry(row)
        if not expiry:
            continue

        state = await get_reminder_state(user_id)
        # Уже отправляли «за 3 дня» — пропускаем
        if state.get("reminded_3d_at"):
            continue

        days_left = (expiry - now).days + 1
        text = _reminder_text(days_left, _is_trial_user(row))
        if await _send_safe(bot, telegram_id, text):
            await set_reminder_sent(user_id, "3d")
            log.info("3d reminder sent tg_id=%s expires=%s", telegram_id, expiry.date())

    # Пользователи у которых истекает через 0-1 день → напоминание «за 1 день»
    users_1d = await get_users_expiring_soon(days_min=0, days_max=1)
    for row in users_1d:
        user_id     = row["id"]
        telegram_id = row["telegram_id"]
        expiry      = _effective_expiry(row)
        if not expiry:
            continue

        state = await get_reminder_state(user_id)
        # Уже отправляли «за 1 день» — пропускаем
        if state.get("reminded_1d_at"):
            continue

        days_left = max(0, (expiry - now).days + 1)
        text = _reminder_text(days_left if days_left > 0 else 1, _is_trial_user(row))
        if await _send_safe(bot, telegram_id, text):
            await set_reminder_sent(user_id, "1d")
            log.info("1d reminder sent tg_id=%s expires=%s", telegram_id, expiry.date())


async def run_reminder_loop(bot: Bot) -> None:
    """
    Бесконечный цикл напоминаний. Запускать как asyncio.Task.
    Интервал: каждые 6 часов.
    """
    log.info("Subscription reminder started (interval=%dh)", _CHECK_INTERVAL_HOURS)
    # Первый прогон — сразу при старте (через 60 сек чтобы дать боту запуститься)
    await asyncio.sleep(60)

    while True:
        try:
            await _check_and_notify(bot)
        except Exception as e:
            log.exception("reminder loop error: %s", e)

        # Ждём следующий прогон
        await asyncio.sleep(_CHECK_INTERVAL_HOURS * 3600)
