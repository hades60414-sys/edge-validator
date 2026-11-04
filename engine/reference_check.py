"""reference_check — 用 scipy / statsmodels / arch 當【參考實作】交叉比對引擎每個統計檢定。

引擎本身(judge_web / statshim)只依賴 numpy/pandas(要在 Pyodide 跑);本檔只在
CPython 測試/稽核時 import 參考套件,絕不被 analyze() 呼到。

兩類比對:
  exact       —— 同一數學定義、同一輸入(必要時同一亂數流),應逐位或到浮點誤差相同。
                 參考 = scipy.stats / statsmodels / 依論文逐式寫的 naive loop。
  statistical —— bootstrap 檢定與外部套件(arch)用不同亂數流,只能比 p 值的分布一致性;
                 容差取 Monte Carlo 誤差 + 已文件化的設計差異(studentizer、偏差校正)。

每個 check_* 回傳 dict:
  name / kind / reference / n_cases / max_abs_err / max_rel_err / tol / ok / note
`python -m engine.reference_check` 印出 markdown 表(docs/reference-check.md 的數字來源);
engine/test_reference_check.py 對每個 check 斷言 ok。全部 seed 固定,可重跑、結果確定。
"""
from __future__ import annotations

import math
import sys
import warnings
from itertools import combinations

import numpy as np
import pandas as pd

from . import judge_web as jw
from . import statshim as ss

SEED = 20261104


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _err(a, b):
    """逐元素誤差;NaN↔NaN、同號 inf 視為相等,一邊 NaN 一邊有限 → inf 誤差。"""
    a = np.atleast_1d(np.asarray(a, dtype=float))
    b = np.atleast_1d(np.asarray(b, dtype=float))
    same = (np.isnan(a) & np.isnan(b)) | (np.isinf(a) & np.isinf(b) & (np.sign(a) == np.sign(b)))
    with np.errstate(invalid="ignore", over="ignore"):
        d = np.abs(a - b)
    d = np.where(same, 0.0, d)
    d = np.where(np.isnan(d), np.inf, d)
    with np.errstate(divide="ignore", invalid="ignore"):
        rel = np.where(same, 0.0, d / np.maximum(np.abs(b), np.finfo(float).tiny))
    rel = np.where(np.isnan(rel), np.inf, rel)
    return d, rel


def _result(name, kind, reference, abs_err, rel_err, tol, n_cases, note="", tol_on="abs"):
    abs_err = np.atleast_1d(np.asarray(abs_err, dtype=float))
    rel_err = np.atleast_1d(np.asarray(rel_err, dtype=float))
    mae = float(abs_err.max()) if abs_err.size else 0.0
    mre = float(rel_err.max()) if rel_err.size else 0.0
    metric = mae if tol_on == "abs" else mre
    return {"name": name, "kind": kind, "reference": reference, "n_cases": int(n_cases),
            "max_abs_err": mae, "max_rel_err": mre, "tol": float(tol), "tol_on": tol_on,
            "ok": bool(metric <= tol), "note": note}


# ===========================================================================
# 1. statshim vs scipy.stats
# ===========================================================================
def check_norm_cdf():
    from scipy import stats
    rng = np.random.default_rng(SEED)
    x = np.concatenate([
        rng.uniform(-8, 8, 2000),
        -np.logspace(0, np.log10(37.5), 200),           # 深左尾(相對誤差才是重點)
        [-38.5, -37.5, -20.0, -10.0, -8.5, -1e-300, 0.0, 1e-300, 8.0, 10.0, 40.0,
         np.inf, -np.inf, np.nan],
    ])
    got = ss.norm_cdf(x)
    ref = stats.norm.cdf(x)
    d, rel = _err(got, ref)
    # 左尾 Φ(x)≪1 時只有相對誤差有意義(p 值、尾機率都靠它);右尾看絕對誤差。
    tail = (x < 0) & np.isfinite(x) & (ref > 1e-300)
    rel_tail = rel[tail]
    return _result("statshim.norm_cdf", "exact", "scipy.stats.norm.cdf",
                   d, rel_tail, 1e-12, x.size, tol_on="rel",
                   note="相對誤差只在左尾 (x<0, Φ>1e-300) 計;絕對誤差全域")


def check_norm_ppf():
    from scipy import stats
    rng = np.random.default_rng(SEED + 1)
    lo = 10.0 ** rng.uniform(-300, np.log10(0.5), 2000)
    p = np.concatenate([lo, 1.0 - lo[lo > 1e-15], rng.uniform(0, 1, 2000),
                        [0.0, 1.0, 5e-324, 1e-320, 1e-310, 1e-308, 1e-17, 0.02425,
                         0.5, 0.97575, 1 - 1e-16, np.nan, -0.1, 1.1]])
    ref = stats.norm.ppf(p)
    errors = 0
    got = np.empty_like(p)
    for i, pi in enumerate(p):
        try:
            got[i] = ss.norm_ppf(pi)
        except Exception:  # noqa: BLE001 — 例外本身就是不一致
            got[i] = np.nan
            errors += 1
    d, rel = _err(got, ref)
    # 次正規數 p(<2.2e-308)本身只剩 ~1e-4 相對解析度,Φ⁻¹ 到 1e-9 在雙精度下不可達:
    # 另外只要求「不丟例外、有限、|誤差|<1e-6」,不計入主容差。
    sub = np.isfinite(p) & (p > 0) & (p < np.finfo(float).tiny)
    sub_ok = bool(np.all(d[sub] < 1e-6))
    if errors or not sub_ok:
        d = np.where(sub, np.inf, d)
    note = (f"例外 {errors} 次" if errors else "無例外") + \
        f";次正規數 p 最大誤差 {float(d[sub].max()) if sub.any() else 0:.2g}(容差 1e-6,另計)"
    return _result("statshim.norm_ppf", "exact", "scipy.stats.norm.ppf",
                   np.where(sub, 0.0, d), np.where(sub, 0.0, rel), 1e-9, p.size,
                   note=note + ";含 p∈{0,1,NaN,越界}")


