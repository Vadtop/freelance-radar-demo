#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Единая точка Prometheus-метрик + безопасные обёртки.
Использование:
    from bot.services.metrics import (
        PROJECTS_PROCESSED_TOTAL,
        PROJECTS_SKIPPED_TOTAL,
        PROJECTS_ERRORS_TOTAL,
        CYCLE_DURATION_SECONDS,
        TG_SEND_TOTAL,
        TG_ERRORS_TOTAL,
        AI_CALLS_TOTAL,
        PAYMENTS_TOTAL,
        safe_inc, safe_set, safe_observe,
    )

ВАЖНО:
— Имена и наборы label’ов ДОЛЖНЫ совпадать при объявлении и при .labels(...)
— Если не уверены, используйте safe_*: они сами приведут набор лейблов к объявленному.
"""

from __future__ import annotations

from typing import Dict, Iterable, Mapping, Optional

from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry, REGISTRY

# ───────────────────────────────────────────────────────────────────
# Объявления метрик (ОБЯЗАТЕЛЬНО единообразные label-имена)
# ───────────────────────────────────────────────────────────────────

# Парсеры
PROJECTS_PROCESSED_TOTAL = Counter(
    "projects_processed_total", "Сколько проектов обработано", ["source"]
)
PROJECTS_SKIPPED_TOTAL = Counter(
    "projects_skipped_total", "Сколько проектов отброшено фильтрами/дедупом", ["source", "reason"]
)
PROJECTS_ERRORS_TOTAL = Counter(
    "projects_errors_total", "Ошибки парсинга/фетчинга", ["source", "stage"]
)
CYCLE_DURATION_SECONDS = Histogram(
    "parser_cycle_duration_seconds", "Длительность одного цикла парсера", ["source"]
)

# Telegram
TG_SEND_TOTAL = Counter(
    "telegram_send_total", "Сколько сообщений/карточек отправлено", ["type", "result"]
)
TG_ERRORS_TOTAL = Counter(
    "telegram_errors_total", "Неотловленные исключения в Telegram-боте", ["place"]
)

# AI
AI_CALLS_TOTAL = Counter(
    "ai_calls_total", "Вызовы AI-движка", ["model", "result"]
)

# Платежи
PAYMENTS_TOTAL = Counter(
    "payments_total", "Статусы платежей", ["provider", "status"]
)

# Общие gauge, на которые удобно писать значение
HEALTH_GAUGE = Gauge("app_health", "0/1: здоровье компонент", ["component"])

# ───────────────────────────────────────────────────────────────────
# Безопасные обёртки, чтобы не ловить Incorrect label names
# ───────────────────────────────────────────────────────────────────

def _labels_of(metric) -> Iterable[str]:
    """Возвращает объявленные имена label’ов у метрики."""
    # prometheus_client прячет структуру, но _labelnames доступно
    return getattr(metric, "_labelnames", ()) or ()

def _normalized_labels(metric, labels: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    declared = list(_labels_of(metric))
    if not declared:
        return {}
    labels = dict(labels or {})
    # Гарантируем, что у нас есть все объявленные ключи
    norm = {k: str(labels.get(k, "")) for k in declared}
    return norm

def safe_inc(metric, labels: Optional[Mapping[str, str]] = None, value: float = 1.0) -> None:
    metric.labels(**_normalized_labels(metric, labels)).inc(value)

def safe_set(metric, labels: Optional[Mapping[str, str]] = None, value: float = 0.0) -> None:
    metric.labels(**_normalized_labels(metric, labels)).set(value)

def safe_observe(metric, labels: Optional[Mapping[str, str]] = None, value: float = 0.0) -> None:
    metric.labels(**_normalized_labels(metric, labels)).observe(value)

# ───────────────────────────────────────────────────────────────────
# Back-compat (если где-то импортировали старые имена)
# ───────────────────────────────────────────────────────────────────

__all__ = [
    "PROJECTS_PROCESSED_TOTAL",
    "PROJECTS_SKIPPED_TOTAL",
    "PROJECTS_ERRORS_TOTAL",
    "CYCLE_DURATION_SECONDS",
    "TG_SEND_TOTAL",
    "TG_ERRORS_TOTAL",
    "AI_CALLS_TOTAL",
    "PAYMENTS_TOTAL",
    "HEALTH_GAUGE",
    "safe_inc",
    "safe_set",
    "safe_observe",
]

# Лимиты
LIMIT_DENIED_TOTAL = Counter(
    "limit_denied_total", "Отказы из-за суточного лимита", ["reason"]
)