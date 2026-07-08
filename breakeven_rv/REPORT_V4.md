# breakeven_rv — v4: component separation at the sector level (run 2026-07-08)

> **Motivation.** v3 closed the auction hypothesis (index artifact) and left one
> standing positive: the confirmed-cell result — when Residual A is dislocated AND
> bond-built Residual B independently confirms, ~48% of subsequent gap-closing is
> genuine price movement (vs ~10% unconditionally, negative when B contradicts). v4
> interrogates that result through the component framework
> `residual = flow/balance-sheet pressure (1) + premia moves (2) + unspanned information (3) + model error (4)`,
> where only component 1 is the compensated, tradeable object and B-confirmation is
> the current component-1 detector. **Signal-side only: no backtesting, no PnL, no
> execution rules in this run.** Sample: 109 episodes at entry |z_A|≥1 (2008-2026;
> dealer cuts bind 2013+, n=79). Raw tables: `reports/v4_*.csv`; figures:
> `reports/figures/v4_*.png`; build notes: IMPLEMENTATION.md (v4 section).

## TL;DR — the five synthesis answers

**1. How much apparent resolution is phantom? Half — and the confirmed cell does NOT
escape.** Among live-z-converged episodes, the frozen-z (entry coefficients, entry
vol) says the market never actually moved in **53%** of cases at entry 1.0 (47% at
0.75, **71%** at 1.5 — the *bigger* the dislocation, the more its "resolution" is
model adaptation). By B-state: confirm 41%, neutral 51%, contradict 67%. The
confirmed cell is the least phantom-prone but 4-in-10 of even its resolutions are
phantom — the episode-level view of v2's 59% coefficient-drift share, now measured
per episode. A quarter of episodes never close in frozen-z within 60bd of the live
exit. (Figure `v4_phantom_vs_genuine.png`: Feb-2020 live z returns to 0 while frozen
z bottoms at −15σ and ends at −7.5σ.)

**2. Is B_clean a sharper detector? No — purification makes it duller.** Financing
state (GCF spread + MOVE) explains only **2.5%** of B_bond's level variance (0.1% of
changes), and — overturning the hypothesis — the share is *lowest* in the biggest
stress year (2020: 1.5%; peak 59% in 2013, a calm year). Re-running the quadrants on
the causally-cleaned residual: confirm-cell price share falls 48%→43% and the
contradict cell's warning power is destroyed (−0.43→+0.07). **The
financing-correlated part of B was part of the signal. Keep B_bond raw.**
(corr(z_clean, z_raw)=0.74; no specialness series exists — documented stub.)

**3. Does dealer positioning confirm the compensation theory? Yes on level,
no on direction; additive to the B-gate but not yet establishable.** Price share by
dealer 1y-z tercile at entry: **−0.29 / −0.35 / +0.68** (low/mid/high), hit rate
0.48/0.58/0.69 — reversion pays when dealer TIPS inventory is heavy, direct support
for paid-to-warehouse. But the *direction-aligned* version (stuffed+cheap vs
opposed) shows nothing (0.04 vs 0.03) — it is warehousing intensity, not sign
alignment, that identifies. Interaction: within low-flow, even confirm is negative
(−0.39, n=6); within high-flow, neutral (+0.96, n=23) rivals confirm (+0.52, n=5) —
flow looks **additive** (possibly stronger than B), but every confirm×flow cell is
under the n=10 anecdote line: a two-factor gate is *suggested, not established*.
Caveat: 2013+ only (n=79). Time-clustering was checked and is NOT a concern — the 1y
rolling-z construction de-trends the position series, and the high tercile spreads
across 2014-2026 (median year 2018).

**4. Is "fade confirmed richness" structural? No — v3's reading is overturned.**
All-episode price-share difference rich−cheap: **+0.07 [95% CI −0.61, +0.84]**. In
the confirmed cell the point estimate actually *favors cheap* (−0.32, CI spans 0;
cheap-confirm n=5 → not establishable, anecdote rule). The MAE left tail is slightly
*worse* on the rich side (p5: −27.3 vs −23.1bp). v3's short-side-only PnL was a
small-sample artifact of which episodes fell where, not a structural property of
rich dislocations. No side rule is warranted.

**5. Do the two component-1 regimes separate ex ante? Not at entry — the
pre-declared rule is blind; they separate in-flight, and the wait is cheap.** All
five worst-MAE episodes (incl. the −101bp Feb-2020 spiral) were flagged **normal at
entry** — the spiral develops mid-episode, after the entry signal fires. The
separation lives in the in-episode state (ex-post descriptor, valid for a
monitoring rule, never entry classification): episodes where flags reach ≥2
in-flight have price share **−0.37 vs +0.28**, and the toxic cell is
contradict×spiral: price share −1.63, mean −12.8bp, MAE p5 **−75bp** (n=8). The
cost-of-waiting measurement (11 spiral episodes): **median 80% of the reversion
survives** entering at stabilization (first day 5d Δz stops worsening) instead of at
signal; in the 2020 episode the wait converts −70bp into +21bp
(`v4_wait_cost_2020.png`). The empirical price of "don't catch the spiral" is ~20%
of the premium — cheap insurance against the only tail that matters.

