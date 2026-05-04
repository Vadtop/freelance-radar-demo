#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Парсер HTML-каталогов FL.ru и Kwork + RSS-фиды.
Актуален на 04 августа 2025 г.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urljoin
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup
from loguru import logger

from bot.fetcher import Project
from bot.settings import FL_RSS_URL, KWORK_RSS_URL

try:
    import feedparser
except ImportError:
    feedparser = None

# ————————————————————————————————————————————————————————————————————————————————
#    REGEX’Ы
# ————————————————————————————————————————————————————————————————————————————————

# Kwork: ссылки вида /projects/123-slug или /projects/123-slug/ (и абсолютные URL)
_HREF_RE_KWORK = re.compile(r"""
    ^(?:https?://(?:www\.)?kwork\.ru)?   # optional domain
    /projects/
    \d+                                  # ID
    (?:-[^/?#]+)?                        # optional slug
    (?:/|$)                              # trailing slash или конец
""", re.X)

# FL.ru: ссылки вида /projects/123 или /projects/123/slug.html и с доменом
_HREF_RE_FL = re.compile(r"""
    ^(?:https?://(?:www\.)?fl\.ru)?      # optional domain
    /projects/
    \d+                                  # ID
    (?:/[^/?#]+)?                        # optional slug
    /?                                   # optional slash
    (?:[?#].*)?$                         # optional query or fragment
""", re.X)

# Встроенный JSON на Kwork
_WANTS_BLOCKS = [
    re.compile(r'wants\s*:\s*\[(.*?)\]\s*,', re.S),
    re.compile(r'"wants"\s*:\s*\[(.*?)\]\s*,', re.S),
]

# FIX: расширенные паттерны цен
_RX_RANGE = re.compile(r"(\d[\d\s]{0,12})\s*[–—-]\s*(\d[\d\s]{0,12})\s*(?:₽|руб)", re.I)
_RX_FROM  = re.compile(r"(?:^|\D)(от)\s*(\d[\d\s]{0,12})\s*(?:₽|руб)", re.I)
_RX_TO    = re.compile(r"(?:^|\D)(до)\s*(\d[\d\s]{0,12})\s*(?:₽|руб)", re.I)
_RX_SINGLE= re.compile(r"(\d[\d\s]{0,12})\s*(?:₽|руб)", re.I)
_RX_NEGOT = re.compile(r"договорн|по\s+договор", re.I)

_PRICE_RE_LEGACY = re.compile(r"(\d[\d\s]*)\s*₽")
_ID_RE    = re.compile(r"(\d+)")
_TS_FORMATS = ("%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S")

DUMP_DIR = os.getenv("DUMP_DIR", "./data")
_seen_dt_parse_errors: set[str] = set()

_NBSP_RE = re.compile(r"[\u00A0\u202F\u2007\u2060]")

def _norm_spaces(s: str) -> str:
    return _NBSP_RE.sub(" ", (s or "")).strip()

