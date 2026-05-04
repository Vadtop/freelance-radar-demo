from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from loguru import logger

from bot.ai.schemas import CaseHit

DB_PATH = os.getenv("CASES_DB_PATH", str(Path(__file__).parent / "cases.db"))

_USE_SQLITE_VEC = False
_faiss_index = None
_faiss_metadata: list[dict] = []


def _try_sqlite_vec():
    global _USE_SQLITE_VEC
    try:
        import sqlite_vec
        _USE_SQLITE_VEC = True
        return True
    except ImportError:
        logger.info("sqlite-vec not available, using FAISS fallback")
        return False


_try_sqlite_vec()


class CasesStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or DB_PATH
        self._conn = None

    async def _get_conn(self):
        if self._conn is not None:
            return self._conn
        import aiosqlite
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        if _USE_SQLITE_VEC:
            import sqlite_vec
            self._conn.connection.enable_load_extension(True)
            sqlite_vec.load(self._conn.connection)
            await self._conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS cases_vec USING vec0(embedding float[1024])")
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                stack TEXT DEFAULT '',
                price TEXT DEFAULT '',
                duration TEXT DEFAULT '',
                link TEXT DEFAULT '',
                description TEXT DEFAULT '',
                embedding_id INTEGER
            )
        """)
        await self._conn.commit()
        return self._conn

    async def add_case(self, case: dict, embedding: list[float] | None = None) -> int:
        conn = await self._get_conn()
        cursor = await conn.execute(
            "INSERT INTO cases(title, stack, price, duration, link, description) VALUES(?,?,?,?,?,?)",
            (case.get("title", ""), case.get("stack", ""), case.get("price", ""),
             case.get("duration", ""), case.get("link", ""), case.get("description", "")),
        )
        row_id = cursor.lastrowid
        if embedding and _USE_SQLITE_VEC:
            emb_json = json.dumps(embedding)
            try:
                cur2 = await conn.execute("INSERT INTO cases_vec(rowid, embedding) VALUES(?, ?)", (row_id, emb_json))
                emb_id = cur2.lastrowid
                await conn.execute("UPDATE cases SET embedding_id=? WHERE id=?", (emb_id, row_id))
            except Exception as e:
                logger.warning(f"Failed to insert embedding: {e}")
        await conn.commit()
        return row_id

    def search(self, query_embedding: list[float], top_k: int = 3) -> list[CaseHit]:
        if _USE_SQLITE_VEC:
            return self._search_vec(query_embedding, top_k)
        return self._search_faiss(query_embedding, top_k)

    def _search_vec(self, query_embedding: list[float], top_k: int = 3) -> list[CaseHit]:
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            emb_json = json.dumps(query_embedding)
            cur = conn.execute("""
                SELECT c.title, c.stack, c.price, c.duration, c.link,
                       v.distance
                FROM cases_vec v
                JOIN cases c ON c.embedding_id = v.rowid
                WHERE v.embedding MATCH ?
                ORDER BY v.distance
                LIMIT ?
            """, (emb_json, top_k))
            rows = cur.fetchall()
            results = []
            for r in rows:
                sim = max(0.0, 1.0 - float(r["distance"]))
                results.append(CaseHit(
                    title=r["title"], stack=r["stack"], price=r["price"],
                    duration=r["duration"], link=r["link"], similarity=sim,
                ))
            return results
        except Exception as e:
            logger.warning(f"sqlite-vec search failed: {e}")
            return []
        finally:
            conn.close()

    def _search_faiss(self, query_embedding: list[float], top_k: int = 3) -> list[CaseHit]:
        global _faiss_index, _faiss_metadata
        self._load_faiss()
        if _faiss_index is None or _faiss_index.ntotal == 0:
            return []
        import numpy as np
        q = np.array([query_embedding], dtype=np.float32)
        k = min(top_k, _faiss_index.ntotal)
        distances, indices = _faiss_index.search(q, k)
        results = []
        for i in range(k):
            idx = int(indices[0][i])
            if idx < 0 or idx >= len(_faiss_metadata):
                continue
            m = _faiss_metadata[idx]
            sim = max(0.0, 1.0 - float(distances[0][i]))
            results.append(CaseHit(
                title=m.get("title", ""), stack=m.get("stack", ""),
                price=m.get("price", ""), duration=m.get("duration", ""),
                link=m.get("link", ""), similarity=sim,
            ))
        return results

    def _load_faiss(self):
        global _faiss_index, _faiss_metadata
        if _faiss_index is not None:
            return
        try:
            import faiss
            import sqlite3
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.execute("SELECT id, title, stack, price, duration, link, description FROM cases")
            rows = cur.fetchall()
            conn.close()
            if not rows:
                return
            _faiss_metadata = [dict(r) for r in rows]
            dim = 1024
            _faiss_index = faiss.IndexFlatIP(dim)
            logger.info(f"FAISS index loaded with 0 vectors (embeddings need rebuild)")
        except ImportError:
            logger.info("FAISS not available, case search disabled")
        except Exception as e:
            logger.warning(f"FAISS load failed: {e}")
