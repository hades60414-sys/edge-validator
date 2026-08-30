# -*- coding: utf-8 -*-
"""R21 手術回歸測試(對應 R20 panel 必修清單)。

必修2(MED):<5% 缺值剔除窗可買判決翻轉 → 敏感度試算 + fail-closed 降級。
  騙局復刻:400 期誠實 inconclusive(夏普 ~0.7)的策略挖掉自己最差 19 天(4.75%<5%)
  → 剔除後夏普 ~2.7、86 分 likely-real。修後:以「缺值集中在極端虧損日」情境
  (觀測最差單期報酬補入;p5 實測打不破 0.95 高信心地板=規則形同虛設,故用 min)
  重算夏普/DSR,跌破雜訊地板(0.60)或高信心地板(0.95,原判決倚賴的 DSR 支柱)
  → 降級 inconclusive;誠實強 edge 存活、無缺值路徑零行為改變。
必修3(LOW):混時區時間戳讓兩次解析都 raise → 守衛靜默解除。修後 utc=True 重試
  (統一 UTC 後照常判重/排序),再失敗才跳過且必發 date_guard_skipped_unparseable。
必修4(LOW):benchmark_idx 型別異常(字串元素/非可迭代/非整數浮點)direct-API 裸崩
  或靜默地板截斷 → 一律走 bench_pair_idx_invalid 警語+位置配對退路。
(必修1 README 校準釘子在 test_readme_claims.py;必修5 前端圖表座標在
 tools/verify_align.node.js 的 R20 斷言區。)

R23 收尾手術(檔尾 R23 區):補入值收緊為 min(觀測 min, −3σ_obs)——R22 quant 的
有界分布騙局(平滑收租+挖崩盤日,觀測 min 太溫和打不穿判準)修法;肥尾誠實資料
觀測 min 通常已 <−3σ → 行為不變;誤傷率(誠實 edge 被降級的代價)以多 seed 量測
入檔並斷言區間(數字同步揭露於 docs/INTEGRITY_MATRIX.md)。
"""
import numpy as np
import pandas as pd
import pytest

from engine import analyze


def _codes(coded):
    return [c["code"] for c in coded]


def _sharpe_annual(r, ppy=252.0):
    r = np.asarray(r, dtype=float)
    sd = r.std(ddof=0)
    assert sd > 0
    return float((r.mean() * ppy) / (sd * np.sqrt(ppy)))


# ===========================================================================
# 必修2:缺值剔除敏感度 fail-closed
# ===========================================================================
def _scam_payload():
    """騙局復刻:誠實 inconclusive 策略,挖掉自己最差 19/400 天(4.75% < 5% 門檻)。"""
    rng = np.random.default_rng(20260710)
    r = 0.0004 + 0.0105 * rng.standard_normal(400)
    vals = r.tolist()
    for i in np.argsort(r)[:19]:
        vals[int(i)] = None
    return r, vals


def test_missing_scam_downgraded_not_likely_real():
    """挖最差 19/400 → 修後必須不再 likely-real:降級 inconclusive + reason code。"""
    r, vals = _scam_payload()
    honest = analyze({"mode": "returns", "returns": r.tolist(),
                      "periods_per_year": 252, "n_trials": 1})
    assert honest["verdict"]["overall"] == "inconclusive"  # 騙局前提:誠實版本判不明
    out = analyze({"mode": "returns", "returns": vals,
                   "periods_per_year": 252, "n_trials": 1})
    assert out["ok"] is True
    assert out["metrics"]["sharpe"] > 2.0            # 剔除後表面夏普確實被灌高(騙局存在)
    assert out["verdict"]["overall"] != "likely-real"
    assert out["verdict"]["overall"] == "inconclusive"
    codes = _codes(out["verdict"]["reasons_coded"])
    assert "missing_sensitivity_downgrade" in codes
    assert "missing_sensitivity_unstable" in _codes(out["verdict"]["red_flags_coded"])
    dg = next(c for c in out["verdict"]["reasons_coded"]
              if c["code"] == "missing_sensitivity_downgrade")
    assert dg["params"]["n_missing"] == 19
    assert dg["params"]["dsr_sensitivity"] < dg["params"]["bar"]
    ms = out["missing_sensitivity"]
    assert ms["unstable"] is True and ms["bar_crossed"] is not None
    # 敏感度後的夏普確實顯著低於表面夏普(補入的正是騙局挖掉的那類壞日)
    assert ms["sharpe_sensitivity"] < ms["sharpe_observed"] - 0.5


