# Reference check — 統計檢定 vs scipy / statsmodels / arch 參考實作

> 2026-11-04 · 分支 `test/reference-check-20261104`
> 目的:對 repo 裡**每一個**統計檢定,用業界參考實作產生隨機與邊界資料交叉比對數值誤差;
> 列出不一致之處、根因與修正。可重跑、seed 固定、結果確定。

## 怎麼重跑

```bash
pip install -r engine/requirements-test.txt      # numpy pandas scipy statsmodels arch pytest
python -m engine.reference_check                 # 印出下方「修正後」報表;任何 ❌ → exit 1
pytest engine/test_reference_check.py -v         # 同一批 check 的 pytest 版(CI 快層也跑)
```

- 比對程式:[`engine/reference_check.py`](../engine/reference_check.py)(每個 `check_*` 一項)。
- pytest 包裝 + 回歸釘:[`engine/test_reference_check.py`](../engine/test_reference_check.py)。
- 參考套件**只在測試側 import**,引擎 runtime 仍只依賴 numpy/pandas(Pyodide 不受影響)。

## 檢定 × 參考實作對照

| 引擎檢定 | 函式 | 參考實作 | 比對方式 |
|---|---|---|---|
| 常態 CDF / PPF、偏度、峰度 | `statshim.norm_cdf/norm_ppf/skew/kurtosis` | `scipy.stats.norm.cdf/ppf`、`scipy.stats.skew/kurtosis(bias=True)` | exact:隨機 + p∈{0,1,次正規數,1e-300…}、深左尾 x∈[-38,-1]、NaN/±inf、n=3/4、1e8 位移、1e-10 尺度、單點跳躍 |
| E[max SR](False Strategy Theorem) | `expected_max_sharpe` | `scipy.stats.norm.isf` 逐式;另以 `scipy.integrate.quad` 算 E[max of N normals] 精確值 | exact(N 到 1e15)+ informational(閉式近似本身的誤差) |
| PSR / **Deflated Sharpe** | `probabilistic_sharpe`、`deflated_sharpe` | pandas `std(ddof=1)` + scipy skew/kurtosis/norm,依 Bailey & López de Prado (2014) 逐式 | exact:184 組(真試驗池、SE² proxy、n_trials=1、NaN/inf 混入、常數、N=1e9);p 值尾端相對精度 vs `norm.sf` |
| **PBO / CSCV** | `pbo_cscv` | 依 Bailey-Borwein-López de Prado-Zhu (2017) 的 naive loop(真的拼 IS/OOS 列)+ `scipy.stats.rankdata` | exact:25 組隨機 + N=2、奇數 N、短 T 降塊、重複欄、1e3 位移 |
| Circular block bootstrap 底座 | `_circular_block_bootstrap_means` | naive 顯式索引 CBB(同亂數流);解析 Var* | exact + statistical |
| 自適應 block 長 | `_auto_block_len` | `statsmodels.tsa.stattools.acf` 的 lag-1 自相關 | exact(AR(1) φ∈{0,.1,.3,.6,-.3}) |
| **Hansen SPA**(consistent / lower / upper) | `_spa_pvalues` | Hansen (2005) naive loop(同 bootstrap 樣本);`arch.bootstrap.SPA(bootstrap="circular", studentize=True)` | exact(演算法)+ statistical(12 組,含真 edge 與爛候選) |
| **White Reality Check** | `spa["p_upper"]`(= Hansen SPA_u = studentized RC) | `arch.bootstrap.SPA(...).pvalues["upper"]`;另記錄 `arch.bootstrap.RealityCheck(studentize=False)`(原版 White 2000) | statistical + informational |
| **Romano-Wolf StepM** | `_romano_wolf_adj_p` | Romano & Wolf (2016) step-down naive loop;`arch.bootstrap.StepM(size=0.10)` 的 superior set | exact(調整後 p)+ statistical(逐候選決策一致率) |
| Permutation / block-bootstrap null | `permutation_null` | naive 顯式索引 CBB;`scipy.stats.t.ppf`(ddof=0 夏普的精確 null 分位)、`scipy.stats.ttest_1samp` | exact + statistical(3×300 組資料集) |
| 績效指標 | `compute_metrics` | pandas `cumprod/cummax/std`;Sortino & Price (1994) 下方偏差 | exact(含只有一筆/全相同負報酬) |
| 成本壓力 | `cost_stress` | pandas `dropna` + `std(ddof=0)` | exact(含 NaN 換手) |
| 退化輸入 | `analyze` 等 | 定義:常數序列夏普無定義 → 不得產生 edge 證據 | exact(3 種常數/近常數序列) |