def _dump_html(name: str, html: str) -> None:
    try:
        os.makedirs(DUMP_DIR, exist_ok=True)
        p = os.path.join(DUMP_DIR, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(html)
        logger.warning("HTML dumped → {}", p)
    except Exception as e:
        logger.warning("Can't dump html {}: {}", name, e)

def _to_number(s: str) -> Optional[float]:
    s = _norm_spaces(s)
    s = re.sub(r"[^\d]", "", s or "")
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None

def _parse_price(text: str) -> float:
    """
    FIX: поддержка диапазонов/«от»/«до»/«договорной».
    Возвращаем «верхнюю границу» для диапазона, иначе найденную сумму.
    """
    t = _norm_spaces(text or "")

    if _RX_NEGOT.search(t):
        return 0.0

    m = _RX_RANGE.search(t)
    if m:
        hi = _to_number(m.group(2))
        lo = _to_number(m.group(1))
        if hi: return float(hi)
        if lo: return float(lo)
        return 0.0

    m = _RX_FROM.search(t)
    if m:
        val = _to_number(m.group(2))
        return float(val) if val else 0.0

    m = _RX_TO.search(t)
    if m:
        val = _to_number(m.group(2))
        return float(val) if val else 0.0

    m = _RX_SINGLE.search(t)
    if m:
        val = _to_number(m.group(1))
        return float(val) if val else 0.0

    # legacy fallback, если что-то экзотическое
    m = _PRICE_RE_LEGACY.search(t)
    if m:
        try:
            return float(m.group(1).replace(" ", ""))
        except ValueError:
            pass
    return 0.0

def _parse_int(text: str) -> Optional[int]:
    m = _ID_RE.search(text or "")
    return int(m.group(1)) if m else None

def _safe_dt(text: str) -> Optional[dt.datetime]:
    if not text:
        return None
    try:
        return parsedate_to_datetime(text)
    except Exception:
        pass
    for fmt in _TS_FORMATS:
        try:
            return dt.datetime.strptime(text.strip(), fmt)
        except Exception:
            continue
    if text not in _seen_dt_parse_errors:
        logger.debug("Unparsed date: ‘{}’", text)
        _seen_dt_parse_errors.add(text)
    return None

def _dedupe(seq: Iterable[str]) -> List[str]:
    seen, out = set(), []
    for s in seq:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out

def _is_challenge(html: str) -> bool:
    return any(marker in html for marker in (
        "Checking your browser", "ddos-guard",
    ))

# ————————————————————————————————————————————————————————————————————————————————
#    ПАРСИНГ СПИСКА ПРОЕКТОВ
# ————————————————————————————————————————————————————————————————————————————————

def _extract_kwork_urls_from_embedded_json(html: str, base: str) -> List[str]:
    if not html:
        return []
    block = None
    for rx in _WANTS_BLOCKS:
        m = rx.search(html)
        if m:
            block = m.group(1)
            break
    if not block:
        return []

    ids = re.findall(r'["\']?id["\']?\s*:\s*(\d+)', block)
    if not ids:
        return []

    urls, seen = [], set()
    for sid in ids:
        try:
            pid = int(sid)
        except Exception:
            continue
        href = f"/projects/{pid}"
        full = href if href.startswith("http") else urljoin(base, href)
        if full not in seen:
            seen.add(full)
            urls.append(full)
    return urls

def parse_multiple_projects_kwork(html: str, base: str) -> List[str]:
    if not html or _is_challenge(html):
        logger.warning("Kwork HTML looks like challenge/empty")
        if html:
            _dump_html("dump_kwork.html", html)
        return []

    soup  = BeautifulSoup(html, "lxml")
    hrefs = [a["href"] for a in soup.find_all("a", href=True)]
    urls  = [h if h.startswith("http") else urljoin(base, h)
             for h in hrefs if _HREF_RE_KWORK.match(h)]
    urls  = _dedupe(urls)

    if not urls:
        urls = _extract_kwork_urls_from_embedded_json(html, base)

    urls.sort(key=lambda u: _parse_int(u) or 0)

    logger.info("Kwork parser found {} project links", len(urls))
    if not urls:
        _dump_html("dump_kwork_no_links.html", html)
    return urls

def parse_multiple_projects(html: str, base: str) -> List[str]:
    if not html or _is_challenge(html):
        logger.warning("FL HTML looks like challenge/empty")
        if html:
            _dump_html("dump_fl.html", html)
        return []

    soup  = BeautifulSoup(html, "lxml")
    hrefs = [a["href"] for a in soup.find_all("a", href=True)]
    urls  = [urljoin(base, h) for h in hrefs if _HREF_RE_FL.match(h)]
    urls  = _dedupe(urls)

    logger.info("FL parser found {} project links", len(urls))
    if not urls:
        _dump_html("dump_fl_no_links.html", html)
    return urls

# ————————————————————————————————————————————————————————————————————————————————
#    ПАРСИНГ ОДНОГО ПРОЕКТА
# ————————————————————————————————————————————————————————————————————————————————

def parse_single_project(html: str, url: str) -> Optional[Project]:
    if not html or _is_challenge(html):
        logger.warning("Single project HTML looks like challenge/empty → {}", url)
        if html:
            _dump_html(f"dump_single_{_parse_int(url) or 'x'}.html", html)
        return None

    soup = BeautifulSoup(html, "lxml")

    # ── TITLE ──
    title_tag = soup.find(["h1", "h2"])
    title = title_tag.get_text(strip=True) if title_tag else ""

    if not title or title.lower() in {"", "без названия"}:
        og_title = soup.find("meta", attrs={"property": "og:title"}) or soup.find("meta", attrs={"name": "twitter:title"})
        if og_title and og_title.get("content"):
            title = og_title["content"].strip()

    if not title:
        page_title = soup.find("title")
        if page_title:
            title = re.sub(r"\s*[-–—]\s*Kwork.*$", "", page_title.get_text(" ", strip=True)).strip()

    if not title:
        title = "Без названия"

    # ── DESCRIPTION ──
    desc_block  = soup.find("div", class_=re.compile("(?i)(desc|description|content|text|brief|details)"))
    description = desc_block.get_text("\n", strip=True) if desc_block else ""

    if not description:
        og_desc = (
            soup.find("meta", attrs={"property": "og:description"})
            or soup.find("meta", attrs={"name": "description"})
            or soup.find("meta", attrs={"name": "twitter:description"})
        )
        if og_desc and og_desc.get("content"):
            description = og_desc["content"].strip()

    if description:
        description = re.sub(r"\s*Kwork\s*—?\s*фриланс[- ]маркетплейс.*$", "", description, flags=re.I).strip()
        description = re.sub(r"^\s*Фриланс\s+маркетплейс\s*$", "", description, flags=re.I).strip()

    # ── PRICE ──
    # FIX: используем расширенный парсер
    price_val = _parse_price(soup.get_text(" ", strip=True))

    # ── ID ──
    pid = _parse_int(url) or _parse_int(title)
    if not pid:
        any_id = soup.find(attrs={"data-id": True})
        if any_id:
            try:
                pid = int(any_id.get("data-id"))
            except Exception:
                pid = None
    if not pid:
        logger.warning("Can't parse project id → {}", url)
        return None

    # ── CREATED_AT ──
    created_at = None
    time_tag = soup.find("time")
    if time_tag and time_tag.get("datetime"):
        try:
            created_at = dt.datetime.fromisoformat(
                time_tag["datetime"].replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except Exception:
            created_at = None

    if not created_at:
        for key in ("article:published_time", "og:updated_time", "article:modified_time"):
            mt = soup.find("meta", attrs={"property": key})
            if mt and mt.get("content"):
                try:
                    created_at = dt.datetime.fromisoformat(
                        mt["content"].replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                    break
                except Exception:
                    pass
    if not created_at and time_tag:
        created_at = _safe_dt(time_tag.get_text(strip=True))

    return Project(
        id=pid,
        title=title,
        description=description,
        price=price_val,
        url=url,
        status=None,
        bid=None,
        auto_capable=0,
        source=("kwork" if "kwork" in url else "fl"),
        created_at=created_at,
    )

# ————————————————————————————————————————————————————————————————————————————————
#    RSS
# ————————————————————————————————————————————————————————————————————————————————
def _rss_from_string(xml: str, source: str) -> Tuple[List[str], List[Project]]:
    if feedparser is None:
        logger.error("feedparser not installed → RSS disabled")
        return [], []

    d = feedparser.parse(xml)
    urls, projects = [], []
    for e in d.entries:
        link = e.link
        pid  = _parse_int(link) or _parse_int(e.get("id", ""))
        if not pid:
            continue
        title = e.title
        desc  = e.get("summary", "")
        price = _parse_price(f"{title} {desc}")
        published = _safe_dt(e.get("published", "")) or dt.datetime.utcnow()

        urls.append(link)
        projects.append(Project(
            id=pid,
            title=title,
            description=desc,
            price=price,
            url=link,
            status=None,
            bid=None,
            auto_capable=0,
            source=source,
            created_at=published,
        ))
    return urls, projects

async def parse_rss_kwork(fetcher) -> List[str]:
    if not KWORK_RSS_URL:
        return []
    try:
        xml = await fetcher.fetch_html(KWORK_RSS_URL, use_browser=False, timeout_ms=15000)
        if not xml:
            return []
        urls, _ = _rss_from_string(xml, "kwork")
        logger.info("Kwork RSS yielded {} urls", len(urls))
        return urls
    except Exception as e:
        logger.error("RSS Kwork error: {}", e)
        return []

async def parse_rss_fl(fetcher) -> List[str]:
    if not FL_RSS_URL:
        return []
    try:
        xml = await fetcher.fetch_html(FL_RSS_URL, use_browser=False, timeout_ms=15000)
        if not xml:
            logger.warning("FL RSS didn't return data")
            return []
        urls, _ = _rss_from_string(xml, "fl")
        logger.info("FL RSS yielded {} urls", len(urls))
        return urls
    except Exception as e:
        logger.error("FL RSS error: {} ({})", e, type(e).__name__)
    return []
