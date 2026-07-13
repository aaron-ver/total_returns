"""
EVENT STUDIO — fully customizable cross-asset auction/syndication event analysis
(boss spec 2026-07, via the desk: "30y AUD / 10-30y NZD syndications should show concessions
2-4 weeks before — almost surely cross-market vs Bunds or UST matched maturity, probably on
curve, probably outright — then strong outright performance after the supply").

One self-contained HTML (eventstudio.html): every linker supply EVENT (auction or syndication,
from the method-tagged calendar) ships with ±W-day cumulative windows for FOUR legs —

  out   the event bond's own financed outright (DV01-normalized engine bp)
  nom   its paired same-country nominal          -> BE = out - nom  (client-side)
  de    matched-maturity GERMAN nominal existing at the event (DBR/OBL/BKO ladder)
  us    matched-maturity US nominal (us_bonds per-bond sheets)

plus the full daily CMT bucket panel (linker/nominal/BE per market bucket) so curve and fly
structures against ANY bucket are computed client-side. Filters (markets, method, tenor band,
sample window) x structures (outright / BE / xmkt DE / xmkt US / curve / belly fly) x windows
are all interactive — no rebuild needed to ask a new question.

Rebase convention: cum = 0 on the event day (k=0). "Concession" = -path[-N] (how much the
structure CHEAPENED into the event); "post" = path[+N]. The pre->post regression tests whether
big concessions predict the snap-back. Clearing-price overlays (NZ yields already parsed) are
the planned phase 2.

Run:  python eventstudio_intl.py            # build eventstudio.html
Wired into pipeline RENDER + S3 push + the portal.
"""
from __future__ import annotations
import json
import os
import sys

import numpy as np
import pandas as pd

import linkers
import buckets_intl as bk
import auctions_intl
import cmt_intl
import engine_intl as eng
import us_bonds

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = linkers.CACHE
CMT_DIR = os.path.join(CACHE, "cmt")
W = 40                                       # precomputed half-window (trading days)


# ------------------------------------------------------------------ window extraction
def _window(bp: pd.Series, date, w=W):
    """Cumulative bp around `date` on the series' OWN trading days, rebased to 0 at k=0.
    Returns list of 2w+1 (None-padded at edges), or None if the event isn't in the series."""
    s = pd.to_numeric(bp, errors="coerce")
    idx = s.index
    pos = idx.searchsorted(pd.Timestamp(date))
    if pos <= 2 or pos >= len(idx) - 1:
        return None
    v = s.to_numpy(float)
    out = [None] * (2 * w + 1)
    run = 0.0
    for k in range(1, w + 1):                # forward: sum of bp (pos+1 .. pos+k)
        j = pos + k
        if j >= len(v):
            break
        run += 0.0 if np.isnan(v[j]) else v[j]
        out[w + k] = round(run, 1)
    out[w] = 0.0
    run = 0.0
    for k in range(1, w + 1):                # backward: cum at -k = -(sum of bp pos-k+1 .. pos)
        j = pos - k + 1
        if j < 0:
            break
        run += 0.0 if np.isnan(v[j]) else v[j]
        out[w - k] = round(-run, 1)
    return out


# ------------------------------------------------------------------ matched-maturity hedges
class Matcher:
    """Closest-maturity nominal EXISTING at the event date, with a lazily built return frame."""

    def __init__(self, country):
        nu = pd.read_csv(os.path.join(CACHE, "nominal_universe.csv"),
                         parse_dates=["maturity", "issue_date"])
        nu = nu[(nu["country"] == country) & nu["maturity"].notna()]
        nu = nu[nu["isin"].map(lambda i: os.path.exists(
            os.path.join(CACHE, "daily", f"{i}.parquet")))]
        self.nu = nu.reset_index(drop=True)
        self.country = country
        self._frames = {}

    def frame(self, isin):
        if isin not in self._frames:
            try:
                f = eng.nominal_series(isin, self.country)
                self._frames[isin] = f["bp"] if (f is not None and not f.empty) else None
            except Exception:
                self._frames[isin] = None
        return self._frames[isin]

    def window(self, event_date, target_mat, w=W):
        c = self.nu
        ok = c["issue_date"].isna() | (c["issue_date"] <= event_date)
        cand = c[ok].copy()
        if cand.empty:
            return None, None
        cand["gap"] = (cand["maturity"] - target_mat).abs()
        for _, r in cand.sort_values("gap").head(4).iterrows():   # try nearest few (data coverage)
            s = self.frame(r["isin"])
            if s is None:
                continue
            wnd = _window(s, event_date, w)
            if wnd is not None:
                return wnd, f"{r['isin']} {r['maturity'].date()}"
        return None, None


