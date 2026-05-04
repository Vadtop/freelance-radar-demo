from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

import aiohttp
from loguru import logger

from bot.ai.schemas import AgentContext, PipelineResult, FreelancerProfile, ProjectAnalysis
from bot.ai.llm import create_trace, LLMCall, OPENROUTER_BASE_URL, OPENROUTER_API_KEY, MODEL_DRAFT

_MCP_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "mcp_analyze_brief",
            "description": "Анализирует ТЗ → тип проекта, требования, оценка полноты. Вызвать первым.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "Текст ТЗ"},
                    "category": {"type": "string", "description": "Категория биржи (опц.)"},
                    "price_hint": {"type": "string", "description": "Бюджет заказчика (опц.)"},
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_recall_cases",
            "description": "Ищет похожие кейсы в истории фрилансера. Вызвать если в ТЗ упомянут конкретный стек.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Поисковый запрос (описание ТЗ)"},
                    "top_k": {"type": "integer", "description": "Сколько кейсов вернуть (3 по умолч.)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_estimate_project",
            "description": "Декомпозиция проекта → часы → цена. Вызвать после анализа.",
            "parameters": {
                "type": "object",
                "properties": {
                    "analysis_json": {"type": "string", "description": "JSON с результатами анализа"},
                    "price_hint": {"type": "string", "description": "Бюджет заказчика (опц.)"},
                },
                "required": ["analysis_json"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_critique_draft",
            "description": "Проверяет отклик на запрещённые фразы и шаблонность. Вызвать перед финальным выводом.",
            "parameters": {
                "type": "object",
                "properties": {
                    "draft": {"type": "string", "description": "Текст отклика для проверки"},
                    "description": {"type": "string", "description": "ТЗ для сверки (опц.)"},
                },
                "required": ["draft"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_get_freelancer_profile",
            "description": "Возвращает профиль фрилансера (стек, кейсы, прайс).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_get_competitor_stats",
            "description": "Парсит отклики и цены конкурентов. Вызвать если URL заказа известен.",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_url": {"type": "string", "description": "URL карточки заказа"},
                },
                "required": [],
            },
        },
    },
]


async def _execute_tool(name: str, arguments: dict) -> str:
    from bot.ai.steps.analyze import run_analyze
    from bot.ai.steps.recall import run_recall
    from bot.ai.steps.estimate import run_estimate
    from bot.ai.steps.critique import run_critique

    if name == "mcp_analyze_brief":
        ctx = AgentContext(
            title="",
            description=arguments["description"],
            category=arguments.get("category") or None,
            price_hint=float(arguments["price_hint"]) if arguments.get("price_hint") else None,
        )
        analysis, _ = await run_analyze(ctx)
        return analysis.model_dump_json(ensure_ascii=False)

    elif name == "mcp_recall_cases":
        ctx = AgentContext(title=arguments["query"], description=arguments["query"])
        top_k = arguments.get("top_k", 3)
        hits, _ = await run_recall(ctx)
        return json.dumps([h.model_dump() for h in hits[:top_k]], ensure_ascii=False)

    elif name == "mcp_estimate_project":
        analysis = ProjectAnalysis.model_validate_json(arguments["analysis_json"])
        ctx = AgentContext(
            title="", description="",
            price_hint=float(arguments["price_hint"]) if arguments.get("price_hint") else None,
            analysis=analysis,
        )
        estimate, _ = await run_estimate(ctx)
        return estimate.model_dump_json(ensure_ascii=False)

    elif name == "mcp_critique_draft":
        ctx = AgentContext(title="", description=arguments.get("description", ""))
        critique, _ = await run_critique(ctx, arguments["draft"])
        return critique.model_dump_json(ensure_ascii=False)

    elif name == "mcp_get_freelancer_profile":
        profile_path = Path(__file__).parent / "profile.md"
        if profile_path.exists():
            return profile_path.read_text(encoding="utf-8")
        return "Python AI-разработчик"

    elif name == "mcp_get_competitor_stats":
        return json.dumps({"note": "competitor stats not implemented", "url": arguments.get("card_url", "")})

    return f"Unknown tool: {name}"


