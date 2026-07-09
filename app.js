/* ============================================================================
   Edge Validator — app.js
   Pyodide 掛載 + CSV 解析 + call engine.analyze + 手繪圖表渲染。
   全程 client-side。關鍵步驟都 console.log,方便主控用瀏覽器工具驗。
   ============================================================================ */
'use strict';

const PYODIDE_VERSION = 'v0.26.4';
const ENGINE_FILES = ['statshim.py', 'judge_web.py', '__init__.py'];

// ---- 全域狀態 ----
const State = {
  pyodide: null,
  engineReady: false,
  loading: false,
  parsed: null,      // { mode, dates, returns|matrix, colNames, srcName, nRows, note }
  baselines: null,   // benchmarks.json 的 benchmarks 物件
  lang: 'zh-tw',     // 'zh-tw' | 'en' — 目前介面語言
  lastOut: null,     // 最近一次 analyze 回傳(切語言時用來重繪判決)
  lastParsed: null,  // 最近一次分析用的 parsed(重繪淨值圖 x 軸標籤要它)
  lastBenchNote: '', // 最近一次的基準對齊提示(切語言時重繪 warnings 用)
};

// ---- 短手 ----
const $ = (id) => document.getElementById(id);
const el = (tag, cls, html) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
};
const log = (...a) => console.log('[edge-validator]', ...a);
const warn = (...a) => console.warn('[edge-validator]', ...a);

