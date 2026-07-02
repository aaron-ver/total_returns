"""
Baseline visualizer for the European / UK linker return series (self-contained dashboard_intl.html).

Views:
  * Cumulative   -- cumulative returns, by constant-maturity Bucket or By bond (Breakeven / Outright).
                    Breakeven leg uses the CONTEMPORANEOUS nominal hedge (closest-maturity then-
                    existing, beta=1); outright = the financed linker leg. (Analogue of the US
                    fixed-maturity breakeven chart.)
  * Auction cycle -- event study around each bucket's own auctions (seasonal_intl): mean within-cycle
                    cumulative path, rebased to 0 on the auction day (concession into / snap-back
                    after the tap). NON-GILTS only (stable monthly session); gilts/Germany show a note.
  * Calendar     -- mean monthly return by calendar month (index seasonality). ALL markets.

Units: engine bp (= $/100k-DV01 P&L). Weekly-sampled cumulative for a light file. Reads the cmt_intl /
engine / seasonal_intl caches as-is (run cmt_intl.py, then seasonal is computed live here).

Run:  python dashboard_intl.py            # build dashboard_intl.html and open it
      python dashboard_intl.py --no-open  # build only
"""
from __future__ import annotations
import os, sys, json, webbrowser
import pandas as pd

import linkers
import buckets_intl as bk
import seasonal_intl as sea
import energy_intl as en

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = linkers.CACHE
CMT_DIR = os.path.join(CACHE, "cmt")
PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"
MKT_ORDER = ["IT_BTPEI", "FR_OATEI", "FR_OATI", "ES_EI", "UK_3M", "DE_EI"]


def _weekly(s):
    s = pd.to_numeric(s, errors="coerce").dropna()
    return None if s.empty else s.resample("W-FRI").last().dropna()


def _ser(cum, idx):
    return [None if pd.isna(v) else int(round(v)) for v in cum.reindex(idx).values]


def _slip(fin, gc):
    """Cumulative repo half-spread sensitivity: Σ fin_bp/gc (weekly). Cost of x bp half-spread on a
    leg = (x/100)*slip bp (fin_bp = d/360*gc/100*V/DV01, so extra per bp of spread = fin_bp/(100*gc))."""
    g = pd.to_numeric(gc, errors="coerce"); g = g.where(g > 0)
    return _weekly((pd.to_numeric(fin, errors="coerce") / g).cumsum())


def build_payload(hedge_map=None):
    u = linkers.load_universe(include_deferred=True)
    u["mat"] = pd.to_datetime(u["maturity"])
    hedge_map = hedge_map or {}
    try:
        import crude
        crude_cum_w = _weekly(crude.load("Brent")["usd_per_contract"].cumsum())   # weekly cum $/contract
    except Exception:
        crude_cum_w = None
    out = {}
    for m in MKT_ORDER:
        if m not in bk.MARKETS:
            continue
        entry = {"label": bk.MARKETS[m], "buckets": {}, "bonds": {}}
        for b in bk.ORDER:
            p = os.path.join(CMT_DIR, f"{m}__{b}.parquet")
            if not os.path.exists(p):
                continue
            d = pd.read_parquet(p)
            ot = _weekly(d["cum_linker_bp"])
            if ot is None:
                continue
            be = _weekly(d["cum_BE_bp"]) if ("r_BE_bp" in d and d["r_BE_bp"].notna().any()) else None
            slipL = _slip(d.get("linker_fin_bp"), d.get("gc_repo"))
            slipN = _slip(d.get("nominal_fin_bp"), d.get("gc_repo")) if be is not None else None
            crudeB = hedge_map.get(m, {}).get(b)
            crudeCum = _ser(crude_cum_w, ot.index) if (crude_cum_w is not None and crudeB) else None
            entry["buckets"][b] = {"x": [t.strftime("%Y-%m-%d") for t in ot.index],
                                   "out": _ser(ot, ot.index), "be": _ser(be, ot.index) if be is not None else None,
                                   "slipL": _ser(slipL, ot.index) if slipL is not None else None,
                                   "slipN": _ser(slipN, ot.index) if slipN is not None else None,
                                   "crudeB": crudeB, "crudeCum": crudeCum}
        for _, r in u[u["market"] == m].sort_values("mat").iterrows():
            isin = r["isin"]; rp = os.path.join(CACHE, "returns", f"{isin}.parquet")
            if not os.path.exists(rp):
                continue
            rr = pd.read_parquet(rp)
            if "bp" not in rr:
                continue
            ot = _weekly(rr["bp"].cumsum())
            if ot is None:
                continue
            be = None; slipN = None
            slipL = _slip(rr.get("fin_bp"), rr.get("gc"))
            bpath = os.path.join(CACHE, "breakeven", f"{isin}.parquet")
            if os.path.exists(bpath):
                bb = pd.read_parquet(bpath)
                if "cum_BE_bp" in bb:
                    be = _weekly(bb["cum_BE_bp"]); slipN = _slip(bb.get("nominal_fin_bp"), bb.get("gc_repo"))
            entry["bonds"][isin] = {"label": str(r["desc"]), "x": [t.strftime("%Y-%m-%d") for t in ot.index],
                                    "out": _ser(ot, ot.index), "be": _ser(be, ot.index) if be is not None else None,
                                    "slipL": _ser(slipL, ot.index) if slipL is not None else None,
                                    "slipN": _ser(slipN, ot.index) if slipN is not None else None}
        if entry["buckets"] or entry["bonds"]:
            out[m] = entry
    return out


