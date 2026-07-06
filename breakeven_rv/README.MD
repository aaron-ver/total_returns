# TIPS Breakeven Fair Value, Reversion & Auction Study — Project Plan

> **Purpose of this document:** A self-contained brief describing the design of a
> US TIPS breakeven fair-value + rich/cheap + reversion + auction-performance study.
> It captures both the conceptual framing and the concrete build plan so it can be
> used as context for further work (including as an input to an LLM assistant).

---

## 0. Problem statement

Two linked goals:

1. **Fair value:** Build a macro/fundamental model for the "fair" level of the US
   10y TIPS breakeven, so that the deviation of the market from the model is a
   rich/cheap signal.
2. **Auction hypothesis:** Test whether TIPS auctions perform better when the sector
   is cheap to fair value going in — with the expectation that this only matters when
   cheapness/richness is *significant* (i.e. a non-linear / tail effect).
3. **Stretch:** If a useful signal exists, test systematic trading strategies on it —
   even if the auction hypothesis itself finds nothing.

---

## 1. Core concepts (framing)

### 1.1 What a breakeven is

`Breakeven = expected inflation + inflation risk premium − TIPS liquidity premium`

None of the three components is individually observable, so we model the **whole
breakeven** and treat the model residual as the rich/cheap signal. "Fair value" is a
modelling construct, not a truth — every such model is wrong; the question is whether
its residual is *useful*.

### 1.2 Fair value is NOT the goal of maximising R²

This is the single most important idea in the project:

- The fair-value model is **contemporaneous** — it says where the breakeven *should be
  right now* given current drivers. It is not a forecast.
- **Better fit does not mean better PnL.** If the model fits the market perfectly, the
  residual is zero and there is no signal. Overfitting is doubly bad: it hurts
  generalisation *and* it absorbs the very mispricing we want to trade.
- We want the fitted value to be a genuine **economic attractor** — a level the market
  reverts *to* — without letting the fit soak up the transient dislocation.
- The real objective is the **reversion quality of the residual** (half-life,
  mean-reversion reliability, Sharpe of trading it), *not* R².

### 1.3 The trade is a convergence bet

A rich/cheap residual is only tradeable if large residuals were historically followed
by **convergence**. The gap can close two ways:

- **(good)** the market reverts toward the model → PnL;
- **(neutral/bad)** fundamentals move so the model rises to meet the market → residual
  closes, no PnL, possibly a loss.

Before that, the market can keep moving against you. So **fit and tradeability are
different questions**, and reversion must be tested separately from fit.

### 1.4 Two-layer architecture (opposite philosophies)

| | **Layer 1 — Fair value** | **Layer 2 — Reversion / conditioning** |
|---|---|---|
| Target | current breakeven *level* | future *change* in the residual |
| Philosophy | parsimony; do NOT explain everything | accuracy = PnL; richer features OK |
| Factors | few, market-priced, real-time | residuals + slower/structural conditioning vars |
| Danger | overfitting kills the signal | overfitting a tiny sample into in-sample nonsense |

Adding factors always raises in-sample R² — that is a **warning, not a benefit**, in
Layer 1. Richer factors belong in Layer 2, where explaining more genuinely helps.

### 1.5 Why deep/complex models don't trivially win

- Financial returns are mostly noise; a universal approximator fits the noise and dies
  out-of-sample.
- The target is **non-stationary** — the driver→breakeven map shifts by regime
  (coefficients swing and flip sign).
- The target is **adversarial/endogenous** — trading on a signal arbitrages it away.

Practical implication: on this data, a **regularised linear / LASSO model is often hard
to beat**. Use complexity only if it beats simple models out-of-sample. "Strong" means
well-validated and economically grounded, not high-capacity.

---

## 2. The two residuals (the diagnostic core)

We compute **two independent rich/cheap readings** on the same instrument. They are
*not* inputs to each other — they are two lenses.

### 2.1 Residual A — fundamental

`A = market breakeven − macro fair-value model` (Layer 1). Regression-based. Answers:
*is inflation mispriced relative to the economy?* Contaminated by "maybe my macro model
is just wrong."

### 2.2 Residual B — liquidity / basis

`B = TIPS breakeven − matched-maturity inflation swap.`

- A **zero-coupon inflation swap** pays realised cumulative CPI vs a fixed rate; the
  fixed rate is the market's priced inflation in derivative form.
- Both the TIPS breakeven and the swap reference the **same CPI**, so expected inflation
  and the inflation risk premium **cancel by construction**.
- What remains is almost purely the **TIPS-specific liquidity / financing / supply**
  premium (the "iota" / asset-swap basis).
- It is a **subtraction of two traded prices, not a model** — which is exactly why it is
  a clean liquidity read while A is not.

### 2.3 The quadrant diagnostic (this is the novel contribution)

The *relationship* between A and B is a higher-order signal:

| A (fundamental) | B (liquidity) | Interpretation | Action |
|---|---|---|---|
| cheap | cheap | Two independent identifications agree | Highest conviction; size up |
| fair | cheap | Pure supply/liquidity dislocation | Best clean revert; short horizon |
| cheap | fair | **Likely model error** (missing driver) | **Do not trade as snap-back** |
| disagree in direction | | Fundamental read has decoupled | Early warning / cut signal |

