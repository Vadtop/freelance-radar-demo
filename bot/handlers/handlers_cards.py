#!/usr/bin/env python3
from __future__ import annotations

import html as _html
import json

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message
from loguru import logger

from bot.database import get_card_cache, record_pitch, save_generation_run, save_generation_feedback
from bot.ai_engine import AIEngine
from bot.utils.detail_enricher import enrich_project
from bot.settings import USE_AGENT_V2

router = Router(name="cards")


@router.callback_query(F.data.startswith("pitch_"))
async def cb_pitch(call: CallbackQuery) -> None:
    await call.answer()

    card_key = call.data.split("_", 1)[1] if "_" in call.data else ""
    if not card_key:
        await call.message.answer("Ошибка: не удалось определить карточку.")
        return

    card = await get_card_cache(card_key)
    if not card:
        await call.message.answer("Карточка устарела. Нажмите ссылку в карточке для отклика.")
        return

    loading = await call.message.answer("⏳ Читаю ТЗ и генерирую отклик...")

    title = card.get("title", "")
    desc = card.get("description", "")
    url = card.get("url", "")
    price = card.get("price")
    category = card.get("category") or ""
    source = card.get("source") or ""

    if USE_AGENT_V2:
        try:
            result = await _pitch_v2(call, card_key, title, desc, url, price, category, source)
        except Exception as e:
            logger.error(f"cb_pitch V2 failed: {e}")
            await call.message.answer("Ошибка генерации отклика (V2). Попробуйте позже.")
            try:
                await loading.delete()
            except Exception:
                pass
            return
    else:
        try:
            full_desc = desc
            if url:
                try:
                    enriched = await enrich_project(url, need_desc=True, need_price=False)
                    if enriched and enriched.get("description"):
                        fetched = enriched["description"].strip()
                        if len(fetched) > len(full_desc):
                            full_desc = fetched
                            logger.info("Enriched desc for {}: RSS={} chars → page={} chars", url, len(desc), len(full_desc))
                except Exception as e:
                    logger.warning("enrich_project failed for {}: {}", url, e)

            draft, plan = await AIEngine.generate_reply_and_plan(
                title=title,
                description=full_desc,
                price=price,
                url=url,
                category=category,
                source=source,
            )
        except Exception as e:
            logger.error("cb_pitch AI failed: {}", e)
            await call.message.answer("Ошибка генерации отклика. Попробуйте позже.")
            try:
                await loading.delete()
            except Exception:
                pass
            return

        if not draft:
            await call.message.answer("Не удалось сгенерировать отклик. Попробуйте позже.")
            try:
                await loading.delete()
            except Exception:
                pass
            return

        parts = [f"🤖 <b>AI-отклик:</b>\n<pre>{_esc(draft)}</pre>"]
        if plan:
            parts.append(f"\n📋 <b>План:</b>\n<pre>{_esc(plan)}</pre>")
        if url:
            parts.append(f'\n🔗 <a href="{url}">Открыть проект</a>')

        await call.message.answer("\n".join(parts), parse_mode="HTML", disable_web_page_preview=True)

        try:
            rows = []
            if url:
                rows.append([InlineKeyboardButton(text="🔗 Открыть проект", url=url)])
            rows.append([InlineKeyboardButton(text="✅ Откликнулся", callback_data="noop")])
            new_kb = InlineKeyboardMarkup(inline_keyboard=rows)
            await call.message.edit_reply_markup(reply_markup=new_kb)
        except Exception as e:
            logger.warning("edit_reply_markup failed: {}", e)

    try:
        await loading.delete()
    except Exception:
        pass

    try:
        await record_pitch(call.from_user.id, card_key, source)
    except Exception as e:
        logger.warning("record_pitch failed: {}", e)


