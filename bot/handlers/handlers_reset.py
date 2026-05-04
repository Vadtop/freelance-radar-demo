#!/usr/bin/env python3
from __future__ import annotations

import os

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from loguru import logger

from bot.storage import save_last_fl_project, save_last_project

router = Router(name="reset")

_ADMIN_ENV = os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS = [int(x) for x in _ADMIN_ENV.split(",") if x.strip().isdigit()]
if not ADMIN_IDS:
    _fb = os.getenv("ADMIN_CHAT_ID", "0").strip()
    if _fb.isdigit() and int(_fb):
        ADMIN_IDS = [int(_fb)]


def _is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


@router.message(Command("reset_fl"))
async def cmd_reset_fl(msg: Message) -> None:
    if not _is_admin(msg.from_user.id):
        return
    save_last_fl_project(0)
    logger.info("FL last_id reset to 0 by admin {}", msg.from_user.id)
    await msg.answer("✅ FL last_id сброшен в 0. На следующем тике парсера придут все текущие проекты из RSS.")


@router.message(Command("reset_kwork"))
async def cmd_reset_kwork(msg: Message) -> None:
    if not _is_admin(msg.from_user.id):
        return
    try:
        save_last_project(0)
    except Exception as e:
        await msg.answer(f"❌ Ошибка: {e}")
        return
    logger.info("Kwork last_id reset to 0 by admin {}", msg.from_user.id)
    await msg.answer("✅ Kwork last_id сброшен в 0.")
