"""
scanner.py — Multi-exchange Kraken + Binance USDM scanner.

Scans all liquid perpetuals across Kraken Futures and Binance USDM.
Runs every 5 minutes. Returns up to 50 candidates with direction and setup type.

Data sources (public endpoints, no auth required):
  Kraken Futures:  https://futures.kraken.com  (US-accessible)
  Binance fapi:    https://fapi.binance.com     (public data only)

Step 2 sub-filters — any one passing sends the candidate through.
All fired setups tracked in scan_setups for per-setup performance attribution:
  A. momentum        — vol_spike OR price_move; ADX ≥ 15
  B. kst_cross       — KST oscillator crossed its signal line
  C. supertrend      — SuperTrend flipped direction this bar
  D. ranging_mr      — ADX < 20; price displaced ≥ 0.20% from VWAP (mean-reversion)
  E. funding_collect — |funding/8h| ≥ 0.01%; enter in the direction that gets paid

Symbol normalization:
  Kraken PF_XBTUSD → base_asset='BTC', exchange='kraken', symbol stays 'PF_XBTUSD'
  Binance BTCUSDT  → base_asset='BTC', exchange='binance', symbol stays 'BTCUSDT'
  direction is always 'LONG' or 'SHORT' for execution-layer compatibility.

Output fields (backward-compatible with v10_runner.py):
  symbol, direction, exchange, base_asset, scan_setups, primary_setup,
  vol_spike, adx_15m, price_move_1h_pct, price_move_4h_pct,
  atr_15m, stop_pct, target_pct, expected_profit,
  funding_rate, funding_cost_pct, vol_usd, volume_24h_usd,
  correlation_penalty, regime_penalty, spread_pct,
  bid_depth_usd, ask_depth_usd, price.
"""

import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import urllib.request as _urllib
    import json as _json

    _HTTP_OK = True
except ImportError:
    _HTTP_OK = False

try:
    import numpy as np

    _NUMPY_OK = True
except ImportError:
    _NUMPY_OK = False


# ── Exchange base URLs ─────────────────────────────────────────────────────────
_KRAKEN_BASE = "https://futures.kraken.com/derivatives/api/v3"
_KRAKEN_CHARTS = "https://futures.kraken.com/api/charts/v1"
_BINANCE_BASE = "https://fapi.binance.com"
_HL_BASE = "https://api.hyperliquid.xyz/info"

# ── Filter thresholds ──────────────────────────────────────────────────────────
_MIN_VOLUME_24H_USD = 2_500_000  # $2.5M/day — aligned with economics gate $3M floor
_MIN_VOL_SPIKE = 0.4  # sub-filter A: vol/20-bar avg
_MIN_PRICE_MOVE_1H = 0.15  # sub-filter A: 1h absolute price move %
_MIN_ADX_MOMENTUM = 15  # sub-filter A/B/C: trend confirmation
_MAX_ADX_RANGING = 20  # sub-filter D: ranging when ADX < this
_MIN_VWAP_DISP_PCT = 0.20  # sub-filter D: min VWAP displacement %
_MIN_FUNDING_COLLECT = 0.0001  # sub-filter E: |per-8h rate| to collect
_MIN_OB_DEPTH_USD = 5_000  # $5K each side — relaxed for Kraken thin books
_MAX_SPREAD_PCT = 0.25  # PERCENT units (= 0.25%) — spread_pct is stored as % so 0.25 means 25 bps; economics gate uses fraction units (÷100 before passing)
_MIN_EXPECTED_PROFIT = 0.25  # $ — lower floor to pass more candidates
_TOP_N = 50
_MAX_STEP1_BINANCE = 100  # cap Binance universe at top 100 by volume
_MAX_STEP1_HYPERLIQUID = 80  # top 80 HL markets by volume
_ROUND_TRIP_FEE_PCT = 0.00130  # 0.065% × 2 (Kraken); conservative for Binance too
_FUNDING_HOLD_PERIODS = 1.5  # expected 8h-equivalent funding periods held
_PARALLEL_WORKERS = 20  # concurrent kline fetch threads

# ── Setup priority order (for primary_setup selection) ───────────────────────
_SETUP_PRIORITY = [
    "supertrend",
    "kst_cross",
    "momentum",
    "ranging_mr",
    "funding_collect",
]

# ── Module-level cache ────────────────────────────────────────────────────────
_CACHE_TTL = 300
_lock = threading.RLock()
_last_scan_ts: float = 0.0
_last_candidates: List[Dict] = []

# ── Kraken → universal symbol mapping ────────────────────────────────────────
_KRAKEN_BASE_MAP = {"XBT": "BTC", "XDG": "DOGE"}
_KRAKEN_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}


# ══════════════════════════════════════════════════════════════════════════════
# HTTP HELPER
# ══════════════════════════════════════════════════════════════════════════════


def _get(url: str, timeout: int = 10) -> Optional[Dict]:
    if not _HTTP_OK:
        return None
    try:
        req = _urllib.Request(url, headers={"User-Agent": "AlgoBot/1.0"})
        with _urllib.urlopen(req, timeout=timeout) as resp:
            return _json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.debug(f"[scanner] GET failed: {url!r} — {e}")
        return None