def test_missing_sensitivity_warning_carries_numbers():
    """警語升級:missing_values_dropped 必含敏感度數字(fill_value/夏普前後/DSR 前後)
    與「真實報酬不可驗證/極端虧損日會高估」措辭。"""
    _, vals = _scam_payload()
    out = analyze({"mode": "returns", "returns": vals,
                   "periods_per_year": 252, "n_trials": 1})
    w = next(c for c in out["warnings_coded"] if c["code"] == "missing_values_dropped")
    for k in ("fill_value", "sharpe_observed", "sharpe_sensitivity",
              "dsr_observed", "dsr_sensitivity", "n_missing", "n_kept"):
        assert k in w["params"], k
    wi = _codes(out["warnings_coded"]).index("missing_values_dropped")
    zh = out["warnings"][wi]
    assert "不可驗證" in zh and "極端虧損日" in zh and "敏感度試算" in zh
    assert f"{w['params']['sharpe_sensitivity']:.2f}" in zh


def test_missing_sensitivity_honest_strong_edge_survives():
    """誠實強 edge(樣本夏普 2.5+)含 2% 隨機缺值:敏感度存活、判決不動、警語含數字。"""
    rng = np.random.default_rng(3)
    r = 0.0017 + 0.0105 * rng.standard_normal(400)
    assert _sharpe_annual(r) >= 2.5
    vals = r.tolist()
    for i in rng.choice(400, 8, replace=False):   # 2% 隨機缺值
        vals[int(i)] = None
    out = analyze({"mode": "returns", "returns": vals,
                   "periods_per_year": 252, "n_trials": 1})
    assert out["ok"] and out["verdict"]["overall"] == "likely-real"
    assert "missing_sensitivity_downgrade" not in _codes(out["verdict"]["reasons_coded"])
    ms = out["missing_sensitivity"]
    assert ms is not None and ms["unstable"] is False
    assert ms["dsr_sensitivity"] >= ms["conf_bar"]   # 支柱在最差日補入下仍站得住
    w = next(c for c in out["warnings_coded"] if c["code"] == "missing_values_dropped")
    assert "sharpe_sensitivity" in w["params"]       # 只揭露不降級


def test_no_missing_clean_path_unchanged():
    """無缺值路徑零行為改變:missing_sensitivity=None、無敏感度警語/降級 code,
    判決與指標和乾淨基準完全一致(同一輸入跑兩次逐位確定性)。"""
    rng = np.random.default_rng(6)
    r = (0.0008 + 0.01 * rng.standard_normal(300)).tolist()
    out = analyze({"mode": "returns", "returns": r, "periods_per_year": 252,
                   "n_trials": 1})
    assert out["missing_sensitivity"] is None
    assert "missing_values_dropped" not in _codes(out["warnings_coded"])
    assert "missing_sensitivity_downgrade" not in _codes(out["verdict"]["reasons_coded"])
    out2 = analyze({"mode": "returns", "returns": r, "periods_per_year": 252,
                    "n_trials": 1})
    assert out == out2


def test_missing_sensitivity_matrix_winner_uses_known_values():
    """matrix 贏家欄敏感度:被剔列中贏家欄自己【有值】者用真實值補(已知就用已知),
    只有贏家欄自身缺值才補觀測 min——與手算逐位一致。"""
    rng = np.random.default_rng(4)
    T = 200
    mat = {"A": (0.0025 + 0.01 * rng.standard_normal(T)).tolist(),   # 贏家(高漂移)
           "B": (0.0002 + 0.01 * rng.standard_normal(T)).tolist(),
           "C": (0.0002 + 0.01 * rng.standard_normal(T)).tolist()}
    ref_A = list(mat["A"])
    mat["B"][10] = None
    mat["B"][60] = None
    mat["C"][130] = None            # 3 列剔除;贏家欄 A 在這三列都有真實值
    out = analyze({"mode": "matrix", "matrix": mat, "n_trials": 3,
                   "periods_per_year": 252})
    assert out["ok"]
    ms = out["missing_sensitivity"]
    assert ms is not None and ms["winner_known_used"] is True and ms["n_missing"] == 3
    # 手算真值:剔除後的 A 欄 + 被剔三列的 A 真實值 = 完整原 A 欄(順序不影響夏普)
    assert ms["sharpe_sensitivity"] == pytest.approx(_sharpe_annual(ref_A), rel=1e-12)
    w = next(c for c in out["warnings_coded"] if c["code"] == "missing_rows_dropped")
    assert w["params"]["winner_known_used"] is True


