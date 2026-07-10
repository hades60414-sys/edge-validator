# -*- coding: utf-8 -*-
"""R19 手術回歸測試(資料完整性整面掃 + R18 panel 必修清單)。

必修1(HIGH):日期重複/亂序零檢查 → 重複 ≥5% 拒審、<5% 保留首見剔除、亂序穩定排序。
  騙局復刻:年化夏普 -0.72 的真虧策略,最好 60 天整列複製 4 份按日期排序 → 舊版判
  86 分 likely-real 零警語;修後【拒審】。
必修2(MED):bench_cmp 無 idx 退路的位置截斷必須吃 keep_mask(修錯位配對)。
必修3(MED):基準/turnover 的 nan_to_num(→0) fail-open 收口(絕不填 0)。
必修4:輸入通道×異常型態整面掃的新防禦格(型別/n_trials/ppy/cost_bps/極端值/長度)。
必修5b:nav prev==0 → NaN 傳播(不再憑空捏造持平日)。
必修5c:n_trials=1 的 DSR 判決句不再講「扣掉多重檢定」。
政策全表:docs/INTEGRITY_MATRIX.md(通道×異常→政策→測試名)。
"""
import numpy as np
import pandas as pd
import pytest

from engine import analyze
from engine.judge_web import cost_stress, detect_and_convert, nav_to_returns


def _sharpe_annual(r, ppy=252.0):
    r = np.asarray(r, dtype=float)
    sd = r.std(ddof=0)
    assert sd > 0
    return float((r.mean() * ppy) / (sd * np.sqrt(ppy)))


def _codes(coded):
    return [c["code"] for c in coded]


def _biz_dates(n, start="2023-01-02"):
    return [str(d.date()) for d in pd.date_range(start, periods=n, freq="B")]


def _assert_same_judgment(a, b):
    """兩份 analyze 輸出的判決層逐位一致(warnings 允許不同——揭露本來就該不同)。"""
    for k in ("sharpe", "cagr", "max_drawdown", "ann_vol", "final_equity", "n_periods"):
        assert a["metrics"][k] == pytest.approx(b["metrics"][k], rel=1e-12, abs=1e-15), k
    assert a["dsr"]["dsr_prob"] == pytest.approx(b["dsr"]["dsr_prob"], rel=1e-12)
    assert a["permutation_null"]["p_value"] == b["permutation_null"]["p_value"]
    assert a["verdict"]["overall"] == b["verdict"]["overall"]
    assert a["verdict"]["score_0to100"] == b["verdict"]["score_0to100"]
    assert a["equity_curve"] == pytest.approx(b["equity_curve"], rel=1e-12)


# ===========================================================================
# 必修1:日期重複 —— 騙局復刻與門檻
# ===========================================================================
def _losing_series(seed=20260710, n=250):
    rng = np.random.default_rng(seed)
    r = -0.0013 + 0.015 * rng.standard_normal(n)   # 年化夏普 ~-0.72,真實在虧
    return r, _biz_dates(n)


def test_duplicate_dates_scam_rejected():
    """騙局復刻:最好 60 天整列複製 4 份、按日期排序上傳(490 列,240 列重複日期)
    → 必須結構化拒審。同一批重複列若配上假造的唯一日期,舊路徑會判 86 分
    likely-real——這正是本閘存在的理由(佐證斷言在下一支測試)。"""
    r, dates = _losing_series()
    best = np.argsort(r)[-60:]
    rows = list(zip(dates, r.tolist()))
    for _ in range(4):
        rows += [(dates[int(i)], float(r[int(i)])) for i in best]
    rows.sort(key=lambda x: x[0])
    out = analyze({"mode": "returns", "returns": [x[1] for x in rows],
                   "dates": [x[0] for x in rows], "n_trials": 1})
    assert out["ok"] is False
    w = next(c for c in out["warnings_coded"] if c["code"] == "duplicate_dates_reject")
    assert w["params"]["n_dup"] == 240 and w["params"]["n_periods"] == 490
    assert out["verdict"]["overall"] != "likely-real"
    assert out["verdict"]["score_0to100"] == 0
    # 拒審訊息要向日內使用者講清楚出路(date-only 日內資料會撞同一天)
    assert "時間戳" in out["warnings"][0]


def test_duplicate_scam_magnitude_evidence():
    """佐證騙局規模:同一批 490 列(240 列為複製的好日子)配上唯一日期
    → 引擎無從識破,判 likely-real 高分。日期重複檢查就是攔這個的。"""
    r, dates = _losing_series()
    best = np.argsort(r)[-60:]
    vals = r.tolist() + [float(r[int(i)]) for i in best] * 4
    fake_dates = _biz_dates(len(vals))
    out = analyze({"mode": "returns", "returns": vals, "dates": fake_dates, "n_trials": 1})
    assert out["ok"] and out["verdict"]["overall"] == "likely-real"
    assert out["verdict"]["score_0to100"] >= 80


def test_duplicate_dates_mild_still_rejected():
    """溫和版:只複製最好 50 天 1 份(300 列,50/300≈17% ≥5%)→ 一樣拒審。
    (修前這招能把 14 分洗到 56 分。)"""
    r, dates = _losing_series()
    best = np.argsort(r)[-50:]
    rows = list(zip(dates, r.tolist())) + [(dates[int(i)], float(r[int(i)])) for i in best]
    rows.sort(key=lambda x: x[0])
    out = analyze({"mode": "returns", "returns": [x[1] for x in rows],
                   "dates": [x[0] for x in rows], "n_trials": 1})
    assert out["ok"] is False
    assert "duplicate_dates_reject" in _codes(out["warnings_coded"])


