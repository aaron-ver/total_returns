"""
EXPERIMENT 1 — index-extension-conditioned month-end returns (Barclays guide, top pick).

Idea: month-end flows aren't uniform — they're proportional to that month's INDEX DURATION
EXTENSION, which is predictable from the issuance calendar: new bonds entering at the month-end
rebalance push index duration up; nothing entering while the index ages pulls it down; short bonds
dropping below the 1y threshold jump it up. Passive/index money buys duration into big extensions.
So instead of "is there a month-end effect", test "is the month-end return PROPORTIONAL to the
month's extension" — and does it reverse in the first days of the next month.

Extension proxy (documented simplifications, fine for a CONDITIONING variable):
  * membership: first-issue <= rebal date, time-to-maturity >= 1y (index drop rule)
  * duration proxy: time-to-maturity in years (linkers are low-coupon; ranks months correctly)
  * weights: US = cumulative auction totalAccepted (real, time-varying);
             intl = current AMT_OUTSTANDING held constant (static cache; entry/exit still drives ext)
  ext(m) = D[members(m), amt(m)] - D[members(prev), amt(prev)]   both evaluated with TTM at m,
  i.e. exactly the duration JUMP the index takes at the month-end rebalance.

Returns conditioned: the cached engine bp series (DV01-normalized, financed). ME window = last 5
trading days of the month; REV = first 5 of the next month.

Run:  .venv/Scripts/python.exe experiments/exp_extension.py
Out:  experiments/out/extension_series_{US,INTL}.csv, extension_results.csv + printed summary
"""
from __future__ import annotations
import exp_common as C
import numpy as np
import pandas as pd

MIN_MONTHS = 24


# ------------------------------------------------------------------ extension series
def _extension(members: pd.DataFrame, amt_events: pd.DataFrame | None, start, end):
    """Monthly index-extension series from a bond table (isin/cusip, first_issue, maturity[, amt]).
    amt_events: optional (id, date, amount) issuance events for time-varying weights."""
    b = members.reset_index(drop=True)
    ids = b["id"].to_numpy()
    fi = b["first_issue"].to_numpy("datetime64[ns]")
    mat = b["maturity"].to_numpy("datetime64[ns]")
    static_w = b["amt"].fillna(1.0).to_numpy(float) if "amt" in b else np.ones(len(b))

    ev = None
    if amt_events is not None and len(amt_events):
        ev = amt_events.sort_values("date")

    def amounts(asof):
        if ev is None:
            return static_w
        cum = ev[ev["date"] <= asof].groupby("id")["amount"].sum()
        return np.array([cum.get(i, 0.0) for i in ids])

    rebals = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="ME")
    rows = []
    for k in range(1, len(rebals)):
        prev, cur = rebals[k - 1], rebals[k]
        ttm = (mat - np.datetime64(cur)) / np.timedelta64(365, "D")   # durations evaluated AT cur
        in_new = (fi <= np.datetime64(cur)) & (ttm >= 1.0)
        ttm_prev = (mat - np.datetime64(prev)) / np.timedelta64(365, "D")
        in_old = (fi <= np.datetime64(prev)) & (ttm_prev >= 1.0)     # old membership, aged to cur
        w_new, w_old = amounts(cur), amounts(prev)
        if w_new[in_new].sum() <= 0 or w_old[in_old].sum() <= 0:
            continue
        d_new = float((w_new[in_new] * ttm[in_new]).sum() / w_new[in_new].sum())
        d_old = float((w_old[in_old] * ttm[in_old]).sum() / w_old[in_old].sum())
        rows.append({"month": cur.to_period("M"), "ext_y": d_new - d_old,
                     "n_index": int(in_new.sum()), "entrants": int((in_new & ~in_old).sum()),
                     "dropouts": int((in_old & (ttm < 1.0)).sum())})
    return pd.DataFrame(rows).set_index("month")


def us_extension():
    a = C.us_tips_auctions()
    first = a.groupby("cusip")["issueDate"].min()
    mat = a.groupby("cusip")["maturityDate"].max()
    bonds = pd.DataFrame({"id": first.index, "first_issue": first.values, "maturity": mat.values})
    ev = a.rename(columns={"cusip": "id", "issueDate": "date", "totalAccepted": "amount"})[
        ["id", "date", "amount"]].dropna()
    return _extension(bonds, ev, a["issueDate"].min(), pd.Timestamp.today())


