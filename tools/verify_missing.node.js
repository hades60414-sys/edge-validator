/* 驗證 R17 必修1「缺值/非數字格靜默填 0」修復(前端 parseCSV 路徑的獨立 node 真值對照,
   pytest 打不到 JS,故比照 verify_align.node.js:從【實際出貨的】app.js 抽函數 eval)。
   用法:node tools/verify_missing.node.js(任何 cwd 皆可;以本檔位置定位 repo 根)

   騙局復刻:真實年虧 ~28% 的策略,把最差 100 天的報酬格留空上傳。
   - 修前:空格被靜默填 0 → 波動被壓低、虧損被抹掉 → 可判到 80+ 分「可能是真的 edge」。
   - 修後:缺格率 40% ≥ 5% → 【拒絕評審】(perr_missing_reject),絕不填 0。
   另驗:小缺值(<5%)→ 整列剔除+警語揭露、統計量與手算一致;矩陣模式整列剔除保持
   橫斷面對齊;含缺格列合計 ≥5% 拒審;全空欄(行尾逗號)剔除欄不拒審。 */
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
const parseCSV = eval('(function(){ const t = (k, ...a) => k + "|" + a.join(","); '
  + srcs.join('\n') + '\n return parseCSV; })()');

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
// 情境 1:騙局復刻 —— 年虧 ~28% 策略,最差 100/250 天留空 → 必須拒審
// ============================================================================
console.log('=== 情境 1:騙局復刻(最差 100 天留空,40% 缺格)===');
{
  const N = 250;
  const rets = [];
  for (let i = 0; i < N; i++) rets.push(-0.0013 + 0.015 * gauss()); // 年化 ~-28%
  const order = rets.map((v, i) => [v, i]).sort((a, b) => a[0] - b[0]);
  const blank = new Set(order.slice(0, 100).map(p => p[1])); // 最差 100 天
  const lines = ['date,ret'];
  for (let i = 0; i < N; i++) lines.push(`${isoDate(i)},${blank.has(i) ? '' : rets[i]}`);
  let threw = null;
  try { parseCSV(lines.join('\n'), 'scam.csv'); } catch (e) { threw = e; }
  assert(threw !== null, '缺最差 100 天 → parseCSV 拒絕評審(丟錯,不靜默)');
  assert(threw && String(threw.message).startsWith('perr_missing_reject'),
    `拒審走 perr_missing_reject 錯誤卡(實際:${threw && threw.message.slice(0, 40)}…)`);
  assert(threw && threw.message.includes('100') && threw.message.includes('250'),
    '錯誤帶欄缺格數量(100/250)');
}

// ============================================================================
// 情境 2:小缺值(4/250 = 1.6% < 5%)→ 整列剔除+警語,統計量與手算一致,絕不補 0
// ============================================================================
console.log('\n=== 情境 2:小缺值(1.6%)→ 整列剔除+揭露 ===');
{
  const N = 250;
  const rets = [];
  for (let i = 0; i < N; i++) {
    let v = 0.0007 + 0.01 * gauss();
    if (v === 0) v = 1e-6; // 保證非 0,才能驗「絕未填 0」
    rets.push(v);
  }
  const blank = new Set([10, 77, 150, 240]);
  const lines = ['date,ret'];
  for (let i = 0; i < N; i++) lines.push(`${isoDate(i)},${blank.has(i) ? '' : rets[i]}`);
  const p = parseCSV(lines.join('\n'), 'small.csv');
  assert(p.returns.length === 246 && p.dates.length === 246,
    '4 缺格 → 該 4 期整列剔除(returns/dates 同步 246 期)');
  assert(Array.isArray(p.parseWarns) && p.parseWarns.some(w => w.key === 'pw_missing_dropped'
    && w.args[1] === 4 && w.args[2] === 250),
    '警語揭露:pw_missing_dropped(欄名, 4, 250)');
  assert(p.returns.every(v => v !== 0), '絕未以 0 填補(所有值皆非 0)');
  // 手算真值:剔除後的均值 = 原值扣掉留空 4 期的均值
  const kept = rets.filter((_, i) => !blank.has(i));
  const meanKept = kept.reduce((a, b) => a + b, 0) / kept.length;
  const meanParsed = p.returns.reduce((a, b) => a + b, 0) / p.returns.length;
  assert(Math.abs(meanKept - meanParsed) < 1e-12, `剔除後均值與手算一致(${meanParsed.toFixed(8)})`);
  assert(p.dates[0] === '2024-01-01' && !p.dates.includes(isoDate(10)),
    '日期同步剔除(缺格日不在 dates 內)');
}

