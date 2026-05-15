"""Slack slash command 處理測試（不打網路）"""

import hashlib
import hmac
import os
import time

os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("FINNHUB_API_KEY", "test")
os.environ.setdefault("TAVILY_API_KEY", "test")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test")
os.environ.setdefault("SLACK_CHANNEL", "C012DAILY")

from app.slack_commands import (  # noqa: E402
    channel_allowed,
    cmd_help,
    cmd_status,
    cmd_unknown,
    cmd_watchlist,
    verify_slack_signature,
)


def _sign(secret: str, body: bytes, ts: str) -> str:
    base = b"v0:" + ts.encode() + b":" + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


# ── verify_slack_signature ─────────────────────────────────────

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
    ts = str(int(time.time()) - 60 * 10)  # 10 分鐘前
    sig = _sign(secret, body, ts)
    assert verify_slack_signature(
        signing_secret=secret, timestamp=ts, body=body, signature=sig
    ) is False


def test_signature_rejects_empty_secret():
    body = b"x"
    ts = str(int(time.time()))
    assert verify_slack_signature(
        signing_secret="", timestamp=ts, body=body, signature="v0=anything"
    ) is False


def test_signature_rejects_non_numeric_timestamp():
    secret = "s"
    body = b"x"
    sig = "v0=deadbeef"
    assert verify_slack_signature(
        signing_secret=secret, timestamp="not-a-number", body=body, signature=sig
    ) is False


# ── channel_allowed ────────────────────────────────────────────

def test_channel_allowed_by_id(monkeypatch):
    from app import slack_commands
    monkeypatch.setattr(slack_commands.settings, "slack_channel", "C012DAILY")
    assert channel_allowed("C012DAILY", "some-name") is True
    assert channel_allowed("C999OTHER", "some-name") is False


def test_channel_allowed_by_name(monkeypatch):
    from app import slack_commands
    monkeypatch.setattr(slack_commands.settings, "slack_channel", "#us-stock-daily")
    assert channel_allowed("C001X", "us-stock-daily") is True
    assert channel_allowed("C001X", "random") is False


def test_channel_allowed_empty_setting(monkeypatch):
    from app import slack_commands
    monkeypatch.setattr(slack_commands.settings, "slack_channel", "")
    assert channel_allowed("C001X", "anything") is False


# ── command responses ──────────────────────────────────────────

def test_cmd_help_is_ephemeral():
    r = cmd_help()
    assert r["response_type"] == "ephemeral"
    assert "/newsletter run" in r["text"]
    assert "/newsletter status" in r["text"]


def test_cmd_unknown_includes_usage():
    r = cmd_unknown("foo")
    assert "foo" in r["text"]
    assert "/newsletter help" in r["text"]


def test_cmd_status_no_trigger():
    r = cmd_status(
        next_run="2026-05-16 08:00:00+08:00",
        last_trigger_ts=0.0,
        bg_task_count=0,
        cooldown_seconds=300,
    )
    assert r["response_type"] == "ephemeral"
    assert "未曾觸發" in r["text"]
    assert "閒置" in r["text"]
    assert "2026-05-16" in r["text"]


def test_cmd_status_in_cooldown():
    r = cmd_status(
        next_run=None,
        last_trigger_ts=time.time() - 100,  # 100 秒前
        bg_task_count=1,
        cooldown_seconds=300,
    )
    assert "冷卻中" in r["text"]
    assert "正在跑" in r["text"]
    assert "(無)" in r["text"]


def test_cmd_watchlist(monkeypatch):
    from app import slack_commands
    monkeypatch.setattr(slack_commands, "load_watchlist", lambda: ["AAPL", "NVDA"])
    r = cmd_watchlist()
    assert "AAPL" in r["text"]
    assert "NVDA" in r["text"]
    assert "2 檔" in r["text"]


def test_cmd_watchlist_empty(monkeypatch):
    from app import slack_commands
    monkeypatch.setattr(slack_commands, "load_watchlist", lambda: [])
    r = cmd_watchlist()
    assert "空的" in r["text"]


def test_cmd_watchlist_handles_load_failure(monkeypatch):
    from app import slack_commands

    def _boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(slack_commands, "load_watchlist", _boom)
    r = cmd_watchlist()
    assert "讀取失敗" in r["text"]
    assert "disk full" in r["text"]