async def _call_llm_with_tools(
    messages: list[dict],
    tools: list[dict] | None = None,
    *,
    model: str = MODEL_DRAFT,
    temperature: float = 0.7,
    max_tokens: int = 2000,
    trace_id: str | None = None,
) -> tuple[dict, LLMCall]:
    api_key = OPENROUTER_API_KEY
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    url = f"{OPENROUTER_BASE_URL.rstrip('/')}/chat/completions"
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/Vadtop/freelance-radar",
        "X-Title": "freelance-radar-v2-agentic",
    }

    t0 = time.monotonic()
    timeout_cfg = aiohttp.ClientTimeout(total=60.0)
    async with aiohttp.ClientSession(timeout=timeout_cfg, headers=headers) as s:
        async with s.post(url, json=payload) as r:
            js = await r.json(content_type=None)
            if r.status != 200:
                err = json.dumps(js, ensure_ascii=False)[:500]
                raise RuntimeError(f"OpenRouter HTTP {r.status}: {err}")

    latency_ms = (time.monotonic() - t0) * 1000
    usage = js.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    message = js["choices"][0]["message"]

    call = LLMCall(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=_estimate_cost(model, prompt_tokens, completion_tokens),
        latency_ms=latency_ms,
    )

    logger.debug(f"Agentic LLM {model}: {prompt_tokens}+{completion_tokens} tok, ${call.cost_usd:.6f}")
    return message, call


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = {
        "anthropic/claude-haiku-4.5": (0.80 / 1e6, 4.0 / 1e6),
        "deepseek/deepseek-chat": (0.14 / 1e6, 0.28 / 1e6),
    }
    rate = rates.get(model, (0.14 / 1e6, 0.28 / 1e6))
    return prompt_tokens * rate[0] + completion_tokens * rate[1]


async def run_agentic(ctx: AgentContext) -> PipelineResult:
    trace_id = await create_trace("agent_v2_agentic", ctx)

    profile = _load_profile()
    ctx.profile = profile

    system_prompt = (
        "Ты — AI-агент, который пишет отклики на фриланс-заказы. "
        "Используй tools чтобы:\n"
        "1. mcp_analyze_brief — обязательно первым делом\n"
        "2. mcp_recall_cases — если в ТЗ упомянут стек\n"
        "3. mcp_estimate_project — после анализа\n"
        "4. mcp_get_competitor_stats — если URL заказа известен\n"
        "5. mcp_critique_draft — перед финальным выводом\n"
        "6. mcp_get_freelancer_profile — если нужен профиль\n\n"
        "Сам решай в каком порядке и сколько раз вызывать. "
        "Когда готов — выведи финальный отклик как обычный текст (без tool call)."
    )

    user_prompt = (
        f"ТЗ:\n{ctx.description}\n\n"
        f"Категория: {ctx.category or 'не указана'}\n"
        f"Бюджет: {ctx.price_hint or 'не указан'}\n"
        f"Источник: {ctx.source}\n"
        f"URL: {ctx.url}\n\n"
        "Напиши отклик. Сначала используй tools, потом выведи финальный текст."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    total_tokens = 0
    total_cost = 0.0
    max_iterations = 10
    final_text = ""

    for iteration in range(max_iterations):
        message, call = await _call_llm_with_tools(
            messages=messages,
            tools=_MCP_TOOLS,
            trace_id=trace_id,
        )
        total_tokens += call.prompt_tokens + call.completion_tokens
        total_cost += call.cost_usd

        tool_calls = message.get("tool_calls")

        if not tool_calls:
            final_text = (message.get("content") or "").strip()
            break

        messages.append(message)

        for tc in tool_calls:
            tc_id = tc.get("id", "")
            tc_func = tc.get("function", {})
            tc_name = tc_func.get("name", "")
            tc_args_str = tc_func.get("arguments", "{}")

            try:
                tc_args = json.loads(tc_args_str) if isinstance(tc_args_str, str) else tc_args_str
            except json.JSONDecodeError:
                tc_args = {}

            logger.info(f"Agentic tool call #{iteration}: {tc_name}({list(tc_args.keys())})")

            try:
                result = await _execute_tool(tc_name, tc_args)
            except Exception as e:
                logger.warning(f"Agentic tool {tc_name} failed: {e}")
                result = f"Error: {e}"

            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": result[:2000],
            })

    if not final_text:
        last_content = ""
        for m in reversed(messages):
            if isinstance(m, dict) and m.get("role") == "assistant" and m.get("content"):
                last_content = m["content"]
                break
        final_text = last_content.strip()

    logger.info(f"Agentic done: {total_tokens} tok, ${total_cost:.4f}, iterations={iteration+1 if 'iteration' in dir() else '?'}")

    return PipelineResult(
        context=ctx,
        draft_a=final_text,
        draft_b="",
        questions=[],
        critique=None,
        final_score=0,
        total_tokens=total_tokens,
        total_cost_usd=total_cost,
        langfuse_trace_id=trace_id,
    )


def _load_profile() -> FreelancerProfile:
    path = Path(__file__).parent / "profile.md"
    raw = path.read_text(encoding="utf-8").strip() if path.exists() else os.getenv("FREELANCER_PROFILE", "Python AI-разработчик")
    return FreelancerProfile(raw_text=raw)