## The gate, as of v4

**Best current definition of a tradeable dislocation:**
enter only when (i) |z_A| ≥ 1 **and** (ii) raw bond-B confirms (both_cheap /
both_rich — not B_clean), with (iii) dealer-inventory 1y-z as a *provisional* second
flow factor (supportive evidence, cells too small to mandate); then (iv) run an
**in-flight state monitor**: if crisis flags reach ≥2 mid-episode — especially if B
simultaneously flips to contradict — stand aside and re-enter at stabilization
(5d Δz no longer worsening), paying ~20% of the premium to skip the spiral tail.
And haircut expected capture for the phantom rate: even confirmed "resolutions" are
~40% model adaptation.

**What v4 changed:** added the in-flight monitor (entry-state classification is
blind to the spiral); killed the B-purification idea (financing is part of the
signal); killed the side-asymmetry rule (not structural); promoted dealer inventory
to candidate co-gate (pending N); quantified the phantom haircut. **No backtesting
was done; v5 takes this gate to strategy testing.**

## Per-experiment detail

| experiment | deliverable | headline numbers |
|---|---|---|
| 1 frozen-z | `v4_frozen_z_episodes.csv` | phantom 53% (entry 1.0); by state 41/51/67%; frozen-never-closes-in-60bd 25% |
| 2 B variance | `v4_b_decomposition.csv`, `v4_quadrants_bclean.csv` | financing R² 2.5% level (2020: 1.5%); confirm share 48→43%, contradict −0.43→+0.07 under B_clean |
| 3 flow | `v4_priceshare_by_flow.csv` | terciles −0.29/−0.35/+0.68; aligned-cut null; low-flow confirm negative (n=6) |
| 4 asymmetry | `v4_asymmetry.csv` | rich−cheap +0.07 [−0.61,+0.84]; MAE p5 −27.3 (rich) vs −23.1 (cheap); worst-MAE crisis-at-entry share 0.00 |
| 5 state | `v4_state_conditioning.csv`, `v4_state_sensitivity.csv`, `v4_wait_cost.csv` | entry-crisis n=8 (fine outcomes); in-episode spiral −0.37 vs +0.28; contradict×spiral MAE p5 −75bp; wait survives 80% |

Sensitivity appendix (`v4_state_sensitivity.csv`, reported whole, not tuned): the
crisis/normal price-share separation holds at pctl 0.85/0.90 with ≥1 or ≥2 flags;
0.95/≥2 leaves n=3 (degenerate); ≥3 flags never fires at entry. The rule's
*blindness at entry* is invariant across the grid — it is a property of when entries
happen (before the spiral), not of the thresholds.

## v3 → v4 reconciliation

| number | v3 | v4 | why |
|---|---|---|---|
| confirm-cell price share (entry 1.0/exit 0.25) | 0.476 (n=21) | 0.476 unchanged; 0.426 (n=17) under B_clean | same machinery; B_clean re-classification only |
| contradict cell as warning | −0.43 | −0.43 raw; +0.07 under B_clean | purification destroys the warning — financing part is signal |
| "fade confirmed richness" (short-side PnL only) | post-hoc split | rich−cheap +0.07 [−0.61,+0.84]; cheap-confirm favored in point estimate | promoted to hypothesis and rejected as structural |
| March-2020 loss attribution | both_cheap backtest trades | the −101bp MAE episode entered 2020-02-19 as *disagree*, normal-state; flags hit 2 in-flight | the tail is a mid-episode regime shift, not an entry-state error |
| episode grid 9.7/31/59 split | stable across grid | phantom rate 47→71% rising with entry z | the frozen-z view adds: bigger dislocations resolve MORE by model drift |
| dealer positions (Track 2, auctions) | no effect, doesn't absorb z_B | strong level effect on episode price share (+0.68 top tercile) | different object: episode reversion quality, not auction outcomes |

## Data & discipline notes

- New pulls this run: SPX (BBG) and CPFF (FRED; the public FRA-OIS stand-in — no free
  FRA-OIS series exists, documented). Everything else reuses v3 assets.
- All entry-state variables are real-time computable (expanding percentiles, 1y
  rolling ranks, publication-lagged dealer data). The in-episode state tables are
  explicitly labeled EX-POST and are evidence for a monitoring rule only.
- The crisis rule (≥2 of three 90th-pctl flags) was declared in config before any
  outcome split; the sensitivity grid is reported whole.
- Anecdote rule applied: cheap-confirm (n=5), confirm×flow (n=5-6), entry-crisis ×
  b_state (n=2-4) are all reported with their n and marked not establishable.
