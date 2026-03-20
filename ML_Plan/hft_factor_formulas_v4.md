# HFT Factor Formula Reference v4 — VIB Bar Edition (BTC-USDT Perpetual, Hyperliquid)

> **Scope:** 12 categories, ~180 atomic factors (L0, L0i, L1 base formulas only — rolling aggregations and L2 transforms in Doc 2 v2)  
> **Bar type:** Volume Imbalance Bars (VIB) from Doc 0  
> **Data assumed:** VIB bars + intra-bar raw trades (with taker `side`) + LOB snapshots at bar open  
> **Convention:** All formulas use consistent notation defined below  
> **Predecessor:** v3 (calendar-time bars) preserved as `hft_factor_formulas_v3.md`

### Changelog v4 (from v3.1)

- **Bar type reframing:** All factors now defined for VIB bars, not calendar-time bars
- **LOB snapshot alignment:** L0 factors use the LOB snapshot at bar OPEN (pre-trade state), not bar close
- **Window notation:** All L1 rolling windows expressed in bar counts ($W$ bars), not time intervals. Calibration table in Appendix A.
- **§4.3 OHLC volatility estimators:** Added duration-normalization annotation for variable-length VIB bars
- **§5.6 Amihud:** Added VIB volume-homogenization annotation
- **§8.1 VPIN:** Added redundancy note (VIB imbalance conceptually overlaps with VPIN volume classification)
- **§9.2 Arrival rate:** Redefined for VIB bars (trades per bar, normalized by duration)
- **NEW §10: Footprint Features** — Intra-bar order flow decomposition by price level (delta profile, POC, value area, imbalance stacking)
- **NEW §11: VIB Bar Metadata Factors** — Duration, imbalance, tick count, VWAP-close deviation
- **NEW §12: Hyperliquid-Specific Factors** — Order count fragmentation, address-based features
- Updated factor count to ~180 (after redundancy cuts and moving rolling aggregations to Doc 2 v2: dropped Parkinson, RVCoM, SMF, VWPR, ABAR, ImbDir, imbalance total counts; merged duplicate definitions; moved §10.7 rolling footprint, rolling NSMF, rolling λ, DurRatio to Doc 2)

---

## Design Note: Bar Type & Computation Levels

### VIB Bar Context

All factors in this document are defined on **Volume Imbalance Bars (VIB)** constructed in Doc 0. Key properties:

- Each VIB bar is triggered when the cumulative signed volume imbalance exceeds a dynamic threshold
- Bars have **variable time duration** (seconds to minutes) but roughly **uniform information content**
- Each bar carries OHLCV data plus metadata: `duration_ms`, `imbalance`, `n_ticks`, `n_orders`, `n_unique_addrs`, `vwap`
- The raw trades within each bar are available for intra-bar feature computation

### LOB Snapshot Alignment

**L0 factors use the LOB snapshot at bar OPEN time** — the most recent LOB snapshot with timestamp strictly before the bar's first trade. This captures the book state **before** the bar's trades occurred, which is the state you'd observe at decision time.

In code: `snapshot = lob_snapshots.asof(bar_open_timestamp - 1)` (strictly pre-trade)

### Computation Levels

Every factor falls into one of three computation levels:

| Level | Description | Example |
|-------|-------------|---------|
| **L0: Single-snapshot** | Computed from one LOB snapshot at bar open time $t$. No window needed. | Spread $S_q(t)$, Depth Imbalance $\text{DI}_i(t)$ |
| **L0i: Intra-bar** | Computed from raw trades within a single bar. Uses tick-level data between bar open and close. | Footprint delta profile, intra-bar VWAP, tick entropy |
| **L1: Across-bar rolling window** | Requires a rolling window of $W$ bars. | Realized Variance $\text{RV}_W$, Trade Imbalance $\text{TI}_W$, OFI$_W$ |
| **L2: Temporal transform** | Applied to a time series of L0/L0i/L1 values (e.g., rolling mean, z-score, lag, diff). **Not defined here.** | Rolling mean of RV, Z-score of Pressure, Δ of cancel rate |

**Rule:** Each formula below specifies its computation level. L2 transforms are deliberately excluded to keep this document focused on raw factor definitions. Apply L2 transforms uniformly across all factors during feature engineering (Doc 2).

### Window Notation

All L1 windows are expressed in **bar counts** ($W$ bars), not time intervals. Since VIB bars have variable duration, bar-count windows adapt naturally: during active markets, $W$ bars cover less time (but the same information content); during quiet markets, more time.

| Window $W$ | Approximate time (at ~1 VIB bar/min avg) | Use |
|---|---|---|
| $W = 5$ | ~5 min | Ultra-short, LOB event factors |
| $W = 20$ | ~20 min | Micro features: cancel metrics, OFI, aggressiveness |
| $W = 60$ | ~1 hour | Standard: moments, correlation, trend strength |
| $W = 120$ | ~2 hours | Longer-term: VPIN, Amihud, volume entropy |

> **v3 → v4 mapping:** v3 used time-based windows (1s, 10s, 1min, 5min). In v4, replace time with bar count. The exact mapping depends on average VIB bar rate. For BTC on Hyperliquid at ~1 bar/min, $W=60$ ≈ 1 hour.

---

## 0. Notation & Data Schema

### Orderbook Snapshot (at bar open time $t$)

> **Alignment:** The LOB snapshot used for each bar is the one at the bar's OPEN timestamp — the book state before the bar's trades occurred. In code: `snapshot = lob_snapshots.asof(bar_open_timestamp - 1)`

| Symbol | Meaning |
|--------|---------|
| $P^a_i(t)$ | Ask price at level $i$ ($i=1$ is best ask) |
| $P^b_i(t)$ | Bid price at level $i$ ($i=1$ is best bid) |
| $Q^a_i(t)$ | Ask quantity at level $i$ |
| $Q^b_i(t)$ | Bid quantity at level $i$ |
| $N$ | Total number of orderbook levels (e.g. 5, 10, 20) |
| $M(t)$ | Mid-price: $M(t) = \frac{P^a_1(t) + P^b_1(t)}{2}$ |
| $M_w(t)$ | Weighted mid-price: $M_w(t) = \frac{P^a_1(t) \cdot Q^b_1(t) + P^b_1(t) \cdot Q^a_1(t)}{Q^a_1(t) + Q^b_1(t)}$ |

### Trade / Tick Data

| Symbol | Meaning |
|--------|---------|
| $p_k$ | Trade price of the $k$-th trade |
| $q_k$ | Trade quantity of the $k$-th trade |
| $d_k$ | Trade direction: $+1$ = taker buy, $-1$ = taker sell (direct from Hyperliquid `side` field — no tick rule needed) |
| $t_k$ | Timestamp of the $k$-th trade |

### VIB Bar Data

| Symbol | Meaning |
|--------|---------|
| $O_b, H_b, L_b, C_b$ | Open, High, Low, Close prices of bar $b$ |
| $V_b$ | Total volume of bar $b$ |
| $\Delta t_b$ | Duration of bar $b$ in milliseconds (`duration_ms`) |
| $\theta_b$ | Signed volume imbalance that triggered bar $b$ (`imbalance`) |
| $n^{tick}_b$ | Number of trades within bar $b$ (`n_ticks`) |
| $n^{ord}_b$ | Number of distinct orders within bar $b$ (`n_orders`) |
| $n^{addr}_b$ | Number of unique taker addresses within bar $b$ (`n_unique_addrs`) |
| $\text{VWAP}_b$ | Volume-weighted average price within bar $b$ |
| $\mathcal{T}_b$ | Set of all trades $(p_k, q_k, d_k, t_k)$ within bar $b$ — used for intra-bar (L0i) features |

### Cancel / LOB Diff Data

| Symbol | Meaning |
|--------|---------|
| $N_{\text{cancel},\tau}$ | Number of order cancellations in interval $\tau$ |
| $N_{\text{new},\tau}$ | Number of new order placements in interval $\tau$ |
| $N_{\text{trades},\tau}$ | Number of trades (fills) in interval $\tau$ |
| $q_c$ | Quantity of a single cancelled order |
| $q_n$ | Quantity of a single newly placed order |
| $q_f$ | Quantity of a single filled order |
| $t_{\text{place},c}$ | Placement timestamp of cancelled order $c$ |
| $t_{\text{cancel},c}$ | Cancellation timestamp of cancelled order $c$ |

### General

| Symbol | Meaning |
|--------|---------|
| $r_b$ | Log-return of bar $b$: $r_b = \ln C_b - \ln C_{b-1}$ |
| $r_\tau$ | Log-return over interval $\tau$: $r_\tau = \ln M(t) - \ln M(t-\tau)$ (for intra-bar use) |
| $V_\tau$ | Total volume in interval $\tau$: $V_\tau = \sum_{k \in \tau} q_k$ |
| $W$ | Rolling window size in **bar count** (e.g., $W=20$ means 20 bars) |
| $\overline{x}$ | Mean of $x$ over a specified window |
| $\sigma(x)$ | Standard deviation of $x$ over a specified window |

---

## 1. Order Book State & Pressure Factors

### 1.1 Bid-Ask Spread (Tightness) `L0`

**Quoted Spread**

$$S_q(t) = P^a_1(t) - P^b_1(t)$$

**Relative Spread (bps)**

$$S_{rel}(t) = \frac{P^a_1(t) - P^b_1(t)}{M(t)} \times 10000$$

**Effective Spread** (per trade $k$) `L0 per trade`

$$S_{eff,k} = 2 \cdot d_k \cdot (p_k - M(t_k))$$

**Log Spread**

$$S_{log}(t) = \ln P^a_1(t) - \ln P^b_1(t)$$

---

### 1.2 Order Book Depth `L0`

**Best-Level Depth**

$$D_1(t) = Q^a_1(t) + Q^b_1(t)$$

**Cumulative $k$-Level Depth**

$$D_k(t) = \sum_{i=1}^{k} \left(Q^a_i(t) + Q^b_i(t)\right)$$

**Depth Imbalance (Level $i$)**

$$\text{DI}_i(t) = \frac{Q^b_i(t) - Q^a_i(t)}{Q^b_i(t) + Q^a_i(t)}$$

