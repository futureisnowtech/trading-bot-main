# Practitioner Research for a 1‑Minute Crypto Algo on Coinbase Advanced Trade

## Project constraints and feasibility realities

### Confirmed system facts in this project
You are running a fully automated, no‑intervention spot crypto system on entity["company","Coinbase","crypto exchange us"] Advanced Trade with (i) a ~$500 account, (ii) ~$100 per trade position sizing, (iii) 1‑minute candles scanned every 60 seconds, (iv) 8–12 liquid-ish pairs (BTC/ETH/SOL/etc.), (v) **no margin, no shorts, no options**, and (vi) a stated **taker fee of 0.6% per side** (≈1.2% round trip). You also already run multi‑indicator entry logic (3‑variant MACD consensus, Williams %R extremes, momentum breakout) and an “AI agent debate” layer. (All of the above is confirmed by your message.)

Coinbase’s own help documentation describes Advanced Trade fees as a maker/taker schedule with **≤0.4% maker and ≤0.6% taker**, tiered by trailing 30‑day volume. citeturn29search2turn29search7

### Design implications that follow directly from the fee floor
A **1.2% round‑trip taker fee is an extremely high hurdle** for 1‑minute horizons. In most liquid crypto pairs, the *median* 1‑minute absolute move is usually far smaller than 1% (and the move you capture is smaller than the candle’s high‑low because of timing, spread, and slippage). This implies:

* If you trade **frequently** at market (taker), **many statistically real microstructure edges will be economically untradable** because their expected “markout” is measured in **basis points**, not >100 bps. This is not theoretical: empirical microstructure work on crypto market making explicitly frames the taker fee as a “profitability threshold” that is hard to overcome even when the signal has pre‑fee edge. citeturn18view0turn19view1turn23view0  
* Your system can still use 1‑minute candles for *entry timing*, but the **holding period must often be longer than 1–5 minutes** (think tens of minutes to a few hours) *or* you must bias toward **maker executions** (limit orders) whenever possible to bring the hurdle down. The Coinbase fee schedule itself supports this (maker fee can be lower than taker, depending on tier). citeturn29search2turn29search7  

### ASSUMPTION and open variables you must pin down (because they change the “best strategy” ranking)
ASSUMPTION: your **effective all‑in trading cost** is exactly 1.2% round trip. In production it will be:

* **fee + spread + slippage + adverse selection**
* and will vary by pair, time of day, order type (market vs limit), and volatility regime.

This report therefore treats **1.2%** as a baseline *hard floor*, but flags whenever a strategy is only viable if you (a) get maker fees, (b) trade less, or (c) target larger multi‑ATR moves.

## Intraday strategies that have evidence of edge on high‑frequency crypto and can be adapted to 1‑minute execution

This section focuses on strategies that (i) have published evidence on crypto intraday predictability or crypto microstructure and (ii) can be implemented with the kind of market data Coinbase retail endpoints provide.

### Order‑book imbalance and microprice drift as a directional edge

**Name**: Top‑of‑book imbalance → microprice‑biased direction (“microprice signal”)  
**Origin / source**: entity["people","Sasha Stoikov","quant trader"]’s microprice formulation (slides / practitioner exposition) citeturn8search1turn8search4, and the broader order‑flow imbalance literature (price changes driven by imbalance at best bid/ask) from entity["people","Rama Cont","quant researcher"] and coauthors entity["people","Arseniy Kukanov","quant researcher"] (order flow imbalance, linear price impact relation). citeturn8search0turn8search9turn8search24  

**Core formulas (exact)**  

Let best bid price/size be \((P^b_t, Q^b_t)\) and best ask price/size be \((P^a_t, Q^a_t)\).

* **Midprice**:  
\[
m_t=\frac{P^a_t+P^b_t}{2}
\]
* **Normalized order book imbalance (OBI)** (top of book):  
\[
\text{OBI}_t=\frac{Q^b_t-Q^a_t}{Q^b_t+Q^a_t}\in(-1,1)
\]
This exact normalized form is used in Coinbase‑based microstructure work (across multiple levels/time samples) and is explicitly defined as an “order imbalance” variable in high‑frequency Coinbase BTC data. citeturn15view0  

* **Microprice** (quantity‑weighted “fair” price proxy):  
\[
\mu_t=\frac{P^a_tQ^b_t + P^b_tQ^a_t}{Q^b_t+Q^a_t}
\]
The microprice concept is widely used as a short‑horizon predictor and is explicitly described as a quantity‑weighted function of top bid/ask prices. citeturn22view3turn23view0  

Define a *microprice premium*:
\[
\Delta^\mu_t=\frac{\mu_t-m_t}{m_t}
\]

**1‑minute crypto‑calibrated entry rules (taker version)**
You can compute these every minute from Coinbase WebSocket `ticker` best bid/ask quantities and prices. citeturn28view0

A concrete, implementable long entry rule:

1. **Liquidity sanity**: spread in bps is below a cap  
\[
s_t = 10{,}000\cdot\frac{P^a_t-P^b_t}{m_t} \le s_{\max}
\]
Typical crypto‑minute calibration: \(s_{\max}\in[3,12]\) bps for majors (you must measure this per pair).

2. **Imbalance threshold**:  
\[
\text{OBI}_t \ge 0.20 \quad\text{(mild)}\qquad\text{or}\qquad \text{OBI}_t \ge 0.35 \quad\text{(strong)}
\]
3. **Microprice confirmation**:  
\[
\Delta^\mu_t \ge \Delta_{\min}
\]
Typical calibration: \(\Delta_{\min}\in[1,5]\) bps.

4. **Trade‑flow confirmation (see TFI definition below)** over the last 60 seconds:  
\[
\text{TFI}_{t,60s} \ge 0.10
\]

**Exit rules (must be fee‑aware)**
With a 1.2% round trip taker fee, you cannot run “1–10 bps scalps.” You need exits designed for **multi‑ATR continuation**:

* **Stop**: below entry by \(k_s\cdot \text{ATR}_{14,1m}\)  
* **Target**: above entry by \(k_t\cdot \text{ATR}_{14,1m}\)  
* **Time stop**: close after \(T_{\max}\) minutes if neither hit

Fee‑aware starter values:
* \(k_s=2.0\), \(k_t=4.0\), \(T_{\max}=180\) minutes (forces you into larger realized moves, or you simply won’t clear fees)