def plotly_tag():
    try:
        import requests
        r = requests.get(PLOTLY_CDN, timeout=40); r.raise_for_status()
        return "<script>" + r.text + "</script>"
    except Exception as e:
        print(f"  (could not embed Plotly: {e}; using CDN link — needs internet to open)")
        return f'<script src="{PLOTLY_CDN}"></script>'


HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Intl linker returns</title>
__PLOTLY__
<style>
:root{--bg:#0f1419;--panel:#1b2430;--ink:#e6edf3;--muted:#8b98a5;--line:#2d3a48;--accent:#2f81f7}
*{box-sizing:border-box}
body{margin:0;font:13px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink);height:100vh;display:flex}
.side{width:288px;background:var(--panel);border-right:1px solid var(--line);padding:16px;overflow:auto;user-select:none}
.main{flex:1;display:flex;flex-direction:column;min-width:0}
h1{font-size:15px;margin:0 0 14px}
.grp{margin-bottom:15px}
.grp>label{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px}
select{width:100%;background:#0f1722;color:var(--ink);border:1px solid var(--line);border-radius:5px;padding:7px}
input[type=range]{width:100%;accent-color:var(--accent);margin-top:3px}
.hl{color:var(--accent);font-variant-numeric:tabular-nums}
.seg{display:flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.seg button{flex:1;background:transparent;color:var(--ink);border:0;padding:7px 4px;cursor:pointer;font-size:12px}
.seg button.on{background:var(--accent);color:#fff}
.chk{display:flex;align-items:center;gap:7px;padding:3px 2px;cursor:pointer;font-size:12px}
.chk input{accent-color:var(--accent)}.chk .sw{width:11px;height:11px;border-radius:2px;flex:none}
.mini{display:flex;gap:8px;margin-bottom:6px}
.mini button{flex:1;background:transparent;border:1px solid var(--line);color:var(--muted);border-radius:5px;padding:4px;cursor:pointer;font-size:11px}
#totals{display:flex;gap:18px;flex-wrap:wrap;padding:12px 18px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
.tot b{font-size:16px}.tot .lab{color:var(--muted);font-size:11px;display:block}
#chart{flex:1;min-height:0}
.note{color:var(--muted);font-size:11px;padding:8px 18px}
#stats{padding:2px 18px 8px}
table.stt{border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:12px}
table.stt th,table.stt td{padding:3px 14px;text-align:right;border-bottom:1px solid var(--line)}
table.stt th{color:var(--muted);font-weight:600}
table.stt td:first-child,table.stt th:first-child{text-align:left}
</style></head>
<body>
<div class="side">
  <h1>Intl linker returns</h1>
  <div class="grp"><label>Market</label><select id="market"></select></div>
  <div class="grp"><label>View</label><div class="seg" id="view">
    <button data-v="cum" class="on">Cumulative</button><button data-v="cycle">Auction cycle</button><button data-v="cal">Calendar</button></div></div>
  <div class="grp" id="groupgrp"><label>Group by</label><div class="seg" id="group">
    <button data-g="buckets" class="on">Buckets</button><button data-g="bonds">By bond</button></div></div>
  <div class="grp" id="cycwingrp" style="display:none"><label>Window (±trading days)</label><div class="seg" id="cycwin">
    <button data-w="10" class="on">10</button><button data-w="15">15</button><button data-w="21">21</button></div></div>
  <div class="grp" id="dispgrp" style="display:none"><label>Display</label><div class="seg" id="cycdisp">
    <button data-d="mean" class="on">Mean</button><button data-d="box">Box</button><button data-d="regress">Regress</button></div></div>
  <div class="grp" id="sampgrp" style="display:none"><label>Sample window</label><div class="seg" id="samp">
    <button data-s="full" class="on">Full</button><button data-s="5y">5Y</button><button data-s="3y">3Y</button></div></div>
  <div class="grp" id="energygrp" style="display:none"><label class="chk" style="text-transform:none;font-size:12px;color:var(--ink)"><input type="checkbox" id="energy"> Energy hedge (Brent)</label>
    <div class="note" style="padding:2px 0 0">subtract h·Brent$; h = full‑sample crude β (contracts)</div></div>
  <div class="grp"><label>Metric</label><div class="seg" id="metric">
    <button data-m="be" class="on">Breakeven</button><button data-m="out">Outright</button></div></div>
  <div class="grp" id="betagrp"><label>Hedge β <span class="hl" id="bv">1.00</span></label>
    <input type="range" id="beta" min="0" max="1.5" step="0.05" value="1">
    <div class="note" style="padding:2px 0 0">BE = linker − β·nominal (β=1 = equal DV01)</div></div>
  <div class="grp" id="posgrp"><label>Position</label><div class="seg" id="pos">
    <button data-p="long" class="on">Long</button><button data-p="short">Short (mirror)</button></div></div>
  <div class="grp" id="dategrp"><label>Date range (window)</label>
    <input type="date" id="start"><input type="date" id="end" style="margin-top:4px"></div>
  <div class="grp" id="repogrp"><label>Repo half-spread — linker <span class="hl" id="xlv">0.0</span> / nom <span class="hl" id="xnv">0.0</span> bp</label>
    <input type="range" id="xl" min="0" max="10" step="0.5" value="0">
    <input type="range" id="xn" min="0" max="10" step="0.5" value="0" style="margin-top:4px"></div>
  <div class="grp"><label id="serlab">Series</label>
    <div class="mini"><button id="all">All</button><button id="none">None</button></div>
    <div id="series"></div></div>
</div>
<div class="main">
  <div id="totals"></div>
  <div id="chart"></div>
  <div id="stats"></div>
  <div class="note" id="note"></div>
</div>
<script>
const DATA = __PAYLOAD__, SEAS = __SEAS__, ORDER = __ORDER__;
const COLORS = ["#2f81f7","#3fb950","#e3b341","#f0883e","#db61a2","#a371f7","#56d4dd","#f85149",
                "#7ee787","#ffa657","#bc8cff","#79c0ff","#d29922","#ff7b72","#39c5cf","#e6edf3"];
const BORDER=["2y","5y","7y","10y","12y","15y","20y","25y","30y","40y","50y"];
const MONTHS=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const S = {market:ORDER[0], view:"cum", group:"buckets", metric:"be", cycw:10, samp:"full", cycdisp:"mean", beta:1, energy:false, pos:"long", start:null, end:null, xL:0, xN:0, regpair:"P2>P3", sel:{}};
const PLBL={P1:"P1 (−10→−5)", P2:"P2 (−5→0)", P3:"P3 (0→+5)", P4:"P4 (+5→+10)"};

function curItems(){
  const e=DATA[S.market];
  if(S.view==="cycle"){ const C=SEAS.cycle[S.market]||{}; return BORDER.filter(b=>C[b]).map(b=>[b,b,C[b]]); }
  if(S.view==="cal"){ const C=SEAS.calendar[S.market]||{}; return BORDER.filter(b=>C[b]).map(b=>[b,b,C[b]]); }
  if(S.group==="bonds") return Object.keys(e.bonds).map(k=>[k,e.bonds[k].label,e.bonds[k]]);
  return BORDER.filter(b=>e.buckets[b]).map(b=>[b,b,e.buckets[b]]);
}
function selKey(){ return S.market+"|"+S.view+"|"+(S.view==="cum"?S.group:"b"); }
function ensureSel(){ if(!S.sel[selKey()]) S.sel[selKey()]=new Set(curItems().map(it=>it[0])); return S.sel[selKey()]; }

function buildSeriesList(){
  const sel=ensureSel(), box=document.getElementById("series"), items=curItems();
  document.getElementById("serlab").textContent = (S.view==="cum"&&S.group==="bonds")?"Bonds (by maturity)":"Buckets";
  box.innerHTML="";
  items.forEach((it,i)=>{ const [k,lab]=it,c=COLORS[i%COLORS.length];
    const row=document.createElement("label"); row.className="chk";
    row.innerHTML=`<input type="checkbox" ${sel.has(k)?"checked":""}><span class="sw" style="background:${c}"></span><span>${lab}</span>`;
    row.querySelector("input").onchange=e=>{ e.target.checked?sel.add(k):sel.delete(k); render(); };
    box.appendChild(row); });
}

const LAY=()=>({paper_bgcolor:"#0f1419",plot_bgcolor:"#0f1419",font:{color:"#e6edf3",size:12},
  margin:{l:58,r:18,t:10,b:40},showlegend:false,hovermode:"x unified",
  xaxis:{gridcolor:"#2d3a48"},yaxis:{gridcolor:"#2d3a48",zerolinecolor:"#3d4a58"}});
function note(t){ Plotly.react("chart",[],{...LAY(),annotations:[{text:t,showarrow:false,font:{color:"#8b98a5",size:14},x:0.5,y:0.5,xref:"paper",yref:"paper"}]}); }
const mlab=()=> S.metric==="be"?"breakeven":"outright";
// breakeven at chosen beta from cumulative legs: BE(beta) = cum_linker - beta*cum_nominal = out - beta*(out-be)
function beBeta(o,b){ return o.map((v,i)=> (v==null||!b||b[i]==null)?null : Math.round((v-S.beta*(v-b[i]))*100)/100); }
function perf(cum){   // Sharpe/ann-ret/vol/maxDD/hit from a weekly cumulative-bp series (rf=0)
  const v=cum.filter(c=>c!=null); if(v.length<4) return null;
  const r=[]; for(let i=1;i<v.length;i++) r.push(v[i]-v[i-1]);
  const mu=r.reduce((a,b)=>a+b,0)/r.length, sd=Math.sqrt(r.reduce((a,b)=>a+(b-mu)**2,0)/(r.length-1));
  const annret=mu*52, annvol=sd*Math.sqrt(52);
  let peak=-1e18, mdd=0; for(const c of v){ if(c>peak) peak=c; if(c-peak<mdd) mdd=c-peak; }
  return {annret:Math.round(annret), annvol:Math.round(annvol),
          sharpe:annvol?Math.round(annret/annvol*100)/100:null,
          mdd:Math.round(mdd), hit:Math.round(r.filter(x=>x>0).length/r.length*100)};
}
function pctile(a,p){ if(!a.length) return null; const x=(a.length-1)*p/100, lo=Math.floor(x), hi=Math.ceil(x); return lo===hi?a[lo]:a[lo]+(a[hi]-a[lo])*(x-lo); }
function cycStats(obj){   // client-side per-offset distribution over taps in the sample window, at beta
  const offs=obj.offsets, cut=SEAS.sampcut[S.samp], evs=[];
  for(let e=0;e<obj.dates.length;e++){ if(!cut || obj.dates[e]>=cut) evs.push(e); }
  const cB=obj.crudeB, hedge=S.energy && cB && obj.crude;   // Brent energy hedge (per-event crude path)
  const R={mean:[],med:[],q1:[],q3:[],lo:[],hi:[],n:evs.length};
  for(let k=0;k<offs.length;k++){ const vals=[];
    for(const e of evs){ const o=obj.out[e][k], b=obj.be?obj.be[e][k]:null;
      let v; if(S.metric==="out"){ if(o==null) continue; v=o; } else { if(o==null||b==null) continue; v=o-S.beta*(o-b); }
      if(hedge){ const cr=obj.crude[e][k]; if(cr!=null){ const h=S.metric==="out"?cB[0]:(cB[0]-S.beta*cB[1]); v=v-h*cr/1e5; } }
      vals.push(v); }
    if(!vals.length){ ["mean","med","q1","q3","lo","hi"].forEach(s=>R[s].push(null)); continue; }
    vals.sort((a,b)=>a-b);
    R.mean.push(Math.round(vals.reduce((a,b)=>a+b,0)/vals.length*100)/100);
    R.med.push(pctile(vals,50)); R.q1.push(pctile(vals,25)); R.q3.push(pctile(vals,75)); R.lo.push(vals[0]); R.hi.push(vals[vals.length-1]);
  }
  return R;
}
function olsJS(x,y){ const n=x.length; if(n<3) return null;
  const mx=x.reduce((a,b)=>a+b,0)/n, my=y.reduce((a,b)=>a+b,0)/n;
  let sxx=0,sxy=0,syy=0; for(let i=0;i<n;i++){ sxx+=(x[i]-mx)**2; sxy+=(x[i]-mx)*(y[i]-my); syy+=(y[i]-my)**2; }
  if(sxx===0) return null; const b=sxy/sxx, r2=syy?(sxy*sxy)/(sxx*syy):0;
  const s2=x.reduce((a,xi,i)=>a+(y[i]-(my-b*mx+b*xi))**2,0)/(n-2), se=Math.sqrt(s2/sxx);
  return {beta:b, r2:r2, t:se?b/se:null, n:n, a:my-b*mx}; }
function cycPeriods(obj){   // per tap (in window), the 4 period returns from the metric+beta(+energy) path
  const offs=obj.offsets, cut=SEAS.sampcut[S.samp], cB=obj.crudeB, hedge=S.energy&&cB&&obj.crude;
  const need=[-10,-5,0,5,10].map(k=>offs.indexOf(k)); const ev=[];
  if(need.some(i=>i<0)) return ev;
  for(let e=0;e<obj.dates.length;e++){ if(cut && obj.dates[e]<cut) continue;
    const val=k=>{ const o=obj.out[e][k], b=obj.be?obj.be[e][k]:null;
      let v; if(S.metric==="out"){ if(o==null) return null; v=o; } else { if(o==null||b==null) return null; v=o-S.beta*(o-b); }
      if(hedge){ const cr=obj.crude[e][k]; if(cr!=null){ const h=S.metric==="out"?cB[0]:(cB[0]-S.beta*cB[1]); v=v-h*cr/1e5; } } return v; };
    const p=need.map(val); if(p.some(x=>x==null)) continue;
    ev.push({P1:p[1]-p[0], P2:p[2]-p[1], P3:p[3]-p[2], P4:p[4]-p[3]}); }
  return ev;
}
function calStats(obj){   // client-side per-calendar-month distribution across years, in the window, at beta
  const cut=SEAS.sampcut[S.samp], cutm=cut?cut.slice(0,7):null, bym={}, yrs=new Set();
  const cB=obj.crudeB, cm=SEAS.crude_monthly||{}, hedge=S.energy && cB;   // Brent energy hedge
  for(let mo=1;mo<=12;mo++) bym[mo]=[];
  for(let e=0;e<obj.ym.length;e++){ const y=obj.ym[e]; if(cutm && y<cutm) continue;
    const o=obj.out[e], b=obj.be?obj.be[e]:null;
    let v; if(S.metric==="out"){ if(o==null) continue; v=o; } else { if(o==null||b==null) continue; v=o-S.beta*(o-b); }
    if(hedge){ const cr=cm[y]; if(cr!=null){ const h=S.metric==="out"?cB[0]:(cB[0]-S.beta*cB[1]); v=v-h*cr/1e5; } }
    bym[+y.slice(5,7)].push(v); yrs.add(y.slice(0,4)); }
  const R={mean:[],med:[],q1:[],q3:[],lo:[],hi:[],n:yrs.size};
  for(let mo=1;mo<=12;mo++){ const vals=bym[mo].slice().sort((a,b)=>a-b);
    if(!vals.length){ ["mean","med","q1","q3","lo","hi"].forEach(s=>R[s].push(null)); continue; }
    R.mean.push(Math.round(vals.reduce((a,b)=>a+b,0)/vals.length*100)/100);
    R.med.push(pctile(vals,50)); R.q1.push(pctile(vals,25)); R.q3.push(pctile(vals,75)); R.lo.push(vals[0]); R.hi.push(vals[vals.length-1]);
  }
  return R;
}

function render(){
  document.getElementById("groupgrp").style.display = S.view==="cum"?"":"none";
  document.getElementById("cycwingrp").style.display = (S.view==="cycle"&&S.cycdisp!=="regress")?"":"none";
  document.getElementById("dispgrp").style.display = (S.view==="cycle"||S.view==="cal")?"":"none";
  document.querySelector('#cycdisp button[data-d="regress"]').style.display = S.view==="cycle"?"":"none";
  document.getElementById("sampgrp").style.display = (S.view==="cycle"||S.view==="cal")?"":"none";
  document.getElementById("betagrp").style.display = S.metric==="be"?"":"none";
  document.getElementById("energygrp").style.display = (S.view==="cal"||S.view==="cycle"||(S.view==="cum"&&S.group==="buckets"))?"":"none";
  document.getElementById("posgrp").style.display = S.view==="cum"?"":"none";
  document.getElementById("dategrp").style.display = S.view==="cum"?"":"none";
  document.getElementById("repogrp").style.display = S.view==="cum"?"":"none";
  const sel=ensureSel(), items=curItems(); let traces=[], tot=[], any=false, anyMetric=false, statsRows=[], regRows=[];
  items.forEach((it,i)=>{ const [k,lab,obj]=it, c=COLORS[i%COLORS.length];
    if(S.view==="cycle"){
      if(S.cycdisp==="regress"){
        const ev=cycPeriods(obj); if(ev.length>=3) anyMetric=true; if(!sel.has(k)||ev.length<3) return; any=true;
        const pp=S.regpair.split(">"), x=ev.map(e=>e[pp[0]]), y=ev.map(e=>e[pp[1]]);
        traces.push({x,y,name:lab,mode:"markers",type:"scatter",marker:{color:c,size:6,opacity:0.55},
          hovertemplate:pp[0]+" %{x:.1f} → "+pp[1]+" %{y:.1f} bp<extra>"+lab+"</extra>"});
        const o=olsJS(x,y);
        if(o){ const xr=[Math.min.apply(null,x),Math.max.apply(null,x)];
          traces.push({x:xr,y:xr.map(v=>o.a+o.beta*v),mode:"lines",line:{color:c,width:1.6},showlegend:false,hoverinfo:"skip"}); }
        [["P1","P2"],["P2","P3"],["P3","P4"]].forEach(pr=>regRows.push([lab,c,pr[0]+">"+pr[1],olsJS(ev.map(e=>e[pr[0]]),ev.map(e=>e[pr[1]]))]));
        tot.push([lab, null, c, ev.length]); return;
      }
      const st=cycStats(obj), has=st.mean.some(v=>v!=null); if(has) anyMetric=true; if(!sel.has(k)||!has) return; any=true;
      if(S.cycdisp==="box"){
        traces.push({type:"box",name:lab,x:obj.offsets,q1:st.q1,median:st.med,q3:st.q3,lowerfence:st.lo,upperfence:st.hi,mean:st.mean,
          marker:{color:c},line:{color:c},fillcolor:c+"22",boxmean:true,whiskerwidth:0.5,hovertemplate:"%{x}<br>max %{upperfence}<br>q3 %{q3}<br>med %{median}<br>q1 %{q1}<br>min %{lowerfence}<br>mean %{mean}<extra>"+lab+"</extra>"});
        traces.push({x:obj.offsets,y:st.mean,name:lab+" mean",mode:"lines",type:"scatter",line:{color:c,width:1.6},showlegend:false,
          hovertemplate:"%{x} bd  mean <b>%{y}</b> bp<extra>"+lab+"</extra>"});
      }else{
        traces.push({x:obj.offsets,y:st.mean,name:lab,mode:"lines+markers",type:"scatter",line:{color:c,width:1.8},marker:{size:4},
          hovertemplate:"%{x} bd  <b>%{y}</b> bp<extra>"+lab+"</extra>"});
      }
      const wi=obj.offsets.indexOf(S.cycw); tot.push([lab, wi>=0?st.mean[wi]:null, c, st.n]);
    }else if(S.view==="cal"){
      const st=calStats(obj), has=st.mean.some(v=>v!=null); if(has) anyMetric=true; if(!sel.has(k)||!has) return; any=true;
      if(S.cycdisp==="box"){
        traces.push({type:"box",name:lab,x:MONTHS,q1:st.q1,median:st.med,q3:st.q3,lowerfence:st.lo,upperfence:st.hi,mean:st.mean,
          marker:{color:c},line:{color:c},fillcolor:c+"22",boxmean:true,whiskerwidth:0.5,hovertemplate:"%{x}<br>max %{upperfence}<br>q3 %{q3}<br>med %{median}<br>q1 %{q1}<br>min %{lowerfence}<br>mean %{mean}<extra>"+lab+"</extra>"});
        traces.push({x:MONTHS,y:st.mean,name:lab+" mean",mode:"lines",type:"scatter",line:{color:c,width:1.6},showlegend:false,
          hovertemplate:"%{x}  mean <b>%{y}</b> bp<extra>"+lab+"</extra>"});
      }else{
        traces.push({x:MONTHS,y:st.mean,name:lab,mode:"lines+markers",type:"scatter",line:{color:c,width:1.8},marker:{size:5},
          hovertemplate:"%{x}  <b>%{y}</b> bp<extra>"+lab+"</extra>"});
      }
      tot.push([lab,Math.round(st.mean.reduce((a,v)=>a+(v||0),0)),c, st.n]);
    }else{
      let xs=obj.x, y = S.metric==="out"? obj.out.slice() : beBeta(obj.out, obj.be);
      if(S.xL||S.xN){ const sL=obj.slipL, sN=obj.slipN;    // repo half-spread: pay more on long leg / earn less on short
        y = y.map((v,i)=>{ if(v==null) return null; let a=v; if(sL&&sL[i]!=null) a-=S.xL/100*sL[i];
          if(S.metric!=="out" && sN && sN[i]!=null) a-=S.beta*S.xN/100*sN[i]; return a; }); }
      if(S.energy && obj.crudeB && obj.crudeCum){          // Brent energy hedge: subtract h * cumulative crude
        const cc=obj.crudeCum, h=S.metric==="out"?obj.crudeB[0]:(obj.crudeB[0]-S.beta*obj.crudeB[1]);
        y = y.map((v,i)=> (v==null||cc[i]==null)?v : v - h*cc[i]/1e5); }
      if(S.start||S.end){ const nx=[],ny=[]; for(let j=0;j<xs.length;j++){ if((!S.start||xs[j]>=S.start)&&(!S.end||xs[j]<=S.end)){ nx.push(xs[j]); ny.push(y[j]); } } xs=nx; y=ny; }
      let base=null; for(const v of y){ if(v!=null){ base=v; break; } }        // rebase to window start
      if(base!=null) y=y.map(v=> v==null?null:Math.round((v-base)*10)/10);
      if(S.pos==="short") y=y.map(v=> v==null?null:-v);                          // short = mirror
      const has=y.some(v=>v!=null); if(has) anyMetric=true; if(!sel.has(k)||!has) return; any=true;
      traces.push({x:xs,y:y,name:lab,mode:"lines",type:"scattergl",line:{color:c,width:1.6},connectgaps:false,
        hovertemplate:"%{x}  <b>%{y}</b> bp<extra>"+lab+"</extra>"});
      let lv=null,cnt=0; for(let j=0;j<y.length;j++){ if(y[j]!=null){lv=y[j];cnt++;} } tot.push([lab,lv,c,cnt]);
      const pf=perf(y); if(pf) statsRows.push([lab,c,pf]);
    }
  });
  const statsEl=document.getElementById("stats");
  if(S.view==="cum" && statsRows.length){
    statsEl.innerHTML = "<table class='stt'><tr><th>series</th><th>ann ret</th><th>ann vol</th><th>Sharpe</th><th>max DD</th><th>hit %</th></tr>"
    + statsRows.map(r=>`<tr><td style='color:${r[1]}'>${r[0]}</td><td>${r[2].annret>0?'+':''}${r[2].annret}</td><td>${r[2].annvol}</td><td>${r[2].sharpe==null?'–':r[2].sharpe}</td><td>${r[2].mdd}</td><td>${r[2].hit}</td></tr>`).join("")
    + "</table><div class='note' style='padding:2px 0 0'>annualized from weekly bp (rf=0); max DD in bp; over the date‑range window, rebased to 0 at start</div>";
  } else if(S.view==="cycle" && S.cycdisp==="regress" && regRows.length){
    statsEl.innerHTML = "<table class='stt'><tr><th>bucket</th><th>transition</th><th>β</th><th>R²</th><th>t</th><th>n</th></tr>"
    + regRows.map(r=>{const o=r[3], f=(o&&o.n<8)?" ⚠":"", hl=r[2]===S.regpair?";background:#243040":"";
      return `<tr onclick="setRegPair('${r[2]}')" style="cursor:pointer${hl}"><td style='color:${r[1]}'>${r[0]}</td><td>${r[2].replace(">","→")}</td><td>${o?o.beta.toFixed(2):'–'}</td><td>${o?o.r2.toFixed(2):'–'}</td><td>${(o&&o.t!=null)?o.t.toFixed(1):'–'}</td><td>${o?o.n:0}${f}</td></tr>`;}).join("")
    + "</table><div class='note' style='padding:2px 0 0'>Click a row to plot that regression (current: "+S.regpair.replace(">","→")+"). Periods P1:−10→−5, P2:−5→0, P3:0→+5, P4:+5→+10 td. β/R²/t of predicting the later period from the earlier across taps; ⚠ n<8 (thin).</div>";
  } else { statsEl.innerHTML=""; }
  const totlab = S.view==="cal"?"Σ yr":S.view==="cycle"?("+"+S.cycw+"bd"):"latest";
  document.getElementById("totals").innerHTML = any ? tot.slice(0,12).map(t=>
    `<div class="tot"><span class="lab" style="color:${t[2]}">${t[0]} <span style="color:var(--muted)">${totlab} · n=${t[3]}</span></span><b>${t[1]==null?"–":(t[1]>0?"+":"")+t[1]}</b> bp</div>`).join("") : "";
  const NOTES={cum:"Cumulative "+mlab()+". Buckets = constant-maturity (held bond rolls forward, contemporaneous nominal hedge β=1); By bond = per-issue. bp = $/100k-DV01.",
    cycle:"Within-cycle path rebased to 0 on the auction day (x = trading days from the tap), EVENT-pooled around each of THIS bucket's taps (schedule-agnostic; gilts incl). Mean = avg across taps; Box = cross-tap distribution (median/quartiles, min–max whiskers, ◇ mean). Sample = full/5y/3y of taps. Energy hedge subtracts h·Brent (cumulative around each tap) to strip the crude-driven concession.",
    cal:"Per-calendar-month "+mlab()+" — Mean path or Box = distribution ACROSS YEARS for each month. Index-seasonality signature (euro-HICP / UK-RPI accrual); Sample filters years; n=years; Σ yr ≈ mean annual. Energy hedge subtracts h·Brent (h = full-sample crude β, contracts) — see how much seasonality is just oil."};
  document.getElementById("note").textContent=NOTES[S.view];
  if(S.view==="cycle" && !SEAS.cycle[S.market]){ note("Auction-cycle needs a stable schedule — not available for "+DATA[S.market].label+" (gilts auction on scattered days / Germany has no comparator). Try Calendar."); return; }
  if(!any){ note("no "+mlab()+" series"+(anyMetric?" selected":" for this market (outright only)")); return; }
  const L=LAY();
  if(S.view==="cycle" && S.cycdisp==="regress"){ const pp=S.regpair.split(">");
    L.xaxis.title=PLBL[pp[0]]+" bp"; L.yaxis.title=PLBL[pp[1]]+" bp"; L.hovermode="closest";
    L.shapes=[{type:"line",x0:0,x1:0,y0:0,y1:1,yref:"paper",line:{color:"#3d4a58",width:0.6}}]; }
  else if(S.view==="cycle"){ L.xaxis.title="trading days from auction (0 = tap)"; L.yaxis.title="cum "+mlab()+" bp (rebased at auction)";
    L.shapes=[{type:"line",x0:0,x1:0,y0:0,y1:1,yref:"paper",line:{color:"#8b98a5",dash:"dot",width:1}}];
    if(S.cycdisp==="box"){ L.boxmode="group"; L.hovermode="closest"; L.xaxis.range=[-S.cycw-0.6, S.cycw+0.6]; }
    else { L.xaxis.range=[-S.cycw, S.cycw]; } }
  else if(S.view==="cal"){ L.xaxis.title="calendar month"; L.yaxis.title="monthly "+mlab()+" (bp)";
    L.hovermode = S.cycdisp==="box"?"closest":"x unified"; if(S.cycdisp==="box") L.boxmode="group"; }
  else { L.xaxis.type="date"; L.xaxis.rangeslider={visible:true,thickness:0.06,bgcolor:"#1b2430"}; L.yaxis.title="cumulative "+mlab()+" (bp)"; }
  Plotly.react("chart",traces,L,{responsive:true,displaylogo:false,modeBarButtonsToRemove:["lasso2d","select2d","autoScale2d"]});
}

function setSeg(id,a,v,key){ document.querySelectorAll(`#${id} button`).forEach(b=>b.classList.toggle("on",b.dataset[a]===v)); S[key]=v; }
document.querySelectorAll("#view button").forEach(b=>b.onclick=()=>{setSeg("view","v",b.dataset.v,"view"); if(S.view!=="cycle"&&S.cycdisp==="regress") setSeg("cycdisp","d","mean","cycdisp"); buildSeriesList();render();});
document.querySelectorAll("#group button").forEach(b=>b.onclick=()=>{setSeg("group","g",b.dataset.g,"group");buildSeriesList();render();});
document.querySelectorAll("#metric button").forEach(b=>b.onclick=()=>{setSeg("metric","m",b.dataset.m,"metric");render();});
document.querySelectorAll("#cycwin button").forEach(b=>b.onclick=()=>{document.querySelectorAll("#cycwin button").forEach(x=>x.classList.toggle("on",x===b));S.cycw=+b.dataset.w;render();});
document.querySelectorAll("#samp button").forEach(b=>b.onclick=()=>{setSeg("samp","s",b.dataset.s,"samp");render();});
document.querySelectorAll("#cycdisp button").forEach(b=>b.onclick=()=>{setSeg("cycdisp","d",b.dataset.d,"cycdisp");render();});
document.getElementById("beta").oninput=e=>{S.beta=+e.target.value; document.getElementById("bv").textContent=S.beta.toFixed(2); render();};
document.getElementById("energy").onchange=e=>{S.energy=e.target.checked; render();};
document.querySelectorAll("#pos button").forEach(b=>b.onclick=()=>{setSeg("pos","p",b.dataset.p,"pos");render();});
document.getElementById("start").onchange=e=>{S.start=e.target.value||null; render();};
document.getElementById("end").onchange=e=>{S.end=e.target.value||null; render();};
document.getElementById("xl").oninput=e=>{S.xL=+e.target.value; document.getElementById("xlv").textContent=S.xL.toFixed(1); render();};
document.getElementById("xn").oninput=e=>{S.xN=+e.target.value; document.getElementById("xnv").textContent=S.xN.toFixed(1); render();};
window.setRegPair=function(p){ S.regpair=p; render(); };   // click a regression-table row to plot that pair
document.getElementById("all").onclick=()=>{ensureSel();curItems().forEach(it=>S.sel[selKey()].add(it[0]));buildSeriesList();render();};
document.getElementById("none").onclick=()=>{S.sel[selKey()]=new Set();buildSeriesList();render();};
const msel=document.getElementById("market");
ORDER.forEach(m=>{const o=document.createElement("option");o.value=m;o.textContent=DATA[m].label;msel.appendChild(o);});
msel.onchange=()=>{S.market=msel.value;buildSeriesList();render();};
buildSeriesList(); render();
</script></body></html>
"""


def build(open_browser=True, path=None):
    try:                                                       # full-sample Brent hedge betas (once, shared)
        hm = en.hedge_map()
    except Exception as e:
        print(f"  (energy hedge map unavailable: {e})"); hm = {}
    payload = build_payload(hm)
    if not payload:
        print("  no intl CMT/return caches found — run cmt_intl.py first"); return
    seas = sea.build()
    try:                                                       # attach the Brent energy hedge (cal + cycle)
        seas["crude_monthly"] = en.crude_monthly()
        for grp in ("calendar", "cycle"):
            for m, bs in seas.get(grp, {}).items():
                for b, obj in bs.items():
                    obj["crudeB"] = hm.get(m, {}).get(b)       # [bL, bN] full-sample crude betas, or None
    except Exception as e:
        print(f"  (energy hedge unavailable: {e})"); seas["crude_monthly"] = {}
    order = [m for m in MKT_ORDER if m in payload]
    html = (HTML.replace("__PLOTLY__", plotly_tag())
                .replace("__PAYLOAD__", json.dumps(payload))
                .replace("__SEAS__", json.dumps(seas))
                .replace("__ORDER__", json.dumps(order)))
    path = path or os.path.join(HERE, "dashboard_intl.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    nb = sum(len(payload[m]["buckets"]) for m in order); nbond = sum(len(payload[m]["bonds"]) for m in order)
    ncy = sum(len(v) for v in seas["cycle"].values())
    print(f"  wrote {path}  ({len(order)} markets, {nb} buckets, {nbond} bonds, {ncy} auction-cycles, {os.path.getsize(path)//1024} KB)")
    if open_browser:
        try:
            webbrowser.open("file://" + path.replace("\\", "/"))
        except Exception:
            pass


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    build(open_browser="--no-open" not in sys.argv)