**Cumulative Depth Imbalance (Top $k$ levels)**

$$\text{CDI}_k(t) = \frac{\sum_{i=1}^{k} Q^b_i(t) - \sum_{i=1}^{k} Q^a_i(t)}{\sum_{i=1}^{k} Q^b_i(t) + \sum_{i=1}^{k} Q^a_i(t)}$$

> **Cross-ref:** $\text{CDI}_N$ is the same as what was formerly "Depth Pressure Component" in the Pressure decomposition (Section 1.6). Also equivalent to "Order Imbalance" $\text{OI}$ when computed over total LOB depth.

**Depth-Weighted Price (VWAP of each side)**

$$\bar{P}^b_k(t) = \frac{\sum_{i=1}^{k} P^b_i(t) \cdot Q^b_i(t)}{\sum_{i=1}^{k} Q^b_i(t)}, \quad \bar{P}^a_k(t) = \frac{\sum_{i=1}^{k} P^a_i(t) \cdot Q^a_i(t)}{\sum_{i=1}^{k} Q^a_i(t)}$$

**Mid-Price Relative Position** (中间价相对买方均价均值 — from Guotai Junan)

$$\text{MPRP}^b_k(t) = \frac{M(t) - \bar{P}^b_k(t)}{M(t)}, \quad \text{MPRP}^a_k(t) = \frac{\bar{P}^a_k(t) - M(t)}{M(t)}$$

> Measures how "stretched" the mid-price is relative to the depth-VWAP of each side. High $\text{MPRP}^b$ means bid-side depth concentrates far below mid — potentially thin support near the top of book.

**Mid-Price Relative Position Asymmetry**

$$\text{MPRP}^{\Delta}_k(t) = \text{MPRP}^a_k(t) - \text{MPRP}^b_k(t)$$

---

### 1.3 Order Book Width / Breadth `L0`

**Price Range Covered (Ask Side, top $k$ levels)**

$$W^a_k(t) = P^a_k(t) - P^a_1(t)$$

**Price Range Covered (Bid Side, top $k$ levels)**

$$W^b_k(t) = P^b_1(t) - P^b_k(t)$$

**Width Imbalance**

$$\text{WI}_k(t) = \frac{W^a_k(t) - W^b_k(t)}{W^a_k(t) + W^b_k(t)}$$

**Level Gap** (gap between adjacent levels, ask side)

$$G^a_i(t) = P^a_{i+1}(t) - P^a_i(t), \quad i = 1, \ldots, k-1$$

**Mean Gap Ratio** (density proxy)

$$\bar{G}^a_k(t) = \frac{1}{k-1}\sum_{i=1}^{k-1} G^a_i(t)$$

---

### 1.4 Order Book Slope `L0`

**Bid Slope**

$$\text{Slope}^b(t) = \frac{\sum_{i=1}^{N} Q^b_i(t)}{P^b_1(t) - P^b_N(t)}$$

**Ask Slope**

$$\text{Slope}^a(t) = \frac{\sum_{i=1}^{N} Q^a_i(t)}{P^a_N(t) - P^a_1(t)}$$

**Simple Slope (Best-Level Only)**

$$\text{Slope}_1(t) = \frac{Q^a_1(t) + Q^b_1(t)}{P^a_1(t) - P^b_1(t)}$$

**Slope Imbalance**

$$\text{SI}(t) = \frac{\text{Slope}^b(t) - \text{Slope}^a(t)}{\text{Slope}^b(t) + \text{Slope}^a(t)}$$

---

### 1.5 Order Book Shape (LOB Convexity, Hump, Multi-Peak) `L0`

> Source: Zhihu 冷门高频因子 — LOB shape analysis. Academic reference: Bouchaud et al. (2002) on average LOB shape.

**LOB Convexity** (second derivative of quantity w.r.t. price distance)

For each side $s \in \{a, b\}$, define the distance from best quote $\delta_i = |P^s_i(t) - P^s_1(t)|$ and quantity profile $Q^s_i$. Fit a quadratic:

$$Q^s(δ) \approx \alpha + \beta \delta + \gamma \delta^2$$

The **convexity coefficient** is:

$$\text{Conv}^s(t) = \hat{\gamma}^s$$

> $\gamma > 0$: convex (U-shaped, depth concentrates away from best) — typical of liquid markets.  
> $\gamma < 0$: concave (hump near best) — depth thins out further from best.

**Convexity Imbalance**

$$\text{CI}(t) = \text{Conv}^b(t) - \text{Conv}^a(t)$$

**Hump Position** (level at which quantity peaks)

$$i^{*,s}(t) = \arg\max_{i \in \{1,\ldots,N\}} Q^s_i(t)$$

As a normalized feature (0 = best level, 1 = deepest level):

$$\text{HP}^s(t) = \frac{i^{*,s}(t) - 1}{N - 1}$$

> $\text{HP} \approx 0$: depth peaks at best quote (aggressive resting liquidity). $\text{HP} \approx 1$: depth peaks at deep levels (passive/defensive posture).

**Hump Prominence** (how much the peak stands out)

$$\text{Prom}^s(t) = \frac{Q^s_{i^*}(t)}{\frac{1}{N}\sum_{i=1}^{N} Q^s_i(t)}$$

> Ratio of peak-level quantity to mean-level quantity. Values >> 1 indicate a sharp hump.

**Multi-Peak Detection** (number of local maxima)

$$\text{NPeaks}^s(t) = \left|\left\{i : Q^s_i > Q^s_{i-1} \text{ and } Q^s_i > Q^s_{i+1},\ 2 \leq i \leq N-1\right\}\right|$$

> $\text{NPeaks} = 1$: standard humped shape. $\text{NPeaks} \geq 2$: bimodal/multimodal depth distribution — may indicate multiple participant groups or support/resistance clustering.

**LOB Depth Entropy** (uniformity of liquidity across levels)

For side $s \in \{a, b\}$, define the depth proportion at each level:

$$f^s_i(t) = \frac{Q^s_i(t)}{\sum_{j=1}^{N} Q^s_j(t)}$$

$$H^s_{LOB}(t) = -\sum_{i=1}^{N} f^s_i(t) \ln\left(f^s_i(t)\right)$$

> Maximum entropy $H_{\max} = \ln(N)$ when depth is perfectly uniform across all levels. Low entropy → depth concentrated at few levels (fragile, wall-like). High entropy → evenly distributed (resilient to sweeps).

**Normalized LOB Entropy** (scale-free, in $[0, 1]$)

$$\hat{H}^s_{LOB}(t) = \frac{H^s_{LOB}(t)}{\ln(N)}$$

**Entropy Imbalance** (bid vs. ask entropy asymmetry)

$$\Delta H_{LOB}(t) = \hat{H}^a_{LOB}(t) - \hat{H}^b_{LOB}(t)$$

> Positive $\Delta H$: ask-side more uniform than bid-side → bid depth is "lumpier" (concentrated at specific levels, potentially more fragile). Useful as a regime feature alongside CDI and Pressure.

**LOB Shape Vector** (structured input for ML; not a single scalar)

$$\mathbf{X}_{LOB}(t) = \left[Q^b_1, \ldots, Q^b_N,\ Q^a_1, \ldots, Q^a_N,\ P^b_1, \ldots, P^b_N,\ P^a_1, \ldots, P^a_N\right]$$

---

### 1.6 Multi-Level Pressure `L0`

> Formerly Category 3. Pressure factors combine depth and width into directional signals.

**Multi-Level Pressure** (Tianfeng Securities style)

$$\text{Press}(t) = \ln\left(\frac{\sum_{i=1}^{N} Q^b_i(t) \cdot w_i}{\sum_{i=1}^{N} Q^a_i(t) \cdot w_i}\right)$$

where $w_i$ is a decay weight, e.g. $w_i = e^{-\lambda(i-1)}$ or $w_i = \frac{1}{i}$.

**Simple L1 Pressure**

$$\text{Press}_1(t) = \frac{Q^b_1(t) - Q^a_1(t)}{Q^b_1(t) + Q^a_1(t)}$$

> **Note:** $\text{Press}_1(t) \equiv \text{DI}_1(t)$ (Section 1.2). Included here for completeness of the pressure framework.

**Pressure Decomposition** (Depth × Width)

$$\text{Press}^{depth}(t) = \text{CDI}_N(t), \quad \text{Press}^{width}(t) = \text{WI}_N(t)$$

> These are aliases to factors in Sections 1.2 and 1.3. Listed here to document the Tianfeng decomposition framework.

**Depth × Width Interaction** (regime detection)

$$\text{DW}(t) = \text{CDI}_N(t) \times \text{WI}_N(t)$$

> Same-sign CDI and WI → reinforcing (strong directional signal). Opposite signs → ambiguous regime.

**Pressure Acceleration** `L1 (requires two snapshots)`

$$\dot{\text{Press}}(t) = \text{Press}(t) - \text{Press}(t-1)$$

---

## 2. Order Flow / Imbalance Factors

### 2.1 Order Flow Imbalance — OFI (Cont, Kukanov & Stoikov, 2014) `L1`

We observe the limit order book at discrete times $t_0 < t_1 < \ldots < t_N$. At each observation $n$, let:

- $P^B_n, P^A_n$ — best bid and best ask prices
- $q^B_n, q^A_n$ — quantities at the best bid and best ask

Define the **bid-side quantity change**:

$$\Delta q^B_n = q^B_n \cdot \mathbf{1}_{\{P^B_n \geq P^B_{n-1}\}} - q^B_{n-1} \cdot \mathbf{1}_{\{P^B_n \leq P^B_{n-1}\}}$$

And the **ask-side quantity change**:

$$\Delta q^A_n = q^A_n \cdot \mathbf{1}_{\{P^A_n \leq P^A_{n-1}\}} - q^A_{n-1} \cdot \mathbf{1}_{\{P^A_n \geq P^A_{n-1}\}}$$

The **event-level order flow imbalance** is:

$$e_n = \Delta q^B_n - \Delta q^A_n$$

The **aggregated Order Flow Imbalance** over an interval containing $N$ observations:

