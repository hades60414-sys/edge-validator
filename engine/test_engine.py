"""Edge Validator engine 測試。

用有 numpy/pandas(＋測試對照用 scipy)的 venv 跑:
    C:/Users/user/Desktop/auto-quant-btc/.venv/Scripts/python.exe -m pytest engine/test_engine.py -v

涵蓋:
1. statshim 對照 scipy 已知值(norm_cdf/norm_ppf/skew/kurtosis)。
2. analyze returns 模式跑通、契約鍵齊全。
3. analyze matrix 模式跑通 PBO / FWER。
4. 檢力:植入 edge 的序列 → likely-real;純雜訊 → 不 likely-real(likely-overfit/inconclusive)。
5. DSR 對照:自算 DSR vs farm judge.py 的 scipy 版誤差 < 1e-6(scipy 只在測試 import)。

引擎本身不 import scipy;此檔的 scipy import 僅供對照。
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import statshim as ss
from engine import judge_web as jw
from engine import analyze


# ---------------------------------------------------------------------------
# 契約鍵(前端依賴)
# ---------------------------------------------------------------------------
TOP_KEYS = {"ok", "warnings", "metrics", "dsr", "permutation_null", "pbo",
            "fwer", "cost_stress", "benchmark_compare", "verdict",
            "equity_curve", "benchmark_curve"}
METRIC_KEYS = {"sharpe", "sortino", "cagr", "ann_vol", "max_drawdown", "calmar",
               "n_periods", "top_bar_concentration", "final_equity"}
DSR_KEYS = {"sr_annual", "sr0", "dsr_prob", "p_value", "n_trials"}
PERM_KEYS = {"real_sharpe", "null_p95_sharpe", "p_value", "passes"}
VERDICT_KEYS = {"overall", "score_0to100", "reasons", "red_flags"}


# ===========================================================================
# 1. statshim 對照 scipy
# ===========================================================================
def test_statshim_vs_scipy():
    from scipy import stats
    ps = np.array([1e-8, 1e-4, 0.001, 0.01, 0.025, 0.1, 0.3, 0.5, 0.7, 0.9,
                   0.975, 0.99, 0.999, 1 - 1e-4, 1 - 1e-8])
    assert np.max(np.abs(ss.norm_ppf(ps) - stats.norm.ppf(ps))) < 1e-9
    xs = np.linspace(-6, 6, 300)
    assert np.max(np.abs(ss.norm_cdf(xs) - stats.norm.cdf(xs))) < 1e-12
    # 已知值
    assert abs(ss.norm_ppf(0.975) - 1.959963984540054) < 1e-9
    assert abs(ss.norm_cdf(0.0) - 0.5) < 1e-15
    rng = np.random.default_rng(0)
    r = rng.standard_t(4, 800)
    assert abs(ss.skew(r) - stats.skew(r)) < 1e-10
    assert abs(ss.kurtosis(r, fisher=False) - stats.kurtosis(r, fisher=False)) < 1e-10


# ===========================================================================
# 2. analyze returns 模式跑通
# ===========================================================================
def test_analyze_returns_mode_contract():
    rng = np.random.default_rng(1)
    r = 0.0006 + 0.01 * rng.standard_normal(400)
    out = analyze({"mode": "returns", "returns": r.tolist(),
                   "n_trials": 5, "periods_per_year": 252})
    assert out["ok"] is True
    assert TOP_KEYS.issubset(out.keys())
    assert METRIC_KEYS.issubset(out["metrics"].keys())
    assert DSR_KEYS.issubset(out["dsr"].keys())
    assert PERM_KEYS.issubset(out["permutation_null"].keys())
    assert VERDICT_KEYS.issubset(out["verdict"].keys())
    assert out["pbo"] is None            # returns 模式無 PBO
    assert out["fwer"] is None
    assert len(out["equity_curve"]) == 400
    assert out["metrics"]["n_periods"] == 400
    assert 0 <= out["verdict"]["score_0to100"] <= 100
    assert out["verdict"]["overall"] in ("likely-real", "likely-overfit", "inconclusive")


def test_analyze_with_benchmark_and_cost():
    rng = np.random.default_rng(2)
    n = 300
    r = 0.0008 + 0.012 * rng.standard_normal(n)
    bench = 0.0003 + 0.012 * rng.standard_normal(n)
    turnover = np.abs(0.2 * rng.standard_normal(n))
    out = analyze({"mode": "returns", "returns": r.tolist(),
                   "benchmark_returns": bench.tolist(),
                   "cost_bps_per_turnover": 10.0, "turnover": turnover.tolist(),
                   "periods_per_year": 252})
    assert out["benchmark_compare"] is not None
    assert set(out["benchmark_compare"]) == {"bench_sharpe", "bench_cagr",
                                             "excess_cagr", "strategy_beats"}
    assert out["cost_stress"] is not None
    assert set(out["cost_stress"]) == {"x1_sharpe", "x3_sharpe", "x6_sharpe"}
    assert out["benchmark_curve"] is not None and len(out["benchmark_curve"]) == n
    # 成本越高夏普越低(單調)
    cs = out["cost_stress"]
    assert cs["x1_sharpe"] >= cs["x3_sharpe"] >= cs["x6_sharpe"]


def test_infer_periods_from_dates():
    rng = np.random.default_rng(3)
    dates = pd.date_range("2023-01-01", periods=260, freq="B").astype(str).tolist()
    r = 0.0005 + 0.01 * rng.standard_normal(260)
    out = analyze({"mode": "returns", "returns": r.tolist(), "dates": dates})
    assert out["ok"] is True
    # 工作日序列(含週末缺口)→ 平均步距 ~1.4 天 → ~260/年
    assert 240 <= jw._infer_ppy(dates, None) <= 264
    # 週資料 → ~52
    wk = pd.date_range("2023-01-01", periods=60, freq="W").astype(str).tolist()
    assert 50 <= jw._infer_ppy(wk, None) <= 53
    # 月資料 → ~12
    mo = pd.date_range("2020-01-31", periods=48, freq="ME").astype(str).tolist()
    assert 11 <= jw._infer_ppy(mo, None) <= 13


# ===========================================================================
# 3. analyze matrix 模式跑通 PBO / FWER
# ===========================================================================
def test_analyze_matrix_mode():
    rng = np.random.default_rng(4)
    n = 250
    matrix = {}
    for i in range(8):
        matrix[f"param_{i}"] = (0.0003 + 0.011 * rng.standard_normal(n)).tolist()
    bench = (0.0002 + 0.011 * rng.standard_normal(n)).tolist()
    out = analyze({"mode": "matrix", "matrix": matrix, "n_trials": 8,
                   "benchmark_returns": bench, "periods_per_year": 252})
    assert out["ok"] is True
    assert out["pbo"] is not None
    assert "pbo" in out["pbo"] and "n_combinations" in out["pbo"]
    assert 0.0 <= out["pbo"]["pbo"] <= 1.0
    assert out["fwer"] is not None
    assert "spa" in out["fwer"] and "per_candidate" in out["fwer"]
    assert len(out["fwer"]["per_candidate"]) == 8
    for name, pc in out["fwer"]["per_candidate"].items():
        assert set(pc) >= {"pass", "rw_p_adj", "t_stat"}
    assert out["dsr"]["n_trials"] >= 8


# ===========================================================================
# 4. 檢力測試:植入 edge vs 純雜訊
# ===========================================================================
def test_power_planted_edge_is_likely_real():
    rng = np.random.default_rng(42)
    n = 500
    # 明確 edge:高年化夏普(~2.0),低成本、贏基準
    r = 0.0012 + 0.008 * rng.standard_normal(n)
    bench = 0.0001 + 0.008 * rng.standard_normal(n)
    turnover = np.abs(0.05 * rng.standard_normal(n))
    out = analyze({"mode": "returns", "returns": r.tolist(),
                   "benchmark_returns": bench.tolist(),
                   "cost_bps_per_turnover": 5.0, "turnover": turnover.tolist(),
                   "n_trials": 1, "periods_per_year": 252})
    assert out["verdict"]["overall"] == "likely-real", \
        (out["verdict"]["overall"], out["verdict"]["score_0to100"],
         out["metrics"]["sharpe"], out["permutation_null"], out["dsr"])
    assert out["verdict"]["score_0to100"] >= 62


def test_power_pure_noise_is_not_real():
    rng = np.random.default_rng(7)
    n = 400
    # 純雜訊,零漂移,還謊稱試了很多參數
    r = 0.011 * rng.standard_normal(n)
    bench = 0.011 * rng.standard_normal(n)
    out = analyze({"mode": "returns", "returns": r.tolist(),
                   "benchmark_returns": bench.tolist(),
                   "n_trials": 200, "periods_per_year": 252})
    assert out["verdict"]["overall"] != "likely-real", \
        (out["verdict"], out["dsr"], out["permutation_null"])


def test_power_overfit_matrix():
    # 很多純雜訊參數 + 挑最佳 → PBO 應偏高 / DSR 低 → 不該 likely-real
    rng = np.random.default_rng(11)
    n = 260
    matrix = {f"p{i}": (0.012 * rng.standard_normal(n)).tolist() for i in range(20)}
    bench = (0.012 * rng.standard_normal(n)).tolist()
    out = analyze({"mode": "matrix", "matrix": matrix, "n_trials": 20,
                   "benchmark_returns": bench, "periods_per_year": 252})
    assert out["verdict"]["overall"] != "likely-real", (out["verdict"], out["pbo"], out["dsr"])


# ===========================================================================
# 5. DSR 對照 farm judge.py 的 scipy 版(誤差 < 1e-6)
# ===========================================================================
def test_dsr_matches_scipy_reference():
    """自算 DSR(statshim)vs scipy 參考實作,同序列誤差 < 1e-6。"""
    from scipy import stats

    EULER = 0.5772156649015329

    def ref_expected_max_sharpe(sr_var, n_trials):
        if n_trials < 2:
            return 0.0
        sd = float(np.sqrt(max(sr_var, 0.0)))
        z1 = stats.norm.ppf(1 - 1.0 / n_trials)
        z2 = stats.norm.ppf(1 - 1.0 / (n_trials * np.e))
        return sd * ((1 - EULER) * z1 + EULER * z2)

    def ref_probabilistic_sharpe(sr_hat, sr_bench, n_obs, skew, kurt):
        denom = np.sqrt(1 - skew * sr_hat + (kurt - 1) / 4.0 * sr_hat ** 2)
        if not np.isfinite(denom) or denom == 0:
            return float("nan")
        z = (sr_hat - sr_bench) * np.sqrt(max(n_obs - 1, 1)) / denom
        return float(stats.norm.cdf(z))

    def ref_deflated_sharpe(returns, n_trials, pool):
        r = pd.Series(returns).dropna()
        std = r.std()
        sr = float(r.mean() / std) if std > 0 else 0.0
        pool = np.asarray(pool, float)
        pool = pool[np.isfinite(pool)]
        sr_var = float(np.var(pool, ddof=1)) if len(pool) > 1 else 0.0
        sr0 = ref_expected_max_sharpe(sr_var, n_trials)
        return ref_probabilistic_sharpe(
            sr, sr0, len(r),
            float(stats.skew(r)) if len(r) > 2 else 0.0,
            float(stats.kurtosis(r, fisher=False)) if len(r) > 2 else 3.0)

    rng = np.random.default_rng(99)
    max_err = 0.0
    for trial in range(6):
        n = rng.integers(120, 600)
        r = 0.0005 * rng.standard_normal() + 0.01 * rng.standard_normal(n)
        pool = 0.05 * rng.standard_normal(30)
        n_trials = int(rng.integers(2, 500))
        mine = jw.deflated_sharpe(r, n_trials, pool)["dsr"]
        ref = ref_deflated_sharpe(r, n_trials, pool)
        if np.isfinite(mine) and np.isfinite(ref):
            max_err = max(max_err, abs(mine - ref))
    assert max_err < 1e-6, f"DSR 對照最大誤差 {max_err:.2e} 未達 1e-6"


# ===========================================================================
# 邊界:空輸入 / 極短序列不崩
# ===========================================================================
def test_empty_and_short_inputs():
    assert analyze({"mode": "returns", "returns": []})["ok"] is False
    assert analyze({"mode": "matrix", "matrix": {}})["ok"] is False
    out = analyze({"mode": "returns", "returns": [0.01, -0.01, 0.02, 0.0, 0.01]})
    assert out["ok"] is True  # 短序列給出結果 + warning
    assert any("樣本" in w for w in out["warnings"])


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
