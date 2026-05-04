#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Freelancehunt parser:
- Официальный REST API: https://api.freelancehunt.com/v2/
- Bearer-токен авторизация (FREELANCEHUNT_TOKEN)
- Эндпоинт: GET /v2/projects?page=1&filter[status]=active
- Rate limit: 1 запрос / 2 сек
- Prometheus /metrics на порту 8005
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

from bot.notifier import Notifier
from bot.utils.compat_cards import send_card_compat
from bot.storage import load_last_freelancehunt_project, save_last_freelancehunt_project
from bot.database import (
    init_db,
    can_user_send_cards,
    list_all_users,
    increment_user_stats_async,
    save_project_for_analytics,
)
from bot.settings import (
    FREELANCEHUNT_TOKEN,
    FREELANCEHUNT_METRICS_PORT,
    FREELANCEHUNT_PARSER_ENABLED,
    PARSING_INTERVAL_SECONDS,
    MIN_BUDGET_RUB,
    ADMIN_CHAT_ID,
    SEND_ALL_TO_ADMIN,
)

log = logging.getLogger("freelancehunt_parser")

_API_BASE = "https://api.freelancehunt.com/v2"
_PROJECTS_URL = f"{_API_BASE}/projects"

# ──────────────────────────── logging & metrics ────────────────────────────

def _setup_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d - %(message)s",
    )


M_LOOP    = Counter("fh_loop_total",        "Parser main loops")
M_ERRS    = Counter("fh_errors_total",      "Unhandled errors")
M_SENT    = Counter("fh_cards_sent_total",  "Cards sent")
M_SKIPPED = Counter("fh_skipped_total",     "Projects skipped")
G_LAST_ID = Gauge("fh_last_id",             "Last processed Freelancehunt project id")

_METRICS_STARTED = False


def _safe_metrics() -> None:
    global _METRICS_STARTED
    if _METRICS_STARTED:
        return
    try:
        port = int(os.getenv("FREELANCEHUNT_METRICS_PORT", str(FREELANCEHUNT_METRICS_PORT or 8005)))
        if port > 0:
            start_http_server(port)
            log.info("✅ /metrics on :%s", port)
            _METRICS_STARTED = True
    except Exception as e:
        log.warning("⚠️ Prometheus start failed: %s", e)


# ──────────────────────────── helpers ────────────────────────────

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


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _budget_to_rub(amount: Any, currency: str) -> Optional[int]:
    """Конвертирует бюджет в рубли (приблизительно, для фильтрации по min_budget)."""
    if amount is None:
        return None
    try:
        amt = float(amount)
    except (ValueError, TypeError):
        return None
    cur = (currency or "UAH").upper()
    # Грубый курс для фильтрации (не финансовая точность)
    rates = {"UAH": 2.5, "USD": 90.0, "EUR": 100.0, "RUB": 1.0, "RUR": 1.0}
    return int(amt * rates.get(cur, 2.5))


