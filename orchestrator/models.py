"""輸出合約（雙軌）：markdown 給人看，findings 給機器評分。從第一天就定死。

v2 起 Finding 多兩個欄位（都有預設值，向後相容）：
  source      "llm" | "static" | "graph" —— 誰產的（eval 可 per-source 切片）
  confidence  0.0–1.0 —— static 恆為 1.0；LLM 從基礎值起跳，被 adversary
              確認加權、推翻打折；synthesis 依門檻過濾
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class Finding(BaseModel):
    rule: str = Field(description="規則代號，例如 BUG / MISSING-TEST / NAMING / SECURITY / STYLE / PERF")
    file: str = ""
    line: int | None = None
    severity: str = "info"  # info | warning | error
    note: str = ""
    # 依據的團隊規則檔路徑（例如 rules/common/naming.md）；一般發現留空。
    rule_ref: str = ""
    # 依據的知識條目 id（例如 ["PR12-I1"]）。清單而非單值是刻意的：一條 finding
    # 可以同時站在兩條不變式上。只有「本次注入區塊裡真的出現過」的 id 留得下來
    # （synthesis 確定性驗證），所以這個欄位是引用率的分子，不是模型的自由欄。
    knowledge_ref: list[str] = Field(default_factory=list)
    # 具體修法建議（一兩句，可含短碼）；報告會以「↳ 建議：」呈現，選填
    suggestion: str = ""
    source: str = "llm"      # llm | static | graph
    # 產出這條 finding 的審查面向名（報告的 Dimensions 表交叉引用用；static 留空）
    dimension: str = ""
    confidence: float = 0.7


class ReviewResult(BaseModel):
    markdown: str
    findings: list[Finding] = []
    stats: dict = {}         # pipeline 各階段的統計（進 trace metadata / footer）

    def findings_summary(self) -> list[dict]:
        return [f.model_dump() for f in self.findings]
