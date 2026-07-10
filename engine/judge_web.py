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

# ★R17 必修1:缺值 fail-closed 門檻★
#   空白/非數值格以 0 填補會人為壓低波動、抬高夏普(把最差的日子留空即可騙分)。
#   本引擎【絕不填 0】:缺格率 < 門檻 → 整期剔除並告警揭露;≥ 門檻 → 結構化拒審。
#   與前端 parseCSV 同判準,雙層防禦(引擎也被本地 Streamlit 版直呼)。
MISSING_MAX_RATE = 0.05


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
    # R19 必修4:極端量級(如百分比誤當小數 → 單期報酬 500)會讓冪運算 OverflowError
    # 裸崩——改夾成 inf(數學上就是天文數字),讓 analyze 的 extreme_returns 警語接手。
    if years > 0 and final_equity > 0:
        try:
            cagr = final_equity ** (1.0 / years) - 1.0
        except OverflowError:
            cagr = float("inf")
    else:
        cagr = float("nan")

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
    """單條策略每期報酬的 DSR。trial_sharpes=試過各參數的每期 Sharpe 池(matrix 模式才有真池)。

    試驗離散度(across-trial variance,Bailey & López de Prado 的 E[max SR] 需要它)取法:
    - 池內 ≥2 條:用真試驗池的樣本變異(matrix 模式,原式)。
    - 池內 <2 條且 n_trials>1(returns 模式:宣稱試過 N 組參數但只上傳最後一條,無真池):
      用【SR 估計標準誤】SE(SR)=sqrt((1+SR²/2)/n) 的平方當試驗離散度的保守下限做真通縮,
      並以 sr_variance_proxy=True 誠實揭露這是 proxy。真試驗池的離散度通常 ≥ 單條 SR 的
      抽樣誤差,故此通縮是下限、偏溫和不偏嚴。修掉舊 bug:「單序列池 variance=0 →
      E[max SR]≡0 → n_trials 完全無效」的假通縮(UI 卻宣稱已扣 N 次試驗的運氣)。
    - n_trials=1:不通縮(sr0=0),行為與舊版逐位相同。
    回傳每期尺度 sr / sr0 / dsr_prob / p_value / sr_variance_proxy。"""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    std = r.std(ddof=1) if r.size > 1 else 0.0
    sr = float(r.mean() / std) if std > 0 else 0.0
    pool = np.asarray(trial_sharpes, dtype=float)
    pool = pool[np.isfinite(pool)]
    variance_proxy = False
    if pool.size > 1:
        sr_variance = float(np.var(pool, ddof=1))
    elif int(n_trials) > 1:
        n_eff = max(int(r.size), 2)
        sr_variance = float((1.0 + 0.5 * sr * sr) / n_eff)  # SE(SR)² 保守下限 proxy
        variance_proxy = True
    else:
        sr_variance = 0.0
    sr0 = expected_max_sharpe(sr_variance, n_trials)
    dsr = probabilistic_sharpe(
        sr_hat=sr, sr_benchmark=sr0, n_obs=r.size,
        skew=ss.skew(r) if r.size > 2 else 0.0,
        kurt=ss.kurtosis(r, fisher=False) if r.size > 2 else 3.0,
    )
    return {"sr_daily": sr, "sr0_daily": sr0, "dsr": dsr,
            "p_value": (1.0 - dsr) if np.isfinite(dsr) else float("nan"),
            "n_trials": int(n_trials), "sr_variance_proxy": variance_proxy}


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
#
#    ★R15 校準手術★:R14 panel 實測舊版(block_len=10 固定 × 以 bootstrap se 做
#    studentize)在純雜訊下反保守 ~2 倍(名目 10% FWER 實測 19.0%、SPA p<0.05 打
#    12.0%;200 sims × K=20 × T=250)。根因兩個,都修:
#    (a) studentizer 用了【噪聲大的 bootstrap se】:tb=(boot-dbar)/se 依構造恆為單位
#        變異,但 t_obs=dbar/se 的真實尺度隨 se 的估計噪聲(block 越長噪聲越大,L=10
#        時 ~10%+)亂跳,max-of-K 專挑「se 偶然偏小」的候選 → 觀測 max 系統性膨脹。
#        修:studentizer 改用低噪聲的 iid se(s_k/√T,ddof=1);序列相關仍由 block
#        bootstrap 的【null 分布形狀】承擔(兩側同除一把尺,尺度自然抵銷,校準不破)。
#    (b) CBB 變異數估計的有限樣本下偏:E[Var*] ≈ (σ²/T)(1-L/T) → null 分布偏瘦。
#        修:中心化後的 bootstrap 離差乘 1/√(1-L/T) 校正。
#    另 block_len 改自適應(簡化 Politis-White:T^(1/3) × AR(1) plug-in 因子),
#    iid 資料取短 block(估計噪聲小)、序列相關資料自動加長。
#    修後實測數字見 run_fwer_gates docstring(名目 10% → 9.9%、名目 5% → 4.5%)。
# ===========================================================================
def _auto_block_len(diffs: np.ndarray) -> int:
    """自適應 block 長度(簡化 Politis-White):L = T^(1/3) × ((1+ρ)/(1-ρ))^(2/3),
    ρ = 各候選 lag-1 自相關的中位數(截尾到 [0, 0.8])。iid 資料 → ρ≈0 → L≈T^(1/3)
    (短 block、估計噪聲小);序列相關資料 → block 自動加長吃掉相依。clip 到 [2, T/10]。"""
    T = diffs.shape[0]
    x = diffs - diffs.mean(axis=0)
    den = (x * x).sum(axis=0)
    num = (x[1:] * x[:-1]).sum(axis=0)
    rho = np.where(den > _SE_FLOOR, num / np.maximum(den, _SE_FLOOR), 0.0)
    r = float(np.clip(np.median(rho), 0.0, 0.8))
    L = (T ** (1.0 / 3.0)) * (((1.0 + r) / (1.0 - r)) ** (2.0 / 3.0))
    return int(np.clip(int(round(L)), 2, max(2, T // 10)))


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


def _spa_pvalues(dbar: np.ndarray, boot_means: np.ndarray, se: np.ndarray, T: int,
                 se_null: np.ndarray = None) -> dict:
    """Hansen SPA p 值。se=studentizer(R15 後為低噪 iid se);se_null=dbar 抽樣 sd 的
    估計(R15 後為偏差校正過的 bootstrap sd),只用在 mu_c 重心化門檻——該門檻的語意是
    「輸到統計上顯著才重心化」,得用 dbar 的真實尺度,不能用 studentizer 尺度。"""
    if se_null is None:
        se_null = se
    t_stat = dbar / se
    t_obs = float(max(0.0, t_stat.max()))
    loglog = float(np.sqrt(2.0 * np.log(np.log(T)))) if T > 15 else 0.0
    mu_c = np.where(dbar <= -se_null * loglog, dbar, 0.0)
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
                   alpha=0.10, n_bootstrap=1000, block_len=None, seed=20260707,
                   min_obs=100, n_blocks_pbo=12) -> dict:
    """對一批候選跑 Romano-Wolf(逐候選)+ SPA + PBO(套件級)。matrix_values=(T,K),
    bench=(T,) 同期基準報酬。缺基準時傳 0 序列(=絕對報酬 vs 0)。

    block_len=None(預設)→ 自適應(_auto_block_len);給定數字則照用(重現舊行為用)。

    ★R15 校準(見第 3 節節首註解)★:studentizer 改低噪 iid se、CBB 離差乘
    1/√(1-L/T) 校正、block 長自適應。修後實測(校準實驗,seed 可重現):
      純雜訊 1000 sims × K=20 × T=250:名目 10% RW FWER → 實測 9.9%(修前 19.0%);
        名目 5% SPA → 實測 4.5%(修前 12.0%)。
      T=120:9.8%/5.0%;K=50:8.6%/4.4%;AR(1)=0.15:11.2%/5.9%;
      AR(1)=0.30:12.5%/7.5%(block bootstrap 有限 T 固有殘餘,強序列相關下仍偏鬆
        ~1-3pp——展示層文案據此只講「已校準(誤差 ±3pp 內)」,不宣稱精確)。
      檢力保留:植入年化夏普 ~3.2 真 edge → 77.6% 偵測(500 sims)。
    回傳 base["calibration"] 揭露所用 block_len / 校正因子 / studentizer。"""
    K = matrix_values.shape[1]
    base = {"computed": False, "alpha": alpha, "n_candidates": K,
            "per_candidate": {}, "n_rejected": 0,
            "spa": {"p_value": float("nan"), "p_lower": float("nan"),
                    "p_upper": float("nan"), "t_max": float("nan"), "best": None},
            "pbo": {"pbo": float("nan"), "n_combinations": 0, "n_strategies": K},
            "calibration": None}
    T = matrix_values.shape[0]
    if K < 1 or T < min_obs:
        base["reason"] = f"對齊後樣本 {T} < min_obs {min_obs}" if K >= 1 else "無候選"
        base["n_obs"] = int(T)
        return base

    diffs = matrix_values - bench[:, None]
    L = int(block_len) if block_len else _auto_block_len(diffs)
    L = max(1, min(L, T))
    rng = np.random.default_rng(seed)
    boot_means = _circular_block_bootstrap_means(diffs, n_bootstrap, L, rng)
    dbar = diffs.mean(axis=0)
    # (b) CBB 變異數有限樣本下偏校正:E[Var*]≈(σ²/T)(1-L/T) → 離差乘 1/√(1-L/T)。
    spread_corr = float(1.0 / np.sqrt(max(1.0 - L / T, 0.25)))
    boot_means = dbar + (boot_means - dbar) * spread_corr
    # (a) studentizer:低噪 iid se(兩側同除,序列相關由 bootstrap null 形狀承擔)。
    se = np.maximum(diffs.std(axis=0, ddof=1) / np.sqrt(T), _SE_FLOOR)
    # dbar 抽樣 sd 的最佳估計(校正後 bootstrap sd)→ 只給 SPA 的 mu_c 重心化門檻。
    se_null = np.maximum(boot_means.std(axis=0, ddof=1), _SE_FLOOR)

    spa = _spa_pvalues(dbar, boot_means, se, T, se_null=se_null)
    spa["best"] = names[int(np.argmax(dbar / se))]
    p_adj = _romano_wolf_adj_p(dbar, boot_means, se)
    reject = p_adj <= alpha
    t_stat = dbar / se
    per = {names[k]: {"pass": bool(reject[k]), "rw_p_adj": float(p_adj[k]),
                      "t_stat": float(t_stat[k]), "mean_excess_daily": float(dbar[k])}
           for k in range(K)}
    base.update({"computed": True, "reason": "", "n_obs": int(T), "spa": spa,
                 "pbo": pbo_cscv(matrix_values, n_blocks_pbo),
                 "per_candidate": per, "n_rejected": int(reject.sum()),
                 "calibration": {"block_len": int(L), "spread_corr": spread_corr,
                                 "studentizer": "iid_se", "n_bootstrap": int(n_bootstrap)}})
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
    cost_bps 為「每 1.0 單位換手的 round-trip 成本(bps)」。

    R19 必修3:非有限的 returns/turnover【不再填 0】——turnover 填 0 會低估成本、
    returns 填 0 會壓低波動,皆屬 fail-open。改為逐期【剔除】非有限對;analyze 端另有
    「非有限率 ≥5% → 跳過本閘」的守衛與揭露,本函數是直呼方的最後防線。"""
    r = np.asarray(returns, dtype=float)
    tn = np.asarray(turnover, dtype=float)
    apy = float(periods_per_year) if periods_per_year else 252.0
    n = min(r.size, tn.size)
    r, tn = r[:n], tn[:n]
    fin = np.isfinite(r) & np.isfinite(tn) & (tn >= 0.0)  # 負換手=倒貼成本,一併剔除
    r, tn = r[fin], tn[fin]
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
    # score_breakdown:各閘加減分逐項揭露(R12 修:score 組成不再是黑箱)。
    # 首項固定 base=50;之後每次加減分都記一筆 {code, delta},code 與 reasons_coded 對齊。
    # 不變式:clip(Σdelta, 0, 100) == score_0to100(測試釘死)。純加欄,向後相容。
    score_breakdown = [{"code": "base", "delta": 50.0}]

    def _bump(code: str, delta: float):
        nonlocal score
        score += delta
        score_breakdown.append({"code": code, "delta": float(delta)})

    dsr = signals.get("dsr_prob")
    pbo = signals.get("pbo")
    perm_p = signals.get("perm_p")
    perm_pass = signals.get("perm_pass")
    cost3 = signals.get("cost3_sharpe")
    beats_bench = signals.get("beats_bench")
    excess_cagr = signals.get("excess_cagr")
    conc = signals.get("concentration")
    n_trials = signals.get("n_trials", 1)
    # R17 必修2:申報 vs 採用分開帶。n_trials=引擎實際用於通縮的試驗數(matrix 模式可能
    # 被欄數地板/雜訊硬化保守上調);n_trials_declared=使用者申報值。措辭據此誠實分流,
    # 不再把硬化後的數字冒充成「你試了 N 種」。
    n_trials_declared = signals.get("n_trials_declared")
    trials_uplifted = (n_trials_declared is not None
                       and int(n_trials_declared) < int(n_trials or 1))
    n_periods = signals.get("n_periods", 0)
    real_sharpe = signals.get("real_sharpe")
    capped_escape = signals.get("capped_escape", False)
    dsr_var_proxy = bool(signals.get("dsr_var_proxy", False))
    span_years = signals.get("calendar_span_years")

    overfit = False
    real = True  # 先假設 real,逐條扣

    # --- DSR ---
    # dsr_var_proxy=True(returns 模式、n_trials>1、無真試驗池)時文案與數學一致:
    # 通縮用的是「以 SR 估計標準誤為試驗離散度下限」的保守 proxy,照實講,不假稱有真試驗池。
    # 非 proxy 路徑(n_trials=1 或 matrix 真池)的 zh 文案與 params 逐位維持舊版不變。
    if dsr is not None and np.isfinite(dsr):
        if dsr >= 0.95:
            if dsr_var_proxy:
                _reason(f"通縮夏普(DSR)={dsr:.2f}:單一序列沒有真試驗池,以 SR 估計標準誤作為"
                        f"試驗離散度的保守下限、對「試了 {n_trials} 種參數」做真通縮後,真實有 edge "
                        "的機率仍高(離散度為保守 proxy,非真試驗池)。",
                        "dsr_high_confidence", dsr=float(dsr), n_trials=int(n_trials),
                        var_proxy=True)
            elif int(n_trials or 1) <= 1:
                # R19 必修5c:n_trials=1 無多重檢定可扣——「扣掉試了 1 種參數的多重檢定」
                # 是機翻感的假話(沒有通縮發生),zh/en 都走專用措辭。
                _reason(f"通縮夏普(DSR)={dsr:.2f}:你未申報參數搜尋(n_trials=1),無多重"
                        "檢定通縮可扣;以單曲線口徑檢定,真實有 edge 的機率高。",
                        "dsr_high_confidence", dsr=float(dsr), n_trials=1)
            else:
                _reason(f"通縮夏普(DSR)={dsr:.2f}:扣掉試了 {n_trials} 種參數的多重檢定後,真實有 edge 的機率仍高。",
                        "dsr_high_confidence", dsr=float(dsr), n_trials=int(n_trials))
            _bump("dsr_high_confidence", 18.0)
        elif dsr >= 0.60:
            if dsr_var_proxy:
                _reason(f"通縮夏普(DSR)={dsr:.2f}:以 SR 標準誤為試驗離散度的保守通縮"
                        f"(單序列無真試驗池,試驗數={n_trials})後過了雜訊地板(0.60),"
                        "但信心非頂級——別當鐵板。",
                        "dsr_above_noise_floor", dsr=float(dsr), n_trials=int(n_trials),
                        var_proxy=True)
            else:
                _reason(f"通縮夏普(DSR)={dsr:.2f}:過了雜訊地板(0.60),但信心非頂級——別當鐵板。",
                        "dsr_above_noise_floor", dsr=float(dsr))
            _bump("dsr_above_noise_floor", 6.0)
        else:
            if dsr_var_proxy:
                _reason(f"通縮夏普(DSR)={dsr:.2f} < 0.60:以 SR 標準誤為試驗離散度、通縮"
                        f"「試了 {n_trials} 種參數」的運氣後,真有 edge 的機率不到一半,高度疑似雜訊。",
                        "dsr_below_noise_floor", dsr=float(dsr), n_trials=int(n_trials),
                        var_proxy=True)
                _flag(f"DSR={dsr:.2f} 低於雜訊地板 0.60。", "dsr_below_noise_floor",
                      dsr=float(dsr), var_proxy=True)
            elif int(n_trials or 1) <= 1:
                # R19 必修5c:n_trials=1 不存在多重檢定——「扣掉多重檢定後」措辭分流。
                _reason(f"通縮夏普(DSR)={dsr:.2f} < 0.60:即使未申報參數搜尋(n_trials=1,"
                        "無多重檢定通縮),真有 edge 的機率仍不到一半,高度疑似雜訊。",
                        "dsr_below_noise_floor", dsr=float(dsr), n_trials=1)
                _flag(f"DSR={dsr:.2f} 低於雜訊地板 0.60。", "dsr_below_noise_floor", dsr=float(dsr))
            else:
                _reason(f"通縮夏普(DSR)={dsr:.2f} < 0.60:扣掉多重檢定後,真有 edge 的機率不到一半,高度疑似雜訊。",
                        "dsr_below_noise_floor", dsr=float(dsr), n_trials=int(n_trials))
                _flag(f"DSR={dsr:.2f} 低於雜訊地板 0.60。", "dsr_below_noise_floor", dsr=float(dsr))
            _bump("dsr_below_noise_floor", -22.0)
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
        _bump("capped_escape_fail_closed", -14.0)
        real = False  # 誠實降級:至少不 likely-real(視其他訊號落在 inconclusive/overfit)

    # --- PBO ---
    if pbo is not None and np.isfinite(pbo):
        if pbo > 0.5:
            _reason(f"回測過配機率(PBO)={pbo:.2f} > 0.50:樣本內選到的最佳者,樣本外多半墊底——典型過擬合特徵。",
                    "pbo_high", pbo=float(pbo))
            _flag(f"PBO={pbo:.2f} > 0.50。", "pbo_high", pbo=float(pbo))
            _bump("pbo_high", -20.0)
            overfit = True
            real = False
        else:
            _reason(f"回測過配機率(PBO)={pbo:.2f} ≤ 0.50:樣本內贏家在樣本外沒有系統性崩盤。",
                    "pbo_ok", pbo=float(pbo))
            _bump("pbo_ok", 10.0)
    # returns 模式無 PBO,不扣分

    # --- permutation null ---
    if perm_p is not None and np.isfinite(perm_p):
        if perm_p > 0.5:
            _reason(f"隨機重排檢定 p={perm_p:.2f} > 0.50:把報酬時序打亂後,一半以上的隨機版本夏普不輸真實——沒看到真訊號。",
                    "perm_noise", p=float(perm_p))
            _flag(f"permutation p={perm_p:.2f} > 0.50。", "perm_noise", p=float(perm_p))
            _bump("perm_noise", -18.0)
            overfit = True
            real = False
        elif perm_pass:
            _reason(f"隨機重排檢定 p={perm_p:.2f}:真實夏普勝過 95% 的隨機打亂版本,時序結構帶了資訊。",
                    "perm_pass", p=float(perm_p))
            _bump("perm_pass", 14.0)
        else:
            _reason(f"隨機重排檢定 p={perm_p:.2f}:未勝過隨機 p95 門檻,訊號不夠乾淨。",
                    "perm_below_threshold", p=float(perm_p))
            _bump("perm_below_threshold", -4.0)
            real = False

    # --- 成本 ×3 ---
    if cost3 is not None and np.isfinite(cost3):
        if cost3 > 0:
            _reason(f"成本壓力 ×3 後夏普={cost3:.2f} 仍為正:對交易成本有一定緩衝。",
                    "cost_x3_positive", sharpe=float(cost3))
            _bump("cost_x3_positive", 8.0)
        else:
            _reason(f"成本壓力 ×3 後夏普={cost3:.2f} ≤ 0:成本稍微保守就翻負,edge 恐被摩擦吃光。",
                    "cost_x3_negative", sharpe=float(cost3))
            _flag("成本 ×3 後夏普轉負。", "cost_x3_negative")
            _bump("cost_x3_negative", -12.0)
            real = False

    # --- 贏基準 ---
    if beats_bench is not None:
        has_xc = excess_cagr is not None and np.isfinite(excess_cagr)
        if beats_bench:
            xc = f"(超額 CAGR {excess_cagr*100:+.1f}%)" if has_xc else ""
            _reason(f"夏普勝過基準{xc}:相對買進持有有加值。",
                    "bench_beaten", excess_cagr=(float(excess_cagr) if has_xc else None))
            _bump("bench_beaten", 8.0)
        else:
            _reason("夏普未勝過基準:相對買進持有沒有明顯優勢,先確認是否值得多做這些交易。",
                    "bench_not_beaten")
            _bump("bench_not_beaten", -8.0)
            real = False

    # --- 集中度 ---
    if conc is not None and np.isfinite(conc):
        if conc >= 0.40:
            _reason(f"報酬集中度={conc*100:.0f}%:最好那一期就貢獻近半以上報酬,績效靠少數暴衝,脆弱。",
                    "concentration_high", concentration=float(conc))
            _flag(f"單期集中度 {conc*100:.0f}% ≥ 40%。", "concentration_high", concentration=float(conc))
            _bump("concentration_high", -12.0)
            real = False
        else:
            _reason(f"報酬集中度={conc*100:.0f}%:報酬分布相對均勻,不靠少數幾根。",
                    "concentration_ok", concentration=float(conc))
            _bump("concentration_ok", 4.0)

    # --- n_trials 懲罰 ---
    # R17 必修2:硬化/欄數地板上調時,不再說「你試了 N 種」(那是引擎的保守通縮數,
    # 不是使用者的申報)——改講「通縮以 N 種計(你申報 M 種,保守上調)」,與 DSR 卡
    # 「申報 M → 保守上調 N」的揭露一致,不自相矛盾。申報=採用時措辭維持自然。
    if n_trials and n_trials > 20:
        pen = min(15.0, 3.0 * np.log10(n_trials))
        if trials_uplifted:
            _reason(f"通縮以 {n_trials} 種試驗計(你申報 {int(n_trials_declared)} 種,"
                    "引擎依欄數地板/雜訊硬化保守上調):搜尋越廣,靠運氣撞到漂亮結果的機率越高,已按此扣分。",
                    "many_trials_penalty", n_trials=int(n_trials),
                    declared=int(n_trials_declared))
        else:
            _reason(f"你試了 {n_trials} 種參數:試越多,靠運氣撞到漂亮結果的機率越高,已按此扣分。",
                    "many_trials_penalty", n_trials=int(n_trials))
        _bump("many_trials_penalty", -float(pen))
    elif n_trials and n_trials > 1:
        if trials_uplifted:
            _reason(f"通縮以 {n_trials} 種試驗計(你申報 {int(n_trials_declared)} 種,"
                    "引擎依欄數地板/雜訊硬化保守上調),已納入多重檢定校正(DSR)。",
                    "trials_corrected", n_trials=int(n_trials),
                    declared=int(n_trials_declared))
        else:
            _reason(f"你試了 {n_trials} 種參數:已納入多重檢定校正(DSR)。",
                    "trials_corrected", n_trials=int(n_trials))

    # --- 樣本量提醒 ---
    if n_periods and n_periods < 60:
        _reason(f"樣本只有 {n_periods} 期:統計檢定力弱,任何結論都要打折看待。",
                "sample_short", n_periods=int(n_periods))
        _flag(f"樣本過短(僅 {n_periods} 期)。", "sample_short", n_periods=int(n_periods))
        _bump("sample_short", -8.0)

    # --- 日曆跨度提醒(R12 MED:期數多 ≠ 時間長,2650 根 1 分 K 只有 ~0.04 年)---
    #   誠實揭露年化數字是短窗外插;檢定力扣分已由 sample_short(期數)承擔,此處
    #   刻意【不扣分】只照實告知,避免同一件事罰兩次(期數與跨度高度相關)。
    if span_years is not None and np.isfinite(span_years) and span_years < 0.5:
        _reason(f"資料日曆跨度僅 {span_years:.2f} 年(不到半年):年化夏普/CAGR/年化波動都是"
                "把短窗表現外插成一整年,量級容易誇大——年化數字當方向參考就好,別當保證。",
                "short_calendar_span", span_years=float(span_years))

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
            "reasons_coded": reasons_coded, "red_flags_coded": red_flags_coded,
            "score_breakdown": score_breakdown}


# ===========================================================================
# 7. 統一入口
# ===========================================================================
def _infer_ppy(dates, fallback, warn=None):
    """由日期推年化頻率。warn=可選 (zh, code, **params) 回呼:解析失敗回退 252 時誠實告警
    (修 R12 LOW:舊版裸 except 靜默吞掉壞日期,使用者不知道年化基準已悄悄變 252)。

    R19 必修4:明示的 periods_per_year 需為【正的有限數】——負值/0/NaN/不可解析會讓
    年化夏普吃 sqrt(負數) 之類靜默變 NaN;無效時告警並改走日期推斷/252,不裸例外。
    R19b 必修3:ppy=0(數值)修前被 `if fallback:` 當 falsy 靜默跳過驗證、無警語
    (字串 "0" 與 -3 反而有)——矩陣宣稱「≤0 → 警語」因此超宣稱。改判 None 才算未申報,
    0 走同一 ppy_invalid 告警路徑,行為維持保守回退。"""
    if fallback is not None:
        try:
            f = float(fallback)
        except (TypeError, ValueError):
            f = float("nan")
        if np.isfinite(f) and f > 0:
            return f
        if warn is not None:
            warn(f"periods_per_year={fallback!r} 無效(需為正的有限數):已忽略,改由日期"
                 "推斷(無日期則回退 252)。", "ppy_invalid", value=str(fallback))
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
        except Exception as exc:
            # 日期壞掉 → 回退 252,但【必須】告警(修 R12 LOW:不再靜默吞錯)
            if warn is not None:
                warn(f"日期欄解析失敗({type(exc).__name__}):年化頻率回退為 252(日頻)。"
                     "若你的資料不是日頻,年化夏普/CAGR 會失真——請修正日期格式或明示 periods_per_year。",
                     "ppy_fallback", error=type(exc).__name__)
    return 252.0


def _missing_reject(zh: str, code: str, **params) -> dict:
    """R17 必修1:缺值 fail-closed 的【結構化拒審】結果(照 matrix_empty 慣例:
    可渲染、不裸丟例外)。前端以 warnings_coded 的 code 渲染對應語言錯誤卡。"""
    coded = [{"code": code, "params": params}]
    return {"ok": False, "warnings": [zh], "warnings_coded": coded,
            "metrics": {}, "verdict": {"overall": "inconclusive",
            "score_0to100": 0, "reasons": [zh], "red_flags": [],
            "reasons_coded": coded, "red_flags_coded": [], "score_breakdown": []}}


def _date_integrity_guard(values, dates, keep_mask, warn):
    """★R19 必修1:日期完整性守衛(重複時間戳/亂序)——與前端 parseCSV 同判準的第二層防禦★

    實證騙局:把最好的 60 天整列複製 4 份再按日期排序上傳,重複列會把「好日子」灌水,
    可把年化夏普 -2.49 的真虧策略洗成 86 分 likely-real。這也不只防作弊:pandas concat
    不慎產生重複列是散戶常見意外,舊版會被靜默加分。

    values=(T,) 或 (T,K) ndarray;dates=與列數等長的日期字串。政策(門檻與缺值守衛同 5%):
    - 【完全相同的時間戳記】重複率 ≥5% → 結構化拒審(第 5 個回傳值=reject dict)。
      唯一性用【解析後的完整時間戳】:含時間的日內資料各 bar 不相撞、不誤傷;date-only 的
      日內資料會撞同一天 → 拒審訊息裡明講「請提供含時間的完整時間戳記」。
    - 重複率 <5% → 保留首見、重複列【整列剔除】(合成進 keep_mask 供基準/turnover 同步)+告警。
    - 去重後日期唯一但非遞增 → 依日期【穩定排序】恢復真實時序(排序是恢復真相不是竄改,
      照實揭露亂序筆數),回 sort_perm(新座標 j ← 舊座標 sort_perm[j])供配對通道同步重排。
    - 垃圾日期(解析不了的字串)【逐元素 coerce,不解除守衛】(R19b 必修1:修前任一垃圾
      日期讓整段 early-return,1 格 "n/a" 就能靜默解除重複守衛=毒日期繞過):
      可解析列照常做時戳級重複判定(分母=全列數);垃圾(NaT)列不參與重複判定——
      N/A 不是時戳,identical 垃圾字串不構成「複製好日子」證據,那是「日期壞掉」問題,
      照舊由 ppy_fallback 承擔;任一 NaT 在場 → 【跳過排序步】(無法建立全序)+
      `dates_partially_unparseable` 誠實揭露;全垃圾 → 行為等同無日期(不給新能力)。
    回 (values, dates, keep_mask, sort_perm, reject_or_None)。
    """
    n = int(values.shape[0])
    d_str = [str(d) for d in dates]
    # 逐元素解析(errors='coerce':垃圾字串只變 NaT,不再讓一格垃圾癱瘓整個守衛)。
    # 用【解析後的時間戳值】判重複(比純字串嚴:'2024-01-01' 與 '2024/1/1' 同刻也算重複,
    # 混格式閃避無效);含時間的日內資料各 bar 時戳不同 → 不誤傷。
    # pandas 2.x/3.x 的整欄格式推斷會把混格式變體('2023/1/16')也 coerce 成 NaT
    # (等於幫格式變體閃避洗白)→ 有 NaT 時再用 format='mixed'(逐元素推斷)重試,
    # 取 NaT 較少者;兩者皆炸(極舊 pandas 等)才視同全不可解析。
    try:
        idx = pd.to_datetime(pd.Series(d_str), errors="coerce")
    except Exception:
        idx = None
    if idx is None or bool(idx.isna().any()):
        try:
            idx2 = pd.to_datetime(pd.Series(d_str), format="mixed", errors="coerce")
        except Exception:
            idx2 = None
        if idx2 is not None and (idx is None
                                 or int(idx2.isna().sum()) < int(idx.isna().sum())):
            idx = idx2
    if idx is None:
        return values, dates, keep_mask, None, None
    nat = idx.isna().to_numpy()
    n_nat = int(nat.sum())
    if n_nat == n:
        # 全垃圾 = 行為等同無日期(矩陣「不可解析」格已誠實標定;ppy_fallback 告警承擔)
        return values, dates, keep_mask, None, None
    ts = idx.values.astype("int64")   # NaT → iNaT 哨兵值,但 nat 遮罩已把它們排除在判重外
    seen = set()
    dup_pos = []
    for i in np.flatnonzero(~nat).tolist():
        v = int(ts[i])
        if v in seen:
            dup_pos.append(i)
        else:
            seen.add(v)
    if dup_pos:
        rate = len(dup_pos) / float(n)
        if rate >= MISSING_MAX_RATE:
            return values, dates, keep_mask, None, _missing_reject(
                f"日期重複拒審:{len(dup_pos)}/{n} 期({rate * 100:.0f}%)的時間戳記與先前列"
                "完全相同。重複列會把「好日子」複製灌水、人為抬高夏普與判分——本引擎不靜默"
                "去重放行,重複率 ≥5% 時誠實拒審。請檢查資料(常見成因:pandas concat 產生"
                "重複列、或蓄意複製最佳區段);若為日內資料,請提供含時間的完整時間戳記"
                "(只給日期會使同日多根 bar 撞成重複)。",
                "duplicate_dates_reject", n_dup=len(dup_pos), n_periods=n,
                rate=round(rate, 4), threshold=MISSING_MAX_RATE)
        keep2 = np.ones(n, dtype=bool)
        keep2[dup_pos] = False
        values = values[keep2]
        dates = [d for d, k in zip(dates, keep2) if k]
        ts = ts[keep2]
        nat = nat[keep2]
        if keep_mask is not None:
            orig_pos = np.flatnonzero(keep_mask)
            km = keep_mask.copy()
            km[orig_pos[dup_pos]] = False
            keep_mask = km
        else:
            keep_mask = keep2
        warn(f"日期重複處理:{len(dup_pos)} 列的時間戳記與先前列完全相同(<5%),已【保留首見、"
             f"整列剔除重複列】(重複列會複製好日子灌水判分,絕不靜默保留),有效樣本 "
             f"{int(values.shape[0])} 期。",
             "duplicate_dates_dropped", n_dup=len(dup_pos), n_kept=int(values.shape[0]))
    # R19b 必修1:任一 NaT 在場 → 無法對整批資料建立全序 → 【跳過排序步】+誠實揭露
    # (垃圾列已不參與上方判重;排序只在全可解析時發生,keep_mask/sort_perm 座標語意不變)
    if n_nat:
        warn(f"日期部分無法解析:{n_nat} 列的日期不是可解析的時間戳記(垃圾字串/空白等),"
             "該些列【不參與重複判定】(N/A 不是時戳,相同的垃圾字串不構成「複製好日子」"
             "證據——可解析列的重複判定照常執行),且本批資料【略過時序排序檢查】"
             "(含不可解析日期無法建立完整時序)。年化頻率照舊由日期通道誠實回退並另行告警。",
             "dates_partially_unparseable", n_unparseable=n_nat,
             n_periods=int(values.shape[0]))
        return values, dates, keep_mask, None, None
    # 亂序檢查(去重後日期已唯一):非遞增 → 穩定排序恢復真實時序
    sort_perm = None
    if ts.size > 1 and not bool(np.all(np.diff(ts) > 0)):
        order = np.argsort(ts, kind="stable")
        n_moved = int((order != np.arange(order.size)).sum())
        values = values[order]
        dates = [dates[int(i)] for i in order]
        sort_perm = order
        warn(f"日期亂序處理:{n_moved} 列不在時間順序上,已依日期【穩定排序】恢復真實時序"
             "後再檢定(排序是恢復真相、不是竄改資料——照實揭露)。",
             "dates_sorted", n_moved=n_moved, n_periods=int(values.shape[0]))
    return values, dates, keep_mask, sort_perm, None


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
    # R19 必修4:n_trials 通道硬化——非整數/負值/0 不再靜默通過(int("abc") 會裸例外、
    # 負值會流進通縮公式),一律回退 1(不通縮)並告警。
    # R19b 必修3:修前 `int(... or 1)` 把 0 當 falsy 先換成 1 → n_trials=0 靜默回退、
    # 無 n_trials_invalid 警語(負值反而有)。改為只有 None(未申報)才免驗,
    # 0 落進下方 <1 的既有告警路徑,行為維持保守回退 1。
    _nt_raw = payload.get("n_trials")
    try:
        n_trials = 1 if _nt_raw is None else int(_nt_raw)
    except (TypeError, ValueError):
        _warn(f"n_trials={payload.get('n_trials')!r} 無法解析為整數:已回退為 1(不通縮)。"
              "請檢查申報值。", "n_trials_invalid", value=str(payload.get("n_trials")))
        n_trials = 1
    if n_trials < 1:
        _warn(f"n_trials={n_trials} 無效(需 ≥1):已回退為 1(不通縮)。",
              "n_trials_invalid", value=str(n_trials))
        n_trials = 1
    n_trials_declared = n_trials  # R17 必修2:使用者申報值,供判決措辭誠實分流
    bench_ret = payload.get("benchmark_returns")
    bench_idx = payload.get("benchmark_idx")  # R17 必修4:交集日在使用者序列中的索引
    cost_bps = payload.get("cost_bps_per_turnover")
    turnover = payload.get("turnover")
    keep_mask = None  # R17 必修1:缺值剔除遮罩(原座標),供基準配對索引重映射
    sort_perm = None  # R19 必修1:日期亂序穩定排序的置換(新座標 j ← 舊座標 sort_perm[j])
    # R19 必修4:基準序列只解析一次;含不可解析型別 → 告警+略過所有基準通道(不裸例外)
    bench_arr_raw = None
    if bench_ret is not None:
        try:
            bench_arr_raw = np.asarray(bench_ret, dtype=float)
        except (TypeError, ValueError):
            _warn("基準序列含無法解析為數值的內容:本次【略過】所有基準相關檢定(略過不算通過)。",
                  "bench_invalid_type")

    # ---- 取主報酬序列 ----
    if mode == "matrix":
        matrix = payload.get("matrix") or {}
        if not matrix:
            return {"ok": False, "warnings": ["matrix 模式但 matrix 為空"],
                    "warnings_coded": [{"code": "matrix_empty", "params": {}}],
                    "metrics": {}, "verdict": {"overall": "inconclusive",
                    "score_0to100": 0, "reasons": ["無資料"], "red_flags": [],
                    "reasons_coded": [{"code": "no_data", "params": {}}],
                    "red_flags_coded": [], "score_breakdown": []}}
        names = list(matrix.keys())
        # R19 必修4:非數字型別 fail-closed(np.asarray(dtype=float) 對字串會裸例外 →
        # 改結構化拒審,前端/直呼方都拿得到可渲染的原因)
        try:
            cols = [np.asarray(matrix[k], dtype=float) for k in names]
        except (TypeError, ValueError):
            return _missing_reject(
                "資料型別拒審:matrix 含無法解析為數值的內容(非數字字串等)。"
                "請確認每欄都是數值(報酬率或淨值)後再試。", "invalid_values_type")
        T = min(len(c) for c in cols)
        cols = [c[:T] for c in cols]
        mat = np.column_stack(cols)
        # ★R17 必修1:缺值 fail-closed(矩陣)——絕不以 0 填補★
        #   任一欄非有限值率 ≥5% → 結構化拒審(帶欄名);<5% → 含缺格的列【整列剔除】
        #   (保持各欄橫斷面對齊)並告警;剔除列合計 ≥5% 也拒審(多欄缺格互不重疊會
        #   複利吃樣本,同樣不允許靜默)。dates 同步剔除。
        finite = np.isfinite(mat)
        if not bool(finite.all()):
            col_bad = (~finite).sum(axis=0)
            worst = int(np.argmax(col_bad))
            worst_rate = float(col_bad[worst]) / float(T)
            if worst_rate >= MISSING_MAX_RATE:
                return _missing_reject(
                    f"缺值拒審:欄「{names[worst]}」有 {int(col_bad[worst])}/{T} 期"
                    f"({worst_rate * 100:.0f}%)為空白或非數值(NaN)。以 0 填補會人為壓低"
                    "波動、抬高夏普、扭曲判決——本引擎絕不填 0;任一欄缺格率 ≥5% 時誠實拒審。"
                    "請補齊資料或移除該欄後再試。",
                    "missing_values_reject", col=str(names[worst]),
                    n_missing=int(col_bad[worst]), n_periods=int(T),
                    rate=round(worst_rate, 4), threshold=MISSING_MAX_RATE)
            row_keep = finite.all(axis=1)
            n_drop = int((~row_keep).sum())
            drop_rate = n_drop / float(T)
            if drop_rate >= MISSING_MAX_RATE:
                return _missing_reject(
                    f"缺值拒審:各欄缺格雖皆 <5%,但含缺格的列合計 {n_drop}/{T}"
                    f"({drop_rate * 100:.0f}%)——整列剔除以保持橫斷面對齊後樣本失真過大,"
                    "本引擎誠實拒審(絕不以 0 填補)。請補齊資料後再試。",
                    "missing_rows_reject", n_rows_dropped=n_drop, n_periods=int(T),
                    rate=round(drop_rate, 4), threshold=MISSING_MAX_RATE)
            keep_mask = row_keep
            mat = mat[row_keep]
            T = int(mat.shape[0])
            if dates is not None and len(dates) == row_keep.size:
                dates = [d for d, k in zip(dates, row_keep) if k]
            _warn(f"缺值處理:{n_drop} 列含空白/非數值,已【整列剔除】以保持各欄橫斷面對齊"
                  f"(絕不以 0 填補,填 0 會人為壓低波動),有效樣本 {T} 期。",
                  "missing_rows_dropped", n_rows_dropped=n_drop, n_kept=int(T))
        # ★R19 必修1:日期完整性守衛(重複時間戳/亂序)——必須在挑最佳欄之前★
        if dates is not None and len(dates) == T:
            mat, dates, keep_mask, sort_perm, rej = _date_integrity_guard(
                mat, dates, keep_mask, _warn)
            if rej is not None:
                return rej
            T = int(mat.shape[0])
        elif dates is not None and len(dates) != T:
            _warn(f"日期欄長度({len(dates)})與資料期數({T})不符:日期僅用於頻率推斷,"
                  "列級完整性檢查(重複/亂序/同步剔除)無法執行——請檢查資料。",
                  "dates_len_mismatch", n_dates=int(len(dates)), n_periods=int(T))
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
        # R19 必修4:非數字型別 fail-closed(同 matrix)
        try:
            returns = np.asarray(payload.get("returns") or [], dtype=float)
        except (TypeError, ValueError):
            return _missing_reject(
                "資料型別拒審:returns 含無法解析為數值的內容(非數字字串等)。"
                "請確認序列是數值(報酬率或淨值)後再試。", "invalid_values_type")
        if returns.size == 0:
            return {"ok": False, "warnings": ["returns 模式但 returns 為空"],
                    "warnings_coded": [{"code": "returns_empty", "params": {}}],
                    "metrics": {}, "verdict": {"overall": "inconclusive",
                    "score_0to100": 0, "reasons": ["無資料"], "red_flags": [],
                    "reasons_coded": [{"code": "no_data", "params": {}}],
                    "red_flags_coded": [], "score_breakdown": []}}
        # ★R17 必修1:缺值 fail-closed(單序列)——絕不以 0 填補★
        #   舊版 nan_to_num(nan=0.0) 會讓「最差 100 天留空」的騙局被填 0 洗白;
        #   修後:缺格率 ≥5% → 結構化拒審;<5% → 整期剔除+告警;dates 同步剔除。
        finite_mask = np.isfinite(returns)
        n_bad = int((~finite_mask).sum())
        if n_bad:
            rate = n_bad / float(returns.size)
            if rate >= MISSING_MAX_RATE:
                return _missing_reject(
                    f"缺值拒審:報酬序列有 {n_bad}/{returns.size} 期({rate * 100:.0f}%)"
                    "為空白或非數值(NaN)。以 0 填補會人為壓低波動、抬高夏普、扭曲判決——"
                    "本引擎絕不填 0;缺格率 ≥5% 時誠實拒審。請補齊資料或移除缺值過多的期間後再試。",
                    "missing_values_reject", n_missing=n_bad, n_periods=int(returns.size),
                    rate=round(float(rate), 4), threshold=MISSING_MAX_RATE)
            keep_mask = finite_mask
            returns = returns[finite_mask]
            if dates is not None and len(dates) == finite_mask.size:
                dates = [d for d, k in zip(dates, finite_mask) if k]
            _warn(f"缺值處理:{n_bad} 期空白/非數值已【整期剔除】(絕不以 0 填補,"
                  f"填 0 會人為壓低波動、抬高夏普),有效樣本 {returns.size} 期,"
                  "統計檢定在剔除後的序列上執行。",
                  "missing_values_dropped", n_missing=n_bad, n_kept=int(returns.size))
        # ★R19 必修1:日期完整性守衛(重複時間戳/亂序)★
        if dates is not None and len(dates) == returns.size and returns.size > 0:
            returns, dates, keep_mask, sort_perm, rej = _date_integrity_guard(
                returns, dates, keep_mask, _warn)
            if rej is not None:
                return rej
        elif dates is not None and len(dates) != returns.size:
            _warn(f"日期欄長度({len(dates)})與資料期數({returns.size})不符:日期僅用於"
                  "頻率推斷,列級完整性檢查(重複/亂序/同步剔除)無法執行——請檢查資料。",
                  "dates_len_mismatch", n_dates=int(len(dates)), n_periods=int(returns.size))
        names, mat = None, None
        trial_sharpes = np.array([_sharpe_periodic(returns)])
        harden_diag = None  # returns 模式不硬化(硬化只在 matrix)

    # R19 必修1:年化頻率在【日期完整性守衛之後】才推(重複/亂序日期會扭曲平均步距)
    ppy = _infer_ppy(dates, payload.get("periods_per_year"), warn=_warn)

    n = returns.size
    if n < 30:
        _warn(f"樣本僅 {n} 期,統計檢定力弱,結論僅供參考。", "short_sample", n=int(n))

    # ---- 日曆跨度檢查(R12 MED):樣本警告只看期數會漏掉「期數多、時間短」的日內資料——
    #      2650 根 1 分 K 只有 ~0.04 年,年化數字全是外插。span<0.5 年 → 誠實告警。----
    span_years = None
    if dates and len(dates) > 2:
        try:
            _idx = pd.to_datetime(pd.Series(dates))
            span_years = float((_idx.iloc[-1] - _idx.iloc[0]).total_seconds()) / (86400.0 * 365.25)
        except Exception:
            span_years = None  # 日期壞掉的告警已由 _infer_ppy 的 ppy_fallback 承擔,不重複
    if span_years is not None and np.isfinite(span_years) and span_years < 0.5:
        _warn(f"資料日曆跨度僅 {span_years:.2f} 年(不到 0.5 年):所有年化數字(年化夏普/"
              "CAGR/年化波動)都是把短窗表現外插成一整年,參考性有限——建議累積至少半年再看年化。",
              "short_calendar_span", span_years=float(span_years))

    # ---- 極端值提醒(R19 必修4):|單期報酬| ≥ 10(=1000%)幾乎必是單位錯 ----
    #   最常見成因:把百分比當小數(5% 誤填成 5.0)。不拒審(可能有真極端事件),
    #   但年化與判分會嚴重失真 → 誠實告警,請使用者確認單位。
    if n:
        n_extreme = int((np.abs(returns) >= 10.0).sum())
        if n_extreme:
            _warn(f"單期報酬量級異常:{n_extreme} 期的|報酬| ≥ 10(=1000%)。最常見成因是"
                  "【把百分比當小數】(5% 誤填成 5.0)或單位錯誤——所有年化數字與判分會嚴重"
                  "失真,請先確認報酬單位(小數:0.05 = 5%)再解讀本判決。",
                  "extreme_returns", n=n_extreme,
                  max_abs=float(np.max(np.abs(returns))))

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
           "harden": harden_diag,  # matrix 硬化診斷(returns 模式為 None)
           # R12 HIGH 修:returns 模式 n_trials>1 無真試驗池 → 試驗離散度用 SE(SR)² 保守
           # proxy 做【真通縮】(舊版 variance=0 → sr0≡0 → n_trials 無效)。誠實揭露之。
           "sr_var_proxy": bool(dsr_res.get("sr_variance_proxy", False))}

    # ---- permutation null ----
    perm = permutation_null(returns, ppy, n_perm=n_perm_eff)

    # ---- PBO / FWER(僅 matrix)----
    # ★R15 修:基準對齊不再靜默(修前:bench 長度 < 矩陣期數——前端日期交集覆蓋 80-99%
    #   時常態發生——會被【靜默】換成零序列,SPA/RW 名義上「vs 基準」實際 vs 絕對報酬,
    #   同一報告的 benchmark_compare 卻用真基準,兩卡自相矛盾)。
    #   引擎收到的 bench 已無日期(前端做過日期交集才送),長度不符時無從知道缺哪幾期、
    #   與矩陣逐期配對不可能 → 誠實三態:
    #   - aligned:bench 期數 ≥ 矩陣期數 → 逐期配對,真 vs 基準。
    #   - zero_fallback:bench 較短無法配對 → 改 vs 零基準(絕對報酬)誠實執行,
    #     【必發】fwer_bench_fallback_zero 警語(帶覆蓋率),SPA/RW 卡須據此揭露。
    #   - zero_no_benchmark:使用者沒選基準 → vs 零基準(原本文件化行為),
    #     以 benchmark_kind 揭露讓前端措辭正確(「絕對報酬」而非「贏基準」)。
    pbo_out = None
    fwer_out = None
    if mode == "matrix" and mat is not None and mat.shape[1] >= 2:
        pbo_out = pbo_cscv(mat, 12)
        T_mat = mat.shape[0]
        fwer_bench_kind = "zero_no_benchmark"
        fwer_bench_cov = None
        fwer_keep = None  # R19 必修3:基準非有限率 <5% 時的 FWER 專用剔列遮罩(mat+bench 同步)
        if bench_arr_raw is not None:
            # ★R19 必修3:基準不再 nan_to_num 填 0(填 0 稀釋基準=反保守 fail-open)★
            b_arr = bench_arr_raw
            # ★R17 收尾(驗收官刀):引擎剔除過缺值列時,基準必須以同一 keep_mask 同步剔除,
            #   否則 b_arr[:T_mat] 位置截斷會逐期錯位、卻仍宣稱 aligned/coverage=1.0(假揭露)。
            #   基準長度=原始列數 → 同步剔除;對不上原始列數 → 無法確定配對,誠實降級零基準。
            bench_pairable = True
            if keep_mask is not None:
                if b_arr.size == keep_mask.size:
                    b_arr = b_arr[keep_mask]
                else:
                    bench_pairable = False
            if bench_pairable and b_arr.size >= T_mat:
                bench_arr = b_arr[:T_mat]
                # R19 必修1:列經日期穩定排序 → 基準同步重排,維持逐期配對
                if sort_perm is not None:
                    bench_arr = bench_arr[sort_perm]
                # ★R19 必修3:基準缺值 fail-closed(與策略端同標準、同 5% 門檻)★
                bfin = np.isfinite(bench_arr)
                n_bad_b = int((~bfin).sum())
                if n_bad_b == 0:
                    fwer_bench_kind = "aligned"
                    fwer_bench_cov = 1.0
                elif n_bad_b / float(T_mat) >= MISSING_MAX_RATE:
                    fwer_bench_cov = round(float(bfin.sum()) / float(T_mat), 4)
                    bench_arr = np.zeros(T_mat)
                    fwer_bench_kind = "zero_fallback"
                    _warn(f"SPA/Romano-Wolf 的基準有 {n_bad_b}/{T_mat} 期"
                          f"({n_bad_b / T_mat * 100:.0f}%)為非有限值(NaN/inf):以 0 填補會"
                          "稀釋基準、讓「贏過基準」變太容易——本引擎絕不填 0,此檢定已誠實降級為 "
                          "vs 絕對報酬(零基準)執行,勿當作「贏過基準」。",
                          "fwer_bench_nonfinite_fallback_zero",
                          coverage=fwer_bench_cov, n_nonfinite=n_bad_b, n_periods=int(T_mat))
                else:
                    fwer_keep = bfin
                    fwer_bench_kind = "aligned"
                    fwer_bench_cov = round(float(bfin.sum()) / float(T_mat), 4)
                    _warn(f"SPA/Romano-Wolf 的基準有 {n_bad_b}/{T_mat} 期為非有限值"
                          f"(NaN/inf,<5%):該些期已從 FWER 檢定【剔除】(矩陣與基準同步、"
                          f"絕不填 0),FWER 在 {int(bfin.sum())} 期上執行——與主判決的樣本數"
                          "不同,照實揭露。",
                          "fwer_bench_nonfinite_rows_dropped",
                          n_dropped=n_bad_b, n_used=int(bfin.sum()))
            elif not bench_pairable:
                fwer_bench_cov = 0.0
                bench_arr = np.zeros(T_mat)
                fwer_bench_kind = "zero_fallback"
                _warn(f"SPA/Romano-Wolf 的基準無法逐期配對:引擎已因缺值/重複日期整列剔除 "
                      f"{int((~keep_mask).sum())} 列,而基準長度({b_arr.size} 期)與原始矩陣"
                      f"列數({int(keep_mask.size)})不符,剔除後無法同步重配對。此檢定已改為 "
                      "vs 絕對報酬(零基準)誠實執行——勿當作「贏過基準」。",
                      "fwer_bench_fallback_zero", coverage=0.0,
                      bench_len=int(b_arr.size), n_periods=int(T_mat))
            else:
                fwer_bench_cov = float(b_arr.size) / float(T_mat) if T_mat else 0.0
                bench_arr = np.zeros(T_mat)
                fwer_bench_kind = "zero_fallback"
                _warn(f"SPA/Romano-Wolf 的基準無法逐期配對:基準 {b_arr.size} 期 vs 矩陣 "
                      f"{T_mat} 期(覆蓋 {fwer_bench_cov*100:.0f}%,引擎端無日期可做共同索引"
                      "對齊)。此檢定已改為 vs 絕對報酬(零基準)誠實執行——與「對照基準」卡"
                      "(用真基準、各自彙總比較)語意不同,請分開解讀,勿當作「贏過基準」。",
                      "fwer_bench_fallback_zero", coverage=round(fwer_bench_cov, 4),
                      bench_len=int(b_arr.size), n_periods=int(T_mat))
        else:
            bench_arr = np.zeros(T_mat)
        # R19 必修3:基準非有限率 <5% → 該些列從 FWER 計算剔除(mat+bench 一起);
        # PBO(pbo_out)不吃基準,維持全樣本。
        mat_f = mat[fwer_keep] if fwer_keep is not None else mat
        bench_f = bench_arr[fwer_keep] if fwer_keep is not None else bench_arr
        fw = run_fwer_gates(mat_f, names, bench_f, n_bootstrap=n_boot_eff, n_blocks_pbo=12)
        fwer_out = {"spa": fw["spa"], "per_candidate": fw["per_candidate"],
                    "n_rejected": fw["n_rejected"],
                    "benchmark_kind": fwer_bench_kind, "bench_coverage": fwer_bench_cov,
                    "calibration": fw.get("calibration")}
        if not fw["computed"]:
            _warn(f"FWER 未計算:{fw.get('reason', '樣本不足')}(需 ≥100 期)。",
                  "fwer_not_computed", n_obs=int(fw.get("n_obs", 0)), min_obs=100)

    # ---- 成本壓力(僅有 turnover)----
    #   R19 必修3:turnover 的 fail-open 收口——非有限值填 0 會【低估成本】放水過關。
    #   政策(與策略端同 5% 門檻):<5% → 該些期從成本計算剔除+揭露;≥5% → 跳過
    #   cost_stress+警語(絕不填 0)。cost_bps 負值/非有限也拒(負成本=倒貼,反保守)。
    cost_out = None
    if turnover is not None and cost_bps is not None:
        try:
            cb = float(cost_bps)
        except (TypeError, ValueError):
            cb = float("nan")
        try:
            tn = np.asarray(turnover, dtype=float)
        except (TypeError, ValueError):
            tn = None
        if not np.isfinite(cb) or cb < 0:
            _warn(f"cost_bps_per_turnover={cost_bps!r} 無效(需為非負的有限數):本次【略過】"
                  "成本壓力測試(略過不算通過)。", "cost_bps_invalid", value=str(cost_bps))
        elif tn is None:
            _warn("turnover 含無法解析為數值的內容:本次【略過】成本壓力測試(略過不算通過)。",
                  "turnover_invalid_type")
        else:
            # R17 收尾:引擎剔除過缺值/重複列 → turnover 以同一 keep_mask 同步剔除;
            # R19:列經日期排序 → 同步重排,維持逐期配對。
            if keep_mask is not None and tn.size == keep_mask.size:
                tn = tn[keep_mask]
            # R19b 必修2:長度檢查必須在截斷/排序【之前】——修前 tn[:n][sort_perm]
            # 先把長度切齊,turnover_len_mismatch 在日期被排序過的資料上永不觸發(假沉默)。
            if tn.size != n:
                _warn(f"turnover 長度({tn.size})與報酬期數({n})不符:以「雙邊截到共同長度」"
                      "概略配對計算成本(照實揭露;請確認兩者是否同一時段的逐期序列)。",
                      "turnover_len_mismatch", turnover_len=int(tn.size), n_periods=int(n))
            if sort_perm is not None and tn.size >= n:
                tn = tn[:n][sort_perm]
            m_c = int(min(n, tn.size))
            r_c, tn_c = returns[:m_c], tn[:m_c]
            # 無效期 = 非有限【或負值】:負 turnover 會讓 net = r - tn×cost 反向【加分】
            # (負成本=倒貼),與填 0 同屬 fail-open,一律同門檻處理。
            tfin = np.isfinite(tn_c) & (tn_c >= 0.0)
            n_bad_t = int((~tfin).sum())
            if m_c and n_bad_t / float(m_c) >= MISSING_MAX_RATE:
                _warn(f"turnover 有 {n_bad_t}/{m_c} 期({n_bad_t / m_c * 100:.0f}%)為非有限值"
                      "(NaN/inf)或負值:以 0 填補會【低估成本】、負換手更會倒貼加分——"
                      "本引擎絕不填 0,無效率 ≥5% 時【略過】成本壓力測試(略過不算通過)。"
                      "請補齊換手率後再試。",
                      "turnover_nonfinite_skipped", n_nonfinite=n_bad_t, n_periods=int(m_c),
                      rate=round(n_bad_t / float(m_c), 4), threshold=MISSING_MAX_RATE)
            else:
                if n_bad_t:
                    r_c, tn_c = r_c[tfin], tn_c[tfin]
                    _warn(f"turnover 有 {n_bad_t} 期為非有限值(NaN/inf)或負值(<5%):該些期"
                          f"已從成本計算【剔除】(絕不填 0 低估成本、絕不讓負換手倒貼),"
                          f"成本閘在 {int(tn_c.size)} 期上執行。",
                          "turnover_nonfinite_dropped", n_dropped=n_bad_t, n_used=int(tn_c.size))
                cost_out = cost_stress(r_c, tn_c, cb, ppy)

    # ---- 基準比較(R17 必修4:共同日【配對子集】比較)----
    #   修前:交集對齊的 bench 只含兩邊都有的日子、策略卻用全序列各自彙總比較 →
    #   策略多算了基準缺席的日子(交集模式容許到 20%),beats_bench(±8 分、影響 real
    #   旗標)部分繫於未配對日。修後:主判決仍用策略全序列;只有本卡在配對子集上比——
    #   前端傳 benchmark_idx(交集日在使用者序列中的索引,與 benchmark_returns 逐位對應);
    #   引擎若剔除過缺值列,先把配對索引同步剔除並重映射;無 idx 時退回「雙邊截到共同
    #   長度」的位置配對(等長情形與舊行為逐位相同,不再讓策略單邊多算)。
    bench_cmp = None
    if bench_arr_raw is not None:
        # R19 必修3:基準不再 nan_to_num 填 0(填 0 稀釋基準)——非有限對在下方 pairwise 剔除
        b = bench_arr_raw
        strat_pair = None
        paired = False
        skip_pair = False
        if bench_idx is not None:
            idx = np.asarray(list(bench_idx), dtype=int) if len(bench_idx) else np.empty(0, dtype=int)
            n_orig = int(keep_mask.size) if keep_mask is not None else n
            # R19 必修4:idx 需【唯一】——重複索引會把同一天算兩次(灌水配對樣本)
            valid = (idx.size == b.size and idx.size >= 2
                     and int(idx.min()) >= 0 and int(idx.max()) < n_orig
                     and int(np.unique(idx).size) == int(idx.size))
            idx_input_invalid = not valid
            if valid and keep_mask is not None:
                # 缺值/重複列剔除過:落在被剔除列上的配對日一併剔除(基準側同步),再重映射到新座標
                pair_ok = keep_mask[idx]
                new_pos = np.cumsum(keep_mask) - 1
                idx = new_pos[idx[pair_ok]]
                b = b[pair_ok]
                valid = idx.size >= 2
            if valid and sort_perm is not None:
                # R19 必修1:列經日期穩定排序 → 配對索引映射到新座標,並依時序重排配對
                inv = np.empty(n, dtype=int)
                inv[sort_perm] = np.arange(n)
                idx = inv[idx]
                order2 = np.argsort(idx, kind="stable")
                idx = idx[order2]
                b = b[order2]
            if valid:
                strat_pair = returns[idx]
                paired = True
            elif idx_input_invalid:
                _warn("基準配對索引無效(長度/範圍/唯一性不符或不足 2 期):"
                      "退回「雙邊截到共同長度」的位置配對比較。",
                      "bench_pair_idx_invalid",
                      idx_len=int(np.asarray(list(bench_idx)).size), bench_len=int(b.size))
            else:
                # R19 順修 LOW:配對索引重映射後剩 <2 對——此時基準已是配對子集、無法退回
                # 位置配對(會逐期錯位),誠實【略過】本卡並照實說明(舊警語謊稱「退回位置配對」)。
                _warn("基準配對索引經缺值/重複列剔除同步後剩不到 2 對:配對樣本不足,"
                      "本次【略過】基準比較(不退回位置配對——該退路在此無法保證逐期對應)。",
                      "bench_pair_too_few", n_pairs=int(idx.size))
                skip_pair = True
        if strat_pair is None and not skip_pair:
            # R19 必修2:位置配對退路也必須吃 keep_mask/sort_perm——修前用【原座標】基準對
            # 【剔列後】策略位置截斷,逐期錯位(實測錯位夏普 2.4868 vs 對齊真值 1.9889),
            # 且與同一報告的 benchmark_curve(已吃 keep_mask)自相矛盾。
            if keep_mask is not None and b.size == keep_mask.size:
                b = b[keep_mask]
            if sort_perm is not None and b.size >= n:
                b = b[:n][sort_perm]
            m_len = int(min(n, b.size))
            b = b[:m_len]
            strat_pair = returns[:m_len]
        if strat_pair is not None and b.size:
            # R19 必修3:配對子集內 pairwise 剔除非有限對(絕不填 0),n_paired 如實反映
            bfin_p = np.isfinite(b)
            n_bad_p = int((~bfin_p).sum())
            if n_bad_p:
                b = b[bfin_p]
                strat_pair = strat_pair[bfin_p]
                _warn(f"基準比較:{n_bad_p} 個配對期的基準值為非有限(NaN/inf),已【逐對剔除】"
                      f"(絕不以 0 填補稀釋基準),實際配對 {int(b.size)} 期——如實揭露。",
                      "bench_pairs_dropped_nonfinite", n_dropped=n_bad_p, n_paired=int(b.size))
        if strat_pair is not None and b.size >= 2:
            bm = compute_metrics(b, ppy)
            sm = compute_metrics(strat_pair, ppy)  # 配對子集上的策略指標(僅供本卡)
            excess = (sm["cagr"] - bm["cagr"]) if (np.isfinite(sm["cagr"]) and np.isfinite(bm["cagr"])) else float("nan")
            beats = (np.isfinite(sm["sharpe"]) and np.isfinite(bm["sharpe"])
                     and sm["sharpe"] > bm["sharpe"])
            bench_cmp = {"bench_sharpe": bm["sharpe"], "bench_cagr": bm["cagr"],
                         "excess_cagr": excess, "strategy_beats": bool(beats),
                         "paired": bool(paired), "n_paired": int(b.size),
                         "strat_sharpe_paired": sm["sharpe"],
                         "strat_cagr_paired": sm["cagr"]}

    # ---- 綜合裁決 ----
    verdict = _verdict({
        "dsr_prob": dsr["dsr_prob"],
        "pbo": pbo_out["pbo"] if pbo_out else None,
        "perm_p": perm["p_value"], "perm_pass": perm["passes"],
        "cost3_sharpe": cost_out["x3_sharpe"] if cost_out else None,
        "beats_bench": bench_cmp["strategy_beats"] if bench_cmp else None,
        "excess_cagr": bench_cmp["excess_cagr"] if bench_cmp else None,
        "concentration": metrics["top_bar_concentration"],
        "n_trials": n_trials, "n_trials_declared": n_trials_declared,
        "n_periods": n, "real_sharpe": perm["real_sharpe"],
        "capped_escape": bool(harden_diag["capped_escape"]) if harden_diag else False,
        "dsr_var_proxy": bool(dsr_res.get("sr_variance_proxy", False)),
        "calendar_span_years": span_years,
    })

    # ---- equity curves ----
    equity_curve = _equity_from_returns(returns).tolist()
    bench_curve = None
    if bench_cmp is not None:
        # 視覺層維持 nan_to_num(曲線平接):基準缺值已由上方 bench_pairs_dropped_nonfinite /
        # fwer_bench_nonfinite_* 警語如實揭露,此處只影響畫圖、不進任何判分。
        b = np.nan_to_num(bench_arr_raw, nan=0.0)
        # R17 收尾:剔除過缺值/重複列 → 基準曲線同步剔除;R19:排序同步,與主曲線逐期對齊
        if keep_mask is not None and b.size == keep_mask.size:
            b = b[keep_mask]
        if sort_perm is not None and b.size >= n:
            b = b[:n][sort_perm]
        bench_curve = _equity_from_returns(b[:n]).tolist()

    return {
        "ok": True, "warnings": warnings, "warnings_coded": warnings_coded,
        "metrics": metrics, "dsr": dsr, "permutation_null": perm,
        "pbo": pbo_out, "fwer": fwer_out, "cost_stress": cost_out,
        "benchmark_compare": bench_cmp, "verdict": verdict,
        "equity_curve": equity_curve, "benchmark_curve": bench_curve,
    }


# ===========================================================================
# 8. 權威偵測與轉換(E2/R12 MED:前端偵測下沉)
#    JS 端的「淨值 vs 報酬」heuristic 與民國年正規化原本零測試、默默轉換資料。
#    此節把兩者下沉到引擎:app.js 照舊先偵測(當 UI 即時提示),但 payload 多帶
#    raw{values|matrix, dates, js_kind(s)};analyze 前先過 detect_and_convert,
#    由 Python(pytest 釘死)重做權威偵測與轉換,與 JS hint 不一致時 warning 告知。
#    數字解析(%、千分位、全形)仍在 JS(確定性字串處理);風險集中的【判斷】在這裡。
#    ★本節為獨立追加區段(檔尾),不動上方 DSR/FWER/analyze 任何一行。★
# ===========================================================================
import re as _re

_DATE_HEAD_RE = _re.compile(r"^(\d{3,4})[-/.](\d{1,2})[-/.](\d{1,2})([T ]\d{1,2}:\d{2}(?::\d{2})?)?")
_DATE_YMD8_RE = _re.compile(r"^(\d{4})(\d{2})(\d{2})$")


def normalize_date_str(s) -> str:
    """單一日期字串 → ISO(民國年→西元;YYYYMMDD→ISO;日內時間戳保留時間)。
    與 app.js normalizeDate 同語意的權威版:3 位年或 1<年<1911 視為民國年 +1911。
    解析不了原樣返回(讓 _infer_ppy 的 pd.to_datetime 再試/失敗自然退回 252)。"""
    s = str(s).strip()
    m = _DATE_HEAD_RE.match(s)
    if m:
        y = int(m.group(1))
        if len(m.group(1)) == 3 or (1 < y < 1911):
            y += 1911
        tm = m.group(4)
        tm = (" " + tm[1:]) if tm else ""   # 去掉開頭的 'T' 或空白,統一單一空白
        return f"{y}-{int(m.group(2)):02d}-{int(m.group(3)):02d}{tm}"
    m = _DATE_YMD8_RE.match(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return s


def _finite_list(vals) -> list:
    """JSON 來的 list(可含 None/NaN/字串數字)→ 只留有限 float。"""
    out = []
    for v in (vals or []):
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if np.isfinite(f):
            out.append(f)
    return out


def detect_series_kind(vals) -> str:
    """一欄數值是「報酬」還是「淨值」的權威偵測(與 app.js detectSeriesKind 同判準):
    - 有限值 <3 → 'returns'(資訊不足,保守不轉換)
    - |max|<1.5 且有負值 → 'returns'(典型漲跌幅)
    - 全正且(|max|>3 或單調比例>0.55)→ 'nav'
    - 全正且單調比例>0.6 → 'nav'(1.0x 附近的淨值)
    - 其餘 → 'returns'(含【負淨值】邊界:出現負值且量級大 → 不當淨值轉換,
      因 nav→returns 對跨零序列會產生無意義的爆炸報酬;照 returns 處理並由檢定自然懲罰)"""
    clean = _finite_list(vals)
    if len(clean) < 3:
        return "returns"
    abs_max = max(abs(v) for v in clean)
    any_neg = any(v < 0 for v in clean)
    all_pos = all(v > 0 for v in clean)
    up = sum(1 for i in range(1, len(clean)) if clean[i] >= clean[i - 1])
    mono = up / (len(clean) - 1)
    if abs_max < 1.5 and any_neg:
        return "returns"
    if all_pos and (abs_max > 3 or mono > 0.55):
        return "nav"
    if all_pos and mono > 0.6:
        return "nav"
    return "returns"


def nav_to_returns(vals) -> list:
    """淨值 → 逐期報酬(與 app.js navToReturns 同語意):首期 0。

    ★R17 必修1:非有限/不可解析值【不再填 0】★——改保留 NaN(自身與下一期的報酬都成
    NaN,因為兩者都依賴缺失的淨值),交由 analyze 入口的缺值守衛整期剔除或拒審。
    舊行為(NaN→0 → prev=0 → 報酬 0)等於憑空捏造「持平日」,會稀釋波動,已移除。
    ★R19 必修5b:前值為 0 也改 NaN★——除以 0 無法定義報酬,舊行為記 0.0 等於憑空捏造
    「持平日」稀釋波動;改 NaN 傳播、交同一套缺值守衛(<5% 剔除揭露、≥5% 拒審)。"""
    clean = []
    for v in (vals or []):
        try:
            f = float(v)
        except (TypeError, ValueError):
            f = float("nan")
        clean.append(f if np.isfinite(f) else float("nan"))
    out = [0.0]
    for i in range(1, len(clean)):
        prev, cur = clean[i - 1], clean[i]
        if not (np.isfinite(prev) and np.isfinite(cur)) or prev == 0.0:
            out.append(float("nan"))
        else:
            out.append(cur / prev - 1.0)
    return out


def _clean_returns(vals) -> list:
    """報酬欄清洗:R17 必修1 起,NaN/None/不可解析【不再填 0】→ 保留 NaN,
    交由 analyze 入口的缺值守衛整期剔除(<5%)或結構化拒審(≥5%),fail-closed。"""
    out = []
    for v in (vals or []):
        try:
            f = float(v)
        except (TypeError, ValueError):
            f = float("nan")
        out.append(f if np.isfinite(f) else float("nan"))
    return out


def detect_and_convert(payload: dict) -> dict:
    """analyze 前的權威偵測/轉換層。純函數:回【新 dict】,不改入參。

    契約:
    - payload 無 "raw"(舊呼叫方/測試直接餵 returns/matrix)→ 原樣返回,零行為改變。
    - payload["raw"] = {values|matrix, dates, js_kind|js_kinds} 時:
        * dates:逐筆 normalize_date_str(民國年→西元等),【覆蓋】payload["dates"];
          與前端已正規化的 dates 不一致 → warning(code=detect_dates_mismatch)。
        * returns 模式:raw.values 重偵測 kind、重轉換,【覆蓋】payload["returns"];
          與 js_kind 不一致 → warning(code=detect_kind_mismatch)。
        * matrix 模式:raw.matrix 逐欄同上,【覆蓋】payload["matrix"]。
    - 偵測層 warnings 放 payload["_detect_warnings"] / ["_detect_warnings_coded"]
      (與引擎 warnings 同結構,呼叫方自行併入 analyze 輸出)。"""
    raw = payload.get("raw")
    if not isinstance(raw, dict):
        return payload

    p = dict(payload)
    warns, warns_coded = [], []

    def _warn(zh: str, code: str, **params):
        warns.append(zh)
        warns_coded.append({"code": code, "params": params})

    # ---- 日期:引擎正規化為權威 ----
    rdates = raw.get("dates")
    if rdates:
        pdates = [normalize_date_str(d) for d in rdates]
        js_dates = payload.get("dates")
        if js_dates is not None:
            js_list = [str(d) for d in js_dates]
            n_diff = sum(1 for a, b in zip(js_list, pdates) if a != b) + abs(len(js_list) - len(pdates))
            if n_diff:
                _warn(f"日期正規化:引擎與前端有 {n_diff} 筆不一致(民國年/格式處理),已以引擎(Python)結果為準。",
                      "detect_dates_mismatch", n_diff=int(n_diff))
        p["dates"] = pdates

    def _authoritative(vals, js_hint, col=None):
        kind = detect_series_kind(vals)
        if js_hint in ("nav", "returns") and js_hint != kind:
            _warn((f"欄「{col}」" if col else "") + f"序列型別偵測不一致:前端判「{js_hint}」、引擎權威判「{kind}」,"
                  f"已以引擎({kind})為準做{'淨值→報酬轉換' if kind == 'nav' else '逐期報酬處理'}——請肉眼確認你的資料型別。",
                  "detect_kind_mismatch", col=col, js=js_hint, py=kind)
        return nav_to_returns(vals) if kind == "nav" else _clean_returns(vals), kind

    mode = payload.get("mode", "returns")
    if mode == "matrix":
        rmat = raw.get("matrix")
        if isinstance(rmat, dict) and rmat:
            js_kinds = raw.get("js_kinds") or {}
            new_mat = {}
            for name, vals in rmat.items():
                conv, _ = _authoritative(vals, js_kinds.get(name), col=name)
                new_mat[name] = conv
            p["matrix"] = new_mat
    else:
        rvals = raw.get("values")
        if rvals:
            conv, _ = _authoritative(rvals, raw.get("js_kind"))
            p["returns"] = conv

    p["_detect_warnings"] = warns
    p["_detect_warnings_coded"] = warns_coded
    return p