def _moment_samples(rng):
    yield rng.standard_normal(500)
    yield rng.standard_t(3, 800)
    yield rng.lognormal(0, 1, 300)
    yield rng.standard_normal(3)
    yield rng.standard_normal(4)
    yield 1e8 + rng.standard_normal(200)          # 大位移(兩趟中心化的抵銷誤差)
    yield 1e-10 * rng.standard_normal(200)        # 極小尺度
    yield np.r_[np.zeros(99), 1.0]                # 單點跳躍(極端偏/峰)
    for _ in range(30):
        n = int(rng.integers(5, 2000))
        yield rng.standard_t(rng.uniform(2.5, 30), n) * rng.uniform(1e-4, 1e2)


def check_skew_kurtosis():
    from scipy import stats
    rng = np.random.default_rng(SEED + 2)
    abs_e, rel_e, n = [], [], 0
    for x in _moment_samples(rng):
        for got, ref in ((ss.skew(x), stats.skew(x)),
                         (ss.kurtosis(x, fisher=False), stats.kurtosis(x, fisher=False)),
                         (ss.kurtosis(x, fisher=True), stats.kurtosis(x, fisher=True))):
            d, r = _err(got, ref)
            abs_e.append(d[0])
            rel_e.append(r[0])
            n += 1
    return _result("statshim.skew / kurtosis", "exact",
                   "scipy.stats.skew / kurtosis (bias=True)",
                   abs_e, rel_e, 1e-9, n, tol_on="rel",
                   note="常數序列刻意分歧(shim 回 0/3,scipy 回 NaN),另案斷言,不計入")


# ===========================================================================
# 2. DSR / PSR / E[max SR] vs scipy(Bailey & López de Prado 2014 逐式)
# ===========================================================================
def ref_expected_max_sharpe(sr_var, n_trials):
    """scipy 參考:用 isf(上尾精確)而非 ppf(1-1/N)(1-1/N 在大 N 時有捨入)。"""
    from scipy import stats
    if n_trials < 2:
        return 0.0
    sd = math.sqrt(max(sr_var, 0.0))
    return sd * ((1 - jw.EULER_GAMMA) * stats.norm.isf(1.0 / n_trials)
                 + jw.EULER_GAMMA * stats.norm.isf(1.0 / (n_trials * math.e)))


def ref_psr(sr_hat, sr_bench, n_obs, skew, kurt):
    from scipy import stats
    denom = math.sqrt(1 - skew * sr_hat + (kurt - 1) / 4.0 * sr_hat ** 2)
    z = (sr_hat - sr_bench) * math.sqrt(max(n_obs - 1, 1)) / denom
    return float(stats.norm.cdf(z)), float(stats.norm.sf(z))


def ref_deflated_sharpe(returns, n_trials, pool):
    from scipy import stats
    r = pd.Series(returns, dtype=float).dropna()
    r = r[np.isfinite(r)]
    std = r.std(ddof=1) if len(r) > 1 else 0.0
    # 退化波動政策(與引擎同一門檻,是政策不是數值):std ≤ 1e-9·|mean| → 無可量測波動,SR=0
    degenerate = (not std > 0) or std <= 1e-9 * abs(r.mean())
    sr = 0.0 if degenerate else float(r.mean() / std)
    pool = np.asarray(pool, float)
    pool = pool[np.isfinite(pool)]
    if pool.size > 1:
        sr_var = float(np.var(pool, ddof=1))
    elif n_trials > 1:
        sr_var = (1 + 0.5 * sr * sr) / max(len(r), 2)
    else:
        sr_var = 0.0
    sr0 = ref_expected_max_sharpe(sr_var, n_trials)
    sk = float(stats.skew(r)) if len(r) > 2 and not degenerate else 0.0
    ku = float(stats.kurtosis(r, fisher=False)) if len(r) > 2 and not degenerate else 3.0
    dsr, pval = ref_psr(sr, sr0, len(r), sk, ku)
    return {"sr": sr, "sr0": sr0, "dsr": dsr, "p_value": pval}