容差設定:exact 類 1e-9~1e-12(或整數逐一相同);statistical 類取 Monte Carlo 標準誤 × ~3 再加已文件化設計差異
(SPA vs arch:|Δp| ≤ 0.06,reps=4000;StepM 決策不一致率 ≤ 5%;permutation null p95 比值偏離 ≤ 3%)。

## 修正後結果(本分支,實際輸出)

python 3.13.18 · numpy 2.5.3 · pandas 3.0.5 · scipy 1.18.1 · statsmodels 0.15.0 · arch 8.0.0

| check | 類型 | 參考實作 | cases | max abs err | max rel err | 容差 | 結果 | 備註 |
|---|---|---|---:|---:|---:|---:|:---:|---|
| statshim.norm_cdf | exact | scipy.stats.norm.cdf | 2214 | 2.22e-16 | 1.98e-13 | 1e-12 (rel) | ✅ | 相對誤差只在左尾 (x<0, Φ>1e-300) 計;絕對誤差全域 |
| statshim.norm_ppf | exact | scipy.stats.norm.ppf | 4125 | 1.42e-14 | 1.72e-13 | 1e-09 (abs) | ✅ | 無例外;次正規數 p 最大誤差 6.8e-08(容差 1e-6,另計);含 p∈{0,1,NaN,越界} |
| statshim.skew / kurtosis | exact | scipy.stats.skew / kurtosis (bias=True) | 114 | 2.78e-17 | 1.58e-16 | 1e-09 (rel) | ✅ | 常數序列刻意分歧(shim 回 0/3,scipy 回 NaN),另案斷言,不計入 |
| judge_web.expected_max_sharpe | exact | scipy.stats.norm.isf(論文公式) | 36 | 1.78e-15 | 3.12e-16 | 1e-10 (rel) | ✅ | N 到 1e15;N<2 → 0 |
| E[max SR] 閉式近似 vs 精確積分 | informational | scipy.integrate.quad | 5 | 0.0358 | 0.0233 | 0.05 (rel) | ✅ | 論文近似本身的誤差(非實作 bug) |
| judge_web.deflated_sharpe(+PSR) | exact | pandas std(ddof=1) + scipy skew/kurtosis/norm | 184 | 3.89e-16 | 3.89e-16 | 1e-09 (abs) | ✅ | 含 proxy 路徑、n_trials=1、NaN/inf 混入、常數、N=1e9 |
| DSR p_value 尾端相對精度 | exact | scipy.stats.norm.sf | 40 | 2.12e-13 | 2.12e-13 | 1e-06 (rel) | ✅ | 強 edge 下 p≪1e-16 時 1-Φ(z) 會被抵銷成 0 |
| 退化波動(常數序列) | exact | 定義:夏普無定義 → 不得給 edge 證據 | 3 | 0 | 0 | 0 (abs) | ✅ | 3/3 fail-closed |
| judge_web.pbo_cscv | exact | naive CSCV loop + scipy.stats.rankdata | 32 | 0 | 0 | 1e-12 (abs) | ✅ | 全部逐位一致 |
| judge_web._circular_block_bootstrap_means | exact | naive 顯式索引 CBB(同亂數流) | 6 | 4.16e-16 | 4.16e-16 | 1e-12 (abs) | ✅ | 含 L=1、L=T、T 不整除 L |
| CBB 平均數變異 vs 解析值 | statistical | 解析公式(Künsch/Politis-Romano) | 3 | 0.0133 | 0.0133 | 0.03 (rel) | ✅ | B=40000,MC 相對誤差 ~1% |
| _spa_pvalues / _romano_wolf_adj_p | exact | Hansen(2005)/Romano-Wolf(2016) naive loop | 4 | 0 | 0 | 1e-12 (abs) | ✅ | 同一組 bootstrap 平均數,比演算法本身 |
| _auto_block_len(lag-1 ACF) | exact | statsmodels.tsa.stattools.acf | 5 | 0 | 0 | 0 (abs) | ✅ | block 長整數逐一相同 |
| Hansen SPA / White RC(studentized) | statistical | arch.bootstrap.SPA(circular, studentize) | 12 | 0.0524 | 0.0524 | 0.06 (abs) | ✅ | reps=4000;p_upper 即 Hansen 的 SPA_u = studentized White Reality Check |
| 原版 White RC(非 studentized)vs 引擎 p_upper | informational | arch.bootstrap.RealityCheck(studentize=False) | 12 | 0.0524 | 0.0524 | 0.15 (abs) | ✅ | 不同統計量,只記錄差距 |
| Romano-Wolf StepM 拒絕集 | statistical | arch.bootstrap.StepM(circular, studentize) | 12 | 0.0121 | 0.0121 | 0.05 (abs) | ✅ | 逐候選決策不一致率 0.012(2/165) |
| judge_web.permutation_null | exact | naive 顯式索引 CBB(長度 T、偏差校正) | 6 | 1.33e-15 | 1.33e-15 | 1e-09 (abs) | ✅ | 含 T 不整除 L、L=1、L=T |
| permutation_null vs 古典 t 檢定 | statistical | scipy.stats.t.ppf / ttest_1samp | 900 | 0.0067 | 0.0067 | 0.03 (abs) | ✅ | null p95 / t 分位 = T=60:0.995±0.013、T=250:1.003±0.008、T=1000:1.007±0.006;p 值平均 Δ=-0.0001 |
| judge_web.compute_metrics | exact | pandas cumprod/cummax + Sortino-Price 下方偏差 | 43 | 0 | 0 | 1e-09 (abs) | ✅ | 最差欄位=sharpe (0) |
| judge_web.cost_stress | exact | pandas dropna + std(ddof=0) | 20 | 3.55e-15 | 3.55e-15 | 1e-09 (abs) | ✅ | 含 2% NaN 換手(剔除不填 0) |