def _post(url: str, data: dict, timeout: int = 10) -> Optional[Dict]:
    """HTTP POST with JSON body — used for Hyperliquid API."""
    if not _HTTP_OK:
        return None
    try:
        body = _json.dumps(data).encode("utf-8")
        req = _urllib.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "AlgoBot/1.0"},
        )
        with _urllib.urlopen(req, timeout=timeout) as resp:
            return _json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.debug(f"[scanner] POST failed: {url!r} — {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# SYMBOL NORMALIZATION
# ══════════════════════════════════════════════════════════════════════════════


def _kraken_base(symbol: str) -> str:
    """PF_XBTUSD → 'BTC'. PF_ETHUSD → 'ETH'."""
    raw = symbol.replace("PF_", "").replace("USD", "").upper()
    return _KRAKEN_BASE_MAP.get(raw, raw)


def _binance_base(symbol: str) -> str:
    """BTCUSDT → 'BTC'. 1000SHIBUSDT → 'SHIB'."""
    s = symbol
    if s.startswith("1000"):
        s = s[4:]
    return s.replace("USDT", "").replace("BUSD", "").replace("USD", "").upper()


# ══════════════════════════════════════════════════════════════════════════════
# KRAKEN DATA FETCHERS
# ══════════════════════════════════════════════════════════════════════════════


def _kraken_tickers() -> List[Dict]:
    data = _get(f"{_KRAKEN_BASE}/tickers", timeout=10)
    if data is None:
        logger.warning("[scanner] Kraken tickers unavailable")
        return []
    return data.get("tickers", [])


def _kraken_klines(symbol: str, interval: str, n_bars: int = 60) -> List[List[float]]:
    """Returns list of [open, high, low, close, volume] rows, oldest first."""
    if interval not in _KRAKEN_INTERVALS:
        return []
    bar_secs = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
        "1w": 604800,
    }.get(interval, 900)
    from_ts = int(time.time()) - (n_bars + 5) * bar_secs
    data = _get(f"{_KRAKEN_CHARTS}/trade/{symbol}/{interval}?from={from_ts}", timeout=8)
    if not data:
        return []
    rows = []
    for k in data.get("candles", []):
        try:
            rows.append(
                [
                    float(k["open"]),
                    float(k["high"]),
                    float(k["low"]),
                    float(k["close"]),
                    float(k["volume"]),
                ]
            )
        except Exception:
            continue
    return rows  # ascending (oldest first)


def _kraken_ob(symbol: str) -> Dict:
    """Returns {'bids': [...], 'asks': [...]} or {}. Kraken bids are ascending."""
    data = _get(f"{_KRAKEN_BASE}/orderbook?symbol={symbol}", timeout=5)
    if not data:
        return {}
    return data.get("orderBook", {})


# ══════════════════════════════════════════════════════════════════════════════
# BINANCE DATA FETCHERS
# ══════════════════════════════════════════════════════════════════════════════


def _binance_tickers() -> List[Dict]:
    data = _get(f"{_BINANCE_BASE}/fapi/v1/ticker/24hr", timeout=10)
    if data is None:
        logger.info(
            "[scanner] Binance tickers unavailable (geo-block or down) — Kraken only"
        )
        return []
    return data if isinstance(data, list) else []


def _binance_funding_all() -> Dict[str, float]:
    """Returns {symbol: per_8h_rate} for all Binance perps. One API call."""
    data = _get(f"{_BINANCE_BASE}/fapi/v1/premiumIndex", timeout=10)
    if not data or not isinstance(data, list):
        return {}
    result = {}
    for item in data:
        sym = item.get("symbol", "")
        try:
            result[sym] = float(item.get("lastFundingRate", 0) or 0)
        except Exception:
            result[sym] = 0.0
    return result


def _binance_klines(symbol: str, interval: str, n_bars: int = 60) -> List[List[float]]:
    """Returns list of [open, high, low, close, volume] rows, oldest first.
    Binance kline format: [open_time, open, high, low, close, base_vol, close_time, quote_vol, ...]
    """
    url = f"{_BINANCE_BASE}/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={n_bars}"
    data = _get(url, timeout=8)
    if not data or not isinstance(data, list):
        return []
    rows = []
    for k in data:
        try:
            rows.append(
                [float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[7])]
            )  # OHLC + quote volume (USDT)
        except Exception:
            continue
    return rows  # ascending (oldest first)


def _binance_ob(symbol: str) -> Dict:
    """Returns {'bids': [...], 'asks': [...]} or {}.
    Binance bids are DESCENDING (best bid at index 0), asks ASCENDING.
    Normalized here to match Kraken convention (bids ascending → best at [-1]).
    """
    data = _get(f"{_BINANCE_BASE}/fapi/v1/depth?symbol={symbol}&limit=20", timeout=5)
    if not data:
        return {}
    # Binance bids: [[price, qty], ...] descending → reverse to ascending for uniform processing
    bids = list(reversed(data.get("bids", [])))
    asks = data.get("asks", [])
    return {"bids": bids, "asks": asks}


# ══════════════════════════════════════════════════════════════════════════════
# HYPERLIQUID DATA FETCHERS
# ══════════════════════════════════════════════════════════════════════════════