The point of the split is **diagnostic** — knowing *what kind* of cheap you're looking
at so you know how to run the *one* underlying position (sizing, horizon, exit). It is
**not** two separate trades to stack (that would just be leveraged single-factor risk).
The one exception: trading B in isolation (long TIPS breakeven / short matched swap) is
a deliberate, direct trade on the basis with its own funding/carry profile.

---

## 3. Reversion mechanics differ by type (why the split matters)

- **Liquidity/basis cheap (B):** supply/financing-driven (auction concession, dealer
  balance sheet, repo specialness, risk-off). Usually **fast-reverting**, higher hit
  rate, lower capacity.
- **Fundamental cheap (A):** market's priced inflation diverges from the macro read.
  Can **persist for a long time**; may not be mispricing at all (the market may know
  something the model lacks). Long half-life, lower hit rate, real model-error risk.

---

## 4. Adverse selection & risk management (hard-won lessons)

- In a mean-reversion trade, an adverse move makes the signal look **stronger**, so a
  **price-based stop fights the thesis** and exits at the worst-priced moment.
- Extreme readings that "keep going" are usually **information, not noise** — a draw
  from a *new* distribution because someone has newer information than your
  history-calibrated model can see. This is why **accuracy can decouple from PnL**
  (high hit rate + fat left tail = negative skew).
- You cannot out-model adverse selection with more of the *same* price history. Defences:
  1. **Time stops** at ~3× the estimated reversion half-life (exit on the clock, not the
     P&L).
  2. **Position sizing carries the tail** — size so the un-catchable adverse move can't
     ruin you (size for the skew *ex ante*).
  3. **An orthogonal, non-price signal** that fundamentals moved — e.g. "A cheap but B
     now moving" says the fundamental read is the one that's wrong. This is the only
     legitimate reason to cut on *information* rather than P&L.