def intl_extension(market):
    u = C.intl_universe()
    u = u[u["market"] == market].dropna(subset=["first_issue"])
    if len(u) < 4:
        return None
    amt = C.intl_amt_outstanding()
    bonds = pd.DataFrame({"id": u["isin"], "first_issue": u["first_issue"], "maturity": u["maturity"],
                          "amt": u["isin"].map(amt)})
    return _extension(bonds, None, u["first_issue"].min() + pd.DateOffset(years=1),
                      pd.Timestamp.today())


# ------------------------------------------------------------------ conditioning study
def _condition(ext: pd.DataFrame, bp: pd.Series, label, series):
    """Regress ME-window and reversal-window returns on the month's extension; tercile means."""
    w = C.month_windows(bp).join(ext["ext_y"], how="inner").dropna(subset=["ext_y"])
    if len(w) < MIN_MONTHS:
        return None
    o_me = C.ols(w["ext_y"], w["me"]); o_rev = C.ols(w["ext_y"], w["rev"])
    t = pd.qcut(w["ext_y"], 3, labels=False, duplicates="drop")   # 0=lo .. k=hi (k<=2)
    ter = w.groupby(t, observed=True)["me"].mean()
    lo, hi = (ter.iloc[0], ter.iloc[-1]) if len(ter) >= 2 else (np.nan, np.nan)
    return {"series": label, "leg": series, "n_months": len(w),
            "beta_me_bp_per_y": round(o_me["slope"], 2), "t_me": round(o_me["tstat"], 2),
            "r2_me": round(o_me["r2"], 3),
            "beta_rev_bp_per_y": round(o_rev["slope"], 2), "t_rev": round(o_rev["tstat"], 2),
            "me_loExt_bp": round(lo, 2), "me_hiExt_bp": round(hi, 2)}


def run():
    results = []

    print("== US TIPS index extension (proxy) ==")
    ext_us = us_extension()
    ext_us.to_csv(f"{C.OUT}/extension_series_US.csv")
    prof = ext_us.groupby(ext_us.index.month)["ext_y"].mean()
    print("  mean extension (y) by calendar month "
          "(guide predicts Jan/Jul high — new 10y + drop-outs; Jun/Dec low/negative):")
    print("   " + "  ".join(f"{m:>2d}:{v:+.3f}" for m, v in prof.items()))
    for tenor in C.US_TENORS:
        r = C.us_returns(tenor)
        for leg, col in (("TIPS", "r_TIPS_bp"), ("BE", "r_BE_bp")):
            res = _condition(ext_us, r[col], f"US_{tenor}", leg)
            if res:
                results.append(res)

    print("\n== intl linker-market extension (per market, static-weight proxy) ==")
    for mkt in C.intl_markets():
        ext = intl_extension(mkt)
        if ext is None or len(ext) < MIN_MONTHS:
            continue
        ext.to_csv(f"{C.OUT}/extension_series_{mkt}.csv")
        for b in C.cmt_buckets(mkt):
            d = C.cmt(mkt, b)
            if d is None:
                continue
            for leg, col in (("linker", "r_linker_bp"), ("BE", "r_BE_bp")):
                if col not in d:
                    continue
                res = _condition(ext, d[col], f"{mkt}_{b}", leg)
                if res:
                    results.append(res)

    out = pd.DataFrame(results)
    out.to_csv(f"{C.OUT}/extension_results.csv", index=False)
    sig = out[np.abs(out["t_me"]) >= 2.0].sort_values("t_me", key=np.abs, ascending=False)
    print(f"\n== extension-conditioned month-end: {len(out)} series tested, "
          f"{len(sig)} with |t|>=2 on the ME window ==")
    cols = ["series", "leg", "n_months", "beta_me_bp_per_y", "t_me", "r2_me",
            "beta_rev_bp_per_y", "t_rev", "me_loExt_bp", "me_hiExt_bp"]
    print((sig if len(sig) else out.sort_values("t_me", key=np.abs, ascending=False).head(12))[cols]
          .to_string(index=False))
    print(f"\n  full table -> {C.OUT}/extension_results.csv")
    print("  read: beta_me = bp of ME-window return per YEAR of index extension; a positive beta with"
          "\n        a negative beta_rev is the buy-pressure-then-reversal signature.")
    return out


if __name__ == "__main__":
    if C.sys.platform == "win32":
        C.sys.stdout.reconfigure(encoding="utf-8")
    run()
