"""
Bloomberg Desktop API (DAPI) connector for the TIPS/breakeven total-return build.

Requires: an active Bloomberg Terminal logged in on this machine (bbcomm running)
and the `blpapi` package (installed: 3.26.5.1). Connects to localhost:8194.

Field map validated against the terminal on 2026-06-22 — see reference.MD §10.
"""
from __future__ import annotations
import datetime as _dt
import blpapi


_SHARED = None  # reused session for batch/loop work (opening a session is expensive)


def _session() -> blpapi.Session:
    opts = blpapi.SessionOptions()
    opts.setServerHost("localhost")
    opts.setServerPort(8194)
    s = blpapi.Session(opts)
    if not s.start():
        raise RuntimeError("Cannot start Bloomberg session — is the Terminal running and logged in?")
    if not s.openService("//blp/refdata"):
        raise RuntimeError("Cannot open //blp/refdata")
    return s


def open_session():
    """Open (once) and return a module-level shared session. Call close_session() when done.
    Reusing one session across many requests is ~100x faster than start/stop per call."""
    global _SHARED
    if _SHARED is None:
        _SHARED = _session()
    return _SHARED


def close_session():
    global _SHARED
    if _SHARED is not None:
        _SHARED.stop()
        _SHARED = None


def _drain(s, on_msg):
    while True:
        ev = s.nextEvent(10000)
        for msg in ev:
            on_msg(msg)
        if ev.eventType() == blpapi.Event.RESPONSE:
            break


def reference(securities, fields, overrides=None):
    """Point-in-time reference data. overrides e.g. {'SETTLE_DT': '20260622'}.
    Returns {security: {field: value}}. Reuses the shared session if one is open."""
    shared = _SHARED is not None
    s = _SHARED if shared else _session()
    try:
        svc = s.getService("//blp/refdata")
        req = svc.createRequest("ReferenceDataRequest")
        for sec in securities:
            req.append("securities", sec)
        for f in fields:
            req.append("fields", f)
        if overrides:
            ovs = req.getElement("overrides")
            for k, v in overrides.items():
                o = ovs.appendElement()
                o.setElement("fieldId", k)
                o.setElement("value", str(v))
        s.sendRequest(req)
        out = {}

        def handle(msg):
            if not msg.hasElement("securityData"):
                return
            sd = msg.getElement("securityData")
            for i in range(sd.numValues()):
                item = sd.getValue(i)
                sec = item.getElementAsString("security")
                rec = out.setdefault(sec, {})
                if item.hasElement("fieldData"):
                    fd = item.getElement("fieldData")
                    for j in range(fd.numElements()):
                        e = fd.getElement(j)
                        try:
                            rec[str(e.name())] = e.getValue()
                        except Exception:
                            rec[str(e.name())] = e.getValueAsString()
        _drain(s, handle)
        return out
    finally:
        if not shared:
            s.stop()


def history(securities, fields, start, end, periodicity="DAILY"):
    """Daily (or other) historical series. start/end as 'YYYYMMDD'.
    Returns {security: [ {date, field: value, ...}, ... ]}.
    Reuses the shared session if one is open."""
    if isinstance(securities, str):
        securities = [securities]
    shared = _SHARED is not None
    s = _SHARED if shared else _session()
    try:
        svc = s.getService("//blp/refdata")
        req = svc.createRequest("HistoricalDataRequest")
        for sec in securities:
            req.append("securities", sec)
        for f in fields:
            req.append("fields", f)
        req.set("startDate", start)
        req.set("endDate", end)
        req.set("periodicitySelection", periodicity)
        s.sendRequest(req)
        out = {}

        def handle(msg):
            if not msg.hasElement("securityData"):
                return
            sd = msg.getElement("securityData")
            sec = sd.getElementAsString("security")
            rows = out.setdefault(sec, [])
            if sd.hasElement("fieldData"):
                fda = sd.getElement("fieldData")
                for i in range(fda.numValues()):
                    pt = fda.getValue(i)
                    r = {}
                    for j in range(pt.numElements()):
                        e = pt.getElement(j)
                        try:
                            r[str(e.name())] = e.getValue()
                        except Exception:
                            r[str(e.name())] = e.getValueAsString()
                    rows.append(r)
        _drain(s, handle)
        return out
    finally:
        if not shared:
            s.stop()


# --- Validated field / ticker map (reference.MD §10) ----------------------
# Identify bonds by CUSIP + " Govt" (description tickers like "TII 2 1/8 ..."
# are unreliable; CUSIP always resolves).
FIELDS_BOND_STATIC = ["SECURITY_DES", "ID_CUSIP", "CPN", "MATURITY", "ISSUE_DT",
                      "BASE_CPI", "REFERENCE_INDEX"]
# TIPS: REFERENCE_CPI = daily reference index (DRI); IDX_RATIO = index ratio (IR).
# Both honor a SETTLE_DT override for any settlement date.
FIELDS_TIPS_DAILY = ["PX_CLEAN_MID", "PX_DIRTY_MID", "INT_ACC", "IDX_RATIO",
                     "REFERENCE_CPI", "YLD_YTM_MID", "RISK_MID", "DUR_ADJ_MID"]
# Nominal: PX_DIRTY_MID is the cash dirty price (no IR involved).
FIELDS_NOMINAL_DAILY = ["PX_CLEAN_MID", "PX_DIRTY_MID", "INT_ACC",
                        "YLD_YTM_MID", "RISK_MID", "DUR_ADJ_MID"]
# On-the-run generics that return the current CUSIP:
OTR_TICKERS = {"TIPS_5y": "CTII5 Govt", "TIPS_10y": "CTII10 Govt", "TIPS_30y": "CTII30 Govt",
               "NOM_10y": "CT10 Govt", "NOM_5y": "CT5 Govt", "NOM_30y": "CT30 Govt"}
CPI_TICKER = "CPURNSA Index"     # CPI-U NSA (note: PX_LAST is current print, see caveat)
GC_REPO_TICKER = "SOFRRATE Index"  # SOFR == GC Treasury repo rate (reference.MD §7.3)


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    otr = reference(list(OTR_TICKERS.values()), ["SECURITY_DES", "ID_CUSIP", "PX_LAST", "YLD_YTM_MID"])
    print("Current on-the-runs:")
    for tic, rec in otr.items():
        print(f"  {tic:12s} {rec.get('SECURITY_DES'):24s} cusip={rec.get('ID_CUSIP')}")
