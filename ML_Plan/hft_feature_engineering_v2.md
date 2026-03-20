# HFT Feature Engineering Pipeline v2 — VIB Bar Edition

> **Document 2 of 5** — How to turn raw factor formulas into a feature matrix  
> **Scope:** Single-asset crypto (BTC-USDT perpetual, Hyperliquid)  
> **Bar type:** Volume Imbalance Bars (VIB) from Doc 0  
> **Input:** VIB bars + LOB snapshots + raw trades + bar metadata  
> **Output:** ~4500+ raw feature candidates, normalized, named, ready for selection  
> **Companion documents:**  
> - `hft_factor_formulas_v4.md` — atomic factor definitions (Doc 1, ~180 factors)  
> - `hft_labeling_sampling.md` — labeling & sampling (Doc 3)  
> - `hft_feature_selection_cfi.md` — clustered feature selection (Doc 4)  
> - Model Training & Evaluation (Doc 5, forthcoming)

### Changelog v2 (from v1)

- **Bar type:** Calendar-time bars → VIB bars. All windows now in bar counts, not time.
- **New stage:** Added Stage 2 (L0i: Intra-Bar Features) for footprint, VIB metadata, and per-bar trade features.
- **Windows:** 1s/10s/1min/5min → $W = 5, 20, 60, 120$ bars.
- **New factors:** Footprint features (§10 of v4), VIB metadata (§11), Hyperliquid-specific (§12).
- **Removed stale references:** Binance 100ms snapshots, SMF, ABAR, Parkinson, RVCoM, VWPR.
- **Naming convention:** New prefixes (`fp`, `vib`, `hl`), bar-count window labels.
- **Output size:** ~86,400 rows/day → ~1440 rows/day (one per VIB bar).

---

## 0. Design Philosophy

### Why Hierarchical?

Raw LOB and trade data arrives as irregular, high-dimensional streams. A flat approach — computing every factor at every possible window and transform — produces an unmanageable feature space with massive redundancy. The hierarchical approach imposes structure:

```
          ┌─────────────────────────────────┐
          │  L2: Temporal Transforms         │  rolling mean, z-score, Δ, lag, EMA
          │  Applied uniformly to all below  │  → multiplies feature count by ~7×
          └────────────┬────────────────────┘
                       │
          ┌────────────┴────────────────────┐
          │  L1: Across-Bar Rolling Window  │  OFI, moments, correlations, cancel metrics
          │  One value per (bar × W)        │  → ~110 factors × 4 windows
          └────────────┬────────────────────┘
                       │
          ┌────────────┴────────────────────┐
          │  L0i: Intra-Bar Features        │  footprint, VIB metadata, per-bar trades
          │  One value per bar              │  → ~38 factors (NEW in v2)
          └────────────┬────────────────────┘
                       │
          ┌────────────┴────────────────────┐
          │  L0: Per-Snapshot Features      │  spread, depth, slope, shape, pressure
          │  One value per bar (at bar open) │  → ~43 factors (incl. Hyperliquid L0)
          └─────────────────────────────────┘
```

Each layer only depends on the layer below it:

1. **Modularity** — swap, add, or remove factors at any layer without rewriting the others
2. **Debuggability** — trace any model feature back through exactly four layers
3. **Computational efficiency** — L0 and L0i once per bar, L1 as rolling aggregations, L2 as a vectorized pass

### The Pyramid: 180 → ~4500+

| Stage | Approximate Count | What happens |
|-------|-------------------|--------------|
| Base factors (v4) | 180 | Atomic definitions (L0, L0i, L1 base) |
| L0 × level subsets | ~43 × 3 = ~129 | LOB factors at level subsets $k = 1, 3, 5, 10$ |
| L0i (per-bar) | ~38 | Footprint, VIB metadata, per-bar trade features |
| L1 × window sizes | ~99 × 4 = ~396 | Each L1 factor at $W = 5, 20, 60, 120$ |
| L0i → L1 promotions | ~20 × 3 = ~60 | Rolling aggregation of selected L0i factors |
| Subtotal base features | ~623 | |
| × applicable L2 transforms (avg ~5 per feature × 2 lookbacks) | × ~7 | |
| + cross-level ratios | + ~15 | |
| + cross-scale ratios | + ~20 | |
| + interactions | + ~30 | |
| **Raw candidates** | **~4500+** | Passed to feature selection (Doc 4) |

The discipline: **generate broadly in this document, select ruthlessly in Doc 4**.

---

## 1. Stage 1: L0 — Per-Snapshot Features

### What belongs here

Every factor tagged `L0` in v4 — computed from a single LOB snapshot. The snapshot used is the one at the **VIB bar's open timestamp** (pre-trade state).

