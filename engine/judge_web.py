"""judge_web — Edge Validator 的統一裁判引擎(純 numpy/pandas,Pyodide-ready)。

把 auto-quant-btc FARM 農場的「不被自己騙」制度萃取成一顆瀏覽器內可跑的引擎:
- DSR(Deflated Sharpe Ratio)         ← farm/judge.py,scipy 換成 statshim
- Romano-Wolf StepM / Hansen SPA / PBO ← farm/fwer_gates.py,幾乎原封(本就純 numpy)
- Sharpe/Sortino/Calmar/MDD/集中度      ← backtest/metrics.py
- round-trip bps 成本壓力               ← farm/factor_harvest_mvp.py 的 CostModel 精神
- permutation / block-bootstrap null    ← 本檔新寫(單報酬序列 vs null p95)

唯一入口 `analyze(payload: dict) -> dict`,契約見 README / 前端依賴。純函數,不碰檔案/網路。

裁判哲學(誠實、保守):過關只代表「沒明顯過擬合」,不代表會賺。reasons 用人話講清楚。
"""
from itertools import combinations

import numpy as np
import pandas as pd

from . import statshim as ss

EULER_GAMMA = 0.5772156649015329
_SE_FLOOR = 1e-12


# ===========================================================================
# 1. 績效指標(移植 backtest/metrics.py,改吃 periods_per_year 純數字)
# ===========================================================================
def _equity_from_returns(r: np.ndarray, start=1.0) -> np.ndarray:
    return np.cumprod(1.0 + np.nan_to_num(r, nan=0.0)) * start


def _max_drawdown(equity: np.ndarray) -> float:
    if equity.size == 0:
        return 0.0
    roll_max = np.maximum.accumulate(equity)
    dd = equity / roll_max - 1.0
    return float(dd.min())


def compute_metrics(returns, periods_per_year: float) -> dict:
    """一包風險調整後指標。returns=每期報酬率(已扣成本)。periods_per_year=252/52/12/365..."""
    r = np.nan_to_num(np.asarray(returns, dtype=float), nan=0.0)
    n = int(r.size)
    apy = float(periods_per_year) if periods_per_year else 252.0
    eq = _equity_from_returns(r)
    final_equity = float(eq[-1]) if n else 1.0
    total_return = final_equity - 1.0 if n else 0.0
    years = n / apy if apy else 0.0
    cagr = (final_equity ** (1.0 / years) - 1.0) if years > 0 and final_equity > 0 else float("nan")

    mean = r.mean() * apy if n else 0.0
    vol = r.std(ddof=0) * np.sqrt(apy) if n else 0.0
    sharpe = float(mean / vol) if vol > 0 else float("nan")

    neg = r[r < 0]
    downside = neg.std(ddof=0) * np.sqrt(apy) if neg.size else 0.0
    sortino = float(mean / downside) if downside > 0 else float("nan")

    mdd = _max_drawdown(eq)
    calmar = float(cagr / abs(mdd)) if mdd < 0 and np.isfinite(cagr) else float("nan")

    pos_sum = r[r > 0].sum()
    top_conc = float(r.max() / pos_sum) if pos_sum > 0 else float("nan")

    return {
        "sharpe": sharpe, "sortino": sortino, "cagr": cagr, "ann_vol": float(vol),
        "max_drawdown": mdd, "calmar": calmar, "n_periods": n,
        "top_bar_concentration": top_conc, "final_equity": final_equity,
        "total_return": float(total_return),
    }


def _sharpe_periodic(r: np.ndarray) -> float:
    """每期(非年化)Sharpe,DSR / permutation 用。"""
    if r.size == 0:
        return 0.0
    std = r.std(ddof=0)
    return float(r.mean() / std) if std > 0 else 0.0


# ===========================================================================
# 2. Deflated Sharpe Ratio(移植 farm/judge.py,scipy→statshim)
# ===========================================================================
def expected_max_sharpe(sr_variance: float, n_trials: int) -> float:
    """n_trials 條零技術策略中,最佳者 Sharpe 的期望值(每期尺度)。"""
    if n_trials < 2:
        return 0.0
    sd = float(np.sqrt(max(sr_variance, 0.0)))
    z1 = ss.norm_ppf(1.0 - 1.0 / n_trials)
    z2 = ss.norm_ppf(1.0 - 1.0 / (n_trials * np.e))
    return sd * ((1.0 - EULER_GAMMA) * z1 + EULER_GAMMA * z2)


def probabilistic_sharpe(sr_hat: float, sr_benchmark: float, n_obs: int,
                         skew: float, kurt: float) -> float:
    """PSR:給定非常態報酬,P(真 SR > sr_benchmark)。kurt 為非超額(normal=3)。"""
    denom = np.sqrt(1.0 - skew * sr_hat + (kurt - 1.0) / 4.0 * sr_hat ** 2)
    if not np.isfinite(denom) or denom == 0:
        return float("nan")
    z = (sr_hat - sr_benchmark) * np.sqrt(max(n_obs - 1, 1)) / denom
    return float(ss.norm_cdf(z))


