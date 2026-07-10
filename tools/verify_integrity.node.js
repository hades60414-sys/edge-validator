/* 驗證 R19 必修1/5a「日期完整性 + RFC4180 引號感知切割」的前端 parseCSV 路徑
   (獨立 node 真值對照,pytest 打不到 JS,比照 verify_missing.node.js:從【實際出貨的】
   app.js 抽函數 eval)。用法:node tools/verify_integrity.node.js(任何 cwd 皆可)

   騙局復刻:真實年虧策略(250 期含日期),最好 60 天整列複製 4 份、按日期排序上傳
   (490 列,240 列重複日期)→ 修前:重複的好日子灌水,可判 86 分 likely-real;
   修後:重複率 ≥5% → 【拒絕評審】(perr_dup_dates_reject)。
   另驗:溫和版(複製 50 天 1 份)拒審;<5% 重複保留首見+揭露;亂序穩定排序+揭露且與
   事先排好序的同資料逐位一致;日內唯一時戳不誤傷;帶引號千分位 "1,234.56" 正確解析;
   欄名重複改名保留不靜默覆蓋。
   情境 8(R19b 政策收斂):垃圾日期(N/A 等)不進重複判定——值全有效+幾列 "N/A" 日期
   不再整檔誤殺拒審(接受+揭露 pw_dates_partial_unparseable);騙局+1 格垃圾日期照樣
   拒審(毒日期不解除守衛);垃圾在場略過排序步+揭露。 */
'use strict';
const fs = require('fs');
const path = require('path');

const appSrc = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

// ---- 從出貨 app.js 抽出 parseCSV 及其依賴(t 以 stub 代替:回 key|args,斷言認 key)----
function pick(re, name) {
  const m = appSrc.match(re);
  if (!m) { console.error('抽不到', name); process.exit(1); }
  return m[0];
}
const srcs = [
  pick(/const MISSING_MAX_RATE = [\d.]+;/, 'MISSING_MAX_RATE'),
  pick(/function isNum\(x\).*$/m, 'isNum'),
  pick(/function escapeHtml\([\s\S]*?\n}/, 'escapeHtml'),
  pick(/function parseNumberCell\([\s\S]*?\n}/, 'parseNumberCell'),
  pick(/function detectDelim\([\s\S]*?\n}/, 'detectDelim'),
  pick(/function splitCSVLine\([\s\S]*?\n}/, 'splitCSVLine'),
  pick(/function dateSortKey\([\s\S]*?\n}/, 'dateSortKey'),
  pick(/function applyDateIntegrity\([\s\S]*?\n}/, 'applyDateIntegrity'),
  pick(/function looksLikeDate\([\s\S]*?\n}/, 'looksLikeDate'),
  pick(/function normalizeDate\([\s\S]*?\n}/, 'normalizeDate'),
  pick(/function navToReturns\([\s\S]*?\n}/, 'navToReturns'),
  pick(/function detectSeriesKind\([\s\S]*?\n}/, 'detectSeriesKind'),
  pick(/function guardMissingColumn\([\s\S]*?\n}/, 'guardMissingColumn'),
  pick(/function parseCSV\([\s\S]*?\n}/, 'parseCSV'),
];
// eslint-disable-next-line no-eval
const bundle = eval('(function(){ const t = (k, ...a) => k + "|" + a.join(","); '
  + srcs.join('\n')
  + '\n return { parseCSV, splitCSVLine, navToReturns }; })()');
const { parseCSV, splitCSVLine, navToReturns } = bundle;

// ---- 斷言器 ----
let fail = 0;
const assert = (cond, msg) => { if (!cond) { console.error('  ✗ FAIL:', msg); fail++; } else console.log('  ✓', msg); };

