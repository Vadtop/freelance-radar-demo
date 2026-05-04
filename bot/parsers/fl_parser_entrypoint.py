#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FL.ru RSS parser:
- Опрашивает публичный RSS: https://freelance.fl.ru/rss/projects/
- Фильтрация по last_id (без дублей)
- Рассылка пользователям через notifier.py
- Prometheus /metrics на порту 8002
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
from bot.storage import load_last_fl_project, save_last_fl_project, is_last_id_stale
from bot.database import (
    init_db,
    can_user_send_cards,
    list_all_users,
    increment_user_stats_async,
    save_project_for_analytics,
)
from bot.settings import (
    FL_METRICS_PORT,
    LAST_FL_PROJECT_FILE,
    PARSING_INTERVAL_SECONDS,
    MIN_BUDGET_RUB,
    ADMIN_CHAT_ID,
    SEND_ALL_TO_ADMIN,
)

log = logging.getLogger("fl_parser")

FL_RSS_URL = os.getenv("FL_RSS_URL", "https://www.fl.ru/rss/all.xml")
FL_RSS_PARSER_ENABLED = os.getenv("FL_RSS_PARSER_ENABLED", "true").lower() in ("1", "true")

if not FL_RSS_PARSER_ENABLED:
    log.info("FL RSS parser disabled by env, exiting")
    import sys
    sys.exit(0)


def _setup_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d - %(message)s",
    )


M_LOOP    = Counter("fl_loop_total",    "Parser main loops")
M_ERRS    = Counter("fl_errors_total",  "Unhandled errors")
M_SENT    = Counter("fl_cards_sent_total", "Cards sent")
M_SKIPPED = Counter("fl_skipped_total", "Projects skipped")
G_LAST_ID = Gauge("fl_last_id",         "Last processed FL project id")

_METRICS_STARTED = False


def _safe_metrics() -> None:
    global _METRICS_STARTED
    if _METRICS_STARTED:
        return
    try:
        port = int(os.getenv("FL_METRICS_PORT", str(FL_METRICS_PORT or 8002)))
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
            log.debug("No project ID in entry link: %s", link)
            continue

        title = _strip_html(getattr(entry, "title", "") or "")
        summary = _strip_html(getattr(entry, "summary", "") or getattr(entry, "description", "") or "")
        price = _extract_price(summary) or _extract_price(title)

        published_parsed = getattr(entry, "published_parsed", None)

        cards.append({
            "id":          pid,
            "title":       title or f"FL project #{pid}",
            "description": summary or "Description not available.",
            "url":         link,
            "price":       price,
            "source":      "fl",
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

    interval = int(os.getenv("PARSING_INTERVAL_SECONDS", str(PARSING_INTERVAL_SECONDS or 90)))
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
            "title": "FL RSS Parser: Self-test",
            "description": f"Service started, RSS: {FL_RSS_URL}",
            "url": FL_RSS_URL,
            "source": "fl",
        }
        try:
            await send_card_compat(notifier, admin_chat, test_card)
            log.info("Self-test card sent to admin")
        except Exception as e:
            log.error("Self-test send failed: %s", e)

    last_id = int(load_last_fl_project() or 0)

    if os.getenv("FL_RESET_ON_START", "false").lower() in ("1", "true", "yes"):
        log.info("FL_RESET_ON_START=true: resetting last_id from %s to 0", last_id)
        last_id = 0
        save_last_fl_project(0)
    elif last_id and is_last_id_stale(LAST_FL_PROJECT_FILE, max_age_seconds=1800):
        log.info("last_id=%s is stale (>30min old), auto-resetting to 0", last_id)
        last_id = 0
        save_last_fl_project(0)

    G_LAST_ID.set(last_id)
    log.info(
        "Start FL RSS loop, url=%s, last_id=%s, min_budget=%s, admin=%s, admin_only=%s",
        FL_RSS_URL, last_id, min_budget_rub, admin_chat, send_all_to_admin,
    )

    while not stop_flag.is_set():
        M_LOOP.inc()
        try:
            fresh = int(load_last_fl_project() or 0)
            if fresh < last_id:
                log.info("last_id changed externally: %s -> %s", last_id, fresh)
                last_id = fresh
                G_LAST_ID.set(last_id)
            cards = await _fetch_rss(FL_RSS_URL)
            log.info("FL RSS: %d projects fetched", len(cards))

            for card in cards:
                pid: int = card["id"]

                if pid <= last_id:
                    M_SKIPPED.inc()
                    log.debug("skip old id=%s", pid)
                    continue

                try:
                    await save_project_for_analytics(
                        title=card.get("title", ""),
                        budget=card.get("price"),
                        source="fl",
                        found_at=card.get("published") or "",
                    )
                except Exception:
                    pass

                price = card.get("price") or 0
                if price and min_budget_rub and price < min_budget_rub:
                    M_SKIPPED.inc()
                    log.debug("skip budget %s < %s, id=%s", price, min_budget_rub, pid)
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

                if not targets:
                    log.warning("No targets for id=%s", pid)

                for tg_id in targets:
                    try:
                        await send_card_compat(notifier, tg_id, card, auto_ai_if_pro=True)
                        M_SENT.inc()
                        if pid > last_id:
                            last_id = pid
                            save_last_fl_project(last_id)
                            G_LAST_ID.set(last_id)
                        try:
                            await _maybe_await(increment_user_stats_async, tg_id, "fl")
                        except TypeError:
                            try:
                                await _maybe_await(increment_user_stats_async, tg_id)
                            except Exception:
                                log.debug("increment_user_stats_async fallback failed", exc_info=True)
                    except Exception as e:
                        log.error("send failed to %s id=%s: %s", tg_id, pid, e)

            await asyncio.wait_for(stop_flag.wait(), timeout=interval)

        except asyncio.TimeoutError:
            continue
        except Exception as e:
            M_ERRS.inc()
            log.exception("Unhandled error in FL loop: %s", e)
            await asyncio.sleep(3)

    log.info("FL RSS parser stopped by signal")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
