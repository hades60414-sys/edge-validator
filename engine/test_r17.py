# -*- coding: utf-8 -*-
"""R17 手術回歸測試(對應 R16 面試官 panel 必修清單)。

必修1(HIGH):缺值/非數字格靜默填 0 → fail-closed。
  騙局復刻:真實年虧 ~28% 的策略把最差 100 天留空 → 舊版 nan_to_num(nan=0)
  把虧損抹掉、波動壓低 → 可判高分;修後【拒審】,絕不 likely-real。
必修2(MED):engine reasons 不得用硬化後 n_trials 冒充使用者申報值。
必修4(LOW):基準比較必須在共同日【配對子集】上算(主判決仍用全序列)。
"""
import numpy as np
import pytest

from engine import analyze


def _sharpe_annual(r, ppy=252.0):
    """獨立手算年化夏普(與 compute_metrics 同式:mean/std(ddof=0)×√ppy)。"""
    r = np.asarray(r, dtype=float)
    sd = r.std(ddof=0)
    assert sd > 0
    return float((r.mean() * ppy) / (sd * np.sqrt(ppy)))


def _codes(coded):
    return [c["code"] for c in coded]


# ===========================================================================
# 必修1:缺值 fail-closed(returns 模式)
# ===========================================================================
def test_missing_scam_rejected_returns_mode():
    """騙局復刻:250 期含日期、最差 100 天留空 → 必須結構化拒審,絕不 likely-real。"""
    rng = np.random.default_rng(20260710)
    r = -0.0013 + 0.015 * rng.standard_normal(250)   # 年化約 -28%,真實在虧
    worst = np.argsort(r)[:100]
    vals = r.tolist()
    for i in worst:
        vals[int(i)] = None                            # JSON null(留空格)
    dates = [str(d.date()) for d in
             __import__("pandas").date_range("2024-01-01", periods=250, freq="B")]
    out = analyze({"mode": "returns", "returns": vals, "dates": dates, "n_trials": 1})
    assert out["ok"] is False
    assert "missing_values_reject" in _codes(out["warnings_coded"])
    assert out["verdict"]["overall"] != "likely-real"
    assert out["verdict"]["score_0to100"] == 0
    # 佐證騙局真的存在:同一資料若照舊版填 0,會拿到像樣的分數(這正是要堵的洞)
    zero_filled = [0.0 if v is None else v for v in vals]
    cheated = analyze({"mode": "returns", "returns": zero_filled,
                       "dates": dates, "n_trials": 1})
    assert cheated["ok"] and cheated["verdict"]["score_0to100"] > 50


def test_missing_small_dropped_matches_hand_cleaned():
    """<5% 缺值:整期剔除+告警,統計量與「手動剔除後」逐位一致(絕非填 0)。"""
    rng = np.random.default_rng(7)
    r = (0.0007 + 0.01 * rng.standard_normal(250)).tolist()
    bad = [3, 100, 200]
    vals = list(r)
    vals[3] = None
    vals[100] = float("nan")
    vals[200] = None
    out = analyze({"mode": "returns", "returns": vals, "periods_per_year": 252})
    assert out["ok"] is True
    assert "missing_values_dropped" in _codes(out["warnings_coded"])
    w = next(c for c in out["warnings_coded"] if c["code"] == "missing_values_dropped")
    assert w["params"]["n_missing"] == 3 and w["params"]["n_kept"] == 247
    hand = [v for i, v in enumerate(r) if i not in bad]
    ref = analyze({"mode": "returns", "returns": hand, "periods_per_year": 252})
    assert out["metrics"]["n_periods"] == 247
    for k in ("sharpe", "cagr", "max_drawdown", "ann_vol", "final_equity"):
        assert out["metrics"][k] == pytest.approx(ref["metrics"][k], rel=1e-12), k


def test_missing_dates_filtered_in_sync():
    """剔除缺值期時 dates 同步剔除(日曆跨度/年化推算不會錯位)。"""
    import pandas as pd
    rng = np.random.default_rng(11)
    vals = (0.0005 + 0.01 * rng.standard_normal(120)).tolist()
    vals[5] = None
    dates = [str(d.date()) for d in pd.date_range("2024-01-01", periods=120, freq="B")]
    out = analyze({"mode": "returns", "returns": vals, "dates": dates})
    assert out["ok"] and out["metrics"]["n_periods"] == 119