// ---- 確定性偽隨機(mulberry32)----
function mulberry32(seed) {
  return function () {
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rnd = mulberry32(20260710);
const gauss = () => { const u = Math.max(rnd(), 1e-12), v = rnd(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); };
const isoDate = (i) => new Date(Date.UTC(2024, 0, 1) + i * 86400000).toISOString().slice(0, 10);

// ============================================================================
// 情境 1:騙局復刻 —— 最好 60 天整列複製 4 份、按日期排序(240/490 重複)→ 拒審
// ============================================================================
console.log('=== 情境 1:日期重複騙局(複製最好 60 天 ×4)→ 拒審 ===');
{
  const N = 250;
  const rets = [];
  for (let i = 0; i < N; i++) rets.push(-0.0013 + 0.015 * gauss()); // 真實在虧
  const best = rets.map((v, i) => [v, i]).sort((a, b) => b[0] - a[0]).slice(0, 60).map(p => p[1]);
  const rows = [];
  for (let i = 0; i < N; i++) rows.push([isoDate(i), rets[i]]);
  for (let k = 0; k < 4; k++) for (const i of best) rows.push([isoDate(i), rets[i]]);
  rows.sort((a, b) => (a[0] < b[0] ? -1 : 1));
  const lines = ['date,ret'].concat(rows.map(r => r.join(',')));
  let threw = null;
  try { parseCSV(lines.join('\n'), 'scam_dup.csv'); } catch (e) { threw = e; }
  assert(threw !== null, '240/490 列重複日期 → parseCSV 拒絕評審(丟錯,不靜默去重)');
  assert(threw && String(threw.message).startsWith('perr_dup_dates_reject'),
    `拒審走 perr_dup_dates_reject(實際:${threw && threw.message.slice(0, 40)}…)`);
  assert(threw && threw.message.includes('240') && threw.message.includes('490'),
    '錯誤帶重複列數量(240/490)');
}

// ============================================================================
// 情境 2:溫和版 —— 複製最好 50 天 1 份(50/300 ≈ 17% ≥5%)→ 一樣拒審
// ============================================================================
console.log('\n=== 情境 2:溫和版(複製 50 天 1 份)→ 拒審 ===');
{
  const N = 250;
  const rets = [];
  for (let i = 0; i < N; i++) rets.push(-0.0013 + 0.015 * gauss());
  const best = rets.map((v, i) => [v, i]).sort((a, b) => b[0] - a[0]).slice(0, 50).map(p => p[1]);
  const rows = [];
  for (let i = 0; i < N; i++) rows.push([isoDate(i), rets[i]]);
  for (const i of best) rows.push([isoDate(i), rets[i]]);
  rows.sort((a, b) => (a[0] < b[0] ? -1 : 1));
  let threw = null;
  try { parseCSV(['date,ret'].concat(rows.map(r => r.join(','))).join('\n'), 'mild.csv'); }
  catch (e) { threw = e; }
  assert(threw && String(threw.message).startsWith('perr_dup_dates_reject'),
    '50/300 重複(≥5%)→ perr_dup_dates_reject 拒審');
}

// ============================================================================
// 情境 3:<5% 重複(2 列)→ 保留首見、剔除重複列+揭露
// ============================================================================
console.log('\n=== 情境 3:2 列重複(<5%)→ 保留首見+揭露 ===');
{
  const N = 250;
  const rets = [];
  for (let i = 0; i < N; i++) rets.push(0.0007 + 0.01 * gauss());
  const lines = ['date,ret'];
  for (let i = 0; i < N; i++) {
    lines.push(`${isoDate(i)},${rets[i]}`);
    if (i === 40 || i === 120) lines.push(`${isoDate(i)},9.9`); // 同日期、junk 值(首見須勝出)
  }
  const p = parseCSV(lines.join('\n'), 'small_dup.csv');
  assert(p.returns.length === 250 && p.dates.length === 250,
    '2 列重複 → 剔除後餘 250 期(dates/returns 同步)');
  assert(p.parseWarns.some(w => w.key === 'pw_dup_dates_dropped' && w.args[0] === 2),
    '警語揭露:pw_dup_dates_dropped(2 列)');
  assert(!p.returns.includes(9.9), '保留首見:junk 9.9(後至的重複列)全數被剔除');
  assert(p.returns.every((v, i) => Math.abs(v - rets[i]) < 1e-15),
    '剔除後逐位等於原始無重複序列(真值對照)');
}

// ============================================================================
// 情境 4:亂序唯一日期 → 穩定排序+揭露,且與事先排好序的同資料逐位一致
// ============================================================================
console.log('\n=== 情境 4:亂序 → 穩定排序恢復時序 ===');
{
  const N = 200;
  const rets = [];
  for (let i = 0; i < N; i++) rets.push(0.0005 + 0.01 * gauss());
  // 確定性洗牌
  const order = [];
  for (let i = 0; i < N; i++) order.push(i);
  for (let i = N - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [order[i], order[j]] = [order[j], order[i]]; }
  const linesShuffled = ['date,ret'].concat(order.map(i => `${isoDate(i)},${rets[i]}`));
  const linesSorted = ['date,ret'];
  for (let i = 0; i < N; i++) linesSorted.push(`${isoDate(i)},${rets[i]}`);
  const ps = parseCSV(linesShuffled.join('\n'), 'shuffled.csv');
  const pr = parseCSV(linesSorted.join('\n'), 'sorted.csv');
  assert(ps.parseWarns.some(w => w.key === 'pw_dates_sorted' && w.args[0] > 0),
    '警語揭露:pw_dates_sorted(亂序筆數 > 0)');
  assert(!pr.parseWarns.some(w => w.key === 'pw_dates_sorted'), '已排序資料不誤報');
  assert(ps.returns.length === pr.returns.length
    && ps.returns.every((v, i) => v === pr.returns[i])
    && ps.dates.every((d, i) => d === pr.dates[i]),
    '排序後與事先排好序的同資料【逐位一致】(returns + dates)');
}

// ============================================================================
// 情境 5:日內資料 —— 唯一完整時戳不誤傷;date-only 撞同一天 → 拒審
// ============================================================================
console.log('\n=== 情境 5:日內時戳相容性 ===');
{
  const lines = ['datetime,ret'];
  for (let d = 0; d < 5; d++) {
    for (let m = 0; m < 30; m++) {
      const hh = String(9 + Math.floor(m / 60)).padStart(2, '0');
      const mm = String(m % 60).padStart(2, '0');
      lines.push(`${isoDate(d)} ${hh}:${mm},${0.0001 + 0.001 * gauss()}`);
    }
  }
  const p = parseCSV(lines.join('\n'), 'intraday.csv');
  assert(p.returns.length === 150
    && !p.parseWarns.some(w => w.key === 'pw_dup_dates_dropped' || w.key === 'pw_dates_sorted'),
    '唯一完整時戳(同日多 bar)→ 不觸發重複/亂序處理');
  // date-only 日內:同一天 30 根 bar 全撞
  const lines2 = ['date,ret'];
  for (let d = 0; d < 5; d++) for (let m = 0; m < 30; m++) lines2.push(`${isoDate(d)},${0.0001 + 0.001 * gauss()}`);
  let threw = null;
  try { parseCSV(lines2.join('\n'), 'dateonly.csv'); } catch (e) { threw = e; }
  assert(threw && String(threw.message).startsWith('perr_dup_dates_reject'),
    'date-only 日內(同日撞 30 根)→ 拒審(訊息提示補完整時戳)');
}

// ============================================================================
// 情境 6:RFC4180 引號感知 —— "1,234.56" 千分位欄位(Excel/券商標準輸出)
// ============================================================================
console.log('\n=== 情境 6:帶引號千分位 → 正確解析(修前 100% 拒審) ===');
{
  assert(JSON.stringify(splitCSVLine('a,"1,234.56",b', ',')) === JSON.stringify(['a', '1,234.56', 'b']),
    'splitCSVLine:引號內逗號不切割');
  assert(JSON.stringify(splitCSVLine('"he said ""hi""",2', ',')) === JSON.stringify(['he said "hi"', '2']),
    'splitCSVLine:"" 跳脫為一個引號');
  const lines = ['date,nav'];
  let nav = 10000;
  for (let i = 0; i < 100; i++) {
    nav *= 1 + 0.0005 + 0.008 * gauss();
    const navStr = nav.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    lines.push(`${isoDate(i)},"${navStr}"`);
  }
  let p = null, threw = null;
  try { p = parseCSV(lines.join('\n'), 'quoted.csv'); } catch (e) { threw = e; }
  assert(threw === null, '帶引號千分位淨值檔 → 不再拒審');
  assert(p && p.mode === 'returns' && p.returns.length === 100,
    '解析為 100 期單序列');
  assert(p && p.jsKind === 'nav', '千分位淨值被正確偵測為 nav(數值完整,非撕欄殘渣)');
}

// ============================================================================
// 情境 7:欄名重複 → 改名保留+揭露(修前後欄靜默覆蓋前欄)
// ============================================================================
console.log('\n=== 情境 7:欄名重複 → 改名保留不覆蓋 ===');
{
  const N = 120;
  const lines = ['date,strat,strat,strat'];
  for (let i = 0; i < N; i++) {
    lines.push(`${isoDate(i)},${0.001 + 0.01 * gauss()},${0.0005 + 0.01 * gauss()},${-0.0002 + 0.01 * gauss()}`);
  }
  const p = parseCSV(lines.join('\n'), 'dupcol.csv');
  assert(p.mode === 'matrix' && p.colNames.length === 3,
    '三個同名欄全數保留(3 策略,無靜默覆蓋)');
  assert(new Set(p.colNames).size === 3, '欄名已去重(改名)');
  assert(p.parseWarns.filter(w => w.key === 'pw_dup_colname').length === 2,
    '警語揭露:pw_dup_colname ×2');
  const cols = Object.values(p.matrix);
  assert(cols.length === 3 && !cols[0].every((v, i) => v === cols[1][i]),
    '三欄內容各自獨立(未被互相覆蓋)');
}

// ============================================================================
// 情境 8:垃圾日期政策收斂(R19b)—— N/A 不誤殺、毒日期不解除守衛、垃圾在場不排序
// ============================================================================
console.log('\n=== 情境 8:垃圾日期政策(R19b 與引擎收斂) ===');
{
  // 8a:250 列值全有效、14 列日期 "N/A"(同字串 5.6%)→ 接受+揭露(修前被當重複時戳整檔拒審)
  const N = 250;
  const lines = ['date,ret'];
  for (let i = 0; i < N; i++) {
    const d = (i >= 100 && i < 114) ? 'N/A' : isoDate(i);
    lines.push(`${d},${(0.0005 + 0.01 * gauss()).toFixed(6)}`);
  }
  let threw = null, p = null;
  try { p = parseCSV(lines.join('\n'), 'na_dates.csv'); } catch (e) { threw = e; }
  assert(threw === null, '14 列 "N/A" 日期(值全有效)→ 不再整檔誤殺拒審');
  assert(p && p.returns.length === 250, '250 列全數保留(垃圾列不被當「重複」剔除)');
  assert(p && p.parseWarns.some(w => w.key === 'pw_dates_partial_unparseable' && w.args[0] === 14),
    '警語揭露:pw_dates_partial_unparseable(14 列)');
  assert(p && !p.parseWarns.some(w => w.key === 'pw_dup_dates_dropped' || w.key === 'pw_dates_sorted'),
    'identical 垃圾字串不構成重複證據;垃圾在場不做排序步');

  // 8b:騙局(240/490 重複好日子)+ 1 格垃圾日期 → 毒日期不解除守衛,照樣拒審
  const rets = [];
  for (let i = 0; i < N; i++) rets.push(-0.0013 + 0.015 * gauss());
  const best = rets.map((v, i) => [v, i]).sort((a, b) => b[0] - a[0]).slice(0, 60).map(q => q[1]);
  const rows = [];
  for (let i = 0; i < N; i++) rows.push([isoDate(i), rets[i]]);
  for (let k = 0; k < 4; k++) for (const i of best) rows.push([isoDate(i), rets[i]]);
  rows.sort((a, b) => (a[0] < b[0] ? -1 : 1));
  rows[0][0] = 'n/a-garbage';
  let threw2 = null;
  try { parseCSV(['date,ret'].concat(rows.map(r2 => r2.join(','))).join('\n'), 'poison.csv'); }
  catch (e) { threw2 = e; }
  assert(threw2 !== null && String(threw2.message).startsWith('perr_dup_dates_reject'),
    `騙局 + 1 格垃圾日期 → 照樣 perr_dup_dates_reject 拒審(實際:${threw2 ? String(threw2.message).slice(0, 40) : '未丟錯'}…)`);

  // 8c:亂序 + 1 格垃圾 → 略過排序步+揭露(排序只在全可解析時發生)
  const M = 200;
  const rets3 = [];
  for (let i = 0; i < M; i++) rets3.push(0.0005 + 0.01 * gauss());
  const order = [];
  for (let i = 0; i < M; i++) order.push(i);
  for (let i = M - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [order[i], order[j]] = [order[j], order[i]]; }
  const lines3 = ['date,ret'].concat(order.map((i, k) => `${k === 3 ? 'N/A' : isoDate(i)},${rets3[i]}`));
  const p3 = parseCSV(lines3.join('\n'), 'shuffled_garbage.csv');
  assert(p3.returns.length === 200 && !p3.parseWarns.some(w => w.key === 'pw_dates_sorted'),
    '亂序+垃圾在場 → 不做排序(無法建立全序,與引擎同政策)');
  assert(p3.parseWarns.some(w => w.key === 'pw_dates_partial_unparseable' && w.args[0] === 1),
    '警語揭露:pw_dates_partial_unparseable(1 列)');
}

console.log(fail ? `\n✗ ${fail} 條斷言失敗` : '\n✓ 全部斷言通過');
process.exit(fail ? 1 : 0);
