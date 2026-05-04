from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from bot.ai.schemas import AgentContext, Estimate, EstimateModule
from bot.ai.llm import call_llm, MODEL_ESTIMATE, LLMCall

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "estimate.txt"


async def run_estimate(ctx: AgentContext, trace_id: str | None = None) -> tuple[Estimate, LLMCall]:
    if not ctx.analysis:
        return Estimate(), LLMCall(model=MODEL_ESTIMATE)

    prompt_text = _PROMPT_PATH.read_text(encoding="utf-8")

    profile_str = ctx.profile.raw_text if ctx.profile else "Python-разработчик"
    competitor_info = "нет данных"
    if ctx.competitor_count is not None:
        parts = [f"откликов: {ctx.competitor_count}"]
        if ctx.competitor_min_price is not None:
            parts.append(f"мин. цена: {ctx.competitor_min_price}₽")
        if ctx.competitor_avg_days is not None:
            parts.append(f"средний срок: {ctx.competitor_avg_days} дн.")
        competitor_info = ", ".join(parts)

    analysis_json = ctx.analysis.model_dump_json(indent=2, ensure_ascii=False)

    user_msg = prompt_text.format(
        profile=profile_str,
        analysis=analysis_json,
        price_hint=ctx.price_hint or "не указан",
        competitor_info=competitor_info,
    )

    content, call = await call_llm(
        model=MODEL_ESTIMATE,
        system="Ты — оценщик ИТ-проектов. Возвращай ТОЛЬКО валидный JSON без markdown-блоков.",
        user=user_msg,
        temperature=0.3,
        max_tokens=2000,
        trace_id=trace_id,
        span_name="estimate",
    )

    estimate = _parse_estimate(content)
    ctx.estimate = estimate
    logger.info(f"ESTIMATE: {estimate.total_hours}h, {estimate.suggested_days}d, {estimate.suggested_price_min}-{estimate.suggested_price_max}₽")
    return estimate, call


def _parse_estimate(content: str) -> Estimate:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        import re
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        obj = json.loads(cleaned)
        modules = []
        for m in obj.get("modules", []):
            modules.append(EstimateModule(
                name=m.get("name", ""),
                hours=float(m.get("hours", 0)),
                risk_factor=float(m.get("risk_factor", 1.0)),
            ))
        return Estimate(
            modules=modules,
            total_hours=float(obj.get("total_hours", 0)),
            risk_coefficient=float(obj.get("risk_coefficient", 1.0)),
            suggested_price_min=float(obj.get("suggested_price_min", 0)),
            suggested_price_max=float(obj.get("suggested_price_max", 0)),
            suggested_days=int(obj.get("suggested_days", 0)),
            justification=obj.get("justification", ""),
        )
    except Exception as e:
        logger.warning(f"Failed to parse ESTIMATE JSON: {e}, raw: {content[:300]}")
        return Estimate(justification="Не удалось автоматически оценить проект")
