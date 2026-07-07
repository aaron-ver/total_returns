# breakeven_rv — as-built implementation notes

> Companion to [README.MD](README.MD) (the plan). This file records **what was actually
> built**, where every number comes from, and **every place the build deviates from the
> plan** and why. Results: [REPORT.md](REPORT.md) (v1), [REPORT_V2.md](REPORT_V2.md)
> (Layer 2 + backtest, per the v2 spec).

## Layout

```
breakeven_rv/
  README.MD          the plan (unchanged input document)
  IMPLEMENTATION.md  this file — structure, decisions, deviations
  REPORT.md          first-pass results + interpretation (tracked)
  config.py          ALL paths / tickers / parameters in one place
  data_bbg.py        BBG pull: USSWIT ZC swap curve, USGGBE/USGGT CM breakevens &
                     real yields, MOVE, BBDXY  -> cache/bbg.parquet
  data_fred.py       FRED pull (no key): nominal curve, VIX, broad USD, FRED CM
                     breakevens (cross-check)  -> cache/fred.parquet
  data_auctions.py   TreasuryDirect full auction internals (bid-to-cover, high/median
                     yield, dealer/indirect/direct takedown)  -> cache/auctions_tips.parquet
  panel.py           master daily business-day panel + derived factors, vintage rules
                     -> cache/panel.parquet
  layer1.py          rolling OLS / EWLS four-factor fair value + LASSO rotation
                     diagnostic  -> cache/layer1_{swap10,be10}.parquet
  residuals.py       Residual A + Residual B + z-scores + quadrant classification
                     -> cache/residuals.parquet
  reversion.py       §8 go/no-go: forward residual change on residual level, NW errors,
                     regimes, per-quadrant  -> reports/reversion.csv
  auction_study.py   §9 auction study: lagged-z terciles vs auction outcomes + placebo
                     -> reports/auction_study.csv, auction_panel.csv, auction_placebo.csv
  validation.py      NW/HAC OLS wrapper, half-life (static + rolling), hit-rate,
                     rolling z, cluster-bootstrap OLS, perf stats (no lookahead anywhere)
  --- Layer 2 (v2 spec) ---
  track1.py          Track 1: walk-forward ridge conditioning on fwd dA vs z_A-only
                     baseline, feature-stability table, gated GBM
                     -> reports/track1_oos.csv, track1_stability.csv
  track1_decomp.py   Track 1's critical deliverable: frozen-coefficient fit-reversion
                     decomposition of every |z_A|>1 episode
                     -> reports/fit_reversion_episodes.csv
  track2.py          Track 2: auction event model, week+month-cluster bootstrap,
                     expanding-annual OOS per-auction scores
                     -> reports/track2_inference.csv, track2_scores.csv
  track3_backtest.py Track 3: A/B strategies on the engine's FINANCED BE returns,
                     threshold x cost grids, worst-episode narration, decomposition
                     reconciliation, transfer diagnostics
                     -> reports/track3_*.csv
  run_all.py         orchestration: pull | build | analyze | layer2 | all
  cache/             (gitignored) parquet pulls + built frames
  reports/           REPORT*.md tracked; csv + figures gitignored
```

Run from the **repo root** (so root modules `bbg.py` etc. resolve):

```
python -m breakeven_rv.run_all pull      # needs the Bloomberg terminal (like data_layer.py)
python -m breakeven_rv.run_all build     # cache-only, no terminal
python -m breakeven_rv.run_all analyze   # cache-only, no terminal
```

## Data sources (what feeds what)

| Series | Source | Used for |
|---|---|---|
| USSWIT 1-30y ZC CPI swaps | BBG (`USSWITn Curncy`), 2004-07+ | Layer-1 target (swap space), Residual B |
| CM TIPS breakevens 5/10/30y | BBG (`USGGBEnn Index`), 2004+ | Residual B, auction post-performance |
| CM real yields 5/10/30y | BBG (`USGGTnnY Index`) | reference / future use |
| MOVE, BBDXY | BBG | L2 conditioning; USD factor |
| Nominal curve 3m/2/5/10/30y | FRED H.15 (`DGS*`) | slope factors |
| VIX | FRED (`VIXCLS`) | L1 factor |
| Fed broad USD | FRED (`DTWEXBGS`) | cross-check only (see deviation 4) |
| T10YIE / T5YIE / DFII10 | FRED | cross-check of BBG generics (corr 0.999, mean diff 1.4bp) |
| RBOB XB1 | repo `cache/energy_raw.parquet` (existing pull) | L1 gasoline factor |
| CPI-U NSA, GCF repo | repo `cache/macro.parquet` (existing pull) | L2 conditioning (publication-lagged) |
| TIPS auction internals | TreasuryDirect API (public) | auction study LHS |

