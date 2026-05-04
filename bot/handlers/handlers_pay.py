#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from aiogram import Router, types, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from loguru import logger

from bot.services.payments import PaymentService

router = Router(name="payments")
_pay = PaymentService()

DEFAULT_PRICE = int(os.getenv("PRO_PRICE_RUB", "199"))

def _parse_args(text: str) -> str:
    return (text or "").split(" ", 1)[1].strip() if " " in (text or "") else ""

@router.message(Command("pay"))
async def cmd_pay(msg: Message) -> None:
    """
    Техническая: /pay <сумма>
    Создаёт mock-инвойс на произвольную сумму (для тестов).
    """
    args = _parse_args(msg.text or "")
    if not args:
        await msg.answer("Укажи через пробел: /pay <сумма в ₽>\nПример: /pay 199")
        return
    try:
        amount = float(args)
        if amount <= 0:
            raise ValueError
    except Exception:
        await msg.answer("Некорректная сумма. Пример: /pay 199")
        return

    pid, url = _pay.create_payment(user_id=msg.from_user.id, amount_rub=amount)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Оплатить {int(amount)} ₽", url=url)]
    ])
    await msg.answer(
        "🧾 Счёт выставлен (mock).\n"
        f"Сумма: <b>{int(amount)} ₽</b>\n"
        "После успешной оплаты провайдер пришлёт вебхук, и PRO активируется автоматически.",
        parse_mode="HTML",
        disable_web_page_preview=False,
        reply_markup=kb,
    )

@router.message(Command("buy_pro"))
async def cmd_buy_pro(msg: Message) -> None:
    """
    Удобная покупка PRO: /buy_pro
    Цена берётся из env PRO_PRICE_RUB (по умолчанию 199 ₽).
    """
    amount = float(DEFAULT_PRICE)
    pid, url = _pay.create_payment(user_id=msg.from_user.id, amount_rub=amount)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Купить PRO за {int(amount)} ₽", url=url)]
    ])
    await msg.answer(
        "⭐ <b>PRO</b>\n"
        "• Больше карточек в сутки\n"
        "• Встроенный AI-черновик и план\n"
        "• Приоритетная скорость\n\n"
        f"Сумма: <b>{int(amount)} ₽</b>\n"
        "После успешной оплаты PRO активируется автоматически (вебхук).",
        parse_mode="HTML",
        disable_web_page_preview=False,
        reply_markup=kb,
    )

# 👇 Колбэк от кнопок «Купить PRO» из /start и /me — вызывает ровно то же, что и /buy_pro
@router.callback_query(F.data == "hint_buy_pro")
async def cb_buy_pro(call: types.CallbackQuery):
    await cmd_buy_pro(call.message)
    await call.answer()