def _hl_meta_and_ctxs() -> List[Dict]:
    """
    One POST returns all HL perp markets: price, 24h volume, funding.
    Already normalised to the same format as Kraken/Binance tickers.
    Applies $500K/24h volume floor internally.
    """
    data = _post(_HL_BASE, {"type": "metaAndAssetCtxs"}, timeout=10)
    if not data or not isinstance(data, list) or len(data) < 2:
        logger.info("[scanner] Hyperliquid unavailable — skipping HL")
        return []

    universe = data[0].get("universe", [])  # [{name, szDecimals, maxLeverage, ...}]
    ctxs = data[1]  # [{funding, dayNtlVlm, markPx, midPx, ...}]

    result = []
    for i, meta in enumerate(universe):
        if i >= len(ctxs):
            break
        ctx = ctxs[i]
        try:
            name = str(meta.get("name", "")).upper()
            if not name:
                continue
            mark_px = float(ctx.get("markPx", 0) or 0)
            if mark_px <= 0:
                continue
            vol_usd = float(ctx.get("dayNtlVlm", 0) or 0)
            if vol_usd < _MIN_VOLUME_24H_USD:
                continue
            fund_8h = float(ctx.get("funding", 0) or 0)
            fund_ann = fund_8h * 3 * 365  # annualise per-8h → per-year
            mid_px = float(ctx.get("midPx", mark_px) or mark_px)
            result.append(
                {
                    "symbol": name,
                    "exchange": "hyperliquid",
                    "base_asset": name,
                    "price": mid_px,
                    "volume_24h_usd": vol_usd,
                    "vol_usd": vol_usd,
                    "funding_rate": fund_ann,
                    "bid": mid_px * 0.9998,  # placeholder; real OB in Step 3
                    "ask": mid_px * 1.0002,
                }
            )
        except (ValueError, TypeError):
            continue

    logger.debug(
        f"[scanner] Hyperliquid: {len(result)} markets ≥ ${_MIN_VOLUME_24H_USD / 1e3:.0f}K vol"
    )
    return result


def _hl_klines(coin: str, interval: str = "15m", n_bars: int = 65) -> List[List[float]]:
    """OHLCV from Hyperliquid candleSnapshot. Returns [[O,H,L,C,V_usd], ...] oldest first."""
    bar_secs = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}.get(
        interval, 900
    )
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (n_bars + 5) * bar_secs * 1000

    data = _post(
        _HL_BASE,
        {
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms,
            },
        },
        timeout=8,
    )

    if not data or not isinstance(data, list):
        return []

    rows = []
    for k in data:
        try:
            close = float(k.get("c", 0) or 0)
            vol_usd = float(k.get("v", 0) or 0) * close  # coin vol × price = USD vol
            rows.append([float(k["o"]), float(k["h"]), float(k["l"]), close, vol_usd])
        except Exception:
            continue
    return rows  # ascending (oldest first)


def _hl_ob(coin: str) -> Dict:
    """L2 order book from Hyperliquid. Returns {'bids': [[px,sz],...], 'asks': [[px,sz],...]}."""
    data = _post(_HL_BASE, {"type": "l2Book", "coin": coin}, timeout=5)
    if not data:
        return {}
    levels = data.get("levels", [])
    if len(levels) < 2:
        return {}

    # levels[0]=bids (descending), levels[1]=asks (ascending)
    # Each entry: {'px': '50000', 'sz': '1.5', 'n': 3}  — sz is in coin
    def _parse(level_list):
        out = []
        for l in level_list:
            try:
                out.append([float(l["px"]), float(l["sz"])])
            except Exception:
                pass
        return out

    bids = sorted(_parse(levels[0]), key=lambda x: x[0])  # ascending
    asks = sorted(_parse(levels[1]), key=lambda x: x[0])  # ascending
    return {"bids": bids, "asks": asks}


# ══════════════════════════════════════════════════════════════════════════════
# TECHNICAL INDICATORS (pure Python / NumPy)
# ══════════════════════════════════════════════════════════════════════════════


def _calc_adx(
    highs: List[float], lows: List[float], closes: List[float], period: int = 14
) -> float:
    """Wilder's ADX. Returns 20.0 on insufficient data."""
    if not _NUMPY_OK or len(highs) < period + 2:
        return 20.0
    h = np.array(highs, dtype=float)
    lo = np.array(lows, dtype=float)
    c = np.array(closes, dtype=float)
    tr = np.maximum(
        h[1:] - lo[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(lo[1:] - c[:-1]))
    )
    dm_p = np.where(
        (h[1:] - h[:-1]) > (lo[:-1] - lo[1:]), np.maximum(h[1:] - h[:-1], 0.0), 0.0
    )
    dm_m = np.where(
        (lo[:-1] - lo[1:]) > (h[1:] - h[:-1]), np.maximum(lo[:-1] - lo[1:], 0.0), 0.0
    )

    def _smooth(a, p):
        s = np.zeros(len(a))
        if len(a) < p:
            return s
        s[p - 1] = a[:p].sum()
        for i in range(p, len(a)):
            s[i] = s[i - 1] - s[i - 1] / p + a[i]
        return s

    eps = 1e-9
    di_p = 100.0 * _smooth(dm_p, period) / (_smooth(tr, period) + eps)
    di_m = 100.0 * _smooth(dm_m, period) / (_smooth(tr, period) + eps)
    dx = 100.0 * np.abs(di_p - di_m) / (di_p + di_m + eps)
    slc = dx[period - 1 :]
    if len(slc) < period:
        return 20.0
    adx_s = np.zeros(len(slc))
    adx_s[period - 1] = slc[:period].mean()
    for i in range(period, len(slc)):
        adx_s[i] = adx_s[i - 1] * (1.0 - 1.0 / period) + slc[i] / period
    return float(min(max(adx_s[-1], 0.0), 100.0))


def _calc_vol_spike(volumes: List[float], window: int = 20) -> float:
    """Current bar volume / mean of prior `window` bars."""
    if len(volumes) < window + 1:
        return 1.0
    cur = volumes[-1]
    avg = (
        float(np.mean(volumes[-window - 1 : -1]))
        if _NUMPY_OK
        else sum(volumes[-window - 1 : -1]) / window
    )
    return cur / (avg + 1e-9)


