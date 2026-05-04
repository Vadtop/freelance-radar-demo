from __future__ import annotations

from pathlib import Path

from loguru import logger

from bot.ai.schemas import AgentContext, PipelineResult, FreelancerProfile
from bot.ai.llm import create_trace, LLMCall
from bot.ai.steps.analyze import run_analyze
from bot.ai.steps.recall import run_recall
from bot.ai.steps.estimate import run_estimate
from bot.ai.steps.draft import run_draft
from bot.ai.steps.critique import run_critique

_PROFILE_PATH = Path(__file__).parent / "profile.md"


def _load_profile() -> FreelancerProfile:
    if _PROFILE_PATH.exists():
        raw = _PROFILE_PATH.read_text(encoding="utf-8").strip()
    else:
        import os
        raw = os.getenv("FREELANCER_PROFILE", "Python AI-разработчик, aiogram, FastAPI, OpenAI API, RAG.")
    return FreelancerProfile(raw_text=raw)


async def run_pipeline(ctx: AgentContext) -> PipelineResult:
    total_tokens = 0
    total_cost = 0.0
    all_calls: list[LLMCall] = []

    trace_id = await create_trace("agent_v2_pipeline", ctx)

    profile = _load_profile()
    ctx.profile = profile

    # Step 1: ANALYZE
    try:
        analysis, call = await run_analyze(ctx, trace_id=trace_id)
        all_calls.append(call)
        total_tokens += call.prompt_tokens + call.completion_tokens
        total_cost += call.cost_usd
    except Exception as e:
        logger.error(f"ANALYZE step failed: {e}")
        from bot.ai.schemas import ProjectAnalysis
        ctx.analysis = ProjectAnalysis(completeness_score=5, needs_clarification=True)

    # Step 2: RECALL
    try:
        hits, call = await run_recall(ctx, trace_id=trace_id)
        all_calls.append(call)
    except Exception as e:
        logger.warning(f"RECALL step failed: {e}")

    # Step 3: ESTIMATE
    try:
        estimate, call = await run_estimate(ctx, trace_id=trace_id)
        all_calls.append(call)
        total_tokens += call.prompt_tokens + call.completion_tokens
        total_cost += call.cost_usd
    except Exception as e:
        logger.error(f"ESTIMATE step failed: {e}")

    # Step 4: DRAFT (A/B variants)
    questions: list[str] = []
    draft_a = ""
    draft_b = ""

    if ctx.analysis and ctx.analysis.needs_clarification and ctx.analysis.completeness_score < 5:
        try:
            questions = await _generate_questions(ctx, trace_id)
        except Exception as e:
            logger.warning(f"QUESTIONS generation failed: {e}")
            questions = ctx.analysis.questions_to_ask if ctx.analysis else []

    try:
        draft_a, draft_b, calls = await run_draft(ctx, trace_id=trace_id)
        all_calls.extend(calls)
        for c in calls:
            total_tokens += c.prompt_tokens + c.completion_tokens
            total_cost += c.cost_usd
    except Exception as e:
        logger.error(f"DRAFT step failed: {e}")

    # Step 5: CRITIQUE both drafts
    critique_a = None
    final_score = 0
    for label, draft in [("A", draft_a), ("B", draft_b)]:
        if not draft:
            continue
        try:
            critique, calls = await run_critique(ctx, draft, trace_id=trace_id)
            all_calls.extend(calls)
            for c in calls:
                total_tokens += c.prompt_tokens + c.completion_tokens
                total_cost += c.cost_usd
            if label == "A":
                critique_a = critique
                final_score = critique.score
        except Exception as e:
            logger.warning(f"CRITIQUE {label} failed: {e}")

    logger.info(f"PIPELINE done: score={final_score}, tokens={total_tokens}, cost=${total_cost:.4f}")

    return PipelineResult(
        context=ctx,
        draft_a=draft_a,
        draft_b=draft_b,
        questions=questions,
        critique=critique_a,
        final_score=final_score,
        total_tokens=total_tokens,
        total_cost_usd=total_cost,
        langfuse_trace_id=trace_id,
    )


async def _generate_questions(ctx: AgentContext, trace_id: str | None = None) -> list[str]:
    from bot.ai.llm import call_llm, MODEL_DRAFT
    prompt_path = Path(__file__).parent / "prompts" / "questions.txt"
    prompt_text = prompt_path.read_text(encoding="utf-8")
    analysis_text = ctx.analysis.model_dump_json(indent=2, ensure_ascii=False) if ctx.analysis else ""
    user_msg = prompt_text.format(description=ctx.description, analysis=analysis_text)

    content, _ = await call_llm(
        model=MODEL_DRAFT,
        system="Ты — Python AI-разработчик. Генерируешь уточняющие вопросы. Без приветствий.",
        user=user_msg,
        temperature=0.5,
        max_tokens=600,
        trace_id=trace_id,
        span_name="questions",
    )

    lines = [l.strip().lstrip("0123456789.-) ") for l in content.strip().splitlines() if l.strip()]
    return lines[:5]
