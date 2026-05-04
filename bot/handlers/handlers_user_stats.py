#!/usr/bin/env python3
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.database import count_user_pitches

router = Router(name="user_stats")


@router.message(Command("stats_user"))
async def cmd_stats_user(msg: Message) -> None:
    stats = await count_user_pitches(msg.from_user.id)
    text = (
        "📊 <b>Твоя статистика</b>\n\n"
        f"Откликов сегодня: <b>{stats['today']}</b>\n"
        f"Откликов всего: <b>{stats['total']}</b>"
    )
    await msg.answer(text, parse_mode="HTML")


@router.message(Command("stats_responses"))
async def cmd_stats_responses(msg: Message) -> None:
    from bot.database import _get_db

    db = await _get_db()
    try:
        cur = await db.execute("""
            SELECT
                COUNT(*) AS total_runs,
                SUM(CASE WHEN gf.sent_to_client = 1 THEN 1 ELSE 0 END) AS sent,
                SUM(CASE WHEN gf.won_order = 1 THEN 1 ELSE 0 END) AS won,
                SUM(CASE WHEN gf.got_reply = 1 AND gf.won_order = 0 THEN 1 ELSE 0 END) AS rejected,
                AVG(gr.final_score) AS avg_score,
                SUM(gr.total_tokens) AS total_tokens,
                SUM(gr.total_cost_rub) AS total_cost
            FROM generation_runs gr
            LEFT JOIN generation_feedback gf ON gf.run_id = gr.id
        """)
        row = await cur.fetchone()
        if not row or row[0] == 0:
            await msg.answer("📭 Пока нет данных по откликам V2.", parse_mode="HTML")
            return

        total = int(row[0] or 0)
        sent = int(row[1] or 0)
        won = int(row[2] or 0)
        rejected = int(row[3] or 0)
        avg_score = float(row[4] or 0)
        total_tokens = int(row[5] or 0)
        total_cost = float(row[6] or 0)

        conv = (won / sent * 100) if sent > 0 else 0

        text = (
            "📊 <b>Статистика откликов V2</b>\n\n"
            f"Всего генераций: <b>{total}</b>\n"
            f"Отправлено клиенту: <b>{sent}</b>\n"
            f"Выиграно: <b>{won}</b>\n"
            f"Отказ: <b>{rejected}</b>\n"
            f"Конверсия: <b>{conv:.1f}%</b>\n\n"
            f"Средний score: <b>{avg_score:.1f}/10</b>\n"
            f"Токенов потрачено: <b>{total_tokens:,}</b>\n"
            f"Стоимость: <b>{total_cost:.0f}₽</b>"
        )
        await msg.answer(text, parse_mode="HTML")
    except Exception as e:
        await msg.answer(f"Ошибка: {e}", parse_mode="HTML")