def deflated_sharpe(returns: np.ndarray, n_trials: int,
                    trial_sharpes: np.ndarray) -> dict:
    """單條策略每期報酬的 DSR。trial_sharpes=試過各參數的每期 Sharpe 池(matrix 模式才有真池;
    returns 模式退化成 n_trials 驅動的通縮)。回傳每期尺度 sr / sr0 / dsr_prob / p_value。"""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    std = r.std(ddof=1) if r.size > 1 else 0.0
    sr = float(r.mean() / std) if std > 0 else 0.0
    pool = np.asarray(trial_sharpes, dtype=float)
    pool = pool[np.isfinite(pool)]
    sr_variance = float(np.var(pool, ddof=1)) if pool.size > 1 else 0.0
    sr0 = expected_max_sharpe(sr_variance, n_trials)
    dsr = probabilistic_sharpe(
        sr_hat=sr, sr_benchmark=sr0, n_obs=r.size,
        skew=ss.skew(r) if r.size > 2 else 0.0,
        kurt=ss.kurtosis(r, fisher=False) if r.size > 2 else 3.0,
    )
    return {"sr_daily": sr, "sr0_daily": sr0, "dsr": dsr,
            "p_value": (1.0 - dsr) if np.isfinite(dsr) else float("nan"),
            "n_trials": int(n_trials)}


# ---------------------------------------------------------------------------
# 2.5 matrix n_trials 硬化(★防雜訊放水核心,單一真源★)
#     農場 NOISE_POOL 精神:多重檢定的分母必須反映【真實搜尋廣度】。「N 欄挑最佳」時,
#     達不到真實信心地板(DSR≥0.95)的最佳欄,與「一次幸運的雜訊抽樣」不可區分——此時把
#     n_trials 沿倍率放大到裁判自己的 DSR 跌破雜訊地板(0.60),使它【不會】被判 likely-real。
#     真 edge 一開始就站上 0.95,維持誠實 n_trials=N(=欄數),不被多罰。
#
#     這段【本】在 local/sources.py(本地版),R4 搬進 engine 成單一真源:公開站(app.js
#     只要照送 mode=matrix)與本地版共用同一硬化,不分叉。engine 內部直接呼 deflated_sharpe
#     算 DSR(與 analyze 回的 dsr_prob 逐位元相同,已驗),故無遞迴、確定性、Pyodide-safe。
# ---------------------------------------------------------------------------
REAL_CONF_BAR = 0.95   # 農場真實信心地板:DSR≥此值才算「有 edge 的機率夠高」(維持誠實 n_trials)
NOISE_FLOOR = 0.60     # 裁判雜訊地板:未達真實信心的最佳欄,硬化到 DSR 跌破此值使其不判為真
HARDEN_MULTS = (2, 4, 8, 16, 32)  # n_trials 放大梯度


def harden_matrix_n_trials(returns: np.ndarray, trial_sharpes: np.ndarray,
                           base_n_trials: int) -> tuple[int, dict]:
    """決定「N 欄挑最佳」該用多大的 n_trials 送 DSR 通縮。純函數,只讀。

    returns       : matrix 挑出的主序列(樣本內夏普最高欄)。
    trial_sharpes : 各欄每期夏普池(DSR 通縮的真試驗池)。
    base_n_trials : 誠實基準 = 欄數 N。

    做法(誠實、無魔術常數,兩門檻取自裁判/農場既有語意):
      pass1:用誠實 n_trials=N 算 DSR。DSR ≥ 0.95 → 真達標,回 (N, 診斷),不多罰。
      DSR < 0.95 → 沿 HARDEN_MULTS 逐級放大 n_trials,回報「首次讓 DSR < 0.60」那級(或封頂級)。
                   高 n_trials 正是「這個贏家在如此廣的搜尋下毫不出奇」的誠實表述,非灌水。

    ★封頂逃逸 fail-closed(R5)★:若沿 HARDEN_MULTS 放大到封頂上限(32×base),DSR【仍】
      ≥ 雜訊地板(0.60),代表這個贏家在極廣搜尋下依舊未被通縮到雜訊——但這【不是】真達標
      的證據,而是「欄數過多/樣本過短,通縮上限吃不掉它」的病態:高欄數×極短樣本組合下,一次
      幸運的雜訊抽樣也能撐住 DSR。此時設 capped_escape=True,交給 _verdict 誠實 fail-closed
      (不判 likely-real),而非讓它靠殘餘 DSR≥0.60 混過雜訊地板。

    回 (effective_n_trials, diag)。diag 帶 dsr_at_base / dsr_final / bar / hardened /
    capped_escape,供揭露。
    """
    base = max(int(base_n_trials), 2)
    diag = {"bar": REAL_CONF_BAR, "noise_floor": NOISE_FLOOR, "base_n_trials": base,
            "dsr_at_base": None, "dsr_final": None, "hardened": False,
            "capped_escape": False}

    def _dsr(nt: int) -> float:
        return float(deflated_sharpe(returns, nt, trial_sharpes)["dsr"])

    d0 = _dsr(base)
    diag["dsr_at_base"] = d0
    diag["dsr_final"] = d0
    # DSR 算不出(樣本/變異不足)→ 不硬掛,回誠實 base(讓裁判用既有邏輯處理)
    if not (d0 == d0):  # NaN check(無 numpy 依賴)
        return base, diag
    if d0 >= REAL_CONF_BAR:
        return base, diag  # 真達標:維持誠實 n_trials,不多罰

    eff = base
    for mult in HARDEN_MULTS:
        eff = base * mult
        d = _dsr(eff)
        diag["dsr_final"] = d
        diag["hardened"] = True
        if d == d and d < NOISE_FLOOR:
            return eff, diag
    # 封頂仍未跌破雜訊地板:fail-closed。已盡量通縮(回最大級),並標記封頂逃逸讓裁判擋下。
    if diag["dsr_final"] == diag["dsr_final"] and diag["dsr_final"] >= NOISE_FLOOR:
        diag["capped_escape"] = True
    return eff, diag