async def _pitch_v2(
    call: CallbackQuery,
    card_key: str,
    title: str,
    desc: str,
    url: str,
    price,
    category: str,
    source: str,
) -> None:
    from bot.ai_engine import AIEngine

    full_desc = desc
    if url:
        try:
            enriched = await enrich_project(url, need_desc=True, need_price=False)
            if enriched and enriched.get("description"):
                fetched = enriched["description"].strip()
                if len(fetched) > len(full_desc):
                    full_desc = fetched
        except Exception as e:
            logger.warning("enrich_project failed for {}: {}", url, e)

    result = await AIEngine.generate_reply_and_plan_v2(
        title=title,
        description=full_desc,
        price=float(price) if price else None,
        url=url,
        category=category,
        source=source,
    )

    ctx = result.context
    analysis = ctx.analysis
    estimate = ctx.estimate

    parts = []

    if analysis:
        parts.append(f"🔍 <b>Анализ</b>")
        parts.append(f"  Тип: {analysis.project_type}")
        parts.append(f"  Сложность: {analysis.complexity}")
        parts.append(f"  Полнота ТЗ: {analysis.completeness_score}/10")
        if analysis.red_flags:
            parts.append(f"  ⚠ {', '.join(analysis.red_flags[:3])}")

    if estimate:
        parts.append(f"\n💰 <b>Оценка</b>")
        parts.append(f"  ~{estimate.total_hours:.0f} ч, {estimate.suggested_days} дн, {estimate.suggested_price_min:.0f}-{estimate.suggested_price_max:.0f}₽")
        if estimate.justification:
            parts.append(f"  {estimate.justification[:100]}")

    if ctx.past_orders:
        parts.append(f"\n📚 <b>Похожее:</b>")
        for c in ctx.past_orders[:2]:
            parts.append(f"  — {c.title} ({c.price}, {c.duration})")

    if result.draft_a:
        parts.append(f"\n✍️ <b>Вариант A</b> (короткий)")
        parts.append(f"<pre>{_esc(result.draft_a)}</pre>")

    if result.draft_b:
        parts.append(f"\n✍️ <b>Вариант B</b> (развёрнутый)")
        parts.append(f"<pre>{_esc(result.draft_b)}</pre>")

    if result.questions:
        parts.append(f"\n❓ <b>Уточняющие вопросы</b>")
        for i, q in enumerate(result.questions[:5], 1):
            parts.append(f"  {i}. {_esc(q)}")

    if result.critique:
        parts.append(f"\n📊 Score: {result.final_score}/10")

    if url:
        parts.append(f'\n🔗 <a href="{url}">Открыть проект</a>')

    text = "\n".join(parts)
    await call.message.answer(text, parse_mode="HTML", disable_web_page_preview=True)

    try:
        analysis_json = analysis.model_dump_json(ensure_ascii=False) if analysis else None
        estimate_json = estimate.model_dump_json(ensure_ascii=False) if estimate else None
        critique_json = result.critique.model_dump_json(ensure_ascii=False) if result.critique else None
        run_id = await save_generation_run(
            card_key=card_key,
            user_id=call.from_user.id,
            total_tokens=result.total_tokens,
            total_cost_rub=result.total_cost_usd * 95,
            langfuse_trace_id=result.langfuse_trace_id,
            analysis_json=analysis_json,
            estimate_json=estimate_json,
            draft_a=result.draft_a,
            draft_b=result.draft_b,
            critique_json=critique_json,
            final_score=result.final_score,
        )
    except Exception as e:
        logger.warning(f"save_generation_run failed: {e}")
        run_id = 0

    rows = []
    if url:
        rows.append([InlineKeyboardButton(text="🔗 Открыть проект", url=url)])

    ab_row = []
    if result.draft_a:
        ab_row.append(InlineKeyboardButton(text="📋 A", callback_data=f"copy_a_{card_key}"))
    if result.draft_b:
        ab_row.append(InlineKeyboardButton(text="📋 B", callback_data=f"copy_b_{card_key}"))
    ab_row.append(InlineKeyboardButton(text="🔄", callback_data=f"pitch_{card_key}"))
    rows.append(ab_row)

    feedback_row = []
    if run_id:
        feedback_row.append(InlineKeyboardButton(text="✅ Отправил", callback_data=f"fb_sent_{run_id}"))
        feedback_row.append(InlineKeyboardButton(text="🏆 Выиграл", callback_data=f"fb_won_{run_id}"))
        feedback_row.append(InlineKeyboardButton(text="❌ Отказ", callback_data=f"fb_lost_{run_id}"))
    rows.append(feedback_row)

    new_kb = InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await call.message.edit_reply_markup(reply_markup=new_kb)
    except Exception as e:
        logger.warning(f"edit_reply_markup failed: {e}")


@router.callback_query(F.data.startswith("copy_a_"))
async def cb_copy_a(call: CallbackQuery) -> None:
    await call.answer("Вариант A скопирован (долгое нажатие на текст)")
    card_key = call.data.split("copy_a_", 1)[1]
    _send_copy_hint(call, card_key, "a")