## Key modelling decisions

- **Fair value is fit in swap space** (10y ZC CPI swap), per the plan's own §6 suggestion —
  avoids TIPS carry seasonality and the roll. The decomposition is then clean and additive:
  `BE rich/cheap = A (swap vs macro model) + B (TIPS BE vs swap)`, keeping the two lenses
  independent by construction. A direct BE-space fit is built alongside as robustness
  (`layer1_be10.parquet`).
- **Windows:** rolling 504bd OLS (Barclays ~2y OOS sweet spot) + EWLS half-life 252bd.
  z = residual / same-window (504bd) residual vol. The current day sits inside its own fit
  window (weight 1/504, negligible absorption; `exclude_current=True` exists to check).
- **Residual B is z-scored against a rolling 2y mean/vol** — the iota has a persistent
  negative *level*; the signal is deviation from norm, not sign. Wherever B's *reversion*
  is tested, B is first demeaned with the same trailing window.
- **Quadrants** at |z| > 1: `both_*`, `A_only_*`, `B_only_*`, `disagree`, `neutral`.
- **Auction signal is lagged**: mean z over t-10..t-5bd pre-auction (before the concession
  builds). Outcomes are demeaned within tenor x reopening cells (fixed effects by
  demeaning — parsimonious for N=203). Buckets = z terciles; the non-linearity check is a
  `1[z < -1]` dummy alongside linear z.
- **Placebo test** (added beyond the plan): the same forward-BE-change-on-z_B regression on
  *all* days vs *auction* days, to show the effect is auction-concentrated and not just
  "B reverts anyway". This turned out to be the strongest result — see REPORT.md.

## Deviations from the plan / environment (each deliberate, all documented)

1. **Folder name**: the request said `breakevn_rv`; the existing folder (with the plan
   already in it) is `breakeven_rv` — kept the existing, correctly-spelled one.
2. **Constant-maturity breakeven is sourced from Bloomberg generics** (`USGGBE10`), as the
   plan anticipated might be needed. Cross-checked against FRED T10YIE (corr 0.999). The
   repo's own spliced-OTR breakeven **return** series (`exports/breakeven_10y.csv`, from
   engine.py) is a different object (financed total return, not a level) and is *not* used
   in v1; it becomes relevant at the backtest stage where real PnL matters.
