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
  const setMsg = (m) => { $('loaderMsg').textContent = m; log('loader:', m); };
  loader.classList.add('show');
  setEngineStatus('載入中…', 'warm');

  try {
    if (typeof loadPyodide !== 'function') {
      throw new Error('Pyodide 載入器不存在(CDN 未載入)。請確認網路可連 cdn.jsdelivr.net。');
    }
    setMsg('正在啟動 Pyodide 執行環境…');
    State.pyodide = await loadPyodide({
      indexURL: `https://cdn.jsdelivr.net/pyodide/${PYODIDE_VERSION}/full/`,
    });
    log('Pyodide 啟動完成');

    setMsg('正在載入 NumPy / Pandas(數值套件,約 10MB)…');
    await State.pyodide.loadPackage(['numpy', 'pandas']);
    log('numpy/pandas 載入完成');

    setMsg('正在注入裁判引擎程式碼…');
    await loadEngineSource(State.pyodide);
    log('engine 模組注入完成');

    // 冒煙測試:確認 analyze 可呼叫
    setMsg('正在自我測試引擎…');
    const smoke = await State.pyodide.runPythonAsync(`
import json
from engine import analyze
_r = analyze({"mode":"returns","returns":[0.01,-0.005,0.008,0.002,-0.003,0.006],
              "dates":None,"n_trials":1,"periods_per_year":252})
json.dumps({"ok": _r["ok"], "verdict": _r["verdict"]["overall"]})
`);
    log('引擎自測:', smoke);

    State.engineReady = true;
    setEngineStatus('引擎就緒', 'live');
    loader.classList.remove('show');
    return true;
  } catch (e) {
    warn('引擎載入失敗:', e);
    showError(`<b>裁判引擎載入失敗。</b><br>${escapeHtml(String(e.message || e))}<br>
      這通常是網路無法連到 Pyodide CDN(cdn.jsdelivr.net)。請檢查連線後重試。`);
    setEngineStatus('引擎失敗', '');
    loader.classList.remove('show');
    return false;
  } finally {
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
    if (!resp.ok) throw new Error(`抓取 ${url} 失敗(HTTP ${resp.status})`);
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
async function loadBaselines() {
  try {
    const resp = await fetch('benchmarks/baselines.json', { cache: 'no-store' });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    State.baselines = data.benchmarks || {};
    const sel = $('benchSel');
    for (const [key, b] of Object.entries(State.baselines)) {
      const opt = el('option');
      opt.value = key;
      opt.textContent = b.name_zh || b.name_en || key;
      sel.appendChild(opt);
    }
    log('baselines 載入:', Object.keys(State.baselines));
  } catch (e) {
    warn('baselines 載入失敗(可離線使用,只是少了對照基準):', e);
    $('benchDesc').textContent = '對照基準載入失敗(不影響其他檢定)。';
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
  let m = s.match(/^(\d{3,4})[-/.](\d{1,2})[-/.](\d{1,2})/);
  if (m) {
    let y = parseInt(m[1], 10);
    if (m[1].length === 3 || (y < 1911 && y > 1)) y += 1911; // 民國年(3 位或小數值)
    const mo = String(parseInt(m[2], 10)).padStart(2, '0');
    const d = String(parseInt(m[3], 10)).padStart(2, '0');
    return `${y}-${mo}-${d}`;
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
  const absMax = Math.max(...clean.map(Math.abs));
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
  if (rawLines.length < 2) throw new Error('檔案內容太少:至少需要一列標題(可略)加幾列資料。');

  const delim = detectDelim(rawLines[0]);
  const rows = rawLines.map(l => l.split(delim).map(c => c.trim()));

  // 標題偵測:第一列若無任何可解析數字,視為標題
  const firstNumericCount = rows[0].filter(c => isNum(parseNumberCell(c))).length;
  let header = null, body = rows;
  if (firstNumericCount === 0) { header = rows[0]; body = rows.slice(1); }
  if (body.length < 2) throw new Error('資料列太少(去掉標題後不足 2 列)。請確認每列是一期報酬或淨值。');

  const nCols = Math.max(...body.map(r => r.length));

  // ---- 情況 A:單欄 → returns 或 nav ----
  if (nCols === 1) {
    const vals = body.map(r => parseNumberCell(r[0]));
    const good = vals.filter(isNum).length;
    if (good < 2) throw new Error('抓不到可解析的數字。請確認欄位是數值(報酬率或淨值)。');
    const kind = detectSeriesKind(vals);
    const clean = vals.map(v => isNum(v) ? v : 0);
    const returns = kind === 'nav' ? navToReturns(clean) : clean;
    return {
      mode: 'returns', dates: null, returns, matrix: null, colNames: null,
      srcName, nRows: returns.length,
      note: kind === 'nav' ? '偵測為淨值序列,已自動轉為逐期報酬。' : '偵測為逐期報酬序列。',
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
    if (good < 2) throw new Error('第二欄抓不到數字。請確認格式為「日期,報酬」或「日期,淨值」。');
    const kind = detectSeriesKind(vals);
    const clean = vals.map(v => isNum(v) ? v : 0);
    const returns = kind === 'nav' ? navToReturns(clean) : clean;
    return {
      mode: 'returns', dates, returns, matrix: null, colNames: null,
      srcName, nRows: returns.length,
      note: (kind === 'nav' ? '偵測為「日期+淨值」,已轉為報酬。' : '偵測為「日期+報酬」。')
            + `含日期欄,每年期數可自動推算。`,
    };
  }

  // ---- 情況 C:matrix(第一欄日期 + 多欄策略,或多欄純數值)----
  const hasDateCol = firstColIsDate;
  const startCol = hasDateCol ? 1 : 0;
  const nStrat = nCols - startCol;
  if (nStrat < 1) throw new Error('欄位結構無法辨識。');

  const dates = hasDateCol ? col0.map(normalizeDate) : null;
  const names = [];
  for (let c = startCol; c < nCols; c++) {
    names.push((header && header[c]) ? header[c] : `策略${c - startCol + 1}`);
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
      note: kind === 'nav' ? '單策略(淨值→報酬)。' : '單策略報酬。',
    };
  }

  // 真 matrix:每欄各自偵測 nav/returns
  const matrix = {};
  for (let ci = 0; ci < names.length; ci++) {
    const c = startCol + ci;
    const vals = body.map(r => parseNumberCell(r[c]));
    const kind = detectSeriesKind(vals);
    const clean = vals.map(v => isNum(v) ? v : 0);
    matrix[names[ci]] = kind === 'nav' ? navToReturns(clean) : clean;
  }
  return {
    mode: 'matrix', dates, returns: null, matrix, colNames: names,
    srcName, nRows: body.length,
    note: `偵測為 ${names.length} 策略矩陣。將啟用 PBO / SPA / Romano-Wolf 全套閘;主序列取樣本內夏普最高者。`,
  };
}

// ============================================================================
// 4. 基準對齊
// ============================================================================
// 把選中基準的 returns 對齊到使用者資料。
// 有日期 → 依日期交集對齊(逐日期查表);無日期 → 用尾端長度對齊。
function alignBenchmark(benchKey, userDates, userLen) {
  if (!benchKey || !State.baselines || !State.baselines[benchKey]) return { arr: null, note: '' };
  const b = State.baselines[benchKey];
  const bDates = b.dates || null;
  const bRet = b.returns || [];

  if (userDates && bDates && bDates.length) {
    // 建 date→return 查表,對使用者每個日期取值;缺的填 0
    const map = new Map();
    for (let i = 0; i < bDates.length; i++) map.set(bDates[i], bRet[i]);
    const out = new Array(userLen).fill(0);
    let hit = 0;
    for (let i = 0; i < userDates.length && i < userLen; i++) {
      const v = map.get(userDates[i]);
      if (v != null && isFinite(v)) { out[i] = v; hit++; }
    }
    if (hit >= userLen * 0.5) {
      return { arr: out, note: `基準依日期對齊(${hit}/${userLen} 日命中)。` };
    }
    // 日期重疊太低 → 退化成長度對齊
    warn('基準日期重疊過低,改用長度對齊');
  }
  // 長度對齊:取基準尾端 userLen 期(較新的一段)
  const tail = bRet.slice(Math.max(0, bRet.length - userLen));
  const out = new Array(userLen).fill(0);
  for (let i = 0; i < tail.length; i++) out[out.length - tail.length + i] = tail[i];
  return {
    arr: out,
    note: userDates
      ? '基準日期與你的資料不重疊,改用長度對齊(取基準最近一段),僅供概略對照。'
      : '你的資料無日期欄,基準以長度對齊(取基準最近一段),僅供概略對照。',
  };
}

// ============================================================================
// 5. 執行分析
// ============================================================================
async function runAnalysis() {
  hideError();
  if (!State.parsed) { showError('請先上傳 CSV 或載入示範資料。'); return; }

  const btn = $('runBtn');
  btn.classList.add('busy'); btn.disabled = true;
  $('runBtn').querySelector('.txt').textContent = '準備引擎…';

  try {
    const ok = await ensureEngine();
    if (!ok) return;

    $('runBtn').querySelector('.txt').textContent = '運算中…';

    const p = State.parsed;
    const nTrials = Math.max(1, parseInt($('nTrials').value, 10) || 1);
    const ppyRaw = $('ppy').value;
    const ppy = ppyRaw ? parseFloat(ppyRaw) : null;

    // 基準對齊
    const benchKey = $('benchSel').value;
    const userLen = p.mode === 'matrix'
      ? Math.min(...Object.values(p.matrix).map(a => a.length))
      : p.returns.length;
    const { arr: benchArr, note: benchNote } = alignBenchmark(benchKey, p.dates, userLen);

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
    };
    log('payload:', { mode: payload.mode, n_trials: nTrials, ppy, hasBench: !!benchArr,
                      len: userLen, benchNote });

    // 傳進 Pyodide:用 JSON 字串最穩(避免 toPy 對 null 的細節)
    State.pyodide.globals.set('_payload_json', JSON.stringify(payload));
    const result = await State.pyodide.runPythonAsync(`
import json
from engine import analyze
_p = json.loads(_payload_json)
_out = analyze(_p)
_out  # 回傳 dict,交給 JS 用 toJs 轉
`);
    const out = result.toJs({ dict_converter: Object.fromEntries });
    if (result.destroy) result.destroy();
    log('analyze 回傳:', out);

    if (!out.ok) {
      showError('分析失敗:' + (out.warnings ? out.warnings.join('；') : '未知錯誤'));
      return;
    }
    renderResults(out, p, benchNote);
    $('results').classList.add('show');
    $('results').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) {
    warn('runAnalysis 例外:', e);
    showError('<b>運算發生例外。</b><br>' + escapeHtml(String(e.message || e)));
  } finally {
    btn.classList.remove('busy'); btn.disabled = false;
    $('runBtn').querySelector('.txt').textContent = '照妖 · 開始統計檢定';
  }
}

// ============================================================================
// 6. 渲染結果
// ============================================================================
const VERDICT_META = {
  'likely-real':    { text: '可能是真的 edge', cls: 'likely-real',
    sub: '扣掉多重檢定與運氣成分後,證據仍支持這條策略帶了真訊號。但這只代表「沒發現明顯過擬合」,不保證會賺——真錢前務必前向驗證。',
    stamp: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>' },
  'likely-overfit': { text: '疑似過擬合', cls: 'likely-overfit',
    sub: '多項指標顯示這條曲線八成是把雜訊擬合出來的產物。在賠真錢之前發現它,是這台機器的價值。',
    stamp: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>' },
  'inconclusive':   { text: '結論不明', cls: 'inconclusive',
    sub: '證據不足以判定真偽。通常是樣本太短、訊號不夠乾淨,或剛好卡在門檻。建議補更多樣本或做前向測試再定奪。',
    stamp: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 17h.01M9.1 9a3 3 0 0 1 5.8 1c0 2-3 2.5-3 4"/></svg>' },
};

function renderResults(out, parsed, benchNote) {
  const v = out.verdict;
  const meta = VERDICT_META[v.overall] || VERDICT_META['inconclusive'];

  // ---- 主判決 ----
  const card = $('verdictCard');
  card.setAttribute('data-verdict', v.overall);
  $('verdictText').textContent = meta.text;
  $('verdictStamp').innerHTML = meta.stamp;
  $('verdictSub').textContent = meta.sub;

  // 分數 + 量表(動畫)
  const score = isNum(v.score_0to100) ? v.score_0to100 : 0;
  animateNumber($('scoreNum'), score, 900, 0);
  requestAnimationFrame(() => { $('gaugeFill').style.width = Math.max(2, score) + '%'; });

  // reasons
  const rl = $('reasonsList'); rl.innerHTML = '';
  (v.reasons || []).forEach(r => {
    rl.appendChild(el('li', null,
      `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M9 12l2 2 4-4"/><circle cx="12" cy="12" r="9"/></svg><span>${escapeHtml(r)}</span>`));
  });

  // red flags
  const flagsBox = $('flagsBox'); const fl = $('flagsList'); fl.innerHTML = '';
  const flags = v.red_flags || [];
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

  // warnings + bench note
  const warnBox = $('warningsBox');
  const allWarn = [...(out.warnings || [])];
  if (benchNote) allWarn.push(benchNote);
  if (parsed && parsed.note) allWarn.push('解析:' + parsed.note);
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
    metricCell('年化夏普', fmt(m.sharpe, 2), shCls),
    metricCell('Sortino', fmt(m.sortino, 2), isNum(m.sortino) && m.sortino > 0 ? 'pos' : (isNum(m.sortino) ? 'neg' : '')),
    metricCell('CAGR', fmtPct(m.cagr, 1), cagrCls),
    metricCell('年化波動', fmtPct(m.ann_vol, 1), ''),
    metricCell('最大回撤', fmtPct(m.max_drawdown, 1), isNum(m.max_drawdown) && m.max_drawdown < -0.3 ? 'neg' : 'warn'),
    metricCell('Calmar', fmt(m.calmar, 2), ''),
    metricCell('期末淨值', fmt(m.final_equity, 2), isNum(m.final_equity) && m.final_equity >= 1 ? 'pos' : 'neg', '×'),
    metricCell('報酬集中度', fmtPct(m.top_bar_concentration, 0), concCls),
    metricCell('樣本期數', isNum(m.n_periods) ? String(m.n_periods) : '—', m.n_periods < 60 ? 'warn' : ''),
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
    let bk = 'na', bt = 'N/A';
    if (isNum(prob)) {
      if (prob >= 0.95) { bk = 'pass'; bt = '高信心'; }
      else if (prob >= 0.60) { bk = 'mid'; bt = '過雜訊地板'; }
      else { bk = 'fail'; bt = '疑似雜訊'; }
    }
    const barW = isNum(prob) ? Math.round(prob * 100) : 0;
    cards.push(gateCard({
      title: '通縮夏普 DSR', sub: 'Deflated Sharpe · 多重檢定校正',
      badgeKind: bk, badgeTxt: bt,
      note: `扣掉你試了 ${d.n_trials} 種參數的運氣成分後,真有 edge 的機率。門檻:雜訊地板 0.60、高信心 0.95。`,
      rows: [
        ['真實有 edge 機率', fmt(prob, 3), isNum(prob) && prob >= 0.6 ? 'pos' : 'neg'],
        ['年化夏普', fmt(d.sr_annual, 2), isNum(d.sr_annual) && d.sr_annual > 0 ? 'pos' : 'neg'],
        ['通縮門檻 SR₀', fmt(d.sr0, 2)],
        ['p 值', fmt(d.p_value, 3)],
      ],
      extra: `<div class="threshbar" style="margin-top:12px"><div class="f" style="width:${barW}%"></div><div class="mark" style="left:60%"></div></div>`,
    }));
  }

  // --- permutation null ---
  if (perm) {
    const pass = perm.passes;
    const pv = perm.p_value;
    let bk = 'na', bt = 'N/A';
    if (isNum(pv)) {
      if (pass) { bk = 'pass'; bt = '勝過隨機'; }
      else if (pv > 0.5) { bk = 'fail'; bt = '輸給隨機'; }
      else { bk = 'mid'; bt = '未達門檻'; }
    }
    cards.push(gateCard({
      title: '隨機重排檢定', sub: 'Matched-null · block-bootstrap',
      badgeKind: bk, badgeTxt: bt,
      note: `抹掉漂移、保留波動,bootstrap ${perm.n_perm || 0} 次。你的真實夏普要贏過 95% 的隨機版本才算過。`,
      rows: [
        ['真實年化夏普', fmt(perm.real_sharpe, 2), isNum(perm.real_sharpe) && perm.real_sharpe > 0 ? 'pos' : 'neg'],
        ['隨機 95 百分位', fmt(perm.null_p95_sharpe, 2)],
        ['p 值(越低越真)', fmt(pv, 3), isNum(pv) && pv < 0.05 ? 'pos' : (isNum(pv) && pv > 0.5 ? 'neg' : '')],
      ],
    }));
  }

  // --- PBO(matrix)---
  if (out.pbo) {
    const pbo = out.pbo.pbo;
    let bk = 'na', bt = 'N/A';
    if (isNum(pbo)) { if (pbo <= 0.5) { bk = 'pass'; bt = '未系統性崩盤'; } else { bk = 'fail'; bt = '樣本外墊底'; } }
    cards.push(gateCard({
      title: '回測過配機率 PBO', sub: 'CSCV · 樣本內外一致性',
      badgeKind: bk, badgeTxt: bt,
      note: `樣本內選到的最佳者,在樣本外墊底的機率。> 0.50 是典型過擬合特徵。`,
      rows: [
        ['過配機率 PBO', fmt(pbo, 3), isNum(pbo) && pbo <= 0.5 ? 'pos' : 'neg'],
        ['策略數', isNum(out.pbo.n_strategies) ? String(out.pbo.n_strategies) : '—'],
        ['組合數', isNum(out.pbo.n_combinations) ? String(out.pbo.n_combinations) : '—'],
      ],
    }));
  }

  // --- FWER / SPA(matrix)---
  if (out.fwer && out.fwer.spa) {
    const spa = out.fwer.spa;
    const nRej = out.fwer.n_rejected;
    let bk = 'na', bt = 'N/A';
    if (isNum(spa.p_value)) { if (spa.p_value <= 0.10) { bk = 'pass'; bt = '有倖存者'; } else { bk = 'fail'; bt = '全軍覆沒'; } }
    cards.push(gateCard({
      title: 'SPA + Romano-Wolf', sub: 'Hansen 優越性 · 家族誤差修正',
      badgeKind: bk, badgeTxt: bt,
      note: `資料挖掘後,最佳策略真的贏過基準嗎?多重比較修正後還能倖存幾條。`,
      rows: [
        ['SPA p 值', fmt(spa.p_value, 3), isNum(spa.p_value) && spa.p_value <= 0.1 ? 'pos' : 'neg'],
        ['最佳候選', spa.best ? escapeHtml(String(spa.best)) : '—'],
        ['倖存候選數', isNum(nRej) ? String(nRej) : '—', isNum(nRej) && nRej > 0 ? 'pos' : 'neg'],
      ],
    }));
  }

  // --- 成本壓力 ---
  if (out.cost_stress) {
    const c = out.cost_stress;
    let bk = 'na', bt = 'N/A';
    if (isNum(c.x3_sharpe)) { if (c.x3_sharpe > 0) { bk = 'pass'; bt = '×3 仍正'; } else { bk = 'fail'; bt = '×3 翻負'; } }
    cards.push(gateCard({
      title: '成本壓力測試', sub: 'round-trip bps · ×1/×3/×6',
      badgeKind: bk, badgeTxt: bt,
      note: `手續費/滑價放大後,edge 還撐得住嗎?×3 是保守情境。`,
      rows: [
        ['×1 夏普', fmt(c.x1_sharpe, 2), isNum(c.x1_sharpe) && c.x1_sharpe > 0 ? 'pos' : 'neg'],
        ['×3 夏普', fmt(c.x3_sharpe, 2), isNum(c.x3_sharpe) && c.x3_sharpe > 0 ? 'pos' : 'neg'],
        ['×6 夏普', fmt(c.x6_sharpe, 2), isNum(c.x6_sharpe) && c.x6_sharpe > 0 ? 'pos' : 'neg'],
      ],
    }));
  }

  // --- 基準比較 ---
  if (out.benchmark_compare) {
    const b = out.benchmark_compare;
    const beats = b.strategy_beats;
    cards.push(gateCard({
      title: '對照基準', sub: '相對買進持有的加值',
      badgeKind: beats ? 'pass' : 'fail', badgeTxt: beats ? '贏過基準' : '未贏基準',
      note: `跟被動基準比。夏普沒贏、或超額報酬為負,先確認多做這些交易值不值得。`,
      rows: [
        ['策略 vs 基準夏普', `${fmt(out.metrics.sharpe, 2)} vs ${fmt(b.bench_sharpe, 2)}`, beats ? 'pos' : 'neg'],
        ['基準 CAGR', fmtPct(b.bench_cagr, 1)],
        ['超額 CAGR', fmtSigned(b.excess_cagr * 100, 1) + '%', isNum(b.excess_cagr) && b.excess_cagr > 0 ? 'pos' : 'neg'],
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
function drawEquityChart(equity, bench, parsed) {
  const host = $('equityChart');
  host.innerHTML = '';
  if (!equity || equity.length < 2) { host.innerHTML = '<p class="cap">無足夠資料繪圖。</p>'; return; }

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
  svg.setAttribute('aria-label', '淨值曲線圖');

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

  // x 軸端點標籤(首/末日期或期數)
  const lab0 = parsed && parsed.dates ? parsed.dates[0] : '第 1 期';
  const labN = parsed && parsed.dates ? parsed.dates[Math.min(parsed.dates.length - 1, n - 1)] : `第 ${n} 期`;
  svg.appendChild(mkText(padL, H - 8, lab0, 'axis-text', 'start'));
  svg.appendChild(mkText(W - padR, H - 8, labN, 'axis-text', 'end'));

  host.appendChild(svg);

  $('equityLegend').innerHTML =
    `<span><i style="background:var(--scan)"></i>你的策略(期末 ${fmt(equity[n - 1], 2)}×)</span>` +
    (hasBench ? `<span><i style="background:var(--ink-2)"></i>對照基準(期末 ${fmt(bench[bench.length - 1], 2)}×)</span>` : '');
}

// 7b. permutation null 直方圖 + 真實 Sharpe 標線
function drawNullChart(perm) {
  const host = $('nullChart');
  host.innerHTML = '';
  if (!perm || !isNum(perm.real_sharpe) || !isNum(perm.null_p95_sharpe)) {
    host.innerHTML = '<p class="cap">樣本不足,未進行隨機重排檢定。</p>'; return;
  }
  // 引擎沒回傳整個 null 分布,只有 p95 與 real。我們用「常態近似」重建一條示意分布:
  // 以 0 為中心(H0 抹掉漂移→期望夏普≈0),p95 推 sigma(p95 ≈ 1.645σ)。純示意,標線用真值。
  const p95 = perm.null_p95_sharpe;
  const real = perm.real_sharpe;
  const sigma = Math.max(1e-6, Math.abs(p95) / 1.645);
  const mu = 0;

  const W = 520, H = 300, padL = 20, padR = 16, padT = 20, padB = 34;
  const iw = W - padL - padR, ih = H - padT - padB;

  // x 範圍:涵蓋 real 與分布尾巴
  const lo = Math.min(mu - 3.2 * sigma, real - 0.4 * Math.abs(real) - 0.2);
  const hi = Math.max(mu + 3.2 * sigma, real + 0.4 * Math.abs(real) + 0.2);
  const X = (v) => padL + ((v - lo) / (hi - lo)) * iw;

  // 生成常態密度直方圖(30 bins)
  const bins = 30;
  const dens = [];
  let dmax = 0;
  for (let i = 0; i < bins; i++) {
    const x = lo + ((i + 0.5) / bins) * (hi - lo);
    const y = Math.exp(-0.5 * ((x - mu) / sigma) ** 2);
    dens.push({ x, y });
    dmax = Math.max(dmax, y);
  }
  const Y = (d) => padT + (1 - d / dmax) * ih;

  const svg = document.createElementNS(SVGNS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', '隨機重排虛無分布直方圖');

  // 底軸
  svg.appendChild(mkLine(padL, padT + ih, W - padR, padT + ih, 'axis-line'));

  // 直方圖 bar
  const bw = iw / bins;
  dens.forEach((d, i) => {
    const bx = padL + i * bw;
    const by = Y(d.y);
    const rect = document.createElementNS(SVGNS, 'rect');
    rect.setAttribute('x', bx + 0.5); rect.setAttribute('y', by);
    rect.setAttribute('width', Math.max(0.5, bw - 1)); rect.setAttribute('height', padT + ih - by);
    rect.setAttribute('class', 'hist-bar');
    svg.appendChild(rect);
  });

  // p95 門檻線(琥珀虛線)
  const xp95 = X(p95);
  svg.appendChild(mkLine(xp95, padT, xp95, padT + ih, 'hist-p95'));
  svg.appendChild(mkText(xp95, padT - 6, `隨機 p95 = ${fmt(p95, 2)}`, 'axis-text', 'middle'));

  // real Sharpe 標線(判決色)
  const passed = perm.passes;
  const xr = X(real);
  const realLine = mkLine(xr, padT - 2, xr, padT + ih, 'hist-real');
  realLine.style.stroke = passed ? 'var(--real)' : 'var(--overfit)';
  svg.appendChild(realLine);
  // 三角標
  const tri = document.createElementNS(SVGNS, 'path');
  tri.setAttribute('d', `M ${xr - 5} ${padT - 2} L ${xr + 5} ${padT - 2} L ${xr} ${padT + 6} Z`);
  tri.setAttribute('fill', passed ? 'var(--real)' : 'var(--overfit)');
  svg.appendChild(tri);

  // x 軸標籤
  svg.appendChild(mkText(padL, H - 14, fmt(lo, 1), 'axis-text', 'start'));
  svg.appendChild(mkText(W - padR, H - 14, fmt(hi, 1), 'axis-text', 'end'));
  svg.appendChild(mkText((padL + W - padR) / 2, H - 14, '年化夏普(隨機打亂的世界)', 'axis-text', 'middle'));

  host.appendChild(svg);

  $('nullLegend').innerHTML =
    `<span><i style="background:var(--scan-dim)"></i>隨機分布(示意)</span>` +
    `<span><i style="background:var(--incon)"></i>隨機 95 百分位</span>` +
    `<span><i style="background:${passed ? 'var(--real)' : 'var(--overfit)'}"></i>你的真實夏普 ${fmt(real, 2)}(p=${fmt(perm.p_value, 3)})</span>`;
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
function setParsed(parsed) {
  State.parsed = parsed;
  const pill = $('filePill');
  pill.classList.add('show');
  $('fileName').textContent = parsed.srcName;
  const modeTxt = parsed.mode === 'matrix'
    ? `矩陣 · ${parsed.colNames.length} 策略 × ${parsed.nRows} 期`
    : `${parsed.nRows} 期${parsed.dates ? ' · 含日期' : ''}`;
  $('fileMeta').textContent = `(${modeTxt})`;
  $('runBtn').disabled = false;
  hideError();
  log('已解析:', { mode: parsed.mode, nRows: parsed.nRows, note: parsed.note });
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
    showError(`<b>CSV 解析失敗。</b><br>${escapeHtml(String(e.message || e))}<br>
      支援格式:①單欄報酬或淨值 ②「日期,數值」兩欄 ③日期 + 多策略矩陣。`);
  }
}

async function loadSample(which) {
  hideError();
  const path = which === 'genuine' ? 'sample/sample_genuine.csv' : 'sample/sample_overfit.csv';
  try {
    const resp = await fetch(path, { cache: 'no-store' });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const text = await resp.text();
    const parsed = parseCSV(text, which === 'genuine' ? 'sample_genuine.csv(示範)' : 'sample_overfit.csv(示範)');
    setParsed(parsed);
    // 示範預設參數:過擬合樣本用高 n_trials 展示 DSR 通縮
    if (which === 'overfit') { $('nTrials').value = 150; }
    else { $('nTrials').value = 1; }
    log('已載入示範:', which);
  } catch (e) {
    warn('載入示範失敗:', e);
    showError('<b>載入示範失敗。</b><br>' + escapeHtml(String(e.message || e)));
  }
}

function bindUI() {
  const drop = $('drop'), input = $('fileInput');
  input.addEventListener('change', (e) => { if (e.target.files[0]) handleFile(e.target.files[0]); });

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
    const desc = k && State.baselines && State.baselines[k]
      ? (State.baselines[k].desc || State.baselines[k].name_zh)
      : '選一條看你有沒有贏過無腦買進持有。';
    $('benchDesc').textContent = desc;
  });

  // 讓引擎在使用者上傳/互動時先預熱(不阻塞 UI),縮短點「照妖」後的等待
  const warmOnce = () => { document.removeEventListener('pointerdown', warmOnce);
    if (!State.engineReady && !State.loading) ensureEngine().catch(() => {}); };
  document.addEventListener('pointerdown', warmOnce, { once: true });
}

// ============================================================================
// 啟動
// ============================================================================
document.addEventListener('DOMContentLoaded', () => {
  log('啟動 · Pyodide', PYODIDE_VERSION);
  bindUI();
  loadBaselines();
  setEngineStatus('引擎待命', '');
});