## 修正前結果(`master` @ bba2c29 跑同一套比對)

| check | 類型 | 參考實作 | cases | max abs err | max rel err | 容差 | 結果 | 備註 |
|---|---|---|---:|---:|---:|---:|:---:|---|
| statshim.norm_cdf | exact | scipy.stats.norm.cdf | 2214 | 2.22e-16 | 1 | 1e-12 (rel) | ❌ | 相對誤差只在左尾 (x<0, Φ>1e-300) 計;絕對誤差全域 |
| statshim.norm_ppf | exact | scipy.stats.norm.ppf | 4125 | 0.0788 | 0.00941 | 1e-09 (abs) | ❌ | 例外 2 次;次正規數 p 最大誤差 inf(容差 1e-6,另計);含 p∈{0,1,NaN,越界} |
| statshim.skew / kurtosis | exact | scipy.stats.skew / kurtosis (bias=True) | 114 | 2.78e-17 | 1.58e-16 | 1e-09 (rel) | ✅ | 常數序列刻意分歧(shim 回 0/3,scipy 回 NaN),另案斷言,不計入 |
| judge_web.expected_max_sharpe | exact | scipy.stats.norm.isf(論文公式) | 36 | 0.00705 | 0.00088 | 1e-10 (rel) | ❌ | N 到 1e15;N<2 → 0 |
| E[max SR] 閉式近似 vs 精確積分 | informational | scipy.integrate.quad | 5 | 0.0358 | 0.0233 | 0.05 (rel) | ✅ | 論文近似本身的誤差(非實作 bug) |
| judge_web.deflated_sharpe(+PSR) | exact | pandas std(ddof=1) + scipy skew/kurtosis/norm | 184 | 4.59e+15 | 4.59e+15 | 1e-09 (abs) | ❌ | 含 proxy 路徑、n_trials=1、NaN/inf 混入、常數、N=1e9 |
| DSR p_value 尾端相對精度 | exact | scipy.stats.norm.sf | 40 | 1 | 1 | 1e-06 (rel) | ❌ | 強 edge 下 p≪1e-16 時 1-Φ(z) 會被抵銷成 0 |
| 退化波動(常數序列) | exact | 定義:夏普無定義 → 不得給 edge 證據 | 3 | 3 | 3 | 0 (abs) | ❌ | const +0.1%: dsr=1 sharpe=7.32e+16 ok=True verdict=likely-real; const + 1e-17 jitter: dsr=0.989 sharpe=2.63e+16 ok=True verdict=likely-real; const + 1e-12 jump: dsr=0.978 sharpe=2.75e+11 ok=True verdict=likely-real |
| judge_web.pbo_cscv | exact | naive CSCV loop + scipy.stats.rankdata | 32 | 0.397 | 0.397 | 1e-12 (abs) | ❌ | rand T=125 N=15: engine=0.2597 ref=0.3398; rand T=161 N=39: engine=0.3485 ref=0.3820; rand T=135 N=3: engine=0.6190 ref=0.9156; rand T=433 N=3: engine=0.5097 ref=0.8636; rand T=164 N=13: engine=0.1688 ref=0.1959; rand T=178 N=3: engine=0.6753 ref=0.9654; rand T=86 N=19: engine=0.4048 ref=0.4784; rand T=411 N=9: engine=0.0584 ref=0.1418; rand T=249 N=19: engine=0.2013 ref=0.2489; rand T=339 N=3: engine=0.5833 ref=0.9805; rand T=434 N=35: engine=0.0639 ref=0.0768; odd N=3: engine=0.9253 ref=0.9978; odd N=5: engine=0.4513 ref=0.8377; odd N=7: engine=0.3117 ref=0.5671; duplicate columns: engine=0.1071 ref=0.2197; offset 1e3 (moments cancellation): engine=0.8279 ref=0.8582 |
| judge_web._circular_block_bootstrap_means | exact | naive 顯式索引 CBB(同亂數流) | 6 | 4.16e-16 | 4.16e-16 | 1e-12 (abs) | ✅ | 含 L=1、L=T、T 不整除 L |
| CBB 平均數變異 vs 解析值 | statistical | 解析公式(Künsch/Politis-Romano) | 3 | 0.0133 | 0.0133 | 0.03 (rel) | ✅ | B=40000,MC 相對誤差 ~1% |
| _spa_pvalues / _romano_wolf_adj_p | exact | Hansen(2005)/Romano-Wolf(2016) naive loop | 4 | 0 | 0 | 1e-12 (abs) | ✅ | 同一組 bootstrap 平均數,比演算法本身 |
| _auto_block_len(lag-1 ACF) | exact | statsmodels.tsa.stattools.acf | 5 | 0 | 0 | 0 (abs) | ✅ | block 長整數逐一相同 |
| Hansen SPA / White RC(studentized) | statistical | arch.bootstrap.SPA(circular, studentize) | 12 | 0.0524 | 0.0524 | 0.06 (abs) | ✅ | reps=4000;p_upper 即 Hansen 的 SPA_u = studentized White Reality Check |
| 原版 White RC(非 studentized)vs 引擎 p_upper | informational | arch.bootstrap.RealityCheck(studentize=False) | 12 | 0.0524 | 0.0524 | 0.15 (abs) | ✅ | 不同統計量,只記錄差距 |
| Romano-Wolf StepM 拒絕集 | statistical | arch.bootstrap.StepM(circular, studentize) | 12 | 0.0121 | 0.0121 | 0.05 (abs) | ✅ | 逐候選決策不一致率 0.012(2/165) |
| judge_web.permutation_null | exact | naive 顯式索引 CBB(長度 T、偏差校正) | 6 | 0.322 | 0.322 | 1e-09 (abs) | ❌ | 含 T 不整除 L、L=1、L=T |
| permutation_null vs 古典 t 檢定 | statistical | scipy.stats.t.ppf / ttest_1samp | 900 | 0.113 | 0.113 | 0.03 (abs) | ❌ | null p95 / t 分位 = T=60:0.887±0.012、T=250:0.958±0.008、T=1000:0.978±0.006;p 值平均 Δ=-0.0016 |
| judge_web.compute_metrics | exact | pandas cumprod/cummax + Sortino-Price 下方偏差 | 43 | inf | inf | 1e-09 (abs) | ❌ | 最差欄位=sortino (inf) |
| judge_web.cost_stress | exact | pandas dropna + std(ddof=0) | 20 | 3.55e-15 | 3.55e-15 | 1e-09 (abs) | ✅ | 含 2% NaN 換手(剔除不填 0) |