| v4 Section | Factors | Count |
|------------|---------|-------|
| §1.1 Spread | $S_q$, $S_{rel}$, $S_{log}$ | 3 |
| §1.2 Depth | $D_1$, $D_k$, $\text{DI}_i$, $\text{CDI}_k$, $\bar{P}^{b/a}_k$, $\text{MPRP}^{b/a}_k$, $\text{MPRP}^\Delta_k$ | 8+ |
| §1.3 Width | $W^{a/b}_k$, $\text{WI}_k$, $G^a_i$, $\bar{G}^a_k$ | 5+ |
| §1.4 Slope | $\text{Slope}^{a/b}$, $\text{Slope}_1$, $\text{SI}$ | 4 |
| §1.5 Shape | $\text{Conv}^{a/b}$, $\text{CI}$, $\text{HP}^{a/b}$, $\text{Prom}^{a/b}$, $\text{NPeaks}^{a/b}$, $H^{a/b}_{LOB}$, $\hat{H}^{a/b}_{LOB}$, $\Delta H_{LOB}$ | 11 |
| §1.6 Pressure | $\text{Press}$, $\text{Press}_1$, $\text{DW}$ | 3 |
| §12.1 Order Count (Hyperliquid) | $\text{OCI}_i$, $\bar{q}^{ord}_{a/b,i}$, $\text{FI}^{side}$ | 5+ |
| §12.2 Order Size Dispersion | $\text{OSD}_i$ | 2+ |

### Computation model

- **Trigger:** every VIB bar
- **Input:** one LOB snapshot at bar open: `lob_snapshots.asof(bar_open_timestamp - 1)`
- **Output:** one row with ~43 scalar values per bar
- **No lookback required** — each output depends only on the current snapshot

### Level parameterization

Many L0 factors accept a level parameter $k$. Compute at multiple cutoffs and let selection (Doc 4) decide which to keep:

| Level subset | Symbol | Typical use |
|---|---|---|
| Top-of-book | $k = 1$ | Best-level spread, DI₁, Press₁ |
| Near levels | $k = 3$ | CDI₃, Slope over 3 levels |
| Mid levels | $k = 5$ | Standard LOB depth metrics |
| Full book | $k = 10$ or $k = 20$ | Total depth, full-book entropy, shape features |

This is the **cross-level axis** (detailed in Section 6).

---

## 2. Stage 2: L0i — Intra-Bar Features (NEW in v2)

### What belongs here

Every factor tagged `L0i` in v4 — computed from raw trades $\mathcal{T}_b$ within a single VIB bar, or from bar metadata fields.

| v4 Section | Factors | Count |
|------------|---------|-------|
| §10.1 Price Level Aggregation | $V^{buy}_\ell$, $V^{sell}_\ell$, $\delta_\ell$ (profile) | Intermediate (not output directly) |
| §10.2 POC | $\ell^{POC}_b$, $\text{POC}_{rel}$, $\delta^{POC}_b$ | 3 |
| §10.3 Value Area | $\text{VAH}_b$, $\text{VAL}_b$, $\text{VAW}_b$, $\text{CVA}_b$ | 4 |
| §10.4 Footprint Delta | $\Delta_b$, $\Delta^{frac}_b$, $\Delta^{upper}_b$, $\Delta^{lower}_b$, $\text{DDiv}_b$ | 5 |
| §10.5 Imbalance Stacking | $\text{BuyStack}_b$, $\text{SellStack}_b$ | 2 |
| §10.6 Unfinished Auction | $\text{UnfHigh}_b$, $\text{UnfLow}_b$ | 2 |
| §11.1 Duration | $\Delta t_b$, $\ln(\Delta t_b)$ | 2 |
| §11.2 Volume Imbalance | $\theta_b$, $\theta^{norm}_b$ | 2 |
| §11.3 Trade Count | $\bar{q}_b$ (avg trade size) | 1 |
| §11.4 Order Fragmentation | $\text{TPO}_b$ | 1 |
| §11.5 VWAP-Close Deviation | $\text{VCD}_b$ | 1 |
| §11.6 Address Diversity | $n^{addr}_b$, $\text{TPA}_b$, $\text{ACR}_b$ | 3 |
| §9.2 Trade Arrival | $n^{tick}_b$, $\lambda_b$ | 2 |
| §9.2 Inter-Trade Duration | $\bar{\delta}_b$, $\sigma^\delta_b$, $\text{CV}^\delta_b$, $\text{TWTR}_b$ | 4 |
| §9.1 Trade Size Distribution | $\bar{q}_b$, $\text{Var}^q_b$, $\text{Skew}^q_b$, $\text{Kurt}^q_b$, $H_q$, $\text{SmallVR}_b$, $\text{LargeVR}_b$ | 6 |

