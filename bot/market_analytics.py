#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MarketAnalytics — еженедельная аналитика фриланс-рынка.
Собирает данные из projects_analytics за 7 дней,
формирует сводку и генерирует инсайты через DeepSeek.
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Optional

import aiohttp
from loguru import logger

from bot.database import _get_db

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")).rstrip("/")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

_RU_STOP_WORDS = frozenset(
    "и в на не что как с а но все так его это к у же вы из за бы по только "
    "её мне был до если да нет ещё о ли ей тут где когда уже он они мы "
    "то для себя ни чем будет её кого много надо ну чего тут".split()
)


class MarketAnalytics:
    async def weekly_report(self, admin_chat_id: int) -> str:
        db = await _get_db()
        cursor = await db.execute(
            "SELECT title, budget, source, found_at FROM projects_analytics "
            "WHERE julianday('now') - julianday(found_at) <= 7"
        )
        rows = await cursor.fetchall()

        if not rows:
            return "📊 <b>Аналитика за неделю</b>\n\nДанных пока нет — парсеры ещё не собрали заказы."

        source_counts: Counter = Counter()
        source_budgets: dict[str, list[int]] = {}
        word_counter: Counter = Counter()

        for r in rows:
            src = r["source"] or "unknown"
            source_counts[src] += 1
            budget = r["budget"]
            if budget is not None:
                source_budgets.setdefault(src, []).append(int(budget))
            title = (r["title"] or "").lower()
            for w in title.split():
                w = w.strip(".,!?:;()-«»\"'/\\").strip()
                if len(w) >= 3 and w not in _RU_STOP_WORDS:
                    word_counter[w] += 1

        lines = [f"📊 <b>Аналитика фриланс-рынка за неделю</b>\n"]
        lines.append(f"Всего заказов: <b>{len(rows)}</b>\n")

        for src, cnt in source_counts.most_common():
            budgets = source_budgets.get(src, [])
            avg = int(sum(budgets) / len(budgets)) if budgets else 0
            lines.append(f"  {src}: {cnt} заказов, средний бюджет {avg} ₽")

        lines.append("\n<b>Топ-15 слов в заголовках:</b>")
        for w, c in word_counter.most_common(15):
            lines.append(f"  {w}: {c}")

        stats_text = "\n".join(lines)

        ai_insight = await self._ask_ai(stats_text)
        if ai_insight:
            stats_text += f"\n\n<b>🤖 AI-инсайты:</b>\n{ai_insight}"

        return stats_text

    async def _ask_ai(self, stats: str) -> Optional[str]:
        if not DEEPSEEK_API_KEY:
            return None
        try:
            url = f"{DEEPSEEK_API_URL}/chat/completions"
            payload = {
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": "Ты аналитик фриланс-рынка. Выдели 3-5 инсайтов: что популярно, средний чек, на что обратить внимание фрилансеру. Пиши кратко на русском.",
                    },
                    {
                        "role": "user",
                        "content": f"Статистика фриланс-заказов за неделю:\n{stats}",
                    },
                ],
                "temperature": 0.3,
                "max_tokens": 400,
            }
            headers = {
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            }
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
                async with s.post(url, json=payload, headers=headers) as r:
                    if r.status == 200:
                        js = await r.json(content_type=None)
                        return js["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning("MarketAnalytics AI call failed: %s", e)
        return None
