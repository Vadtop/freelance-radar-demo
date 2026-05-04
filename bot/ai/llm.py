from __future__ import annotations

import os
import json
import time
from typing import Optional, Any

import aiohttp
from loguru import logger
from pydantic import BaseModel

from bot.ai.schemas import AgentContext

OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

MODEL_DRAFT = os.getenv("MODEL_DRAFT", "anthropic/claude-haiku-4.5")
MODEL_ANALYZE = os.getenv("MODEL_ANALYZE", "deepseek/deepseek-chat")
MODEL_ESTIMATE = os.getenv("MODEL_ESTIMATE", "deepseek/deepseek-chat")
MODEL_CRITIQUE = os.getenv("MODEL_CRITIQUE", "deepseek/deepseek-chat")

LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "")
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")

_langfuse_client = None


def _get_langfuse():
    global _langfuse_client
    if not LANGFUSE_HOST or not LANGFUSE_PUBLIC_KEY:
        return None
    if _langfuse_client is not None:
        return _langfuse_client
    try:
        from langfuse import Langfuse
        _langfuse_client = Langfuse(
            publicKey=LANGFUSE_PUBLIC_KEY,
            secretKey=LANGFUSE_SECRET_KEY,
            host=LANGFUSE_HOST,
        )
        return _langfuse_client
    except Exception as e:
        logger.warning(f"Langfuse init failed: {e}")
        return None


class LLMCall:
    __slots__ = ("model", "prompt_tokens", "completion_tokens", "cost_usd", "latency_ms")

    def __init__(self, model: str, prompt_tokens: int = 0, completion_tokens: int = 0, cost_usd: float = 0.0, latency_ms: float = 0.0):
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.cost_usd = cost_usd
        self.latency_ms = latency_ms


async def call_llm(
    *,
    model: str,
    system: str = "",
    user: str = "",
    temperature: float = 0.7,
    max_tokens: int = 2000,
    response_format: Optional[dict] = None,
    timeout: float = 60.0,
    trace_id: Optional[str] = None,
    span_name: str = "llm_call",
) -> tuple[str, LLMCall]:
    api_key = OPENROUTER_API_KEY
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    url = f"{OPENROUTER_BASE_URL.rstrip('/')}/chat/completions"
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        payload["response_format"] = response_format

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/Vadtop/freelance-radar",
        "X-Title": "freelance-radar-v2",
    }

    lf_span = None
    lf = _get_langfuse()
    if lf and trace_id:
        try:
            lf_trace = lf.get_trace(trace_id)
            lf_span = lf_trace.span(name=span_name, input={"model": model, "system": system[:200], "user": user[:200]})
        except Exception:
            pass

    t0 = time.monotonic()
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
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
    content = js["choices"][0]["message"]["content"]

    call = LLMCall(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=_estimate_cost(model, prompt_tokens, completion_tokens),
        latency_ms=latency_ms,
    )

    if lf_span:
        try:
            lf_span.end(output=content[:500], usage={"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens})
        except Exception:
            pass

    logger.debug(f"LLM {model}: {prompt_tokens}+{completion_tokens} tok, ${call.cost_usd:.6f}, {latency_ms:.0f}ms")
    return content, call


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = {
        "anthropic/claude-haiku-4.5": (0.80 / 1e6, 4.0 / 1e6),
        "deepseek/deepseek-chat": (0.14 / 1e6, 0.28 / 1e6),
        "z-ai/glm-4.6": (0.0, 0.0),
    }
    rate = rates.get(model, (0.14 / 1e6, 0.28 / 1e6))
    return prompt_tokens * rate[0] + completion_tokens * rate[1]


async def create_trace(name: str, ctx: AgentContext) -> Optional[str]:
    lf = _get_langfuse()
    if not lf:
        return None
    try:
        trace = lf.trace(name=name, metadata={"title": ctx.title, "source": ctx.source})
        return trace.id
    except Exception:
        return None