$$\text{OFI}_N = \sum_{n=1}^{N} e_n$$

**Contemporaneous Price Impact Regression** (Cont et al., Eq. 7):

$$\Delta P_N = \beta \cdot \text{OFI}_N + \varepsilon_N$$

where $\Delta P_N = M(t_N) - M(t_0)$ is the mid-price change over the interval, and $\hat{\beta}$ measures the price impact per unit of order flow imbalance.

**Multi-Level OFI Extension** (extending Cont et al. to levels $1, \ldots, L$):

For each level $i$, define $e^{(i)}_n$ analogously using $P^{B,(i)}_n, P^{A,(i)}_n, q^{B,(i)}_n, q^{A,(i)}_n$.

$$\text{OFI}^{(i)}_N = \sum_{n=1}^{N} e^{(i)}_n, \quad i = 1, \ldots, L$$

**Multi-level regression:**

$$\Delta P_N = \alpha + \sum_{i=1}^{L} \beta_i \cdot \text{OFI}^{(i)}_N + \varepsilon_N$$

---

### 2.2 Normalized & Derived OI Measures `L1`

**Normalized OFI**

$$\text{OFI}^{norm}_N = \frac{\text{OFI}_N}{\sum_{n=1}^{N}(q^B_n + q^A_n)}$$

**Trade Imbalance** (from trade tape, over interval $\tau$)

$$\text{TI}_\tau = \frac{\sum_{k \in \tau} d_k \cdot q_k}{\sum_{k \in \tau} q_k}$$

**Trade-Price Deviation from Mid** (per trade)

$$z_k = \ln(p_k) - \ln(M(t_k))$$

**Aggregated Z** (over interval $\tau$)

$$\bar{z}_\tau = \frac{1}{n_\tau} \sum_{k \in \tau} z_k$$

---

### 2.3 OI Statistical Properties `L1`

Computed over a window containing OI values $\{\text{OFI}(1), \ldots, \text{OFI}(m)\}$:

**OFI Autocorrelation (lag $l$)**

$$\rho^{OFI}_l = \text{Corr}\left(\text{OFI}(j),\ \text{OFI}(j-l)\right)$$

**OFI Volatility (clustering proxy)**

$$\sigma^{OFI} = \text{std}\left(\{\text{OFI}(j)\}\right)$$

**OFI Mean-Reversion Speed** (AR(1) proxy)

$$\text{OFI}(j) = \alpha + \phi \cdot \text{OFI}(j-1) + \varepsilon, \quad \kappa = -\ln(|\hat{\phi}|)$$

$\kappa > 0$ implies mean-reversion; larger $\kappa$ = faster reversion.

**OFI Sign Persistence**

$$\text{SP} = \frac{1}{m-1} \sum_{j=2}^{m} \mathbf{1}\left[\text{sign}(\text{OFI}(j)) = \text{sign}(\text{OFI}(j-1))\right]$$

> **Cross-ref:** Analogous to $P_{\text{same}}$ in Section 9.5, but computed on OFI signs rather than raw trade signs.

**OFI Absolute Change Rate**

$$\text{ACR} = \frac{1}{m-1}\sum_{j=2}^{m} |\text{OFI}(j) - \text{OFI}(j-1)|$$

---

### 2.4 Order Aggressiveness `L0 per trade / L1 aggregated`

**Aggressiveness Score** (how far past the mid-price a taker trade executes)

$$A_k = d_k \cdot \frac{p_k - M(t_k)}{S_q(t_k) / 2}$$

$A_k > 1$ means the trade walked through multiple levels.

**Mean Aggressiveness** (over interval $\tau$)

$$\bar{A}_\tau = \frac{1}{n_\tau}\sum_{k \in \tau} A_k$$

**Aggressiveness Differential**

$$\Delta A_\tau = \bar{A}^{buy}_\tau - \bar{A}^{sell}_\tau$$

where $\bar{A}^{buy}$ averages over taker-buy trades only, $\bar{A}^{sell}$ over taker-sell.

---

### 2.5 Active Buy/Sell Proportion `L1`

**Active Buy Volume Ratio**

$$\text{ABR}_W = \frac{\sum_{b=1}^{W} V^{buy}_b}{\sum_{b=1}^{W} V_b}$$

> **Redundancy note:** For a single bar, $\text{ABR}_b = 0.5 + \Delta^{frac}_b / 2$ — a linear transform of $\Delta^{frac}_b$ (§10.4). ABR is only kept at $W > 1$ bar windows where the rolling aggregate provides a distinct view from per-bar delta. ~~Active Buy Amount Ratio (ABAR)~~ removed — for BTC, price varies <0.1% within a window, so ABAR ≈ ABR.

**Proportion of Trades Above Best Ask** (价格高于卖一的比例 — from Guotai Junan)

$$\text{PTA}_\tau = \frac{\sum_{k \in \tau} \mathbf{1}[p_k > P^a_1(t_k)]}{\sum_{k \in \tau} 1}$$

> Measures how often trades "walk the book" past the best ask. High PTA signals aggressive buying.

**Proportion of Trades Below Best Bid**

$$\text{PTB}_\tau = \frac{\sum_{k \in \tau} \mathbf{1}[p_k < P^b_1(t_k)]}{\sum_{k \in \tau} 1}$$

**Large Trade Buy Ratio** (trades above $\theta$ threshold)

$$\text{LTBR}_\tau = \frac{\sum_{k \in \tau} p_k q_k \cdot \mathbf{1}[d_k=+1] \cdot \mathbf{1}[p_k q_k > \theta]}{\sum_{k \in \tau} p_k q_k \cdot \mathbf{1}[p_k q_k > \theta]}$$

**Buy Concentration** (Herfindahl-style)

$$\text{BC}_\tau = \frac{\sum_{k \in \tau, d_k=+1} (p_k q_k)^2}{\left(\sum_{k \in \tau, d_k=+1} p_k q_k\right)^2}$$

Higher values → fewer large buyers dominating.

---

## 3. Price Microstructure / Momentum-Reversal Factors

### 3.1 High-Frequency Reversal `L1`

**HF Return Mean** (intraday bar returns averaged)

Given $n$ bars of length $\tau$:

$$\text{HFRev} = \frac{1}{n}\sum_{j=1}^{n} r_{\tau,j}$$

**Signed Volume-Weighted Return**

$$\text{SVWR}_\tau = \frac{\sum_{k \in \tau} d_k \cdot q_k \cdot r_k}{\sum_{k \in \tau} q_k}$$

where $r_k = \ln(p_k) - \ln(p_{k-1})$.

---

### 3.2 Trend Strength `L1`

**Intraday Displacement / Path Ratio** (趋势强度因子)

$$\text{TS} = \frac{|M(T) - M(0)|}{\sum_{j=1}^{n} |M(t_j) - M(t_{j-1})|}$$

Values near 1 → strong trend; near 0 → choppy/mean-reverting.

**Return-to-Volatility Ratio** (收益波动比因子 — from Zhihu sources)

$$\text{RWR} = \frac{P_{\text{close}} - P_{\text{open}}}{P_{\text{high}} - P_{\text{low}}}$$

> Ranges from $-1$ to $+1$. Measures trend efficiency within a bar.

**Mid-Price Basis (MPB)**

$$\text{MPB}(t) = \text{VWAP}_\tau(t) - \frac{M(t) + M(t-\tau)}{2}$$

where $\text{VWAP}_\tau(t) = \frac{\sum_{k \in [t-\tau, t]} p_k \cdot q_k}{\sum_{k \in [t-\tau, t]} q_k}$

**Cumulative MPB**

$$\text{CMPB}(T) = \sum_{j=1}^{n} \text{MPB}(t_j)$$

---

### 3.3 Intraday Window Momentum `L1`

**Session Return** (custom crypto sessions)

$$r_{\text{session}} = \ln M(t_{\text{end}}) - \ln M(t_{\text{start}})$$

**Net Buy Pressure per Session**

$$\text{NBP}_{\text{session}} = \frac{\sum_{k \in \text{session}} d_k \cdot p_k \cdot q_k}{\sum_{k \in \text{session}} p_k \cdot q_k}$$

**Rolling Momentum Differential**

$$\Delta r_W(t) = r_{[t-W/2, t]} - r_{[t-W, t-W/2]}$$

---

### 3.4 Price Impact Factors `L1`

**Amihud-Style Price Impact** (per interval $\tau$)

$$\lambda_\tau = \frac{|r_\tau|}{V_\tau}$$

**Kyle's Lambda** (regression-based)

$$\Delta M(t) = \alpha + \lambda \cdot \text{SignedFlow}(t) + \varepsilon$$

where $\text{SignedFlow}(t) = \sum_{k \in \delta t} d_k \cdot q_k$.

**Permanent vs. Transient Impact** (Cont-Kukanov-Stoikov)

Using the OFI defined in Section 2.1:

$$\Delta P_N = \alpha + \beta_1 \cdot \text{OFI}_N + \beta_2 \cdot \text{OFI}_{N-1} + \varepsilon_N$$

- $\beta_1$: contemporaneous impact
- $\beta_1 + \beta_2$: permanent impact
- $-\beta_2$: transient (reverted) component

**Volume-Normalized Impact** (square-root law)

$$\text{VNI}_\tau = \frac{|r_\tau|}{\sqrt{V_\tau}}$$

---

## 4. Volatility & Higher-Order Moment Factors

> **Design note:** All factors here are L1 (within-window aggregation). They require a set of observations within a window. L2 temporal transforms (rolling mean of RV, etc.) are applied downstream.

### 4.1 Return Moments `L1`

Given $n$ returns $\{r_1, \ldots, r_n\}$ within a single computation window:

**Realized Variance**

$$\text{RV} = \sum_{j=1}^{n} r_j^2$$

**Realized Volatility**

$$\sigma_{\text{RV}} = \sqrt{\text{RV}}$$

**Return Skewness**

$$\text{Skew}^r = \frac{\frac{1}{n}\sum_{j=1}^{n}(r_j - \bar{r})^3}{\left(\frac{1}{n}\sum_{j=1}^{n}(r_j - \bar{r})^2\right)^{3/2}}$$

