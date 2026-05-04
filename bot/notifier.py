#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import html
import re
import os
import json
import datetime as dt
from typing import Optional, Tuple, Any, List

import aiohttp
from loguru import logger

from bot.settings import TELEGRAM_BOT_TOKEN, REQUEST_TIMEOUT
from bot.services.metrics import (
    TG_SEND_TOTAL, TG_ERRORS_TOTAL, AI_CALLS_TOTAL, safe_inc, LIMIT_DENIED_TOTAL
)
from bot.ai_engine import AIEngine
from bot.database import get_or_create_user, can_user_send_cards, increment_user_stats_async

API_URL = "https://api.telegram.org/bot{token}/{method}"

SEND_WITHOUT_KEYWORDS = os.getenv("SEND_WITHOUT_KEYWORDS", "false").lower() in ("1", "true", "yes", "on")

def _escape_html(text: str) -> str:
    return html.escape(text or "", quote=False)

def _escape_attr(text: str) -> str:
    return html.escape(text or "", quote=True)

def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")

def _shorten(text: str, limit: int) -> str:
    text = text or ""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"

def _fmt_price(price: Optional[Any], currency: str | None = "RUB") -> str:
    if price is None:
        return ""
    if isinstance(price, str):
        s = price.strip()
        return s if s else ""
    try:
        p = float(price)
    except Exception:
        return ""
    if p <= 0:
        return ""
    s = f"{int(p):,}".replace(",", " ").replace(" ", "\u202F")
    curr = (currency or "RUB").upper()
    return f"{s} ₽" if curr in ("RUB", "RUR", "") else f"{s} {curr}"

def _parse_keywords(raw: Any) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        toks = [str(x) for x in raw]
    else:
        s = str(raw)
        toks = re.split(r"[,\n;]+|\s{2,}", s)
    out = []
    for t in toks:
        t = t.strip().lower()
        if len(t) >= 3:
            out.append(t)
    seen = set(); res = []
    for t in out:
        if t not in seen:
            seen.add(t); res.append(t)
    return res


_WB = r"(?<![а-яёА-ЯЁa-zA-Z0-9_])"
_WE = r"(?![а-яёА-ЯЁa-zA-Z0-9_])"


def _kw_match(text: str, kws: List[str]) -> bool:
    if not kws:
        return True
    hay = (text or "").lower()
    for k in kws:
        if " " in k:
            # phrase: just substring match, spaces already break words
            if k in hay:
                return True
        else:
            pattern = _WB + re.escape(k) + _WE
            if re.search(pattern, hay, re.UNICODE):
                return True
    return False


_ID_IN_URL = re.compile(r"/(?:projects|tasks|order|project)/(\d{3,})", re.I)


def _card_key_from_url(url: str, source: str = "") -> str:
    m = _ID_IN_URL.search(url or "")
    if m:
        return f"{source}_{m.group(1)}" if source else m.group(1)
    return str(abs(hash(url)))[:12]

def _strip_code_fences(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:\w+)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()

def _normalize_ai(ai_text: Optional[str], ai_plan: Optional[str]) -> Tuple[str, str]:
    draft_out = ""
    plan_out = ""

    at = _strip_code_fences(ai_text or "")
    ap = _strip_code_fences(ai_plan or "")

    parsed_from_text = False
    if at:
        try:
            obj = json.loads(at)
            if isinstance(obj, dict):
                draft_out = str(obj.get("draft") or "").strip()
                plan_val = obj.get("plan")
                if isinstance(plan_val, list):
                    plan_out = "\n".join(f"{i+1}. {str(x).strip()}" for i, x in enumerate(plan_val) if str(x).strip())
                else:
                    plan_out = str(plan_val or "").strip()
                parsed_from_text = True
        except (json.JSONDecodeError, ValueError):
            pass  # не JSON — попробуем другой формат ниже

    if not parsed_from_text:
        draft_out = at

    if ap and not plan_out:
        try:
            obj = json.loads(ap)
            if isinstance(obj, list):
                plan_out = "\n".join(f"{i+1}. {str(x).strip()}" for i, x in enumerate(obj) if str(x).strip())
            elif isinstance(obj, dict) and "plan" in obj:
                v = obj["plan"]
                if isinstance(v, list):
                    plan_out = "\n".join(f"{i+1}. {str(x).strip()}" for i, x in enumerate(v) if str(x).strip())
                else:
                    plan_out = str(v or "").strip()
            else:
                plan_out = str(obj).strip()
        except Exception:
            lines = [ln.strip("-• \t") for ln in ap.splitlines() if ln.strip()]
            if len(lines) >= 2:
                plan_out = "\n".join(f"{i+1}. {ln}" for i, ln in enumerate(lines))
            else:
                plan_out = ap

    if not plan_out and ap.startswith("[") and ap.endswith("]"):
        try:
            arr = json.loads(ap.replace("'", '"'))
            if isinstance(arr, list):
                plan_out = "\n".join(f"{i+1}. {str(x).strip()}" for i, x in enumerate(arr) if str(x).strip())
        except (json.JSONDecodeError, ValueError):
            pass  # невалидный JSON-массив — оставляем plan_out пустым

    draft_out = (draft_out or "").strip()
    plan_out = (plan_out or "").strip()
    plan_out = re.sub(r"^\s*(\d+)\)\s*", r"\1. ", plan_out, flags=re.M)

    return draft_out, plan_out

