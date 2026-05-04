# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Optional, Dict, Tuple

import aiohttp
from loguru import logger
from bs4 import BeautifulSoup  # pip install beautifulsoup4

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MyFreelanceBot/1.0; +https://example.local)"
}

# FIX: нормализация пробелов, в т.ч. неразрывных/узких
_NBSP_RE = re.compile(r"[\u00A0\u202F\u2007\u2060]")

# FIX: расширенные паттерны цен (диапазоны, от/до, одиночное, договорной)
_RX_RANGE = re.compile(r"(\d[\d\s]{0,12})\s*[–—-]\s*(\d[\d\s]{0,12})\s*(?:₽|руб)", re.I)
_RX_FROM  = re.compile(r"(?:^|\D)(от)\s*(\d[\d\s]{0,12})\s*(?:₽|руб)", re.I)
_RX_TO    = re.compile(r"(?:^|\D)(до)\s*(\d[\d\s]{0,12})\s*(?:₽|руб)", re.I)
_RX_SINGLE= re.compile(r"(\d[\d\s]{0,12})\s*(?:₽|руб)", re.I)
_RX_NEGOT = re.compile(r"договорн|по\s+договор", re.I)

def _norm_spaces(s: str) -> str:
    return _NBSP_RE.sub(" ", (s or "")).strip()

async def _fetch_text(url: str, timeout: int = 30) -> str:
    async with aiohttp.ClientSession(headers=HEADERS) as s:
        async with s.get(url, timeout=timeout, allow_redirects=True) as r:
            r.raise_for_status()
            return await r.text()

def _clean(s: Optional[str]) -> str:
    return (s or "").strip()

def _meta(soup: BeautifulSoup, *names: str) -> Optional[str]:
    for n in names:
        tag = soup.find("meta", attrs={"name": n}) or soup.find("meta", attrs={"property": n})
        if tag:
            v = tag.get("content")
            if v:
                return v
    return None

def _to_number(s: str) -> Optional[float]:
    if not s:
        return None
    s = _norm_spaces(s)
    s = re.sub(r"[^\d]", "", s)
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None

def _guess_price_from_text(text: str) -> Tuple[Optional[float], Optional[str]]:
    """
    Возвращает (price, note). Если «договорной» — (None, 'negotiable').
    """
    t = _norm_spaces(text)

    if _RX_NEGOT.search(t):
        return None, "negotiable"

    m = _RX_RANGE.search(t)
    if m:
        lo, hi = _to_number(m.group(1)), _to_number(m.group(2))
        if hi:
            return hi, None
        if lo:
            return lo, None

    m = _RX_FROM.search(t)
    if m:
        val = _to_number(m.group(2))
        if val:
            return val, "from"

    m = _RX_TO.search(t)
    if m:
        val = _to_number(m.group(2))
        if val:
            return val, "to"

    m = _RX_SINGLE.search(t)
    if m:
        val = _to_number(m.group(1))
        if val:
            return val, None

    return None, None

def _find_kwork_price_node(soup: BeautifulSoup) -> str:
    """
    Пробуем найти цену в типичных местах карточки Kwork.
    Возвращаем текстовый сниппет (для последующего парсинга).
    """
    # часто встречающиеся классы/подписи
    candidates = []

    # По классам (где-то «price», «budget», «wants-card__price» и т.п.)
    for cls_re in (r"price", r"budget", r"wants.*price", r"offer__price", r"project.*price"):
        node = soup.find(attrs={"class": re.compile(cls_re, re.I)})
        if node:
            candidates.append(node.get_text(" ", strip=True))

    # По лейблу «Бюджет» / «Цена»
    for label in ("Бюджет", "Цена"):
        lbl = soup.find(string=re.compile(label, re.I))
        if lbl:
            # взять ближайший родитель/соседа
            parent_text = lbl.parent.get_text(" ", strip=True) if getattr(lbl, "parent", None) else str(lbl)
            candidates.append(parent_text)

    # Без лишнего шума — первый подходящий
    for c in candidates:
        c = (c or "").strip()
        if _RX_NEGOT.search(c) or _RX_SINGLE.search(c) or _RX_RANGE.search(c) or _RX_FROM.search(c) or _RX_TO.search(c):
            return c

    # fallback — пусто
    return ""

async def enrich_project(url: str, need_desc: bool = True, need_price: bool = True) -> Dict[str, Optional[str]]:
    """
    Возвращает только недостающее:
    { 'description': str|None, 'price': float|None, 'currency': 'RUB'|None, 'price_note': str|None }
    НИКОГДА не бросает исключения — максимум логирует и возвращает пустые поля.
    """
    logger.info(f"[enricher] enrich start url={url} need_desc={need_desc} need_price={need_price}")
    result: Dict[str, Optional[str]] = {"description": None, "price": None, "currency": None, "price_note": None}
    try:
        html = await _fetch_text(url)
        soup = BeautifulSoup(html, "html.parser")

        if need_desc:
            # приоритет: og:description → description → h1+lead → первые абзацы
            desc = _meta(soup, "og:description", "description")
            if not _clean(desc):
                h1 = soup.find("h1")
                lead = soup.find(class_=re.compile(r"(lead|description)", re.I))
                cand = " ".join([_clean(h1.get_text() if h1 else ""), _clean(lead.get_text() if lead else "")]).strip()
                if not cand:
                    p = soup.find("p")
                    cand = _clean(p.get_text()) if p else ""
                desc = cand
            result["description"] = _clean(desc) or None

        if need_price:
            # FIX: сначала «точечный» поиск узла с ценой
            price_snippet = _find_kwork_price_node(soup)
            if not price_snippet:
                # fallback: по всему тексту (может быть шумно, но надёжно)
                price_snippet = soup.get_text(separator=" ", strip=True)

            price, note = _guess_price_from_text(price_snippet)
            if note:
                result["price_note"] = note
            if price and price > 0:
                result["price"] = price  # type: ignore[assignment]
                result["currency"] = "RUB"

            # короткий дебаг (не спамит HTML)
            logger.info(f"[enricher] price_snippet={_norm_spaces(price_snippet)[:120]} → price={result['price']} note={result['price_note']}")

        logger.info(f"[enricher] enrich done: desc? {bool(result['description'])} price={result['price']}")
    except Exception as e:
        logger.warning(f"[enricher] enrich error: {e!r}")
    return result
