"""Edge Validator engine 測試。

用有 numpy/pandas(＋測試對照用 scipy)的 venv 跑:
    <numpy-pandas-venv>/python -m pytest engine/test_engine.py -v

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
# 4.5 matrix n_trials 硬化(★engine 內建防雜訊放水,R4 搬入的單一真源★)
# ===========================================================================
def test_engine_matrix_hardening_kills_public_path_noise():
    """公開站 app.js 送的 matrix payload(n_trials=1 UI 預設)——engine 自己硬化,
    20 欄純高斯雜訊【不得】被判 likely-real。這是公開站放水的直接回歸鎖。"""
    leaks = 0
    for s in range(60):
        rng = np.random.default_rng(s)
        n = 260
        matrix = {f"策略{i+1}": (0.012 * rng.standard_normal(n)).tolist() for i in range(20)}
        # 模擬公開 app.js matrix 上傳:mode=matrix、n_trials 用 UI 預設 1、無日期
        out = analyze({"mode": "matrix", "dates": None, "returns": None, "matrix": matrix,
                       "n_trials": 1, "periods_per_year": None, "benchmark_returns": None,
                       "cost_bps_per_turnover": None, "turnover": None})
        if out["verdict"]["overall"] == "likely-real":
            leaks += 1
    assert leaks == 0, f"公開站路徑 20 欄雜訊放水 {leaks}/60 判 likely-real(硬化失效)"


def test_engine_hardening_reports_diag_and_escalates():
    """硬化診斷齊全:雜訊矩陣 → hardened=True 且用的 n_trials 高於誠實 base(欄數)。"""
    rng = np.random.default_rng(20260709)
    n = 260
    matrix = {f"c{i}": (0.012 * rng.standard_normal(n)).tolist() for i in range(20)}
    out = analyze({"mode": "matrix", "matrix": matrix, "n_trials": 1})
    hd = out["dsr"]["harden"]
    assert hd is not None
    assert set(hd) >= {"bar", "noise_floor", "base_n_trials", "dsr_at_base",
                       "dsr_final", "hardened"}
    assert hd["base_n_trials"] == 20               # 誠實基準=欄數
    assert hd["hardened"] is True                  # 雜訊被通縮上調
    assert out["dsr"]["n_trials"] > 20             # 有效 n_trials 高於誠實 base
    assert hd["dsr_final"] <= hd["dsr_at_base"] + 1e-9  # 通縮方向正確(DSR 不反升)


def test_engine_hardening_keeps_true_edge_honest():
    """真 edge 矩陣(DSR≥0.95):維持誠實 n_trials=欄數、hardened=False、仍 likely-real。"""
    rng = np.random.default_rng(1000)
    n = 500
    matrix = {f"p{i}": (0.0012 + 0.008 * rng.standard_normal(n)).tolist() for i in range(20)}
    bench = (0.0001 + 0.008 * rng.standard_normal(n)).tolist()
    out = analyze({"mode": "matrix", "matrix": matrix, "n_trials": 1,
                   "benchmark_returns": bench, "periods_per_year": 252})
    hd = out["dsr"]["harden"]
    assert hd["hardened"] is False                 # 真 edge 不被多罰
    assert out["dsr"]["n_trials"] == 20            # 維持誠實 n_trials
    assert out["dsr"]["dsr_prob"] >= 0.95
    assert out["verdict"]["overall"] == "likely-real"


def test_engine_returns_mode_unaffected_by_hardening():
    """單序列(mode=returns)路徑【完全不受硬化影響】:harden 診斷為 None、n_trials 原樣。"""
    rng = np.random.default_rng(1)
    r = 0.0006 + 0.01 * rng.standard_normal(400)
    out = analyze({"mode": "returns", "returns": r.tolist(),
                   "n_trials": 5, "periods_per_year": 252})
    assert out["dsr"]["harden"] is None            # returns 模式不硬化
    assert out["dsr"]["n_trials"] == 5             # n_trials 原樣不被上調


# ===========================================================================
# 4.6 封頂逃逸 fail-closed(★R5:雜訊硬化封頂缺口★)
# ===========================================================================
def test_capped_escape_specific_seed_fails_closed():
    """R5 掃描找到的具體封頂逃逸點:K=50 欄 × N=120 期 純高斯雜訊,某 seed 下硬化放大到
    封頂(32×=1600 試驗)DSR 仍 =0.634 ≥ 0.60 雜訊地板。修前判 likely-real(放水),
    修後必須 fail-closed(不判 likely-real),且 harden 診斷帶 capped_escape=True 與殘餘 DSR。"""
    K, N, s = 50, 120, 30
    rng = np.random.default_rng(s * 100003 + K * 7 + N)
    matrix = {f"c{i}": (0.012 * rng.standard_normal(N)).tolist() for i in range(K)}
    out = analyze({"mode": "matrix", "matrix": matrix, "n_trials": 1, "periods_per_year": 252})
    hd = out["dsr"]["harden"]
    assert hd["capped_escape"] is True                       # 封頂逃逸被標記
    assert hd["hardened"] is True
    assert out["dsr"]["n_trials"] == K * 32                  # 已放大到封頂上限
    assert hd["dsr_final"] >= jw.NOISE_FLOOR                 # 殘餘 DSR 仍站在雜訊地板上
    assert out["verdict"]["overall"] != "likely-real"       # fail-closed:不判為真
    assert any("封頂逃逸" in f for f in out["verdict"]["red_flags"])


def test_capped_escape_never_leaks_across_extreme_grid():
    """極端 K×短樣本全網格掃描:凡是硬化放大到封頂上限、殘餘 DSR 仍 ≥ 雜訊地板者
    (capped_escape=True),【一律不得】被判 likely-real。附殘餘 DSR 供揭露。"""
    Ks = [50, 100, 200, 500]
    Ns = [60, 120, 260]
    capped_seen = 0
    capped_leaks = 0
    max_residual_dsr = 0.0
    for K in Ks:
        for N in Ns:
            for s in range(40):
                rng = np.random.default_rng(s * 100003 + K * 7 + N)
                matrix = {f"c{i}": (0.012 * rng.standard_normal(N)).tolist() for i in range(K)}
                out = analyze({"mode": "matrix", "matrix": matrix, "n_trials": 1,
                               "periods_per_year": 252})
                hd = out["dsr"]["harden"]
                if hd and hd["capped_escape"]:
                    capped_seen += 1
                    max_residual_dsr = max(max_residual_dsr, hd["dsr_final"])
                    # fail-closed 核心保證:封頂逃逸者絕不 likely-real
                    if out["verdict"]["overall"] == "likely-real":
                        capped_leaks += 1
    assert capped_seen > 0, "掃描網格未觸發任何封頂逃逸(測試無效,需含極端 K×短樣本)"
    assert capped_leaks == 0, (
        f"封頂逃逸放水 {capped_leaks}/{capped_seen} 判 likely-real"
        f"(殘餘 DSR 最高 {max_residual_dsr:.3f})")


def test_capped_escape_flag_off_for_normal_noise_and_true_edge():
    """capped_escape 只在『放大到封頂仍 ≥ 地板』時觸發;一般雜訊(硬化即跌破地板)與
    真 edge(DSR≥0.95 不硬化)都不得誤設此旗,以免濫殺。"""
    # 一般雜訊:20 欄 × 260 期,硬化通常在中間級就跌破 0.60
    rng = np.random.default_rng(11)
    matrix = {f"p{i}": (0.012 * rng.standard_normal(260)).tolist() for i in range(20)}
    out_noise = analyze({"mode": "matrix", "matrix": matrix, "n_trials": 20})
    assert out_noise["dsr"]["harden"]["capped_escape"] is False
    # 真 edge:DSR≥0.95 早退,未硬化,capped_escape 必為 False
    rng = np.random.default_rng(1000)
    matrix = {f"p{i}": (0.0012 + 0.008 * rng.standard_normal(500)).tolist() for i in range(20)}
    bench = (0.0001 + 0.008 * rng.standard_normal(500)).tolist()
    out_edge = analyze({"mode": "matrix", "matrix": matrix, "n_trials": 1,
                        "benchmark_returns": bench, "periods_per_year": 252})
    assert out_edge["dsr"]["harden"]["capped_escape"] is False
    assert out_edge["verdict"]["overall"] == "likely-real"   # 真 edge 不被 fail-closed 誤殺


def test_high_k_short_n_emits_sample_adequacy_warning():
    """欄數 ≥ 樣本期數 且 DSR 在誠實基準即 ≥0.95(硬化不觸發)→ 檢定力懸崖,須加誠實警語
    (不動 verdict,避免誤殺真 edge)。用 R5 掃描找到的 K=100×N=60 seed70 雜訊贏家。"""
    K, N, s = 100, 60, 70
    rng = np.random.default_rng(s * 100003 + K * 7 + N)
    matrix = {f"c{i}": (0.012 * rng.standard_normal(N)).tolist() for i in range(K)}
    out = analyze({"mode": "matrix", "matrix": matrix, "n_trials": 1, "periods_per_year": 252})
    hd = out["dsr"]["harden"]
    assert hd["hardened"] is False and hd["dsr_at_base"] >= 0.95   # 誠實基準即達標(病態)
    assert any("欄數" in w and ("檢定力" in w or "前向" in w) for w in out["warnings"]), \
        f"高欄數×短樣本未加檢定力警語:{out['warnings']}"


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
# 5.5 結構化 reason codes(reasons_coded / red_flags_coded / warnings_coded)
#     ——加欄向後相容:zh 字串不變,coded 與 zh 逐位 1:1,對應同一輸入確定性。
# ===========================================================================
def _assert_coded_shape(coded):
    for item in coded:
        assert isinstance(item, dict) and set(item) == {"code", "params"}
        assert isinstance(item["code"], str) and item["code"]
        assert isinstance(item["params"], dict)


def test_reasons_coded_alignment_and_shape():
    """coded 欄與 zh 字串欄長度 1:1;每項 {code, params};已知情境命中預期 code。"""
    rng = np.random.default_rng(7)
    n = 400
    out = analyze({"mode": "returns", "returns": (0.011 * rng.standard_normal(n)).tolist(),
                   "benchmark_returns": (0.011 * rng.standard_normal(n)).tolist(),
                   "n_trials": 200, "periods_per_year": 252})
    v = out["verdict"]
    assert len(v["reasons_coded"]) == len(v["reasons"])
    assert len(v["red_flags_coded"]) == len(v["red_flags"])
    assert len(out["warnings_coded"]) == len(out["warnings"])
    _assert_coded_shape(v["reasons_coded"] + v["red_flags_coded"] + out["warnings_coded"])
    codes = [c["code"] for c in v["reasons_coded"]]
    # 高 n_trials 雜訊:DSR 崩、n_trials 懲罰、收尾三態之一必在
    assert "many_trials_penalty" in codes
    assert codes[-1] in ("closing_likely_real", "closing_inconclusive", "closing_likely_overfit")
    # params 帶原始數值(前端模板格式化用)
    dsr_items = [c for c in v["reasons_coded"] if c["code"].startswith("dsr_")]
    assert dsr_items and all(("dsr" in c["params"] or c["code"] == "dsr_not_computable")
                             for c in dsr_items)


def test_reasons_coded_is_additive_not_mutating():
    """加 coded 欄不得改變既有值:同輸入,舊契約欄位(含 zh reasons 全文)完全不變、
    同輸入同輸出(確定性)。"""
    import json as _json
    rng = np.random.default_rng(2)
    n = 300
    payload = {"mode": "returns",
               "returns": (0.0008 + 0.012 * rng.standard_normal(n)).tolist(),
               "benchmark_returns": (0.0003 + 0.012 * rng.standard_normal(n)).tolist(),
               "cost_bps_per_turnover": 10.0,
               "turnover": np.abs(0.2 * rng.standard_normal(n)).tolist(),
               "periods_per_year": 252}
    o1 = analyze(_json.loads(_json.dumps(payload)))
    o2 = analyze(_json.loads(_json.dumps(payload)))

    def strip(o):
        if isinstance(o, dict):
            return {k: strip(x) for k, x in o.items()
                    if k not in ("reasons_coded", "red_flags_coded", "warnings_coded")}
        if isinstance(o, list):
            return [strip(x) for x in o]
        return o
    assert _json.dumps(strip(o1), sort_keys=True, ensure_ascii=False, allow_nan=True) \
        == _json.dumps(strip(o2), sort_keys=True, ensure_ascii=False, allow_nan=True)
    assert _json.dumps(o1["verdict"]["reasons_coded"], sort_keys=True) \
        == _json.dumps(o2["verdict"]["reasons_coded"], sort_keys=True)
    # zh reasons 是原本的人話字串(含全形標點),證明沒被 coded 化取代
    assert any("通縮夏普" in r for r in o1["verdict"]["reasons"])


def test_warnings_coded_paths():
    """warnings_coded 各路徑:短樣本 / 空輸入 / matrix 高欄數檢定力警語。"""
    # 短樣本
    out = analyze({"mode": "returns", "returns": [0.01, -0.01, 0.02, 0.0, 0.01]})
    assert any(c["code"] == "short_sample" for c in out["warnings_coded"])
    # 空輸入(錯誤路徑也帶 coded)
    bad = analyze({"mode": "returns", "returns": []})
    assert bad["ok"] is False
    assert [c["code"] for c in bad["warnings_coded"]] == ["returns_empty"]
    assert [c["code"] for c in bad["verdict"]["reasons_coded"]] == ["no_data"]
    # 高欄數 × 短樣本 → high_dim_low_power(沿用 R5 掃描 seed)
    K, N, s = 100, 60, 70
    rng = np.random.default_rng(s * 100003 + K * 7 + N)
    matrix = {f"c{i}": (0.012 * rng.standard_normal(N)).tolist() for i in range(K)}
    out2 = analyze({"mode": "matrix", "matrix": matrix, "n_trials": 1, "periods_per_year": 252})
    assert any(c["code"] == "high_dim_low_power" and c["params"]["n_cols"] == K
               for c in out2["warnings_coded"])


def test_capped_escape_reason_coded():
    """封頂逃逸情境:reason/red_flag 皆帶對應 code。"""
    K, N, s = 50, 120, 30
    rng = np.random.default_rng(s * 100003 + K * 7 + N)
    matrix = {f"c{i}": (0.012 * rng.standard_normal(N)).tolist() for i in range(K)}
    out = analyze({"mode": "matrix", "matrix": matrix, "n_trials": 1, "periods_per_year": 252})
    v = out["verdict"]
    assert any(c["code"] == "capped_escape_fail_closed" for c in v["reasons_coded"])
    assert any(c["code"] == "capped_escape" for c in v["red_flags_coded"])


# ===========================================================================
# 邊界:空輸入 / 極短序列不崩
# ===========================================================================
def test_empty_and_short_inputs():
    assert analyze({"mode": "returns", "returns": []})["ok"] is False
    assert analyze({"mode": "matrix", "matrix": {}})["ok"] is False
    out = analyze({"mode": "returns", "returns": [0.01, -0.01, 0.02, 0.0, 0.01]})
    assert out["ok"] is True  # 短序列給出結果 + warning
    assert any("樣本" in w for w in out["warnings"])


# ===========================================================================
# 日內頻率:_infer_ppy 日內化 + 長序列效能守衛
# ===========================================================================
def test_infer_ppy_intraday_tw_1m():
    """台股 1 分 K(20 交易日 × 266 bar,跳過週末)→ ppy ≈ 266 × ~280 ≈ 7 萬級,非 252/35040。"""
    dates = []
    d = pd.Timestamp("2026-06-01")  # 週一
    n_days = 0
    while n_days < 20:
        if d.dayofweek < 5:
            base = d + pd.Timedelta(hours=9)
            dates.extend((base + pd.Timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M")
                         for m in range(266))
            n_days += 1
        d += pd.Timedelta(days=1)
    ppy = jw._infer_ppy(dates, None)
    assert 55000 <= ppy <= 78000, ppy
    # 顯式 periods_per_year 覆寫永遠優先
    assert jw._infer_ppy(dates, 65000) == 65000.0
    # 日頻不受影響(回歸)
    daily = pd.date_range("2024-01-01", periods=300, freq="B")
    assert 240 <= jw._infer_ppy([t.strftime("%Y-%m-%d") for t in daily], None) <= 264


def test_infer_ppy_intraday_crypto_1m():
    """加密 1m 連續 3 天(24/7)→ 1440 bar/日 × 365 = 525,600(舊 clip 35040 已拆)。"""
    idx = pd.date_range("2026-01-01", periods=3 * 1440, freq="min")
    ppy = jw._infer_ppy([t.strftime("%Y-%m-%d %H:%M") for t in idx], None)
    assert 500000 <= ppy <= 545000, ppy


def test_long_series_guard_reduces_resamples():
    """>50k 期:permutation 降 500、帶 long_series_guard warning;≤50k 不動(n_perm=2000)。"""
    rng = np.random.default_rng(7)
    r = (0.0004 * rng.standard_normal(60000)).tolist()
    out = analyze({"mode": "returns", "returns": r, "periods_per_year": 65000})
    assert out["ok"] is True
    assert out["permutation_null"]["n_perm"] == 500
    assert any(c["code"] == "long_series_guard" for c in out["warnings_coded"])
    out2 = analyze({"mode": "returns", "returns": r[:1000], "periods_per_year": 65000})
    assert out2["permutation_null"]["n_perm"] == 2000
    assert not any(c["code"] == "long_series_guard" for c in out2["warnings_coded"])


# ===========================================================================
# 6. R12 必修回歸(E1):returns 模式 DSR 真通縮(SE proxy)/ 日曆跨度警語 /
#    ppy_fallback / score_breakdown
# ===========================================================================
def test_returns_dsr_strictly_decreasing_in_n_trials():
    """R12 HIGH 核心釘死:單序列 returns 模式,n_trials=1/150/10000 的 DSR 必須嚴格遞減。
    舊 bug:單序列 trial pool=[自己] → variance=0 → sr0≡0 → 三者 DSR 完全相同(通縮空轉),
    UI 卻宣稱「扣掉試 N 種參數的運氣」。"""
    rng = np.random.default_rng(5)
    r = (0.0006 + 0.01 * rng.standard_normal(400)).tolist()
    outs = {nt: analyze({"mode": "returns", "returns": r, "n_trials": nt,
                         "periods_per_year": 252}) for nt in (1, 150, 10000)}
    d1, d150, d10000 = (outs[nt]["dsr"]["dsr_prob"] for nt in (1, 150, 10000))
    assert d1 > d150 > d10000, (d1, d150, d10000)
    # sr0(通縮門檻)隨 n_trials 嚴格上升
    s1, s150, s10000 = (outs[nt]["dsr"]["sr0"] for nt in (1, 150, 10000))
    assert s1 == 0.0 and 0.0 < s150 < s10000, (s1, s150, s10000)
    # proxy 揭露:n_trials=1 不用 proxy(行為不變);>1 用 SE proxy 並照實標記
    assert outs[1]["dsr"]["sr_var_proxy"] is False
    assert outs[150]["dsr"]["sr_var_proxy"] is True
    assert outs[10000]["dsr"]["sr_var_proxy"] is True


def test_returns_dsr_proxy_matches_scipy_reference():
    """proxy 路徑的 DSR 對照 scipy 參考實作(同公式、scipy 統計量)誤差 < 1e-6。"""
    from scipy import stats
    EULER = 0.5772156649015329
    rng = np.random.default_rng(123)
    max_err = 0.0
    for n_trials in (2, 5, 150, 10000):
        r = 0.0006 + 0.01 * rng.standard_normal(400)
        mine = jw.deflated_sharpe(r, n_trials, np.array([jw._sharpe_periodic(r)]))
        assert mine["sr_variance_proxy"] is True
        rr = r[np.isfinite(r)]
        std = rr.std(ddof=1)
        sr = float(rr.mean() / std)
        se2 = (1.0 + 0.5 * sr * sr) / rr.size          # SE(SR)² proxy
        z1 = stats.norm.ppf(1 - 1.0 / n_trials)
        z2 = stats.norm.ppf(1 - 1.0 / (n_trials * np.e))
        sr0 = np.sqrt(se2) * ((1 - EULER) * z1 + EULER * z2)
        denom = np.sqrt(1 - stats.skew(rr) * sr
                        + (stats.kurtosis(rr, fisher=False) - 1) / 4.0 * sr ** 2)
        ref = float(stats.norm.cdf((sr - sr0) * np.sqrt(rr.size - 1) / denom))
        assert abs(float(mine["sr0_daily"]) - float(sr0)) < 1e-9
        max_err = max(max_err, abs(float(mine["dsr"]) - ref))
    assert max_err < 1e-6, f"proxy DSR 對照最大誤差 {max_err:.2e}"


def test_returns_n_trials_1_behavior_unchanged():
    """舊行為回歸:n_trials=1 不通縮(sr0=0)、無 proxy 標記、DSR reason 沿用舊文案
    (params 不含 var_proxy)。"""
    rng = np.random.default_rng(42)
    n = 500
    r = 0.0012 + 0.008 * rng.standard_normal(n)
    out = analyze({"mode": "returns", "returns": r.tolist(), "n_trials": 1,
                   "periods_per_year": 252})
    assert out["dsr"]["sr0"] == 0.0
    assert out["dsr"]["sr_var_proxy"] is False
    for c in out["verdict"]["reasons_coded"]:
        if c["code"].startswith("dsr_"):
            assert "var_proxy" not in c["params"], c
    # DSR 數值 = 未通縮 PSR(sr0=0),與舊版逐位相同的公式路徑
    assert np.isfinite(out["dsr"]["dsr_prob"])


def test_matrix_mode_dsr_uses_true_pool_not_proxy():
    """matrix 模式本來就有真 trial pool(各欄 Sharpe)→ 不得走 proxy(回歸:matrix 不受影響)。"""
    rng = np.random.default_rng(4)
    n = 250
    matrix = {f"p{i}": (0.0003 + 0.011 * rng.standard_normal(n)).tolist() for i in range(8)}
    out = analyze({"mode": "matrix", "matrix": matrix, "n_trials": 8, "periods_per_year": 252})
    assert out["dsr"]["sr_var_proxy"] is False
    for c in out["verdict"]["reasons_coded"]:
        if c["code"].startswith("dsr_"):
            assert "var_proxy" not in c["params"], c


def test_short_calendar_span_warning_intraday():
    """R12 MED:2650 根 1 分 K(10 個交易日 ≈ 0.04 年)期數夠多但日曆時間極短 →
    必須發 short_calendar_span 警語 + verdict reasons 提及;長跨度日頻資料不誤發。"""
    dates = []
    d = pd.Timestamp("2026-06-01")  # 週一
    n_days = 0
    while n_days < 10:
        if d.dayofweek < 5:
            base = d + pd.Timedelta(hours=9)
            dates.extend((base + pd.Timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M")
                         for m in range(265))
            n_days += 1
        d += pd.Timedelta(days=1)
    assert len(dates) == 2650
    rng = np.random.default_rng(9)
    r = (0.0001 * rng.standard_normal(2650)).tolist()
    out = analyze({"mode": "returns", "returns": r, "dates": dates})
    hits = [c for c in out["warnings_coded"] if c["code"] == "short_calendar_span"]
    assert hits, out["warnings"]
    assert 0.0 <= hits[0]["params"]["span_years"] < 0.5
    assert any(c["code"] == "short_calendar_span" for c in out["verdict"]["reasons_coded"]), \
        out["verdict"]["reasons"]
    assert any("年化" in rr for rr in out["verdict"]["reasons"])
    # 反向:~1.15 年日頻資料不得誤發
    daily = pd.date_range("2024-01-01", periods=300, freq="B").astype(str).tolist()
    r2 = (0.0005 + 0.01 * rng.standard_normal(300)).tolist()
    out2 = analyze({"mode": "returns", "returns": r2, "dates": daily})
    assert not any(c["code"] == "short_calendar_span" for c in out2["warnings_coded"])
    assert not any(c["code"] == "short_calendar_span"
                   for c in out2["verdict"]["reasons_coded"])


def test_ppy_fallback_warning_on_bad_dates():
    """R12 LOW:壞日期不再被裸 except 靜默吞掉——回退 252 必須帶 ppy_fallback 警語。"""
    rng = np.random.default_rng(3)
    r = (0.0005 + 0.01 * rng.standard_normal(50)).tolist()
    out = analyze({"mode": "returns", "returns": r, "dates": ["not-a-date-xx"] * 50})
    assert out["ok"] is True
    hits = [c for c in out["warnings_coded"] if c["code"] == "ppy_fallback"]
    assert hits, out["warnings"]
    assert "error" in hits[0]["params"]
    # 好日期不誤發
    good = pd.date_range("2024-01-01", periods=50, freq="B").astype(str).tolist()
    out2 = analyze({"mode": "returns", "returns": r, "dates": good})
    assert not any(c["code"] == "ppy_fallback" for c in out2["warnings_coded"])


def test_score_breakdown_sums_to_score():
    """R12 LOW:verdict.score_breakdown 各閘加減分逐項揭露,clip(Σdelta,0,100)=score,
    且每個非 base 項的 code 都能在 reasons_coded ∪ red_flags_coded 找到出處。"""
    rng = np.random.default_rng(42)
    n = 500
    edge = 0.0012 + 0.008 * rng.standard_normal(n)
    bench = 0.0001 + 0.008 * rng.standard_normal(n)
    noise = 0.011 * np.random.default_rng(7).standard_normal(400)
    mrng = np.random.default_rng(11)
    noisy_matrix = {f"p{i}": (0.012 * mrng.standard_normal(260)).tolist() for i in range(20)}
    payloads = [
        {"mode": "returns", "returns": edge.tolist(), "benchmark_returns": bench.tolist(),
         "n_trials": 1, "periods_per_year": 252},
        {"mode": "returns", "returns": noise.tolist(), "n_trials": 200,
         "periods_per_year": 252},
        {"mode": "matrix", "matrix": noisy_matrix, "n_trials": 20, "periods_per_year": 252},
        {"mode": "returns", "returns": noise[:40].tolist(), "n_trials": 5},  # 短樣本路徑
    ]
    for p in payloads:
        out = analyze(p)
        bd = out["verdict"]["score_breakdown"]
        assert bd[0] == {"code": "base", "delta": 50.0}
        total = sum(item["delta"] for item in bd)
        assert round(float(np.clip(total, 0.0, 100.0)), 1) == out["verdict"]["score_0to100"], \
            (bd, out["verdict"]["score_0to100"])
        codes = ({c["code"] for c in out["verdict"]["reasons_coded"]}
                 | {c["code"] for c in out["verdict"]["red_flags_coded"]})
        for item in bd[1:]:
            assert item["code"] in codes, (item, codes)
    # 錯誤路徑也帶(空 list,契約一致)
    bad = analyze({"mode": "returns", "returns": []})
    assert bad["verdict"]["score_breakdown"] == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