def _project_to_card(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Конвертирует элемент из /v2/projects в карточку для send_card_compat."""
    try:
        pid = item.get("id")
        attrs = item.get("attributes") or {}
        links = item.get("links") or {}

        title = _strip_html(attrs.get("name") or "")
        desc  = _strip_html(attrs.get("description") or "")
        url   = (links.get("self") or {}).get("web") or ""

        # Бюджет
        budget_obj = attrs.get("budget") or {}
        price_raw  = budget_obj.get("amount")
        currency   = (budget_obj.get("currency") or "UAH").upper()
        price_rub  = _budget_to_rub(price_raw, currency)

        # Категория из навыков
        skills = attrs.get("skills") or []
        category = ", ".join(
            s.get("name") or "" for s in skills[:3] if isinstance(s, dict) and s.get("name")
        )

        if not pid or not url:
            log.debug("project missing id or url: %s", item)
            return None

        return {
            "id":          int(pid),
            "title":       title or f"Проект #{pid}",
            "description": desc or "Описание не доступно.",
            "url":         url,
            "price":       price_rub,
            "currency":    "RUB",
            "source":      "freelancehunt",
            "category":    category,
        }
    except Exception as e:
        log.warning("_project_to_card error: %s item=%s", e, item)
        return None


# ──────────────────────────── API client ────────────────────────────

class FreelancehuntClient:
    """Минимальный async-клиент для Freelancehunt API v2."""

    def __init__(self, token: str, rate_limit_delay: float = 2.0) -> None:
        self._token = token
        self._delay = rate_limit_delay
        self._last_call: float = 0.0
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "FreelancehuntClient":
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 KworkBot/1.0",
            },
            timeout=30.0,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _get(self, url: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        """GET с соблюдением rate limit."""
        now = asyncio.get_event_loop().time()
        wait = self._delay - (now - self._last_call)
        if wait > 0:
            await asyncio.sleep(wait)

        assert self._client is not None
        resp = await self._client.get(url, params=params)
        self._last_call = asyncio.get_event_loop().time()
        resp.raise_for_status()
        return resp.json()

    async def get_active_projects(self, page: int = 1) -> List[Dict[str, Any]]:
        """Возвращает список активных проектов (первая страница)."""
        data = await self._get(
            _PROJECTS_URL,
            params={"page[number]": page, "page[size]": 25},
        )
        return (data.get("data") or []) if isinstance(data, dict) else []


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

    token = os.getenv("FREELANCEHUNT_TOKEN", FREELANCEHUNT_TOKEN or "")
    enabled = _parse_bool_env(os.getenv("FREELANCEHUNT_PARSER_ENABLED", str(FREELANCEHUNT_PARSER_ENABLED)))

    if not enabled:
        log.info("Freelancehunt parser disabled (FREELANCEHUNT_PARSER_ENABLED=false). Выход.")
        return

    if not token:
        log.error("FREELANCEHUNT_TOKEN не задан — парсер не может работать.")
        return

    interval = int(os.getenv("PARSING_INTERVAL_SECONDS", str(PARSING_INTERVAL_SECONDS or 60)))
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

    last_id = int(load_last_freelancehunt_project() or 0)
    G_LAST_ID.set(last_id)
    log.info(
        "▶️  Start Freelancehunt loop, last_id=%s, min_budget_rub=%s, admin=%s, admin_only=%s",
        last_id, min_budget_rub, admin_chat, send_all_to_admin,
    )

    async with FreelancehuntClient(token) as client:

        # self-test
        if admin_chat:
            test_card = {
                "title": "✅ Freelancehunt Parser: Self-test",
                "description": f"Сервис запущен, метрики на :{os.getenv('FREELANCEHUNT_METRICS_PORT', '8005')}",
                "url": "https://freelancehunt.com/projects/",
                "source": "freelancehunt",
            }
            try:
                await send_card_compat(notifier, admin_chat, test_card)
                log.info("✅ Self-test card sent to admin")
            except Exception as e:
                log.error("❌ Self-test send failed: %s", e)

        while not stop_flag.is_set():
            M_LOOP.inc()
            try:
                items = await client.get_active_projects()
                log.info("Freelancehunt API: %d проектов получено", len(items))

                # Сортируем по возрастанию id
                items.sort(key=lambda x: int(x.get("id") or 0))

                for item in items:
                    pid = int(item.get("id") or 0)
                    if not pid or pid <= last_id:
                        M_SKIPPED.inc()
                        log.debug("skip old id=%s", pid)
                        continue

                    card = _project_to_card(item)
                    if not card:
                        log.debug("skip unconvertible project id=%s", pid)
                        continue

                    try:
                        await save_project_for_analytics(
                            title=card.get("title", ""),
                            budget=card.get("price"),
                            source="freelancehunt",
                            found_at=card.get("published_at") or "",
                        )
                    except Exception:
                        pass

                    # Фильтр по бюджету
                    price = card.get("price") or 0
                    if price and min_budget_rub and price < min_budget_rub:
                        M_SKIPPED.inc()
                        log.debug("skip budget %s < %s id=%s", price, min_budget_rub, pid)
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
                        log.warning("No targets for id=%s", pid)

                    # Отправка
                    for tg_id in targets:
                        try:
                            await send_card_compat(notifier, tg_id, card, auto_ai_if_pro=True)
                            M_SENT.inc()
                            if pid > last_id:
                                last_id = pid
                                save_last_freelancehunt_project(last_id)
                                G_LAST_ID.set(last_id)
                            try:
                                await _maybe_await(increment_user_stats_async, tg_id, "freelancehunt")
                            except TypeError:
                                try:
                                    await _maybe_await(increment_user_stats_async, tg_id)
                                except Exception:
                                    log.debug("increment_user_stats_async fallback failed", exc_info=True)
                        except Exception as e:
                            log.error("send failed to %s id=%s: %s", tg_id, pid, e)

                await asyncio.wait_for(stop_flag.wait(), timeout=interval)

            except httpx.HTTPStatusError as e:
                M_ERRS.inc()
                if e.response.status_code == 429:
                    log.warning("Rate limit hit (429), ждём 10 сек")
                    await asyncio.sleep(10)
                elif e.response.status_code == 401:
                    log.error("❌ Неверный FREELANCEHUNT_TOKEN (401). Парсер остановлен.")
                    break
                else:
                    log.error("HTTP error %s: %s | body: %s", e.response.status_code, e, e.response.text[:300])
                    await asyncio.sleep(5)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                M_ERRS.inc()
                log.exception("Unhandled error in Freelancehunt loop: %s", e)
                await asyncio.sleep(5)

    log.info("🛑 Freelancehunt parser stopped")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
