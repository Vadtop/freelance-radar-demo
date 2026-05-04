#!/usr/bin/env python3
from __future__ import annotations

import os
import asyncio
import logging

from dotenv import load_dotenv

load_dotenv()

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.settings import DEMO_MODE
from bot.demo_orders import DEMO_ORDERS

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

router = Router()


def _orders_kb() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, order in enumerate(DEMO_ORDERS, 1):
        builder.button(text=f"✉️ Отклик: {order['title'][:30]}", callback_data=f"gen_{i}")
    for i, order in enumerate(DEMO_ORDERS, 1):
        builder.button(text=f"🔍 Pipeline: {order['title'][:25]}", callback_data=f"pipe_{i}")
    builder.adjust(3, 3)
    return builder.as_markup()


@router.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 <b>Freelance Radar — Демо</b>\n\n"
        "Это демо-бот, показывающий AI-генерацию откликов на фриланс-заказы.\n\n"
        "🔍 Multi-step AI pipeline:\n"
        "   ANALYZE → RECALL → ESTIMATE → DRAFT A/B → CRITIQUE\n\n"
        "Нажми <b>/orders</b> чтобы увидеть демо-заказы и кнопки.\n\n"
        "Стек: Python, aiogram 3.x, OpenRouter (DeepSeek/Claude), MCP-tools, "
        "sentence-transformers, aiosqlite",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("orders"))
async def cmd_orders(message: types.Message):
    lines = ["📋 <b>Демо-заказы:</b>\n"]
    for i, order in enumerate(DEMO_ORDERS, 1):
        lines.append(
            f"<b>{i}.</b> {order['title']}\n"
            f"   💰 {order['budget']}  ·  🗂 {order['platform']}\n"
            f"   {order['description']}\n"
        )
    await message.answer(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=_orders_kb(),
    )


@router.callback_query(F.data.startswith("gen_"))
async def cb_gen(callback: types.CallbackQuery):
    idx = int(callback.data.removeprefix("gen_")) - 1
    if idx < 0 or idx >= len(DEMO_ORDERS):
        await callback.answer("Неверный номер", show_alert=True)
        return

    order = DEMO_ORDERS[idx]
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"⏳ Генерирую отклик на: <b>{order['title']}</b>...",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()

    try:
        draft = await _generate_draft(order)
        await callback.message.answer(
            f"✉️ <b>Отклик на:</b> {order['title']}\n\n{draft}",
            parse_mode=ParseMode.HTML,
            reply_markup=_orders_kb(),
        )
    except Exception as e:
        log.exception("Generation failed")
        await callback.message.answer(f"❌ Ошибка генерации: {e}")


@router.callback_query(F.data.startswith("pipe_"))
async def cb_pipe(callback: types.CallbackQuery):
    idx = int(callback.data.removeprefix("pipe_")) - 1
    if idx < 0 or idx >= len(DEMO_ORDERS):
        await callback.answer("Неверный номер", show_alert=True)
        return

    order = DEMO_ORDERS[idx]
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"⏳ Запускаю pipeline на: <b>{order['title']}</b>...",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()

    try:
        result = await _run_pipeline(order)
        await callback.message.answer(
            result,
            parse_mode=ParseMode.HTML,
            reply_markup=_orders_kb(),
        )
    except Exception as e:
        log.exception("Pipeline failed")
        await callback.message.answer(f"❌ Ошибка pipeline: {e}")