def _calc_kst(closes: List[float]) -> Tuple[float, float, bool, bool]:
    """
    KST (Know Sure Thing) oscillator.
    Returns (kst, signal, bullish_cross, bearish_cross).
    Periods: ROC(10,15,20,30), SMA(10,10,10,15), Signal SMA(9), weights (1,2,3,4).
    """
    ROC_P = (10, 15, 20, 30)
    SMA_P = (10, 10, 10, 15)
    SIG_P = 9
    WEIGHTS = (1, 2, 3, 4)
    need = max(ROC_P) + max(SMA_P) + SIG_P + 5
    if len(closes) < need:
        return 0.0, 0.0, False, False

    c = closes
    rcmas = []
    for roc_p, sma_p in zip(ROC_P, SMA_P):
        roc = [
            (c[i] - c[i - roc_p]) / (c[i - roc_p] + 1e-9) * 100
            for i in range(roc_p, len(c))
        ]
        if len(roc) < sma_p:
            return 0.0, 0.0, False, False
        sma = [
            sum(roc[i - sma_p + 1 : i + 1]) / sma_p for i in range(sma_p - 1, len(roc))
        ]
        rcmas.append(sma)

    min_len = min(len(r) for r in rcmas)
    if min_len < SIG_P + 2:
        return 0.0, 0.0, False, False

    kst_s = [
        sum(w * rcmas[j][len(rcmas[j]) - min_len + i] for j, w in enumerate(WEIGHTS))
        for i in range(min_len)
    ]
    sig_s = [
        sum(kst_s[i - SIG_P + 1 : i + 1]) / SIG_P for i in range(SIG_P - 1, len(kst_s))
    ]

    if len(sig_s) < 2 or len(kst_s) < 2:
        return 0.0, 0.0, False, False

    kst_cur, kst_prv = kst_s[-1], kst_s[-2]
    sig_cur, sig_prv = sig_s[-1], sig_s[-2]
    bull = (kst_cur > sig_cur) and (kst_prv <= sig_prv)
    bear = (kst_cur < sig_cur) and (kst_prv >= sig_prv)
    return kst_cur, sig_cur, bull, bear


def _calc_supertrend(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 10,
    mult: float = 3.0,
) -> Tuple[int, bool, bool]:
    """
    SuperTrend. Returns (direction, cross_up, cross_down).
    direction=1 → bullish, -1 → bearish.
    cross_up   → flipped from bearish to bullish on the last bar.
    cross_down → flipped from bullish to bearish on the last bar.
    """
    if len(closes) < period + 3:
        return 1, False, False
    # True Range
    tr_list = [
        max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        for i in range(1, len(closes))
    ]
    # Wilder's ATR
    atr = [0.0] * len(tr_list)
    atr[period - 1] = sum(tr_list[:period]) / period
    for i in range(period, len(tr_list)):
        atr[i] = (atr[i - 1] * (period - 1) + tr_list[i]) / period
    # Aligned OHLC (skip first bar — used for prev-close in TR)
    h = highs[1:]
    lo = lows[1:]
    c = closes[1:]
    ub = [(h[i] + lo[i]) / 2 + mult * atr[i] for i in range(len(c))]
    lb = [(h[i] + lo[i]) / 2 - mult * atr[i] for i in range(len(c))]
    if len(c) < 2:
        return 1, False, False
    dirs = [1] * len(c)
    fub = list(ub)
    flb = list(lb)
    for i in range(1, len(c)):
        fub[i] = ub[i] if ub[i] < fub[i - 1] or c[i - 1] > fub[i - 1] else fub[i - 1]
        flb[i] = lb[i] if lb[i] > flb[i - 1] or c[i - 1] < flb[i - 1] else flb[i - 1]
        if dirs[i - 1] == -1 and c[i] > fub[i]:
            dirs[i] = 1
        elif dirs[i - 1] == 1 and c[i] < flb[i]:
            dirs[i] = -1
        else:
            dirs[i] = dirs[i - 1]
    cur, prv = dirs[-1], dirs[-2]
    return cur, (cur == 1 and prv == -1), (cur == -1 and prv == 1)


def _calc_vwap(
    highs: List[float], lows: List[float], closes: List[float], volumes: List[float]
) -> float:
    """Session VWAP from provided bars."""
    total_vol = sum(volumes)
    if total_vol <= 0:
        return closes[-1] if closes else 0.0
    return (
        sum((h + lo + c) / 3 * v for h, lo, c, v in zip(highs, lows, closes, volumes))
        / total_vol
    )


def _calc_atr(
    highs: List[float], lows: List[float], closes: List[float], period: int = 14
) -> float:
    """Wilder's ATR. Falls back to mean |Δclose| on short series."""
    n = len(closes)
    if n < 2:
        return 0.0
    tr_list = [
        max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        for i in range(1, n)
    ]
    if len(tr_list) < period:
        return sum(tr_list) / len(tr_list) if tr_list else 0.0
    atr = sum(tr_list[:period]) / period
    for i in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[i]) / period
    return atr


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — UNIVERSE (both exchanges)
# ══════════════════════════════════════════════════════════════════════════════