3. **Sample effectively starts 2008-10**: USSWIT data begins 2004-07 and the two stacked
   504bd windows (fit + z) consume ~4 years. The plan's "pre-COVID" regime split is
   therefore 2008-2014 / 2015-2020. Getting pre-GFC coverage would require shorter windows,
   not more data (US ZC swap quotes don't exist meaningfully before 2004).
4. **USD factor is BBDXY, not the Fed broad TWD**: DTWEXBGS re-benchmarks weights annually
   (a vintage-discipline leak, plan §6); BBDXY is a traded, unrevised index. DTWEXBGS is
   pulled and kept as a cross-check.
5. **Gasoline = XB1 front-month log level** (repo's existing pull). Contract-roll jumps are
   not adjusted out of the *level* (Barclays uses spot/front gasoline the same way); the
   repo's roll-adjusted return series exists if this ever needs upgrading.
6. **Auction tail**: the classic tail needs the 1pm WI snap, which no public source has.
   Proxies used: `tail_median_bp = high − median yield` (auction-internal dispersion,
   standard in the literature) and, as the primary performance measure, post-auction
   1/3/5d CM-breakeven moves. If the desk can source WI deadline snaps (BBG auction
   pages / dealer records), `data_auctions.py` is the insertion point.
7. **CPI vintage caveat** (inherited from the repo): `CPURNSA` is the current print, not
   as-first-published. The NSA *index level* is essentially never revised (unlike SA), so
   the leak is negligible; publication timing IS handled (a print is usable from the 15th
   of the following month, `config.CPI_PUB_DAY`). CPI only enters Layer-2 conditioning /
   the LASSO diagnostic — never Layer 1.
8. **Reopenings are included** in the auction study (with tenor x reopening fixed
   effects); contingency/test auctions < $1bn are excluded (same rule as root
   `auctions.py`). Excluding reopenings entirely would halve N=203.
9. **Not built yet (deliberately, per plan §12 sequencing)**: the Layer-2 conditioning
   model (GBM/regularised linear on top of the go/no-go) and the walk-forward strategy
   backtest with time stops / tail sizing. The go/no-go passed, so these are the natural
   next phase; the auction study (the explicit first deliverable) is done.
10. **CPI fixings market / market-implied core breakeven** (plan §6 L2 wishlist) is not
    sourced — needs a desk feed (BBG tickers exist but are sparse). Documented as a
    future upgrade, not silently dropped.

## v2 (Layer 2 + backtest) — key implementation decisions & deviations

1. **Two separate tracks, no pooled model** — per the v2 spec, matching how each residual
   works (A = everyday-but-fit-contaminated; B = auction-event-only).
2. **Track 1 features**: no core-BE/fixings feed exists in the repo, so energy attribution
   uses the spec's fallback proxy — gasoline-factor contribution to the 20d swap-BE move
   (`beta_log_gas x d20 log_gas / d20 swap`), clipped to [-1, 2], set to 0 when |d20 move|
   < 5bp (nothing to attribute). The quadrant state enters as confirm/contradict dummies
   PLUS their z_A interactions (the hypothesis is inherently an interaction; documented
   rather than silent).
3. **Walk-forward hygiene**: expanding window, annual refits, an h-day PURGE between train
   and test (overlapping forward targets never straddle the boundary), features
   standardized on train only, baseline and full model evaluated on the identical sample.
   Ridge alpha by in-train CV. GBM gate: ridge must show OOS lift > 0.005 R² first — it
   never did, so no GBM was fit (as the spec intends).
4. **Fit-reversion decomposition** refines the spec's 2-way split into 3 additive parts
   (identity-checked per episode): price move (PnL), frozen-beta factor move (fundamental
   catch-up), coefficient drift (fit-reversion). The spec's "market-vs-frozen-fair" =
   parts 1+2 is also reported. Episodes are non-overlapping; time stop uses the ROLLING
   half-life at entry (504bd window, clipped 5-60bd), not the full-sample estimate.
5. **Track 2 clustering**: the spec says week-cluster bootstrap; empirically NO two TIPS
   auctions share an ISO week (n_clusters = n), making it an iid bootstrap. Month-cluster
   results are reported alongside as the binding robustness check (auctions 1-2/month and
   the signal persists across adjacent auctions). Conclusions identical.
6. **Dealer balance-sheet proxy**: no primary-dealer TIPS position feed in the repo; MOVE
   1y percentile used (spec's stated fallback). It carries no weight in the results.
7. **Backtest instrument**: the engine's financed OTR breakeven returns
   (`cache/returns_{tenor}.parquet`, r_BE_bp) — carry, financing, roll included. Costs as
   round-trip bp of BE yield at 0.5/1.0/2.0 (half on entry, half on exit); DV01-normalized
   1bp yield move = 1bp return, so cost and return units match.
8. **A-strategy entry nuance**: entries are skipped while the state is ALREADY "disagree"
   (the spec's cut rule says exit when the quadrant *flips to* disagree — entering there
   would trigger the cut instantly). Position size fixed at entry (no daily rescaling).
9. **B-strategy deviation (diagnostic only)**: alongside the spec'd t-5 entry, a t0-entry
   variant + a per-trade leg attribution (concession leg vs post-auction leg) were added
   because the spec'd rule lost money and the cause needed isolating. Clearly labeled;
   the spec'd rule's results are reported unmodified.
10. **Transfer diagnostics (added beyond spec, and decisive)**: the same post-auction
    effect measured in four spaces (CM index / financed OTR return / auctioned bond vs
    stop / OTR BE yield from bond quotes). The effect exists only in the CM index —
    see REPORT_V2.md finding 5. This reframes v1's auction result as index-level, not
    (yet) tradeable, and sets the single next step: rebuild Residual B from bond-level
    prices and re-test.

## v3 (bond-level Residual B + re-validation) — data sourcing outcomes & decisions

**P0 sourcing (attempted in spec order; verdicts):**

| item | outcome |
|---|---|
| 1. Bond-level TIPS quotes | **Obtained via source (a)** — the repo's existing per-CUSIP BBG pulls already cover every engine-held CUSIP with zero gaps (verified); consolidated by `data_bonds.py` -> `cache/tips_bond_quotes.parquet` (296k rows, 177 CUSIPs, 2010+; BBG mid closes). TRACE/CRSP not needed. |
| 2. Dealer TIPS positions | **Obtained** — NY Fed FR2004 public API (`data_dealer.py`), maturity-bucket net positions, weekly 2013-04+ (earlier series breaks carry no TIPS line — the control binds only on 2013+ auctions). Levels NOT comparable across the 4 series breaks: downstream uses within-break z / changes only. Vintage rule: usable from as-of + 10 calendar days (config.DEALER_PUB_LAG_D). |
| 3. CPI fixings / core BE | **Tickers exist, history unusable** — USSWIF1-12 ("USD INFL CPI FIX <MON> 1Y") resolve and quote, but BBG history starts 2025-01 (~17 months). No market-implied core-BE series resolvable (USGGBEC10 empty). `data_fixings.py` caches what exists (accumulating); the gasoline-attribution proxy stays. |
| 4. WI yield snaps | **Not obtainable** — no historical DAPI field; no desk records this run. `data_wi.py` is the documented stub: drop `inbox/wi_snaps.csv` (cusip, auctionDate, wi_yield_1pm) and true tails activate. high−median proxy remains in force. |
| 5. Market-quoted iota / ASW | **Obtained** — ASSET_SWAP_SPD_MID serves historically per CUSIP; `data_asw.py` pulled the full 10y universe (95 CUSIPs, 2010+). Used as the B_bond cross-check: right sign at level (−0.23), uninformative at daily changes (BBG computed-ASW quality caveat). |

**P1 construction decisions (`b_bond.py`):**
- Nominal leg: PCHIP over H.15 pillars {2,5,7,10,20,30}y (DGS7/DGS20 added to the FRED
  pull) at the bond's actual maturity date; swap leg linearly interpolated to the same
  date over USSWIT {1..30}. Both legs maturity-matched, never the 10y pillar.
- Seasonal adjustment: expanding month-of-year component (same-calendar-month mean of
  prior years minus all-prior mean; zero until 36 months of history) — lookahead-free.
  The engine's seasonality machinery works in carry/return space, not level space, so
  this simplified level adjustment was built instead (documented deviation). With/without
  figure: `reports/figures/b_bond_seasonal.png`.
- Primary B = mean of OTR and 1st-off-the-run bonds (two independent quote sets halve
  idiosyncratic quote noise); per-bond columns retained. z on trailing 2y mean/vol,
  same convention as index-B.
- B_bond is 10y-sector-level; in v3 auction tests it is applied to all tenors as the
  sector signal (10y-only subsample reported alongside) — deviation from v2's
  tenor-matched index-z, forced by construction scope.

**v3 modules:** `v3_experiment.py` (transfer decision table, Track-2 rebuild with the
dealer control, OOS scores, index-artifact autopsy, signal decay),
`v3_revalidate.py` (quadrant re-check with bond-B states, long/short splits, episode
robustness grid, MAE/MFE, identity-residual check), `v3_metrics.py` (surviving-book
grid, capacity box, breakeven cost, entry-latency decay, rolling Sharpe),
`v3_figures.py`. Reused with parameterization: `track1_decomp.episodes(entry_z,
exit_z, quad_override)` (+ side/MAE/MFE/identity columns),
`track3_backtest.backtest_A(quad_override, confirm_gate)`.

**v3 caveats:**
- The transfer/Track-2 sample starts 2012 (bond quotes 2010 + 2y z window): 174
  auctions vs v2's 197-203. Sample-change was ruled out directly: the v2 index-built
  signal re-run on the identical 2012+ set still gives slope −1.25 (t −3.98, n=174)
  on the CM outcome, vs −0.46 (t −1.0) for the bond-built signal — the collapse is
  the signal rebuild, not the sample.
- Week clustering remains vacuous for TIPS (no shared weeks); month-cluster reported.
- P4 conditionality: transfer outcome was (c), so the spec'd B-strategy v3 backtest was
  NOT run. The confirm-gated A grid (v3_metrics) stands in as the surviving-book test.

## Known caveats

- `both_cheap` occupies only ~2% of days (~85 obs) — the highest-conviction quadrant has
  the smallest sample; treat its point estimates accordingly.
- Auction observations cluster in time (5y/10y/30y auctions in the same week share the
  macro state); the tercile t-stats treat auctions as independent. NW(2) lags are used in
  the pooled regressions but a week-cluster bootstrap would be the rigorous upgrade.
- The B lens uses the CM breakeven vs a ZC swap: a par-vs-zero-coupon convexity and
  seasonality mismatch is embedded in the iota's *level* — another reason B is always
  used demeaned vs its trailing norm.
