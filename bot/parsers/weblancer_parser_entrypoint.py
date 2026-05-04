#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Weblancer RSS parser:
- Опрашивает публичный RSS: https://www.weblancer.net/projects/feed/
- Фильтрация по last_id (без дублей)
- Рассылка пользователям через notifier.py
- Prometheus /metrics
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
from typing import Any, Dict, List, Optional

import httpx
from prometheus_client import Counter, Gauge, start_http_server

try:
    import feedparser
except ImportError:
    feedparser = None

from bot.notifier import Notifier
from bot.utils.compat_cards import send_card_compat
from bot.storage import load_last_weblancer_project, save_last_weblancer_project
from bot.database import (
    init_db,
    can_user_send_cards,
    list_all_users,
    increment_user_stats_async,
    save_project_for_analytics,
)
from bot.settings import (
    PARSING_INTERVAL_SECONDS,
    MIN_BUDGET_RUB,
    ADMIN_CHAT_ID,
    SEND_ALL_TO_ADMIN,
)

log = logging.getLogger("weblancer_parser")

WEBLANCER_RSS_URL = os.getenv("WEBLANCER_RSS_URL", "https://www.weblancer.net/projects/?cat=0&subcats=1&budget=0&term=0&remote=0&type=projects&action=rss")
WEBLANCER_PARSER_ENABLED = os.getenv("WEBLANCER_PARSER_ENABLED", "true").lower() in ("1", "true")

if not WEBLANCER_PARSER_ENABLED:
    log.info("Weblancer parser disabled by env, exiting")
    import sys
    sys.exit(0)


def _setup_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d - %(message)s",
    )


M_LOOP    = Counter("weblancer_loop_total",    "Parser main loops")
M_ERRS    = Counter("weblancer_errors_total",  "Unhandled errors")
M_SENT    = Counter("weblancer_cards_sent_total", "Cards sent")
M_SKIPPED = Counter("weblancer_skipped_total", "Projects skipped")
G_LAST_ID = Gauge("weblancer_last_id",         "Last processed Weblancer project id")

_METRICS_STARTED = False


def _safe_metrics() -> None:
    global _METRICS_STARTED
    if _METRICS_STARTED:
        return
    try:
        port = int(os.getenv("WEBLANCER_METRICS_PORT", "8007"))
        if port > 0:
            start_http_server(port)
            log.info("/metrics on :%s", port)
            _METRICS_STARTED = True
    except Exception as e:
        log.warning("Prometheus start failed: %s", e)


_ID_RE = re.compile(r"/projects/(\d+)", re.I)

_PRICE_RE = re.compile(
    r"(?:от\s*|до\s*|бюджет[:\s]*)?(\d[\d\s]*[\d])"
    r"\s*(?:руб(?:лей)?|₽|RUB|\$|USD|€|EUR)",
    re.I | re.UNICODE,
)


def _extract_project_id(url: str) -> Optional[int]:
    m = _ID_RE.search(url or "")
    return int(m.group(1)) if m else None


def _extract_price(text: str) -> Optional[int]:
    m = _PRICE_RE.search(text or "")
    if not m:
        return None
    try:
        return int(m.group(1).replace(" ", "").replace("\u00a0", ""))
    except ValueError:
        return None


def _strip_html(text: str) -> str:
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


async def _fetch_rss(url: str, timeout: float = 30.0) -> List[Dict[str, Any]]:
    if feedparser is None:
        log.error("feedparser not installed. pip install feedparser")
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
        pid = _extract_project_id(link)
        if not pid:
            continue

        title = _strip_html(getattr(entry, "title", "") or "")
        summary = _strip_html(getattr(entry, "summary", "") or getattr(entry, "description", "") or "")
        price = _extract_price(summary) or _extract_price(title)
        published_parsed = getattr(entry, "published_parsed", None)

        cards.append({
            "id":          pid,
            "title":       title or f"Weblancer #{pid}",
            "description": summary or "Description not available.",
            "url":         link,
            "price":       price,
            "source":      "weblancer",
            "published":   published_parsed,
        })

    cards.sort(key=lambda c: c["id"])
    return cards


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
        log.error("feedparser not installed. Exiting.")
        return

    interval = int(os.getenv("PARSING_INTERVAL_SECONDS", str(PARSING_INTERVAL_SECONDS or 180)))
    min_budget_rub = int(os.getenv("MIN_BUDGET_RUB", str(MIN_BUDGET_RUB or 0)))

    try:
        admin_chat = int(os.getenv("ADMIN_CHAT_ID", str(ADMIN_CHAT_ID or 0)))
    except (ValueError, TypeError):
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

    if admin_chat:
        test_card = {
            "title": "Weblancer Parser: Self-test",
            "description": f"Service started, RSS: {WEBLANCER_RSS_URL}",
            "url": WEBLANCER_RSS_URL,
            "source": "weblancer",
        }
        try:
            await send_card_compat(notifier, admin_chat, test_card)
        except Exception as e:
            log.error("Self-test send failed: %s", e)

    last_id = int(load_last_weblancer_project() or 0)
    G_LAST_ID.set(last_id)
    log.info("Start Weblancer loop, url=%s, last_id=%s", WEBLANCER_RSS_URL, last_id)

    while not stop_flag.is_set():
        M_LOOP.inc()
        try:
            cards = await _fetch_rss(WEBLANCER_RSS_URL)
            log.info("Weblancer RSS: %d projects fetched", len(cards))

            for card in cards:
                pid: int = card["id"]

                if pid <= last_id:
                    M_SKIPPED.inc()
                    continue

                try:
                    await save_project_for_analytics(
                        title=card.get("title", ""),
                        budget=card.get("price"),
                        source="weblancer",
                        found_at=str(card.get("published") or ""),
                    )
                except Exception:
                    pass

                price = card.get("price") or 0
                if price and min_budget_rub and price < min_budget_rub:
                    M_SKIPPED.inc()
                    continue

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

                for tg_id in targets:
                    try:
                        await send_card_compat(notifier, tg_id, card, auto_ai_if_pro=True)
                        M_SENT.inc()
                        if pid > last_id:
                            last_id = pid
                            save_last_weblancer_project(last_id)
                            G_LAST_ID.set(last_id)
                        try:
                            await _maybe_await(increment_user_stats_async, tg_id, "cards_sent")
                        except Exception:
                            pass
                    except Exception as e:
                        log.error("send failed to %s id=%s: %s", tg_id, pid, e)

            await asyncio.wait_for(stop_flag.wait(), timeout=interval)

        except asyncio.TimeoutError:
            continue
        except Exception as e:
            M_ERRS.inc()
            log.exception("Unhandled error in Weblancer loop: %s", e)
            await asyncio.sleep(3)

    log.info("Weblancer parser stopped by signal")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