def _step1_universe(
    kraken_tickers: List[Dict],
    binance_tickers: List[Dict],
    binance_funding: Dict[str, float],
) -> List[Dict]:
    """
    Filter both exchanges to liquid active perpetuals.
    Returns normalized candidate list with: symbol, exchange, base_asset,
    price, volume_24h_usd, funding_rate (annualized decimal), bid, ask.
    """
    result: List[Dict] = []

    # ── Kraken ────────────────────────────────────────────────────────────────
    for t in kraken_tickers:
        sym = t.get("symbol", "")
        if not sym.startswith("PF_"):
            continue
        if t.get("tag") != "perpetual":
            continue
        if t.get("suspended", False):
            continue
        try:
            vol_usd = float(t.get("volumeQuote", 0) or 0)
            price = float(t.get("last", 0) or 0)
            if vol_usd < _MIN_VOLUME_24H_USD or price <= 0:
                continue
            # Kraken fundingRate is annualized decimal (per scanner convention)
            fund_ann = float(t.get("fundingRate", 0) or 0)
            result.append(
                {
                    "symbol": sym,
                    "exchange": "kraken",
                    "base_asset": _kraken_base(sym),
                    "price": price,
                    "volume_24h_usd": vol_usd,
                    "vol_usd": vol_usd,
                    "funding_rate": fund_ann,  # annualized decimal
                    "bid": float(t.get("bid", price) or price),
                    "ask": float(t.get("ask", price) or price),
                }
            )
        except (ValueError, TypeError):
            continue

    # ── Binance ───────────────────────────────────────────────────────────────
    binance_candidates = []
    for t in binance_tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        # Skip index/leverage tokens (contain numbers other than 1000 prefix or known patterns)
        base = _binance_base(sym)
        if not base or any(ch.isdigit() for ch in base):
            continue
        try:
            vol_usd = float(t.get("quoteVolume", 0) or 0)  # USDT volume
            price = float(t.get("lastPrice", 0) or 0)
            if vol_usd < _MIN_VOLUME_24H_USD or price <= 0:
                continue
            # Binance lastFundingRate is per-8h → convert to annualized decimal
            fund_8h = binance_funding.get(sym, 0.0)
            fund_ann = fund_8h * 3 * 365  # per-8h × 3 × 365 = annualized
            binance_candidates.append(
                {
                    "symbol": sym,
                    "exchange": "binance",
                    "base_asset": base,
                    "price": price,
                    "volume_24h_usd": vol_usd,
                    "vol_usd": vol_usd,
                    "funding_rate": fund_ann,
                    "bid": float(t.get("bidPrice", price) or price),
                    "ask": float(t.get("askPrice", price) or price),
                }
            )
        except (ValueError, TypeError):
            continue

    # Cap Binance at top N by volume to limit API calls in Step 2
    binance_candidates.sort(key=lambda x: x["volume_24h_usd"], reverse=True)
    result.extend(binance_candidates[:_MAX_STEP1_BINANCE])

    logger.info(
        f"[scanner] Step 1 (vol>${_MIN_VOLUME_24H_USD / 1e3:.0f}K): "
        f"{len(kraken_tickers)} kraken + {len(binance_tickers)} binance "
        f"→ {len(result)} candidates "
        f"({sum(1 for c in result if c['exchange'] == 'kraken')} kraken, "
        f"{sum(1 for c in result if c['exchange'] == 'binance')} binance)"
    )
    return result


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — MULTI-SETUP FILTER (parallel)
# ══════════════════════════════════════════════════════════════════════════════


def _evaluate_one_symbol(c: Dict) -> List[Dict]:
    """
    Fetch klines for one symbol, evaluate all 5 sub-filters, return
    a list of (direction, setup) candidates that passed (0, 1, or 2 entries).

    All sub-filters are independent — any passing sends that direction through.
    Multiple setups for the same direction are merged into one candidate with
    scan_setups listing all triggered setups.
    """
    sym = c["symbol"]
    exchange = c["exchange"]
    price = c["price"]
    fund_ann = c["funding_rate"]

    try:
        # Fetch 15m klines (60 bars ≈ 15h; needed for KST which requires ~55 bars)
        if exchange == "kraken":
            klines = _kraken_klines(sym, "15m", 65)
        elif exchange == "hyperliquid":
            klines = _hl_klines(sym, "15m", 65)
        else:
            klines = _binance_klines(sym, "15m", 65)

        if len(klines) < 22:  # need at least 22 bars for ADX(14)
            logger.debug(f"[scanner] {sym}: only {len(klines)} bars — skip")
            return []

        opens = [k[0] for k in klines]
        highs = [k[1] for k in klines]
        lows = [k[2] for k in klines]
        closes = [k[3] for k in klines]
        vols = [k[4] for k in klines]

        # Drop incomplete current bar (volume < 10% of prior bar)
        if len(vols) >= 2 and vols[-2] > 0 and vols[-1] / vols[-2] < 0.10:
            opens = opens[:-1]
            highs = highs[:-1]
            lows = lows[:-1]
            closes = closes[:-1]
            vols = vols[:-1]

        if len(closes) < 20:
            return []

        # ── Common indicators ─────────────────────────────────────────────────
        adx = _calc_adx(highs, lows, closes, 14)
        vs = _calc_vol_spike(vols, 20)
        atr = _calc_atr(highs, lows, closes, 14)

        bars_1h = min(4, len(closes) - 1)
        pm_1h = abs(closes[-1] - closes[-bars_1h]) / (closes[-bars_1h] + 1e-9) * 100

        bars_4h = min(16, len(closes) - 1)
        pm_4h = (closes[-1] - closes[-bars_4h]) / (closes[-bars_4h] + 1e-9) * 100

        # KST
        kst_val, kst_sig, kst_bull, kst_bear = _calc_kst(closes)

        # SuperTrend
        st_dir, st_up, st_down = _calc_supertrend(highs, lows, closes, 10, 3.0)

        # VWAP (session VWAP from all available bars)
        vwap = _calc_vwap(highs, lows, closes, vols)
        vwap_disp = (closes[-1] - vwap) / (vwap + 1e-9) * 100

        # Per-8h funding rate (undo annualization for ranging/funding checks)
        fund_8h = fund_ann / (3 * 365) if (3 * 365) > 0 else 0.0

        # ── Sub-filter evaluation ─────────────────────────────────────────────
        # direction → set of setups that fired for that direction
        fired: Dict[str, set] = {"LONG": set(), "SHORT": set()}

        # A. Momentum
        activity = (vs >= _MIN_VOL_SPIKE) or (pm_1h >= _MIN_PRICE_MOVE_1H)
        if activity and adx >= _MIN_ADX_MOMENTUM:
            direction = (
                "LONG" if (closes[-1] > closes[-min(4, len(closes) - 1)]) else "SHORT"
            )
            fired[direction].add("momentum")

        # B. KST cross
        if adx >= _MIN_ADX_MOMENTUM:
            if kst_bull:
                fired["LONG"].add("kst_cross")
            if kst_bear:
                fired["SHORT"].add("kst_cross")

        # C. SuperTrend cross (only on confirmed flip this bar)
        if st_up:
            fired["LONG"].add("supertrend")
        if st_down:
            fired["SHORT"].add("supertrend")

        # D. Ranging / mean-reversion (ADX confirms ranging regime)
        if adx < _MAX_ADX_RANGING and abs(vwap_disp) >= _MIN_VWAP_DISP_PCT:
            if vwap_disp < 0:  # price below VWAP → oversold → LONG reversion
                fired["LONG"].add("ranging_mr")
            elif vwap_disp > 0:  # price above VWAP → overbought → SHORT reversion
                fired["SHORT"].add("ranging_mr")

        # E. Funding collection (size conservatively; collect by holding)
        if abs(fund_8h) >= _MIN_FUNDING_COLLECT:
            # Negative funding → longs paid by shorts → LONG collects
            # Positive funding → shorts paid by longs → SHORT collects
            if fund_8h < 0:
                fired["LONG"].add("funding_collect")
            else:
                fired["SHORT"].add("funding_collect")

        if not fired["LONG"] and not fired["SHORT"]:
            return []

        # ── Build one candidate per direction that fired ──────────────────────
        results = []
        for direction, setups in fired.items():
            if not setups:
                continue

            # primary_setup = highest-priority setup in the triggered set
            primary = next((s for s in _SETUP_PRIORITY if s in setups), list(setups)[0])

            # stop/target from ATR (1.5× stop, 3× target → 2:1 R:R minimum)
            stop_pct = (atr * 1.5) / (price + 1e-9) * 100
            target_pct = (atr * 3.0) / (price + 1e-9) * 100

            candidate = {
                **c,  # carry forward exchange, base_asset, funding_rate, etc.
                "direction": direction,
                "scan_setups": sorted(setups),
                "primary_setup": primary,
                "vol_spike": round(vs, 3),
                "price_move_1h_pct": round(pm_1h, 3),
                "price_move_4h_pct": round(pm_4h, 3),
                "adx_15m": round(adx, 1),
                "atr_15m": round(atr, 8),
                "stop_pct": round(stop_pct, 3),
                "target_pct": round(target_pct, 3),
                "kst_value": round(kst_val, 4),
                "kst_signal": round(kst_sig, 4),
                "supertrend_dir": st_dir,
                "vwap": round(vwap, 8),
                "vwap_disp_pct": round(vwap_disp, 3),
            }
            results.append(candidate)

        return results

    except Exception as e:
        logger.debug(f"[scanner] step2 error {sym}: {e}")
        return []


