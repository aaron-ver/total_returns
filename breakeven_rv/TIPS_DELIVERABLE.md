# TIPS Breakeven Project — What We Did, What We Found, What It's Worth

*Plain-language summary of the full research program (five iterations, 2008–2026 data,
three tenors: 5y / 10y / 30y). Technical detail lives in REPORT.md through REPORT_V5.md.*

---

## The original question

You asked: **do TIPS auctions go better when the market looks cheap beforehand?**
To answer it we needed a definition of "cheap," so we built a fair-value model
(a regression that says where the breakeven "should" be, given oil prices, the dollar,
interest rates, and market volatility). "Cheap" = the market trades below what the
model says. We then tested the auction idea, and kept pulling on every thread the
tests exposed.

## The three headline findings

### 1. The auction effect looked real, and was a measurement illusion

Auctions that happened when the market was cheap went on to outperform by ~4bp, 
with strong statistics. However, this was built from Bloomberg's standard "constant-maturity" 
breakeven series — a *calculated* index, stitched together from bond quotes, not a tradeable price. 
When we rebuilt the same signal from **actual bond prices** and
measured outcomes in **actual bond prices**, the effect disappeared everywhere.

What happened: the index drifts about half a basis point away from real bond prices
in the week before each auction, then snaps back when the new bond arrives and the
quotes refresh. 

### 2. Most "mean reversion" in a fair-value model is the model giving up, not the market correcting

When the market deviates from a fair-value model, the gap closes, and our gaps closed
with overwhelming statistical significance. But a gap can close **two ways**: the market 
can move back to the model (real pnl), or **the model can move to the market** (
nothing). Standard tests can't tell these apart, because they only watch the gap.

We built a test that can: freeze the model's coefficients on the day you would have
entered the trade, then track which side actually moved. The result, across ~110
dislocation episodes:

- **~10%** of the gap-closing was the market genuinely moving back (real money)
- **~30%** was the economy shifting so the market turned out to be right all along
- **~60%** was the model quietly re-fitting itself until it agreed with the market

Worse, the bigger the dislocation, the *more* fake the eventual "resolution"
(47% fake at small deviations, 71% at large ones) — because big deviations are exactly
the data points a rolling regression can't ignore and bends toward. So the standard
instinct — "only trade the extreme readings" — systematically selects the fakest
signals. This replicated on all three tenors.

### 3. The reversion that IS real is compensation for warehousing risk — and you can see who's being paid

The ~10% of genuine reversion isn't randomly scattered. It concentrates almost
entirely in one state: **when primary dealers are holding unusually large TIPS
inventories** (public weekly data from the NY Fed).

Split every dislocation episode by dealer inventory at entry: when inventories were
light, "cheap" markets kept getting cheaper — betting on reversion actively lost.
When inventories were heavy, reversion was real and reliable. This pattern was found
on the 10y and then **confirmed out-of-sample on the 5y and 30y**, which had no role
in generating the idea.

My theory: when dealers' balance sheets are stuffed, prices deviate because
someone is compensated to absorb bonds and the earnings is real but when balance sheets 
are light, a big deviation means usually that the market already priced information we don't have
and we end up on the wrong side.

**capacity number: this fires ~5–6 times per year across the whole TIPS
curve, worth a few basis points each.**

## The risk finding: crises are detectable in flight, and dodging them is cheap

The one thing that destroyed every otherwise-profitable version of this trade was a
single kind of event: the self-reinforcing liquidation (March 2020 being the
canonical case — a position entered on an ordinary-looking signal lost 101bp before
the eventual reversion arrived).

Findings, tested across all three tenors:

- **You cannot screen these out at entry.** Every catastrophic episode looked normal
  on the day the signal fired. The spiral develops mid-trade.
- **You can see them developing.** A simple four-gauge monitor (bond-market
  volatility, equity-market volatility, the speed of the move, and whether everything
  is selling off together) flagged the danger a median of **8 business days before
  the bottom** in 77% of crisis episodes.
- **Stepping aside is cheap.** Exiting when the monitor trips and re-entering once
  the market stops deteriorating kept **~85–100% of the eventual reversion** while
  skipping the worst of the drawdown. In the 2020 episode this turns −70bp into +21bp.

## The regime-change result (your suggestion, tested)

You suggested that a model stable for years, with variables changed only at regime
breaks, beats a continuously-adapting one — and that detecting the breaks is an
interesting problem. We tested it directly.

A fair-value model with coefficients **frozen within regimes** and re-estimated only
at detected breaks cuts the fake-reversion rate from **53% to 13%** — a frozen model
cannot quietly bend toward the market, so its signals are honest by construction.
The best break detector turned out to be the crisis monitor above: it found 5–6
breaks in 18 years (2007, 2008, 2020, 2022, 2025), all recognizable events, no false
alarms in calm periods. Classical statistical break tests failed — they fired
constantly on ordinary drift.

The measured limitation: a stress-based detector only sees stressful regime changes.
It slept through the 2021–22 inflation surge (a boom, not a panic), leaving the
frozen model badly stale for a year. So the honest summary: **frozen-within-regime
models fix the fake-reversion problem; the open problem is detecting the quiet
regime changes.** That's the natural next research object.