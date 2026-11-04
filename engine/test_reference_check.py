"""Reference check:scipy / statsmodels / arch 參考實作 vs 引擎每個統計檢定(可重跑、seed 固定)。

每個 check 定義在 engine/reference_check.py;這裡逐一參數化成 pytest case,失敗訊息帶誤差/備註。
完整報表:`python -m engine.reference_check`(docs/reference-check.md 的數字來源)。
參考套件只在測試側 import;缺套件時整檔 skip 並明示(CI 已安裝,見 engine-tests.yml)。
"""
import os
import sys
import warnings

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("scipy")
pytest.importorskip("statsmodels")
pytest.importorskip("arch")

from engine import reference_check as rc  # noqa: E402
from engine import statshim as ss  # noqa: E402
from engine import judge_web as jw  # noqa: E402


@pytest.mark.parametrize("check", rc.ALL_CHECKS, ids=[f.__name__ for f in rc.ALL_CHECKS])
def test_reference(check):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        res = check()
    assert res["ok"], (f"{res['name']} vs {res['reference']}: max_abs={res['max_abs_err']:.3g} "
                       f"max_rel={res['max_rel_err']:.3g} tol={res['tol']} ({res['tol_on']}) "
                       f"— {res['note']}")


# ---------------------------------------------------------------------------
# 釘死個別回歸(reference-check 找到的 bug,修後不得回退)
# ---------------------------------------------------------------------------
def test_norm_ppf_no_overflow_on_subnormal():
    for p in (1e-310, 1e-320, 5e-324):
        x = ss.norm_ppf(p)
        assert np.isfinite(x) and x < -37


def test_norm_cdf_left_tail_relative_precision():
    from scipy import stats
    for x in (-8.0, -10.0, -20.0, -37.0):
        assert abs(ss.norm_cdf(x) / stats.norm.cdf(x) - 1) < 1e-12


def test_constant_series_skew_kurtosis_documented_divergence():
    """常數序列:scipy 回 NaN(+警告),statshim 刻意回 0 / 3(常態值)——DSR 端有退化守衛,不靠它。"""
    assert ss.skew(np.full(10, 0.01)) == 0.0
    assert ss.kurtosis(np.full(10, 0.01)) == 3.0


def test_pbo_lambda_zero_counts_as_overfit():
    """奇數 N:IS 冠軍 OOS 恰為中位(λ=0)必須計入 PBO(λ ≤ 0)。N=3 時差異最大。"""
    rng = np.random.default_rng(3)
    v = 0.01 * rng.standard_normal((240, 3))
    out = jw.pbo_cscv(v, 12)
    assert out["pbo"] == rc.ref_pbo(v, 12)


def test_sortino_single_negative_is_finite():
    m = jw.compute_metrics(np.r_[0.02, -0.01, -0.01, 0.03], 252)
    assert np.isfinite(m["sortino"]) and m["sortino"] > 0


def test_analyze_rejects_constant_returns():
    from engine import analyze
    out = analyze({"mode": "returns", "returns": [0.001] * 300, "n_trials": 5,
                   "periods_per_year": 252})
    assert out["ok"] is False
    assert out["warnings_coded"][0]["code"] == "zero_variance_reject"
    assert out["verdict"]["overall"] == "inconclusive"