def _step2_multi_setup(candidates: List[Dict]) -> List[Dict]:
    """Run all 5 sub-filters in parallel. Any sub-filter hit passes."""
    all_results: List[Dict] = []
    with ThreadPoolExecutor(max_workers=_PARALLEL_WORKERS) as pool:
        futures = {pool.submit(_evaluate_one_symbol, c): c for c in candidates}
        for future in as_completed(futures, timeout=90):
            try:
                result = future.result()
                if result:
                    all_results.extend(result)
            except Exception as e:
                logger.debug(f"[scanner] step2 future error: {e}")
    return all_results


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — LIQUIDITY (OB depth + spread)
# ══════════════════════════════════════════════════════════════════════════════


def _check_ob_one(c: Dict) -> Optional[Dict]:
    """Fetch OB for one candidate. Returns updated candidate or None on rejection."""
    sym = c["symbol"]
    exchange = c["exchange"]
    try:
        if exchange == "kraken":
            ob = _kraken_ob(sym)
        elif exchange == "hyperliquid":
            ob = _hl_ob(sym)
        else:
            ob = _binance_ob(sym)
        bids = ob.get("bids", [])
        asks = ob.get("asks", [])
        if not bids or not asks:
            logger.debug(f"[scanner] step3 {sym}: empty OB — rejected")
            return None
        # Both exchanges: bids ascending (best bid at [-1]), asks ascending (best ask at [0])
        near_bids = bids[-10:]
        bid_depth = sum(float(b[0]) * float(b[1]) for b in near_bids)
        ask_depth = sum(float(a[0]) * float(a[1]) for a in asks[:10])
        best_bid = float(bids[-1][0])
        best_ask = float(asks[0][0])
        mid = (best_bid + best_ask) / 2.0
        spread_pct = (best_ask - best_bid) / (mid + 1e-9) * 100
        if (
            bid_depth >= _MIN_OB_DEPTH_USD
            and ask_depth >= _MIN_OB_DEPTH_USD
            and spread_pct <= _MAX_SPREAD_PCT
        ):
            c.update(
                {
                    "bid_depth_usd": round(bid_depth, 0),
                    "ask_depth_usd": round(ask_depth, 0),
                    "spread_pct": round(spread_pct, 4),
                }
            )
            return c
        logger.debug(
            f"[scanner] step3 {sym}: bid={bid_depth:.0f} ask={ask_depth:.0f} "
            f"spread={spread_pct:.4f}% — rejected"
        )
        return None
    except Exception as e:
        logger.debug(f"[scanner] step3 error {sym}: {e} — rejected")
        return None