# ===========================================================================
# 必修3:混時區時間戳 → utc=True 重試;整批解析失敗 → 跳過必警
# ===========================================================================
def _tz_dates(n, start="2024-01-01 09:00", tz="Asia/Taipei"):
    return pd.date_range(start, periods=n, freq="B", tz=tz)


def test_mixed_timezone_duplicate_scam_caught():
    """混時區重複騙局:同一瞬間以 +08:00 與 +00:00 兩種寫法灌重複(≥5%)——
    修前兩次解析都 raise → 守衛靜默解除;修後 utc=True 統一後照抓,拒審。"""
    rng = np.random.default_rng(21)
    base = _tz_dates(100)
    r = (0.0005 + 0.01 * rng.standard_normal(100)).tolist()
    dates = [d.isoformat() for d in base]
    vals = list(r)
    for i in np.argsort(r)[-6:]:                  # 複製 6 個最好日(6% ≥ 5%)
        vals.append(r[int(i)])
        dates.append(base[int(i)].tz_convert("UTC").isoformat())  # 換時區寫法閃避
    out = analyze({"mode": "returns", "returns": vals, "dates": dates})
    assert out["ok"] is False
    assert "duplicate_dates_reject" in _codes(out["warnings_coded"])


def test_mixed_timezone_clean_guard_runs_and_ppy_inferred():
    """混時區但各瞬間唯一(正當 ISO-8601):守衛照常執行、不誤傷、不發跳過警語;
    年化頻率由日期正常推斷(不再 ppy_fallback 252 假警)。"""
    rng = np.random.default_rng(22)
    base = _tz_dates(300)
    r = (0.0006 + 0.01 * rng.standard_normal(300)).tolist()
    dates = [(base[i].tz_convert("UTC").isoformat() if i % 2
              else base[i].isoformat()) for i in range(300)]
    out = analyze({"mode": "returns", "returns": r, "dates": dates})
    codes = _codes(out["warnings_coded"])
    assert out["ok"] is True
    for bad in ("duplicate_dates_reject", "duplicate_dates_dropped",
                "date_guard_skipped_unparseable", "ppy_fallback"):
        assert bad not in codes, bad


def test_mixed_timezone_small_dup_dropped():
    """混時區 <5% 重複:保留首見、剔除重複列(與同時區資料同政策)。"""
    rng = np.random.default_rng(23)
    base = _tz_dates(100)
    r = (0.0005 + 0.01 * rng.standard_normal(100)).tolist()
    dates = [d.isoformat() for d in base]
    vals = list(r) + [r[40], r[70]]
    dates += [base[40].tz_convert("UTC").isoformat(),
              base[70].tz_convert("UTC").isoformat()]   # 2/102 ≈ 2%
    out = analyze({"mode": "returns", "returns": vals, "dates": dates})
    assert out["ok"] is True
    w = next(c for c in out["warnings_coded"] if c["code"] == "duplicate_dates_dropped")
    assert w["params"]["n_dup"] == 2 and w["params"]["n_kept"] == 100


def test_date_guard_total_parse_failure_skips_with_warning(monkeypatch):
    """整批解析例外(含 utc 重試)→ 守衛跳過【必發】date_guard_skipped_unparseable
    (修前完全靜默=守衛被解除卻無人知曉),資料原樣通過、不裸例外。"""
    from engine import judge_web as jw

    def _boom(*a, **k):
        raise ValueError("boom")

    monkeypatch.setattr(jw.pd, "to_datetime", _boom)
    warns = []

    def warn(zh, code, **params):
        warns.append(code)

    vals = np.arange(10, dtype=float) / 1000.0
    v2, d2, km, sp, rej = jw._date_integrity_guard(
        vals, ["2024-01-01"] * 10, None, warn)
    assert rej is None and sp is None and km is None
    assert np.array_equal(v2, vals)
    assert warns == ["date_guard_skipped_unparseable"]


# ===========================================================================
# 必修4:benchmark_idx 型別異常 → 警語路徑,不裸崩、不靜默截斷
# ===========================================================================
@pytest.fixture()
def _bench_setup():
    rng = np.random.default_rng(24)
    r = (0.0006 + 0.01 * rng.standard_normal(120)).tolist()
    bench = (0.0003 + 0.01 * rng.standard_normal(120)).tolist()
    return r, bench


