"""Slack slash command 處理測試（不打網路）"""

import asyncio
import hashlib
import hmac
import os
import time

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("FINNHUB_API_KEY", "test")
os.environ.setdefault("TAVILY_API_KEY", "test")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test")
os.environ.setdefault("SLACK_CHANNEL", "C012DAILY")

from app import slack_commands as sc  # noqa: E402
from app.slack_commands import (  # noqa: E402
    channel_allowed,
    cmd_help,
    cmd_pause,
    cmd_resume,
    cmd_run,
    cmd_status,
    cmd_watchlist,
    dispatch,
    verify_slack_signature,
)
from app.state import cooldown_state, pipeline_state, scheduler_handle  # noqa: E402


def _sign(secret: str, body: bytes, ts: str) -> str:
    base = b"v0:" + ts.encode() + b":" + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    """每個 test 前清空全域狀態。"""
    cooldown_state.last_manual_trigger = 0.0
    cooldown_state.background_tasks.clear()
    pipeline_state.started_at = 0.0
    pipeline_state.finished_at = 0.0
    pipeline_state.success = None
    pipeline_state.error = None
    pipeline_state.ticker_count = 0
    scheduler_handle.scheduler = None
    yield


# ─── verify_slack_signature ──────────────────────────────


def test_signature_accepts_valid():
    secret = "secret123"
    body = b"token=x&command=/newsletter&text=run"
    ts = str(int(time.time()))
    sig = _sign(secret, body, ts)
    assert verify_slack_signature(
        signing_secret=secret, timestamp=ts, body=body, signature=sig
    ) is True


def test_signature_rejects_wrong_secret():
    body = b"token=x"
    ts = str(int(time.time()))
    sig = _sign("wrong", body, ts)
    assert verify_slack_signature(
        signing_secret="real", timestamp=ts, body=body, signature=sig
    ) is False


def test_signature_rejects_old_timestamp():
    secret = "s"
    body = b"x"
    ts = str(int(time.time()) - 60 * 10)
    sig = _sign(secret, body, ts)
    assert verify_slack_signature(
        signing_secret=secret, timestamp=ts, body=body, signature=sig
    ) is False


def test_signature_rejects_empty_secret():
    assert verify_slack_signature(
        signing_secret="", timestamp="0", body=b"x", signature="v0=anything"
    ) is False


def test_signature_rejects_non_numeric_timestamp():
    assert verify_slack_signature(
        signing_secret="s", timestamp="oops", body=b"x", signature="v0=deadbeef"
    ) is False


# ─── channel_allowed ────────────────────────────────────


def test_channel_allowed_by_id(monkeypatch):
    monkeypatch.setattr(sc.settings, "slack_channel", "C012DAILY")
    assert channel_allowed("C012DAILY", "anything") is True
    assert channel_allowed("C999OTHER", "anything") is False


def test_channel_allowed_by_name(monkeypatch):
    monkeypatch.setattr(sc.settings, "slack_channel", "#us-stock-daily")
    assert channel_allowed("C001X", "us-stock-daily") is True
    assert channel_allowed("C001X", "random") is False


def test_channel_allowed_empty(monkeypatch):
    monkeypatch.setattr(sc.settings, "slack_channel", "")
    assert channel_allowed("C001X", "anything") is False


# ─── help ───────────────────────────────────────────────


def test_help_overview_lists_all_commands():
    r = cmd_help([])
    text = r["text"]
    for cmd in ("status", "ping", "run", "pause", "resume", "watchlist"):
        assert cmd in text


def test_help_for_known_command():
    r = cmd_help(["watchlist"])
    assert "watchlist add" in r["text"]
    assert "watchlist remove" in r["text"]


def test_help_for_unknown_command():
    r = cmd_help(["nonexistent"])
    assert "nonexistent" in r["text"]


# ─── status ─────────────────────────────────────────────


def test_status_no_runs_yet():
    r = cmd_status([])
    assert "尚未跑過" in r["text"]
    assert "未曾觸發" in r["text"]


def test_status_after_success():
    pipeline_state.started_at = time.time() - 50
    pipeline_state.finished_at = time.time() - 5
    pipeline_state.success = True
    pipeline_state.ticker_count = 7
    r = cmd_status([])
    assert "✅ 成功" in r["text"]
    assert "7 檔" in r["text"]


def test_status_after_failure():
    pipeline_state.started_at = time.time() - 30
    pipeline_state.finished_at = time.time() - 10
    pipeline_state.success = False
    pipeline_state.error = "OpenAI rate limit"
    r = cmd_status([])
    assert "❌ 失敗" in r["text"]
    assert "OpenAI rate limit" in r["text"]


def test_status_in_cooldown():
    cooldown_state.last_manual_trigger = time.time() - 50
    r = cmd_status([])
    assert "冷卻剩" in r["text"]


