"""
AI 模組共用例外類。

獨立檔案避免 writer/editor 反向 import planner 造成的耦合。
"""


class AIGenerationError(Exception):
    """AI 生成過程的可預期失敗（API 錯誤、結構驗證失敗、輸出截斷等）。"""
