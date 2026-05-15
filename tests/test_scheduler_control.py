"""scheduler_control 模組測試（不啟動真實 APScheduler）"""

import os
from datetime import datetime
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("FINNHUB_API_KEY", "test")
os.environ.setdefault("TAVILY_API_KEY", "test")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test")
os.environ.setdefault("SLACK_CHANNEL", "C012DAILY")

from app import scheduler_control as sc  # noqa: E402
from app.state import scheduler_handle  # noqa: E402


# ─── parse_duration ─────────────────────────────────────


@pytest.mark.parametrize("inp,expected_sec,expected_label", [
    ("30m", 1800, "30 分鐘"),
    ("2h", 7200, "2 小時"),
    ("1d", 86400, "1 天"),
    ("60m", 3600, "60 分鐘"),
    ("5M", 300, "5 分鐘"),  # 大寫單位也接受
])
def test_parse_duration_valid(inp, expected_sec, expected_label):
    seconds, label = sc.parse_duration(inp)
    assert seconds == expected_sec
    assert label == expected_label


@pytest.mark.parametrize("inp", ["", "30", "lol", "0m", "-5h", "30s", "abc30m"])
def test_parse_duration_invalid(inp):
    seconds, label = sc.parse_duration(inp)
    assert seconds is None
    assert label is None


# ─── pause / resume 透過 mock scheduler ──────────────


@pytest.fixture
def mock_scheduler(monkeypatch):
    sched = MagicMock()
    sched.get_job.return_value = None
    monkeypatch.setattr(scheduler_handle, "scheduler", sched)
    yield sched
    monkeypatch.setattr(scheduler_handle, "scheduler", None)


def test_pause_indefinite_calls_scheduler(mock_scheduler):
    sc.pause_indefinite()
    mock_scheduler.pause_job.assert_called_once_with(scheduler_handle.job_id)


def test_pause_indefinite_removes_existing_auto_resume(mock_scheduler):
    mock_scheduler.get_job.return_value = MagicMock()  # auto-resume 存在
    sc.pause_indefinite()
    mock_scheduler.remove_job.assert_called_once_with(scheduler_handle.auto_resume_job_id)


def test_pause_for_schedules_auto_resume(mock_scheduler):
    resume_at = sc.pause_for(60 * 30)
    assert isinstance(resume_at, datetime)
    mock_scheduler.pause_job.assert_called_once()
    mock_scheduler.add_job.assert_called_once()
    # 應該以 auto_resume_job_id 排程，且 replace_existing=True
    kwargs = mock_scheduler.add_job.call_args.kwargs
    assert kwargs["id"] == scheduler_handle.auto_resume_job_id
    assert kwargs["replace_existing"] is True


def test_resume_removes_pending_auto_resume(mock_scheduler):
    mock_scheduler.get_job.return_value = MagicMock()
    sc.resume()
    mock_scheduler.resume_job.assert_called_once_with(scheduler_handle.job_id)
    mock_scheduler.remove_job.assert_called_once_with(scheduler_handle.auto_resume_job_id)


def test_resume_no_op_when_no_auto_resume(mock_scheduler):
    mock_scheduler.get_job.return_value = None
    sc.resume()
    mock_scheduler.remove_job.assert_not_called()


def test_is_paused_when_next_run_none(mock_scheduler):
    job = MagicMock()
    job.next_run_time = None
    mock_scheduler.get_job.return_value = job
    assert sc.is_paused() is True


def test_is_paused_when_running(mock_scheduler):
    job = MagicMock()
    job.next_run_time = datetime(2026, 5, 16)
    mock_scheduler.get_job.return_value = job
    assert sc.is_paused() is False


def test_is_paused_no_scheduler():
    scheduler_handle.scheduler = None
    assert sc.is_paused() is False


def test_pause_without_scheduler_raises():
    scheduler_handle.scheduler = None
    with pytest.raises(RuntimeError):
        sc.pause_indefinite()
    with pytest.raises(RuntimeError):
        sc.pause_for(60)
    with pytest.raises(RuntimeError):
        sc.resume()
