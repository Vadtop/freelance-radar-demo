from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from bot.ai.schemas import AgentContext, CritiqueResult
from bot.ai.llm import call_llm, MODEL_CRITIQUE, MODEL_DRAFT, LLMCall
from bot.ai.banned_phrases import BANNED_PHRASES, check_banned

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "critique.txt"
_BANNED_BLOCK = "\n".join(f"— {p}" for p in BANNED_PHRASES[:15])

MAX_REWRITES = 1


async def run_critique(
    ctx: AgentContext,
    draft: str,
    trace_id: str | None = None,
) -> tuple[CritiqueResult, list[LLMCall]]:
    calls: list[LLMCall] = []

    prompt_text = _PROMPT_PATH.read_text(encoding="utf-8")
    user_msg = prompt_text.format(
        draft=draft,
        description=ctx.description,
        banned_phrases=_BANNED_BLOCK,
    )

    content, call = await call_llm(
        model=MODEL_CRITIQUE,
        system="Ты — редактор. Возвращай ТОЛЬКО валидный JSON без markdown-блоков.",
        user=user_msg,
        temperature=0.3,
        max_tokens=1000,
        trace_id=trace_id,
        span_name="critique",
    )
    calls.append(call)

    critique = _parse_critique(content)

    banned = check_banned(draft)
    if banned:
        critique.issues.extend([f"Запрещённая фраза: «{b}»" for b in banned])
        critique.score = max(0, critique.score - 2)
        critique.should_rewrite = True

    if critique.should_rewrite and critique.score < 7 and MAX_REWRITES > 0:
        logger.info(f"CRITIQUE score={critique.score}, rewriting...")
        rewrite_hints = "\n".join(f"— {s}" for s in critique.suggestions)
        rewrite_prompt = (
            f"Перепиши отклик, исправив эти проблемы:\n{rewrite_hints}\n\n"
            f"ЗАПРЕЩЁННЫЕ ФРАЗЫ (НЕ используй):\n{_BANNED_BLOCK}\n\n"
            f"ОРИГИНАЛЬНЫЙ ОТКЛИК:\n{draft}\n\n"
            f"ТЗ:\n{ctx.description}\n\n"
            "Перепиши. Без приветствий, без шаблонов, конкретно."
        )
        try:
            rewritten, call_rw = await call_llm(
                model=MODEL_DRAFT,
                system="Ты — Python AI-разработчик. Переписываешь отклик, исправляя проблемы. Без приветствий, без шаблонов.",
                user=rewrite_prompt,
                temperature=0.7,
                max_tokens=1000,
                trace_id=trace_id,
                span_name="rewrite",
            )
            calls.append(call_rw)

            re_banned = check_banned(rewritten)
            if not re_banned:
                draft = rewritten.strip()
                critique.should_rewrite = False
                critique.score = max(7, critique.score + 2)
                critique.issues = [i for i in critique.issues if "Запрещённая фраза" not in i]
                logger.info(f"REWRITE succeeded, new score={critique.score}")
            else:
                logger.info(f"REWRITE still has banned phrases: {re_banned}")
        except Exception as e:
            logger.warning(f"REWRITE failed: {e}")

    logger.info(f"CRITIQUE: score={critique.score}, should_rewrite={critique.should_rewrite}")
    return critique, calls


def _parse_critique(content: str) -> CritiqueResult:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        import re
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        obj = json.loads(cleaned)
        return CritiqueResult(
            score=min(10, max(0, int(obj.get("score", 5)))),
            issues=obj.get("issues", []),
            suggestions=obj.get("suggestions", []),
            should_rewrite=obj.get("should_rewrite", False),
        )
    except Exception as e:
        logger.warning(f"Failed to parse CRITIQUE JSON: {e}, raw: {content[:300]}")
        return CritiqueResult(score=5, issues=["Не удалось распарсить результат критики"])
