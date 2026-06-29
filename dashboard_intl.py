"""
Baseline visualizer for the European / UK linker return series (self-contained dashboard_intl.html).

Two views (no seasonal yet -- the auction/calendar conventions differ per market, see cmt_intl):
  * Buckets  -- constant-maturity cumulative returns per remaining-tenor bucket (the analogue of the
                US fixed-maturity breakeven chart). Breakeven leg uses the CONTEMPORANEOUS nominal
                hedge (closest-maturity then-existing, beta=1); outright = the financed linker leg.
  * By bond  -- the single cumulative return series per individual bond (outright always; breakeven
                where a street comparator exists), so you can eyeball returns issue-by-issue.

Metric toggle (Breakeven / Outright) + per-series checkboxes + market switch. Cumulative units are
engine bp (= $/100k-DV01 P&L). Weekly-sampled for a light, snappy file. Reads the cmt_intl / engine
caches as-is (run cmt_intl.py first to refresh).

Run:  python dashboard_intl.py            # build dashboard_intl.html and open it
      python dashboard_intl.py --no-open  # build only
"""
from __future__ import annotations
import os, sys, json, webbrowser
import pandas as pd

import linkers
import buckets_intl as bk

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = linkers.CACHE
CMT_DIR = os.path.join(CACHE, "cmt")
PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"
MKT_ORDER = ["IT_BTPEI", "FR_OATEI", "FR_OATI", "ES_EI", "UK_3M", "DE_EI"]


def _weekly(s):
    """Weekly (Fri) sample of a cumulative series -> light payload; None if empty."""
    s = pd.to_numeric(s, errors="coerce").dropna()
    if s.empty:
        return None
    return s.resample("W-FRI").last().dropna()


def _series(cum, base_idx):
    return [None if pd.isna(v) else int(round(v)) for v in cum.reindex(base_idx).values]


