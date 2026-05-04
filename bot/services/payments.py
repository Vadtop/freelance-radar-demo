#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import uuid
from typing import Dict, Any, Tuple
from loguru import logger

class PaymentService:
    """
    Простой mock-провайдер платежей.
    Для реальной интеграции (ЮKassa/Stripe) сюда встанет создание платежа и возврат invoice_url.
    """

    def __init__(self, provider: str = "mock"):
        self.provider = provider

    def create_payment(self, user_id: int, amount_rub: float, provider: str | None = None) -> Tuple[str, str]:
        """
        Возвращает (payment_id, invoice_url).
        """
        pid = str(uuid.uuid4())
        url = f"https://pay.example.mock/invoice/{pid}?amount={int(amount_rub)}&u={user_id}"
        logger.info(f"[payments] create_payment provider={provider or self.provider} uid={user_id} amount={amount_rub} id={pid}")
        return pid, url

    async def verify_payment(self, payment_id: str) -> bool:
        logger.info(f"[payments] verify payment_id={payment_id} (mock true)")
        return True

    async def handle_webhook(self, data: Dict[str, Any]) -> bool:
        logger.info(f"[payments] webhook data={data}")
        return True