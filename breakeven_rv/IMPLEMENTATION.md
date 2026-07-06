# breakeven_rv — as-built implementation notes

> Companion to [README.MD](README.MD) (the plan). This file records **what was actually
> built**, where every number comes from, and **every place the build deviates from the
> plan** and why. Results and interpretation live in [REPORT.md](REPORT.md).

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
  validation.py      NW/HAC OLS wrapper, half-life, hit-rate, rolling z (no lookahead)
  run_all.py         orchestration: pull | build | analyze | all
  cache/             (gitignored) parquet pulls + built frames
  reports/           REPORT.md tracked; csv + figures gitignored
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

## Known caveats

- `both_cheap` occupies only ~2% of days (~85 obs) — the highest-conviction quadrant has
  the smallest sample; treat its point estimates accordingly.
- Auction observations cluster in time (5y/10y/30y auctions in the same week share the
  macro state); the tercile t-stats treat auctions as independent. NW(2) lags are used in
  the pooled regressions but a week-cluster bootstrap would be the rigorous upgrade.
- The B lens uses the CM breakeven vs a ZC swap: a par-vs-zero-coupon convexity and
  seasonality mismatch is embedded in the iota's *level* — another reason B is always
  used demeaned vs its trailing norm.