- **Rules should be pre-committed, not static.** Parameters (thresholds, horizons,
  sizes) can be state-dependent functions of live vol / half-life / regime — the *rule*
  is fixed in advance, its *inputs* adapt. Keep adaptive degrees of freedom **few and
  economically motivated**, or you overfit the *strategy* to history (adaptivity
  relocates the bias-variance problem, it doesn't remove it).

---

## 5. Regime-break lessons (from the EUR / sovereign-crisis analogue)

Relevant as a stress-test of assumptions, not as a US template:

- Debt/GDP is **not** the crux; **monetary sovereignty** is. The US/UK/Japan issue debt
  in a currency they control → risk shows up as *inflation/debasement* (already inside
  the breakeven), not default. Euro members don't control the ECB individually → genuine
  **default + redenomination premium** with no monetary-sovereign analogue.
- In the euro crisis, breakevens moved violently for **non-inflation reasons** (sovereign
  credit / redenomination), contaminating the residual — the maximal-intensity version of
  the "A cheap, model missing a driver" failure mode.
- These were **structural breaks** (ruptures), plus policy interventions (SMP,
  "whatever it takes") and pure plumbing shocks (index exclusion forcing tracker selling).
- Takeaways for the US build: (a) a residual is only as good as the completeness of the
  driver set — some regimes have a driver you cannot model; (b) economically-grouped
  automated selection hedges "which regime are we in"; (c) the moments that break you are
  when a variable *outside your model's worldview* starts driving prices, unforecastable
  from the price series.
- **Do not** reflexively import EUR feature-basket machinery into the US model — US TIPS
  is single-issuer, no credit premium, gasoline-dominated, so parsimony is *more*
  appropriate there.

---

## 6. Data assembly (≈40% of the work; where projects fail)

- **Target:** constant-maturity 10y TIPS breakeven + real yield, daily (may need to
  source constant-maturity BE from Bloomberg). Consider modelling the fair-value engine
  in **CPI-swap space** to avoid TIPS carry seasonality and the roll, then translate to
  TIPS via the iota.
- **Layer-1 factors:** RBOB gasoline, broad trade-weighted USD, VIX, MOVE, nominal curve
  points (3m, 2y, 5y, 10y). All market-priced, daily, real-time.
- **Layer-2 conditioning factors:** core-vs-energy inflation split (or market-implied
  core breakeven from the fixings market), realised inflation momentum, auction calendar,
  seasonality/carry state, stress-regime indicator, breakeven momentum.
- **Residual B inputs:** matched-maturity zero-coupon inflation swaps and/or TIPS
  asset-swap spreads.
- **Auction data (per auction):** date, tenor, new-issue vs reopening, size, WI yield at
  deadline, stop-out yield (→ **tail**), bid-to-cover, dealer/indirect/direct split, and
  the issue's price 1/3/5 days post-auction.
- **Vintage-data discipline (critical):** use values *as known on each date*, not revised
  series. Using a CPI print that didn't exist until t+40 to compute "fair value at t"
  makes the backtest fiction. This invalidates more strategy research than anything else.

---

## 7. Layer 1 — Fair value (parsimonious anchor)

- **Baseline:** reproduce the Barclays four-factor model — 3m10y nominal slope, log
  gasoline, VIX, log trade-weighted USD — on CPI swaps; rolling ~2y window (their
  out-of-sample sweet spot), or EWLS so recent data counts more without a hard cutoff.
- **LASSO variant** over a slightly wider economically-grouped basket as a robustness
  check — *not* to maximise fit, but to see whether the four factors are stable or the
  key driver has rotated.
- **Do not chase R².** Target ~low-0.80s adj-R² and stop. If R² is "too good," you've
  absorbed the mispricing.
- **Output:** fitted fair value → **Residual A** + z-score (denominator = same-window
  residual vol).

---

## 8. Layer 2 — Reversion (where the strategy lives)

**Target (LHS):** future *change* in the residual over horizon h (e.g. how much it
closes over 5/10/20 days, or its sign). Accuracy here *is* PnL.

**Features (RHS):**
- current residual / z-score (the foundational predictor of its own future change);
- quadrant / A-vs-B relationship;
- energy-vs-core attribution;
- stress regime (MOVE / VIX / funding proxy);
- carry / seasonality state and breakeven momentum;
- auction proximity.

**Method — start simple, escalate only if justified:**
1. **Go/no-go test FIRST:** regress forward residual change on today's z-score alone,
   **separately for A, B, and each quadrant.** Does a big cheap residual reliably precede
   a rise? If residuals are random walks, **there is no strategy** and nothing fixes it.
   This regression is the project's pivot point.
2. If reversion exists, add conditioning features via a **regularised linear model or a
   shallow GBM**, walk-forward validated. If GBM doesn't beat the linear model
   out-of-sample, use the linear one.
3. Consider anything heavier only if step 2 shows robust, stable out-of-sample lift.

---

## 9. Auction study (explicit deliverable; ship this first)

Self-contained special case of Layer 2; depends only on §7–§8 residuals.

- **Hypothesis:** auctions perform better when the sector is cheap to fair value going in.
- **LHS:** auction performance — **tail** primarily; also bid-to-cover, dealer takedown,
  post-auction 1/3/5-day move.
- **RHS:** z-scored residual measured **at a lag** (5–10 days pre-auction, *before* the
  concession builds — otherwise you just measure the concession, near-tautologically),
  plus tenor fixed effects.
- **Non-linearity (the boss's point):** don't force a linear fit. Sort auctions into
  cheap/neutral/rich **buckets** (terciles/quintiles) and show mean outcome per bucket —
  a clean monotonic table beats a spline on this N. Optionally add a `|z| > threshold`
  interaction.
- **Test A and B separately.** Prior: **B (liquidity) likely has more auction signal**
  than A, since an auction is a supply/positioning event. Either result is informative.
- **Confounds:** the concession itself (measure at a lag); seasonality/carry (work in
  swap or forward space).

---

## 10. Validation (non-negotiable, given the sample)

- **Walk-forward / out-of-sample everywhere.** In-sample fit is meaningless here.
- **Overlap:** daily observations of an h-day-ahead target are heavily autocorrelated —
  effective sample ≪ row count. Use Newey-West / block standard errors; don't trust naive
  t-stats.
- **Four-quadrant reversion scatter:** do agree-cases revert faster/more reliably than
  disagree-cases? If yes, the diagnostic is validated — the genuinely novel result.
- **Regime robustness:** must survive pre-COVID, COVID, and the 2021–22 spike. A
  one-regime strategy is not a strategy.
- Small-sample caution: TIPS auctions are sparse (~couple dozen/year across 5/10/30y).
  Prefer bucket analysis and parsimonious specs; be suspicious when a complex model beats
  a regularised linear one.

---

## 11. Execution / risk (summary)

- **Entry:** z beyond a vol-scaled threshold, ideally confirmed by the quadrant
  (both-cheap = high conviction).
- **Exit:** primarily a **time stop** at ~3× estimated half-life, not a price stop.
- **Cut signal:** "A cheap but B moves" = fundamentals shifted, not a clean revert.
- **Sizing carries the tail:** size for negative skew ex ante; don't rely on stops to
  catch the informed adverse move.

---

## 12. Suggested sequencing

1. **Build the data set** (with vintage discipline). ← the real work.
2. Reproduce **Layer-1 fair value**; compute **Residual A** + z-score.
3. Construct **Residual B** (the basis; mind maturity-matching + seasonality/carry).
4. **Deliver the auction study** (needs only steps 2–3).
5. Run the **go/no-go reversion test** on A, B, and quadrants.
6. If reversion exists, build **Layer 2**; if not, report the null cleanly (a
   well-evidenced null is a real result) and pivot.
7. **Backtest** with walk-forward; size for the tail.

---

## 13. Headline for stakeholders

The novel contribution is **not** a fancier fair-value model (everyone has one). It is
the **two-residual diagnostic** — knowing *what kind* of cheap you're looking at
(fundamental vs liquidity) — and testing whether that classification predicts reversion
and auction outcomes. That is the defensible "creative" angle.