#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from loguru import logger

from bot.database import (
    get_or_create_user,
    get_user_stats,
    update_user_keywords,
    update_user_budget,
    update_user_limit,
)

router = Router(name="settings")

def _parse_args(text: str) -> str:
    return (text or "").split(" ", 1)[1].strip() if " " in (text or "") else ""


@router.message(Command("set_keywords"))
async def cmd_set_keywords(msg: Message) -> None:
    args = _parse_args(msg.text or "")
    if not args:
        await msg.answer("Укажи через пробел: /set_keywords &lt;список фраз через запятую&gt;\nПример: /set_keywords python, aiogram, парсинг")
        return
    await update_user_keywords(msg.from_user.id, args)
    await msg.answer(f"✅ Ключевые фразы обновлены:\n<i>{args}</i>", parse_mode="HTML")

@router.message(Command("set_budget"))
async def cmd_set_budget(msg: Message) -> None:
    args = _parse_args(msg.text or "")
    if not args:
        await msg.answer("Укажи через пробел: /set_budget &lt;сумма в ₽&gt;\nПример: /set_budget 5000")
        return
    try:
        val = int(float(args))
    except Exception:
        await msg.answer("Не понял сумму. Пример: /set_budget 5000")
        return
    await update_user_budget(msg.from_user.id, val)
    await msg.answer(f"✅ Минимальный бюджет обновлён: <b>{val} ₽</b>", parse_mode="HTML")

async def _set_limit_per_day(user_id: int, value: int) -> bool:
    return await update_user_limit(int(user_id), int(value))


@router.message(Command("limit_per_day"))
async def cmd_limit_per_day(msg: Message) -> None:
    args = _parse_args(msg.text or "")
    if not args:
        await msg.answer("Укажи через пробел: /limit_per_day &lt;число в сутки&gt;\nПример: /limit_per_day 50")
        return
    try:
        val = int(args)
        if val < 0:
            raise ValueError
    except ValueError:
        await msg.answer("Некорректное значение. Пример: /limit_per_day 50")
        return
    await _set_limit_per_day(msg.from_user.id, val)
    await msg.answer(f"✅ Лимит карточек в сутки установлен: <b>{val}</b>", parse_mode="HTML")
