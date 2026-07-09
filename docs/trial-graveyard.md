# 試驗墓地:「4,800+ 次試驗、0 個存活」的完整佐證

> Edge Validator 的裁判制度萃取自一座私有量化策略農場。README 與站上反覆引用一個數字:
> **4,800+ 次策略試驗、0 個通過誠實的樣本外驗證**。
> 這份文件交代這個數字是怎麼記帳的、判死流程長什麼樣、以及——依照本產品「誠實優先」的靈魂——
> **這個數字本身的證據力上限在哪裡**(它不是本結論最強的證據,最強的是繞過它也成立的那條鏈)。
>
> 來源:農場 2026-07-07 的總體診斷書與紅隊自查報告(私有 repo,內含策略細節,不公開;
> 本文為脫敏摘錄,只保留總量統計與方法論,不含任何策略參數、程式碼或帳戶資訊)。

---

## 1. 總量統計(截至 2026-07-07,均經工具實核)

| 項目 | 數字 | 記帳方式 |
|---|---|---|
| 相異試驗總數(分母) | **4,821** | 全域 `seen_ids` 帳本:每個「策略構型 × 參數組」得一個唯一 id,重複送審不重計 |
| 策略名冊(registry)總數 | 42 | 曾被收錄進候選名冊的策略 |
| 其中:已退役 | 33 | 每週全場公平重判後單向退役(退役不得復活) |
| 其中:低信心 diversifier | 9 | DSR 落在 0.10–0.60 區間,制度誠實標明「分散者」而非「alpha 種子」 |
| 其中:高信心 survivor | **0** | 從未有任何策略通過高信心 DSR 地板(0.60) |

「0 存活」的精確含義:**4,821 個相異試驗的分母下,沒有任何一條策略通過
以該分母通縮後的 DSR 高信心地板**。曾有 2 名策略短暫掛過 survivor 狀態,
後查明是資料缺欄造成的假象(回測宇宙裡混入全零報酬的殭屍序列),依制度退役——
survivor 軌從未有過真貨。

## 2. 試驗怎麼記帳(為什麼分母可信)

- **唯一 id 記帳**:每個相異試驗(策略構型 × 參數組)在送審時登記唯一 id,
  進全域 `seen_ids` 帳本。分母 4,821 是帳本長度,不是事後回憶。
- **holdout 快照凍結**:每條策略收錄當下,樣本外(holdout)成績量測一次、
  快照凍結、**永不重算**——堵死「多算幾次挑好的那次」。
- **holdout 查詢帳本 + 預算**:對樣本外資料的每一次查詢都記帳、有預算上限,
  防止透過反覆窺看 holdout 把樣本外「用成」樣本內。
- **噪音池雙尺**:嫁接/衍生策略的顯著性,對照「繼承自同族的熱雜訊池」而非
  冷雜訊池——防止拿更弱的 null 灌水。
- **收錄驗屍閘**:收錄前強制檢查回測宇宙的資料完整性(缺欄殭屍序列攔在門外)。

## 3. 判死流程(一條策略要活下來得過什麼)

1. **DSR 地板**:Deflated Sharpe Ratio(Bailey & López de Prado)以全域分母通縮,
   高信心地板 0.60。試驗越多,地板實質越高——這是多重檢定的代價,制度不減免。
2. **PBO / CSCV**:回測過擬合機率,樣本內贏家在樣本外墊底的機率。
3. **去洩漏檢查**:樣本內外之間的選擇洩漏顯式量測(農場實測洩漏係數高達 0.81,
   即帳面績效大半來自「挑到好窗」而非真 edge)。
4. **成本壓力**:手續費/滑價 ×3、×6 壓測(農場實測:×3 後 Sharpe 剩約 1.0、
   ×6 剩約 0.66,而小市值標的的真實滑價可能是假設的 5–20 倍)。
5. **每週全場公平重判**:所有在冊策略每週用同一把尺重新評,退化即單向退役。
6. **對抗式驗收**:每個影響結論的變更,交一個乾淨上下文(fresh-context)的
   審查者對抗把關——三天內攔下 4 個會讓結論放水的真 bug。

## 4. 誠實限定:這個數字的證據力上限(請務必讀)

農場對「4,821 → 0」這個數字自己做過紅隊自查,結論是**它是整條論證裡最弱的一環**,
原因:DSR 地板隨分母膨脹——分母搜到 4,821 時,地板實質上已被推到「史上頂級 alpha」
等級的門檻。所以「0 存活」嚴格說只證明「**沒有史詩級 edge**」,不能單獨證明
「沒有任何 edge」。

真正扛住「免費資料+散戶資本下沒有可部署 alpha」這個結論的,是**繞過 DSR 也成立的
誠實證據鏈**:

- 樣本內限定(IS-only)重育後,holdout Sharpe 僅約 **0.3–0.5**(毛的、未扣實際摩擦);
- 選擇洩漏係數 ~0.81(帳面好看大半來自挑窗);
- 正報酬的大宗被歸因為 **beta 包裝**(跟著市場漲,不是 alpha);
- 年換手 ~83× 之下,成本 ×3/×6 壓測所剩無幾,而真實滑價可能遠高於假設;
- 容量上限估計僅數萬~二十萬美元等級。

**適用範圍也要誠實**:以上結論的證據母體是「日線價量訊號挖掘」這一個範式
(免費/易得資料、散戶資本)。對範式內的窄結論,農場自評信心 85–90%;
對「散戶完全無錢可賺」的全稱版,只有 60–70%——因為對不存在於 K 線裡的
機制性機會(如制度性配額、事件驅動)零檢定力。

Edge Validator 繼承的正是這套「連自己的招牌數字都要拆穿」的裁判哲學:
**有疑慮就不放行(fail-closed),講結論先講證據力上限。**

## 5. 脫敏聲明

本文自兩份私有報告摘錄,**只保留**總量統計、記帳方法、判死流程與紅隊自查結論;
**不含**策略名稱/參數/程式碼、持倉/帳戶資訊、個人路徑,亦無法從本文回推任何策略。

---

## English summary (TL;DR)

The "4,800+ trials, 0 survivors" claim on the Edge Validator README comes from a private
quant strategy farm (as of 2026-07-07): a global `seen_ids` ledger counted **4,821 distinct
trials** (strategy config × parameter set, deduplicated), a 42-strategy registry with 33
retired + 9 low-confidence diversifiers, and **0 strategies ever passing the high-confidence
DSR floor (0.60)** deflated by that full denominator. Anti-self-deception machinery: frozen
holdout snapshots (measured once, never recomputed), a holdout query ledger with budgets,
noise-pool nulls for derived strategies, weekly one-way re-judging, and adversarial
fresh-context review of every result-affecting change.

Honest caveat (the farm red-teamed its own headline number): with a 4,821 denominator the
DSR floor inflates to "all-time-great alpha" territory, so the count alone only proves *no
epic edge*. The stronger chain that survives without DSR: IS-only re-bred holdout Sharpe of
only ~0.3–0.5 gross, selection leakage ~0.81, returns mostly beta-wrapped, ~83× annual
turnover that dies under 3–6× cost stress, and capacity in the tens of thousands of USD.
Scope: daily price-volume signal mining on free data at retail scale (85–90% self-assessed
confidence for that narrow claim; only 60–70% for any universal "retail can't make money"
claim). No strategy details, code, or account information are included here by design.