**Return Kurtosis**

$$\text{Kurt}^r = \frac{\frac{1}{n}\sum_{j=1}^{n}(r_j - \bar{r})^4}{\left(\frac{1}{n}\sum_{j=1}^{n}(r_j - \bar{r})^2\right)^{2}}$$

> **Convention:** This is raw (non-excess) kurtosis. Normal distribution = 3. For excess kurtosis (normal = 0), subtract 3: $\text{Kurt}^r_{\text{excess}} = \text{Kurt}^r - 3$. Most ML libraries (scipy, pandas) default to excess kurtosis — be consistent.

**Downside Variance**

$$\text{DV} = \sum_{j: r_j < 0} r_j^2$$

**Upside Variance**

$$\text{UV} = \sum_{j: r_j \geq 0} r_j^2$$

**Up/Down Variance Ratio**

$$\text{UDVR} = \frac{\text{UV}}{\text{DV}}$$

---

### 4.2 Volume Moments `L1`

Given $n$ volume observations $\{V_1, \ldots, V_n\}$:

**Volume Variance**

$$\text{VV} = \frac{1}{n}\sum_{j=1}^{n}(V_j - \bar{V})^2$$

**Volume Coefficient of Variation**

$$\text{CV}^V = \frac{\sqrt{\text{VV}}}{\bar{V}}$$

**Volume Skewness**

$$\text{Skew}^V = \frac{\frac{1}{n}\sum_{j=1}^{n}(V_j - \bar{V})^3}{(\sqrt{\text{VV}})^3}$$

**Volume Kurtosis**

$$\text{Kurt}^V = \frac{\frac{1}{n}\sum_{j=1}^{n}(V_j - \bar{V})^4}{(\text{VV})^2}$$

> **Convention:** Raw kurtosis (same as Return Kurtosis above). Subtract 3 for excess kurtosis.

---

### 4.3 Realized Volatility Decompositions `L1`

**Bipower Variation** (robust to jumps)

$$\text{BPV} = \frac{\pi}{2} \sum_{j=2}^{n} |r_j| \cdot |r_{j-1}|$$

**Jump Component**

$$J = \max\left(\text{RV} - \text{BPV},\ 0\right)$$

**Relative Jump Ratio**

$$\text{RJ} = \frac{J}{\text{RV}}$$

**Garman-Klass Volatility** (per bar with OHLC)

$$\sigma_{GK}^2 = 0.5 \cdot (\ln H - \ln L)^2 - (2\ln 2 - 1)(\ln C - \ln O)^2$$

> ~~Parkinson Volatility~~ removed in v4 — it uses only $(\ln H - \ln L)^2$, which is a strict subset of GK's information (GK adds the close-open term). Correlation with GK is typically >0.95.

**Rogers-Satchell Volatility**

$$\sigma_{RS}^2 = (\ln H - \ln C)(\ln H - \ln O) + (\ln L - \ln C)(\ln L - \ln O)$$

> **VIB bar note:** GK and RS assume fixed-duration bars. With variable-duration VIB bars, the raw estimates are still valid as "volatility per bar" (natural when each bar has similar information content). For cross-bar comparison, consider duration-normalized variants: $\sigma^2_{GK,norm} = \sigma^2_{GK} / \Delta t_b$. Compute both and let feature selection (Doc 4) decide.

---

### 4.4 Cross-Moment: Return × Volume `L1`

> ~~Return-Volume Co-Moment (RVCoM)~~ removed in v4 — it is an unnormalized version of $\rho_{PV}$ (§6.1). The normalized Price-Volume Correlation is preferred; it is scale-invariant and more interpretable.

---

## 5. Liquidity Factors (Bervas Framework)

### 5.1 Tightness

(See Section 1.1 — Quoted Spread, Relative Spread, Effective Spread, Log Spread)

### 5.2 Depth `L0`

**Total Resting Liquidity (top $k$ levels, dollar terms)**

$$\text{TRL}_k(t) = \sum_{i=1}^{k}\left(P^a_i(t) Q^a_i(t) + P^b_i(t) Q^b_i(t)\right)$$

**Depth at Best (dollar terms)**

$$\text{D1}_{\$}(t) = P^a_1(t) Q^a_1(t) + P^b_1(t) Q^b_1(t)$$

**Depth Ratio** (best vs. total)

$$\text{DR}_k(t) = \frac{\text{D1}_{\$}(t)}{\text{TRL}_k(t)}$$

### 5.3 Resiliency `L1`

**Depth Recovery Rate** (after a large taker trade at time $t_0$)

$$\text{RR}(\Delta t) = \frac{Q^{\text{side}}_{\text{refilled}}(t_0 + \Delta t)}{Q^{\text{side}}_{\text{consumed}}(t_0)}$$

**Spread Recovery Time**

$$\tau_{\text{recover}} = \inf\left\{s > 0 : S_q(t_0 + s) \leq S_q(t_0^{-})\right\}$$

**Exponential Resilience Rate** (decay fit)

$$S_q(t_0 + s) - S_q^{\text{eq}} \approx A \cdot e^{-\gamma s}$$

$\hat{\gamma}$ is the resilience rate.

### 5.4 Breadth

(See Section 1.3 — Width factors, Width Imbalance, Gap metrics)

### 5.5 Session Liquidity `L1`

**Volume Concentration by Session**

$$\text{VC}_{\text{session}} = \frac{V_{\text{session}}}{V_{\text{24h}}}$$

**Volume Entropy**

$$H_V = -\sum_{j=1}^{B} \frac{V_j}{V_{\text{total}}} \ln\left(\frac{V_j}{V_{\text{total}}}\right)$$

### 5.6 Amihud Illiquidity `L1`

**Classic Amihud**

$$\text{ILLIQ}_\tau = \frac{|r_\tau|}{V_\tau \cdot \bar{p}_\tau}$$

**Log Amihud**

$$\text{ILLIQ}^{log}_\tau = \ln(1 + \text{ILLIQ}_\tau)$$

> **VIB bar note:** VIB bars trigger on roughly similar volume imbalance magnitudes, which partially homogenizes $V_\tau$ across bars. This means Amihud on VIB bars captures more of the return variation ($|r_\tau|$) and less of the volume variation. The factor is still informative (it measures "how much did price move per unit of volume?") but may be less discriminative as a *liquidity* measure compared to calendar-time bars where volume variation is high. Keep it — let feature selection decide its importance.

---

## 6. Price-Volume Correlation Factors

### 6.1 Synchronous Correlation `L1`

**Intraday Price-Volume Correlation**

$$\rho_{PV} = \text{Corr}\left(\{r_j\},\ \{V_j\}\right)$$

**Absolute-Return-Volume Correlation**

$$\rho_{|r|V} = \text{Corr}\left(\{|r_j|\},\ \{V_j\}\right)$$

**Price-Volume Rank Correlation** (Spearman)

$$\rho^{rank}_{PV} = \text{SpearmanCorr}\left(\{r_j\},\ \{V_j\}\right)$$

**Buy-Sell Ratio Correlation with Price** (买卖比例相关性 — from Guotai Junan)

Define buy-sell ratio per bar $j$:

$$\text{BSR}_j = \frac{\sum_{k \in j, d_k=+1} q_k}{\sum_{k \in j, d_k=-1} q_k}$$

$$\rho_{BSR,P} = \text{Corr}\left(\{\text{BSR}_j\},\ \{r_j\}\right)$$

> Positive: price rises when buying pressure increases (momentum regime). Negative: divergence (potential reversal signal).

---

### 6.2 Lead-Lag Correlation `L1`

**Price-Leading-Volume**

$$\rho_{P \to V}(l) = \text{Corr}\left(\{r_j\},\ \{V_{j+l}\}\right)$$

**Volume-Leading-Price**

$$\rho_{V \to P}(l) = \text{Corr}\left(\{V_j\},\ \{r_{j+l}\}\right)$$

**Lead-Lag Asymmetry**

$$\text{LLA}(l) = \rho_{V \to P}(l) - \rho_{P \to V}(l)$$

---

## 7. Order Book Event Factors

### 7.0 Cancel Inference from Consecutive Snapshots

> **Problem:** Crypto exchanges rarely expose explicit cancel messages. We must infer cancels by comparing consecutive LOB snapshots and subtracting known fills from the trade stream.

**Setup:** We have two consecutive snapshots at times $t_{n-1}$ and $t_n$. Let $\mathcal{P}^s$ denote the set of all price levels observed on side $s \in \{a, b\}$ across both snapshots. For any price $p$ not present in a snapshot, define $Q^s_p(t) = 0$.

From the trade stream, we observe the total filled volume at each price $p$ on side $s$ during the interval $(t_{n-1}, t_n]$:

$$F^s_p(n) = \sum_{\substack{k \in (t_{n-1}, t_n] \\ p_k = p}} q_k \cdot \mathbf{1}\left[\text{trade consumes side } s\right]$$

> For taker buys ($d_k = +1$): the ask side is consumed, so $F^a_p$ increments. For taker sells ($d_k = -1$): the bid side is consumed, so $F^b_p$ increments.

**Step 1: Net quantity change at each price level**

$$\Delta Q^s_p(n) = Q^s_p(t_n) - Q^s_p(t_{n-1})$$

**Step 2: Inferred cancel volume** (quantity that disappeared but was NOT filled)

$$C^s_p(n) = \max\left(-\Delta Q^s_p(n) - F^s_p(n),\  0\right)$$

> If $C^s_p > 0$: some resting quantity vanished without being traded → cancel.

**Step 3: Inferred new order volume** (quantity that appeared fresh)

$$A^s_p(n) = \max\left(\Delta Q^s_p(n) + F^s_p(n),\  0\right)$$

> If $A^s_p > 0$: net new quantity arrived at this price (accounting for fills that consumed some of the prior depth).

**Step 4: Aggregate to interval-level cancel statistics**

Over an interval $\tau$ spanning multiple snapshot transitions $\{n_1, \ldots, n_M\}$:

$$\sum_{\text{cancels}} q_c = \sum_{n \in \tau} \sum_{p \in \mathcal{P}^s} C^s_p(n)$$