def check_expected_max_sharpe():
    abs_e, rel_e, n = [], [], 0
    for nt in (2, 3, 10, 100, 10 ** 3, 10 ** 6, 10 ** 9, 10 ** 12, 10 ** 15):
        for var in (1e-6, 1e-3, 0.04, 1.0):
            d, r = _err(jw.expected_max_sharpe(var, nt), ref_expected_max_sharpe(var, nt))
            abs_e.append(d[0])
            rel_e.append(r[0])
            n += 1
    return _result("judge_web.expected_max_sharpe", "exact",
                   "scipy.stats.norm.isf(論文公式)", abs_e, rel_e, 1e-10, n, tol_on="rel",
                   note="N 到 1e15;N<2 → 0")


def check_expected_max_vs_integral():
    """資訊性:論文的閉式近似 vs E[max of N iid N(0,1)] 的精確數值積分(scipy.integrate)。
    這是【公式本身】的近似誤差,不是實作 bug;斷言只要求近似誤差 <5%(N≥10)。"""
    from scipy import integrate, stats

    def exact(n):
        f = lambda x: x * n * stats.norm.pdf(x) * stats.norm.cdf(x) ** (n - 1)  # noqa: E731
        return integrate.quad(f, -12, 12, limit=400)[0]

    rel_e, abs_e, n = [], [], 0
    for nt in (10, 30, 100, 1000, 10000):
        e = exact(nt)
        a = jw.expected_max_sharpe(1.0, nt)
        abs_e.append(abs(a - e))
        rel_e.append(abs(a - e) / e)
        n += 1
    return _result("E[max SR] 閉式近似 vs 精確積分", "informational",
                   "scipy.integrate.quad", abs_e, rel_e, 0.05, n, tol_on="rel",
                   note="論文近似本身的誤差(非實作 bug)")


def _dsr_cases(rng):
    for _ in range(60):
        n = int(rng.integers(30, 1500))
        r = 0.0005 * rng.standard_normal() + 0.01 * rng.standard_t(rng.uniform(3, 30), n)
        pool = 0.05 * rng.standard_normal(int(rng.integers(2, 60)))
        yield r, int(rng.integers(2, 5000)), pool
        yield r, int(rng.integers(2, 5000)), pool[:1]   # proxy 路徑
        yield r, 1, pool[:1]                             # 不通縮
    # 邊界
    yield np.array([0.01, -0.02, 0.03]), 10, np.array([0.1, 0.2])
    yield np.r_[rng.standard_normal(200) * 0.01, np.nan, np.inf], 50, np.array([0.1, 0.05, 0.0])
    yield np.full(100, 0.001), 10, np.array([0.1, 0.2])     # 常數 → sr=0
    yield 0.01 * rng.standard_normal(5000), 10 ** 9, 0.02 * rng.standard_normal(100)


def check_deflated_sharpe():
    rng = np.random.default_rng(SEED + 3)
    abs_e, n = [], 0
    for r, nt, pool in _dsr_cases(rng):
        got = jw.deflated_sharpe(r, nt, pool)
        ref = ref_deflated_sharpe(r, nt, pool)
        for k_got, k_ref in (("sr_daily", "sr"), ("sr0_daily", "sr0"),
                             ("dsr", "dsr"), ("p_value", "p_value")):
            d, _ = _err(got[k_got], ref[k_ref])
            abs_e.append(d[0])
        n += 1
    return _result("judge_web.deflated_sharpe(+PSR)", "exact",
                   "pandas std(ddof=1) + scipy skew/kurtosis/norm", abs_e, abs_e, 1e-9, n,
                   note="含 proxy 路徑、n_trials=1、NaN/inf 混入、常數、N=1e9")


def check_dsr_pvalue_tail():
    """p_value=1-DSR 在 DSR→1 時有抵銷:比對 scipy norm.sf 的【相對】誤差。"""
    rng = np.random.default_rng(SEED + 4)
    rel_e, n = [], 0
    for _ in range(40):
        T = int(rng.integers(500, 3000))
        r = rng.uniform(0.002, 0.01) + 0.01 * rng.standard_normal(T)   # 強 edge → p 極小
        pool = 0.01 * rng.standard_normal(10)
        got = jw.deflated_sharpe(r, 10, pool)
        ref = ref_deflated_sharpe(r, 10, pool)
        if ref["p_value"] > 1e-300:
            _, rr = _err(got["p_value"], ref["p_value"])
            rel_e.append(rr[0])
            n += 1
    return _result("DSR p_value 尾端相對精度", "exact", "scipy.stats.norm.sf",
                   rel_e, rel_e, 1e-6, n, tol_on="rel",
                   note="強 edge 下 p≪1e-16 時 1-Φ(z) 會被抵銷成 0")