## 不一致之處與修正

| # | 檢定 | 不一致(修正前實測) | 根因 | 修正 | 影響 |
|---|---|---|---|---|---|
| 1 | **退化輸入 → DSR / 夏普 / 判決** | 常數 +0.1%/期 的序列:夏普 **7.3e16**、DSR **1.0**、`analyze` 判 **likely-real 86 分**;近常數(1e-17、1e-12 擾動)同樣 likely-real | 浮點下 `std(常數)` ≈ 1e-19 而非 0,`std > 0` 判斷漏接 | 新增 `_is_degenerate_sd`(std ≤ 1e-9·\|mean\| 即每期 \|SR\| ≥ 1e9 視為無波動):`_sharpe_periodic`→0、`deflated_sharpe` sr=0/偏峰回常態值、`compute_metrics.sharpe`→NaN;`analyze` 新增結構化拒審 `zero_variance_reject`(zh 訊息 + app.js en 字串);statshim 動差同門檻 | **fail-open 修補**(最嚴重)。登記於 INTEGRITY_MATRIX |
| 2 | **PBO** | 奇數 N 時系統性低估:N=3 差 0.30~0.40(例:0.51 vs 0.86)、N=5 0.45 vs 0.84;偶數 N 全部逐位一致 | 引擎用 `λ < 0`;論文 PBO = ∫_{-∞}^{0} f(λ)dλ。N 奇數時 IS 冠軍落在 OOS 中位數 ⇒ λ=0,被漏數(N=3 漏掉整個中位名次) | 改 `λ ≤ 0`(與論文定義、本引擎 fail-closed 哲學一致) | 奇數欄 matrix 的 PBO 變高(更保守);60 組奇數欄雜訊 matrix:1 組 inconclusive → 0 組(全 likely-overfit);既有 168 測試全綠 |
| 3 | **PBO 數值** | 1e3 位移資料:0.828 vs 0.858 | `Σx²/n − mean²` 災難性抵銷 → 變異被夾到 1e-18,排名變雜訊 | 先扣各欄全樣本均值再累積動差(變異數平移不變),夏普時加回 | 只影響量級異常的輸入 |
| 4 | **Permutation null** | null p95 對 t 精確分位的比值:T=60 **0.887**、T=250 **0.958**、T=1000 0.978(皆 ≥3σ 顯著)⇒ 門檻偏低 = **反保守** | (a) 重抽長度用 m·L > T 期(分布偏瘦);(b) CBB 平均數變異的有限樣本下偏 (1−L/T)——FWER 底座 R15 已校正,這裡漏了 | 重抽截到 T 期(最後一塊取 rem);平均數乘 1/√(1−L/T)(與 `run_fwer_gates` 的 `spread_corr` 同一校正) | 修後 0.995 / 1.003 / 1.007(皆在 1σ 內)。sample_genuine p95 1.141→1.168、perm p 0.009→0.011,判決不變 |
| 5 | **statshim.norm_cdf** | 左尾相對誤差:x=−8 為 2%,x ≤ −8.3 直接回 0(scipy:Φ(−10)=7.6e−24) | ½(1+erf(x/√2)) 在左尾抵銷 | 改 ½·erfc(−x/√2) | 左尾相對誤差 ≤ 2e−13 |
| 6 | **statshim.norm_ppf** | p ≤ 1e−320 丟 **OverflowError**;p=1e−17 誤差 **0.078**(宣稱 < 1e−9) | Halley 修正的 exp(x²/2) 溢位;修正步依賴 #5 已抵銷的 Φ | 上半部用對稱轉到下尾;Halley 改寫成等價的 (Φ(x)/p−1)·√(2π)·exp(ln p + x²/2) | 正規數 p 誤差 ≤ 1.4e−14;次正規數 p ≤ 6.8e−8(雙精度解析度極限),不再丟例外 |
| 7 | **E[max SR]** | N=1e12 相對誤差 1.3e−6;N=1e15 絕對誤差 0.007 | Φ⁻¹(1−1/N) 的 1−1/N 有捨入 | 改 −Φ⁻¹(1/N)(= scipy `norm.isf`) | ≤ 3e−16 |
| 8 | **DSR p 值** | 強 edge 時 p 值相對誤差 100%(回 0) | `p = 1 − DSR` 抵銷 | 直接取 Φ(−z) | 相對誤差 ≤ 2e−13 |
| 9 | **Sortino** | 負報酬全相同或只有一筆時回 NaN;一般情況系統性高估 | 分母用「負報酬子集的 std」(去均值),非 Sortino & Price 的下方偏差 | 改 √(mean(min(r,0)²)),對全部期數、目標 0 | 只影響顯示(Sortino 不進判分)。sample_genuine 2.61→2.42 |
| 10 | **statshim.skew/kurtosis** | 近常數序列回雜訊比值(任意大數) | `m2 == 0` 判斷漏接浮點雜訊 | 同 #1 門檻,回常態值 0 / 3 | 與 scipy 刻意分歧(scipy 回 NaN),已文件化 + 測試釘住 |