@router.callback_query(F.data.startswith("copy_b_"))
async def cb_copy_b(call: CallbackQuery) -> None:
    await call.answer("Вариант B скопирован (долгое нажатие на текст)")
    card_key = call.data.split("copy_b_", 1)[1]
    _send_copy_hint(call, card_key, "b")


async def _send_copy_hint(call: CallbackQuery, card_key: str, variant: str) -> None:
    pass


@router.callback_query(F.data.startswith("fb_sent_"))
async def cb_fb_sent(call: CallbackQuery) -> None:
    await call.answer()
    run_id = int(call.data.split("fb_sent_")[1])
    try:
        await save_generation_feedback(run_id, chosen_variant="unknown", sent_to_client=True)
    except Exception as e:
        logger.warning(f"save_generation_feedback failed: {e}")


@router.callback_query(F.data.startswith("fb_won_"))
async def cb_fb_won(call: CallbackQuery) -> None:
    await call.answer("🏆 Отлично!")
    run_id = int(call.data.split("fb_won_")[1])
    try:
        await save_generation_feedback(run_id, chosen_variant="unknown", won_order=True, user_rating=5)
    except Exception as e:
        logger.warning(f"save_generation_feedback failed: {e}")


@router.callback_query(F.data.startswith("fb_lost_"))
async def cb_fb_lost(call: CallbackQuery) -> None:
    await call.answer()
    run_id = int(call.data.split("fb_lost_")[1])
    try:
        await save_generation_feedback(run_id, chosen_variant="unknown", got_reply=True)
    except Exception as e:
        logger.warning(f"save_generation_feedback failed: {e}")


@router.callback_query(F.data.startswith("skip_"))
async def cb_skip(call: CallbackQuery) -> None:
    await call.answer("Пропущено")

    try:
        await call.message.delete()
    except Exception:
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery) -> None:
    await call.answer()


def _esc(text: str) -> str:
    return _html.escape(text or "", quote=False)


@router.message(Command("test_pitch"))
async def cmd_test_pitch(msg: Message) -> None:
    from bot.ai_engine import AIEngine
    from bot.settings import USE_AGENT_V2

    if not USE_AGENT_V2:
        await msg.answer("USE_AGENT_V2=false — V2 не включён. Добавь USE_AGENT_V2=true в .env")
        return

    loading = await msg.answer("⏳ Тестирую V2 pipeline...")

    try:
        result = await AIEngine.generate_reply_and_plan_v2(
            title="Нужен Telegram-бот для рассылки уведомлений",
            description="Нужен Telegram-бот для автоматической рассылки уведомлений клиентам с подключением к нашей CRM Битрикс24. 500+ пользователей, webhook на изменение сделки, шаблоны сообщений. Важно: уведомления должны приходить в течение 5 секунд после события в CRM.",
            price=50000,
            url="https://kwork.ru/projects/test123",
            category="telegram_bot",
            source="kwork",
        )

        ctx = result.context
        parts = []

        if ctx.analysis:
            parts.append(f"🔍 <b>Анализ</b>")
            parts.append(f"  Тип: {ctx.analysis.project_type}")
            parts.append(f"  Сложность: {ctx.analysis.complexity}")
            parts.append(f"  Полнота ТЗ: {ctx.analysis.completeness_score}/10")

        if ctx.estimate:
            parts.append(f"\n💰 <b>Оценка</b>")
            parts.append(f"  ~{ctx.estimate.total_hours:.0f} ч, {ctx.estimate.suggested_days} дн, {ctx.estimate.suggested_price_min:.0f}-{ctx.estimate.suggested_price_max:.0f}₽")

        if result.draft_a:
            parts.append(f"\n✍️ <b>Вариант A</b> (короткий)")
            parts.append(f"<pre>{_esc(result.draft_a)}</pre>")

        if result.draft_b:
            parts.append(f"\n✍️ <b>Вариант B</b> (развёрнутый)")
            parts.append(f"<pre>{_esc(result.draft_b)}</pre>")

        if result.questions:
            parts.append(f"\n❓ <b>Вопросы</b>")
            for i, q in enumerate(result.questions[:5], 1):
                parts.append(f"  {i}. {_esc(q)}")

        parts.append(f"\n📊 Score: {result.final_score}/10 | Токены: {result.total_tokens} | ${result.total_cost_usd:.4f}")

        text = "\n".join(parts)
        await msg.answer(text, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"test_pitch failed: {e}")
        await msg.answer(f"Ошибка: {e}")
    finally:
        try:
            await loading.delete()
        except Exception:
            pass
