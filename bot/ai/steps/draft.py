from __future__ import annotations

from pathlib import Path

from loguru import logger

from bot.ai.schemas import AgentContext
from bot.ai.llm import call_llm, MODEL_DRAFT, LLMCall
from bot.ai.banned_phrases import BANNED_PHRASES, check_banned

_PROMPT_SHORT = Path(__file__).parent.parent / "prompts" / "draft_short.txt"
_PROMPT_LONG = Path(__file__).parent.parent / "prompts" / "draft_long.txt"

_BANNED_BLOCK = "\n".join(f"— {p}" for p in BANNED_PHRASES[:15])


async def run_draft(ctx: AgentContext, trace_id: str | None = None) -> tuple[str, str, list[LLMCall]]:
    calls: list[LLMCall] = []

    analysis_text = ""
    if ctx.analysis:
        analysis_text = ctx.analysis.model_dump_json(indent=2, ensure_ascii=False)

    estimate_text = ""
    if ctx.estimate:
        estimate_text = ctx.estimate.model_dump_json(indent=2, ensure_ascii=False)

    past_orders_text = ""
    if ctx.past_orders:
        lines = []
        for c in ctx.past_orders:
            parts = [f"— {c.title}"]
            if c.stack:
                parts.append(f"  Стек: {c.stack}")
            if c.price:
                parts.append(f"  Цена: {c.price}")
            if c.duration:
                parts.append(f"  Срок: {c.duration}")
            if c.link:
                parts.append(f"  Ссылка: {c.link}")
            lines.append("\n".join(parts))
        past_orders_text = "\n\n".join(lines)
    else:
        past_orders_text = "Нет похожих кейсов в базе"

    profile_text = ctx.profile.raw_text if ctx.profile else "Python AI-разработчик"

    common_vars = dict(
        description=ctx.description,
        analysis=analysis_text,
        estimate=estimate_text,
        past_orders=past_orders_text,
        profile=profile_text,
        banned_phrases=_BANNED_BLOCK,
    )

    draft_a = ""
    draft_b = ""

    short_prompt = _PROMPT_SHORT.read_text(encoding="utf-8").format(**common_vars)
    try:
        content_a, call_a = await call_llm(
            model=MODEL_DRAFT,
            system="Ты — Python AI-разработчик. Пишешь отклик на заказ. Без приветствий, без шаблонов.",
            user=short_prompt,
            temperature=0.8,
            max_tokens=800,
            trace_id=trace_id,
            span_name="draft_a",
        )
        draft_a = content_a.strip()
        calls.append(call_a)
    except Exception as e:
        logger.warning(f"DRAFT A failed: {e}")
        draft_a = ""

    long_prompt = _PROMPT_LONG.read_text(encoding="utf-8").format(**common_vars)
    try:
        content_b, call_b = await call_llm(
            model=MODEL_DRAFT,
            system="Ты — Python AI-разработчик. Пишешь отклик на заказ. Без приветствий, без шаблонов.",
            user=long_prompt,
            temperature=0.8,
            max_tokens=1200,
            trace_id=trace_id,
            span_name="draft_b",
        )
        draft_b = content_b.strip()
        calls.append(call_b)
    except Exception as e:
        logger.warning(f"DRAFT B failed: {e}")
        draft_b = ""

    banned_a = check_banned(draft_a)
    banned_b = check_banned(draft_b)
    if banned_a:
        logger.info(f"DRAFT A has banned phrases: {banned_a}")
    if banned_b:
        logger.info(f"DRAFT B has banned phrases: {banned_b}")

    return draft_a, draft_b, calls