$$N_{\text{cancel},\tau} = \sum_{n \in \tau} \sum_{p \in \mathcal{P}^s} \mathbf{1}\left[C^s_p(n) > 0\right]$$

$$\sum_{\text{new}} q_n = \sum_{n \in \tau} \sum_{p \in \mathcal{P}^s} A^s_p(n)$$

$$N_{\text{new},\tau} = \sum_{n \in \tau} \sum_{p \in \mathcal{P}^s} \mathbf{1}\left[A^s_p(n) > 0\right]$$

**Crypto-Specific Notes:**

1. **Binance `@depth` diff stream** provides $\Delta Q^s_p$ directly (no need to subtract snapshots), but you still need the trade stream for $F^s_p$ to separate fills from cancels.
2. **Snapshot frequency matters:** At 100ms snapshots, multiple cancels+adds at the same price within one interval collapse into a single net change — this underestimates both $C$ and $A$. Faster snapshots or diff streams reduce this aliasing.
3. **Fill attribution at best level:** When a taker buy sweeps ask levels 1–3, attribute $F^a_p$ to each consumed level using the trade price and LOB state at $t_{n-1}$. For trades that walk the book, split the fill volume proportionally across levels.
4. **Edge case — price level shift:** If $P^a_1(t_n) > P^a_1(t_{n-1})$ and the old best ask level vanishes entirely, the disappeared volume could be either filled or cancelled. Use $F^a_p$ from the trade stream to disambiguate.

---

### 7.1 Cancellation Factors `L1`

> **Data requirement:** LOB diff/delta stream (e.g. Binance `@depth` stream) or reconstructed from sequential snapshots. See **Section 7.0** for the cancel inference formulas and notation. See **Section 0** for symbol definitions ($q_c$, $q_n$, $q_f$, etc.).

#### 7.1.1 Basic Cancel Metrics

**Cancel Rate** (by count)

$$\text{CR}_\tau = \frac{N_{\text{cancel},\tau}}{N_{\text{new},\tau} + N_{\text{cancel},\tau}}$$

**Cancel Volume Ratio**

$$\text{CVR}_\tau = \frac{\sum_{\text{cancels}} q_c}{\sum_{\text{new orders}} q_n}$$

**Cancel-to-Trade Ratio**

$$\text{CTR}_\tau = \frac{N_{\text{cancel},\tau}}{N_{\text{trades},\tau}}$$

**Cancel Volume to Trade Volume Ratio**

$$\text{CVTR}_\tau = \frac{\sum_{\text{cancels}} q_c}{\sum_{\text{trades}} q_k}$$

#### 7.1.2 Side-Specific Cancel Metrics

**Bid Cancel Volume Ratio**

$$\text{CVR}^{bid}_\tau = \frac{\sum_{\text{bid cancels}} q_c}{\sum_{\text{bid new}} q_n}$$

**Ask Cancel Volume Ratio**

$$\text{CVR}^{ask}_\tau = \frac{\sum_{\text{ask cancels}} q_c}{\sum_{\text{ask new}} q_n}$$

**Asymmetric Cancel Ratio**

$$\text{ACR}_\tau = \frac{\text{CVR}^{ask}_\tau - \text{CVR}^{bid}_\tau}{\text{CVR}^{ask}_\tau + \text{CVR}^{bid}_\tau}$$

> Positive ACR: more ask-side cancellation → hidden buying intent.

#### 7.1.3 Level-Specific Cancel Metrics

**Level-$i$ Cancel Volume** (ask/bid separately)

$$\text{CV}^{a}_i(\tau) = \sum_{\text{ask cancels at level } i} q_c, \quad \text{CV}^{b}_i(\tau) = \sum_{\text{bid cancels at level } i} q_c$$

**Cancel Depth Profile** (distribution of cancels across levels)

$$\text{CDP}^a_i(\tau) = \frac{\text{CV}^a_i(\tau)}{\sum_{j=1}^{N} \text{CV}^a_j(\tau)}$$

> If cancels concentrate at best level → fleeting liquidity / potential spoofing.

**Best-Level Cancel Concentration**

$$\text{BCC}^a_\tau = \text{CDP}^a_1(\tau), \quad \text{BCC}^b_\tau = \text{CDP}^b_1(\tau)$$

#### 7.1.4 Fleeting Order Metrics

**Fleeting Order Ratio** (orders cancelled within threshold $\delta t$)

$$\text{FOR}_\tau = \frac{N_{\text{cancel within } \delta t}}{N_{\text{new},\tau}}$$

> Typical $\delta t$: 50ms–500ms for crypto.

**Fleeting Volume Ratio**

$$\text{FVR}_\tau = \frac{\sum_{\text{cancel within } \delta t} q_c}{\sum_{\text{new}} q_n}$$

**Mean Order Lifetime** (inferred from diff streams)

$$\bar{L}_\tau = \frac{1}{N_{\text{cancel}}}\sum_c (t_{\text{cancel},c} - t_{\text{place},c})$$

**Order Lifetime Distribution Skewness**

$$\text{Skew}^L_\tau = \text{Skew}\left(\{t_{\text{cancel},c} - t_{\text{place},c}\}_c\right)$$

> Right-skewed: most orders are short-lived, a few persist → typical HFT signature.

#### 7.1.5 Pull-to-Fill Metrics

**Pull-to-Fill Ratio** (fraction of placed volume that is cancelled vs. filled)

$$\text{PFR}_\tau = \frac{\sum_{\text{cancels}} q_c}{\sum_{\text{cancels}} q_c + \sum_{\text{fills}} q_f}$$

> High PFR → low execution commitment; potential phantom liquidity.

**Side-Specific Pull-to-Fill**

$$\text{PFR}^{bid}_\tau = \frac{\sum_{\text{bid cancels}} q_c}{\sum_{\text{bid cancels}} q_c + \sum_{\text{bid fills}} q_f}$$

$$\text{PFR}^{ask}_\tau = \frac{\sum_{\text{ask cancels}} q_c}{\sum_{\text{ask cancels}} q_c + \sum_{\text{ask fills}} q_f}$$

**Pull-to-Fill Asymmetry**

$$\text{PFRA}_\tau = \text{PFR}^{ask}_\tau - \text{PFR}^{bid}_\tau$$

#### 7.1.6 Cancel-Price Interaction

**Pre-Movement Cancel Intensity** (cancels in the $\delta t$ window before a price move)

$$\text{PMCI}(t_{\text{move}}) = \frac{N_{\text{cancel}}([t_{\text{move}}-\delta t,\ t_{\text{move}}])}{\delta t}$$

**Cancel Momentum** (change in cancel rate over consecutive intervals)

$$\dot{\text{CR}}_\tau = \text{CR}_\tau - \text{CR}_{\tau-1}$$

---

### 7.2 Event Clustering / Arrival `L1`

**Trade Arrival Rate**

$$\lambda_{\text{trade}}(\tau) = \frac{N_{\text{trades},\tau}}{|\tau|}$$

**Inter-Arrival Time Stats**

$$\delta_k = t_{k+1} - t_k, \quad \bar{\delta} = \text{mean}(\delta_k), \quad \sigma_\delta = \text{std}(\delta_k)$$

**Burstiness** (Goh & Barabási)

$$B = \frac{\sigma_\delta - \bar{\delta}}{\sigma_\delta + \bar{\delta}}$$

**Trade Clustering Coefficient** (fraction below threshold $\epsilon$)

$$\text{TCC} = \frac{1}{n-1}\sum_{k} \mathbf{1}[\delta_k < \epsilon]$$

---

### 7.3 Order Flow Autocorrelation `L1`

**Trade Sign Autocorrelation (lag $l$)**

$$\rho^{d}_l = \text{Corr}\left(d_k,\ d_{k-l}\right)$$

**Signed Volume Autocorrelation**

$$\rho^{dq}_l = \text{Corr}\left(d_k q_k,\ d_{k-l} q_{k-l}\right)$$

**Hurst Exponent of Signed Flow**

Estimated via R/S analysis or DFA on $\{d_k q_k\}$:

$H > 0.5$: persistent; $H < 0.5$: anti-persistent.

---

### 7.4 Abnormal Orders & Wall Detection `L0 / L1`

> Source: Zhihu 冷门高频因子 (异常挂单). Adapted for crypto: large resting orders far from mid-price that signal support/resistance or spoofing.

**Size Outlier Score** (per snapshot level) `L0`

$$\text{SOS}_i(t) = \frac{Q^{\text{side}}_i(t) - \text{median}(Q^{\text{side}}_i)_W}{\text{MAD}(Q^{\text{side}}_i)_W}$$

**Wall Detection** (far-level volume concentration) `L0`

$$\text{Wall}^s(t) = \frac{\max_{i \in \{k_{\text{far}}, \ldots, N\}} Q^s_i(t)}{\frac{1}{N}\sum_{i=1}^{N} Q^s_i(t)}$$

where $k_{\text{far}}$ defines the "far" zone (e.g. levels beyond top 3). $\text{Wall} \gg 1$ indicates a large resting order far from the best quote.

**Far-Level Volume Ratio**

$$\text{FLVR}^s(t) = \frac{\sum_{i=k_{\text{far}}}^{N} Q^s_i(t)}{\sum_{i=1}^{N} Q^s_i(t)}$$

> High FLVR: most resting volume is far from best quote (defensive/support-building posture).

**Spoofing Indicator** `L1`

$$\text{Spoof}(t) = \mathbf{1}\left[\text{SOS}_1(t) > 3 \text{ and order cancels within } \Delta t\right]$$

**Iceberg Detection Proxy** (repeated fills at same price with depth replenishment) `L1`

$$\text{ICE}_\tau = \frac{N_{\text{fills at same price with depth refill within } \tau}}{N_{\text{total fills}}}$$

---

## 8. Informed Trading / Smart Money Factors

### 8.1 Smart Money Flow `L0i / L1`

