# breakeven_rv — first-pass results (v1, run 2026-07-06)

> Generated from `python -m breakeven_rv.run_all all`. Raw tables: `reports/*.csv`.
> Sample: 2008-10 → 2026-07 (~4,529 business days), 203 TIPS auctions.
> Plan references (§) are to [README.MD](README.MD); build details in [IMPLEMENTATION.md](IMPLEMENTATION.md).

## TL;DR

1. **Go/no-go: GO.** Both residuals mean-revert with strong significance, in every regime.
   There is a strategy to build (plan §8 step 1 passes).
2. **The auction hypothesis is confirmed — through the liquidity lens (B), not the
   fundamental one (A)**, exactly as the plan's prior said (§9). Auctions into a cheap
   TIPS-vs-swap basis richen ~4.3bp more (top vs bottom tercile, 1d, t≈4.4) than
   auctions into a rich basis.
3. **The placebo makes it clean**: z_B has *no* everyday predictive power for forward BE
   changes (t < 1) — the effect exists **only on auction days** (t −4.2/−3.3/−2.4 at
   1/3/5d). Supply events are the catalyst that converts basis-cheapness into performance.
   This is the novel, defensible result.
4. **One prior was wrong** (§3): the *fundamental* residual A is the fast one
   (half-life ~14bd) and the basis B the slow one (~65-90bd) — not the reverse. See
   interpretation below.
5. **Non-linearity: not found.** The `|z|>1` cheap-tail dummy adds nothing on top of
   linear z in the auction study. On this N, the effect looks linear.

## Layer 1 — fair value (§7)

Four-factor (3m10y slope, log RBOB, VIX, log BBDXY), rolling 504bd OLS on the 10y ZC
CPI swap; EWLS(hl=252) variant tracks it closely.

- Median in-window adj-R²: **0.78** (swap target) / 0.80 (BE target) — inside the plan's
  "low-0.80s and stop" zone. Residual sd ≈ 12bp.
- LASSO rotation diagnostic (9-factor economically-grouped basket, monthly refits):
  gasoline selected in **93%** of windows, VIX 84%, CPI-yoy 75%, USD 72%. The four
  baseline factors stay live; no wholesale driver rotation, though slope share (62%)
  says the curve factor is the least stable — worth watching, not acting on.

## The two residuals + quadrants (§2)

- corr(z_A, z_B) = **−0.13** → genuinely two independent lenses.
- Half-lives: **A ≈ 14bd, B ≈ 65-90bd** — opposite of the §3 prior. Interpretation: A is
  the residual of a *rolling* model, so it closes partly by the model adapting to the
  market (the §1.3 "neutral/bad" convergence mode — fit reversion, not necessarily PnL
  reversion; the backtest phase must separate these). B is a traded-price spread with no
  model to chase it, and behaves like a slow institutional/balance-sheet premium whose
  tradeable moments are *event-driven* (see auction placebo) rather than everyday drift.
- Quadrant occupancy: neutral 41%, B_only_rich 19%, A_only_rich 11%, disagree 9%,
  A_only_cheap 8%, B_only_cheap 7%, both_rich 4%, **both_cheap 2%** (small-sample cell).

## Go/no-go reversion (§8) — `reports/reversion.csv`

Forward residual change on residual level (NW errors, lags ≥ h):

| signal | h | beta | t (NW) | hit rate \|z\|>1 |
|---|---|---|---|---|
| A (fundamental) | 5 | −0.20 | −7.3 | 0.67 |
| A | 10 | −0.34 | −7.4 | 0.71 |
| A | 20 | −0.56 | −8.8 | 0.78 |
| B (liquidity, demeaned) | 5 | −0.03 | −3.4 | 0.60 |
| B | 10 | −0.06 | −3.3 | 0.63 |
| B | 20 | −0.12 | −3.4 | 0.69 |

- **Regime robustness (§10): passes.** At h=10 the beta is negative and significant in all
  five sub-periods (2008-14, 2015-20, COVID, 2021-22 spike, 2023-present) for both A and B.
- **Quadrant validation (§2.3):** the key cell — when only A says cheap/rich (the
  "likely model error" quadrant), **B does not converge** (beta +0.12, t +0.8 cheap;
  +0.06, t +1.3 rich): the basis simply doesn't move to bail out a fundamental-only
  signal. B's own reversion is concentrated where B itself is dislocated (B_only, both_*).
  That is the diagnostic doing exactly what it was designed to do: *A-only cheapness
  should not be traded as a snap-back* (§2.3's "do not trade" cell confirmed).

## Auction study (§9) — `reports/auction_study.csv`

Signal = mean z over t-10..t-5 pre-auction; outcomes demeaned within tenor × reopening.
Terciles (~68 auctions each):

**Through z_B (tenor-matched basis) — the result:**

| outcome (demeaned) | cheap | neutral | rich | cheap−rich | t |
|---|---|---|---|---|---|
| post_be_1d (bp) | **+2.1** | +0.0 | **−2.2** | **+4.3** | **4.4** |
| post_be_3d (bp) | +2.4 | +0.1 | −2.5 | +4.9 | 3.5 |
| post_be_5d (bp) | +2.3 | −0.4 | −1.9 | +4.2 | 2.6 |
| tail_median_bp | +0.68 | −0.25 | −0.43 | +1.1 | 1.8 |
| dealer_pct | −1.0 | +0.3 | +0.7 | −1.7 | −0.6 |

Monotone across all three post-auction horizons. Note the texture: cheap-basis auctions
*tail slightly more at the print* (weakly, t≈1.8) and then *outperform after* — a
concession/absorption story: constrained dealers demand a price to take supply into an
already-cheap basis, and the concession + basis then revert. "Auction performs better"
is true in the post-auction-performance sense, not the auction-stats sense.

**Through z_A (fundamental): nothing.** No outcome clears |t|=1.9. An auction is a
supply/positioning event; the macro-mispricing lens has no claim on it. Both this null
and B's positive are exactly the plan's stated prior (§9).

**Placebo — the clean identification** (`reports/auction_placebo.csv`):

| h | beta all days (t) | beta auction days (t) |
|---|---|---|
| 1d | +0.06 (0.8) | **−1.05 (−4.2)** |
| 3d | +0.09 (0.5) | **−1.60 (−3.3)** |
| 5d | +0.14 (0.5) | **−1.46 (−2.4)** |

z_B predicts nothing on ordinary days; on auction days it predicts strongly. The auction
is the reversion catalyst. This also *explains* B's long everyday half-life: the basis
premium doesn't drift back, it gets *arbitraged back when supply forces a price*.

**Non-linearity (§9, "the boss's point"):** the `1[z<−1]` tail dummy is insignificant on
every outcome once linear z is in. On N=203 the honest read is a **linear** effect; the
"only extremes matter" version is not supported (the tercile table, which would show it,
doesn't show it either).

## What's next (plan §12, remaining)

1. **Layer 2 conditioning model** (h-day residual change on z + quadrant + energy/core
   split + stress + auction proximity; regularised linear first, GBM only if it wins
   walk-forward) — justified now that the go/no-go passed.
2. **Backtest with real PnL**: translate signals into the financed breakeven total-return
   space that already exists in this repo (`engine.py` / `exports/breakeven_10y.csv`),
   with time stops at ~3× half-life and ex-ante tail sizing (§4, §11). This is where
   "A reverts fast" gets tested for *fit-reversion vs PnL-reversion*.
3. **Week-cluster bootstrap** on the auction tables (auctions cluster in time).
4. Optional data upgrades: WI-deadline tails (desk source), CPI fixings/core-BE market.
