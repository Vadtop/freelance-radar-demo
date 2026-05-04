# -*- coding: utf-8 -*-
#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import datetime as dt
import os
import secrets
from typing import Any, Dict, List, Literal, Optional, TypedDict

import aiosqlite
from loguru import logger

DB_PATH = os.getenv("SQLITE_PATH", "data/bot.db")

_db: Optional[aiosqlite.Connection] = None
_db_lock = asyncio.Lock()

_TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "3"))


class UserRow(TypedDict, total=False):
    id: int
    telegram_id: int
    username: str
    is_admin: bool
    keywords: str
    min_budget: int
    premium_until: Optional[str]
    trial_until: Optional[str]
    referral_code: Optional[str]
    referred_by: Optional[int]
    categories: str
    kwork_enabled: int
    fl_enabled: int
    habr_enabled: int
    freelancehunt_enabled: int
    created_at: Optional[str]


class UserStatsRow(TypedDict, total=False):
    cards_sent: int
    solution_sent: int
    projects_found: int


StatsField = Literal["cards_sent", "solution_sent", "projects_found"]


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_dt(val: Optional[str]) -> Optional[dt.datetime]:
    if not val:
        return None
    if isinstance(val, dt.datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=dt.timezone.utc)
        return val
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(val[:26], fmt).replace(tzinfo=dt.timezone.utc) if fmt.endswith("%z") else dt.datetime.strptime(val, fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    return None


def _row_to_dict(row: Optional[aiosqlite.Row]) -> Optional[Dict]:
    if row is None:
        return None
    return dict(row)


async def _get_db() -> aiosqlite.Connection:
    global _db
    if _db is not None:
        return _db
    async with _db_lock:
        if _db is not None:
            return _db
        os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
        _db = await aiosqlite.connect(DB_PATH, timeout=5)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
        logger.info("SQLite database opened: {}", DB_PATH)
        return _db


async def _ensure_user_stats(user_id: int) -> None:
    db = await _get_db()
    await db.execute("INSERT OR IGNORE INTO user_stats(user_id) VALUES(?)", (user_id,))


async def init_db() -> None:
    db = await _get_db()
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            is_admin INTEGER DEFAULT 0,
            keywords TEXT DEFAULT '',
            min_budget INTEGER DEFAULT 0,
            premium_until TEXT,
            trial_until TEXT,
            referral_code TEXT UNIQUE,
            referred_by INTEGER,
            categories TEXT DEFAULT '',
            kwork_enabled INTEGER NOT NULL DEFAULT 1,
            fl_enabled INTEGER NOT NULL DEFAULT 1,
            habr_enabled INTEGER NOT NULL DEFAULT 0,
            freelancehunt_enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS user_stats (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            cards_sent INTEGER DEFAULT 0,
            solution_sent INTEGER DEFAULT 0,
            projects_found INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            price REAL,
            url TEXT UNIQUE NOT NULL,
            status TEXT,
            bid TEXT,
            auto_capable INTEGER DEFAULT 0,
            source TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS user_limits (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            limit_per_day INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS user_daily (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            stats_date TEXT NOT NULL DEFAULT (date('now')),
            cards_sent INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, stats_date)
        );

        CREATE TABLE IF NOT EXISTS user_reminders (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            reminded_3d_at TEXT,
            reminded_1d_at TEXT
        );

        CREATE TABLE IF NOT EXISTS projects_analytics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            budget INTEGER,
            source TEXT,
            found_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS card_cache (
            card_key TEXT PRIMARY KEY,
            card_json TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS generation_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_key TEXT,
            user_id INTEGER,
            started_at TEXT DEFAULT (datetime('now')),
            finished_at TEXT,
            total_tokens INTEGER DEFAULT 0,
            total_cost_rub REAL DEFAULT 0,
            langfuse_trace_id TEXT,
            analysis_json TEXT,
            estimate_json TEXT,
            draft_a TEXT,
            draft_b TEXT,
            critique_json TEXT,
            final_score INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS generation_feedback (
            run_id INTEGER PRIMARY KEY REFERENCES generation_runs(id),
            chosen_variant TEXT,
            edited_after INTEGER DEFAULT 0,
            sent_to_client INTEGER DEFAULT 0,
            got_reply INTEGER DEFAULT 0,
            won_order INTEGER DEFAULT 0,
            user_rating INTEGER
        );
    """)
    await db.commit()
    logger.info("SQLite tables ensured")


async def list_all_users() -> List[UserRow]:
    db = await _get_db()
    cursor = await db.execute("SELECT * FROM users ORDER BY id DESC")
    rows = await cursor.fetchall()
    return [_row_to_dict(r) for r in rows]


async def get_or_create_user(telegram_id: int) -> UserRow:
    db = await _get_db()
    cursor = await db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    row = await cursor.fetchone()
    if row:
        d = _row_to_dict(row)
        await _ensure_user_stats(d["id"])
        return d
    trial_until = _now_utc() + dt.timedelta(days=_TRIAL_DAYS)
    ref_code = secrets.token_urlsafe(6)[:8]
    await db.execute(
        "INSERT INTO users(telegram_id, trial_until, referral_code, created_at) VALUES(?,?,?,datetime('now'))",
        (telegram_id, trial_until.isoformat(), ref_code),
    )
    await db.commit()
    cursor = await db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    row = await cursor.fetchone()
    d = _row_to_dict(row)
    await _ensure_user_stats(d["id"])
    return d


async def update_user_keywords(telegram_id: int, keywords: str) -> bool:
    db = await _get_db()
    cursor = await db.execute("UPDATE users SET keywords = ? WHERE telegram_id = ?", (keywords, telegram_id))
    await db.commit()
    return cursor.rowcount > 0


async def update_user_budget(telegram_id: int, min_budget: int) -> bool:
    db = await _get_db()
    cursor = await db.execute("UPDATE users SET min_budget = ? WHERE telegram_id = ?", (min_budget, telegram_id))
    await db.commit()
    return cursor.rowcount > 0


async def update_user_limit(telegram_id: int, limit_per_day: int) -> bool:
    db = await _get_db()
    cursor = await db.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
    row = await cursor.fetchone()
    if not row:
        return False
    uid = row[0]
    await db.execute(
        "INSERT OR REPLACE INTO user_limits(user_id, limit_per_day) VALUES(?,?)",
        (uid, int(limit_per_day)),
    )
    await db.commit()
    return True


async def get_user_limit(telegram_id: int) -> int:
    db = await _get_db()
    cursor = await db.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
    row = await cursor.fetchone()
    if not row:
        return 0
    uid = row[0]
    cursor = await db.execute("SELECT limit_per_day FROM user_limits WHERE user_id = ?", (uid,))
    row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def get_daily_cards(telegram_id: int) -> int:
    db = await _get_db()
    cursor = await db.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
    row = await cursor.fetchone()
    if not row:
        return 0
    uid = row[0]
    cursor = await db.execute(
        "SELECT cards_sent FROM user_daily WHERE user_id = ? AND stats_date = date('now')",
        (uid,),
    )
    row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def get_user_stats(telegram_id: int) -> UserStatsRow:
    db = await _get_db()
    cursor = await db.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
    row = await cursor.fetchone()
    if not row:
        return {"cards_sent": 0, "solution_sent": 0, "projects_found": 0}
    uid = row[0]
    await _ensure_user_stats(uid)
    cursor = await db.execute(
        "SELECT cards_sent, solution_sent, projects_found FROM user_stats WHERE user_id = ?",
        (uid,),
    )
    row = await cursor.fetchone()
    return _row_to_dict(row) if row else {"cards_sent": 0, "solution_sent": 0, "projects_found": 0}


async def increment_user_stats_async(
    telegram_id: int,
    field: StatsField = "cards_sent",
    *,
    delta: int = 1,
) -> None:
    allowed = {"cards_sent", "solution_sent", "projects_found"}
    fld = str(field)
    if fld in {"kwork", "fl", "habr", "freelancehunt"}:
        fld = "cards_sent"
    if fld not in allowed:
        logger.error(f"increment_user_stats_async: invalid field '{field}'")
        return

    db = await _get_db()
    cursor = await db.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
    row = await cursor.fetchone()
    if not row:
        return
    uid = row[0]
    await _ensure_user_stats(uid)
    await db.execute(
        f"UPDATE user_stats SET {fld} = COALESCE({fld}, 0) + ? WHERE user_id = ?",
        (int(delta), uid),
    )
    if fld == "cards_sent":
        await db.execute(
            "INSERT INTO user_daily(user_id, stats_date, cards_sent) VALUES(?,date('now'),1) "
            "ON CONFLICT(user_id, stats_date) DO UPDATE SET cards_sent = cards_sent + 1",
            (uid,),
        )
    await db.commit()


async def is_user_premium(telegram_id: int) -> bool:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT premium_until, trial_until FROM users WHERE telegram_id = ?",
        (telegram_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return False
    now = _now_utc()
    pu = _parse_dt(row["premium_until"])
    tu = _parse_dt(row["trial_until"])
    return bool((pu and pu > now) or (tu and tu > now))


async def can_user_send_cards(telegram_id: int) -> bool:
    return True


ProjectDict = Dict[str, Any]


async def add_project(project: ProjectDict | Any) -> None:
    db = await _get_db()
    data = (
        project._asdict() if hasattr(project, "_asdict")
        else dict(project) if isinstance(project, dict)
        else project.__dict__
    )
    await db.execute(
        "INSERT OR REPLACE INTO projects(id, title, description, price, url, status, bid, auto_capable, source) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (
            data.get("id"),
            data.get("title"),
            data.get("description"),
            data.get("price"),
            data.get("url"),
            data.get("status"),
            data.get("bid"),
            data.get("auto_capable", 0),
            data.get("source"),
        ),
    )
    await db.commit()


async def update_project_result(project_id: int, result_data: Dict[str, Any]) -> bool:
    db = await _get_db()
    cursor = await db.execute("UPDATE projects SET bid = ? WHERE id = ?", (result_data.get("text", ""), project_id))
    await db.commit()
    return cursor.rowcount > 0


async def update_project_status(project_id: int, status: str) -> bool:
    db = await _get_db()
    cursor = await db.execute("UPDATE projects SET status = ? WHERE id = ?", (status, project_id))
    await db.commit()
    return cursor.rowcount > 0


async def get_users_expiring_soon(days_min: int, days_max: int) -> List[Dict]:
    db = await _get_db()
    cursor = await db.execute(
        """
        SELECT id, telegram_id, premium_until, trial_until FROM users
        WHERE
            (premium_until IS NOT NULL AND julianday(premium_until) - julianday('now') BETWEEN ? AND ?)
            OR (trial_until IS NOT NULL AND premium_until IS NULL AND julianday(trial_until) - julianday('now') BETWEEN ? AND ?)
        """,
        (days_min, days_max, days_min, days_max),
    )
    rows = await cursor.fetchall()
    return [_row_to_dict(r) for r in rows]


async def get_reminder_state(user_id: int) -> Dict:
    db = await _get_db()
    cursor = await db.execute("SELECT reminded_3d_at, reminded_1d_at FROM user_reminders WHERE user_id = ?", (user_id,))
    row = await cursor.fetchone()
    return _row_to_dict(row) if row else {}


async def set_reminder_sent(user_id: int, kind: str) -> None:
    db = await _get_db()
    col = "reminded_3d_at" if kind == "3d" else "reminded_1d_at"
    await db.execute(
        f"INSERT OR REPLACE INTO user_reminders(user_id, {col}) VALUES(?,datetime('now'))",
        (user_id,),
    )
    await db.commit()


async def get_user_by_referral_code(code: str) -> Optional[Dict]:
    db = await _get_db()
    cursor = await db.execute(
        "SELECT id, telegram_id, premium_until, trial_until FROM users WHERE referral_code = ?",
        (code.strip(),),
    )
    row = await cursor.fetchone()
    return _row_to_dict(row) if row else None


async def add_bonus_days(telegram_id: int, days: int) -> None:
    db = await _get_db()
    cursor = await db.execute("SELECT premium_until, trial_until FROM users WHERE telegram_id = ?", (telegram_id,))
    row = await cursor.fetchone()
    if not row:
        return
    now = _now_utc()
    pu = _parse_dt(row["premium_until"])
    tu = _parse_dt(row["trial_until"])
    bonus = dt.timedelta(days=days)

    if pu and pu > now:
        new_val = (pu + bonus).isoformat()
        await db.execute("UPDATE users SET premium_until = ? WHERE telegram_id = ?", (new_val, telegram_id))
    else:
        base = tu if (tu and tu > now) else now
        new_val = (base + bonus).isoformat()
        await db.execute("UPDATE users SET trial_until = ? WHERE telegram_id = ?", (new_val, telegram_id))
    await db.commit()


async def set_referred_by(telegram_id: int, referrer_telegram_id: int) -> bool:
    db = await _get_db()
    cursor = await db.execute(
        "UPDATE users SET referred_by = ? WHERE telegram_id = ? AND referred_by IS NULL",
        (referrer_telegram_id, telegram_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def count_referrals(telegram_id: int) -> int:
    db = await _get_db()
    cursor = await db.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (telegram_id,))
    row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def set_user_exchanges(
    telegram_id: int,
    *,
    kwork: bool,
    fl: bool,
    habr: bool,
    freelancehunt: bool,
) -> None:
    db = await _get_db()
    await db.execute(
        "UPDATE users SET kwork_enabled=?, fl_enabled=?, habr_enabled=?, freelancehunt_enabled=? WHERE telegram_id=?",
        (int(kwork), int(fl), int(habr), int(freelancehunt), telegram_id),
    )
    await db.commit()


async def save_project_for_analytics(title: str, budget: int | None, source: str, found_at: str) -> None:
    db = await _get_db()
    await db.execute(
        "INSERT INTO projects_analytics(title, budget, source, found_at) VALUES(?,?,?,?)",
        (title, budget, source, found_at),
    )
    await db.commit()


async def save_card_cache(card_key: str, card: dict) -> None:
    import json
    db = await _get_db()
    await db.execute(
        "INSERT OR REPLACE INTO card_cache (card_key, card_json) VALUES (?, ?)",
        (card_key, json.dumps(card, ensure_ascii=False)),
    )
    await db.commit()


async def get_card_cache(card_key: str) -> dict | None:
    import json
    db = await _get_db()
    cursor = await db.execute(
        "SELECT card_json FROM card_cache WHERE card_key = ?", (card_key,)
    )
    row = await cursor.fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


async def cleanup_card_cache(max_age_hours: int = 24) -> None:
    db = await _get_db()
    await db.execute(
        "DELETE FROM card_cache WHERE created_at < datetime('now', ?)",
        (f"-{max_age_hours} hours",),
    )
    await db.commit()


async def record_pitch(user_id: int, card_key: str, source: str) -> None:
    db = await _get_db()
    await db.execute(
        "CREATE TABLE IF NOT EXISTS user_pitches ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  user_id INTEGER NOT NULL,"
        "  card_key TEXT NOT NULL,"
        "  source TEXT,"
        "  created_at TEXT DEFAULT (datetime('now'))"
        ")"
    )
    await db.execute(
        "INSERT INTO user_pitches(user_id, card_key, source) VALUES(?,?,?)",
        (user_id, card_key, source)
    )
    await db.commit()


async def count_user_pitches(user_id: int) -> dict:
    db = await _get_db()
    await db.execute(
        "CREATE TABLE IF NOT EXISTS user_pitches ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  user_id INTEGER NOT NULL,"
        "  card_key TEXT NOT NULL,"
        "  source TEXT,"
        "  created_at TEXT DEFAULT (datetime('now'))"
        ")"
    )
    cur = await db.execute(
        "SELECT "
        "  SUM(CASE WHEN date(created_at)=date('now') THEN 1 ELSE 0 END) AS today,"
        "  COUNT(*) AS total "
        "FROM user_pitches WHERE user_id=?",
        (user_id,)
    )
    row = await cur.fetchone()
    return {"today": int(row[0] or 0), "total": int(row[1] or 0)}


async def save_generation_run(
    card_key: str,
    user_id: int,
    total_tokens: int,
    total_cost_rub: float,
    langfuse_trace_id: str | None,
    analysis_json: str | None,
    estimate_json: str | None,
    draft_a: str,
    draft_b: str,
    critique_json: str | None,
    final_score: int,
) -> int:
    db = await _get_db()
    cursor = await db.execute(
        "INSERT INTO generation_runs(card_key, user_id, finished_at, total_tokens, total_cost_rub, "
        "langfuse_trace_id, analysis_json, estimate_json, draft_a, draft_b, critique_json, final_score) "
        "VALUES(?,?,datetime('now'),?,?,?,?,?,?,?,?,?,?)",
        (card_key, user_id, total_tokens, total_cost_rub, langfuse_trace_id,
         analysis_json, estimate_json, draft_a, draft_b, critique_json, final_score),
    )
    await db.commit()
    return cursor.lastrowid


async def save_generation_feedback(
    run_id: int,
    chosen_variant: str,
    edited_after: bool = False,
    sent_to_client: bool = False,
    got_reply: bool = False,
    won_order: bool = False,
    user_rating: int | None = None,
) -> None:
    db = await _get_db()
    await db.execute(
        "INSERT OR REPLACE INTO generation_feedback(run_id, chosen_variant, edited_after, "
        "sent_to_client, got_reply, won_order, user_rating) VALUES(?,?,?,?,?,?,?)",
        (run_id, chosen_variant, int(edited_after), int(sent_to_client), int(got_reply), int(won_order), user_rating),
    )
    await db.commit()


async def get_generation_run(run_id: int) -> dict | None:
    db = await _get_db()
    cursor = await db.execute("SELECT * FROM generation_runs WHERE id = ?", (run_id,))
    row = await cursor.fetchone()
    return _row_to_dict(row)


async def close_pool():
    global _db
    if _db is not None:
        try:
            await _db.close()
        finally:
            _db = None
        logger.info("SQLite connection closed")
