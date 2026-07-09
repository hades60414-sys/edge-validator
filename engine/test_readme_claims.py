# -*- coding: utf-8 -*-
"""README 門面數字對帳測試:README 宣稱的測試數/行數 vs 實際,漂移即紅。

產品靈魂=誠實。門面(README)上機器可驗的數字,不允許與現實漂移:
- 「N 個測試」        ↔ pytest 對 engine/ 的實際收集數(subprocess --collect-only,含本檔)
- 「約 N 行」         ↔ 實際行數(宣稱帶「約」→ 容忍 ±5%)
- 「129 行」/"129-line"(不帶「約」的精確宣稱)↔ 實際行數,零容忍
任一邊改了(README 亂寫數字,或程式碼漂移出容忍帶),本測試變紅,強迫兩邊對齊。
CI 的快層/慢層都跑 `pytest engine`,本檔自動被涵蓋,無需改 workflow。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


def _readme_text() -> str:
    return README.read_text(encoding="utf-8")


def _line_count(rel: str) -> int:
    return len((ROOT / rel).read_text(encoding="utf-8").splitlines())


def _claimed(pattern: str, text: str) -> int:
    """抓 README 宣稱數字;宣稱不見了也算紅(門面聲明不得無聲蒸發)。"""
    m = re.search(pattern, text)
    assert m, f"README 找不到宣稱樣式:{pattern!r}(宣稱被刪除或改寫,請同步更新本測試)"
    return int(m.group(1).replace(",", ""))


def test_readme_engine_test_count_matches_pytest_collect():
    """「N 個測試」必須等於 pytest 對 engine/ 的實際收集數(精確,零容忍)。"""
    claimed = _claimed(r"([\d,]+)\s*個測試", _readme_text())
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "engine", "--collect-only", "-q",
         "-p", "no:cacheprovider"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=300,
    )
    m = re.search(r"(\d+)(?:/\d+)?\s+tests?\s+collected", proc.stdout)
    assert m, f"無法從 pytest --collect-only 輸出解析測試數:\n{proc.stdout[-800:]}"
    actual = int(m.group(1))
    assert claimed == actual, (
        f"README 宣稱 {claimed} 個測試,pytest 實際收集 {actual} 個——門面數字漂移,請更新 README。"
    )


def test_readme_judge_web_line_claim_within_tolerance():
    """「engine/judge_web.py,約 N 行」:帶「約」→ 實際行數 ±5% 內。"""
    claimed = _claimed(r"judge_web\.py`?[,,]\s*約\s*([\d,]+)\s*行", _readme_text())
    actual = _line_count("engine/judge_web.py")
    assert abs(claimed - actual) <= max(1, round(actual * 0.05)), (
        f"README 宣稱 judge_web.py 約 {claimed} 行,實際 {actual} 行(容忍 ±5%)——請更新 README。"
    )


def test_readme_appjs_line_claim_within_tolerance():
    """「app.js 約 N 行」:帶「約」→ 實際行數 ±5% 內。"""
    claimed = _claimed(r"app\.js`?\s*約\s*([\d,]+)\s*行", _readme_text())
    actual = _line_count("app.js")
    assert abs(claimed - actual) <= max(1, round(actual * 0.05)), (
        f"README 宣稱 app.js 約 {claimed} 行,實際 {actual} 行(容忍 ±5%)——請更新 README。"
    )


def test_readme_statshim_line_claims_exact():
    """statshim 的行數宣稱不帶「約」(中文「N 行換掉」與英文 "N-line")→ 精確,零容忍。"""
    text = _readme_text()
    actual = _line_count("engine/statshim.py")
    zh = _claimed(r"([\d,]+)\s*行換掉整個 scipy", text)
    en = _claimed(r"A\s+([\d,]+)-line\s+pure-numpy", text)
    assert zh == actual, f"README(中)宣稱 statshim {zh} 行,實際 {actual} 行——請更新 README。"
    assert en == actual, f"README(英)宣稱 statshim {en}-line,實際 {actual} 行——請更新 README。"
