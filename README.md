# Edge Validator — 策略照妖鏡

**把你的回測丟進來,它假設你是錯的,然後用學界標準的統計檢定試著反駁你。**
過得了關的,才可能是真 edge;過不了的,恭喜你在賠真錢之前就知道了。

### 👉 [**線上 Live Demo — hades60414-sys.github.io/edge-validator**](https://hades60414-sys.github.io/edge-validator/) 👈

零安裝、零註冊、資料不上傳——打開就能玩(內建兩份示範資料:一份真 edge、一份過擬合)。

![Edge Validator 首屏](docs/screenshots/01-hero.png)

---

## 為什麼做這個

這套裁判制度不是憑空發明的。它萃取自一座量化策略農場的殘酷經驗:**4,800+ 次策略試驗、0 個通過誠實的樣本外驗證**。農場的結論是——免費資料+散戶資本下,可部署的 alpha 幾乎不存在;回測裡那些漂亮的曲線,絕大多數是把雜訊擬合成了形狀。

但那套「不放水的裁判」本身有價值。與其再蓋一個幫你「找到看起來會賺的策略」的回測平台,不如把裁判獨立出來,**在你把錢放進一個過擬合的回測之前攔下你**。這就是這個產品的全部。

## 它長什麼樣

上傳逐期報酬(或淨值曲線),按下「照妖」,得到一份**白話判決書**——先給不懂統計的人一句話結論,再給懂統計的人完整檢定證據:

![判決卡:通過七道閘的真 edge 樣本](docs/screenshots/02-verdict-genuine.png)

其中「你 vs 純運氣」這張圖,把你的報酬順序隨機洗牌上千次,看你贏得過幾 % 的隨機版本:

![你贏過 99% 的隨機打亂版本](docs/screenshots/03-null-vs-luck.png)

介面完整支援中英雙語(判決主文、七道閘、方法論全部雙語化):

![English verdict card](docs/screenshots/04-verdict-en.png)

## 架構:整台統計引擎跑在你的瀏覽器裡

![架構圖](docs/architecture.svg)

這個站沒有後端。不是「後端很小」,是**沒有**——GitHub Pages 只送靜態檔,你的 CSV 從進到出都不離開瀏覽器。做到這件事有幾個實際要解的難題:

### 1. Python 統計引擎 → 瀏覽器內執行(Pyodide / WebAssembly)

