"""
PostgreSQL 連線池與 schema migration。

- 在 FastAPI lifespan 啟動時 `init_pool()` → 建池 + 跑 idempotent migration
- 在 lifespan 結束時 `close_pool()`
- 若 `DATABASE_URL` 未設定，整個模組「靜默停用」（`is_db_enabled()` 回 False）
  → watchlist 模組自動退回檔案模式，本地開發與測試不用裝 PG

Pool 設定保守：min=1 max=5、command_timeout=10s。日報服務 QPS 極低，
連線數開太大反而吃掉 Zeabur Postgres 的免費 connection 配額。
"""

from __future__ import annotations

import asyncpg

from app.config import log, settings

_pool: asyncpg.Pool | None = None


_MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS watchlist (
    ticker      TEXT        PRIMARY KEY,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 確保按加入順序穩定列出
CREATE INDEX IF NOT EXISTS watchlist_added_at_idx ON watchlist (added_at ASC);
"""


def is_db_enabled() -> bool:
    """有 pool 就表示 DB 模式啟用。"""
    return _pool is not None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("PostgreSQL pool 尚未初始化（DATABASE_URL 未設？）")
    return _pool


async def init_pool() -> asyncpg.Pool | None:
    """建立連線池並跑 migration。DATABASE_URL 未設則回 None（檔案模式）。"""
    global _pool
    if not settings.database_url:
        log.info("DATABASE_URL 未設定 → watchlist 使用檔案模式")
        return None

    _pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=1,
        max_size=5,
        command_timeout=10.0,
    )
    async with _pool.acquire() as conn:
        await conn.execute(_MIGRATION_SQL)
    log.info("✅ PostgreSQL 連線池就緒；watchlist 表已 ensure")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("🔌 PostgreSQL 連線池已關閉")
