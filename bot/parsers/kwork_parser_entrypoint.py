#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kwork parser — использует официальный API если заданы KWORK_LOGIN/KWORK_PASSWORD,
иначе fallback на HTML-скрапинг.
"""
from __future__ import annotations

try:
    from kwork import Kwork as _Kwork
    KWORK_API_AVAILABLE = True
except ImportError:
    KWORK_API_AVAILABLE = False

from dotenv import load_dotenv
load_dotenv()

import asyncio, logging, os, signal, inspect, re
from datetime import datetime, timezone
from html import unescape
from typing import Dict, List, Optional, Any
from prometheus_client import start_http_server

import bot.parser as pr
from bot.fetcher import Fetcher
from bot.notifier import Notifier
from bot.utils.compat_cards import send_card_compat
from bot.database import init_db, can_user_send_cards, list_all_users, increment_user_stats_async, save_project_for_analytics
from bot.settings import (
    KWORK_METRICS_PORT, PARSING_INTERVAL_SECONDS, MIN_BUDGET_RUB,
    ADMIN_CHAT_ID, SEND_ALL_TO_ADMIN, KWORK_CATEGORY_URL,
    KWORK_LOGIN, KWORK_PASSWORD, KWORK_USE_API,
)

log = logging.getLogger("kwork_parser")

def _notify_admin(text: str):
    try:
        import requests as _req
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        admin = os.getenv("ADMIN_CHAT_ID")
        if token and admin:
            _req.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": int(admin), "text": text[:4000]},
                timeout=10,
            )
    except Exception:
        pass

def _setup_logging():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s [%(levelname)s] %(message)s")

def _safe_metrics():
    try:
        port = int(os.getenv("KWORK_METRICS_PORT", str(KWORK_METRICS_PORT or 0)))
        if port > 0:
            start_http_server(port)
            log.info("Prometheus /metrics on :%s", port)
    except Exception as e:
        log.warning("metrics start failed: %s", e)

async def _maybe_await(fn, *a, **k):
    r = fn(*a, **k)
    if inspect.isawaitable(r): return await r
    return r

def _dedup(items: List[Any], key=None) -> List[Any]:
    seen = set(); out = []
    for x in items or []:
        k = key(x) if key else x
        if k and k not in seen:
            seen.add(k); out.append(x)
    return out

# ─── API-режим ────────────────────────────────────────────────────────────────

def _api_project_to_card(p: Any) -> Dict[str, Any]:
    """Конвертирует объект kwork.WantWorker → карточку."""
    pid = getattr(p, "id", None)
    url = f"https://kwork.ru/projects/{pid}/view" if pid else ""
    published_raw = getattr(p, "date_confirm", None)
    if isinstance(published_raw, str):
        try:
            published = datetime.fromisoformat(published_raw).replace(tzinfo=timezone.utc)
        except ValueError:
            published = datetime.now(timezone.utc)
    elif isinstance(published_raw, datetime):
        published = published_raw
    else:
        published = datetime.now(timezone.utc)

    return {
        "title":       getattr(p, "title", "") or "Без названия",
        "description": getattr(p, "description", "") or "",
        "price":       getattr(p, "price", None),
        "url":         url,
        "source":      "kwork",
        "published_at": published,
        "category":    str(getattr(p, "category_id", "") or ""),
    }

async def _fetch_via_api(kwork_client: Any, seen_ids: set) -> List[Dict[str, Any]]:
    """Получает новые проекты через API библиотеки kesha1225/kwork."""
    try:
        projects = await kwork_client.get_projects()
        new_items = [p for p in (projects or []) if getattr(p, "id", None) not in seen_ids]
        for p in new_items:
            seen_ids.add(getattr(p, "id"))
        log.info("Kwork API: получено %s проектов, новых %s", len(projects or []), len(new_items))
        return new_items
    except Exception as exc:
        log.error("Kwork API get_projects failed: %s", exc, exc_info=True)
        return []

# ─── HTML fallback (старый способ) ───────────────────────────────────────────

def _fallback_from_html(html: str, url: str) -> Dict[str, Any]:
    if not isinstance(html, str): html = ""
    title = None; desc = None
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if not m: m = re.search(r'<title[^>]*>(.*?)</title>', html, re.I | re.S)
    if m: title = unescape(m.group(1)).strip()
    m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if not m: m = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if m: desc = unescape(m.group(1)).strip()
    return {"title": title or "Kwork проект", "description": (desc or "")[:500], "url": url, "source": "kwork", "published_at": None}

def _html_project_to_card(p: Any) -> Dict[str, Any]:
    if isinstance(p, dict): raw = p
    else:
        try: raw = {k: v for k, v in vars(p).items() if not k.startswith("_")}
        except Exception: raw = {}
    return {
        "title":       raw.get("title") or raw.get("name") or raw.get("header") or "No title",
        "description": raw.get("description") or raw.get("text") or "",
        "price":       raw.get("price") or raw.get("budget"),
        "url":         raw.get("url") or raw.get("link") or raw.get("href"),
        "source":      "kwork",
        "published_at": None,
    }

async def _get_html(fetcher, url: str) -> str:
    try:
        res = await fetcher.fetch_html(url)
        if hasattr(res, "text"): res = res.text
        if isinstance(res, (bytes, bytearray)):
            try: res = res.decode("utf-8", "ignore")
            except Exception: pass
        return res if isinstance(res, str) else ""
    except Exception:
        return ""

async def _call_list_kwork(html: str, base: str) -> List[str]:
    fn = getattr(pr, "parse_multiple_projects_kwork", None)
    if not fn: return []
    for args in ((html, base), (base, html), (html,)):
        try:
            r = await _maybe_await(fn, *args)
            if isinstance(r, list): return r
        except TypeError: continue
        except Exception: return []
    return []

async def _call_single_kwork(html: str, url: str) -> Optional[Any]:
    for name in ("parse_single_project", "parse_single_project_kwork"):
        fn = getattr(pr, name, None)
        if not fn: continue
        for args in ((html, url), (url, html), (html,), (url,)):
            try: return await _maybe_await(fn, *args)
            except TypeError: continue
            except Exception: return None
    return None

# ─── Общие вспомогательные функции ───────────────────────────────────────────

async def _inc_cards_sent(chat_id: int, delta: int = 1) -> None:
    try:
        await _maybe_await(increment_user_stats_async, int(chat_id), "cards_sent", delta=delta)
    except Exception as exc:
        log.warning("Failed to increment cards_sent for chat %s: %s", chat_id, exc)

async def _safe_init_db():
    try:
        await _maybe_await(init_db)
        log.info("db initialized")
    except Exception as e:
        log.error("init_db failed: %s", e)

async def _get_targets(send_to_admin_only: bool, admin_chat: int) -> List[int]:
    if send_to_admin_only and admin_chat > 0:
        return [admin_chat]
    try:
        users = await _maybe_await(list_all_users)
        targets = []
        for u in users or []:
            tid = getattr(u, "telegram_id", None) or (u.get("telegram_id") if isinstance(u, dict) else None)
            if tid and await _maybe_await(can_user_send_cards, int(tid)):
                targets.append(int(tid))
        return targets or ([admin_chat] if admin_chat else [])
    except Exception as e:
        log.warning("users lookup failed: %s", e)
        return [admin_chat] if admin_chat else []

async def _send_card(notifier: Notifier, targets: List[int], card: Dict[str, Any],
                     min_budget: int, url: str) -> None:
    price = card.get("price")
    try:
        if min_budget and price is not None and int(float(price)) < min_budget:
            log.debug("skip budget<min: price=%s < %s, url=%s", price, min_budget, url)
            return
    except (ValueError, TypeError) as exc:
        log.debug("Budget parse error for %s price=%r: %s", url, price, exc)

    for chat in targets:
        card["user_telegram_id"] = int(chat)
        try:
            await send_card_compat(notifier, int(chat), card, auto_ai_if_pro=True)
            await _inc_cards_sent(int(chat), 1)
            log.info("sent card to %s: %s", chat, url)
        except Exception as e:
            log.error("send_card failed for chat %s url %s: %s", chat, url, e)

# ─── Основной цикл ────────────────────────────────────────────────────────────

async def main():
    _setup_logging()
    _safe_metrics()

    stop = asyncio.Event()
    def _sig(*_): stop.set()
    for s in (signal.SIGINT, signal.SIGTERM):
        try: signal.signal(s, _sig)
        except Exception: pass

    await _safe_init_db()
    notifier = Notifier()
    interval = int(os.getenv("PARSING_INTERVAL_SECONDS", str(PARSING_INTERVAL_SECONDS or 30)))
    min_budget = int(os.getenv("MIN_BUDGET_RUB", str(MIN_BUDGET_RUB or 0)))
    send_to_admin_only = bool(SEND_ALL_TO_ADMIN)
    admin_chat = int(ADMIN_CHAT_ID or 0)

    # API-режим
    kwork_client = None
    api_seen_ids: set = set()
    if KWORK_USE_API and KWORK_API_AVAILABLE:
        try:
            kwork_client = _Kwork(login=KWORK_LOGIN, password=KWORK_PASSWORD)
            await kwork_client.get_token()
            me = await kwork_client.get_me()
            log.info("Kwork API авторизован как: %s", getattr(me, "username", "?"))
        except Exception as exc:
            log.error("Kwork API авторизация провалилась: %s. Используем HTML fallback.", exc)
            kwork_client = None
    elif KWORK_USE_API and not KWORK_API_AVAILABLE:
        log.warning("Kwork API включён, но библиотека kwork не установлена (pip install kwork==0.0.5 --no-deps). Используем HTML fallback.")

    # HTML fallback (httpx only, no Playwright/Flare)
    fetcher = None
    if kwork_client is None:
        log.info("Kwork parser: режим HTML-скрапинга (httpx)")
        fetcher = Fetcher()

    try:
        while not stop.is_set():
            try:
                targets = await _get_targets(send_to_admin_only, admin_chat)

                if kwork_client is not None:
                    # ── API путь ──────────────────────────────────────────
                    new_projects = await _fetch_via_api(kwork_client, api_seen_ids)
                    for p in new_projects:
                        url = f"https://kwork.ru/projects/{getattr(p, 'id', '')}/view"
                        card = _api_project_to_card(p)
                        try:
                            await save_project_for_analytics(
                                title=card.get("title", ""),
                                budget=int(float(card.get("price") or 0)) if card.get("price") else None,
                                source="kwork",
                                found_at=datetime.now(timezone.utc).isoformat(),
                            )
                        except Exception:
                            pass
                        await _send_card(notifier, targets, card, min_budget, url)

                else:
                    # ── HTML fallback ─────────────────────────────────────
                    rss_urls: List[str] = []
                    fn_rss = getattr(pr, "parse_rss_kwork", None)
                    if callable(fn_rss):
                        try: rss_urls = await _maybe_await(fn_rss, fetcher)
                        except Exception as e: log.warning("parse_rss_kwork failed: %s", e)

                    html_list = await _get_html(fetcher, KWORK_CATEGORY_URL)
                    list_urls = await _call_list_kwork(html_list, KWORK_CATEGORY_URL)
                    urls = _dedup(rss_urls + list_urls)
                    log.info("Kwork HTML fallback: %s urls", len(urls))

                    for url in urls:
                        try:
                            html_project = await _get_html(fetcher, url)
                            if not html_project:
                                log.warning("empty html, skip: %s", url)
                                continue
                            proj = await _call_single_kwork(html_project, url)
                            if proj is None:
                                fb = _fallback_from_html(html_project, url)
                                card = {**fb, "price": None}
                                try:
                                    await save_project_for_analytics(
                                        title=card.get("title", ""),
                                        budget=None,
                                        source="kwork",
                                        found_at=datetime.now(timezone.utc).isoformat(),
                                    )
                                except Exception:
                                    pass
                                await _send_card(notifier, targets, card, min_budget, url)
                                continue
                            card = _html_project_to_card(proj)
                            if not card.get("url"): card["url"] = url
                            try:
                                await save_project_for_analytics(
                                    title=card.get("title", ""),
                                    budget=int(float(card.get("price") or 0)) if card.get("price") else None,
                                    source="kwork",
                                    found_at=datetime.now(timezone.utc).isoformat(),
                                )
                            except Exception:
                                pass
                            await _send_card(notifier, targets, card, min_budget, url)
                        except Exception as e:
                            log.error("project flow failed %s: %s", url, e)

            except Exception as e:
                log.exception("Kwork cycle failed: %s", e)
                try: _notify_admin(f"Kwork parser crashed: {e!r}")
                except Exception: pass

            try: await asyncio.wait_for(stop.wait(), timeout=max(5, interval))
            except asyncio.TimeoutError: pass

    finally:
        if kwork_client is not None:
            try: await kwork_client.close()
            except Exception: pass

if __name__ == "__main__":
    asyncio.run(main())