// ============================================================================
// 0. i18n — 中英雙語字典 + t() 機制(不引第三方庫)
// ============================================================================
// STRINGS[lang][key]。值可為字串或 (…args)=>字串(需插值/分支的動態文案)。
// 靜態 HTML 文案掛 data-i18n / data-i18n-html / data-i18n-title,由 applyStaticI18n 套用。
// 動態判決文案(app.js 生成)呼叫 t(key, ...args)。
// 英文以量化圈(r/algotrading)口吻撰寫,專業、地道、保留誠實 nuance,非機翻。
const STRINGS = {
  'zh-tw': {
    // —— 頂欄 / 引擎狀態 ——
    engine_idle: '引擎待命',
    engine_loading: '載入中…',
    engine_ready: '引擎就緒',
    engine_failed: '引擎失敗',

    // —— HERO ——
    hero_kicker: 'FORENSIC BACKTEST LAB · 客戶端運算',
    hero_h1: '你的 edge 是真的,<br>還是把<span class="accent">雜訊</span>擬合成了曲線?',
    hero_lede: '大部分回測平台幫你「找到看起來會賺的策略」。這一台相反——它<strong>假設你的策略是假的</strong>,'
      + '然後用一整套學界標準的統計檢定,試著反駁你。過得了關的,才可能是真 edge;過不了的,'
      + '恭喜你在賠真錢之前就知道了。',
    hero_chip1: '資料不離開你的瀏覽器',
    hero_chip2: '最多七道統計閘 · 依資料啟用',
    hero_chip3: '純靜態,零後端上傳',

    // —— STEP 01 上傳 ——
    step01_no: 'STEP 01 / 上傳',
    step01_h: '丟進你的回測報酬',
    step01_p: '一欄逐期報酬率、或「日期,報酬」兩欄、或多策略矩陣(日期 + 多欄)。支援 UTF-8 / Big5 / cp950 編碼、百分比與千分位符號、淨值曲線(會自動轉報酬)。',
    drop_big: '拖放 CSV 到這裡,或點擊選檔',
    drop_sub: '.csv / .txt · 最多幾千列 · 全程在你本機運算',
    file_remove: '✕ 移除',
    sample_label: '沒有資料?先玩玩示範 —',
    sample_genuine: '有真 edge 的樣本<em>過得了關的長相</em>',
    sample_overfit: '過擬合的樣本<em>被照妖鏡抓包的長相</em>',

    // —— STEP 02 參數 ——
    step02_no: 'STEP 02 / 校準',
    step02_h: '設定裁判參數',
    step02_p: '最關鍵的是「你試了幾種參數」——這決定了通縮夏普(DSR)要扣掉多少運氣成分。誠實填,別自欺。',
    param_trials_label: '你試了幾種參數 / 策略變體?',
    param_trials_hint: 'Number of trials。你在挑到這條曲線之前,總共回測過幾組參數?試越多,靠運氣撞到漂亮結果的機率越高,DSR 會據此通縮。誠實填。',
    param_trials_lo: '1 = 沒調參',
    param_trials_hi: '50+ = 大量掃描',
    param_ppy_label: '每年期數',
    param_ppy_hint: 'Periods per year。日資料≈252、週資料≈52、月資料≈12、加密全年無休≈365。留空且有日期欄時會自動推算。',
    param_ppy_auto: '自動(從日期推算)',
    param_ppy_252: '日(股票 ≈ 252)',
    param_ppy_365: '日(加密 = 365)',
    param_ppy_52: '週 = 52',
    param_ppy_12: '月 = 12',
    param_ppy_4: '季 = 4',
    param_ppy_sub: '無日期欄時,預設 252。',
    param_bench_label: '對照基準',
    param_bench_hint: '拿一條被動基準跟你的策略比。基準依你資料的【日期交集】對齊(缺日不以 0 填補);交集不足 80% 時誠實略過比較並提示,不放水。',
    param_bench_none: '不比較',
    param_bench_desc: '選一條看你有沒有贏過無腦買進持有。',

    run_btn: '照妖 · 開始統計檢定',
    run_prep: '準備引擎…',
    run_running: '運算中…',

    // —— 載入條 ——
    loader_init: '正在載入裁判引擎(Pyodide + NumPy/Pandas,約 10–15MB)…',
    loader_start_py: '正在啟動 Pyodide 執行環境…',
    loader_load_pkg: '正在載入 NumPy / Pandas(數值套件,約 10MB)…',
    loader_inject: '正在注入裁判引擎程式碼…',
    loader_selftest: '正在自我測試引擎…',
    loader_step0: '啟動 Python',
    loader_step1: '載入 NumPy / Pandas',
    loader_step2: '注入引擎',
    loader_step3: '自我測試',
    loader_foot: '首次會多花幾秒下載數值套件,之後由瀏覽器快取,一切都在你本機跑。',
    loader_eta: (s) => `預估約再 ${s} 秒`,
    loader_eta_soon: '快好了…',

    // —— 判決書 ——
    verdict_no: 'VERDICT / 判決書',
    verdict_h: '照妖鏡的裁決',
    verdict_label: '綜合判定',
    score_cap: '誠實分數 / 100',
    gauge_lo: '過擬合',
    gauge_mid: '不明',
    gauge_hi: '可能真實',
    reasons_h: '裁判怎麼看(人話)',
    flags_h: '紅旗',
    plain_frame: '<b>統計過關 ≠ 會賺。</b>這只是照妖鏡,不是水晶球——它只回答「這條曲線像不像雜訊/過度最佳化」,不預測未來、不給買賣建議。你的資料全程留在瀏覽器、不上傳。往下捲有給懂統計的人看的完整檢定細節。',
    tech_divider: '以下給想深究的人:完整統計證據',

    // —— 指標 / 閘 / 圖表 區塊標題 ——
    metrics_no: 'METRICS / 績效指標',
    metrics_h: '風險調整後績效',
    metrics_p: '這些是原始績效數字(不含誠實性校正)——好看的數字容易騙人,所以上面那句白話結論才是重點。',
    gates_no: 'GATES / 統計檢定',
    gates_h: '統計閘的細節(最多七道,依資料形態啟用)',
    gates_p: 'DSR(通縮夏普)與隨機重排必跑;PBO、SPA/Romano–Wolf 需多策略矩陣;成本壓力需換手率、對照基準需你選一條。未啟用的閘標「略過」——略過不算通過。上方白話重點就是從這幾道閘翻譯來的。',
    charts_no: 'SCOPE / 視覺化',
    charts_h: '看得見的證據',
    chart_equity_h: '淨值曲線 vs 對照基準',
    chart_equity_cap: '從 1.0 起算的資金曲線。基準對齊到你資料的期數。',
    chart_null_h: '你 vs 純運氣',
    chart_null_cap: '把你的報酬順序隨機洗牌上千次(抹掉漂移、只留波動),看「靠運氣能刷到的年化夏普」長怎樣。你的真值站得越右、贏過越多隨機版本,越不像雜訊。',

    // —— 方法論 ——
    method_no: 'METHODOLOGY / 方法論',
    method_h: '最多七道閘,各擋什麼騙局',
    method_p: '這套裁判制度萃取自一座量化策略農場(4,800+ 次試驗、0 個通過誠實樣本外驗證——<a href="https://github.com/hades60414-sys/edge-validator/blob/master/docs/trial-graveyard.md" target="_blank" rel="noopener">完整佐證與記帳方式</a>)。它的存在就是為了在你把錢放進過擬合的回測前攔下你。並非每次七道全跑:依你給的資料形態啟用,略過不算通過。',
    m1_h: '通縮夏普', m1_p: '扣掉「你試了幾種參數才挑到這條曲線」的運氣成分。試越多,門檻越高。',
    m2_h: '回測過配機率 PBO', m2_p: '樣本內選到的最佳者,樣本外還站得住嗎?墊底的機率就是過擬合機率(僅矩陣模式)。',
    m3_h: 'Hansen 優越性檢定', m3_p: '資料挖掘後,你的策略真的贏過基準嗎?控制家族誤差(僅矩陣模式)。',
    m4_h: 'Romano–Wolf 篩選', m4_p: '多重比較修正後,還能倖存的候選有幾個(僅矩陣模式)。',
    m5_h: '成本壓力測試', m5_p: '手續費/滑價 ×1 / ×3 / ×6 後,夏普還正嗎?edge 常被摩擦吃光(需提供換手率)。',
    m6_h: '隨機重排檢定', m6_p: '抹掉漂移、保留波動,重排上千次。你贏得過隨機打亂的版本嗎?',
    m7_h: '對照基準', m7_p: 'vs 0050 定期定額 / 農場的誠實 beta 策略。相對無腦買進持有,你有加值嗎?(需選基準)',
    m8_h: '綜合判決', m8_p: '三態:<b style="color:var(--real)">可能真實</b> / <b style="color:var(--incon)">結論不明</b> / <b style="color:var(--overfit)">疑似過擬合</b>。過關≠會賺。',

    // —— 免責 / 頁尾 ——
    disc_h: '免責聲明 · 請務必讀完',
    disc1: '<b>這不是投資建議。</b>它是統計工具,不推薦任何策略、不叫你買賣。對照組的農場策略是「誠實的 beta 參考基準」,不是 alpha、不是推銷。',
    disc2: '<b>統計通過是必要非充分條件。</b>過了這些閘不代表會賺,只代表「沒發現明顯的過度擬合/挖掘偏誤」。真金白銀前請務必做前向(walk-forward)驗證與小額實測。',
    disc3: '<b>你的資料不離開瀏覽器。</b>所有計算用 Pyodide(瀏覽器內的 Python)在你本機跑,CSV 不上傳任何伺服器。關掉分頁,一切歸零。',
    footer_brand: 'EDGE&nbsp;<b>VALIDATOR</b> · 策略照妖鏡',
    footer_note: '裁判引擎萃取自 auto-quant-btc 農場的「不被自己騙」制度,經多代理對抗審查。純靜態站,部署於 GitHub Pages。© 2026',

    // —— 錯誤 / 解析提示 ——
    err_no_data: '請先上傳 CSV 或載入示範資料。',
    err_engine_fail: (msg) => `<b>裁判引擎載入失敗。</b><br>${msg}<br>`
      + '這通常是網路無法連到 Pyodide CDN(cdn.jsdelivr.net)。請檢查連線後重試。',
    err_analyze_fail: (why) => '分析失敗:' + why,
    err_analyze_unknown: '未知錯誤',
    err_runtime: (msg) => '<b>運算發生例外。</b><br>' + msg,
    err_csv_fail: (msg) => `<b>CSV 解析失敗。</b><br>${msg}<br>`
      + '支援格式:①單欄報酬或淨值 ②「日期,數值」兩欄 ③日期 + 多策略矩陣。',
    err_sample_fail: (msg) => '<b>載入示範失敗。</b><br>' + msg,
    bench_load_fail: '對照基準載入失敗(不影響其他檢定)。',
    err_no_pyodide: 'Pyodide 載入器不存在(CDN 未載入)。請確認網路可連 cdn.jsdelivr.net。',
    err_fetch_engine: (url, status) => `抓取 ${url} 失敗(HTTP ${status})`,

    // —— 檔案 pill ——
    file_meta_matrix: (nStrat, nRows) => `矩陣 · ${nStrat} 策略 × ${nRows} 期`,
    file_meta_series: (nRows, hasDate) => `${nRows} 期${hasDate ? ' · 含日期' : ''}`,
    sample_genuine_name: 'sample_genuine.csv(示範)',
    sample_overfit_name: 'sample_overfit.csv(示範)',
    parse_prefix: '解析:',
    strat_col: (i) => `策略${i}`,

    // —— 解析提示(note)——
    note_series_ret: '偵測為逐期報酬序列。',
    note_series_nav: '偵測為淨值序列,已自動轉為逐期報酬。',
    note_dv_ret: '偵測為「日期+報酬」。含日期欄,每年期數可自動推算。',
    note_dv_nav: '偵測為「日期+淨值」,已轉為報酬。含日期欄,每年期數可自動推算。',
    note_single_ret: '單策略報酬。',
    note_single_nav: '單策略(淨值→報酬)。',
    note_matrix: (n) => `偵測為 ${n} 策略矩陣。將啟用 PBO / SPA / Romano-Wolf 全套閘;主序列取樣本內夏普最高者。`,
    // —— 基準對齊提示 ——
    align_bydate_intersect: (hit, len) => `基準依日期【交集】對齊(${hit}/${len} 日兩邊都有;缺日直接略過、不以 0 填補,避免稀釋基準)。`,
    align_skip_overlap: (hit, len) => `基準日期交集不足(${hit}/${len} 日,低於 80%):為避免以 0 填補稀釋基準、讓「贏過基準」變太容易,本次【略過】基準比較。`,
    align_len_nodate_overlap: '基準日期與你的資料不重疊,改用長度對齊(取基準最近的等長一段,無填補),僅供概略對照。',
    align_len_nodate: '你的資料無日期欄,基準以長度對齊(取基準最近的等長一段,無填補),僅供概略對照。',
    align_len_partial: (blen, len) => `你的資料無日期欄且基準較短(${blen}/${len} 期):以基準全段對照、不補 0,僅供概略對照。`,
    align_skip_short: (blen, len) => `基準長度不足(${blen}/${len} 期,低於 80%)且無日期可交集:本次【略過】基準比較(不以 0 填補灌長度)。`,
    // —— 錯誤(解析) ——
    perr_too_few: '檔案內容太少:至少需要一列標題(可略)加幾列資料。',
    perr_body_few: '資料列太少(去掉標題後不足 2 列)。請確認每列是一期報酬或淨值。',
    perr_single_num: '抓不到可解析的數字。請確認欄位是數值(報酬率或淨值)。',
    perr_dv_num: '第二欄抓不到數字。請確認格式為「日期,報酬」或「日期,淨值」。',
    perr_cols: '欄位結構無法辨識。',

    // —— 圖表 empty / 標籤 / legend ——
    equity_empty: '資料太少,無法繪製資金曲線。<br><span>至少需要兩期報酬。</span>',
    null_empty: '樣本太短,未進行隨機重排檢定。<br><span>需要至少約 8 期報酬。</span>',
    equity_period: (i) => `第 ${i} 期`,
    equity_legend_strat: (x) => `你的策略(期末 ${x}×)`,
    equity_legend_bench: (x) => `對照基準(期末 ${x}×)`,
    null_aria: (real, beat) => `隨機打亂虛無分布。你的真實年化夏普 ${real}`
      + (beat != null ? `,勝過約 ${beat}% 的隨機版本` : '') + '。',
    null_flag: (x) => `你 · ${x}`,
    null_passline: (x) => `過關線 ${x}`,
    null_xcaption: '年化夏普 —— 把你的報酬順序隨機洗牌上千次能刷到的分布',
    null_legend_dist: '隨機打亂的成績分布(示意形狀)',
    null_legend_thresh: '95% 過關門檻',
    null_legend_real: (x) => `你的真實夏普 ${x}`,
    // 金句橫幅(作品集必殺一行)
    null_beat_verb_solid: '穩穩勝過',
    null_beat_verb_beat: '勝過',
    null_beat_verb_only: '只贏過',
    null_banner: (verb, pctTxt, pv) =>
      `<span class="nb-lead">你${verb}</span>`
      + `<span class="nb-num">${pctTxt}<small>%</small></span>`
      + `<span class="nb-tail">的隨機打亂版本 <em>· p=${pv}</em></span>`,

    // —— 指標網格標籤 ——
    mt_sharpe: '年化夏普', mt_sortino: 'Sortino', mt_cagr: 'CAGR', mt_vol: '年化波動',
    mt_maxdd: '最大回撤', mt_calmar: 'Calmar', mt_final: '期末淨值',
    mt_conc: '報酬集中度', mt_nperiods: '樣本期數',

    // —— 閘卡 badge ——
    b_na: 'N/A',
    b_high: '高信心', b_floor: '過雜訊地板', b_noise: '疑似雜訊',
    b_beatrand: '勝過隨機', b_loserand: '輸給隨機', b_nothresh: '未達門檻',
    b_nocrash: '未系統性崩盤', b_oosbottom: '樣本外墊底',
    b_survivor: '有倖存者', b_wipeout: '全軍覆沒',
    b_x3pos: '×3 仍正', b_x3neg: '×3 翻負',
    b_beatbench: '贏過基準', b_notbeatbench: '未贏基準',

    // —— DSR 閘 ——
    g_dsr_title: '通縮夏普 DSR', g_dsr_sub: 'Deflated Sharpe · 多重檢定校正',
    g_dsr_note: (n) => `扣掉你試了 ${n} 種參數的運氣成分後,真有 edge 的機率。門檻:雜訊地板 0.60、高信心 0.95。`,
    // 硬化揭露:引擎有效 n_trials > 使用者申報值(matrix 雜訊硬化上調)時,照實講清楚。
    g_dsr_note_hardened: (declared, eff) => `你申報試過 ${declared} 種,引擎依雜訊硬化規則保守上調為 ${eff} 種做通縮(防止幸運的雜訊欄矇混過關)。扣掉這份運氣後,真有 edge 的機率。門檻:雜訊地板 0.60、高信心 0.95。`,
    g_dsr_prob: '真實有 edge 機率', g_dsr_sr: '年化夏普', g_dsr_sr0: '通縮門檻 SR₀', g_dsr_p: 'p 值',
    // —— permutation 閘 ——
    g_perm_title: '隨機重排檢定', g_perm_sub: 'Matched-null · block-bootstrap',
    g_perm_note: (n) => `抹掉漂移、保留波動,bootstrap ${n} 次。你的真實夏普要贏過 95% 的隨機版本才算過。`,
    g_perm_matrix_caveat: '注意:矩陣模式下此閘只檢「挑出的最佳欄」那一條序列,對「挑最好那欄」的選擇偏誤會失真(偏樂觀)——即使這裡顯示「不像純運氣」,矩陣模式請以 FWER/PBO 為準。',
    g_perm_real: '真實年化夏普', g_perm_p95: '隨機 95 百分位', g_perm_p: 'p 值(越低越真)',
    // —— PBO 閘 ——
    g_pbo_title: '回測過配機率 PBO', g_pbo_sub: 'CSCV · 樣本內外一致性',
    g_pbo_note: '樣本內選到的最佳者,在樣本外墊底的機率。> 0.50 是典型過擬合特徵。',
    g_pbo_pbo: '過配機率 PBO', g_pbo_nstrat: '策略數', g_pbo_ncomb: '組合數',
    // —— SPA / Romano-Wolf 閘 ——
    g_spa_title: 'SPA + Romano-Wolf', g_spa_sub: 'Hansen 優越性 · 家族誤差修正',
    g_spa_note: '資料挖掘後,最佳策略真的贏過基準嗎?多重比較修正後還能倖存幾條。',
    g_spa_note_zero_nobench: '⚠ 你沒選對照基準:此檢定實際比的是【絕對報酬】(vs 零基準),「倖存」≠「贏過基準」。',
    g_spa_note_zero_fallback: (cov) => `⚠ 你的基準只覆蓋 ${cov}% 期數、無法逐期配對:此檢定誠實改比【絕對報酬】(vs 零基準),「倖存」≠「贏過基準」——與下方基準比較卡語意不同。`,
    g_spa_p: 'SPA p 值', g_spa_best: '最佳候選', g_spa_nrej: '倖存候選數',
    // —— 成本壓力閘 ——
    g_cost_title: '成本壓力測試', g_cost_sub: 'round-trip bps · ×1/×3/×6',
    g_cost_note: '手續費/滑價放大後,edge 還撐得住嗎?×3 是保守情境。',
    g_cost_x1: '×1 夏普', g_cost_x3: '×3 夏普', g_cost_x6: '×6 夏普',
    // —— 基準比較閘 ——
    g_bench_title: '對照基準', g_bench_sub: '相對買進持有的加值',
    g_bench_note: '跟被動基準比。夏普沒贏、或超額報酬為負,先確認多做這些交易值不值得。',
    g_bench_vs: '策略 vs 基準夏普', g_bench_cagr: '基準 CAGR', g_bench_excess: '超額 CAGR',

    // —— 白話首屏 定調小標 ——
    tag_real: '看起來像真本事',
    tag_overfit: '八成是雜訊擬合出來的',
    tag_incon: '還不能下定論',

    // —— 白話一句話結論(plainHeadlineSentence)——
    ph_money_profit: (c) => `這條曲線看起來很賺(年化約 ${c})`,
    ph_money_loss: (c) => `這條曲線其實在虧(年化約 ${c})`,
    ph_money_neutral: '先看這條曲線的體質',
    // 短日曆跨度(<0.5 年):年化 = 短窗外插易膨風 → 主數字降級為期間累計報酬,不做年化外插。
    ph_money_profit_span: (tot, yrs) => `這條曲線這段期間累計賺約 ${tot}(資料僅約 ${yrs} 年,短窗不做年化外插)`,
    ph_money_loss_span: (tot, yrs) => `這條曲線這段期間累計虧約 ${tot}(資料僅約 ${yrs} 年,短窗不做年化外插)`,
    ph_money_span_suffix: '(短窗外插,易膨風)',
    // 短窗醒目警告條(從小字警語升級)
    span_warn_label: '短窗外插警告',
    ph_beat_sharpe_less: (x) => `、風險調整後(夏普)贏過無腦買進持有,但年化其實少賺約 ${x}——你是用更低的波動換到的`,
    ph_beat_win: (x) => `、而且贏過無腦買進持有基準${x}`,
    ph_beat_win_more: (x) => `,多賺約 ${x}`,
    ph_beat_cagr_only: (x) => `、年化雖多賺約 ${x},但風險調整後(夏普)沒贏過無腦買進持有(多賺是靠多冒險換的)`,
    ph_beat_lose: '、但沒贏過無腦買進持有基準',
    ph_real_single: (money, beat) => `${money}${beat}。扣掉運氣成分後統計上站得住——但你只丟了【一條】曲線(沒告訴我你試過幾種參數),`
      + '對「剛好幸運抽中這一條」的分辨力有限,likely-real 也可能只是好運。真金白銀前,務必用你沒看過的新資料前向實測再定奪。',
    ph_real_multi: (money, beat, n) => `${money}${beat}。扣掉「試了 ${n} 次剛好矇到」的運氣成分後,統計上還是站得住——`
      + '像是真本事,不只是運氣好。(仍非保證會賺,真錢前請小額前向驗證。)',
    ph_overfit: (money, beat) => `${money}${beat}。但統計上這漂亮數字八成是「試很多次剛好矇到」或承擔高風險換來的,`
      + '不像可靠的真本事——別憑它下真錢。在你賠錢之前照出來,正是這台機器的用處。',
    ph_incon: (money, beat) => `${money}${beat}。但統計上還不能確定這是真本事,還是剛好運氣好——證據不足以定論,`
      + '建議再累積更多樣本,或做前向測試再定奪。',

    // —— 白話三重點(plainThreePoints)——
    // ① permutation
    pt_perm_pass: (p) => `<b>贏得過隨機打亂嗎:贏了。</b>把你的報酬順序隨機洗牌上千次,你的真實成績仍站在 95% 的洗牌版本之上(p=${p})——這條曲線不像純運氣拼出來的。`,
    pt_perm_noise: (p) => `<b>贏得過隨機打亂嗎:沒贏。</b>把報酬順序隨機洗牌後,超過一半的洗牌版本都能刷出跟你一樣好的成績(p=${p})——這很像是雜訊。`,
    pt_perm_mid: (p) => `<b>贏得過隨機打亂嗎:沒到門檻。</b>你的成績比多數隨機洗牌版本好,但還沒好到能穩穩勝過 95%(p=${p})。`,
    pt_perm_na: '<b>贏得過隨機打亂嗎:</b>樣本太短,沒能做隨機重排檢定。',
    // ② DSR(clause 三態:真試驗池 / SR 標準誤保守通縮 proxy(單序列宣稱 n_trials>1)/ 單條)
    pt_dsr_trial_multi: (n) => `扣掉你試了 ${n} 種參數的運氣後`,
    pt_dsr_trial_multi_proxy: (n) => `以 SR 估計標準誤作試驗離散度的保守下限、通縮「試了 ${n} 種參數」的運氣後(單一序列無真試驗池)`,
    // 硬化揭露:引擎有效 n_trials > 申報值 → 白話重點照實交代上調。
    pt_dsr_trial_hardened: (declared, eff) => `你申報試過 ${declared} 種,引擎依雜訊硬化規則保守上調為 ${eff} 種通縮(防止幸運的雜訊欄過關)後`,
    pt_dsr_trial_single: '你未申報搜參(n_trials=1),以單曲線口徑檢定',
    pt_dsr_high: (clause, prob) => `<b>扣掉「試很多次」的運氣後還站得住嗎:站得住。</b>${clause},真有 edge 的機率約 ${prob}——高信心。`,
    pt_dsr_mid: (clause, prob) => `<b>扣掉「試很多次」的運氣後還站得住嗎:勉強站上雜訊地板。</b>${clause},真有 edge 的機率約 ${prob}(過了 60% 的雜訊地板,但沒到 95% 高信心)。`,
    pt_dsr_low: (clause, prob) => `<b>扣掉「試很多次」的運氣後還站得住嗎:站不住。</b>${clause},真有 edge 的機率只剩約 ${prob}——這漂亮數字多半是挑出來的運氣。`,
    pt_dsr_na: '<b>扣掉「試很多次」的運氣後還站得住嗎:</b>資料不足以估算通縮夏普。',
    // ③ benchmark
    pt_bench_win: (c) => `<b>贏過無腦買進持有嗎:贏了。</b>風險調整後(夏普)勝過被動基準${c}——相對無腦持有有加值。`,
    pt_bench_win_more: (x) => `,年化多賺約 ${x}`,
    pt_bench_half: (x) => `<b>贏過無腦買進持有嗎:半贏。</b>年化多賺約 ${x},但風險調整後(夏普)沒贏——多出來的報酬是靠多冒險換的,不算真占便宜。`,
    pt_bench_lose: (c) => `<b>贏過無腦買進持有嗎:沒贏${c}。</b>先確認多做這些交易值不值得。`,
    pt_bench_lose_less: (x) => `,年化少賺約 ${x}`,
    pt_bench_na: '<b>贏過無腦買進持有嗎:</b>你沒選對照基準(或資料無法對齊),這次略過比較。想比就在上方「對照基準」挑一條。',
    // matrix FWER 句(kindClause 依 fwer.benchmark_kind 分流——aligned=真贏基準;zero=絕對報酬,勿當贏基準)
    pt_fwer_survive: (nStrat, nRej, kindClause) => `<b>一次比 ${nStrat}條策略,幾條經得起考驗:${nRej} 條倖存。</b>`
      + `多重比較修正(把「比越多越容易矇到一條」的運氣扣掉)後,${kindClause}`,
    pt_fwer_kind_aligned: (nRej) => `還有 ${nRej} 條在家族錯誤率(FWER)控制下顯著贏過基準。`,
    pt_fwer_kind_zero_nobench: (nRej) => `${nRej} 條顯著繳出正的【絕對報酬】(vs 零基準——你沒選對照基準,此檢定比的是絕對報酬,不是贏過基準)。`,
    pt_fwer_kind_zero_fallback: (nRej, cov) => `${nRej} 條顯著繳出正的【絕對報酬】(vs 零基準)。你的基準只覆蓋 ${cov}% 期數、無法逐期配對,此檢定誠實改比絕對報酬——與基準比較卡語意不同,勿當「贏過基準」。`,
    pt_fwer_survivor_caveat: '倖存 ≠ 判決:總判決以 DSR/PBO 為準;α=10% 下純雜訊平均每 10 次也會出現 1 次倖存者。',
    pt_fwer_wipe: (nStrat) => `<b>一次比 ${nStrat}條策略,幾條經得起考驗:全軍覆沒。</b>`
      + '把「比越多越容易矇到」的運氣扣掉後,沒有任何一條穩穩過關——這正是「一堆策略挑最好的一條」最典型的雜訊陷阱。',
    pt_pbo: (pbo, tail) => `<b>挑出來的最佳策略,樣本外會不會墊底:</b>過配機率 PBO = ${pbo}${tail}`,
    pt_pbo_over: '(>0.5,典型過擬合特徵——樣本內的冠軍到樣本外常墊底)。',
    pt_pbo_ok: '(≤0.5,沒有系統性崩盤)。',
    pt_matrix_na: '<b>多策略矩陣:</b>樣本期數不足(需 ≥100 期),PBO/SPA 全套閘未啟用。',

    // —— VERDICT_META ——
    vm_real_text: '可能是真的 edge',
    vm_real_sub: '扣掉多重檢定與運氣成分後,證據仍支持這條策略帶了真訊號。但這只代表「沒發現明顯過擬合」,不保證會賺——真錢前務必前向驗證。',
    vm_overfit_text: '疑似過擬合',
    vm_overfit_sub: '多項指標顯示這條曲線八成是把雜訊擬合出來的產物。在賠真錢之前發現它,是這台機器的價值。',
    vm_incon_text: '結論不明',
    vm_incon_sub: '證據不足以判定真偽。通常是樣本太短、訊號不夠乾淨,或剛好卡在門檻。建議補更多樣本或做前向測試再定奪。',
  },

  en: {
    // —— topbar / engine status ——
    engine_idle: 'Engine idle',
    engine_loading: 'Loading…',
    engine_ready: 'Engine ready',
    engine_failed: 'Engine failed',

    // —— HERO ——
    hero_kicker: 'FORENSIC BACKTEST LAB · CLIENT-SIDE',
    hero_h1: 'Is your edge real,<br>or did you fit <span class="accent">noise</span> into a pretty curve?',
    hero_lede: 'Most backtest platforms help you <em>find</em> a strategy that looks profitable. This one does the opposite — it '
      + '<strong>assumes your strategy is fake</strong>, then throws a full battery of academic-grade statistical tests at it, trying to disprove you. '
      + "What survives might be a real edge; what doesn't — congratulations, you found out before betting real money.",
    hero_chip1: 'Your data never leaves the browser',
    hero_chip2: 'Up to seven statistical gates · enabled by your data',
    hero_chip3: 'Fully static, zero backend upload',

    // —— STEP 01 upload ——
    step01_no: 'STEP 01 / UPLOAD',
    step01_h: 'Drop in your backtest returns',
    step01_p: 'A single column of per-period returns, two columns of "date, return", or a multi-strategy matrix (date + many columns). Handles UTF-8 / Big5 / cp950 encodings, percent and thousands separators, and equity curves (auto-converted to returns).',
    drop_big: 'Drop a CSV here, or click to choose a file',
    drop_sub: '.csv / .txt · up to a few thousand rows · computed entirely on your machine',
    file_remove: '✕ Remove',
    sample_label: 'No data yet? Try a demo first —',
    sample_genuine: 'A sample with a real edge<em>what passing the gates looks like</em>',
    sample_overfit: 'An overfit sample<em>what getting caught looks like</em>',

    // —— STEP 02 params ——
    step02_no: 'STEP 02 / CALIBRATE',
    step02_h: 'Set the referee parameters',
    step02_p: 'The one that matters most is "how many parameter sets you tried" — it decides how much luck the Deflated Sharpe (DSR) has to strip out. Fill it in honestly; don\'t kid yourself.',
    param_trials_label: 'How many parameters / strategy variants did you try?',
    param_trials_hint: 'Number of trials. How many parameter sets did you backtest in total before landing on this curve? The more you tried, the higher the odds of stumbling onto a pretty result by luck — DSR deflates accordingly. Be honest.',
    param_trials_lo: '1 = no tuning',
    param_trials_hi: '50+ = heavy sweep',
    param_ppy_label: 'Periods per year',
    param_ppy_hint: 'Periods per year. Daily ≈ 252, weekly ≈ 52, monthly ≈ 12, crypto (24/7) ≈ 365. Leave blank with a date column and it\'s inferred automatically.',
    param_ppy_auto: 'Auto (infer from dates)',
    param_ppy_252: 'Daily (stocks ≈ 252)',
    param_ppy_365: 'Daily (crypto = 365)',
    param_ppy_52: 'Weekly = 52',
    param_ppy_12: 'Monthly = 12',
    param_ppy_4: 'Quarterly = 4',
    param_ppy_sub: 'Defaults to 252 when there is no date column.',
    param_bench_label: 'Benchmark',
    param_bench_hint: 'Compare your strategy against a passive benchmark. It aligns on the DATE INTERSECTION of your data (missing days are never zero-filled); if the intersection covers less than 80%, the comparison is honestly skipped with a note — no fudging.',
    param_bench_none: 'No comparison',
    param_bench_desc: 'Pick one to see whether you beat naive buy-and-hold.',

    run_btn: 'RUN · Start statistical tests',
    run_prep: 'Preparing engine…',
    run_running: 'Computing…',

    // —— loader ——
    loader_init: 'Loading the referee engine (Pyodide + NumPy/Pandas, ~10–15 MB)…',
    loader_start_py: 'Starting the Pyodide runtime…',
    loader_load_pkg: 'Loading NumPy / Pandas (numeric stack, ~10 MB)…',
    loader_inject: 'Injecting the referee engine…',
    loader_selftest: 'Self-testing the engine…',
    loader_step0: 'Boot Python',
    loader_step1: 'Load NumPy / Pandas',
    loader_step2: 'Inject engine',
    loader_step3: 'Self-test',
    loader_foot: 'The first run spends a few extra seconds downloading the numeric stack; after that the browser caches it, and everything runs on your machine.',
    loader_eta: (s) => `~${s}s to go`,
    loader_eta_soon: 'almost done…',

    // —— verdict ——
    verdict_no: 'VERDICT',
    verdict_h: 'The referee\'s ruling',
    verdict_label: 'Overall call',
    score_cap: 'Honesty score / 100',
    gauge_lo: 'Overfit',
    gauge_mid: 'Unclear',
    gauge_hi: 'Likely real',
    reasons_h: 'How the referee sees it (plain English)',
    flags_h: 'Red flags',
    plain_frame: '<b>Passing these tests ≠ it will make money.</b> This is a forensic lens, not a crystal ball — it only answers "does this curve look like noise / over-optimization?" It does not predict the future or give buy/sell advice. Your data stays in the browser and is never uploaded. Scroll down for the full test details, written for the stats-literate.',
    tech_divider: 'For those who want to dig in: the full statistical evidence',

    // —— metrics / gates / charts section heads ——
    metrics_no: 'METRICS',
    metrics_h: 'Risk-adjusted performance',
    metrics_p: 'These are the raw performance numbers (before any honesty correction) — pretty numbers lie easily, which is why the plain-English verdict above is what matters.',
    gates_no: 'GATES',
    gates_h: 'The gates in detail (up to seven, enabled by your data)',
    gates_p: 'DSR (Deflated Sharpe) and the permutation shuffle always run; PBO and SPA/Romano–Wolf need a multi-strategy matrix; cost stress needs turnover and the benchmark needs you to pick one. Gates that don\'t apply are marked "skipped" — a skip never counts as a pass. The plain-English points above are translated straight from these gates.',
    charts_no: 'SCOPE',
    charts_h: 'Evidence you can see',
    chart_equity_h: 'Equity curve vs benchmark',
    chart_equity_cap: 'Capital curve indexed to 1.0. The benchmark is aligned to your data\'s period count.',
    chart_null_h: 'You vs pure luck',
    chart_null_cap: 'We reshuffle your return order thousands of times (killing the drift, keeping only the volatility) to see what annualized Sharpe luck alone can produce. The further right your real value sits — and the more shuffled versions it beats — the less it looks like noise.',

    // —— methodology ——
    method_no: 'METHODOLOGY',
    method_h: 'Up to seven gates, each catching a different con',
    method_p: 'This referee system is distilled from a quant strategy farm (4,800+ trials, 0 survivors of honest out-of-sample validation — <a href="https://github.com/hades60414-sys/edge-validator/blob/master/docs/trial-graveyard.md" target="_blank" rel="noopener">full evidence and how that number is accounted for</a>). Its whole reason to exist is to stop you before you put money into an overfit backtest. Not all seven run every time: gates engage based on the data you provide, and a skip never counts as a pass.',
    m1_h: 'Deflated Sharpe', m1_p: 'Strips out the luck of "how many parameters you tried before picking this curve." The more you tried, the higher the bar.',
    m2_h: 'Backtest overfitting prob. (PBO)', m2_p: 'Does the in-sample winner still hold up out-of-sample? The probability it ends up worst is the overfitting probability (matrix mode only).',
    m3_h: 'Hansen superiority test', m3_p: 'After all that data mining, does your strategy genuinely beat the benchmark? Controls family-wise error (matrix mode only).',
    m4_h: 'Romano–Wolf screening', m4_p: 'After multiple-comparison correction, how many candidates actually survive (matrix mode only).',
    m5_h: 'Cost stress test', m5_p: 'With fees/slippage at ×1 / ×3 / ×6, is the Sharpe still positive? Edges are often eaten alive by friction (requires turnover).',
    m6_h: 'Permutation shuffle test', m6_p: 'Kill the drift, keep the volatility, reshuffle thousands of times. Can you beat the randomly shuffled versions?',
    m7_h: 'Benchmark', m7_p: 'vs 0050 monthly DCA / the farm\'s honest beta strategies. Relative to naive buy-and-hold, did you add anything? (requires picking a benchmark)',
    m8_h: 'Composite verdict', m8_p: 'Three states: <b style="color:var(--real)">Likely real</b> / <b style="color:var(--incon)">Inconclusive</b> / <b style="color:var(--overfit)">Likely overfit</b>. Passing ≠ profit.',

    // —— disclaimer / footer ——
    disc_h: 'Disclaimer · please read in full',
    disc1: '<b>This is not investment advice.</b> It is a statistical tool — it recommends no strategy and tells you nothing to buy or sell. The farm strategies used for comparison are "honest beta reference benchmarks," not alpha, and not a pitch.',
    disc2: '<b>Passing is necessary but not sufficient.</b> Clearing these gates does not mean it will make money — only that "no obvious overfitting / data-mining bias was found." Before risking real capital, always run walk-forward validation and a small live test.',
    disc3: '<b>Your data never leaves the browser.</b> All computation runs locally via Pyodide (Python in the browser); your CSV is never uploaded to any server. Close the tab and everything is gone.',
    footer_brand: 'EDGE&nbsp;<b>VALIDATOR</b> · Honest backtest forensics',
    footer_note: 'The referee engine is distilled from the auto-quant-btc farm\'s "don\'t fool yourself" discipline, hardened by multi-agent adversarial review. Fully static site, deployed on GitHub Pages. © 2026',

    // —— errors / parse notes ——
    err_no_data: 'Please upload a CSV or load a demo first.',
    err_engine_fail: (msg) => `<b>The referee engine failed to load.</b><br>${msg}<br>`
      + 'This is usually a network issue reaching the Pyodide CDN (cdn.jsdelivr.net). Check your connection and retry.',
    err_analyze_fail: (why) => 'Analysis failed: ' + why,
    err_analyze_unknown: 'unknown error',
    err_runtime: (msg) => '<b>A runtime exception occurred.</b><br>' + msg,
    err_csv_fail: (msg) => `<b>CSV parsing failed.</b><br>${msg}<br>`
      + 'Supported formats: (1) a single column of returns or equity, (2) two columns of "date, value", (3) date + multi-strategy matrix.',
    err_sample_fail: (msg) => '<b>Failed to load the demo.</b><br>' + msg,
    bench_load_fail: 'Benchmarks failed to load (other tests are unaffected).',
    err_no_pyodide: 'The Pyodide loader is missing (the CDN did not load). Check that your network can reach cdn.jsdelivr.net.',
    err_fetch_engine: (url, status) => `Failed to fetch ${url} (HTTP ${status})`,

    // —— file pill ——
    file_meta_matrix: (nStrat, nRows) => `matrix · ${nStrat} strategies × ${nRows} periods`,
    file_meta_series: (nRows, hasDate) => `${nRows} periods${hasDate ? ' · with dates' : ''}`,
    sample_genuine_name: 'sample_genuine.csv (demo)',
    sample_overfit_name: 'sample_overfit.csv (demo)',
    parse_prefix: 'Parsing: ',
    strat_col: (i) => `Strategy ${i}`,

    // —— parse notes ——
    note_series_ret: 'Detected as a per-period return series.',
    note_series_nav: 'Detected as an equity series; auto-converted to per-period returns.',
    note_dv_ret: 'Detected as "date + return." Has a date column, so periods-per-year can be inferred automatically.',
    note_dv_nav: 'Detected as "date + equity," converted to returns. Has a date column, so periods-per-year can be inferred automatically.',
    note_single_ret: 'Single strategy, returns.',
    note_single_nav: 'Single strategy (equity → returns).',
    note_matrix: (n) => `Detected as a ${n}-strategy matrix. The full PBO / SPA / Romano-Wolf gates are enabled; the primary series is the one with the highest in-sample Sharpe.`,
    // —— benchmark alignment notes ——
    align_bydate_intersect: (hit, len) => `Benchmark aligned on the date INTERSECTION (${hit}/${len} days present on both sides; missing days are dropped, never zero-filled, to avoid diluting the benchmark).`,
    align_skip_overlap: (hit, len) => `Benchmark date intersection too thin (${hit}/${len} days, under 80%): rather than zero-fill the gaps (which dilutes the benchmark and makes "beats benchmark" too easy), the comparison is SKIPPED this run.`,
    align_len_nodate_overlap: "The benchmark dates don't overlap yours; fell back to length-alignment (the benchmark's most recent equally-long stretch, no padding), rough comparison only.",
    align_len_nodate: "Your data has no date column, so the benchmark is length-aligned (its most recent equally-long stretch, no padding), rough comparison only.",
    align_len_partial: (blen, len) => `Your data has no date column and the benchmark is shorter (${blen}/${len} periods): the benchmark's full stretch is used as-is, no zero-padding — rough comparison only.`,
    align_skip_short: (blen, len) => `Benchmark too short (${blen}/${len} periods, under 80%) with no dates to intersect: the comparison is SKIPPED this run (no zero-padding to fake the length).`,
    // —— parse errors ——
    perr_too_few: 'Too little content: at least one header row (optional) plus a few data rows are needed.',
    perr_body_few: 'Too few data rows (fewer than 2 after removing the header). Make sure each row is one period of return or equity.',
    perr_single_num: 'No parseable numbers found. Make sure the column is numeric (returns or equity).',
    perr_dv_num: 'No numbers found in the second column. Make sure the format is "date, return" or "date, equity".',
    perr_cols: 'Could not recognize the column structure.',

    // —— chart empty / labels / legend ——
    equity_empty: 'Too little data to draw the capital curve.<br><span>At least two periods of returns are needed.</span>',
    null_empty: 'Sample too short — no permutation test was run.<br><span>Needs at least ~8 periods of returns.</span>',
    equity_period: (i) => `Period ${i}`,
    equity_legend_strat: (x) => `Your strategy (ends at ${x}×)`,
    equity_legend_bench: (x) => `Benchmark (ends at ${x}×)`,
    null_aria: (real, beat) => `Shuffled null distribution. Your real annualized Sharpe is ${real}`
      + (beat != null ? `, beating about ${beat}% of the random versions` : '') + '.',
    null_flag: (x) => `You · ${x}`,
    null_passline: (x) => `pass line ${x}`,
    null_xcaption: 'Annualized Sharpe — the distribution luck can produce by reshuffling your returns thousands of times',
    null_legend_dist: 'Shuffled-return distribution (illustrative shape)',
    null_legend_thresh: '95% pass threshold',
    null_legend_real: (x) => `Your real Sharpe ${x}`,
    // headline banner (the portfolio one-liner)
    null_beat_verb_solid: 'cleanly beat',
    null_beat_verb_beat: 'beat',
    null_beat_verb_only: 'only beat',
    null_banner: (verb, pctTxt, pv) =>
      `<span class="nb-lead">You ${verb}</span>`
      + `<span class="nb-num">${pctTxt}<small>%</small></span>`
      + `<span class="nb-tail">of the shuffled-random versions <em>· p=${pv}</em></span>`,

    // —— metric grid labels ——
    mt_sharpe: 'Ann. Sharpe', mt_sortino: 'Sortino', mt_cagr: 'CAGR', mt_vol: 'Ann. vol',
    mt_maxdd: 'Max drawdown', mt_calmar: 'Calmar', mt_final: 'Final equity',
    mt_conc: 'Return concentration', mt_nperiods: 'Sample size',

    // —— gate badges ——
    b_na: 'N/A',
    b_high: 'high conf.', b_floor: 'above noise floor', b_noise: 'likely noise',
    b_beatrand: 'beats random', b_loserand: 'loses to random', b_nothresh: 'below threshold',
    b_nocrash: 'no systematic crash', b_oosbottom: 'bottoms out-of-sample',
    b_survivor: 'survivors found', b_wipeout: 'wiped out',
    b_x3pos: '×3 still +', b_x3neg: '×3 turns −',
    b_beatbench: 'beats benchmark', b_notbeatbench: "doesn't beat benchmark",

    // —— DSR gate ——
    g_dsr_title: 'Deflated Sharpe (DSR)', g_dsr_sub: 'Deflated Sharpe · multiple-testing correction',
    g_dsr_note: (n) => `The probability of a real edge after stripping out the luck of trying ${n} parameter set(s). Thresholds: noise floor 0.60, high confidence 0.95.`,
    // Hardening disclosure: engine's effective n_trials > user-declared value (matrix noise-hardening).
    g_dsr_note_hardened: (declared, eff) => `You declared ${declared} parameter set(s) tried; under its noise-hardening rule the engine conservatively raised the deflation count to ${eff} (to keep a lucky noise column from sneaking through). The probability of a real edge after stripping out that luck. Thresholds: noise floor 0.60, high confidence 0.95.`,
    g_dsr_prob: 'P(real edge)', g_dsr_sr: 'Ann. Sharpe', g_dsr_sr0: 'Deflated threshold SR₀', g_dsr_p: 'p-value',
    // —— permutation gate ——
    g_perm_title: 'Permutation shuffle test', g_perm_sub: 'Matched-null · block-bootstrap',
    g_perm_note: (n) => `Drift removed, volatility kept, bootstrapped ${n} times. Your real Sharpe must beat 95% of the random versions to pass.`,
    g_perm_matrix_caveat: 'Note: in matrix mode this gate only tests the single best-picked column, so it is distorted (optimistic) with respect to the selection bias of "picking the best column" — even if it reads "not pure luck" here, defer to FWER/PBO in matrix mode.',
    g_perm_real: 'Real ann. Sharpe', g_perm_p95: 'Random 95th pct', g_perm_p: 'p-value (lower = more real)',
    // —— PBO gate ——
    g_pbo_title: 'Backtest overfit prob. (PBO)', g_pbo_sub: 'CSCV · in/out-of-sample consistency',
    g_pbo_note: 'The probability the in-sample winner ends up worst out-of-sample. > 0.50 is a classic overfitting signature.',
    g_pbo_pbo: 'Overfit prob. PBO', g_pbo_nstrat: 'Strategies', g_pbo_ncomb: 'Combinations',
    // —— SPA / Romano-Wolf gate ——
    g_spa_title: 'SPA + Romano-Wolf', g_spa_sub: 'Hansen superiority · FWER control',
    g_spa_note: 'After the data mining, does the best strategy genuinely beat the benchmark? How many survive multiple-comparison correction.',
    g_spa_note_zero_nobench: '⚠ No benchmark selected: this test actually compares ABSOLUTE returns (vs a zero benchmark) — "survivors" ≠ "beat the benchmark."',
    g_spa_note_zero_fallback: (cov) => `⚠ Your benchmark covers only ${cov}% of periods and cannot be paired period-by-period: this test honestly falls back to ABSOLUTE returns (vs zero) — "survivors" ≠ "beat the benchmark," different in meaning from the Benchmark card below.`,
    g_spa_p: 'SPA p-value', g_spa_best: 'Best candidate', g_spa_nrej: 'Survivors',
    // —— cost stress gate ——
    g_cost_title: 'Cost stress test', g_cost_sub: 'round-trip bps · ×1/×3/×6',
    g_cost_note: 'With fees/slippage scaled up, does the edge hold? ×3 is the conservative case.',
    g_cost_x1: '×1 Sharpe', g_cost_x3: '×3 Sharpe', g_cost_x6: '×6 Sharpe',
    // —— benchmark gate ——
    g_bench_title: 'Benchmark', g_bench_sub: 'value added over buy-and-hold',
    g_bench_note: "Compared to a passive benchmark. If the Sharpe doesn't win or excess return is negative, ask whether all that extra trading was worth it.",
    g_bench_vs: 'Strategy vs bench Sharpe', g_bench_cagr: 'Benchmark CAGR', g_bench_excess: 'Excess CAGR',

    // —— plain headline tags ——
    tag_real: 'Looks like genuine skill',
    tag_overfit: 'Most likely noise fitted into a curve',
    tag_incon: 'Not enough to call it yet',

    // —— plain headline sentence ——
    ph_money_profit: (c) => `This curve looks profitable (~${c}/yr)`,
    ph_money_loss: (c) => `This curve is actually losing money (~${c}/yr)`,
    ph_money_neutral: "Let's look at this curve's constitution first",
    // Short calendar span (<0.5y): annualized figures are short-window extrapolations →
    // demote the headline number to the period total return, no annualized extrapolation.
    ph_money_profit_span: (tot, yrs) => `This curve gained about ${tot} over the period covered (only ~${yrs} years of data — no annualized extrapolation on a short window)`,
    ph_money_loss_span: (tot, yrs) => `This curve lost about ${tot} over the period covered (only ~${yrs} years of data — no annualized extrapolation on a short window)`,
    ph_money_span_suffix: ' (short-window extrapolation — easily overstated)',
    span_warn_label: 'SHORT-WINDOW EXTRAPOLATION WARNING',
    ph_beat_sharpe_less: (x) => `, and on a risk-adjusted (Sharpe) basis it beats naive buy-and-hold — but it actually earns about ${x} less per year, which you bought with lower volatility`,
    ph_beat_win: (x) => `, and it beats naive buy-and-hold${x}`,
    ph_beat_win_more: (x) => `, earning about ${x} more per year`,
    ph_beat_cagr_only: (x) => `, and while it earns about ${x} more per year, on a risk-adjusted (Sharpe) basis it does not beat naive buy-and-hold (the extra return was bought by taking more risk)`,
    ph_beat_lose: ", but it didn't beat naively buying and holding the benchmark",
    ph_real_single: (money, beat) => `${money}${beat}. After stripping out luck it holds up statistically — but you only handed me ONE curve (you didn't tell me how many parameters you tried), `
      + 'so its power to tell "genuinely good" from "you happened to draw a lucky one" is limited; likely-real could still just be good luck. Before betting real money, always forward-test it on fresh data you haven\'t seen.',
    ph_real_multi: (money, beat, n) => `${money}${beat}. Even after stripping out the luck of "trying ${n} times and hitting one by chance," it still holds up statistically — `
      + 'this looks like genuine skill, not just luck. (Still no guarantee of profit; before real money, forward-test it at small size.)',
    ph_overfit: (money, beat) => `${money}${beat}. But statistically this pretty number is most likely "tried many times and got lucky once," or bought by taking on high risk — `
      + "it doesn't look like a reliable edge, so don't bet real money on it. Catching it before you lose money is exactly what this machine is for.",
    ph_incon: (money, beat) => `${money}${beat}. But statistically it's still unclear whether this is genuine skill or just luck — the evidence isn't enough to call it. `
      + 'Gather more samples, or run a forward test, before deciding.',

    // —— plain three points ——
    // ① permutation
    pt_perm_pass: (p) => `<b>Beats random shuffling? Yes.</b> Reshuffle your return order thousands of times and your real result still stands above 95% of the shuffled versions (p=${p}) — this curve doesn't look pieced together by luck.`,
    pt_perm_noise: (p) => `<b>Beats random shuffling? No.</b> After reshuffling your return order, over half the shuffled versions match your result (p=${p}) — this looks a lot like noise.`,
    pt_perm_mid: (p) => `<b>Beats random shuffling? Not to threshold.</b> Your result beats most shuffled versions, but not cleanly enough to clear 95% (p=${p}).`,
    pt_perm_na: '<b>Beats random shuffling?</b> Sample too short to run the permutation test.',
    // ② DSR — clauses are full sentence-openers (capitalized); templates no longer prepend
    // "With " (the old assembly produced "With after stripping out…" — R12 LOW fix).
    pt_dsr_trial_multi: (n) => `After stripping out the luck of trying ${n} parameter sets`,
    pt_dsr_trial_multi_proxy: (n) => `After conservatively deflating for the luck of trying ${n} parameter sets — using the Sharpe-ratio standard error as a lower bound on trial dispersion, since a single series carries no true trial pool`,
    // Hardening disclosure: effective n_trials > declared → say so plainly in the three points.
    pt_dsr_trial_hardened: (declared, eff) => `You declared ${declared} parameter set(s) tried, and under its noise-hardening rule the engine conservatively raised the deflation count to ${eff} (to keep a lucky noise column from passing); after stripping out that luck`,
    pt_dsr_trial_single: 'With no parameter search declared (n_trials=1), judged on a single-curve basis',
    pt_dsr_high: (clause, prob) => `<b>Still holds after removing "many tries" luck? Yes.</b> ${clause}, the probability of a real edge is about ${prob} — high confidence.`,
    pt_dsr_mid: (clause, prob) => `<b>Still holds after removing "many tries" luck? Barely, above the noise floor.</b> ${clause}, the probability of a real edge is about ${prob} (past the 60% noise floor, but short of 95% high confidence).`,
    pt_dsr_low: (clause, prob) => `<b>Still holds after removing "many tries" luck? No.</b> ${clause}, the probability of a real edge drops to about ${prob} — this pretty number is mostly cherry-picked luck.`,
    pt_dsr_na: '<b>Still holds after removing "many tries" luck?</b> Not enough data to estimate the Deflated Sharpe.',
    // ③ benchmark
    pt_bench_win: (c) => `<b>Beats naive buy-and-hold? Yes.</b> On a risk-adjusted (Sharpe) basis it beats the passive benchmark${c} — real value added over just holding.`,
    pt_bench_win_more: (x) => `, earning about ${x} more per year`,
    pt_bench_half: (x) => `<b>Beats naive buy-and-hold? Half a win.</b> It earns about ${x} more per year, but on a risk-adjusted (Sharpe) basis it doesn't win — the extra return was bought by taking more risk, so it's not a real free lunch.`,
    pt_bench_lose: (c) => `<b>Beats naive buy-and-hold? No${c}.</b> First make sure all that extra trading is worth it.`,
    pt_bench_lose_less: (x) => `, earning about ${x} less per year`,
    pt_bench_na: '<b>Beats naive buy-and-hold?</b> You picked no benchmark (or the data couldn\'t be aligned), so the comparison is skipped this time. Pick one under "Benchmark" above to compare.',
    // matrix FWER (kindClause branches on fwer.benchmark_kind — aligned = beats benchmark; zero = absolute return, not benchmark-beating)
    pt_fwer_survive: (nStrat, nRej, kindClause) => `<b>Testing ${nStrat}strategies at once, how many hold up: ${nRej} survive.</b> `
      + `After multiple-comparison correction (removing the luck of "the more you compare, the easier to hit one"), ${kindClause}`,
    pt_fwer_kind_aligned: (nRej) => `${nRej} genuinely beat the benchmark under family-wise error (FWER) control.`,
    pt_fwer_kind_zero_nobench: (nRej) => `${nRej} deliver statistically positive ABSOLUTE returns (vs a zero benchmark — no benchmark selected, so this tests absolute return, not benchmark-beating).`,
    pt_fwer_kind_zero_fallback: (nRej, cov) => `${nRej} show statistically positive ABSOLUTE returns (vs a zero benchmark). Your benchmark covers only ${cov}% of periods and cannot be paired period-by-period, so this test honestly falls back to absolute returns — different in meaning from the Benchmark card.`,
    pt_fwer_survivor_caveat: 'Survivors ≠ verdict — the verdict follows DSR/PBO; at α=10%, even pure noise yields a survivor about 1 run in 10.',
    pt_fwer_wipe: (nStrat) => `<b>Testing ${nStrat}strategies at once, how many hold up: wiped out.</b> `
      + 'After removing the "more comparisons, more flukes" luck, not a single one cleanly holds up — this is the textbook noise trap of "pick the best of a pile of strategies."',
    pt_pbo: (pbo, tail) => `<b>Will the chosen best strategy bottom out out-of-sample:</b> overfit probability PBO = ${pbo}${tail}`,
    pt_pbo_over: ' (>0.5, a classic overfit signature — the in-sample champion often ends up last out-of-sample).',
    pt_pbo_ok: ' (≤0.5, no systematic collapse).',
    pt_matrix_na: '<b>Multi-strategy matrix:</b> not enough periods (need ≥100), so the full PBO/SPA gates were not run.',

    // —— VERDICT_META ——
    vm_real_text: 'Likely a real edge',
    vm_real_sub: 'After stripping out multiple testing and luck, the evidence still supports this strategy carrying a real signal. But that only means "no obvious overfitting was found" — it is no guarantee of profit; always forward-test before real money.',
    vm_overfit_text: 'Likely overfit',
    vm_overfit_sub: 'Several metrics show this curve is most likely noise fitted into a shape. Finding it before you lose real money is what this machine is worth.',
    vm_incon_text: 'Inconclusive',
    vm_incon_sub: 'The evidence is not enough to call it real or fake. Usually the sample is too short, the signal not clean enough, or it sits right on the threshold. Gather more samples or run a forward test before deciding.',

    // —— engine reason codes → EN 渲染模板 ——
    // 引擎回 reasons_coded / red_flags_coded / warnings_coded([{code, params}],與 zh 字串
    // 逐位 1:1)。EN 模式用這些模板渲染;zh 模式直接用引擎的中文字串(單一真源)。
    // 數字格式化刻意跟引擎 zh 字串同精度(dsr/pbo/p/sharpe 兩位、超額 CAGR ±一位、集中度整數%)。
    // reasons(rc_)
    // var_proxy=true(引擎:單序列宣稱 n_trials>1、無真試驗池 → 以 SR 標準誤作試驗離散度
    // 下限保守通縮)時,EN 文案照實渲染 proxy 語意,與引擎 zh 字串 1:1 同義。
    rc_dsr_high_confidence: (p) => p.var_proxy
      ? `Deflated Sharpe (DSR)=${fmt(p.dsr, 2)}: a single series carries no true trial pool, so the Sharpe-ratio standard error is used as a conservative lower bound on trial dispersion to genuinely deflate for "${p.n_trials} parameter sets tried" — and the probability of a real edge still stays high (dispersion is a conservative proxy, not a true trial pool).`
      : `Deflated Sharpe (DSR)=${fmt(p.dsr, 2)}: even after correcting for the multiple testing of ${p.n_trials} parameter trials, the probability of a real edge stays high.`,
    rc_dsr_above_noise_floor: (p) => p.var_proxy
      ? `Deflated Sharpe (DSR)=${fmt(p.dsr, 2)}: after a conservative deflation using the Sharpe-ratio standard error as the trial dispersion (single series, no true trial pool, trials=${p.n_trials}), it clears the 0.60 noise floor — but confidence is not top-tier; don't treat it as ironclad.`
      : `Deflated Sharpe (DSR)=${fmt(p.dsr, 2)}: above the 0.60 noise floor, but confidence is not top-tier — don't treat it as ironclad.`,
    rc_dsr_below_noise_floor: (p) => p.var_proxy
      ? `Deflated Sharpe (DSR)=${fmt(p.dsr, 2)} < 0.60: after deflating the luck of "${p.n_trials} parameter sets tried" with the Sharpe-ratio standard error as trial dispersion, the odds of a real edge are under half — highly likely noise.`
      : `Deflated Sharpe (DSR)=${fmt(p.dsr, 2)} < 0.60: after the multiple-testing correction, the odds of a real edge are under half — highly likely noise.`,
    rc_dsr_not_computable: () => 'DSR could not be computed (insufficient sample or variance); excluded from the verdict.',
    rc_capped_escape_fail_closed: (p) => `Noise-hardening scaled the trial count to its cap (${p.n_trials}) and the Deflated Sharpe still hasn't dropped below the 0.60 noise floor — this usually means too many columns / too short a sample, so this in-sample winner is statistically indistinguishable from one lucky draw of noise. The engine fails closed: overfitting cannot be ruled out, so it is not called real.`,
    rc_pbo_high: (p) => `Probability of backtest overfitting (PBO)=${fmt(p.pbo, 2)} > 0.50: the in-sample best pick usually lands near the bottom out-of-sample — a classic overfitting signature.`,
    rc_pbo_ok: (p) => `Probability of backtest overfitting (PBO)=${fmt(p.pbo, 2)} ≤ 0.50: the in-sample winner shows no systematic collapse out-of-sample.`,
    rc_perm_noise: (p) => `Permutation test p=${fmt(p.p, 2)} > 0.50: after shuffling the return order, more than half of the random versions match your real Sharpe — no genuine signal found.`,
    rc_perm_pass: (p) => `Permutation test p=${fmt(p.p, 2)}: your real Sharpe beats 95% of the shuffled versions — the time-series structure carries information.`,
    rc_perm_below_threshold: (p) => `Permutation test p=${fmt(p.p, 2)}: didn't clear the random p95 threshold; the signal isn't clean enough.`,
    rc_cost_x3_positive: (p) => `Sharpe after ×3 cost stress = ${fmt(p.sharpe, 2)}, still positive: some buffer against trading costs.`,
    rc_cost_x3_negative: (p) => `Sharpe after ×3 cost stress = ${fmt(p.sharpe, 2)} ≤ 0: slightly conservative costs flip it negative — friction may eat the whole edge.`,
    rc_bench_beaten: (p) => `Sharpe beats the benchmark${isNum(p.excess_cagr) ? ` (excess CAGR ${fmtSigned(p.excess_cagr * 100, 1)}%)` : ''}: adds value over buy-and-hold.`,
    rc_bench_not_beaten: () => "Sharpe does not beat the benchmark: no clear advantage over buy-and-hold — first ask whether all this extra trading is worth it.",
    rc_concentration_high: (p) => `Return concentration=${fmtPct(p.concentration, 0)}: the single best period contributes close to half (or more) of the returns — performance rides on a few spikes; fragile.`,
    rc_concentration_ok: (p) => `Return concentration=${fmtPct(p.concentration, 0)}: returns are spread fairly evenly, not riding on a few bars.`,
    rc_many_trials_penalty: (p) => `You tried ${p.n_trials} parameter sets: the more you try, the higher the odds of hitting a pretty result by luck — points deducted accordingly.`,
    rc_trials_corrected: (p) => `You tried ${p.n_trials} parameter sets: multiple-testing correction (DSR) has been applied.`,
    rc_sample_short: (p) => `Only ${p.n_periods} periods in the sample: statistical power is weak, so discount every conclusion.`,
    rc_short_calendar_span: (p) => `The data spans only ${fmt(p.span_years, 2)} calendar years (under half a year): annualized Sharpe / CAGR / vol are a short window extrapolated to a full year and easily overstate the magnitude — treat annualized numbers as directional only, not a promise.`,
    rc_closing_likely_real: () => 'Important: passing these tests only means "no obvious overfitting was found" — it does not guarantee future profit. Before real money, always walk-forward validate and live-test at small size.',
    rc_closing_inconclusive: () => "Inconclusive: the evidence isn't enough to call it real or fake. Gather more samples or run a forward test before deciding.",
    rc_closing_likely_overfit: () => "Reminder: even if some metrics look good, the red flags above say this strategy is most likely an overfit artifact — don't bet real money on it.",
    rc_no_data: () => 'No data.',
    // red flags(rf_)
    rf_dsr_below_noise_floor: (p) => `DSR=${fmt(p.dsr, 2)} is below the 0.60 noise floor.`,
    rf_capped_escape: () => 'Capped escape: with the trial count scaled to its cap, DSR still sits above the noise floor — too many columns / too short a sample to rule out overfitting.',
    rf_pbo_high: (p) => `PBO=${fmt(p.pbo, 2)} > 0.50.`,
    rf_perm_noise: (p) => `Permutation p=${fmt(p.p, 2)} > 0.50.`,
    rf_cost_x3_negative: () => 'Sharpe turns negative after ×3 cost stress.',
    rf_concentration_high: (p) => `Single-period concentration ${fmtPct(p.concentration, 0)} ≥ 40%.`,
    rf_sample_short: (p) => `Sample too short (only ${p.n_periods} periods).`,
    // warnings(rw_)
    rw_short_sample: (p) => `Only ${p.n} periods — statistical power is weak; treat the conclusions as indicative only.`,
    rw_high_dim_low_power: (p) => `Heads up: you searched ${p.n_cols} columns over a ${p.n_periods}-period sample (columns ≥ periods). Here even a "passing" Deflated Sharpe is statistically hard to tell apart from one lucky noise winner — the pass has weak power. Re-check with a longer sample or a walk-forward test; don't take it at face value.`,
    rw_fwer_not_computed: (p) => `FWER gates not computed: aligned sample has ${p.n_obs} periods, below the required ${p.min_obs}.`,
    rw_matrix_empty: () => 'Matrix mode, but the matrix is empty.',
    rw_returns_empty: () => 'Returns mode, but the returns series is empty.',
    rw_short_calendar_span: (p) => `The data spans only ${fmt(p.span_years, 2)} calendar years (under 0.5): every annualized number (ann. Sharpe / CAGR / ann. vol) extrapolates a short window to a full year, so their usefulness is limited — accumulate at least half a year before reading annualized figures.`,
    rw_long_series_guard: (p) => `Long series (${p.n} periods): permutation resamples reduced to ${p.n_perm} and bootstrap to ${p.n_boot} (browser performance guard; p-value resolution gets coarser, the test semantics are unchanged).`,
    rw_fwer_bench_fallback_zero: (p) => `SPA / Romano–Wolf benchmark fallback: your benchmark covers only ${Math.round((p.coverage || 0) * 100)}% of periods (${p.bench_len}/${p.n_periods}) and cannot be paired period-by-period, so these tests honestly compare against a ZERO benchmark (absolute return) instead — different in meaning from the Benchmark card, do not read "survivors" as "beat the benchmark."`,
    rw_ppy_fallback: (p) => `Date column failed to parse (${p.error}): annualization frequency fell back to 252 (daily). If your data is not daily, annualized Sharpe / CAGR will be distorted — fix the date format or set periods-per-year explicitly.`,
    // 偵測下沉層(engine detect_and_convert)
    rw_detect_kind_mismatch: (p) => `Series-type detection disagreed${p.col ? ` on column "${p.col}"` : ''}: the browser hinted "${p.js}" but the engine's authoritative check says "${p.py}". The engine's call (${p.py}) was used for the analysis — eyeball your data to make sure it really is ${p.py === 'nav' ? 'an equity/NAV curve' : 'per-period returns'}.`,
    rw_detect_dates_mismatch: (p) => `Date normalization disagreed on ${p.n_diff} row(s) between the browser and the engine (ROC-calendar / format handling). The engine's (Python) normalization was used.`,
  },
};