**Total: ~38 L0i factors per bar**

### Computation model

- **Trigger:** every VIB bar
- **Input:** the set of raw trades $\mathcal{T}_b = \{(p_k, q_k, d_k, t_k)\}$ within the bar, plus bar metadata from Doc 0
- **Output:** one row with ~38 scalar values per bar
- **No lookback required** — each output depends only on the current bar's trades

### Footprint computation detail

The footprint features (v4 §10.1–10.6) require discretizing the bar's price range into tick-sized levels and aggregating buy/sell volume per level. The implementation:

```
for each bar b:
    levels = unique trade prices within [L_b, H_b]
    for each level ℓ in levels:
        V_buy[ℓ] = sum(q_k for k in T_b if p_k == ℓ and d_k == +1)
        V_sell[ℓ] = sum(q_k for k in T_b if p_k == ℓ and d_k == -1)
        delta[ℓ] = V_buy[ℓ] - V_sell[ℓ]
    → compute POC, Value Area, stacking from the delta profile
```

On Hyperliquid, BTC tick size is 0.1 USDT. A typical VIB bar spanning ~10 USDT has ~100 price levels. With `side` provided directly (no tick rule), the classification is exact.

### L0i → L1 promotion rules

Some L0i factors have natural rolling aggregations that should be computed as L1 features (in Stage 3). These are factors where the **multi-bar trend** carries signal beyond the per-bar value:

| L0i factor | L1 rolling aggregation | What it captures |
|---|---|---|
| $\Delta^{frac}_b$ | $\overline{\Delta^{frac}}_W = \text{mean}(\Delta^{frac}_b, \ldots)$ | Sustained directional flow over $W$ bars (= rolling NSMF) |
| $\text{POC}_{rel,b}$ | $\overline{\text{POC}_{rel}}_W$ | Where volume concentrates on average (distribution vs. accumulation) |
| $\ell^{POC}_b$ | $\text{POC\_drift}_W = \ell^{POC}_b - \ell^{POC}_{b-W}$ | How far the most-traded price has shifted |
| $\text{BuyStack}_b$ | $\overline{\text{BuyStack}}_W$, $\overline{\text{SellStack}}_W$ | Average aggressiveness of sweeps over recent bars |
| $\Delta t_b$ | $\overline{\Delta t}_W$ | Average bar duration (market activity baseline) |
| $\lambda_b$ | $\bar{\lambda}_W$ | Average trade intensity |
| $\theta^{norm}_b$ | $\overline{\theta^{norm}}_W$ | Average directional intensity of recent bars |
| $\text{VCD}_b$ | $\overline{\text{VCD}}_W$ | Persistent closing pressure direction |
| $\text{TPA}_b$ | $\overline{\text{TPA}}_W$ | Sustained address concentration |

These promotions produce ~20 additional L0i-derived L1 features × 3 window sizes ($W = 20, 60, 120$) = ~60 features.

---

## 3. Stage 3: L1 — Across-Bar Rolling Window

### What belongs here

Every factor tagged `L1` in v4 (base formula with window parameter $W$), plus the L0i → L1 promotions from Stage 2.

| v4 Section | Factors | Count |
|------------|---------|-------|
| §2.1–2.4 Order Flow / Imbalance | OFI, VOI, TI, Aggressiveness | ~15 |
| §2.5 Active Buy Proportion | ABR (rolling), PTA, PTB, LTBR, BC | 5 |
| §3.1–3.4 Microstructure | Reversal, trend strength, momentum, price impact | ~12 |
| §4.1–4.3 Volatility / Moments | RV, skew, kurtosis, GK, RS, BPV, jump | ~14 |
| §5.1–5.6 Liquidity | Resiliency, session NBP, Amihud | ~6 |
| §6.1–6.2 PV Correlation | $\rho_{PV}$, $\rho_{|r|V}$, $\text{ABCorr}$, lag-lead | ~6 |
| §7.0–7.4 Cancel / Event | Cancel rate, fleeting ratio, event clustering | ~27 |
| §8.1–8.3 Smart Money | VPIN, large order detection, BVC | ~8 |
| §9.3–9.6 Transaction | Execution quality, clustering, runs, LTMD | ~6 |
| §12.3–12.4 Hyperliquid L1 | Address persistence, liquidation features | ~5 |
| L0i → L1 promotions | Rolling footprint, VIB metadata | ~20 |
| **Total L1 base** | | **~124** |

### Window sizes for VIB bars

