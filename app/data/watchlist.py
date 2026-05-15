"""
自選股清單讀寫器（雙模式）

模式切換：
- `DATABASE_URL` 有設 → 走 PostgreSQL（`app.db` 模組）
- 未設 → 走檔案模式（`settings.watchlist_path`，配合 repo 種子 fallback）

對外公開的 API 都是 `async`，內部依模式選擇 DB 或檔案實作。

讀取 fallback 鏈：
1. 主來源（DB 或 `WATCHLIST_PATH`）
2. repo 根的 `watchlist.json`（種子；只有檔案模式會自動 fallback；
   DB 模式由 `main.py` lifespan 在啟動時主動 seed-if-empty）
3. `DEFAULT_WATCHLIST`（最後安全網，避免 hedge_fund 拿到空清單）

提供 `add_tickers` / `remove_tickers` / `clear_watchlist` 給 Slack 介面用，
回 `MutationResult` 給呼叫方組裝回饋訊息。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from app import db as db_module
from app.config import (
    DEFAULT_WATCHLIST,
    MAX_WATCHLIST_SIZE,
    TICKER_PATTERN,
    log,
    settings,
)

_TICKER_RE = re.compile(rf"^{TICKER_PATTERN}$")


# ─── 路徑解析（檔案模式 / DB seed 共用）─────────────────────


def _resolve_path() -> Path:
    p = Path(settings.watchlist_path)
    if not p.is_absolute():
        p = Path(__file__).resolve().parents[2] / p
    return p


def _repo_seed_path() -> Path:
    """repo 根的 watchlist.json（部署到 Volume / DB 時的初始種子來源）。"""
    return Path(__file__).resolve().parents[2] / "watchlist.json"


# ─── ticker 解析 / 正規化（共用工具）────────────────────────


def _parse_tickers(raw: object) -> list[str]:
    """從輸入序列解析 + 正規化 + 上限截斷。失敗回 []。"""
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    valid: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        t = item.strip().upper()
        if not _TICKER_RE.match(t) or t in seen:
            continue
        seen.add(t)
        valid.append(t)
        if len(valid) >= MAX_WATCHLIST_SIZE:
            break
    return valid


def normalize_inputs(raw: Iterable[str]) -> tuple[list[str], list[str]]:
    """使用者輸入 → (合法且去重的 tickers, 不合法的原樣輸入)。"""
    valid: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for r in raw:
        original = r if isinstance(r, str) else str(r)
        token = original.strip().upper()
        if not token:
            continue
        if not _TICKER_RE.match(token):
            invalid.append(original)
            continue
        if token in seen:
            continue
        seen.add(token)
        valid.append(token)
    return valid, invalid


# ─── 檔案模式：低階讀寫 ─────────────────────────────────────


def _read_file_raw() -> list[str]:
    """檔案模式 raw 讀取，不做 DEFAULT fallback。"""
    primary = _resolve_path()
    seed = _repo_seed_path()
    path = primary if primary.exists() else (seed if seed.exists() else None)
    if path is None:
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.error("解析 watchlist 失敗 (%s): %s", path, e)
        return []
    raw = data.get("tickers") if isinstance(data, dict) else None
    return _parse_tickers(raw)


def _write_file(tickers: list[str]) -> Path:
    """原子寫入 settings.watchlist_path。"""
    path = _resolve_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"tickers": tickers}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    log.info("💾 watchlist 寫入檔案 %s（%d 檔）", path, len(tickers))
    return path


# ─── DB 模式：低階讀寫 ──────────────────────────────────────


async def _read_db_raw() -> list[str]:
    """DB 模式 raw 讀取。"""
    pool = db_module.get_pool()
    rows = await pool.fetch(
        "SELECT ticker FROM watchlist ORDER BY added_at ASC, ticker ASC LIMIT $1",
        MAX_WATCHLIST_SIZE,
    )
    return _parse_tickers([r["ticker"] for r in rows])


async def _db_insert_many(tickers: list[str]) -> None:
    if not tickers:
        return
    pool = db_module.get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO watchlist (ticker) VALUES ($1) ON CONFLICT (ticker) DO NOTHING",
            [(t,) for t in tickers],
        )


async def _db_delete_many(tickers: list[str]) -> None:
    if not tickers:
        return
    pool = db_module.get_pool()
    await pool.execute("DELETE FROM watchlist WHERE ticker = ANY($1::text[])", tickers)


async def _db_clear() -> None:
    pool = db_module.get_pool()
    await pool.execute("DELETE FROM watchlist")


# ─── 對外公開 API（async；依模式分派） ───────────────────────


async def read_raw_watchlist() -> list[str]:
    """讀取現存清單；不做 DEFAULT fallback（給 Slack mutation 與 status 用）。"""
    if db_module.is_db_enabled():
        return await _read_db_raw()
    return _read_file_raw()


async def load_watchlist() -> list[str]:
    """給 pipeline 用的安全讀取——空 / 壞掉時 fallback 到 DEFAULT_WATCHLIST。"""
    raw = await read_raw_watchlist()
    if not raw:
        log.warning("watchlist 為空或讀取失敗，使用預設清單 %s", DEFAULT_WATCHLIST)
        return list(DEFAULT_WATCHLIST)
    log.info("📋 讀取 watchlist: %s", raw)
    return raw


async def save_watchlist(tickers: list[str]) -> None:
    """整批替換清單。DB 模式用 transaction（DELETE + 批次 INSERT）。"""
    if db_module.is_db_enabled():
        pool = db_module.get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM watchlist")
                if tickers:
                    await conn.executemany(
                        "INSERT INTO watchlist (ticker) VALUES ($1)",
                        [(t,) for t in tickers],
                    )
        log.info("💾 watchlist 寫入 DB（%d 檔）", len(tickers))
    else:
        _write_file(tickers)


async def seed_from_file_if_empty() -> int:
    """DB 模式 + DB 為空時，從現有檔案 / repo 種子寫入 DB。回傳寫入檔數。

    呼叫時機：main.py lifespan 在 init_pool 之後。
    檔案模式下這函式 no-op。
    """
    if not db_module.is_db_enabled():
        return 0
    current = await _read_db_raw()
    if current:
        return 0
    seed = _read_file_raw()
    if not seed:
        return 0
    await save_watchlist(seed)
    log.info("🌱 PostgreSQL watchlist 從檔案種子初始化（%d 檔）", len(seed))
    return len(seed)


# ─── Mutation API ───────────────────────────────────────────


@dataclass
class MutationResult:
    """add / remove / clear 操作的回報。"""

    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    skipped_missing: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)
    over_cap: list[str] = field(default_factory=list)
    final_count: int = 0


async def add_tickers(raw_inputs: Iterable[str]) -> MutationResult:
    """加 tickers。"""
    current = await read_raw_watchlist()
    candidates, invalid = normalize_inputs(raw_inputs)
    existing = set(current)
    new_list = list(current)
    added: list[str] = []
    skipped: list[str] = []
    over_cap: list[str] = []

    for t in candidates:
        if t in existing:
            skipped.append(t)
            continue
        if len(new_list) >= MAX_WATCHLIST_SIZE:
            over_cap.append(t)
            continue
        new_list.append(t)
        existing.add(t)
        added.append(t)

    if added:
        if db_module.is_db_enabled():
            await _db_insert_many(added)
        else:
            _write_file(new_list)

    return MutationResult(
        added=added,
        skipped_existing=skipped,
        invalid=invalid,
        over_cap=over_cap,
        final_count=len(new_list),
    )


async def remove_tickers(raw_inputs: Iterable[str]) -> MutationResult:
    """移除 tickers。"""
    current = await read_raw_watchlist()
    candidates, invalid = normalize_inputs(raw_inputs)
    existing = set(current)
    removed = [t for t in candidates if t in existing]
    missing = [t for t in candidates if t not in existing]
    new_list = [t for t in current if t not in set(removed)]

    if removed:
        if db_module.is_db_enabled():
            await _db_delete_many(removed)
        else:
            _write_file(new_list)

    return MutationResult(
        removed=removed,
        skipped_missing=missing,
        invalid=invalid,
        final_count=len(new_list),
    )


async def clear_watchlist() -> MutationResult:
    """清空。MutationResult.removed 為原本內容。"""
    current = await read_raw_watchlist()
    if db_module.is_db_enabled():
        await _db_clear()
    else:
        _write_file([])
    return MutationResult(removed=current, final_count=0)