// t(key, ...args) — 取當前語言字串;函式值就以 args 呼叫。找不到 key 退回 zh-tw 再退回 key 本身。
function t(key, ...args) {
  const table = STRINGS[State.lang] || STRINGS['zh-tw'];
  let v = table[key];
  if (v === undefined) v = STRINGS['zh-tw'][key];
  if (v === undefined) return key;
  return typeof v === 'function' ? v(...args) : v;
}

// codedStrings — 引擎結構化 reason codes → 當前語言字串陣列。
// 引擎保證 coded 與 raw(zh 字串)逐位 1:1:EN 模式且有對應模板 → 用 code+params 渲染英文;
// zh 模式 / 缺 code / 缺模板 → 一律退回引擎原字串(向後相容,永不丟資訊)。
function codedStrings(coded, raw, prefix) {
  const rawArr = Array.isArray(raw) ? raw : [];
  if (State.lang !== 'en' || !Array.isArray(coded) || coded.length !== rawArr.length) return rawArr;
  return coded.map((c, i) => {
    const fallback = rawArr[i] != null ? String(rawArr[i]) : String((c && c.code) || '');
    if (!c || !c.code) return fallback;
    const key = prefix + c.code;
    if (Object.prototype.hasOwnProperty.call(STRINGS.en, key)) return t(key, c.params || {});
    return fallback;
  });
}

