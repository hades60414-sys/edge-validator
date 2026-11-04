# -*- coding: utf-8 -*-
"""稽核補測(docs/audit/20261104.md):替引擎最重要、先前【沒有直接單元測試】的純函式補釘。

原則:
- 只釘【現行行為】(不改引擎任何一行);數值以手算/閉式解/scipy 對照為準。
- 稽核發現的 bug 以 `xfail(strict=True)` 記錄:現在必然失敗(=bug 仍在),
  修好後 strict xfail 會轉紅提醒移除標記——bug 不會被靜默遺忘,也不會讓套件變紅。

先前只經 analyze() 端到端間接覆蓋(或完全沒覆蓋)的函式:
  _equity_from_returns / _max_drawdown / compute_metrics / _sharpe_periodic /
  expected_max_sharpe / probabilistic_sharpe / harden_matrix_n_trials /
  _circular_block_bootstrap_means / _spa_pvalues / _romano_wolf_adj_p /
  _block_moments / _sharpe_from_moments / pbo_cscv / permutation_null(直呼)/
  cost_stress(直呼)/ _verdict(直呼)/ _parse_dates_series / _missing_reject /
  _finite_list / _clean_returns,以及 statshim 的邊界值。
"""
import math
from itertools import combinations

import numpy as np
import pandas as pd
import pytest

from engine import statshim as ss
from engine import judge_web as jw


# ---------------------------------------------------------------------------
# statshim 邊界
# ---------------------------------------------------------------------------
class TestStatshimEdges:
    def test_norm_ppf_boundaries(self):
        assert ss.norm_ppf(0.0) == -math.inf
        assert ss.norm_ppf(1.0) == math.inf
        assert math.isnan(ss.norm_ppf(-0.1))
        assert math.isnan(ss.norm_ppf(1.1))
        assert math.isnan(ss.norm_ppf(float("nan")))

    @pytest.mark.parametrize("p", [1e-8, 1e-4, 0.02, 0.02425, 0.3, 0.5, 0.7,
                                   0.97575, 0.99, 1 - 1e-6, 1 - 1e-8])
    def test_norm_ppf_vs_scipy_all_three_regions(self, p):
        from scipy import stats
        assert abs(ss.norm_ppf(p) - stats.norm.ppf(p)) < 1e-9

    @pytest.mark.xfail(strict=True, reason="AUDIT L-1:p<1e-8 / p>1-1e-8 時 norm_ppf 誤差 >1e-9"
                       "(norm_cdf 以 0.5*(1+erf) 計算,尾端相消誤差使 Halley 修正失準)")
    @pytest.mark.parametrize("p", [1e-10, 1 - 1e-10])
    def test_audit_l1_norm_ppf_extreme_tail_precision(self, p):
        from scipy import stats
        assert abs(ss.norm_ppf(p) - stats.norm.ppf(p)) < 1e-9

    def test_extreme_tail_error_irrelevant_for_dsr(self):
        # L-1 的實際影響:expected_max_sharpe 在 n_trials ≤ 1e9 時相對誤差仍 < 1e-9
        from scipy import stats
        g = 0.5772156649015329
        for n in (10, 10 ** 5, 10 ** 9):
            ref = (1 - g) * stats.norm.ppf(1 - 1 / n) + g * stats.norm.ppf(1 - 1 / (n * math.e))
            assert abs(jw.expected_max_sharpe(1.0, n) - ref) / ref < 1e-9

    def test_cdf_ppf_roundtrip_and_symmetry(self):
        for p in (0.001, 0.05, 0.25, 0.5, 0.75, 0.95, 0.999):
            assert abs(ss.norm_cdf(ss.norm_ppf(p)) - p) < 1e-12
            assert abs(ss.norm_ppf(p) + ss.norm_ppf(1 - p)) < 1e-9

    def test_array_inputs_return_arrays(self):
        out = ss.norm_cdf([-1.0, 0.0, 1.0])
        assert isinstance(out, np.ndarray) and out.shape == (3,)
        assert abs(out[1] - 0.5) < 1e-15 and abs(out[0] + out[2] - 1.0) < 1e-15
        pp = ss.norm_ppf(np.array([0.025, 0.5, 0.975]))
        assert pp.shape == (3,) and abs(pp[2] - 1.959963984540054) < 1e-9

    def test_norm_cdf_tails(self):
        assert ss.norm_cdf(-40.0) == 0.0
        assert ss.norm_cdf(40.0) == 1.0
        assert ss.norm_cdf(-math.inf) == 0.0 and ss.norm_cdf(math.inf) == 1.0

    def test_moments_degenerate(self):
        assert math.isnan(ss.skew([]))
        assert math.isnan(ss.kurtosis([]))
        assert ss.skew([2.0, 2.0, 2.0]) == 0.0
        assert ss.kurtosis([2.0, 2.0, 2.0]) == 3.0
        assert ss.kurtosis([2.0, 2.0, 2.0], fisher=True) == 0.0

    def test_moments_vs_scipy_asymmetric(self):
        from scipy import stats
        x = np.random.default_rng(3).lognormal(size=400)
        assert abs(ss.skew(x) - stats.skew(x)) < 1e-10
        assert abs(ss.kurtosis(x) - stats.kurtosis(x, fisher=False)) < 1e-10
        assert abs(ss.kurtosis(x, fisher=True) - stats.kurtosis(x)) < 1e-10