# Источник → (иконка, полное название)
_SOURCE_META = {
    "kwork":         ("🟣", "Kwork.ru"),
    "fl":            ("🔵", "FL.ru"),
    "habr":          ("🔴", "Habr Freelance"),
    "freelancehunt": ("🟠", "Freelancehunt"),
}


def _human_time(published_at: Any) -> str:
    """Переводит datetime/строку в читаемое время на русском."""
    if not published_at:
        return ""
    ts: Optional[dt.datetime] = None
    if isinstance(published_at, dt.datetime):
        ts = published_at
    elif isinstance(published_at, str):
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                ts = dt.datetime.strptime(published_at[:19], fmt)
                break
            except ValueError:
                pass
    if ts is None:
        # не смогли распарсить — показываем как есть, до 16 символов
        return str(published_at)[:16]
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    diff = int((dt.datetime.now(dt.timezone.utc) - ts).total_seconds())
    if diff < 0:
        return ""
    if diff < 60:
        return "только что"
    if diff < 3600:
        return f"{diff // 60} мин. назад"
    if diff < 86400:
        return f"{diff // 3600} ч. назад"
    days = diff // 86400
    if days == 1:
        return "вчера"
    if days < 7:
        return f"{days} дн. назад"
    return ts.strftime("%d.%m.%Y")


def format_card_message(card: dict) -> str:
    title = _escape_html((_strip_tags(card.get("title") or "")).strip() or "Без названия")
    src   = (card.get("source") or "").strip().lower()
    url   = str(card.get("url") or "").strip()

    if not src and url:
        _u = url.lower()
        if "kwork" in _u:
            src = "kwork"
        elif "fl.ru" in _u:
            src = "fl"

    icon, source_full = _SOURCE_META.get(src, ("📋", card.get("source") or "Биржа"))
    time_str = _human_time(card.get("published_at"))

    # Строка 1: источник + время
    header = f"{icon} <b>{_escape_html(source_full)}</b>"
    if time_str:
        header += f"  ·  🕐 {_escape_html(time_str)}"

    # Строка 2: заголовок проекта
    title_line = f"📌 <b>{title}</b>"

    # Строка 3: бюджет + категория
    price_str = _fmt_price(card.get("price"), card.get("currency")) if "price" in card else ""
    cat = _escape_html((card.get("category") or "").strip())
    price_part = f"💰 <b>{_escape_html(price_str)}</b>" if price_str else "💰 договорная"
    meta = price_part + (f"  ·  🗂 {cat}" if cat else "")

    parts: List[str] = [header, title_line, meta]

    # Описание — без лишнего label
    desc = _shorten(_strip_tags(card.get("description") or ""), 400)
    if desc:
        parts.append("")
        parts.append(_escape_html(desc))

    return "\n".join(parts)