# ===========================================================================
# 必修1:缺值 fail-closed(matrix 模式)
# ===========================================================================
def _noise_matrix(rng, T, names):
    return {nm: (0.0002 + 0.01 * rng.standard_normal(T)).tolist() for nm in names}


def test_missing_matrix_column_reject_names_column():
    rng = np.random.default_rng(3)
    mat = _noise_matrix(rng, 200, ["A", "B", "C"])
    for i in range(0, 200, 10):        # B 欄 10% 缺
        mat["B"][i] = None
    out = analyze({"mode": "matrix", "matrix": mat, "n_trials": 3})
    assert out["ok"] is False
    w = out["warnings_coded"][0]
    assert w["code"] == "missing_values_reject" and w["params"]["col"] == "B"


def test_missing_matrix_rows_dropped_keeps_alignment():
    rng = np.random.default_rng(4)
    mat = _noise_matrix(rng, 200, ["A", "B", "C"])
    keep_ref = {nm: list(vals) for nm, vals in mat.items()}
    mat["B"][10] = None
    mat["B"][60] = None
    mat["C"][130] = None               # 3 列含缺格(1.5%)
    out = analyze({"mode": "matrix", "matrix": mat, "n_trials": 3,
                   "periods_per_year": 252})
    assert out["ok"] is True
    w = next(c for c in out["warnings_coded"] if c["code"] == "missing_rows_dropped")
    assert w["params"]["n_rows_dropped"] == 3 and w["params"]["n_kept"] == 197
    assert out["metrics"]["n_periods"] == 197
    # 對齊真值:整列剔除 = 各欄都拿掉同三列 → 與手動剔除後重跑逐位一致
    kept_idx = [i for i in range(200) if i not in (10, 60, 130)]
    hand = {nm: [keep_ref[nm][i] for i in kept_idx] for nm in keep_ref}
    ref = analyze({"mode": "matrix", "matrix": hand, "n_trials": 3,
                   "periods_per_year": 252})
    assert out["metrics"]["sharpe"] == pytest.approx(ref["metrics"]["sharpe"], rel=1e-12)


def test_missing_matrix_union_rate_rejects():
    rng = np.random.default_rng(5)
    mat = _noise_matrix(rng, 200, ["A", "B", "C"])
    for base, nm in ((1, "A"), (5, "B"), (9, "C")):   # 各欄 4 缺(2%),互不重疊 → 12 列 6%
        for k in range(4):
            mat[nm][base + 30 * k] = None
    out = analyze({"mode": "matrix", "matrix": mat, "n_trials": 3})
    assert out["ok"] is False
    assert out["warnings_coded"][0]["code"] == "missing_rows_reject"


# ===========================================================================
# 必修2:申報 vs 硬化後 n_trials 的誠實措辭
# ===========================================================================
def test_trials_wording_discloses_declared_when_uplifted():
    """matrix 噪聲 25 欄、申報 5:有效 n_trials 被欄數地板/硬化上調 → 措辭必含「申報」,
    coded params 帶 declared,絕不再說『你試了 <上調後數字> 種參數』。"""
    rng = np.random.default_rng(42)
    mat = _noise_matrix(rng, 150, [f"s{i}" for i in range(25)])
    out = analyze({"mode": "matrix", "matrix": mat, "n_trials": 5,
                   "periods_per_year": 252})
    assert out["ok"]
    eff = out["dsr"]["n_trials"]
    assert eff > 5  # 欄數地板(25)或雜訊硬化必然上調
    reasons = out["verdict"]["reasons"]
    trial_reasons = [r for r in reasons if ("試驗" in r or "試了" in r) and "封頂" not in r]
    assert any("申報 5 種" in r for r in trial_reasons), trial_reasons
    assert any(f"通縮以 {eff} 種試驗計" in r for r in trial_reasons), trial_reasons
    # 不得出現「你試了 <上調後> 種參數」的冒充句式
    assert not any(f"你試了 {eff} 種參數" in r for r in reasons)
    coded = out["verdict"]["reasons_coded"]
    tc = next(c for c in coded if c["code"] in ("many_trials_penalty", "trials_corrected"))
    assert tc["params"]["declared"] == 5 and tc["params"]["n_trials"] == eff