@router.message(Command("gen"))
async def cmd_gen(message: types.Message):
    idx = _parse_index(message.text)
    if idx is None:
        await message.answer("Использование: /gen <номер 1-3>")
        return

    order = DEMO_ORDERS[idx]
    await message.answer(
        f"⏳ Генерирую отклик на: <b>{order['title']}</b>...",
        parse_mode=ParseMode.HTML,
    )
    await message.chat.do("typing")

    try:
        draft = await _generate_draft(order)
        await message.answer(
            f"✉️ <b>Отклик на:</b> {order['title']}\n\n{draft}",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        log.exception("Generation failed")
        await message.answer(f"❌ Ошибка генерации: {e}")


@router.message(Command("pipeline"))
async def cmd_pipeline(message: types.Message):
    idx = _parse_index(message.text)
    if idx is None:
        await message.answer("Использование: /pipeline <номер 1-3>")
        return

    order = DEMO_ORDERS[idx]
    await message.answer(
        f"⏳ Запускаю pipeline на: <b>{order['title']}</b>...",
        parse_mode=ParseMode.HTML,
    )
    await message.chat.do("typing")

    try:
        result = await _run_pipeline(order)
        await message.answer(result, parse_mode=ParseMode.HTML)
    except Exception as e:
        log.exception("Pipeline failed")
        await message.answer(f"❌ Ошибка pipeline: {e}")


@router.message(Command("about"))
async def cmd_about(message: types.Message):
    await message.answer(
        "🛠 <b>Freelance Radar — стек и возможности</b>\n\n"
        "<b>AI Pipeline:</b>\n"
        "1. ANALYZE — анализ ТЗ, тип проекта, требования, red flags\n"
        "2. RECALL — поиск похожих кейсов (FAISS/sqlite-vec + sentence-transformers)\n"
        "3. ESTIMATE — декомпозиция → часы → цена\n"
        "4. DRAFT A/B — два варианта отклика (Claude Haiku + DeepSeek)\n"
        "5. CRITIQUE — self-critique, проверка на banned phrases\n\n"
        "<b>Модели:</b> Claude Haiku (draft), DeepSeek (analyze/estimate/critique)\n"
        "<b>Кейс-база:</b> sentence-transformers + FAISS/sqlite-vec\n"
        "<b>Observability:</b> Langfuse tracing, Prometheus metrics\n"
        "<b>Парсеры:</b> FL.ru, Kwork, Habr Freelance, Freelancehunt, Weblancer\n\n"
        "🤖 Agentic режим: LLM сама выбирает какие MCP-tools вызвать",
        parse_mode=ParseMode.HTML,
    )


def _parse_index(text: str) -> int | None:
    parts = text.strip().split()
    if len(parts) < 2:
        return None
    try:
        n = int(parts[1])
        if 1 <= n <= len(DEMO_ORDERS):
            return n - 1
    except ValueError:
        pass
    return None


async def _generate_draft(order: dict) -> str:
    from bot.ai.schemas import AgentContext
    from bot.ai.pipeline import run_pipeline

    ctx = AgentContext(
        title=order["title"],
        description=order["description"],
        price_hint=None,
        source=order.get("platform", "FL.ru"),
    )
    result = await run_pipeline(ctx)
    draft = result.draft_a or result.draft_b or "Не удалось сгенерировать"
    if result.critique:
        draft += f"\n\n📊 <b>Score:</b> {result.critique.score}/10"
    return draft


async def _run_pipeline(order: dict) -> str:
    from bot.ai.schemas import AgentContext
    from bot.ai.pipeline import run_pipeline

    ctx = AgentContext(
        title=order["title"],
        description=order["description"],
        price_hint=None,
        source=order.get("platform", "FL.ru"),
    )
    result = await run_pipeline(ctx)

    lines = [f"🔍 <b>Pipeline: {order['title']}</b>\n"]

    if ctx.analysis:
        lines.append(
            f"\n📐 <b>ANALYZE</b>\n"
            f"Тип: {ctx.analysis.project_type}\n"
            f"Полнота: {ctx.analysis.completeness_score}/10\n"
            f"Сложность: {ctx.analysis.complexity}\n"
            f"Требования: {', '.join(ctx.analysis.key_requirements[:5])}"
        )
        if ctx.analysis.red_flags:
            lines.append(f"⚠️ Red flags: {', '.join(ctx.analysis.red_flags[:3])}")

    if ctx.estimate:
        lines.append(
            f"\n📊 <b>ESTIMATE</b>\n"
            f"Часы: {ctx.estimate.total_hours:.0f}\n"
            f"Цена: {ctx.estimate.suggested_price_min:.0f}–{ctx.estimate.suggested_price_max:.0f} ₽\n"
            f"Срок: {ctx.estimate.suggested_days} дн."
        )

    if ctx.past_orders:
        cases = "\n".join(f"  — {c.title} ({c.similarity:.0%})" for c in ctx.past_orders[:3])
        lines.append(f"\n🧠 <b>RECALL</b>\n{cases}")

    if result.draft_a:
        lines.append(f"\n✉️ <b>DRAFT A</b>\n{result.draft_a[:1500]}")
    if result.draft_b:
        lines.append(f"\n✉️ <b>DRAFT B</b>\n{result.draft_b[:800]}")

    if result.critique:
        lines.append(
            f"\n🔎 <b>CRITIQUE</b>\n"
            f"Score: {result.critique.score}/10\n"
            f"Rewrite: {'да' if result.critique.should_rewrite else 'нет'}"
        )
        if result.critique.issues:
            lines.append(f"Issues: {', '.join(result.critique.issues[:3])}")

    lines.append(f"\n💰 Tokens: {result.total_tokens} · ${result.total_cost_usd:.4f}")

    return "\n".join(lines)


async def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN/BOT_TOKEN is not set")

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    log.info("Freelance Radar DEMO bot started (DEMO_MODE=%s)", DEMO_MODE)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
