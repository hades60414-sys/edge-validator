# -*- coding: utf-8 -*-
"""偵測下沉層(engine.judge_web §8 detect_and_convert)的釘死測試。

R12 MED 修:JS 的淨值/報酬 heuristic 與民國年正規化原本零測試、默默轉換資料。
此檔用 pytest 把 Python 權威版的行為逐案釘死:
- 漲跌報酬序列 → 'returns' 原樣
- 單調淨值 → 'nav' → 轉逐期報酬
- 含負淨值邊界 → 保守判 'returns'(不做會爆炸的 nav 轉換)
- 民國年 / YYYYMMDD / 日內時間戳 正規化
- detect_and_convert 端到端:覆蓋 payload、hint 不一致出 warning、無 raw 原樣通過
"""
import math

import numpy as np
import pytest

from engine.judge_web import (
    detect_and_convert,
    detect_series_kind,
    nav_to_returns,
    normalize_date_str,
)


# ---------------------------------------------------------------------------
# detect_series_kind
# ---------------------------------------------------------------------------
class TestDetectSeriesKind:
    def test_mixed_sign_small_returns(self):
        # 典型漲跌報酬:小量級、有正有負
        vals = [0.01, -0.005, 0.008, 0.002, -0.003, 0.006]
        assert detect_series_kind(vals) == "returns"

    def test_monotone_nav_large(self):
        # 單調上升的大數淨值(如帳戶權益 100 → 110)
        vals = [100.0, 101.5, 102.2, 103.0, 104.8, 106.1]
        assert detect_series_kind(vals) == "nav"

    def test_nav_near_one_monotoneish(self):
        # 1.0x 附近、相當單調 → 淨值
        vals = [1.00, 1.01, 1.02, 1.015, 1.03, 1.04, 1.05]
        assert detect_series_kind(vals) == "nav"

    def test_nav_with_negative_value_boundary(self):
        # 含負淨值邊界:量級大 + 出現負值 → 保守判 returns,
        # 因 nav→returns 跨零會產生無意義爆炸報酬;檢定端自然懲罰這種資料。
        vals = [100.0, 60.0, -10.0, 20.0, 35.0]
        assert detect_series_kind(vals) == "returns"

    def test_too_short_defaults_returns(self):
        assert detect_series_kind([100.0, 101.0]) == "returns"
        assert detect_series_kind([]) == "returns"
        assert detect_series_kind([None, float("nan"), 1.0]) == "returns"

    def test_percent_style_returns_all_positive_but_jagged(self):
        # 全正但小量級且不單調(單調比例 ≤0.55)→ returns,不誤判為淨值
        vals = [0.01, 0.002, 0.008, 0.001, 0.009, 0.003, 0.007, 0.002]
        # 單調比例 = 3/7 ≈ 0.43
        assert detect_series_kind(vals) == "returns"


# ---------------------------------------------------------------------------
# nav_to_returns
# ---------------------------------------------------------------------------
class TestNavToReturns:
    def test_basic_conversion(self):
        r = nav_to_returns([100.0, 110.0, 99.0])
        assert r[0] == 0.0
        assert r[1] == pytest.approx(0.10)
        assert r[2] == pytest.approx(99.0 / 110.0 - 1.0)

    def test_zero_prev_gives_nan(self):
        # R19 必修5b:prev==0 除以 0 無法定義報酬——舊行為記 0.0 = 憑空捏造「持平日」
        # 稀釋波動,改 NaN 傳播、交 analyze 入口的缺值守衛(<5% 剔除揭露、≥5% 拒審)。
        r = nav_to_returns([0.0, 50.0, 100.0])
        assert r[0] == 0.0 and math.isnan(r[1]) and r[2] == pytest.approx(1.0)

    def test_nan_propagates_fail_closed(self):
        # R17 必修1:NaN/None 不再填 0(填 0 = 憑空捏造持平日、稀釋波動)——
        # 缺失淨值讓自身與下一期報酬都成 NaN,交由 analyze 入口守衛剔除/拒審。
        r = nav_to_returns([100.0, None, 110.0])
        assert r[0] == 0.0
        assert math.isnan(r[1]) and math.isnan(r[2])


# ---------------------------------------------------------------------------
# normalize_date_str(民國年正規化下沉)
# ---------------------------------------------------------------------------
class TestNormalizeDate:
    def test_roc_three_digit_year(self):
        assert normalize_date_str("113/01/05") == "2024-01-05"

    def test_roc_three_digit_year_dash(self):
        assert normalize_date_str("099-12-31") == "2010-12-31"

    def test_two_digit_year_passthrough_like_js(self):
        # 2 位年不在 JS/引擎的民國年判準內(regex 要求 3-4 位年)→ 原樣通過,行為一致
        assert normalize_date_str("99-12-31") == "99-12-31"

    def test_gregorian_passthrough(self):
        assert normalize_date_str("2024-01-05") == "2024-01-05"
        assert normalize_date_str("2024/1/5") == "2024-01-05"

    def test_yyyymmdd(self):
        assert normalize_date_str("20240105") == "2024-01-05"

    def test_intraday_timestamp_kept(self):
        assert normalize_date_str("113/01/05 09:30") == "2024-01-05 09:30"
        assert normalize_date_str("2024-01-05T09:30:15") == "2024-01-05 09:30:15"

    def test_unparseable_passthrough(self):
        assert normalize_date_str("hello") == "hello"