# ---------------------------------------------------------------------------
# 1. 績效指標
# ---------------------------------------------------------------------------
class TestEquityAndDrawdown:
    def test_equity_cumprod(self):
        eq = jw._equity_from_returns(np.array([0.1, -0.5, 1.0]))
        assert np.allclose(eq, [1.1, 0.55, 1.1])

    def test_equity_start_scales(self):
        eq = jw._equity_from_returns(np.array([0.1, 0.1]), start=100.0)
        assert np.allclose(eq, [110.0, 121.0])

    def test_max_drawdown_known(self):
        assert jw._max_drawdown(np.array([1.0, 2.0, 1.0, 3.0])) == pytest.approx(-0.5)
        assert jw._max_drawdown(np.array([1.0, 1.5, 2.0])) == 0.0
        assert jw._max_drawdown(np.array([])) == 0.0

    def test_max_drawdown_uses_running_peak_not_first_value(self):
        # 峰在中段:2 → 0.5 = -75%,不是相對首值 1 的 -50%
        assert jw._max_drawdown(np.array([1.0, 2.0, 0.5, 1.8])) == pytest.approx(-0.75)


class TestComputeMetrics:
    def test_hand_computed_two_periods(self):
        m = jw.compute_metrics([0.1, -0.1], periods_per_year=1)
        assert m["n_periods"] == 2
        assert m["final_equity"] == pytest.approx(0.99)
        assert m["total_return"] == pytest.approx(-0.01)
        assert m["ann_vol"] == pytest.approx(0.1)          # std(ddof=0)*sqrt(1)
        assert m["sharpe"] == pytest.approx(0.0, abs=1e-15)
        assert m["max_drawdown"] == pytest.approx(0.99 / 1.1 - 1.0)
        assert m["cagr"] == pytest.approx(0.99 ** 0.5 - 1.0)
        assert m["top_bar_concentration"] == pytest.approx(1.0)
        assert m["calmar"] == pytest.approx(m["cagr"] / abs(m["max_drawdown"]))

    def test_sharpe_annualisation(self):
        r = np.random.default_rng(0).normal(0.001, 0.01, 500)
        m = jw.compute_metrics(r, 252)
        assert m["sharpe"] == pytest.approx(r.mean() / r.std() * np.sqrt(252))
        neg = r[r < 0]
        assert m["sortino"] == pytest.approx(r.mean() * 252 / (neg.std() * np.sqrt(252)))

    def test_empty_series(self):
        m = jw.compute_metrics([], 252)
        assert m["n_periods"] == 0 and m["final_equity"] == 1.0
        assert m["total_return"] == 0.0 and m["max_drawdown"] == 0.0
        assert math.isnan(m["sharpe"]) and math.isnan(m["cagr"])

    def test_zero_volatility_gives_nan_ratios(self):
        # 0.5 / 0.25 可被二進位精確表示 → std 恰為 0 → 比率不定義(NaN)
        for c in (0.5, 0.25):
            m = jw.compute_metrics([c] * 10, 252)
            assert math.isnan(m["sharpe"])           # vol=0 → 不定義,不回 inf
            assert math.isnan(m["sortino"])          # 無負報酬
            assert math.isnan(m["calmar"])           # 無回撤
            assert m["top_bar_concentration"] == pytest.approx(0.1)

    @pytest.mark.xfail(strict=True, reason="AUDIT M-2:常數報酬的 std 因浮點捨入可為 ~1e-19 而非 0,"
                       "`vol > 0` 判準放行 → 夏普 ~1e16(依常數值的二進位表示而定)")
    def test_audit_m2_constant_returns_sharpe_not_astronomic(self):
        m = jw.compute_metrics([0.001] * 300, 252)
        assert not (np.isfinite(m["sharpe"]) and abs(m["sharpe"]) > 1e6)

    def test_ppy_falsy_defaults_to_252(self):
        r = [0.01, -0.02, 0.015, 0.0]
        assert jw.compute_metrics(r, 0)["sharpe"] == pytest.approx(
            jw.compute_metrics(r, 252)["sharpe"])

    def test_overflow_clamped_to_inf(self):
        # R19 必修4:百分比誤當小數 → cagr 冪運算 Overflow 夾成 inf,不裸崩
        m = jw.compute_metrics([500.0] * 300, 365 * 24 * 60)
        assert m["cagr"] == math.inf

    def test_total_wipeout_cagr_nan(self):
        m = jw.compute_metrics([0.1, -1.0], 252)
        assert m["final_equity"] == 0.0 and math.isnan(m["cagr"])
        assert m["max_drawdown"] == pytest.approx(-1.0)

    def test_nan_filled_zero_is_direct_api_behaviour(self):
        # 現行行為釘死(稽核 L-? 見報告):compute_metrics 自身仍 nan→0;
        # analyze 入口已先剔除非有限值,故不影響主判決。
        a = jw.compute_metrics([0.01, float("nan"), -0.01], 252)
        b = jw.compute_metrics([0.01, 0.0, -0.01], 252)
        assert a["sharpe"] == pytest.approx(b["sharpe"])


