/* 驗證缺值 fail-closed 的前端供料層(R17 必修1 建立;★R21b 改版:前端不再剔列★)。
   (前端 parseCSV 路徑的獨立 node 真值對照,pytest 打不到 JS,故比照 verify_align.node.js:
   從【實際出貨的】app.js 抽函數 eval)。
   用法:node tools/verify_missing.node.js(任何 cwd 皆可;以本檔位置定位 repo 根)

   R21b 政策(修驗收官 A 的 MED:舊版前端 <5% 先剔列 → 引擎在瀏覽器路徑永遠看不到
   NaN → R21 缺值敏感度/降級在公開站全是死碼):
   - ≥5% 缺格:前端快速拒審照舊(fail-fast UX,perr_missing_reject /
     perr_missing_rows_reject,與引擎同判準);
   - <5% 缺格:【保留缺值位】(NaN;JSON.stringify → null)連同日期原樣送引擎,
     由引擎守衛(pytest 釘死)整列剔除+缺值敏感度試算+fail-closed 降級;
     前端只以 pw_missing_detected 揭露,絕不填 0、絕不自行剔列。 */
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
const isNaNv = (v) => typeof v === 'number' && Number.isNaN(v);
const isNumV = (v) => typeof v === 'number' && isFinite(v);

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
// 情境 1:騙局復刻 —— 年虧 ~28% 策略,最差 100/250 天留空(40% ≥5%)→ 前端快速拒審照舊
// ============================================================================
console.log('=== 情境 1:騙局復刻(最差 100 天留空,40% 缺格)→ 快速拒審照舊 ===');
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
// 情境 2:小缺值(4/250 = 1.6% < 5%)→ 前端【不剔列】:缺值位保留 NaN、日期全保留,
//         警語改 pw_missing_detected,絕不補 0(剔除/敏感度交引擎)
// ============================================================================
console.log('\n=== 情境 2:小缺值(1.6%)→ 缺值位保留、不前端剔列 ===');
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
  assert(p.returns.length === 250 && p.dates.length === 250,
    '前端不剔列:returns/dates 皆為全長 250 期');
  assert([...blank].every(i => isNaNv(p.returns[i]) && isNaNv(p.rawValues[i])),
    '缺值位保留 NaN(returns 與 rawValues 皆是,位置一一對應)');
  assert(p.returns.filter(v => !isNumV(v)).length === 4
    && p.rawValues.filter(v => !isNumV(v)).length === 4,
    '非有限值恰 4 個(不多殺、不少留)');
  assert(Array.isArray(p.parseWarns) && p.parseWarns.some(w => w.key === 'pw_missing_detected'
    && w.args[1] === 4 && w.args[2] === 250),
    '警語揭露:pw_missing_detected(欄名, 4, 250)——不再宣稱「已剔除」');
  assert(!p.parseWarns.some(w => /^pw_missing_(dropped|rows)$/.test(w.key)),
    '舊「已剔除」警語(pw_missing_dropped/pw_missing_rows)不再出現(避免與引擎警語矛盾)');
  assert(p.returns.every(v => !isNumV(v) || v !== 0), '絕未以 0 填補(所有有限值皆非 0)');
  // 真值:有限值逐位等於原始值(無位移、無改寫)
  assert(rets.every((v, i) => blank.has(i) ? isNaNv(p.returns[i]) : Math.abs(p.returns[i] - v) < 1e-15),
    '有限值逐位等於原始值(缺值不造成位移)');
  assert(p.dates[10] === isoDate(10) && p.dates.includes(isoDate(10)),
    '缺格日的日期保留在場(連同 null 值一起交引擎)');
}