// applyStaticI18n — 掃 [data-i18n]/[data-i18n-html]/[data-i18n-title] 節點,套當前語言。
function applyStaticI18n() {
  document.querySelectorAll('[data-i18n]').forEach(node => {
    node.textContent = t(node.getAttribute('data-i18n'));
  });
  document.querySelectorAll('[data-i18n-html]').forEach(node => {
    node.innerHTML = t(node.getAttribute('data-i18n-html'));
  });
  document.querySelectorAll('[data-i18n-title]').forEach(node => {
    node.title = t(node.getAttribute('data-i18n-title'));
  });
}

// setLang — 切語言:存偏好、更新 <html lang>、切按鈕態、重套靜態文案、重繪動態內容。
function setLang(lang) {
  if (lang !== 'en' && lang !== 'zh-tw') lang = 'zh-tw';
  State.lang = lang;
  try { localStorage.setItem('ev_lang', lang); } catch (e) {}
  document.documentElement.lang = (lang === 'en' ? 'en' : 'zh-Hant');
  // 語言鈕狀態
  document.querySelectorAll('.lang-btn').forEach(b => {
    const on = b.getAttribute('data-lang') === lang;
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  applyStaticI18n();
  refreshDynamicI18n();
  log('語言切換:', lang);
}

// 讀存檔偏好(啟動時用)。無效值退回 zh-tw。
function readLangPref() {
  try {
    const l = localStorage.getItem('ev_lang');
    if (l === 'en' || l === 'zh-tw') return l;
  } catch (e) {}
  return 'zh-tw';
}

// 切語言時重繪所有由 JS 生成的動態內容:引擎狀態、基準下拉、判決/圖表(若已有結果)。
function refreshDynamicI18n() {
  // 引擎狀態文字(依當前引擎態選 key)
  if (State.engineReady) setEngineStatus(t('engine_ready'), 'live');
  else if (State.loading) setEngineStatus(t('engine_loading'), 'warm');
  else setEngineStatus(t('engine_idle'), '');
  // 基準下拉選項標籤 + 說明
  rebuildBenchOptions();
  // 檔案 pill(若已解析)
  if (State.parsed) refreshFilePill();
  // 判決 / 圖表(若已跑過分析)——重跑純呈現層渲染,數字不變、只換語言
  if (State.lastOut) {
    // 基準對齊提示要用當前語言重新翻譯(存的是 key/args)
    const bn = State.lastBenchNoteKey
      ? t(State.lastBenchNoteKey, ...(State.lastBenchNoteArgs || []))
      : '';
    State.lastBenchNote = bn;
    renderResults(State.lastOut, State.lastParsed, bn);
  }
}

// ============================================================================
// 數字格式化
// ============================================================================
function isNum(x) { return typeof x === 'number' && isFinite(x); }
function fmt(x, d = 2) {
  if (x == null || (typeof x === 'number' && !isFinite(x))) return '—';
  return Number(x).toFixed(d);
}
function fmtPct(x, d = 1) {
  if (!isNum(x)) return '—';
  return (x * 100).toFixed(d) + '%';
}
function fmtSigned(x, d = 2) {
  if (!isNum(x)) return '—';
  return (x >= 0 ? '+' : '') + x.toFixed(d);
}

// ============================================================================
// 1. Pyodide 掛載 + 引擎載入
// ============================================================================
function setEngineStatus(txt, cls) {
  $('engineStatus').textContent = txt;
  const dot = $('engineDot');
  dot.className = 'dot' + (cls ? ' ' + cls : '');
}

async function ensureEngine() {
  if (State.engineReady) return true;
  if (State.loading) {
    // 已在載,等它
    while (State.loading) { await new Promise(r => setTimeout(r, 120)); }
    return State.engineReady;
  }
  State.loading = true;
  const loader = $('loader');
  // 效能感知:各階段進場時的「預估剩餘秒數」(首載冷快取實測 ~15s;之後快取命中會提早完成,
  // ETA 只會跳快不會拖慢——寧可低承諾高兌現)。
  const STAGE_ETA = [14, 9, 2.5, 1.5];
  let etaDeadline = 0;
  const etaNode = $('loaderEta');
  const renderEta = () => {
    if (!etaNode) return;
    const remain = Math.ceil((etaDeadline - performance.now()) / 1000);
    etaNode.textContent = remain >= 2 ? t('loader_eta', remain) : t('loader_eta_soon');
  };
  const etaTimer = etaNode ? setInterval(renderEta, 500) : null;
  const stopEta = () => { if (etaTimer) clearInterval(etaTimer); if (etaNode) etaNode.textContent = ''; };
  // 步驟燈:done=已完成、active=進行中,讓等待有節奏、可預期
  const setStep = (idx) => {
    const steps = document.querySelectorAll('#loaderSteps .lstep');
    steps.forEach((s, i) => {
      s.classList.toggle('done', i < idx);
      s.classList.toggle('active', i === idx);
    });
    if (idx < STAGE_ETA.length) {
      etaDeadline = performance.now() + STAGE_ETA[idx] * 1000;
      renderEta();
    }
  };
  const setMsg = (m, step) => {
    $('loaderMsg').textContent = m;
    if (isNum(step)) setStep(step);
    log('loader:', m);
  };
  loader.classList.add('show');
  setStep(0);
  setEngineStatus(t('engine_loading'), 'warm');

  try {
    if (typeof loadPyodide !== 'function') {
      throw new Error(t('err_no_pyodide'));
    }
    setMsg(t('loader_start_py'), 0);
    State.pyodide = await loadPyodide({
      indexURL: `https://cdn.jsdelivr.net/pyodide/${PYODIDE_VERSION}/full/`,
    });
    log('Pyodide 啟動完成');

    setMsg(t('loader_load_pkg'), 1);
    await State.pyodide.loadPackage(['numpy', 'pandas']);
    log('numpy/pandas 載入完成');

    setMsg(t('loader_inject'), 2);
    await loadEngineSource(State.pyodide);
    log('engine 模組注入完成');

    // 冒煙測試:確認 analyze 可呼叫
    setMsg(t('loader_selftest'), 3);
    const smoke = await State.pyodide.runPythonAsync(`
import json
from engine import analyze
_r = analyze({"mode":"returns","returns":[0.01,-0.005,0.008,0.002,-0.003,0.006],
              "dates":None,"n_trials":1,"periods_per_year":252})
json.dumps({"ok": _r["ok"], "verdict": _r["verdict"]["overall"]})
`);
    log('引擎自測:', smoke);

    setStep(4);  // 全部完成
    State.engineReady = true;
    setEngineStatus(t('engine_ready'), 'live');
    loader.classList.remove('show');
    return true;
  } catch (e) {
    warn('引擎載入失敗:', e);
    showError(t('err_engine_fail', escapeHtml(String(e.message || e))));
    setEngineStatus(t('engine_failed'), '');
    loader.classList.remove('show');
    return false;
  } finally {
    stopEta();
    State.loading = false;
  }
}

// 把 engine/*.py 抓進 Pyodide 檔案系統,再讓 `from engine import analyze` 可用。
async function loadEngineSource(py) {
  // 建立 engine 目錄
  py.runPython(`
import os, sys
os.makedirs("engine", exist_ok=True)
if "" not in sys.path:
    sys.path.insert(0, "")
`);
  for (const fn of ENGINE_FILES) {
    const url = `engine/${fn}`;
    log('fetch engine 檔:', url);
    const resp = await fetch(url, { cache: 'no-store' });
    if (!resp.ok) throw new Error(t('err_fetch_engine', url, resp.status));
    let src = await resp.text();
    // 寫進 Pyodide FS
    py.FS.writeFile(`engine/${fn}`, src);
  }
  // 清掉可能殘留的 import 快取後再 import
  py.runPython(`
import importlib, sys
for m in [k for k in list(sys.modules) if k == "engine" or k.startswith("engine.")]:
    del sys.modules[m]
import engine
importlib.reload(engine)
`);
}

// ============================================================================
// 2. baselines.json 載入
// ============================================================================
// baselines.json 只有中文 desc;英文說明在前端補(產品名 name_en 已在 JSON)。
// key → 英文一句話說明(下拉選中時顯示在 benchDesc)。
const BENCH_DESC_EN = {
  tw_0050_dca: 'Monthly dollar-cost-averaging into Taiwan\'s 0050 ETF (total-return adjusted): equity = holdings value / cumulative invested. The retail "just DCA into the index" benchmark. Market data is a self-computed NAV ratio, no per-stock prices.',
  tw_factor_harvest: 'The farm\'s factor_harvest five-factor blend (value/momentum/low-vol/size/quality), equal-weighted, monthly rebalanced, vol-targeted, net of Taiwan trading costs — a BACKTESTED NAV. Beta / factor premia, not alpha; backtests overstate live results; for reference only, not a recommendation.',
  crypto_highrisk_beta: 'The farm\'s highrisk_beta strategy: BTC × vol-target × a 200-day-MA trend gate (crash insurance), leverage-capped, net of costs — a BACKTESTED NAV. Directional beta / compensated market risk, not alpha; crypto is highly volatile and backtests badly overstate live results; for reference only, not a recommendation.',
};

// 依語言取基準顯示名 / 說明
function benchName(b, key) {
  if (State.lang === 'en') return b.name_en || b.name_zh || key;
  return b.name_zh || b.name_en || key;
}
function benchDesc(b, key) {
  if (State.lang === 'en') return BENCH_DESC_EN[key] || b.name_en || t('param_bench_desc');
  return b.desc || b.name_zh || t('param_bench_desc');
}

// 重建基準下拉選項(切語言時要重跑,以更新標籤語言,同時保留當前選擇)。
function rebuildBenchOptions() {
  const sel = $('benchSel');
  if (!sel) return;
  const cur = sel.value;
  // 第一個 option 是「不比較」(由 data-i18n 管),其餘清掉重建
  while (sel.options.length > 1) sel.remove(1);
  if (State.baselines) {
    for (const [key, b] of Object.entries(State.baselines)) {
      const opt = el('option');
      opt.value = key;
      opt.textContent = benchName(b, key);
      sel.appendChild(opt);
    }
  }
  sel.value = cur;  // 還原選擇
  // 更新說明文字
  const k = sel.value;
  if (k && State.baselines && State.baselines[k]) {
    $('benchDesc').textContent = benchDesc(State.baselines[k], k);
  } else {
    $('benchDesc').textContent = t('param_bench_desc');
  }
}

async function loadBaselines() {
  try {
    const resp = await fetch('benchmarks/baselines.json', { cache: 'no-store' });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    State.baselines = data.benchmarks || {};
    rebuildBenchOptions();
    log('baselines 載入:', Object.keys(State.baselines));
  } catch (e) {
    warn('baselines 載入失敗(可離線使用,只是少了對照基準):', e);
    $('benchDesc').textContent = t('bench_load_fail');
  }
}

// ============================================================================
// 3. CSV 解析(對親友寬容)
// ============================================================================

// 3a. 解碼:先試 UTF-8,失敗或出現替代字元則試 Big5。
async function decodeFile(file) {
  const buf = await file.arrayBuffer();
  const bytes = new Uint8Array(buf);
  // 嘗試 UTF-8(fatal:true 遇壞位元組會丟)
  const tryDecode = (enc, fatal) => {
    try { return new TextDecoder(enc, { fatal }).decode(bytes); }
    catch (e) { return null; }
  };
  let text = tryDecode('utf-8', true);
  if (text != null && !text.includes('�')) { log('編碼: UTF-8'); return text; }
  // Big5 / cp950(TextDecoder 標籤 'big5' 涵蓋台灣券商常見編碼)
  for (const enc of ['big5', 'gbk', 'utf-8']) {
    const t = tryDecode(enc, false);
    if (t != null) {
      // 選替代字元最少的
      const bad = (t.match(/�/g) || []).length;
      if (bad === 0) { log('編碼:', enc); return t; }
    }
  }
  // 全都有壞字元 → 用寬鬆 UTF-8 保底
  log('編碼: UTF-8(寬鬆保底)');
  return new TextDecoder('utf-8', { fatal: false }).decode(bytes);
}

// 3b. 一個「數字」token → float(容忍 %、千分位、全形、民國年除外)
function parseNumberCell(raw) {
  if (raw == null) return NaN;
  let s = String(raw).trim();
  if (s === '' || s === '-' || s.toLowerCase() === 'nan' || s.toLowerCase() === 'null') return NaN;
  let pct = false;
  if (s.endsWith('%')) { pct = true; s = s.slice(0, -1); }
  s = s.replace(/,/g, '');          // 千分位
  s = s.replace(/[+\s]/g, '');      // 前導 + 與空白
  s = s.replace(/[０-９．－]/g, (c) => '０１２３４５６７８９．－'.indexOf(c) < 10
        ? String('０１２３４５６７８９．－'.indexOf(c)) : (c === '．' ? '.' : '-')); // 全形數字
  const v = parseFloat(s);
  if (!isFinite(v)) return NaN;
  return pct ? v / 100 : v;
}

// 3c. 判斷一個 token 是否像日期
function looksLikeDate(s) {
  if (s == null) return false;
  s = String(s).trim();
  // ISO / 斜線 / 民國年都先粗判
  return /^\d{3,4}[-/.]\d{1,2}[-/.]\d{1,2}/.test(s) || /^\d{4}\d{2}\d{2}$/.test(s);
}

// 3d. 正規化日期 → ISO(含民國年 → 西元)
function normalizeDate(s) {
  s = String(s).trim();
  // 日內時間戳(YYYY-MM-DD HH:MM[:SS])保留時間 → 引擎 _infer_ppy 才認得出日內步距(1 分 K 等)。
  let m = s.match(/^(\d{3,4})[-/.](\d{1,2})[-/.](\d{1,2})([T ]\d{1,2}:\d{2}(?::\d{2})?)?/);
  if (m) {
    let y = parseInt(m[1], 10);
    if (m[1].length === 3 || (y < 1911 && y > 1)) y += 1911; // 民國年(3 位或小數值)
    const mo = String(parseInt(m[2], 10)).padStart(2, '0');
    const d = String(parseInt(m[3], 10)).padStart(2, '0');
    const tm = m[4] ? ' ' + m[4].replace(/^[T ]/, '') : '';
    return `${y}-${mo}-${d}${tm}`;
  }
  m = s.match(/^(\d{4})(\d{2})(\d{2})$/);
  if (m) return `${m[1]}-${m[2]}-${m[3]}`;
  return s;
}

// 3e. 淨值序列 → 報酬序列(偵測後轉換)
function navToReturns(vals) {
  const r = [0];
  for (let i = 1; i < vals.length; i++) {
    const prev = vals[i - 1];
    r.push(prev !== 0 && isFinite(prev) ? vals[i] / prev - 1 : 0);
  }
  return r;
}

// 3f. 偵測一欄數值是「報酬」還是「淨值」
//     報酬:值多落在 -0.5~0.5 附近、可正可負、均值近 0;
//     淨值:大致單調上升的正大數(或從 1/100/1000 起算)。
function detectSeriesKind(vals) {
  const clean = vals.filter(isNum);
  if (clean.length < 3) return 'returns';
  // 迴圈算 absMax:日內 CSV 可達數十萬列,Math.max(...arr) 展開會爆呼叫堆疊(RangeError)。
  let absMax = 0;
  for (const v of clean) { const a = Math.abs(v); if (a > absMax) absMax = a; }
  const anyNeg = clean.some(v => v < 0);
  const allPos = clean.every(v => v > 0);
  // 單調性(允許少量回檔):升序步數比例
  let up = 0;
  for (let i = 1; i < clean.length; i++) if (clean[i] >= clean[i - 1]) up++;
  const monoRatio = up / (clean.length - 1);
  // 典型報酬:絕對值都很小(<1.5)且有正有負
  if (absMax < 1.5 && anyNeg) return 'returns';
  // 典型淨值:全正、值偏大或明顯單調
  if (allPos && (absMax > 3 || monoRatio > 0.55)) return 'nav';
  // 全正但值小(如 1.0x 附近)且相當單調 → 也當淨值
  if (allPos && monoRatio > 0.6) return 'nav';
  return 'returns';
}

// 3g. 分隔符偵測
function detectDelim(line) {
  const counts = { ',': (line.match(/,/g) || []).length,
                   '\t': (line.match(/\t/g) || []).length,
                   ';': (line.match(/;/g) || []).length };
  let best = ',', n = -1;
  for (const [d, c] of Object.entries(counts)) if (c > n) { n = c; best = d; }
  return n > 0 ? best : ',';
}

// 3h. 主解析:回傳統一結構或丟出可讀錯誤
function parseCSV(text, srcName) {
  // 去 BOM、統一換行、拆行
  text = text.replace(/^﻿/, '').replace(/\r\n?/g, '\n');
  const rawLines = text.split('\n').map(l => l.trim()).filter(l => l.length > 0);
  if (rawLines.length < 2) throw new Error(t('perr_too_few'));

  const delim = detectDelim(rawLines[0]);
  const rows = rawLines.map(l => l.split(delim).map(c => c.trim()));

  // 標題偵測:第一列若無任何可解析數字,視為標題
  const firstNumericCount = rows[0].filter(c => isNum(parseNumberCell(c))).length;
  let header = null, body = rows;
  if (firstNumericCount === 0) { header = rows[0]; body = rows.slice(1); }
  if (body.length < 2) throw new Error(t('perr_body_few'));

  // 迴圈版(勿用 Math.max(...spread):數十萬列(如加密 1m 一年 52.5 萬列)會 RangeError 爆棧)
  let nCols = 0;
  for (let i = 0; i < body.length; i++) { if (body[i].length > nCols) nCols = body[i].length; }

  // ---- 情況 A:單欄 → returns 或 nav ----
  // rawValues / datesRaw / jsKind(s):偵測下沉(R12 MED)——JS 的 nav/returns 偵測與民國年
  // 正規化只當【提示】,原始數值與原始日期字串一併送引擎,由 Python(有 pytest 釘死)重做
  // 權威偵測與轉換;不一致時引擎以 warning 告知。
  if (nCols === 1) {
    const vals = body.map(r => parseNumberCell(r[0]));
    const good = vals.filter(isNum).length;
    if (good < 2) throw new Error(t('perr_single_num'));
    const kind = detectSeriesKind(vals);
    const clean = vals.map(v => isNum(v) ? v : 0);
    const returns = kind === 'nav' ? navToReturns(clean) : clean;
    return {
      mode: 'returns', dates: null, returns, matrix: null, colNames: null,
      srcName, nRows: returns.length,
      noteKey: kind === 'nav' ? 'note_series_nav' : 'note_series_ret',
      rawValues: vals, datesRaw: null, jsKind: kind,
    };
  }

  // ---- 判斷第一欄是不是日期 ----
  const col0 = body.map(r => r[0]);
  const dateLike = col0.filter(looksLikeDate).length;
  const firstColIsDate = dateLike >= body.length * 0.6;

  // ---- 情況 B:date, value(兩欄)----
  if (firstColIsDate && nCols === 2) {
    const dates = col0.map(normalizeDate);
    const vals = body.map(r => parseNumberCell(r[1]));
    const good = vals.filter(isNum).length;
    if (good < 2) throw new Error(t('perr_dv_num'));
    const kind = detectSeriesKind(vals);
    const clean = vals.map(v => isNum(v) ? v : 0);
    const returns = kind === 'nav' ? navToReturns(clean) : clean;
    return {
      mode: 'returns', dates, returns, matrix: null, colNames: null,
      srcName, nRows: returns.length,
      noteKey: kind === 'nav' ? 'note_dv_nav' : 'note_dv_ret',
      rawValues: vals, datesRaw: col0.slice(), jsKind: kind,
    };
  }

  // ---- 情況 C:matrix(第一欄日期 + 多欄策略,或多欄純數值)----
  const hasDateCol = firstColIsDate;
  const startCol = hasDateCol ? 1 : 0;
  const nStrat = nCols - startCol;
  if (nStrat < 1) throw new Error(t('perr_cols'));

  const dates = hasDateCol ? col0.map(normalizeDate) : null;
  const names = [];
  for (let c = startCol; c < nCols; c++) {
    names.push((header && header[c]) ? header[c] : t('strat_col', c - startCol + 1));
  }

  // 若只有 1 條策略欄 → 退化成 returns
  if (nStrat === 1) {
    const vals = body.map(r => parseNumberCell(r[startCol]));
    const kind = detectSeriesKind(vals);
    const clean = vals.map(v => isNum(v) ? v : 0);
    const returns = kind === 'nav' ? navToReturns(clean) : clean;
    return {
      mode: 'returns', dates, returns, matrix: null, colNames: null,
      srcName, nRows: returns.length,
      noteKey: kind === 'nav' ? 'note_single_nav' : 'note_single_ret',
      rawValues: vals, datesRaw: hasDateCol ? col0.slice() : null, jsKind: kind,
    };
  }

  // 真 matrix:每欄各自偵測 nav/returns(rawMatrix/jsKinds 一併留存,供引擎權威重偵測)
  const matrix = {};
  const rawMatrix = {};
  const jsKinds = {};
  for (let ci = 0; ci < names.length; ci++) {
    const c = startCol + ci;
    const vals = body.map(r => parseNumberCell(r[c]));
    const kind = detectSeriesKind(vals);
    const clean = vals.map(v => isNum(v) ? v : 0);
    matrix[names[ci]] = kind === 'nav' ? navToReturns(clean) : clean;
    rawMatrix[names[ci]] = vals;
    jsKinds[names[ci]] = kind;
  }
  return {
    mode: 'matrix', dates, returns: null, matrix, colNames: names,
    srcName, nRows: body.length,
    noteKey: 'note_matrix', noteArgs: [names.length],
    rawMatrix, datesRaw: hasDateCol ? col0.slice() : null, jsKinds,
  };
}

// ============================================================================
// 4. 基準對齊
// ============================================================================
// 把選中基準的 returns 對齊到使用者資料。誠實原則(R12 HIGH 修):
// 【絕不以 0 填補缺日】——補 0 會稀釋基準的波動與報酬,讓「贏過基準」變得太容易
// (基準夏普被人為壓低,beats_bench 偏易觸發、判決被灌分)。
// 有日期 → 只取【交集日期】(兩邊都有的日);交集覆蓋率 < ALIGN_MIN_COVER → 誠實 skip。
// 無日期 → 基準夠長時取尾端等長一段;不夠長(覆蓋率 < 門檻)→ 誠實 skip,不補 0。
const ALIGN_MIN_COVER = 0.8;

function alignBenchmark(benchKey, userDates, userLen) {
  if (!benchKey || !State.baselines || !State.baselines[benchKey]) return { arr: null, noteKey: null, noteArgs: [] };
  const b = State.baselines[benchKey];
  const bDates = b.dates || null;
  const bRet = b.returns || [];

  if (userDates && bDates && bDates.length) {
    // 交集對齊:只收兩邊都有的日期(順序照使用者資料),缺日直接略過、不填 0
    const map = new Map();
    for (let i = 0; i < bDates.length; i++) map.set(bDates[i], bRet[i]);
    const out = [];
    let hit = 0;
    for (let i = 0; i < userDates.length && i < userLen; i++) {
      const v = map.get(userDates[i]);
      if (v != null && isFinite(v)) { out.push(v); hit++; }
    }
    if (hit >= userLen * ALIGN_MIN_COVER && hit >= 2) {
      return { arr: out, noteKey: 'align_bydate_intersect', noteArgs: [hit, userLen] };
    }
    // 交集覆蓋率不足 → 誠實 skip(舊版在此退化成長度對齊+補 0 = 稀釋基準,已移除)
    warn('基準日期交集不足(', hit, '/', userLen, '),略過基準比較');
    return { arr: null, noteKey: 'align_skip_overlap', noteArgs: [hit, userLen] };
  }

  // 無日期可交集:長度對齊,但只在基準涵蓋足夠時,且絕不補 0
  if (bRet.length >= userLen) {
    // 基準夠長:取尾端等長一段(較新的一段),一對一、無填補
    return {
      arr: bRet.slice(bRet.length - userLen),
      noteKey: userDates ? 'align_len_nodate_overlap' : 'align_len_nodate',
      noteArgs: [],
    };
  }
  if (bRet.length >= userLen * ALIGN_MIN_COVER && bRet.length >= 2) {
    // 基準略短(≥80% 覆蓋):用基準全段對照(不足處不補 0——引擎會以基準自身長度算其指標)
    return { arr: bRet.slice(), noteKey: 'align_len_partial', noteArgs: [bRet.length, userLen] };
  }
  // 基準太短 → 誠實 skip(舊版頭部補 0 灌長度,已移除)
  warn('基準長度不足(', bRet.length, '/', userLen, '),略過基準比較');
  return { arr: null, noteKey: 'align_skip_short', noteArgs: [bRet.length, userLen] };
}

// ============================================================================
// 5. 執行分析
// ============================================================================
async function runAnalysis() {
  hideError();
  if (!State.parsed) { showError(t('err_no_data')); return; }

  const btn = $('runBtn');
  btn.classList.add('busy'); btn.disabled = true;
  $('runBtn').querySelector('.txt').textContent = t('run_prep');

  try {
    const ok = await ensureEngine();
    if (!ok) return;

    $('runBtn').querySelector('.txt').textContent = t('run_running');

    const p = State.parsed;
    const nTrials = Math.max(1, parseInt($('nTrials').value, 10) || 1);
    const ppyRaw = $('ppy').value;
    const ppy = ppyRaw ? parseFloat(ppyRaw) : null;

    // 基準對齊
    const benchKey = $('benchSel').value;
    const userLen = p.mode === 'matrix'
      ? Math.min(...Object.values(p.matrix).map(a => a.length))
      : p.returns.length;
    const { arr: benchArr, noteKey: benchNoteKey, noteArgs: benchNoteArgs } = alignBenchmark(benchKey, p.dates, userLen);
    const benchNote = benchNoteKey ? t(benchNoteKey, ...(benchNoteArgs || [])) : '';
    // 記住原始 key/args,切語言時重繪 warnings 用
    State.lastBenchNoteKey = benchNoteKey;
    State.lastBenchNoteArgs = benchNoteArgs || [];
    // 硬化揭露:記住使用者「申報」的 n_trials,渲染時與引擎回的有效 n_trials 比對
    State.lastDeclaredNTrials = nTrials;

    const payload = {
      mode: p.mode,
      dates: p.dates,
      returns: p.mode === 'returns' ? p.returns : null,
      matrix: p.mode === 'matrix' ? p.matrix : null,
      n_trials: nTrials,
      periods_per_year: ppy,
      benchmark_returns: benchArr,
      cost_bps_per_turnover: null,
      turnover: null,
      // 偵測下沉(R12 MED):原始數值/原始日期字串 + JS 偵測 hint 一併送引擎,
      // 由 Python detect_and_convert 重做權威偵測與轉換(民國年/淨值vs報酬),
      // 與 hint 不一致時引擎以 warning 告知。JSON.stringify 會把 NaN 序列化為 null,引擎已處理。
      raw: {
        values: p.rawValues || null,
        matrix: p.rawMatrix || null,
        dates: p.datesRaw || null,
        js_kind: p.jsKind || null,
        js_kinds: p.jsKinds || null,
      },
    };
    log('payload:', { mode: payload.mode, n_trials: nTrials, ppy, hasBench: !!benchArr,
                      len: userLen, benchNote });

    // 傳進 Pyodide:用 JSON 字串最穩(避免 toPy 對 null 的細節)
    State.pyodide.globals.set('_payload_json', JSON.stringify(payload));
    const result = await State.pyodide.runPythonAsync(`
import json
from engine import analyze
from engine.judge_web import detect_and_convert
_p = detect_and_convert(json.loads(_payload_json))  # 權威偵測/轉換(無 raw 時原樣通過)
_out = analyze(_p)
# 偵測層 warnings(民國年/淨值偵測不一致等)併入分析 warnings,前端一併顯示
_dw = list(_p.get("_detect_warnings") or [])
_dwc = list(_p.get("_detect_warnings_coded") or [])
if _dw and isinstance(_out, dict) and _out.get("ok"):
    _out["warnings"] = _dw + list(_out.get("warnings") or [])
    _out["warnings_coded"] = _dwc + list(_out.get("warnings_coded") or [])
_out  # 回傳 dict,交給 JS 用 toJs 轉
`);
    const out = result.toJs({ dict_converter: Object.fromEntries });
    if (result.destroy) result.destroy();
    log('analyze 回傳:', out);

    if (!out.ok) {
      const whys = codedStrings(out.warnings_coded, out.warnings, 'rw_');
      showError(t('err_analyze_fail', whys.length ? whys.join(State.lang === 'en' ? '; ' : '；') : t('err_analyze_unknown')));
      return;
    }
    // 記住結果 + 當時 parsed,供切語言時純呈現層重繪(數字不變、只換文字)
    State.lastOut = out;
    State.lastParsed = p;
    State.lastBenchNote = benchNote;
    renderResults(out, p, benchNote);
    $('results').classList.add('show');
    smoothScrollTo($('results'));  // 判決出爐 → 自動捲到判決卡(尊重 reduced-motion)
  } catch (e) {
    warn('runAnalysis 例外:', e);
    showError(t('err_runtime', escapeHtml(String(e.message || e))));
  } finally {
    btn.classList.remove('busy'); btn.disabled = false;
    $('runBtn').querySelector('.txt').textContent = t('run_btn');
  }
}

// ============================================================================
// 6. 渲染結果
// ============================================================================

// ---- 白話結論生成(給不懂統計的朋友:一句話 + 三重點)----
// 移植自本地 app_local.py 的 _plain_headline_sentence / _plain_three_points 精神,
// 但公開站是 BYO-CSV:沒有持倉歸因、沒有時間對半切 holdout,改用引擎實際回的
// permutation(贏過隨機打亂)/ DSR(扣掉試 N 次運氣)/ benchmark(贏過基準)三根柱。

// 首屏白話定調(最大字之上的小標)。
// 首屏定調小標(依 verdict 三態取 i18n key)。
function plainHeadlineTag(overall) {
  if (overall === 'likely-real') return t('tag_real');
  if (overall === 'likely-overfit') return t('tag_overfit');
  return t('tag_incon');
}

// 把判決濃縮成一句人話(最大字)。動態依 verdict / CAGR / 贏不贏基準 / n_trials 生成。
// 一個不懂統計的朋友只讀這一句,就該抓到「賺不賺 + 這數字信不信得過」。全部走 t(),中英同構。
function plainHeadlineSentence(overall, metrics, benchCmp, nTrials, spanInfo) {
  const cagr = metrics ? metrics.cagr : null;
  const looksProfitable = isNum(cagr) && cagr > 0;

  // 賺賠開場。短日曆跨度(short_calendar_span,<0.5 年)時年化 = 短窗外插易膨風:
  // 主數字誠實降級為「期間累計報酬」;沒有期末淨值可算時退而加「(短窗外插)」註記。
  let money;
  const finalEq = metrics ? metrics.final_equity : null;
  if (spanInfo && isNum(finalEq)) {
    const tot = finalEq - 1;
    const yrsTxt = isNum(spanInfo.years) ? fmt(spanInfo.years, 2) : '<0.5';
    money = tot >= 0
      ? t('ph_money_profit_span', fmtPct(tot, 1), yrsTxt)
      : t('ph_money_loss_span', fmtPct(Math.abs(tot), 1), yrsTxt);
  } else if (looksProfitable) {
    money = t('ph_money_profit', fmtPct(cagr, 1)) + (spanInfo ? t('ph_money_span_suffix') : '');
  } else if (isNum(cagr) && cagr <= 0) {
    money = t('ph_money_loss', fmtPct(cagr, 1)) + (spanInfo ? t('ph_money_span_suffix') : '');
  } else {
    money = t('ph_money_neutral');
  }

  // 贏不贏基準(誠實處理四情境,避免「贏過…多賺 -6.9%」這種自相矛盾)
  // strategy_beats 是【風險調整後(夏普)】的勝負;excess_cagr 是【年化報酬】差,兩者可能不同號。
  let beat = '';
  if (benchCmp) {
    const xc = benchCmp.excess_cagr;
    const hasXc = isNum(xc);
    if (benchCmp.strategy_beats) {
      if (hasXc && xc < 0) {
        // 夏普贏、但年化其實少賺 → 用更低波動換的,不是矛盾
        beat = t('ph_beat_sharpe_less', fmtPct(-xc, 1));
      } else {
        const xcTxt = hasXc ? t('ph_beat_win_more', fmtPct(xc, 1)) : '';
        beat = t('ph_beat_win', xcTxt);
      }
    } else {
      if (hasXc && xc > 0) {
        // 年化多賺、但夏普沒贏 → 靠多冒險換的
        beat = t('ph_beat_cagr_only', fmtPct(xc, 1));
      } else {
        beat = t('ph_beat_lose');
      }
    }
  }

  // 單一序列(n_trials<=1)的誠實警語:純雜訊單一序列實測約一到兩成會被判 likely-real(隨波動浮動),
  // 此時「像真本事」若不加註,信心會超過統計該有的謙遜。
  const singleSeries = !isNum(nTrials) || nTrials <= 1;

  if (overall === 'likely-real') {
    return singleSeries ? t('ph_real_single', money, beat) : t('ph_real_multi', money, beat, nTrials);
  }
  if (overall === 'likely-overfit') {
    return t('ph_overfit', money, beat);
  }
  return t('ph_incon', money, beat);  // inconclusive
}

// 首屏三個一句話重點(icon + 一行)。公開站三根柱:
//   ① 贏過隨機打亂嗎(permutation)② 扣掉試 N 次運氣後還站得住嗎(DSR)③ 贏過基準嗎(benchmark)
// matrix 模式多一句「N 個策略裡幾個倖存(FWER)」。回 [{icon, text}]。
function plainThreePoints(out, benchCmp) {
  const points = [];
  const perm = out.permutation_null;
  const dsr = out.dsr;

  // ① 贏過隨機打亂嗎(permutation null)
  if (perm && isNum(perm.p_value)) {
    if (perm.passes) {
      points.push({ icon: '🎲', text: t('pt_perm_pass', fmt(perm.p_value, 3)) });
    } else if (perm.p_value > 0.5) {
      points.push({ icon: '🎲', text: t('pt_perm_noise', fmt(perm.p_value, 3)) });
    } else {
      points.push({ icon: '🎲', text: t('pt_perm_mid', fmt(perm.p_value, 3)) });
    }
  } else {
    points.push({ icon: '🎲', text: t('pt_perm_na') });
  }

  // ② 扣掉試 N 次運氣後還站得住嗎(DSR)
  if (dsr && isNum(dsr.dsr_prob)) {
    const nt = isNum(dsr.n_trials) ? dsr.n_trials : 1;
    const prob = dsr.dsr_prob;
    // 引擎回什麼就渲染什麼:單序列宣稱 n_trials>1 時,引擎以 SR 標準誤作試驗離散度
    // 下限做保守通縮(sr_variance_proxy / reason params.var_proxy)→ 用 proxy 語意的 clause。
    const varProxy = dsr.sr_var_proxy === true || dsr.sr_variance_proxy === true
      || ((out.verdict && out.verdict.reasons_coded) || []).some(c =>
           c && typeof c.code === 'string' && c.code.indexOf('dsr_') === 0
           && c.params && c.params.var_proxy === true);
    // 硬化揭露:引擎回的有效 n_trials > 使用者申報值(matrix 誠實基準/雜訊硬化上調)
    // → 白話重點照實說「你申報 N 種,引擎保守上調為 M 種」,不讓上調靜默發生。
    const declared = isNum(State.lastDeclaredNTrials) ? State.lastDeclaredNTrials : null;
    const trialClause = (declared != null && nt > declared)
      ? t('pt_dsr_trial_hardened', declared, nt)
      : nt > 1
        ? t(varProxy ? 'pt_dsr_trial_multi_proxy' : 'pt_dsr_trial_multi', nt)
        : t('pt_dsr_trial_single');
    if (prob >= 0.95) {
      points.push({ icon: '🎯', text: t('pt_dsr_high', trialClause, fmtPct(prob, 0)) });
    } else if (prob >= 0.60) {
      points.push({ icon: '🎯', text: t('pt_dsr_mid', trialClause, fmtPct(prob, 0)) });
    } else {
      points.push({ icon: '🎯', text: t('pt_dsr_low', trialClause, fmtPct(prob, 0)) });
    }
  } else {
    points.push({ icon: '🎯', text: t('pt_dsr_na') });
  }

  // ③ 贏過基準嗎(benchmark)
  if (benchCmp) {
    const xc = benchCmp.excess_cagr;
    const hasXc = isNum(xc);
    if (benchCmp.strategy_beats) {
      const c = hasXc && xc > 0 ? t('pt_bench_win_more', fmtPct(xc, 1)) : '';
      points.push({ icon: '🆚', text: t('pt_bench_win', c) });
    } else if (hasXc && xc > 0) {
      points.push({ icon: '🆚', text: t('pt_bench_half', fmtPct(xc, 1)) });
    } else {
      const c = hasXc ? t('pt_bench_lose_less', fmtPct(Math.abs(xc), 1)) : '';
      points.push({ icon: '🆚', text: t('pt_bench_lose', c) });
    }
  } else {
    points.push({ icon: '🆚', text: t('pt_bench_na') });
  }

  // matrix 模式:多一句「N 個策略裡幾個倖存(FWER)」
  if (out.pbo || (out.fwer && out.fwer.spa)) {
    const nStrat = out.pbo && isNum(out.pbo.n_strategies) ? out.pbo.n_strategies : null;
    const nRej = out.fwer ? out.fwer.n_rejected : null;
    const pbo = out.pbo ? out.pbo.pbo : null;
    // nStrat 前綴:中文「N 條」、英文「N 」,無值時空字串(t() 的句型已含量詞尾)
    const nStratTxt = nStrat != null ? nStrat + ' ' : '';
    let txt;
    if (isNum(nRej)) {
      if (nRej > 0) {
        // 依 fwer.benchmark_kind 分流語意:aligned=真贏基準;zero_*=絕對報酬,勿當贏基準(誠實揭露)
        const bk = out.fwer && out.fwer.benchmark_kind;
        const covPct = out.fwer && isNum(out.fwer.bench_coverage) ? Math.round(out.fwer.bench_coverage * 100) : null;
        let kindClause;
        if (bk === 'aligned') kindClause = t('pt_fwer_kind_aligned', nRej);
        else if (bk === 'zero_fallback' && covPct != null) kindClause = t('pt_fwer_kind_zero_fallback', nRej, covPct);
        else kindClause = t('pt_fwer_kind_zero_nobench', nRej);
        txt = t('pt_fwer_survive', nStratTxt, nRej, kindClause) + ' ' + t('pt_fwer_survivor_caveat');
      } else {
        txt = t('pt_fwer_wipe', nStratTxt);
      }
    } else if (isNum(pbo)) {
      txt = t('pt_pbo', fmt(pbo, 2), pbo > 0.5 ? t('pt_pbo_over') : t('pt_pbo_ok'));
    } else {
      txt = t('pt_matrix_na');
    }
    points.push({ icon: '🗂️', text: txt });
  }

  return points;
}

// stamp SVG 與 verdict 態綁定,不隨語言變;text/sub 走 t()。
const VERDICT_STAMP = {
  'likely-real':    '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
  'likely-overfit': '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>',
  'inconclusive':   '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 17h.01M9.1 9a3 3 0 0 1 5.8 1c0 2-3 2.5-3 4"/></svg>',
};
function verdictMeta(overall) {
  const o = VERDICT_STAMP[overall] ? overall : 'inconclusive';
  if (o === 'likely-real')    return { text: t('vm_real_text'),    sub: t('vm_real_sub'),    stamp: VERDICT_STAMP[o] };
  if (o === 'likely-overfit') return { text: t('vm_overfit_text'), sub: t('vm_overfit_sub'), stamp: VERDICT_STAMP[o] };
  return { text: t('vm_incon_text'), sub: t('vm_incon_sub'), stamp: VERDICT_STAMP[o] };
}

function renderResults(out, parsed, benchNote) {
  const v = out.verdict;
  const meta = verdictMeta(v.overall);

  // ---- 主判決 ----
  const card = $('verdictCard');
  card.setAttribute('data-verdict', v.overall);
  $('verdictText').textContent = meta.text;
  $('verdictStamp').innerHTML = meta.stamp;
  $('verdictSub').textContent = meta.sub;

  // ---- 白話首屏(給不懂統計的朋友:最大字一句話 + 三重點)----
  renderPlain(out);

  // 分數 + 量表(動畫)
  const score = isNum(v.score_0to100) ? v.score_0to100 : 0;
  animateNumber($('scoreNum'), score, 900, 0);
  requestAnimationFrame(() => { $('gaugeFill').style.width = Math.max(2, score) + '%'; });

  // reasons(EN 模式用引擎 reason codes 渲染英文;zh 用引擎原字串)
  const rl = $('reasonsList'); rl.innerHTML = '';
  codedStrings(v.reasons_coded, v.reasons, 'rc_').forEach(r => {
    rl.appendChild(el('li', null,
      `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M9 12l2 2 4-4"/><circle cx="12" cy="12" r="9"/></svg><span>${escapeHtml(r)}</span>`));
  });

  // red flags(同 reasons:EN 走 codes)
  const flagsBox = $('flagsBox'); const fl = $('flagsList'); fl.innerHTML = '';
  const flags = codedStrings(v.red_flags_coded, v.red_flags, 'rf_');
  if (flags.length) {
    flagsBox.classList.remove('hidden');
    $('reasonsGrid').classList.add('has-flags');
    flags.forEach(f => {
      fl.appendChild(el('li', null,
        `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><path d="M4 22v-7"/></svg><span>${escapeHtml(f)}</span>`));
    });
  } else {
    flagsBox.classList.add('hidden');
    $('reasonsGrid').classList.remove('has-flags');
  }

  // warnings + bench note(引擎 warnings 走 codes,bench note / parse note 本就 i18n)
  const warnBox = $('warningsBox');
  const allWarn = [...codedStrings(out.warnings_coded, out.warnings, 'rw_')];
  // 短窗外插警語升級:short_calendar_span 從小字警語列拉出來,升為判決卡頂部的醒目警告條
  //(年化數字全是短窗外插,不該只用小字帶過)。coded 與 raw 逐位 1:1,索引可對齊。
  const spanBar = $('spanWarnBar');
  if (spanBar) {
    let spanIdx = -1;
    if (Array.isArray(out.warnings_coded) && out.warnings_coded.length === allWarn.length) {
      spanIdx = out.warnings_coded.findIndex(c => c && c.code === 'short_calendar_span');
    }
    if (spanIdx >= 0 && allWarn[spanIdx] != null) {
      spanBar.innerHTML = `<b>${escapeHtml(t('span_warn_label'))}</b><span>${escapeHtml(String(allWarn[spanIdx]))}</span>`;
      spanBar.classList.remove('hidden');
      allWarn.splice(spanIdx, 1);
    } else {
      spanBar.classList.add('hidden');
      spanBar.innerHTML = '';
    }
  }
  if (benchNote) allWarn.push(benchNote);
  if (parsed && parsed.noteKey) allWarn.push(t('parse_prefix') + t(parsed.noteKey, ...(parsed.noteArgs || [])));
  if (allWarn.length) {
    warnBox.classList.remove('hidden');
    warnBox.innerHTML = allWarn.map(w => `<span>› ${escapeHtml(w)}</span>`).join('');
  } else warnBox.classList.add('hidden');

  // ---- 指標網格 ----
  renderMetrics(out.metrics);

  // ---- 檢定閘 ----
  renderGates(out);

  // ---- 圖表 ----
  drawEquityChart(out.equity_curve, out.benchmark_curve, parsed);
  drawNullChart(out.permutation_null);
}

// 短日曆跨度偵測:引擎 warnings_coded 含 short_calendar_span(資料 <0.5 年)→
// 回 {years}(供年化大字降級與醒目警告條);否則 null。
function shortSpanInfo(out) {
  const coded = out && out.warnings_coded;
  if (!Array.isArray(coded)) return null;
  for (const c of coded) {
    if (c && c.code === 'short_calendar_span') {
      const y = c.params && isNum(c.params.span_years) ? c.params.span_years : null;
      return { years: y };
    }
  }
  return null;
}

// 白話首屏:定調小標 + 一句話結論(最大字)+ 三個一句話重點。
function renderPlain(out) {
  const v = out.verdict;
  const overall = v.overall;
  const nTrials = out.dsr ? out.dsr.n_trials : 1;
  const benchCmp = out.benchmark_compare || null;

  $('plainTag').textContent = plainHeadlineTag(overall);
  $('plainHeadline').textContent = plainHeadlineSentence(overall, out.metrics, benchCmp, nTrials, shortSpanInfo(out));

  const list = $('plainPoints');
  list.innerHTML = '';
  plainThreePoints(out, benchCmp).forEach(p => {
    const li = el('li', null,
      `<span class="pico" aria-hidden="true">${p.icon}</span><span class="ptxt">${p.text}</span>`);
    list.appendChild(li);
  });
}

function animateNumber(node, target, dur, decimals) {
  const start = performance.now();
  const from = 0;
  function step(t) {
    const k = Math.min(1, (t - start) / dur);
    const eased = 1 - Math.pow(1 - k, 3);
    node.textContent = (from + (target - from) * eased).toFixed(decimals);
    if (k < 1) requestAnimationFrame(step);
    else node.textContent = target.toFixed(decimals);
  }
  requestAnimationFrame(step);
}

function metricCell(k, v, cls, sub) {
  return `<div class="metric"><div class="k">${k}</div>
    <div class="v ${cls || ''}">${v}${sub ? `<small> ${sub}</small>` : ''}</div></div>`;
}

function renderMetrics(m) {
  if (!m) { $('metricsGrid').innerHTML = ''; return; }
  const shCls = isNum(m.sharpe) ? (m.sharpe > 0 ? 'pos' : 'neg') : '';
  const cagrCls = isNum(m.cagr) ? (m.cagr > 0 ? 'pos' : 'neg') : '';
  const concCls = isNum(m.top_bar_concentration) ? (m.top_bar_concentration >= 0.4 ? 'neg' : (m.top_bar_concentration >= 0.25 ? 'warn' : '')) : '';
  const cells = [
    metricCell(t('mt_sharpe'), fmt(m.sharpe, 2), shCls),
    metricCell(t('mt_sortino'), fmt(m.sortino, 2), isNum(m.sortino) && m.sortino > 0 ? 'pos' : (isNum(m.sortino) ? 'neg' : '')),
    metricCell(t('mt_cagr'), fmtPct(m.cagr, 1), cagrCls),
    metricCell(t('mt_vol'), fmtPct(m.ann_vol, 1), ''),
    metricCell(t('mt_maxdd'), fmtPct(m.max_drawdown, 1), isNum(m.max_drawdown) && m.max_drawdown < -0.3 ? 'neg' : 'warn'),
    metricCell(t('mt_calmar'), fmt(m.calmar, 2), ''),
    metricCell(t('mt_final'), fmt(m.final_equity, 2), isNum(m.final_equity) && m.final_equity >= 1 ? 'pos' : 'neg', '×'),
    metricCell(t('mt_conc'), fmtPct(m.top_bar_concentration, 0), concCls),
    metricCell(t('mt_nperiods'), isNum(m.n_periods) ? String(m.n_periods) : '—', m.n_periods < 60 ? 'warn' : ''),
  ];
  $('metricsGrid').innerHTML = cells.join('');
}

// ---- 檢定閘卡 ----
function badge(kind, txt) { return `<span class="badge ${kind}">${txt}</span>`; }

function gateCard({ title, sub, badgeKind, badgeTxt, note, rows, extra }) {
  const rowHtml = (rows || []).map(r =>
    `<div class="row"><span class="kk">${r[0]}</span><span class="vv ${r[2] || ''}">${r[1]}</span></div>`
  ).join('');
  return `<div class="gate">
    <div class="gate-head">
      <div class="gate-title">${title}<small>${sub || ''}</small></div>
      ${badge(badgeKind, badgeTxt)}
    </div>
    ${note ? `<p class="gate-note">${note}</p>` : ''}
    <div class="kv">${rowHtml}</div>
    ${extra || ''}
  </div>`;
}

function renderGates(out) {
  const cards = [];
  const d = out.dsr, perm = out.permutation_null;

  // --- DSR ---
  if (d) {
    const prob = d.dsr_prob;
    let bk = 'na', bt = t('b_na');
    if (isNum(prob)) {
      if (prob >= 0.95) { bk = 'pass'; bt = t('b_high'); }
      else if (prob >= 0.60) { bk = 'mid'; bt = t('b_floor'); }
      else { bk = 'fail'; bt = t('b_noise'); }
    }
    const barW = isNum(prob) ? Math.round(prob * 100) : 0;
    // 硬化揭露(閘卡):引擎有效 n_trials > 申報值 → 卡片註解照實交代保守上調
    const declaredNT = isNum(State.lastDeclaredNTrials) ? State.lastDeclaredNTrials : null;
    const dsrNote = (declaredNT != null && isNum(d.n_trials) && d.n_trials > declaredNT)
      ? t('g_dsr_note_hardened', declaredNT, d.n_trials)
      : t('g_dsr_note', d.n_trials);
    cards.push(gateCard({
      title: t('g_dsr_title'), sub: t('g_dsr_sub'),
      badgeKind: bk, badgeTxt: bt,
      note: dsrNote,
      rows: [
        [t('g_dsr_prob'), fmt(prob, 3), isNum(prob) && prob >= 0.6 ? 'pos' : 'neg'],
        [t('g_dsr_sr'), fmt(d.sr_annual, 2), isNum(d.sr_annual) && d.sr_annual > 0 ? 'pos' : 'neg'],
        [t('g_dsr_sr0'), fmt(d.sr0, 2)],
        [t('g_dsr_p'), fmt(d.p_value, 3)],
      ],
      extra: `<div class="threshbar" style="margin-top:12px"><div class="f" style="width:${barW}%"></div><div class="mark" style="left:60%"></div></div>`,
    }));
  }

  // --- permutation null ---
  if (perm) {
    const pass = perm.passes;
    const pv = perm.p_value;
    let bk = 'na', bt = t('b_na');
    if (isNum(pv)) {
      if (pass) { bk = 'pass'; bt = t('b_beatrand'); }
      else if (pv > 0.5) { bk = 'fail'; bt = t('b_loserand'); }
      else { bk = 'mid'; bt = t('b_nothresh'); }
    }
    // 矩陣模式:此閘只檢「挑出的最佳欄」,對選擇偏誤失真 → 加註「以 FWER/PBO 為準」,
    // 避免與 SPA「全軍覆沒」並列時自相矛盾(R12 LOW 修)。
    const isMatrix = !!(out.pbo || (out.fwer && out.fwer.spa));
    const permNote = t('g_perm_note', perm.n_perm || 0)
      + (isMatrix ? `<br><em class="gate-caveat">${t('g_perm_matrix_caveat')}</em>` : '');
    cards.push(gateCard({
      title: t('g_perm_title'), sub: t('g_perm_sub'),
      badgeKind: bk, badgeTxt: bt,
      note: permNote,
      rows: [
        [t('g_perm_real'), fmt(perm.real_sharpe, 2), isNum(perm.real_sharpe) && perm.real_sharpe > 0 ? 'pos' : 'neg'],
        [t('g_perm_p95'), fmt(perm.null_p95_sharpe, 2)],
        [t('g_perm_p'), fmt(pv, 3), isNum(pv) && pv < 0.05 ? 'pos' : (isNum(pv) && pv > 0.5 ? 'neg' : '')],
      ],
    }));
  }

  // --- PBO(matrix)---
  if (out.pbo) {
    const pbo = out.pbo.pbo;
    let bk = 'na', bt = t('b_na');
    if (isNum(pbo)) { if (pbo <= 0.5) { bk = 'pass'; bt = t('b_nocrash'); } else { bk = 'fail'; bt = t('b_oosbottom'); } }
    cards.push(gateCard({
      title: t('g_pbo_title'), sub: t('g_pbo_sub'),
      badgeKind: bk, badgeTxt: bt,
      note: t('g_pbo_note'),
      rows: [
        [t('g_pbo_pbo'), fmt(pbo, 3), isNum(pbo) && pbo <= 0.5 ? 'pos' : 'neg'],
        [t('g_pbo_nstrat'), isNum(out.pbo.n_strategies) ? String(out.pbo.n_strategies) : '—'],
        [t('g_pbo_ncomb'), isNum(out.pbo.n_combinations) ? String(out.pbo.n_combinations) : '—'],
      ],
    }));
  }

  // --- FWER / SPA(matrix)---
  if (out.fwer && out.fwer.spa) {
    const spa = out.fwer.spa;
    const nRej = out.fwer.n_rejected;
    let bk = 'na', bt = t('b_na');
    if (isNum(spa.p_value)) { if (spa.p_value <= 0.10) { bk = 'pass'; bt = t('b_survivor'); } else { bk = 'fail'; bt = t('b_wipeout'); } }
    // 依 benchmark_kind 補誠實註記:zero 模式=比絕對報酬,勿當「贏過基準」
    const bkind = out.fwer.benchmark_kind;
    let spaNote = t('g_spa_note');
    if (bkind === 'zero_no_benchmark') spaNote += ' ' + t('g_spa_note_zero_nobench');
    else if (bkind === 'zero_fallback') {
      const cv = isNum(out.fwer.bench_coverage) ? Math.round(out.fwer.bench_coverage * 100) : '?';
      spaNote += ' ' + t('g_spa_note_zero_fallback', cv);
    }
    cards.push(gateCard({
      title: t('g_spa_title'), sub: t('g_spa_sub'),
      badgeKind: bk, badgeTxt: bt,
      note: spaNote,
      rows: [
        [t('g_spa_p'), fmt(spa.p_value, 3), isNum(spa.p_value) && spa.p_value <= 0.1 ? 'pos' : 'neg'],
        [t('g_spa_best'), spa.best ? escapeHtml(String(spa.best)) : '—'],
        [t('g_spa_nrej'), isNum(nRej) ? String(nRej) : '—', isNum(nRej) && nRej > 0 ? 'pos' : 'neg'],
      ],
    }));
  }

  // --- 成本壓力 ---
  if (out.cost_stress) {
    const c = out.cost_stress;
    let bk = 'na', bt = t('b_na');
    if (isNum(c.x3_sharpe)) { if (c.x3_sharpe > 0) { bk = 'pass'; bt = t('b_x3pos'); } else { bk = 'fail'; bt = t('b_x3neg'); } }
    cards.push(gateCard({
      title: t('g_cost_title'), sub: t('g_cost_sub'),
      badgeKind: bk, badgeTxt: bt,
      note: t('g_cost_note'),
      rows: [
        [t('g_cost_x1'), fmt(c.x1_sharpe, 2), isNum(c.x1_sharpe) && c.x1_sharpe > 0 ? 'pos' : 'neg'],
        [t('g_cost_x3'), fmt(c.x3_sharpe, 2), isNum(c.x3_sharpe) && c.x3_sharpe > 0 ? 'pos' : 'neg'],
        [t('g_cost_x6'), fmt(c.x6_sharpe, 2), isNum(c.x6_sharpe) && c.x6_sharpe > 0 ? 'pos' : 'neg'],
      ],
    }));
  }

  // --- 基準比較 ---
  if (out.benchmark_compare) {
    const b = out.benchmark_compare;
    const beats = b.strategy_beats;
    cards.push(gateCard({
      title: t('g_bench_title'), sub: t('g_bench_sub'),
      badgeKind: beats ? 'pass' : 'fail', badgeTxt: beats ? t('b_beatbench') : t('b_notbeatbench'),
      note: t('g_bench_note'),
      rows: [
        [t('g_bench_vs'), `${fmt(out.metrics.sharpe, 2)} vs ${fmt(b.bench_sharpe, 2)}`, beats ? 'pos' : 'neg'],
        [t('g_bench_cagr'), fmtPct(b.bench_cagr, 1)],
        [t('g_bench_excess'), fmtSigned(b.excess_cagr * 100, 1) + '%', isNum(b.excess_cagr) && b.excess_cagr > 0 ? 'pos' : 'neg'],
      ],
    }));
  }

  $('gatesGrid').innerHTML = cards.join('');
}

// ============================================================================
// 7. 手繪圖表(inline SVG,不引第三方)
// ============================================================================
const SVGNS = 'http://www.w3.org/2000/svg';

// 7a. 淨值曲線 vs 基準(對數視覺可選,這裡用線性但自動縮放)
// 長序列(日內 1 分 K 等,>50k 期)抽稀:單條 SVG path 塞 50 萬點會拖垮瀏覽器。
// 分桶保 min/max(桶內先出現者在前)→ 回撤尖點不會被抽稀抹掉;首末點原樣保留。
// 只影響「畫」,統計全部在引擎端用完整序列算完才回來。
function decimateSeries(arr, maxPts = 2400) {
  const n = arr.length;
  if (n <= maxPts) return arr;
  const buckets = Math.floor(maxPts / 2) - 1;
  const out = [arr[0]];
  const step = (n - 2) / buckets;
  for (let b = 0; b < buckets; b++) {
    const s = 1 + Math.floor(b * step);
    const e = Math.min(n - 1, 1 + Math.floor((b + 1) * step));
    if (s >= e) continue;
    let mi = s, ma = s;
    for (let i = s + 1; i < e; i++) {
      if (arr[i] < arr[mi]) mi = i;
      if (arr[i] > arr[ma]) ma = i;
    }
    const first = Math.min(mi, ma), second = Math.max(mi, ma);
    out.push(arr[first]);
    if (second !== first) out.push(arr[second]);
  }
  out.push(arr[n - 1]);
  return out;
}

function drawEquityChart(equity, bench, parsed) {
  const host = $('equityChart');
  host.innerHTML = '';
  if (!equity || equity.length < 2) {
    host.innerHTML = `<div class="chart-empty">${t('equity_empty')}</div>`;
    $('equityLegend').innerHTML = '';
    return;
  }
  // 抽稀防呆(日內長序列);srcN 記原始期數,x 軸末標籤才對得上真正的最後一期。
  const srcN = equity.length;
  equity = decimateSeries(equity);
  if (bench && bench.length >= 2) bench = decimateSeries(bench);

  const W = 640, H = 300, padL = 46, padR = 16, padT = 16, padB = 28;
  const iw = W - padL - padR, ih = H - padT - padB;

  const series = [equity];
  const hasBench = bench && bench.length >= 2;
  if (hasBench) series.push(bench);

  let vmin = Infinity, vmax = -Infinity;
  series.forEach(s => s.forEach(v => { if (isFinite(v)) { vmin = Math.min(vmin, v); vmax = Math.max(vmax, v); } }));
  if (!isFinite(vmin) || !isFinite(vmax) || vmin === vmax) { vmin = 0.9; vmax = 1.1; }
  const pad = (vmax - vmin) * 0.08 || 0.05;
  vmin -= pad; vmax += pad;

  const n = equity.length;
  const X = (i) => padL + (i / (n - 1)) * iw;
  const Y = (v) => padT + (1 - (v - vmin) / (vmax - vmin)) * ih;

  const svg = document.createElementNS(SVGNS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', t('chart_equity_h'));

  // defs: 面積漸層
  svg.innerHTML = `<defs>
    <linearGradient id="eqgrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="var(--scan)" stop-opacity="0.28"/>
      <stop offset="100%" stop-color="var(--scan)" stop-opacity="0"/>
    </linearGradient></defs>`;

  // y 軸格線 + 標籤(4 格)
  for (let g = 0; g <= 4; g++) {
    const val = vmin + (g / 4) * (vmax - vmin);
    const yy = Y(val);
    svg.appendChild(mkLine(padL, yy, W - padR, yy, 'grid-line'));
    svg.appendChild(mkText(padL - 8, yy + 3, fmt(val, 2), 'axis-text', 'end'));
  }
  // 基準線 1.0(起點)
  if (vmin < 1 && vmax > 1) {
    const y1 = Y(1.0);
    svg.appendChild(mkLine(padL, y1, W - padR, y1, 'axis-line'));
  }

  // 面積(策略)
  let areaD = `M ${X(0)} ${Y(equity[0])}`;
  for (let i = 1; i < n; i++) areaD += ` L ${X(i)} ${Y(equity[i])}`;
  areaD += ` L ${X(n - 1)} ${Y(vmin)} L ${X(0)} ${Y(vmin)} Z`;
  const area = document.createElementNS(SVGNS, 'path');
  area.setAttribute('d', areaD); area.setAttribute('class', 'eq-area');
  svg.appendChild(area);

  // 基準線
  if (hasBench) svg.appendChild(mkPath(bench, X, Y, 'eq-bench'));
  // 策略線
  svg.appendChild(mkPath(equity, X, Y, 'eq-strat'));
  // 策略末端發光點:視線落點,強調期末淨值
  const ex = X(n - 1), ey = Y(equity[n - 1]);
  const halo = document.createElementNS(SVGNS, 'circle');
  halo.setAttribute('cx', ex); halo.setAttribute('cy', ey); halo.setAttribute('r', 6);
  halo.setAttribute('class', 'eq-end-halo');
  svg.appendChild(halo);
  const dot = document.createElementNS(SVGNS, 'circle');
  dot.setAttribute('cx', ex); dot.setAttribute('cy', ey); dot.setAttribute('r', 3);
  dot.setAttribute('class', 'eq-end-dot');
  svg.appendChild(dot);

  // x 軸端點標籤(首/末日期或期數)
  const lab0 = parsed && parsed.dates ? parsed.dates[0] : t('equity_period', 1);
  const labN = parsed && parsed.dates ? parsed.dates[Math.min(parsed.dates.length - 1, srcN - 1)] : t('equity_period', srcN);
  svg.appendChild(mkText(padL, H - 8, lab0, 'axis-text', 'start'));
  svg.appendChild(mkText(W - padR, H - 8, labN, 'axis-text', 'end'));

  host.appendChild(svg);

  $('equityLegend').innerHTML =
    `<span><i style="background:var(--scan)"></i>${escapeHtml(t('equity_legend_strat', fmt(equity[n - 1], 2)))}</span>` +
    (hasBench ? `<span><i style="background:var(--ink-2)"></i>${escapeHtml(t('equity_legend_bench', fmt(bench[bench.length - 1], 2)))}</span>` : '');
}

// 7b. permutation null 分布 + 真實 Sharpe 標線 —— 「你 vs 純運氣」的必殺視覺
//     引擎只回 p95 / real / p_value(不回整條 null 陣列),但 p_value 已是「隨機版本裡
//     贏過你的比例」的無偏估計 → 由它反推「你贏過 X% 的隨機版本」這個作品集金句。
//     鐘形曲線是常態近似(以 0 為心、p95≈1.645σ 推 σ),純為示意形狀;真正的統計事實
//     (你贏過幾成、p 值、是否過門檻)全部來自引擎回傳值,標線用真值。
function drawNullChart(perm) {
  const host = $('nullChart');
  host.innerHTML = '';
  if (!perm || !isNum(perm.real_sharpe) || !isNum(perm.null_p95_sharpe)) {
    host.innerHTML = `<div class="chart-empty">${t('null_empty')}</div>`;
    $('nullLegend').innerHTML = '';
    return;
  }
  const p95 = perm.null_p95_sharpe;
  const real = perm.real_sharpe;
  const pv = isNum(perm.p_value) ? perm.p_value : null;
  const passed = perm.passes;
  const accent = passed ? 'var(--real)' : 'var(--overfit)';
  const accentGlow = passed ? 'var(--real-glow)' : 'var(--overfit-glow)';
  // 你贏過的隨機版本比例 = 1 - p_value(p 值 = 隨機裡 >= 你的比例)
  const beatPct = pv != null ? Math.max(0, Math.min(100, (1 - pv) * 100)) : null;

  const sigma = Math.max(1e-6, Math.abs(p95) / 1.645);
  const mu = 0;

  const W = 520, H = 268, padL = 14, padR = 14, padT = 40, padB = 40;
  const iw = W - padL - padR, ih = H - padT - padB;
  const baseY = padT + ih;

  // x 範圍:涵蓋分布尾巴與真實 Sharpe,兩側各留一點呼吸
  const lo = Math.min(mu - 3.3 * sigma, real - 0.35 * Math.abs(real) - 0.25);
  const hi = Math.max(mu + 3.3 * sigma, real + 0.35 * Math.abs(real) + 0.25);
  const X = (v) => padL + ((v - lo) / (hi - lo)) * iw;

  // 常態密度曲線取樣(平滑,非直方 bar)
  const N = 120;
  const pts = [];
  let dmax = 0;
  for (let i = 0; i <= N; i++) {
    const x = lo + (i / N) * (hi - lo);
    const y = Math.exp(-0.5 * ((x - mu) / sigma) ** 2);
    pts.push({ x, y });
    dmax = Math.max(dmax, y);
  }
  const Y = (d) => padT + (1 - d / dmax) * (ih * 0.9);  // 頂端留白讓峰不頂天

  const svg = document.createElementNS(SVGNS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label',
    t('null_aria', fmt(real, 2), beatPct != null ? Math.round(beatPct) : null));

  const xr = X(real);
  const clampXr = Math.max(padL + 1, Math.min(W - padR - 1, xr));

  svg.innerHTML = `<defs>
    <linearGradient id="nullFill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="var(--scan)" stop-opacity="0.20"/>
      <stop offset="100%" stop-color="var(--scan)" stop-opacity="0.015"/>
    </linearGradient>
    <linearGradient id="nullBeat" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="${passed ? 'var(--real)' : 'var(--overfit)'}" stop-opacity="0.24"/>
      <stop offset="100%" stop-color="${passed ? 'var(--real)' : 'var(--overfit)'}" stop-opacity="0.02"/>
    </linearGradient>
    <clipPath id="beatClip"><rect x="${padL}" y="${padT - 20}" width="${Math.max(0, clampXr - padL)}" height="${ih + 24}"/></clipPath>
  </defs>`;

  // 主分布面積(你「沒」贏的隨機世界:曲線下全域,淡青綠)
  let d = `M ${X(lo)} ${baseY}`;
  pts.forEach(p => { d += ` L ${X(p.x)} ${Y(p.y)}`; });
  d += ` L ${X(hi)} ${baseY} Z`;
  const area = document.createElementNS(SVGNS, 'path');
  area.setAttribute('d', d);
  area.setAttribute('fill', 'url(#nullFill)');
  svg.appendChild(area);

  // 「你贏過的」區塊:同一面積但裁到 real 左側,填判決色 → 一眼看見你吃掉了多少隨機世界
  const beatArea = document.createElementNS(SVGNS, 'path');
  beatArea.setAttribute('d', d);
  beatArea.setAttribute('fill', 'url(#nullBeat)');
  beatArea.setAttribute('clip-path', 'url(#beatClip)');
  svg.appendChild(beatArea);

  // 分布輪廓線
  let ld = `M ${X(pts[0].x)} ${Y(pts[0].y)}`;
  pts.forEach((p, i) => { if (i) ld += ` L ${X(p.x)} ${Y(p.y)}`; });
  const line = document.createElementNS(SVGNS, 'path');
  line.setAttribute('d', ld);
  line.setAttribute('class', 'null-curve');
  svg.appendChild(line);

  // 底軸
  svg.appendChild(mkLine(padL, baseY, W - padR, baseY, 'axis-line'));

  // 0 參考刻度(隨機世界的期望夏普)
  const x0 = X(0);
  if (x0 > padL + 6 && x0 < W - padR - 6) {
    svg.appendChild(mkLine(x0, baseY, x0, baseY + 5, 'axis-line'));
    svg.appendChild(mkText(x0, baseY + 17, '0', 'axis-text', 'middle'));
  }

  // p95 門檻線(過關線,琥珀虛線)
  const xp95 = X(p95);
  if (xp95 > padL && xp95 < W - padR) {
    svg.appendChild(mkLine(xp95, Y(dmax * 0.02), xp95, baseY, 'hist-p95'));
    svg.appendChild(mkText(xp95, baseY + 17, t('null_passline', fmt(p95, 1)), 'axis-text hist-p95-txt', 'middle'));
  }

  // real Sharpe 標線 + 頂端旗標(判決色,發光)
  const realLine = mkLine(clampXr, padT - 22, clampXr, baseY, 'hist-real');
  realLine.style.stroke = accent;
  svg.appendChild(realLine);
  // 旗標膠囊:「你 · Sharpe X.XX」/ 「You · X.XX」
  const flagTxt = t('null_flag', fmt(real, 2));
  const flagW = Math.max(58, flagTxt.length * 8.4 + 16);
  const onRight = clampXr > W - padR - flagW / 2 - 4;
  const flagX = onRight ? clampXr - flagW - 2 : (clampXr < padL + flagW / 2 + 4 ? clampXr + 2 : clampXr - flagW / 2);
  const flagG = document.createElementNS(SVGNS, 'g');
  const flagRect = document.createElementNS(SVGNS, 'rect');
  flagRect.setAttribute('x', flagX); flagRect.setAttribute('y', padT - 34);
  flagRect.setAttribute('width', flagW); flagRect.setAttribute('height', 20);
  flagRect.setAttribute('rx', 2);
  flagRect.setAttribute('fill', accent);
  flagG.appendChild(flagRect);
  const flagLabel = mkText(flagX + flagW / 2, padT - 20, flagTxt, 'null-flag-txt', 'middle');
  flagG.appendChild(flagLabel);
  svg.appendChild(flagG);
  // 旗標小尖角指向標線
  const tri = document.createElementNS(SVGNS, 'path');
  tri.setAttribute('d', `M ${clampXr - 4} ${padT - 14} L ${clampXr + 4} ${padT - 14} L ${clampXr} ${padT - 9} Z`);
  tri.setAttribute('fill', accent);
  svg.appendChild(tri);

  // x 軸說明
  svg.appendChild(mkText((padL + W - padR) / 2, H - 6,
    t('null_xcaption'), 'axis-text axis-caption', 'middle'));

  host.appendChild(svg);

  // ---- 金句橫幅:你贏過 X% 的隨機版本(作品集必殺一行)----
  const banner = $('nullBanner');
  if (banner) {
    if (beatPct != null) {
      const pctTxt = beatPct >= 99.5 ? '>99' : (beatPct <= 0.5 ? '<1' : String(Math.round(beatPct)));
      const verb = passed ? t('null_beat_verb_solid') : (beatPct >= 50 ? t('null_beat_verb_beat') : t('null_beat_verb_only'));
      banner.className = 'null-banner ' + (passed ? 'good' : (beatPct >= 50 ? 'mid' : 'bad'));
      banner.innerHTML = t('null_banner', verb, pctTxt, fmt(pv, 3));
      banner.style.display = 'flex';
    } else {
      banner.style.display = 'none';
    }
  }

  $('nullLegend').innerHTML =
    `<span><i style="background:var(--scan-dim);opacity:.6"></i>${escapeHtml(t('null_legend_dist'))}</span>` +
    `<span><i style="background:var(--incon)"></i>${escapeHtml(t('null_legend_thresh'))}</span>` +
    `<span><i style="background:${passed ? 'var(--real)' : 'var(--overfit)'}"></i>${escapeHtml(t('null_legend_real', fmt(real, 2)))}</span>`;
}

// SVG 小工具
function mkLine(x1, y1, x2, y2, cls) {
  const l = document.createElementNS(SVGNS, 'line');
  l.setAttribute('x1', x1); l.setAttribute('y1', y1);
  l.setAttribute('x2', x2); l.setAttribute('y2', y2);
  if (cls) l.setAttribute('class', cls);
  return l;
}
function mkText(x, y, txt, cls, anchor) {
  const t = document.createElementNS(SVGNS, 'text');
  t.setAttribute('x', x); t.setAttribute('y', y);
  if (cls) t.setAttribute('class', cls);
  if (anchor) t.setAttribute('text-anchor', anchor);
  t.textContent = txt;
  return t;
}
function mkPath(arr, X, Y, cls) {
  let d = `M ${X(0)} ${Y(arr[0])}`;
  for (let i = 1; i < arr.length; i++) d += ` L ${X(i)} ${Y(arr[i])}`;
  const p = document.createElementNS(SVGNS, 'path');
  p.setAttribute('d', d); p.setAttribute('class', cls);
  return p;
}

// ============================================================================
// 8. 錯誤 / 工具
// ============================================================================
function showError(html) { const e = $('errbox'); e.innerHTML = html; e.classList.add('show'); }
function hideError() { $('errbox').classList.remove('show'); }
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ============================================================================
// 9. UI 綁定
// ============================================================================
// 檔案摘要 pill 的名稱 + 中繼資訊(切語言時要重繪 modeTxt,故獨立成函式)。
function refreshFilePill() {
  const parsed = State.parsed;
  if (!parsed) return;
  const pill = $('filePill');
  pill.classList.add('show');
  $('fileName').textContent = parsed.srcName;
  const modeTxt = parsed.mode === 'matrix'
    ? t('file_meta_matrix', parsed.colNames.length, parsed.nRows)
    : t('file_meta_series', parsed.nRows, !!parsed.dates);
  $('fileMeta').textContent = `(${modeTxt})`;
}

function setParsed(parsed) {
  State.parsed = parsed;
  refreshFilePill();
  $('runBtn').disabled = false;
  hideError();
  // demo 動線:資料就緒 → 自動帶到「校準參數」區,下一步一目瞭然(尊重 reduced-motion)
  smoothScrollTo($('calibrate'));
  log('已解析:', { mode: parsed.mode, nRows: parsed.nRows, noteKey: parsed.noteKey });
}

async function handleFile(file) {
  if (!file) return;
  hideError();
  try {
    log('讀檔:', file.name, file.size, 'bytes');
    const text = await decodeFile(file);
    const parsed = parseCSV(text, file.name);
    setParsed(parsed);
  } catch (e) {
    warn('解析失敗:', e);
    State.parsed = null;
    $('runBtn').disabled = true;
    $('filePill').classList.remove('show');
    showError(t('err_csv_fail', escapeHtml(String(e.message || e))));
  }
}

async function loadSample(which) {
  hideError();
  const path = which === 'genuine' ? 'sample/sample_genuine.csv' : 'sample/sample_overfit.csv';
  try {
    const resp = await fetch(path, { cache: 'no-store' });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const text = await resp.text();
    const parsed = parseCSV(text, t(which === 'genuine' ? 'sample_genuine_name' : 'sample_overfit_name'));
    setParsed(parsed);
    // 示範預設參數:過擬合樣本用高 n_trials 展示 DSR 通縮
    if (which === 'overfit') { $('nTrials').value = 150; }
    else { $('nTrials').value = 1; }
    log('已載入示範:', which);
  } catch (e) {
    warn('載入示範失敗:', e);
    showError(t('err_sample_fail', escapeHtml(String(e.message || e))));
  }
}

function bindUI() {
  const drop = $('drop'), input = $('fileInput');
  input.addEventListener('change', (e) => { if (e.target.files[0]) handleFile(e.target.files[0]); });

  // 鍵盤可達性:上傳區可 Tab 聚焦,Enter / Space 開啟檔案選擇(等同點擊)
  drop.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
      e.preventDefault();
      input.click();
    }
  });

  ['dragenter', 'dragover'].forEach(ev =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('dragover'); }));
  ['dragleave', 'drop'].forEach(ev =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('dragover'); }));
  drop.addEventListener('drop', (e) => {
    const f = e.dataTransfer.files[0];
    if (f) handleFile(f);
  });

  $('fileClear').addEventListener('click', (e) => {
    e.preventDefault(); e.stopPropagation();
    State.parsed = null;
    $('filePill').classList.remove('show');
    $('runBtn').disabled = true;
    input.value = '';
  });

  $('sampleGenuine').addEventListener('click', () => loadSample('genuine'));
  $('sampleOverfit').addEventListener('click', () => loadSample('overfit'));
  $('runBtn').addEventListener('click', runAnalysis);

  $('benchSel').addEventListener('change', (e) => {
    const k = e.target.value;
    $('benchDesc').textContent = k && State.baselines && State.baselines[k]
      ? benchDesc(State.baselines[k], k)
      : t('param_bench_desc');
  });

  // 語言切換鈕
  document.querySelectorAll('.lang-btn').forEach(b => {
    b.addEventListener('click', () => setLang(b.getAttribute('data-lang')));
  });

  // 引擎預熱:縮短點「照妖」後的等待。
  // ① 首次互動(pointerdown)立即預熱——使用者一動手,十之八九會走到照妖。
  // ② 就算不動手:load 完 3 秒後趁瀏覽器 idle 自動預熱(requestIdleCallback,無則直接跑),
  //    首載 ~15s 的 Pyodide 下載在使用者讀文案時就完成。
  const warm = () => { if (!State.engineReady && !State.loading) ensureEngine().catch(() => {}); };
  const warmOnce = () => { document.removeEventListener('pointerdown', warmOnce); warm(); };
  document.addEventListener('pointerdown', warmOnce, { once: true });
  const idleWarm = () => {
    if (typeof window.requestIdleCallback === 'function') {
      window.requestIdleCallback(warm, { timeout: 4000 });
    } else {
      warm();
    }
  };
  if (document.readyState === 'complete') setTimeout(idleWarm, 3000);
  else window.addEventListener('load', () => setTimeout(idleWarm, 3000), { once: true });
}

// 尊重 prefers-reduced-motion 的捲動:偏好減少動態時用瞬跳,否則平滑。
function smoothScrollTo(node) {
  if (!node) return;
  let reduce = false;
  try { reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) {}
  node.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
}

// ============================================================================
// 啟動
// ============================================================================
document.addEventListener('DOMContentLoaded', () => {
  log('啟動 · Pyodide', PYODIDE_VERSION);
  // 先定語言(讀存檔偏好),再綁 UI / 載基準,套上靜態文案
  State.lang = readLangPref();
  bindUI();
  setLang(State.lang);   // 套靜態 i18n + 語言鈕態(此時 baselines 尚未載入,rebuildBenchOptions 無害)
  loadBaselines();       // 載完會自行 rebuildBenchOptions,用當前語言標籤
  setEngineStatus(t('engine_idle'), '');
});