def test_duplicate_dates_small_dropped_keeps_first_seen():
    """<5% 重複(2/252):保留首見、剔除重複列+揭露;統計量與手動剔除版逐位一致。"""
    rng = np.random.default_rng(7)
    r = (0.0007 + 0.01 * rng.standard_normal(250)).tolist()
    dates = _biz_dates(250)
    # 在日期 40、120 的原始列【之後】插入「同日期、不同值」的重複列(首見值必須勝出)
    rows = list(zip(dates, r))
    rows.insert(41, (dates[40], 9.9))     # 原 40 列後
    rows.insert(122, (dates[120], -9.9))  # 原 120 列(現位於 121)後
    d2 = [x[0] for x in rows]
    vals = [x[1] for x in rows]
    out = analyze({"mode": "returns", "returns": vals, "dates": d2,
                   "periods_per_year": 252})
    assert out["ok"] is True
    w = next(c for c in out["warnings_coded"] if c["code"] == "duplicate_dates_dropped")
    assert w["params"]["n_dup"] == 2 and w["params"]["n_kept"] == 250
    ref = analyze({"mode": "returns", "returns": r, "dates": dates,
                   "periods_per_year": 252})
    _assert_same_judgment(out, ref)   # 首見保留 → 9.9/-9.9 junk 全不在


def test_duplicate_dates_mixed_format_same_instant_caught():
    """混格式閃避無效:'2023-01-03' 與 '2023/1/3' 是同一時刻 → 也算重複。"""
    rng = np.random.default_rng(8)
    vals = (0.0005 + 0.01 * rng.standard_normal(60)).tolist()
    dates = _biz_dates(60)
    vals.append(0.05)
    dates.append(dates[10].replace("-0", "/").replace("-", "/"))  # 同日、不同格式
    out = analyze({"mode": "returns", "returns": vals, "dates": dates})
    codes = _codes(out["warnings_coded"])
    assert "duplicate_dates_dropped" in codes
    assert out["metrics"]["n_periods"] == 60


def test_intraday_unique_timestamps_not_hurt():
    """含時間的日內資料(同日多 bar、時戳唯一)不誤傷:無重複警語/拒審。"""
    idx = pd.date_range("2024-03-04 09:01", periods=300, freq="min")
    rng = np.random.default_rng(9)
    vals = (0.00001 + 0.001 * rng.standard_normal(300)).tolist()
    out = analyze({"mode": "returns", "returns": vals,
                   "dates": [t.strftime("%Y-%m-%d %H:%M") for t in idx]})
    assert out["ok"] is True
    codes = _codes(out["warnings_coded"])
    assert "duplicate_dates_reject" not in codes
    assert "duplicate_dates_dropped" not in codes