@pytest.mark.parametrize("bad_idx", [["a"] * 120, 7, [i + 0.5 for i in range(120)]],
                         ids=["string-elements", "non-iterable-scalar", "non-integer-floats"])
def test_bench_idx_type_anomalies_warn_and_fall_back(_bench_setup, bad_idx):
    """三態釘住:字串元素(修前 ValueError 裸崩)/ 非可迭代(修前 TypeError 裸崩)/
    非整數浮點(修前被 astype(int) 靜默地板截斷照常配對)→ 一律 bench_pair_idx_invalid
    警語 + 位置配對退路。"""
    r, bench = _bench_setup
    out = analyze({"mode": "returns", "returns": r, "periods_per_year": 252,
                   "benchmark_returns": bench, "benchmark_idx": bad_idx})
    assert out["ok"] is True
    assert "bench_pair_idx_invalid" in _codes(out["warnings_coded"])
    bc = out["benchmark_compare"]
    assert bc is not None and bc["paired"] is False and bc["n_paired"] == 120


def test_bench_idx_integral_floats_accepted_as_ints(_bench_setup):
    """整值浮點(1.0)無歧義:照整數接受,與 int 索引結果逐位一致(非截斷產物)。"""
    r, bench = _bench_setup
    out_f = analyze({"mode": "returns", "returns": r, "periods_per_year": 252,
                     "benchmark_returns": bench,
                     "benchmark_idx": [float(i) for i in range(120)]})
    out_i = analyze({"mode": "returns", "returns": r, "periods_per_year": 252,
                     "benchmark_returns": bench,
                     "benchmark_idx": list(range(120))})
    assert out_f["ok"] and out_f["benchmark_compare"]["paired"] is True
    assert out_f["benchmark_compare"] == out_i["benchmark_compare"]
    assert "bench_pair_idx_invalid" not in _codes(out_f["warnings_coded"])


# ===========================================================================
# R23 收尾手術:補入值 = min(觀測 min, −3σ_obs)
# ===========================================================================
def _run_returns(vals):
    return analyze({"mode": "returns", "returns": vals,
                    "periods_per_year": 252, "n_trials": 1})


def test_bounded_scam_downgraded_r23():
    """R22 quant 的有界分布騙局必須降級(R23 必修1)。

    構造(R22 panel 實錄):381 期 uniform[-0.8%, +1.0%] 平滑收租 + 挖掉 19 個 -6%
    崩盤日(4.75%<5%)。修前:觀測 min=-0.8% 補入太溫和,DSR 只壓到 0.99、
    unstable=False → 14 分翻 86 分 likely-real(公開站 E2E 復現)。修後:薄尾平滑
    序列的 −3σ_obs 比觀測 min 深 → fill 收緊,DSR 跌破 0.95 支柱 → 降級。"""
    rng = np.random.default_rng(20260710)
    smooth = rng.uniform(-0.008, 0.010, 381)
    crash = np.full(19, -0.06)
    full = np.concatenate([smooth, crash])
    rng.shuffle(full)
    honest = _run_returns(full.tolist())
    assert honest["verdict"]["overall"] != "likely-real"   # 騙局前提:誠實全序列非真
    vals = full.tolist()
    for i in np.argsort(full)[:19]:
        vals[int(i)] = None
    out = _run_returns(vals)
    assert out["ok"] is True
    ms = out["missing_sensitivity"]
    kept = np.array([v for v in vals if v is not None], dtype=float)
    # 薄尾平滑序列:−3σ 比觀測 min 深 → fill 必須取 −3σ(修前=觀測 min 即放行)
    assert ms["fill_value"] == pytest.approx(-3.0 * kept.std(ddof=0))
    assert ms["fill_value"] < float(kept.min())
    assert ms["unstable"] is True
    assert out["verdict"]["overall"] == "inconclusive"
    assert "missing_sensitivity_downgrade" in _codes(out["verdict"]["reasons_coded"])


