"""
Build a self-contained interactive HTML dashboard for the breakeven return series.

Why HTML: the repo bid/offer effect (and the seasonal β-hedge) is LINEAR in its parameter, so
every recomputation (slider move, date range, tenor switch, β) is a trivial client-side vector
op -> instant, no server, no lag. Plotly.js for charts (zoom/pan/hover), native HTML date
pickers + range sliders, a clean CSS layout, a sortable raw-numbers table (daily or monthly),
live net P&L, and a one-click CSV download of the current window.

Tenor is MULTI-SELECT only where overlay is supported -- the Chart and the seasonal Calendar (one
line per tenor); every other view is single-tenor (selecting a tenor switches to it, and switching
to such a view collapses to the primary). Windows are shared. The two repo half-spread sliders apply
everywhere now (chart, table AND every seasonal view): they net the financing cost off each leg
(TIPS by x_TIPS, UST by x_UST; the breakeven scales the UST cost by beta); set both 0 for mid.

Gasoline hedge: the Chart, Table and the seasonal Breakeven metric all support a β slider
(BE = TIPS − β·UST, default 100%), a gas-hedge ON/OFF toggle, and a 2Y/5Y hedge-window toggle.
When on, the breakeven P&L is netted against a short gasoline position sized by the month's hedge
ratio h(β) = bT − β·bU contracts (the per-leg gas-regression slopes over the chosen rolling
window, walk-forward, rebalanced monthly in sync with the DV01 rebalance; see hedge.py). All
client-side off the shipped daily gas series + monthly (bT,bU) coefficients.

Three views (top-left toggle):
  * Chart  -- single tenor: cumulative long/short/mid breakeven net P&L (repo half-spread sliders,
    β, optional gas hedge).
    Multiple tenors: one cumulative LONG-BE line per tenor (repo half-spread applied; set both
    sliders 0 for mid) + per-tenor totals across the top.
  * Table  -- the same, as daily or monthly rows with a window total (primary tenor).
  * Seasonal -- auction-cycle & calendar analysis (engine.seasonal_table). Every month is split
    into 4 periods around its single TIPS auction (A0..A4, ±1 week; shared monthly anchor across
    all three tenors). Six sub-modes (toggle):
      - Aggregate: a BOX PLOT per (month, period) -- box=IQR, whiskers=1.5*IQR, solid=median, bright
        yellow tick=MEAN, only outlier year-points (hover=year+value), y-axis rescaled to p1..p99 so
        boxes fill -- + cumulative Σ-median path + a "within-month signature" (per-period boxes).
      - History: each (year,month,period) bucket as a point over time (high-contrast lines, x=year);
        period CHECKBOXES + Month filter (e.g. P1 all months = every P1 over time).
      - Calendar: the CALENDAR effect -- daily P&L by business-day-of-month (holidays/d=0 excluded),
        shown as the SAME box plot (box/median/mean tick) when single tenor+window, lines when
        overlaying; From-start/From-end day count; Month filter.
      - Predict: OLS regressions P1->P2, P2->P3, P3->P4, P2+P3->P4, (P1+P2)->P3, (P1+P2)->(P3+P4),
        the cross-month P4->next-P1, and month->next-month (totals, adjacency = the filtered set)
        (slope, R^2, corr, t-stat, n; |t|>2 flagged) + a scatter -- does early performance predict later?
      - Cumul: cumulative P&L holding only the selected slice (period checkboxes, combinable e.g.
        P1+P2) each filtered month -- x lists ONLY the kept months in sequence (jumps over excluded,
        e.g. Issue=New+P1 = every new-issue month's P1) + metrics (n, total, mean, vol, Sharpe-ann,
        max drawdown) on the slice returns.
      - Event: every auction aligned at day 0, ±N business days; each line = that auction's
        cumulative metric rebased to 0 on the auction day, + thick median & mean across all
        filtered auctions -> spot per-cycle out/under-performers. Year selector + Window (±N).
    Shared controls: Metric TIPS / Nominal / Breakeven (β slider on Breakeven, = TIPS − β·UST,
    default β=100% = equal-DV01 plain breakeven); Sample window(s) Full / 5Y / 3Y (multi-select ->
    overlaid in signature/calendar/predict for degradation comparison); Issue type All / New / Reopen
    (new-issue vs reopening months); Months multi-select (any subset; quick All/H1/H2/None). Client-side off the
    shipped seas table, so every control is instant. Units = engine bp (= $/100k-DV01 P&L; ×$100k = $).

Output: dashboard.html  (open in any browser; Plotly is embedded so it works offline).

On launch it first calls engine.refresh() — pull the latest data from Bloomberg
(data_layer.update) and rebuild returns_<tenor>.parquet — so the dashboard is never stale.
If the Terminal isn't running the pull is skipped and it builds from the cached data.

Run:  python dashboard.py             # refresh, build dashboard.html, and open it
      python dashboard.py --no-update # build from the cached data as-is (no Bloomberg pull)
      python dashboard.py --no-open   # build but don't open the browser
"""
from __future__ import annotations
import os, sys, json, webbrowser
import pandas as pd

import engine
import hedge

HERE = os.path.dirname(os.path.abspath(__file__))
TENORS = ["5y", "10y", "30y"]
PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"


def build_payload():
    """Per tenor: daily leg returns (for Chart/Table) + the pre-bucketed seasonal keystone table
    (engine.seasonal_table -> rows of year,month,period with the bucket-SUMMED leg P&L). The
    Seasonal view aggregates this client-side — median across years, IQR band, n — as the
    tenor/metric/β change. Empty/clamped buckets ship as null and are excluded from the median."""
    st = engine.seasonal_table(save=True)
    ac, inew = engine.tips_auction_calendar(), engine.tips_auction_isnew()   # monthly TIPS auction dates + new/reopen
    aucts = [{"d": pd.Timestamp(v).strftime("%Y-%m-%d"), "n": bool(inew.get(k, True))}
             for k, v in sorted(ac.items(), key=lambda kv: pd.Timestamp(kv[1]))]
    data = {}
    for t in TENORS:
        p = os.path.join(engine.CACHE, f"returns_{t}.parquet")
        if not os.path.exists(p):
            continue
        d = engine.load_returns(t).dropna(subset=["r_BE_bp"])
        s = st[st["tenor"] == t]
        nn = lambda col: [None if pd.isna(v) else round(float(v), 4) for v in s[col]]
        gas = hedge.daily_gas_usd(d.index)              # daily gas $/contract aligned to the chart dates
        data[t] = {
            "dates": [x.strftime("%Y-%m-%d") for x in d.index],
            "rT": [round(float(v), 4) for v in d["r_TIPS_bp"]],
            "rU": [round(float(v), 4) for v in d["r_UST_bp"]],
            "rBE": [round(float(v), 4) for v in d["r_BE_bp"]],
            "sT": [round(float(v), 6) for v in d["s_TIPS"]],
            "sU": [round(float(v), 6) for v in d["s_UST"]],
            "dd": [0 if pd.isna(v) else int(v) for v in d["days"]],   # settlement span; 0 = market holiday
            "g": [round(float(v), 2) for v in gas],        # daily gas $/contract (for the gas hedge)
            "hedge": hedge.hedge_coef_map(t),              # {window:{'YYYY-MM':[bT,bU]}} -> h(β)=bT−β·bU contracts

            "seas": {                                  # keystone bucket table (this tenor)
                "y": [int(v) for v in s["year"]], "m": [int(v) for v in s["month"]],
                "p": [int(v) for v in s["period"]], "t": nn("tips_pnl"), "u": nn("ust_pnl"),
                "st": nn("tips_slip"), "su": nn("ust_slip"),   # Σ repo half-spread sensitivity (apply x_TIPS/x_UST)
                "g": nn("gas_pnl"),                        # Σ gas $/contract in the bucket (gas hedge)
                "d": [int(v) for v in s["trading_days"]], "c": [bool(v) for v in s["clamped"]],
                "n": [bool(v) for v in s["new_issue"]],   # new-issue (vs reopening) month
            },
            "aucts": aucts,                                # monthly TIPS auction dates (for the event view)
        }
    if not data:
        raise SystemExit("No returns_*.parquet — run:  python engine.py")
    return data


def plotly_tag():
    """Embed Plotly inline (offline-safe); fall back to the CDN <script> if the fetch fails."""
    try:
        import requests
        r = requests.get(PLOTLY_CDN, timeout=40)
        r.raise_for_status()
        return "<script>" + r.text + "</script>"
    except Exception as e:
        print(f"  (could not embed Plotly: {e}; using CDN link — needs internet to open)")
        return f'<script src="{PLOTLY_CDN}"></script>'


HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Breakeven financed total return</title>
__PLOTLY__
<style>
  :root{--bg:#0f1419;--panel:#1b2430;--ink:#e6edf3;--muted:#8b98a5;--line:#2d3a48;--accent:#2f81f7;
        --green:#3fb950;--red:#f85149;--grey:#8b98a5;}
  *{box-sizing:border-box}
  body{margin:0;font:13px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}
  .app{display:grid;grid-template-columns:260px 1fr;height:100vh}
  .side{background:var(--panel);border-right:1px solid var(--line);padding:16px;overflow:auto;user-select:none}
  .main{display:flex;flex-direction:column;min-width:0}
  h1{font-size:15px;margin:0 0 14px}
  .grp{margin-bottom:16px}
  .grp>label{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px}
  .seg{display:flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}
  .seg button{flex:1;background:transparent;color:var(--ink);border:0;padding:7px 4px;cursor:pointer;font-size:12px}
  .seg button.on{background:var(--accent);color:#fff}
  .row{display:flex;align-items:center;gap:8px;margin:6px 0}
  input[type=range]{width:100%;touch-action:none}
  input[type=date]{width:100%;background:#0f1722;color:var(--ink);border:1px solid var(--line);border-radius:5px;padding:6px}
  .val{min-width:42px;text-align:right;font-variant-numeric:tabular-nums;color:var(--accent)}
  button.act{width:100%;background:var(--accent);color:#fff;border:0;border-radius:6px;padding:9px;cursor:pointer;font-size:13px;margin-top:4px}
  button.ghost{background:transparent;border:1px solid var(--line);color:var(--ink)}
  .totals{display:flex;gap:18px;padding:10px 18px;border-bottom:1px solid var(--line);flex-wrap:wrap}
  .tot{font-variant-numeric:tabular-nums}.tot b{font-size:18px}.tot .lab{color:var(--muted);font-size:11px;display:block}
  .tot.l b{color:var(--green)}.tot.s b{color:var(--red)}.tot.m b{color:var(--grey)}
  #chart{flex:1;min-height:0}
  .tablewrap{flex:1;overflow:auto;padding:0 18px 18px}
  table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
  th,td{padding:4px 10px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
  th{position:sticky;top:0;background:var(--panel);color:var(--muted);font-weight:600;cursor:default}
  td:first-child,th:first-child{text-align:left}
  tr.total td{font-weight:700;border-top:2px solid var(--line);position:sticky;bottom:0;background:var(--panel)}
  .pos{color:var(--green)}.neg{color:var(--red)}
  .note{color:var(--muted);font-size:11px;padding:6px 18px}
  .hl{color:var(--accent)}
  #seaswrap{flex:1;min-height:0;display:flex;flex-direction:column;padding:0 10px 8px}
  #seastop{flex:1.7;min-height:0}#seassig{flex:1;min-height:0}
  #seashist{flex:1;min-height:0;display:flex;flex-direction:column;padding:0 10px 8px}
  #histchart{flex:1;min-height:0}
  .grp.off{opacity:.4}
  select{width:100%;background:#0f1722;color:var(--ink);border:1px solid var(--line);border-radius:5px;padding:6px}
  .checks{display:flex;flex-direction:column;gap:8px;margin-top:2px}
  .checks label{display:flex;align-items:center;gap:9px;font-size:14px;cursor:pointer;color:var(--ink)}
  .checks input{width:17px;height:17px;cursor:pointer;accent-color:var(--accent);flex:none}
  .checks .sw{width:12px;height:12px;border-radius:3px;flex:none}
  .checks .rng{color:var(--muted);font-size:11px}
  .mcheck{flex-direction:row;flex-wrap:wrap;gap:5px 4px}
  .mcheck label{width:29%;font-size:12px;gap:4px}
  #mquick.msel button{font-size:11px;padding:5px 2px}
  #seasmode.sm4{flex-wrap:wrap}#seasmode.sm4 button{flex:1 1 30%;font-size:11px;padding:6px 2px}
  #seascal,#seasreg,#seascum,#seasev{flex:1;min-height:0;display:flex;flex-direction:column;padding:0 10px 8px}
  #seascalchart,#regchart,#cumchart,#evchart{flex:1;min-height:0}
  #cummetrics{display:flex;gap:20px;flex-wrap:wrap;padding:6px 2px;font-variant-numeric:tabular-nums}
  #cummetrics .met{font-size:12px}#cummetrics .met b{font-size:16px;display:block}#cummetrics .met span{color:var(--muted);font-size:10px}
  #regtablewrap{padding:4px 0 8px}
  #regtablewrap table{width:auto;font-variant-numeric:tabular-nums;font-size:12px}
  #regtablewrap th,#regtablewrap td{padding:3px 14px;text-align:right;border-bottom:1px solid var(--line);position:static}
  #regtablewrap td:first-child,#regtablewrap th:first-child{text-align:left}
</style></head>
<body><div class="app">
  <div class="side">
    <h1>Breakeven financed TR</h1>
    <div class="grp"><label>Tenor <span class="note" id="tennote" style="padding:0">(multi = overlay in Chart/Calendar)</span></label><div class="seg" id="tenor"></div></div>
    <div class="grp"><label>Repo half-spread x_TIPS <span class="hl" id="xtv">3.0</span> bp</label>
      <input type="range" id="xt" min="0" max="25" step="0.5" value="3"></div>
    <div class="grp"><label>Repo half-spread x_UST <span class="hl" id="xuv">3.0</span> bp</label>
      <input type="range" id="xu" min="0" max="25" step="0.5" value="3"></div>
    <div class="grp rv"><label>Date range</label>
      <div class="row"><input type="date" id="start"></div>
      <div class="row"><input type="date" id="end"></div>
      <div class="seg" id="range" style="margin-top:6px"><button data-range="full">Full</button>
        <button data-range="5y">5Y</button><button data-range="3y">3Y</button></div></div>
    <div class="grp"><label>View</label><div class="seg" id="view">
      <button data-view="chart" class="on">Chart</button><button data-view="table">Table</button>
      <button data-view="seasonal">Seasonal</button></div></div>
    <div class="grp posv" style="display:none"><label>Position (cumulative) <span class="note" style="padding:0">short = mirror</span></label>
      <div class="seg" id="pos"><button data-pos="long" class="on">Long</button><button data-pos="short">Short</button></div></div>
    <div class="grp rv"><label>Table frequency</label><div class="seg" id="freq">
      <button data-freq="monthly" class="on">Monthly</button><button data-freq="daily">Daily</button></div></div>
    <div class="grp sv" style="display:none"><label>Seasonal metric</label><div class="seg" id="smetric">
      <button data-smetric="tips" class="on">TIPS</button><button data-smetric="nom">Nominal</button>
      <button data-smetric="be">Breakeven</button></div></div>
    <div class="grp bev" id="betagrp" style="display:none"><label>Beta (β) <span class="hl" id="betav">100</span>%
      &nbsp;<span class="note" style="padding:0">BE = TIPS − β·UST (β=100 = equal DV01)</span></label>
      <input type="range" id="beta" min="0" max="150" step="5" value="100"></div>
    <div class="grp bev" id="gasgrp" style="display:none"><label>Gasoline hedge <span class="note" style="padding:0">BE − h·gas (short gas vs long BE)</span></label>
      <div class="seg" id="gas"><button data-gas="off" class="on">Off</button><button data-gas="on">On</button></div></div>
    <div class="grp bev" id="gwingrp" style="display:none"><label>Hedge window <span class="note" style="padding:0">rolling regression lookback</span></label>
      <div class="seg" id="gwin"><button data-gwin="2y">2Y</button><button data-gwin="5y" class="on">5Y</button></div></div>
    <div class="grp sv" style="display:none"><label>Sample window(s)</label>
      <div class="checks" id="swin">
        <label><input type="checkbox" value="full" checked><span class="sw" style="background:#2f81f7"></span><b>Full</b><span class="rng">2011 → now</span></label>
        <label><input type="checkbox" value="5y"><span class="sw" style="background:#f5a623"></span><b>5Y</b><span class="rng">last 5 years</span></label>
        <label><input type="checkbox" value="3y"><span class="sw" style="background:#e63946"></span><b>3Y</b><span class="rng">last 3 years</span></label>
      </div></div>
    <div class="grp sv" style="display:none"><label>Issue type (auction)</label><div class="seg" id="issue">
      <button data-issue="all" class="on">All</button><button data-issue="new">New</button><button data-issue="reopen">Reopen</button></div></div>
    <div class="grp cv" style="display:none"><label>Day count</label><div class="seg" id="calend">
      <button data-calend="start" class="on">From start</button><button data-calend="end">From end</button></div></div>
    <div class="grp sv" style="display:none"><label>Seasonal view</label><div class="seg sm4" id="seasmode">
      <button data-seasmode="agg" class="on">Aggregate</button><button data-seasmode="hist">History</button>
      <button data-seasmode="cal">Calendar</button><button data-seasmode="reg">Predict</button>
      <button data-seasmode="cum">Cumul</button><button data-seasmode="event">Event</button></div></div>
    <div class="grp hv" style="display:none"><label>Periods (auction cycle)</label>
      <div class="checks" id="speriod">
        <label><input type="checkbox" value="1" checked><span class="sw" style="background:#4cc9f0"></span><b>P1</b><span class="rng">m/e → auction−1w</span></label>
        <label><input type="checkbox" value="2" checked><span class="sw" style="background:#e63946"></span><b>P2</b><span class="rng">auction−1w → auction</span></label>
        <label><input type="checkbox" value="3" checked><span class="sw" style="background:#52b788"></span><b>P3</b><span class="rng">auction → auction+1w</span></label>
        <label><input type="checkbox" value="4" checked><span class="sw" style="background:#c77dff"></span><b>P4</b><span class="rng">auction+1w → m/e</span></label>
      </div></div>
    <div class="grp mv" style="display:none"><label>Months (multi-select)</label>
      <div class="seg msel" id="mquick" style="margin-bottom:6px"><button data-mq="all">All</button><button data-mq="h1">H1</button><button data-mq="h2">H2</button><button data-mq="none">None</button></div>
      <div class="checks mcheck" id="smonths"></div></div>
    <div class="grp ev" style="display:none"><label>Event year (lines)</label>
      <select id="evyear"></select></div>
    <div class="grp ev" style="display:none"><label>Window ± <span class="hl" id="evnv">15</span> business days</label>
      <input type="range" id="evn" min="5" max="30" step="1" value="15"></div>
    <div class="grp pv" style="display:none"><label>Regression to plot</label>
      <select id="regpair"><option value="P1>P2">P1 → P2</option><option value="P2>P3">P2 → P3</option>
        <option value="P3>P4">P3 → P4</option><option value="P23>P4">P2+P3 → P4</option>
        <option value="P12>P3">P1+P2 → P3</option><option value="P12>P34">P1+P2 → P3+P4</option>
        <option value="P4>P1n">P4 → next-month P1</option><option value="M>Mn">month → next month</option></select></div>
    <button class="act rv" id="dl">Download CSV (window)</button>
    <p class="note">Long-BE = long TIPS / short UST. Both directions carry the slippage
      (long pays GC+x, short earns GC&minus;x), so they are not mirror images. Mid = zero spread.
      The two repo half-spread sliders apply <b>everywhere</b> (chart, table and every seasonal view):
      they net the financing cost off each leg (TIPS by x_TIPS, UST by x_UST; β scales the UST cost).
      Set both 0 for mid. <b>β</b>, <b>gasoline hedge</b> and the <b>hedge window</b> act on the
      breakeven (Chart, Table &amp; the seasonal Breakeven metric): hedged BE = BE &minus;
      h·(gas&nbsp;$/contract)/$100k, h = the month's contracts ratio (bT&minus;β·bU) from the 2Y/5Y
      rolling regression, held all month. Months before a window is full are unhedged. Specialness
      not modeled. Full data: <code>python export.py</code>.</p>
  </div>
  <div class="main">
    <div class="totals rv" id="totals"></div>
    <div id="chart"></div>
    <div class="tablewrap" id="tablewrap" style="display:none"><table id="tbl"></table><div class="note" id="tnote"></div></div>
    <div id="seaswrap" style="display:none">
      <div class="note" id="seascap"></div>
      <div id="seastop"></div>
      <div class="note" style="padding-top:10px"><b>Within-month signature</b> — the four periods averaged across all 12 months (isolates the auction cycle):</div>
      <div id="seassig"></div>
    </div>
    <div id="seashist" style="display:none">
      <div class="note" id="histcap"></div>
      <div id="histchart"></div>
    </div>
    <div id="seascal" style="display:none">
      <div class="note" id="calcap"></div>
      <div id="seascalchart"></div>
    </div>
    <div id="seasreg" style="display:none">
      <div class="note" id="regcap"></div>
      <div id="regtablewrap"></div>
      <div id="regchart"></div>
    </div>
    <div id="seascum" style="display:none">
      <div class="note" id="cumcap"></div>
      <div id="cummetrics"></div>
      <div id="cumchart"></div>
    </div>
    <div id="seasev" style="display:none">
      <div class="note" id="evcap"></div>
      <div id="evchart"></div>
    </div>
  </div>
</div>
<script>
const DATA = __DATA__;
const TENORS = Object.keys(DATA);
const S = {tenor: TENORS.includes("10y")?"10y":TENORS[0], tenors:(TENORS.includes("10y")?["10y"]:[TENORS[0]]), xT:3, xU:3, start:null, end:null, view:"chart", freq:"monthly", smetric:"tips", beta:100, gas:"off", gwin:"5y", seasmode:"agg", periods:[1,2,3,4], smonths:[1,2,3,4,5,6,7,8,9,10,11,12], swins:["full"], calend:"start", issue:"all", regpair:"P1>P2", evyear:"all", evn:15, pos:"long"};
const MONTHS=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const PCOL=["#bcd4f2","#7fb0e8","#2f81f7","#1b4f8f"];   // aggregate bars: ordered light->dark ramp
const HCOL=["#4cc9f0","#e63946","#52b788","#c77dff"];   // history lines: high-contrast (overlay-friendly)
const PLAB=["P1 · prev m/e → auction−1w","P2 · auction−1w → auction","P3 · auction → auction+1w","P4 · auction+1w → m/e"];
const $ = id => document.getElementById(id);
const fmt = (x,d=1) => (x>=0?"+":"") + x.toFixed(d);
const cls = x => x>=0?"pos":"neg";
// --- gasoline hedge: h(β) = bT − β·bU contracts (per tenor/window/month); hedged BE $ = BE$ − h·gas$ ---
function hLookup(hedgeObj, ym, beta){          // ym = "YYYY-MM"; returns contracts h(β) for the active window, or null
  if(!hedgeObj) return null; const o=hedgeObj[S.gwin]; if(!o) return null;
  const c=o[ym]; return c ? c[0]-beta*c[1] : null; }
function gasOn(){ return S.gas==="on"; }
function gasNote(){ return gasOn()?", gas-hedged "+S.gwin.toUpperCase():""; }   // appended to BE metric labels
function cfgLab(){ return "β="+S.beta+"%"+(gasOn()?", gas-hedged "+S.gwin.toUpperCase():""); }   // chart/totals tag

function winIdx(){
  const d = DATA[S.tenor], n = d.dates.length; let lo=0, hi=n-1;
  if(S.start){ while(lo<n && d.dates[lo]<S.start) lo++; }
  if(S.end){ while(hi>=0 && d.dates[hi]>S.end) hi--; }
  return {d, lo, hi};
}
function series(){
  const {d,lo,hi} = winIdx();
  const o = {dates:[],rT:[],rU:[],rBE:[],lBE:[],sBE:[],cL:[],cS:[],cM:[]};
  const beta=S.beta/100, gh=gasOn();
  let cl=0,cs=0,cm=0;
  for(let i=lo;i<=hi;i++){
    const be = d.rT[i]-beta*d.rU[i];                       // breakeven (β-weighted nominal)
    const slip = S.xT*d.sT[i] + beta*S.xU*d.sU[i];         // repo half-spread (β scales UST cost)
    let hg=0; if(gh){ const h=hLookup(d.hedge, d.dates[i].slice(0,7), beta); if(h!=null) hg=h*(d.g[i]||0)/1e5; }
    const lbe = be-slip-hg, sbe = -be-slip+hg, mid = be-hg;  // gas: short h vs long BE -> ∓hg
    cl+=lbe; cs+=sbe; cm+=mid;
    o.dates.push(d.dates[i]); o.rT.push(d.rT[i]); o.rU.push(d.rU[i]); o.rBE.push(mid);
    o.lBE.push(lbe); o.sBE.push(sbe); o.cL.push(cl); o.cS.push(cs); o.cM.push(cm);
  }
  return o;
}
function monthly(s){
  const map={}, ord=[];
  for(let i=0;i<s.dates.length;i++){
    const m=s.dates[i].slice(0,7);
    if(!(m in map)){map[m]=[0,0,0,0,0]; ord.push(m);}
    const a=map[m]; a[0]+=s.rT[i]; a[1]+=s.rU[i]; a[2]+=s.rBE[i]; a[3]+=s.lBE[i]; a[4]+=s.sBE[i];
  }
  return ord.map(m=>[m,...map[m]]);
}
function overlayOK(){ return S.view==="chart" || (S.view==="seasonal" && S.seasmode==="cal"); }  // only Chart & seasonal Calendar overlay tenors
function applyView(){
  const seasonal = S.view==="seasonal", mode = S.seasmode;
  if(!overlayOK() && S.tenors.length>1){ S.tenors=[S.tenor]; syncTenorBtns(); }   // collapse to primary where overlay is unsupported
  $("tennote").textContent = overlayOK() ? "(multi = overlay here)" : "(single tenor in this view)";
  document.querySelectorAll(".rv").forEach(e=>e.style.display=seasonal?"none":"");
  document.querySelectorAll(".sv").forEach(e=>e.style.display=seasonal?"":"none");
  document.querySelectorAll(".hv").forEach(e=>e.style.display=(seasonal&&(mode==="hist"||mode==="cum"))?"":"none"); // periods (history/cumul)
  document.querySelectorAll(".mv").forEach(e=>e.style.display=(seasonal&&mode!=="agg")?"":"none");        // month (hist/cal/reg/cum)
  document.querySelectorAll(".cv").forEach(e=>e.style.display=(seasonal&&mode==="cal")?"":"none");        // day-count (calendar)
  document.querySelectorAll(".pv").forEach(e=>e.style.display=(seasonal&&mode==="reg")?"":"none");        // regression picker
  document.querySelectorAll(".ev").forEach(e=>e.style.display=(seasonal&&mode==="event")?"":"none");      // event year + window
  document.querySelectorAll(".posv").forEach(e=>e.style.display=(seasonal||(S.view==="chart"&&S.tenors.length>1))?"":"none"); // long/short (cumulative views)
  const bev = !seasonal || S.smetric==="be";                            // β / gas hedge / window apply to the breakeven (chart, table, seasonal-BE)
  document.querySelectorAll(".bev").forEach(e=>e.style.display=bev?"":"none");
  $("gwingrp").classList.toggle("off", !gasOn());                       // hedge window only matters when gas is on
  $("chart").style.display=(!seasonal && S.view==="chart")?"":"none";
  $("tablewrap").style.display=(!seasonal && S.view==="table")?"":"none";
  $("seaswrap").style.display=(seasonal&&mode==="agg")?"":"none";
  $("seashist").style.display=(seasonal&&mode==="hist")?"":"none";
  $("seascal").style.display=(seasonal&&mode==="cal")?"":"none";
  $("seasreg").style.display=(seasonal&&mode==="reg")?"":"none";
  $("seascum").style.display=(seasonal&&mode==="cum")?"":"none";
  $("seasev").style.display=(seasonal&&mode==="event")?"":"none";
}
const SEASDRAW={agg:()=>drawSeasonal(),hist:()=>drawHistory(),cal:()=>drawCalendar(),reg:()=>drawPredict(),cum:()=>drawCumul(),event:()=>drawEvent()};
function render(){
  applyView();
  if(S.view==="seasonal"){ (SEASDRAW[S.seasmode]||SEASDRAW.agg)(); return; }
  const s = series();
  const last = a => a.length?a[a.length-1]:0;
  const win = s.dates.length ? (s.dates[0]+" → "+s.dates[s.dates.length-1]) : "—";
  if(S.view==="chart" && S.tenors.length>1){            // per-tenor long/short-BE totals (slippage applied)
    let h=""; const isS=S.pos==="short", dir=isS?"short":"long";
    for(const t of S.tenors){ const c=cumBEFor(t), arr=isS?c.short:c.long, v=arr.length?arr[arr.length-1]:0;
      h+="<div class='tot'><span class='lab' style='color:"+TCOL[t]+"'>"+t+" "+dir+"-BE</span><b class='"+(v>=0?"pos":"neg")+"'>"+fmt(v,0)+"</b> bp</div>"; }
    $("totals").innerHTML=h+"<div class='tot'><span class='lab'>config</span><b style='font-size:12px'>"+cfgLab()+"</b></div>"
      +"<div class='tot'><span class='lab'>window</span><b style='font-size:13px'>"+win+"</b></div>";
  } else {
    $("totals").innerHTML="<div class='tot l'><span class='lab'>long breakeven</span><b>"+fmt(last(s.cL),0)+"</b> bp</div>"
      +"<div class='tot s'><span class='lab'>short breakeven</span><b>"+fmt(last(s.cS),0)+"</b> bp</div>"
      +"<div class='tot m'><span class='lab'>mid (repo x=0)</span><b>"+fmt(last(s.cM),0)+"</b> bp</div>"
      +"<div class='tot'><span class='lab'>config</span><b style='font-size:12px'>"+cfgLab()+"</b></div>"
      +"<div class='tot'><span class='lab'>window</span><b style='font-size:13px'>"+win+(s.dates.length?" ("+s.dates.length+"d)":"")+"</b></div>";
  }
  if(S.view==="chart"){ S.tenors.length>1 ? drawChartMulti() : drawChart(s); } else { drawTable(s); }
}
let _raf=0;                                  // coalesce re-renders to one per animation frame: the
function scheduleRender(){                    // slider handler then returns instantly (so the thumb
  if(_raf) return;                            // tracks the cursor) and the heavy series+chart redraw
  _raf=requestAnimationFrame(()=>{_raf=0; render();});  // runs deferred -- a fast drag no longer
}                                             // backs up the main thread (the stutter / no-drop cursor)
function drawChart(s){
  const tr=(y,name,color)=>({x:s.dates,y:y,name:name,mode:"lines",line:{width:1.6,color:color},hovertemplate:"%{y:+.1f} bp<extra>"+name+"</extra>"});
  const data=[tr(s.cM,"mid","#8b98a5"),tr(s.cL,"long BE","#3fb950"),tr(s.cS,"short BE","#f85149")];
  const layout={paper_bgcolor:"#0f1419",plot_bgcolor:"#0f1419",font:{color:"#e6edf3"},margin:{l:55,r:20,t:30,b:40},
    title:{text:S.tenor+" breakeven cumulative net P&L (bp, linear-sum) — "+cfgLab(),font:{size:14}},
    xaxis:{gridcolor:"#2d3a48"},yaxis:{gridcolor:"#2d3a48",zeroline:true,zerolinecolor:"#3a4a5a",title:"bp"},
    hovermode:"x unified",legend:{orientation:"h",y:1.08}};
  Plotly.react("chart",data,layout,{responsive:true,displaylogo:false});
}
const TCOL={"5y":"#4cc9f0","10y":"#f5a623","30y":"#e63946"};   // tenor colors for overlay
function cumBEFor(tenor){                                       // cumulative LONG- and SHORT-BE (bp; β, repo half-spread & gas hedge applied) over the window
  const d=DATA[tenor], n=d.dates.length; let lo=0,hi=n-1;
  if(S.start){while(lo<n&&d.dates[lo]<S.start)lo++;}
  if(S.end){while(hi>=0&&d.dates[hi]>S.end)hi--;}
  const beta=S.beta/100, gh=gasOn();
  const xs=[],lng=[],sht=[]; let cl=0,cs=0;                     // short = −BE − slip (matches series(): not an exact mirror, the half-spread is a cost both ways)
  for(let i=lo;i<=hi;i++){ const be=d.rT[i]-beta*d.rU[i], slip=S.xT*d.sT[i]+beta*S.xU*d.sU[i];
    let hg=0; if(gh){ const h=hLookup(d.hedge,d.dates[i].slice(0,7),beta); if(h!=null) hg=h*(d.g[i]||0)/1e5; }
    cl+=be-slip-hg; cs+=-be-slip+hg; xs.push(d.dates[i]); lng.push(cl); sht.push(cs); }
  return {xs,long:lng,short:sht};   // at x=0/β=100/gas-off long equals the mid breakeven and short is its exact mirror
}
function drawChartMulti(){                                      // overlay tenors: one long- or short-BE line each (slider-responsive)
  const isS=S.pos==="short", dir=isS?"short":"long";
  const data=S.tenors.map(t=>{ const c=cumBEFor(t);
    return {x:c.xs,y:isS?c.short:c.long,name:t+" "+dir+"-BE",mode:"lines",line:{width:1.7,color:TCOL[t]},hovertemplate:t+" %{y:+.1f} bp<extra></extra>"};});
  Plotly.react("chart",data,{paper_bgcolor:"#0f1419",plot_bgcolor:"#0f1419",font:{color:"#e6edf3"},margin:{l:55,r:20,t:30,b:40},
    title:{text:S.tenors.join(" / ")+" "+dir+" breakeven cumulative (bp) — "+cfgLab()+"; repo half-spread x_TIPS/x_UST applied (set both 0 for mid)",font:{size:14}},
    xaxis:{gridcolor:"#2d3a48"},yaxis:{gridcolor:"#2d3a48",zeroline:true,zerolinecolor:"#3a4a5a",title:"bp"},
    hovermode:"x unified",legend:{orientation:"h",y:1.08}},{responsive:true,displaylogo:false});
}
function median(a){ if(!a.length)return null; const b=a.slice().sort((x,y)=>x-y),n=b.length,h=n>>1; return n%2?b[h]:(b[h-1]+b[h])/2; }
function mean(a){ return a.length? a.reduce((s,x)=>s+x,0)/a.length : null; }
function quantile(a,q){ if(!a.length)return null; const b=a.slice().sort((x,y)=>x-y),pos=(b.length-1)*q,lo=Math.floor(pos); return b[lo]+((b[lo+1]??b[lo])-b[lo])*(pos-lo); }
function poolRange(arrs,pad){ const pool=[]; for(const a of arrs) for(const v of a) pool.push(v); if(pool.length<3)return null;
  pool.sort((x,y)=>x-y); const lo=quantile(pool,0.01),hi=quantile(pool,0.99),p=(hi-lo)*(pad||0.10)||1; return [lo-p,hi+p]; }   // robust y-range (p1..p99) so boxes fill, extreme outliers clip
const MEANMARK={symbol:"line-ew",color:"#ffd633",size:9,line:{width:2,color:"#ffd633"}};   // high-contrast mean tick
// --- shared seasonal filters: sample window(s) Full/5Y/3Y, issue type new/reopen, month/H1/H2 ---
const WINS={"full":0,"5y":60,"3y":36}, WINCOL={"full":"#2f81f7","5y":"#f5a623","3y":"#e63946"}, WINLBL={"full":"Full","5y":"5Y","3y":"3Y"};
function lastYM(){ const s=DATA[S.tenor].seas; let mx=0; for(let i=0;i<s.y.length;i++){const k=s.y[i]*12+s.m[i]; if(k>mx)mx=k;} return mx; }
function winCutOf(w){ return w==="full" ? 0 : lastYM()-(WINS[w]-1); }      // min y*12+m for window w
function winList(){ const l=["full","5y","3y"].filter(w=>S.swins.includes(w)); return l.length?l:["full"]; }
function winMain(){ return winList()[0]; }                                 // longest selected (full>5y>3y): dense views
function sgn(){ return S.pos==="short"?-1:1; }                // short position = mirror the (mid) metric
function dirS(){ return S.pos==="short"?" — SHORT":""; }
// Metric net of the repo half-spread. The half-spread is a financing COST in either direction
// (long pays GC+x, short earns GC−x), so it is subtracted regardless of sgn(); at x=0 long/short
// are exact mirrors. seasMetric works off the bucket-summed slippage (st/su); dayMetric off daily s.
function seasMetric(sd,i){ const beta=S.beta/100; let v,slip;
  const t=sd.t[i],u=sd.u[i],ts=sd.st[i],us=sd.su[i];
  if(S.smetric==="tips"){ v=t; slip=S.xT*(ts||0); }
  else if(S.smetric==="nom"){ v=u; slip=S.xU*(us||0); }
  else { if(t==null||u==null) return null; v=t-beta*u; slip=S.xT*(ts||0)+beta*S.xU*(us||0); }
  if(v==null) return null;
  let hg=0;                                                     // gas hedge (breakeven only): h(β)·Σgas$/100k
  if(S.smetric==="be" && gasOn()){ const ym=sd.y[i]+"-"+String(sd.m[i]).padStart(2,"0");
    const h=hLookup(DATA[S.tenor].hedge, ym, beta); if(h!=null) hg=h*(sd.g[i]||0)/1e5; }
  return sgn()*(v-hg)-slip; }
function dayMetric(d,i){ const beta=S.beta/100; let v,slip;
  if(S.smetric==="tips"){ v=d.rT[i]; slip=S.xT*d.sT[i]; }
  else if(S.smetric==="nom"){ v=d.rU[i]; slip=S.xU*d.sU[i]; }
  else { v=d.rT[i]-beta*d.rU[i]; slip=S.xT*d.sT[i]+beta*S.xU*d.sU[i]; }
  if(v==null) return null;
  let hg=0;
  if(S.smetric==="be" && gasOn()){ const h=hLookup(d.hedge, d.dates[i].slice(0,7), beta); if(h!=null) hg=h*(d.g[i]||0)/1e5; }
  return sgn()*(v-hg)-slip; }
function monthPass(m){ return S.smonths.includes(m); }
function monthLbl(){ const a=S.smonths; if(a.length===12)return "all months"; if(!a.length)return "no months";
  return a.length<=4 ? a.slice().sort((x,y)=>x-y).map(m=>MONTHS[m-1]).join(", ") : a.length+" months"; }
function rowPass(s,i,withMonth,cut){                          // s = DATA[tenor].seas, i = row index, cut = winCutOf(w)
  if(S.issue==="new" && !s.n[i]) return false;
  if(S.issue==="reopen" && s.n[i]) return false;
  if(cut && (s.y[i]*12+s.m[i])<cut) return false;
  if(withMonth && !monthPass(s.m[i])) return false;
  return true;
}
function filtDesc(){ return winList().map(w=>WINLBL[w]).join("+")+" sample"
  + (S.issue==="all"?"":", "+(S.issue==="new"?"new-issue":"reopening")+" months"); }
function seasonalAgg(cut){
  // Pure group-by on the keystone bucket table for one sample window (cut = min y*12+m, 0=full):
  // per (year,month,period) the bucket-SUMMED leg P&L (bp). metric: TIPS=t, Nominal=u, Breakeven=
  // t-(β/100)·u. Stack across YEARS with the median per (month,period); IQR (q25..q75) + n.
  const sd=DATA[S.tenor].seas;
  const valOf=i=>seasMetric(sd,i);    // net of repo half-spread (sliders) + gas hedge (β·BE)
  const byMP={}, byMPy={}, byP={1:[],2:[],3:[],4:[]}, byPy={1:[],2:[],3:[],4:[]}, clMP={};
  for(let i=0;i<sd.y.length;i++){ if(!rowPass(sd,i,false,cut))continue;   // window + issue (not month: this IS the by-month view)
    const v=valOf(i); if(v==null)continue;
    const m=sd.m[i],p=sd.p[i],k=(m-1)*4+(p-1);
    (byMP[k]=byMP[k]||[]).push(v); (byMPy[k]=byMPy[k]||[]).push(sd.y[i]);   // raw values + years (for box plots / hover)
    byP[p].push(v); byPy[p].push(sd.y[i]); if(sd.c[i])clMP[k]=(clMP[k]||0)+1; }
  const med=[[],[],[],[]],q1=[[],[],[],[]],q3=[[],[],[],[]],ns=[[],[],[],[]],clamp=[[],[],[],[]];
  for(let m=0;m<12;m++)for(let p=0;p<4;p++){ const k=m*4+p, arr=byMP[k]||[];
    med[p][m]=median(arr); q1[p][m]=quantile(arr,0.25); q3[p][m]=quantile(arr,0.75);
    ns[p][m]=arr.length; clamp[p][m]=clMP[k]||0; }
  const cum=[]; let c=0;                                       // cumulative seasonal path (Σ medians)
  for(let m=0;m<12;m++)for(let p=0;p<4;p++){ const v=med[p][m]; if(v!=null)c+=v; cum.push(c); }
  const sig=[1,2,3,4].map(p=>median(byP[p])), sq1=[1,2,3,4].map(p=>quantile(byP[p],0.25)),
        sq3=[1,2,3,4].map(p=>quantile(byP[p],0.75)), sn=[1,2,3,4].map(p=>byP[p].length);
  return {med,q1,q3,ns,clamp,cum,sig,sq1,sq3,sn,rawMP:byMP,rawMPy:byMPy,rawP:byP,rawPy:byPy};
}
const MNAME={tips:"TIPS return",nom:"Nominal (UST) return",be:"Breakeven"};
function mlabel(){ return MNAME[S.smetric]+(S.smetric==="be"?" (β="+S.beta+"%"+gasNote()+")":"")+dirS(); }   // metric label + β/gas/dir
function drawSeasonal(){
  const wl=winList(), a=seasonalAgg(winCutOf(winMain()));     // 48-box uses the longest selected window
  // box-and-whisker per (month, period): box=IQR, whiskers=1.5*IQR, solid=median, dashed=mean
  // (boxmean), all year points jittered with hover (year + value). One box trace per period.
  const boxTr=[0,1,2,3].map(p=>{ const xs=[],ys=[],txt=[];
    for(let m=0;m<12;m++){ const k=m*4+p, vals=a.rawMP[k]||[], yrs=a.rawMPy[k]||[];
      for(let j=0;j<vals.length;j++){ xs.push(m*4+p); ys.push(vals[j]); txt.push(yrs[j]); } }
    return {type:"box",name:"P"+(p+1),x:xs,y:ys,text:txt,boxmean:false,boxpoints:"all",jitter:0.5,pointpos:0,
      whiskerwidth:0.4,marker:{color:PCOL[p],size:3,opacity:.4},line:{color:PCOL[p],width:1},fillcolor:PCOL[p]+"22",
      hovertemplate:"%{text} · P"+(p+1)+": %{y:+.2f} bp<extra></extra>"};});
  const mxx=[],myy=[];                                          // bright mean tick per box (high contrast)
  for(let m=0;m<12;m++)for(let p=0;p<4;p++){ const k=m*4+p, arr=a.rawMP[k]||[]; if(arr.length){mxx.push(m*4+p); myy.push(mean(arr));} }
  const meanTr={type:"scatter",mode:"markers",name:"mean",x:mxx,y:myy,marker:MEANMARK,hovertemplate:"mean %{y:+.2f} bp<extra></extra>"};
  const cumTr={type:"scatter",mode:"lines",name:"cumulative (Σ medians)",yaxis:"y2",
    x:a.cum.map((_,i)=>i),y:a.cum,line:{color:"#d6a13a",width:2},hovertemplate:"cumulative %{y:+.1f} bp<extra></extra>"};
  const mname=mlabel();
  const yr=poolRange(Object.values(a.rawMP));
  Plotly.react("seastop",[...boxTr,meanTr,cumTr],{
    paper_bgcolor:"#0f1419",plot_bgcolor:"#0f1419",font:{color:"#e6edf3"},margin:{l:54,r:54,t:36,b:30},boxmode:"overlay",
    title:{text:S.tenor+" — "+mname+" per auction-cycle period (box=IQR, ―median, ┃mean, "+WINLBL[winMain()]+" window)",font:{size:13}},
    xaxis:{tickvals:MONTHS.map((_,m)=>m*4+1.5),ticktext:MONTHS,gridcolor:"#2d3a48",range:[-0.6,47.6]},
    yaxis:{gridcolor:"#2d3a48",zeroline:true,zerolinecolor:"#3a4a5a",title:"period P&L (bp)",range:yr||undefined},
    yaxis2:{overlaying:"y",side:"right",title:"cumulative (bp)",showgrid:false,zeroline:false},
    hovermode:"closest",legend:{orientation:"h",y:1.14,font:{size:10}}},{responsive:true,displaylogo:false});
  // within-month signature: single window -> box per period (gold box-width mean line + points);
  // multiple windows -> side-by-side box-and-whisker per window, grouped by period (native mean line)
  let sigTr, sigLayout={paper_bgcolor:"#0f1419",plot_bgcolor:"#0f1419",font:{color:"#e6edf3"},margin:{l:54,r:20,t:8,b:26},
    yaxis:{gridcolor:"#2d3a48",zeroline:true,zerolinecolor:"#3a4a5a",title:"P&L (bp)"},xaxis:{gridcolor:"#2d3a48"}};
  if(wl.length===1){
    // numeric x at p=1..4 with fixed box width so the mean can be drawn as a line spanning the box
    sigTr=[1,2,3,4].map(p=>({type:"box",name:"P"+p,x:(a.rawP[p]||[]).map(()=>p),y:a.rawP[p]||[],text:a.rawPy[p]||[],
      width:0.5,boxmean:false,boxpoints:"all",jitter:0.6,pointpos:0,marker:{color:PCOL[p-1],size:4,opacity:.45},line:{color:PCOL[p-1],width:1},fillcolor:PCOL[p-1]+"22",
      hovertemplate:"%{text} · P"+p+": %{y:+.3f} bp<extra></extra>"}));
    const mx=[],my=[];                                          // gold mean as a horizontal line the width of the box
    [1,2,3,4].forEach(p=>{ const mv=mean(a.rawP[p]||[]); if(mv!=null){ mx.push(p-0.25,p+0.25,null); my.push(mv,mv,null); } });
    sigTr.push({type:"scatter",mode:"lines",name:"mean",x:mx,y:my,line:{color:"#ffd633",width:3},hovertemplate:"mean %{y:+.3f} bp<extra></extra>"});
    sigLayout.showlegend=false; sigLayout.yaxis.range=poolRange([1,2,3,4].map(p=>a.rawP[p]||[]))||undefined;
    sigLayout.xaxis={tickvals:[1,2,3,4],ticktext:["P1","P2","P3","P4"],gridcolor:"#2d3a48",range:[0.4,4.6]};
  } else {
    const aggs=wl.map(w=>seasonalAgg(winCutOf(w)));            // one box per (window, period), grouped by period
    sigTr=wl.map((w,wi)=>{ const s=aggs[wi], xs=[],ys=[],tx=[];
      [1,2,3,4].forEach(p=>{ const vals=s.rawP[p]||[], yrs=s.rawPy[p]||[]; for(let j=0;j<vals.length;j++){xs.push("P"+p);ys.push(vals[j]);tx.push(yrs[j]);} });
      return {type:"box",name:WINLBL[w],x:xs,y:ys,text:tx,boxmean:true,boxpoints:"all",jitter:0.5,pointpos:0,whiskerwidth:0.4,
        marker:{color:WINCOL[w],size:3,opacity:.4},line:{color:WINCOL[w],width:1},fillcolor:WINCOL[w]+"22",
        hovertemplate:"%{text} · %{x} ["+WINLBL[w]+"]: %{y:+.3f} bp<extra></extra>"};});
    sigLayout.boxmode="group"; sigLayout.showlegend=true; sigLayout.legend={orientation:"h",y:1.2,font:{size:10}};
    const pools=[]; aggs.forEach(s=>[1,2,3,4].forEach(p=>pools.push(s.rawP[p]||[]))); sigLayout.yaxis.range=poolRange(pools)||undefined;
  }
  Plotly.react("seassig",sigTr,sigLayout,{responsive:true,displaylogo:false});
  const desc=S.smetric==="be"?("<b>Breakeven = TIPS − "+S.beta+"%·UST</b> (β=100% is the plain DV01-matched breakeven)")
                             :("<b>"+MNAME[S.smetric]+"</b> leg");
  $("seascap").innerHTML="Showing "+desc+" per auction-cycle period — <b>"+filtDesc()+"</b>. Each <b>box</b> = "
    +"the across-years distribution of that bucket's summed P&L (bp on 100k DV01; ×$100k = $): box = 25–75th "
    +"(IQR), solid line = median, bright-yellow ┃ = <b>mean</b>. The <b>whiskers (the &lsquo;fence&rsquo;)</b> "
    +"reach the furthest year still within <b>1.5×IQR</b> of the nearer quartile (the Tukey fence); any year "
    +"beyond it is a statistical outlier. <b>Every year is plotted as a faint jittered point</b> (hover for "
    +"year + value), so you see the full sample, not just the outliers. Gold line = cumulative Σ-median path. "
    +"Split around the month's single TIPS auction (A0..A4, ±1w)."
    +(winList().length>1?" Signature compares your selected windows (side-by-side box-and-whisker per period).":"");
}
function seasonalHistory(){
  // No aggregation: every (year,month,period) bucket as its own point, optionally filtered to one
  // period and/or one calendar month, returned per period sorted chronologically.
  const sd=DATA[S.tenor].seas, pday=[4,11,18,25], cut=winCutOf(winMain());   // day-of-month to place each period
  const valOf=i=>seasMetric(sd,i);   // net of repo half-spread (sliders) + gas hedge (β·BE)
  const byP={1:[],2:[],3:[],4:[]};
  for(let i=0;i<sd.y.length;i++){
    const p=sd.p[i],m=sd.m[i],y=sd.y[i];
    if(!S.periods.includes(p)) continue;
    if(!rowPass(sd,i,true,cut)) continue;             // window + issue + month
    const v=valOf(i); if(v==null) continue;
    byP[p].push({x:y+"-"+String(m).padStart(2,"0")+"-"+String(pday[p-1]).padStart(2,"0"),v:v});
  }
  for(const p in byP) byP[p].sort((a,b)=>a.x<b.x?-1:1);
  return byP;
}
function drawHistory(){
  const byP=seasonalHistory(), periods=S.periods.slice().sort();
  const single=S.smonths.length<=2;                           // few months -> few pts/yr; markers help
  const traces=[]; let all=[];
  for(const p of periods){
    const arr=byP[p]; all=all.concat(arr.map(o=>o.v));
    traces.push({type:"scatter",mode:single?"lines+markers":"lines",name:"P"+p,connectgaps:true,
      x:arr.map(o=>o.x),y:arr.map(o=>o.v),marker:{color:HCOL[p-1],size:6},line:{color:HCOL[p-1],width:1.5,shape:"linear"},
      hovertemplate:"%{x|%Y-%m} · P"+p+": %{y:+.2f} bp<extra></extra>"});
  }
  const med=median(all);
  const mname=mlabel();
  const perlbl=periods.length===4?"P1–P4":periods.map(p=>"P"+p).join(",")||"none", monthlbl=monthLbl();
  Plotly.react("histchart",traces,{
    paper_bgcolor:"#0f1419",plot_bgcolor:"#0f1419",font:{color:"#e6edf3"},margin:{l:54,r:20,t:36,b:40},
    title:{text:S.tenor+" — "+mname+" per bucket over time ("+perlbl+", "+monthlbl+")",font:{size:13}},
    xaxis:{type:"date",gridcolor:"#2d3a48",dtick:"M12",tickformat:"%Y",ticks:"outside",tickcolor:"#2d3a48",showline:true,linecolor:"#3a4a5a"},
    yaxis:{gridcolor:"#2d3a48",zeroline:true,zerolinecolor:"#3a4a5a",title:"bucket P&L (bp)"},
    hovermode:"closest",legend:{orientation:"h",y:1.13,font:{size:11}},
    shapes:med==null?[]:[{type:"line",xref:"paper",x0:0,x1:1,yref:"y",y0:med,y1:med,line:{color:"#d6a13a",width:1,dash:"dash"}}],
    annotations:med==null?[]:[{xref:"paper",x:1,y:med,yref:"y",text:"median "+med.toFixed(2),showarrow:false,font:{color:"#d6a13a",size:10},xanchor:"right",yanchor:"bottom"}]
  },{responsive:true,displaylogo:false});
  $("histcap").innerHTML="Each point = one month's bucket P&L (summed daily bp"
    +(S.smetric==="be"?", BE = TIPS − "+S.beta+"%·UST":"")+") for <b>"+perlbl+"</b>, "+monthlbl
    +", joined left→right in time (x = year). "+all.length+" buckets; dashed gold = median of the shown selection."
    +" Tick periods + pick a month at left (e.g. P1 only + All months = every month's P1 through time; P3 + Jan = each January's P3).";
}
// ============ Calendar effect: average daily P&L by business-day-of-month ============
function ymNewMap(tenor){ const s=DATA[tenor||S.tenor].seas,mp={}; for(let i=0;i<s.y.length;i++)mp[s.y[i]*12+s.m[i]]=s.n[i]; return mp; }
function seasonalCalendar(cut, tenor){
  // Per-DAY: tag each day with its business-day-of-month (its ordinal among the trading days of its
  // month) and aggregate the metric by that key across the filtered sample. S.calend="end" counts
  // from the LAST trading day (key −1 = last day, −2 = second-last) so month-ends align across months
  // (turn-of-month) instead of smearing at the high forward-BDOMs (months run 19–23 trading days).
  const d=DATA[tenor||S.tenor], nm=ymNewMap(tenor);
  const valOf=i=>dayMetric(d,i);                                 // net of repo half-spread (sliders)
  const hol=i=>d.dd[i]===0;                                     // bond-market holiday (stale d=0 row): not a trading day
  const T={}; for(let i=0;i<d.dates.length;i++){ if(hol(i))continue; const ym=d.dates[i].slice(0,7); T[ym]=(T[ym]||0)+1;}  // real trading days/month
  const byB={}; let curYM="", bd=0;
  for(let i=0;i<d.dates.length;i++){
    if(hol(i)) continue;                                        // skip holidays: don't count in BDOM, don't aggregate
    const ds=d.dates[i], ym=ds.slice(0,7);
    if(ym!==curYM){curYM=ym; bd=0;} bd++;                       // counts only real trading days in the month
    const m=+ds.slice(5,7), key=(+ds.slice(0,4))*12+m;
    if(!monthPass(m)) continue;
    if(S.issue==="new" && !nm[key]) continue;
    if(S.issue==="reopen" && nm[key]) continue;
    if(cut && key<cut) continue;
    const v=valOf(i); if(v==null||isNaN(v)) continue;
    const bk = S.calend==="end" ? (bd - T[ym] - 1) : bd;        // from end: last day -> -1
    (byB[bk]=byB[bk]||[]).push(v);
  }
  const keys=Object.keys(byB).map(Number).sort((a,b)=>a-b);
  const med=keys.map(b=>median(byB[b])), q1=keys.map(b=>quantile(byB[b],0.25)),
        q3=keys.map(b=>quantile(byB[b],0.75)), ns=keys.map(b=>byB[b].length);
  let c=0; const cum=med.map(v=>{c+=(v||0); return c;});
  return {keys,med,q1,q3,ns,cum,raw:byB};
}
function drawCalendar(){
  const wl=winList(), mname=mlabel();
  const xtitle = S.calend==="end" ? "business day from month-end  (−1 = last trading day)"
                                  : "business day of month  (1 = first trading day)";
  let traces, layout={paper_bgcolor:"#0f1419",plot_bgcolor:"#0f1419",font:{color:"#e6edf3"},margin:{l:54,r:54,t:36,b:42},
    title:{text:S.tenor+" — "+mname+" by "+(S.calend==="end"?"day-from-month-end":"business day of month")+(S.tenors.length>1||wl.length>1?" (median lines)":" (box=IQR, ┃mean)"),font:{size:13}},
    xaxis:{title:xtitle,gridcolor:"#2d3a48",dtick:1},
    yaxis:{title:"median daily P&L (bp)",gridcolor:"#2d3a48",zeroline:true,zerolinecolor:"#3a4a5a"},
    hovermode:"closest",legend:{orientation:"h",y:1.12,font:{size:10}}};
  if(S.tenors.length>1){                                        // overlay TENORS (primary window): one median line each
    traces=S.tenors.map(t=>{ const a=seasonalCalendar(winCutOf(winMain()), t);
      return {type:"scatter",mode:"lines+markers",name:t,x:a.keys,y:a.med,line:{color:TCOL[t],width:1.7},marker:{size:4,color:TCOL[t]},
        customdata:a.ns,hovertemplate:"day %{x} ["+t+"]: median %{y:+.3f} bp (n=%{customdata})<extra></extra>"};});
  } else if(wl.length===1){                                     // single tenor + window: box per day + mean + cumulative
    const a=seasonalCalendar(winCutOf(wl[0])); const xs=[],ys=[];
    for(const k of a.keys) for(const v of a.raw[k]){ xs.push(k); ys.push(v); }
    traces=[{type:"box",name:"daily",x:xs,y:ys,boxmean:false,boxpoints:"outliers",whiskerwidth:0.4,
        marker:{color:"#2f81f7",size:3,opacity:.4},line:{color:"#2f81f7",width:1},fillcolor:"#2f81f733",
        hovertemplate:"day %{x}: %{y:+.3f} bp<extra></extra>"},
      {type:"scatter",mode:"markers",name:"mean",x:a.keys,y:a.keys.map(k=>mean(a.raw[k])),marker:MEANMARK,hovertemplate:"mean %{y:+.3f} bp<extra></extra>"},
      {type:"scatter",mode:"lines",name:"cumulative",yaxis:"y2",x:a.keys,y:a.cum,line:{color:"#d6a13a",width:2},hovertemplate:"cumulative %{y:+.2f} bp<extra></extra>"}];
    layout.boxmode="overlay"; layout.yaxis.range=poolRange(Object.values(a.raw))||undefined;
    layout.yaxis2={overlaying:"y",side:"right",title:"cumulative (bp)",showgrid:false,zeroline:false};
  } else {                                                      // single tenor, multiple windows: one median line each
    traces=wl.map(w=>{ const a=seasonalCalendar(winCutOf(w));
      return {type:"scatter",mode:"lines+markers",name:WINLBL[w],x:a.keys,y:a.med,line:{color:WINCOL[w],width:1.6},marker:{size:4,color:WINCOL[w]},
        customdata:a.ns,hovertemplate:"day %{x} ["+WINLBL[w]+"]: median %{y:+.3f} bp (n=%{customdata})<extra></extra>"};});
  }
  Plotly.react("seascalchart",traces,layout,{responsive:true,displaylogo:false});
  $("calcap").innerHTML="The <b>calendar effect</b>: median daily P&L by <b>"+(S.calend==="end"?"day from month-end":"business day of month")
    +"</b>, independent of the auction cycle — <b>"+filtDesc()+"</b>, "+monthLbl()
    +(S.tenors.length>1?". One line per <b>tenor</b> ("+WINLBL[winMain()]+" window)."
        :wl.length===1?". Box = IQR, ┃ = <b>mean</b>, ― = median; gold = cumulative within-month path."
                   :". One line per sample window (compare degradation).")
    +" Note x = trading days (≈19–23/month), not calendar dates; use From-end to align the turn-of-month.";
}
// ============ Predict: OLS regressions between within-month periods ============
function rowVal(s,i){ return seasMetric(s,i); }   // net of repo half-spread (sliders) + gas hedge (β·BE)
function monthBuckets(cut, withFilters){                        // {ym:{1,2,3,4}}; withFilters=issue+month, else window-only
  const s=DATA[S.tenor].seas, out={};
  for(let i=0;i<s.y.length;i++){
    if(withFilters ? !rowPass(s,i,true,cut) : (cut && (s.y[i]*12+s.m[i])<cut)) continue;
    const v=rowVal(s,i); if(v==null)continue;
    const ym=s.y[i]*12+s.m[i]; (out[ym]=out[ym]||{})[s.p[i]]=v; }
  return out;
}
const REGS=[                                                    // cross:true -> target is NEXT month's bucket
  {key:"P1>P2",lab:"P1 → P2",fx:b=>b[1],fy:b=>b[2]},
  {key:"P2>P3",lab:"P2 → P3",fx:b=>b[2],fy:b=>b[3]},
  {key:"P3>P4",lab:"P3 → P4",fx:b=>b[3],fy:b=>b[4]},
  {key:"P23>P4",lab:"P2+P3 → P4",fx:b=>b[2]+b[3],fy:b=>b[4]},
  {key:"P12>P3",lab:"P1+P2 → P3",fx:b=>b[1]+b[2],fy:b=>b[3]},
  {key:"P12>P34",lab:"P1+P2 → P3+P4",fx:b=>b[1]+b[2],fy:b=>b[3]+b[4]},
  {key:"P4>P1n",lab:"P4 → next-month P1",cross:true,fx:b=>b[4],fy:b=>b[1]},
];
function ols(xs,ys){
  const n=xs.length; if(n<3)return null;
  let sx=0,sy=0,sxx=0,sxy=0,syy=0;
  for(let i=0;i<n;i++){sx+=xs[i];sy+=ys[i];sxx+=xs[i]*xs[i];sxy+=xs[i]*ys[i];syy+=ys[i]*ys[i];}
  const mx=sx/n,my=sy/n,ssxx=sxx-n*mx*mx,ssxy=sxy-n*mx*my,ssyy=syy-n*my*my;
  if(ssxx<=0||ssyy<=0)return null;
  const b=ssxy/ssxx,a=my-b*mx,r=ssxy/Math.sqrt(ssxx*ssyy),r2=r*r;
  const sse=Math.max(ssyy-b*ssxy,0),seb=Math.sqrt(sse/Math.max(n-2,1)/ssxx),t=seb>0?b/seb:0;
  return {n,a,b,r,r2,t};
}
function regResults(cut){
  const mbF=monthBuckets(cut,true), mbW=monthBuckets(cut,false);   // filtered (predictor) + window-only (next-month target)
  const res=REGS.map(R=>{ const xs=[],ys=[];
    for(const ym in mbF){ const b=mbF[ym], tb=R.cross?mbW[(+ym)+1]:b;   // cross: target = next calendar month
      if(!tb) continue;
      const xv=R.fx(b), yv=R.fy(tb);
      if(xv==null||yv==null||isNaN(xv)||isNaN(yv))continue; xs.push(xv); ys.push(yv); }
    return {key:R.key,lab:R.lab,xs,ys,f:ols(xs,ys)}; });
  // month -> next month: each month's TOTAL P&L (P1+P2+P3+P4) predicts the next month's total.
  // Adjacency follows the FILTERED set (sorted), so Issue=New gives Jan->Feb->Apr->Jul->Oct->Jan...
  const yms=Object.keys(mbF).map(Number).sort((a,b)=>a-b);
  const tot=ym=>{const b=mbF[ym]; return (b[1]||0)+(b[2]||0)+(b[3]||0)+(b[4]||0);};
  const xs=[],ys=[];
  for(let i=0;i+1<yms.length;i++){ xs.push(tot(yms[i])); ys.push(tot(yms[i+1])); }
  res.push({key:"M>Mn",lab:"month → next month",xs,ys,f:ols(xs,ys)});
  return res;
}
function regTable(res){
  let h="<table><thead><tr><th>relationship</th><th>n</th><th>slope</th><th>R²</th><th>corr</th><th>t-stat</th></tr></thead><tbody>";
  for(const r of res){ const f=r.f;
    h+="<tr><td>"+r.lab+"</td>"+(f?("<td>"+f.n+"</td><td>"+f.b.toFixed(3)+"</td><td>"+f.r2.toFixed(3)
      +"</td><td>"+f.r.toFixed(3)+"</td><td class='"+(Math.abs(f.t)>2?"pos":"")+"'>"+f.t.toFixed(2)+"</td>")
      :"<td colspan='5'>n/a (need ≥3 months)</td>")+"</tr>"; }
  return h+"</tbody></table>";
}
function drawPredict(){
  const wl=winList();
  $("regtablewrap").innerHTML = wl.map(w=>
    (wl.length>1?"<div class='note' style='padding:2px 0 0'><b style='color:"+WINCOL[w]+"'>"+WINLBL[w]+" window</b></div>":"")
    + regTable(regResults(winCutOf(w)))).join("");
  const res=regResults(winCutOf(winMain())), sel=res.find(r=>r.key===S.regpair)||res[0], f=sel.f;
  const traces=[{type:"scatter",mode:"markers",name:"months",x:sel.xs,y:sel.ys,
    marker:{color:"#2f81f7",size:6,opacity:.7},hovertemplate:"predictor %{x:+.2f} → target %{y:+.2f} bp<extra></extra>"}];
  if(f){ const xmn=Math.min(...sel.xs),xmx=Math.max(...sel.xs);
    traces.push({type:"scatter",mode:"lines",name:"OLS fit",x:[xmn,xmx],y:[f.a+f.b*xmn,f.a+f.b*xmx],line:{color:"#f5a623",width:2}}); }
  const mname=mlabel();
  Plotly.react("regchart",traces,{
    paper_bgcolor:"#0f1419",plot_bgcolor:"#0f1419",font:{color:"#e6edf3"},margin:{l:54,r:20,t:34,b:42},
    title:{text:sel.lab+"   ("+mname+", "+WINLBL[winMain()]+" window"+(S.smonths.length===12?"":", "+monthLbl())+")"+(f?"   slope "+f.b.toFixed(2)+", R² "+f.r2.toFixed(2)+", t "+f.t.toFixed(1)+", n "+f.n:""),font:{size:13}},
    xaxis:{title:"predictor (bp)",gridcolor:"#2d3a48",zeroline:true,zerolinecolor:"#3a4a5a"},
    yaxis:{title:"target (bp)",gridcolor:"#2d3a48",zeroline:true,zerolinecolor:"#3a4a5a"},
    hovermode:"closest",showlegend:false},{responsive:true,displaylogo:false});
  $("regcap").innerHTML="Does early-month performance predict later? Each point = one month ("+filtDesc()
    +(S.smonths.length===12?"":", "+monthLbl())+"); OLS of target on predictor, bucket P&L in bp. "
    +"<b>P4 → next-month P1</b> is cross-month (this month's P4 vs the <i>following</i> month's P1; filters apply to the P4 month). "
    +"|t|&gt;2 (~5% significance) highlighted green. Pick which relationship to plot at left; with multiple windows "
    +"the table compares them (scatter shows the "+WINLBL[winMain()]+" window). Month / Issue / Sample condition the regression.";
}
// ============ Cumul: filtered-slice cumulative P&L + performance metrics ============
function drawCumul(){
  const cut=winCutOf(winMain()), mb=monthBuckets(cut,true), periods=S.periods.slice().sort();
  const yms=Object.keys(mb).map(Number).sort((a,b)=>a-b);
  const plabel=periods.length===4?"P1–P4":periods.map(p=>"P"+p).join("+")||"none";
  const mname=mlabel();
  const labels=[], slices=[], cum=[]; let c=0;                  // one slice per filtered month = Σ selected periods
  for(const ym of yms){ const b=mb[ym]; let v=0,has=false;
    for(const p of periods){ if(b[p]!=null){ v+=b[p]; has=true; } }
    if(!has) continue;
    const y=Math.floor((ym-1)/12), m=ym-y*12;
    labels.push(y+"-"+String(m).padStart(2,"0")); slices.push(v); c+=v; cum.push(c); }
  const tickvals=[],ticktext=[]; let py="";                    // one tick per year (first kept slice of each year)
  for(const lab of labels){ const yr=lab.slice(0,4); if(yr!==py){tickvals.push(lab); ticktext.push(yr); py=yr;} }
  const n=slices.length, total=c, mu=mean(slices)||0;
  let varr=0; for(const v of slices) varr+=(v-mu)*(v-mu); const vol=n>1?Math.sqrt(varr/(n-1)):0;
  const spanY=n>1?(yms[yms.length-1]-yms[0])/12:1, spy=spanY>0?n/spanY:12;   // slices per year (for annualizing)
  const sharpe=vol>0?mu/vol*Math.sqrt(spy):0;
  let peak=-1e18,mdd=0; for(const v of cum){ if(v>peak)peak=v; if(peak-v>mdd)mdd=peak-v; }
  const f1=x=>(x>=0?"+":"")+x.toFixed(1);
  const met=n?[{l:"slices",v:String(n),c:""},{l:"total",v:f1(total)+" bp",c:total>=0?"pos":"neg"},
    {l:"mean / slice",v:(mu>=0?"+":"")+mu.toFixed(2)+" bp",c:mu>=0?"pos":"neg"},{l:"vol / slice",v:vol.toFixed(2)+" bp",c:""},
    {l:"Sharpe (ann)",v:sharpe.toFixed(2),c:sharpe>=0?"pos":"neg",s:"×√"+Math.round(spy)+"/yr"},
    {l:"max drawdown",v:"−"+mdd.toFixed(1)+" bp",c:"neg"}]:[];
  $("cummetrics").innerHTML=met.map(m=>"<div class='met'><b class='"+m.c+"'>"+m.v+"</b>"+m.l+(m.s?" <span>"+m.s+"</span>":"")+"</div>").join("");
  Plotly.react("cumchart",[{type:"scatter",mode:"lines",x:labels,y:cum,line:{color:"#3fb950",width:2},
      hovertemplate:"%{x}: cum %{y:+.1f} bp<extra></extra>"}],{
    paper_bgcolor:"#0f1419",plot_bgcolor:"#0f1419",font:{color:"#e6edf3"},margin:{l:55,r:20,t:34,b:62},
    title:{text:S.tenor+" — cumulative "+mname+", "+plabel+" slices ("+filtDesc()+", "+monthLbl()+")",font:{size:13}},
    xaxis:{type:"category",gridcolor:"#2d3a48",tickvals:tickvals,ticktext:ticktext,tickangle:0},
    yaxis:{gridcolor:"#2d3a48",zeroline:true,zerolinecolor:"#3a4a5a",title:"cumulative P&L (bp)"},
    hovermode:"closest",showlegend:false},{responsive:true,displaylogo:false});
  $("cumcap").innerHTML=(periods.length?"":"<b>Select ≥1 period at left.</b> ")
    +"Cumulative P&L holding only the <b>"+plabel+"</b> slice each month ("+filtDesc()+", "+monthLbl()
    +"). The x-axis lists <b>only the kept months</b> in sequence (so it jumps over excluded ones — e.g. Issue=New + P1 = "
    +"each new-issue month's P1: 2011-01, 2011-02, 2011-04, …). Metrics are on the per-slice returns; Sharpe is annualized by the slice frequency.";
}
// ============ Event: returns aligned at the auction (day 0), ±N business days ============
function drawEvent(){
  const d=DATA[S.tenor], N=S.evn;
  const idx={}; for(let i=0;i<d.dates.length;i++) idx[d.dates[i]]=i;
  const val=i=>dayMetric(d,i);                                   // net of repo half-spread (sliders)
  const offs=[]; for(let k=-N;k<=N;k++) offs.push(k);
  const ref=offs.map(()=>[]);                                  // per-offset values across ALL filtered auctions (median/mean)
  const curves=[]; let nAuc=0;
  for(const a of d.aucts){
    const y=+a.d.slice(0,4), m=+a.d.slice(5,7);
    if(S.issue==="new"&&!a.n) continue; if(S.issue==="reopen"&&a.n) continue; if(!monthPass(m)) continue;
    const ai=idx[a.d]; if(ai==null) continue;
    const raw=offs.map(k=>{ const i=ai+k; return (i>=1&&i<d.dates.length)?val(i):null; });
    let cc=0; const cum=raw.map(v=>v==null?null:(cc+=v,cc));    // cumulate over the window
    const base=cum[N]; if(base==null) continue;                // rebase to 0 on the auction day (offset 0)
    const reb=cum.map(v=>v==null?null:v-base);
    curves.push({y,m,reb}); reb.forEach((v,j)=>{ if(v!=null) ref[j].push(v); }); nAuc++;
  }
  const traces=[];
  for(const cv of curves){                                     // individual auction lines
    const sel = S.evyear==="all" || cv.y===+S.evyear;
    if(S.evyear!=="all" && !sel) continue;                     // a specific year -> only that year's lines
    const faint = S.evyear==="all";
    traces.push({type:"scatter",mode:"lines",x:offs,y:cv.reb,name:cv.y+"-"+String(cv.m).padStart(2,"0"),showlegend:!faint,
      line:{width:faint?0.6:1.7,color:faint?"#8b98a5":undefined},opacity:faint?0.16:0.95,
      hovertemplate:cv.y+"-"+String(cv.m).padStart(2,"0")+"  day %{x}: %{y:+.2f} bp<extra></extra>"});
  }
  traces.push({type:"scatter",mode:"lines",name:"median (all yrs)",x:offs,y:ref.map(a=>median(a)),line:{color:"#ffd633",width:3.5},hovertemplate:"median day %{x}: %{y:+.2f} bp<extra></extra>"});
  traces.push({type:"scatter",mode:"lines",name:"mean (all yrs)",x:offs,y:ref.map(a=>mean(a)),line:{color:"#e6edf3",width:2.5,dash:"dot"},hovertemplate:"mean day %{x}: %{y:+.2f} bp<extra></extra>"});
  const mname=mlabel();
  Plotly.react("evchart",traces,{
    paper_bgcolor:"#0f1419",plot_bgcolor:"#0f1419",font:{color:"#e6edf3"},margin:{l:56,r:20,t:34,b:42},
    title:{text:S.tenor+" — "+mname+" around the auction (event-aligned; day 0 = auction)",font:{size:13}},
    xaxis:{title:"business days from auction (0 = auction day)",gridcolor:"#2d3a48",zeroline:false,dtick:5},
    yaxis:{title:"cumulative P&L, rebased to 0 at auction (bp)",gridcolor:"#2d3a48",zeroline:true,zerolinecolor:"#3a4a5a"},
    hovermode:"closest",legend:{orientation:"h",y:1.12,font:{size:10}},
    shapes:[{type:"line",x0:0,x1:0,yref:"paper",y0:0,y1:1,line:{color:"#8b98a5",width:1,dash:"dot"}}]},{responsive:true,displaylogo:false});
  $("evcap").innerHTML="Every auction aligned at <b>day 0</b>; each line = that auction's cumulative "+mname
    +" rebased to 0 on the auction day, ±"+N+" business days. "
    +(S.evyear==="all"?"Faint grey = every auction":"Coloured = <b>"+S.evyear+"</b> auctions")
    +"; <b>thick gold = median</b>, dotted white = mean across all "+nAuc+" "
    +(S.issue==="all"?"":(S.issue==="new"?"new-issue ":"reopening "))+"auctions ("+monthLbl()+"). "
    +"Lines above/below the median are out/under-performers that cycle. Widen ± with the Window slider; pick a year at left.";
}
function drawTable(s){
  const cols=["period","TIPS","UST","BEmid","longBE","shortBE"];
  let rows, tot=[0,0,0,0,0];
  if(S.freq==="monthly"){ rows=monthly(s); rows.forEach(r=>{for(let k=0;k<5;k++)tot[k]+=r[k+1];}); }
  else {
    rows=s.dates.map((dt,i)=>[dt,s.rT[i],s.rU[i],s.rBE[i],s.lBE[i],s.sBE[i]]);
    rows.forEach(r=>{for(let k=0;k<5;k++)tot[k]+=r[k+1];});
  }
  const cap=1500, note=$("tnote"); let shown=rows;
  if(rows.length>cap){ shown=rows.slice(rows.length-cap); note.textContent="showing last "+cap+" of "+rows.length+" rows — narrow the date range or use Monthly."; }
  else note.textContent="";
  let h="<thead><tr>"+cols.map(c=>"<th>"+c+"</th>").join("")+"</tr></thead><tbody>";
  for(const r of shown){
    h+="<tr><td>"+r[0]+"</td>"+r.slice(1).map(v=>"<td class='"+cls(v)+"'>"+fmt(v,1)+"</td>").join("")+"</tr>";
  }
  h+="<tr class='total'><td>TOTAL</td>"+tot.map(v=>"<td class='"+cls(v)+"'>"+fmt(v,1)+"</td>").join("")+"</tr></tbody>";
  $("tbl").innerHTML=h;
}
function downloadCSV(){
  const s=series();
  let csv="date,TIPS_bp,UST_bp,BEmid_bp,longBE_bp,shortBE_bp\n";
  for(let i=0;i<s.dates.length;i++) csv+=[s.dates[i],s.rT[i],s.rU[i],s.rBE[i].toFixed(4),s.lBE[i].toFixed(4),s.sBE[i].toFixed(4)].join(",")+"\n";
  const a=document.createElement("a");
  a.href=URL.createObjectURL(new Blob([csv],{type:"text/csv"}));
  a.download="breakeven_"+S.tenor+"_b"+S.beta+(gasOn()?"_gas"+S.gwin:"")+"_xT"+S.xT+"_xU"+S.xU+".csv"; a.click();
}
// ---- wire controls ----
function seg(id,key,after){ $(id).querySelectorAll("button").forEach(b=>b.onclick=()=>{
  S[key]=b.dataset[key]; $(id).querySelectorAll("button").forEach(x=>x.classList.toggle("on",x===b)); (after||render)(); });}
const tenWrap=$("tenor"); TENORS.forEach(t=>{const b=document.createElement("button");b.textContent=t;b.dataset.tenor=t;
  if(S.tenors.includes(t))b.classList.add("on"); tenWrap.appendChild(b);});
function syncTenorBtns(){ tenWrap.querySelectorAll("button").forEach(x=>x.classList.toggle("on",S.tenors.includes(x.dataset.tenor))); }
tenWrap.querySelectorAll("button").forEach(b=>b.onclick=()=>{ const t=b.dataset.tenor;
  if(overlayOK()){                                                                         // Chart / seasonal Calendar: multi-toggle (>=1 stays on)
    if(S.tenors.includes(t)){ if(S.tenors.length>1) S.tenors=S.tenors.filter(x=>x!==t); }
    else S.tenors=TENORS.filter(x=>S.tenors.includes(x)||x===t);                           // add, keep tenor order
  } else S.tenors=[t];                                                                      // every other view: single-select only
  S.tenor=S.tenors[0];                                                                      // primary = first selected
  syncTenorBtns(); render();});
seg("view","view"); seg("freq","freq"); seg("smetric","smetric"); seg("seasmode","seasmode");
seg("issue","issue"); seg("calend","calend"); seg("pos","pos"); seg("gas","gas"); seg("gwin","gwin");
$("speriod").querySelectorAll("input").forEach(cb=>cb.onchange=()=>{
  S.periods=[...$("speriod").querySelectorAll("input:checked")].map(x=>+x.value); render(); });
$("swin").querySelectorAll("input").forEach(cb=>cb.onchange=()=>{
  S.swins=[...$("swin").querySelectorAll("input:checked")].map(x=>x.value); render(); });
$("xt").oninput=e=>{S.xT=+e.target.value; $("xtv").textContent=S.xT.toFixed(1); scheduleRender();};
$("xu").oninput=e=>{S.xU=+e.target.value; $("xuv").textContent=S.xU.toFixed(1); scheduleRender();};
$("beta").oninput=e=>{S.beta=+e.target.value; $("betav").textContent=S.beta; scheduleRender();};
$("smonths").innerHTML=MONTHS.map((mm,i)=>"<label><input type='checkbox' value='"+(i+1)+"' checked> "+mm+"</label>").join("");
$("smonths").querySelectorAll("input").forEach(cb=>cb.onchange=()=>{
  S.smonths=[...$("smonths").querySelectorAll("input:checked")].map(x=>+x.value); render(); });
$("mquick").querySelectorAll("button").forEach(b=>b.onclick=()=>{ const q=b.dataset.mq;
  S.smonths=q==="all"?[1,2,3,4,5,6,7,8,9,10,11,12]:q==="h1"?[1,2,3,4,5,6]:q==="h2"?[7,8,9,10,11,12]:[];
  $("smonths").querySelectorAll("input").forEach(cb=>cb.checked=S.smonths.includes(+cb.value)); render(); });
$("regpair").onchange=e=>{S.regpair=e.target.value; render();};
$("evn").oninput=e=>{S.evn=+e.target.value; $("evnv").textContent=S.evn; scheduleRender();};
$("evyear").onchange=e=>{S.evyear=e.target.value; render();};
(function(){ const ys=[...new Set((DATA[S.tenor].aucts||[]).map(a=>a.d.slice(0,4)))].sort();   // populate event-year dropdown
  $("evyear").innerHTML="<option value='all'>All years</option>"+ys.map(y=>"<option value='"+y+"'>"+y+"</option>").join("");
  S.evyear=ys.length?ys[ys.length-1]:"all"; $("evyear").value=S.evyear; })();
function setRangeBtn(name){ document.querySelectorAll("#range button").forEach(x=>x.classList.toggle("on", x.dataset.range===name)); }
$("start").onchange=e=>{S.start=e.target.value||null; setRangeBtn(null); render();};
$("end").onchange=e=>{S.end=e.target.value||null; setRangeBtn(null); render();};
$("dl").onclick=downloadCSV;
document.querySelectorAll("[data-range]").forEach(b=>b.onclick=()=>{
  const all=DATA[S.tenor].dates, lastD=all[all.length-1]; let s=null;
  if(b.dataset.range==="full") s=null;
  else { const yrs=+b.dataset.range.replace("y",""); const d=new Date(lastD); d.setFullYear(d.getFullYear()-yrs); s=d.toISOString().slice(0,10); }   // 5Y / 3Y
  S.start=s; S.end=null; $("start").value=s||""; $("end").value=""; setRangeBtn(b.dataset.range); render();
});
setRangeBtn("full");   // default view window is full history
render();
</script></body></html>
"""


def build(path=None, open_browser=True):
    path = path or os.path.join(HERE, "dashboard.html")
    html = (HTML
            .replace("__PLOTLY__", plotly_tag())
            .replace("__DATA__", json.dumps(build_payload(), separators=(",", ":"))))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    mb = os.path.getsize(path) / 1e6
    print(f"  wrote {path}  ({mb:.1f} MB)")
    if open_browser:
        try:
            webbrowser.open("file://" + path.replace("\\", "/"))
        except Exception:
            pass
    return path


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    engine.refresh(update_data="--no-update" not in sys.argv)   # fresh data + returns first
    build(open_browser="--no-open" not in sys.argv)