def test_trials_wording_natural_when_declared_equals_effective():
    """returns 模式申報 30(=採用)→ 維持自然措辭,不出現「申報」。"""
    rng = np.random.default_rng(6)
    r = (0.0008 + 0.01 * rng.standard_normal(300)).tolist()
    out = analyze({"mode": "returns", "returns": r, "n_trials": 30,
                   "periods_per_year": 252})
    assert out["ok"]
    reasons = out["verdict"]["reasons"]
    assert any("你試了 30 種參數" in r for r in reasons)
    assert not any("申報" in r for r in reasons)
    tc = next(c for c in out["verdict"]["reasons_coded"]
              if c["code"] == "many_trials_penalty")
    assert "declared" not in tc["params"]


# ===========================================================================
# 必修4:基準比較的共同日配對(含手算真值)
# ===========================================================================
def test_bench_cmp_paired_subset_hand_truth():
    """策略的大賺日全落在基準缺席日:全序列比會誤判 beats=True(舊行為),
    配對子集比則誠實 beats=False。全部數字對手算。"""
    returns = [0.001, -0.002, 0.0015, 0.001, 0.05,
               -0.001, 0.002, -0.0005, 0.06, 0.001]
    idx = [0, 1, 2, 3, 5, 6, 7, 9]              # 基準缺席第 4、8 天(策略的兩根暴衝)
    bench = [0.002, -0.001, 0.0025, -0.002, 0.0005, 0.003, -0.001, 0.002]
    sr_full = _sharpe_annual(returns)
    sr_pair = _sharpe_annual([returns[i] for i in idx])
    sr_bench = _sharpe_annual(bench)
    assert sr_full > sr_bench > sr_pair          # 手算前提:未配對日正是偏置來源
    out = analyze({"mode": "returns", "returns": returns, "periods_per_year": 252,
                   "benchmark_returns": bench, "benchmark_idx": idx})
    bc = out["benchmark_compare"]
    assert bc is not None and bc["paired"] is True and bc["n_paired"] == 8
    assert bc["strat_sharpe_paired"] == pytest.approx(sr_pair, rel=1e-12)
    assert bc["bench_sharpe"] == pytest.approx(sr_bench, rel=1e-12)
    assert bc["strategy_beats"] is False          # 修後:配對子集上誠實判輸
    # 主判決不受影響:全序列指標維持全長
    assert out["metrics"]["n_periods"] == 10
    assert out["metrics"]["sharpe"] == pytest.approx(sr_full, rel=1e-12)


def test_bench_pair_idx_invalid_falls_back_with_warning():
    rng = np.random.default_rng(8)
    r = (0.0006 + 0.01 * rng.standard_normal(120)).tolist()
    bench = (0.0003 + 0.01 * rng.standard_normal(120)).tolist()
    out = analyze({"mode": "returns", "returns": r, "periods_per_year": 252,
                   "benchmark_returns": bench,
                   "benchmark_idx": [0, 5]})       # 長度與 bench 不符 → 無效
    assert out["ok"]
    assert "bench_pair_idx_invalid" in _codes(out["warnings_coded"])
    bc = out["benchmark_compare"]
    assert bc is not None and bc["paired"] is False and bc["n_paired"] == 120


def test_bench_pair_remaps_after_missing_drop():
    """引擎端剔除缺值期後,配對索引同步剔除並重映射(不錯位)。"""
    rng = np.random.default_rng(9)
    r = (0.0006 + 0.01 * rng.standard_normal(100)).tolist()
    bench = (0.0003 + 0.01 * rng.standard_normal(100)).tolist()
    vals = list(r)
    vals[10] = None
    vals[50] = None                                # 2% 缺 → 剔除
    out = analyze({"mode": "returns", "returns": vals, "periods_per_year": 252,
                   "benchmark_returns": bench,
                   "benchmark_idx": list(range(100))})
    assert out["ok"]
    bc = out["benchmark_compare"]
    assert bc["paired"] is True and bc["n_paired"] == 98
    kept = [v for i, v in enumerate(r) if i not in (10, 50)]
    assert bc["strat_sharpe_paired"] == pytest.approx(_sharpe_annual(kept), rel=1e-12)
    kept_bench = [v for i, v in enumerate(bench) if i not in (10, 50)]
    assert bc["bench_sharpe"] == pytest.approx(_sharpe_annual(kept_bench), rel=1e-12)