class TestSharpePeriodic:
    def test_values(self):
        r = np.array([0.01, 0.03, -0.01])
        assert jw._sharpe_periodic(r) == pytest.approx(r.mean() / r.std(ddof=0))
        assert jw._sharpe_periodic(np.array([])) == 0.0
        assert jw._sharpe_periodic(np.array([0.5, 0.5])) == 0.0


# ---------------------------------------------------------------------------
# 2. DSR 組件
# ---------------------------------------------------------------------------
class TestExpectedMaxSharpe:
    def test_fewer_than_two_trials_is_zero(self):
        assert jw.expected_max_sharpe(1.0, 1) == 0.0
        assert jw.expected_max_sharpe(1.0, 0) == 0.0

    def test_zero_or_negative_variance_is_zero(self):
        assert jw.expected_max_sharpe(0.0, 100) == 0.0
        assert jw.expected_max_sharpe(-1.0, 100) == 0.0

    @pytest.mark.parametrize("var,n", [(0.01, 2), (0.0004, 10), (1.0, 1000)])
    def test_matches_bailey_lopez_de_prado_formula(self, var, n):
        from scipy import stats
        g = 0.5772156649015329
        ref = math.sqrt(var) * ((1 - g) * stats.norm.ppf(1 - 1 / n)
                                + g * stats.norm.ppf(1 - 1 / (n * math.e)))
        assert jw.expected_max_sharpe(var, n) == pytest.approx(ref, rel=1e-9)

    def test_monotone_in_trials_and_scales_with_sd(self):
        vals = [jw.expected_max_sharpe(0.01, n) for n in (2, 5, 20, 100, 1000)]
        assert all(b > a for a, b in zip(vals, vals[1:]))
        assert jw.expected_max_sharpe(0.04, 50) == pytest.approx(
            2 * jw.expected_max_sharpe(0.01, 50))


class TestProbabilisticSharpe:
    def test_equal_to_benchmark_is_half(self):
        assert jw.probabilistic_sharpe(0.1, 0.1, 250, 0.0, 3.0) == pytest.approx(0.5)

    def test_normal_case_closed_form(self):
        from scipy import stats
        sr, n = 0.1, 252
        z = sr * math.sqrt(n - 1) / math.sqrt(1 + 0.5 * sr * sr)
        assert jw.probabilistic_sharpe(sr, 0.0, n, 0.0, 3.0) == pytest.approx(
            stats.norm.cdf(z), abs=1e-12)

    def test_negative_variance_term_is_nan_not_crash(self):
        # 1 - skew*sr + (k-1)/4*sr² < 0 → sqrt(負) → NaN,由 isfinite 守住
        with np.errstate(invalid="ignore"):
            assert math.isnan(jw.probabilistic_sharpe(1.0, 0.0, 100, 10.0, 3.0))

    def test_tiny_n_does_not_divide_by_zero(self):
        v = jw.probabilistic_sharpe(0.2, 0.0, 1, 0.0, 3.0)  # max(n-1,1)
        assert 0.5 < v < 1.0


class TestDeflatedSharpeBranches:
    r = np.random.default_rng(11).normal(0.001, 0.01, 300)

    def test_single_trial_no_deflation(self):
        d = jw.deflated_sharpe(self.r, 1, np.array([0.1]))
        assert d["sr0_daily"] == 0.0 and d["sr_variance_proxy"] is False
        assert d["p_value"] == pytest.approx(1 - d["dsr"])
        assert d["sr_daily"] == pytest.approx(self.r.mean() / self.r.std(ddof=1))

    def test_proxy_branch(self):
        d = jw.deflated_sharpe(self.r, 10, np.array([0.1]))
        sr = self.r.mean() / self.r.std(ddof=1)
        var = (1 + 0.5 * sr * sr) / self.r.size
        assert d["sr_variance_proxy"] is True
        assert d["sr0_daily"] == pytest.approx(jw.expected_max_sharpe(var, 10))

    def test_true_pool_branch_ignores_nonfinite(self):
        pool = np.array([0.02, 0.05, np.nan, -0.01, np.inf])
        d = jw.deflated_sharpe(self.r, 4, pool)
        var = np.var([0.02, 0.05, -0.01], ddof=1)
        assert d["sr_variance_proxy"] is False
        assert d["sr0_daily"] == pytest.approx(jw.expected_max_sharpe(var, 4))

    def test_nonfinite_returns_are_dropped(self):
        r2 = np.concatenate([self.r, [np.nan, np.inf]])
        assert jw.deflated_sharpe(r2, 1, [0.0])["dsr"] == pytest.approx(
            jw.deflated_sharpe(self.r, 1, [0.0])["dsr"])


