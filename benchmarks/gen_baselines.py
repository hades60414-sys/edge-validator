"""gen_baselines.py — Edge Validator 對照基準線離線產生器。

離線把三條「對照基準」的歷史淨值 + 逐期報酬烤成靜態 JSON,給前端(Pyodide)當
比較基準用——瀏覽器只讀 JSON、不重算。

三條基準:
  1. tw_0050_dca        0050 定期定額(每月固定金額)的等效淨值序列(散戶「無腦」基準)。
                        資料 = marketvault market.price_daily 的 0050 還原價(含息 total-return)。
  2. tw_factor_harvest  農場 factor_harvest_mvp 的 multi 因子 nav_net(扣成本淨值)。
  3. crypto_highrisk_beta 農場 highrisk_beta_mvp 的 BTC vol-target×200MA 回測淨值(equity)。

跳過:gbm_factor(需 lightgbm,farm venv 未裝)。

誠實聲明:
  - factor_harvest / highrisk 是【回測】淨值(實盤打折);beta 非 alpha;僅供對照,非推薦。
  - JSON 只放 報酬/淨值序列 + 日期,不放任何個股價格明細。
  - 三條基準各自市場的交易日曆不同(加密 7 日/週、台股約 5 日/週),故各留原生日期軸、
    皆為日頻 ISO 日期;前端要比較時以日期對齊即可。缺資料區間誠實截短。

跑法(用 marketvault venv,有 marketvault access;farm 那條走 subprocess 用 farm venv):
  C:/Users/user/Desktop/marketvault/.venv/Scripts/python.exe gen_baselines.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

MV = Path(r"C:/Users/user/Desktop/marketvault")
FARM_REPO = Path(r"C:/Users/user/Desktop/auto-quant-btc")
FARM_VENV = FARM_REPO / ".venv" / "Scripts" / "python.exe"
OUT = Path(__file__).resolve().parent / "baselines.json"

if str(MV) not in sys.path:
    sys.path.insert(0, str(MV))


# --------------------------------------------------------------------------
# sanity 指標(對每條序列印年化/Sharpe/MDD)
# --------------------------------------------------------------------------
def _sanity(dates: list[str], returns: list[float], nav: list[float], periods_per_year: float) -> dict:
    r = np.asarray(returns, dtype=float)
    n = np.asarray(nav, dtype=float)
    d0, d1 = pd.Timestamp(dates[0]), pd.Timestamp(dates[-1])
    yrs = (d1 - d0).days / 365.25
    cagr = float(n[-1] ** (1.0 / yrs) - 1.0) if yrs > 0 and n[-1] > 0 else float("nan")
    vol = float(r.std(ddof=1) * np.sqrt(periods_per_year)) if len(r) > 1 else float("nan")
    ann = float(r.mean() * periods_per_year)
    sharpe = ann / vol if vol and vol > 0 else float("nan")
    peak = np.maximum.accumulate(n)
    mdd = float((n / peak - 1.0).min())
    return dict(n=len(nav), start=dates[0], end=dates[-1], years=round(yrs, 2),
                cagr=round(cagr, 4), ann_vol=round(vol, 4), sharpe=round(sharpe, 3),
                max_dd=round(mdd, 4))


# --------------------------------------------------------------------------
# 1) 0050 定期定額(DCA)
# --------------------------------------------------------------------------
def build_0050_dca(monthly_amount: float = 10000.0) -> dict:
    """0050 每月固定金額買進的等效淨值。

    DCA 淨值定義(貼近散戶體感):每月第一個交易日投入 monthly_amount(名目),
    以當日還原價換算累積股數;每個交易日的「淨值」= 當前持股市值 / 累積投入本金。
    起點淨值 = 1.0(第一次投入當天,市值≈本金)。逐期報酬 = 該淨值的日變動率。

    注意:DCA 的淨值是「市值/累積投入」比值,反映的是持有部位的報酬體驗;因每月加碼,
    後段單日%變動會被較大的部位主導(這正是 DCA 的真實行為)。這條是「無腦定期定額」
    基準,不是單筆買進持有。
    """
    from marketvault.db import engine
    e = engine()
    q = """
        SELECT p.trade_date, COALESCE(p.adj_close, p.close) AS adj
        FROM market.price_daily p JOIN market.security s USING(security_id)
        WHERE s.symbol = '0050' AND COALESCE(p.adj_close, p.close) IS NOT NULL
        ORDER BY p.trade_date
    """
    px = pd.read_sql(q, e, parse_dates=["trade_date"]).set_index("trade_date")["adj"].astype(float)
    px = px[~px.index.duplicated(keep="last")].sort_index()
    px = px[px > 0]

    # 資料衛生:marketvault 的 0050 adj_close 在 2014-01-02 有一個 -75% 的單日斷點
    # (還原因子在 2014 前算壞;raw close 當日並無此崩跌,2020/2022 真實回撤都正確)。
    # 台股自 2015-06 才放寬到 ±10%、之前 ±7%,任何單日 |還原報酬|>12% 皆屬還原瑕疵。
    # 誠實作法:從最後一個「壞斷點」之後起算,只用乾淨區間(實測 = 2014-01-03 起,
    # 12.5 年、含 2020 COVID -28%、2022 -34% 的真實回撤)。不美化、只剔除已壞資料。
    r_all = px.pct_change()
    bad_breaks = r_all.index[r_all.abs() > 0.12]
    clean_start = px.index[0]
    trimmed_from = None
    if len(bad_breaks) > 0:
        # 起點設在最後一個壞斷點「當日」(該日報酬本身壞,故從隔一交易日的價格序列重算)
        last_break = bad_breaks.max()
        clean_start = last_break
        trimmed_from = clean_start.strftime("%Y-%m-%d")
    px = px[px.index >= clean_start]

    # 每月第一個交易日 = 投入日
    first_of_month = px.groupby([px.index.year, px.index.month]).head(1).index
    contrib_days = set(pd.DatetimeIndex(first_of_month))

    shares = 0.0
    invested = 0.0
    dates, nav_vals = [], []
    for d, price in px.items():
        if d in contrib_days:
            shares += monthly_amount / price
            invested += monthly_amount
        if invested <= 0:
            continue
        mkt_val = shares * price
        nav_vals.append(mkt_val / invested)  # 市值 / 累積投入
        dates.append(d)

    nav = pd.Series(nav_vals, index=pd.DatetimeIndex(dates))
    # 正規化起點到 1.0(第一投入日市值/本金理論上=1.0,浮點保險起見顯式歸一)
    nav = nav / nav.iloc[0]
    returns = nav.pct_change().fillna(0.0)

    iso = [d.strftime("%Y-%m-%d") for d in nav.index]
    return dict(
        name_zh="0050 定期定額",
        name_en="0050 Monthly DCA",
        dates=iso,
        returns=[round(float(x), 8) for x in returns.values],
        nav=[round(float(x), 8) for x in nav.values],
        desc=("台灣 50 ETF(0050,含息還原)每月固定金額定期定額的淨值 = 持股市值/累積投入。"
              "散戶『無腦定期定額』基準。市場資料為自算淨值比值,不含個股價格明細。"
              + (f"(資料衛生:marketvault 還原價在 {trimmed_from} 前有還原斷點,已誠實截短、自此乾淨區間起算。)"
                 if trimmed_from else "")),
        _trimmed_from=trimmed_from,
    )


# --------------------------------------------------------------------------
# 2) factor_harvest multi nav_net(marketvault venv 可直接 import)
# --------------------------------------------------------------------------
def build_factor_harvest() -> dict:
    sys.path.insert(0, str(FARM_REPO / "farm"))
    import factor_harvest_mvp as fh  # noqa: E402
    res = fh.run_all()
    r = res["multi"]
    nav = r.nav_net.dropna()
    nav = nav / nav.iloc[0]  # 正規化起點 1.0
    returns = nav.pct_change().fillna(0.0)
    iso = [d.strftime("%Y-%m-%d") for d in nav.index]
    return dict(
        name_zh="農場因子收割(multi)",
        name_en="Farm Factor Harvest (multi)",
        dates=iso,
        returns=[round(float(x), 8) for x in returns.values],
        nav=[round(float(x), 8) for x in nav.values],
        desc=("農場 factor_harvest_mvp 五因子(value/momentum/low_vol/size/quality)等權合成、"
              "月再平衡、vol-target 控風險、扣台股交易成本後的【回測淨值】(nav_net)。"
              "beta/因子溢酬,非 alpha;回測會高估實盤;僅供對照,非推薦。"),
    )


# --------------------------------------------------------------------------
# 3) highrisk_beta BTC(farm venv 需要 config/farm/backtest,走 subprocess)
# --------------------------------------------------------------------------
_HIGHRISK_SUBPROC = r'''
import os, sys, json
os.chdir(r"C:/Users/user/Desktop/auto-quant-btc")
sys.path.insert(0, r"C:/Users/user/Desktop/auto-quant-btc")
from farm.highrisk_beta_mvp import backtest_single
res, df, pos = backtest_single("BTC")   # BTC vol-target x 200MA(模組預設生產配置)
eq = res["equity"].dropna()
eq = eq / float(eq.iloc[0])             # 正規化起點 1.0
ret = eq.pct_change().fillna(0.0)
out = {
    "dates": [d.strftime("%Y-%m-%d") for d in eq.index],
    "returns": [round(float(x), 8) for x in ret.values],
    "nav": [round(float(x), 8) for x in eq.values],
}
sys.stdout.write("__JSON__" + json.dumps(out) + "__END__")
'''


def build_highrisk_beta() -> dict:
    proc = subprocess.run(
        [str(FARM_VENV), "-c", _HIGHRISK_SUBPROC],
        capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"highrisk subprocess failed rc={proc.returncode}\nSTDERR:\n{proc.stderr[-2000:]}")
    raw = proc.stdout
    if "__JSON__" not in raw or "__END__" not in raw:
        raise RuntimeError(f"highrisk subprocess produced no JSON marker.\nSTDOUT tail:\n{raw[-1500:]}\nSTDERR:\n{proc.stderr[-1500:]}")
    payload = json.loads(raw.split("__JSON__", 1)[1].split("__END__", 1)[0])
    return dict(
        name_zh="加密高風險 beta(BTC)",
        name_en="Crypto High-Risk Beta (BTC)",
        dates=payload["dates"],
        returns=payload["returns"],
        nav=payload["nav"],
        desc=("農場 highrisk_beta_mvp:BTC × vol-target(目標年化波動)× 200MA 趨勢閘(崩盤保險)、"
              "槓桿上限、扣成本後的【回測淨值】(equity)。方向性 beta/被補償的市場風險,非 alpha;"
              "加密高波動、回測嚴重高估實盤;僅供對照,非推薦。"),
    )


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def main() -> None:
    benchmarks: dict = {}
    failures: dict = {}
    # 每條各自的年化週期數(sanity 用):台股/因子 ~252 交易日;加密 365 日曆日
    ppy = {"tw_0050_dca": 252.0, "tw_factor_harvest": 252.0, "crypto_highrisk_beta": 365.0}

    builders = [
        ("tw_0050_dca", build_0050_dca),
        ("tw_factor_harvest", build_factor_harvest),
        ("crypto_highrisk_beta", build_highrisk_beta),
    ]
    sanity_report = {}
    extra_notes = []
    for key, fn in builders:
        try:
            b = fn()
            # 把非序列的內部標記(如資料截短原因)抽出,不放進前端 JSON 的 benchmark 欄位
            trimmed = b.pop("_trimmed_from", None)
            if trimmed:
                extra_notes.append(f"{key} 自 {trimmed} 起算(還原斷點前已截短)")
            assert len(b["dates"]) == len(b["returns"]) == len(b["nav"]) > 0, "length mismatch or empty"
            assert abs(b["nav"][0] - 1.0) < 1e-6, f"nav[0] != 1.0 ({b['nav'][0]})"
            assert not any(np.isnan(x) for x in b["nav"]), "NaN in nav"
            assert not any(np.isnan(x) for x in b["returns"]), "NaN in returns"
            benchmarks[key] = b
            sanity_report[key] = _sanity(b["dates"], b["returns"], b["nav"], ppy[key])
            print(f"[OK] {key}: {sanity_report[key]}")
        except Exception as ex:
            failures[key] = f"{type(ex).__name__}: {ex}"
            print(f"[FAIL] {key}: {failures[key]}")

    note = (
        "對照基準線,由本機離線回測/自算產生。三條基準市場不同、交易日曆不同"
        "(加密 7 日/週、台股約 5 日/週),各留原生日頻 ISO 日期軸;前端比較時以日期對齊。"
        "tw_0050_dca=0050 含息定期定額自算淨值(市值/累積投入);tw_factor_harvest / "
        "crypto_highrisk_beta 為農場【回測】淨值(扣成本),beta 非 alpha、實盤打折、僅供對照非推薦。"
        "gbm_factor 因 farm venv 缺 lightgbm 未納入。JSON 僅含報酬/淨值序列與日期,不含個股價格明細。"
    )
    if extra_notes:
        note += " 資料截短:" + "; ".join(extra_notes) + "。"
    if failures:
        note += " 失敗基準:" + "; ".join(f"{k}({v})" for k, v in failures.items()) + "。"

    doc = dict(
        meta=dict(
            generated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            source="edge-validator/benchmarks/gen_baselines.py (marketvault + auto-quant-btc farm)",
            note=note,
            sanity=sanity_report,
        ),
        benchmarks=benchmarks,
    )
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT} ({OUT.stat().st_size:,} bytes) with {len(benchmarks)} benchmark(s)")
    if failures:
        print(f"FAILURES: {failures}")


if __name__ == "__main__":
    main()