class USMatcher:
    """Same, over the US nominal per-bond sheets (us_bonds)."""

    def __init__(self):
        idx = us_bonds.load_index()
        self.idx = idx[idx["leg"] == "nominal"].reset_index(drop=True)
        self._frames = {}

    def frame(self, cusip):
        if cusip not in self._frames:
            df = us_bonds.load_bond(cusip)
            self._frames[cusip] = df["bp"] if (df is not None and not df.empty) else None
        return self._frames[cusip]

    def window(self, event_date, target_mat, w=W):
        c = self.idx
        ok = c["first"] <= event_date
        cand = c[ok].copy()
        if cand.empty:
            return None, None
        cand["gap"] = (cand["maturity"] - target_mat).abs()
        for _, r in cand.sort_values("gap").head(4).iterrows():
            s = self.frame(r["cusip"])
            if s is None:
                continue
            wnd = _window(s, event_date, w)
            if wnd is not None:
                return wnd, f"{r['cusip']} {pd.Timestamp(r['maturity']).date()}"
        return None, None


# ------------------------------------------------------------------ payload
def build_payload():
    u = linkers.load_universe(include_deferred=True)
    matof = dict(zip(u["isin"], pd.to_datetime(u["maturity"])))
    labof = dict(zip(u["isin"], u["desc"]))
    mktof = dict(zip(u["isin"], u["market"]))

    a = auctions_intl.load()
    a = a[a["market"].isin(bk.MARKETS)].copy()
    a["event_date"] = pd.to_datetime(a["event_date"])
    a["src_rank"] = (a["source"] != "dmo").astype(int)        # prefer dmo rows (amounts, method)
    a = (a.sort_values(["isin", "event_date", "src_rank"])
          .drop_duplicates(subset=["isin", "event_date"]))

    # per-bond outright + paired-nominal daily bp
    def bond_bp(isin):
        p = os.path.join(CACHE, "returns", f"{isin}.parquet")
        if not os.path.exists(p):
            return None
        return pd.read_parquet(p)["bp"]

    def paired_nom_bp(isin):
        p = os.path.join(CACHE, "breakeven", f"{isin}.parquet")
        if not os.path.exists(p):
            return None
        d = pd.read_parquet(p)
        return d["r_nominal_bp"] if "r_nominal_bp" in d else None

    de = Matcher("DE")
    usm = USMatcher()

    events, n_skip = [], 0
    for _, r in a.iterrows():
        isin = r["isin"]
        mat = matof.get(isin)
        obp = bond_bp(isin)
        if obp is None or pd.isna(mat):
            n_skip += 1
            continue
        wout = _window(obp, r["event_date"])
        if wout is None:
            n_skip += 1
            continue
        nbp = paired_nom_bp(isin)
        wnom = _window(nbp, r["event_date"]) if nbp is not None else None
        wde, delab = de.window(r["event_date"], mat)
        wus, uslab = usm.window(r["event_date"], mat)
        tenor = (mat - r["event_date"]).days / 365.25
        amt = r["amount"]
        events.append({
            "m": r["market"], "i": isin, "lab": labof.get(isin, isin),
            "d": r["event_date"].strftime("%Y-%m-%d"),
            "k": "synd" if r.get("method") == "synd" else "auction",
            "new": bool(not r["reopening"]) if pd.notna(r["reopening"]) else False,
            "t": round(tenor, 1),
            "a": round(float(amt) / 1e9, 2) if pd.notna(amt) else None,
            "out": wout, "nom": wnom, "de": wde, "us": wus,
            "hde": delab, "hus": uslab})
    print(f"  events: {len(events)} built, {n_skip} skipped (no data/window)")

    # bucket panel: daily bp per market__bucket on a global grid (curve/fly legs)
    files = sorted(f for f in os.listdir(CMT_DIR) if f.endswith(".parquet"))
    frames = {}
    lo = hi = None
    for f in files:
        d = pd.read_parquet(os.path.join(CMT_DIR, f))
        frames[f[:-8]] = d
        lo = d.index.min() if lo is None else min(lo, d.index.min())
        hi = d.index.max() if hi is None else max(hi, d.index.max())
    grid = pd.bdate_range(lo, hi)
    gpos = {d: i for i, d in enumerate(grid)}
    BK = {}
    for key, d in frames.items():
        e = {}
        for col, name in (("r_linker_bp", "out"), ("r_nominal_bp", "nom"), ("r_BE_bp", "be")):
            s = pd.to_numeric(d[col], errors="coerce").dropna()
            if s.empty:
                e[name] = None
                continue
            s = s[s.index.isin(gpos)]
            arr = [round(float(v), 2) for v in s.values]
            e[name] = {"o": gpos[s.index[0]],
                       "y": arr,
                       "p": [gpos[t] for t in s.index]}      # explicit positions (gaps allowed)
        BK[key] = e
    print(f"  buckets: {len(BK)} panels on {len(grid)}-day grid")

    return {"W": W, "EVENTS": events,
            "BK": BK, "GRID": [d.strftime("%Y-%m-%d") for d in grid],
            "MKTS": {m: bk.MARKETS[m] for m in bk.MARKETS},
            "BKORDER": bk.ORDER,
            "ASOF": pd.Timestamp.today().strftime("%Y-%m-%d")}


