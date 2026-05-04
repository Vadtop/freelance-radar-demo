#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot.fetcher  •  httpx-only (no Playwright/FlareSolverr)
"""

from __future__ import annotations

import asyncio
import json
import os
import random
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, NamedTuple, Optional

import httpx
from loguru import logger

from bot.settings import (
    HTTPX_TIMEOUT,
    PROXY_STRING,
    PROXY_LIST,
    CLEARANCE_FILE,
)

MAX_HTTPX_BYTES = int(os.getenv("MAX_HTTPX_BYTES", "1500000"))

try:
    from bot.services.metrics import FETCHER_ERRORS_TOTAL
except Exception:
    from prometheus_client import Counter
    FETCHER_ERRORS_TOTAL = Counter("fetcher_errors_total", "Fetcher errors", ["stage", "reason"])

REAL_UA: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
]


class _Proxy:
    def __init__(self, url: str):
        self.url = url
        self.failures = 0
        self.ban_until: datetime | None = None

    def mark(self, ok: bool) -> None:
        if ok:
            self.failures = 0
            self.ban_until = None
        else:
            self.failures += 1
            if self.failures >= 3:
                self.ban_until = datetime.now() + timedelta(minutes=10)

    def ready(self) -> bool:
        return not self.ban_until or self.ban_until <= datetime.now()


class _ProxyPool:
    def __init__(self, urls: List[str]):
        if not urls and PROXY_STRING:
            urls = [PROXY_STRING]
        self._p: deque[_Proxy] = deque(_Proxy(u) for u in urls)

    def acquire(self) -> Optional[_Proxy]:
        if not self._p:
            return None
        for _ in range(len(self._p)):
            pr = random.choice(list(self._p))
            if pr.ready():
                return pr
        return None

    def report(self, pr: _Proxy | None, ok: bool) -> None:
        if pr:
            pr.mark(ok)


class Project(NamedTuple):
    id: int
    title: str
    description: str | None
    price: float
    url: str
    status: str | None
    bid: str | None
    auto_capable: int
    source: str
    created_at: float | None = None


class ImprovedFetcher:
    def __init__(self, *, proxy_url: str | None = None, offline_mode: bool = False):
        proxies = PROXY_LIST or ([proxy_url] if proxy_url else ([PROXY_STRING] if PROXY_STRING else []))
        self._pool = _ProxyPool(proxies)
        self._offline = bool(offline_mode)
        self._cookie_path = Path(CLEARANCE_FILE)
        self._ua = random.choice(REAL_UA)

    async def fetch_html(self, url: str, *, use_browser: bool = False, timeout_ms: int = 60_000) -> str | None:
        if self._offline:
            return None
        pr = self._pool.acquire()
        html = await self._httpx_get(url, pr)
        if html and not self._looks_challenge(html):
            return html
        return None

    async def fetch_json(self, url: str, *, timeout_ms: int = 20_000) -> dict | None:
        pr = self._pool.acquire()
        try:
            async with httpx.AsyncClient(
                timeout=timeout_ms / 1000,
                follow_redirects=True,
                proxies=(pr.url if pr else None),
            ) as cli:
                r = await cli.get(url, headers={"User-Agent": self._ua, "Cookie": self._cookie_header()})
                if r.status_code == 200:
                    return r.json()
        except Exception as exc:
            logger.debug("get_json httpx failed for {}: {}", url, exc)
        return None

    async def close(self) -> None:
        pass

    def _cookie_header(self) -> str:
        if not self._cookie_path.exists():
            return ""
        try:
            jar = json.loads(self._cookie_path.read_text("utf-8"))
            return "; ".join(f"{c['name']}={c['value']}" for c in jar)
        except Exception:
            return ""

    async def _httpx_get(self, url: str, pr: _Proxy | None) -> str | None:
        try:
            async with httpx.AsyncClient(
                timeout=HTTPX_TIMEOUT,
                follow_redirects=True,
                proxies=(pr.url if pr else None),
                headers={"User-Agent": self._ua, "Cookie": self._cookie_header()},
            ) as cli:
                async with cli.stream("GET", url) as r:
                    if r.status_code != 200:
                        self._pool.report(pr, False)
                        return None
                    total = 0
                    chunks = []
                    async for chunk in r.aiter_bytes():
                        chunks.append(chunk)
                        total += len(chunk)
                        if total >= MAX_HTTPX_BYTES:
                            break
                    text = b"".join(chunks).decode(r.encoding or "utf-8", errors="ignore")
                    ok = not self._looks_challenge(text)
                    self._pool.report(pr, ok)
                    return text if ok else None
        except Exception:
            self._pool.report(pr, False)
            FETCHER_ERRORS_TOTAL.labels("httpx", "error").inc()
            return None

    @staticmethod
    def _looks_challenge(html: str) -> bool:
        return any(
            tok in (html or "")
            for tok in (
                "Checking your browser",
                "__cf_bm",
                "/cdn-cgi/challenge-platform",
                "cf-chl-turnstile",
                "turnstile.cloudflare.com",
                "ddos-guard",
                "/account/login",
            )
        ) or len(html or "") < 5_000


Fetcher = ImprovedFetcher