| Window $W$ (bars) | Approx. time (~1 bar/min) | Primary factors |
|---|---|---|
| **$W = 5$** | ~5 min | OFI, aggressiveness, fleeting orders, trade arrival, cancel metrics |
| **$W = 20$** | ~20 min | OFI autocorrelation, effective spread, micro-momentum, cancel rate, short-term imbalance |
| **$W = 60$** | ~1 hour | RV, skewness, kurtosis, PV-corr, trend strength, RWR, rolling footprint metrics |
| **$W = 120$** | ~2 hours | VPIN, Amihud, volume entropy, large trade ratio, address persistence, liquidation features |

**Design choice:** 4 core windows ($W = 5, 20, 60, 120$) is sufficient. $W = 360$ (~6 hours) adds ~124 raw features but most information is captured by L2 transforms on $W = 120$ features. Include $W = 360$ only if model performance justifies it.

### Aggregation functions

Not all L1 factors use simple summation. The aggregation depends on the factor type:

| Factor type | Aggregation | Example |
|---|---|---|
| Flow/imbalance | Sum | OFI = $\sum e_n$, $\Delta_b$ sum over $W$ bars |
| Rate/proportion | Mean or ratio | ABR = buy_vol / total_vol, CR = cancel_count / total_count |
| Moment | Standard statistical estimator | RV = $\sum r^2_b$, Skew = centered 3rd moment of $\{r_b\}$ |
| Correlation | Pearson/Spearman over paired series within window | $\rho_{PV}$, $\rho_{BSR,P}$ |
| Extremal | Max, min, or argmax | Walk-the-book depth |
| Count | Raw count | $n_\tau$, $N_{\text{cancel}}$ |
| L0i rolling | Mean or difference | $\overline{\Delta^{frac}}_W$, $\text{POC\_drift}_W$ |

### Output schema

Each L1 computation produces one value per (bar, window_size). At each bar $b$, the L1 features look back $W$ bars ending at $b$ (inclusive). No future information.

### The cancel inference step

This is the critical bridge between raw data and cancel factors. It happens once, at L1 computation time:

1. Align consecutive LOB snapshots (at bar open times) with the trade stream
2. Apply the formulas from v4 Section 7.0 to produce $C^s_p(n)$ and $A^s_p(n)$ per snapshot transition
3. Aggregate $C$, $A$ values into window-level cancel statistics ($N_{\text{cancel}}$, $\sum q_c$, etc.)
4. All Section 7.1 factors consume these pre-computed cancel aggregates

---

## 4. Stage 4: L2 — Temporal Transforms

### Purpose

L2 transforms convert a point-in-time L0, L0i, or L1 value into a feature that carries **temporal context** — how the factor has behaved recently, whether it's rising or falling, whether it's abnormally high or low.

### The 7 standard transforms

Applied **uniformly** to every L0, L0i, and L1 factor. Let $x_b$ denote any base factor value at bar $b$.

| # | Transform | Formula | What it captures |
|---|-----------|---------|-----------------|
| T1 | Rolling Mean | $\bar{x}_W = \frac{1}{W}\sum_{i=0}^{W-1} x_{b-i}$ | Recent average level |
| T2 | Rolling Std | $\sigma^x_W = \text{std}(\{x_{b-i}\}_{i=0}^{W-1})$ | Recent variability |
| T3 | Z-Score | $z^x_b = \frac{x_b - \bar{x}_W}{\sigma^x_W}$ | Is current value anomalous? |
| T4 | First Difference | $\Delta x_b = x_b - x_{b-1}$ | Direction of change (momentum) |
| T5 | Lag-1 | $x_{b-1}$ | Autoregressive input |
| T6 | EMA | $\text{EMA}_b = \alpha x_b + (1-\alpha)\text{EMA}_{b-1}$ | Smoothed trend |
| T7 | Ratio to Rolling Median | $\tilde{x}_b = \frac{x_b}{\text{median}_W(x)}$ | Robust relative level |

### Lookback windows

| Lookback $W_{L2}$ | Meaning (at ~1 VIB bar/min) |
|---|---|
| 20 bars | ~20 minutes |
| 60 bars | ~1 hour |

**Design choice:** Use 2 lookback windows (short = 20, long = 60) for most transforms. This keeps the multiplier at ~7× rather than exploding. The 60-bar lookback is the workhorse for z-score.

### Which transforms to apply where

Not every transform is meaningful for every factor:

