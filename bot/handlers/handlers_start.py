# -*- coding: utf-8 -*-
from __future__ import annotations

from aiogram import Router, types, F
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.fsm.context import FSMContext

import datetime as dt
from bot.database import (
    get_or_create_user,
    get_user_by_referral_code,
    add_bonus_days,
    set_referred_by,
    count_referrals,
)

router = Router(name="start")


def _trial_banner(user) -> str:
    """Возвращает строку о trial или пустую строку если trial истёк/нет."""
    trial_until = user.get("trial_until")
    premium_until = user.get("premium_until")
    now = dt.datetime.now(dt.timezone.utc)

    # Уже оплатил Pro — не показываем trial-баннер
    if premium_until:
        if isinstance(premium_until, dt.datetime):
            pu = premium_until if premium_until.tzinfo else premium_until.replace(tzinfo=dt.timezone.utc)
            if pu > now:
                return ""

    if not trial_until:
        return ""
    if isinstance(trial_until, dt.datetime):
        tu = trial_until if trial_until.tzinfo else trial_until.replace(tzinfo=dt.timezone.utc)
        if tu > now:
            days_left = (tu - now).days + 1
            return f"\n\n🎁 <b>Активен Pro Trial: {days_left} дн.</b> — все функции Pro бесплатно!"
    return ""


def _start_text(user) -> str:
    kw = (user.get("keywords") or "").strip() or "не заданы"
    budget = user.get("min_budget") or 0
    return (
        "👋 Привет! Я подбираю свежие проекты с <b>Kwork</b>, <b>FL</b>, <b>Habr Freelance</b> "
        "и <b>Freelancehunt</b> — и присылаю сюда в виде удобных карточек.\n\n"
        "Чтобы задачи были по твоей теме:\n"
        "🔑 задай ключевые фразы → <code>/set_keywords python, aiogram</code>\n"
        "💰 установи минимальный бюджет → <code>/set_budget 5000</code>\n\n"
        "📌 Команды: /me, /help, /exchanges, /test_pitch, /stats_responses\n"
        "Текущие настройки:\n"
        f"— ключи — <i>{kw}</i>\n"
        f"— <b>Мин. бюджет</b> — <b>{budget} ₽</b>"
    )


def _start_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "🔑 Ключевые слова", "callback_data": "hint_set_keywords"},
                {"text": "💰 Мин. бюджет", "callback_data": "hint_set_budget"},
            ],
            [{"text": "👤 Профиль", "callback_data": "hint_me"}],
            [{"text": "🔄 Выбор бирж", "callback_data": "hint_exchanges"}],
            [{"text": "❓ Помощь", "callback_data": "hint_help"}],
        ]
    }


@router.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject, state: FSMContext):
    tg_id = message.from_user.id
    is_new_user = False

    # Проверяем — новый ли пользователь (до get_or_create)
    from bot.database import _get_db
    try:
        db = await _get_db()
        cursor = await db.execute("SELECT id FROM users WHERE telegram_id = ?", (tg_id,))
        existing = await cursor.fetchone()
        is_new_user = existing is None
    except Exception:
        pass

    user = await get_or_create_user(tg_id)

    # Обрабатываем реферальный параметр (только для новых пользователей)
    ref_param = (command.args or "").strip()
    if is_new_user and ref_param.startswith("ref_"):
        ref_code = ref_param[4:]  # убираем "ref_"
        referrer = await get_user_by_referral_code(ref_code)
        if referrer and referrer["telegram_id"] != tg_id:
            await set_referred_by(tg_id, referrer["telegram_id"])
            await add_bonus_days(tg_id, 3)
            await add_bonus_days(referrer["telegram_id"], 7)
            try:
                await message.bot.send_message(
                    referrer["telegram_id"],
                    "🎉 По твоей ссылке зарегистрировался новый пользователь!\n"
                    "<b>+7 дней Pro</b> добавлены к твоей подписке.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            await message.answer(
                "🎁 Ты пришёл по реферальной ссылке — тебе +3 дня к Trial!\n"
                "Всего <b>6 дней Pro бесплатно</b>.",
                parse_mode="HTML",
            )

    # Новый пользователь → онбординг, возвращающийся → стандартный экран
    if is_new_user:
        from bot.handlers.handlers_onboarding import start_onboarding
        await start_onboarding(message, state)
    else:
        user = await get_or_create_user(tg_id)
        await message.answer(
            _start_text(user),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=_start_keyboard(),
        )


@router.message(Command("referral"))
async def cmd_referral(message: types.Message):
    tg_id = message.from_user.id
    user = await get_or_create_user(tg_id)
    ref_code = user.get("referral_code") or ""
    referrals_count = await count_referrals(tg_id)

    bot_info = await message.bot.get_me()
    bot_username = bot_info.username

    if ref_code:
        link = f"https://t.me/{bot_username}?start=ref_{ref_code}"
    else:
        link = f"https://t.me/{bot_username}"

    await message.answer(
        "👥 <b>Реферальная программа</b>\n\n"
        "Пригласи друга — получи <b>+7 дней Pro</b>.\n"
        "Друг получит <b>+3 дня</b> к своему Trial.\n\n"
        f"Твоя ссылка:\n<code>{link}</code>\n\n"
        f"Приглашено: <b>{referrals_count}</b> чел.\n"
        f"Заработано дней: <b>{referrals_count * 7}</b>",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "hint_set_keywords")
async def cb_hint_keywords(call: types.CallbackQuery):
    await call.message.answer(
        "Введи ключевые фразы через запятую:\n<code>/set_keywords python, aiogram</code>",
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data == "hint_set_budget")
async def cb_hint_budget(call: types.CallbackQuery):
    await call.message.answer(
        "Укажи минимальный бюджет в рублях:\n<code>/set_budget 5000</code>",
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data == "hint_exchanges")
async def cb_hint_exchanges(call: types.CallbackQuery):
    await call.message.answer(
        "Управляй биржами через команду:\n<code>/exchanges</code>",
        parse_mode="HTML",
    )
    await call.answer()


# ---------- /help и кнопка «Помощь» ----------
HELP_TEXT = (
    "❓ <b>Помощь</b>\n\n"
    "Я присылаю свежие проекты с <b>FL</b>, <b>Kwork</b>, <b>Habr Freelance</b> и <b>Freelancehunt</b>.\n"
    "Чтобы получать релевантные задачи:\n"
    "• 🔑 задай ключевые слова → <code>/set_keywords python, aiogram</code>\n"
    "• 💰 укажи минимальный бюджет → <code>/set_budget 5000</code>\n"
    "• 🔄 выбери биржи → <code>/exchanges</code>\n\n"
    "👤 Профиль — /me\n"
    "🤖 Тест AI-отклика V2 — /test_pitch\n"
    "📊 Статистика откликов — /stats_responses\n"
)

def _help_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "🔑 Ключевые слова", "callback_data": "hint_set_keywords"},
                {"text": "💰 Мин. бюджет", "callback_data": "hint_set_budget"},
            ],
            [{"text": "👤 Профиль", "callback_data": "hint_me"}],
        ]
    }

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        HELP_TEXT,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=_help_keyboard(),
    )

@router.callback_query(F.data == "hint_help")
async def cb_help(call: types.CallbackQuery):
    await call.message.answer(
        HELP_TEXT,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=_help_keyboard(),
    )
    await call.answer()
