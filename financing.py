"""
Repo financing with a tunable bid/offer half-spread (the desk's slippage knob).

Why a knob, not data
--------------------
We checked Bloomberg for a real repo bid/offer to use instead of a parameter:
  - GCFRTSY Index (our GC rate) is the DTCC GCF Repo *Index* -- a volume-weighted average
    of executed GCF repo trades. It is effectively a traded mid; it has NO bid/ask
    (PX_LAST == PX_MID, no PX_BID/PX_ASK).
  - USRG1T Curncy (USD Govt GC O/N) *does* expose PX_BID/PX_ASK, but they sit ~±50bp
    around the mid -- an indicative placeholder band, not a tradeable GC bid/offer
    (real GC bid/offer is a few bp). Not usable as a historical spread series.
  - Bond-level repo (RRA / RB... tickers) is entitlement-gated on this terminal.
So there is no reliable historical repo bid/offer to pull. We model it as a configurable
half-spread `x` around the GC mid -- exactly the desk's instruction. Tune/stress `x`.

This module is financing-only. It does NOT model specialness (a one-sided drag on a short
nominal); that is a separate effect and is deliberately out of scope (we finance both legs
at the same GC mid ± the symmetric bid/offer x).

The financing rule (applied per leg, never net the legs first)
--------------------------------------------------------------
  - The leg you are LONG  pays    GC + x   (you borrow cash to hold the bond).
  - The leg you are SHORT receives GC - x  (reverse repo: lend cash, borrow the bond).
  - Accrue act/360 on the leg's FULL dirty cash value V (TIPS: V = dirty_real x IR;
    nominal: V = dirty_price). reference.MD §5.2, §7, §9.3.
"""
from __future__ import annotations

DEFAULT_HALF_SPREAD_BP = 3.0  # x: repo bid/offer half-spread in bp. Tunable / stressable.


def leg_financing(v_prev, days, gc_rate_pct, side, x_bp=DEFAULT_HALF_SPREAD_BP):
    """Signed financing cashflow for ONE leg over `days`, act/360, on dirty cash value v_prev.

    v_prev       : full dirty cash value carried into the period (per 100 face, or $ -- the
                   sign/scale just follows v_prev).
    days         : calendar days in the financing period (act/360).
    gc_rate_pct  : GC mid rate in percent (e.g. 3.67 for GCFRTSY).
    side         : 'long'  -> pays GC + x  (returns NEGATIVE cash, a cost)
                   'short' -> earns GC - x (returns POSITIVE cash, income)
    x_bp         : half-spread in bp.

    Returns the financing cashflow to ADD to that leg's P&L (already signed).
    """
    if side not in ("long", "short"):
        raise ValueError("side must be 'long' or 'short'")
    rate = gc_rate_pct + (x_bp / 100.0 if side == "long" else -x_bp / 100.0)  # percent
    interest = v_prev * (rate / 100.0) * (days / 360.0)
    return -interest if side == "long" else +interest


def breakeven_financing(v_tips_prev, v_nom_prev, days, gc_rate_pct, direction,
                        x_bp=DEFAULT_HALF_SPREAD_BP):
    """Total financing cash for a DV01-weighted breakeven *package* over `days`.

    direction = 'long_be'  : long TIPS  (pay GC+x)  + short nominal (earn GC-x)
    direction = 'short_be' : short TIPS (earn GC-x) + long  nominal (pay  GC+x)

    Note v_nom_prev should already be DV01-scaled to the TIPS leg (nominal face =
    TIPS face x DV01_TIPS/DV01_nom) so the two financing legs are on the hedged package.
    long_be and short_be are NOT mirror images: each pays the half-spread on its long leg
    and gives up the half-spread on its short leg, so both carry slippage.
    """
    if direction == "long_be":
        tips = leg_financing(v_tips_prev, days, gc_rate_pct, "long", x_bp)
        nom = leg_financing(v_nom_prev, days, gc_rate_pct, "short", x_bp)
    elif direction == "short_be":
        tips = leg_financing(v_tips_prev, days, gc_rate_pct, "short", x_bp)
        nom = leg_financing(v_nom_prev, days, gc_rate_pct, "long", x_bp)
    else:
        raise ValueError("direction must be 'long_be' or 'short_be'")
    return tips + nom, {"tips_leg": tips, "nom_leg": nom}


if __name__ == "__main__":
    # sanity check: $100 each leg, 1 day, GC 3.67%, x=3bp
    V, gc = 100.0, 3.67
    for x in (0.0, 3.0, 10.0):
        long_pay = leg_financing(V, 1, gc, "long", x)
        short_earn = leg_financing(V, 1, gc, "short", x)
        pkg, parts = breakeven_financing(V, V, 1, gc, "long_be", x)
        print(f"x={x:4.1f}bp | long leg pays {long_pay:+.6f} | short leg earns {short_earn:+.6f} "
              f"| long_be pkg financing {pkg:+.6f} (drag = 2x on the spread)")
