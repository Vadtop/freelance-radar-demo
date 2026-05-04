from __future__ import annotations

from loguru import logger

from bot.ai.schemas import AgentContext, CaseHit
from bot.ai.llm import LLMCall, MODEL_ANALYZE

import os

USE_RECALL = os.getenv("USE_RECALL", "false").lower() in ("1", "true")


async def run_recall(ctx: AgentContext, trace_id: str | None = None) -> tuple[list[CaseHit], LLMCall]:
    if not USE_RECALL:
        logger.info("RECALL: disabled (USE_RECALL=false)")
        return [], LLMCall(model=MODEL_ANALYZE)

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        logger.info("RECALL: sentence-transformers not installed, skipping")
        return [], LLMCall(model=MODEL_ANALYZE)

    try:
        from bot.cases.store import CasesStore
    except Exception as e:
        logger.warning(f"RECALL: CasesStore not available: {e}")
        return [], LLMCall(model=MODEL_ANALYZE)

    _EMBEDDING_MODEL = None

    def _get_embedding_model():
        nonlocal _EMBEDDING_MODEL
        if _EMBEDDING_MODEL is not None:
            return _EMBEDDING_MODEL
        try:
            _EMBEDDING_MODEL = SentenceTransformer("intfloat/multilingual-e5-large")
            return _EMBEDDING_MODEL
        except Exception as e:
            logger.warning(f"Embedding model load failed: {e}")
            return None

    query_text = f"{ctx.title}\n{ctx.description}"[:1000]
    model = _get_embedding_model()
    if model is None:
        return [], LLMCall(model=MODEL_ANALYZE)

    try:
        query_vec = model.encode(query_text, normalize_embeddings=True).tolist()
    except Exception as e:
        logger.warning(f"Embedding failed: {e}")
        return [], LLMCall(model=MODEL_ANALYZE)

    try:
        store = CasesStore()
        hits = store.search(query_vec, top_k=3)
        ctx.past_orders = hits
        logger.info(f"RECALL: found {len(hits)} similar cases")
        return hits, LLMCall(model=MODEL_ANALYZE)
    except Exception as e:
        logger.warning(f"RECALL search failed: {e}")
        return [], LLMCall(model=MODEL_ANALYZE)