# ---------------------------------------------------------------------------
# detect_and_convert 端到端
# ---------------------------------------------------------------------------
class TestDetectAndConvert:
    def test_no_raw_passthrough_identical(self):
        payload = {"mode": "returns", "returns": [0.01, -0.02], "dates": None}
        out = detect_and_convert(payload)
        assert out is payload  # 無 raw:原物返回,零行為改變

    def test_nav_values_converted_authoritatively(self):
        nav = [100.0, 105.0, 102.9, 110.0, 111.1]
        payload = {
            "mode": "returns",
            "returns": [0.0] * 5,  # 前端算的(這裡故意放錯,證明引擎覆蓋)
            "dates": None,
            "raw": {"values": nav, "js_kind": "nav", "dates": None},
        }
        out = detect_and_convert(payload)
        exp = nav_to_returns(nav)
        assert out["returns"] == pytest.approx(exp)
        assert out["_detect_warnings"] == []  # hint 一致 → 無警告

    def test_kind_mismatch_warns_and_engine_wins(self):
        nav = [100.0, 101.0, 102.0, 103.5, 105.0]
        payload = {
            "mode": "returns",
            "returns": nav,  # 前端誤判 returns,原樣送
            "dates": None,
            "raw": {"values": nav, "js_kind": "returns", "dates": None},
        }
        out = detect_and_convert(payload)
        # 引擎權威判 nav → 轉報酬,且 warning 告知不一致
        assert out["returns"] == pytest.approx(nav_to_returns(nav))
        codes = [w["code"] for w in out["_detect_warnings_coded"]]
        assert "detect_kind_mismatch" in codes
        params = out["_detect_warnings_coded"][0]["params"]
        assert params["js"] == "returns" and params["py"] == "nav"

    def test_roc_dates_normalized_and_mismatch_warned(self):
        payload = {
            "mode": "returns",
            "returns": [0.01, -0.01, 0.02],
            "dates": ["113/01/05", "113/01/08", "113/01/09"],  # 前端沒轉(模擬缺陷)
            "raw": {
                "values": [0.01, -0.01, 0.02],
                "js_kind": "returns",
                "dates": ["113/01/05", "113/01/08", "113/01/09"],
            },
        }
        out = detect_and_convert(payload)
        assert out["dates"] == ["2024-01-05", "2024-01-08", "2024-01-09"]
        codes = [w["code"] for w in out["_detect_warnings_coded"]]
        assert "detect_dates_mismatch" in codes

    def test_dates_agreement_no_warning(self):
        payload = {
            "mode": "returns",
            "returns": [0.01, -0.01, 0.02],
            "dates": ["2024-01-05", "2024-01-08", "2024-01-09"],  # 前端已正確轉
            "raw": {
                "values": [0.01, -0.01, 0.02],
                "js_kind": "returns",
                "dates": ["113/01/05", "113/01/08", "113/01/09"],
            },
        }
        out = detect_and_convert(payload)
        assert out["dates"] == ["2024-01-05", "2024-01-08", "2024-01-09"]
        assert out["_detect_warnings"] == []

    def test_matrix_per_column_conversion(self):
        rmat = {
            "nav_col": [1.00, 1.02, 1.05, 1.06, 1.10],
            "ret_col": [0.01, -0.02, 0.015, 0.0, -0.005],
        }
        payload = {
            "mode": "matrix",
            "matrix": {"nav_col": [0.0] * 5, "ret_col": [9.9] * 5},  # 故意錯,證明覆蓋
            "dates": None,
            "raw": {"matrix": rmat, "js_kinds": {"nav_col": "nav", "ret_col": "returns"},
                    "dates": None},
        }
        out = detect_and_convert(payload)
        assert out["matrix"]["nav_col"] == pytest.approx(nav_to_returns(rmat["nav_col"]))
        assert out["matrix"]["ret_col"] == pytest.approx(rmat["ret_col"])
        assert out["_detect_warnings"] == []

    def test_negative_nav_boundary_not_converted(self):
        vals = [100.0, 60.0, -10.0, 20.0, 35.0]
        payload = {
            "mode": "returns", "returns": vals, "dates": None,
            "raw": {"values": vals, "js_kind": "returns", "dates": None},
        }
        out = detect_and_convert(payload)
        assert out["returns"] == pytest.approx(vals)  # 判 returns:原樣(清洗後)
        assert out["_detect_warnings"] == []

    def test_pure_function_input_untouched(self):
        nav = [100.0, 101.0, 102.0, 103.0]
        payload = {
            "mode": "returns", "returns": list(nav), "dates": None,
            "raw": {"values": nav, "js_kind": "nav", "dates": None},
        }
        before = list(payload["returns"])
        _ = detect_and_convert(payload)
        assert payload["returns"] == before  # 入參不被就地修改

    def test_end_to_end_with_analyze(self):
        # 淨值 CSV 全鏈路:detect_and_convert → analyze,期末淨值須等於 nav 比值
        from engine import analyze
        rng = np.random.default_rng(7)
        nav = list(np.cumprod(1.0 + rng.normal(0.001, 0.01, 120)) * 100.0)
        payload = {
            "mode": "returns", "returns": [0.0] * len(nav),
            "dates": None, "n_trials": 1, "periods_per_year": 252,
            "raw": {"values": nav, "js_kind": "nav", "dates": None},
        }
        p2 = detect_and_convert(payload)
        out = analyze(p2)
        assert out["ok"]
        assert out["metrics"]["final_equity"] == pytest.approx(nav[-1] / nav[0], rel=1e-9)