def _step3_liquidity(candidates: List[Dict]) -> List[Dict]:
    """Parallel OB depth check. Fail-closed: empty OB → rejected."""
    passed = []
    with ThreadPoolExecutor(max_workers=_PARALLEL_WORKERS) as pool:
        futures = {pool.submit(_check_ob_one, c): c for c in candidates}
        for future in as_completed(futures, timeout=60):
            try:
                result = future.result()
                if result is not None:
                    passed.append(result)
            except Exception:
                pass
    return passed


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — EXPECTED VALUE
# ══════════════════════════════════════════════════════════════════════════════


def _step4_expected_value(
    candidates: List[Dict], account_balance: float = 5_000.0, risk_pct: float = 0.015
) -> List[Dict]:
    """
    EV = (0.52 × net_win × position_usd) − (0.48 × net_loss × position_usd)
    Uses per-candidate stop_pct/target_pct from ATR (set in Step 2).
    Minimum EV ≥ _MIN_EXPECTED_PROFIT.

    funding_rate field is annualized decimal; divide by (365×3) for per-8h,
    multiply by _FUNDING_HOLD_PERIODS for expected hold cost.
    """
    passed = []
    for c in candidates:
        try:
            price = c["price"]
            stop_pct = c.get("stop_pct", 0.5) / 100  # already %, convert to fraction
            target_pct = c.get("target_pct", 1.0) / 100

            if stop_pct <= 0:
                continue

            dollar_risk = account_balance * risk_pct
            position_usd = dollar_risk / stop_pct
            fee_pct = _ROUND_TRIP_FEE_PCT

            # funding_rate is annualized decimal → per-8h
            fund_ann = abs(c.get("funding_rate", 0.0))
            fund_per8h = fund_ann / (365 * 3)
            fund_cost = fund_per8h * _FUNDING_HOLD_PERIODS

            net_win = target_pct - fee_pct - max(0.0, fund_cost)
            net_loss = stop_pct + fee_pct
            ev = (0.52 * net_win * position_usd) - (0.48 * net_loss * position_usd)

            if ev >= _MIN_EXPECTED_PROFIT:
                c.update(
                    {
                        "expected_profit": round(ev, 2),
                        "funding_cost_pct": round(fund_cost * 100, 4),
                    }
                )
                passed.append(c)
            else:
                logger.debug(
                    f"[scanner] step4 {c['symbol']} {c['direction']}: "
                    f"ev=${ev:.2f} < ${_MIN_EXPECTED_PROFIT} — rejected"
                )
        except Exception as e:
            logger.debug(f"[scanner] step4 error {c.get('symbol')}: {e} — rejected")
    return passed


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — CORRELATION (pass-through; real check in risk_engine.py)
# ══════════════════════════════════════════════════════════════════════════════


def _step5_correlation(
    candidates: List[Dict], open_positions: Optional[List[str]] = None
) -> List[Dict]:
    for c in candidates:
        c.setdefault("correlation_penalty", 1.0)
    return candidates


# ══════════════════════════════════════════════════════════════════════════════
# STEP 6 — REGIME FILTER
# ══════════════════════════════════════════════════════════════════════════════


def _step6_regime_filter(candidates: List[Dict], regime: str = "UNKNOWN") -> List[Dict]:
    """
    UNKNOWN: all pass.
    HIGH_VOL: require vol_spike >= 1.5 (momentum setups only; ranging still passes).
    RANGING: block momentum setups with ADX > 30; ranging_mr always passes.
    TRENDING_UP/DOWN: counter-trend candidates get 0.80 regime_penalty.
    """
    if regime == "UNKNOWN":
        for c in candidates:
            c.setdefault("regime_penalty", 1.0)
        return candidates

    passed = []
    for c in candidates:
        setups = c.get("scan_setups", [])
        adx = c.get("adx_15m", 25)
        vs = c.get("vol_spike", 1.0)
        dirn = c.get("direction", "LONG")

        is_ranging = "ranging_mr" in setups
        is_funding = "funding_collect" in setups
        is_momentum = bool({"momentum", "kst_cross", "supertrend"} & set(setups))

        if regime == "HIGH_VOL":
            # Ranging and funding setups pass regardless; momentum needs high vol
            if is_momentum and not is_ranging and not is_funding:
                if vs < 1.5:
                    continue

        elif regime == "RANGING":
            # Block pure momentum with strong trend; MR + funding always pass
            if is_momentum and not is_ranging and not is_funding and adx > 30:
                continue

        elif regime in ("TRENDING_UP", "TRENDING_DOWN"):
            counter = (regime == "TRENDING_UP" and dirn == "SHORT") or (
                regime == "TRENDING_DOWN" and dirn == "LONG"
            )
            # Only penalise; don't block (signal engine may still enter)
            c["regime_penalty"] = 0.80 if counter else 1.0

        c.setdefault("regime_penalty", 1.0)
        passed.append(c)

    return passed


# ══════════════════════════════════════════════════════════════════════════════
# STEP 7 — DEDUPLICATE + RANK + TOP N
# ══════════════════════════════════════════════════════════════════════════════


def _step7_rank_and_top(candidates: List[Dict], n: int = _TOP_N) -> List[Dict]:
    """
    Deduplication: if same base_asset + direction + primary_setup appears on both
    exchanges, keep the higher-EV candidate.
    Then rank by expected_profit descending, return top N.
    Strip large intermediate fields before returning.
    """
    # Dedup: key = (base_asset, direction, primary_setup)
    best: Dict[tuple, Dict] = {}
    for c in candidates:
        key = (
            c.get("base_asset", c["symbol"]),
            c["direction"],
            c.get("primary_setup", ""),
        )
        existing = best.get(key)
        if existing is None or c.get("expected_profit", 0) > existing.get(
            "expected_profit", 0
        ):
            best[key] = c

    ranked = sorted(
        best.values(), key=lambda x: x.get("expected_profit", 0), reverse=True
    )
    _drop = {"closes_15m", "highs_15m", "lows_15m", "vols_15m"}
    for c in ranked:
        for k in _drop:
            c.pop(k, None)
    return ranked[:n]


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════


