#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AIEngine — лёгкая обёртка над OpenAI-совместимым API (DeepSeek/OpenAI и т.п.)
Режимы:
— MOCK: быстрый фейковый ответ (для оффлайна/дешёвых тестов)
— REAL: запрос к совместимому Chat Completions API

env:
  OPENAI_API_KEY
  OPENAI_BASE_URL (опц., напр. https://api.deepseek.com )
  OPENAI_MODEL   (по умолчанию: deepseek-chat)
  HTTP_PROXY / HTTPS_PROXY (если нужны)
"""

from __future__ import annotations

import os
from typing import Tuple, Optional, Any

import aiohttp
from loguru import logger

def _get_model() -> str:
    return os.getenv("OPENAI_MODEL", os.getenv("DEEPSEEK_MODEL", "deepseek-chat"))

def _get_base_url() -> str:
    return os.getenv("OPENAI_BASE_URL", os.getenv("DEEPSEEK_URL", os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/v1")))

def _get_api_key() -> str:
    return os.getenv("OPENAI_API_KEY", os.getenv("DEEPSEEK_API_KEY", ""))

def _is_mock() -> bool:
    return os.getenv("OFFLINE_MODE", "false").lower() in ("1", "true") or \
           os.getenv("DEEPSEEK_MOCK", "false").lower() in ("1", "true")

# backward compat aliases (read at call time via functions above)
DEFAULT_MODEL = ""
BASE_URL = ""
API_KEY  = ""
MOCK_MODE = False

from bot.settings import FREELANCER_PROFILE


class AIEngine:
    @classmethod
    async def generate_bid_and_solution(
        cls,
        project_text: str,
        model: Optional[str] = None,
        timeout: float = 40.0,
    ) -> Tuple[str, str]:
        if not project_text or len(project_text) < 5:
            return ("Готов(а) помочь — отправьте детали проекта.", "")
        api_key = _get_api_key()
        if _is_mock() or not api_key:
            return cls._mock_draft(project_text), cls._mock_plan(project_text)
        try:
            return await cls._real_call(project_text, model=model or _get_model(), timeout=timeout)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"AIEngine fallback to MOCK due to: {e!r}")
            return cls._mock_draft(project_text), cls._mock_plan(project_text)

    @classmethod
    async def generate_reply_and_plan_v2(
        cls,
        *,
        title: str = "",
        description: Optional[str] = None,
        price: Optional[float] = None,
        url: Optional[str] = None,
        category: Optional[str] = None,
        source: Optional[str] = None,
        deadline_hint: Optional[str] = None,
        **_: Any,
    ) -> Any:
        from bot.ai.schemas import AgentContext
        from bot.settings import USE_AGENT_V2, USE_MCP_AGENT

        ctx = AgentContext(
            title=title,
            description=description or "",
            price_hint=price,
            category=category,
            source=source or "",
            url=url or "",
            deadline_hint=deadline_hint,
        )

        if USE_MCP_AGENT:
            from bot.ai.agentic import run_agentic
            return await run_agentic(ctx)

        from bot.ai.pipeline import run_pipeline
        return await run_pipeline(ctx)

    # Адаптер под старый интерфейс: ai.generate_reply_and_plan(...)
    # Работает и при вызове через класс, и через инстанс.
    @classmethod
    async def generate_reply_and_plan(
        cls,
        *,
        title: str = "",
        description: Optional[str] = None,
        price: Optional[float] = None,
        url: Optional[str] = None,
        category: Optional[str] = None,
        source: Optional[str] = None,
        user_telegram_id: Optional[int] = None,  # игнорируем, но «глотаем» для совместимости
        currency: Optional[str] = None,
        timeout: float = 40.0,
        model: Optional[str] = None,
        **_: Any,  # на будущее: безопасно глотаем неожиданные параметры
    ) -> Tuple[str, str]:
        try:
            parts = []
            if title:
                parts.append(f"Проект: {title}")
            if category:
                parts.append(f"Категория: {category}")
            if price is not None:
                parts.append(f"Бюджет: {price} {currency or ''}".strip())
            if source:
                parts.append(f"Источник: {source}")
            if url:
                parts.append(f"Ссылка: {url}")
            if description:
                parts.append(f"Описание:\n{description}")

            project_text = "\n".join(p for p in parts if p) or (description or title or "")
            return await cls.generate_bid_and_solution(
                project_text=project_text,
                model=model,
                timeout=timeout,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"AIEngine.generate_reply_and_plan fallback error: {e!r}")
            return "", ""

    @classmethod
    async def _real_call(cls, project_text: str, model: str, timeout: float) -> Tuple[str, str]:
        url = f"{_get_base_url().rstrip('/')}/chat/completions"

        profile = os.getenv("FREELANCER_PROFILE", "").strip()
        if not profile:
            profile = "Python-разработчик, общий опыт."

        system_content = (
            "Ты — Python-разработчик, пишущий отклик на заказ от первого лица. "
            "Твоя цель — за 4 предложения убедить заказчика, что ты понял его задачу и решишь её лучше других.\n\n"
            f"ТВОЙ ПРОФИЛЬ:\n{profile}\n\n"
            "СТРУКТУРА ОТКЛИКА (ровно 4 предложения, в этом порядке):\n"
            "1. Покажи что ты понял КОНКРЕТНУЮ проблему/потребность из ТЗ "
            "(не общую формулировку «вижу что вам нужен бот», а конкретно: «вам нужно автоматизировать "
            "уведомления клиентам через Telegram с подключением к вашей CRM»).\n"
            "2. Опиши КОНКРЕТНОЕ техническое решение из своего стека: какие библиотеки, какой подход "
            "(пример: «Сделаю на aiogram 3.x с webhook на FastAPI, очередь сообщений через Redis "
            "чтобы не упёрлись в rate-limit Telegram»).\n"
            "3. Сошлись на РЕЛЕВАНТНЫЙ кейс из профиля с цифрой "
            "(пример: «Похожую систему делал для рассылок — AI-ассистент за 15к, "
            "обрабатывал 500 сообщений в час»).\n"
            "4. Конкретный результат и срок "
            "(пример: «Покажу рабочий MVP за 3 дня, полная сдача — 7 дней, цена 25к»).\n\n"
            "ЗАПРЕЩЕНО:\n"
            "— Шаблонные фразы: «готов обсудить», «жду ответа», «регулярные апдейты», "
            "«прозрачный план», «начать сегодня», «есть опыт», «качественно и в срок».\n"
            "— Обращение «Здравствуйте» / «Добрый день» — сразу к делу.\n"
            "— Технологии которых нет в профиле фрилансера.\n"
            "— Восклицательные знаки и капс.\n"
            "— Слова «я» в начале каждого предложения (вариативность).\n\n"
            "СТИЛЬ: разговорный, уверенный, конкретный. Как сообщение коллеге, а не презентация."
        )

        plan_instruction = (
            "Также верни план работ — 4-5 КОНКРЕТНЫХ шагов с техническими деталями "
            "(не «1. Обсуждение требований», а «1. Развернуть aiogram 3 + FastAPI скелет, настроить webhook на staging»). "
            "Без шага «обсуждение/уточнение» — это и так подразумевается."
        )

        user_content = (
            f"ЗАКАЗ:\n{project_text}\n\n"
            "Напиши отклик по структуре выше и план работ.\n"
            + plan_instruction + "\n\n"
            'Верни ТОЛЬКО валидный JSON без markdown-блоков:\n'
            '{"draft": "<отклик 4 предложения>", "plan": ["шаг1", "шаг2", "шаг3", "шаг4"]}'
        )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        payload = {"model": model, "messages": messages, "temperature": 0.8, "max_tokens": 1200}

        timeout_cfg = aiohttp.ClientTimeout(total=timeout)
        headers = {"Authorization": f"Bearer {_get_api_key()}", "Content-Type": "application/json"}

        async with aiohttp.ClientSession(timeout=timeout_cfg, headers=headers) as s:
            async with s.post(url, json=payload) as r:
                js = await r.json(content_type=None)
                if r.status != 200:
                    raise RuntimeError(f"AI HTTP {r.status}: {js}")
                content = js["choices"][0]["message"]["content"]
                draft, plan = cls._parse_model_content(content)
                return draft, plan

    @staticmethod
    def _parse_model_content(content: str) -> Tuple[str, str]:
        import json, re
        cleaned = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
        try:
            obj = json.loads(cleaned)
            draft = str(obj.get("draft") or "").strip()
            plan_val = obj.get("plan")
            if isinstance(plan_val, list):
                plan = "\n".join(f"{i+1}. {str(x).strip()}" for i, x in enumerate(plan_val) if str(x).strip())
            else:
                plan = str(plan_val or "").strip()
            if draft or plan:
                return draft, plan
        except Exception:
            logger.warning("AI JSON parse failed, raw content: {}", content[:500])
            pass
        parts = re.split(r"(?:^|\n)План[:：]|Plan[:：]", content, maxsplit=1, flags=re.I)
        if len(parts) == 2:
            return parts[0].strip(), parts[1].strip()
        return content.strip(), ""

    @staticmethod
    def _mock_draft(project_text: str) -> str:
        return (
            "Здравствуйте! Задача понятна — могу подключиться и выполнить. "
            "Предложу прозрачный план работ, регулярные апдейты и аккуратные сроки. "
            "Готов(а) обсудить детали и начать сегодня."
        )

    @staticmethod
    def _mock_plan(project_text: str) -> str:
        return (
            "1) Уточню требования и критерии готовности;\n"
            "2) Подготовлю архитектуру/план работ;\n"
            "3) Реализую и покрою базовыми тестами;\n"
            "4) Деплой и короткая эксплуатация;\n"
            "5) Передача и документация."
        )