// ============================================================================
// 情境 3:矩陣 —— 單欄 ≥5% 缺格 → 拒審(帶欄名)
// ============================================================================
console.log('\n=== 情境 3:矩陣單欄 10% 缺格 → 拒審 ===');
{
  const N = 200;
  const lines = ['date,A,B,C'];
  for (let i = 0; i < N; i++) {
    const b = (i % 10 === 3) ? '' : (0.0003 + 0.009 * gauss()); // B 欄 10% 缺
    lines.push(`${isoDate(i)},${0.0005 + 0.01 * gauss()},${b},${0.0002 + 0.011 * gauss()}`);
  }
  let threw = null;
  try { parseCSV(lines.join('\n'), 'mat_bad.csv'); } catch (e) { threw = e; }
  assert(threw && String(threw.message).startsWith('perr_missing_reject') && threw.message.includes('B'),
    '任一欄缺格率 ≥5% → 拒審且錯誤帶欄名 B');
}

// ============================================================================
// 情境 4:矩陣小缺值 → 整列剔除保持橫斷面對齊
// ============================================================================
console.log('\n=== 情境 4:矩陣小缺值 → 整列剔除、橫斷面對齊 ===');
{
  const N = 200;
  const A = [], B = [], C = [];
  for (let i = 0; i < N; i++) {
    A.push(((i % 7) - 3) * 0.001 + 0.0001); // 確定性可驗映射(有正有負 → 判 returns 不轉換)
    B.push(0.0003 + 0.009 * gauss());
    C.push(0.0002 + 0.011 * gauss());
  }
  const blankB = new Set([5, 60, 130]);   // B 欄 3 缺(1.5%)
  const blankC = new Set([20, 190]);      // C 欄 2 缺(1%),不重疊 → 共 5 列
  const lines = ['date,A,B,C'];
  for (let i = 0; i < N; i++) {
    lines.push(`${isoDate(i)},${A[i]},${blankB.has(i) ? '' : B[i]},${blankC.has(i) ? '' : C[i]}`);
  }
  const p = parseCSV(lines.join('\n'), 'mat_small.csv');
  assert(p.mode === 'matrix' && p.nRows === 195, '5 列含缺格 → 整列剔除,餘 195 列');
  const cols = Object.values(p.matrix);
  assert(cols.every(c => c.length === 195), '各欄長度一致(橫斷面對齊)');
  // 對齊真值:剔除後第 k 列的 A 值必須等於原始「未缺格列」序列的第 k 個 A 值
  const keptIdx = [];
  for (let i = 0; i < N; i++) if (!blankB.has(i) && !blankC.has(i)) keptIdx.push(i);
  assert(p.matrix.A.every((v, k) => Math.abs(v - A[keptIdx[k]]) < 1e-15),
    'A 欄逐列對映原始未缺格列(整列剔除,非各欄各剔各的)');
  assert(p.dates.length === 195 && p.dates[0] === isoDate(0) && !p.dates.includes(isoDate(60)),
    '日期同步整列剔除');
  assert(p.parseWarns.filter(w => w.key === 'pw_missing_dropped').length === 2
    && p.parseWarns.some(w => w.key === 'pw_missing_rows' && w.args[0] === 5),
    '警語:B/C 兩欄各一則 + 整列剔除總數 5 揭露');
}

// ============================================================================
// 情境 5:矩陣各欄 <5% 但含缺格列合計 ≥5% → 拒審(複利吃樣本不放行)
// ============================================================================
console.log('\n=== 情境 5:缺格列合計 6% → 拒審 ===');
{
  const N = 200;
  const lines = ['date,A,B,C'];
  const bA = new Set([1, 30, 61, 90]), bB = new Set([5, 40, 70, 100]), bC = new Set([10, 50, 80, 110]);
  for (let i = 0; i < N; i++) {
    lines.push(`${isoDate(i)},${bA.has(i) ? '' : 0.001},${bB.has(i) ? '' : 0.002},${bC.has(i) ? '' : 0.003}`);
  }
  let threw = null;
  try { parseCSV(lines.join('\n'), 'mat_union.csv'); } catch (e) { threw = e; }
  assert(threw && String(threw.message).startsWith('perr_missing_rows_reject'),
    '各欄 2% 但缺格列合計 12/200=6% → perr_missing_rows_reject 拒審');
}

// ============================================================================
// 情境 6:行尾多餘逗號(無標題全空欄)→ 剔除欄並告警,不整檔拒審
// ============================================================================
console.log('\n=== 情境 6:行尾逗號全空欄 → 剔除欄不拒審 ===');
{
  const N = 60;
  const lines = ['date,ret,'];
  for (let i = 0; i < N; i++) lines.push(`${isoDate(i)},${0.0005 + 0.01 * gauss()},`);
  const p = parseCSV(lines.join('\n'), 'trailing.csv');
  assert(p.mode === 'returns' && p.returns.length === N,
    '全空欄剔除後退化為單策略 returns,期數不變');
  assert(p.parseWarns.some(w => w.key === 'pw_empty_col_dropped'),
    '警語揭露:pw_empty_col_dropped');
}

console.log(fail ? `\n✗ ${fail} 條斷言失敗` : '\n✓ 全部斷言通過');
process.exit(fail ? 1 : 0);