def plotly_tag():
    import dashboard_intl
    return dashboard_intl.plotly_tag()


HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Event Studio — linker supply events</title>
__PLOTLY__
<style>
:root{--bg:#0f1419;--panel:#1b2430;--ink:#e6edf3;--muted:#8b98a5;--line:#2d3a48;--accent:#2f81f7}
*{box-sizing:border-box}
body{margin:0;font:13px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink);height:100vh;display:flex}
.side{width:300px;background:var(--panel);border-right:1px solid var(--line);padding:14px;overflow:auto;user-select:none}
.main{flex:1;display:flex;flex-direction:column;min-width:0}
h1{font-size:15px;margin:0 0 12px}
.grp{margin-bottom:13px}
.grp>label{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em;margin-bottom:5px}
select,input[type=number]{background:#0f1722;color:var(--ink);border:1px solid var(--line);border-radius:5px;padding:6px}
select{width:100%}
.seg{display:flex;border:1px solid var(--line);border-radius:6px;overflow:hidden;flex-wrap:wrap}
.seg button{flex:1;background:transparent;color:var(--ink);border:0;padding:6px 4px;cursor:pointer;font-size:12px;min-width:52px}
.seg button.on{background:var(--accent);color:#fff}
.chips{display:flex;flex-wrap:wrap;gap:4px}
.chip{border:1px solid var(--line);border-radius:12px;padding:3px 9px;cursor:pointer;font-size:11.5px;color:var(--muted)}
.chip.on{background:var(--accent);border-color:var(--accent);color:#fff}
input[type=range]{width:100%;accent-color:var(--accent)}
.hl{color:var(--accent);font-variant-numeric:tabular-nums}
.row2{display:flex;gap:8px}.row2>*{flex:1}
#chart{flex:1;min-height:0}
#totals{display:flex;gap:18px;padding:8px 14px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.tot .lab{color:var(--muted);font-size:10.5px;display:block}
.tot b{font-size:15px}
#stats{padding:8px 14px;border-top:1px solid var(--line);max-height:30vh;overflow:auto}
table.stt{border-collapse:collapse;font-size:12px}
.stt th,.stt td{padding:3px 10px;text-align:right;border-bottom:1px solid var(--line)}
.stt th{color:var(--muted);font-weight:600}.stt td:first-child,.stt th:first-child{text-align:left}
.note{color:var(--muted);font-size:11.5px;padding:6px 14px}
</style></head><body>
<div class="side">
  <h1>Event Studio</h1>
  <div class="grp"><label>Markets</label><div class="chips" id="mkts"></div></div>
  <div class="grp"><label>Event type</label><div class="seg" id="kind">
    <button data-k="all">All</button><button data-k="auction">Auctions</button><button data-k="synd" class="on">Synd</button></div></div>
  <div class="grp"><label>New lines vs taps</label><div class="seg" id="newf">
    <button data-n="all" class="on">Both</button><button data-n="new">New only</button><button data-n="tap">Taps only</button></div></div>
  <div class="grp"><label>Remaining tenor (years)</label><div class="row2">
    <input type="number" id="tmin" value="8" min="0" max="50" step="1">
    <input type="number" id="tmax" value="50" min="0" max="50" step="1"></div></div>
  <div class="grp"><label>Sample</label><div class="seg" id="samp">
    <button data-s="full" class="on">Full</button><button data-s="10y">10Y</button><button data-s="5y">5Y</button></div></div>
  <div class="grp"><label>Structure</label><select id="struct">
    <option value="out">Outright (event bond)</option>
    <option value="be">Breakeven (vs paired nominal)</option>
    <option value="xus" selected>X-mkt vs US matched maturity</option>
    <option value="xde">X-mkt vs Bund matched maturity</option>
    <option value="crv">Curve: bond − own-mkt bucket</option>
    <option value="fly">Fly: 2·bond − two buckets</option></select></div>
  <div class="grp" id="bsel1" style="display:none"><label>Bucket leg</label><select id="bk1"></select></div>
  <div class="grp" id="bsel2" style="display:none"><label>Second wing</label><select id="bk2"></select></div>
  <div class="grp"><label>Window ±<span class="hl" id="wv">30</span> td</label>
    <input type="range" id="wslide" min="5" max="40" value="30"></div>
  <div class="grp"><label>Pre/post horizon <span class="hl" id="nv">20</span> td</label>
    <input type="range" id="nslide" min="5" max="40" value="20"></div>
  <div class="grp"><label>Display</label><div class="seg" id="disp">
    <button data-d="mean" class="on">Mean+IQR</button><button data-d="spag">Events</button><button data-d="reg">Pre→Post</button></div></div>
</div>
<div class="main">
  <div id="totals"></div>
  <div id="chart"></div>
  <div id="stats"></div>
  <div class="note" id="note"></div>
</div>
<script>
const P=__PAYLOAD__;
const W=P.W;
const S={mkts:new Set(["AU_TIB","NZ_IIB"]),kind:"synd",newf:"all",tmin:8,tmax:50,samp:"full",
         struct:"xus",bk1:null,bk2:null,w:30,n:20,disp:"mean"};
const MC={IT_BTPEI:"#4cc9f0",FR_OATEI:"#f5a623",FR_OATI:"#f8c471",ES_EI:"#e63946",UK_3M:"#9b5de5",
          DE_EI:"#8b98a5",JP_JGBI:"#3fb950",AU_TIB:"#ff8fa3",NZ_IIB:"#48bfe3"};
// ---- bucket daily series -> cum window around a date position ----
const GPOS={}; P.GRID.forEach((d,i)=>GPOS[d]=i);
function bkWindow(key,leg,date,w){
  const e=P.BK[key]; if(!e||!e[leg]) return null;
  const {y,p}=e[leg];                      // daily bp at grid positions p
  const g=GPOS[date]; if(g==null) return null;
  // find index of last p <= g  (event day within the bucket series)
  let lo=0,hi=p.length-1,c=-1;
  while(lo<=hi){const m=(lo+hi)>>1; if(p[m]<=g){c=m;lo=m+1;}else hi=m-1;}
  if(c<3||c>=p.length-1) return null;
  const out=new Array(2*w+1).fill(null); out[w]=0;
  let run=0; for(let k=1;k<=w;k++){ const j=c+k; if(j>=y.length)break; run+=y[j]; out[w+k]=Math.round(run*10)/10; }
  run=0; for(let k=1;k<=w;k++){ const j=c-k+1; if(j<0)break; run+=y[j]; out[w-k]=Math.round(-run*10)/10; }
  return out;
}
function sub(a,b){ if(!a||!b)return null; return a.map((v,i)=> (v==null||b[i]==null)?null:Math.round((v-b[i])*10)/10); }
function structPath(ev){
  const w=S.w, off=W-w, sl=arr=>arr?arr.slice(off,off+2*w+1):null;
  const out=sl(ev.out);
  if(S.struct==="out") return out;
  if(S.struct==="be")  return sub(out,sl(ev.nom));
  if(S.struct==="xus") return sub(out,sl(ev.us));
  if(S.struct==="xde") return sub(out,sl(ev.de));
  if(S.struct==="crv"){ if(!S.bk1)return null; return sub(out,bkWindow(ev.m+"__"+S.bk1,"out",ev.d,w)); }
  if(S.struct==="fly"){ if(!S.bk1||!S.bk2)return null;
    const a=bkWindow(ev.m+"__"+S.bk1,"out",ev.d,w), b=bkWindow(ev.m+"__"+S.bk2,"out",ev.d,w);
    if(!out||!a||!b)return null;
    return out.map((v,i)=>(v==null||a[i]==null||b[i]==null)?null:Math.round((2*v-a[i]-b[i])*10)/10); }
  return out;
}
function cut(){ if(S.samp==="full")return null; const y=S.samp==="10y"?10:5;
  const d=new Date(); d.setFullYear(d.getFullYear()-y); return d.toISOString().slice(0,10); }
function filtered(){
  const c=cut();
  return P.EVENTS.filter(e=> S.mkts.has(e.m)
    && (S.kind==="all"||e.k===S.kind)
    && (S.newf==="all"||(S.newf==="new"?e.new:!e.new))
    && e.t>=S.tmin && e.t<=S.tmax
    && (!c||e.d>=c));
}
function mean(a){return a.length?a.reduce((x,y)=>x+y,0)/a.length:null;}
function med(a){if(!a.length)return null;const b=a.slice().sort((x,y)=>x-y),h=b.length>>1;return b.length%2?b[h]:(b[h-1]+b[h])/2;}
function q(a,p){if(!a.length)return null;const b=a.slice().sort((x,y)=>x-y);const x=(b.length-1)*p,l=Math.floor(x);return b[l]+((b[l+1]??b[l])-b[l])*(x-l);}
function tstat(a){const n=a.length;if(n<3)return null;const m=mean(a);const sd=Math.sqrt(a.reduce((s,x)=>s+(x-m)**2,0)/(n-1));return sd?m/(sd/Math.sqrt(n)):null;}
function ols(x,y){const n=x.length;if(n<4)return null;const mx=mean(x),my=mean(y);
  let sxx=0,sxy=0,syy=0;for(let i=0;i<n;i++){sxx+=(x[i]-mx)**2;sxy+=(x[i]-mx)*(y[i]-my);syy+=(y[i]-my)**2;}
  if(!sxx)return null;const b=sxy/sxx,r2=syy?sxy*sxy/(sxx*syy):0;
  const s2=x.reduce((s,xi,i)=>s+(y[i]-(my-b*mx+b*xi))**2,0)/(n-2),se=Math.sqrt(s2/sxx);
  return {b,a:my-b*mx,r2,t:se?b/se:null,n};}
const SLAB={out:"outright",be:"breakeven (vs paired nominal)",xus:"vs US matched-maturity nominal",
  xde:"vs Bund matched-maturity nominal",crv:"curve vs bucket",fly:"belly fly vs two buckets"};
function render(){
  document.getElementById("bsel1").style.display=(S.struct==="crv"||S.struct==="fly")?"":"none";
  document.getElementById("bsel2").style.display=S.struct==="fly"?"":"none";
  const evs=filtered(), w=S.w, offs=[]; for(let k=-w;k<=w;k++)offs.push(k);
  const rows=[]; const paths=[];
  for(const e of evs){ const p=structPath(e); if(p&&p.some(v=>v!=null)){paths.push({e,p});} }
  const n=paths.length;
  // stats: concession = -path[-N], post = path[+N]  (N capped at w)
  const N=Math.min(S.n,w);
  const bykind={auction:{pre:[],post:[]},synd:{pre:[],post:[]}};
  const pre=[],post=[];
  for(const {e,p} of paths){ const a=p[w-N],b=p[w+N];
    if(a!=null){pre.push(-a); bykind[e.k].pre.push(-a);}
    if(b!=null){post.push(b); bykind[e.k].post.push(b);} }
  let traces=[],layout;
  if(S.disp==="reg"){
    const xs=[],ys=[],cols=[],labs=[];
    for(const {e,p} of paths){ const a=p[w-N],b=p[w+N];
      if(a!=null&&b!=null){xs.push(-a);ys.push(b);cols.push(MC[e.m]||"#888");labs.push(e.lab+" "+e.d+" ("+e.k+")");} }
    traces.push({x:xs,y:ys,mode:"markers",type:"scatter",marker:{color:cols,size:7,opacity:0.75},
      text:labs,hovertemplate:"conc %{x:.1f} → post %{y:.1f} bp<extra>%{text}</extra>"});
    const o=ols(xs,ys);
    if(o){const xr=[Math.min(...xs),Math.max(...xs)];
      traces.push({x:xr,y:xr.map(v=>o.a+o.b*v),mode:"lines",line:{color:"#e6edf3",width:1.6},hoverinfo:"skip"});}
    layout={xaxis:{title:"concession into event (bp, -"+N+"td→0)"},yaxis:{title:"post-event ("+N+"td, bp)"}};
    document.getElementById("stats").innerHTML = o?
      `<table class="stt"><tr><th>regression</th><th>β</th><th>R²</th><th>t</th><th>n</th></tr>
       <tr><td>post = a + β·concession</td><td>${o.b.toFixed(2)}</td><td>${o.r2.toFixed(2)}</td><td>${o.t?o.t.toFixed(1):"–"}</td><td>${o.n}</td></tr></table>`:"";
  } else {
    if(S.disp==="spag"){
      for(const {e,p} of paths)
        traces.push({x:offs,y:p,mode:"lines",line:{color:MC[e.m]||"#888",width:1},opacity:0.35,
          hovertemplate:"%{x}td %{y:+.1f}bp<extra>"+e.lab+" "+e.d+" ("+e.k+")</extra>"});
    }
    const M=[],Q1=[],Q3=[];
    for(let i=0;i<offs.length;i++){ const vals=[];
      for(const {p} of paths){ if(p[i]!=null)vals.push(p[i]); }
      M.push(vals.length?Math.round(mean(vals)*10)/10:null);
      Q1.push(q(vals,0.25)); Q3.push(q(vals,0.75)); }
    traces.push({x:offs,y:Q3,mode:"lines",line:{width:0},showlegend:false,hoverinfo:"skip"});
    traces.push({x:offs,y:Q1,mode:"lines",line:{width:0},fill:"tonexty",fillcolor:"rgba(47,129,247,0.18)",showlegend:false,hoverinfo:"skip"});
    traces.push({x:offs,y:M,mode:"lines+markers",line:{color:"#2f81f7",width:2.4},marker:{size:4},
      hovertemplate:"%{x}td mean <b>%{y}</b> bp<extra></extra>"});
    layout={xaxis:{title:"trading days from event (0 = auction/pricing day)"},yaxis:{title:"cum bp (rebased at 0)"}};
    // stats table by kind
    const row=(k,o)=>{const tp=tstat(o.pre),ts=tstat(o.post);
      return `<tr><td>${k}</td><td>${o.pre.length}</td><td>${o.pre.length?mean(o.pre).toFixed(1):"–"}</td>
      <td>${tp?tp.toFixed(1):"–"}</td><td>${o.post.length?mean(o.post).toFixed(1):"–"}</td><td>${ts?ts.toFixed(1):"–"}</td></tr>`;};
    let html=`<table class="stt"><tr><th>events</th><th>n</th><th>mean concession −${N}→0</th><th>t</th><th>mean post 0→+${N}</th><th>t</th></tr>`;
    html+=row("all",{pre,post});
    if(S.kind==="all"){ html+=row("auctions",bykind.auction)+row("synd",bykind.synd); }
    document.getElementById("stats").innerHTML=html+"</table>";
  }
  Plotly.react("chart",traces,{...layout,paper_bgcolor:"#0f1419",plot_bgcolor:"#0f1419",
    font:{color:"#e6edf3",size:12},margin:{l:60,r:20,t:26,b:44},showlegend:false,
    title:{text:SLAB[S.struct]+(S.struct==="crv"&&S.bk1?" ("+S.bk1+")":S.struct==="fly"&&S.bk1?" ("+S.bk1+"/"+S.bk2+")":""),font:{size:13}},
    xaxis:{...layout.xaxis,gridcolor:"#2d3a48",zerolinecolor:"#3d4a58"},
    yaxis:{...layout.yaxis,gridcolor:"#2d3a48",zerolinecolor:"#3d4a58"}},{responsive:true,displaylogo:false});
  const mk=[...S.mkts].join(", ");
  document.getElementById("totals").innerHTML=
    `<div class="tot"><span class="lab">events</span><b>${n}</b></div>`+
    `<div class="tot"><span class="lab">markets</span><b style="font-size:12px">${mk||"none"}</b></div>`+
    `<div class="tot"><span class="lab">type</span><b style="font-size:12px">${S.kind}${S.newf!=="all"?" · "+S.newf:""}</b></div>`+
    `<div class="tot"><span class="lab">tenor</span><b style="font-size:12px">${S.tmin}–${S.tmax}y</b></div>`+
    `<div class="tot"><span class="lab">structure</span><b style="font-size:12px">${SLAB[S.struct]}</b></div>`;
  document.getElementById("note").textContent =
    "All legs DV01-normalized financed bp (engine conventions); cross-market hedge = closest-maturity "+
    "nominal existing at each event (per-event bond, Bund ladder incl OBL/BKO). Rebased 0 on event day. "+
    "Data through "+P.ASOF+". Private — do not forward.";
}
// ---- wire ----
const mdiv=document.getElementById("mkts");
Object.entries(P.MKTS).forEach(([m,lab])=>{ const c=document.createElement("span");
  c.className="chip"+(S.mkts.has(m)?" on":""); c.textContent=lab;
  c.onclick=()=>{ S.mkts.has(m)?S.mkts.delete(m):S.mkts.add(m); c.classList.toggle("on"); render(); };
  mdiv.appendChild(c); });
["kind","newf","samp","disp"].forEach(id=>{ const key={kind:"k",newf:"n",samp:"s",disp:"d"}[id];
  document.querySelectorAll("#"+id+" button").forEach(b=>b.onclick=()=>{
    S[{kind:"kind",newf:"newf",samp:"samp",disp:"disp"}[id]]=b.dataset[key];
    document.querySelectorAll("#"+id+" button").forEach(x=>x.classList.toggle("on",x===b)); render(); });});
document.getElementById("struct").onchange=e=>{S.struct=e.target.value;render();};
const bk1=document.getElementById("bk1"),bk2=document.getElementById("bk2");
P.BKORDER.forEach(b=>{ bk1.add(new Option(b,b)); bk2.add(new Option(b,b)); });
S.bk1=P.BKORDER[3]||P.BKORDER[0]; S.bk2=P.BKORDER[5]||P.BKORDER[0]; bk1.value=S.bk1; bk2.value=S.bk2;
bk1.onchange=e=>{S.bk1=e.target.value;render();}; bk2.onchange=e=>{S.bk2=e.target.value;render();};
document.getElementById("tmin").onchange=e=>{S.tmin=+e.target.value;render();};
document.getElementById("tmax").onchange=e=>{S.tmax=+e.target.value;render();};
document.getElementById("wslide").oninput=e=>{S.w=+e.target.value;document.getElementById("wv").textContent=S.w;render();};
document.getElementById("nslide").oninput=e=>{S.n=+e.target.value;document.getElementById("nv").textContent=S.n;render();};
render();
</script></body></html>"""


def build(open_browser=False, path=None):
    payload = build_payload()
    html = (HTML.replace("__PLOTLY__", plotly_tag())
                .replace("__PAYLOAD__", json.dumps(payload, separators=(",", ":"))))
    path = path or os.path.join(HERE, "eventstudio.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  wrote {path}  ({len(payload['EVENTS'])} events, {len(payload['BK'])} bucket panels, "
          f"{os.path.getsize(path) // 1024 // 1024} MB)")
    if open_browser:
        import webbrowser
        webbrowser.open("file://" + path)
    return path


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    build(open_browser="--no-open" not in sys.argv)
