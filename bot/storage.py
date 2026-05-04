#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
from loguru import logger

from bot.settings import (
    LAST_KWORK_PROJECT_FILE,
    LAST_FL_PROJECT_FILE,
    LAST_HABR_PROJECT_FILE,
    LAST_FREELANCEHUNT_PROJECT_FILE,
    LAST_FREELANCERU_PROJECT_FILE,
    LAST_WEBLANCER_PROJECT_FILE,
)


def _read_int(path: str) -> int | None:
    if not os.path.exists(path):
        logger.info("📁 Файл %s не существует, начинаем с 0", path)
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("last_id", 0))
    except Exception as e:
        logger.warning("Не удалось прочитать %s: %s", path, e)
        return None


def _read_mtime(path: str) -> float:
    """Return saved_at timestamp from JSON or file mtime as fallback."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return float(data.get("saved_at", os.path.getmtime(path)))
    except Exception:
        try:
            return os.path.getmtime(path)
        except Exception:
            return 0.0


def _write_int(path: str, value: int) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"last_id": value, "saved_at": time.time()}, f)
    except Exception as e:
        logger.warning("Не удалось записать %s: %s", path, e)


def is_last_id_stale(path: str, max_age_seconds: int = 3600) -> bool:
    """Return True if the saved last_id file is older than max_age_seconds."""
    if not os.path.exists(path):
        return False
    age = time.time() - _read_mtime(path)
    return age > max_age_seconds


def load_last_project() -> int | None:
    """
    Для Kwork‑парсера: возвращает последний сохранённый ID проекта или None.
    """
    return _read_int(LAST_KWORK_PROJECT_FILE)


def save_last_project(project_id: int) -> None:
    """
    Для Kwork‑парсера: сохраняет последний обработанный ID проекта.
    """
    _write_int(LAST_KWORK_PROJECT_FILE, project_id)


def load_last_fl_project() -> int | None:
    """
    Для FL‑парсера: возвращает последний сохранённый ID проекта или None.
    """
    return _read_int(LAST_FL_PROJECT_FILE)


def save_last_fl_project(project_id: int) -> None:
    """
    Для FL‑парсера: сохраняет последний обработанный ID проекта.
    """
    _write_int(LAST_FL_PROJECT_FILE, project_id)


def load_last_habr_project() -> int | None:
    """
    Для Habr-парсера: возвращает последний сохранённый ID задачи или None.
    """
    return _read_int(LAST_HABR_PROJECT_FILE)


def save_last_habr_project(task_id: int) -> None:
    """
    Для Habr-парсера: сохраняет последний обработанный ID задачи.
    """
    _write_int(LAST_HABR_PROJECT_FILE, task_id)


def load_last_freelancehunt_project() -> int | None:
    """
    Для Freelancehunt-парсера: возвращает последний сохранённый ID проекта или None.
    """
    return _read_int(LAST_FREELANCEHUNT_PROJECT_FILE)


def save_last_freelancehunt_project(project_id: int) -> None:
    """
    Для Freelancehunt-парсера: сохраняет последний обработанный ID проекта.
    """
    _write_int(LAST_FREELANCEHUNT_PROJECT_FILE, project_id)


def load_last_freelanceru_project() -> int | None:
    return _read_int(LAST_FREELANCERU_PROJECT_FILE)


def save_last_freelanceru_project(project_id: int) -> None:
    _write_int(LAST_FREELANCERU_PROJECT_FILE, project_id)


def load_last_weblancer_project() -> int | None:
    return _read_int(LAST_WEBLANCER_PROJECT_FILE)


def save_last_weblancer_project(project_id: int) -> None:
    _write_int(LAST_WEBLANCER_PROJECT_FILE, project_id)