class TestHardenMatrixNTrials:
    def _pool(self, mat):
        return np.array([jw._sharpe_periodic(mat[:, k]) for k in range(mat.shape[1])])

    def test_true_edge_not_hardened(self):
        rng = np.random.default_rng(5)
        mat = rng.normal(0, 0.01, (750, 5))
        mat[:, 0] += 0.003
        pool = self._pool(mat)
        eff, diag = jw.harden_matrix_n_trials(mat[:, 0], pool, 5)
        assert eff == 5 and diag["hardened"] is False and diag["capped_escape"] is False
        assert diag["dsr_at_base"] >= jw.REAL_CONF_BAR

    def test_noise_winner_hardened_until_below_floor_or_capped(self):
        rng = np.random.default_rng(7)
        mat = rng.normal(0, 0.01, (250, 20))
        pool = self._pool(mat)
        best = int(np.argmax(pool))
        eff, diag = jw.harden_matrix_n_trials(mat[:, best], pool, 20)
        if diag["dsr_at_base"] < jw.REAL_CONF_BAR:
            assert diag["hardened"] is True
            assert eff in [20 * m for m in jw.HARDEN_MULTS]
            assert diag["dsr_final"] < jw.NOISE_FLOOR or diag["capped_escape"]

    def test_base_floor_is_two(self):
        r = np.random.default_rng(1).normal(0, 0.01, 200)
        eff, diag = jw.harden_matrix_n_trials(r, np.array([0.01, -0.01]), 0)
        assert diag["base_n_trials"] == 2 and eff >= 2

    def test_deterministic(self):
        rng = np.random.default_rng(9)
        mat = rng.normal(0, 0.01, (200, 8))
        pool = self._pool(mat)
        a = jw.harden_matrix_n_trials(mat[:, 0], pool, 8)
        b = jw.harden_matrix_n_trials(mat[:, 0], pool, 8)
        assert a == b


# ---------------------------------------------------------------------------
# 3. FWER 底座 / PBO
# ---------------------------------------------------------------------------
class TestCircularBlockBootstrap:
    diffs = np.random.default_rng(2).normal(0.0, 1.0, (60, 3))

    def test_shape_and_determinism(self):
        a = jw._circular_block_bootstrap_means(self.diffs, 50, 5, np.random.default_rng(1))
        b = jw._circular_block_bootstrap_means(self.diffs, 50, 5, np.random.default_rng(1))
        assert a.shape == (50, 3) and np.array_equal(a, b)

    def test_full_length_block_is_exact_rotation(self):
        # L=T → 每次抽樣 = 整條序列的循環旋轉 → 平均值與原樣本均值逐位相同
        m = jw._circular_block_bootstrap_means(self.diffs, 30, 60, np.random.default_rng(0))
        assert np.allclose(m, self.diffs.mean(axis=0))

    def test_constant_series(self):
        d = np.full((40, 2), 0.25)
        m = jw._circular_block_bootstrap_means(d, 20, 7, np.random.default_rng(0))
        assert np.allclose(m, 0.25)

    def test_matches_naive_reference(self):
        T, K, L, B = 23, 2, 4, 15
        d = np.random.default_rng(4).normal(size=(T, K))
        got = jw._circular_block_bootstrap_means(d, B, L, np.random.default_rng(99))
        rng = np.random.default_rng(99)
        m = int(np.ceil(T / L))
        rem = T - (m - 1) * L
        starts = rng.integers(0, T, size=(B, m))
        ref = np.empty((B, K))
        for b in range(B):
            idx = []
            for j in range(m):
                ln = L if j < m - 1 else rem
                idx += [(starts[b, j] + t) % T for t in range(ln)]
            assert len(idx) == T
            ref[b] = d[idx].mean(axis=0)
        assert np.allclose(got, ref)


