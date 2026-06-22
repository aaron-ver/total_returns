"""
Pricing engine: real/nominal DCF -> dirty price, accrued, and DV01 (reference.MD §4).

Same machinery for BOTH legs; only the inputs differ:
  - TIPS    : discount REAL cashflows at the REAL yield; cash DV01 = real DV01 x index ratio.
  - Nominal : discount nominal cashflows at the nominal yield (IR = 1).

DV01 is computed by a symmetric 1bp bump-and-reprice on the dirty price. We report it on
Bloomberg's "Risk" basis (RISK_MID == ModDur x dirty/100 == DV01-per-100bp per 100 face)
so it can be compared field-for-field.

Conventions: US Treasury / TIPS semiannual, actual/actual (ICMA) within the coupon period
(w = days(settle->next coupon)/days(period)).  Coupon dates step 6 months off the maturity
day, end-of-month preserved.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def _is_eom(ts):
    return (ts + pd.Timedelta(days=1)).month != ts.month


def _shift_months(maturity, n, eom):
    """Maturity shifted by n months, preserving day-of-month (or EOM)."""
    m = maturity.month - 1 + n
    y = maturity.year + m // 12
    m = m % 12 + 1
    if eom:
        return pd.Timestamp(y, m, 1) + pd.offsets.MonthEnd(0)
    last = (pd.Timestamp(y, m, 1) + pd.offsets.MonthEnd(0)).day
    return pd.Timestamp(y, m, min(maturity.day, last))


def coupon_schedule(maturity, settle, freq=2):
    """Return (prev_coupon, [future coupons strictly after settle, incl. maturity])."""
    maturity = pd.Timestamp(maturity); settle = pd.Timestamp(settle)
    step = 12 // freq
    eom = _is_eom(maturity)
    dates, k = [], 0
    while True:
        d = _shift_months(maturity, -k * step, eom)
        dates.append(d)
        if d <= settle:
            break
        k += 1
    dates = sorted(dates)
    prev = max(d for d in dates if d <= settle)
    future = [d for d in dates if d > settle]
    return prev, future


def price_real_dirty(settle, maturity, coupon, ytm, freq=2):
    """DCF dirty price per 100 face at yield `ytm` (percent), ignoring IR (reference.MD §4.1).
    Returns (dirty, clean, accrued, N, w)."""
    settle = pd.Timestamp(settle)
    prev, future = coupon_schedule(maturity, settle, freq)
    nxt = future[0]
    period = (nxt - prev).days
    w = (nxt - settle).days / period
    N = len(future)
    c = coupon / freq                      # coupon per 100 per period
    y = ytm / 100.0 / freq                 # periodic yield
    k = np.arange(N)
    disc = (1.0 + y) ** (k + w)
    dirty = float(np.sum(c / disc) + 100.0 / disc[-1])
    accrued = c * (1.0 - w)
    return dirty, dirty - accrued, accrued, N, w


def risk_dv01(settle, maturity, coupon, ytm, ir=1.0, freq=2, bump_bp=1.0):
    """Symmetric bump DV01 on the dirty cash value (= real dirty x ir).
    Returns dict with dv01_per_1bp (cash, per 100 face) and bbg_risk (== dv01*100,
    matching Bloomberg RISK_MID)."""
    h = bump_bp / 2.0 / 100.0              # half-bump in percent (0.5bp -> 0.005%)
    up, _, _, _, _ = price_real_dirty(settle, maturity, coupon, ytm + h, freq)
    dn, _, _, _, _ = price_real_dirty(settle, maturity, coupon, ytm - h, freq)
    dv01_real = (dn - up) / bump_bp        # price change per 1bp, per 100 face (real)
    dv01_cash = dv01_real * ir
    return {"dv01_per_1bp": dv01_cash, "bbg_risk": dv01_cash * 100.0}


# --- vectorized over a date index ------------------------------------------
def dv01_series(dates, maturity, coupon, ytm_series, ir_series=None, freq=2):
    """Compute the BBG-risk-equivalent DV01 for each date. ytm_series aligned to `dates`
    (percent). ir_series optional (TIPS); defaults to 1.0 (nominal)."""
    out = {}
    for d in dates:
        y = ytm_series.get(d)
        if y is None or pd.isna(y):
            continue
        ir = 1.0 if ir_series is None else float(ir_series.get(d, 1.0))
        out[d] = risk_dv01(d, maturity, coupon, float(y), ir=ir, freq=freq)["bbg_risk"]
    return pd.Series(out)