| Factor characteristic | Skip these transforms | Rationale |
|---|---|---|
| Already a ratio in $[-1, 1]$ (e.g. CDI, ABR, SI) | T7 (ratio to median) | Already normalized; ratio is meaningless |
| Binary/count (e.g. NPeaks, UnfHigh, CVA) | T1, T2, T6 | Mean/std of a count is noisy; use T3 z-score and T4 Δ only |
| Already a difference (e.g. Pressure Acceleration) | T4 (first diff) | Would be a second derivative — rarely useful |
| Highly non-stationary (e.g. raw dollar depth) | All except T3, T4 | Level depends on price; only relative measures are stable |

### Feature explosion budget

| Base | Count |
|---|---|
| L0 factors × level subsets | ~43 × 3 = ~129 |
| L0i factors | ~38 |
| L1 factors × window sizes | ~124 × 4 = ~496 |
| L0i → L1 promotions | ~60 |
| Subtotal base features | ~723 |
| × applicable L2 transforms (avg ~5 per feature × 2 lookbacks) | × ~7 |
| **L2 output** | **~5000** |

This is the raw candidate pool before cross-level, cross-scale, interactions, and selection.

---

## 5. Stage 5a: Cross-Level Features

### The depth axis

LOB factors that accept a level parameter $k$ should be computed at multiple levels to capture the **near vs. far book** distinction. This is not a temporal dimension — it's a structural dimension of the order book itself.

### Recommended level grid

| Tier | Levels | What it captures |
|---|---|---|
| Top-of-book (TOB) | $k = 1$ | Immediate executable liquidity, tightest spread |
| Near book | $k = 3$ | First few ticks of depth — moderate-size trade sweep |
| Mid book | $k = 5$ or $k = 10$ | Standard depth metrics |
| Full book | $k = N$ (e.g. 20) | Total visible liquidity, shape/entropy features |

### Which L0 factors get the level treatment

| Factor | Compute at | Produces |
|---|---|---|
| $\text{CDI}_k$ | $k \in \{1, 3, 5, 10\}$ | 4 variants |
| $D_k$ (cumulative depth) | $k \in \{1, 5, 10, 20\}$ | 4 variants |
| $\text{WI}_k$ | $k \in \{5, 10, 20\}$ | 3 variants |
| $\bar{P}^{b/a}_k$, $\text{MPRP}^{b/a}_k$ | $k \in \{5, 10\}$ | 4 variants (2 sides × 2 levels) |
| Slope | $k \in \{5, N\}$ | 2 variants per side |
| Shape features (Conv, HP, Prom, Entropy) | Full book only ($k = N$) | 1 variant each |
| $\text{OCI}_k$ (Hyperliquid order count) | $k \in \{1, 3, 5\}$ | 3 variants |

### Cross-level ratio features

Derived from multi-level computations — high-information-density features:

| Feature | Formula | Interpretation |
|---|---|---|
| Near/Far Depth Ratio | $D_3 / D_{20}$ | Fraction of total depth in top 3 levels |
| TOB vs. Full Imbalance | $\text{CDI}_1 - \text{CDI}_{20}$ | Does TOB direction agree with full-book? Divergence = potential trap. |
| Near/Far Entropy Ratio | $\hat{H}^s_{3} / \hat{H}^s_{20}$ | Is near-book liquidity more concentrated than far-book? |
| Slope Gradient | $\text{Slope}^s_{5} / \text{Slope}^s_{N}$ | Does depth density increase or decrease further from best? |

### Budget impact

Cross-level computation roughly triples the L0 factor count: 43 base → ~100–130 level-parameterized variants + ~10–15 cross-level ratios. Total L0 before L2: ~130.

---

## 6. Stage 5b: Cross-Scale Features

### The window axis

L1 factors computed at multiple windows provide a natural multi-scale view. Cross-scale features explicitly capture the **relationship between timescales** — something no single-window feature can express.

### Core cross-scale ratios

| Feature | Formula | Interpretation |
|---|---|---|
| OFI Scale Ratio | $\text{OFI}_{W=5} / \text{OFI}_{W=60}$ | Flow alignment across scales. Near 1 → sustained. Sign flip → decelerating. |
| Volatility Ratio | $\text{RV}_{W=20} / \text{RV}_{W=120}$ | Short-term vol spike relative to backdrop. |
| TI Scale Divergence | $\text{TI}_{W=5} - \text{TI}_{W=120}$ | Recent trade imbalance vs. sustained. |
| Spread Percentile | $S_q(b)$ relative to $W=60$ distribution | Is current spread tight or wide vs. recent regime? |
| Cancel Rate Acceleration | $\text{CR}_{W=5} / \text{CR}_{W=60}$ | Are cancels spiking now vs. recent average? |
| Arrival Rate Ratio | $\lambda_{W=5} / \lambda_{W=60}$ | Is trade intensity accelerating? |
| Footprint Delta Scale | $\overline{\Delta^{frac}}_{W=20} / \overline{\Delta^{frac}}_{W=120}$ | Is directional flow strengthening or fading? |