def test_dateonly_intraday_rejected_with_timestamp_hint():
    """date-only 的日內資料(同日 10 根 bar 全撞同一天)→ 拒審,且訊息提示補時間戳。"""
    rng = np.random.default_rng(10)
    vals = (0.0001 + 0.002 * rng.standard_normal(100)).tolist()
    days = _biz_dates(10)
    dates = [days[i // 10] for i in range(100)]
    out = analyze({"mode": "returns", "returns": vals, "dates": dates})
    assert out["ok"] is False
    assert "duplicate_dates_reject" in _codes(out["warnings_coded"])
    assert "時間戳" in out["warnings"][0]


# ===========================================================================
# 必修1:日期亂序 —— 穩定排序恢復時序,判決與事先排好序的同資料逐位一致
# ===========================================================================
def test_missing_and_duplicate_compose_keep_mask_bench_synced():
    """缺值剔除 + 重複日期剔除【複合】時 keep_mask 正確合成:基準(原始座標、無 idx)
    同步剔除,與手動清理版逐位一致。"""
    rng = np.random.default_rng(34)
    r = 0.0006 + 0.01 * rng.standard_normal(200)
    dates = _biz_dates(200)
    rows = list(zip(dates, r.tolist()))
    rows.insert(101, (dates[100], 7.7))                 # 重複日期(junk,首見勝出)
    vals = [x[1] for x in rows]
    d_in = [x[0] for x in rows]
    vals[10] = None                                     # 缺值
    bench = 0.0003 + 0.01 * rng.standard_normal(201)    # 原始座標(含 dup 列)
    out = analyze({"mode": "returns", "returns": vals, "dates": d_in,
                   "periods_per_year": 252, "benchmark_returns": bench.tolist()})
    assert out["ok"] and out["metrics"]["n_periods"] == 199
    codes = _codes(out["warnings_coded"])
    assert "missing_values_dropped" in codes and "duplicate_dates_dropped" in codes
    kept = [i for i in range(201) if i not in (10, 101)]
    ref = analyze({"mode": "returns",
                   "returns": [vals[i] for i in kept],
                   "dates": [d_in[i] for i in kept], "periods_per_year": 252,
                   "benchmark_returns": [float(bench[i]) for i in kept]})
    _assert_same_judgment(out, ref)
    for k in ("bench_sharpe", "strat_sharpe_paired", "strategy_beats", "n_paired"):
        assert out["benchmark_compare"][k] == pytest.approx(
            ref["benchmark_compare"][k], rel=1e-12), k


def test_unsorted_dates_sorted_matches_presorted():
    rng = np.random.default_rng(11)
    r = 0.0006 + 0.01 * rng.standard_normal(250)
    dates = _biz_dates(250)
    perm = rng.permutation(250)
    out = analyze({"mode": "returns", "returns": [float(r[i]) for i in perm],
                   "dates": [dates[i] for i in perm], "n_trials": 1})
    ref = analyze({"mode": "returns", "returns": r.tolist(), "dates": dates,
                   "n_trials": 1})
    assert out["ok"]
    w = next(c for c in out["warnings_coded"] if c["code"] == "dates_sorted")
    assert w["params"]["n_moved"] > 0
    _assert_same_judgment(out, ref)
    assert "dates_sorted" not in _codes(ref["warnings_coded"])  # 已排序者不誤報


def test_unsorted_dates_benchmark_idx_remapped():
    """亂序資料 + benchmark_idx(指亂序座標):排序後配對索引同步重映射,
    bench_cmp 與「事先排好序 + 對應 idx」版逐位一致。"""
    rng = np.random.default_rng(12)
    r = 0.0006 + 0.01 * rng.standard_normal(120)
    dates = _biz_dates(120)
    bench_pos_sorted = list(range(0, 120, 2))          # 排序座標下的配對日
    bench = (0.0003 + 0.01 * rng.standard_normal(len(bench_pos_sorted))).tolist()
    perm = rng.permutation(120)
    inv = np.empty(120, dtype=int)
    inv[perm] = np.arange(120)
    # 亂序座標下的同一批配對日:排序座標 p 的列現在在位置 inv[p]
    idx_shuffled = [int(inv[p]) for p in bench_pos_sorted]
    out = analyze({"mode": "returns", "returns": [float(r[i]) for i in perm],
                   "dates": [dates[i] for i in perm], "periods_per_year": 252,
                   "benchmark_returns": bench, "benchmark_idx": idx_shuffled})
    ref = analyze({"mode": "returns", "returns": r.tolist(), "dates": dates,
                   "periods_per_year": 252,
                   "benchmark_returns": bench, "benchmark_idx": bench_pos_sorted})
    assert out["ok"] and out["benchmark_compare"]["paired"] is True
    for k in ("bench_sharpe", "strat_sharpe_paired", "excess_cagr", "n_paired",
              "strategy_beats"):
        assert out["benchmark_compare"][k] == pytest.approx(
            ref["benchmark_compare"][k], rel=1e-12), k


def test_unparseable_dates_skip_row_guard_keep_ppy_fallback():
    """垃圾日期(解析不了、全部相同)不觸發重複拒審——那是 ppy_fallback 的守備範圍。"""
    rng = np.random.default_rng(13)
    r = (0.0005 + 0.01 * rng.standard_normal(50)).tolist()
    out = analyze({"mode": "returns", "returns": r, "dates": ["not-a-date-xx"] * 50})
    assert out["ok"] is True
    codes = _codes(out["warnings_coded"])
    assert "ppy_fallback" in codes and "duplicate_dates_reject" not in codes


def test_matrix_duplicate_dates_reject_and_drop():
    """matrix 模式同判準:≥5% 重複拒審;<5% 整列剔除(各欄橫斷面同步)。"""
    rng = np.random.default_rng(14)
    T = 200
    mat = {nm: (0.0002 + 0.01 * rng.standard_normal(T)).tolist() for nm in "ABC"}
    dates = _biz_dates(T)
    bad_dates = [dates[i // 2] for i in range(T)]      # 全是成對重複 → 50%
    out = analyze({"mode": "matrix", "matrix": mat, "dates": bad_dates, "n_trials": 3})
    assert out["ok"] is False
    assert "duplicate_dates_reject" in _codes(out["warnings_coded"])
    # <5%:2 列重複 → 剔除、與手動剔除版夏普一致
    d2 = list(dates)
    d2[50] = d2[49]
    d2[150] = d2[149]
    out2 = analyze({"mode": "matrix", "matrix": mat, "dates": d2, "n_trials": 3,
                    "periods_per_year": 252})
    assert out2["ok"] and out2["metrics"]["n_periods"] == 198
    kept = [i for i in range(T) if i not in (50, 150)]
    hand = {nm: [mat[nm][i] for i in kept] for nm in mat}
    ref = analyze({"mode": "matrix", "matrix": hand, "n_trials": 3,
                   "periods_per_year": 252})
    assert out2["metrics"]["sharpe"] == pytest.approx(ref["metrics"]["sharpe"], rel=1e-12)


# ===========================================================================
# 必修2:bench_cmp 無 idx 退路剔列後必須吃 keep_mask(修錯位配對)
# ===========================================================================
def test_bench_fallback_no_idx_syncs_keep_mask():
    """缺值剔列後、無 benchmark_idx 的位置配對退路:基準必須同步剔列,
    與手動剔除版逐位一致(修前:原座標基準 × 剔列後策略 = 逐期錯位)。"""
    rng = np.random.default_rng(99)
    r = 0.0006 + 0.01 * rng.standard_normal(200)
    bench = 0.0003 + 0.01 * rng.standard_normal(200)
    vals = r.tolist()
    for i in (10, 50, 90, 130):
        vals[i] = None                                  # 2% 缺 → 剔除
    out = analyze({"mode": "returns", "returns": vals, "periods_per_year": 252,
                   "benchmark_returns": bench.tolist()})
    kept = [i for i in range(200) if i not in (10, 50, 90, 130)]
    ref = analyze({"mode": "returns", "returns": [float(r[i]) for i in kept],
                   "periods_per_year": 252,
                   "benchmark_returns": [float(bench[i]) for i in kept]})
    bc, rc = out["benchmark_compare"], ref["benchmark_compare"]
    assert bc["n_paired"] == 196
    for k in ("bench_sharpe", "strat_sharpe_paired", "excess_cagr", "strategy_beats"):
        assert bc[k] == pytest.approx(rc[k], rel=1e-12), k
    assert bc["bench_sharpe"] == pytest.approx(
        _sharpe_annual([bench[i] for i in kept]), rel=1e-12)


def test_bench_idx_duplicates_invalid():
    """benchmark_idx 含重複索引會把同一天算兩次(灌水配對樣本)→ 判無效、
    退回位置配對並告警(整面掃新防禦格)。"""
    rng = np.random.default_rng(30)
    r = (0.0006 + 0.01 * rng.standard_normal(100)).tolist()
    bench = (0.0003 + 0.01 * rng.standard_normal(100)).tolist()
    idx = list(range(99)) + [98]                        # 重複 98
    out = analyze({"mode": "returns", "returns": r, "periods_per_year": 252,
                   "benchmark_returns": bench[:100], "benchmark_idx": idx})
    assert out["ok"]
    assert "bench_pair_idx_invalid" in _codes(out["warnings_coded"])
    assert out["benchmark_compare"]["paired"] is False


def test_bench_pair_too_few_honest_skip_wording():
    """順修 LOW:配對索引經剔列同步後剩 <2 對 → 誠實【略過】(bench_pair_too_few),
    不再謊稱「退回位置配對」(舊 bench_pair_idx_invalid 措辭)、bench_cmp 為 None。"""
    rng = np.random.default_rng(15)
    r = (0.0006 + 0.01 * rng.standard_normal(100)).tolist()
    r[10] = None
    r[50] = None
    out = analyze({"mode": "returns", "returns": r, "periods_per_year": 252,
                   "benchmark_returns": [0.001, -0.002],
                   "benchmark_idx": [10, 50]})          # 兩個配對日恰好都被剔除
    assert out["ok"]
    codes = _codes(out["warnings_coded"])
    assert "bench_pair_too_few" in codes
    assert "bench_pair_idx_invalid" not in codes
    assert out["benchmark_compare"] is None


# ===========================================================================
# 必修3:基準端與 turnover 的 fail-open 收口(絕不填 0)
# ===========================================================================
def _noise_matrix(rng, T, names):
    return {nm: (0.0002 + 0.01 * rng.standard_normal(T)).tolist() for nm in names}


def test_fwer_bench_nonfinite_over_threshold_degrades():
    """基準 10% NaN:修前被靜默填 0(稀釋基準、反保守)——修後誠實降級 zero_fallback
    +警語,coverage=有限值比例。"""
    rng = np.random.default_rng(16)
    mat = _noise_matrix(rng, 200, ["A", "B", "C"])
    bench = (0.0002 + 0.01 * rng.standard_normal(200)).tolist()
    for i in range(0, 200, 10):
        bench[i] = None                                 # 10% NaN
    out = analyze({"mode": "matrix", "matrix": mat, "n_trials": 3,
                   "periods_per_year": 252, "benchmark_returns": bench})
    assert out["ok"]
    assert out["fwer"]["benchmark_kind"] == "zero_fallback"
    w = next(c for c in out["warnings_coded"]
             if c["code"] == "fwer_bench_nonfinite_fallback_zero")
    assert w["params"]["coverage"] == pytest.approx(0.9)
    assert out["fwer"]["bench_coverage"] == pytest.approx(0.9)


def test_fwer_bench_nonfinite_small_dropped_equals_hand():
    """基準 2% NaN:該些列從 FWER 計算剔除(mat+bench 一起)+揭露,
    與手動剔除版逐位一致(bootstrap 固定 seed)。"""
    rng = np.random.default_rng(17)
    mat = _noise_matrix(rng, 200, ["A", "B", "C"])
    bench = (0.0002 + 0.01 * rng.standard_normal(200)).tolist()
    bad = (7, 77, 150)
    for i in bad:
        bench[i] = float("nan")
    out = analyze({"mode": "matrix", "matrix": mat, "n_trials": 3,
                   "periods_per_year": 252, "benchmark_returns": bench})
    assert out["ok"]
    assert out["fwer"]["benchmark_kind"] == "aligned"
    w = next(c for c in out["warnings_coded"]
             if c["code"] == "fwer_bench_nonfinite_rows_dropped")
    assert w["params"]["n_dropped"] == 3 and w["params"]["n_used"] == 197
    kept = [i for i in range(200) if i not in bad]
    hand_mat = {nm: [mat[nm][i] for i in kept] for nm in mat}
    ref = analyze({"mode": "matrix", "matrix": hand_mat, "n_trials": 3,
                   "periods_per_year": 252,
                   "benchmark_returns": [bench[i] for i in kept]})
    assert out["fwer"]["spa"]["p_value"] == pytest.approx(
        ref["fwer"]["spa"]["p_value"], abs=0.0)
    assert out["fwer"]["n_rejected"] == ref["fwer"]["n_rejected"]
    # 主判決(PBO/metrics)不吃基準缺值:維持全 200 期
    assert out["metrics"]["n_periods"] == 200


def test_bench_cmp_nonfinite_pairwise_dropped_equals_hand():
    """基準比較:配對子集內 pairwise 剔除非有限對+n_paired 如實反映+揭露,
    與手動清理版逐位一致(修前 nan→0 稀釋基準)。"""
    rng = np.random.default_rng(18)
    r = 0.0006 + 0.01 * rng.standard_normal(150)
    bench = 0.0003 + 0.01 * rng.standard_normal(150)
    b_in = bench.tolist()
    bad = (5, 60, 100)
    for i in bad:
        b_in[i] = None
    out = analyze({"mode": "returns", "returns": r.tolist(), "periods_per_year": 252,
                   "benchmark_returns": b_in})
    w = next(c for c in out["warnings_coded"]
             if c["code"] == "bench_pairs_dropped_nonfinite")
    assert w["params"]["n_dropped"] == 3 and w["params"]["n_paired"] == 147
    bc = out["benchmark_compare"]
    kept = [i for i in range(150) if i not in bad]
    assert bc["n_paired"] == 147
    assert bc["bench_sharpe"] == pytest.approx(
        _sharpe_annual([bench[i] for i in kept]), rel=1e-12)
    assert bc["strat_sharpe_paired"] == pytest.approx(
        _sharpe_annual([r[i] for i in kept]), rel=1e-12)


def test_turnover_nonfinite_small_dropped_equals_hand():
    """turnover 2% NaN:該些期從成本計算剔除+揭露,與手動清理版逐位一致(絕不填 0)。"""
    rng = np.random.default_rng(19)
    r = 0.0006 + 0.01 * rng.standard_normal(200)
    tn = np.abs(rng.standard_normal(200))
    tn_in = tn.tolist()
    bad = (3, 120)
    for i in bad:
        tn_in[i] = None
    out = analyze({"mode": "returns", "returns": r.tolist(), "periods_per_year": 252,
                   "turnover": tn_in, "cost_bps_per_turnover": 20})
    w = next(c for c in out["warnings_coded"]
             if c["code"] == "turnover_nonfinite_dropped")
    assert w["params"]["n_dropped"] == 2 and w["params"]["n_used"] == 198
    kept = [i for i in range(200) if i not in bad]
    exp = cost_stress(np.asarray([r[i] for i in kept]),
                      np.asarray([tn[i] for i in kept]), 20.0, 252.0)
    for k in ("x1_sharpe", "x3_sharpe", "x6_sharpe"):
        assert out["cost_stress"][k] == pytest.approx(exp[k], rel=1e-12), k


def test_turnover_nonfinite_over_threshold_skips_cost_gate():
    """turnover 10% NaN:填 0 會低估成本 → 修後【跳過】成本閘+警語,絕不填 0。"""
    rng = np.random.default_rng(20)
    r = (0.0006 + 0.01 * rng.standard_normal(200)).tolist()
    tn = np.abs(rng.standard_normal(200)).tolist()
    for i in range(0, 200, 10):
        tn[i] = None
    out = analyze({"mode": "returns", "returns": r, "periods_per_year": 252,
                   "turnover": tn, "cost_bps_per_turnover": 20})
    assert out["cost_stress"] is None
    w = next(c for c in out["warnings_coded"]
             if c["code"] == "turnover_nonfinite_skipped")
    assert w["params"]["n_nonfinite"] == 20


def test_cost_stress_unit_never_zero_fills():
    """cost_stress 單元層(直呼方最後防線):非有限對逐期剔除,與手動清理版一致。"""
    r = np.array([0.01, float("nan"), 0.02, -0.01, 0.005])
    tn = np.array([1.0, 1.0, float("nan"), 0.5, 2.0])
    out = cost_stress(r, tn, 20.0, 252.0)
    ref = cost_stress(np.array([0.01, -0.01, 0.005]), np.array([1.0, 0.5, 2.0]),
                      20.0, 252.0)
    for k in out:
        assert out[k] == pytest.approx(ref[k], rel=1e-12)


# ===========================================================================
# 必修4:整面掃新防禦格(型別 / n_trials / ppy / cost_bps / ±inf / 極端值 / 長度)
# ===========================================================================
def test_invalid_values_type_structured_reject():
    out = analyze({"mode": "returns", "returns": ["a", "b", "c"]})
    assert out["ok"] is False
    assert "invalid_values_type" in _codes(out["warnings_coded"])
    out2 = analyze({"mode": "matrix", "matrix": {"A": ["x"] * 5, "B": [0.1] * 5}})
    assert out2["ok"] is False
    assert "invalid_values_type" in _codes(out2["warnings_coded"])


def test_n_trials_invalid_falls_back_to_one_with_warning():
    rng = np.random.default_rng(21)
    r = (0.0006 + 0.01 * rng.standard_normal(100)).tolist()
    for bad in ("abc", -5):
        out = analyze({"mode": "returns", "returns": r, "n_trials": bad,
                       "periods_per_year": 252})
        assert out["ok"]
        assert "n_trials_invalid" in _codes(out["warnings_coded"]), bad
        assert out["dsr"]["n_trials"] == 1


def test_ppy_invalid_warns_and_falls_back():
    """periods_per_year=-1 / 'abc':修前 -1 會讓年化吃 sqrt(負數) 靜默變 NaN。"""
    rng = np.random.default_rng(22)
    r = (0.0006 + 0.01 * rng.standard_normal(100)).tolist()
    ref = analyze({"mode": "returns", "returns": r, "periods_per_year": 252})
    for bad in (-1, "abc", float("nan")):
        out = analyze({"mode": "returns", "returns": r, "periods_per_year": bad})
        assert out["ok"], bad
        assert "ppy_invalid" in _codes(out["warnings_coded"]), bad
        assert out["metrics"]["sharpe"] == pytest.approx(ref["metrics"]["sharpe"])


def test_turnover_negative_treated_as_invalid():
    """負 turnover 會讓 net = r - tn×cost 反向加分(負成本=倒貼)——與非有限同門檻:
    <5% 剔除+揭露;≥5% 跳閘。"""
    rng = np.random.default_rng(31)
    r = 0.0006 + 0.01 * rng.standard_normal(200)
    tn = np.abs(rng.standard_normal(200))
    tn_in = tn.tolist()
    tn_in[5] = -3.0                                     # 1 期負值(<5%)
    out = analyze({"mode": "returns", "returns": r.tolist(), "periods_per_year": 252,
                   "turnover": tn_in, "cost_bps_per_turnover": 20})
    w = next(c for c in out["warnings_coded"]
             if c["code"] == "turnover_nonfinite_dropped")
    assert w["params"]["n_dropped"] == 1
    kept = [i for i in range(200) if i != 5]
    exp = cost_stress(np.asarray([r[i] for i in kept]),
                      np.asarray([tn[i] for i in kept]), 20.0, 252.0)
    assert out["cost_stress"]["x3_sharpe"] == pytest.approx(exp["x3_sharpe"], rel=1e-12)
    # ≥5%:跳閘
    tn_bad = tn.tolist()
    for i in range(0, 200, 10):
        tn_bad[i] = -1.0
    out2 = analyze({"mode": "returns", "returns": r.tolist(), "periods_per_year": 252,
                    "turnover": tn_bad, "cost_bps_per_turnover": 20})
    assert out2["cost_stress"] is None
    assert "turnover_nonfinite_skipped" in _codes(out2["warnings_coded"])


def test_bench_turnover_invalid_type_skipped_with_warning():
    """基準/turnover 含非數字型別:略過該通道+警語(不裸例外、不影響主判決)。"""
    rng = np.random.default_rng(32)
    r = (0.0006 + 0.01 * rng.standard_normal(100)).tolist()
    out = analyze({"mode": "returns", "returns": r, "periods_per_year": 252,
                   "benchmark_returns": ["x"] * 100})
    assert out["ok"] and out["benchmark_compare"] is None
    assert "bench_invalid_type" in _codes(out["warnings_coded"])
    out2 = analyze({"mode": "returns", "returns": r, "periods_per_year": 252,
                    "turnover": ["x"] * 100, "cost_bps_per_turnover": 20})
    assert out2["ok"] and out2["cost_stress"] is None
    assert "turnover_invalid_type" in _codes(out2["warnings_coded"])


def test_turnover_length_mismatch_disclosed():
    """turnover 長度與報酬期數不符:修前完全靜默 min 截斷 → 修後照實揭露。"""
    rng = np.random.default_rng(33)
    r = (0.0006 + 0.01 * rng.standard_normal(120)).tolist()
    tn = np.abs(rng.standard_normal(90)).tolist()
    out = analyze({"mode": "returns", "returns": r, "periods_per_year": 252,
                   "turnover": tn, "cost_bps_per_turnover": 20})
    assert out["ok"] and out["cost_stress"] is not None
    w = next(c for c in out["warnings_coded"] if c["code"] == "turnover_len_mismatch")
    assert w["params"]["turnover_len"] == 90 and w["params"]["n_periods"] == 120


def test_cost_bps_negative_skips_with_warning():
    """cost_bps<0 = 負成本(倒貼)會人為抬高壓力後夏普 → 略過成本閘+警語。"""
    rng = np.random.default_rng(23)
    r = (0.0006 + 0.01 * rng.standard_normal(100)).tolist()
    tn = np.abs(rng.standard_normal(100)).tolist()
    out = analyze({"mode": "returns", "returns": r, "periods_per_year": 252,
                   "turnover": tn, "cost_bps_per_turnover": -20})
    assert out["cost_stress"] is None
    assert "cost_bps_invalid" in _codes(out["warnings_coded"])


def test_inf_treated_as_missing():
    """±inf 走缺值守衛(isfinite):<5% 剔除+揭露;≥5% 拒審。"""
    rng = np.random.default_rng(24)
    r = (0.0006 + 0.01 * rng.standard_normal(250)).tolist()
    r[10] = float("inf")
    r[100] = float("-inf")
    out = analyze({"mode": "returns", "returns": r, "periods_per_year": 252})
    assert out["ok"] and out["metrics"]["n_periods"] == 248
    assert "missing_values_dropped" in _codes(out["warnings_coded"])


def test_extreme_returns_unit_error_warned():
    """|單期報酬| ≥ 10(=1000%):多半是百分比當小數的單位錯 → 警語提示(不拒審)。"""
    rng = np.random.default_rng(25)
    r = (0.05 + 1.0 * rng.standard_normal(100)).tolist()   # 5% 誤填成 5.0 的世界
    r[3] = 12.0
    out = analyze({"mode": "returns", "returns": r, "periods_per_year": 252})
    assert out["ok"]
    w = next(c for c in out["warnings_coded"] if c["code"] == "extreme_returns")
    assert w["params"]["n"] >= 1 and w["params"]["max_abs"] >= 12.0
    # 正常量級資料不誤報
    out2 = analyze({"mode": "returns",
                    "returns": (0.0006 + 0.01 * rng.standard_normal(100)).tolist(),
                    "periods_per_year": 252})
    assert "extreme_returns" not in _codes(out2["warnings_coded"])


def test_dates_length_mismatch_warned():
    """dates 與 values 長度不符:列級檢查無法執行 → 誠實告警(修前完全靜默)。"""
    rng = np.random.default_rng(26)
    r = (0.0006 + 0.01 * rng.standard_normal(100)).tolist()
    out = analyze({"mode": "returns", "returns": r, "dates": _biz_dates(80)})
    assert out["ok"]
    w = next(c for c in out["warnings_coded"] if c["code"] == "dates_len_mismatch")
    assert w["params"]["n_dates"] == 80 and w["params"]["n_periods"] == 100


# ===========================================================================
# 必修5b:nav prev==0 → NaN 傳播(兩層一致;引擎端到端)
# ===========================================================================
def test_nav_zero_prev_propagates_nan():
    """淨值途中出現 0:下一期報酬不可定義 → NaN 傳播交缺值守衛
    (舊行為記 0.0 = 憑空捏造持平日、稀釋波動)。該 NaN 走 analyze 守衛 → 剔除+揭露。"""
    rng = np.random.default_rng(27)
    nav = np.cumprod(1.0 + 0.001 + 0.01 * rng.standard_normal(120)) * 100.0
    nav_l = nav.tolist()
    nav_l[60] = 0.0                        # 憑空一個 0(壞資料)
    conv = nav_to_returns(nav_l)
    assert np.isnan(conv[61])              # prev==0 → NaN(不再是 0.0)
    assert conv[60] == pytest.approx(-1.0)  # 掉到 0 本身是可定義的 -100%
    out = analyze({"mode": "returns", "returns": conv, "periods_per_year": 252})
    assert out["ok"]
    w = next(c for c in out["warnings_coded"] if c["code"] == "missing_values_dropped")
    assert w["params"]["n_missing"] == 1


def test_zero_crossing_nav_survives_as_returns_without_crash():
    """含 0 的「淨值」序列會被權威偵測判為 returns(all-positive 不成立,偵測邊界既有
    文件化行為)——巨量值曾讓 cagr 冪運算 OverflowError 裸崩(R19 整面掃抓到的崩潰格)。
    修後:不崩、extreme_returns 警語照實提示單位錯。"""
    rng = np.random.default_rng(29)
    nav = np.cumprod(1.0 + 0.001 + 0.01 * rng.standard_normal(120)) * 100.0
    nav_l = nav.tolist()
    nav_l[60] = 0.0
    p = detect_and_convert({"mode": "returns",
                            "raw": {"values": nav_l, "js_kind": "nav"}})
    out = analyze(p)                       # 修前:OverflowError 裸例外
    assert out["ok"]
    assert "extreme_returns" in _codes(out["warnings_coded"])


# ===========================================================================
# 必修5c:n_trials=1 的 DSR 判決句分流(不再講「扣掉試了 1 種參數的多重檢定」)
# ===========================================================================
def test_dsr_wording_n_trials_one_no_deflation_language():
    rng = np.random.default_rng(28)
    good = (0.002 + 0.008 * rng.standard_normal(300)).tolist()   # 高分路徑
    out = analyze({"mode": "returns", "returns": good, "n_trials": 1,
                   "periods_per_year": 252})
    reasons = out["verdict"]["reasons"]
    dsr_r = [r for r in reasons if r.startswith("通縮夏普")]
    assert dsr_r and not any("試了 1 種參數" in r for r in dsr_r), dsr_r
    assert any("未申報參數搜尋" in r for r in dsr_r), dsr_r
    coded = next(c for c in out["verdict"]["reasons_coded"]
                 if c["code"].startswith("dsr_"))
    assert coded["params"].get("n_trials") == 1
    # 低分路徑(below floor)同樣分流
    bad = (-0.002 + 0.01 * rng.standard_normal(300)).tolist()
    out2 = analyze({"mode": "returns", "returns": bad, "n_trials": 1,
                    "periods_per_year": 252})
    dsr_r2 = [r for r in out2["verdict"]["reasons"] if r.startswith("通縮夏普")]
    if dsr_r2 and "< 0.60" in dsr_r2[0]:
        assert "多重檢定後" not in dsr_r2[0] or "無多重檢定通縮" in dsr_r2[0], dsr_r2
    # n_trials>1 的既有措辭不變
    out3 = analyze({"mode": "returns", "returns": good, "n_trials": 12,
                    "periods_per_year": 252})
    assert any("試了 12 種參數" in r for r in out3["verdict"]["reasons"])


# ===========================================================================
# R19b:毒日期繞過(驗收官 B blocker)、兩層政策收斂與 LOW 清尾
# ===========================================================================
def test_poison_date_cannot_disarm_duplicate_guard():
    """毒日期繞過(R19b 必修1,HIGH blocker):R18 騙局(240/490 重複好日子)只要把
    1 格日期改成垃圾字串,修前整個守衛 early-return → 86 分 likely-real、唯一警語
    ppy_fallback;修後可解析列照常判重 → 拒審(README「重複時戳 ≥5% 一律拒審」為真)。"""
    r, dates = _losing_series()
    best = np.argsort(r)[-60:]
    rows = list(zip(dates, r.tolist()))
    for _ in range(4):
        rows += [(dates[int(i)], float(r[int(i)])) for i in best]
    rows.sort(key=lambda x: x[0])
    vals = [x[1] for x in rows]
    ds = [x[0] for x in rows]
    ds[0] = "n/a-garbage"
    out = analyze({"mode": "returns", "returns": vals, "dates": ds, "n_trials": 1})
    assert out["ok"] is False
    assert "duplicate_dates_reject" in _codes(out["warnings_coded"])


def test_format_variant_duplicates_with_garbage_still_caught():
    """格式變體(2023-01-16 vs 2023/1/16 同刻)+ 垃圾日期同時在場:pandas 整欄格式
    推斷會把變體 coerce 成 NaT(等於幫閃避洗白)→ 引擎須退 format='mixed' 逐元素
    解析,同刻重複照抓、照樣拒審。"""
    r, dates = _losing_series(n=250)
    best = np.argsort(r)[-30:]
    vals = r.tolist() + [float(r[int(i)]) for i in best]
    variant = [dates[int(i)].replace("-0", "-").replace("-", "/") for i in best]
    ds = dates + variant                     # 30/280 ≈ 10.7% 同刻重複
    ds[5] = "garbage-date"                   # 再摻 1 格垃圾
    out = analyze({"mode": "returns", "returns": vals, "dates": ds, "n_trials": 1})
    assert out["ok"] is False
    assert "duplicate_dates_reject" in _codes(out["warnings_coded"])


def test_all_garbage_dates_equivalent_to_no_dates():
    """全垃圾日期 = 行為等同無日期(不給新能力):判決層與無日期版逐位一致、
    ppy_fallback 照發、不發「部分解析」警語(沒有可解析列可言)。"""
    rng = np.random.default_rng(41)
    r = (0.0005 + 0.01 * rng.standard_normal(60)).tolist()
    out = analyze({"mode": "returns", "returns": r, "dates": ["n/a"] * 60})
    ref = analyze({"mode": "returns", "returns": r})
    codes = _codes(out["warnings_coded"])
    assert out["ok"] and "ppy_fallback" in codes
    assert "duplicate_dates_reject" not in codes
    assert "duplicate_dates_dropped" not in codes
    assert "dates_partially_unparseable" not in codes
    _assert_same_judgment(out, ref)


def test_partial_garbage_dates_skip_sort_disclosed():
    """垃圾日期在場 → 跳過排序步(無法建立全序)+ dates_partially_unparseable 揭露;
    資料保持原順序(判決與無日期版逐位一致)。"""
    rng = np.random.default_rng(42)
    n = 100
    r = 0.0006 + 0.01 * rng.standard_normal(n)
    dates = _biz_dates(n)
    perm = rng.permutation(n)
    ds = [dates[i] for i in perm]
    ds[7] = "not-a-date"
    out = analyze({"mode": "returns", "returns": r[perm].tolist(), "dates": ds,
                   "periods_per_year": 252})
    codes = _codes(out["warnings_coded"])
    assert out["ok"]
    assert "dates_partially_unparseable" in codes and "dates_sorted" not in codes
    w = next(c for c in out["warnings_coded"]
             if c["code"] == "dates_partially_unparseable")
    assert w["params"]["n_unparseable"] == 1
    ref = analyze({"mode": "returns", "returns": r[perm].tolist(),
                   "periods_per_year": 252})
    _assert_same_judgment(out, ref)


def test_small_dup_with_garbage_dropped_and_disclosed():
    """<5% 重複 + 垃圾在場:可解析列的重複照剔(保留首見)、垃圾列保留不剔、
    duplicate_dates_dropped 與 dates_partially_unparseable 並發。"""
    rng = np.random.default_rng(43)
    n = 200
    r = (0.0004 + 0.01 * rng.standard_normal(n)).tolist()
    d2 = _biz_dates(n)
    d2[50] = d2[49]
    d2[150] = d2[149]                        # 2 列重複(1%)
    d2[10] = "N/A"                           # 1 列垃圾
    out = analyze({"mode": "returns", "returns": r, "dates": d2,
                   "periods_per_year": 252})
    codes = _codes(out["warnings_coded"])
    assert out["ok"]
    assert "duplicate_dates_dropped" in codes
    assert "dates_partially_unparseable" in codes
    assert out["metrics"]["n_periods"] == 198   # 只剔 2 列重複;垃圾列保留


def test_turnover_len_mismatch_fires_on_sorted_data():
    """R19b 必修2(LOW):長度檢查移到截斷/排序之前——修前 tn[:n][sort_perm] 先把
    長度切齊,turnover_len_mismatch 在日期被排序的資料上永不觸發。"""
    rng = np.random.default_rng(44)
    n = 120
    r = 0.0006 + 0.01 * rng.standard_normal(n)
    dates = _biz_dates(n)
    perm = rng.permutation(n)
    tn = np.abs(rng.standard_normal(n + 15))
    out = analyze({"mode": "returns", "returns": r[perm].tolist(),
                   "dates": [dates[i] for i in perm],
                   "turnover": tn.tolist(), "cost_bps_per_turnover": 20,
                   "periods_per_year": 252})
    codes = _codes(out["warnings_coded"])
    assert "dates_sorted" in codes and "turnover_len_mismatch" in codes
    assert out["cost_stress"] is not None
    w = next(c for c in out["warnings_coded"] if c["code"] == "turnover_len_mismatch")
    assert w["params"]["turnover_len"] == n + 15 and w["params"]["n_periods"] == n


def test_n_trials_zero_falls_back_with_warning():
    """R19b 必修3(LOW):n_trials=0 修前被 `or 1` 當 falsy 靜默洗成 1(無警語,
    負值反而有)→ 修後走既有 n_trials_invalid 告警路徑,行為維持保守回退 1。"""
    rng = np.random.default_rng(45)
    r = (0.0006 + 0.01 * rng.standard_normal(100)).tolist()
    out = analyze({"mode": "returns", "returns": r, "n_trials": 0,
                   "periods_per_year": 252})
    assert out["ok"]
    assert "n_trials_invalid" in _codes(out["warnings_coded"])
    assert out["dsr"]["n_trials"] == 1


def test_ppy_zero_ignored_with_warning():
    """R19b 必修3(LOW):periods_per_year=0(數值)修前被 `if fallback:` 當 falsy
    靜默跳過驗證(字串 "0" 與 -3 反而有警語)→ 修後同路徑 ppy_invalid 告警,
    改由日期推斷(無日期回退 252),數字不變。"""
    rng = np.random.default_rng(46)
    r = (0.0006 + 0.01 * rng.standard_normal(100)).tolist()
    ref = analyze({"mode": "returns", "returns": r, "periods_per_year": 252})
    out = analyze({"mode": "returns", "returns": r, "periods_per_year": 0})
    assert out["ok"]
    assert "ppy_invalid" in _codes(out["warnings_coded"])
    assert out["metrics"]["sharpe"] == pytest.approx(ref["metrics"]["sharpe"])