> ~~Volume-Weighted Signed Flow (SMF)~~ removed in v4 — for BTC, price varies <0.1% within a bar, so $\text{SMF} = \sum d_k p_k q_k \approx \bar{p} \cdot \sum d_k q_k = \bar{p} \cdot \Delta_b$. The dollar weighting adds no information. Use $\Delta_b$ (§10.4) or $\Delta^{frac}_b$ instead.

> **Normalized Smart Money Flow (NSMF):** The per-bar value is $\Delta^{frac}_b$ (§10.4). Rolling NSMF over $W$ bars is a standard L1 aggregation — see Doc 2 v2 for parameterization.

**VPIN — Volume-Synchronized Probability of Informed Trading** (Easley, López de Prado & O'Hara, 2012)

Partition total volume into equal-sized buckets of size $\bar{V}$. For bucket $n$, classify buy vs. sell volume:

**Variant A — Direct taker labels (crypto):** When the exchange provides taker-side flags ($d_k$):

$$V^{buy}_n = \sum_{k \in \text{bucket}_n} q_k \cdot \mathbf{1}[d_k = +1], \quad V^{sell}_n = \sum_{k \in \text{bucket}_n} q_k \cdot \mathbf{1}[d_k = -1]$$

**Variant B — Bulk Volume Classification / BVC (original):** When taker labels are unavailable (equities, some venues). For each sub-bar $j$ within bucket $n$ with volume $V_j$ and price change $\delta_j = P^{close}_j - P^{open}_j$:

$$V^{buy}_j = V_j \cdot \Phi\left(\frac{\delta_j}{\sigma_{\delta}}\right), \quad V^{sell}_j = V_j - V^{buy}_j$$

where $\Phi(\cdot)$ is the CDF of the standard normal distribution, and $\sigma_\delta$ is the standard deviation of price changes estimated over a trailing window. Then aggregate across sub-bars within the bucket:

$$V^{buy}_n = \sum_{j \in \text{bucket}_n} V^{buy}_j, \quad V^{sell}_n = \sum_{j \in \text{bucket}_n} V^{sell}_j$$

> **Note:** Variant A is preferred for crypto since taker labels are exact. Variant B is the original formulation and is necessary for venues without trade-side labeling. We already define BVC in Section 8.3 (Trade Direction Inference). The $\Phi()$ function maps each bar's normalized price change to a probability that the volume was buyer-initiated.

> **VIB bar redundancy note:** VIB bars are triggered by cumulative *volume imbalance* — conceptually similar to what VPIN measures (the fraction of informed/directional volume). The VIB bar's own `imbalance` ($\theta_b$) field already encodes the signed volume imbalance that triggered the bar, and the bar metadata factor $|\theta_b| / V_b$ (§11) is a per-bar normalized imbalance analogous to single-bucket VPIN. Despite this overlap, **keep VPIN** because: (1) VPIN operates on equal-volume buckets which may not align with VIB bar boundaries, providing a complementary view; (2) rolling VPIN over $N_b$ buckets captures a different time horizon than the VIB trigger threshold; (3) research on dollar-volume bars shows VPIN retains strong out-of-sample predictive power (Easley et al., O'Hara). Let feature selection (Doc 4) resolve any redundancy.

**VPIN per bucket:**

$$\text{VPIN}_n = \frac{|V^{buy}_n - V^{sell}_n|}{\bar{V}}$$

**Rolling VPIN** over $N_b$ buckets:

$$\overline{\text{VPIN}} = \frac{1}{N_b}\sum_{n=1}^{N_b} \text{VPIN}_n$$

---

### 8.2 Large Order Detection `L1`

**Large Trade Ratio** (dollar-value threshold $\theta$)

$$\text{LTR}_\tau = \frac{\sum_{k \in \tau} p_k q_k \cdot \mathbf{1}[p_k q_k > \theta]}{\sum_{k \in \tau} p_k q_k}$$

**Large Net Buy** (signed large trades)

$$\text{LNB}_\tau = \sum_{k \in \tau} d_k \cdot p_k q_k \cdot \mathbf{1}[p_k q_k > \theta]$$

**Whale Detection Score**

$$\text{WDS}_k = \frac{p_k q_k - \text{median}(pq)_W}{\text{MAD}(pq)_W}$$

Flag if $\text{WDS} > 5$.

---

### 8.3 Trade Direction Inference

> In crypto with taker-side labels, $d_k$ is directly observed. Fallbacks for venues without it:

**Lee-Ready Rule**

$$d_k^{LR} = \begin{cases} +1 & \text{if } p_k > M(t_k) \\ -1 & \text{if } p_k < M(t_k) \\ d_{k-1}^{LR} & \text{if } p_k = M(t_k) \end{cases}$$

**Bulk Volume Classification (BVC)**

$$d_k^{BVC} = 2 \cdot \Phi\left(\frac{p_k - \bar{p}_\tau}{\sigma_{p,\tau}}\right) - 1$$

---

## 9. Transaction-Based Features

> Factors derived purely from the trade/tick tape, independent of orderbook state. These capture execution patterns, trade-size distributions, and timing microstructure.

### 9.1 Trade Size Distribution `L1`

**Mean Trade Size**

$$\bar{q}_\tau = \frac{1}{n_\tau}\sum_{k \in \tau} q_k$$

**Mean Trade Notional**

$$\overline{pq}_\tau = \frac{1}{n_\tau}\sum_{k \in \tau} p_k q_k$$

**Trade Size Variance**

$$\text{Var}^q_\tau = \frac{1}{n_\tau}\sum_{k \in \tau} (q_k - \bar{q}_\tau)^2$$

**Trade Size Skewness**

$$\text{Skew}^q_\tau = \frac{\frac{1}{n_\tau}\sum_{k}(q_k - \bar{q})^3}{(\sqrt{\text{Var}^q})^3}$$

**Trade Size Kurtosis**

$$\text{Kurt}^q_\tau = \frac{\frac{1}{n_\tau}\sum_{k}(q_k - \bar{q})^4}{(\text{Var}^q)^2}$$

> Raw kurtosis (same convention as §4.1). High kurtosis → heavy tails → presence of occasional very large trades.

**Log Trade Size Entropy** (diversity of trade sizes)

Discretize trade sizes into $B$ bins:

$$H_q = -\sum_{b=1}^{B} f_b \ln(f_b)$$

where $f_b$ = fraction of trades in bin $b$. Low entropy → homogeneous (potential algo-driven).

**Small/Large Trade Volume Decomposition**

$$\text{SmallVR}_\tau = \frac{\sum_{k: p_k q_k \leq \theta_S} q_k}{V_\tau}, \quad \text{LargeVR}_\tau = \frac{\sum_{k: p_k q_k > \theta_L} q_k}{V_\tau}$$

---

### 9.2 Trade Arrival Patterns `L0i / L1`

**Trade Count per Bar** (intra-bar)

$$n_b = n^{tick}_b = |\mathcal{T}_b|$$

> With VIB bars, this is a direct metadata field from Doc 0. It captures how many individual fills composed the bar.

**Trade Intensity** (duration-normalized arrival rate per bar)

$$\lambda_b = \frac{n^{tick}_b}{\Delta t_b / 1000}$$

> Trades per second within the bar. Since VIB bars have variable duration, $\lambda_b$ captures whether a bar was formed by many rapid trades (high $\lambda$) or fewer slow trades (low $\lambda$). This replaces the v3 formula $\lambda_\tau = n_\tau / |\tau|$ which assumed fixed $\tau$. Rolling $\bar{\lambda}_W$ is a standard L1 aggregation — see Doc 2 v2.

**Inter-Trade Duration Mean**

$$\bar{\delta}_\tau = \frac{1}{n_\tau - 1}\sum_{k=1}^{n_\tau - 1} (t_{k+1} - t_k)$$

**Inter-Trade Duration Std**

$$\sigma^\delta_\tau = \text{std}\left(\{t_{k+1} - t_k\}\right)$$

**Inter-Trade Duration CV** (regularity measure)

$$\text{CV}^\delta_\tau = \frac{\sigma^\delta_\tau}{\bar{\delta}_\tau}$$

> CV near 0 → regular/periodic trading (algo). CV >> 1 → bursty/irregular.

**Time-Weighted Trade Rate** (TWTR)

$$\text{TWTR}_\tau = \frac{\sum_{k \in \tau} q_k \cdot \Delta t_k^{-1}}{\sum_{k \in \tau} \Delta t_k^{-1}}$$

where $\Delta t_k = t_k - t_{k-1}$. Upweights trades that happen in rapid succession.

---

### 9.3 Execution Quality / Slippage `L0 per trade / L1 aggregated`

**Realized Spread** (execution cost per trade)

$$\text{RS}_k = 2 \cdot d_k \cdot (p_k - M(t_k + \Delta t))$$

> Measures how much price reverted after the trade. Positive → informed trade. $\Delta t$ typically 1–5 seconds.

**Price Improvement** (trade price vs. same-side best quote)

$$\text{PI}_k = \begin{cases} P^a_1(t_k) - p_k & \text{if } d_k = +1 \text{ (taker buy)} \\ p_k - P^b_1(t_k) & \text{if } d_k = -1 \text{ (taker sell)} \end{cases}$$

> Positive PI → trade executed better than best quote (e.g. hidden orders, partial fills at better levels).

**Implementation Shortfall** (deviation from mid at decision time $t_0$ to execution at $t_k$)

$$\text{IS}_k = d_k \cdot \frac{p_k - M(t_0)}{M(t_0)}$$

**Walk-the-Book Depth** (how many levels a single trade consumes)

$$\text{WTB}_k = \text{max level consumed by trade } k$$

$$\overline{\text{WTB}}_\tau = \frac{1}{n_\tau}\sum_{k \in \tau} \text{WTB}_k$$

---

### 9.4 Trade Price Clustering `L1`

**Round-Size Ratio** (fraction of trades at round quantities, e.g. integer lots)

$$\text{RSR}_\tau = \frac{\sum_{k \in \tau} \mathbf{1}[q_k \in \text{round set}]}{n_\tau}$$

> High RSR → more retail/manual trading. Low RSR → algo-dominated.

**Price Clustering Index** (fraction of trades at round prices)