裁判引擎(`engine/judge_web.py`,約 770 行)是純 Python + numpy/pandas,透過 [Pyodide](https://pyodide.org) 編譯成 WASM 在瀏覽器內跑。同一份引擎程式碼在 CPython 下直接 `pytest`(25 個測試),前端只是把它注入 Pyodide——**測試環境與生產環境是同一份程式碼**,不存在「JS 重寫一遍然後兩邊算不一樣」的問題。

### 2. 沒有 scipy 怎麼辦:純 numpy 的 `statshim`

DSR 需要常態分布的 CDF/PPF、偏度、峰度——通常這是 `scipy.stats` 的活,但 scipy 在 Pyodide 裡是幾十 MB 的負擔。解法是 `engine/statshim.py`:用 stdlib 的 `math.erf` 實作 `norm_cdf`,用 Acklam 有理逼近 + Halley 修正實作 `norm_ppf`(對照 scipy 誤差 < 1e-9),偏度/峰度手算動差、對齊 scipy 的預設行為。129 行換掉整個 scipy 依賴。

### 3. 雜訊硬化 + fail-closed:裁判不能被「幸運的雜訊」騙過

對抗審查時發現:高欄數 × 短樣本下,純雜訊矩陣偶爾也能抽到一條撐過 DSR 的曲線。修法是讓 DSR 的試驗數懲罰沿倍率序列遞增,直到把雜訊壓回地板;若放大到封頂(32×)**仍然**壓不下去,引擎不會聳肩放行,而是標記 `capped_escape` 並強制**不判 likely-real**——無法排除過擬合時,寧可 fail-closed。這是整個產品的裁判哲學:**有疑慮就不放行**。

### 4. 中英雙語 i18n

全站字串(含判決主文的動態模板)走 `data-i18n` 屬性 + 字典切換,語言偏好存 localStorage、首繪前就設好 `<html lang>`。中文為主(觀眾是台灣金融圈),英文完整可用。

### 5. 體感工程

Pyodide 首次載入要下載約 10–15MB 的數值套件——載入條做了分段進度(啟動 Python → 載入 NumPy/Pandas → 注入引擎 → 自我測試),之後瀏覽器快取。CSV 解析支援 UTF-8 / Big5 / cp950(台灣券商匯出檔的現實)、百分比與千分位符號、淨值曲線自動轉報酬。

## 七道誠實閘

| # | 閘 | 擋什麼騙局 |
|---|-----|-----------|
| 1 | **DSR** 通縮夏普(Bailey & López de Prado) | 扣掉「你試了幾種參數才挑到這條曲線」的運氣成分 |
| 2 | **PBO / CSCV** 回測過擬合機率 | 樣本內最優,樣本外墊底的機率(矩陣模式) |
| 3 | **Hansen SPA** 優越性檢定 | 資料挖掘後,真的贏過基準嗎(矩陣模式) |
| 4 | **Romano–Wolf StepM** | 多重比較家族誤差修正後的倖存篩選(矩陣模式) |
| 5 | **成本壓力測試** | 手續費/滑價 ×1/×3/×6 後,夏普還正嗎 |
| 6 | **隨機重排 null** | 打亂報酬順序上千次,你贏得過隨機嗎 |
| 7 | **對照基準** | vs 0050 買進持有等被動基準,你有加值嗎 |

綜合判決三態:**可能真實 / 結論不明 / 疑似過擬合**。過關 ≠ 會賺——它只代表「沒發現明顯的過擬合/挖掘偏誤」。

## 隱私與誠實聲明

- **你的資料不離開瀏覽器。** 所有計算在你本機的 Pyodide 內跑,CSV 不上傳任何伺服器、無任何追蹤。關掉分頁,一切歸零。
- **這不是投資建議。** 它是統計工具,不推薦任何策略、不叫你買賣。
- **統計通過是必要非充分條件。** 真金白銀前,請務必做前向(walk-forward)驗證與小額實測。

## 技術棧

| 層 | 選擇 |
|----|------|
| 前端 | Vanilla JS + 手寫 CSS(無框架、無 build step,`app.js` 約 2,000 行) |
| 統計引擎 | Python(numpy / pandas)+ 自製 `statshim`(取代 scipy) |
| 執行環境 | Pyodide 0.26(Python-in-WASM,瀏覽器內) |
| 測試 | pytest(引擎 25 測試,CPython 下直接跑同一份引擎碼) |
| 部署 | GitHub Pages,純靜態、零後端 |

```
index.html / app.js / style.css   ← 前端(解析、i18n、圖表、判決渲染)
engine/judge_web.py               ← 統一裁判引擎(唯一入口 analyze(payload) → verdict)
engine/statshim.py                ← 純 numpy 的 scipy 替身
engine/test_engine.py             ← 引擎測試
benchmarks/baselines.json         ← 對照基準(隨站台靜態載入)
docs/                             ← 截圖與架構圖
```

---

## English (TL;DR)

**Edge Validator** is a client-side forensic lens for backtests: upload your strategy's per-period returns and it assumes you are wrong, then tries to refute you with academically standard tests — Deflated Sharpe Ratio, PBO/CSCV, Hansen's SPA, Romano–Wolf StepM, cost stress (×1/×3/×6), permutation nulls, and passive benchmarks.

**Try it live: [hades60414-sys.github.io/edge-validator](https://hades60414-sys.github.io/edge-validator/)** — no install, no signup, no upload.

Architecture highlights:

- **Zero backend.** The full Python/numpy referee engine runs *inside your browser* via Pyodide (WASM). Your CSV never leaves your machine.
- **Same code in test and prod.** The engine is plain Python, unit-tested under CPython with pytest, and injected verbatim into Pyodide — no JS reimplementation drift.
- **No scipy needed.** A 129-line pure-numpy `statshim` replaces scipy.stats (norm CDF via `math.erf`, PPF via Acklam's approximation + Halley refinement, |err| < 1e-9).
- **Fail-closed by design.** When escalated noise-hardening still can't rule out overfitting (capped-escape), the referee refuses to call "likely real." When in doubt, it does not pass you.
- **Born from 4,800+ strategy trials with 0 honest out-of-sample survivors.** This tool is the referee system distilled from that farm — built to stop you before you fund an overfit backtest.

*Not investment advice. Passing the gates is necessary, not sufficient — always walk-forward test before real money.*
