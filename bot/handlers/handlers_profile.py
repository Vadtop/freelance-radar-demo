# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from aiogram import Router, types, F
from aiogram.filters import Command

from bot.database import (
    get_or_create_user,
    get_user_stats,
    get_user_limit,
    get_daily_cards,
    is_user_premium,
)

router = Router(name="profile")


def _fmt_profile(
    *,
    tg: int,
    keywords: str,
    min_budget: int | float,
    premium: bool,
    limit: int,
    custom_limit: bool,
    today: int,
    stats: dict,
) -> str:
    return (
        "👤 <b>Профиль</b>\n"
        f"ID: <code>{tg}</code>\n"
        f"Ключевые слова: <i>{keywords or 'не заданы'}</i>\n"
        f"Мин. бюджет: <b>{int(min_budget) if min_budget else 0} ₽</b>\n"
        f"Тариф: <b>{'PRO' if premium else 'Базовый'}</b>\n"
        f"Лимит/сутки: <b>{limit}</b> " + ("(свой)" if custom_limit else "(по тарифу)") + "\n"
        f"Сегодня отправлено: <b>{today}/{limit}</b>\n"
        f"Всего карточек: <b>{int(stats.get('cards_sent', 0))}</b>\n"
        f"Черновиков: <b>{int(stats.get('solution_sent', 0))}</b>\n"
        f"Проектов найдено: <b>{int(stats.get('projects_found', 0))}</b>"
    )


async def _send_profile(to: types.Message):
    u = await get_or_create_user(to.chat.id)

    tg = u.get("telegram_id") or to.chat.id
    keywords = (u.get("keywords") or "").strip()
    min_budget = u.get("min_budget") or 0

    premium = await is_user_premium(to.chat.id)

    # базовый лимит из БД/настроек пользователя
    try:
        limit, custom_limit = await get_user_limit(to.chat.id)  # -> (int, bool is_custom)
    except Exception:
        limit, custom_limit = (u.get("limit_per_day") or 50), False

    # если PRO и «свой» лимит не задан — подменяем на тарифный из ENV
    if premium and not custom_limit:
        limit = int(os.getenv("PRO_LIMIT_PER_DAY", "1000"))

    # фактическая отправка за сегодня
    try:
        today = await get_daily_cards(to.chat.id)
    except Exception:
        today = 0

    # прочая статистика
    try:
        stats = await get_user_stats(to.chat.id)
        if not isinstance(stats, dict):
            stats = {}
    except Exception:
        stats = {}

    text = _fmt_profile(
        tg=tg,
        keywords=keywords,
        min_budget=min_budget,
        premium=premium,
        limit=int(limit),
        custom_limit=bool(custom_limit),
        today=int(today),
        stats=stats,
    )

    # скрываем «Черновиков: 0»
    if int(stats.get("solution_sent", 0) or 0) == 0:
        text = "\n".join(line for line in text.splitlines() if not line.startswith("Черновиков:"))

    await to.answer(
        text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup={
            "inline_keyboard": [
                [
                    {"text": "🔑 Ключевые слова", "callback_data": "hint_set_keywords"},
                    {"text": "💰 Мин. бюджет", "callback_data": "hint_set_budget"},
                ],
                [{"text": "❓ Помощь", "callback_data": "hint_help"}],
            ]
        },
    )


@router.message(Command("me", "me_full"))
async def cmd_me(message: types.Message):
    await _send_profile(message)


# Кнопка «👤 Профиль» из /start — открывает профиль сразу
@router.callback_query(F.data == "hint_me")
async def cb_open_profile(call: types.CallbackQuery):
    await _send_profile(call.message)
    await call.answer()
