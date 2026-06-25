"""
Build a self-contained interactive HTML dashboard for the breakeven return series.

Why HTML: the repo bid/offer effect (and the seasonal β-hedge) is LINEAR in its parameter, so
every recomputation (slider move, date range, tenor switch, β) is a trivial client-side vector
op -> instant, no server, no lag. Plotly.js for charts (zoom/pan/hover), native HTML date
pickers + range sliders, a clean CSS layout, a sortable raw-numbers table (daily or monthly),
live net P&L, and a one-click CSV download of the current window.

Three views (top-left toggle):
  * Chart  -- cumulative long/short/mid breakeven net P&L (the repo half-spread sliders).
  * Table  -- the same, as daily or monthly rows with a window total.
  * Seasonal -- auction-cycle & calendar analysis (engine.seasonal_table). Every month is split
    into 4 periods around its single TIPS auction (A0..A4, ±1 week; shared monthly anchor across
    all three tenors). Four sub-modes (toggle):
      - Aggregate: 4 bars/calendar-month (median across years, 25-75 IQR, n) + cumulative seasonal
        path + a "within-month signature" (each period pooled across months -> the pure cycle).
      - History: each (year,month,period) bucket as a point over time (high-contrast lines, x=year);
        period CHECKBOXES + Month filter (e.g. P1 all months = every P1 over time).
      - Calendar: the CALENDAR effect -- median daily P&L by business-day-of-month (turn-of-month
        etc.), independent of the auction cycle; Month filter to isolate one month.
      - Predict: OLS regressions P1->P2, P2->P3, P3->P4, (P1+P2)->P3, (P1+P2)->(P3+P4) across months
        (slope, R^2, corr, t-stat, n; |t|>2 flagged) + a scatter -- does early-month predict later?
    Shared controls: Metric TIPS / Nominal / Breakeven (β slider on Breakeven, = TIPS − β·UST,
    β=100% plain, default 75%); Sample window Full / 5Y / 3Y; Issue type All / New / Reopen
    (new-issue vs reopening months). All aggregation is client-side off the shipped seas table, so
    every control is instant. Units = engine bp (= $/100k-DV01 P&L; ×$100k = dollars).

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

HERE = os.path.dirname(os.path.abspath(__file__))
TENORS = ["5y", "10y", "30y"]
PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"


def build_payload():
    """Per tenor: daily leg returns (for Chart/Table) + the pre-bucketed seasonal keystone table
    (engine.seasonal_table -> rows of year,month,period with the bucket-SUMMED leg P&L). The
    Seasonal view aggregates this client-side — median across years, IQR band, n — as the
    tenor/metric/β change. Empty/clamped buckets ship as null and are excluded from the median."""
    st = engine.seasonal_table(save=True)
    data = {}
    for t in TENORS:
        p = os.path.join(engine.CACHE, f"returns_{t}.parquet")
        if not os.path.exists(p):
            continue
        d = engine.load_returns(t).dropna(subset=["r_BE_bp"])
        s = st[st["tenor"] == t]
        nn = lambda col: [None if pd.isna(v) else round(float(v), 4) for v in s[col]]
        data[t] = {
            "dates": [x.strftime("%Y-%m-%d") for x in d.index],
            "rT": [round(float(v), 4) for v in d["r_TIPS_bp"]],
            "rU": [round(float(v), 4) for v in d["r_UST_bp"]],
            "rBE": [round(float(v), 4) for v in d["r_BE_bp"]],
            "sT": [round(float(v), 6) for v in d["s_TIPS"]],
            "sU": [round(float(v), 6) for v in d["s_UST"]],
            "seas": {                                  # keystone bucket table (this tenor)
                "y": [int(v) for v in s["year"]], "m": [int(v) for v in s["month"]],
                "p": [int(v) for v in s["period"]], "t": nn("tips_pnl"), "u": nn("ust_pnl"),
                "d": [int(v) for v in s["trading_days"]], "c": [bool(v) for v in s["clamped"]],
                "n": [bool(v) for v in s["new_issue"]],   # new-issue (vs reopening) month
            },
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
  #seasmode.sm4 button{font-size:11px;padding:7px 2px}
  #seascal,#seasreg{flex:1;min-height:0;display:flex;flex-direction:column;padding:0 10px 8px}
  #seascalchart,#regchart{flex:1;min-height:0}
  #regtablewrap{padding:4px 0 8px}
  #regtablewrap table{width:auto;font-variant-numeric:tabular-nums;font-size:12px}
  #regtablewrap th,#regtablewrap td{padding:3px 14px;text-align:right;border-bottom:1px solid var(--line);position:static}
  #regtablewrap td:first-child,#regtablewrap th:first-child{text-align:left}
</style></head>
<body><div class="app">
  <div class="side">
    <h1>Breakeven financed TR</h1>
    <div class="grp"><label>Tenor</label><div class="seg" id="tenor"></div></div>
    <div class="grp rv"><label>Repo half-spread x_TIPS <span class="hl" id="xtv">3.0</span> bp</label>
      <input type="range" id="xt" min="0" max="25" step="0.5" value="3"></div>
    <div class="grp rv"><label>Repo half-spread x_UST <span class="hl" id="xuv">3.0</span> bp</label>
      <input type="range" id="xu" min="0" max="25" step="0.5" value="3"></div>
    <div class="grp rv"><label>Date range</label>
      <div class="row"><input type="date" id="start"></div>
      <div class="row"><input type="date" id="end"></div>
      <div class="seg" id="range" style="margin-top:6px"><button data-range="full">Full</button>
        <button data-range="5y">5y</button><button data-range="1y">1y</button><button data-range="ytd">YTD</button></div></div>
    <div class="grp"><label>View</label><div class="seg" id="view">
      <button data-view="chart" class="on">Chart</button><button data-view="table">Table</button>
      <button data-view="seasonal">Seasonal</button></div></div>
    <div class="grp rv"><label>Table frequency</label><div class="seg" id="freq">
      <button data-freq="monthly" class="on">Monthly</button><button data-freq="daily">Daily</button></div></div>
    <div class="grp sv" style="display:none"><label>Seasonal metric</label><div class="seg" id="smetric">
      <button data-smetric="tips" class="on">TIPS</button><button data-smetric="nom">Nominal</button>
      <button data-smetric="be">Breakeven</button></div></div>
    <div class="grp sv" id="betagrp" style="display:none"><label>Beta (β) <span class="hl" id="betav">75</span>%
      &nbsp;<span class="note" style="padding:0">BE = TIPS − β·UST (β=100 plain)</span></label>
      <input type="range" id="beta" min="0" max="150" step="5" value="75"></div>
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
      <button data-seasmode="cal">Calendar</button><button data-seasmode="reg">Predict</button></div></div>
    <div class="grp hv" style="display:none"><label>Periods (auction cycle)</label>
      <div class="checks" id="speriod">
        <label><input type="checkbox" value="1" checked><span class="sw" style="background:#4cc9f0"></span><b>P1</b><span class="rng">m/e → auction−1w</span></label>
        <label><input type="checkbox" value="2" checked><span class="sw" style="background:#e63946"></span><b>P2</b><span class="rng">auction−1w → auction</span></label>
        <label><input type="checkbox" value="3" checked><span class="sw" style="background:#52b788"></span><b>P3</b><span class="rng">auction → auction+1w</span></label>
        <label><input type="checkbox" value="4" checked><span class="sw" style="background:#c77dff"></span><b>P4</b><span class="rng">auction+1w → m/e</span></label>
      </div></div>
    <div class="grp mv" style="display:none"><label>Month</label>
      <select id="smonth"><option value="all">All months</option><option value="h1">H1 (Jan–Jun)</option><option value="h2">H2 (Jul–Dec)</option><option value="1">Jan</option><option value="2">Feb</option>
        <option value="3">Mar</option><option value="4">Apr</option><option value="5">May</option><option value="6">Jun</option>
        <option value="7">Jul</option><option value="8">Aug</option><option value="9">Sep</option><option value="10">Oct</option>
        <option value="11">Nov</option><option value="12">Dec</option></select></div>
    <div class="grp pv" style="display:none"><label>Regression to plot</label>
      <select id="regpair"><option value="P1>P2">P1 → P2</option><option value="P2>P3">P2 → P3</option>
        <option value="P3>P4">P3 → P4</option><option value="P23>P4">P2+P3 → P4</option>
        <option value="P12>P3">P1+P2 → P3</option><option value="P12>P34">P1+P2 → P3+P4</option>
        <option value="P4>P1n">P4 → next-month P1</option></select></div>
    <button class="act rv" id="dl">Download CSV (window)</button>
    <p class="note">Long-BE = long TIPS / short UST. Both directions carry the slippage
      (long pays GC+x, short earns GC&minus;x), so they are not mirror images. Mid = zero spread.
      Specialness not modeled. Full hand-replication data: <code>python export.py</code>.</p>
  </div>
  <div class="main">
    <div class="totals rv">
      <div class="tot l"><span class="lab">long breakeven</span><b id="tl">+0</b> bp</div>
      <div class="tot s"><span class="lab">short breakeven</span><b id="ts">+0</b> bp</div>
      <div class="tot m"><span class="lab">mid (x=0)</span><b id="tm">+0</b> bp</div>
      <div class="tot"><span class="lab">window</span><b id="twin" style="font-size:13px">&mdash;</b></div>
    </div>
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
  </div>
</div>
<script>
const DATA = __DATA__;
const TENORS = Object.keys(DATA);
const S = {tenor: TENORS.includes("10y")?"10y":TENORS[0], xT:3, xU:3, start:null, end:null, view:"chart", freq:"monthly", smetric:"tips", beta:75, seasmode:"agg", periods:[1,2,3,4], smonth:"all", swins:["full"], calend:"start", issue:"all", regpair:"P1>P2"};
const MONTHS=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const PCOL=["#bcd4f2","#7fb0e8","#2f81f7","#1b4f8f"];   // aggregate bars: ordered light->dark ramp
const HCOL=["#4cc9f0","#e63946","#52b788","#c77dff"];   // history lines: high-contrast (overlay-friendly)
const PLAB=["P1 · prev m/e → auction−1w","P2 · auction−1w → auction","P3 · auction → auction+1w","P4 · auction+1w → m/e"];
const $ = id => document.getElementById(id);
const fmt = (x,d=1) => (x>=0?"+":"") + x.toFixed(d);
const cls = x => x>=0?"pos":"neg";

function winIdx(){
  const d = DATA[S.tenor], n = d.dates.length; let lo=0, hi=n-1;
  if(S.start){ while(lo<n && d.dates[lo]<S.start) lo++; }
  if(S.end){ while(hi>=0 && d.dates[hi]>S.end) hi--; }
  return {d, lo, hi};
}
function series(){
  const {d,lo,hi} = winIdx();
  const o = {dates:[],rT:[],rU:[],rBE:[],lBE:[],sBE:[],cL:[],cS:[],cM:[]};
  let cl=0,cs=0,cm=0;
  for(let i=lo;i<=hi;i++){
    const slip = S.xT*d.sT[i] + S.xU*d.sU[i];
    const lbe = d.rBE[i]-slip, sbe = -d.rBE[i]-slip, mid = d.rBE[i];
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
function applyView(){
  const seasonal = S.view==="seasonal", mode = S.seasmode;
  document.querySelectorAll(".rv").forEach(e=>e.style.display=seasonal?"none":"");
  document.querySelectorAll(".sv").forEach(e=>e.style.display=seasonal?"":"none");
  document.querySelectorAll(".hv").forEach(e=>e.style.display=(seasonal&&mode==="hist")?"":"none");      // periods (history)
  document.querySelectorAll(".mv").forEach(e=>e.style.display=(seasonal&&mode!=="agg")?"":"none");        // month (hist/cal/reg)
  document.querySelectorAll(".cv").forEach(e=>e.style.display=(seasonal&&mode==="cal")?"":"none");        // day-count (calendar)
  document.querySelectorAll(".pv").forEach(e=>e.style.display=(seasonal&&mode==="reg")?"":"none");        // regression picker
  $("betagrp").classList.toggle("off", seasonal && S.smetric!=="be");   // β only acts on Breakeven
  $("chart").style.display=(!seasonal && S.view==="chart")?"":"none";
  $("tablewrap").style.display=(!seasonal && S.view==="table")?"":"none";
  $("seaswrap").style.display=(seasonal&&mode==="agg")?"":"none";
  $("seashist").style.display=(seasonal&&mode==="hist")?"":"none";
  $("seascal").style.display=(seasonal&&mode==="cal")?"":"none";
  $("seasreg").style.display=(seasonal&&mode==="reg")?"":"none";
}
const SEASDRAW={agg:()=>drawSeasonal(),hist:()=>drawHistory(),cal:()=>drawCalendar(),reg:()=>drawPredict()};
function render(){
  applyView();
  if(S.view==="seasonal"){ (SEASDRAW[S.seasmode]||SEASDRAW.agg)(); return; }
  const s = series();
  const last = a => a.length?a[a.length-1]:0;
  $("tl").textContent=fmt(last(s.cL),0); $("ts").textContent=fmt(last(s.cS),0); $("tm").textContent=fmt(last(s.cM),0);
  $("twin").textContent = s.dates.length ? (s.dates[0]+"  →  "+s.dates[s.dates.length-1]+"  ("+s.dates.length+" days)") : "—";
  if(S.view==="chart"){ drawChart(s); } else { drawTable(s); }
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
    title:{text:S.tenor+" breakeven cumulative net P&L (bp, linear-sum)",font:{size:14}},
    xaxis:{gridcolor:"#2d3a48"},yaxis:{gridcolor:"#2d3a48",zeroline:true,zerolinecolor:"#3a4a5a",title:"bp"},
    hovermode:"x unified",legend:{orientation:"h",y:1.08}};
  Plotly.react("chart",data,layout,{responsive:true,displaylogo:false});
}
function median(a){ if(!a.length)return null; const b=a.slice().sort((x,y)=>x-y),n=b.length,h=n>>1; return n%2?b[h]:(b[h-1]+b[h])/2; }
function quantile(a,q){ if(!a.length)return null; const b=a.slice().sort((x,y)=>x-y),pos=(b.length-1)*q,lo=Math.floor(pos); return b[lo]+((b[lo+1]??b[lo])-b[lo])*(pos-lo); }
// --- shared seasonal filters: sample window(s) Full/5Y/3Y, issue type new/reopen, month/H1/H2 ---
const WINS={"full":0,"5y":60,"3y":36}, WINCOL={"full":"#2f81f7","5y":"#f5a623","3y":"#e63946"}, WINLBL={"full":"Full","5y":"5Y","3y":"3Y"};
function lastYM(){ const s=DATA[S.tenor].seas; let mx=0; for(let i=0;i<s.y.length;i++){const k=s.y[i]*12+s.m[i]; if(k>mx)mx=k;} return mx; }
function winCutOf(w){ return w==="full" ? 0 : lastYM()-(WINS[w]-1); }      // min y*12+m for window w
function winList(){ const l=["full","5y","3y"].filter(w=>S.swins.includes(w)); return l.length?l:["full"]; }
function winMain(){ return winList()[0]; }                                 // longest selected (full>5y>3y): dense views
function monthPass(m){ if(S.smonth==="all")return true; if(S.smonth==="h1")return m<=6; if(S.smonth==="h2")return m>=7; return m===+S.smonth; }
function monthLbl(){ return S.smonth==="all"?"all months":S.smonth==="h1"?"H1 (Jan–Jun)":S.smonth==="h2"?"H2 (Jul–Dec)":MONTHS[+S.smonth-1]; }
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
  const sd=DATA[S.tenor].seas, beta=S.beta/100;
  const valOf=i=>{ const t=sd.t[i],u=sd.u[i];
    if(S.smetric==="tips")return t; if(S.smetric==="nom")return u;
    return (t==null||u==null)?null:t-beta*u; };
  const byMP={}, byP={1:[],2:[],3:[],4:[]}, clMP={};
  for(let i=0;i<sd.y.length;i++){ if(!rowPass(sd,i,false,cut))continue;   // window + issue (not month: this IS the by-month view)
    const v=valOf(i); if(v==null)continue;
    const m=sd.m[i],p=sd.p[i],k=(m-1)*4+(p-1);
    (byMP[k]=byMP[k]||[]).push(v); byP[p].push(v); if(sd.c[i])clMP[k]=(clMP[k]||0)+1; }
  const med=[[],[],[],[]],q1=[[],[],[],[]],q3=[[],[],[],[]],ns=[[],[],[],[]],clamp=[[],[],[],[]];
  for(let m=0;m<12;m++)for(let p=0;p<4;p++){ const k=m*4+p, arr=byMP[k]||[];
    med[p][m]=median(arr); q1[p][m]=quantile(arr,0.25); q3[p][m]=quantile(arr,0.75);
    ns[p][m]=arr.length; clamp[p][m]=clMP[k]||0; }
  const cum=[]; let c=0;                                       // cumulative seasonal path (Σ medians)
  for(let m=0;m<12;m++)for(let p=0;p<4;p++){ const v=med[p][m]; if(v!=null)c+=v; cum.push(c); }
  const sig=[1,2,3,4].map(p=>median(byP[p])), sq1=[1,2,3,4].map(p=>quantile(byP[p],0.25)),
        sq3=[1,2,3,4].map(p=>quantile(byP[p],0.75)), sn=[1,2,3,4].map(p=>byP[p].length);
  return {med,q1,q3,ns,clamp,cum,sig,sq1,sq3,sn};
}
const MNAME={tips:"TIPS return",nom:"Nominal (UST) return",be:"Breakeven"};
function drawSeasonal(){
  const wl=winList(), a=seasonalAgg(winCutOf(winMain()));     // 48-bar uses the longest selected window
  const barTr=[0,1,2,3].map(p=>({type:"bar",name:PLAB[p],x:MONTHS.map((_,m)=>m*4+p),y:a.med[p],
    marker:{color:PCOL[p]},width:0.92,
    error_y:{type:"data",symmetric:false,array:a.med[p].map((v,m)=>v==null?0:a.q3[p][m]-v),
             arrayminus:a.med[p].map((v,m)=>v==null?0:v-a.q1[p][m]),color:"rgba(230,237,243,.28)",thickness:1,width:0},
    customdata:MONTHS.map((mm,m)=>[mm,a.ns[p][m],a.q1[p][m],a.q3[p][m]]),
    hovertemplate:"%{customdata[0]} · P"+(p+1)+"<br>median %{y:+.2f} bp  (IQR %{customdata[2]:+.2f}..%{customdata[3]:+.2f}, n=%{customdata[1]})<extra></extra>"}));
  const cumTr={type:"scatter",mode:"lines",name:"cumulative (Σ medians)",yaxis:"y2",
    x:a.cum.map((_,i)=>i),y:a.cum,line:{color:"#d6a13a",width:2},hovertemplate:"cumulative %{y:+.1f} bp<extra></extra>"};
  const mname=MNAME[S.smetric]+(S.smetric==="be"?" (β="+S.beta+"%)":"");
  Plotly.react("seastop",[...barTr,cumTr],{
    paper_bgcolor:"#0f1419",plot_bgcolor:"#0f1419",font:{color:"#e6edf3"},margin:{l:54,r:54,t:36,b:30},
    barmode:"overlay",title:{text:S.tenor+" — "+mname+" per auction-cycle period (median bp, "+WINLBL[winMain()]+" window)",font:{size:13}},
    xaxis:{tickvals:MONTHS.map((_,m)=>m*4+1.5),ticktext:MONTHS,gridcolor:"#2d3a48",range:[-0.6,47.6]},
    yaxis:{gridcolor:"#2d3a48",zeroline:true,zerolinecolor:"#3a4a5a",title:"period median (bp)"},
    yaxis2:{overlaying:"y",side:"right",title:"cumulative (bp)",showgrid:false,zeroline:false},
    hovermode:"closest",legend:{orientation:"h",y:1.14,font:{size:10}}},{responsive:true,displaylogo:false});
  // within-month signature: one bar per period (single window, period-coloured) OR grouped by window
  let sigTr;
  if(wl.length===1){ const s=a;
    sigTr=[{type:"bar",x:["P1","P2","P3","P4"],y:s.sig,marker:{color:PCOL},width:0.6,
      error_y:{type:"data",symmetric:false,array:s.sig.map((v,p)=>v==null?0:s.sq3[p]-v),arrayminus:s.sig.map((v,p)=>v==null?0:v-s.sq1[p]),color:"rgba(230,237,243,.4)",thickness:1.2,width:4},
      customdata:PLAB.map((l,p)=>[l,s.sn[p]]),hovertemplate:"%{customdata[0]}<br>median %{y:+.3f} bp (n=%{customdata[1]})<extra></extra>"}];
  } else { sigTr=wl.map(w=>{ const s=seasonalAgg(winCutOf(w));
      return {type:"bar",name:WINLBL[w],x:["P1","P2","P3","P4"],y:s.sig,marker:{color:WINCOL[w]},
        error_y:{type:"data",symmetric:false,array:s.sig.map((v,p)=>v==null?0:s.sq3[p]-v),arrayminus:s.sig.map((v,p)=>v==null?0:v-s.sq1[p]),color:"rgba(230,237,243,.3)",thickness:1,width:3},
        customdata:s.sn.map((n,p)=>[PLAB[p],n]),hovertemplate:"%{customdata[0]} ["+WINLBL[w]+"]<br>median %{y:+.3f} bp (n=%{customdata[1]})<extra></extra>"};}); }
  Plotly.react("seassig",sigTr,{
    paper_bgcolor:"#0f1419",plot_bgcolor:"#0f1419",font:{color:"#e6edf3"},margin:{l:54,r:20,t:8,b:26},barmode:"group",
    showlegend:wl.length>1,legend:{orientation:"h",y:1.2,font:{size:10}},
    yaxis:{gridcolor:"#2d3a48",zeroline:true,zerolinecolor:"#3a4a5a",title:"median (bp)"},xaxis:{gridcolor:"#2d3a48"}},
    {responsive:true,displaylogo:false});
  const desc=S.smetric==="be"?("<b>Breakeven = TIPS − "+S.beta+"%·UST</b> (β=100% is the plain DV01-matched breakeven)")
                             :("<b>"+MNAME[S.smetric]+"</b> leg");
  $("seascap").innerHTML="Showing "+desc+" per auction-cycle period — <b>"+filtDesc()+"</b>. Bars = "
    +"<b>median across years</b> of the bucket-summed P&L (bp on 100k DV01; ×$100k = $); whiskers = "
    +"25–75th pctile; gold = cumulative seasonal path. Split around the month's single TIPS auction (A0..A4, ±1w).";
}
function seasonalHistory(){
  // No aggregation: every (year,month,period) bucket as its own point, optionally filtered to one
  // period and/or one calendar month, returned per period sorted chronologically.
  const sd=DATA[S.tenor].seas, beta=S.beta/100, pday=[4,11,18,25], cut=winCutOf(winMain());   // day-of-month to place each period
  const valOf=(t,u)=>S.smetric==="tips"?t:S.smetric==="nom"?u:(t==null||u==null?null:t-beta*u);
  const byP={1:[],2:[],3:[],4:[]};
  for(let i=0;i<sd.y.length;i++){
    const p=sd.p[i],m=sd.m[i],y=sd.y[i];
    if(!S.periods.includes(p)) continue;
    if(!rowPass(sd,i,true,cut)) continue;             // window + issue + month
    const v=valOf(sd.t[i],sd.u[i]); if(v==null) continue;
    byP[p].push({x:y+"-"+String(m).padStart(2,"0")+"-"+String(pday[p-1]).padStart(2,"0"),v:v});
  }
  for(const p in byP) byP[p].sort((a,b)=>a.x<b.x?-1:1);
  return byP;
}
const MNAME2={tips:"TIPS return",nom:"Nominal (UST) return",be:"Breakeven"};
function drawHistory(){
  const byP=seasonalHistory(), periods=S.periods.slice().sort();
  const single=S.smonth!=="all";                              // one month -> few pts/yr; markers help
  const traces=[]; let all=[];
  for(const p of periods){
    const arr=byP[p]; all=all.concat(arr.map(o=>o.v));
    traces.push({type:"scatter",mode:single?"lines+markers":"lines",name:"P"+p,connectgaps:true,
      x:arr.map(o=>o.x),y:arr.map(o=>o.v),marker:{color:HCOL[p-1],size:6},line:{color:HCOL[p-1],width:1.5,shape:"linear"},
      hovertemplate:"%{x|%Y-%m} · P"+p+": %{y:+.2f} bp<extra></extra>"});
  }
  const med=median(all);
  const mname=MNAME2[S.smetric]+(S.smetric==="be"?" (β="+S.beta+"%)":"");
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
function ymNewMap(){ const s=DATA[S.tenor].seas,mp={}; for(let i=0;i<s.y.length;i++)mp[s.y[i]*12+s.m[i]]=s.n[i]; return mp; }
function seasonalCalendar(cut){
  // Per-DAY: tag each day with its business-day-of-month (its ordinal among the trading days of its
  // month) and aggregate the metric by that key across the filtered sample. S.calend="end" counts
  // from the LAST trading day (key −1 = last day, −2 = second-last) so month-ends align across months
  // (turn-of-month) instead of smearing at the high forward-BDOMs (months run 19–23 trading days).
  const d=DATA[S.tenor], beta=S.beta/100, nm=ymNewMap();
  const valOf=i=>S.smetric==="tips"?d.rT[i]:S.smetric==="nom"?d.rU[i]:d.rT[i]-beta*d.rU[i];
  const T={}; for(let i=0;i<d.dates.length;i++){const ym=d.dates[i].slice(0,7); T[ym]=(T[ym]||0)+1;}  // trading days/month
  const byB={}; let curYM="", bd=0;
  for(let i=0;i<d.dates.length;i++){
    const ds=d.dates[i], ym=ds.slice(0,7);
    if(ym!==curYM){curYM=ym; bd=0;} bd++;                       // counts every trading day in the month
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
  return {keys,med,q1,q3,ns,cum};
}
function drawCalendar(){
  const wl=winList(), mname=MNAME[S.smetric]+(S.smetric==="be"?" (β="+S.beta+"%)":"");
  const xtitle = S.calend==="end" ? "business day from month-end  (−1 = last trading day)"
                                  : "business day of month  (1 = first trading day)";
  let traces, layout={paper_bgcolor:"#0f1419",plot_bgcolor:"#0f1419",font:{color:"#e6edf3"},margin:{l:54,r:54,t:36,b:42},
    title:{text:S.tenor+" — "+mname+" by "+(S.calend==="end"?"day-from-month-end":"business day of month")+" (median)",font:{size:13}},
    xaxis:{title:xtitle,gridcolor:"#2d3a48",dtick:1},
    yaxis:{title:"median daily P&L (bp)",gridcolor:"#2d3a48",zeroline:true,zerolinecolor:"#3a4a5a"},
    hovermode:"closest",legend:{orientation:"h",y:1.12,font:{size:10}}};
  if(wl.length===1){                                            // single window: bars + cumulative path
    const a=seasonalCalendar(winCutOf(wl[0]));
    traces=[{type:"bar",name:"median",x:a.keys,y:a.med,marker:{color:"#2f81f7"},
      error_y:{type:"data",symmetric:false,array:a.med.map((v,i)=>v==null?0:a.q3[i]-v),arrayminus:a.med.map((v,i)=>v==null?0:v-a.q1[i]),color:"rgba(230,237,243,.22)",thickness:1,width:0},
      customdata:a.ns,hovertemplate:"day %{x}: median %{y:+.3f} bp (n=%{customdata})<extra></extra>"},
      {type:"scatter",mode:"lines",name:"cumulative",yaxis:"y2",x:a.keys,y:a.cum,line:{color:"#d6a13a",width:2},hovertemplate:"cumulative %{y:+.2f} bp<extra></extra>"}];
    layout.yaxis2={overlaying:"y",side:"right",title:"cumulative (bp)",showgrid:false,zeroline:false};
  } else {                                                      // multiple windows: one median line each
    traces=wl.map(w=>{ const a=seasonalCalendar(winCutOf(w));
      return {type:"scatter",mode:"lines+markers",name:WINLBL[w],x:a.keys,y:a.med,line:{color:WINCOL[w],width:1.6},marker:{size:4,color:WINCOL[w]},
        customdata:a.ns,hovertemplate:"day %{x} ["+WINLBL[w]+"]: median %{y:+.3f} bp (n=%{customdata})<extra></extra>"};});
  }
  Plotly.react("seascalchart",traces,layout,{responsive:true,displaylogo:false});
  $("calcap").innerHTML="The <b>calendar effect</b>: median daily P&L by <b>"+(S.calend==="end"?"day from month-end":"business day of month")
    +"</b>, independent of the auction cycle — <b>"+filtDesc()+"</b>, "+monthLbl()
    +(wl.length===1?". Bars = median (whiskers 25–75th); gold = cumulative within-month path."
                   :". One line per sample window (compare degradation).")
    +" Note x = trading days (≈19–23/month), not calendar dates; use From-end to align the turn-of-month.";
}
// ============ Predict: OLS regressions between within-month periods ============
function rowVal(s,i){ const beta=S.beta/100,t=s.t[i],u=s.u[i];
  return S.smetric==="tips"?t:S.smetric==="nom"?u:(t==null||u==null?null:t-beta*u); }
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
  return REGS.map(R=>{ const xs=[],ys=[];
    for(const ym in mbF){ const b=mbF[ym], tb=R.cross?mbW[(+ym)+1]:b;   // cross: target = next calendar month
      if(!tb) continue;
      const xv=R.fx(b), yv=R.fy(tb);
      if(xv==null||yv==null||isNaN(xv)||isNaN(yv))continue; xs.push(xv); ys.push(yv); }
    return {key:R.key,lab:R.lab,xs,ys,f:ols(xs,ys)}; });
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
  const mname=MNAME[S.smetric]+(S.smetric==="be"?" (β="+S.beta+"%)":"");
  Plotly.react("regchart",traces,{
    paper_bgcolor:"#0f1419",plot_bgcolor:"#0f1419",font:{color:"#e6edf3"},margin:{l:54,r:20,t:34,b:42},
    title:{text:sel.lab+"   ("+mname+", "+WINLBL[winMain()]+" window"+(S.smonth==="all"?"":", "+monthLbl())+")"+(f?"   slope "+f.b.toFixed(2)+", R² "+f.r2.toFixed(2)+", t "+f.t.toFixed(1)+", n "+f.n:""),font:{size:13}},
    xaxis:{title:"predictor (bp)",gridcolor:"#2d3a48",zeroline:true,zerolinecolor:"#3a4a5a"},
    yaxis:{title:"target (bp)",gridcolor:"#2d3a48",zeroline:true,zerolinecolor:"#3a4a5a"},
    hovermode:"closest",showlegend:false},{responsive:true,displaylogo:false});
  $("regcap").innerHTML="Does early-month performance predict later? Each point = one month ("+filtDesc()
    +(S.smonth==="all"?"":", "+monthLbl())+"); OLS of target on predictor, bucket P&L in bp. "
    +"<b>P4 → next-month P1</b> is cross-month (this month's P4 vs the <i>following</i> month's P1; filters apply to the P4 month). "
    +"|t|&gt;2 (~5% significance) highlighted green. Pick which relationship to plot at left; with multiple windows "
    +"the table compares them (scatter shows the "+WINLBL[winMain()]+" window). Month / Issue / Sample condition the regression.";
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
  a.download="breakeven_"+S.tenor+"_xT"+S.xT+"_xU"+S.xU+".csv"; a.click();
}
// ---- wire controls ----
function seg(id,key,after){ $(id).querySelectorAll("button").forEach(b=>b.onclick=()=>{
  S[key]=b.dataset[key]; $(id).querySelectorAll("button").forEach(x=>x.classList.toggle("on",x===b)); (after||render)(); });}
const tenWrap=$("tenor"); TENORS.forEach(t=>{const b=document.createElement("button");b.textContent=t;b.dataset.tenor=t;
  if(t===S.tenor)b.classList.add("on"); tenWrap.appendChild(b);});
tenWrap.querySelectorAll("button").forEach(b=>b.onclick=()=>{S.tenor=b.dataset.tenor;
  tenWrap.querySelectorAll("button").forEach(x=>x.classList.toggle("on",x===b)); render();});
seg("view","view"); seg("freq","freq"); seg("smetric","smetric"); seg("seasmode","seasmode");
seg("issue","issue"); seg("calend","calend");
$("speriod").querySelectorAll("input").forEach(cb=>cb.onchange=()=>{
  S.periods=[...$("speriod").querySelectorAll("input:checked")].map(x=>+x.value); render(); });
$("swin").querySelectorAll("input").forEach(cb=>cb.onchange=()=>{
  S.swins=[...$("swin").querySelectorAll("input:checked")].map(x=>x.value); render(); });
$("xt").oninput=e=>{S.xT=+e.target.value; $("xtv").textContent=S.xT.toFixed(1); scheduleRender();};
$("xu").oninput=e=>{S.xU=+e.target.value; $("xuv").textContent=S.xU.toFixed(1); scheduleRender();};
$("beta").oninput=e=>{S.beta=+e.target.value; $("betav").textContent=S.beta; scheduleRender();};
$("smonth").onchange=e=>{S.smonth=e.target.value; render();};
$("regpair").onchange=e=>{S.regpair=e.target.value; render();};
function setRangeBtn(name){ document.querySelectorAll("#range button").forEach(x=>x.classList.toggle("on", x.dataset.range===name)); }
$("start").onchange=e=>{S.start=e.target.value||null; setRangeBtn(null); render();};
$("end").onchange=e=>{S.end=e.target.value||null; setRangeBtn(null); render();};
$("dl").onclick=downloadCSV;
document.querySelectorAll("[data-range]").forEach(b=>b.onclick=()=>{
  const all=DATA[S.tenor].dates, lastD=all[all.length-1]; let s=null;
  if(b.dataset.range==="full") s=null;
  else if(b.dataset.range==="ytd") s=lastD.slice(0,4)+"-01-01";
  else { const yrs=+b.dataset.range.replace("y",""); const d=new Date(lastD); d.setFullYear(d.getFullYear()-yrs); s=d.toISOString().slice(0,10); }
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
