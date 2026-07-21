"""
eventflow.html — self-contained dashboard for the weekend/headline de-risking study.

Views:
  Windows    mean return per clock window (bars + stats), optional split by headline regime
  Cumulative running sum of each window's weekly return = the naive strategy equity curves
  Heatmap    mean hourly return by (day-of-week x 2h ET bucket) — where in the week it moves
  News clock headline volume by ET hour-of-day (Q4: do the 'attacks' cluster after 15:00?)

Instruments: TU / TY / US / WN outrights + CURVE (TU - beta*US, parallel risk stripped).
Headline regime: weeks split hi/lo by median iran-news volume (needs eventflow/pull_news.py
cache; sections auto-hide until it exists).

Run:  python eventflow/dashboard_eventflow.py        -> eventflow.html (repo root)
"""
from __future__ import annotations
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from eventflow.common import TZ
from eventflow import pull_bars, pull_news, pull_news_sources, windows as WD

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WINDOW_COLS = ["thu_into_ldn", "ldn_to_us", "weekend_gap", "mon_ldn"]


def build_payload():
    tu, ty, us, wn = (WD.closes_et(s) for s in ("TU", "TY", "US", "WN"))
    beta = WD.curve_beta(tu, us)
    frames = {"TU": tu, "TY": ty, "US": us, "WN": wn}
    weekly = {k: WD.week_windows(s) for k, s in frames.items()}
    weekly["CURVE"] = (weekly["TU"].astype(float) - beta * weekly["US"].astype(float)).round(2)

    inst = {}
    for k, w in weekly.items():
        inst[k] = {"weeks": list(w.index),
                   **{c: [None if pd.isna(v) else float(v) for v in w[c]] for c in WINDOW_COLS}}

    heat = {}
    for k, s in frames.items():
        g, n = WD.dow_hour_grid(s)
        heat[k] = {"z": [[None if pd.isna(v) else float(v) for v in row]
                         for row in g.reindex(index=range(7)).values.tolist()],
                   "hours": [int(h) for h in g.columns]}

    news = {}
    for q in ("iran", "trump"):
        d = pull_news.load(q)
        if d is None or d.empty:
            continue
        v = d["vol"].copy()
        v.index = v.index.tz_convert(TZ)
        # hour-of-day clock (weekdays only), and weekly Thu->Wed intensity for the regime split
        wk = v[v.index.dayofweek < 5]
        intraday = wk[(wk.index.hour != 0) | (wk.index.minute != 0)]   # drop daily-resolution rows
        # the clock needs INTRADAY stamps; daily-coarsened points all sit at 00:00 and would lie
        clock = (intraday.groupby(intraday.index.hour).mean().reindex(range(24))
                 if len(intraday) > 50 else pd.Series(index=range(24), dtype=float))
        weekly_int = v.resample("W-FRI").mean()
        news[q] = {"clock": [None if pd.isna(x) else round(float(x), 3) for x in clock],
                   "weeks": [t.strftime("%Y-%m-%d") for t in weekly_int.index],
                   "vol": [None if pd.isna(x) else round(float(x), 3) for x in weekly_int]}

    # article-level timestamps from NYT / Guardian (eventflow/pull_news_sources.py) — the clock
    # from these is actual publish times of individual articles, cleaner than GDELT's volume index
    arts = {}
    for src in ("nyt", "guardian"):
        for q in ("iran", "trump"):
            d = pull_news_sources.load(src, q)
            if d is None or len(d) < 30:
                continue
            ts = pd.DatetimeIndex(d["ts"]).tz_convert(TZ)
            wk = ts[ts.dayofweek < 5]
            c = pd.Series(1, index=wk).groupby(wk.hour).sum().reindex(range(24)).fillna(0)
            arts[f"{src} {q}"] = {"clock": [round(float(x), 2) for x in c / c.sum() * 100],
                                  "n": int(c.sum())}

    return {"INST": inst, "HEAT": heat, "NEWS": news, "ARTS": arts, "BETA": round(beta, 3),
            "BOSS_START": WD.BOSS_START, "ASOF": pd.Timestamp.today().strftime("%Y-%m-%d")}


def plotly_tag():
    sys.path.insert(0, ROOT)
    import dashboard_intl
    return dashboard_intl.plotly_tag()


HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Event flow — weekend de-risking</title>
__PLOTLY__
<style>
:root{--bg:#0f1419;--panel:#1b2430;--ink:#e6edf3;--muted:#8b98a5;--line:#2d3a48;--accent:#2f81f7}
*{box-sizing:border-box}
body{margin:0;font:13px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink);height:100vh;display:flex}
.side{width:280px;background:var(--panel);border-right:1px solid var(--line);padding:14px;overflow:auto}
.main{flex:1;display:flex;flex-direction:column;min-width:0}
h1{font-size:15px;margin:0 0 12px}
.grp{margin-bottom:14px}
.grp>label{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em;margin-bottom:5px}
.seg{display:flex;border:1px solid var(--line);border-radius:6px;overflow:hidden;flex-wrap:wrap}
.seg button{flex:1;background:transparent;color:var(--ink);border:0;padding:7px 4px;cursor:pointer;font-size:12px;min-width:46px}
.seg button.on{background:var(--accent);color:#fff}
#chart{flex:1;min-height:0}
#stats{padding:8px 14px;border-top:1px solid var(--line);max-height:32vh;overflow:auto}
table.stt{border-collapse:collapse;font-size:12px}
.stt th,.stt td{padding:3px 10px;text-align:right;border-bottom:1px solid var(--line)}
.stt th{color:var(--muted);font-weight:600}.stt td:first-child,.stt th:first-child{text-align:left}
.note{color:var(--muted);font-size:11.5px;padding:6px 14px}
</style></head><body>
<div class="side">
  <h1>Event flow — weekend de-risking</h1>
  <div class="grp"><label>View</label><div class="seg" id="view">
    <button data-v="win" class="on">Windows</button><button data-v="cum">Cumulative</button>
    <button data-v="heat">Heatmap</button><button data-v="clock">News clock</button></div></div>
  <div class="grp"><label>Instrument</label><div class="seg" id="inst">
    <button data-i="TU" class="on">TU 2y</button><button data-i="TY">TY 10y</button>
    <button data-i="US">US 30y</button><button data-i="WN">WN ultra</button>
    <button data-i="CURVE">Curve</button></div></div>
  <div class="grp"><label>Sample</label><div class="seg" id="samp">
    <button data-s="full" class="on">Full (2024→)</button><button data-s="boss">Mar-26→</button></div></div>
  <div class="grp" id="splitgrp" style="display:none"><label>Headline regime (iran)</label><div class="seg" id="split">
    <button data-h="off" class="on">Off</button><button data-h="on">Hi vs Lo weeks</button></div></div>
  <div class="grp"><label>Position</label><div class="seg" id="pos">
    <button data-p="long" class="on">Long</button><button data-p="short">Short</button></div></div>
</div>
<div class="main">
  <div id="chart"></div>
  <div id="stats"></div>
  <div class="note" id="note"></div>
</div>
<script>
const P=__PAYLOAD__;
const WCOLS=["thu_into_ldn","ldn_to_us","weekend_gap","mon_ldn"];
const WLAB={thu_into_ldn:"Thu17→Fri Ldn close",ldn_to_us:"Ldn→US close (Fri)",weekend_gap:"Weekend gap",mon_ldn:"Mon London"};
const S={view:"win",inst:"TU",samp:"full",split:"off",pos:"long"};
const hasNews=!!P.NEWS.iran;
document.getElementById("splitgrp").style.display=hasNews?"":"none";
// weekly iran intensity lookup for regime split
let hiWeek=null;
if(hasNews){ const w=P.NEWS.iran.weeks,v=P.NEWS.iran.vol;
  const pairs=w.map((d,i)=>[d,v[i]]).filter(x=>x[1]!=null);
  const med=pairs.map(x=>x[1]).sort((a,b)=>a-b)[pairs.length>>1];
  hiWeek={}; pairs.forEach(([d,x])=>hiWeek[d]=x>=med); }
function mean(a){return a.length?a.reduce((x,y)=>x+y,0)/a.length:null;}
function tstat(a){const n=a.length;if(n<4)return null;const m=mean(a);
  const sd=Math.sqrt(a.reduce((s,x)=>s+(x-m)**2,0)/(n-1));return sd?m/(sd/Math.sqrt(n)):null;}
function sgn(){return S.pos==="short"?-1:1;}
function weeksOf(){ const d=P.INST[S.inst], keep=[];
  for(let i=0;i<d.weeks.length;i++){
    if(S.samp==="boss"&&d.weeks[i]<P.BOSS_START)continue;
    keep.push(i); } return keep; }
function seriesFor(c,regime){ const d=P.INST[S.inst], out=[];
  for(const i of weeksOf()){ const v=d[c][i]; if(v==null)continue;
    if(regime!=null&&hiWeek){ const h=hiWeek[d.weeks[i]]; if(h==null||h!==regime)continue; }
    out.push(sgn()*v); } return out; }
function render(){
  let traces=[],layout={},rows="";
  const dark={paper_bgcolor:"#0f1419",plot_bgcolor:"#0f1419",font:{color:"#e6edf3",size:12},
    margin:{l:60,r:24,t:36,b:46}};
  if(S.view==="win"){
    const regimes=(S.split==="on"&&hasNews)?[["hi",true,"#e63946"],["lo",false,"#3fb950"]]:[["all",null,"#2f81f7"]];
    for(const [lab,reg,col] of regimes){
      const ys=[],errs=[];
      for(const c of WCOLS){ const a=seriesFor(c,reg); const m=mean(a);
        ys.push(m==null?null:Math.round(m*100)/100);
        errs.push(a.length>3?1.96*Math.sqrt(a.reduce((s,x)=>s+(x-mean(a))**2,0)/(a.length-1))/Math.sqrt(a.length):null); }
      traces.push({x:WCOLS.map(c=>WLAB[c]),y:ys,name:lab,type:"bar",marker:{color:col},
        error_y:{type:"data",array:errs,color:"#8b98a5",thickness:1},
        hovertemplate:"%{x}: <b>%{y}</b> bp<extra>"+lab+"</extra>"});
    }
    layout={...dark,barmode:"group",showlegend:S.split==="on",legend:{orientation:"h",y:1.1},
      title:{text:S.inst+" — mean "+(S.pos)+" return per clock window (bp of price, 95% CI)",font:{size:13}},
      yaxis:{gridcolor:"#2d3a48",zerolinecolor:"#4d5a68",title:"bp"},xaxis:{gridcolor:"#2d3a48"}};
    rows="<table class='stt'><tr><th>window</th><th>regime</th><th>n</th><th>mean bp</th><th>t</th><th>hit%</th></tr>";
    for(const c of WCOLS) for(const [lab,reg] of (S.split==="on"&&hasNews?[["hi",true],["lo",false]]:[["all",null]])){
      const a=seriesFor(c,reg), t=tstat(a);
      rows+=`<tr><td>${WLAB[c]}</td><td>${lab}</td><td>${a.length}</td><td>${a.length?mean(a).toFixed(2):"–"}</td>
        <td>${t?t.toFixed(2):"–"}</td><td>${a.length?Math.round(100*a.filter(x=>x>0).length/a.length):"–"}</td></tr>`; }
    rows+="</table>";
  } else if(S.view==="cum"){
    const d=P.INST[S.inst], cols=["#2f81f7","#f5a623","#e63946","#3fb950"];
    WCOLS.forEach((c,ci)=>{ const xs=[],ys=[]; let run=0;
      for(const i of weeksOf()){ const v=d[c][i]; if(v==null)continue; run+=sgn()*v; xs.push(d.weeks[i]); ys.push(Math.round(run*10)/10); }
      traces.push({x:xs,y:ys,name:WLAB[c],mode:"lines",line:{color:cols[ci],width:1.8},
        hovertemplate:"%{x} <b>%{y}</b> bp<extra>"+WLAB[c]+"</extra>"}); });
    layout={...dark,showlegend:true,legend:{orientation:"h",y:1.12},
      title:{text:S.inst+" — cumulative "+(S.pos)+" P&L per window strategy (bp of price)",font:{size:13}},
      yaxis:{gridcolor:"#2d3a48",zerolinecolor:"#4d5a68",title:"bp"},xaxis:{gridcolor:"#2d3a48"}};
  } else if(S.view==="heat"){
    const h=P.HEAT[S.inst==="CURVE"?"TU":S.inst];
    const days=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"];
    traces.push({z:h.z,x:h.hours.map(x=>x+":00"),y:days,type:"heatmap",
      colorscale:[[0,"#e63946"],[0.5,"#0f1419"],[1,"#3fb950"]],zmid:0,
      colorbar:{tickfont:{color:"#8b98a5"}},hovertemplate:"%{y} %{x} ET: %{z} bp/h<extra></extra>"});
    layout={...dark,title:{text:(S.inst==="CURVE"?"TU":S.inst)+" — mean hourly return by day × 2h ET bucket (bp)",font:{size:13}},
      xaxis:{title:"ET hour"},yaxis:{autorange:"reversed"}};
    rows="";
  } else {   // news clock
    const hasArts=Object.keys(P.ARTS||{}).length>0;
    if(!hasNews && !hasArts){ rows="<i>news cache not built yet — run eventflow/pull_news.py / pull_news_sources.py</i>"; }
    else{
      const hrs=[...Array(24).keys()].map(h=>h+":00");
      for(const [q,col] of [["iran","#e63946"],["trump","#f5a623"]]){
        if(!P.NEWS[q])continue;
        traces.push({x:hrs,y:P.NEWS[q].clock,name:"GDELT "+q,type:"bar",
          marker:{color:col},opacity:0.75,hovertemplate:"%{x} ET: %{y}<extra>GDELT "+q+"</extra>"}); }
      const acol={"nyt iran":"#ff8fa3","guardian iran":"#c9184a","nyt trump":"#ffd166","guardian trump":"#e07a00"};
      for(const [k,a] of Object.entries(P.ARTS||{})){
        traces.push({x:hrs,y:a.clock,name:k+" ("+a.n+" articles)",type:"scatter",mode:"lines+markers",
          line:{color:acol[k]||"#8b98a5",width:2},marker:{size:5},
          hovertemplate:"%{x} ET: %{y}% of articles<extra>"+k+"</extra>"}); }
      layout={...dark,barmode:"group",showlegend:true,legend:{orientation:"h",y:1.12},
        title:{text:"Headlines by ET hour of day (weekdays) — does the news land after 15:00?",font:{size:13}},
        yaxis:{gridcolor:"#2d3a48",title:"% (bars: GDELT vol · lines: share of articles)"},
        xaxis:{gridcolor:"#2d3a48"},
        shapes:[{type:"line",x0:"15:00",x1:"15:00",y0:0,y1:1,yref:"paper",line:{color:"#e6edf3",dash:"dot",width:1.5}}]};
      rows="";
    }
  }
  Plotly.react("chart",traces,layout,{responsive:true,displaylogo:false});
  document.getElementById("stats").innerHTML=rows||"";
  document.getElementById("note").textContent=
    "Hourly CME futures (Yahoo), 2024→"+P.ASOF+". CURVE = TU − "+P.BETA+"·US (parallel risk stripped). "+
    "Units = bp of futures PRICE (≈1 TU tick = 0.39bp); no transaction costs. Windows in ET; London close ≈ 11:30 ET. "+
    "Private — do not forward.";
}
document.querySelectorAll("#view button").forEach(b=>b.onclick=()=>{S.view=b.dataset.v;
  document.querySelectorAll("#view button").forEach(x=>x.classList.toggle("on",x===b));render();});
document.querySelectorAll("#inst button").forEach(b=>b.onclick=()=>{S.inst=b.dataset.i;
  document.querySelectorAll("#inst button").forEach(x=>x.classList.toggle("on",x===b));render();});
document.querySelectorAll("#samp button").forEach(b=>b.onclick=()=>{S.samp=b.dataset.s;
  document.querySelectorAll("#samp button").forEach(x=>x.classList.toggle("on",x===b));render();});
document.querySelectorAll("#split button").forEach(b=>b.onclick=()=>{S.split=b.dataset.h;
  document.querySelectorAll("#split button").forEach(x=>x.classList.toggle("on",x===b));render();});
document.querySelectorAll("#pos button").forEach(b=>b.onclick=()=>{S.pos=b.dataset.p;
  document.querySelectorAll("#pos button").forEach(x=>x.classList.toggle("on",x===b));render();});
render();
</script></body></html>"""


def build(open_browser=False, path=None):
    payload = build_payload()
    html = (HTML.replace("__PLOTLY__", plotly_tag())
                .replace("__PAYLOAD__", json.dumps(payload, separators=(",", ":"))))
    path = path or os.path.join(ROOT, "eventflow.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    news = "with" if payload["NEWS"] else "WITHOUT"
    print(f"  wrote {path}  ({len(payload['INST'])} instruments, {news} news, "
          f"{os.path.getsize(path) // 1024} KB)")
    if open_browser:
        import webbrowser
        webbrowser.open("file://" + path)
    return path


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    build(open_browser="--no-open" not in sys.argv)
