# -*- coding: utf-8 -*-
from __future__ import annotations

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.database import update_user_keywords, update_user_budget

router = Router(name="onboarding")


class OnboardingState(StatesGroup):
    ask_keywords = State()
    ask_budget   = State()


def _skip_kb(cb: str) -> dict:
    return {"inline_keyboard": [[{"text": "⏭ Пропустить", "callback_data": cb}]]}


async def start_onboarding(message: types.Message, state: FSMContext) -> None:
    """Точка входа — вызывается из cmd_start для новых пользователей."""
    await state.set_state(OnboardingState.ask_keywords)
    await message.answer(
        "👋 <b>Привет! Давай настроим бота за 2 шага.</b>\n\n"
        "<b>Шаг 1 / 2</b> — ключевые слова\n\n"
        "Напиши темы проектов через запятую — я буду присылать только подходящие.\n"
        "Пример: <code>python, aiogram, парсинг</code>",
        parse_mode="HTML",
        reply_markup=_skip_kb("ob_skip_kw"),
    )


# ── Шаг 1: ключевые слова ───────────────────────────────────────────────

@router.message(OnboardingState.ask_keywords)
async def ob_got_keywords(message: types.Message, state: FSMContext) -> None:
    kw = (message.text or "").strip()
    if kw:
        await update_user_keywords(message.from_user.id, kw)
        await message.answer(f"✅ Сохранено: <i>{kw}</i>", parse_mode="HTML")
    await _ask_budget(message, state)


@router.callback_query(F.data == "ob_skip_kw", OnboardingState.ask_keywords)
async def ob_skip_keywords(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer("Пропущено")
    await _ask_budget(call.message, state)


async def _ask_budget(message: types.Message, state: FSMContext) -> None:
    await state.set_state(OnboardingState.ask_budget)
    await message.answer(
        "<b>Шаг 2 / 2</b> — минимальный бюджет\n\n"
        "Укажи минимальную сумму заказа в рублях — проекты дешевле приходить не будут.\n"
        "Пример: <code>5000</code>  (или <code>0</code> чтобы получать всё)",
        parse_mode="HTML",
        reply_markup=_skip_kb("ob_skip_budget"),
    )


# ── Шаг 2: бюджет ──────────────────────────────────────────────────────

@router.message(OnboardingState.ask_budget)
async def ob_got_budget(message: types.Message, state: FSMContext) -> None:
    txt = (message.text or "").strip()
    try:
        val = max(0, int(float(txt)))
    except Exception:
        await message.answer(
            "Не понял сумму — введи число, например <code>5000</code>, или нажми «Пропустить».",
            parse_mode="HTML",
        )
        return
    if val > 0:
        await update_user_budget(message.from_user.id, val)
    await state.clear()
    await _finish(message)


@router.callback_query(F.data == "ob_skip_budget", OnboardingState.ask_budget)
async def ob_skip_budget(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer("Пропущено")
    await state.clear()
    await _finish(call.message)


# ── Финал ──────────────────────────────────────────────────────────────

async def _finish(message: types.Message) -> None:
    await message.answer(
        "🎉 <b>Готово, мониторинг запущен ✅</b>\n\n"
        "💡 <b>Что ещё можно сделать:</b>\n"
        "• Поменять ключи → /set_keywords\n"
        "• Поменять бюджет → /set_budget\n"
        "• Выбрать биржи → /exchanges",
        parse_mode="HTML",
    )