$$\text{PCI}_\tau = \frac{\sum_{k \in \tau} \mathbf{1}[p_k \bmod \Delta_{\text{round}} = 0]}{n_\tau}$$

where $\Delta_{\text{round}}$ is a round increment (e.g. \$1, \$10, \$100 depending on asset price).

**Unique Price Ratio** (diversity of execution prices)

$$\text{UPR}_\tau = \frac{|\{p_k : k \in \tau\}|}{n_\tau}$$

Low UPR → many trades at same price → concentrated execution.

---

### 9.5 Signed Trade Sequence Patterns `L1`

**Run Length** (consecutive trades in the same direction)

$$\text{RL}_j = \text{length of } j\text{-th run of consecutive same-sign } d_k$$

**Mean Run Length**

$$\overline{\text{RL}}_\tau = \frac{1}{J}\sum_{j=1}^{J} \text{RL}_j$$

> Long runs → order-splitting / momentum. Short runs → balanced flow.

**Run Count Ratio** (runs vs. total trades; Wald-Wolfowitz type)

$$\text{RCR}_\tau = \frac{J}{n_\tau}$$

$\text{RCR} \approx 0.5$ → random; $\text{RCR} \ll 0.5$ → persistent (trending); $\text{RCR} \gg 0.5$ → alternating (mean-reverting).

**Consecutive Direction Probability**

$$P_{\text{same}} = \frac{1}{n-1}\sum_{k=2}^{n} \mathbf{1}[d_k = d_{k-1}]$$

> **Cross-ref:** Analogous to OFI Sign Persistence $\text{SP}$ (Section 2.3) but applied to raw trade signs $d_k$ rather than aggregated OFI signs.

**Direction Transition Matrix**

$$P_{ij} = P(d_k = j \mid d_{k-1} = i), \quad i,j \in \{-1, +1\}$$

Features: $P_{++}, P_{--}, P_{+-}, P_{-+}$.

---

### 9.6 Stambaugh-Style Return Predictors from Trades `L1`

> ~~Volume-Weighted Price Relative to Close (VWPR)~~ removed in v4 — equivalent to $-\text{VCD}_b$ (§11.5 VWAP-Close Deviation). $\text{VWPR} = \text{VWAP}/\text{close} - 1 \approx -(\text{close} - \text{VWAP})/\text{VWAP}$. Use VCD in §11.5.

**Last-Trade vs. Mid Deviation**

$$\text{LTMD}_\tau = \frac{p_{\text{last},\tau} - M(t_{\text{end}})}{M(t_{\text{end}})}$$

> Captures closing pressure direction.

---

## 10. Footprint Features (Intra-Bar Order Flow by Price Level)

Footprint features decompose each bar's trade activity by price level, revealing the internal order flow structure that OHLCV summarizes away. These are **L0i** (intra-bar) features computed from $\mathcal{T}_b$ — the raw trades within bar $b$.

> **Data requirement:** Each trade needs price $p_k$, quantity $q_k$, and direction $d_k$ (taker buy/sell). Hyperliquid provides $d_k$ directly via the `side` field — no tick rule approximation needed.

### 10.1 Price Level Aggregation `L0i`

Discretize the bar's price range $[L_b, H_b]$ into tick-sized levels. For each price level $\ell$:

$$V^{buy}_\ell = \sum_{k \in \mathcal{T}_b : p_k = \ell,\, d_k = +1} q_k, \quad V^{sell}_\ell = \sum_{k \in \mathcal{T}_b : p_k = \ell,\, d_k = -1} q_k$$

$$\delta_\ell = V^{buy}_\ell - V^{sell}_\ell \quad \text{(delta at level } \ell\text{)}$$

$$V^{total}_\ell = V^{buy}_\ell + V^{sell}_\ell$$

This produces a **delta profile** — a vector of $\delta_\ell$ values across all active price levels within the bar.

### 10.2 Point of Control (POC) `L0i`

The price level with the highest total volume within the bar:

$$\ell^{POC}_b = \arg\max_\ell\, V^{total}_\ell$$

**POC position relative to bar range:**

$$\text{POC}_{rel} = \frac{\ell^{POC}_b - L_b}{H_b - L_b}$$

> $\text{POC}_{rel}$ near 0 → most volume at the low (accumulation zone). Near 1 → most volume at the high (distribution zone). Near 0.5 → balanced.

**POC delta** (buy-sell imbalance at the most traded price):

$$\delta^{POC}_b = \delta_{\ell^{POC}_b}$$

### 10.3 Value Area `L0i`

The contiguous price range containing 70% of the bar's total volume, centered on the POC:

1. Start with $\ell^{POC}_b$
2. Expand upward or downward one level at a time, choosing the direction with more volume
3. Stop when accumulated volume $\geq 0.7 \cdot V_b$

**Value Area High / Low:**

$$\text{VAH}_b, \quad \text{VAL}_b$$

**Value Area Width** (normalized by bar range):

$$\text{VAW}_b = \frac{\text{VAH}_b - \text{VAL}_b}{H_b - L_b}$$

> Narrow VAW → volume concentrated at a few levels (consensus price). Wide VAW → volume dispersed (price discovery).

**Close relative to Value Area:**

$$\text{CVA}_b = \begin{cases} +1 & \text{if } C_b > \text{VAH}_b \quad \text{(close above VA — bullish)} \\ -1 & \text{if } C_b < \text{VAL}_b \quad \text{(close below VA — bearish)} \\ 0 & \text{if } \text{VAL}_b \leq C_b \leq \text{VAH}_b \quad \text{(close inside VA)} \end{cases}$$

### 10.4 Footprint Delta Features `L0i`

**Total bar delta** (net buy-sell imbalance across all levels):

$$\Delta_b = \sum_\ell \delta_\ell = V^{buy}_b - V^{sell}_b$$

> This is equivalent to the bar's overall signed flow. Closely related to the VIB trigger imbalance $\theta_b$ (§11) but computed differently: $\Delta_b$ counts all volume by direction, while $\theta_b$ uses the tick-rule-weighted cumulative threshold.

**Delta as fraction of volume:**

$$\Delta^{frac}_b = \frac{\Delta_b}{V_b}$$

**Upper half delta vs. lower half delta:**

$$\Delta^{upper}_b = \sum_{\ell > M_b} \delta_\ell, \quad \Delta^{lower}_b = \sum_{\ell \leq M_b} \delta_\ell$$

where $M_b = (H_b + L_b) / 2$ is the bar's midpoint.

**Delta divergence** (spatial imbalance within the bar):

$$\text{DDiv}_b = \Delta^{upper}_b - \Delta^{lower}_b$$

> Positive: buyers dominate the upper half (aggressive buying into resistance). Negative: sellers dominate the lower half (aggressive selling into support).

### 10.5 Imbalance Stacking `L0i`

An **imbalance** at level $\ell$ exists when one side dominates by a threshold ratio:

$$\text{Imb}_\ell = \begin{cases} +1 & \text{if } V^{buy}_\ell > R \cdot V^{sell}_\ell \text{ and } V^{sell}_\ell > 0 \\ -1 & \text{if } V^{sell}_\ell > R \cdot V^{buy}_\ell \text{ and } V^{buy}_\ell > 0 \\ 0 & \text{otherwise} \end{cases}$$

where $R$ is the imbalance ratio threshold (typically $R = 3$).

**Stacked imbalances:** Count consecutive price levels with the same imbalance sign:

$$\text{BuyStack}_b = \max \text{ run length of consecutive } \text{Imb}_\ell = +1$$

$$\text{SellStack}_b = \max \text{ run length of consecutive } \text{Imb}_\ell = -1$$

> Stacked buy imbalances (3+ consecutive levels where buyers dominate 3:1) often signal aggressive sweeping of ask-side liquidity. Stacked sell imbalances signal aggressive selling through bid-side levels.

> ~~Total imbalance count ($n^{imb,buy}_b$, $n^{imb,sell}_b$)~~ removed — the stack run lengths are strictly more informative (they capture spatial structure, not just count). Total count is recoverable from the stacking analysis if needed.

### 10.6 Unfinished Auction `L0i`

An **unfinished auction** occurs when the bar's high or low has zero volume on one side:

$$\text{UnfHigh}_b = \mathbf{1}[V^{sell}_{H_b} = 0] \quad \text{(no selling at the high → potential continuation up)}$$

$$\text{UnfLow}_b = \mathbf{1}[V^{buy}_{L_b} = 0] \quad \text{(no buying at the low → potential continuation down)}$$

> These signal that the auction at the extreme price was one-sided — the market ran out of one side's liquidity without any opposing flow, suggesting the move may continue.

> **Rolling aggregations** of footprint features (rolling mean of $\Delta^{frac}$, POC drift, average stack lengths) are L1 operations defined in Doc 2 v2. This document defines only the per-bar (L0i) atomic formulas.

> **Factor count for §10:** ~14 L0i per-bar factors

---

## 11. VIB Bar Metadata Factors

VIB bars carry metadata from their construction process (Doc 0) that are themselves informative features. These are available directly from the bar DataFrame with no additional computation.

### 11.1 Bar Duration `L0i`

$$\Delta t_b \quad \text{(milliseconds)}$$

**Log duration:**

$$\ln(\Delta t_b)$$

> Short-duration bars = high market activity / fast information arrival. Long-duration bars = quiet periods. Duration is a proxy for market activity intensity — the inverse of "time between events" in event-time sampling. The ratio-to-rolling-mean variant ($\Delta t_b / \overline{\Delta t}_W$) is an L2 transform (T7) — defined in Doc 2 v2.

### 11.2 Volume Imbalance `L0i`

$$\theta_b \quad \text{(signed imbalance that triggered the bar)}$$

**Normalized imbalance:**

$$\theta^{norm}_b = \frac{|\theta_b|}{V_b}$$

> How much of the bar's total volume was directional. High $\theta^{norm}$ → the bar was triggered by a strong one-sided flow. Low $\theta^{norm}$ → the bar accumulated volume from both sides before triggering. Note: $\text{sign}(\theta_b) \approx \text{sign}(\Delta_b)$ (§10.4) — do not create a separate sign feature; use $\theta_b$ directly (carries both sign and magnitude).