**Why it can work in crypto specifically (microstructure reason)**
The core microstructure result is that **short‑interval price changes are strongly related to imbalance at the best bid/ask (“order flow imbalance”)** and that this relationship is often more robust than using volume alone. citeturn8search0turn8search9turn14view0  
High‑frequency crypto markets are continuous, fragmented, and heavily algorithmic; microstructure metrics (including imbalance and toxicity measures like VPIN/Roll) demonstrably have predictive power for crypto price dynamics in published crypto microstructure research. citeturn12view1turn14view3  

**Does it survive 1.2% round‑trip taker fees at $100?**
In most implementations, **not as a pure 1‑minute scalper**, because imbalance/microprice edges are typically measured in **basis points**. Evidence from a live‑experiment paper on the most liquid crypto market shows even an imbalance‑direction taker strategy can look “impressive before fees” but becomes **unprofitable after taker fees**; and that the taker fee acts as a binding profitability threshold. citeturn19view1turn18view0turn23view0  

**Best use in your setup**
Use microprice/OBI/TFI primarily as:
1) a **trade filter** (“only trade breakouts when microstructure agrees”), and/or  
2) a **maker‑entry timing tool** (enter with limit orders near bid/ask when the imbalance suggests you are less likely to be adversely selected), rather than a market‑order scalp engine.

**Python implementation notes (Coinbase‑specific)**
* Subscribe to Coinbase WebSocket `ticker` for best bid/ask and quantities (top of book) and `market_trades` for trade prints. citeturn28view0turn28view1  
* Maintain rolling windows keyed by `(product_id, timestamp)`:
  * last 60s of trades for TFI
  * last N minutes of ATR
  * last 5–15 minutes of OBI mean/variance

**Edge‑case warnings**
* **Spoofing / layering**: crypto order books can contain manipulative volume; a Coinbase‑focused spoofing study finds order‑book imbalances predict returns at minute horizons *and* shows spoofing deteriorates market quality and changes the meaning of imbalance. citeturn18view1turn17search23  
* **Adverse selection around news/forced flows**: imbalance can be “right” but you still lose via slippage when the market jumps through the book.

image_group{"layout":"carousel","aspect_ratio":"16:9","query":["order book imbalance diagram bid ask","microprice vs midprice illustration","VWAP anchored vwap chart example","crypto order flow footprint chart example"],"num_per_query":1}

### Reversal‑selection market making as a way around fee drag (maker‑leaning)

**Name**: “Reversal” classifier for maker fills at the touch (trade against imbalance only when the imbalance is *likely wrong*)  
**Origin / source**: Live‑trading experiment and modeling in entity["people","Jakob Albers","oxford statistician"] et al., *To Make, or to Take…* (arXiv 2025, experiments run 2024) citeturn18view0turn23view0turn19view1  

**Key published findings you can directly operationalize**
1. “Natural” and commonly cited strategies (including naive market making and imbalance‑following) can be **highly unprofitable** once realistic mechanics and fees are included. citeturn19view1turn18view0  
2. Maker vs taker has a structural trade‑off: maker orders get better fee/price but face fill uncertainty and adverse selection; takers always fill but have the taker fee hurdle. citeturn18view0turn23view0  
3. Their approach identifies a subset of cases (“reversals”) where a maker order posted **counter to the imbalance** combines high fill probability with positive subsequent drift, and they build a signal to detect such cases. citeturn19view1turn23view0  

**Core microstructure definitions (as used in the paper)**
They define top‑of‑book imbalance (rendering omitted in the HTML excerpt, but explicitly described as a function of top bid/ask quantities) and microprice. citeturn23view0turn22view3  
For operational use on Coinbase, you can use the explicit normalized imbalance definition from Coinbase BTC high‑frequency research (Nejat) as your OBI. citeturn15view0  

**Implementable (Coinbase retail) adaptation**
ASSUMPTION: you can’t obtain true queue position from Coinbase L2 (it is not full L3), so you cannot reproduce their exact queue‑position conditioning. You *can*, however, adapt the **idea**:

1. Only place **maker** orders when:
   * spread is tight (pair‑specific)
   * top‑of‑book “age” is high (no recent price change)
   * OBI is adverse (e.g., \(\text{OBI}\le -0.20\)) **but** short‑term trade returns are oscillatory, not trending (their “ret_autocov” intuition) citeturn20view2turn19view1  

2. Train a short‑horizon classifier (logistic regression first, because it’s interpretable) to predict whether the next \(k\) seconds/minutes are “reversal‑like” vs continuation‑like.

**Concrete feature set you can build from Coinbase endpoints**
Coinbase supports:
* `level2` (order book updates) citeturn28view0  
* `market_trades` (trade prints, batched) citeturn28view1  
* `ticker` (best bid/ask and quantities) citeturn28view0  