# ===========================================================================
# 3. FWER 層:circular block bootstrap 底座 + SPA + Romano-Wolf + PBO
#    (移植 farm/fwer_gates.py,本就純 numpy)
# ===========================================================================
def _circular_block_bootstrap_means(diffs: np.ndarray, n_boot: int, block_len: int,
                                    rng: np.random.Generator) -> np.ndarray:
    T, K = diffs.shape
    L = int(max(1, min(block_len, T)))
    m = int(np.ceil(T / L))
    rem = T - (m - 1) * L
    ext = np.concatenate([diffs, diffs[:L - 1]], axis=0) if L > 1 else diffs
    c = np.concatenate([np.zeros((1, K)), np.cumsum(ext, axis=0)], axis=0)
    sum_l = c[L:L + T] - c[:T]
    sum_rem = c[rem:rem + T] - c[:T]
    means = np.empty((n_boot, K), dtype=float)
    chunk = max(1, int(4e7 // max(1, m * K)))
    for lo in range(0, n_boot, chunk):
        hi = min(n_boot, lo + chunk)
        starts = rng.integers(0, T, size=(hi - lo, m))
        s = sum_l[starts[:, :m - 1]].sum(axis=1) if m > 1 else 0.0
        means[lo:hi] = (s + sum_rem[starts[:, m - 1]]) / T
    return means


def _spa_pvalues(dbar: np.ndarray, boot_means: np.ndarray, se: np.ndarray, T: int) -> dict:
    t_stat = dbar / se
    t_obs = float(max(0.0, t_stat.max()))
    loglog = float(np.sqrt(2.0 * np.log(np.log(T)))) if T > 15 else 0.0
    mu_c = np.where(dbar <= -se * loglog, dbar, 0.0)
    centered = boot_means - dbar
    b = boot_means.shape[0]
    out = {}
    for key, mu in (("p_lower", np.minimum(dbar, 0.0)),
                    ("p_value", mu_c),
                    ("p_upper", np.zeros_like(dbar))):
        tb = np.maximum(((centered + mu) / se).max(axis=1), 0.0)
        out[key] = float((1 + int(np.sum(tb >= t_obs))) / (b + 1))
    out["t_max"] = t_obs
    return out


def _romano_wolf_adj_p(dbar: np.ndarray, boot_means: np.ndarray, se: np.ndarray) -> np.ndarray:
    K = dbar.shape[0]
    t_stat = dbar / se
    tb = (boot_means - dbar) / se
    order = np.argsort(-t_stat)
    suffix_max = np.maximum.accumulate(tb[:, order][:, ::-1], axis=1)[:, ::-1]
    b = tb.shape[0]
    p_adj = np.empty(K, dtype=float)
    prev = 0.0
    for j in range(K):
        p_j = (1 + int(np.sum(suffix_max[:, j] >= t_stat[order[j]]))) / (b + 1)
        prev = max(prev, p_j)
        p_adj[order[j]] = prev
    return p_adj


def _block_moments(values: np.ndarray, n_blocks: int):
    t = values.shape[0]
    bounds = np.linspace(0, t, n_blocks + 1, dtype=int)
    sums = np.array([values[bounds[i]:bounds[i + 1]].sum(axis=0) for i in range(n_blocks)])
    sumsqs = np.array([(values[bounds[i]:bounds[i + 1]] ** 2).sum(axis=0) for i in range(n_blocks)])
    counts = np.array([[bounds[i + 1] - bounds[i]] * values.shape[1] for i in range(n_blocks)],
                      dtype=float)
    return sums, sumsqs, counts


def _sharpe_from_moments(s, ss_, n):
    mean = s / n
    var = ss_ / n - mean ** 2
    sd = np.sqrt(np.clip(var, 1e-18, None))
    return mean / sd


def pbo_cscv(values: np.ndarray, n_blocks: int = 12) -> dict:
    """整批策略的 PBO。values=(T,K) 報酬矩陣。回傳 pbo(0~1,越低越好)。"""
    if values.shape[1] < 2:
        return {"pbo": float("nan"), "n_combinations": 0, "n_strategies": int(values.shape[1])}
    if n_blocks % 2 != 0:
        n_blocks -= 1
    while n_blocks > 2 and values.shape[0] < n_blocks * 4:
        n_blocks -= 2
    if n_blocks < 2:
        return {"pbo": float("nan"), "n_combinations": 0, "n_strategies": int(values.shape[1])}
    n_strategies = values.shape[1]
    sums, sumsqs, counts = _block_moments(values, n_blocks)
    is_idx = np.array(list(combinations(range(n_blocks), n_blocks // 2)))
    mask = np.zeros((len(is_idx), n_blocks), dtype=bool)
    mask[np.arange(len(is_idx))[:, None], is_idx] = True
    oos_idx = np.nonzero(~mask)[1].reshape(len(is_idx), n_blocks // 2)
    sr_is = _sharpe_from_moments(
        np.add.reduce(sums[is_idx], axis=1),
        np.add.reduce(sumsqs[is_idx], axis=1),
        np.add.reduce(counts[is_idx], axis=1))
    sr_oos = _sharpe_from_moments(
        np.add.reduce(sums[oos_idx], axis=1),
        np.add.reduce(sumsqs[oos_idx], axis=1),
        np.add.reduce(counts[oos_idx], axis=1))
    best = np.argmax(sr_is, axis=1)
    best_oos = sr_oos[np.arange(len(is_idx)), best]
    omega = (sr_oos <= best_oos[:, None]).sum(axis=1) / (n_strategies + 1)
    omega = np.clip(omega, 1e-9, 1 - 1e-9)
    logits = np.log(omega / (1 - omega))
    return {"pbo": float(np.mean(logits < 0)), "n_combinations": int(len(logits)),
            "n_strategies": n_strategies, "n_blocks": n_blocks}


def run_fwer_gates(matrix_values: np.ndarray, names: list, bench: np.ndarray,
                   alpha=0.10, n_bootstrap=1000, block_len=10, seed=20260707,
                   min_obs=100, n_blocks_pbo=12) -> dict:
    """對一批候選跑 Romano-Wolf(逐候選)+ SPA + PBO(套件級)。matrix_values=(T,K),
    bench=(T,) 同期基準報酬。缺基準時傳 0 序列(=絕對報酬 vs 0)。"""
    K = matrix_values.shape[1]
    base = {"computed": False, "alpha": alpha, "n_candidates": K,
            "per_candidate": {}, "n_rejected": 0,
            "spa": {"p_value": float("nan"), "p_lower": float("nan"),
                    "p_upper": float("nan"), "t_max": float("nan"), "best": None},
            "pbo": {"pbo": float("nan"), "n_combinations": 0, "n_strategies": K}}
    T = matrix_values.shape[0]
    if K < 1 or T < min_obs:
        base["reason"] = f"對齊後樣本 {T} < min_obs {min_obs}" if K >= 1 else "無候選"
        base["n_obs"] = int(T)
        return base

    diffs = matrix_values - bench[:, None]
    rng = np.random.default_rng(seed)
    boot_means = _circular_block_bootstrap_means(diffs, n_bootstrap, block_len, rng)
    dbar = diffs.mean(axis=0)
    se = np.maximum(boot_means.std(axis=0, ddof=1), _SE_FLOOR)

    spa = _spa_pvalues(dbar, boot_means, se, T)
    spa["best"] = names[int(np.argmax(dbar / se))]
    p_adj = _romano_wolf_adj_p(dbar, boot_means, se)
    reject = p_adj <= alpha
    t_stat = dbar / se
    per = {names[k]: {"pass": bool(reject[k]), "rw_p_adj": float(p_adj[k]),
                      "t_stat": float(t_stat[k]), "mean_excess_daily": float(dbar[k])}
           for k in range(K)}
    base.update({"computed": True, "reason": "", "n_obs": int(T), "spa": spa,
                 "pbo": pbo_cscv(matrix_values, n_blocks_pbo),
                 "per_candidate": per, "n_rejected": int(reject.sum())})
    return base


# ===========================================================================
# 4. permutation / block-bootstrap null(本檔新寫,單一報酬序列)
# ===========================================================================
def permutation_null(returns: np.ndarray, periods_per_year: float,
                     n_perm=2000, block_len=None, seed=20260708) -> dict:
    """對【單一報酬序列】建虛無分布(H0=沒有 edge)並檢定。

    做法:把序列先「去均值」(demean)——保留波動度、偏度、峰度等分布形狀,但抹掉漂移
    (drift),然後 circular block-bootstrap N 次,得到「若真實漂移為零、只剩觀察到的波動」
    這個虛無世界下,靠運氣能刷出多高的年化 Sharpe。再看真實 Sharpe 是否顯著勝過它。

    - block_len=None 時自動取 max(1, round(sqrt(T)))(保守吃掉短程自相關)。
    - null_p95_sharpe = 虛無分布 95 百分位(單邊 5% 門檻)。
    - p_value = 虛無分布中 >= 真 Sharpe 的比例(加一平滑),越低越像真 edge。
    - passes = 真 Sharpe > null p95。
    語意:這是「你的正報酬有沒有可能只是零漂移＋這種波動的隨機產物」的直接檢定。
    """
    r = np.nan_to_num(np.asarray(returns, dtype=float), nan=0.0)
    T = r.size
    apy = float(periods_per_year) if periods_per_year else 252.0
    if T < 8:
        return {"real_sharpe": float("nan"), "null_p95_sharpe": float("nan"),
                "p_value": float("nan"), "passes": False, "n_perm": 0}
    real_sr = _sharpe_periodic(r) * np.sqrt(apy)
    dm = r - r.mean()                        # H0:抹掉漂移,保留波動/形狀
    L = int(block_len) if block_len else int(max(1, round(np.sqrt(T))))
    L = max(1, min(L, T))
    m = int(np.ceil(T / L))
    ext = np.concatenate([dm, dm[:L - 1]]) if L > 1 else dm
    c = np.concatenate([[0.0], np.cumsum(ext)])
    csq = np.concatenate([[0.0], np.cumsum(ext ** 2)])
    sum_l = c[L:L + T] - c[:T]
    sumsq_l = csq[L:L + T] - csq[:T]
    rng = np.random.default_rng(seed)
    null = np.empty(n_perm, dtype=float)
    for i in range(n_perm):
        starts = rng.integers(0, T, size=m)
        tot = float(sum_l[starts].sum())
        totsq = float(sumsq_l[starts].sum())
        nn = m * L
        mean = tot / nn
        var = totsq / nn - mean ** 2
        sd = np.sqrt(var) if var > 0 else 0.0
        null[i] = (mean / sd) * np.sqrt(apy) if sd > 0 else 0.0
    p95 = float(np.percentile(null, 95))
    p_value = float((1 + int(np.sum(null >= real_sr))) / (n_perm + 1))
    return {"real_sharpe": float(real_sr), "null_p95_sharpe": p95,
            "p_value": p_value, "passes": bool(real_sr > p95), "n_perm": int(n_perm)}


# ===========================================================================
# 5. 成本壓力(round-trip bps,精神取 factor_harvest_mvp.CostModel)
# ===========================================================================
def cost_stress(returns: np.ndarray, turnover: np.ndarray, cost_bps: float,
                periods_per_year: float) -> dict:
    """每期扣 turnover_t × (cost_bps/1e4) × mult,看 ×1/×3/×6 後年化 Sharpe。
    cost_bps 為「每 1.0 單位換手的 round-trip 成本(bps)」。"""
    r = np.nan_to_num(np.asarray(returns, dtype=float), nan=0.0)
    tn = np.nan_to_num(np.asarray(turnover, dtype=float), nan=0.0)
    apy = float(periods_per_year) if periods_per_year else 252.0
    n = min(r.size, tn.size)
    r, tn = r[:n], tn[:n]
    base_cost = tn * (cost_bps / 1e4)
    out = {}
    for mult, key in ((1.0, "x1_sharpe"), (3.0, "x3_sharpe"), (6.0, "x6_sharpe")):
        net = r - base_cost * mult
        out[key] = _sharpe_periodic(net) * np.sqrt(apy)
    return {k: float(v) for k, v in out.items()}


# ===========================================================================
# 6. 綜合裁決(誠實、保守)
# ===========================================================================
def _verdict(signals: dict) -> dict:
    """收集所有訊號 → overall / score_0to100 / reasons(人話)/ red_flags。

    回傳同時帶【結構化 reason codes】(reasons_coded / red_flags_coded):
    每項 {"code": str, "params": dict},與 reasons / red_flags 逐位 1:1 對應。
    zh 字串維持不變(向後相容);前端 EN 模式用 code+params 渲染英文模板。
    純加欄,不改任何既有值。"""
    reasons, red_flags = [], []
    reasons_coded, red_flags_coded = [], []

    def _reason(zh: str, code: str, **params):
        reasons.append(zh)
        reasons_coded.append({"code": code, "params": params})

    def _flag(zh: str, code: str, **params):
        red_flags.append(zh)
        red_flags_coded.append({"code": code, "params": params})

    score = 50.0

    dsr = signals.get("dsr_prob")
    pbo = signals.get("pbo")
    perm_p = signals.get("perm_p")
    perm_pass = signals.get("perm_pass")
    cost3 = signals.get("cost3_sharpe")
    beats_bench = signals.get("beats_bench")
    excess_cagr = signals.get("excess_cagr")
    conc = signals.get("concentration")
    n_trials = signals.get("n_trials", 1)
    n_periods = signals.get("n_periods", 0)
    real_sharpe = signals.get("real_sharpe")
    capped_escape = signals.get("capped_escape", False)

    overfit = False
    real = True  # 先假設 real,逐條扣

    # --- DSR ---
    if dsr is not None and np.isfinite(dsr):
        if dsr >= 0.95:
            _reason(f"通縮夏普(DSR)={dsr:.2f}:扣掉試了 {n_trials} 種參數的多重檢定後,真實有 edge 的機率仍高。",
                    "dsr_high_confidence", dsr=float(dsr), n_trials=int(n_trials))
            score += 18
        elif dsr >= 0.60:
            _reason(f"通縮夏普(DSR)={dsr:.2f}:過了雜訊地板(0.60),但信心非頂級——別當鐵板。",
                    "dsr_above_noise_floor", dsr=float(dsr))
            score += 6
        else:
            _reason(f"通縮夏普(DSR)={dsr:.2f} < 0.60:扣掉多重檢定後,真有 edge 的機率不到一半,高度疑似雜訊。",
                    "dsr_below_noise_floor", dsr=float(dsr))
            _flag(f"DSR={dsr:.2f} 低於雜訊地板 0.60。", "dsr_below_noise_floor", dsr=float(dsr))
            score -= 22
            overfit = True
            real = False
    else:
        _reason("DSR 無法計算(樣本或變異不足),不納入判斷。", "dsr_not_computable")

    # --- 封頂逃逸 fail-closed(★R5:雜訊硬化封頂缺口的收口★)---
    #   matrix 硬化已把 n_trials 放大到封頂上限(32×欄數),DSR 仍撐在雜訊地板(0.60)之上。
    #   這【不是】edge 的證據,而是「欄數過多/樣本過短,通縮上限吃不掉這個贏家」的病態——
    #   高欄數×極短樣本下,一次幸運雜訊抽樣也能撐住 DSR。誠實 fail-closed:不判 likely-real。
    if capped_escape:
        _reason(
            f"雜訊硬化已把試驗數放大到封頂({n_trials}),通縮夏普仍未跌破雜訊地板(0.60)——"
            "這通常代表【欄數過多/樣本過短】,統計上無法把這個樣本內贏家與一次幸運的雜訊抽樣區分開。"
            "本引擎採保守 fail-closed:無法排除過擬合,不判定為真。",
            "capped_escape_fail_closed", n_trials=int(n_trials))
        _flag("封頂逃逸:試驗數放大到上限後 DSR 仍站在雜訊地板上,欄數過多/樣本過短無法排除過擬合。",
              "capped_escape")
        score -= 14
        real = False  # 誠實降級:至少不 likely-real(視其他訊號落在 inconclusive/overfit)

    # --- PBO ---
    if pbo is not None and np.isfinite(pbo):
        if pbo > 0.5:
            _reason(f"回測過配機率(PBO)={pbo:.2f} > 0.50:樣本內選到的最佳者,樣本外多半墊底——典型過擬合特徵。",
                    "pbo_high", pbo=float(pbo))
            _flag(f"PBO={pbo:.2f} > 0.50。", "pbo_high", pbo=float(pbo))
            score -= 20
            overfit = True
            real = False
        else:
            _reason(f"回測過配機率(PBO)={pbo:.2f} ≤ 0.50:樣本內贏家在樣本外沒有系統性崩盤。",
                    "pbo_ok", pbo=float(pbo))
            score += 10
    # returns 模式無 PBO,不扣分

    # --- permutation null ---
    if perm_p is not None and np.isfinite(perm_p):
        if perm_p > 0.5:
            _reason(f"隨機重排檢定 p={perm_p:.2f} > 0.50:把報酬時序打亂後,一半以上的隨機版本夏普不輸真實——沒看到真訊號。",
                    "perm_noise", p=float(perm_p))
            _flag(f"permutation p={perm_p:.2f} > 0.50。", "perm_noise", p=float(perm_p))
            score -= 18
            overfit = True
            real = False
        elif perm_pass:
            _reason(f"隨機重排檢定 p={perm_p:.2f}:真實夏普勝過 95% 的隨機打亂版本,時序結構帶了資訊。",
                    "perm_pass", p=float(perm_p))
            score += 14
        else:
            _reason(f"隨機重排檢定 p={perm_p:.2f}:未勝過隨機 p95 門檻,訊號不夠乾淨。",
                    "perm_below_threshold", p=float(perm_p))
            score -= 4
            real = False

    # --- 成本 ×3 ---
    if cost3 is not None and np.isfinite(cost3):
        if cost3 > 0:
            _reason(f"成本壓力 ×3 後夏普={cost3:.2f} 仍為正:對交易成本有一定緩衝。",
                    "cost_x3_positive", sharpe=float(cost3))
            score += 8
        else:
            _reason(f"成本壓力 ×3 後夏普={cost3:.2f} ≤ 0:成本稍微保守就翻負,edge 恐被摩擦吃光。",
                    "cost_x3_negative", sharpe=float(cost3))
            _flag("成本 ×3 後夏普轉負。", "cost_x3_negative")
            score -= 12
            real = False

    # --- 贏基準 ---
    if beats_bench is not None:
        has_xc = excess_cagr is not None and np.isfinite(excess_cagr)
        if beats_bench:
            xc = f"(超額 CAGR {excess_cagr*100:+.1f}%)" if has_xc else ""
            _reason(f"夏普勝過基準{xc}:相對買進持有有加值。",
                    "bench_beaten", excess_cagr=(float(excess_cagr) if has_xc else None))
            score += 8
        else:
            _reason("夏普未勝過基準:相對買進持有沒有明顯優勢,先確認是否值得多做這些交易。",
                    "bench_not_beaten")
            score -= 8
            real = False

    # --- 集中度 ---
    if conc is not None and np.isfinite(conc):
        if conc >= 0.40:
            _reason(f"報酬集中度={conc*100:.0f}%:最好那一期就貢獻近半以上報酬,績效靠少數暴衝,脆弱。",
                    "concentration_high", concentration=float(conc))
            _flag(f"單期集中度 {conc*100:.0f}% ≥ 40%。", "concentration_high", concentration=float(conc))
            score -= 12
            real = False
        else:
            _reason(f"報酬集中度={conc*100:.0f}%:報酬分布相對均勻,不靠少數幾根。",
                    "concentration_ok", concentration=float(conc))
            score += 4

    # --- n_trials 懲罰 ---
    if n_trials and n_trials > 20:
        pen = min(15.0, 3.0 * np.log10(n_trials))
        _reason(f"你試了 {n_trials} 種參數:試越多,靠運氣撞到漂亮結果的機率越高,已按此扣分。",
                "many_trials_penalty", n_trials=int(n_trials))
        score -= pen
    elif n_trials and n_trials > 1:
        _reason(f"你試了 {n_trials} 種參數:已納入多重檢定校正(DSR)。",
                "trials_corrected", n_trials=int(n_trials))

    # --- 樣本量提醒 ---
    if n_periods and n_periods < 60:
        _reason(f"樣本只有 {n_periods} 期:統計檢定力弱,任何結論都要打折看待。",
                "sample_short", n_periods=int(n_periods))
        _flag(f"樣本過短(僅 {n_periods} 期)。", "sample_short", n_periods=int(n_periods))
        score -= 8

    # --- 綜合判定 ---
    score = float(np.clip(score, 0, 100))
    if overfit:
        overall = "likely-overfit"
    elif real and score >= 62:
        overall = "likely-real"
    else:
        overall = "inconclusive"

    # 誠實收尾:過關不等於會賺
    if overall == "likely-real":
        _reason("重要:通過這些檢定只代表『沒發現明顯的過度擬合』,不保證未來會賺——真金白銀前請務必前向(walk-forward)驗證與小額實測。",
                "closing_likely_real")
    elif overall == "inconclusive":
        _reason("結論不明:證據不足以判定真偽,建議補更多樣本或做前向測試再定奪。",
                "closing_inconclusive")
    else:
        _reason("提醒:即使某些指標好看,上述紅旗顯示這條策略八成是過擬合的產物,別下真錢。",
                "closing_likely_overfit")

    return {"overall": overall, "score_0to100": round(score, 1),
            "reasons": reasons, "red_flags": red_flags,
            "reasons_coded": reasons_coded, "red_flags_coded": red_flags_coded}


# ===========================================================================
# 7. 統一入口
# ===========================================================================
def _infer_ppy(dates, fallback):
    if fallback:
        return float(fallback)
    if dates and len(dates) > 2:
        try:
            idx = pd.to_datetime(pd.Series(dates))
            total_days = (idx.iloc[-1] - idx.iloc[0]).total_seconds() / 86400.0
            steps = len(idx) - 1
            if total_days > 0 and steps > 0:
                # 用「總span/步數」= 平均每步天數(含週末缺口),日資料 → ~1.4 天/步 → ~260/年,
                # 週資料 → 7 → ~52,月 → ~30 → ~12。比 median 誠實(median 會把週末缺口洗掉)。
                avg_days = total_days / steps
                if avg_days < 0.5:
                    # 日內資料(平均步距 < 半天):ppy = 每日 bar 數 × 每年交易日數,兩者皆由
                    # 資料自己誠實推——bar/日 = 總 bar 數 / 有 bar 的日曆日數;交易日/年 =
                    # 365 × 有 bar 日數 / 日曆 span(台股週一~五 → ~250-260;加密 24/7 → 365)。
                    # 台股 1 分 ≈ 266 bar × ~250 日 ≈ 66k;加密 1m = 1440 × 365 = 525,600。
                    days = idx.dt.normalize()
                    n_days = int(days.nunique())
                    span_days = float((days.iloc[-1] - days.iloc[0]).days) + 1.0
                    bars_per_day = len(idx) / max(n_days, 1)
                    tdays_per_year = 365.0 * n_days / span_days if span_days > 0 else 252.0
                    return float(np.clip(round(bars_per_day * tdays_per_year), 1, 600000))
                return float(np.clip(round(365.0 / avg_days), 1, 35040))
        except Exception:
            pass
    return 252.0


def analyze(payload: dict) -> dict:
    """統一裁判入口。契約見模組 docstring / README。純函數。

    warnings_coded:與 warnings 逐位 1:1 對應的結構化 code(加欄向後相容,
    每項 {"code": str, "params": dict});前端 EN 模式用它渲染英文。"""
    warnings = []
    warnings_coded = []

    def _warn(zh: str, code: str, **params):
        warnings.append(zh)
        warnings_coded.append({"code": code, "params": params})

    mode = payload.get("mode", "returns")
    dates = payload.get("dates")
    n_trials = int(payload.get("n_trials") or 1)
    ppy = _infer_ppy(dates, payload.get("periods_per_year"))
    bench_ret = payload.get("benchmark_returns")
    cost_bps = payload.get("cost_bps_per_turnover")
    turnover = payload.get("turnover")

    # ---- 取主報酬序列 ----
    if mode == "matrix":
        matrix = payload.get("matrix") or {}
        if not matrix:
            return {"ok": False, "warnings": ["matrix 模式但 matrix 為空"],
                    "warnings_coded": [{"code": "matrix_empty", "params": {}}],
                    "metrics": {}, "verdict": {"overall": "inconclusive",
                    "score_0to100": 0, "reasons": ["無資料"], "red_flags": [],
                    "reasons_coded": [{"code": "no_data", "params": {}}],
                    "red_flags_coded": []}}
        names = list(matrix.keys())
        cols = [np.asarray(matrix[k], dtype=float) for k in names]
        T = min(len(c) for c in cols)
        cols = [c[:T] for c in cols]
        mat = np.column_stack(cols)
        mat = np.nan_to_num(mat, nan=0.0)
        # 主序列 = matrix 內每期 Sharpe 最高者(使用者最可能上手的贏家)
        col_sr = np.array([_sharpe_periodic(mat[:, k]) for k in range(mat.shape[1])])
        best_k = int(np.nanargmax(col_sr)) if col_sr.size else 0
        returns = mat[:, best_k]
        trial_sharpes = col_sr
        # ★防雜訊放水:誠實基準 = max(使用者宣稱 n_trials, 欄數),再由 harden 決定有效 n_trials★
        #   達真實信心地板(DSR≥0.95)的真 edge 維持誠實 n_trials;未達地板的最佳欄(與一次
        #   幸運雜訊抽樣不可區分)則上調 n_trials 到 DSR 跌破雜訊地板(0.60),不判為 likely-real。
        #   engine 內建此硬化 → 公開站(app.js 送 mode=matrix)與本地版共用同一判準,不分叉。
        honest_base = max(int(n_trials), len(names))
        n_trials, harden_diag = harden_matrix_n_trials(returns, trial_sharpes, honest_base)
        # ★誠實揭露(R5):高欄數×極短樣本下,即便誠實基準的 DSR≥0.95 也不可信——
        #   一次幸運的雜訊贏家與真 edge 在此樣本長度下【指紋一致】(DSR/permutation/PBO/集中度
        #   全重疊),任何裁判都無法區分。此時 harden 不硬化(已達地板),但 DSR≥0.95 這個「真」
        #   的結論其實踩在檢定力懸崖上。不動 verdict(避免誤殺真 edge),只加警語照實告知。
        if (harden_diag and not harden_diag["hardened"] and harden_diag["dsr_at_base"] is not None
                and harden_diag["dsr_at_base"] >= REAL_CONF_BAR and len(names) >= T):
            _warn(
                f"注意:你在 {T} 期樣本上搜尋了 {len(names)} 欄(欄數 ≥ 樣本期數)。此時即使通縮夏普"
                "(DSR)看似達標,一次幸運的雜訊贏家與真 edge 在統計上難以區分——DSR「達標」的結論"
                "檢定力薄弱,務必以更長樣本或前向(walk-forward)測試複核,別直接當真。",
                "high_dim_low_power", n_cols=int(len(names)), n_periods=int(T))
    else:
        returns = np.nan_to_num(np.asarray(payload.get("returns") or [], dtype=float), nan=0.0)
        if returns.size == 0:
            return {"ok": False, "warnings": ["returns 模式但 returns 為空"],
                    "warnings_coded": [{"code": "returns_empty", "params": {}}],
                    "metrics": {}, "verdict": {"overall": "inconclusive",
                    "score_0to100": 0, "reasons": ["無資料"], "red_flags": [],
                    "reasons_coded": [{"code": "no_data", "params": {}}],
                    "red_flags_coded": []}}
        names, mat = None, None
        trial_sharpes = np.array([_sharpe_periodic(returns)])
        harden_diag = None  # returns 模式不硬化(硬化只在 matrix)

    n = returns.size
    if n < 30:
        _warn(f"樣本僅 {n} 期,統計檢定力弱,結論僅供參考。", "short_sample", n=int(n))

    # ---- 長序列效能守衛(日內資料 >50k 期:permutation/bootstrap 在瀏覽器會拖垮)----
    #   誠實揭露:重抽次數降低只讓 p 值解析度變粗(p 的最小刻度 = 1/(n_perm+1)),
    #   不改變檢定本身的語意;有 dates 的日內序列 ppy 已由 _infer_ppy 正確年化。
    n_perm_eff, n_boot_eff = 2000, 1000
    if n > 50000:
        n_perm_eff, n_boot_eff = 500, 200
        _warn(f"長序列({n} 期):permutation 重抽已降至 {n_perm_eff} 次、"
              f"bootstrap 已降至 {n_boot_eff} 次(瀏覽器效能守衛;p 值解析度變粗,檢定語意不變)。",
              "long_series_guard", n=int(n), n_perm=n_perm_eff, n_boot=n_boot_eff)

    # ---- 指標 ----
    metrics = compute_metrics(returns, ppy)

    # ---- DSR ----
    dsr_res = deflated_sharpe(returns, n_trials, trial_sharpes)
    sr_annual = metrics["sharpe"]
    dsr = {"sr_annual": sr_annual, "sr0": float(dsr_res["sr0_daily"] * np.sqrt(ppy)),
           "dsr_prob": dsr_res["dsr"], "p_value": dsr_res["p_value"], "n_trials": n_trials,
           "harden": harden_diag}  # matrix 硬化診斷(returns 模式為 None)

    # ---- permutation null ----
    perm = permutation_null(returns, ppy, n_perm=n_perm_eff)

    # ---- PBO / FWER(僅 matrix)----
    pbo_out = None
    fwer_out = None
    if mode == "matrix" and mat is not None and mat.shape[1] >= 2:
        pbo_out = pbo_cscv(mat, 12)
        bench_arr = (np.nan_to_num(np.asarray(bench_ret, dtype=float), nan=0.0)[:mat.shape[0]]
                     if bench_ret is not None else np.zeros(mat.shape[0]))
        if bench_arr.size < mat.shape[0]:
            bench_arr = np.zeros(mat.shape[0])
        fw = run_fwer_gates(mat, names, bench_arr, n_bootstrap=n_boot_eff, n_blocks_pbo=12)
        fwer_out = {"spa": fw["spa"], "per_candidate": fw["per_candidate"],
                    "n_rejected": fw["n_rejected"]}
        if not fw["computed"]:
            _warn(f"FWER 未計算:{fw.get('reason', '樣本不足')}(需 ≥100 期)。",
                  "fwer_not_computed", n_obs=int(fw.get("n_obs", 0)), min_obs=100)

    # ---- 成本壓力(僅有 turnover)----
    cost_out = None
    if turnover is not None and cost_bps is not None:
        cost_out = cost_stress(returns, turnover, float(cost_bps), ppy)

    # ---- 基準比較 ----
    bench_cmp = None
    if bench_ret is not None:
        b = np.nan_to_num(np.asarray(bench_ret, dtype=float), nan=0.0)[:n]
        if b.size >= 2:
            bm = compute_metrics(b, ppy)
            excess = (metrics["cagr"] - bm["cagr"]) if (np.isfinite(metrics["cagr"]) and np.isfinite(bm["cagr"])) else float("nan")
            beats = (np.isfinite(metrics["sharpe"]) and np.isfinite(bm["sharpe"])
                     and metrics["sharpe"] > bm["sharpe"])
            bench_cmp = {"bench_sharpe": bm["sharpe"], "bench_cagr": bm["cagr"],
                         "excess_cagr": excess, "strategy_beats": bool(beats)}

    # ---- 綜合裁決 ----
    verdict = _verdict({
        "dsr_prob": dsr["dsr_prob"],
        "pbo": pbo_out["pbo"] if pbo_out else None,
        "perm_p": perm["p_value"], "perm_pass": perm["passes"],
        "cost3_sharpe": cost_out["x3_sharpe"] if cost_out else None,
        "beats_bench": bench_cmp["strategy_beats"] if bench_cmp else None,
        "excess_cagr": bench_cmp["excess_cagr"] if bench_cmp else None,
        "concentration": metrics["top_bar_concentration"],
        "n_trials": n_trials, "n_periods": n, "real_sharpe": perm["real_sharpe"],
        "capped_escape": bool(harden_diag["capped_escape"]) if harden_diag else False,
    })

    # ---- equity curves ----
    equity_curve = _equity_from_returns(returns).tolist()
    bench_curve = None
    if bench_cmp is not None:
        b = np.nan_to_num(np.asarray(bench_ret, dtype=float), nan=0.0)[:n]
        bench_curve = _equity_from_returns(b).tolist()

    return {
        "ok": True, "warnings": warnings, "warnings_coded": warnings_coded,
        "metrics": metrics, "dsr": dsr, "permutation_null": perm,
        "pbo": pbo_out, "fwer": fwer_out, "cost_stress": cost_out,
        "benchmark_compare": bench_cmp, "verdict": verdict,
        "equity_curve": equity_curve, "benchmark_curve": bench_curve,
    }