### 11.3 Tick Count & Fragmentation `L0i`

> $n^{tick}_b$ and $\lambda_b$ are defined in §9.2 (Trade Arrival Patterns). Not repeated here.

**Average trade size:**

$$\bar{q}_b = \frac{V_b}{n^{tick}_b}$$

### 11.4 Order Fragmentation `L0i`

$$n^{ord}_b \quad \text{(number of distinct orders / tx\_hash within the bar)}$$

**Ticks per order:**

$$\text{TPO}_b = \frac{n^{tick}_b}{n^{ord}_b}$$

> TPO > 1 means orders are being filled in multiple partial fills (large orders walking the book). TPO ≈ 1 means each order produces one fill (small or limit orders).

### 11.5 VWAP-Close Deviation `L0i`

$$\text{VCD}_b = \frac{C_b - \text{VWAP}_b}{\text{VWAP}_b}$$

> Positive: price moved above the volume-weighted average by bar close (late buying). Negative: price fell below VWAP by close (late selling). This captures intra-bar price drift direction relative to where most volume transacted.

### 11.6 Address Diversity (Hyperliquid-specific) `L0i`

$$n^{addr}_b \quad \text{(number of unique taker addresses)}$$

**Trades per address:**

$$\text{TPA}_b = \frac{n^{tick}_b}{n^{addr}_b}$$

> TPA high → few addresses doing many trades (concentrated flow, possibly a single whale). TPA ≈ 1 → each trade from a different address (dispersed participation).

**Address concentration ratio:**

$$\text{ACR}_b = \frac{\max_a V^a_b}{V_b}$$

where $V^a_b$ is the volume from the most active taker address in bar $b$. High ACR → one address dominates the bar.

> **Factor count for §11:** ~11 factors

---

## 12. Hyperliquid-Specific Factors

These factors exploit data fields unique to Hyperliquid that are not available on most centralized exchanges.

### 12.1 Order Count per LOB Level `L0`

Hyperliquid provides the number of distinct orders $n_i$ at each price level. From the LOB snapshot at bar open:

**Order count imbalance:**

$$\text{OCI}_i = \frac{n^b_i - n^a_i}{n^b_i + n^a_i}$$

where $n^b_i$ and $n^a_i$ are the order counts at level $i$ on bid and ask sides.

**Average order size per level:**

$$\bar{q}^{ord}_{a,i} = \frac{Q^a_i}{n^a_i}, \quad \bar{q}^{ord}_{b,i} = \frac{Q^b_i}{n^b_i}$$

> Large $\bar{q}^{ord}$ at a level means fewer, larger orders (potential whale / institutional). Small $\bar{q}^{ord}$ means many small orders (fragmented retail or algo).

**Fragmentation index** (across top $L$ levels):

$$\text{FI}^{side} = \frac{1}{L}\sum_{i=1}^{L} n^{side}_i$$

> High fragmentation → many orders at each level (deep, distributed liquidity). Low fragmentation → few large orders (concentrated, fragile liquidity).

### 12.2 Order Size Dispersion per Level `L0`

**Coefficient of variation of order sizes** (if individual order sizes are observable from the snapshot; otherwise use avg as proxy):

$$\text{OSD}_i = \frac{\sigma(q^{ord}_i)}{\bar{q}^{ord}_i}$$

> High OSD → heterogeneous order sizes (mix of retail and institutional). Low OSD → homogeneous (single algo or uniform liquidity provision).

### 12.3 Address Persistence Across Bars `L1`

Track taker addresses across consecutive bars:

**Address overlap ratio:**

$$\text{AOR}_b = \frac{|\mathcal{A}_b \cap \mathcal{A}_{b-1}|}{|\mathcal{A}_b \cup \mathcal{A}_{b-1}|}$$

where $\mathcal{A}_b$ is the set of unique taker addresses in bar $b$.

> High AOR → the same addresses are trading in consecutive bars (persistent flow, possibly informed). Low AOR → different participants each bar (noise / diverse liquidity).

**Rolling unique address count:**

$$n^{addr}_W = |\bigcup_{b=1}^{W} \mathcal{A}_b|$$

> Over a window of $W$ bars, how many distinct addresses participated? High count relative to total trade count → dispersed market. Low count → concentrated.

### 12.4 Liquidation Features `L1`

From Doc 0's liquidation flagging (address-based detection):

**Liquidation volume fraction:**

$$\text{LiqFrac}_W = \frac{\sum_{b=1}^{W} V^{liq}_b}{\sum_{b=1}^{W} V_b}$$

where $V^{liq}_b$ is the volume flagged as liquidation within bar $b$.

**Liquidation direction imbalance:**

$$\text{LiqImb}_W = \frac{V^{liq,buy}_W - V^{liq,sell}_W}{V^{liq,buy}_W + V^{liq,sell}_W}$$

> Liquidation-heavy bars have distinct price dynamics (forced selling/buying, mean-reversion after cascade). The direction tells you which side is being liquidated.

> **Factor count for §12:** ~12 factors

---

## Appendix A: Recommended Windows for VIB Bars

> **Note:** The authoritative window specification and parameterization lives in Doc 2 v2 (Feature Engineering). This table is a quick reference only.

| Window $W$ (bars) | Approx. time (~1 bar/min) | Typical Use |
|---|---|---|
| Intra-bar | Variable per bar | L0i features: footprint, delta profile, POC, VWAP-close deviation |
| $W = 5$ | ~5 min | Ultra-short: LOB event factors, fleeting order detection, cancel metrics |
| $W = 20$ | ~20 min | Micro: OFI, aggressiveness, trade arrival, short-term momentum |
| $W = 60$ | ~1 hour | Standard: return moments, PV-correlation, trend strength, realized vol |
| $W = 120$ | ~2 hours | Medium: VPIN, Amihud, volume entropy, session-level aggregation |
| $W = 360$ | ~6 hours | Long: normalization windows, slow-regime features |

> **v3 → v4 mapping:** v3 "100ms–1s" → intra-bar (L0i). v3 "1s–10s" → $W=5$. v3 "1min–5min" → $W=20$. v3 "15min–1h" → $W=60$. v3 "4h–24h" → $W=120$–$360$. Exact calibration depends on the actual VIB bar rate. Recommended: compute `mean_bar_duration_sec` from historical data and adjust $W$ targets accordingly.

---

## Appendix B: Feature Engineering Tips

1. **Multi-scale:** Compute each L1 factor at 2–3 bar-count windows (e.g., $W = 20, 60, 120$) for hierarchical features.
2. **Cross-level:** For LOB factors, compute at levels 1, 3, 5, 10 separately.
3. **Normalization:** Z-score factors within rolling bar-count windows before feeding to ML models.
4. **Interaction features:** Pressure × Volatility, OFI × Spread, Depth × Aggressiveness, Footprint Delta × Duration.
5. **Regime conditioning:** Some factors flip sign in trending vs. mean-reverting regimes. Bar duration ($\Delta t_b$) is a useful regime proxy.
6. **L2 transforms to apply uniformly:** rolling mean, rolling std, z-score, first difference, lag-1, ratio-to-rolling-median. These are defined in Doc 2.
7. **VIB-specific:** Bar metadata (§11) can be used both as standalone features and as conditioning variables for other factors (e.g., OFI conditioned on short vs. long duration bars).
8. **Footprint-LOB interaction:** Combine footprint delta direction (§10) with LOB depth at corresponding levels — does the delta align with where liquidity sits?

---

## Appendix C: Factor-Category Quick Reference

| # | Category | Sub-count | Data Source | Comp. Level |
|---|----------|-----------|-------------|-------------|
| 1 | Order Book State & Pressure | 31 | LOB snapshot at bar open | L0 |
| 2 | Order Flow / Imbalance (OFI) | 17 | LOB snapshot diffs + trades | L0/L1 |
| 3 | Price Microstructure / Momentum-Reversal | 12 | LOB snapshot + trades | L1 |
| 4 | Volatility & Higher-Order Moments | 14 | Trades / bar OHLCV | L1 |
| 5 | Liquidity (Bervas) | 9 | LOB snapshot + trades | L0/L1 |
| 6 | Price-Volume Correlation | 6 | Trades / bar | L1 |
| 7 | Order Book Events / Cancellation | 27 | LOB diffs / delta stream | L0/L1 |
| 8 | Informed Trading / Smart Money | 8 | Trades | L1 |
| 9 | Transaction-Based Features | 19 | Trades only | L0i/L1 |
| 10 | Footprint Features | 14 | Intra-bar trades by price level | L0i |
| 11 | VIB Bar Metadata | 10 | Bar construction metadata | L0i |
| 12 | Hyperliquid-Specific | 12 | Order count, addresses, liquidations | L0/L1 |
| | **Total** | **~180** | | |

---

## Appendix D: Sources

| Source | Content Used |
|--------|-------------|
| Guotai Junan (国君金工) Level-2 snapshot report | Spread, OI, slope, PV-correlation, PTA, MPRP, BSR correlation |
| Zhihu 冷门高频因子 (p/138926388) | Order aggressiveness, LOB shape (convexity/hump/multi-peak), cancellation, event clustering, resilience, abnormal orders, tick-by-tick factors |
| GitHub yudai-il/High-Frequency | Bervas liquidity framework, OI statistical properties, pressure depth-width decomposition |
| Cont, Kukanov & Stoikov (2014) | OFI original formulation ($e_n$, $\Delta q^B_n$, $\Delta q^A_n$), price impact regression |
| CICC high-frequency factor handbook | Momentum/reversal, volatility decompositions, higher-order features |
| López de Prado (2018) *AFML* Ch. 2, 18, 19 | Information bars, entropy features, microstructural features (Kyle/Amihud/Hasbrouck λ, VPIN) |
| mlfinlab `MicrostructuralFeaturesGenerator` | Inter-bar and intra-bar feature computation pattern for information bars |
| FinMLKit (quantscious/finmlkit) | Footprint features, volume profiles, intra-bar order flow decomposition on information bars |
| Easley, López de Prado & O'Hara (2012) | VPIN — volume-synchronized probability of informed trading |