def build_payload():
    u = linkers.load_universe(include_deferred=True)
    u["mat"] = pd.to_datetime(u["maturity"])
    out = {}
    for m in MKT_ORDER:
        if m not in bk.MARKETS:
            continue
        entry = {"label": bk.MARKETS[m], "buckets": {}, "bonds": {}}
        # --- constant-maturity buckets ---
        for b in bk.ORDER:
            p = os.path.join(CMT_DIR, f"{m}__{b}.parquet")
            if not os.path.exists(p):
                continue
            d = pd.read_parquet(p)
            ot = _weekly(d["cum_linker_bp"])
            if ot is None:
                continue
            has_be = "r_BE_bp" in d and d["r_BE_bp"].notna().any()
            be = _weekly(d["cum_BE_bp"]) if has_be else None
            x = [t.strftime("%Y-%m-%d") for t in ot.index]
            entry["buckets"][b] = {"x": x, "out": _series(ot, ot.index),
                                   "be": _series(be, ot.index) if be is not None else None}
        # --- per bond ---
        for _, r in u[u["market"] == m].sort_values("mat").iterrows():
            isin = r["isin"]
            rp = os.path.join(CACHE, "returns", f"{isin}.parquet")
            if not os.path.exists(rp):
                continue
            rr = pd.read_parquet(rp)
            if "bp" not in rr:
                continue
            ot = _weekly(rr["bp"].cumsum())
            if ot is None:
                continue
            be = None
            bpath = os.path.join(CACHE, "breakeven", f"{isin}.parquet")
            if os.path.exists(bpath):
                bb = pd.read_parquet(bpath)
                if "cum_BE_bp" in bb:
                    be = _weekly(bb["cum_BE_bp"])
            x = [t.strftime("%Y-%m-%d") for t in ot.index]
            entry["bonds"][isin] = {"label": str(r["desc"]), "x": x, "out": _series(ot, ot.index),
                                    "be": _series(be, ot.index) if be is not None else None}
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
.side{width:280px;background:var(--panel);border-right:1px solid var(--line);padding:16px;overflow:auto;user-select:none}
.main{flex:1;display:flex;flex-direction:column;min-width:0}
h1{font-size:15px;margin:0 0 14px}
.grp{margin-bottom:16px}
.grp>label{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px}
select{width:100%;background:#0f1722;color:var(--ink);border:1px solid var(--line);border-radius:5px;padding:7px}
.seg{display:flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.seg button{flex:1;background:transparent;color:var(--ink);border:0;padding:7px 4px;cursor:pointer;font-size:12px}
.seg button.on{background:var(--accent);color:#fff}
.chk{display:flex;align-items:center;gap:7px;padding:3px 2px;cursor:pointer;font-size:12px}
.chk input{accent-color:var(--accent)}
.chk .sw{width:11px;height:11px;border-radius:2px;flex:none}
.mini{display:flex;gap:8px;margin-bottom:6px}
.mini button{flex:1;background:transparent;border:1px solid var(--line);color:var(--muted);border-radius:5px;padding:4px;cursor:pointer;font-size:11px}
#totals{display:flex;gap:18px;flex-wrap:wrap;padding:12px 18px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
.tot b{font-size:16px}.tot .lab{color:var(--muted);font-size:11px;display:block}
#chart{flex:1;min-height:0}
.note{color:var(--muted);font-size:11px;padding:8px 18px}
</style></head>
<body>
<div class="side">
  <h1>Intl linker returns</h1>
  <div class="grp"><label>Market</label><select id="market"></select></div>
  <div class="grp"><label>View</label><div class="seg" id="view">
    <button data-v="buckets" class="on">Constant-maturity</button><button data-v="bonds">By bond</button></div></div>
  <div class="grp"><label>Metric</label><div class="seg" id="metric">
    <button data-m="be" class="on">Breakeven</button><button data-m="out">Outright</button></div></div>
  <div class="grp"><label id="serlab">Series</label>
    <div class="mini"><button id="all">All</button><button id="none">None</button></div>
    <div id="series"></div></div>
</div>
<div class="main">
  <div id="totals"></div>
  <div id="chart"></div>
  <div class="note" id="note"></div>
</div>
<script>
const DATA = __PAYLOAD__;
const ORDER = __ORDER__;
const COLORS = ["#2f81f7","#3fb950","#e3b341","#f0883e","#db61a2","#a371f7","#56d4dd","#f85149",
                "#7ee787","#ffa657","#bc8cff","#79c0ff","#d29922","#ff7b72","#39c5cf","#e6edf3"];
const S = {market:ORDER[0], view:"buckets", metric:"be", sel:{}};

function curEntry(){ return DATA[S.market]; }
function curItems(){  // [ [key,label,obj], ... ] in display order
  const e=curEntry();
  if(S.view==="buckets") return BORDER.filter(b=>e.buckets[b]).map(b=>[b,b,e.buckets[b]]);
  return Object.keys(e.bonds).map(k=>[k,e.bonds[k].label,e.bonds[k]]);
}
const BORDER=["2y","5y","7y","10y","12y","15y","20y","25y","30y","40y","50y"];

function selKey(){ return S.market+"|"+S.view; }
function ensureSel(){ if(!S.sel[selKey()]){ S.sel[selKey()]=new Set(curItems().map(it=>it[0])); } return S.sel[selKey()]; }

function buildSeriesList(){
  const sel=ensureSel(), box=document.getElementById("series"), items=curItems();
  document.getElementById("serlab").textContent = S.view==="buckets" ? "Buckets" : "Bonds (by maturity)";
  box.innerHTML="";
  items.forEach((it,i)=>{
    const [k,lab]=it, c=COLORS[i%COLORS.length];
    const row=document.createElement("label"); row.className="chk";
    row.innerHTML=`<input type="checkbox" ${sel.has(k)?"checked":""}><span class="sw" style="background:${c}"></span><span>${lab}</span>`;
    row.querySelector("input").onchange=e=>{ e.target.checked?sel.add(k):sel.delete(k); render(); };
    box.appendChild(row);
  });
}

function render(){
  const sel=ensureSel(), items=curItems(), traces=[], tot=[];
  let any=false, anyMetric=false;
  items.forEach((it,i)=>{
    const [k,lab,obj]=it; const y=obj[S.metric];
    if(y) anyMetric=true;
    if(!sel.has(k) || !y) return;
    any=true;
    traces.push({x:obj.x, y:y, name:lab, mode:"lines", type:"scattergl",
                 line:{color:COLORS[i%COLORS.length], width:1.6}, connectgaps:false,
                 hovertemplate:"%{x}  <b>%{y}</b> bp<extra>"+lab+"</extra>"});
    let lastv=null; for(let j=y.length-1;j>=0;j--){ if(y[j]!=null){lastv=y[j];break;} }
    tot.push([lab, lastv, COLORS[i%COLORS.length]]);
  });
  const mlab = S.metric==="be" ? "breakeven" : "outright";
  document.getElementById("totals").innerHTML = tot.slice(0,12).map(t=>
    `<div class="tot"><span class="lab" style="color:${t[2]}">${t[0]}</span><b>${t[1]==null?"–":(t[1]>0?"+":"")+t[1]}</b> bp</div>`).join("")
    || `<div class="note">no ${mlab} series selected</div>`;
  document.getElementById("note").textContent = S.view==="buckets"
    ? "Constant-maturity buckets: held bond rolls forward to the band's newest line; breakeven hedged with the contemporaneous closest-maturity nominal (β=1). Cumulative bp = $/100k-DV01."
    : "Per-bond cumulative financed return; breakeven (where shown) uses the street comparator. Cumulative bp = $/100k-DV01.";
  const lay={paper_bgcolor:"#0f1419",plot_bgcolor:"#0f1419",font:{color:"#e6edf3",size:12},
    margin:{l:56,r:18,t:10,b:36},showlegend:false,hovermode:"x unified",
    xaxis:{gridcolor:"#2d3a48",rangeslider:{visible:true,thickness:0.06,bgcolor:"#1b2430"},type:"date"},
    yaxis:{gridcolor:"#2d3a48",zerolinecolor:"#3d4a58",title:"cumulative "+mlab+" (bp)"}};
  Plotly.react("chart", any?traces:[], lay, {responsive:true,displaylogo:false,
    modeBarButtonsToRemove:["lasso2d","select2d","autoScale2d"]});
  if(!any && !anyMetric)
    Plotly.react("chart", [], {...lay, annotations:[{text:"no "+mlab+" data for this market (outright only)",
      showarrow:false, font:{color:"#8b98a5",size:14}, x:0.5,y:0.5,xref:"paper",yref:"paper"}]});
}

function setSeg(id,attr,val,key){
  document.querySelectorAll(`#${id} button`).forEach(b=>b.classList.toggle("on",b.dataset[attr]===val));
  S[key]=val;
}
document.querySelectorAll("#view button").forEach(b=>b.onclick=()=>{setSeg("view","v",b.dataset.v,"view");buildSeriesList();render();});
document.querySelectorAll("#metric button").forEach(b=>b.onclick=()=>{setSeg("metric","m",b.dataset.m,"metric");render();});
document.getElementById("all").onclick=()=>{ensureSel();curItems().forEach(it=>S.sel[selKey()].add(it[0]));buildSeriesList();render();};
document.getElementById("none").onclick=()=>{S.sel[selKey()]=new Set();buildSeriesList();render();};

const msel=document.getElementById("market");
ORDER.forEach(m=>{const o=document.createElement("option");o.value=m;o.textContent=DATA[m].label;msel.appendChild(o);});
msel.onchange=()=>{S.market=msel.value;buildSeriesList();render();};

buildSeriesList(); render();
</script></body></html>
"""


def build(open_browser=True, path=None):
    payload = build_payload()
    if not payload:
        print("  no intl CMT/return caches found — run cmt_intl.py first"); return
    order = [m for m in MKT_ORDER if m in payload]
    html = (HTML.replace("__PLOTLY__", plotly_tag())
                .replace("__PAYLOAD__", json.dumps(payload))
                .replace("__ORDER__", json.dumps(order)))
    path = path or os.path.join(HERE, "dashboard_intl.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    nb = sum(len(payload[m]["buckets"]) for m in order)
    nbond = sum(len(payload[m]["bonds"]) for m in order)
    print(f"  wrote {path}  ({len(order)} markets, {nb} buckets, {nbond} bonds, {os.path.getsize(path)//1024} KB)")
    if open_browser:
        try:
            webbrowser.open("file://" + path.replace("\\", "/"))
        except Exception:
            pass


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    build(open_browser="--no-open" not in sys.argv)