class TestSpaAndRomanoWolf:
    rng = np.random.default_rng(13)
    T = 300
    diffs = rng.normal(0, 0.01, (T, 6))
    diffs[:, 0] += 0.004  # 植入一個真贏家

    def _parts(self):
        dbar = self.diffs.mean(axis=0)
        boot = jw._circular_block_bootstrap_means(self.diffs, 500, 7, np.random.default_rng(0))
        se = self.diffs.std(axis=0, ddof=1) / np.sqrt(self.T)
        return dbar, boot, se

    def test_spa_pvalue_ordering_and_range(self):
        dbar, boot, se = self._parts()
        out = jw._spa_pvalues(dbar, boot, se, self.T)
        b = boot.shape[0]
        for k in ("p_lower", "p_value", "p_upper"):
            assert 1 / (b + 1) <= out[k] <= 1.0
        # Hansen:consistent p 夾在 lower 與 upper 之間
        assert out["p_lower"] <= out["p_value"] <= out["p_upper"]
        assert out["t_max"] == pytest.approx(max(0.0, (dbar / se).max()))

    def test_spa_all_losers_pvalue_is_one(self):
        dbar, boot, se = self._parts()
        neg = -np.abs(dbar) - 1.0  # 全部顯著輸 → t_obs=0 → 每個 bootstrap tb≥0 → p=1
        out = jw._spa_pvalues(neg, boot - dbar + neg, se, self.T)
        assert out["t_max"] == 0.0 and out["p_upper"] == 1.0

    def test_romano_wolf_monotone_with_t_stat(self):
        dbar, boot, se = self._parts()
        p = jw._romano_wolf_adj_p(dbar, boot, se)
        b = boot.shape[0]
        assert p.shape == (6,) and np.all(p >= 1 / (b + 1)) and np.all(p <= 1.0)
        order = np.argsort(-(dbar / se))
        assert np.all(np.diff(p[order]) >= 0)  # step-down:t 越大 adj-p 越小(或相等)
        assert int(np.argmin(p)) == 0

    def test_romano_wolf_single_candidate_equals_plain_bootstrap_p(self):
        dbar, boot, se = self._parts()
        p = jw._romano_wolf_adj_p(dbar[:1], boot[:, :1], se[:1])
        tb = (boot[:, 0] - dbar[0]) / se[0]
        ref = (1 + np.sum(tb >= dbar[0] / se[0])) / (boot.shape[0] + 1)
        assert p[0] == pytest.approx(ref)