def scan(
    open_positions: Optional[List[str]] = None,
    regime: str = "UNKNOWN",
    account_balance: float = 5_000.0,
    force: bool = False,
) -> List[Dict]:
    """
    Full 7-step multi-exchange scanner pipeline.

    Returns up to 50 candidate dicts sorted by expected_profit descending.
    All backward-compatible with v10_runner.py field expectations.
    Returns [] on failure — scheduler sits idle this cycle.

    force=True bypasses the 300s cache (used by dashboard manual scan button).
    """
    global _last_scan_ts, _last_candidates

    with _lock:
        if not force and time.time() - _last_scan_ts < _CACHE_TTL:
            return _last_candidates

    t_start = time.time()
    logger.info(
        "[scanner] Starting multi-exchange scan (Kraken + Binance + Hyperliquid)..."
    )

    try:
        # ── Fetch exchange data (3 calls upfront) ─────────────────────────────
        kraken_raw = _kraken_tickers()
        binance_raw = _binance_tickers()
        binance_fund = _binance_funding_all() if binance_raw else {}
        hl_raw = _hl_meta_and_ctxs()

        # ── Step 1: Universe ──────────────────────────────────────────────────
        universe = _step1_universe(kraken_raw, binance_raw, binance_fund)
        if hl_raw:
            hl_capped = sorted(hl_raw, key=lambda x: x["volume_24h_usd"], reverse=True)[
                :_MAX_STEP1_HYPERLIQUID
            ]
            universe.extend(hl_capped)
            logger.info(
                f"[scanner] Hyperliquid: {len(hl_raw)} eligible → {len(hl_capped)} added to universe"
            )
        if not universe:
            logger.warning("[scanner] No symbols passed Step 1 — scan idle")
            return []

        # ── Step 2: Multi-setup filter (parallel) ────────────────────────────
        step2 = _step2_multi_setup(universe)
        n_kraken = sum(1 for c in step2 if c.get("exchange") == "kraken")
        n_binance = sum(1 for c in step2 if c.get("exchange") == "binance")
        n_hl = sum(1 for c in step2 if c.get("exchange") == "hyperliquid")
        setup_counts = {}
        for c in step2:
            for s in c.get("scan_setups", []):
                setup_counts[s] = setup_counts.get(s, 0) + 1
        logger.info(
            f"[scanner] Step 2 (multi-setup): {len(universe)} → {len(step2)} "
            f"({n_kraken} kraken, {n_binance} binance, {n_hl} hyperliquid) "
            f"setups={setup_counts}"
        )
        if not step2:
            return []

        # ── Step 3: Liquidity (parallel OB) ──────────────────────────────────
        step3 = _step3_liquidity(step2)
        logger.info(f"[scanner] Step 3 (liquidity): {len(step2)} → {len(step3)}")

        # ── Step 4: Expected value ────────────────────────────────────────────
        step4 = _step4_expected_value(step3, account_balance)
        logger.info(
            f"[scanner] Step 4 (EV≥${_MIN_EXPECTED_PROFIT}): "
            f"{len(step3)} → {len(step4)}"
        )

        # ── Step 5–6: Correlation + Regime ───────────────────────────────────
        step5 = _step5_correlation(step4, open_positions)
        step6 = _step6_regime_filter(step5, regime)
        logger.info(f"[scanner] Step 6 (regime={regime}): {len(step5)} → {len(step6)}")

        # ── Step 7: Dedup + rank + top 50 ────────────────────────────────────
        final = _step7_rank_and_top(step6)
        elapsed = time.time() - t_start
        logger.info(f"[scanner] Complete: {len(final)} candidates in {elapsed:.1f}s")

        for c in final:
            logger.info(
                f"[scanner] → {c['symbol']} {c['direction']} "
                f"[{','.join(c.get('scan_setups', []))}] "
                f"spike={c.get('vol_spike', 0):.2f} "
                f"adx={c.get('adx_15m', 0):.0f} "
                f"ev=${c.get('expected_profit', 0):.2f} "
                f"funding={c.get('funding_rate', 0) * 100:.4f}% "
                f"({c.get('exchange', '?')})"
            )

        with _lock:
            _last_scan_ts = time.time()
            _last_candidates = final

        return final

    except Exception as e:
        logger.error(f"[scanner] Fatal error: {e}", exc_info=True)
        return []


def get_last_candidates() -> List[Dict]:
    return _last_candidates


def get_scan_stats() -> Dict:
    return {
        "last_scan_ts": _last_scan_ts,
        "last_scan_age_s": round(time.time() - _last_scan_ts, 0),
        "candidate_count": len(_last_candidates),
        "exchanges": list({c.get("exchange", "?") for c in _last_candidates}),
        "candidates": [
            {
                "symbol": c["symbol"],
                "exchange": c.get("exchange", "?"),
                "base_asset": c.get("base_asset", ""),
                "direction": c.get("direction", "?"),
                "scan_setups": c.get("scan_setups", []),
                "primary_setup": c.get("primary_setup", ""),
                "vol_spike": c.get("vol_spike", 0),
                "adx": c.get("adx_15m", 0),
                "ev": c.get("expected_profit", 0),
                "funding": c.get("funding_rate", 0),
            }
            for c in _last_candidates
        ],
    }
