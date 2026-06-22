"""
Build a self-contained interactive HTML dashboard for the breakeven return series.

Why HTML: the repo bid/offer effect is LINEAR in the half-spread, so every recomputation
(slider move, date range, tenor switch) is a trivial client-side vector op -> instant, no
server, no lag. Plotly.js for charts (zoom/pan/hover), native HTML date pickers + range
sliders, a clean CSS layout, a sortable raw-numbers table (daily or monthly), live net P&L,
and a one-click CSV download of the current window.

Output: dashboard.html  (open in any browser; Plotly is embedded so it works offline).

Run:  python dashboard.py        # builds dashboard.html and opens it
"""
from __future__ import annotations
import os, sys, json, webbrowser
import pandas as pd

import engine

HERE = os.path.dirname(os.path.abspath(__file__))
TENORS = ["5y", "10y", "30y"]
PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"


def build_payload():
    """Per tenor: only days where the breakeven is defined (both legs trading)."""
    data = {}
    for t in TENORS:
        p = os.path.join(engine.CACHE, f"returns_{t}.parquet")
        if not os.path.exists(p):
            continue
        d = engine.load_returns(t).dropna(subset=["r_BE_bp"])
        data[t] = {
            "dates": [x.strftime("%Y-%m-%d") for x in d.index],
            "rT": [round(float(v), 4) for v in d["r_TIPS_bp"]],
            "rU": [round(float(v), 4) for v in d["r_UST_bp"]],
            "rBE": [round(float(v), 4) for v in d["r_BE_bp"]],
            "sT": [round(float(v), 6) for v in d["s_TIPS"]],
            "sU": [round(float(v), 6) for v in d["s_UST"]],
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
  .side{background:var(--panel);border-right:1px solid var(--line);padding:16px;overflow:auto}
  .main{display:flex;flex-direction:column;min-width:0}
  h1{font-size:15px;margin:0 0 14px}
  .grp{margin-bottom:16px}
  .grp>label{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px}
  .seg{display:flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}
  .seg button{flex:1;background:transparent;color:var(--ink);border:0;padding:7px 4px;cursor:pointer;font-size:12px}
  .seg button.on{background:var(--accent);color:#fff}
  .row{display:flex;align-items:center;gap:8px;margin:6px 0}
  input[type=range]{width:100%}
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
</style></head>
<body><div class="app">
  <div class="side">
    <h1>Breakeven financed TR</h1>
    <div class="grp"><label>Tenor</label><div class="seg" id="tenor"></div></div>
    <div class="grp"><label>Repo half-spread x_TIPS <span class="hl" id="xtv">3.0</span> bp</label>
      <input type="range" id="xt" min="0" max="25" step="0.5" value="3"></div>
    <div class="grp"><label>Repo half-spread x_UST <span class="hl" id="xuv">3.0</span> bp</label>
      <input type="range" id="xu" min="0" max="25" step="0.5" value="3"></div>
    <div class="grp"><label>Date range</label>
      <div class="row"><input type="date" id="start"></div>
      <div class="row"><input type="date" id="end"></div>
      <div class="seg" id="range" style="margin-top:6px"><button data-range="full">Full</button>
        <button data-range="5y">5y</button><button data-range="1y">1y</button><button data-range="ytd">YTD</button></div></div>
    <div class="grp"><label>View</label><div class="seg" id="view">
      <button data-view="chart" class="on">Chart</button><button data-view="table">Table</button></div></div>
    <div class="grp"><label>Table frequency</label><div class="seg" id="freq">
      <button data-freq="monthly" class="on">Monthly</button><button data-freq="daily">Daily</button></div></div>
    <button class="act" id="dl">Download CSV (window)</button>
    <p class="note">Long-BE = long TIPS / short UST. Both directions carry the slippage
      (long pays GC+x, short earns GC&minus;x), so they are not mirror images. Mid = zero spread.
      Specialness not modeled. Full hand-replication data: <code>python export.py</code>.</p>
  </div>
  <div class="main">
    <div class="totals">
      <div class="tot l"><span class="lab">long breakeven</span><b id="tl">+0</b> bp</div>
      <div class="tot s"><span class="lab">short breakeven</span><b id="ts">+0</b> bp</div>
      <div class="tot m"><span class="lab">mid (x=0)</span><b id="tm">+0</b> bp</div>
      <div class="tot"><span class="lab">window</span><b id="twin" style="font-size:13px">&mdash;</b></div>
    </div>
    <div id="chart"></div>
    <div class="tablewrap" id="tablewrap" style="display:none"><table id="tbl"></table><div class="note" id="tnote"></div></div>
  </div>
</div>
<script>
const DATA = __DATA__;
const TENORS = Object.keys(DATA);
const S = {tenor: TENORS.includes("10y")?"10y":TENORS[0], xT:3, xU:3, start:null, end:null, view:"chart", freq:"monthly"};
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
function render(){
  const s = series();
  const last = a => a.length?a[a.length-1]:0;
  $("tl").textContent=fmt(last(s.cL),0); $("ts").textContent=fmt(last(s.cS),0); $("tm").textContent=fmt(last(s.cM),0);
  $("twin").textContent = s.dates.length ? (s.dates[0]+"  →  "+s.dates[s.dates.length-1]+"  ("+s.dates.length+" days)") : "—";
  if(S.view==="chart"){ $("chart").style.display=""; $("tablewrap").style.display="none"; drawChart(s); }
  else { $("chart").style.display="none"; $("tablewrap").style.display=""; drawTable(s); }
}
function drawChart(s){
  const tr=(y,name,color)=>({x:s.dates,y:y,name:name,mode:"lines",line:{width:1.6,color:color},hovertemplate:"%{y:+.1f} bp<extra>"+name+"</extra>"});
  const data=[tr(s.cM,"mid","#8b98a5"),tr(s.cL,"long BE","#3fb950"),tr(s.cS,"short BE","#f85149")];
  const layout={paper_bgcolor:"#0f1419",plot_bgcolor:"#0f1419",font:{color:"#e6edf3"},margin:{l:55,r:20,t:30,b:40},
    title:{text:S.tenor+" breakeven cumulative net P&L (bp, linear-sum)",font:{size:14}},
    xaxis:{gridcolor:"#2d3a48"},yaxis:{gridcolor:"#2d3a48",zeroline:true,zerolinecolor:"#3a4a5a",title:"bp"},
    hovermode:"x unified",legend:{orientation:"h",y:1.08}};
  Plotly.react("chart",data,layout,{responsive:true,displaylogo:false});
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
seg("view","view"); seg("freq","freq");
$("xt").oninput=e=>{S.xT=+e.target.value; $("xtv").textContent=S.xT.toFixed(1); render();};
$("xu").oninput=e=>{S.xU=+e.target.value; $("xuv").textContent=S.xU.toFixed(1); render();};
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
    build(open_browser="--no-open" not in sys.argv)
