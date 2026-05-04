#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Habr Freelance parser:
- Опрашивает публичный RSS: https://freelance.habr.com/tasks.rss
- Фильтрация по last_id (без дублей)
- Рассылка пользователям через notifier.py
- Prometheus /metrics на порту 8004
"""

from __future__ import annotations

try:
    from kwork import Kwork
    KWORK_API_AVAILABLE = True
except ImportError:
    KWORK_API_AVAILABLE = False

from dotenv import load_dotenv
load_dotenv()

import asyncio
import logging
import os
import re
import signal
import sys
from typing import Any, Dict, List, Optional

if os.getenv("HABR_PARSER_ENABLED", "false").lower() != "true":
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("habr_parser").info("Habr parser disabled (HABR_PARSER_ENABLED not set)")
    sys.exit(0)

import httpx
from prometheus_client import Counter, Gauge, start_http_server

try:
    import feedparser
except ImportError:
    feedparser = None  # type: ignore

from bot.notifier import Notifier
from bot.utils.compat_cards import send_card_compat
from bot.storage import load_last_habr_project, save_last_habr_project
from bot.database import (
    init_db,
    can_user_send_cards,
    list_all_users,
    increment_user_stats_async,
    save_project_for_analytics,
)
from bot.settings import (
    HABR_RSS_URL,
    HABR_METRICS_PORT,
    PARSING_INTERVAL_SECONDS,
    MIN_BUDGET_RUB,
    ADMIN_CHAT_ID,
    SEND_ALL_TO_ADMIN,
)

log = logging.getLogger("habr_parser")


# ──────────────────────────── logging & metrics ────────────────────────────

def _setup_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d - %(message)s",
    )


M_LOOP    = Counter("habr_loop_total",    "Parser main loops")
M_ERRS    = Counter("habr_errors_total",  "Unhandled errors")
M_SENT    = Counter("habr_cards_sent_total", "Cards sent")
M_SKIPPED = Counter("habr_skipped_total", "Projects skipped")
G_LAST_ID = Gauge("habr_last_id",         "Last processed Habr task id")

_METRICS_STARTED = False


def _safe_metrics() -> None:
    global _METRICS_STARTED
    if _METRICS_STARTED:
        return
    try:
        port = int(os.getenv("HABR_METRICS_PORT", str(HABR_METRICS_PORT or 8004)))
        if port > 0:
            start_http_server(port)
            log.info("✅ /metrics on :%s", port)
            _METRICS_STARTED = True
    except Exception as e:
        log.warning("⚠️ Prometheus start failed: %s", e)


# ──────────────────────────── helpers ────────────────────────────

# Извлекаем числовой ID из URL вида https://freelance.habr.com/tasks/123456
_ID_RE = re.compile(r"/tasks/(\d+)", re.I)

# Попытка вытащить цену из текста описания: "от 5 000 руб", "5000-10000 ₽", "$200"
_PRICE_RE = re.compile(
    r"(?:от\s*|до\s*|бюджет[:\s]*)?(\d[\d\s]*[\d])"
    r"\s*(?:руб(?:лей)?|₽|RUB|\$|USD|USDT|€|EUR)",
    re.I | re.UNICODE,
)


def _extract_task_id(url: str) -> Optional[int]:
    m = _ID_RE.search(url or "")
    return int(m.group(1)) if m else None


def _extract_price(text: str) -> Optional[int]:
    """Извлекает первое найденное число-цену из произвольного текста."""
    m = _PRICE_RE.search(text or "")
    if not m:
        return None
    try:
        return int(m.group(1).replace(" ", "").replace("\u00a0", ""))
    except ValueError:
        return None


def _strip_html(text: str) -> str:
    """Удаляет HTML-теги из строки."""
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _parse_bool_env(val: Any) -> bool:
    return str(val).strip().lower() in ("1", "true", "yes", "y", "on")


def _user_telegram_id(u: Any) -> Optional[int]:
    for key in ("telegram_id", "tg_id", "telegramId"):
        if isinstance(u, dict) and key in u:
            try:
                return int(u[key])
            except Exception:
                pass
        else:
            try:
                return int(getattr(u, key))
            except Exception:
                pass
    return None


# ──────────────────────────── RSS fetch ────────────────────────────

async def _fetch_rss(url: str, timeout: float = 30.0) -> List[Dict[str, Any]]:
    """
    Скачивает RSS и возвращает список карточек:
    [{id, title, description, url, price, published}]
    """
    if feedparser is None:
        log.error("feedparser не установлен — RSS отключён. pip install feedparser")
        return []

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 KworkBot/1.0"})
            resp.raise_for_status()
            raw = resp.text
    except httpx.HTTPError as e:
        log.warning("RSS fetch HTTP error: %s", e)
        return []

    feed = feedparser.parse(raw)
    if feed.bozo and not feed.entries:
        log.warning("feedparser bozo error: %s", feed.bozo_exception)
        return []

    cards: List[Dict[str, Any]] = []
    for entry in feed.entries:
        link = getattr(entry, "link", "") or ""
        task_id = _extract_task_id(link)
        if not task_id:
            log.debug("No task ID in entry link: %s", link)
            continue

        title = _strip_html(getattr(entry, "title", "") or "")
        summary = _strip_html(getattr(entry, "summary", "") or getattr(entry, "description", "") or "")

        # Цена — пробуем из summary
        price = _extract_price(summary) or _extract_price(title)

        # Дата публикации
        published_parsed = getattr(entry, "published_parsed", None)

        cards.append({
            "id":          task_id,
            "title":       title or f"Задача #{task_id}",
            "description": summary or "Описание не доступно.",
            "url":         link,
            "price":       price,
            "source":      "habr",
            "published":   published_parsed,
        })

    # Сортируем по возрастанию ID для монотонного last_id
    cards.sort(key=lambda c: c["id"])
    return cards


# ──────────────────────────── main loop ────────────────────────────

async def _maybe_await(fn, *a, **k):
    import inspect
    r = fn(*a, **k)
    if inspect.isawaitable(r):
        return await r
    return r


async def run() -> None:
    _setup_logging()
    _safe_metrics()

    if feedparser is None:
        log.error("feedparser не установлен. Завершение.")
        return

    rss_url  = os.getenv("HABR_RSS_URL", HABR_RSS_URL or "https://freelance.habr.com/tasks.rss")
    interval = int(os.getenv("PARSING_INTERVAL_SECONDS", str(PARSING_INTERVAL_SECONDS or 120)))
    min_budget_rub = int(os.getenv("MIN_BUDGET_RUB", str(MIN_BUDGET_RUB or 0)))

    try:
        admin_chat = int(os.getenv("ADMIN_CHAT_ID", str(ADMIN_CHAT_ID or 0)))
    except (ValueError, TypeError):
        log.error("Invalid ADMIN_CHAT_ID")
        admin_chat = 0
    send_all_to_admin = _parse_bool_env(os.getenv("SEND_ALL_TO_ADMIN", str(SEND_ALL_TO_ADMIN)))

    stop_flag = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stop_flag.set)
        except NotImplementedError:
            pass

    await _maybe_await(init_db)
    notifier = Notifier()

    # self-test
    if admin_chat:
        test_card = {
            "title": "✅ Habr Parser: Self-test",
            "description": f"Сервис запущен, RSS: {rss_url}",
            "url": rss_url,
            "source": "habr",
        }
        try:
            await send_card_compat(notifier, admin_chat, test_card)
            log.info("✅ Self-test card sent to admin")
        except Exception as e:
            log.error("❌ Self-test send failed: %s", e)

    last_id = int(load_last_habr_project() or 0)
    G_LAST_ID.set(last_id)
    log.info(
        "▶️  Start Habr loop, rss_url=%s, last_id=%s, min_budget=%s, admin=%s, admin_only=%s",
        rss_url, last_id, min_budget_rub, admin_chat, send_all_to_admin,
    )

    while not stop_flag.is_set():
        M_LOOP.inc()
        try:
            cards = await _fetch_rss(rss_url)
            log.info("Habr RSS: %d задач получено", len(cards))

            for card in cards:
                task_id: int = card["id"]

                if task_id <= last_id:
                    M_SKIPPED.inc()
                    log.debug("skip old id=%s", task_id)
                    continue

                try:
                    await save_project_for_analytics(
                        title=card.get("title", ""),
                        budget=card.get("price"),
                        source="habr",
                        found_at=str(card.get("published") or ""),
                    )
                except Exception:
                    pass

                # Фильтр по минимальному бюджету
                price = card.get("price") or 0
                if price and min_budget_rub and price < min_budget_rub:
                    M_SKIPPED.inc()
                    log.debug("skip budget %s < %s, id=%s", price, min_budget_rub, task_id)
                    continue

                # Определяем получателей
                targets: List[int] = []
                if send_all_to_admin and admin_chat:
                    targets = [admin_chat]
                else:
                    try:
                        users = await _maybe_await(list_all_users)
                        for u in users or []:
                            tg_id = _user_telegram_id(u)
                            if not tg_id:
                                continue
                            try:
                                if await _maybe_await(can_user_send_cards, tg_id):
                                    targets.append(int(tg_id))
                            except Exception:
                                continue
                    except Exception as e:
                        log.error("list_all_users failed: %s", e)

                if not targets:
                    log.warning("No targets for id=%s", task_id)

                # Отправка
                for tg_id in targets:
                    try:
                        await send_card_compat(notifier, tg_id, card, auto_ai_if_pro=True)
                        M_SENT.inc()
                        if task_id > last_id:
                            last_id = task_id
                            save_last_habr_project(last_id)
                            G_LAST_ID.set(last_id)
                        try:
                            await _maybe_await(increment_user_stats_async, tg_id, "habr")
                        except TypeError:
                            try:
                                await _maybe_await(increment_user_stats_async, tg_id)
                            except Exception:
                                log.debug("increment_user_stats_async fallback failed", exc_info=True)
                    except Exception as e:
                        log.error("send failed to %s id=%s: %s", tg_id, task_id, e)

            await asyncio.wait_for(stop_flag.wait(), timeout=interval)

        except asyncio.TimeoutError:
            continue
        except Exception as e:
            M_ERRS.inc()
            log.exception("Unhandled error in Habr loop: %s", e)
            await asyncio.sleep(3)

    log.info("🛑 Habr parser stopped by signal")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
