#!/usr/bin/env python3
from __future__ import annotations
import os
from typing import Final

# ───────── РЕЖИМЫ ─────────
ENV: Final[str] = os.getenv("ENV", "production")
DEMO_MODE: Final[bool] = os.getenv("DEMO_MODE", "false").lower() in ("1", "true")
OFFLINE_MODE: Final[bool] = os.getenv("OFFLINE_MODE", "false").lower() in ("1", "true")
LIGHT_MODE:   Final[bool] = os.getenv("LIGHT_MODE", "false").lower()   in ("1", "true")

# ───────── TELEGRAM ─────────
TELEGRAM_BOT_TOKEN: Final[str] = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID:      Final[int] = int(os.getenv("ADMIN_CHAT_ID", "0"))
PARSER_TARGET_CHAT: Final[int] = int(os.getenv("PARSER_TARGET_CHAT", str(ADMIN_CHAT_ID)))
SEND_ALL_TO_ADMIN:  Final[bool] = os.getenv("SEND_ALL_TO_ADMIN", "true").lower() in ("1", "true")
REQUEST_TIMEOUT:    Final[float] = float(os.getenv("REQUEST_TIMEOUT", "30"))

# ───────── ПРОКСИ ─────────
# Один прокси (как раньше)
PROXY_STRING: Final[str | None] = os.getenv("PROXY_STRING")
# Список прокси (через запятую)
PROXY_LIST: Final[list[str]] = [
    p.strip() for p in os.getenv("PROXY_LIST", "").split(",") if p.strip()
]
NO_PROXY:      Final[str] = os.getenv("NO_PROXY", "")

# ───────── URLы ─────────
KWORK_CATEGORY_URL: Final[str] = os.getenv("KWORK_CATEGORY_URL", "https://kwork.ru/projects")
FL_CATEGORY_URL:    Final[str] = os.getenv("FL_CATEGORY_URL",    "https://www.fl.ru/projects/")

# RSS
KWORK_RSS_URL: Final[str] = os.getenv("KWORK_RSS_URL", "https://kwork.ru/rss")
FL_RSS_URL:    Final[str] = os.getenv("FL_RSS_URL",    "https://www.fl.ru/rss/all.xml")

# ───────── ПАРСИНГ ─────────
PARSING_INTERVAL_SECONDS: Final[int] = int(os.getenv("PARSING_INTERVAL_SECONDS", "120"))
MIN_BUDGET_RUB:           Final[int] = int(os.getenv("MIN_BUDGET_RUB", "0"))

# ───────── METRICS ─────────
API_PORT:            Final[int] = int(os.getenv("API_PORT",            "8000"))
PAYMENTS_WEBHOOK_PORT:Final[int] = int(os.getenv("PAYMENTS_WEBHOOK_PORT","8080"))
BOT_METRICS_PORT:    Final[int] = int(os.getenv("BOT_METRICS_PORT",    "8003"))
KWORK_METRICS_PORT:  Final[int] = int(os.getenv("KWORK_METRICS_PORT",  "8001"))
FL_METRICS_PORT:     Final[int] = int(os.getenv("FL_METRICS_PORT",     "8002"))

# ───────── DATABASE ─────────
SQLITE_PATH: Final[str] = os.getenv("SQLITE_PATH", "data/bot.db")

# ───────── FETCHER / CF ─────────
HTTPX_TIMEOUT:   Final[float] = float(os.getenv("HTTPX_TIMEOUT", "45"))
CLEARANCE_FILE:  Final[str]   = os.getenv("CLEARANCE_FILE", "/app/data/fl/cookies.json")

USE_FLARESOLVERR: Final[bool] = os.getenv("USE_FLARESOLVERR", "false").lower() in ("1", "true")
FLARE_URL:        Final[str]  = os.getenv("FLARE_URL", "http://flaresolverr:8191")
FLARE_MAX_TIMEOUT:Final[int]  = int(os.getenv("FLARE_MAX_TIMEOUT", "65000"))

USE_PLAYWRIGHT:   Final[bool] = os.getenv("USE_PLAYWRIGHT", "true").lower() in ("1", "true")
HEADLESS:         Final[bool] = os.getenv("HEADLESS", "true").lower() in ("1", "true")

BROWSER_TIMEOUT:  Final[int]  = int(os.getenv("BROWSER_TIMEOUT", "90000"))
NAV_TIMEOUT:      Final[int]  = int(os.getenv("NAV_TIMEOUT",      "60000"))