From these, you can compute:
* \(\text{OBI}_t\), \(\Delta^\mu_t\), spread bps  
* short‑window return autocovariances (e.g., over 15–60 seconds)  
* trade intensity (#trades per minute), signed volume, TFI  
* top‑of‑book survival time proxy (time since last best bid/ask price change)

**Exit logic**
Maker strategies need “lifecycle rules”:
* cancel if best price moves away (your order is now stale/off‑touch)
* cancel if OBI flips strongly against you after placement
* if filled, target larger post‑fill drift (in your case, you still need to clear fees—maker helps)

**Fee survival**
This family is valuable specifically because your fee schedule strongly incentivizes maker behavior (lower than taker). citeturn29search2turn29search7  
But note the paper’s core warning: naive maker strategies can still be unprofitable due to adverse selection mechanics even before considering your higher Coinbase fees. citeturn19view1turn18view0  

### Intraday momentum and reversal effects documented in crypto literature (time‑of‑day framing)

**Name**: Intraday return predictability (momentum and reversal regimes)  
**Origin / source**: entity["people","Zhuzhu Wen","finance researcher"] et al., SSRN 2022, *Intraday Return Predictability in the Cryptocurrency Markets: Momentum, Reversal, or Both* (BTC 2013–2020; also ETH/LTC/XRP). citeturn10view0  

**What is actually “usable” at 1‑minute execution**
The paper is about **intraday predictors**, not necessarily 1‑minute holding periods. The practitioner implication is to treat your 1‑minute engine as an execution layer, but use intraday predictors as **regime + direction gates**.

A concrete adaptation template:

1. Define a **session clock** (because crypto has no exchange open).  
2. Compute predictor signals on that session clock (first X minutes return, jump regime, liquidity level).  
3. Execute entries on 1‑minute bars using tighter triggers (breakout, VWAP reclaim, microstructure alignment).

**Microstructure reason it can work**
The paper attributes intraday momentum to delayed information diffusion (“late‑informed investors”) and intraday reversal to overreaction/behavioral effects that appear particularly in crypto. citeturn10view0  

**Fee survival**
This class is more promising under high fees **only if it trades infrequently** and targets large moves.

### Session‑defined “open” selection for a 24/7 market

**Name**: Intraday time‑series momentum with volume‑spike opens (crypto “pseudo‑open”)  
**Origin / source**: entity["people","Dan Shen","finance researcher"] (and coauthors), *Bitcoin Intraday Time‑Series Momentum* (paper revision 2021; uses exchange‑specific opens selected where volume spikes; closing time chosen as 5pm EST to align with CME close). citeturn25view2turn10view1  

**Key rule from the paper you can reuse**
Because Bitcoin trades 24/7 with no clear open/close, they explicitly choose the opening time of each exchange **when volume spikes**, and use a fixed close time (5pm EST). citeturn25view2  

**Crypto‑adapted “session range breakout” rules (exact)**
Define two high‑volume sessions (you can refine empirically):
* US liquidity window: 08:00–11:00 ET (your hypothesis)
* Asia window: choose a local spike window (must be measured; don’t guess)

For each session \(S\) with start time \(t_0\):

1. **Opening range** over first \(N\) minutes (typical \(N\in\{15,30,60\}\)):  
\[
H_S=\max\{high_t: t\in[t_0,t_0+N]\},\quad L_S=\min\{low_t: t\in[t_0,t_0+N]\}
\]
2. **Long breakout trigger** (long‑only constraint):  
\[
close_t \ge H_S + \delta
\]
where \(\delta\) is a noise buffer: \(\delta = c\cdot \text{ATR}_{14,1m}\) with \(c\in[0.1,0.3]\).

3. **Confirmation filters** (to reduce false breaks on 1‑minute):
   * Volume impulse: \(vol_t \ge 1.5\cdot \text{SMA}_{20}(vol)\)
   * Microstructure alignment: \(\text{OBI}_t \ge 0.20\) (or \(\Delta^\mu_t>0\))

4. **Exit**:
   * Stop: \(L_S - 1.0\cdot \text{ATR}_{14,1m}\) (or a fixed % if ATR too noisy)
   * Trail: \(1.5\cdot \text{ATR}_{14,1m}\)
   * Time stop: exit at session end or after \(T_{\max}\) minutes.

**Why it can work in crypto**
Intraday crypto studies report that trading activity/volatility/spread dynamics vary by time‑of‑day and can align with US market hours despite crypto’s 24/7 nature. citeturn11view1turn11view0  
The absence of a formal open is not a blocker; it just forces you to define a consistent “session clock,” exactly as Shen et al. do via volume spikes. citeturn25view2  

**Fee survival**
Session breakouts can clear fees **only if** you target “big candle days.” If the signal produces a lot of small false breaks, the fee drag dominates.

### VWAP‑based strategies for 24/7 crypto

**Core VWAP math (exact)**
VWAP is the volume‑weighted average price:
\[
\text{VWAP}=\frac{\sum_i p_i\cdot v_i}{\sum_i v_i}
\]
This is the standard definition across practitioner references and execution research. citeturn24search0turn24search3  

**Anchored VWAP (AVWAP)**
Anchored VWAP uses the **same formula**, but the sum starts from a chosen anchor bar rather than the session open. citeturn24search1  

#### VWAP reclaim with crypto‑native anchoring

**Name**: VWAP reclaim (trend continuation after recapturing “fair value”)  
**Source**: VWAP is a mainstream intraday benchmark; the anchored VWAP construct is documented in ChartSchool. citeturn24search1turn24search0  

**Crypto‑specific anchor problem**
Crypto has no official open, so you must define an anchor that is stable and non‑hindsight. Practitioner‑safe anchors for automation:

* **UTC day anchor**: 00:00 UTC (most defensible “daily reset” for 24/7)
* **CME‑close anchor proxy**: 17:00 ET (matches Shen’s use of 5pm EST as a close reference) citeturn25view2  
* **Session anchor**: start of empirically measured volume spike windows (US/Asia)

ASSUMPTION: 00:00 UTC and 17:00 ET anchors will behave differently by asset; you must test.

**Exact entry rules (long‑only)**
Let \(\text{AVWAP}_t\) be anchored at \(t_a\). Define deviation:
\[
d_t=\frac{close_t-\text{AVWAP}_t}{\text{AVWAP}_t}
\]

Long entry when all are true:
1. \(d_{t-1}<0\) and \(d_t>0\) (cross from below to above AVWAP: reclaim)
2. Slope condition: \(\text{AVWAP}_t - \text{AVWAP}_{t-10} > 0\) (AVWAP rising over last 10 minutes)
3. Volume condition: \(vol_t \ge 1.5\cdot \text{SMA}_{20}(vol)\)
4. Fee‑aware targetability filter: \(\text{ATR}_{14,1m}/close_t \ge 0.004\) (≥0.4% per minute ATR)  
   *Rationale*: if the market is too quiet, you won’t clear 1.2% round trip.

**Exit**
* Stop: \( \text{AVWAP}_t - 1.5\cdot \text{ATR}_{14,1m}\)
* First target: \(+2.0\cdot \text{ATR}_{14,1m}\)
* Runner exit: trail by 1.0 ATR until close crosses back below AVWAP.

**VWAP bands (standard deviation bands)**
VWAP bands are typically expressed as:
\[
\text{Upper}_k=\text{VWAP}+k\cdot\sigma_{(p-\text{VWAP})},\quad
\text{Lower}_k=\text{VWAP}-k\cdot\sigma_{(p-\text{VWAP})}
\]
with \(k\in\{1,2\}\) being common. citeturn24search12turn24search13  

**Crypto calibration note**
VWAP band mean reversion *can* work intraday, but—with your fee floor—it generally needs:
* high volatility regime, and
* reversion distance well beyond ~1.2% (or maker execution).

### Volatility breakouts on 1‑minute crypto: squeeze → expansion

**Name**: Bollinger‑Keltner “Squeeze” (TTM Squeeze family)  
**Source**: The “squeeze” definition (Bollinger Bands inside Keltner Channels; “fires” when they exit) is documented in mainstream technical indicator references. citeturn8search15turn26search33  

**Exact indicator definitions**
Given close series \(C_t\):

* Bollinger middle: \(MB_t=\text{SMA}_{n}(C)\)  
* Bollinger std: \(\sigma_t = \text{StdDev}_{n}(C)\)  
* Bollinger bands: \(BB^{\pm}_t=MB_t \pm k_{bb}\sigma_t\)

Keltner:
* \(KC\_m_t = \text{EMA}_{n}(C)\)
* \(KC^{\pm}_t = KC\_m_t \pm k_{kc}\cdot \text{ATR}_n\)

**Squeeze condition (exact)**
\[
BB^+_t \le KC^+_t \quad\text{and}\quad BB^-_t \ge KC^-_t
\]

**Common crypto‑minute parameterization to start**
* \(n=20\) (20 minutes)
* \(k_{bb}=2.0\)
* \(k_{kc}=1.5\) (sometimes 2.0, but 1.5 is more breakout‑sensitive)

ASSUMPTION: public, peer‑reviewed crypto literature rarely publishes “best” BB/KC parameters at 1‑minute; you should treat these values as a defensible starting grid, not gospel.

**Exact entry/exit rules that respect your fee floor**
Because you must capture >1.2% round trip, you need squeeze trades that transition into **trend legs**:

Long entry:
1. Squeeze was ON for at least \(M\) bars (e.g., \(M\ge 20\))
2. Squeeze turns OFF (“fires”) at time \(t\)
3. Breakout confirmation: \(close_t \ge BB^+_t\)
4. Regime filter: realized volatility ratio (defined below) ≥ 1.3

Exit:
* stop: \(2\cdot ATR_{14}\)
* trail: 1.5 ATR
* time stop: 240 minutes

**Why it can work in crypto**
Crypto exhibits clustering of jumps and volatility (documented in intraday Bitcoin studies), and microstructure metrics like toxicity (VPIN) relate to jump risk. citeturn11view1turn17search3turn12view1  
Squeeze → expansion is a way to systematically capture those regimes *if* you avoid trading quiet chop.

### Mean reversion that is actually defensible for crypto intraday

**Name**: OU‑style intraday mean reversion on a de‑trended series (VWAP‑anchored or EMA‑detrended)  
**Academic grounding (process)**: entity["people","Leonard Ornstein","physicist"] and entity["people","George Uhlenbeck","physicist"]’s Ornstein–Uhlenbeck process (OU) is a classic mean‑reverting diffusion used in finance. citeturn27search0  
**Crypto evidence that mean reversion exists intraday**: documented intraday negative autocorrelation and mean‑regressing behavior in Bitcoin at intraday horizons, plus the use of simple strategies exploiting that behavior. citeturn11view1  

**Exact OU model (continuous‑time)**
For a mean‑reverting state \(X_t\):
\[
dX_t = \kappa(\theta - X_t)\,dt + \sigma\,dW_t
\]
where \(\kappa>0\) is the reversion speed.

**Half‑life**
The OU half‑life is:
\[
t_{1/2}=\frac{\ln 2}{\kappa}
\]
(derivable from the exponential decay of deviations in OU).

**Discrete estimation method you can implement on 1‑minute bars**
Define a de‑trended series \(x_t\) (choose one):
* \(x_t=\log(close_t)-\log(\text{AVWAP}_t)\) (anchored VWAP detrend), or
* \(x_t=\log(close_t)-\text{EMA}_{L}(\log(close))\)

Estimate via AR(1):
\[
\Delta x_t = \alpha + \beta x_{t-1} + \varepsilon_t
\]
Then \(\kappa\approx -\beta\) if \(\Delta t=1\) (small‑step approximation), or more precisely \(\kappa\approx -\ln(1+\beta)\).  

**Z‑score trigger (exact)**
Let rolling mean/std over window \(W\):
\[
z_t=\frac{x_t-\mu_W}{\sigma_W}
\]

Long entry:
* \(z_t \le -z_{\text{in}}\) where \(z_{\text{in}}\in[1.5,2.5]\)
* Regime filter: rolling Hurst \(H<0.45\) (mean‑reverting regime; defined later)
* Volatility floor: \(\text{ATR}_{14}/price \ge 0.005\) (without enough vol, fees kill you)

Exit:
* take profit when \(z_t\ge -0.5\) (partial mean reversion)
* stop if \(z_t\le -3.0\)
* time stop \(T_{\max}=2\cdot t_{1/2}\) (if it won’t revert in ~two half‑lives, you may be in regime shift)

**Which pairs/conditions tend to mean‑revert more (practitioner reality)**
* Mean reversion signals are more exploitable when **microstructure effects dominate** (bid‑ask bounce, local overreaction, liquidity pockets) and when you have a stable “value proxy” (VWAP/AVWAP). Intraday studies explicitly discuss microstructure effects like bid‑ask bounce and unusual negative autocorrelation behavior. citeturn11view1  
* Mean reversion is least reliable during “one‑way flow” regimes (liquidation cascades, news jumps), where OU assumptions break.

## Advanced mathematical signals to implement with exact formulas, thresholds, and sources

### Realized volatility vs rolling volatility ratio

**Name**: Realized volatility ratio (RVol short / RVol long) regime filter  
**Source**: Realized variance as sum of squared intraday returns is standard in the realized volatility literature (e.g., Andersen‑Bollerslev‑Diebold‑Labys framework; and RV definitions). citeturn16search1turn16search13  

**Exact calculation**
Let 1‑minute log returns be \(r_t=\ln(C_t/C_{t-1})\).

Realized variance over a window of \(n\) minutes:
\[
RV_{t,n}=\sum_{i=0}^{n-1} r_{t-i}^2
\]
Realized volatility:
\[
\sigma_{t,n}=\sqrt{RV_{t,n}}
\]

Define ratio:
\[
\rho_t=\frac{\sigma_{t,n_s}}{\sigma_{t,n_l}}
\]
where \(n_s\ll n_l\). A useful starting calibration:
* \(n_s=15\) minutes
* \(n_l=240\) minutes (4 hours)

**Thresholds that matter**
* \(\rho_t \ge 1.3\): volatility expansion regime → enable breakouts
* \(\rho_t \le 0.8\): compressed regime → prefer mean reversion *or* wait for squeeze setups

**Implementation notes**
* Use log returns (numerically stable).
* Clip \(\rho_t\) to avoid blowups when \(\sigma_{t,n_l}\) is tiny (quiet altcoin hours).

**Failure modes**
* During discontinuous jumps, RV spikes can stay elevated; you need a cooldown (e.g., don’t flip regimes more than once per hour).

### Kyle’s Lambda (price impact coefficient)

**Name**: Kyle’s \(\lambda\) (illiquidity / price impact per unit order flow)  
**Originator**: entity["people","Albert Kyle","finance economist"] (Kyle 1985). citeturn7search5turn7search9  

**Exact estimation**
At discrete time (1‑minute bars), define:
* \(\Delta p_t = m_t - m_{t-1}\) where \(m_t\) is midprice
* \(q_t\) = signed net order flow (signed volume) in that minute

A Kyle‑style regression:
\[
\Delta p_t = \alpha + \lambda q_t + \varepsilon_t
\]
\(\lambda\) is the slope.

**Crypto‑implementable \(q_t\) from Coinbase**
From Coinbase `market_trades`, each trade has a `side` that refers to the **maker side**. citeturn28view1  
Define aggressor sign:
* If `side` = BUY (maker bid), the aggressor is a seller → signed aggressor volume is negative.
* If `side` = SELL (maker ask), the aggressor is a buyer → signed aggressor volume is positive.

Then:
\[
q_t=\sum_{i\in t} s_i\cdot v_i,\quad s_i\in\{+1,-1\}
\]

**Normalization you should do**
Raw \(\lambda\) depends on units. For comparability across coins:
* use \(\Delta p_t/p_t\) (return) on the left, and
* use dollar‑signed flow on the right.

**Thresholds that matter (practitioner calibration)**
ASSUMPTION: you must calibrate thresholds per pair. In practice you can use percentiles:
* trade only when \(\lambda_t\) is in the **lowest 30%** of its 14‑day history if you rely on taker orders (low impact)
* or trade only when \(\lambda_t\) is in the **highest 20%** if you run a breakout that explicitly *needs* high impact to move quickly (but expect worse fills)

### Amihud illiquidity ratio

**Name**: Amihud illiquidity (ILLIQ)  
**Originator**: entity["people","Yakov Amihud","finance professor"] (2002). citeturn7search0  

**Exact formula (minute adaptation)**
Classic (daily) form is absolute return divided by dollar volume; you can compute it on 1‑minute bars too:
\[
ILLIQ_t=\frac{|r_t|}{\text{DollarVol}_t}
= \frac{|\ln(C_t/C_{t-1})|}{C_t\cdot V_t}
\]

**Thresholds**
Use rolling percentiles per product:
* Avoid trading when \(ILLIQ_t\) is in the top 20% (thin liquidity → worse slippage → fees hurt more).

**Edge case**
When volume is near zero, ILLIQ explodes; enforce minimum volume filters.

### Order book imbalance (OBI)

**Name**: OBI (top of book or depth‑summed)  
**Crypto‑specific source**: Defined explicitly on high‑frequency Coinbase BTC order book data (top‑10 levels averaged) as a normalized bid‑vs‑ask volume imbalance: citeturn15view0  
Also central in limit‑order‑book trading research (imbalance correlates with next price move). citeturn23view0turn19view1  

**Exact formula (depth‑summed)**
Let levels \(i=1..N\), sampled at micro‑times \(\tau=1..T\) within the minute. Then:
\[
\rho_t=
\frac{\sum_{\tau=1}^{T}\sum_{i=1}^{N}\nu^{b,i}_{\tau}-\sum_{\tau=1}^{T}\sum_{i=1}^{N}\nu^{a,i}_{\tau}}
{\sum_{\tau=1}^{T}\sum_{i=1}^{N}\nu^{b,i}_{\tau}+\sum_{\tau=1}^{T}\sum_{i=1}^{N}\nu^{a,i}_{\tau}}
\]
This is exactly the “order imbalance” definition used in Coinbase BTC order book analysis. citeturn15view0  

Top‑of‑book is the special case \(T=1,N=1\).

**Thresholds for 1‑minute direction**
Start with:
* \(|\rho_t|\ge 0.20\): actionable
* \(|\rho_t|\ge 0.35\): strong (but rarer)

**Failure modes**
Spoofing and sudden cancels can make \(\rho_t\) lie; the spoofing literature confirms this is not a theoretical risk. citeturn17search23turn18view1  

### Trade flow imbalance (TFI)

**Name**: TFI (buy‑initiated vs sell‑initiated flow)  
**Source**: Coinbase BTC analysis defines “trade imbalance” as a normalized difference of buy‑initiated vs sell‑initiated volumes. citeturn15view0  

**Exact formula**
\[
\lambda_t=
\frac{\sum \nu^{buy}_t-\sum \nu^{sell}_t}{\sum \nu^{buy}_t+\sum \nu^{sell}_t}\in(-1,1)
\]
citeturn15view0  

**Coinbase implementation detail that matters**
Coinbase `market_trades` includes `side`, which is the **maker side**. citeturn28view1  
So:
* maker SELL ⇒ taker BUY (buy‑initiated)
* maker BUY ⇒ taker SELL (sell‑initiated)

If you ignore this, your TFI sign is flipped.

**Thresholds**
* \(\lambda_t \ge 0.10\): mild buy pressure
* \(\lambda_t \ge 0.25\): strong buy pressure

**Predictive window**
Published Coinbase BTC work discusses expectation of effects in the “next 1 or 2 intervals” after imbalance extremes (their interval example uses 1‑minute windows). citeturn15view0  

### Hurst exponent for regime selection

**Name**: Rolling Hurst exponent \(H\)  
**Source**: The definition and interpretation (H>0.5 persistence/trend; H<0.5 anti‑persistence/mean reversion) are standard and summarized in references on Hurst. citeturn27search2turn27search5  

**Exact R/S definition (core)**
Rescaled range scaling:
\[
\mathbb{E}\left[\frac{R(n)}{S(n)}\right]=C n^H
\]
and estimate \(H\) as the slope of \(\log(R/S)\) vs \(\log n\). citeturn27search2turn27search16  

**Rolling implementation for 1‑minute crypto**
* Window size: \(N\ge 1000\) minutes (≈16.7 hours) if you want stability.
* Compute H on a rolling window every 60 seconds.

**Actionable thresholds**
* \(H \ge 0.55\): trending regime → enable breakouts / momentum holds
* \(H \le 0.45\): mean‑reverting regime → enable OU/VWAP mean reversion
* else: “noise” regime → trade only strongest setups or stand down

### Kalman filter as a noise‑reduced price estimate

**Name**: 1D Kalman filter for latent “fair price”  
**Source**: Standard Kalman state‑space model and update equations. citeturn27search23turn27search10turn27search3  

**Exact state‑space model (simple price‑level filter)**
Let latent state be \(x_t\) and observed price be \(y_t\) (use midprice).

\[
x_t = x_{t-1} + w_t,\quad w_t\sim \mathcal{N}(0,Q)
\]
\[
y_t = x_t + v_t,\quad v_t\sim \mathcal{N}(0,R)
\]

**Kalman recursion (scalar form)**
Prediction:
\[
\hat{x}^-_t=\hat{x}_{t-1},\quad P^-_t=P_{t-1}+Q
\]
Update:
\[
K_t=\frac{P^-_t}{P^-_t+R}
\]
\[
\hat{x}_t=\hat{x}^-_t+K_t(y_t-\hat{x}^-_t)
\]
\[
P_t=(1-K_t)P^-_t
\]

**Threshold use**
Use the filter output \(\hat{x}_t\) as your “fair value”:
* enter long only if \(close_t-\hat{x}_t\) crosses from negative to positive and OBI/TFI agree
* mean reversion: enter long when \(close_t \ll \hat{x}_t\) *and* Hurst shows anti‑persistence

ASSUMPTION: choose \(Q/R\) by targeting a desired smoothness half‑life (e.g., “acts like a 10‑minute EMA”)—this is calibration, not a known constant.

### Kelly criterion for optimal‑f (binary outcomes)

**Name**: Kelly fraction \(f^*\)  
**Originator**: entity["people","John Larry Kelly Jr.","bell labs researcher"] (1956). citeturn16search3  

**Exact formula (binary bet, full loss on loss)**
If win probability \(p\), loss probability \(q=1-p\), and win payoff is \(b\) (net profit per $1 bet), then:
\[
f^* = p - \frac{q}{b}
\]
This follows from maximizing expected log growth. citeturn16search3turn16search7  

**Fractional Kelly**
Use:
\[
f=\alpha f^*,\quad \alpha\in[0.25,0.50]
\]
to reduce estimation risk (critical for your small sample sizes and nonstationary crypto).

**Dynamic update over rolling 50 trades**
Let rolling window \(W=50\):
* \(\hat{p} = \frac{\#wins}{W}\)
* \(\hat{b} = \frac{\text{avg win %}}{\text{avg loss %}}\) (net of fees!)

Then compute \(f^*\) and clip to max risk caps.

### Volatility‑adjusted position sizing for a $100 base size

**Name**: ATR‑scaled sizing  
**Core formula (exact)**
Let base notional be \(N_0=100\). Define ATR percent:
\[
a_t=\frac{\text{ATR}_{14,1m}}{close_t}
\]
Choose a target ATR percent \(a^*\) (your “normal volatility” point), then:
\[
N_t = N_0 \cdot \frac{a^*}{a_t}
\]
Clip:
\[
N_t \in [N_{\min}, N_{\max}]
\]

**Crypto‑calibrated bounds (practical)**
Given your $500 account:
* \(N_{\max}\le 150\)–200 (avoid concentration)
* \(N_{\min}\ge 25\)–50 (below that, fees dominate)

ASSUMPTION: pick \(a^*\) as the median \(a_t\) over the last 14 days per pair.

### Entropy‑based regime detection (ApEn / SampEn)

**Name**: Approximate Entropy (ApEn) and Sample Entropy (SampEn)  
**Originators**: entity["people","Stuart Pincus","mathematician biomedical researcher"] (ApEn) citeturn7search10turn7search6, entity["people","Joshua Richman","researcher"] and entity["people","J. Randall Moorman","cardiology researcher"] (SampEn). citeturn7search23turn7search11  

**ApEn (exact)**
Given embedding dimension \(m\), tolerance \(r\), series length \(N\):
\[
\text{ApEn}(m,r)=\phi^m(r)-\phi^{m+1}(r)
\]
with:
\[
\phi^m(r)=\frac{1}{N-m+1}\sum_{i=1}^{N-m+1}\ln C_i^m(r)
\]
(see Pincus for full definition). citeturn7search10  

**SampEn (exact)**
\[
\text{SampEn}(m,r,N) = -\ln\left(\frac{A}{B}\right)
\]
where \(A\) counts matches of length \(m+1\) and \(B\) counts matches of length \(m\) under tolerance \(r\). citeturn7search11turn7search23  

**Crypto‑minute parameterization**
Common robust defaults in applied SampEn work:
* \(m=2\)
* \(r=0.2\cdot \text{StdDev}(x)\)
* \(N\ge 1000\) points

ASSUMPTION: lower entropy ↔ “more regular” price paths (trend or structured mean‑reversion). You must map entropy to regime by empirical labeling (trend vs chop) because “low entropy” does not uniquely mean “trend.”

## Expert methodologies and agent‑debate upgrades for 1‑minute crypto spot

### Practitioners and researchers with directly relevant, documented intraday crypto edge frameworks

The most defensible “experts” for your system are not celebrity discretionary traders; they are **market microstructure and HFT researchers who publish measurable signals** in crypto or in LOB settings transferable to crypto.

* entity["people","Amin Nejat","hec montreal researcher"]: Coinbase BTC high‑frequency modeling with order book features and explicit imbalance variables, including a practical warning about spoofing interpretation and 1–2 interval effects. citeturn12view0turn15view0turn14view2  
* entity["people","David Easley","cornell economist"] and entity["people","Maureen O'Hara","cornell economist"] (plus coauthors): microstructure metrics (Roll, VPIN) have predictive power in crypto price dynamics; cross‑asset effects (BTC/ETH measures predicting others) are reported. citeturn12view1turn14view3  
* entity["people","Kose John","nyu stern professor"] et al.: Coinbase spoofing study finds order‑book imbalances predict Bitcoin returns at minute horizons and details how spoofing changes market quality. citeturn18view1turn17search23  
* entity["people","Marcos López de Prado","quant researcher"] (VPIN co‑development is widely attributed in VPIN material): order‑flow toxicity thinking (VPIN) is relevant as a regime/risk filter in crypto. citeturn8search2turn8search26turn8search34  
* entity["people","Enzo Busseti","researcher"] and entity["people","Stephen Boyd","optimization researcher"]: VWAP optimal execution framing is directly relevant when fees dominate and execution quality becomes your primary edge lever. citeturn24search3  
* Market making mechanics and the taker‑fee hurdle are directly studied in the live crypto LOB experiment work. citeturn18view0turn19view1turn23view0  

### Applicability of your current eight agents to 1‑minute crypto (and replacements)

Below, “applies” means the core methodology can be made coherent under: 24/7 market, long‑only spot, 1‑minute execution, and heavy fee drag.

* entity["people","Mark Minervini","stock trader author"] — **Does not apply** (equity swing growth/trend templates). Replace with a **microstructure & liquidity agent** modeled on Easley/O’Hara and Nejat‑style order book features. citeturn12view1turn12view0  
* entity["people","Richard Dennis","turtle trader"] — **Partially applies** only as a breakout archetype, but needs fee‑aware refit: fewer trades, larger targets, intraday session framing. Consider replacing with a **session breakout + volatility expansion agent** using the squeeze/breakout regime logic. citeturn8search15turn11view1  
* entity["people","Larry Williams","trader author"] — **Partially applies** (oscillators like Williams %R can serve as mean‑reversion triggers), but must be regime‑gated and fee‑aware (targets must exceed costs).  
* entity["people","Andreas Clenow","quant trader author"] — **Weak fit** for 1‑minute (trend systems generally need longer horizon to overcome fees). Replace with **intraday time‑of‑day momentum lens** based on documented intraday predictability work. citeturn10view0turn25view2  
* entity["people","Ernest Chan","quant trader author"] — **Best fit** among the list (stat arb, mean reversion modeling, regime filters). Keep, but force crypto‑specific inputs: OU half‑life, Hurst/entropy regime gating, and fee drag math. citeturn27search4turn7search10  
* entity["people","Tom Hougaard","day trader"] — **Partial fit** as an intraday risk‑psychology lens, but for an autonomous bot you need this role to become **execution discipline & trade selection scarcity**: “do we really have expected move > fees?”  
* entity["people","Abdelmessih","trader"] — **ASSUMPTION: identity and methodology** (name is ambiguous). If this agent represents discretionary tape/FX scalping, replace with a **Coinbase tape/flow agent** driven by market_trades + OBI/TFI + spread dynamics (explicit formulas above). citeturn28view1turn15view0  
* entity["people","David Landry","trading educator"] — **Weak fit** for 1‑minute; replace with a **fee/market‑mechanics agent** grounded in the “taker fee is the profitability threshold” reality. citeturn18view0turn29search2  

### Eight philosophical lenses for a 1‑minute crypto entry (with the best “embodiment”)

Lens is what the agent *optimizes*. Each lens corresponds to a different failure mode in 1‑minute crypto.

1. **Microstructure direction** (OBI/microprice/OFI): embody Stoikov/Cont‑style thinking. citeturn8search1turn8search9turn15view0  
2. **Execution & fees** (can we clear the fee floor?): embody Albers‑style maker/taker trade‑off framing + Coinbase fee schedule constraint. citeturn18view0turn29search2  
3. **Liquidity / impact** (Kyle λ, Amihud): embody Kyle/Amihud microstructure. citeturn7search5turn7search0  
4. **Volatility regime** (realized vol ratio, squeeze/expansion): embody realized volatility framework. citeturn16search1turn8search15  
5. **Momentum vs reversal regime choice** (Hurst/entropy): embody Hurst + entropy methods. citeturn27search2turn7search10turn7search23  
6. **Session/time‑of‑day structure** (volume spike “opens”): embody Shen/Wen intraday predictability framing. citeturn25view2turn10view0  
7. **Market manipulation risk** (spoofing detection / distrust imbalance): embody Coinbase spoofing + Nejat’s imbalance vs trade imbalance logic. citeturn18view1turn15view0  
8. **Cross‑market positioning** (derivatives lead spot): embody crypto microstructure papers linking toxicity measures and jumps. citeturn17search3turn12view1  

### The questions each lens must ask before the bot is allowed to enter

These are phrased as “gates” your agents should enforce.

*Microstructure direction*  
“Is \(\text{OBI}\ge 0.20\) **and** is \(\Delta^\mu_t>0\) for at least 2 consecutive samples, or is the book flipping?” citeturn15view0turn23view0  

*Execution & fees*  
“Given current ATR and planned stop/target, what is the **expected gross move** vs **1.2% round trip**? If expected move < ~2× fees, why are we trading?” citeturn18view0turn29search2  

*Liquidity / impact*  
“Is Kyle’s \(\lambda\) in a favorable percentile today for this pair? If not, do we switch to maker‑only or stand down?” citeturn7search5turn7search9  

*Volatility regime*  
“Is \(\rho_t=\sigma_{15}/\sigma_{240}\) above threshold (expansion) for breakout trades, or below (compression) for mean reversion?” citeturn16search1turn16search13  

*Momentum vs reversal*  
“Does rolling Hurst indicate persistence (H>0.55) or anti‑persistence (H<0.45)?” citeturn27search2turn27search5  

*Session structure*  
“Are we currently inside a defined volume‑spike session where intraday predictability is stronger, as suggested by volume/volatility conditioning?” citeturn25view2turn11view1  

*Manipulation risk*  
“Is order imbalance extreme but trade imbalance contradicts it (possible spoofing), i.e., \(\rho_t\approx -1\) but \(\lambda_t>0\)?” citeturn15view0turn18view1  

*Cross‑market positioning*  
“Are funding/OI/liquidations signaling one‑way forced flow risk that invalidates mean reversion entries?” citeturn6search0turn6search32turn3search13  

## Crypto‑specific context signals beyond OHLCV and how to use them in real time

### Funding rates (perpetuals)
**What it measures**: the periodic payment between longs/shorts in perpetual futures; persistent positive funding often implies crowded long positioning.  
**Where to get it**: CoinGlass provides funding rate history endpoints (including OHLC format) and supports major exchanges. citeturn6search0turn6search28turn6search8  
**How to calculate**: use the OI‑weighted or exchange‑aggregated funding rate; compute z‑scores on a rolling window.  
**Actionable conditions** (research‑consistent framing, thresholds must be calibrated):
* Funding z‑score > +2: crowded long risk → avoid long mean reversion fades; prefer breakout with tight risk or stand down.  
Funding dynamics research exists on Bitcoin perpetuals. citeturn3search13turn3search16  

### Open interest changes
**What it measures**: growth/shrink in outstanding leveraged positions.  
**Where to get it**: CoinGlass Open Interest endpoints provide OI OHLC history, aggregated. citeturn6search16turn6search0turn6search8  
**How**: compute \(\Delta OI / OI\) over 5–60 minutes; pair with price return.  
**Actionable**:
* If price up and OI up sharply → trend continuation risk‑on (breakout bias)  
* If price up and OI down → short covering (more mean‑reverting risk)

ASSUMPTION: “OI‑price quadrant” logic is broadly used by derivatives traders, but you must validate predictive power at your 1‑minute execution horizon.

### Liquidation clustering
**What it measures**: forced closes of leveraged positions; often appears as cascade zones.  
**Where to get it**: CoinGlass liquidation endpoints (aggregated liquidation history; liquidation heatmaps exist at product level). citeturn6search32turn6search0turn6search8  
**How**: track liquidation volume spikes; optionally build “liquidation density” by binning liquidation prints by price.  
**Actionable**:
* Do not mean‑revert into rising liquidation intensity; treat as “forced flow regime.”

### Exchange net flows
**What it measures**: coins moving onto exchanges (potential sell pressure) vs off exchanges (potential accumulation), mostly at hourly/daily frequencies.  
**Where**:
* Glassnode exchange net flow endpoints and metric descriptions. citeturn6search2turn6search6turn6search34  
* CryptoQuant exchange flow definitions and API. citeturn6search1turn6search13  

**1‑minute applicability**
These are usually too slow for 1‑minute entries; use as a **regime bias**, not a trigger.

### Fear & Greed Index
**What**: composite sentiment index.  
**Predictive power**: research exists on its relationship to returns/volatility, but evidence is typically at daily horizons, not 1‑minute. citeturn4search23turn4search25  
**Action**: treat as macro regime (risk‑on/off) only.

### Stablecoin flows
**What**: stablecoin movements into exchanges as a proxy for “dry powder” and demand.  
**Where**: Glassnode has stablecoin exchange net flow metrics. citeturn6search18  
**Research note**: stablecoin transfer behavior can be linked to market movements in published work. citeturn4search3  

### Social sentiment velocity
**What**: rate of change of mentions/engagement (not just sentiment level).  
**Where**: LunarCrush provides social and sentiment APIs. citeturn6search11turn6search3turn6search15  
**How**:
\[
\text{SentVel}_t = \frac{\text{Mentions}_{t}-\text{Mentions}_{t-\Delta}}{\Delta}
\]
and similarly for engagement.  
**Actionable**:
* Use percentile triggers: SentVel in top 5% over last 30 days → news/attention regime.

### Bitcoin dominance and correlation
**What**: BTC market cap share and rolling correlations between BTC and alts.  
**Where**: market cap data providers (not covered by the Coinbase API excerpts here); correlations you compute from your own OHLCV.  
**Actionable**:
* If rolling correlation of alt to BTC > 0.8 and BTC dominance rising, reduce simultaneous alt exposure (see correlation sizing below).

ASSUMPTION: dominance itself is too slow for 1‑minute triggers; it’s a portfolio‑level regime control.

## Position sizing and fee math for a small, high‑fee account

### Kelly criterion with rolling updates (exact math)

**Binary Kelly setup**
Let:
* \(p\) = win rate
* \(q=1-p\)
* \(b\) = net profit per $1 risked on a win (e.g., if you risk 1% and average win is +2%, then \(b=2\))

Kelly fraction:
\[
f^* = p - \frac{q}{b}
\]
citeturn16search3  

**Fractional Kelly**
\[
f=\alpha f^*,\quad \alpha\in[0.25,0.50]
\]
(critical because your parameter estimates are noisy and crypto regimes shift).

**Rolling update over last 50 trades**
Compute:
\[
\hat{p}_{50}=\frac{\#wins}{50},\quad \hat{b}_{50}=\frac{\text{avg win (net)}}{\text{avg loss (net)}}
\]
Then:
\[
f_{50}=\alpha\left(\hat{p}_{50}-\frac{1-\hat{p}_{50}}{\hat{b}_{50}}\right)
\]
Clip \(f_{50}\) to a max portfolio risk cap (practitioner necessity for small accounts).

### Volatility targeting with ATR for a $10/day risk budget

You stated: target daily volatility = 2% of $500 = $10/day.

A practical trade‑level implementation consistent with volatility targeting:

Let:
* \(R_{\$}\) = dollars you’re willing to lose if stop is hit
* stop distance as a percent = \(d_t = k_s\cdot ATR_{14}/price\)

Then position notional:
\[
N_t=\frac{R_{\$}}{d_t}
\]

If you allow at most \(n\) independent trades per day and want expected daily risk ≈ $10:
\[
R_{\$} \approx \frac{10}{\sqrt{n}}
\]
ASSUMPTION: independence is false in crypto (high correlation), so you should be more conservative (use \(10/n\) instead of \(10/\sqrt{n}\) if you commonly hold correlated positions).

### Correlation‑adjusted sizing when positions move together

If you hold \(k\) alt positions that are all positively correlated with BTC, then your “true” risk is closer to one big BTC‑beta position than \(k\) independent bets.

Operational rule:
*Compute the correlation matrix \(\Sigma\) of 1‑minute returns over a rolling window (e.g., last 2–7 days).*
Then compute portfolio variance:
\[
\sigma_p^2 = w^\top \Sigma w
\]
Pick weights \(w\) (dollar‑normalized) so that \(\sigma_p\) matches your daily risk target.

ASSUMPTION: This is pure mean‑variance math; you must decide whether to estimate \(\Sigma\) on 1‑minute, 5‑minute, or hourly returns (stability vs responsiveness trade‑off).

### Fee drag breakeven: minimum move, win rate, and R:R

Let round‑trip fee be \(f=0.012\) (1.2%). Let average win (gross) be \(G\) and average loss (gross absolute) be \(L\) as fractions of entry price.

Expected net return per trade:
\[
E = pG - (1-p)L - f
\]
Breakeven requires \(E\ge 0\):
\[
pG - (1-p)L \ge f
\]

Define reward‑to‑risk \(R=\frac{G}{L}\Rightarrow G=RL\). Then:
\[
pRL - (1-p)L \ge f
\Rightarrow L(pR - 1 + p)\ge f
\Rightarrow p(R+1)\ge 1+\frac{f}{L}
\]
So the minimum win rate is:
\[
p_{\min}=\frac{1+\frac{f}{L}}{R+1}
\]

**Interpretation**
If your stop is tight (small \(L\)), \(f/L\) explodes and \(p_{\min}\) becomes unrealistically high.

Example (fee floor implication):
* If \(L=0.5\%\) and \(R=2\): \(f/L=1.2/0.5=2.4\) → \(p_{\min}=(1+2.4)/3\approx 1.133\) (impossible).  
Therefore, **you cannot use a 0.5% stop with a 1% target under 1.2% fees**.

This is the single most important math fact for your system design.

### Optimal number of simultaneous positions for $500 with high correlation and high fees

Portfolio theory in correlated assets implies diminishing diversification benefit as correlation rises. With crypto majors/alts often strongly correlated intraday, the practical optimum is usually:

* **few simultaneous positions**, each with enough expected move to clear fees, rather than many small positions whose edges are eaten by cost and correlation.

Given your constraints, a defensible default is:
* 1–2 concurrent positions unless your correlation matrix shows genuinely low correlation clusters.

ASSUMPTION: The “optimal number” depends on realized correlation regime (risk‑on vs idiosyncratic alt moves) and your execution style (maker vs taker).