### Design principles

1. **Always ratio short/long** — keeps values > 1 when the fast signal is stronger
2. **Same-family only** — cross OFI($W=5$) with OFI($W=60$), not OFI with RV
3. **Sign-aware ratios** — for signed quantities, use difference rather than ratio when denominator can cross zero
4. **Budget:** ~15–20 cross-scale features. These tend to survive selection at a high rate.

---

## 7. Stage 5c: Interaction Features

### Purpose

Interaction features capture **conditional effects** — the predictive power of one factor depends on the level of another.

### Principled interaction pairs

Only create interactions where there's a **microstructure reason** for the cross-effect:

| Interaction | Formula | Microstructure rationale |
|---|---|---|
| OFI × Spread | $\text{OFI}_W \times S_{rel}$ | Flow impact amplified when liquidity is thin |
| Pressure × Volatility | $\text{Press}(b) \times \text{RV}_W$ | Directional pressure matters more in high-vol (breakout) |
| CDI × Cancel Rate | $\text{CDI}_k \times \text{CR}_W$ | Depth imbalance + high cancel → phantom liquidity / spoofing |
| Aggressiveness × Depth | $\bar{A}_W \times D_1(b)^{-1}$ | Aggressive trades into thin depth → larger impact |
| TI × Volume Entropy | $\text{TI}_W \times (H_{\max} - H_V)$ | Trade imbalance during concentrated volume → informed flow |
| OFI × LOB Entropy | $\text{OFI}_W \times (1 - \hat{H}^s_{LOB})$ | Order flow into concentrated book → higher impact |
| Burstiness × Run Length | $B \times \overline{\text{RL}}_W$ | Clustered trades in long same-direction runs → order-splitting |
| VPIN × Spread Change | $\overline{\text{VPIN}} \times \Delta S_q$ | High informed trading + widening spread → adverse selection |
| **Footprint Delta × Depth** | $\Delta^{frac}_b \times D_1(b)$ | Directional flow meeting deep/thin book → impact expectation |
| **Stack × Spread** | $\text{BuyStack}_b \times S_{rel}$ | Aggressive sweep stacking during wide spreads → urgent flow |
| **Duration × Volatility** | $\Delta t_b \times \text{RV}_W$ | Slow bar + high vol → large move brewing in quiet |

### How to compute

| Method | Formula | When to use |
|---|---|---|
| Product | $f_1 \times f_2$ | Both roughly symmetric or both positive |
| Signed product | $\text{sign}(f_1) \times |f_2|$ | One directional, one magnitude |
| Conditional z-score | $z(f_1 \mid f_2 > \text{median})$ | Test "does $f_1$ matter more when $f_2$ is high?" |

**Default to simple product.** Tree-based models discover interactions on their own — explicit interactions mainly help linear models and serve as feature-engineering "hints" for shallow trees.

**Budget:** ~25–35 interaction features. Keep small and principled.

---

## 8. Normalization & Cleaning

### The stationarity problem in 24/7 crypto

Unlike equity markets with daily open/close cycles, BTC-USDT trades continuously:

1. **Dollar-denominated factors drift with price** — $D_1$ in absolute terms is meaningless across a 20% move
2. **Volatility regimes shift without session boundaries** — "normal" spread at 3AM UTC differs from peak hours
3. **No natural "daily" anchor** — can't normalize to "today's average" cleanly

### Normalization strategy

| Factor Type | Recommended Normalization | Rationale |
|---|---|---|
| Dollar-value factors ($D_k$, TRL) | Divide by $M(t)$ to convert to "mid-price units" | Removes price-level drift |
| Bounded ratios ($\text{CDI}$, $\text{ABR}$, $\text{SI}$, $\Delta^{frac}$, $\theta^{norm}$, etc.) | No normalization needed | Already in $[-1, 1]$ or $[0, 1]$ |
| Unbounded signed factors ($\text{OFI}$, $\text{Press}$, $\Delta_b$) | Rolling z-score (lookback 60 bars) | Centers and scales to recent regime |
| Unbounded positive factors ($\text{RV}$, $S_q$, $\lambda_b$, $\Delta t_b$) | Rolling z-score or log-transform then z-score | Log compresses heavy right tail |
| Count factors ($n^{tick}_b$, $N_{\text{cancel}}$, $\text{NPeaks}$, $\text{BuyStack}$) | Leave raw or bin into quantiles | Trees handle counts natively |
| Footprint structural ($\text{POC}_{rel}$, $\text{VAW}$) | No normalization needed | Already in $[0, 1]$ |
| Footprint categorical ($\text{CVA}$, $\text{UnfHigh}$, $\text{UnfLow}$) | Leave as integers | Binary/ternary categorical |

