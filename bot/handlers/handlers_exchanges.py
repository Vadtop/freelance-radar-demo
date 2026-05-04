# -*- coding: utf-8 -*-
from __future__ import annotations

from aiogram import Router, types, F
from aiogram.filters import Command

from bot.database import get_or_create_user, set_user_exchanges

router = Router(name="exchanges")

# Метаданные бирж: ключ → отображаемое имя
_EXCHANGES = {
    "kwork":        "Kwork.ru",
    "fl":           "FL.ru",
    "habr":         "Habr Freelance",
    "freelancehunt": "Freelancehunt",
}

# Значения по умолчанию (до миграции / для новых пользователей)
_DEFAULTS = {"kwork": True, "fl": True, "habr": False, "freelancehunt": False}


def _is_enabled(u: dict, key: str) -> bool:
    val = u.get(f"{key}_enabled")
    return val if val is not None else _DEFAULTS[key]


def _exchange_keyboard(u: dict) -> dict:
    def btn(key: str) -> dict:
        icon = "✅" if _is_enabled(u, key) else "❌"
        return {"text": f"{icon} {_EXCHANGES[key]}", "callback_data": f"toggle_exchange:{key}"}

    return {
        "inline_keyboard": [
            [btn("kwork"), btn("fl")],
            [btn("habr"), btn("freelancehunt")],
        ]
    }


@router.message(Command("exchanges"))
async def cmd_exchanges(message: types.Message):
    u = await get_or_create_user(message.from_user.id)
    await message.answer(
        "🔄 <b>Биржи для мониторинга</b>\n\n"
        "Нажми чтобы включить или выключить биржу.\n"
        "✅ — карточки с этой биржи приходят\n"
        "❌ — карточки с этой биржи не приходят",
        parse_mode="HTML",
        reply_markup=_exchange_keyboard(u),
    )


@router.callback_query(F.data.startswith("toggle_exchange:"))
async def cb_toggle_exchange(call: types.CallbackQuery):
    key = call.data.split(":", 1)[1]
    if key not in _EXCHANGES:
        await call.answer("Неизвестная биржа")
        return

    tg_id = call.from_user.id
    u = await get_or_create_user(tg_id)

    new_val = not _is_enabled(u, key)

    await set_user_exchanges(
        tg_id,
        kwork        = new_val if key == "kwork"        else _is_enabled(u, "kwork"),
        fl           = new_val if key == "fl"           else _is_enabled(u, "fl"),
        habr         = new_val if key == "habr"         else _is_enabled(u, "habr"),
        freelancehunt= new_val if key == "freelancehunt" else _is_enabled(u, "freelancehunt"),
    )

    # Обновляем локальный dict для перерисовки клавиатуры
    u[f"{key}_enabled"] = new_val

    state_str = "включена ✅" if new_val else "выключена ❌"
    try:
        await call.message.edit_reply_markup(reply_markup=_exchange_keyboard(u))
    except Exception:
        pass
    await call.answer(f"{_EXCHANGES[key]} {state_str}")
