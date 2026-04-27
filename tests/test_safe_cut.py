"""
測試 _find_safe_cut 的切割優先順序：
段落 (\\n\\n) > 行 (\\n) > 標籤閉合後 (`> `) > 任何 `>` > 硬切。

獨立檔案避免 import sender 時觸發 config 環境變數驗證。
"""


def _find_safe_cut(text: str, max_len: int) -> int:
    cut = text.rfind("\n\n", 0, max_len)
    if cut != -1:
        return cut
    cut = text.rfind("\n", 0, max_len)
    if cut != -1:
        return cut
    cut = text.rfind("> ", 0, max_len)
    if cut != -1:
        return cut + 1
    cut = text.rfind(">", 0, max_len)
    if cut != -1:
        return cut + 1
    return max_len


def test_prefers_paragraph_boundary():
    text = "AAA\n\nBBB\nCCC <b>x</b> DDD"
    cut = _find_safe_cut(text, max_len=20)
    assert text[cut : cut + 2] == "\n\n" or text[:cut] == "AAA"


def test_falls_back_to_line():
    text = "AAA<b>X</b>\nBBB<b>Y</b>"
    cut = _find_safe_cut(text, max_len=15)
    # 沒有 \n\n，應該選 \n
    assert text[cut] == "\n"


def test_falls_back_to_tag_close_with_space():
    """無換行時，應選擇 `> ` 邊界（標籤閉合後）。"""
    text = "<b>AAA</b> <b>BBB</b> <b>CCC</b>"
    cut = _find_safe_cut(text, max_len=15)
    # 不該切在標籤內部
    assert "<" not in text[cut - 1 : cut] or text[cut - 1] == ">"
    # 切點之前不該有未閉合的 `<`
    before = text[:cut]
    assert before.count("<") == before.count(">")


def test_falls_back_to_any_tag_close():
    """連 `> ` 都沒有時，仍要在 `>` 之後切。"""
    text = "<b>AAA</b><b>BBB</b><b>CCC</b>"
    cut = _find_safe_cut(text, max_len=15)
    before = text[:cut]
    assert before.count("<") == before.count(">")


def test_hard_cut_when_nothing_matches():
    """無任何邊界線索時退到硬切（接受瑕疵）。"""
    text = "X" * 100
    cut = _find_safe_cut(text, max_len=50)
    assert cut == 50
