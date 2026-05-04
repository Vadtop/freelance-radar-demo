from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from bot.ai.schemas import AgentContext, ProjectAnalysis
from bot.ai.llm import call_llm, MODEL_ANALYZE, LLMCall

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "analyze.txt"


async def run_analyze(ctx: AgentContext, trace_id: str | None = None) -> tuple[ProjectAnalysis, LLMCall]:
    prompt_text = _PROMPT_PATH.read_text(encoding="utf-8")
    user_msg = prompt_text.format(
        description=ctx.description,
        category=ctx.category or "не указана",
        price_hint=ctx.price_hint or "не указан",
    )

    content, call = await call_llm(
        model=MODEL_ANALYZE,
        system="Ты — старший аналитик-разработчик. Возвращай ТОЛЬКО валидный JSON без markdown-блоков.",
        user=user_msg,
        temperature=0.3,
        max_tokens=1500,
        trace_id=trace_id,
        span_name="analyze",
    )

    analysis = _parse_analysis(content)
    ctx.analysis = analysis
    logger.info(f"ANALYZE: type={analysis.project_type}, completeness={analysis.completeness_score}, complexity={analysis.complexity}")
    return analysis, call


def _parse_analysis(content: str) -> ProjectAnalysis:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        import re
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        obj = json.loads(cleaned)
        return ProjectAnalysis(
            project_type=obj.get("project_type", "other"),
            key_requirements=obj.get("key_requirements", []),
            hidden_requirements=obj.get("hidden_requirements", []),
            tech_stack_hints=obj.get("tech_stack_hints", []),
            completeness_score=min(10, max(0, int(obj.get("completeness_score", 5)))),
            needs_clarification=obj.get("needs_clarification", False),
            questions_to_ask=obj.get("questions_to_ask", []),
            complexity=obj.get("complexity", "medium"),
            red_flags=obj.get("red_flags", []),
        )
    except Exception as e:
        logger.warning(f"Failed to parse ANALYZE JSON: {e}, raw: {content[:300]}")
        return ProjectAnalysis(
            completeness_score=5,
            needs_clarification=True,
            questions_to_ask=["Не удалось автоматически проанализировать ТЗ — уточните детали"],
        )
