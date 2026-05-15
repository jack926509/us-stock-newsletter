"""按鈕互動 dispatcher 測試"""

import asyncio
import json
import os

os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("FINNHUB_API_KEY", "test")
os.environ.setdefault("TAVILY_API_KEY", "test")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test")
os.environ.setdefault("SLACK_CHANNEL", "C012DAILY")

from app import slack_interactivity as si  # noqa: E402
from app.slack_blocks import (  # noqa: E402
    ACTION_WL_CLEAR_CANCEL,
    ACTION_WL_CLEAR_CONFIRM,
)


def _payload(action_id: str) -> dict:
    return {
        "type": "block_actions",
        "user": {"id": "U_TEST"},
        "channel": {"id": "C012DAILY", "name": "us-stock-daily"},
        "actions": [{"action_id": action_id, "value": ""}],
    }


def test_parse_payload_valid():
    raw = json.dumps({"foo": "bar"})
    assert si.parse_payload(raw) == {"foo": "bar"}


def test_parse_payload_invalid():
    assert si.parse_payload("not json") == {}
    assert si.parse_payload("") == {}


def _async_returning(value):
    async def _fn(*args, **kwargs):
        return value
    return _fn


def test_dispatch_cancel():
    r = asyncio.run(si.dispatch(_payload(ACTION_WL_CLEAR_CANCEL)))
    assert "已取消" in r["text"]
    assert r["replace_original"] is True


def test_dispatch_confirm_clears(monkeypatch):
    from app.data.watchlist import MutationResult

    monkeypatch.setattr(si, "read_raw_watchlist", _async_returning(["AAPL", "NVDA", "TSLA"]))
    captured = {}

    async def fake_clear():
        captured["called"] = True
        return MutationResult(removed=["AAPL", "NVDA", "TSLA"], final_count=0)

    monkeypatch.setattr(si, "clear_watchlist", fake_clear)
    r = asyncio.run(si.dispatch(_payload(ACTION_WL_CLEAR_CONFIRM)))
    assert captured.get("called") is True
    assert "3 檔" in r["text"]
    assert r["replace_original"] is True


def test_dispatch_confirm_when_already_empty(monkeypatch):
    monkeypatch.setattr(si, "read_raw_watchlist", _async_returning([]))
    called = []

    async def fake_clear():
        called.append(1)

    monkeypatch.setattr(si, "clear_watchlist", fake_clear)
    r = asyncio.run(si.dispatch(_payload(ACTION_WL_CLEAR_CONFIRM)))
    assert called == []
    assert "已經是空的" in r["text"]


def test_dispatch_unknown_action():
    r = asyncio.run(si.dispatch(_payload("mystery_action")))
    assert "mystery_action" in r["text"]


def test_dispatch_empty_actions():
    r = asyncio.run(si.dispatch({"actions": []}))
    assert "空的" in r["text"]
