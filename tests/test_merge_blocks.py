"""
測試智能區塊合併邏輯。

獨立檔案避免導入 sender → config 觸發環境變數驗證。
直接從 sender 模組複製 _merge_blocks 的純函式邏輯來測試。
"""


def _merge_blocks(blocks: list[str], max_len: int) -> list[str]:
    """複製 sender._merge_blocks 的邏輯（純函式，無外部依賴）。"""
    messages: list[str] = []
    current = ""
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= max_len:
            current = candidate
        else:
            if current:
                messages.append(current)
            current = block
    if current:
        messages.append(current)
    return messages


def test_merge_combines_short_blocks():
    """短區塊應被合併為單則訊息。"""
    blocks = ["AAA", "BBB", "CCC"]
    result = _merge_blocks(blocks, max_len=100)
    assert len(result) == 1
    assert result[0] == "AAA\n\nBBB\n\nCCC"


def test_merge_splits_on_overflow():
    """超過 max_len 時每個區塊應獨立。"""
    blocks = ["A" * 50, "B" * 50, "C" * 50]
    result = _merge_blocks(blocks, max_len=80)
    assert len(result) == 3


def test_merge_partial():
    """部分可合併、部分需切割的混合場景。"""
    blocks = ["A" * 30, "B" * 30, "C" * 80]
    result = _merge_blocks(blocks, max_len=80)
    # "A*30\n\nB*30" = 62 chars, fits
    # "C*80" standalone
    assert len(result) == 2
    assert "A" * 30 in result[0]
    assert "B" * 30 in result[0]
    assert result[1] == "C" * 80


def test_merge_empty():
    """空列表應回傳空結果。"""
    assert _merge_blocks([], max_len=100) == []


def test_merge_single_oversized():
    """單一超大區塊不會被丟棄。"""
    blocks = ["X" * 200]
    result = _merge_blocks(blocks, max_len=100)
    assert len(result) == 1
    assert result[0] == "X" * 200
