# breakeven_rv — v3: bond-level Residual B and full re-validation (run 2026-07-07)

> **Motivation.** v2 established that (i) Residual A's reversion is 59% coefficient
> drift + 31% factor catch-up and only ~10% tradeable price movement — A survives only
> as a B-gated episodic overlay; and (ii) the auction effect (v1's headline) lives in
> the CM/generic index (slope −1.58) but not in traded bond prices (−0.06) — plausibly
> an index-construction artifact, but not decisively, because z_B itself was built from
> the same index (circular signal/outcome). v3 breaks the circle: B rebuilt from traded
> bond prices (b_bond.py), everything downstream re-tested.
> Build details: [IMPLEMENTATION.md](IMPLEMENTATION.md) (v3 section). Raw tables:
> `reports/v3_*.csv`, `b_bond_sanity.csv`. Figures: `reports/figures/`.

## TL;DR

### 1. The transfer decision table (signal = z_B_bond, lagged t−10..t−5)

| measurement space | slope on z_B_bond | t | n |
|---|---|---|---|
| CM/generic BE index | −0.46 | −1.0 | 174 |
| financed OTR BE return | −0.37 | −1.3 | 174 |
| auctioned bond yield vs stop-out | +0.43 | +0.5 | 168 |
| bond-built BE (10y) | −0.79 | −1.0 | 87 |

**Pre-registered verdict: (c) — nothing predicts anything.** No hedging: with the
bond-built signal, the auction effect is gone in every space *including the CM index
itself*. Sample-change is ruled out directly: on the identical 2012+ auction set
(n=174), the old index-built signal still delivers slope −1.25 (t −4.0) on the CM
outcome while the bond-built signal delivers −0.46 (t −1.0) — the collapse is entirely
the signal rebuild. The v2 effect existed only when signal and outcome shared the same
index construction.

**The autopsy converts (c) into a positive identification of the artifact** (run
regardless, per spec; `reports/v3_autopsy.csv`, figure `v3_autopsy.png`): the
(CM index − bond-built) BE spread sits +0.4–0.6bp elevated through t−4..t0 around 10y
auctions and collapses to ~0 at t+1 — the "auction effect" was the index's own
construction noise correcting when quotes/constituents refresh at the supply event.
**The auction hypothesis is CLOSED as an index artifact.** The v1/v2 auction results
are measurement, not alpha. Nothing here reaches a tradeable price.

### 2. Quadrant re-check (the v2 surviving positive, on bond-B quadrants)

The *diagnostic* survives; the *money* doesn't:

| | v2 (index-B) | v3 (bond-B) |
|---|---|---|
| confirm cell: price-PnL share / mean / hit / n | 53% / +4.7bp / 78% / 18 | **48% / +4.3bp / 76% / 21** |
| contradict cell: price share / mean / n | −32% / −2.7bp / 28 | −46% / −3.4bp / 26 |
| A-backtest confirm-subset net PnL | +64bp (n=20) | **+30bp (n=22)** |

n=21 ≥ 12 → establishable; robust across the entry/exit grid (price share in the
confirm cell 0.44–1.05 at every {0.75,1.0,1.5}×{0.25,0.5} node; `v3_episode_grid.csv`).
BUT the full confirm-gated backtest (all costs, tails included) nets to **zero**:
Sharpe −0.02 at 1.0bp cost, breakeven cost ~0.77bp, per-trade skew −3.2. One event
explains it: the March-2020 `both_cheap` longs (−58.6bp and −16.1bp, quadrant-cut
exits) — ex the single worst trade the book is +53bp, with it −5bp. The plan §4's
"extreme readings that keep going are information" failure mode struck precisely in
the highest-conviction cell, and no ex-ante sizing off the MAE distribution
(5th pct −15bp in the confirm cell, `v3_mae_mfe.csv`) would have carried a −59bp
excursion at meaningful size.

**Long/short asymmetry (P3.2, and it renames the strategy):** surviving PnL is
short-side only — backtest longs −35bp (n=13) vs shorts +29bp (n=23); decomposition
confirm-cell longs are rare (n=5). The honest name is **"fade confirmed richness"**,
not "buy confirmed panics" — and at ~2 entries/year and ~+2bp/year it is not a book.

### 3. Capacity & frequency honesty box

| rule | entries/yr | mean hold | annual bp (net, 1bp cost) | status |
|---|---|---|---|---|
| B auction strategy | — | — | 0 | **CLOSED — index artifact** |
| A confirm-gated overlay | 2.3 | 18bd | ≈ 0 (−0.3) | not deployable at realistic cost |
| └ short-side only (post-hoc split, not a rule) | ~1.5 | 18bd | ~+2 | anecdote-sized |

## Supporting results

- **B_bond construction validates** (`b_bond_sanity.csv`): level corr with index-B
  0.82, matching mean/vol (−23.7/6.1 vs −24.2/6.3bp) — same economics; daily-change
  corr only **0.42** — the index adds large day-frequency noise of its own, which is
  exactly what made the circular v2 result possible. No >0.95 flag. ASW iota
  cross-check: right sign at level (−0.23), zero at daily changes (BBG computed-ASW
  quality; documented).
- **Track 2 rebuilt** on z_B_bond with the dealer-position control
  (`v3_track2_inference.csv`): nothing survives on tradeable outcomes (z_B_bond t
  −1.2 to −1.7); OOS score corr drops 0.42 → 0.16. **P5.1 answer:** dealer positions
  do NOT absorb z_B_bond (its coefficient grows slightly when the control enters) and
  carry no robust effect themselves — the basis is not literally dealer balance sheet
  at this frequency.
- **Signal decay** (`v3_decay.csv`): flat/insignificant at every lag (t−1 to t−10) —
  consistent with there being no signal to decay. For the surviving confirm episodes,
  entry latency costs nothing (`v3_latency.csv`: mean price PnL +4.3bp at lag 0,
  +6.6bp at lag 5 — a slow state, not a perishable trigger).
- **Rolling 2y Sharpe** (figure `v3_rolling_sharpe.png`): the confirm-gate's edge is
  episodic — long positive stretches (2015–19, 2023+, last value ~1.1) destroyed by
  the 2020 tail; min −2.1. An edge that needs one crisis skipped is not an edge.
- **Decomposition identity residual** max 6.5e-13 (appendix sanity, `v3_episodes.csv`).

## v2 → v3 reconciliation

| headline | v2 | v3 | why it moved |
|---|---|---|---|
| auction slope, CM index | −1.58 (t −4.3); −1.25 (t −4.0) on the 2012+ common sample | −0.46 (t −1.0) | signal rebuilt from bonds — v2 slope was signal/outcome shared index noise (sample-change ruled out) |
| auction slope, financed OTR | −0.13 | −0.37 (t −1.3) | same; never significant in either |
| Track 2 OOS score corr | 0.42 | 0.16 | circularity removed |
| z_A adds at auctions | t −2.2 | t −1.5 | controls recomputed in bond space; sample 2012+ |
| confirm-cell price share | 53% (n=18) | 48% (n=21) | quadrants on bond-B — survives |
| confirm-subset backtest PnL | +64bp | +30bp; gated Sharpe ≈ 0 | bond quadrants shift entries; COVID tail dominates |
| B everyday half-life ~90bd | index-B | B_bond similar (level corr 0.82) | construction, not economics |

## The surviving book

**Nothing is deployable.** The B auction strategy is closed as an index artifact; the
confirm-gated A overlay has a real average edge per the decomposition but is
capacity-trivial (~2 entries/yr), cost-fragile (breakeven ~0.8bp round-trip), and
carries a crisis left tail that one event per decade erases. The residual research
assets, which are real:
1. **The artifact finding itself** — CM/generic BE indices are unreliable around
   auctions (±0.5bp construction noise, `v3_autopsy.png`); anything the desk builds on
   USGGBE-family series near supply events inherits it. This generalizes beyond this
   project.
2. **The decomposition tooling** (`track1_decomp.py`) — the fit-reversion vs
   price-reversion split with frozen coefficients is reusable for any rolling-model FV
   residual, and it killed a t=−7.4 "signal" that every conventional test passed.
3. **The transfer-test pattern** (index → financed return → bond quotes → auction
   stop) as the standard tradability gate before any index-derived signal is trusted.
4. **The data feeds**: bond-level quote panel, dealer positions (vintage-safe), ASW
   history, CPI fixings accumulating, and the WI-snap inbox stub.

**The single decision this forces:** stop researching the 10y sector-level basis as an
auction signal. If the desk wants to continue the inflation-RV line, the defensible
next question is *cross-sectional* (bond-vs-bond on the curve, where construction
noise cancels by design and the decomposition tooling transfers directly) — otherwise
this project closes here with the artifact note as its publishable output.