def test_fill_value_is_min_of_obs_min_and_minus_3_sigma():
    """fill_v = min(觀測 min, −3σ_obs) 逐位釘死(σ_obs=剔除後序列 std, ddof=0)。

    肥尾(Student-t df=4):觀測 min 已 <−3σ → fill=觀測 min=行為與修前一致;
    薄尾(uniform 正漂移):觀測 min >−3σ → fill 收緊為 −3σ。"""
    # 肥尾:fill = 觀測 min
    rng = np.random.default_rng(42)
    r = 0.0022 + 0.0075 * rng.standard_t(4, 400)
    vals = r.tolist()
    for i in rng.choice(400, 8, replace=False):
        vals[int(i)] = None
    kept = np.array([v for v in vals if v is not None], dtype=float)
    assert float(kept.min()) < -3.0 * kept.std(ddof=0)     # 前提:肥尾觀測 min 更深
    ms = _run_returns(vals)["missing_sensitivity"]
    assert ms["fill_value"] == pytest.approx(float(kept.min()))
    # 薄尾:fill = −3σ
    rng = np.random.default_rng(7)
    r2 = rng.uniform(-0.004, 0.012, 400)
    vals2 = r2.tolist()
    for i in rng.choice(400, 10, replace=False):
        vals2[int(i)] = None
    kept2 = np.array([v for v in vals2 if v is not None], dtype=float)
    assert float(kept2.min()) > -3.0 * kept2.std(ddof=0)   # 前提:薄尾觀測 min 較淺
    ms2 = _run_returns(vals2)["missing_sensitivity"]
    assert ms2["fill_value"] == pytest.approx(-3.0 * kept2.std(ddof=0))


def test_fat_tail_honest_edge_unaffected_by_r23():
    """肥尾誠實強 edge + 2% MCAR:觀測 min<−3σ → fill 不變(=修前行為),不誤傷。"""
    rng = np.random.default_rng(42)
    r = 0.0022 + 0.0075 * rng.standard_t(4, 400)
    vals = r.tolist()
    for i in rng.choice(400, 8, replace=False):
        vals[int(i)] = None
    out = _run_returns(vals)
    assert out["verdict"]["overall"] == "likely-real"
    ms = out["missing_sensitivity"]
    assert ms["unstable"] is False
    assert "missing_sensitivity_downgrade" not in _codes(out["verdict"]["reasons_coded"])


def test_missing_downgrade_cost_table_documented():
    """誤傷率(誠實 edge 因缺值敏感度被降級的代價)文件測試——R22 quant LOW。

    量測設計照 quant(固定 seed、引擎確定性 → 數字精確可重現;斷言用區間而非
    單點,容許未來校準微調但擋住量級漂移)。R23 實測(30 seed/格):
      - 強 edge(漂移 0.0017,樣本年化夏普 ~2.5)+ 2% MCAR:12/30 = 40%(修前 30%)
      - 強 edge + 3% MCAR:21/30 = 70%(修前 57%)
      - 邊界 edge(漂移 0.0011,DSR 貼著 0.95)+ 3% MCAR:20/21 = 95%(修前同 95%)
    這是 fail-closed 的明碼代價:缺值越多、edge 越邊界,越常被誠實降級——
    「補齊資料再驗」是設計立場,不是 bug。數字同步揭露於 docs/INTEGRITY_MATRIX.md
    (兩邊一起改才允許動本測試的區間)。"""
    def cell(drift, n_miss, seeds):
        base_lr, injured = 0, 0
        for seed in seeds:
            rng = np.random.default_rng(seed)
            r = drift + 0.0105 * rng.standard_normal(400)
            if _run_returns(r.tolist())["verdict"]["overall"] != "likely-real":
                continue
            base_lr += 1
            vals = r.tolist()
            for i in rng.choice(400, n_miss, replace=False):
                vals[int(i)] = None
            out = _run_returns(vals)
            ms = out.get("missing_sensitivity") or {}
            if out["verdict"]["overall"] != "likely-real" and ms.get("unstable"):
                injured += 1
        return base_lr, injured

    b1, i1 = cell(0.0017, 8, range(100, 130))    # 強 edge、2% MCAR
    b2, i2 = cell(0.0017, 12, range(100, 130))   # 強 edge、3% MCAR
    b3, i3 = cell(0.0011, 12, range(200, 230))   # 邊界 edge、3% MCAR
    assert b1 >= 20 and b2 >= 20 and b3 >= 15    # 分母有意義(基準 likely-real 夠多)
    assert 0.25 <= i1 / b1 <= 0.55, f"強 edge 2% 誤傷率漂移:{i1}/{b1}"
    assert 0.55 <= i2 / b2 <= 0.85, f"強 edge 3% 誤傷率漂移:{i2}/{b2}"
    assert 0.85 <= i3 / b3 <= 1.00, f"邊界 edge 3% 誤傷率漂移:{i3}/{b3}"
