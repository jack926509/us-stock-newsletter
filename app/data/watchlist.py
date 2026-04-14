"""
自選股清單讀取器

從 repo 根目錄的 `watchlist.json` 讀取 ticker 清單，
驗證格式後回傳。檔案不存在、JSON 壞掉或清單為空時 fallback 到
`DEFAULT_WATCHLIST`，並寫 warning log，絕不中斷 pipeline。

使用者於 GitHub 網頁直接編輯 watchlist.json → Zeabur webhook 自動重新部署，
之後排程或手動觸發的每次 pipeline 都會即時讀到最新清單。
"""

import json
import re
from pathlib import Path

from app.config import DEFAULT_WATCHLIST, MAX_WATCHLIST_SIZE, log, settings

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def load_watchlist() -> list[str]:
    """讀取 watchlist.json，失敗時 fallback 到 DEFAULT_WATCHLIST。"""
    path = Path(settings.watchlist_path)
    if not path.is_absolute():
        # 相對路徑以 repo 根目錄為基準
        path = Path(__file__).resolve().parents[2] / path

    if not path.exists():
        log.warning("watchlist.json 不存在 (%s)，使用預設清單 %s", path, DEFAULT_WATCHLIST)
        return list(DEFAULT_WATCHLIST)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.error("解析 watchlist.json 失敗: %s，使用預設清單", e)
        return list(DEFAULT_WATCHLIST)

    raw = data.get("tickers") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        log.warning("watchlist.json 格式錯誤（缺少 tickers 陣列），使用預設清單")
        return list(DEFAULT_WATCHLIST)

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

    if not valid:
        log.warning("watchlist.json 無有效 ticker，使用預設清單")
        return list(DEFAULT_WATCHLIST)

    log.info("📋 讀取 watchlist: %s", valid)
    return valid