## 已確認一致(無需修正)

- **Hansen SPA 演算法**、**Romano-Wolf 調整後 p 值**、**CBB 底座**、**自適應 block 長(lag-1 ACF)**、**cost_stress**、**MDD/CAGR/Calmar/vol**:與參考逐位一致(誤差 0 ~ 4e−16)。
- **SPA vs arch**(consistent/lower/upper):12 組最大 |Δp| = 0.052(容差 0.06);**StepM vs arch**:165 個候選決策只有 2 個不一致(1.2%),兩者引擎 p_adj 分別為 0.110、0.150(α=0.10 邊界附近),且都是 arch 拒絕、引擎不拒——方向偏保守,符合 iid studentizer + 偏差校正的設計。
- **E[max SR] 閉式近似**本身相對精確積分的誤差 ≤ 2.3%(N=10…10⁴),是 Bailey & López de Prado 公式的固有近似,非實作錯誤。

## 刻意保留的設計差異(文件化,不修)

| 差異 | 引擎 | 參考 | 理由 |
|---|---|---|---|
| SPA/StepM 的 studentizer | iid se(s/√T)+ CBB 偏差校正 1/√(1−L/T) | arch:kernel 長期變異 | R15 校準手術的結論(低噪 studentizer 才校準);兩者在 iid 下等價,差異已含在容差內 |
| White RC 版本 | 只提供 studentized 版(Hansen SPA_u) | arch `RealityCheck(studentize=False)` 為 White (2000) 原版 | studentized 版在異質變異候選下檢力較好;與原版的差距記錄在 informational 列(≤ 0.052) |
| bootstrap p 值慣例 | (1+#)/(B+1) | arch:#/B | 差 ≤ 1/B,前者保證 p>0(保守) |
| SPA 重心化門檻 | `dbar ≤ −thr` 才重心化 | Hansen:`dbar < −thr` | 測度為零的差異 |
| T ≤ 15 時 loglog 項 | 設 0 | Hansen 式在 T≤e^e 無定義 | `min_obs=100`,預設路徑不可達 |
| PBO 名次並列 | `rankdata(method="max")` 等價(並列算較好名次) | 論文未規定 | 只在欄位完全重複時發生;參考實作採同一慣例並在重複欄案例中比對 |
| PBO 分塊 | `np.linspace` 邊界、T < 4·S 時降塊 | 論文要求 S 等分 | 參考實作採同規則(這是設計而非被測對象) |
| 常數序列偏度/峰度 | 回 0 / 3 | scipy 回 NaN + 警告 | DSR 端已有退化守衛;回常態值讓 shim 為純函數、不丟警告 |
| DSR 試驗池 vs SR̂ 的 ddof | 池用 `_sharpe_periodic`(ddof=0),SR̂ 用 ddof=1 | 論文未區分 | 相對差 O(1/T)(T=250 時 0.4%);改動會擾動既有校準釘,收益不成比例,**本次不改**,列為後續可選 |

## 本次決策(未停下詢問,採最合理預設)

1. **分支**:依需求建 `test/reference-check-20261104`(從 `master` @ bba2c29),draft PR 到 `master`,不合併。
2. **White Reality Check 的對應**:引擎沒有獨立 RC 函式;將 `spa["p_upper"]`(Hansen SPA_u)視為 studentized RC 與 arch 比對,原版非 studentized RC 列為 informational。
3. **PBO 的 λ=0**:採論文積分定義 `λ ≤ 0`(保守),而非「嚴格低於中位數」的口語描述。
4. **退化門檻 1e−9**:std ≤ 1e−9·|mean| ⇔ 每期 |SR| ≥ 1e9,任何真實回測都不可能,不誤傷低波動真策略。
5. **arch 加進測試相依與 CI**:SPA/StepM/RC 只有 arch 有成熟實作;仍是**測試側**相依,引擎 runtime 不變。
6. **README 數字**:測試數 142→168、statshim 行數 129→155、`math.erf`→`math.erfc`(README 釘測試會檢查)。