# ===========================================================================
# R17 收尾(驗收官刀):引擎剔列後,FWER 基準/cost turnover/基準曲線同步重配對
# ===========================================================================
def test_fwer_bench_cost_curve_remap_after_row_drop():
    """引擎剔除缺值列後,FWER 的基準、cost_stress 的 turnover、基準曲線都必須以
    同一 keep_mask 同步剔除——與「手動剔除後重跑」逐位一致,aligned/1.0 揭露才為真。
    (修前:b_arr[:T] 位置截斷逐期錯位、卻仍宣稱 aligned/coverage=1.0=假揭露。)"""
    rng = np.random.default_rng(17)
    mat = _noise_matrix(rng, 200, ["A", "B", "C"])
    ref = {nm: list(vals) for nm, vals in mat.items()}
    bench = (0.0002 + 0.01 * rng.standard_normal(200)).tolist()
    tn = np.abs(rng.standard_normal(200)).tolist()
    mat["A"][20] = None
    mat["C"][150] = None                            # 2 列缺(1%)→ 整列剔除
    out = analyze({"mode": "matrix", "matrix": mat, "n_trials": 3,
                   "periods_per_year": 252, "benchmark_returns": bench,
                   "turnover": tn, "cost_bps_per_turnover": 20})
    assert out["ok"] and out["metrics"]["n_periods"] == 198
    kept = [i for i in range(200) if i not in (20, 150)]
    hand = {nm: [ref[nm][i] for i in kept] for nm in ref}
    exp = analyze({"mode": "matrix", "matrix": hand, "n_trials": 3,
                   "periods_per_year": 252,
                   "benchmark_returns": [bench[i] for i in kept],
                   "turnover": [tn[i] for i in kept],
                   "cost_bps_per_turnover": 20})
    # FWER:基準同步剔除 → aligned 揭露為真、與手動剔除版逐位一致(bootstrap 固定 seed)
    assert out["fwer"]["benchmark_kind"] == "aligned"
    assert out["fwer"]["bench_coverage"] == 1.0
    assert out["fwer"]["n_rejected"] == exp["fwer"]["n_rejected"]
    assert out["fwer"]["spa"]["p_value"] == pytest.approx(
        exp["fwer"]["spa"]["p_value"], abs=0.0)
    # cost_stress:turnover 同步剔除
    for k in ("x1_sharpe", "x3_sharpe", "x6_sharpe"):
        assert out["cost_stress"][k] == pytest.approx(exp["cost_stress"][k], rel=1e-12)
    # 基準曲線:同步剔除後與主曲線逐期對齊
    assert out["benchmark_curve"] == pytest.approx(exp["benchmark_curve"], rel=1e-12)


def test_fwer_bench_unpairable_after_row_drop_degrades_honestly():
    """剔列後基準長度對不上原始列數(無法同步重配對)→ 不得謊稱 aligned,
    必須誠實降級 zero_fallback 並發 fwer_bench_fallback_zero 警語。"""
    rng = np.random.default_rng(18)
    mat = _noise_matrix(rng, 200, ["A", "B", "C"])
    mat["B"][7] = None                              # 1 列缺 → 剔除,T 200→199
    bench = (0.0002 + 0.01 * rng.standard_normal(199)).tolist()  # ≥T_mat 但 ≠ 原始 200
    out = analyze({"mode": "matrix", "matrix": mat, "n_trials": 3,
                   "periods_per_year": 252, "benchmark_returns": bench})
    assert out["ok"]
    assert out["fwer"]["benchmark_kind"] == "zero_fallback"
    assert out["fwer"]["bench_coverage"] == 0.0
    w = next(c for c in out["warnings_coded"] if c["code"] == "fwer_bench_fallback_zero")
    assert w["params"]["coverage"] == 0.0