class TestBlockMomentsAndPbo:
    def test_block_moments_partition_totals(self):
        v = np.random.default_rng(0).normal(size=(37, 3))
        s, sq, c = jw._block_moments(v, 6)
        assert s.shape == (6, 3)
        assert np.allclose(s.sum(axis=0), v.sum(axis=0))
        assert np.allclose(sq.sum(axis=0), (v ** 2).sum(axis=0))
        assert np.all(c.sum(axis=0) == 37)

    def test_sharpe_from_moments_matches_direct(self):
        x = np.random.default_rng(1).normal(0.1, 1.0, 50)
        got = jw._sharpe_from_moments(x.sum(), (x ** 2).sum(), x.size)
        assert got == pytest.approx(x.mean() / x.std(ddof=0))

    def test_pbo_needs_two_strategies(self):
        out = jw.pbo_cscv(np.zeros((100, 1)))
        assert math.isnan(out["pbo"]) and out["n_combinations"] == 0

    def test_pbo_combination_count_and_odd_blocks(self):
        v = np.random.default_rng(2).normal(0, 0.01, (480, 4))
        out = jw.pbo_cscv(v, 12)
        assert out["n_blocks"] == 12 and out["n_combinations"] == math.comb(12, 6)
        out11 = jw.pbo_cscv(v, 11)  # 奇數 → 減 1
        assert out11["n_blocks"] == 10 and out11["n_combinations"] == math.comb(10, 5)

    def test_pbo_shrinks_blocks_for_short_samples(self):
        v = np.random.default_rng(3).normal(0, 0.01, (20, 3))
        out = jw.pbo_cscv(v, 12)       # 20 < 12*4 → 逐級減到 4(20 ≥ 4*4)
        assert out["n_blocks"] == 4

    def test_persistent_winner_has_zero_pbo(self):
        rng = np.random.default_rng(4)
        v = rng.normal(0, 0.01, (480, 5))
        v[:, 2] += 0.02  # 每個子樣本都碾壓
        assert jw.pbo_cscv(v)["pbo"] == 0.0

    def test_pbo_matches_naive_cscv(self):
        rng = np.random.default_rng(6)
        v = rng.normal(0, 0.01, (96, 4))
        nb = 6
        out = jw.pbo_cscv(v, nb)
        bounds = np.linspace(0, 96, nb + 1, dtype=int)
        blocks = [v[bounds[i]:bounds[i + 1]] for i in range(nb)]
        logits = []
        for is_set in combinations(range(nb), nb // 2):
            oos = [i for i in range(nb) if i not in is_set]
            IS = np.vstack([blocks[i] for i in is_set])
            OOS = np.vstack([blocks[i] for i in oos])
            sr_is = IS.mean(0) / IS.std(0)
            sr_oos = OOS.mean(0) / OOS.std(0)
            b = int(np.argmax(sr_is))
            w = np.clip((sr_oos <= sr_oos[b]).sum() / (4 + 1), 1e-9, 1 - 1e-9)
            logits.append(np.log(w / (1 - w)))
        assert out["pbo"] == pytest.approx(np.mean(np.array(logits) < 0))


# ---------------------------------------------------------------------------
# 4. permutation null / 5. cost stress(直呼)
# ---------------------------------------------------------------------------
class TestPermutationNull:
    def test_too_short(self):
        out = jw.permutation_null(np.ones(7) * 0.01, 252)
        assert out["n_perm"] == 0 and out["passes"] is False and math.isnan(out["p_value"])

    def test_strong_drift_passes_with_min_pvalue(self):
        r = np.random.default_rng(0).normal(0.01, 0.005, 300)
        out = jw.permutation_null(r, 252, n_perm=300)
        assert out["passes"] is True and out["p_value"] == pytest.approx(1 / 301)
        assert out["real_sharpe"] == pytest.approx(r.mean() / r.std() * np.sqrt(252))

    def test_zero_drift_does_not_pass(self):
        rng = np.random.default_rng(1)
        r = rng.normal(0, 0.01, 400)
        r -= r.mean()
        out = jw.permutation_null(r, 252, n_perm=500)
        assert out["passes"] is False and out["p_value"] > 0.3

    def test_deterministic_and_seed_sensitive(self):
        r = np.random.default_rng(2).normal(0.0005, 0.01, 200)
        a = jw.permutation_null(r, 252, n_perm=200)
        b = jw.permutation_null(r, 252, n_perm=200)
        c = jw.permutation_null(r, 252, n_perm=200, seed=1)
        assert a == b and a["null_p95_sharpe"] != c["null_p95_sharpe"]

    def test_explicit_block_len_clipped(self):
        r = np.random.default_rng(3).normal(0.0005, 0.01, 50)
        out = jw.permutation_null(r, 252, n_perm=50, block_len=10_000)  # L 夾到 T
        assert np.isfinite(out["null_p95_sharpe"])


class TestCostStressDirect:
    r = np.random.default_rng(8).normal(0.001, 0.01, 250)

    def test_zero_turnover_no_change(self):
        out = jw.cost_stress(self.r, np.zeros(250), 30.0, 252)
        ref = jw._sharpe_periodic(self.r) * np.sqrt(252)
        assert out["x1_sharpe"] == pytest.approx(ref)
        assert out["x1_sharpe"] == out["x3_sharpe"] == out["x6_sharpe"]

    def test_monotone_decreasing_and_exact(self):
        tn = np.full(250, 0.5)
        out = jw.cost_stress(self.r, tn, 20.0, 252)
        assert out["x1_sharpe"] > out["x3_sharpe"] > out["x6_sharpe"]
        net3 = self.r - 0.5 * 20.0 / 1e4 * 3
        assert out["x3_sharpe"] == pytest.approx(jw._sharpe_periodic(net3) * np.sqrt(252))

    def test_invalid_turnover_rows_dropped_not_zero_filled(self):
        tn = np.full(250, 0.5)
        tn[[3, 10]] = [np.nan, -1.0]
        keep = np.ones(250, bool)
        keep[[3, 10]] = False
        out = jw.cost_stress(self.r, tn, 20.0, 252)
        ref = jw.cost_stress(self.r[keep], tn[keep], 20.0, 252)
        assert out == ref

    def test_length_mismatch_truncates(self):
        out = jw.cost_stress(self.r, np.full(100, 0.3), 10.0, 252)
        ref = jw.cost_stress(self.r[:100], np.full(100, 0.3), 10.0, 252)
        assert out == ref


# ---------------------------------------------------------------------------
# 6. _verdict 直呼(判分規則)
# ---------------------------------------------------------------------------
def _sum_breakdown(v):
    return float(np.clip(sum(x["delta"] for x in v["score_breakdown"]), 0, 100))


class TestVerdict:
    def test_no_signals_is_inconclusive_at_50(self):
        v = jw._verdict({})
        assert v["overall"] == "inconclusive" and v["score_0to100"] == 50.0
        assert v["reasons_coded"][0]["code"] == "dsr_not_computable"
        assert v["reasons_coded"][-1]["code"] == "closing_inconclusive"

    def test_strong_signals_likely_real(self):
        v = jw._verdict({"dsr_prob": 0.99, "perm_p": 0.001, "perm_pass": True,
                         "concentration": 0.05, "n_periods": 500, "n_trials": 1})
        assert v["overall"] == "likely-real"
        assert v["score_0to100"] == pytest.approx(50 + 18 + 14 + 4)
        assert v["red_flags"] == []

    def test_low_dsr_is_overfit(self):
        v = jw._verdict({"dsr_prob": 0.3, "n_periods": 500})
        assert v["overall"] == "likely-overfit"
        assert any(f["code"] == "dsr_below_noise_floor" for f in v["red_flags_coded"])

    def test_capped_escape_blocks_likely_real(self):
        v = jw._verdict({"dsr_prob": 0.99, "perm_p": 0.001, "perm_pass": True,
                         "concentration": 0.05, "pbo": 0.1, "cost3_sharpe": 1.0,
                         "beats_bench": True, "excess_cagr": 0.05,
                         "n_periods": 500, "capped_escape": True, "n_trials": 640})
        assert v["overall"] != "likely-real"

    def test_each_soft_fail_vetoes_likely_real(self):
        base = {"dsr_prob": 0.99, "perm_p": 0.001, "perm_pass": True,
                "concentration": 0.05, "pbo": 0.1, "n_periods": 500}
        assert jw._verdict(base)["overall"] == "likely-real"
        for extra in ({"cost3_sharpe": -0.1}, {"beats_bench": False},
                      {"concentration": 0.5}):
            assert jw._verdict({**base, **extra})["overall"] != "likely-real", extra

    def test_hard_fails_force_overfit(self):
        base = {"dsr_prob": 0.99, "perm_p": 0.001, "perm_pass": True, "n_periods": 500}
        assert jw._verdict({**base, "pbo": 0.8})["overall"] == "likely-overfit"
        assert jw._verdict({**base, "perm_p": 0.7, "perm_pass": False})["overall"] == "likely-overfit"

    def test_many_trials_penalty_formula(self):
        v = jw._verdict({"n_trials": 1000})
        d = [x["delta"] for x in v["score_breakdown"] if x["code"] == "many_trials_penalty"]
        assert d == [pytest.approx(-min(15.0, 3 * math.log10(1000)))]

    def test_trials_uplift_wording_code(self):
        v = jw._verdict({"n_trials": 40, "n_trials_declared": 3})
        rc = [r for r in v["reasons_coded"] if r["code"] == "many_trials_penalty"][0]
        assert rc["params"] == {"n_trials": 40, "declared": 3}

    def test_missing_sensitivity_downgrade(self):
        base = {"dsr_prob": 0.99, "perm_p": 0.001, "perm_pass": True,
                "concentration": 0.05, "n_periods": 500}
        ms = {"unstable": True, "bar_crossed": 0.95, "noise_floor": 0.60, "n_missing": 5,
              "fill_value": -0.05, "sharpe_observed": 2.0, "sharpe_sensitivity": 1.0,
              "dsr_observed": 0.99, "dsr_sensitivity": 0.9}
        v = jw._verdict({**base, "missing_sens": ms})
        assert v["overall"] == "inconclusive"
        assert any(r["code"] == "missing_sensitivity_downgrade" for r in v["reasons_coded"])
        # 穩定 → 不降級
        assert jw._verdict({**base, "missing_sens": {**ms, "unstable": False}})["overall"] == "likely-real"

    def test_score_clipped_and_breakdown_invariant(self):
        worst = {"dsr_prob": 0.1, "pbo": 0.9, "perm_p": 0.9, "perm_pass": False,
                 "cost3_sharpe": -1.0, "beats_bench": False, "concentration": 0.9,
                 "n_trials": 10 ** 6, "n_periods": 10, "capped_escape": True}
        v = jw._verdict(worst)
        assert v["score_0to100"] == 0.0 == _sum_breakdown(v)
        assert len(v["reasons"]) == len(v["reasons_coded"])
        assert len(v["red_flags"]) == len(v["red_flags_coded"])

    def test_short_span_reason_has_no_score_delta(self):
        a = jw._verdict({"calendar_span_years": 0.1})
        b = jw._verdict({})
        assert a["score_0to100"] == b["score_0to100"]
        assert any(r["code"] == "short_calendar_span" for r in a["reasons_coded"])

    def test_nan_signals_ignored(self):
        v = jw._verdict({"dsr_prob": float("nan"), "pbo": float("nan"),
                         "perm_p": float("nan"), "cost3_sharpe": float("nan"),
                         "concentration": float("nan")})
        assert v["score_0to100"] == 50.0 and v["overall"] == "inconclusive"


# ---------------------------------------------------------------------------
# 7. 日期 / 頻率 / 小工具
# ---------------------------------------------------------------------------
class TestDatesAndPpy:
    def test_parse_dates_mixed_tz_falls_back_to_utc(self):
        idx = jw._parse_dates_series(["2024-01-01T00:00:00+00:00",
                                      "2024-01-01T08:00:00+08:00"])
        assert str(idx.dt.tz) == "UTC" and idx.iloc[0] == idx.iloc[1]

    def test_infer_ppy_explicit_positive_wins(self):
        assert jw._infer_ppy(["2024-01-01", "2024-02-01", "2024-03-01"], 52) == 52.0
        assert jw._infer_ppy(None, "12") == 12.0

    @pytest.mark.parametrize("bad", [0, -3, "abc", float("nan"), float("inf")])
    def test_infer_ppy_invalid_warns_and_falls_back(self, bad):
        got = []
        out = jw._infer_ppy(None, bad, warn=lambda zh, code, **p: got.append(code))
        assert out == 252.0 and got == ["ppy_invalid"]

    def test_infer_ppy_weekly_and_monthly(self):
        w = [str(d.date()) for d in pd.date_range("2020-01-06", periods=104, freq="7D")]
        assert jw._infer_ppy(w, None) == 52.0
        m = [f"{2015 + i // 12}-{i % 12 + 1:02d}-01" for i in range(96)]
        assert jw._infer_ppy(m, None) == 12.0

    def test_infer_ppy_too_few_dates(self):
        assert jw._infer_ppy(["2024-01-01", "2024-01-02"], None) == 252.0
        assert jw._infer_ppy([], None) == 252.0

    def test_infer_ppy_nonincreasing_span_falls_back_silently(self):
        # 首末相同 → total_days=0 → 252(現行行為;日期守衛已先處理重複/亂序)
        assert jw._infer_ppy(["2024-01-05", "2024-01-01", "2024-01-05"], None) == 252.0


class TestSmallHelpers:
    def test_missing_reject_shape(self):
        out = jw._missing_reject("中文", "some_code", n=3)
        assert out["ok"] is False and out["warnings"] == ["中文"]
        assert out["warnings_coded"] == [{"code": "some_code", "params": {"n": 3}}]
        assert out["verdict"]["overall"] == "inconclusive"
        assert out["verdict"]["score_0to100"] == 0

    def test_finite_list(self):
        assert jw._finite_list([1, "2.5", None, "x", float("nan"), float("inf"), -3]) == [1.0, 2.5, -3.0]
        assert jw._finite_list(None) == []

    def test_clean_returns_keeps_positions_as_nan(self):
        out = jw._clean_returns([0.1, None, "x", float("inf"), "0.2"])
        assert out[0] == 0.1 and out[4] == 0.2
        assert all(math.isnan(v) for v in out[1:4]) and len(out) == 5


# ---------------------------------------------------------------------------
# 稽核發現的已知 bug(strict xfail:修好後轉紅,提醒移除標記)
# ---------------------------------------------------------------------------
@pytest.mark.xfail(strict=True, reason="AUDIT H-1:震盪/虧損淨值(mono≤0.55、max≤3)被判成 returns")
def test_audit_h1_choppy_nav_detected_as_nav():
    rng = np.random.default_rng(1)
    nav = list(np.cumprod(1 + rng.normal(-0.0005, 0.01, 500)))
    assert jw.detect_series_kind(nav) == "nav"


@pytest.mark.xfail(strict=True, reason="AUDIT H-1:誤判後淨值 ≈1.0 被當成每期 +100% 報酬,虧損策略得 likely-real")
def test_audit_h1_losing_nav_never_likely_real_via_frontend_path():
    rng = np.random.default_rng(1)
    nav = list(np.cumprod(1 + rng.normal(-0.0005, 0.01, 500)))
    dates = [str(d.date()) for d in pd.bdate_range("2022-01-03", periods=500)]
    p = jw.detect_and_convert({"mode": "returns", "dates": dates,
                               "raw": {"values": nav, "dates": dates, "js_kind": "returns"}})
    assert jw.analyze(p)["verdict"]["overall"] != "likely-real"


@pytest.mark.xfail(strict=True, reason="AUDIT M-2:常數報酬的判決隨浮點捨入翻轉"
                   "(0.01×300 → likely-overfit;0.001×300 → likely-real 86 分)")
def test_audit_m2_constant_returns_verdict_stable():
    a = jw.analyze({"mode": "returns", "returns": [0.01] * 300})["verdict"]["overall"]
    b = jw.analyze({"mode": "returns", "returns": [0.001] * 300})["verdict"]["overall"]
    assert a == b


@pytest.mark.xfail(strict=True, reason="AUDIT M-1:normalize_date_str 丟掉時區位移")
def test_audit_m1_normalize_keeps_timezone():
    assert "+08:00" in jw.normalize_date_str("2024-01-02T09:30:00+08:00")


@pytest.mark.xfail(strict=True, reason="AUDIT M-1:normalize_date_str 丟掉小數秒 → 次秒級 bar 撞成重複被拒審")
def test_audit_m1_subsecond_bars_not_rejected_via_frontend_path():
    ts = pd.date_range("2024-01-02 09:00:00", periods=200, freq="500ms")
    ds = [t.strftime("%Y-%m-%d %H:%M:%S.%f") for t in ts]
    r = list(np.random.default_rng(0).normal(0.0005, 0.01, 200))
    p = jw.detect_and_convert({"mode": "returns", "dates": ds,
                               "raw": {"values": r, "dates": ds, "js_kind": "returns"}})
    assert jw.analyze(p)["ok"] is True


def test_audit_m1_direct_path_handles_subsecond_correctly():
    # 對照組:不經 detect_and_convert 的直呼路徑行為正確(證明問題只在正規化層)
    ts = pd.date_range("2024-01-02 09:00:00", periods=200, freq="500ms")
    ds = [t.strftime("%Y-%m-%d %H:%M:%S.%f") for t in ts]
    r = list(np.random.default_rng(0).normal(0.0005, 0.01, 200))
    assert jw.analyze({"mode": "returns", "dates": ds, "returns": r})["ok"] is True
