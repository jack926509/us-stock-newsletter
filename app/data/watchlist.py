"""
自選股清單讀寫器

讀取流程：
1. 優先讀 `settings.watchlist_path`（部署環境通常指向 Volume，例 `/data/watchlist.json`）
2. 若 Volume 檔案不存在，fallback 到 repo 根目錄的 `watchlist.json` 當「種子」
3. 若兩者皆無或解析失敗，使用 `DEFAULT_WATCHLIST`

寫入：原子 (`.tmp` → `os.replace`) 寫到 `settings.watchlist_path`。

提供 `add_tickers` / `remove_tickers` / `clear_watchlist` 給 Slack 介面操作，
回傳 `MutationResult` 給呼叫方組裝回饋訊息。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from app.config import DEFAULT_WATCHLIST, MAX_WATCHLIST_SIZE, TICKER_PATTERN, log, settings

_TICKER_RE = re.compile(rf"^{TICKER_PATTERN}$")


# ─── 路徑解析 ─────────────────────────────────────────────────


def _resolve_path() -> Path:
    """`settings.watchlist_path` → Path。相對路徑以 repo 根目錄為基準。"""
    p = Path(settings.watchlist_path)
    if not p.is_absolute():
        p = Path(__file__).resolve().parents[2] / p
    return p


def _repo_seed_path() -> Path:
    """repo 根的 watchlist.json（部署上 Volume 後的初始種子來源）。"""
    return Path(__file__).resolve().parents[2] / "watchlist.json"


# ─── 讀取 ────────────────────────────────────────────────────


def _parse_tickers(raw: object) -> list[str]:
    """從 JSON 內容解析 + 正規化 + 上限截斷。失敗回傳 []。"""
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


def read_raw_watchlist() -> list[str]:
    """讀取現存清單；檔案不存在 / 空 / 壞掉一律回 []，**不** fallback 到 DEFAULT。

    Slack 端的 add/remove/clear 操作以 raw 為基準，避免「使用者剛 clear → load 又補回 DEFAULT」的怪行為。
    """
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


def load_watchlist() -> list[str]:
    """給 pipeline 用的安全讀取——空 / 壞掉時 fallback 到 DEFAULT_WATCHLIST。"""
    raw = read_raw_watchlist()
    if not raw:
        log.warning("watchlist 為空或讀取失敗，使用預設清單 %s", DEFAULT_WATCHLIST)
        return list(DEFAULT_WATCHLIST)
    log.info("📋 讀取 watchlist: %s", raw)
    return raw


# ─── 寫入 ────────────────────────────────────────────────────


def save_watchlist(tickers: list[str]) -> Path:
    """原子寫入 settings.watchlist_path。回傳寫入路徑。

    `tickers` 應該已經是正規化過的乾淨清單；本函式不做格式驗證。
    """
    path = _resolve_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"tickers": tickers}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)  # POSIX atomic rename
    log.info("💾 watchlist 已寫入 %s（%d 檔）", path, len(tickers))
    return path


# ─── Mutation API ───────────────────────────────────────────


@dataclass
class MutationResult:
    """add / remove / clear 操作的回報。"""

    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)  # add 時已存在
    skipped_missing: list[str] = field(default_factory=list)   # remove 時不在清單
    invalid: list[str] = field(default_factory=list)           # 格式不正確
    over_cap: list[str] = field(default_factory=list)          # 超過 MAX_WATCHLIST_SIZE
    final_count: int = 0


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


def add_tickers(raw_inputs: Iterable[str]) -> MutationResult:
    """加 tickers；回傳 MutationResult。"""
    current = read_raw_watchlist()
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
        save_watchlist(new_list)

    return MutationResult(
        added=added,
        skipped_existing=skipped,
        invalid=invalid,
        over_cap=over_cap,
        final_count=len(new_list),
    )


def remove_tickers(raw_inputs: Iterable[str]) -> MutationResult:
    """移除 tickers；回傳 MutationResult。"""
    current = read_raw_watchlist()
    candidates, invalid = normalize_inputs(raw_inputs)
    existing = set(current)
    removed = [t for t in candidates if t in existing]
    missing = [t for t in candidates if t not in existing]
    new_list = [t for t in current if t not in set(removed)]

    if removed:
        save_watchlist(new_list)

    return MutationResult(
        removed=removed,
        skipped_missing=missing,
        invalid=invalid,
        final_count=len(new_list),
    )


def clear_watchlist() -> MutationResult:
    """清空。回傳 MutationResult.removed 為原本內容。"""
    current = read_raw_watchlist()
    save_watchlist([])
    return MutationResult(removed=current, final_count=0)
