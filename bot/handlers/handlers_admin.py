#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import datetime as dt
from typing import List, Optional

from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from loguru import logger

import bot.database as _db
from bot.database import get_or_create_user, _get_db

router = Router(name="admin")

_ADMIN_ENV = os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS: List[int] = [int(x) for x in _ADMIN_ENV.split(",") if x.strip().isdigit()]
if not ADMIN_IDS:
    _fallback = os.getenv("ADMIN_CHAT_ID", "0").strip()
    if _fallback.isdigit() and int(_fallback):
        ADMIN_IDS = [int(_fallback)]


def _is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def _parse_args(text: str) -> str:
    return (text or "").split(" ", 1)[1].strip() if " " in (text or "") else ""


@router.message(Command("status"))
async def cmd_status(msg: Message) -> None:
    if not _is_admin(msg.from_user.id):
        return
    import subprocess
    services = [
        ("freelance-radar", "Бот"),
        ("freelance-radar-fl", "FL.ru парсер"),
        ("freelance-radar-kwork", "Kwork парсер"),
        ("freelance-radar-habr", "Habr парсер"),
        ("freelance-radar-freelancehunt", "Freelancehunt"),
    ]
    lines = ["📡 <b>Статус сервисов</b>\n"]
    for svc, name in services:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True, text=True, timeout=5
            )
            status = result.stdout.strip()
        except Exception:
            status = "unknown"
        icon = "✅" if status == "active" else "❌" if status in ("inactive", "failed") else "⚠️"
        lines.append(f"{icon} {name}: <code>{status}</code>")

    db = await _get_db()
    cursor = await db.execute("SELECT COUNT(*) FROM projects_analytics")
    total_projects = (await cursor.fetchone())[0] or 0
    cursor = await db.execute("SELECT COUNT(*) FROM projects_analytics WHERE date(found_at) = date('now')")
    today_projects = (await cursor.fetchone())[0] or 0
    lines.append(f"\n📊 Проектов в базе: <b>{total_projects}</b> (сегодня: <b>{today_projects}</b>)")

    await msg.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("stats"))
async def cmd_stats(msg: Message) -> None:
    if not _is_admin(msg.from_user.id):
        return

    try:
        db = await _get_db()
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        users_total = (await cursor.fetchone())[0] or 0

        cursor = await db.execute("SELECT COUNT(*) FROM users WHERE date(created_at) = date('now')")
        users_today = (await cursor.fetchone())[0] or 0

        cursor = await db.execute("SELECT COUNT(*) FROM users WHERE julianday('now') - julianday(created_at) <= 7")
        users_7d = (await cursor.fetchone())[0] or 0

        cursor = await db.execute("SELECT COUNT(*) FROM users WHERE premium_until > datetime('now')")
        pro_active = (await cursor.fetchone())[0] or 0

        cursor = await db.execute(
            "SELECT COUNT(*) FROM users WHERE trial_until > datetime('now') AND (premium_until IS NULL OR premium_until <= datetime('now'))"
        )
        trial_active = (await cursor.fetchone())[0] or 0

        cursor = await db.execute(
            "SELECT COALESCE(SUM(cards_sent),0) FROM user_daily WHERE stats_date = date('now')"
        )
        cards_today = (await cursor.fetchone())[0] or 0

        cursor = await db.execute(
            "SELECT COUNT(DISTINCT user_id) FROM user_daily WHERE stats_date = date('now') AND cards_sent > 0"
        )
        active_users_today = (await cursor.fetchone())[0] or 0

        cursor = await db.execute("SELECT COUNT(*) FROM users WHERE referred_by IS NOT NULL")
        referrals_total = (await cursor.fetchone())[0] or 0

    except Exception as e:
        logger.error(f"cmd_stats error: {e}")
        await msg.answer("⚠️ Ошибка при сборе статистики")
        return

    conversion = f"{int(pro_active) / max(1, int(users_total)) * 100:.1f}%"

    text = (
        "📊 <b>Статистика</b>\n\n"
        "👥 <b>Пользователи</b>\n"
        f"  Всего: <b>{int(users_total)}</b>\n"
        f"  Новых сегодня: <b>{int(users_today)}</b>\n"
        f"  Новых за 7 дней: <b>{int(users_7d)}</b>\n"
        f"  Trial активных: <b>{int(trial_active)}</b>\n"
        f"  Pro активных: <b>{int(pro_active)}</b>\n"
        f"  Конверсия → Pro: <b>{conversion}</b>\n\n"
        "📬 <b>Активность</b>\n"
        f"  Карточек сегодня: <b>{int(cards_today)}</b>\n"
        f"  Активных юзеров сегодня: <b>{int(active_users_today)}</b>\n\n"
        "🔗 <b>Рефералы</b>\n"
        f"  Привлечено: <b>{int(referrals_total)}</b>\n"
    )
    await msg.answer(text, parse_mode="HTML")


@router.message(Command("users"))
async def cmd_users(msg: Message) -> None:
    if not _is_admin(msg.from_user.id):
        return
    try:
        db = await _get_db()
        cursor = await db.execute(
            "SELECT telegram_id, username, premium_until FROM users ORDER BY created_at DESC LIMIT 20"
        )
        rows = await cursor.fetchall()
        lines = ["👥 <b>Последние пользователи</b> (20):", ""]
        for r in rows:
            premium = r["premium_until"]
            p = f" (PRO до {premium[:10]})" if premium else ""
            username = f"@{r['username']}" if r["username"] else "—"
            lines.append(f"• <code>{r['telegram_id']}</code> {username}{p}")
        await msg.answer("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error(f"cmd_users error: {e}")


@router.message(Command("insights"))
async def cmd_insights(msg: Message) -> None:
    if not _is_admin(msg.from_user.id):
        return
    await msg.answer("📊 Анализирую рынок за 7 дней...")
    from bot.market_analytics import MarketAnalytics
    try:
        report = await MarketAnalytics().weekly_report(msg.from_user.id)
        await msg.answer(report, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        logger.exception("insights failed")
        await msg.answer(f"❌ Ошибка генерации отчёта: {e}")


@router.message(Command("market_report"))
async def cmd_market_report(msg: Message) -> None:
    if not _is_admin(msg.from_user.id):
        return
    try:
        from bot.market_analytics import MarketAnalytics
        await _db.init_db()
        ma = MarketAnalytics()
        report = await ma.weekly_report(msg.from_user.id)
        await msg.answer(report, parse_mode="HTML")
    except Exception as e:
        logger.error(f"market_report error: {e}")
        await msg.answer("Ошибка при генерации отчёта")


@router.message(Command("gift_pro"))
async def cmd_gift_pro(msg: Message) -> None:
    if not _is_admin(msg.from_user.id):
        return
    args = _parse_args(msg.text or "")
    if not args:
        await msg.answer("Использование: /gift_pro <telegram_id> [дней]\nПример: /gift_pro 123456789 14")
        return
    parts = args.split()
    try:
        target_id = int(parts[0])
        days = int(parts[1]) if len(parts) > 1 else 30
        if days <= 0:
            raise ValueError
    except Exception:
        await msg.answer("Неверные аргументы. Пример: /gift_pro 123456789 30")
        return

    try:
        await _db.add_bonus_days(target_id, days)
        await msg.answer(f"🎁 PRO выдан пользователю <code>{target_id}</code> на <b>{days}</b> дн.", parse_mode="HTML")
    except Exception as e:
        logger.error(f"gift_pro error: {e}")