# ───────── ФАЙЛЫ СОСТОЯНИЯ ─────────
LAST_KWORK_PROJECT_FILE: Final[str] = os.getenv("LAST_KWORK_PROJECT_FILE", "data/last_kwork_id.json")
LAST_FL_PROJECT_FILE:    Final[str] = os.getenv("LAST_FL_PROJECT_FILE",    "data/last_fl_id.json")
LAST_HABR_PROJECT_FILE:         Final[str] = os.getenv("LAST_HABR_PROJECT_FILE",         "data/last_habr_id.json")
LAST_FREELANCEHUNT_PROJECT_FILE:Final[str] = os.getenv("LAST_FREELANCEHUNT_PROJECT_FILE","data/last_freelancehunt_id.json")
LAST_FREELANCERU_PROJECT_FILE: Final[str] = os.getenv("LAST_FREELANCERU_PROJECT_FILE",  "data/last_freelanceru_id.json")
LAST_WEBLANCER_PROJECT_FILE:   Final[str] = os.getenv("LAST_WEBLANCER_PROJECT_FILE",    "data/last_weblancer_id.json")

# ───────── AI ─────────
DEEPSEEK_API_KEY: Final[str | None] = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
DEEPSEEK_API_URL: Final[str]        = os.getenv("DEEPSEEK_API_URL", os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1"))
DEEPSEEK_MODEL:   Final[str]        = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_MOCK:    Final[bool]       = os.getenv("DEEPSEEK_MOCK", "false").lower() in ("1", "true")

FREELANCER_PROFILE: Final[str] = os.getenv("FREELANCER_PROFILE", "")

# ───────── AI V2 (OpenRouter) ─────────
OPENROUTER_API_KEY: Final[str] = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL: Final[str] = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
MODEL_DRAFT: Final[str] = os.getenv("MODEL_DRAFT", "anthropic/claude-haiku-4.5")
MODEL_ANALYZE: Final[str] = os.getenv("MODEL_ANALYZE", "deepseek/deepseek-chat")
MODEL_ESTIMATE: Final[str] = os.getenv("MODEL_ESTIMATE", "deepseek/deepseek-chat")
MODEL_CRITIQUE: Final[str] = os.getenv("MODEL_CRITIQUE", "deepseek/deepseek-chat")
USE_AGENT_V2: Final[bool] = os.getenv("USE_AGENT_V2", "false").lower() in ("1", "true")
USE_MCP_AGENT: Final[bool] = os.getenv("USE_MCP_AGENT", "false").lower() in ("1", "true")
USE_RECALL: Final[bool] = os.getenv("USE_RECALL", "false").lower() in ("1", "true")

# ───────── LANGFUSE ─────────
LANGFUSE_HOST: Final[str] = os.getenv("LANGFUSE_HOST", "")
LANGFUSE_PUBLIC_KEY: Final[str] = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY: Final[str] = os.getenv("LANGFUSE_SECRET_KEY", "")

# ───────── CASES DB ─────────
CASES_DB_PATH: Final[str] = os.getenv("CASES_DB_PATH", "data/cases.db")

# ───────── HABR FREELANCE ─────────
HABR_RSS_URL:     Final[str] = os.getenv("HABR_RSS_URL", "https://freelance.habr.com/tasks.rss")
HABR_METRICS_PORT:Final[int] = int(os.getenv("HABR_METRICS_PORT", "8004"))
HABR_PARSER_ENABLED: Final[bool] = os.getenv("HABR_PARSER_ENABLED", "true").lower() in ("1", "true")

# ───────── FREELANCEHUNT ─────────
FREELANCEHUNT_TOKEN:          Final[str]  = os.getenv("FREELANCEHUNT_TOKEN", "")
FREELANCEHUNT_METRICS_PORT:   Final[int]  = int(os.getenv("FREELANCEHUNT_METRICS_PORT", "8005"))
FREELANCEHUNT_PARSER_ENABLED: Final[bool] = os.getenv("FREELANCEHUNT_PARSER_ENABLED", "false").lower() in ("1", "true")

# ───────── KWORK API ─────────
KWORK_LOGIN:    Final[str] = os.getenv("KWORK_LOGIN", "")
KWORK_PASSWORD: Final[str] = os.getenv("KWORK_PASSWORD", "")
# Если логин/пароль не заданы — парсер упадёт в fallback на HTML-скрапинг
KWORK_USE_API:  Final[bool] = bool(os.getenv("KWORK_LOGIN") and os.getenv("KWORK_PASSWORD"))

# ───────── ПРОЧЕЕ ─────────
MAX_RETRIES:      Final[int] = int(os.getenv("MAX_RETRIES", "5"))
RETRY_DELAY:      Final[int] = int(os.getenv("RETRY_DELAY", "10"))
LOG_LEVEL:        Final[str] = os.getenv("LOG_LEVEL", "INFO")

# ───────── UA ─────────
USER_AGENT: Final[str] = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