// ============================================================================
// 情境 3:矩陣 —— 單欄 ≥5% 缺格 → 快速拒審照舊(帶欄名)
// ============================================================================
console.log('\n=== 情境 3:矩陣單欄 10% 缺格 → 拒審照舊 ===');
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
// 情境 4:矩陣小缺值 → 前端【不剔列】:各欄全長、缺值位保留 NaN、橫斷面天然對齊
// ============================================================================
console.log('\n=== 情境 4:矩陣小缺值 → 不剔列、缺值位保留、對齊交引擎 ===');
{
  const N = 200;
  const A = [], B = [], C = [];
  for (let i = 0; i < N; i++) {
    A.push(((i % 7) - 3) * 0.001 + 0.0001); // 確定性可驗映射(有正有負 → 判 returns 不轉換)
    B.push(0.0003 + 0.009 * gauss());
    C.push(0.0002 + 0.011 * gauss());
  }
  const blankB = new Set([5, 60, 130]);   // B 欄 3 缺(1.5%)
  const blankC = new Set([20, 190]);      // C 欄 2 缺(1%),不重疊 → 共 5 列含缺格
  const lines = ['date,A,B,C'];
  for (let i = 0; i < N; i++) {
    lines.push(`${isoDate(i)},${A[i]},${blankB.has(i) ? '' : B[i]},${blankC.has(i) ? '' : C[i]}`);
  }
  const p = parseCSV(lines.join('\n'), 'mat_small.csv');
  assert(p.mode === 'matrix' && p.nRows === 200, '前端不剔列:nRows 維持全長 200');
  const cols = Object.values(p.matrix);
  assert(cols.every(c => c.length === 200), '各欄長度一致且為全長(對齊剔除交引擎)');
  assert(p.matrix.A.every((v, k) => Math.abs(v - A[k]) < 1e-15),
    'A 欄逐位等於原始值(無位移、無改寫)');
  assert([...blankB].every(i => isNaNv(p.matrix.B[i]) && isNaNv(p.rawMatrix.B[i]))
    && [...blankC].every(i => isNaNv(p.matrix.C[i]) && isNaNv(p.rawMatrix.C[i])),
    'B/C 欄缺值位保留 NaN(matrix 與 rawMatrix 皆是)');
  assert(p.matrix.B.filter(v => !isNumV(v)).length === 3
    && p.matrix.C.filter(v => !isNumV(v)).length === 2,
    '缺值數恰 B=3、C=2(不多殺、不少留)');
  assert(p.dates.length === 200 && p.dates[60] === isoDate(60) && p.dates[20] === isoDate(20),
    '日期全保留(缺格日仍在場,連同 null 交引擎)');
  assert(p.parseWarns.filter(w => w.key === 'pw_missing_detected').length === 2
    && !p.parseWarns.some(w => /^pw_missing_(dropped|rows)$/.test(w.key)),
    '警語:B/C 兩欄各一則 pw_missing_detected;無任何「已剔除」宣稱');
}

// ============================================================================
// 情境 5:矩陣各欄 <5% 但含缺格列合計 ≥5% → 快速拒審照舊(複利吃樣本不放行)
// ============================================================================
console.log('\n=== 情境 5:缺格列合計 6% → 拒審照舊 ===');
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

// ============================================================================
// 情境 7(★R21b 核心真值★):R20 騙局 CSV(挖最差 19/400 = 4.75% < 5%)
//   parseCSV 放行 → 組 payload 經 JSON.stringify → 【必須含 null】直通引擎。
//   舊版前端剔列後 payload 無 null = 引擎敏感度機制在公開站是死碼(驗收官 A 的 MED)。
// ============================================================================
console.log('\n=== 情境 7:R20 騙局(挖最差 19/400)→ payload 含 null 直通引擎 ===');
{
  const N = 400;
  const rets = [];
  for (let i = 0; i < N; i++) rets.push(-0.0004 + 0.012 * gauss()); // 微虧策略
  const order = rets.map((v, i) => [v, i]).sort((a, b) => a[0] - b[0]);
  const blank = new Set(order.slice(0, 19).map(x => x[1])); // 挖最差 19 天(4.75%)
  const lines = ['date,ret'];
  for (let i = 0; i < N; i++) lines.push(`${isoDate(i)},${blank.has(i) ? '' : rets[i]}`);
  let p = null, threw = null;
  try { p = parseCSV(lines.join('\n'), 'scam19.csv'); } catch (e) { threw = e; }
  assert(threw === null && p, '4.75% < 5% → parseCSV 放行(拒審線不誤傷)');
  assert(p.returns.length === 400 && p.dates.length === 400 && p.rawValues.length === 400,
    '全長 400 期送引擎(前端零剔列)');
  // 復刻 runAnalysis 的 payload 組裝(returns + raw.values + dates)→ JSON 真值
  const payload = JSON.parse(JSON.stringify({
    mode: 'returns', dates: p.dates, returns: p.returns,
    raw: { values: p.rawValues, dates: p.datesRaw, js_kind: p.jsKind },
  }));
  const nNullRet = payload.returns.filter(v => v === null).length;
  const nNullRaw = payload.raw.values.filter(v => v === null).length;
  assert(nNullRet === 19 && nNullRaw === 19,
    `payload 經 JSON 序列化後 returns/raw.values 各含 19 個 null(實際 ${nNullRet}/${nNullRaw})`);
  assert([...blank].every(i => payload.returns[i] === null && payload.raw.values[i] === null),
    'null 位置恰為被挖空的最差 19 天(逐位對應)');
  assert(payload.returns.every((v, i) => v === null || Math.abs(v - rets[i]) < 1e-12),
    '其餘 381 期逐位等於原始值(絕未填 0、無位移)');
  assert(payload.dates.length === 400 && [...blank].every(i => payload.dates[i] === isoDate(i)),
    '日期連同缺值位原樣在場(引擎剔列時才同步剔)');
}