### Handling edge cases

| Problem | Solution |
|---|---|
| $V_b = 0$ (bar with no volume — shouldn't happen with VIB) | Set all trade-based features = NaN; drop the bar |
| $Q^a_1 + Q^b_1 = 0$ (empty book at bar open) | Set all L0 = NaN; forward-fill |
| $\sigma^x_W = 0$ → z-score = $\frac{0}{0}$ | Set z-score = 0 |
| Extreme outliers | Winsorize at ±5σ **after** z-scoring |
| NaN in interactions | If either component NaN → interaction NaN. Forward-fill the interaction. |
| VIB bar with only 1 trade | Intra-bar distribution features (skew, kurtosis, CV, entropy) = NaN. POC = that trade's price. |
| Footprint with very few levels (<3) | Stack features = 0. VAW = 1. CVA uses available data. |

### Rank transform alternative

$$\text{rank}_W(x_b) = \frac{|\{i : x_{b-i} \leq x_b,\ 0 \leq i < W\}|}{W}$$

Maps any distribution to uniform $[0, 1]$. Fully robust to outliers but loses magnitude. **Recommendation:** z-score as default; rank as option. Tree-based models are robust to both.

---

## 9. Naming Convention

### Systematic naming

A consistent naming scheme is critical when managing 4500+ feature candidates. Every feature name encodes its full lineage:

```
{category}_{factor}_{side}_{level}_{window}_{transform}_{lookback}
```

### Field definitions

| Field | Values | Examples |
|---|---|---|
| `category` | `lob`, `ofi`, `aggr`, `micro`, `vol`, `liq`, `pvcorr`, `event`, `smart`, `txn`, **`fp`**, **`vib`**, **`hl`** | Short category prefix |
| `factor` | Snake-case factor name | `cdi`, `rv`, `skew_r`, `cr`, `abr`, `vpin`, **`delta_frac`**, **`poc_rel`**, **`buy_stack`**, **`dur`**, **`theta_norm`** |
| `side` | `bid`, `ask`, `both`, omitted if N/A | Only for side-specific factors |
| `level` | `l1`, `l3`, `l5`, `l10`, `l20`, omitted if N/A | LOB level parameter |
| `window` | **`w5`**, **`w20`**, **`w60`**, **`w120`**, omitted for L0/L0i | Aggregation window (bar count) |
| `transform` | `raw`, `mean`, `std`, `zscore`, `delta`, `lag1`, `ema`, `ratmed` | L2 transform applied |
| `lookback` | `w20`, `w60`, omitted for `raw` | L2 lookback length |

### Examples

| Full name | Meaning |
|---|---|
| `lob_cdi_both_l5_raw` | CDI at 5 levels, L0, no transform |
| `ofi_ofi_both_l1_w60_zscore_w60` | OFI level 1, 60-bar window, z-scored over 60 bars |
| `vol_rv_w60_delta` | Realized variance, 60-bar window, first difference |
| `event_cr_ask_w20_ema_w20` | Ask-side cancel rate, 20-bar window, EMA 20-bar |
| `lob_cdi_both_l1_l10_ratio` | Cross-level: CDI level-1 / CDI level-10 |
| `cross_ofi_w5_w60_ratio` | Cross-scale: OFI $W=5$ / OFI $W=60$ |
| `ix_ofi_spread_w60` | Interaction: OFI × Spread at $W=60$ |
| **`fp_delta_frac_raw`** | Footprint: per-bar delta fraction, no transform |
| **`fp_poc_rel_mean_w60`** | Footprint: rolling mean of POC relative position, 60-bar lookback |
| **`fp_buy_stack_raw`** | Footprint: max consecutive buy imbalance levels, no transform |
| **`vib_dur_log_zscore_w60`** | VIB metadata: log duration, z-scored over 60 bars |
| **`vib_theta_norm_raw`** | VIB metadata: normalized imbalance, no transform |
| **`hl_aor_w20_raw`** | Hyperliquid: address overlap ratio, 20-bar window |
| **`ix_fp_delta_depth_w60`** | Interaction: footprint delta × depth at $W=60$ |

### Benefits

1. **Grep-friendly** — `grep "^fp_"` gives all footprint features; `grep "_zscore_"` gives all z-scored
2. **Provenance** — any name traces back to the exact v4 formula, window, and transform
3. **Dedup-friendly** — features with same name except `l5` vs `l10` are obvious correlation-dedup candidates
4. **New categories** are easily extensible — add `entropy_` prefix if AFML entropy features are added later

---

## 10. Output Schema & Handoff to Selection

### Raw feature matrix (before selection)

| Column | Type | Description |
|---|---|---|
| `bar_index` | int64 | VIB bar sequence number |
| `bar_open_ts` | int64 (unix ms) | Bar open timestamp (for joining with labels) |
| `bar_close_ts` | int64 (unix ms) | Bar close timestamp (for temporal alignment) |
| `lob_cdi_both_l5_raw` | float32 | First feature |
| `lob_cdi_both_l5_zscore_w60` | float32 | ... |
| ... | float32 | All ~4500+ candidate features |

> **Note:** Labels and sample weights are NOT in this matrix. They are computed in the Labeling & Sampling document (Doc 3) and joined at training time via `bar_close_ts`. This pipeline outputs **features only**.

### Alignment rules

- **Timestamp convention:** all features at bar $b$ are computable from data at or before bar $b$'s close time
- **No future information:** all feature columns at row $b$ use only data from bars $\leq b$ and the current bar's intra-bar trades
- **L0 features** use the LOB snapshot at bar open — known before bar close
- **L0i features** use the bar's own trades — known at bar close
- **L1 features** use bars $[b-W+1, b]$ — all completed bars
- **One row per VIB bar** — no mixed-resolution alignment needed

### Storage recommendations

| Concern | Recommendation |
|---|---|
| Format | **Parquet** (columnar, compressed, fast partial reads) |
| Precision | float32 for all features (sufficient precision, halves memory) |
| Partitioning | By date (one parquet file per day) |
| Metadata | Store v4 factor version, pipeline version, window sizes, transform lookbacks in parquet metadata |
| Size estimate (pre-selection) | ~4500 features × ~1440 bars/day × 4 bytes ≈ ~26 MB/day |
| Size estimate (post-selection) | ~200 features × ~1440 bars/day × 4 bytes ≈ ~1.2 MB/day |

### Versioning

When the factor set or pipeline changes:
- Bump the pipeline version
- Never overwrite old feature files — downstream models reference a specific pipeline version
- Store the pipeline config (window sizes, transform lookbacks, level grid, VIB bar parameters) as a separate YAML

---

## Appendix: Pipeline Summary

```
Doc 0 Outputs
  ├── VIB bars (OHLCV + metadata)
  ├── LOB snapshots
  └── Raw trades (with side)
       │
       ▼
  ┌──────────────────────────────────────────────────┐
  │ Stage 1: L0 Computation                          │
  │   LOB snapshot at bar open                       │
  │   ~43 base factors × 3 level subsets = ~129      │
  │   + Hyperliquid L0 (order count, fragmentation)  │
  │   Output: one row per bar                        │
  └──────────────────────┬───────────────────────────┘
                         │
  ┌──────────────────────▼───────────────────────────┐
  │ Stage 2: L0i Computation (NEW)                   │
  │   Intra-bar trades T_b + bar metadata            │
  │   Footprint: POC, VA, delta, stacks, auctions    │
  │   VIB metadata: duration, imbalance, fragmentation│
  │   Trade patterns: arrival, size dist, inter-trade │
  │   ~38 factors per bar                            │
  │   Output: one row per bar                        │
  └──────────────────────┬───────────────────────────┘
                         │
  ┌──────────────────────▼───────────────────────────┐
  │ Stage 3: L1 Aggregation                          │
  │   ~124 L1 base factors × 4 windows = ~496       │
  │   + ~20 L0i→L1 promotions × 3 windows = ~60     │
  │   Includes cancel inference (v4 §7.0)            │
  │   Output: one row per bar                        │
  └──────────────────────┬───────────────────────────┘
                         │
  ┌──────────────────────▼───────────────────────────┐
  │ Stage 4: L2 Temporal Transforms                  │
  │   ~723 base × ~7 transforms = ~5000             │
  │   Output: one row per bar                        │
  └──────────────────────┬───────────────────────────┘
                         │
  ┌──────────────────────▼───────────────────────────┐
  │ Stage 5: Cross-Level + Cross-Scale + Interactions│
  │   + ~15 cross-level + ~20 cross-scale            │
  │   + ~30 interactions                             │
  │   Raw candidates: ~4500+                         │
  └──────────────────────┬───────────────────────────┘
                         │
                         ▼
               Raw Feature Matrix
            (parquet, float32, ~4500+ cols)
            (~1440 rows/day, ~26 MB/day)
                         │
           ┌─────────────┼──────────────┐
           ▼             ▼              ▼
     Join labels    Feature Selection   Model Training
      (Doc 3)         (Doc 4)           (Doc 5)
```
