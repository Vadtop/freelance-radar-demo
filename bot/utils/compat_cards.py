# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Optional, Dict, Any

from loguru import logger

from bot.notifier import Notifier
from bot.utils.detail_enricher import enrich_project


def _is_empty_desc(val: Any) -> bool:
    return not str(val or "").strip()


def _need_price(val: Any) -> bool:
    try:
        if val is None:
            return True
        f = float(val)
        return f <= 0
    except Exception:
        return True


def _as_dict(obj: Any) -> Dict[str, Any]:
    """
    Нормализуем входящую «карточку» к обычному dict:
    - dict -> как есть
    - namedtuple/dataclass -> _asdict()
    - произвольный объект -> vars(obj) без приватных полей
    - иначе -> {}
    """
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "_asdict"):
        try:
            d = obj._asdict()
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        try:
            return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
        except Exception:
            pass
    return {}


async def send_card_compat(
    notifier: Notifier,
    chat_id: int,
    card: Dict[str, Any] | Any,
    *,
    auto_ai_if_pro: bool = False,
    reply_to_message_id: Optional[int] = None
) -> Optional[int]:
    """
    Универсальная отправка «карточки проекта». Не трогает БД напрямую,
    не исполняет «строки ответов» от Postgres, только:
    - при необходимости обогащает через detail_enricher;
    - собирает текст;
    - зовёт Notifier.send_project_card().
    """
    try:
        data = _as_dict(card)

        title = (data.get("title") or "").strip() or "Без названия"
        url = (data.get("url") or "").strip()
        source = (data.get("source") or "").strip() or ("kwork" if "kwork" in url.lower() else "")
        desc = data.get("description")
        price = data.get("price")
        currency = (data.get("currency") or "RUB").strip() or "RUB"
        category = (data.get("category") or "").strip()

        ai_text = data.get("ai_text")
        ai_plan = data.get("ai_plan")

        need_desc = _is_empty_desc(desc) or source == "fl"
        need_price_flag = _need_price(price)

        if url and (need_desc or need_price_flag):
            enrich = await enrich_project(url, need_desc=need_desc, need_price=need_price_flag)
            if need_desc and enrich.get("description"):
                desc = enrich["description"]
            if need_price_flag and enrich.get("price") is not None:
                price = enrich["price"]
                currency = enrich.get("currency") or currency

        msg_id = await notifier.send_project_card(
            chat_id,
            title=title,
            price=price,
            url=url or None,
            description=desc,
            category=category or None,
            source=source or None,
            ai_text=ai_text,
            ai_plan=ai_plan,
            user_telegram_id=int(chat_id),
            auto_ai_if_pro=auto_ai_if_pro,
            reply_to_message_id=reply_to_message_id,
            currency=currency or "RUB",
        )
        return msg_id
    except Exception as e:
        logger.error(f"send_card failed: {e} (type={type(card).__name__})")
        return None