def test_status_running():
    pipeline_state.started_at = time.time() - 12
    pipeline_state.finished_at = 0.0
    r = cmd_status([])
    assert "pipeline 運行中" in r["text"]


# ─── run ────────────────────────────────────────────────


def test_run_triggers_when_allowed():
    calls = []

    def trigger():
        calls.append(1)
        return True, "排入背景。", 0

    r = cmd_run([], trigger=trigger)
    assert calls == [1]
    assert "✅" in r["text"]


def test_run_blocked_by_cooldown():
    def trigger():
        return False, "冷卻 200 秒。", 200

    r = cmd_run([], trigger=trigger)
    assert "⏳" in r["text"]
    assert "冷卻" in r["text"]


# ─── pause / resume ────────────────────────────────────


def test_pause_without_scheduler_warns(monkeypatch):
    """scheduler_handle.scheduler is None → pause_indefinite 拋 RuntimeError。"""
    r = cmd_pause([])
    assert "⚠️" in r["text"]


def test_pause_invalid_duration():
    r = cmd_pause(["lol"])
    assert "無法解析時長" in r["text"]


def test_pause_with_duration_calls_pause_for(monkeypatch):
    captured = {}

    def fake_pause_for(seconds):
        captured["seconds"] = seconds
        from datetime import datetime
        return datetime(2026, 5, 16, 10, 0, 0)

    monkeypatch.setattr(sc, "pause_for", fake_pause_for)
    r = cmd_pause(["30m"])
    assert captured["seconds"] == 30 * 60
    assert "30 分鐘" in r["text"]
    assert "2026-05-16" in r["text"]


def test_resume_when_not_paused(monkeypatch):
    monkeypatch.setattr(sc, "is_paused", lambda: False)
    r = cmd_resume([])
    assert "並未暫停" in r["text"]


def test_resume_calls_resume(monkeypatch):
    monkeypatch.setattr(sc, "is_paused", lambda: True)
    called = []
    monkeypatch.setattr(sc, "resume", lambda: called.append(1))
    r = cmd_resume([])
    assert called == [1]
    assert "▶️" in r["text"]


# ─── watchlist sub-commands ────────────────────────────


def test_watchlist_list(monkeypatch):
    monkeypatch.setattr(sc, "read_raw_watchlist", lambda: ["AAPL", "NVDA"])
    r = cmd_watchlist([])
    assert "AAPL" in r["text"]
    assert "2 檔" in r["text"]


def test_watchlist_list_empty(monkeypatch):
    monkeypatch.setattr(sc, "read_raw_watchlist", lambda: [])
    r = cmd_watchlist([])
    assert "空的" in r["text"]


def test_watchlist_add_no_args():
    r = cmd_watchlist(["add"])
    assert "用法" in r["text"]


def test_watchlist_add_dispatches(monkeypatch):
    from app.data.watchlist import MutationResult

    def fake_add(raw):
        return MutationResult(added=["AAPL"], invalid=["bad!"], final_count=1)

    monkeypatch.setattr(sc, "add_tickers", fake_add)
    r = cmd_watchlist(["add", "aapl", "bad!"])
    assert "AAPL" in r["text"]
    assert "bad!" in r["text"]
    assert "1 檔" in r["text"]


def test_watchlist_remove_dispatches(monkeypatch):
    from app.data.watchlist import MutationResult

    def fake_remove(raw):
        return MutationResult(removed=["TSLA"], skipped_missing=["XYZ"], final_count=2)

    monkeypatch.setattr(sc, "remove_tickers", fake_remove)
    r = cmd_watchlist(["remove", "TSLA", "XYZ"])
    assert "已移除" in r["text"]
    assert "不在清單" in r["text"]


def test_watchlist_clear_returns_confirm_blocks(monkeypatch):
    monkeypatch.setattr(sc, "read_raw_watchlist", lambda: ["AAPL", "NVDA"])
    r = cmd_watchlist(["clear"])
    assert "blocks" in r
    # 必有兩個按鈕
    actions_block = next(b for b in r["blocks"] if b["type"] == "actions")
    assert len(actions_block["elements"]) == 2


def test_watchlist_clear_when_empty_skips_confirm(monkeypatch):
    monkeypatch.setattr(sc, "read_raw_watchlist", lambda: [])
    r = cmd_watchlist(["clear"])
    assert "已經是空的" in r["text"]
    assert "blocks" not in r


# ─── dispatch ───────────────────────────────────────────


def test_dispatch_empty_returns_help():
    r = asyncio.run(dispatch("", trigger=lambda: (True, "ok", 0)))
    assert "指令總覽" in r["text"]


def test_dispatch_unknown():
    r = asyncio.run(dispatch("foo bar", trigger=lambda: (True, "ok", 0)))
    assert "foo" in r["text"]


def test_dispatch_routes_to_status():
    r = asyncio.run(dispatch("status", trigger=lambda: (True, "ok", 0)))
    assert "美股日報狀態" in r["text"]
