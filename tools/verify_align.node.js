/* 驗證 R12 HIGH「基準零填充偏置」修復(獨立 node 真值對照,不經 pytest/瀏覽器)。
   用法:node tools/verify_align.node.js(任何 cwd 皆可;以本檔位置定位 repo 根)
   - OLD:修前邏輯(重疊≥50% 補 0 對齊;否則長度對齊+頭部補 0)——內嵌重現。
   - NEW:直接從 repo 根的 app.js 抽出【實際出貨的】alignBenchmark 原始碼 eval。
   情境:同一份資料(策略 200 日、基準只覆蓋其中 120 日 = 60% 交集):
   - 修前:缺的 80 日補 0 → 基準夏普被稀釋 → beats_bench 容易為 true(+8 分放水)。
   - 修後:交集 60% < 80% → skip(誠實顯示重疊不足,不比較)。
   另附 80% 覆蓋情境:修後走交集誠實比較(不補 0),基準夏普不再被稀釋、勝負翻正。 */
'use strict';
const fs = require('fs');
const path = require('path');

const appSrc = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

// ---- 抽出實際出貨的 alignBenchmark(含其依賴常數 ALIGN_MIN_COVER)----
const mConst = appSrc.match(/const ALIGN_MIN_COVER = [\d.]+;/);
const mFn = appSrc.match(/function alignBenchmark\([\s\S]*?\n}/);
if (!mFn || !mConst) { console.error('抽不到 alignBenchmark / ALIGN_MIN_COVER'); process.exit(1); }
const State = { baselines: null };
const warn = () => {};
// eslint-disable-next-line no-eval
const alignBenchmarkNEW = eval('(function(){ ' + mConst[0] + '\n' + mFn[0] + '\n return alignBenchmark; })()');

// ---- 修前邏輯(git a52dbe1 版 app.js 1151-1182 原樣重現)----
function alignBenchmarkOLD(benchKey, userDates, userLen) {
  const b = State.baselines[benchKey];
  const bDates = b.dates || null;
  const bRet = b.returns || [];
  if (userDates && bDates && bDates.length) {
    const map = new Map();
    for (let i = 0; i < bDates.length; i++) map.set(bDates[i], bRet[i]);
    const out = new Array(userLen).fill(0);
    let hit = 0;
    for (let i = 0; i < userDates.length && i < userLen; i++) {
      const v = map.get(userDates[i]);
      if (v != null && isFinite(v)) { out[i] = v; hit++; }
    }
    if (hit >= userLen * 0.5) return { arr: out, noteKey: 'align_bydate', noteArgs: [hit, userLen] };
  }
  const tail = bRet.slice(Math.max(0, bRet.length - userLen));
  const out = new Array(userLen).fill(0);
  for (let i = 0; i < tail.length; i++) out[out.length - tail.length + i] = tail[i];
  return { arr: out, noteKey: userDates ? 'align_len_nodate_overlap' : 'align_len_nodate', noteArgs: [] };
}

// ---- 指標(與 engine compute_metrics 同式:年化夏普 = mean/std(ddof=0) * sqrt(252))----
function sharpe(r, ppy = 252) {
  const n = r.length;
  const mean = r.reduce((a, b) => a + b, 0) / n;
  const vr = r.reduce((a, b) => a + (b - mean) * (b - mean), 0) / n;
  const sd = Math.sqrt(vr);
  return sd > 0 ? (mean * ppy) / (sd * Math.sqrt(ppy)) : NaN;
}

// ---- 構造情境:確定性偽隨機(mulberry32),策略普通、基準其實更強 ----
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

const N = 200;
const userDates = [];
const d0 = new Date('2024-01-01T00:00:00Z');
for (let i = 0; i < N; i++) { const d = new Date(d0.getTime() + i * 86400000); userDates.push(d.toISOString().slice(0, 10)); }
// 策略:年化 ~夏普 0.9 的普通策略
const strat = userDates.map(() => 0.0006 + 0.0105 * gauss());
// 基準:更強(年化 ~夏普 1.6),但只覆蓋使用者 60% 的日子(如週末/假日制度不同)
function makeBench(coverEvery, mu) {
  const dates = [], rets = [];
  for (let i = 0; i < N; i++) if (i % coverEvery !== 0 || coverEvery >= 100) { dates.push(userDates[i]); rets.push(mu + 0.0100 * gauss()); }
  return { dates, returns: rets };
}