// ============================================================================
// 情境 8:日期完整性 × 缺值交互 —— 重複日期(<5%)與缺值(<5%)同檔:
//   判重只看日期欄,NaN 值列原樣同步搬動、不影響重複判定、不被誤殺
// ============================================================================
console.log('\n=== 情境 8:重複日期 × 缺值同檔 → 判重不受 null 值影響 ===');
{
  const N = 100;
  const vals = [];
  for (let i = 0; i < N; i++) vals.push(0.0005 + 0.008 * gauss());
  const blank = new Set([20, 50]); // 2/102 ≈ 2% 缺值
  const lines = ['date,ret'];
  for (let i = 0; i < N; i++) lines.push(`${isoDate(i)},${blank.has(i) ? '' : vals[i]}`);
  lines.push(`${isoDate(3)},${0.777}`);  // 重複日期 2 列(2/102 ≈ 2%)
  lines.push(`${isoDate(7)},${0.888}`);
  const p = parseCSV(lines.join('\n'), 'dup_and_missing.csv');
  assert(p.returns.length === 100 && p.dates.length === 100,
    '2 列重複日期整列剔除(保留首見)、缺值列不被剔 → 餘 100 列');
  assert(isNaNv(p.returns[20]) && isNaNv(p.returns[50])
    && p.dates[20] === isoDate(20) && p.dates[50] === isoDate(50),
    '缺值位(NaN)與其日期原樣同步保留(判重判的是日期欄,不受 null 值影響)');
  assert(Math.abs(p.returns[3] - vals[3]) < 1e-15 && Math.abs(p.returns[7] - vals[7]) < 1e-15,
    '重複日期保留首見值(灌水列 0.777/0.888 被剔)');
  assert(p.parseWarns.some(w => w.key === 'pw_dup_dates_dropped')
    && p.parseWarns.some(w => w.key === 'pw_missing_detected'),
    '兩類警語並存:pw_dup_dates_dropped + pw_missing_detected');
}

// ============================================================================
// 情境 9:nav 路徑 × 缺值 —— 偵測以非空子集判型不被 NaN 帶偏;navToReturns NaN 傳播
// ============================================================================
console.log('\n=== 情境 9:nav 序列含缺值 → 判型不受 NaN 影響、NaN 傳播不填 0 ===');
{
  const N = 60;
  const lines = ['date,nav'];
  for (let i = 0; i < N; i++) {
    lines.push(`${isoDate(i)},${i === 30 ? '' : (100 * Math.pow(1.002, i)).toFixed(6)}`);
  }
  const p = parseCSV(lines.join('\n'), 'nav_gap.csv');
  assert(p.jsKind === 'nav', '含 1 個 NaN 的單調正大數序列仍判 nav(非空子集判型)');
  assert(p.returns.length === 60 && p.returns[0] === 0,
    'nav→returns 全長 60、首期 0');
  assert(isNaNv(p.returns[30]) && isNaNv(p.returns[31])
    && p.returns.filter(v => !isNumV(v)).length === 2,
    '缺失淨值的自身與下一期報酬皆 NaN(傳播,恰 2 個,絕不填 0)');
  assert(isNaNv(p.rawValues[30]) && p.rawValues.filter(v => !isNumV(v)).length === 1,
    'rawValues 保留原始缺值位(引擎權威重偵測拿得到 null)');
}

console.log(fail ? `\n✗ ${fail} 條斷言失敗` : '\n✓ 全部斷言通過');
process.exit(fail ? 1 : 0);
