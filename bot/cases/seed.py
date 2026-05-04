from __future__ import annotations

import asyncio
import json
from pathlib import Path

from loguru import logger

SEED_CASES = [
    {
        "title": "AI-ассистент холодных рассылок",
        "stack": "Python, Playwright, Gmail API, Claude",
        "price": "15 000 ₽",
        "duration": "5 дней",
        "link": "",
        "description": "Автоматизация холодных рассылок через Gmail API с AI-генерацией текстов на Claude. Playwright для парсинга контактов.",
    },
    {
        "title": "RAG-бот школа (Yandex GPT + pgvector)",
        "stack": "Python, FastAPI, pgvector, YandexGPT",
        "price": "80 000 ₽",
        "duration": "14 дней",
        "link": "github.com/Vadtop/sql-rag-pipeline",
        "description": "RAG-система для школы: pgvector для эмбеддингов, YandexGPT для генерации ответов, FastAPI бэкенд.",
    },
    {
        "title": "JARVIS — AI-агент система (production)",
        "stack": "Python, Claude, ChromaDB, 58 tools",
        "price": "",
        "duration": "24/7",
        "link": "",
        "description": "Production AI-агент с 58 MCP-tools, ChromaDB для памяти, Claude как основная модель. Работает 24/7.",
    },
    {
        "title": "hh-mcp-server — MCP сервер для hh.ru",
        "stack": "Python, MCP, Playwright",
        "price": "open-source",
        "duration": "",
        "link": "github.com/Vadtop/hh-mcp-server",
        "description": "MCP-сервер для автоматизации откликов на hh.ru через Playwright.",
    },
    {
        "title": "rag-from-scratch — RAG с нуля",
        "stack": "Python, ChromaDB, sentence-transformers",
        "price": "open-source",
        "duration": "",
        "link": "github.com/Vadtop/rag-from-scratch",
        "description": "Учебный проект: RAG pipeline на ChromaDB + sentence-transformers, полный цикл от документа до ответа.",
    },
]


async def seed_cases():
    from bot.cases.store import CasesStore
    from bot.ai.steps.recall import _get_embedding_model

    store = CasesStore()
    embed_model = _get_embedding_model()

    for case in SEED_CASES:
        embedding = None
        if embed_model:
            try:
                text = f"{case['title']}: {case.get('description', '')}"
                vec = embed_model.encode(text, normalize_embeddings=True)
                embedding = vec.tolist()
            except Exception as e:
                logger.warning(f"Embedding failed for '{case['title']}': {e}")

        try:
            row_id = await store.add_case(case, embedding)
            logger.info(f"Seeded case: {case['title']} (id={row_id})")
        except Exception as e:
            logger.warning(f"Failed to seed case '{case['title']}': {e}")

    logger.info("Cases seeding complete")


if __name__ == "__main__":
    asyncio.run(seed_cases())