function report(tag, benchDef) {
  State.baselines = { X: benchDef };
  const sStrat = sharpe(strat);
  const oldR = alignBenchmarkOLD('X', userDates, N);
  const newR = alignBenchmarkNEW('X', userDates, N);
  const sOld = sharpe(oldR.arr);
  const sNew = newR.arr ? sharpe(newR.arr) : null;
  const cover = benchDef.dates.length / N;
  console.log(`\n=== ${tag}(基準覆蓋 ${(cover * 100).toFixed(0)}%,基準真實夏普 ${sharpe(benchDef.returns).toFixed(2)},策略夏普 ${sStrat.toFixed(2)})===`);
  console.log(`修前:noteKey=${oldR.noteKey} 對齊後基準夏普=${sOld.toFixed(2)}(補0稀釋) → beats_bench=${sStrat > sOld}`);
  if (newR.arr) {
    console.log(`修後:noteKey=${newR.noteKey} 對齊後基準夏普=${sNew.toFixed(2)}(交集無填補) → beats_bench=${sStrat > sNew}`);
  } else {
    console.log(`修後:noteKey=${newR.noteKey} arr=null → 該閘 SKIP(誠實顯示重疊不足,不放水)`);
  }
  return { sStrat, sOld, newR, sNew };
}

// 情境 1:60% 覆蓋(每 5 日缺 2 日 → 用 i%5!==0 & i%5!==1?簡化:每 5 缺 2)
function makeBench60() {
  const dates = [], rets = [];
  for (let i = 0; i < N; i++) if (i % 5 !== 0 && i % 5 !== 1) { dates.push(userDates[i]); rets.push(0.0010 + 0.0100 * gauss()); }
  return { dates, returns: rets };
}
const r1 = report('情境 1:60% 交集', makeBench60());
// 情境 2:80% 覆蓋(每 5 日缺 1),基準經確定性平移使其真實夏普 = 策略 ×1.06(略勝)——
// 補 0 稀釋(×~sqrt(0.8)≈0.894)剛好把勝負翻面 = 放水偏置的最小重現
function makeBenchFlip() {
  const idx = [];
  for (let i = 0; i < N; i++) if (i % 5 !== 0) idx.push(i);
  const sub = idx.map(i => strat[i]);
  const mean = sub.reduce((a, b) => a + b, 0) / sub.length;
  const sd = Math.sqrt(sub.reduce((a, b) => a + (b - mean) * (b - mean), 0) / sub.length);
  const targetMean = (sharpe(strat) * 1.06) / Math.sqrt(252) * sd; // 目標:年化夏普 = 策略×1.06
  const shift = targetMean - mean;
  return { dates: idx.map(i => userDates[i]), returns: sub.map(v => v + shift) };
}
const r2 = report('情境 2:80% 交集', makeBenchFlip());

// ---- 斷言 ----
let fail = 0;
const assert = (cond, msg) => { if (!cond) { console.error('  ✗ FAIL:', msg); fail++; } else console.log('  ✓', msg); };
console.log('\n=== 斷言 ===');
assert(r1.sStrat > r1.sOld, '情境1 修前:補0稀釋讓「普通策略贏過更強基準」= 放水偏置重現(beats=true)');
assert(r1.newR.arr === null && r1.newR.noteKey === 'align_skip_overlap', '情境1 修後:交集<80% → skip(align_skip_overlap)');
assert(r2.newR.arr !== null && r2.newR.noteKey === 'align_bydate_intersect', '情境2 修後:交集≥80% → 交集誠實比較(align_bydate_intersect)');
assert(r2.sNew > r2.sOld, '情境2 修後:基準夏普高於修前補0版(稀釋消失)');
assert(!(r2.sStrat > r2.sNew) && (r2.sStrat > r2.sOld), '情境2:修前 beats=true(放水)、修後 beats=false(誠實)——同一資料,結論翻正');

// ---- R17 必修4:配對索引 idx 的真值斷言 ----
// 交集模式必回 idx(交集日在使用者序列中的索引,與 benchArr 逐位對應)——引擎用它把
// 基準比較限制在【共同日配對子集】上,策略不再單邊多算基準缺席的日子。
const bd2 = makeBenchFlip(); // 確定性重建情境 2 的基準(只依 strat,不耗 rnd 狀態)
const idx2 = r2.newR.idx;
assert(Array.isArray(idx2) && idx2.length === r2.newR.arr.length,
  'R17:交集模式回傳 idx,且與 benchArr 等長(逐位配對)');
assert(idx2.every((v, k) => k === 0 || v > idx2[k - 1]),
  'R17:idx 嚴格遞增(照使用者資料順序)');
const bMap2 = new Map(bd2.dates.map((d, i) => [d, bd2.returns[i]]));
assert(idx2.every((ui, k) => {
  const v = bMap2.get(userDates[ui]);
  return v != null && Math.abs(v - r2.newR.arr[k]) < 1e-12;
}), 'R17:每個 idx 都指向兩邊皆有的同一天,且 benchArr[k] 恰為該日基準報酬(配對真值)');
// 配對子集上的策略夏普 ≠ 全序列夏普(= 修前「策略多算了基準缺席日」的偏置確實存在,
// 引擎現以配對子集判勝負)
const sPair = sharpe(idx2.map(i => strat[i]));
assert(Math.abs(sPair - r2.sStrat) > 1e-9,
  `R17:配對子集策略夏普(${sPair.toFixed(4)})≠ 全序列夏普(${r2.sStrat.toFixed(4)})——未配對日確實影響比較,必須配對`);
process.exit(fail ? 1 : 0);
