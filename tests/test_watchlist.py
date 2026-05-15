"""watchlist 讀寫器測試（檔案模式；DB 模式 import 期保證不啟用 pool）"""

import asyncio
import json
import os

import pytest

# 為了能在無 .env 的情況下 import config，預先塞入必要的 env var
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("FINNHUB_API_KEY", "test")
os.environ.setdefault("TAVILY_API_KEY", "test")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test")
os.environ.setdefault("SLACK_CHANNEL", "C_TEST")
# 顯式清掉 DATABASE_URL 避免測試偶然連到真正 DB
os.environ.pop("DATABASE_URL", None)

from app.config import DEFAULT_WATCHLIST, MAX_WATCHLIST_SIZE, settings  # noqa: E402
from app.data import watchlist as wl_mod  # noqa: E402
from app.data.watchlist import (  # noqa: E402
    add_tickers,
    clear_watchlist,
    load_watchlist,
    normalize_inputs,
    read_raw_watchlist,
    remove_tickers,
    save_watchlist,
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def isolated_wl(tmp_path, monkeypatch):
    """讓 settings.watchlist_path 指向 tmp、且禁用 repo seed fallback。"""
    target = tmp_path / "wl.json"
    monkeypatch.setattr(settings, "watchlist_path", str(target))
    monkeypatch.setattr(wl_mod, "_repo_seed_path", lambda: tmp_path / "no_seed_here.json")

    def _write(payload):
        if payload is None:
            if target.exists():
                target.unlink()
        else:
            target.write_text(json.dumps(payload), encoding="utf-8")
        return target

    return _write


# ─── 讀取（檔案模式） ───────────────────────────────────────


def test_load_valid(isolated_wl):
    isolated_wl({"tickers": ["aapl", "NVDA", "msft"]})
    assert _run(load_watchlist()) == ["AAPL", "NVDA", "MSFT"]


def test_load_dedupes_and_uppercases(isolated_wl):
    isolated_wl({"tickers": ["TSLA", "tsla", "TSLA"]})
    assert _run(load_watchlist()) == ["TSLA"]


def test_load_invalid_filtered(isolated_wl):
    isolated_wl({"tickers": ["AAPL", "", "TOO_LONG_TICKER", "lowercase!", 123, "NVDA"]})
    assert _run(load_watchlist()) == ["AAPL", "NVDA"]


def test_load_hard_cap(isolated_wl):
    isolated_wl({"tickers": [f"T{i}" for i in range(20)]})
    assert len(_run(load_watchlist())) <= MAX_WATCHLIST_SIZE


def test_load_empty_falls_back_to_default(isolated_wl):
    isolated_wl({"tickers": []})
    assert _run(load_watchlist()) == list(DEFAULT_WATCHLIST)


def test_load_missing_file_falls_back(isolated_wl):
    isolated_wl(None)
    assert _run(load_watchlist()) == list(DEFAULT_WATCHLIST)


def test_load_bad_json_falls_back(isolated_wl, tmp_path):
    target = tmp_path / "wl.json"
    target.write_text("{not valid json", encoding="utf-8")
    assert _run(load_watchlist()) == list(DEFAULT_WATCHLIST)


def test_load_wrong_structure_falls_back(isolated_wl):
    isolated_wl({"symbols": ["AAPL"]})
    assert _run(load_watchlist()) == list(DEFAULT_WATCHLIST)


# ─── read_raw_watchlist 與 load 區別 ──────────────────────


def test_read_raw_returns_empty_when_explicit(isolated_wl):
    isolated_wl({"tickers": []})
    assert _run(read_raw_watchlist()) == []


def test_read_raw_returns_empty_when_missing(isolated_wl):
    isolated_wl(None)
    assert _run(read_raw_watchlist()) == []


# ─── save_watchlist（檔案模式）──────────────────────────


def test_save_writes_atomically(isolated_wl, tmp_path):
    target = tmp_path / "wl.json"
    _run(save_watchlist(["AAPL", "NVDA"]))
    assert json.loads(target.read_text())["tickers"] == ["AAPL", "NVDA"]


def test_save_creates_parent_dir(monkeypatch, tmp_path):
    nested = tmp_path / "data" / "deep" / "wl.json"
    monkeypatch.setattr(settings, "watchlist_path", str(nested))
    monkeypatch.setattr(wl_mod, "_repo_seed_path", lambda: tmp_path / "no_seed.json")
    _run(save_watchlist(["AAPL"]))
    assert nested.exists()


# ─── normalize_inputs ────────────────────────────────────


def test_normalize_basic():
    valid, invalid = normalize_inputs(["aapl", "NVDA", "BRK.B"])
    assert valid == ["AAPL", "NVDA", "BRK.B"]
    assert invalid == []


def test_normalize_filters_invalid():
    valid, invalid = normalize_inputs(["AAPL", "lowercase!", "TOO_LONG_TICKER", ""])
    assert valid == ["AAPL"]
    assert invalid == ["lowercase!", "TOO_LONG_TICKER"]


def test_normalize_dedupes():
    valid, _ = normalize_inputs(["AAPL", "aapl", "AAPL"])
    assert valid == ["AAPL"]


# ─── add_tickers ─────────────────────────────────────────


def test_add_to_empty(isolated_wl):
    isolated_wl({"tickers": []})
    r = _run(add_tickers(["AAPL", "nvda"]))
    assert r.added == ["AAPL", "NVDA"]
    assert r.final_count == 2
    assert _run(read_raw_watchlist()) == ["AAPL", "NVDA"]


def test_add_skips_existing(isolated_wl):
    isolated_wl({"tickers": ["AAPL"]})
    r = _run(add_tickers(["AAPL", "NVDA"]))
    assert r.added == ["NVDA"]
    assert r.skipped_existing == ["AAPL"]
    assert r.final_count == 2


def test_add_records_invalid(isolated_wl):
    isolated_wl({"tickers": []})
    r = _run(add_tickers(["AAPL", "fake!"]))
    assert r.added == ["AAPL"]
    assert r.invalid == ["fake!"]


def test_add_respects_cap(isolated_wl):
    isolated_wl({"tickers": [f"T{i}" for i in range(MAX_WATCHLIST_SIZE)]})
    r = _run(add_tickers(["AAPL", "NVDA"]))
    assert r.added == []
    assert r.over_cap == ["AAPL", "NVDA"]
    assert r.final_count == MAX_WATCHLIST_SIZE


def test_add_no_op_does_not_write(isolated_wl, tmp_path):
    isolated_wl({"tickers": ["AAPL"]})
    target = tmp_path / "wl.json"
    mtime_before = target.stat().st_mtime
    _run(add_tickers(["AAPL"]))
    assert target.stat().st_mtime == mtime_before


# ─── remove_tickers ──────────────────────────────────────


def test_remove_existing(isolated_wl):
    isolated_wl({"tickers": ["AAPL", "NVDA", "TSLA"]})
    r = _run(remove_tickers(["nvda"]))
    assert r.removed == ["NVDA"]
    assert r.final_count == 2
    assert _run(read_raw_watchlist()) == ["AAPL", "TSLA"]


def test_remove_missing_recorded(isolated_wl):
    isolated_wl({"tickers": ["AAPL"]})
    r = _run(remove_tickers(["NVDA", "AAPL"]))
    assert r.removed == ["AAPL"]
    assert r.skipped_missing == ["NVDA"]


# ─── clear_watchlist ─────────────────────────────────────


def test_clear(isolated_wl):
    isolated_wl({"tickers": ["AAPL", "NVDA"]})
    r = _run(clear_watchlist())
    assert r.removed == ["AAPL", "NVDA"]
    assert r.final_count == 0
    assert _run(read_raw_watchlist()) == []