# ===========================================================================
# 3. PBO(CSCV)vs 依 Bailey-Borwein-López de Prado-Zhu (2017) 逐式的 naive loop
# ===========================================================================
def ref_pbo(values, n_blocks=12):
    """naive 參考:逐組合把 IS/OOS 列真的拼起來,np.std(ddof=0) 算夏普,
    scipy.stats.rankdata(method='max') 排名 → ω=rank/(N+1),λ=ln(ω/(1-ω)),
    PBO = #{λ ≤ 0}/#組合(論文定義 ∫_{-∞}^{0} f(λ)dλ,含 0)。
    分塊/降塊規則與引擎相同(那是設計選擇,不是被測對象)。"""
    from scipy.stats import rankdata
    T, N = values.shape
    if N < 2:
        return float("nan")
    if n_blocks % 2:
        n_blocks -= 1
    while n_blocks > 2 and T < n_blocks * 4:
        n_blocks -= 2
    bounds = np.linspace(0, T, n_blocks + 1, dtype=int)
    blocks = [np.arange(bounds[i], bounds[i + 1]) for i in range(n_blocks)]
    lam = []
    for is_set in combinations(range(n_blocks), n_blocks // 2):
        is_rows = np.concatenate([blocks[i] for i in is_set])
        oos_rows = np.concatenate([blocks[i] for i in range(n_blocks) if i not in is_set])

        def sharpe(rows):
            x = values[rows]
            sd = x.std(axis=0, ddof=0)
            return x.mean(axis=0) / np.maximum(sd, 1e-9)

        sr_is, sr_oos = sharpe(is_rows), sharpe(oos_rows)
        best = int(np.argmax(sr_is))
        w = rankdata(sr_oos, method="max")[best] / (N + 1)
        lam.append(math.log(w / (1 - w)))
    lam = np.asarray(lam)
    return float(np.mean(lam <= 0))


def check_degenerate_constant():
    """邊界:常數 / 近常數報酬序列(std 只剩浮點雜訊)。參考答案:夏普無定義,
    不可產生任何 edge 證據 → DSR ≤ 0.5、compute_metrics.sharpe=NaN、analyze 結構化拒審。"""
    from . import analyze
    bad = []
    cases = {"const +0.1%": [0.001] * 300,
             "const + 1e-17 jitter": list(0.001 + np.r_[np.zeros(299), 1e-17]),
             "const + 1e-12 jump": list(0.001 + np.r_[np.zeros(299), 1e-12])}
    for label, r in cases.items():
        r = np.asarray(r)
        d = jw.deflated_sharpe(r, 5, np.array([0.1, 0.2]))["dsr"]
        sh = jw.compute_metrics(r, 252)["sharpe"]
        out = analyze({"mode": "returns", "returns": r.tolist(), "n_trials": 5,
                       "periods_per_year": 252})
        if not (d <= 0.5) or np.isfinite(sh) or out.get("ok") is not False:
            bad.append(f"{label}: dsr={d:.3g} sharpe={sh:.3g} ok={out.get('ok')} "
                       f"verdict={out.get('verdict', {}).get('overall')}")
    err = [float(len(bad))]
    return _result("退化波動(常數序列)", "exact", "定義:夏普無定義 → 不得給 edge 證據",
                   err, err, 0, len(cases), note="; ".join(bad) if bad else "3/3 fail-closed")


def _pbo_cases(rng):
    for _ in range(25):
        T = int(rng.integers(48, 600))
        N = int(rng.integers(2, 40))
        edge = rng.uniform(0, 0.002, N) * (rng.random(N) < 0.3)
        yield f"rand T={T} N={N}", edge + 0.01 * rng.standard_normal((T, N))
    # 邊界:N=2、奇數 N(λ=0 可能出現)、短 T 觸發降塊、重複欄(並列)、極端位移
    yield "N=2", 0.01 * rng.standard_normal((200, 2))
    for N in (3, 5, 7):
        yield f"odd N={N}", 0.01 * rng.standard_normal((240, N))
    yield "short T=20 (降塊)", 0.01 * rng.standard_normal((20, 6))
    base = 0.01 * rng.standard_normal((300, 1))
    yield "duplicate columns", np.hstack([base, base, 0.01 * rng.standard_normal((300, 3))])
    yield "offset 1e3 (moments cancellation)", 1e3 + 1e-4 * rng.standard_normal((240, 6))


def check_pbo():
    rng = np.random.default_rng(SEED + 5)
    abs_e, n, bad = [], 0, []
    for label, v in _pbo_cases(rng):
        got = jw.pbo_cscv(v, 12)["pbo"]
        ref = ref_pbo(v, 12)
        d, _ = _err(got, ref)
        abs_e.append(d[0])
        if d[0] > 1e-12:
            bad.append(f"{label}: engine={got:.4f} ref={ref:.4f}")
        n += 1
    return _result("judge_web.pbo_cscv", "exact",
                   "naive CSCV loop + scipy.stats.rankdata", abs_e, abs_e, 1e-12, n,
                   note="; ".join(bad) if bad else "全部逐位一致")


# ===========================================================================
# 4. Bootstrap 底座 / Hansen SPA / White Reality Check / Romano-Wolf StepM
# ===========================================================================
def ref_cbb_means(diffs, n_boot, L, rng):
    """naive circular block bootstrap:顯式索引 (start+0..L-1) mod T,串到 ≥T 截斷。
    用與引擎同一亂數流(同 shape 的 rng.integers)以便逐位比對。"""
    T, K = diffs.shape
    m = int(np.ceil(T / L))
    out = np.empty((n_boot, K))
    starts_all = rng.integers(0, T, size=(n_boot, m))
    for b in range(n_boot):
        idx = (starts_all[b][:, None] + np.arange(L)[None, :]).ravel() % T
        out[b] = diffs[idx[:T]].mean(axis=0)
    return out


def check_cbb():
    rng = np.random.default_rng(SEED + 6)
    abs_e, n = [], 0
    for T, K, L in ((250, 5, 7), (251, 3, 10), (100, 1, 1), (120, 4, 12), (97, 2, 97), (300, 8, 2)):
        x = rng.standard_normal((T, K))
        got = jw._circular_block_bootstrap_means(x, 400, L, np.random.default_rng(1))
        ref = ref_cbb_means(x, 400, L, np.random.default_rng(1))
        abs_e.append(np.max(np.abs(got - ref)))
        n += 1
    return _result("judge_web._circular_block_bootstrap_means", "exact",
                   "naive 顯式索引 CBB(同亂數流)", abs_e, abs_e, 1e-12, n,
                   note="含 L=1、L=T、T 不整除 L")


def check_cbb_variance():
    """CBB 平均數的 bootstrap 變異 vs 精確解析值(T 整除 L 時 Var* = Var_t(blockmean_t)/m),
    並驗證引擎 spread_corr=1/√(1-L/T) 的偏差校正把它拉回 iid 的 σ̂²/T。"""
    rng = np.random.default_rng(SEED + 7)
    rel_e, n = [], 0
    for T, L in ((240, 6), (250, 10), (500, 20)):
        x = rng.standard_normal(T)
        xc = x - x.mean()
        ext = np.r_[xc, xc[:L - 1]]
        bm = np.array([ext[t:t + L].mean() for t in range(T)])
        exact = np.mean(bm ** 2) / (T // L)
        boots = jw._circular_block_bootstrap_means(x[:, None], 40000, L, np.random.default_rng(3))
        emp = boots[:, 0].var()
        rel_e.append(abs(emp / exact - 1))
        n += 1
    return _result("CBB 平均數變異 vs 解析值", "statistical", "解析公式(Künsch/Politis-Romano)",
                   rel_e, rel_e, 0.03, n, tol_on="rel", note="B=40000,MC 相對誤差 ~1%")


def ref_spa(dbar, boot_means, se, se_null, T):
    """Hansen (2005) SPA,逐候選 / 逐 bootstrap 的 naive loop。p 值 (1+#)/(B+1)。
    g_c(x)=x·1{x ≥ -thr},thr = se_null·√(2 loglog T);Z* = d̄* - g(d̄)。"""
    K = dbar.size
    B = boot_means.shape[0]
    t_obs = max(0.0, max(dbar[k] / se[k] for k in range(K)))
    thr = math.sqrt(2 * math.log(math.log(T))) if T > 15 else 0.0
    g = {"p_upper": dbar.copy(),
         "p_lower": np.maximum(dbar, 0.0),
         "p_value": np.array([dbar[k] if dbar[k] > -se_null[k] * thr else 0.0 for k in range(K)])}
    out = {}
    for key, gk in g.items():
        cnt = 0
        for b in range(B):
            tb = max(0.0, max((boot_means[b, k] - gk[k]) / se[k] for k in range(K)))
            cnt += tb >= t_obs
        out[key] = (1 + cnt) / (B + 1)
    return out


def ref_romano_wolf(dbar, boot_means, se):
    """Romano & Wolf (2016) 調整後 p 值 step-down,逐步 naive loop。"""
    K = dbar.size
    t = dbar / se
    tb = (boot_means - dbar) / se
    order = sorted(range(K), key=lambda k: -t[k])
    p = np.empty(K)
    prev = 0.0
    for j, k in enumerate(order):
        remaining = order[j:]
        mx = tb[:, remaining].max(axis=1)
        pj = (1 + np.sum(mx >= t[k])) / (tb.shape[0] + 1)
        prev = max(prev, pj)
        p[k] = prev
    return p


def check_spa_rw_exact():
    rng = np.random.default_rng(SEED + 8)
    abs_e, n = [], 0
    for T, K in ((150, 4), (250, 10), (400, 1), (120, 25)):
        edge = rng.uniform(-0.002, 0.002, K)
        diffs = edge + 0.01 * rng.standard_normal((T, K))
        boot = jw._circular_block_bootstrap_means(diffs, 300, 5, np.random.default_rng(2))
        dbar = diffs.mean(axis=0)
        se = diffs.std(axis=0, ddof=1) / math.sqrt(T)
        se_null = boot.std(axis=0, ddof=1)
        got = jw._spa_pvalues(dbar, boot, se, T, se_null=se_null)
        ref = ref_spa(dbar, boot, se, se_null, T)
        for key in ("p_value", "p_lower", "p_upper"):
            abs_e.append(abs(got[key] - ref[key]))
        abs_e.append(np.max(np.abs(jw._romano_wolf_adj_p(dbar, boot, se) - ref_romano_wolf(dbar, boot, se))))
        n += 1
    return _result("_spa_pvalues / _romano_wolf_adj_p", "exact",
                   "Hansen(2005)/Romano-Wolf(2016) naive loop", abs_e, abs_e, 1e-12, n,
                   note="同一組 bootstrap 平均數,比演算法本身")


def check_block_len_acf():
    from statsmodels.tsa.stattools import acf
    rng = np.random.default_rng(SEED + 9)
    abs_e, n = [], 0
    for phi in (0.0, 0.1, 0.3, 0.6, -0.3):
        T, K = 300, 6
        e = rng.standard_normal((T, K))
        x = np.empty_like(e)
        x[0] = e[0]
        for t in range(1, T):
            x[t] = phi * x[t - 1] + e[t]
        rho_ref = np.median([acf(x[:, k], nlags=1, adjusted=False, fft=False)[1] for k in range(K)])
        r = float(np.clip(rho_ref, 0.0, 0.8))
        L_ref = int(np.clip(int(round(T ** (1 / 3) * ((1 + r) / (1 - r)) ** (2 / 3))), 2, T // 10))
        abs_e.append(abs(jw._auto_block_len(x) - L_ref))
        n += 1
    return _result("_auto_block_len(lag-1 ACF)", "exact", "statsmodels.tsa.stattools.acf",
                   abs_e, abs_e, 0, n, note="block 長整數逐一相同")


def _fwer_vs_arch_cases(rng):
    for i in range(12):
        T = int(rng.choice([250, 400]))
        K = int(rng.choice([5, 10, 20]))
        edge = np.zeros(K)
        if i % 2:
            edge[: max(1, K // 5)] = rng.uniform(0.0008, 0.002)
        if i % 3 == 0:
            edge -= rng.uniform(0, 0.002, K) * (rng.random(K) < 0.5)   # 爛候選(測 SPA 重心化)
        bench = 0.0002 + 0.008 * rng.standard_normal(T)
        models = bench[:, None] + edge + 0.01 * rng.standard_normal((T, K))
        yield T, K, models, bench


def check_spa_vs_arch(reps=4000):
    """統計比對:引擎 SPA(consistent / lower / upper=studentized White RC)vs arch.bootstrap.SPA,
    同 block 長、circular bootstrap、studentize=True。亂數流不同 → 只能比 p 值接近度。
    已知設計差異:引擎 studentizer 用 iid se(R15)+ CBB 偏差校正;arch 用 kernel 變異。"""
    from arch.bootstrap import SPA
    rng = np.random.default_rng(SEED + 10)
    abs_e, n = [], 0
    for T, K, models, bench in _fwer_vs_arch_cases(rng):
        names = [f"m{k}" for k in range(K)]
        fw = jw.run_fwer_gates(models, names, bench, n_bootstrap=reps, seed=7)
        L = fw["calibration"]["block_len"]
        spa = SPA(-bench, -models, block_size=L, reps=reps, bootstrap="circular",
                  studentize=True, seed=11)
        spa.compute()
        pv = spa.pvalues
        for k_eng, k_arch in (("p_value", "consistent"), ("p_lower", "lower"), ("p_upper", "upper")):
            abs_e.append(abs(fw["spa"][k_eng] - float(pv[k_arch])))
        n += 1
    return _result("Hansen SPA / White RC(studentized)", "statistical",
                   "arch.bootstrap.SPA(circular, studentize)", abs_e, abs_e, 0.06, n,
                   note=f"reps={reps};p_upper 即 Hansen 的 SPA_u = studentized White Reality Check")


def check_white_rc_vs_arch(reps=4000):
    """資訊性:引擎沒有【非 studentized】的原版 White (2000) RC;arch.RealityCheck(studentize=False)
    與引擎 p_upper 的差距,說明兩者在異質變異下可分歧(引擎採較有檢力的 studentized 版)。"""
    from arch.bootstrap import RealityCheck
    rng = np.random.default_rng(SEED + 10)
    abs_e, n = [], 0
    for T, K, models, bench in _fwer_vs_arch_cases(rng):
        fw = jw.run_fwer_gates(models, [f"m{k}" for k in range(K)], bench, n_bootstrap=reps, seed=7)
        rc = RealityCheck(-bench, -models, block_size=fw["calibration"]["block_len"], reps=reps,
                          bootstrap="circular", studentize=False, seed=11)
        rc.compute()
        abs_e.append(abs(fw["spa"]["p_upper"] - float(rc.pvalues["upper"])))
        n += 1
    return _result("原版 White RC(非 studentized)vs 引擎 p_upper", "informational",
                   "arch.bootstrap.RealityCheck(studentize=False)", abs_e, abs_e, 0.15, n,
                   note="不同統計量,只記錄差距")


def check_stepm_vs_arch(reps=4000, alpha=0.10):
    """統計比對:引擎 Romano-Wolf(p_adj ≤ α 的拒絕集)vs arch.bootstrap.StepM(size=α)superior set。
    指標 = 逐候選決策不一致的比例。"""
    from arch.bootstrap import StepM
    rng = np.random.default_rng(SEED + 10)
    mismatches, total, n = 0, 0, 0
    per_case = []
    for T, K, models, bench in _fwer_vs_arch_cases(rng):
        names = [f"m{k}" for k in range(K)]
        fw = jw.run_fwer_gates(models, names, bench, alpha=alpha, n_bootstrap=reps, seed=7)
        st = StepM(-bench, -models, size=alpha, block_size=fw["calibration"]["block_len"],
                   reps=reps, bootstrap="circular", studentize=True, seed=11)
        st.compute()
        sup = set(int(s) for s in np.atleast_1d(st.superior_models)) if st.superior_models is not None else set()
        eng = {k for k in range(K) if fw["per_candidate"][names[k]]["pass"]}
        mm = len(sup ^ eng)
        mismatches += mm
        total += K
        per_case.append(mm / K)
        n += 1
    rate = mismatches / max(total, 1)
    return _result("Romano-Wolf StepM 拒絕集", "statistical",
                   "arch.bootstrap.StepM(circular, studentize)", [rate], [rate], 0.05, n,
                   note=f"逐候選決策不一致率 {rate:.3f}({mismatches}/{total})")


# ===========================================================================
# 5. permutation / block-bootstrap null(單序列)
# ===========================================================================
def ref_permutation_null(returns, apy, n_perm, block_len, seed):
    """naive:顯式索引 CBB 重抽去均值序列,長度截到 T(與 FWER 底座同慣例),
    平均數離差乘 1/√(1-L/T) 校正 CBB 有限樣本下偏,算年化夏普。"""
    r = np.asarray(returns, float)
    T = r.size
    dm = r - r.mean()
    L = int(block_len) if block_len else int(max(1, round(math.sqrt(T))))
    L = max(1, min(L, T))
    m = int(math.ceil(T / L))
    corr = 1.0 / math.sqrt(max(1.0 - L / T, 0.25))
    rng = np.random.default_rng(seed)
    null = np.empty(n_perm)
    for i in range(n_perm):
        starts = rng.integers(0, T, size=m)
        idx = ((starts[:, None] + np.arange(L)[None, :]).ravel() % T)[:T]
        x = dm[idx]
        mu = x.mean()
        sd = math.sqrt(max(np.mean(x * x) - mu * mu, 0.0))
        null[i] = (mu * corr / sd) * math.sqrt(apy) if sd > 0 else 0.0
    real = (r.mean() / r.std()) * math.sqrt(apy) if r.std() > 0 else 0.0
    return {"real_sharpe": real, "null_p95_sharpe": float(np.percentile(null, 95)),
            "p_value": (1 + int(np.sum(null >= real))) / (n_perm + 1)}


def check_permutation_exact():
    rng = np.random.default_rng(SEED + 11)
    abs_e, n = [], 0
    for T, L in ((250, None), (251, 7), (60, None), (500, 1), (40, 40), (1000, None)):
        r = 0.0004 + 0.01 * rng.standard_normal(T)
        got = jw.permutation_null(r, 252, n_perm=500, block_len=L, seed=5)
        ref = ref_permutation_null(r, 252, 500, L, 5)
        for k in ("real_sharpe", "null_p95_sharpe", "p_value"):
            abs_e.append(abs(got[k] - ref[k]))
        n += 1
    return _result("judge_web.permutation_null", "exact",
                   "naive 顯式索引 CBB(長度 T、偏差校正)", abs_e, abs_e, 1e-9, n,
                   note="含 T 不整除 L、L=1、L=T")


def check_permutation_vs_ttest(n_sets=300, n_perm=1000):
    """統計比對:iid 常態下,block-bootstrap null 應重現古典結果——
    null p95(每期夏普尺度)≈ t_{.95,T-1}/√(T-1)(scipy.stats.t,ddof=0 夏普的精確 null 分位)。
    L=√T 時有效 block 只有 ~√T 個,單一資料集的 p95 噪聲 ~18%,故平均 300 組資料集
    (標準誤 ~1%)。指標 = 平均比值偏離 1;附 p 值 vs scipy.stats.ttest_1samp 的平均差。"""
    from scipy import stats
    ratio_dev, p_diff, n, parts = [], [], 0, []
    for T in (60, 250, 1000):
        ratios = []
        for s in range(n_sets):
            rng = np.random.default_rng(10 ** 6 + s)
            r = 0.0002 + 0.01 * rng.standard_normal(T)
            o = jw.permutation_null(r, 1, n_perm=n_perm)
            ratios.append(o["null_p95_sharpe"] * math.sqrt(T - 1) / stats.t.ppf(0.95, T - 1))
            p_diff.append(o["p_value"] - stats.ttest_1samp(r, 0, alternative="greater").pvalue)
            n += 1
        ratio_dev.append(abs(np.mean(ratios) - 1.0))
        parts.append(f"T={T}:{np.mean(ratios):.3f}±{np.std(ratios) / math.sqrt(n_sets):.3f}")
    return _result("permutation_null vs 古典 t 檢定", "statistical",
                   "scipy.stats.t.ppf / ttest_1samp", ratio_dev, ratio_dev, 0.03, n,
                   note="null p95 / t 分位 = " + "、".join(parts) +
                        f";p 值平均 Δ={np.mean(p_diff):+.4f}")


# ===========================================================================
# 6. 績效指標 / 成本壓力 vs pandas
# ===========================================================================
def ref_metrics(r, apy):
    s = pd.Series(r, dtype=float).fillna(0.0)
    eq = (1 + s).cumprod()
    mdd = float((eq / eq.cummax() - 1).min())
    years = len(s) / apy
    cagr = eq.iloc[-1] ** (1 / years) - 1 if eq.iloc[-1] > 0 else float("nan")
    vol = s.std(ddof=0) * math.sqrt(apy)
    mean = s.mean() * apy
    # Sortino & Price (1994):下方偏差 = √(mean(min(r,0)²)),對【全部】期數平均,目標 0
    dd = math.sqrt(np.mean(np.minimum(s.values, 0.0) ** 2)) * math.sqrt(apy)
    return {"sharpe": mean / vol if vol > 0 else float("nan"),
            "sortino": mean / dd if dd > 0 else float("nan"),
            "ann_vol": vol, "max_drawdown": mdd, "cagr": cagr,
            "calmar": cagr / abs(mdd) if mdd < 0 else float("nan")}


def check_metrics():
    rng = np.random.default_rng(SEED + 12)
    errs = {k: [] for k in ("sharpe", "sortino", "ann_vol", "max_drawdown", "cagr", "calmar")}
    n = 0
    cases = [0.0005 + 0.01 * rng.standard_t(4, int(rng.integers(20, 2000))) for _ in range(40)]
    cases += [np.r_[0.01 * np.ones(50), -0.001],                 # 只有一個負報酬
              np.r_[0.02, -0.01, -0.01, 0.03],                   # 負報酬全相同(舊 Sortino 分母=0)
              np.r_[0.01 * rng.standard_normal(100), np.nan]]
    for r in cases:
        got = jw.compute_metrics(r, 252)
        ref = ref_metrics(r, 252)
        for k in errs:
            d, rel = _err(got[k], ref[k])
            errs[k].append(min(d[0], rel[0]))
        n += 1
    worst = max(errs, key=lambda k: max(errs[k]))
    flat = [e for v in errs.values() for e in v]
    return _result("judge_web.compute_metrics", "exact",
                   "pandas cumprod/cummax + Sortino-Price 下方偏差", flat, flat, 1e-9, n,
                   note=f"最差欄位={worst} ({max(errs[worst]):.3g})")


def check_cost_stress():
    rng = np.random.default_rng(SEED + 13)
    abs_e, n = [], 0
    for _ in range(20):
        T = int(rng.integers(50, 800))
        r = 0.0006 + 0.01 * rng.standard_normal(T)
        tn = rng.uniform(0, 2, T)
        tn[rng.random(T) < 0.02] = np.nan
        bps = float(rng.uniform(1, 50))
        got = jw.cost_stress(r, tn, bps, 252)
        df = pd.DataFrame({"r": r, "tn": tn}).dropna()
        for mult, key in ((1, "x1_sharpe"), (3, "x3_sharpe"), (6, "x6_sharpe")):
            net = df.r - df.tn * bps / 1e4 * mult
            ref = net.mean() / net.std(ddof=0) * math.sqrt(252)
            abs_e.append(abs(got[key] - ref))
        n += 1
    return _result("judge_web.cost_stress", "exact", "pandas dropna + std(ddof=0)",
                   abs_e, abs_e, 1e-9, n, note="含 2% NaN 換手(剔除不填 0)")


# ===========================================================================
ALL_CHECKS = [
    check_norm_cdf, check_norm_ppf, check_skew_kurtosis,
    check_expected_max_sharpe, check_expected_max_vs_integral,
    check_deflated_sharpe, check_dsr_pvalue_tail, check_degenerate_constant,
    check_pbo,
    check_cbb, check_cbb_variance, check_spa_rw_exact, check_block_len_acf,
    check_spa_vs_arch, check_white_rc_vs_arch, check_stepm_vs_arch,
    check_permutation_exact, check_permutation_vs_ttest,
    check_metrics, check_cost_stress,
]


def run_all():
    with warnings.catch_warnings():
        # scipy 對近常數序列的「catastrophic cancellation」警告:參考側已知,不影響比對
        warnings.simplefilter("ignore", RuntimeWarning)
        return [fn() for fn in ALL_CHECKS]


def to_markdown(results):
    lines = ["| check | 類型 | 參考實作 | cases | max abs err | max rel err | 容差 | 結果 | 備註 |",
             "|---|---|---|---:|---:|---:|---:|:---:|---|"]
    for r in results:
        lines.append(
            f"| {r['name']} | {r['kind']} | {r['reference']} | {r['n_cases']} | "
            f"{r['max_abs_err']:.3g} | {r['max_rel_err']:.3g} | "
            f"{r['tol']:.3g} ({r['tol_on']}) | {'✅' if r['ok'] else '❌'} | {r['note']} |")
    return "\n".join(lines)


if __name__ == "__main__":
    import platform

    import arch
    import scipy
    import statsmodels
    print(f"python {platform.python_version()} · numpy {np.__version__} · pandas {pd.__version__} · "
          f"scipy {scipy.__version__} · statsmodels {statsmodels.__version__} · arch {arch.__version__}\n")
    res = run_all()
    print(to_markdown(res))
    sys.exit(0 if all(r["ok"] for r in res) else 1)