class Notifier:
    def __init__(self, token: Optional[str] = None, session: Optional[aiohttp.ClientSession] = None):
        self.token = token or TELEGRAM_BOT_TOKEN
        self._session = session

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session and not self._session.closed:
            return self._session
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _call_telegram(self, method: str, data: dict, retry: int = 3) -> Tuple[bool, Optional[int]]:
        url = API_URL.format(token=self.token, method=method)
        attempt = 0
        last_exc: Optional[Exception] = None
        while attempt <= max(0, retry):
            attempt += 1
            try:
                session = await self._ensure_session()
                async with session.post(url, json=data) as resp:
                    js = await resp.json(content_type=None)
                    if resp.status == 200 and js.get("ok"):
                        msg = js.get("result", {})
                        return True, int(msg.get("message_id") or 0) or None
                    logger.warning(f"TG {method} failed: {resp.status} {js}")
            except Exception as e:
                last_exc = e
                logger.warning(f"TG {method} exception: {e!r}")
            await asyncio.sleep(min(5, 0.5 * attempt))
        logger.error(f"TG {method} failed after retries. last_exc={last_exc!r}")
        safe_inc(TG_ERRORS_TOTAL, {"place": method})
        return False, None

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
        reply_to_message_id: Optional[int] = None,
        retry: int = 3,
        reply_markup: Optional[dict] = None,
    ) -> Optional[int]:
        if int(chat_id) > 0:
            try:
                allow = await can_user_send_cards(int(chat_id))
                if not allow:
                    safe_inc(LIMIT_DENIED_TOTAL, {"reason": "daily"})
                    logger.info(f"[LIMIT] skip send to tg_id={int(chat_id)}")
                    return None
            except Exception as e:
                logger.error(f"[LIMIT] check failed: {e}")
                return None

        data = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
            "parse_mode": parse_mode,
        }
        if reply_to_message_id:
            data["reply_to_message_id"] = reply_to_message_id
        if reply_markup:
            if not isinstance(reply_markup, dict):
                logger.warning(f"Invalid reply_markup type={type(reply_markup)}; ignored")
            else:
                data["reply_markup"] = reply_markup

        ok, msg_id = await self._call_telegram("sendMessage", data, retry=retry)
        safe_inc(TG_SEND_TOTAL, {"type": "text", "result": "ok" if ok else "fail"})

        if ok and int(chat_id) > 0:
            try:
                await increment_user_stats_async(int(chat_id), "cards_sent")
            except Exception as e:
                logger.warning(f"[LIMIT] inc failed: {e}")

        return msg_id

    async def send_project_card(
        self,
        chat_id: int,
        *,
        title: str,
        price: Optional[Any],
        url: Optional[str],
        description: Optional[str],
        category: Optional[str] = None,
        source: Optional[str] = None,
        ai_text: Optional[str] = None,
        ai_plan: Optional[str] = None,
        user_telegram_id: Optional[int] = None,
        auto_ai_if_pro: bool = False,
        reply_to_message_id: Optional[int] = None,
        retry: int = 3,
        reply_markup: Optional[dict] = None,
        currency: Optional[str] = "RUB",
        published_at: Optional[str] = None,
    ) -> Optional[int]:
        u = None
        try:
            u = await get_or_create_user(int(user_telegram_id or chat_id))
        except Exception as e:
            logger.debug(f"get_or_create_user failed: {e}")

        # Проверяем включена ли эта биржа у пользователя
        if u and source:
            _src_col = {
                "kwork": "kwork_enabled",
                "fl": "fl_enabled",
                "habr": "habr_enabled",
                "freelancehunt": "freelancehunt_enabled",
            }.get((source or "").lower())
            if _src_col:
                # None → колонка ещё не мигрирована → не блокируем
                _enabled = u.get(_src_col)
                if _enabled is False:
                    logger.info(f"[EXCHANGE] skip (disabled) tg_id={chat_id} source={source}")
                    return None

        kws = _parse_keywords((u or {}).get("keywords"))
        if not SEND_WITHOUT_KEYWORDS:
            hay_for_match = " ".join([
                _strip_tags(title or ""),
                _strip_tags(description or ""),
                str(category or "")
            ])
            if not kws:
                logger.info(f"[KW] skip (no user keywords) tg_id={chat_id} title={_shorten(title,60)}")
                return None
            if not _kw_match(hay_for_match, kws):
                logger.info(f"[KW] skip (no match) tg_id={chat_id} kws={kws} title={_shorten(title,60)}")
                return None

        # авто-AI для PRO — как было
        if auto_ai_if_pro and user_telegram_id and (not ai_text and not ai_plan):
            try:
                is_premium = False
                if u:
                    now_utc = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
                    pu = u.get("premium_until")
                    tu = u.get("trial_until")
                    if pu and isinstance(pu, dt.datetime):
                        is_premium = pu > now_utc
                    if not is_premium and tu and isinstance(tu, dt.datetime):
                        is_premium = tu > now_utc
                if is_premium:
                    try:
                        _ = AI_CALLS_TOTAL
                        ai = AIEngine()
                        ai_text, ai_plan = await ai.generate_reply_and_plan(
                            title=_strip_tags(title or ""),
                            description=_strip_tags(description or "")
                        )
                    except Exception as e:
                        logger.warning(f"AI generate failed: {e}")
            except Exception as e:
                logger.warning(f"auto_ai_if_pro check failed: {e}")

        norm_draft, norm_plan = _normalize_ai(ai_text, ai_plan)

        card = {
            "title": title,
            "url": url,
            "source": source,
            "price": price,
            "currency": currency,
            "category": category,
            "description": description,
            "published_at": published_at,
        }
        text = format_card_message(card)

        extra_parts: List[str] = []
        if norm_draft:
            extra_parts += ["", "🤖 <b>AI-черновик:</b>", f"<pre>{_escape_html(norm_draft)}</pre>"]
        if norm_plan:
            extra_parts += ["", "📋 <b>План:</b>", f"<pre>{_escape_html(norm_plan)}</pre>"]
        if extra_parts:
            text = text + "\n\n" + "\n".join(extra_parts)

        kb = reply_markup
        card_key = ""
        if not kb and url:
            card_key = _card_key_from_url(url, source)
            try:
                from bot.database import save_card_cache
                await save_card_cache(card_key, card)
                logger.debug(f"[CACHE] saved card_key={card_key}")
            except Exception as e:
                logger.error(f"[CACHE] save_card_cache failed card_key={card_key}: {e}")
            kb = {
                "inline_keyboard": [
                    [{"text": "🔗 Открыть проект", "url": url}],
                    [
                        {"text": "✍️ Отклик", "callback_data": f"pitch_{card_key}"},
                        {"text": "❌ Пропустить", "callback_data": f"skip_{card_key}"},
                    ],
                ]
            }

        msg_id = await self.send_message(
            chat_id,
            text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_to_message_id=reply_to_message_id,
            retry=retry,
            reply_markup=kb,
        )

        return msg_id
