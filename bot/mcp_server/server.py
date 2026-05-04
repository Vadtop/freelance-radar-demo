from __future__ import annotations

import json
from typing import Optional

from loguru import logger

from bot.ai.schemas import AgentContext, ProjectAnalysis, Estimate, CritiqueResult, CaseHit
from bot.ai.steps.analyze import run_analyze
from bot.ai.steps.recall import run_recall
from bot.ai.steps.estimate import run_estimate
from bot.ai.steps.critique import run_critique

try:
    from mcp.server.fastmcp import FastMCP
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False
    logger.info("FastMCP not available, MCP server disabled")

if _MCP_AVAILABLE:
    mcp = FastMCP("freelance-radar-tools")

    @mcp.tool()
    async def mcp_analyze_brief(description: str, category: str = "", price_hint: str = "") -> str:
        """Анализирует ТЗ проекта → структурированный JSON с типом проекта, требованиями, оценкой полноты."""
        ctx = AgentContext(title="", description=description, category=category or None,
                          price_hint=float(price_hint) if price_hint else None)
        analysis, _ = await run_analyze(ctx)
        return analysis.model_dump_json(ensure_ascii=False)

    @mcp.tool()
    async def mcp_recall_cases(query: str, top_k: int = 3) -> str:
        """Ищет похожие кейсы в истории фрилансера через embedding-поиск."""
        ctx = AgentContext(title=query, description=query)
        hits, _ = await run_recall(ctx)
        results = hits[:top_k]
        return json.dumps([h.model_dump() for h in results], ensure_ascii=False)

    @mcp.tool()
    async def mcp_estimate_project(analysis_json: str, price_hint: str = "") -> str:
        """Декомпозиция проекта по модулям → часы → цена. На вход принимает JSON анализа."""
        analysis = ProjectAnalysis.model_validate_json(analysis_json)
        ctx = AgentContext(title="", description="", price_hint=float(price_hint) if price_hint else None, analysis=analysis)
        estimate, _ = await run_estimate(ctx)
        return estimate.model_dump_json(ensure_ascii=False)

    @mcp.tool()
    async def mcp_critique_draft(draft: str, description: str = "") -> str:
        """Проверяет отклик на запрещённые фразы и шаблонность. Возвращает score 0-10 + правки."""
        ctx = AgentContext(title="", description=description)
        critique, _ = await run_critique(ctx, draft)
        return critique.model_dump_json(ensure_ascii=False)

    @mcp.tool()
    async def mcp_get_freelancer_profile() -> str:
        """Возвращает актуальный профиль фрилансера (стек, кейсы, прайс)."""
        from pathlib import Path
        profile_path = Path(__file__).parent.parent / "ai" / "profile.md"
        if profile_path.exists():
            return profile_path.read_text(encoding="utf-8")
        return "Python AI-разработчик"

    @mcp.tool()
    async def mcp_get_competitor_stats(card_url: str = "") -> str:
        """Парсит количество откликов и средние цены конкурентов (если доступно)."""
        return json.dumps({"note": "competitor stats not implemented yet", "url": card_url})
