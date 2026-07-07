# breakeven_rv — Layer 2 + backtest results (v2, run 2026-07-06)

> Implements the v2 spec (two tracks + backtest). Raw tables: `reports/track*.csv`,
> `reports/fit_reversion_episodes.csv`. Build notes: [IMPLEMENTATION.md](IMPLEMENTATION.md)
> (v2 section). v1 results: [REPORT.md](REPORT.md). All tables OOS or cluster/block-robust
> as spec'd; n reported per cell.

## TL;DR — numbered findings

1. **The fit-reversion decomposition is the decider, and it convicts A.** Across 109
   episodes (|z_A|≥1, 2010-2026), only **9.7%** of the residual gap-closing is the market
   moving (real PnL); **31%** is factors moving (fundamental catch-up) and **59%** is the
   rolling fit adapting (pure fit-reversion). The v1 go/no-go betas (t≈−7) were
   overwhelmingly measuring model adaptation. **Track 1 as an always-on strategy is dead**
   — exactly the outcome the v1 caveat feared, now quantified.
2. **The quadrant diagnostic is what survives.** When B confirms at entry, the price-PnL
   share jumps to **53%** (mean +4.7bp/episode, 78% hit, n=18); B-neutral 17%; when B
   contradicts, episodes **lose** (−32% share, 50% hit, n=28). The backtest agrees: ALL of
   the A-strategy's net PnL comes from B-confirmed entries (+64bp on n=20) vs −3bp on 96
   neutral entries. A is tradeable **only as a B-gated, episodic signal** (~1-2
   entries/year), not an engine.
3. **Track 1 conditioning model: clean negative.** Walk-forward ridge over the spec'd
   feature set never beats bare z_A out-of-sample (lift −0.05 to −0.17 R² at h=5/10/20);
   the GBM gate therefore never opened. In-sample the confirm-interaction has the
   hypothesized sign with high refit-stability, but it does not convert to OOS lift.
   Signals here are state-gates, not regression food.
4. **Track 2 inference (week- AND month-cluster bootstrap): z_B survives every control.**
   β = −1.30 (t −4.0) at 1d net of the concession control (itself significant, −0.18,
   t −2.5), size surprise and MOVE (both nil). **Update to v1:** conditional on controls,
   **z_A now adds** (β −0.69, t −2.2 at 1d, stronger at 3/5d) — fundamental cheapness
   does matter at auctions once the basis and concession are held fixed. The z_A×z_B
   interaction is dead (t 0.5) — linearity confirmed again. OOS scores (annual expanding
   refits, 2016+): corr 0.42 with realized, tercile spread −2.3 → +1.2bp, n=126.
5. **But the auction effect does not survive contact with traded prices.** The transfer
   diagnostics (`reports/track3_transfer.csv`) measure the same post-auction 1d effect in
   four spaces:

   | measurement space | slope on z_B | n |
   |---|---|---|
   | CM/generic BE index (what v1 + Track 2 used) | **−1.58** | 220 |
   | financed OTR BE total return (engine) | −0.13 | 220 |
   | auctioned bond's own yield vs its stop-out | −0.06 | 134 |
   | OTR BE yield rebuilt from the held bonds' quotes | −0.06 | 93 |

   The effect lives **only in the index**. Holding the OTR breakeven through the auction
   captures ~8% of it; buying at the auction itself captures none of the z_B-conditioned
   part (the unconditional ~2-3bp concession capture is there, but it isn't our signal).
   The parsimonious explanation: **CM-index measurement noise (quote/constituent effects)
   that corrects around auction events** — z_B is partly predicting the index's own
   artifact, which is also consistent with the v1 placebo (an "effect" that appears only
   when the index refreshes around supply events). v1's headline auction finding is
   therefore **downgraded: real in the index, not yet demonstrated in tradeable prices.**
6. **Backtest (financed BE total returns, 2011-2026, all cost levels reported):**
   - **A-strategy**: gross positive but cost-fragile — Sharpe at 1bp round-trip: 0.17
     (thr=1.0), 0.32 (thr=1.5); negative at 2bp for thr≤1.0. Skew −2.8 to −3.3 (the
     negative skew the plan predicted; worst episode = COVID, −59bp, quadrant cut fired).
     Sub-periods: positive 2011-14, 2015-20, 2023+; negative in COVID and the 2021-22
     spike. Not deployable as-is; the B-confirmed subset (finding 2) is the survivor.
   - **B-strategy (spec'd t−5 → t+1/t+3)**: **negative at every cost level** (Sharpe
     −0.35 to −0.91 at t+1). Leg attribution: the t−5→t0 concession leg costs −0.15bp/trade
     and the t0→t+1 leg pays only −0.04bp — i.e. even skipping the concession leg
     (t0-entry diagnostic variant) it still loses after costs, because of finding 5.
   - **Reconciliation (the spec'd headline):** A's backtest (+1.2bp gross/trade, +61bp
     net total over 15y) matches the decomposition (+0.7bp/episode price-component) —
     the β=−0.34 reversion produces almost no PnL **because 90% of it was never PnL to
     begin with.** The two independent methods agree.

## Priors confirmed / overturned

| Prior (v1/spec) | Verdict |
|---|---|
| A's reversion may be contaminated by fit-reversion | **Confirmed, worse than feared: 59% fit + 31% factor catch-up** |
| A-reversion cleaner when B confirms, dead when B contradicts | **Confirmed in both decomposition and PnL** |
| B's auction effect is deployable as a pre-auction signal | **Overturned at the bond level — index-only effect** |
| z_A adds nothing at auctions | **Overturned: significant once concession + basis are controlled** |
| Effect linear in z (no threshold triggers) | **Confirmed (interaction t≈0.5)** |
| Richer conditioning features help Layer 2 | **Overturned: no OOS lift over bare z_A** |
| Negative skew must be sized ex ante, price stops useless | **Confirmed: skew −2.8/−3.3, worst losses hit the time/quadrant stops** |

## The single decision this run forces

**Rebuild Residual B from traded prices** (the engine's own OTR breakeven yields vs
matched swaps — the repo already has every input) **and re-run Track 2 + the transfer
test.** Everything else is downstream of it: if bond-level B reproduces the auction
effect, the strategy is real and deployable at ~11 auctions/year with the Track-2 score;
if it doesn't, the auction hypothesis is closed as an index artifact and the surviving
book is the small B-gated A-overlay (finding 2). Until that's done, nothing here should
trade beyond paper.

## Where everything lives

| Artifact | File |
|---|---|
| Track 1 OOS metrics + feature stability | `reports/track1_oos.csv`, `track1_stability.csv` |
| Fit-reversion episodes + shares | `reports/fit_reversion_episodes.csv` |
| Track 2 inference (week+month cluster) | `reports/track2_inference.csv` |
| Track 2 per-auction OOS scores + CIs | `reports/track2_scores.csv` |
| Backtest summary / sub-periods / trades | `reports/track3_summary.csv`, `track3_subperiods.csv`, `track3_trades_A.csv`, `track3_trades_B.csv` |
| Transfer diagnostics (finding 5) | `reports/track3_transfer.csv` |

Reporting-discipline notes: every regression table is cluster-bootstrap or walk-forward
OOS; nothing is presented on the `both_cheap` cell alone (n=6 entries in the backtest —
it is pooled into "B confirms", n=20, per the spec's anecdote rule); the B-strategy's
negative result and the index-artifact finding are, per the spec, results — not failures.
