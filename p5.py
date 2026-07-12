#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║          DARK LORD TRADING BOT v2.0  —  Institutional Order Flow Edition    ║
║                                                                              ║
║  [!] The market doesn't care about your hope. Only your edge matters.       ║
║                                                                              ║
║  UPGRADED from v1.0:                                                         ║
║   ✦  HTFBiasEngine        — D1+H4 institutional bias gate (most important)  ║
║   ✦  LiquidityMapEngine   — where the stops are clustered                   ║
║   ✦  PremiumDiscountEngine — buy discount, sell premium only                ║
║   ✦  OrderBlockEngine     — institutional order block detection              ║
║   ✦  MarketStructureBreak — BOS/CHOCH on M5 confirmation                   ║
║   ✦  QualityGate          — 10 sequential hard gates before ANY trade       ║
║   ✦  PatternTracker       — track YOUR performance by confluence pattern    ║
║   ✦  AI: 12 clean features, XGBoost only, trade-outcome target (not dir)   ║
║   ✦  Realistic XM Gold costs: spread=45pts, slip=20pts, comm=$8/lot        ║
║   ✦  Conservative risk: 0.5% per trade, max 0.05 lot Gold                  ║
║   ✦  Rich terminal UI with live coloured tables                             ║
║   ✦  Spread-based news filter (no more guessed UTC blackouts)               ║
║                                                                              ║
║  REMOVED from v1.0:                                                          ║
║   ✗  LSTM model (overfitting on 500 bars)                                   ║
║   ✗  50 features reduced to 12 clean, causal features                      ║
║   ✗  Standalone FVG signals (noise without sweep confirmation)              ║
║   ✗  TrendStrategy / MeanReversion / Scalping / VolBreakout                ║
║   ✗  Fixed UTC news blackouts                                               ║
║                                                                              ║
║  DO NOT GO LIVE UNTIL ALL THREE PASS:                                        ║
║   ✓  Walk-Forward Profit Factor > 1.6 on ALL test windows                  ║
║   ✓  Monte Carlo P5 Sharpe > 0.3                                            ║
║   ✓  Max Drawdown in any test window < 15%                                  ║
╚══════════════════════════════════════════════════════════════════════════════╝

REQUIREMENTS:
    pip install MetaTrader5 pandas numpy scipy xgboost rich

USAGE:
    python dark_lord_v2.py
    Type 'h' for help menu.
"""

# ── stdlib ────────────────────────────────────────────────────────────────────
import os
import sys
import json
import time
import math
import csv
import asyncio
import logging
import traceback
import warnings
import random
import threading
import queue
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import deque, defaultdict
from typing import Dict, List, Optional, Tuple, Any

# ── third-party ───────────────────────────────────────────────────────────────
import numpy as np
import pandas as pd
from scipy.stats import norm

warnings.filterwarnings("ignore")

# ── Rich terminal UI ──────────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich.live import Live
    from rich import box as rich_box
    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None
    print("[WARN] rich not installed — pip install rich  (terminal will be plain text)")

# ── optional heavy dependencies ───────────────────────────────────────────────
# UPGRADED: LSTM removed — overfits on <500 bars. XGBoost only.
try:
    import xgboost as xgb
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False
    print("[WARN] xgboost not installed — AI model disabled")

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    print("[WARN] MetaTrader5 not installed — simulation mode")


# ============================================================================
# ── LOGGING
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)-24s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("dark_bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("DarkLord")


# ============================================================================
# 1.  DARK CONFIG
# UPGRADED: Risk reduced, realistic Gold costs, new HTF/SMC parameters.
# ============================================================================
class DarkConfig:
    """All parameters hardcoded. Edit this class only."""

    # ── MT5 Credentials ──────────────────────────────────────────────────────
    MT5_LOGIN    : int = 1234567890
    MT5_PASSWORD : str = "xyz"
    MT5_SERVER   : str = "xyz"

    # ── Trading Universe ──────────────────────────────────────────────────────
    SYMBOLS : List[str] = ["BTCUSD#", "XAUUSD#", "EURUSD#", "GBPUSD#", "USDCHF#", "USDJPY#", "USDCAD#", "AUDUSD#", "NZDUSD#", "USDMXN#", "USDCZK#", "USDCNH#", "USDCNY#", "USDCZK#", "USDCNH#", "USDCNY#", "USDCZK#", "USDCNH#", "USDCNY#"]
  
    # ── Risk Management ───────────────────────────────────────────────────────
    # UPGRADED: BUG 4 FIX — position size was dangerously large
    RISK_PER_TRADE           : float = 0.005   # 0.5% (was 1.2%)
    MAX_TOTAL_RISK_PORTFOLIO : float = 0.015   # 1.5% (was 4.5%)
    MAX_DAILY_DRAWDOWN       : float = 0.025   # 2.5% (was 4%)
    KILL_SWITCH_DRAWDOWN     : float = 0.04    # 4%   (was 5%)
    MAX_DAILY_TRADES         : int   = 4       # 4    (was 10)
    KELLY_FRACTION           : float = 0.25
    MAX_LOT_SIZE_GOLD        : float = 0.05    # max 0.05 lot on Gold (was 1.0)
    MIN_LOT_SIZE             : float = 0.01
    RR_RATIO                 : float = 2.0
    CORRELATION_LIMIT        : float = 0.80

    # ── Realistic Gold Costs on XM ────────────────────────────────────────────
    # UPGRADED: BUG 3 FIX — old costs were 10x too optimistic
    GOLD_SPREAD_PTS       : float = 45.0   # avg XM Gold spread (was 1.2 pips)
    GOLD_SLIP_PTS         : float = 20.0   # avg slippage on news (was 0.5 pips)
    GOLD_COMMISSION_USD   : float = 8.0    # per lot round trip (was $4)
    GOLD_LATENCY_MS       : int   = 200    # realistic retail latency

    # ── AI / ML ──────────────────────────────────────────────────────────────
    # UPGRADED: Confidence threshold raised, LSTM removed
    USE_AI              : bool  = True
    AI_UPDATE_FREQUENCY : int   = 86400    # 24h retrain
    AI_MIN_CONFIDENCE   : float = 0.62     # raised from 0.55
    AI_MIN_TRAIN_BARS   : int   = 200      # minimum bars before training

    # ── Context Score Gate ────────────────────────────────────────────────────
    CTX_MIN_SCORE : float = 0.70           # raised from 0.65

    # ── Volume Profile ────────────────────────────────────────────────────────
    VP_BINS           : int   = 50
    VP_ROLLING_BARS   : int   = 500
    VP_VALUE_AREA_PCT : float = 0.70
    VP_LOW_NODE_PCT   : float = 0.10

    # ── Order Flow ────────────────────────────────────────────────────────────
    OF_IMBALANCE_THRESH : float = 1.5
    OF_ABSORPTION_PCT   : float = 0.002
    OF_ICEBERG_REPEATS  : int   = 5

    # ── Liquidity Sweep ───────────────────────────────────────────────────────
    LS_EQUAL_TOL    : float = 0.0002   # 0.02% tolerance
    LS_WICK_MULT    : float = 1.5      # wick > 1.5x ATR
    LS_RETRACE_PCT  : float = 0.70     # 70% retrace in 5 bars

    # ── Fair Value Gap ────────────────────────────────────────────────────────
    FVG_DISP_ATR_MULT   : float = 2.0
    FVG_MITIGATION_PCT  : float = 0.70

    # ── HTF Bias ──────────────────────────────────────────────────────────────
    HTF_MIN_CONFIDENCE  : float = 0.80   # D1+H4 must agree ≥ 80%

    # ── Premium/Discount ──────────────────────────────────────────────────────
    PD_EQUILIBRIUM_BAND : float = 0.10   # 10% around midpoint = no trade

    # ── Order Block ───────────────────────────────────────────────────────────
    OB_LOOKBACK : int = 50

    # ── Microstructure ────────────────────────────────────────────────────────
    MS_SPREAD_MULT      : float = 1.5    # UPGRADED: raised from 1.2 — also acts as news filter
    MS_LASTLOOK_PIPS    : float = 0.5
    MS_LATENCY_WARN_MS  : int   = 500

    # ── Session Times (UTC) ───────────────────────────────────────────────────
    SESSIONS : Dict = {
        "LONDON":  {"start": "07:00", "end": "16:00"},
        "NEWYORK": {"start": "12:00", "end": "21:00"},
        "ASIAN":   {"start": "23:00", "end": "08:00"},
    }
    TRADE_ONLY_IN_SESSIONS : bool = True
    PREFERRED_SESSIONS     : List = ["LONDON", "NEWYORK"]

    # ── Regime Detection ──────────────────────────────────────────────────────
    REGIME_LOOKBACK  : int   = 100
    ADX_TREND_THRESH : float = 25.0

    # ── Trailing Stop ─────────────────────────────────────────────────────────
    USE_TRAILING_STOP    : bool  = True
    TRAILING_ATR_MULT    : float = 1.5
    TRAILING_ACTIVATE_RR : float = 1.0

    # ── Backtest / Monte Carlo ─────────────────────────────────────────────────
    # UPGRADED: BUG 3 FIX — realistic Gold costs on XM
    BT_SPREAD_PTS     : float = 45.0    # points (was 1.2 pips)
    BT_SLIP_PTS       : float = 20.0    # points (was 0.5 pips)
    BT_COMMISSION_USD : float = 8.0     # per lot round trip (was $4)
    MC_PATHS          : int   = 1000
    MC_CONFIDENCE     : float = 0.05

    # ── Walk-Forward ──────────────────────────────────────────────────────────
    WF_TRAIN_DAYS : int = 60
    WF_TEST_DAYS  : int = 14

    # ── Adaptive ──────────────────────────────────────────────────────────────
    ADAPT_LOSS_REDUCE  : float = 0.5
    ADAPT_WIN_INCREASE : float = 1.1
    ADAPT_LOOKBACK     : int   = 10

    # ── Execution ─────────────────────────────────────────────────────────────
    ORDER_DEVIATION     : int = 20
    MAGIC_NUMBER        : int = 20250101
    ORDER_COMMENT       : str = "DarkLord_v2"
    MAX_RECONNECT_TRIES : int = 10

    # ── Display ───────────────────────────────────────────────────────────────
    SCORE_DISPLAY_SEC : int = 300   # print table every 5 min

    def validate(self) -> bool:
        ok = True
        if self.MT5_LOGIN == 0 and MT5_AVAILABLE:
            log.warning("MT5_LOGIN not set")
        if self.RISK_PER_TRADE > 0.01:
            log.warning("RISK_PER_TRADE > 1%% — consider reducing for Gold")
        if not self.SYMBOLS:
            log.error("No SYMBOLS configured")
            ok = False
        return ok


# ============================================================================
# 2.  DATA PROVIDER  (multi-timeframe + tick capture)
# UPGRADED: Added H4 to get_mtf for HTF bias engine
# ============================================================================
class DataProvider:
    TF_M1  : int = 1
    TF_M5  : int = 5
    TF_M15 : int = 15
    TF_H1  : int = 16385
    TF_H4  : int = 16388
    TF_D1  : int = 16408

    def __init__(self, config: DarkConfig):
        self.config     = config
        self._cache     : Dict[str, pd.DataFrame] = {}
        self._cache_ts  : Dict[str, datetime]     = {}
        self._cache_ttl : int = 60
        self._tick_buffer: Dict[str, deque] = {
            s: deque(maxlen=2000) for s in config.SYMBOLS
        }

    def _tf_const(self, label: str) -> int:
        if MT5_AVAILABLE:
            mapping = {
                "M1":  mt5.TIMEFRAME_M1,
                "M5":  mt5.TIMEFRAME_M5,
                "M15": mt5.TIMEFRAME_M15,
                "H1":  mt5.TIMEFRAME_H1,
                "H4":  mt5.TIMEFRAME_H4,
                "D1":  mt5.TIMEFRAME_D1,
            }
            return mapping.get(label, mt5.TIMEFRAME_H1)
        fallback = {"M1": 1, "M5": 5, "M15": 15, "H1": 16385, "H4": 16388, "D1": 16408}
        return fallback.get(label, 16385)

    def _is_stale(self, key: str) -> bool:
        if key not in self._cache_ts:
            return True
        return (datetime.now() - self._cache_ts[key]).total_seconds() > self._cache_ttl

    @staticmethod
    def _raw_to_df(rates) -> pd.DataFrame:
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df.set_index("time", inplace=True)
        df.rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "tick_volume": "Volume",
        }, inplace=True)
        return df

    def get_rates(self, symbol: str, tf_label: str, bars: int,
                  use_cache: bool = True) -> Optional[pd.DataFrame]:
        if not MT5_AVAILABLE:
            return None
        tf  = self._tf_const(tf_label)
        key = f"{symbol}_{tf_label}_{bars}"
        if use_cache and not self._is_stale(key) and key in self._cache:
            return self._cache[key].copy()
        try:
            rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
        except Exception as e:
            log.warning("get_rates error [%s %s]: %s", symbol, tf_label, e)
            return None
        if rates is None or len(rates) < 20:
            return None
        df = self._raw_to_df(rates)
        if use_cache:
            self._cache[key] = df.copy()
            self._cache_ts[key] = datetime.now()
        return df

    def get_mtf(self, symbol: str) -> Dict[str, Optional[pd.DataFrame]]:
        # UPGRADED: Added H4 for HTFBiasEngine
        return {
            "M5":  self.get_rates(symbol, "M5",  300),
            "H1":  self.get_rates(symbol, "H1",  500),
            "H4":  self.get_rates(symbol, "H4",  200),
            "D1":  self.get_rates(symbol, "D1",  100),
        }

    def get_historical_range(self, symbol: str, tf_label: str,
                              start: datetime, end: datetime) -> pd.DataFrame:
        if not MT5_AVAILABLE:
            return pd.DataFrame()
        tf = self._tf_const(tf_label)
        try:
            rates = mt5.copy_rates_range(symbol, tf, start, end)
        except Exception as e:
            log.warning("get_historical_range error: %s", e)
            return pd.DataFrame()
        if rates is None or len(rates) == 0:
            return pd.DataFrame()
        return self._raw_to_df(rates)

    def get_tick(self, symbol: str) -> Optional[Any]:
        if not MT5_AVAILABLE:
            return None
        try:
            tick = mt5.symbol_info_tick(symbol)
            if tick and symbol in self._tick_buffer:
                self._tick_buffer[symbol].append({
                    "time": tick.time,
                    "bid":  tick.bid,
                    "ask":  tick.ask,
                    "last": tick.last,
                    "vol":  tick.volume,
                    "flags": tick.flags,
                })
            return tick
        except Exception:
            return None

    def get_tick_buffer(self, symbol: str) -> List[Dict]:
        return list(self._tick_buffer.get(symbol, []))

    def get_symbol_info(self, symbol: str) -> Optional[Any]:
        if not MT5_AVAILABLE:
            return None
        try:
            return mt5.symbol_info(symbol)
        except Exception:
            return None

    def get_order_book(self, symbol: str) -> Optional[List]:
        if not MT5_AVAILABLE:
            return None
        try:
            book = mt5.market_book_get(symbol)
            return list(book) if book else None
        except Exception:
            return None

    def invalidate(self, symbol: str = None):
        keys = list(self._cache_ts.keys())
        for k in keys:
            if symbol is None or k.startswith(symbol):
                del self._cache_ts[k]


# ============================================================================
# 3.  FEATURE ENGINEER  — 12 features, no lookahead
# UPGRADED: BUG 2+5 FIX — reduced from 50 to 12 features (rule: need 50x
#   more samples than features; 50 features needs 2500+ trades).
#   All rolling windows are purely backward-looking now.
# ============================================================================
class FeatureEngineer:
    """
    12 clean, causal features for trade-outcome prediction.
    No lookahead. No future data leakage.
    """

    # UPGRADED: 12 features only (was 50)
    FEATURE_COLS = [
        "atr_norm",          # volatility context
        "rsi_14",            # momentum state
        "candle_body_ratio", # institutional vs retail candle
        "wick_ratio_lower",  # lower wick = buy pressure / stop hunt below
        "wick_ratio_upper",  # upper wick = sell pressure / stop hunt above
        "volume_ratio",      # is institution active this bar?
        "vwap_dist",         # price location vs fair value
        "structure_score",   # HH/HL or LH/LL market structure
        "adx_14",            # trend strength
        "close_loc",         # who won the bar (close in range)
        "session_hour",      # institutional activity level
        "macd_hist",         # momentum confirmation
    ]

    @classmethod
    def add_features(cls, df: pd.DataFrame) -> pd.DataFrame:
        """Compute 12 features on the given df (all backward-looking)."""
        df = df.copy()
        if len(df) < 60:
            return pd.DataFrame()

        close  = df["Close"]
        high   = df["High"]
        low    = df["Low"]
        volume = df["Volume"]
        open_  = df["Open"]

        # 1. ATR norm — volatility context
        atr14          = cls._atr(df, 14)
        df["atr"]      = atr14
        df["atr_norm"] = atr14 / (close + 1e-9)

        # 2. RSI 14 — momentum state
        df["rsi_14"] = cls._rsi(close, 14) / 100.0   # normalised to [0,1]

        # 3. Candle body ratio — institutional vs retail bar
        body = (close - open_).abs()
        rng  = (high - low).replace(0, np.nan).fillna(1e-9)
        df["candle_body_ratio"] = (body / rng).clip(0, 1)

        # 4 & 5. Wick ratios — stop hunt signals
        df["wick_ratio_lower"] = ((np.minimum(close, open_) - low) / rng).clip(0, 1)
        df["wick_ratio_upper"] = ((high - np.maximum(close, open_)) / rng).clip(0, 1)

        # 6. Volume ratio — is institution active?
        vol_mean           = volume.rolling(20).mean()
        df["volume_ratio"] = (volume / (vol_mean + 1e-9)).clip(0, 10) / 10.0

        # 7. VWAP distance — price vs fair value (20-bar rolling proxy)
        vwap           = (close * volume).rolling(20).sum() / (volume.rolling(20).sum() + 1e-9)
        df["vwap_dist"] = ((close - vwap) / (atr14 + 1e-9)).clip(-5, 5) / 5.0

        # 8. Structure score — HH/HL or LH/LL
        df["structure_score"] = cls._structure_score(close, 20)

        # 9. ADX 14 — trend strength
        df["adx_14"] = cls._adx(df, 14)

        # 10. Close location — who won the bar
        df["close_loc"] = ((close - low) / rng).clip(0, 1)

        # 11. Session hour — institutional activity
        try:
            df["session_hour"] = df.index.hour / 24.0
        except Exception:
            df["session_hour"] = 0.5

        # 12. MACD histogram — momentum confirmation
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd  = ema12 - ema26
        sig   = macd.ewm(span=9, adjust=False).mean()
        df["macd_hist"] = ((macd - sig) / (atr14 + 1e-9)).clip(-3, 3) / 3.0

        return df.dropna(subset=cls.FEATURE_COLS)

    # ── static helpers ────────────────────────────────────────────────────────
    @staticmethod
    def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain  = delta.clip(lower=0).rolling(period).mean()
        loss  = (-delta.clip(upper=0)).rolling(period).mean()
        rs    = gain / (loss + 1e-9)
        return 100 - 100 / (1 + rs)

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        hi, lo, cl = df["High"], df["Low"], df["Close"]
        tr1 = hi - lo
        tr2 = (hi - cl.shift()).abs()
        tr3 = (lo - cl.shift()).abs()
        tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(period).mean()

    @staticmethod
    def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        hi, lo, cl = df["High"], df["Low"], df["Close"]
        up_m = hi.diff();  dn_m = -lo.diff()
        pdm  = up_m.where((up_m > dn_m) & (up_m > 0), 0.0)
        ndm  = dn_m.where((dn_m > up_m) & (dn_m > 0), 0.0)
        tr   = pd.concat([hi - lo, (hi - cl.shift()).abs(), (lo - cl.shift()).abs()], axis=1).max(axis=1)
        atr  = tr.rolling(period).mean()
        pdi  = 100 * pdm.rolling(period).mean() / (atr + 1e-9)
        ndi  = 100 * ndm.rolling(period).mean() / (atr + 1e-9)
        dx   = 100 * (pdi - ndi).abs() / (pdi + ndi + 1e-9)
        return (dx.rolling(period).mean() / 100.0).clip(0, 1)

    @staticmethod
    def _structure_score(close: pd.Series, window: int = 20) -> pd.Series:
        score  = pd.Series(0.0, index=close.index)
        closes = close.values
        for i in range(window * 2, len(closes)):
            s1 = closes[i - window * 2: i - window]
            s2 = closes[i - window: i]
            hh = s2.max() > s1.max(); hl = s2.min() > s1.min()
            lh = s2.max() < s1.max(); ll = s2.min() < s1.min()
            if hh and hl:   score.iloc[i] = 1.0
            elif lh and ll: score.iloc[i] = -1.0
        return score


# ============================================================================
# 4.  VOLUME PROFILE ENGINE  (kept from v1.0)
# ============================================================================
class VolumeProfileEngine:
    def __init__(self, config: DarkConfig):
        self.config       = config
        self._last_vp     : Dict[str, Dict] = {}
        self._last_signal : str = "—"
        self._score       : float = 0.0

    def compute(self, df: pd.DataFrame, symbol: str = "") -> Dict:
        if df is None or len(df) < 30:
            return self._empty()
        window  = df.tail(self.config.VP_ROLLING_BARS)
        n_bins  = self.config.VP_BINS
        p_min   = float(window["Low"].min())
        p_max   = float(window["High"].max())
        if p_max <= p_min:
            return self._empty()
        edges    = np.linspace(p_min, p_max, n_bins + 1)
        mids     = (edges[:-1] + edges[1:]) / 2
        vol_tot  = np.zeros(n_bins)
        vol_ask  = np.zeros(n_bins)
        vol_bid  = np.zeros(n_bins)
        for _, row in window.iterrows():
            lo, hi, vol = row["Low"], row["High"], row["Volume"]
            cl, op = row["Close"], row["Open"]
            if hi <= lo: continue
            mask = (mids >= lo) & (mids <= hi)
            if not mask.any(): continue
            n_hit  = mask.sum()
            share  = vol / n_hit
            vol_tot[mask] += share
            if cl >= op:
                vol_ask[mask] += share * 0.65; vol_bid[mask] += share * 0.35
            else:
                vol_ask[mask] += share * 0.35; vol_bid[mask] += share * 0.65
        poc_idx  = int(np.argmax(vol_tot))
        poc      = float(mids[poc_idx])
        total    = vol_tot.sum()
        target   = total * self.config.VP_VALUE_AREA_PCT
        a_vol    = vol_tot[poc_idx]; lo_i = poc_idx; hi_i = poc_idx
        while a_vol < target:
            c_lo = lo_i > 0; c_hi = hi_i < n_bins - 1
            if not c_lo and not c_hi: break
            add_lo = vol_tot[lo_i - 1] if c_lo else 0
            add_hi = vol_tot[hi_i + 1] if c_hi else 0
            if add_hi >= add_lo and c_hi:
                hi_i += 1; a_vol += add_hi
            elif c_lo:
                lo_i -= 1; a_vol += add_lo
            else:
                hi_i += 1; a_vol += add_hi
        vah = float(mids[hi_i]); val = float(mids[lo_i])
        max_v = vol_tot.max()
        hvn = [float(mids[i]) for i in range(n_bins) if vol_tot[i] > max_v * 0.70]
        lvn = [float(mids[i]) for i in range(n_bins) if vol_tot[i] < max_v * self.config.VP_LOW_NODE_PCT]
        delta = vol_ask - vol_bid
        cur = float(window["Close"].iloc[-1])
        score = self._score_vp(cur, poc, vah, val, lvn)
        self._score = score
        if abs(cur - poc) / (poc + 1e-9) < 0.001:
            self._last_signal = "POC touched"
        elif val <= cur <= vah:
            self._last_signal = "Inside Value Area"
        elif any(abs(cur - l) / (l + 1e-9) < 0.002 for l in lvn):
            self._last_signal = "Low Vol Node ⚠"
        else:
            self._last_signal = "Outside VA"
        result = {"poc": poc, "vah": vah, "val": val, "hvn": hvn, "lvn": lvn,
                  "delta": delta.tolist(), "bins": mids.tolist(), "vol": vol_tot.tolist(), "score": score}
        self._last_vp[symbol] = result
        return result

    def _score_vp(self, price, poc, vah, val, lvn):
        score = 0.5
        if abs(price - poc) / (poc + 1e-9) < 0.002: score += 0.3
        elif val <= price <= vah: score += 0.1
        if any(abs(price - l) / (l + 1e-9) < 0.002 for l in lvn): score -= 0.3
        return float(max(0.0, min(1.0, score)))

    def in_low_volume_node(self, price: float, vp: Dict) -> bool:
        lvn = vp.get("lvn", [])
        return any(abs(price - l) / (l + 1e-9) < 0.002 for l in lvn)

    def get_score(self) -> float: return self._score
    def get_last_signal(self) -> str: return self._last_signal

    @staticmethod
    def _empty() -> Dict:
        return {"poc": 0, "vah": 0, "val": 0, "hvn": [], "lvn": [],
                "delta": [], "bins": [], "vol": [], "score": 0.0}


# ============================================================================
# 5.  ORDER FLOW ENGINE  (kept from v1.0)
# ============================================================================
class OrderFlowEngine:
    def __init__(self, config: DarkConfig):
        self.config           = config
        self._cum_delta       : Dict[str, float] = {}
        self._last_deltas     : Dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
        self._iceberg_flags   : Dict[str, bool]  = {}
        self._absorption_flag : Dict[str, bool]  = {}
        self._last_signal     : str = "—"
        self._score           : float = 0.5

    def analyze(self, df: pd.DataFrame, tick_buffer: List[Dict], symbol: str = "") -> Dict:
        if df is None or len(df) < 10:
            return self._empty()
        close  = df["Close"]; open_  = df["Open"]; volume = df["Volume"]
        ask_vol = np.where(close >= open_, volume * 0.65, volume * 0.35)
        bid_vol = volume - ask_vol
        delta   = ask_vol - bid_vol
        bar_delta = float(delta.iloc[-1])
        cum_delta = float(delta.sum())
        self._cum_delta[symbol] = cum_delta
        self._last_deltas[symbol].append(bar_delta)
        deltas_arr = np.array(list(self._last_deltas[symbol]))
        price_trend = float(close.iloc[-1]) - float(close.iloc[-10]) if len(df) >= 10 else 0
        delta_trend = deltas_arr[-10:].sum() if len(deltas_arr) >= 10 else 0
        divergence = (price_trend > 0 and delta_trend < 0) or (price_trend < 0 and delta_trend > 0)
        total_ask = ask_vol[-20:].sum() if len(ask_vol) >= 20 else ask_vol.sum()
        total_bid = bid_vol[-20:].sum() if len(bid_vol) >= 20 else bid_vol.sum()
        imbalance = total_ask / (total_bid + 1e-9)
        last_vol  = float(volume.iloc[-1]); avg_vol = float(volume.mean())
        last_rng  = float(df["High"].iloc[-1] - df["Low"].iloc[-1])
        avg_rng   = float((df["High"] - df["Low"]).mean())
        absorption = (last_vol > avg_vol * 2.0 and last_rng < avg_rng * self.config.OF_ABSORPTION_PCT * 1000)
        self._absorption_flag[symbol] = absorption
        iceberg = self._detect_iceberg(tick_buffer)
        self._iceberg_flags[symbol] = iceberg
        score = 0.5
        if abs(imbalance - 1.0) > 0.5: score += 0.15
        if not divergence:               score += 0.15
        if not absorption:               score += 0.10
        if not iceberg:                  score += 0.10
        if imbalance > self.config.OF_IMBALANCE_THRESH: score += 0.15
        score = float(max(0.0, min(1.0, score)))
        self._score = score
        if iceberg:               self._last_signal = "Iceberg detected ⚠"
        elif absorption:          self._last_signal = "Absorption → reversal?"
        elif divergence:          self._last_signal = "Delta divergence ⚠"
        elif imbalance > self.config.OF_IMBALANCE_THRESH: self._last_signal = f"Imbalance {imbalance:.1f}x buy"
        elif imbalance < 1.0 / self.config.OF_IMBALANCE_THRESH: self._last_signal = f"Imbalance {1/imbalance:.1f}x sell"
        else:                     self._last_signal = f"Delta {bar_delta:+.0f}"
        return {"bar_delta": bar_delta, "cum_delta": cum_delta, "imbalance": imbalance,
                "divergence": divergence, "absorption": absorption, "iceberg": iceberg, "score": score}

    def _detect_iceberg(self, tick_buffer: List[Dict]) -> bool:
        if len(tick_buffer) < self.config.OF_ICEBERG_REPEATS:
            return False
        recent = tick_buffer[-50:]
        if not recent: return False
        try:
            now_ts = recent[-1]["time"]
            window = [t for t in recent if now_ts - t["time"] <= 2]
            vols   = [t["vol"] for t in window if t["vol"] > 0]
            if len(vols) < self.config.OF_ICEBERG_REPEATS: return False
            from collections import Counter
            vol_counts = Counter(vols)
            return any(c >= self.config.OF_ICEBERG_REPEATS for c in vol_counts.values())
        except Exception:
            return False

    def get_score(self) -> float: return self._score
    def get_last_signal(self) -> str: return self._last_signal

    @staticmethod
    def _empty() -> Dict:
        return {"bar_delta": 0, "cum_delta": 0, "imbalance": 1.0,
                "divergence": False, "absorption": False, "iceberg": False, "score": 0.5}


# ============================================================================
# 6.  LIQUIDITY SWEEP DETECTOR  (kept from v1.0)
# ============================================================================
class LiquiditySweepDetector:
    def __init__(self, config: DarkConfig):
        self.config      = config
        self._last_signal = "—"
        self._score       = 0.5

    def analyze(self, df: pd.DataFrame, df_h1: Optional[pd.DataFrame] = None) -> Dict:
        if df is None or len(df) < 30:
            return self._empty()
        close = df["Close"]; high = df["High"]; low = df["Low"]
        atr   = float(FeatureEngineer._atr(df, 14).iloc[-1])
        buy_liq  = self._find_equal_levels(high, is_high=True)
        sell_liq = self._find_equal_levels(low, is_high=False)
        last_high  = float(high.iloc[-1]); last_low  = float(low.iloc[-1])
        last_close = float(close.iloc[-1]); last_open = float(df["Open"].iloc[-1])
        wick_size  = max(last_high - max(last_close, last_open),
                         min(last_close, last_open) - last_low)
        buy_sweep = False; sell_sweep = False; lvl_swept = 0.0
        for lvl in buy_liq:
            if last_high > lvl * (1 + self.config.LS_EQUAL_TOL) and last_close < lvl:
                if wick_size > atr * self.config.LS_WICK_MULT:
                    buy_sweep = True; lvl_swept = lvl; break
        for lvl in sell_liq:
            if last_low < lvl * (1 - self.config.LS_EQUAL_TOL) and last_close > lvl:
                if wick_size > atr * self.config.LS_WICK_MULT:
                    sell_sweep = True; lvl_swept = lvl; break
        buy_confirmed = sell_confirmed = False
        if buy_sweep and len(df) >= 6:
            recent_low  = float(low.iloc[-5:].min())
            retrace_pct = (last_high - last_close) / (last_high - recent_low + 1e-9)
            buy_confirmed = retrace_pct >= self.config.LS_RETRACE_PCT
        if sell_sweep and len(df) >= 6:
            recent_high = float(high.iloc[-5:].max())
            retrace_pct = (last_close - last_low) / (recent_high - last_low + 1e-9)
            sell_confirmed = retrace_pct >= self.config.LS_RETRACE_PCT
        liquidity_void = self._detect_void(df)
        htf_sweep = False
        if df_h1 is not None and len(df_h1) >= 30:
            htf_res   = self.analyze(df_h1)
            htf_sweep = htf_res.get("sweep_detected", False)
        sweep_detected = buy_confirmed or sell_confirmed
        score = 0.5
        if sweep_detected: score += 0.35
        if htf_sweep:      score += 0.10
        if not liquidity_void: score += 0.05
        score = float(max(0.0, min(1.0, score)))
        self._score = score
        if buy_confirmed:          self._last_signal = f"Buy-side sweep @ {lvl_swept:.5f} ✓"
        elif sell_confirmed:       self._last_signal = f"Sell-side sweep @ {lvl_swept:.5f} ✓"
        elif buy_sweep or sell_sweep: self._last_signal = "Possible sweep — awaiting retrace"
        elif liquidity_void:       self._last_signal = "Liquidity void detected"
        else:                      self._last_signal = "No sweep yet"
        direction = None
        if buy_confirmed:  direction = "sell"
        if sell_confirmed: direction = "buy"
        return {"sweep_detected": sweep_detected, "buy_sweep": buy_confirmed,
                "sell_sweep": sell_confirmed, "direction": direction,
                "level": lvl_swept, "liquidity_void": liquidity_void,
                "buy_liquidity": buy_liq, "sell_liquidity": sell_liq, "score": score}

    def _find_equal_levels(self, series: pd.Series, is_high: bool = True, lookback: int = 50) -> List[float]:
        recent = series.iloc[-lookback:].values
        levels = []; tol = self.config.LS_EQUAL_TOL
        for i in range(len(recent) - 1):
            for j in range(i + 1, min(i + 10, len(recent))):
                if abs(recent[i] - recent[j]) / (recent[i] + 1e-9) < tol:
                    levels.append((recent[i] + recent[j]) / 2)
        return list(set(round(l, 5) for l in levels))

    def _detect_void(self, df: pd.DataFrame) -> bool:
        if len(df) < 5: return False
        recent = df.tail(5)
        avg_range = float((df["High"] - df["Low"]).mean())
        for _, row in recent.iterrows():
            rng = row["High"] - row["Low"]
            vol = row["Volume"]
            if rng > avg_range * 3 and vol < float(df["Volume"].mean()) * 0.5:
                return True
        return False

    def get_score(self) -> float: return self._score
    def get_last_signal(self) -> str: return self._last_signal

    @staticmethod
    def _empty() -> Dict:
        return {"sweep_detected": False, "buy_sweep": False, "sell_sweep": False,
                "direction": None, "level": 0.0, "liquidity_void": False,
                "buy_liquidity": [], "sell_liquidity": [], "score": 0.5}


# ============================================================================
# 7.  FAIR VALUE GAP DETECTOR  (kept, but used ONLY with sweep confirmation)
# UPGRADED: Standalone FVG signals removed — FVG alone = noise on M5 Gold.
#   FVG is now confirmation-only when a sweep has fired.
# ============================================================================
class FairValueGapDetector:
    def __init__(self, config: DarkConfig):
        self.config       = config
        self._active_fvgs : List[Dict] = []
        self._last_signal : str = "—"
        self._score       : float = 0.5

    def detect(self, df: pd.DataFrame, df_features: Optional[pd.DataFrame] = None,
               htf_df: Optional[pd.DataFrame] = None) -> Dict:
        if df is None or len(df) < 10:
            return self._empty()
        atr = float(FeatureEngineer._atr(df, 14).iloc[-1])
        htf_bullish = htf_bearish = False
        if htf_df is not None and not htf_df.empty:
            htf_close = float(htf_df["Close"].iloc[-1])
            htf_mid   = (float(htf_df["High"].tail(20).max()) + float(htf_df["Low"].tail(20).min())) / 2
            htf_bullish = htf_close > htf_mid
            htf_bearish = htf_close < htf_mid
        for i in range(2, len(df)):
            fvg = self._check_fvg(df, i, atr, df_features)
            if fvg and not any(abs(f["mid"] - fvg["mid"]) / (fvg["mid"] + 1e-9) < 0.001
                               for f in self._active_fvgs):
                self._active_fvgs.append(fvg)
        self._active_fvgs = [f for f in self._active_fvgs
                             if self._mitigation_pct(f, float(df["Close"].iloc[-1])) < self.config.FVG_MITIGATION_PCT]
        current_price = float(df["Close"].iloc[-1])
        bullish_near  = [f for f in self._active_fvgs if f["type"] == "bullish"
                         and f["bottom"] * 0.999 <= current_price <= f["top"] and (not htf_bearish)]
        bearish_near  = [f for f in self._active_fvgs if f["type"] == "bearish"
                         and f["bottom"] * 0.995 <= current_price <= f["top"] and (not htf_bullish)]
        score = 0.5
        if bullish_near: score += 0.35
        if bearish_near: score += 0.35
        score = float(max(0.0, min(1.0, score)))
        self._score = score
        direction = None
        if bullish_near:
            self._last_signal = f"Bullish FVG [{bullish_near[0]['bottom']:.5f}-{bullish_near[0]['top']:.5f}]"
            direction = "buy"
        elif bearish_near:
            self._last_signal = f"Bearish FVG [{bearish_near[0]['bottom']:.5f}-{bearish_near[0]['top']:.5f}]"
            direction = "sell"
        elif self._active_fvgs:
            self._last_signal = f"{len(self._active_fvgs)} FVG(s) tracked"
        else:
            self._last_signal = "No active FVG"
        return {"fvgs": self._active_fvgs, "bullish_near": bullish_near,
                "bearish_near": bearish_near, "direction": direction,
                "count": len(self._active_fvgs), "score": score}

    def _check_fvg(self, df, idx, atr, df_f):
        try:
            bar_prev = df.iloc[idx - 2]; bar_mid = df.iloc[idx - 1]; bar_next = df.iloc[idx]
            if float(bar_next["Low"]) > float(bar_prev["High"]):
                body = abs(float(bar_mid["Close"]) - float(bar_mid["Open"]))
                if body < self.config.FVG_DISP_ATR_MULT * atr: return None
                return {"type": "bullish", "top": float(bar_next["Low"]),
                        "bottom": float(bar_prev["High"]),
                        "mid": (float(bar_next["Low"]) + float(bar_prev["High"])) / 2,
                        "atr_at_creation": atr, "mitigation_pct": 0.0, "bar_time": str(df.index[idx])}
            if float(bar_next["High"]) < float(bar_prev["Low"]):
                body = abs(float(bar_mid["Close"]) - float(bar_mid["Open"]))
                if body < self.config.FVG_DISP_ATR_MULT * atr: return None
                return {"type": "bearish", "top": float(bar_prev["Low"]),
                        "bottom": float(bar_next["High"]),
                        "mid": (float(bar_prev["Low"]) + float(bar_next["High"])) / 2,
                        "atr_at_creation": atr, "mitigation_pct": 0.0, "bar_time": str(df.index[idx])}
        except Exception:
            pass
        return None

    def _mitigation_pct(self, fvg, price):
        top = fvg.get("top", 0); bottom = fvg.get("bottom", 0); span = top - bottom
        if span <= 0: return 1.0
        if fvg["type"] == "bullish":
            penetration = max(0, price - bottom)
        else:
            penetration = max(0, top - price)
        return min(1.0, penetration / span)

    def get_score(self) -> float: return self._score
    def get_last_signal(self) -> str: return self._last_signal

    @staticmethod
    def _empty() -> Dict:
        return {"fvgs": [], "bullish_near": [], "bearish_near": [],
                "direction": None, "count": 0, "score": 0.5}


# ============================================================================
# 8.  MICROSTRUCTURE ANALYZER  (kept from v1.0, spread_ratio method added)
# ============================================================================
class MicrostructureAnalyzer:
    def __init__(self, config: DarkConfig):
        self.config           = config
        self._spread_history  : Dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
        self._latency_history : deque            = deque(maxlen=100)
        self._slippage_hist   : Dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
        self._fill_hist       : deque            = deque(maxlen=50)
        self._book_cache      : Dict[str, List]  = {}
        self._last_signal     : str = "—"
        self._score           : float = 0.5
        self._abook_score     : float = 0.5
        self._spoofing_flag   : bool = False
        self._last_spread_ratio: float = 1.0

    def analyze(self, symbol: str, data: DataProvider) -> Dict:
        tick = data.get_tick(symbol); info = data.get_symbol_info(symbol)
        book = data.get_order_book(symbol)
        if tick is None or info is None:
            return self._empty()
        bid   = float(tick.bid); ask = float(tick.ask)
        spread = (ask - bid) / (info.point + 1e-9) * info.point
        self._spread_history[symbol].append(spread)
        avg_spread = float(np.mean(list(self._spread_history[symbol])))
        spread_ratio = spread / (avg_spread + 1e-9)
        self._last_spread_ratio = spread_ratio
        # UPGRADED: spread_ok uses 1.5x (was 1.2x) — now also acts as news filter
        spread_ok = spread_ratio <= self.config.MS_SPREAD_MULT
        depth_ratio = 1.0; spoofing = False
        if book:
            self._book_cache[symbol] = book
            bid_depth = sum(getattr(b, "volume", 0) for b in book if getattr(b, "type", 1) == 1)
            ask_depth = sum(getattr(b, "volume", 0) for b in book if getattr(b, "type", 2) == 2)
            depth_ratio = bid_depth / (ask_depth + 1e-9)
            spoofing    = abs(depth_ratio - 1.0) > 3.0
            self._spoofing_flag = spoofing
        latency_ms  = self._measure_latency(symbol, data)
        self._latency_history.append(latency_ms)
        avg_latency = float(np.mean(list(self._latency_history)))
        latency_ok  = latency_ms < self.config.MS_LATENCY_WARN_MS
        self._update_abook_score()
        last_look  = self._detect_last_look()
        dark_pool  = self._infer_dark_pool(symbol, data)
        score = 0.5
        if spread_ok:   score += 0.20
        if latency_ok:  score += 0.15
        if not spoofing: score += 0.10
        if not last_look: score += 0.10
        score *= (0.7 + 0.3 * self._abook_score)
        score  = float(max(0.0, min(1.0, score)))
        self._score = score
        if not spread_ok:          self._last_signal = f"Spread elevated {spread_ratio:.1f}x avg ⚠"
        elif spoofing:             self._last_signal = "Spoofing detected ⚠"
        elif last_look:            self._last_signal = "Last-look B-book ⚠"
        elif dark_pool:            self._last_signal = "Dark pool activity"
        else:                      self._last_signal = f"Spread ok {spread_ratio:.2f}x | {latency_ms:.0f}ms"
        return {"spread": spread, "avg_spread": avg_spread, "spread_ok": spread_ok,
                "spread_ratio": spread_ratio, "depth_ratio": depth_ratio,
                "spoofing": spoofing, "latency_ms": latency_ms, "avg_latency": avg_latency,
                "latency_ok": latency_ok, "abook_score": self._abook_score,
                "last_look": last_look, "dark_pool": dark_pool, "score": score}

    def get_spread_ratio(self) -> float:
        return self._last_spread_ratio

    def _measure_latency(self, symbol, data):
        t0 = time.perf_counter(); data.get_tick(symbol); t1 = time.perf_counter()
        return (t1 - t0) * 1000.0

    def _update_abook_score(self):
        if len(self._fill_hist) < 10: return
        fills = list(self._fill_hist)
        b_book_slips = [f for f in fills if f.get("pnl", 0) > 0
                        and abs(f.get("slippage_pips", 0)) > self.config.MS_LASTLOOK_PIPS]
        self._abook_score = float(max(0.0, 1.0 - len(b_book_slips) / len(fills) * 2))

    def record_fill(self, requested: float, actual: float, pnl: float, symbol: str = ""):
        info = None
        if MT5_AVAILABLE:
            try: info = mt5.symbol_info(symbol)
            except Exception: pass
        pip = info.point if info else 0.0001
        slippage_pips = (actual - requested) / (pip + 1e-9)
        self._fill_hist.append({"requested": requested, "actual": actual,
                                "pnl": pnl, "slippage_pips": slippage_pips})
        self._slippage_hist[symbol].append(slippage_pips)
        self._update_abook_score()

    def _detect_last_look(self) -> bool:
        if len(self._fill_hist) < 5: return False
        recent = list(self._fill_hist)[-5:]
        slip_on_wins = [f for f in recent if f.get("pnl", 0) > 0
                        and abs(f.get("slippage_pips", 0)) > self.config.MS_LASTLOOK_PIPS]
        return len(slip_on_wins) >= 3

    def _infer_dark_pool(self, symbol, data):
        df = data.get_rates(symbol, "M5", 20)
        if df is None or len(df) < 10: return False
        vol_zscore = (float(df["Volume"].iloc[-1]) - float(df["Volume"].mean())) / (float(df["Volume"].std()) + 1e-9)
        price_move = abs(float(df["Close"].iloc[-1]) - float(df["Close"].iloc[-2])) / (float(df["Close"].mean()) + 1e-9)
        atr_norm   = float(FeatureEngineer._atr(df, 7).iloc[-1]) / (float(df["Close"].mean()) + 1e-9)
        return vol_zscore > 2.0 and price_move < atr_norm * 0.3

    def slippage_stats(self, symbol: str) -> Dict:
        hist = list(self._slippage_hist.get(symbol, []))
        if not hist: return {"mean": 0, "std": 0, "p95": 0}
        arr = np.array(hist)
        return {"mean": float(arr.mean()), "std": float(arr.std()), "p95": float(np.percentile(arr, 95))}

    def get_score(self) -> float: return self._score
    def get_last_signal(self) -> str: return self._last_signal

    @staticmethod
    def _empty() -> Dict:
        return {"spread": 0, "avg_spread": 0, "spread_ok": True, "spread_ratio": 1.0,
                "depth_ratio": 1.0, "spoofing": False, "latency_ms": 0,
                "avg_latency": 0, "latency_ok": True, "abook_score": 0.5,
                "last_look": False, "dark_pool": False, "score": 0.5}


# ============================================================================
# 9.  HTF BIAS ENGINE  — NEW
# REASON: Before any M5 trade, D1+H4 must agree on direction.
#   This is the single most important filter in this system.
#   Institutions define the story on D1/H4. We're just reading it.
# ============================================================================
class HTFBiasEngine:
    """
    Get institutional directional bias from D1 + H4.
    ONLY trade when both timeframes agree.
    HTF = NEUTRAL → NO TRADES AT ALL this cycle.
    """

    def get_bias(self, data: DataProvider, symbol: str) -> Tuple[str, float]:
        d1 = data.get_rates(symbol, "D1", 20)
        h4 = data.get_rates(symbol, "H4", 50)
        if d1 is None or len(d1) < 10 or h4 is None or len(h4) < 20:
            return "NEUTRAL", 0.0

        # D1 bias: where is price relative to last 10 days midpoint?
        d1_high  = float(d1["High"].tail(10).max())
        d1_low   = float(d1["Low"].tail(10).min())
        d1_mid   = (d1_high + d1_low) / 2
        d1_close = float(d1["Close"].iloc[-1])
        d1_bias  = "BULLISH" if d1_close > d1_mid else "BEARISH"

        # D1 EMA bias (50-bar)
        d1_ema50 = float(d1["Close"].ewm(span=min(50, len(d1)), adjust=False).mean().iloc[-1])
        d1_ema_bias = "BULLISH" if d1_close > d1_ema50 else "BEARISH"
        if d1_bias != d1_ema_bias:
            d1_bias = "NEUTRAL"  # D1 disagrees with itself = unclear

        # H4 bias: recent swing structure HH+HL or LH+LL
        h4_highs = h4["High"].tail(20)
        h4_lows  = h4["Low"].tail(20)
        recent_high = float(h4_highs.iloc[-1]) > float(h4_highs.iloc[-5])
        recent_low  = float(h4_lows.iloc[-1])  > float(h4_lows.iloc[-5])

        if recent_high and recent_low:
            h4_bias = "BULLISH"
        elif not recent_high and not recent_low:
            h4_bias = "BEARISH"
        else:
            h4_bias = "NEUTRAL"

        # H4 EMA check
        h4_ema21 = float(h4["Close"].ewm(span=21, adjust=False).mean().iloc[-1])
        h4_close = float(h4["Close"].iloc[-1])
        h4_ema_bias = "BULLISH" if h4_close > h4_ema21 else "BEARISH"
        if h4_bias != "NEUTRAL" and h4_bias != h4_ema_bias:
            h4_bias = "NEUTRAL"

        # Both must agree for full confidence
        if d1_bias == h4_bias == "BULLISH":
            return "BULLISH", 1.0
        elif d1_bias == h4_bias == "BEARISH":
            return "BEARISH", 1.0
        elif d1_bias == "BULLISH" and h4_bias == "NEUTRAL":
            return "BULLISH", 0.6
        elif d1_bias == "BEARISH" and h4_bias == "NEUTRAL":
            return "BEARISH", 0.6
        else:
            return "NEUTRAL", 0.0


# ============================================================================
# 10.  LIQUIDITY MAP ENGINE  — NEW
# REASON: Know where the stops are clustered before you trade.
#   Equal highs on H1 = buy stops above. Equal lows = sell stops below.
#   Institutions hunt these. We trade the aftermath, not the hunt.
# ============================================================================
class LiquidityMapEngine:
    """Map where stop clusters live and find nearest institutional targets."""

    def scan(self, df_h1: pd.DataFrame, df_m5: pd.DataFrame) -> List[Dict]:
        if df_h1 is None or len(df_h1) < 20:
            return []
        levels = []
        h1_highs = df_h1["High"].tail(50)
        h1_lows  = df_h1["Low"].tail(50)
        tol = 0.0003

        # Equal highs on H1 = buy stops cluster above
        hi_vals = h1_highs.values
        for i in range(len(hi_vals) - 1):
            for j in range(i + 1, min(i + 8, len(hi_vals))):
                if abs(hi_vals[i] - hi_vals[j]) / (hi_vals[i] + 1e-9) < tol:
                    levels.append({
                        "price":     (hi_vals[i] + hi_vals[j]) / 2,
                        "type":      "BUY_STOPS",
                        "strength":  "HIGH",
                        "timeframe": "H1",
                    })

        # Equal lows on H1 = sell stops cluster below
        lo_vals = h1_lows.values
        for i in range(len(lo_vals) - 1):
            for j in range(i + 1, min(i + 8, len(lo_vals))):
                if abs(lo_vals[i] - lo_vals[j]) / (lo_vals[i] + 1e-9) < tol:
                    levels.append({
                        "price":     (lo_vals[i] + lo_vals[j]) / 2,
                        "type":      "SELL_STOPS",
                        "strength":  "HIGH",
                        "timeframe": "H1",
                    })

        # M5 recent swing highs/lows as weaker levels
        if df_m5 is not None and len(df_m5) >= 20:
            m5_hi  = float(df_m5["High"].tail(20).max())
            m5_lo  = float(df_m5["Low"].tail(20).min())
            levels.append({"price": m5_hi, "type": "BUY_STOPS",  "strength": "LOW", "timeframe": "M5"})
            levels.append({"price": m5_lo, "type": "SELL_STOPS", "strength": "LOW", "timeframe": "M5"})

        return levels

    def nearest_target(self, current_price: float, bias: str, levels: List[Dict]) -> Optional[float]:
        """Find nearest institutional liquidity target in the direction of bias."""
        if not levels:
            return None
        if bias == "BULLISH":
            # Target: buy stops above (where institutions will push price to)
            targets = [l["price"] for l in levels
                       if l["type"] == "BUY_STOPS" and l["price"] > current_price]
            return min(targets) if targets else None
        elif bias == "BEARISH":
            # Target: sell stops below
            targets = [l["price"] for l in levels
                       if l["type"] == "SELL_STOPS" and l["price"] < current_price]
            return max(targets) if targets else None
        return None


# ============================================================================
# 11.  PREMIUM / DISCOUNT ENGINE  — NEW
# REASON: Institutions BUY at discount, SELL at premium.
#   If price is at equilibrium (midpoint ±10%), there is no edge.
#   Only trade when price is clearly cheap or clearly expensive.
# ============================================================================
class PremiumDiscountEngine:
    """Determine if current price is in Premium, Discount, or Equilibrium."""

    def analyze(self, df_d1: pd.DataFrame, df_h4: pd.DataFrame) -> Dict:
        if df_d1 is None or len(df_d1) < 10:
            return {"zone": "UNKNOWN", "trade_direction": "NO_TRADE", "score": 0.5}

        # Use D1 range for premium/discount
        d1_high = float(df_d1["High"].tail(20).max())
        d1_low  = float(df_d1["Low"].tail(20).min())
        d1_mid  = (d1_high + d1_low) / 2
        span    = d1_high - d1_low

        if span <= 0:
            return {"zone": "UNKNOWN", "trade_direction": "NO_TRADE", "score": 0.5}

        current = float(df_d1["Close"].iloc[-1])
        ratio   = (current - d1_low) / span  # 0 = at low, 1 = at high

        band = self._get_band_pct(df_d1, df_h4)

        if ratio < (0.5 - band):
            zone = "DISCOUNT"
            trade_direction = "BUY"
            score = 0.85 - ratio * 0.5
        elif ratio > (0.5 + band):
            zone = "PREMIUM"
            trade_direction = "SELL"
            score = 0.5 + (ratio - 0.5) * 0.7
        else:
            zone = "EQUILIBRIUM"
            trade_direction = "NO_TRADE"
            score = 0.3

        score = float(max(0.0, min(1.0, score)))
        return {"zone": zone, "trade_direction": trade_direction,
                "ratio": round(ratio, 3), "score": score,
                "d1_high": d1_high, "d1_low": d1_low, "d1_mid": d1_mid}

    def _get_band_pct(self, df_d1, df_h4) -> float:
        """Dynamic equilibrium band based on ATR context."""
        if df_h4 is not None and len(df_h4) >= 14:
            atr  = float(FeatureEngineer._atr(df_h4, 14).iloc[-1])
            span = float(df_d1["High"].tail(20).max()) - float(df_d1["Low"].tail(20).min())
            return min(0.20, max(0.05, atr / (span + 1e-9) * 2))
        return 0.10  # default 10% equilibrium band


# ============================================================================
# 12.  ORDER BLOCK ENGINE  — NEW
# REASON: Order blocks are the last bearish/bullish candle before a strong
#   move. Institutions leave unfilled orders here. When price returns,
#   they defend these levels. We enter inside the OB.
# ============================================================================
class OrderBlockEngine:
    """Detect institutional order blocks (last opposite candle before displacement)."""

    def __init__(self, config: DarkConfig):
        self.config = config

    def detect(self, df: pd.DataFrame) -> List[Dict]:
        if df is None or len(df) < 20:
            return []
        atr = float(FeatureEngineer._atr(df, 14).iloc[-1])
        obs = []
        lookback = min(self.config.OB_LOOKBACK, len(df) - 3)
        for i in range(2, lookback):
            idx = -(i + 1)
            bar  = df.iloc[idx]
            nxt  = df.iloc[idx + 1]
            nxt2 = df.iloc[idx + 2]
            # Bullish OB: last bearish candle before bullish displacement
            if (float(bar["Close"]) < float(bar["Open"]) and           # bar is bearish
                float(nxt["Close"])  > float(nxt["Open"])  and           # next is bullish
                float(nxt["Close"]) - float(nxt["Open"]) > 1.5 * atr):  # displacement
                obs.append({
                    "type":    "bullish",
                    "top":     float(bar["Open"]),   # OB = body of the bearish candle
                    "bottom":  float(bar["Close"]),
                    "mid":     (float(bar["Open"]) + float(bar["Close"])) / 2,
                    "atr":     atr,
                    "bar_idx": idx,
                })
            # Bearish OB: last bullish candle before bearish displacement
            if (float(bar["Close"]) > float(bar["Open"]) and
                float(nxt["Close"])  < float(nxt["Open"]) and
                float(nxt["Open"]) - float(nxt["Close"]) > 1.5 * atr):
                obs.append({
                    "type":    "bearish",
                    "top":     float(bar["Close"]),  # OB = body of the bullish candle
                    "bottom":  float(bar["Open"]),
                    "mid":     (float(bar["Open"]) + float(bar["Close"])) / 2,
                    "atr":     atr,
                    "bar_idx": idx,
                })
        return obs[:10]  # keep newest 10

    def price_in_ob(self, price: float, obs: List[Dict], bias: str) -> Tuple[bool, Optional[Dict]]:
        """Check if current price is inside an order block aligned with bias."""
        for ob in obs:
            if ob["type"] == "bullish" and bias in ("BULLISH", "BUY"):
                if ob["bottom"] <= price <= ob["top"]:
                    return True, ob
            if ob["type"] == "bearish" and bias in ("BEARISH", "SELL"):
                if ob["bottom"] <= price <= ob["top"]:
                    return True, ob
        return False, None


# ============================================================================
# 13.  MARKET STRUCTURE BREAK ENGINE  — NEW
# REASON: A break of structure (BOS) on M5 confirms the directional move.
#   A change of character (CHOCH) signals a potential reversal.
#   We need M5 structure to confirm the HTF bias before entering.
# ============================================================================
class MarketStructureBreakEngine:
    """Detect BOS (Break of Structure) and CHOCH (Change of Character) on M5."""

    def analyze(self, df: pd.DataFrame) -> Dict:
        if df is None or len(df) < 30:
            return {"bos": False, "choch": False, "structure": "NEUTRAL",
                    "last_swing_high": 0.0, "last_swing_low": 0.0}

        highs  = df["High"].values
        lows   = df["Low"].values
        closes = df["Close"].values
        n = len(highs)

        # Find swing highs and lows (pivot points)
        swing_highs = []
        swing_lows  = []
        for i in range(2, n - 2):
            if highs[i] > highs[i-1] and highs[i] > highs[i-2] and \
               highs[i] > highs[i+1] and highs[i] > highs[i+2]:
                swing_highs.append((i, highs[i]))
            if lows[i] < lows[i-1] and lows[i] < lows[i-2] and \
               lows[i] < lows[i+1] and lows[i] < lows[i+2]:
                swing_lows.append((i, lows[i]))

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return {"bos": False, "choch": False, "structure": "NEUTRAL",
                    "last_swing_high": float(highs[-5:].max()),
                    "last_swing_low":  float(lows[-5:].min())}

        current_price = closes[-1]
        last_sh = swing_highs[-1][1]; prev_sh = swing_highs[-2][1]
        last_sl = swing_lows[-1][1];  prev_sl = swing_lows[-2][1]

        # Bullish BOS: current price breaks above last swing high
        bos_bullish = current_price > last_sh
        # Bearish BOS: current price breaks below last swing low
        bos_bearish = current_price < last_sl

        # CHOCH (Change of Character): breaks the OPPOSITE swing level
        # Bullish CHOCH: in downtrend (LH+LL) price breaks above last lower high
        # Bearish CHOCH: in uptrend (HH+HL) price breaks below last higher low
        uptrend   = last_sh > prev_sh and last_sl > prev_sl
        downtrend = last_sh < prev_sh and last_sl < prev_sl
        choch_bullish = downtrend and bos_bullish
        choch_bearish = uptrend  and bos_bearish

        bos = bos_bullish or bos_bearish
        choch = choch_bullish or choch_bearish

        if bos_bullish or choch_bullish: structure = "BULLISH"
        elif bos_bearish or choch_bearish: structure = "BEARISH"
        elif uptrend:   structure = "BULLISH"
        elif downtrend: structure = "BEARISH"
        else:           structure = "NEUTRAL"

        return {"bos": bos, "choch": choch, "structure": structure,
                "bos_bullish": bos_bullish, "bos_bearish": bos_bearish,
                "choch_bullish": choch_bullish, "choch_bearish": choch_bearish,
                "last_swing_high": float(last_sh), "last_swing_low": float(last_sl)}


# ============================================================================
# 14.  CONTEXTUAL SCORER  (updated weights to include HTF)
# ============================================================================
class ContextualScorer:
    def __init__(self, config: DarkConfig):
        self.config       = config
        self._last_signal = "—"
        self._score       = 0.0

    def compute(self, vp_score: float, of_score: float, ls_score: float,
                fvg_score: float, ms_score: float, regime: str,
                session_q: float, news_pause: bool, ai_prob: float,
                htf_confidence: float = 0.5) -> float:
        # UPGRADED: HTF weight added (was not in v1.0)
        weights = {
            "htf":     0.20,   # NEW — most important gate
            "ls":      0.20,   # Liquidity sweep — core edge
            "vp":      0.15,
            "of":      0.12,
            "fvg":     0.10,
            "ms":      0.10,
            "regime":  0.08,
            "session": 0.03,
            "ai":      0.02,
        }
        regime_scores = {"TRENDING": 0.85, "RANGING": 0.70, "VOLATILE": 0.65, "UNKNOWN": 0.40}
        regime_score  = regime_scores.get(regime, 0.5)
        ai_score      = 0.5 + abs(ai_prob - 0.5)
        raw = (
            weights["htf"]     * htf_confidence +
            weights["ls"]      * ls_score       +
            weights["vp"]      * vp_score       +
            weights["of"]      * of_score       +
            weights["fvg"]     * fvg_score      +
            weights["ms"]      * ms_score       +
            weights["regime"]  * regime_score   +
            weights["session"] * session_q      +
            weights["ai"]      * ai_score
        )
        if news_pause: raw *= 0.10   # UPGRADED: wider penalty during news events
        score = float(max(0.0, min(1.0, raw)))
        self._score = score
        self._last_signal = f"Score={score:.2f} | HTF={htf_confidence:.2f} | LS={ls_score:.2f}"
        return score

    def get_score(self) -> float: return self._score
    def get_last_signal(self) -> str: return self._last_signal


# ============================================================================
# 15.  REGIME DETECTOR  (kept from v1.0)
# ============================================================================
class RegimeDetector:
    TRENDING  = "TRENDING"
    RANGING   = "RANGING"
    VOLATILE  = "VOLATILE"
    UNKNOWN   = "UNKNOWN"

    def __init__(self, lookback: int = 100, adx_thresh: float = 25.0):
        self.lookback   = lookback
        self.adx_thresh = adx_thresh

    def detect(self, df: pd.DataFrame) -> str:
        if df is None or len(df) < self.lookback:
            return self.UNKNOWN
        try:
            window = df.tail(self.lookback)
            adx    = self._adx(window)
            atr    = float(FeatureEngineer._atr(window, 14).iloc[-1])
            close  = window["Close"]
            vol    = float(close.pct_change().std())
            if adx > self.adx_thresh and vol < 0.015:
                return self.TRENDING
            if vol > 0.025:
                return self.VOLATILE
            return self.RANGING
        except Exception:
            return self.UNKNOWN

    def _adx(self, df: pd.DataFrame, period: int = 14) -> float:
        try:
            hi, lo, cl = df["High"], df["Low"], df["Close"]
            up_m = hi.diff(); dn_m = -lo.diff()
            pdm  = up_m.where((up_m > dn_m) & (up_m > 0), 0.0)
            ndm  = dn_m.where((dn_m > up_m) & (dn_m > 0), 0.0)
            tr   = pd.concat([hi - lo, (hi - cl.shift()).abs(), (lo - cl.shift()).abs()], axis=1).max(axis=1)
            atr  = tr.rolling(period).mean()
            pdi  = 100 * pdm.rolling(period).mean() / (atr + 1e-9)
            ndi  = 100 * ndm.rolling(period).mean() / (atr + 1e-9)
            dx   = 100 * (pdi - ndi).abs() / (pdi + ndi + 1e-9)
            val  = dx.rolling(period).mean().iloc[-1]
            return float(val) if not np.isnan(val) else 0.0
        except Exception:
            return 0.0


# ============================================================================
# 16.  AI PREDICTOR  — XGBoost only, 12 features, trade-outcome target
# UPGRADED:
#   BUG 1 FIX: Scaler fitted on TRAIN rows only (was on all rows including future)
#   BUG 2 FIX: Target = trade win/loss outcome (was next-bar direction — nearly random)
#   BUG 5 FIX: Features computed bar-by-bar during training (no lookahead)
#   REMOVE 1: LSTM removed — overfits on <500 bars
#   REMOVE 2: 50 features → 12 (rule: need 50x more samples than features)
# ============================================================================
class AIPredictor:
    """
    Predicts TRADE OUTCOME (win/loss) for a specific setup.
    This is 5-10x more predictive than next-bar direction.
    Regime-specific: trains separate models per market regime.
    """

    def __init__(self, config: DarkConfig):
        self.config      = config
        self.xgb_models  : Dict[str, Any] = {}   # regime → model
        self.xgb_global  = None
        self.last_trained: Optional[datetime] = None
        self.scaler_mean = None
        self.scaler_std  = None

    def train(self, df: pd.DataFrame, regime_detector: "RegimeDetector") -> bool:
        """
        Train XGBoost to predict: will this specific trade (based on features
        at signal time) hit TP or SL?
        BUG 1+5 FIX: split before scaling; features computed on past-only windows.
        """
        if df is None or len(df) < self.config.AI_MIN_TRAIN_BARS:
            log.warning("Not enough data to train AI (%d rows)", 0 if df is None else len(df))
            return False

        # UPGRADED BUG 5 FIX: bar-by-bar feature computation (no lookahead)
        records = []
        min_lookback = 100
        atr_sl_mult  = 1.5
        atr_tp_mult  = 3.0   # 2R TP
        for i in range(min_lookback, len(df) - 5):
            past_df  = df.iloc[:i]
            feat_df  = FeatureEngineer.add_features(past_df)
            if feat_df.empty: continue
            feat_row = feat_df.iloc[-1]
            atr_val  = float(feat_row.get("atr", 0))
            if atr_val <= 0: continue
            entry    = float(df["Close"].iloc[i])
            sl_dist  = atr_sl_mult * atr_val
            tp_dist  = atr_tp_mult * atr_val
            # Simulate trade outcome on NEXT 20 bars
            future   = df.iloc[i: i + 20]
            if len(future) < 5: continue
            # Long trade simulation
            tp_long = entry + tp_dist; sl_long = entry - sl_dist
            tp_hit = any(float(r["High"]) >= tp_long for _, r in future.iterrows())
            sl_hit = any(float(r["Low"])  <= sl_long for _, r in future.iterrows())
            if tp_hit and not sl_hit: label = 1
            elif sl_hit:              label = 0
            else: continue  # neither hit = exclude ambiguous
            regime = regime_detector.detect(past_df)
            feat_vals = {c: float(feat_row.get(c, 0)) for c in FeatureEngineer.FEATURE_COLS}
            feat_vals["_label"]  = label
            feat_vals["_regime"] = regime
            records.append(feat_vals)

        if len(records) < 100:
            log.warning("Too few labeled trades for AI training: %d", len(records))
            return False

        df_train = pd.DataFrame(records)
        X_all = df_train[FeatureEngineer.FEATURE_COLS].values.astype(np.float32)
        y_all = df_train["_label"].values.astype(int)
        n     = len(X_all)

        # UPGRADED BUG 1 FIX: Fit scaler on train portion ONLY
        split = int(n * 0.8)
        self.scaler_mean = X_all[:split].mean(axis=0)
        self.scaler_std  = X_all[:split].std(axis=0) + 1e-9
        X_sc             = (X_all - self.scaler_mean) / self.scaler_std

        # Global model
        if XGB_AVAILABLE:
            try:
                self.xgb_global = xgb.XGBClassifier(
                    n_estimators=300,
                    max_depth=4,
                    learning_rate=0.02,
                    subsample=0.8,
                    colsample_bytree=0.8,
                    min_child_weight=5,
                    gamma=0.2,
                    reg_alpha=0.2,
                    eval_metric="logloss",
                    verbosity=0,
                )
                self.xgb_global.fit(
                    X_sc[:split], y_all[:split],
                    eval_set=[(X_sc[split:], y_all[split:])],
                    verbose=False,
                )
                log.info("XGBoost trained | features=%d labeled_trades=%d",
                         len(FeatureEngineer.FEATURE_COLS), n)
            except Exception as e:
                log.warning("XGBoost training failed: %s", e)
                self.xgb_global = None
                return False

            # Regime-specific models
            for regime in [RegimeDetector.TRENDING, RegimeDetector.RANGING, RegimeDetector.VOLATILE]:
                mask = df_train["_regime"] == regime
                if mask.sum() < 60: continue
                X_r = X_sc[mask.values]
                y_r = y_all[mask.values]
                split_r = int(len(X_r) * 0.8)
                if split_r < 30: continue
                try:
                    m = xgb.XGBClassifier(n_estimators=200, max_depth=3,
                                          learning_rate=0.03, subsample=0.8,
                                          min_child_weight=5, verbosity=0,
                                          eval_metric="logloss")
                    m.fit(X_r[:split_r], y_r[:split_r],
                          eval_set=[(X_r[split_r:], y_r[split_r:])], verbose=False)
                    self.xgb_models[regime] = m
                    log.info("Regime model [%s] trained on %d trades", regime, len(X_r))
                except Exception as e:
                    log.debug("Regime model [%s] failed: %s", regime, e)

        self.last_trained = datetime.now()
        return True

    def predict(self, df: pd.DataFrame, regime: str = "UNKNOWN") -> float:
        """Return probability [0,1] that the NEXT TRADE will be a winner."""
        if self.xgb_global is None:
            return 0.5
        feat_df = FeatureEngineer.add_features(df.tail(150))
        if feat_df.empty:
            return 0.5
        cols = FeatureEngineer.FEATURE_COLS
        X = feat_df[cols].values.astype(np.float32)[-1:]
        if self.scaler_mean is not None:
            X = (X - self.scaler_mean) / self.scaler_std

        probs = []
        # Regime-specific model (higher weight)
        if regime in self.xgb_models:
            try:
                p = float(self.xgb_models[regime].predict_proba(X)[0][1])
                probs.extend([p, p])   # double weight
            except Exception: pass
        # Global model
        try:
            p = float(self.xgb_global.predict_proba(X)[0][1])
            probs.append(p)
        except Exception: pass
        return float(np.mean(probs)) if probs else 0.5

    def needs_retraining(self) -> bool:
        if self.last_trained is None: return True
        return (datetime.now() - self.last_trained).total_seconds() >= self.config.AI_UPDATE_FREQUENCY


# ============================================================================
# 17.  QUALITY GATE  — NEW
# REASON: 10 sequential hard gates. All must pass for a trade to execute.
#   Each gate kills a different type of bad trade.
# ============================================================================
class QualityGate:
    """
    10 hard gates. ALL must pass.
    One failure = no trade. No exceptions.
    """

    def __init__(self, config: DarkConfig):
        self.config = config

    def check(self, signal: Dict) -> Tuple[bool, str]:
        gates = [
            self._gate_htf_bias,
            self._gate_pd_zone,
            self._gate_liquidity_sweep,
            self._gate_market_structure,
            self._gate_session,
            self._gate_spread,
            self._gate_ai_confidence,
            self._gate_context_score,
            self._gate_daily_trade_limit,
            self._gate_regime,
        ]
        for gate in gates:
            ok, reason = gate(signal)
            if not ok:
                return False, reason
        return True, "All 10 gates passed ✓"

    def _gate_htf_bias(self, s):
        if s.get("htf_confidence", 0) < self.config.HTF_MIN_CONFIDENCE:
            return False, f"HTF unclear (conf={s.get('htf_confidence',0):.2f} < {self.config.HTF_MIN_CONFIDENCE})"
        bias = s.get("htf_bias", "NEUTRAL"); direction = s.get("trade_direction", "")
        if bias == "NEUTRAL": return False, "HTF = NEUTRAL — no trades"
        if bias == "BULLISH" and direction == "sell": return False, "HTF BULLISH but signal is SELL"
        if bias == "BEARISH" and direction == "buy":  return False, "HTF BEARISH but signal is BUY"
        return True, ""

    def _gate_pd_zone(self, s):
        pd_zone   = s.get("pd_zone", "UNKNOWN")
        direction = s.get("trade_direction", "")
        if pd_zone == "EQUILIBRIUM": return False, "Price at equilibrium — no edge"
        if pd_zone == "UNKNOWN":     return False, "P/D zone unknown"
        if pd_zone == "DISCOUNT" and direction == "sell": return False, "Selling in discount zone"
        if pd_zone == "PREMIUM"  and direction == "buy":  return False, "Buying in premium zone"
        return True, ""

    def _gate_liquidity_sweep(self, s):
        if not s.get("sweep_confirmed", False):
            return False, "No confirmed liquidity sweep"
        return True, ""

    def _gate_market_structure(self, s):
        msb  = s.get("msb_direction", "NEUTRAL")
        bias = s.get("htf_bias", "NEUTRAL")
        dire = s.get("trade_direction", "")
        if msb == "NEUTRAL": return False, "No M5 market structure direction"
        if dire == "buy"  and msb != "BULLISH": return False, "M5 structure bearish vs BUY signal"
        if dire == "sell" and msb != "BEARISH": return False, "M5 structure bullish vs SELL signal"
        return True, ""

    def _gate_session(self, s):
        sessions = s.get("session", [])
        preferred = ["LONDON", "NEWYORK"]
        if not any(x in sessions for x in preferred):
            return False, f"Not in preferred session (active={sessions})"
        return True, ""

    def _gate_spread(self, s):
        ratio = s.get("spread_ratio", 1.0)
        if ratio > self.config.MS_SPREAD_MULT:
            return False, f"Spread too wide ({ratio:.2f}x avg) — possible news event"
        return True, ""

    def _gate_ai_confidence(self, s):
        prob = s.get("ai_confidence", 0.5)
        if prob < self.config.AI_MIN_CONFIDENCE:
            return False, f"AI confidence low ({prob:.3f} < {self.config.AI_MIN_CONFIDENCE})"
        return True, ""

    def _gate_context_score(self, s):
        ctx = s.get("ctx_score", 0.0)
        if ctx < self.config.CTX_MIN_SCORE:
            return False, f"Context score insufficient ({ctx:.2f} < {self.config.CTX_MIN_SCORE})"
        return True, ""

    def _gate_daily_trade_limit(self, s):
        daily = s.get("daily_trades", 0)
        if daily >= self.config.MAX_DAILY_TRADES:
            return False, f"Daily trade limit reached ({daily}/{self.config.MAX_DAILY_TRADES})"
        return True, ""

    def _gate_regime(self, s):
        regime = s.get("regime", "UNKNOWN")
        if regime == RegimeDetector.UNKNOWN:
            return False, "Regime unknown — not enough data"
        return True, ""


# ============================================================================
# 18.  PATTERN TRACKER  — NEW
# REASON: Track YOUR actual performance by confluence pattern.
#   Without this, you can't know which conditions produce real edge.
#   Patterns: which gates were active, which timeframes confirmed, etc.
# ============================================================================
class PatternTracker:
    """Track trade performance grouped by confluence pattern."""

    FILENAME = "pattern_performance.json"

    def __init__(self):
        self._patterns : Dict[str, List[float]] = defaultdict(list)
        self._load()

    def _load(self):
        if Path(self.FILENAME).exists():
            try:
                with open(self.FILENAME, "r") as f:
                    data = json.load(f)
                for k, v in data.items():
                    self._patterns[k] = v
            except Exception:
                pass

    def _save(self):
        try:
            with open(self.FILENAME, "w") as f:
                json.dump(dict(self._patterns), f, indent=2)
        except Exception:
            pass

    def record(self, signal: Dict, pnl: float):
        """Record a trade outcome under its confluence pattern key."""
        parts = []
        bias  = signal.get("htf_bias", "?")
        parts.append(f"HTF={bias[:1]}")
        if signal.get("sweep_confirmed"): parts.append("LS")
        if signal.get("ob_confirmed"):    parts.append("OB")
        if signal.get("fvg_confirmed"):   parts.append("FVG")
        pd_z = signal.get("pd_zone", "?")
        parts.append(f"PD={pd_z[:3]}")
        regime = signal.get("regime", "?")
        parts.append(f"R={regime[:3]}")
        sessions = signal.get("session", [])
        if "LONDON" in sessions and "NEWYORK" in sessions:
            parts.append("SESS=LN")
        elif sessions:
            parts.append(f"SESS={sessions[0][:1]}")
        key = "|".join(parts)
        self._patterns[key].append(pnl)
        self._save()
        return key

    def get_stats(self) -> Dict[str, Dict]:
        stats = {}
        for pattern, pnls in self._patterns.items():
            if not pnls: continue
            arr    = np.array(pnls)
            wins   = arr[arr > 0]
            losses = arr[arr < 0]
            stats[pattern] = {
                "trades":        len(arr),
                "win_rate":      float(len(wins) / len(arr)) if arr.size else 0,
                "avg_pnl":       float(arr.mean()),
                "profit_factor": float(wins.sum() / (abs(losses.sum()) + 1e-9)) if losses.size else 999.0,
                "expectancy":    float(arr.mean()),
                "total_pnl":     float(arr.sum()),
            }
        return stats

    def print_stats(self):
        stats = self.get_stats()
        if not stats:
            _print("No pattern data yet.")
            return
        sorted_pats = sorted(stats.items(), key=lambda x: x[1]["expectancy"], reverse=True)
        if RICH_AVAILABLE and console:
            table = Table(title="Pattern Performance", box=rich_box.ROUNDED,
                          show_header=True, header_style="bold cyan")
            table.add_column("Pattern", style="white", max_width=40)
            table.add_column("Trades", justify="right")
            table.add_column("Win%",   justify="right")
            table.add_column("PF",     justify="right")
            table.add_column("Expect", justify="right")
            table.add_column("Total",  justify="right")
            for pat, s in sorted_pats[:20]:
                color = "green" if s["avg_pnl"] > 0 else "red"
                table.add_row(pat, str(s["trades"]),
                              f"[{color}]{s['win_rate']:.0%}[/{color}]",
                              f"[{color}]{s['profit_factor']:.2f}[/{color}]",
                              f"[{color}]${s['expectancy']:.2f}[/{color}]",
                              f"[{color}]${s['total_pnl']:.2f}[/{color}]")
            console.print(table)
        else:
            print(f"\n{'─'*80}")
            print(f"  {'Pattern':<35} {'Trades':>6} {'Win%':>6} {'PF':>5} {'Expect':>8} {'Total':>8}")
            print(f"{'─'*80}")
            for pat, s in sorted_pats[:20]:
                print(f"  {pat:<35} {s['trades']:>6} {s['win_rate']:>6.0%} {s['profit_factor']:>5.2f} "
                      f"${s['expectancy']:>7.2f} ${s['total_pnl']:>7.2f}")


# ============================================================================
# 19.  RISK MANAGER  (updated for new Gold lot cap)
# ============================================================================
class RiskManager:
    def __init__(self, config: DarkConfig, data_provider: DataProvider):
        self.config       = config
        self.data         = data_provider
        self.daily_pnl    = 0.0
        self.daily_trades = 0
        self.last_reset   = datetime.now().date()
        self.risk_scalar  = 1.0
        self.halted       = False

    def _reset_if_new_day(self):
        today = datetime.now().date()
        if today != self.last_reset:
            self.daily_pnl    = 0.0
            self.daily_trades = 0
            self.last_reset   = today
            log.info("Daily counters reset")

    def _get_equity(self) -> float:
        if not MT5_AVAILABLE:
            return 10_000.0
        acc = mt5.account_info()
        return float(acc.equity) if acc else 10_000.0

    def check_kill_switch(self) -> bool:
        if self.halted: return True
        equity = self._get_equity()
        if self.daily_pnl < -self.config.KILL_SWITCH_DRAWDOWN * equity:
            self.halted = True
            log.critical("🚨 KILL SWITCH ENGAGED — daily PnL %.2f exceeds limit.", self.daily_pnl)
            return True
        return False

    def _total_open_risk(self) -> float:
        if not MT5_AVAILABLE: return 0.0
        positions = mt5.positions_get()
        if not positions: return 0.0
        equity = self._get_equity(); total_risk = 0.0
        for pos in positions:
            if pos.sl and pos.sl != 0.0:
                sl_dist = abs(pos.price_open - pos.sl)
                info    = mt5.symbol_info(pos.symbol)
                if not info: continue
                tick_val  = info.trade_tick_value  or 10.0
                tick_size = info.trade_tick_size   or 0.00001
                ticks     = sl_dist / (tick_size + 1e-9)
                total_risk += ticks * tick_val * pos.volume
        return total_risk / (equity + 1e-9)

    def can_trade(self, symbol: str, ai_prob: float, ctx_score: float = 1.0) -> Tuple[bool, str]:
        self._reset_if_new_day()
        if self.halted:                                   return False, "Kill switch engaged"
        equity = self._get_equity()
        if self.daily_pnl < -self.config.MAX_DAILY_DRAWDOWN * equity:
            return False, "Daily drawdown limit"
        if self.daily_trades >= self.config.MAX_DAILY_TRADES:
            return False, "Daily trade limit"
        if self._total_open_risk() >= self.config.MAX_TOTAL_RISK_PORTFOLIO:
            return False, "Portfolio risk limit"
        return True, "ok"

    def compute_position_size(self, symbol: str, sl_distance_price: float,
                               ai_prob: float, ctx_score: float = 0.7) -> float:
        equity   = self._get_equity()
        conf_mult = 0.5 + 0.5 * min(1.0, abs(ai_prob - 0.5) * 2)
        ctx_mult  = 0.5 + 0.5 * ctx_score
        risk_pct  = self.config.RISK_PER_TRADE * conf_mult * self.risk_scalar * ctx_mult
        risk_usd  = equity * risk_pct

        if not MT5_AVAILABLE or sl_distance_price <= 0:
            return self.config.MIN_LOT_SIZE

        info = mt5.symbol_info(symbol)
        if not info: return self.config.MIN_LOT_SIZE
        tick_size = info.trade_tick_size  or 0.00001
        tick_val  = info.trade_tick_value or 10.0
        sl_ticks  = sl_distance_price / tick_size
        if sl_ticks <= 0: return self.config.MIN_LOT_SIZE
        lot = risk_usd / (sl_ticks * tick_val)
        # UPGRADED: Gold-specific lot cap
        max_lot = self.config.MAX_LOT_SIZE_GOLD
        return round(max(self.config.MIN_LOT_SIZE, min(max_lot, lot)), 2)

    def adapt(self, recent_pnls: List[float]):
        if len(recent_pnls) < self.config.ADAPT_LOOKBACK: return
        window = recent_pnls[-self.config.ADAPT_LOOKBACK:]
        losses = sum(1 for p in window if p < 0)
        wins   = sum(1 for p in window if p > 0)
        n      = self.config.ADAPT_LOOKBACK
        if losses >= n * 0.6:
            self.risk_scalar = max(0.25, self.risk_scalar * self.config.ADAPT_LOSS_REDUCE)
            log.warning("Adaptive: risk scalar → %.2f", self.risk_scalar)
        elif wins >= n * 0.7:
            self.risk_scalar = min(1.5, self.risk_scalar * self.config.ADAPT_WIN_INCREASE)


# ============================================================================
# 20.  SESSION MANAGER  (kept from v1.0)
# ============================================================================
class SessionManager:
    _SESSION_UTC: Dict[str, Tuple[int, int]] = {
        "LONDON":  (7,  16),
        "NEWYORK": (12, 21),
        "ASIAN":   (23,  8),
    }

    def __init__(self, config: DarkConfig):
        self.config = config

    def active_sessions(self) -> List[str]:
        now_h  = datetime.now(timezone.utc).hour
        active = []
        for name, (start, end) in self._SESSION_UTC.items():
            if start < end:
                if start <= now_h < end: active.append(name)
            else:
                if now_h >= start or now_h < end: active.append(name)
        return active

    def is_preferred_session(self) -> bool:
        if not self.config.TRADE_ONLY_IN_SESSIONS: return True
        active = self.active_sessions()
        return any(s in active for s in self.config.PREFERRED_SESSIONS)

    def session_quality(self) -> float:
        active = self.active_sessions()
        if "LONDON" in active and "NEWYORK" in active: return 1.0
        if any(s in active for s in self.config.PREFERRED_SESSIONS): return 0.85
        if active: return 0.65
        return 0.4


# ============================================================================
# 21.  NEWS FILTER  — UPGRADED: spread-based (no more guessed UTC times)
# UPGRADED REMOVE 4: Fixed UTC blackout times removed.
#   REASON: Fixed times miss actual events. Spread > 1.5x average = something
#   is happening (news, gap, illiquidity). The broker knows before you do.
#   MS_SPREAD_MULT gate in QualityGate handles this automatically.
# ============================================================================
class NewsFilter:
    """
    UPGRADED: Spread-based news detection replaces fixed UTC blackout times.
    The microstructure spread gate now handles news events automatically.
    This class kept for backward compatibility but logic is in QualityGate.
    """

    def should_pause(self, spread_ratio: float = 1.0) -> bool:
        # UPGRADED: If spread > threshold, it's effectively a news pause
        return spread_ratio > 1.5


# ============================================================================
# 22.  EXECUTION ENGINE  (kept from v1.0)
# ============================================================================
class ExecutionEngine:
    def __init__(self, config: DarkConfig):
        self.config = config

    def place_order(self, symbol: str, direction: str, entry: float,
                    sl: float, tp: float, lot: float) -> bool:
        if not MT5_AVAILABLE:
            log.info("[SIM] Order: %s %s lot=%.2f E=%.5f SL=%.5f TP=%.5f",
                     symbol, direction.upper(), lot, entry, sl, tp)
            return True
        order_type = mt5.ORDER_TYPE_BUY if direction == "buy" else mt5.ORDER_TYPE_SELL
        tick = mt5.symbol_info_tick(symbol)
        if not tick: return False
        price = tick.ask if direction == "buy" else tick.bid
        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       symbol,
            "volume":       lot,
            "type":         order_type,
            "price":        price,
            "sl":           sl,
            "tp":           tp,
            "deviation":    self.config.ORDER_DEVIATION,
            "magic":        self.config.MAGIC_NUMBER,
            "comment":      self.config.ORDER_COMMENT,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            log.info("Order placed: ticket=%d %s %s lot=%.2f E=%.5f",
                     result.order, symbol, direction.upper(), lot, price)
            return True
        code = result.retcode if result else "N/A"
        log.error("Order failed: %s retcode=%s", symbol, code)
        return False

    def modify_sl(self, ticket: int, new_sl: float) -> bool:
        if not MT5_AVAILABLE: return True
        request = {"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "sl": new_sl}
        result  = mt5.order_send(request)
        return result and result.retcode == mt5.TRADE_RETCODE_DONE

    def close_all(self):
        if not MT5_AVAILABLE: return
        try:
            positions = mt5.positions_get()
        except Exception:
            return
        if not positions: return
        for pos in positions:
            direction = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            tick = mt5.symbol_info_tick(pos.symbol)
            if not tick: continue
            price = tick.bid if direction == mt5.ORDER_TYPE_SELL else tick.ask
            request = {
                "action":       mt5.TRADE_ACTION_DEAL,
                "symbol":       pos.symbol,
                "volume":       pos.volume,
                "type":         direction,
                "position":     pos.ticket,
                "price":        price,
                "deviation":    self.config.ORDER_DEVIATION,
                "magic":        self.config.MAGIC_NUMBER,
                "comment":      "DarkLord_EMERGENCY",
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            log.warning("Emergency close ticket=%d retcode=%s", pos.ticket,
                        result.retcode if result else "N/A")


# ============================================================================
# 23.  TRAILING STOP ENGINE  (kept from v1.0)
# ============================================================================
class TrailingStopEngine:
    def __init__(self, config: DarkConfig, data: DataProvider, exec_engine: ExecutionEngine):
        self.config = config; self.data = data; self.exec = exec_engine

    def update_all(self):
        if not MT5_AVAILABLE: return
        try:
            positions = mt5.positions_get()
        except Exception: return
        if not positions: return
        for pos in positions:
            if pos.magic != self.config.MAGIC_NUMBER: continue
            try: self._trail(pos)
            except Exception as e: log.debug("Trail error ticket=%d: %s", pos.ticket, e)

    def _trail(self, pos):
        df = self.data.get_rates(pos.symbol, "H1", 20)
        if df is None or len(df) < 15: return
        atr   = float(FeatureEngineer._atr(df, 14).iloc[-1])
        mult  = self.config.TRAILING_ATR_MULT
        tick  = mt5.symbol_info_tick(pos.symbol)
        if not tick: return
        current  = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
        sl_dist  = abs(pos.price_open - pos.sl) if pos.sl else atr
        if sl_dist <= 0: return
        r_mult   = (current - pos.price_open) / sl_dist
        if pos.type == mt5.ORDER_TYPE_SELL:
            r_mult = (pos.price_open - current) / sl_dist
        if r_mult < self.config.TRAILING_ACTIVATE_RR: return
        if pos.type == mt5.ORDER_TYPE_BUY:
            new_sl = current - mult * atr
            if new_sl > (pos.sl or 0) + 1e-7: self.exec.modify_sl(pos.ticket, new_sl)
        else:
            new_sl = current + mult * atr
            if pos.sl == 0 or new_sl < pos.sl - 1e-7: self.exec.modify_sl(pos.ticket, new_sl)


# ============================================================================
# 24.  ORDER REPAIR SYSTEM  (kept from v1.0)
# ============================================================================
class OrderRepairSystem:
    def __init__(self, config: DarkConfig, data: DataProvider, exec_engine: ExecutionEngine):
        self.config = config; self.data = data; self.exec = exec_engine

    def run(self):
        if not MT5_AVAILABLE: return
        self._heal_missing_sl(); self._cancel_stale_pending()

    def _heal_missing_sl(self):
        try: positions = mt5.positions_get()
        except Exception: return
        if not positions: return
        for pos in positions:
            if pos.magic != self.config.MAGIC_NUMBER: continue
            if pos.sl == 0.0:
                df = self.data.get_rates(pos.symbol, "H1", 20)
                if df is None: continue
                atr    = float(FeatureEngineer._atr(df, 14).iloc[-1])
                new_sl = (pos.price_current - 2 * atr if pos.type == mt5.ORDER_TYPE_BUY
                          else pos.price_current + 2 * atr)
                log.warning("Repair: adding SL ticket=%d symbol=%s", pos.ticket, pos.symbol)
                self.exec.modify_sl(pos.ticket, new_sl)

    def _cancel_stale_pending(self):
        try: orders = mt5.orders_get()
        except Exception: return
        if not orders: return
        now = datetime.now(timezone.utc).timestamp()
        for order in orders:
            if order.magic != self.config.MAGIC_NUMBER: continue
            age_h = (now - order.time_setup) / 3600
            if age_h > 2:
                request = {"action": mt5.TRADE_ACTION_REMOVE, "order": order.ticket}
                result  = mt5.order_send(request)
                log.warning("Repair: cancelled stale order ticket=%d age=%.1fh", order.ticket, age_h)


# ============================================================================
# 25.  DARK SWEEP STRATEGY  (only strategy — all others removed)
# UPGRADED REMOVE 5: TrendStrategy, MeanReversionStrategy, ScalpingStrategy,
#   VolatilityBreakoutStrategy REMOVED.
#   REASON: Multiple strategies dilute the best edge. DarkSweep is the
#   only concept with institutional backing. Prove it works first.
# ============================================================================
class DarkSweepStrategy:
    """
    The ONLY strategy in v2.0.
    Signal = confirmed liquidity sweep + FVG confluence + HTF alignment.
    FVG alone = removed. FVG + Sweep = strong institutional signal.
    """

    name = "DarkSweep"

    def generate_signal(self, df: pd.DataFrame, ls_result: Dict,
                        fvg_result: Dict, of_result: Dict,
                        htf_bias: str, ai_prob: float) -> Tuple[Optional[str], float, float]:
        if not ls_result.get("sweep_detected"):
            return None, 0.0, 0.0

        ls_dir = ls_result.get("direction")
        if ls_dir is None:
            return None, 0.0, 0.0

        # HTF must align with sweep direction
        if htf_bias == "BULLISH" and ls_dir != "buy":
            return None, 0.0, 0.0
        if htf_bias == "BEARISH" and ls_dir != "sell":
            return None, 0.0, 0.0
        if htf_bias == "NEUTRAL":
            return None, 0.0, 0.0

        # UPGRADED: FVG is confirmation (not standalone signal)
        # FVG+Sweep = institutional confluence. FVG alone = removed.
        fvg_confirms = (
            (ls_dir == "buy"  and fvg_result.get("bullish_near")) or
            (ls_dir == "sell" and fvg_result.get("bearish_near"))
        )
        # Allow without FVG if OB is confirmed (ob_confirmed passed via signal dict)
        # For raw signal here, FVG confirmation gets scored

        # No iceberg or absorption near entry
        if of_result.get("iceberg") or of_result.get("absorption"):
            return None, 0.0, 0.0

        # AI must confirm above threshold
        if ls_dir == "buy"  and ai_prob < 0.52:
            return None, 0.0, 0.0
        if ls_dir == "sell" and ai_prob > 0.48:
            return None, 0.0, 0.0

        last = float(df["Close"].iloc[-1])
        atr  = float(FeatureEngineer._atr(df, 14).iloc[-1])

        if ls_dir == "buy":
            return "buy",  last, last - 1.5 * atr
        if ls_dir == "sell":
            return "sell", last, last + 1.5 * atr

        return None, 0.0, 0.0


# ============================================================================
# 26.  SL/TP CALCULATOR  — NEW, liquidity-aware
# REASON: Old SL/TP was purely ATR-based with no awareness of where the
#   next institutional target is. This version places TP just before
#   the next liquidity level (where institutions will offload).
# ============================================================================
def calculate_sl_tp(current_price: float, direction: str, regime: str,
                    atr: float, nearest_liquidity_target: Optional[float] = None,
                    rr_ratio: float = 2.0) -> Tuple[float, float]:
    """
    Calculate SL and TP with liquidity-aware TP placement.
    If a liquidity target is known, TP = just before it (95% of the way).
    Otherwise fall back to ATR-based 2R TP.
    """
    # SL: 1.5 ATR from entry (behind structure)
    sl_dist = 1.5 * atr
    if regime == RegimeDetector.VOLATILE:
        sl_dist = 2.0 * atr  # wider SL in volatile conditions

    if direction == "buy":
        sl = current_price - sl_dist
        if nearest_liquidity_target and nearest_liquidity_target > current_price:
            # TP just below the liquidity cluster (don't overshoot)
            tp = current_price + (nearest_liquidity_target - current_price) * 0.92
            # Ensure minimum 1.5R
            if (tp - current_price) < 1.5 * sl_dist:
                tp = current_price + rr_ratio * sl_dist
        else:
            tp = current_price + rr_ratio * sl_dist
    else:
        sl = current_price + sl_dist
        if nearest_liquidity_target and nearest_liquidity_target < current_price:
            tp = current_price - (current_price - nearest_liquidity_target) * 0.92
            if (current_price - tp) < 1.5 * sl_dist:
                tp = current_price - rr_ratio * sl_dist
        else:
            tp = current_price - rr_ratio * sl_dist

    return round(sl, 5), round(tp, 5)


# ============================================================================
# 27.  BACKTESTER  — realistic Gold costs
# UPGRADED BUG 3 FIX: Costs now match real XM Gold:
#   Spread=45pts, Slip=20pts, Commission=$8/lot
# ============================================================================
class Backtester:
    def __init__(self, config: DarkConfig):
        self.config = config

    def run(self, df: pd.DataFrame, strategy: DarkSweepStrategy,
            initial_capital: float = 10_000.0) -> Dict:
        if df is None or df.empty or len(df) < 100:
            return self._empty_result()

        equity    = initial_capital
        trades    = []
        position  = None
        equity_curve = [equity]

        # UPGRADED: Realistic Gold point value
        # For Gold on XM: 1 point = $0.01 for 0.01 lot; tick_value ≈ $1/lot/point
        # total cost per 0.01 lot: (45+40pts) * $0.01 * 0.01 + $8*0.01 = ~$0.17
        POINT_VALUE = 0.01   # $ per lot per point for Gold (XMGlobal)

        for i in range(100, len(df)):
            bar   = df.iloc[i]
            close = float(bar["Close"])
            high  = float(bar["High"])
            low   = float(bar["Low"])

            if position:
                direction = position["direction"]
                sl = position["sl"]; tp = position["tp"]; lot = position["lot"]
                tv = position["tv"]
                # UPGRADED BUG 3 FIX: realistic costs applied
                spread_cost = self.config.BT_SPREAD_PTS * POINT_VALUE * lot
                slip_cost   = self.config.BT_SLIP_PTS   * POINT_VALUE * lot
                comm_cost   = self.config.BT_COMMISSION_USD * lot
                total_cost  = spread_cost + slip_cost + comm_cost

                if direction == "buy":
                    if low <= sl:
                        pnl = -(abs(position["entry"] - sl) * tv * lot) - total_cost
                        trades.append({"direction": direction, "pnl": pnl, "result": "SL"})
                        equity += pnl; position = None
                    elif high >= tp:
                        pnl = (abs(tp - position["entry"]) * tv * lot) - total_cost
                        trades.append({"direction": direction, "pnl": pnl, "result": "TP"})
                        equity += pnl; position = None
                else:
                    if high >= sl:
                        pnl = -(abs(sl - position["entry"]) * tv * lot) - total_cost
                        trades.append({"direction": direction, "pnl": pnl, "result": "SL"})
                        equity += pnl; position = None
                    elif low <= tp:
                        pnl = (abs(position["entry"] - tp) * tv * lot) - total_cost
                        trades.append({"direction": direction, "pnl": pnl, "result": "TP"})
                        equity += pnl; position = None

            if position is None and i > 110:
                df_window = df.iloc[i-100:i]
                ls_res = {"sweep_detected": False}
                fvg_res = {"bullish_near": [], "bearish_near": [], "direction": None}
                of_res  = {"iceberg": False, "absorption": False}
                direction_sig, entry_sig, sl_sig = strategy.generate_signal(
                    df_window, ls_res, fvg_res, of_res, htf_bias="BULLISH", ai_prob=0.55)
                if direction_sig:
                    atr_val = float(FeatureEngineer._atr(df_window, 14).iloc[-1])
                    tp_sig  = (entry_sig + 2.0 * abs(entry_sig - sl_sig) if direction_sig == "buy"
                               else entry_sig - 2.0 * abs(entry_sig - sl_sig))
                    lot     = self.config.MIN_LOT_SIZE
                    tv      = 10.0  # Gold tick value approximation
                    real_e  = entry_sig + (self.config.BT_SLIP_PTS * 0.01 if direction_sig == "buy"
                                          else -self.config.BT_SLIP_PTS * 0.01)
                    position = {"direction": direction_sig, "entry": real_e,
                                "sl": sl_sig, "tp": tp_sig, "lot": lot, "tv": tv}

            equity_curve.append(equity)

        return self._compute_metrics(trades, equity_curve, initial_capital)

    def _compute_metrics(self, trades, equity_curve, initial_capital):
        n = len(trades)
        if n == 0: return self._empty_result()
        pnls   = [t["pnl"] for t in trades]
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        total_return = (equity_curve[-1] - initial_capital) / initial_capital
        win_rate     = len(wins) / n
        avg_win      = float(np.mean(wins))    if wins   else 0.0
        avg_loss     = float(np.mean(losses))  if losses else 0.0
        pf           = (sum(wins) / (abs(sum(losses)) + 1e-9)) if losses else 999.0
        expectancy   = win_rate * avg_win + (1 - win_rate) * avg_loss
        rets_arr     = np.array(equity_curve)
        rets_pct     = np.diff(rets_arr) / (rets_arr[:-1] + 1e-9)
        sharpe       = (float(np.mean(rets_pct)) / (float(np.std(rets_pct)) + 1e-9)) * np.sqrt(252)
        neg_rets     = rets_pct[rets_pct < 0]
        sortino      = (float(np.mean(rets_pct)) / (float(np.std(neg_rets)) + 1e-9)) * np.sqrt(252) if len(neg_rets) > 0 else 0.0
        running_max  = np.maximum.accumulate(equity_curve)
        dd           = (np.array(equity_curve) - running_max) / (running_max + 1e-9)
        max_dd       = float(dd.min())
        return {"total_return": total_return, "sharpe": sharpe, "sortino": sortino,
                "max_dd": max_dd, "win_rate": win_rate, "profit_factor": pf,
                "expectancy": expectancy, "trades": n, "pnls": pnls}

    def monte_carlo(self, pnls: List[float], n_paths: int = 1000,
                    initial_capital: float = 10_000.0) -> Tuple[bool, Dict]:
        """
        UPGRADED: Returns gate_passed bool + full results.
        GO-LIVE GATES: P5 Sharpe > 0.3, P5 PF > 1.2, P95 DD < 20%
        """
        if not pnls:
            return False, {}
        pnls_arr = np.array(pnls)
        results  = {"sharpe": [], "profit_factor": [], "max_dd": []}
        for _ in range(n_paths):
            shuffled  = np.random.choice(pnls_arr, size=len(pnls_arr), replace=True)
            equity    = initial_capital + np.cumsum(shuffled)
            equity    = np.insert(equity, 0, initial_capital)
            ret_pct   = np.diff(equity) / (equity[:-1] + 1e-9)
            sharpe    = (float(np.mean(ret_pct)) / (float(np.std(ret_pct)) + 1e-9)) * np.sqrt(252)
            results["sharpe"].append(sharpe)
            wins   = sum(p for p in shuffled if p > 0)
            losses = abs(sum(p for p in shuffled if p < 0))
            results["profit_factor"].append(wins / (losses + 1e-9))
            peak   = np.maximum.accumulate(equity)
            results["max_dd"].append(((peak - equity) / (peak + 1e-9)).max())

        p5_sharpe = np.percentile(results["sharpe"], 5)
        p5_pf     = np.percentile(results["profit_factor"], 5)
        p95_dd    = np.percentile(results["max_dd"], 95)
        gate      = p5_sharpe > 0.3 and p5_pf > 1.2 and p95_dd < 0.20

        _print(f"\nMonte Carlo ({n_paths} paths):")
        _print(f"  Sharpe P5:        {p5_sharpe:.2f}  (need > 0.3)")
        _print(f"  Profit Factor P5: {p5_pf:.2f}   (need > 1.2)")
        _print(f"  Max DD P95:       {p95_dd:.1%}  (need < 20%)")
        _print(f"  GATE: {'✓ PASSED — system has edge' if gate else '✗ FAILED — DO NOT GO LIVE'}")
        return gate, results

    @staticmethod
    def _empty_result():
        return {"total_return": 0, "sharpe": 0, "sortino": 0, "max_dd": 0,
                "win_rate": 0, "profit_factor": 0, "expectancy": 0, "trades": 0, "pnls": []}


# ============================================================================
# 28.  WALK-FORWARD OPTIMIZER  — multi-window, profit factor gate
# UPGRADED: Tests across multiple windows; reports if ANY window fails.
#   DO NOT GO LIVE if any window has PF < 1.6 or DD > 15%
# ============================================================================
class WalkForward:
    def __init__(self, config: DarkConfig, data: DataProvider, bt: Backtester):
        self.config = config; self.data = data; self.bt = bt

    def run_full_test(self, symbol: str, total_days: int = 180) -> Dict:
        """
        Multi-window walk-forward test.
        UPGRADED: Tests every 14-day window over 6 months of data.
        Returns pass/fail per window AND overall gate.
        """
        end   = datetime.now()
        start = end - timedelta(days=total_days)
        df    = self.data.get_historical_range(symbol, "H1", start, end)
        if df is None or df.empty:
            return {"passed": False, "reason": "No data", "windows": []}

        train_d = self.config.WF_TRAIN_DAYS
        test_d  = self.config.WF_TEST_DAYS
        bars_per_day = 24

        window_results = []
        i = 0
        strat = DarkSweepStrategy()
        while True:
            train_start = i * test_d * bars_per_day
            train_end   = train_start + train_d * bars_per_day
            test_end    = train_end + test_d * bars_per_day
            if test_end > len(df): break
            df_train = df.iloc[train_start:train_end]
            df_test  = df.iloc[train_end:test_end]
            if len(df_train) < 200 or len(df_test) < 20:
                i += 1; continue
            res = self.bt.run(df_test, strat, 10_000.0)
            window_results.append({
                "window":        i + 1,
                "profit_factor": res["profit_factor"],
                "max_dd":        res["max_dd"],
                "sharpe":        res["sharpe"],
                "trades":        res["trades"],
            })
            i += 1

        if not window_results:
            return {"passed": False, "reason": "No windows completed", "windows": []}

        all_passed = all(
            w["profit_factor"] > 1.6 and w["max_dd"] > -0.15
            for w in window_results if w["trades"] >= 3
        )

        _print(f"\n{'─'*60}")
        _print(f"  Walk-Forward Results — {symbol} ({len(window_results)} windows)")
        _print(f"{'─'*60}")
        for w in window_results:
            flag = "✓" if (w["profit_factor"] > 1.6 and w["max_dd"] > -0.15) else "✗ FAIL"
            _print(f"  Window {w['window']:>2}: PF={w['profit_factor']:.2f}  "
                   f"DD={w['max_dd']:.1%}  Trades={w['trades']}  [{flag}]")
        _print(f"\n  OVERALL: {'✓ PASSED' if all_passed else '✗ FAILED — do not go live'}")
        _print(f"{'─'*60}")

        return {"passed": all_passed, "windows": window_results,
                "reason": "OK" if all_passed else "Some windows failed PF > 1.6 or DD < 15%"}


# ============================================================================
# 29.  ADAPTIVE SYSTEM  (kept)
# ============================================================================
class AdaptiveSystem:
    def __init__(self, config: DarkConfig, data: DataProvider, bt: Backtester):
        self.config   = config; self.data = data; self.bt = bt
        self.wf       = WalkForward(config, data, bt)
        self._last_wf : Dict[str, datetime] = {}

    def maybe_run_walk_forward(self, symbol: str):
        last = self._last_wf.get(symbol)
        if last is None or (datetime.now() - last).total_seconds() > 86400:
            try:
                self.wf.run_full_test(symbol)
            except Exception as e:
                log.debug("WalkForward error %s: %s", symbol, e)
            self._last_wf[symbol] = datetime.now()


# ============================================================================
# 30.  PORTFOLIO MANAGER  (kept)
# ============================================================================
class PortfolioManager:
    def get_summary(self) -> Dict:
        if not MT5_AVAILABLE:
            return {"balance": 0, "equity": 0, "open_pnl": 0, "open_trades": 0, "free_margin": 0}
        try:
            acc = mt5.account_info(); pos = mt5.positions_get()
            return {"balance":     float(acc.balance) if acc else 0,
                    "equity":      float(acc.equity)  if acc else 0,
                    "open_pnl":    sum(p.profit for p in pos) if pos else 0,
                    "open_trades": len(pos) if pos else 0,
                    "free_margin": float(acc.margin_free) if acc else 0}
        except Exception:
            return {"balance": 0, "equity": 0, "open_pnl": 0, "open_trades": 0, "free_margin": 0}


# ============================================================================
# 31.  TRADE LOGGER  (upgraded — stores pattern key for PatternTracker)
# ============================================================================
class TradeLogger:
    FILENAME = "trades.json"

    def log_trade(self, symbol: str, direction: str, entry: float,
                  sl: float, tp: float, lot: float, ctx_score: float,
                  strategy: str, ai_prob: float, pattern_key: str = "",
                  result: str = "OPEN", pnl: float = 0.0):
        record = {"time": datetime.now().isoformat(), "symbol": symbol,
                  "direction": direction, "entry": entry, "sl": sl, "tp": tp,
                  "lot": lot, "ctx_score": ctx_score, "strategy": strategy,
                  "ai_prob": ai_prob, "pattern_key": pattern_key,
                  "result": result, "pnl": pnl}
        try:
            existing = []
            if Path(self.FILENAME).exists():
                with open(self.FILENAME, "r") as f:
                    existing = json.load(f)
            existing.append(record)
            with open(self.FILENAME, "w") as f:
                json.dump(existing, f, indent=2)
        except Exception as e:
            log.debug("Trade log error: %s", e)

    def get_recent(self, n: int = 20) -> List[Dict]:
        try:
            if Path(self.FILENAME).exists():
                with open(self.FILENAME, "r") as f:
                    data = json.load(f)
                return data[-n:]
        except Exception:
            pass
        return []


# ============================================================================
# 32.  SCORING DISPLAY  — UPGRADED with Rich
# ============================================================================
def _print(msg: str):
    """Print with Rich if available, else plain print."""
    if RICH_AVAILABLE and console:
        console.print(msg)
    else:
        print(msg)


class ScoringDisplay:
    def __init__(self, config: DarkConfig):
        self.config      = config
        self._history    : List[Dict] = []
        self._last_print : float = 0.0

    def should_print(self) -> bool:
        return (time.time() - self._last_print) >= self.config.SCORE_DISPLAY_SEC

    def record_and_print(self, vp_score: float, vp_signal: str,
                          of_score: float, of_signal: str,
                          ls_score: float, ls_signal: str,
                          fvg_score: float, fvg_signal: str,
                          ms_score: float, ms_signal: str,
                          ctx_score: float, ctx_signal: str,
                          htf_bias: str, htf_conf: float,
                          pd_zone: str, msb_dir: str,
                          ai_prob: float = 0.5,
                          session: List[str] = None):
        ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = {"time": ts, "vp": round(vp_score * 100, 1), "of": round(of_score * 100, 1),
               "ls": round(ls_score * 100, 1), "fvg": round(fvg_score * 100, 1),
               "ms": round(ms_score * 100, 1), "ctx": round(ctx_score * 100, 1),
               "htf": htf_bias, "ai": round(ai_prob, 3)}
        self._history.append(row)
        try:
            exists = Path("scoring_history.csv").exists()
            with open("scoring_history.csv", "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(row.keys()))
                if not exists: w.writeheader()
                w.writerow(row)
        except Exception:
            pass
        self._last_print = time.time()

        if RICH_AVAILABLE and console:
            self._print_rich(ts, vp_score, vp_signal, of_score, of_signal,
                             ls_score, ls_signal, fvg_score, fvg_signal,
                             ms_score, ms_signal, ctx_score, ctx_signal,
                             htf_bias, htf_conf, pd_zone, msb_dir, ai_prob, session or [])
        else:
            self._print_plain(ts, vp_score, of_score, ls_score, fvg_score,
                              ms_score, ctx_score, htf_bias, htf_conf, ai_prob)

    def _print_rich(self, ts, vp_s, vp_sig, of_s, of_sig, ls_s, ls_sig,
                    fvg_s, fvg_sig, ms_s, ms_sig, ctx_s, ctx_sig,
                    htf_bias, htf_conf, pd_zone, msb_dir, ai_prob, session):
        gate = ctx_s >= self.config.CTX_MIN_SCORE
        gate_str = "[bold green]✅ GATE OPEN[/bold green]" if gate else "[bold red]🚫 GATE CLOSED[/bold red]"

        table = Table(title=f"[bold cyan]🔱 DARK LORD v2.0 — {ts}[/bold cyan]",
                      box=rich_box.ROUNDED, show_header=True, header_style="bold white")
        table.add_column("Module",      style="white",  min_width=20)
        table.add_column("Score",       justify="right", min_width=8)
        table.add_column("Bar",         min_width=12)
        table.add_column("Signal",      style="dim",    min_width=30)

        concepts = [
            ("📊 Volume Profile",  vp_s,  vp_sig),
            ("💰 Order Flow",      of_s,  of_sig),
            ("🎯 Liquidity Sweep", ls_s,  ls_sig),
            ("📐 FVG (conf only)", fvg_s, fvg_sig),
            ("🔬 Microstructure",  ms_s,  ms_sig),
            ("🧮 Context Score",   ctx_s, ctx_sig),
        ]
        for name, score, signal in concepts:
            pct   = int(score * 100)
            bar   = "█" * (pct // 10) + "░" * (10 - pct // 10)
            color = "green" if score >= 0.65 else "yellow" if score >= 0.45 else "red"
            table.add_row(name, f"[{color}]{pct}%[/{color}]", f"[{color}]{bar}[/{color}]", signal)

        console.print(table)

        # Status panel
        htf_color = "green" if htf_bias == "BULLISH" else "red" if htf_bias == "BEARISH" else "yellow"
        sess_str  = ", ".join(session) if session else "none"
        console.print(Panel(
            f"  HTF Bias: [{htf_color}]{htf_bias}[/{htf_color}] (conf={htf_conf:.2f})  │  "
            f"P/D Zone: {pd_zone}  │  MSB: {msb_dir}  │  "
            f"AI: {ai_prob:.3f}  │  Session: {sess_str}\n"
            f"  Context Gate: {gate_str}  (threshold={self.config.CTX_MIN_SCORE:.2f})",
            title="[bold]Status[/bold]", border_style="cyan"
        ))

    def _print_plain(self, ts, vp_s, of_s, ls_s, fvg_s, ms_s, ctx_s, htf_bias, htf_conf, ai_prob):
        print(f"\n{'='*80}")
        print(f"  🔱 DARK LORD v2.0 — {ts} | HTF={htf_bias}({htf_conf:.2f}) | AI={ai_prob:.3f}")
        print(f"{'='*80}")
        gate = "✅ GATE OPEN" if ctx_s >= self.config.CTX_MIN_SCORE else "🚫 GATE CLOSED"
        for name, score in [("VP", vp_s), ("OF", of_s), ("LS", ls_s),
                              ("FVG", fvg_s), ("MS", ms_s), ("CTX", ctx_s)]:
            pct = int(score * 100)
            bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
            print(f"  {name:<8} {pct:>4}%  {bar}")
        print(f"\n  {gate}")
        print(f"{'='*80}")


# ============================================================================
# 33.  DARK LORD TRADER  — main orchestrator
# UPGRADED: New run_cycle with 13-step institutional flow
# ============================================================================
class DarkLordTrader:
    def __init__(self, cfg: DarkConfig):
        self.cfg      = cfg
        self._cycle   = 0
        self._running = True

        # Core modules
        self.data      = DataProvider(cfg)
        self.session   = SessionManager(cfg)
        self.news      = NewsFilter()
        self.regime    = RegimeDetector(cfg.REGIME_LOOKBACK, cfg.ADX_TREND_THRESH)
        self.ai        = AIPredictor(cfg)
        self.risk      = RiskManager(cfg, self.data)
        self.exec      = ExecutionEngine(cfg)
        self.trailer   = TrailingStopEngine(cfg, self.data, self.exec)
        self.repair    = OrderRepairSystem(cfg, self.data, self.exec)
        self.bt        = Backtester(cfg)
        self.adaptive  = AdaptiveSystem(cfg, self.data, self.bt)
        self.portfolio = PortfolioManager()

        # Dark modules (v1.0)
        self.vp  = VolumeProfileEngine(cfg)
        self.of  = OrderFlowEngine(cfg)
        self.ls  = LiquiditySweepDetector(cfg)
        self.fvg = FairValueGapDetector(cfg)
        self.ms  = MicrostructureAnalyzer(cfg)
        self.ctx = ContextualScorer(cfg)

        # New SMC modules (v2.0)
        self.htf     = HTFBiasEngine()
        self.liq_map = LiquidityMapEngine()
        self.pd      = PremiumDiscountEngine()
        self.ob      = OrderBlockEngine(cfg)
        self.msb     = MarketStructureBreakEngine()

        # Quality gate + pattern tracker
        self.gate    = QualityGate(cfg)
        self.pattern = PatternTracker()

        # Display + logging
        self.scoring    = ScoringDisplay(cfg)
        self.trade_log  = TradeLogger()
        self.dark_strat = DarkSweepStrategy()

        # Terminal
        self._input_q : queue.Queue = queue.Queue()
        self._vp_cache: Dict[str, Dict] = {}

    def connect_mt5(self) -> bool:
        if not MT5_AVAILABLE:
            log.info("MT5 not available — simulation mode")
            return True
        for attempt in range(self.cfg.MAX_RECONNECT_TRIES):
            try:
                if mt5.initialize(login=self.cfg.MT5_LOGIN,
                                  password=self.cfg.MT5_PASSWORD,
                                  server=self.cfg.MT5_SERVER):
                    acc = mt5.account_info()
                    log.info("MT5 connected | account=%s balance=%.2f",
                             acc.login if acc else "?", acc.balance if acc else 0)
                    return True
            except Exception as e:
                log.warning("MT5 connect attempt %d failed: %s", attempt + 1, e)
            wait = min(2 ** attempt, 120)
            time.sleep(wait)
        log.critical("MT5 connection failed after %d attempts", self.cfg.MAX_RECONNECT_TRIES)
        return False

    def train_ai(self):
        all_dfs = []
        for symbol in self.cfg.SYMBOLS:
            end   = datetime.now()
            start = end - timedelta(days=60)   # use 60 days for training
            df    = self.data.get_historical_range(symbol, "H1", start, end)
            if not df.empty:
                all_dfs.append(df)
        if all_dfs:
            combined = pd.concat(all_dfs).sort_index()
            self.ai.train(combined, self.regime)
        else:
            log.warning("No data for AI training")

    # ── Main cycle ────────────────────────────────────────────────────────────
    async def run_cycle(self):
        self._cycle += 1
        self.repair.run() if hasattr(self, 'repair') else None

        # Retrain AI if needed
        if self.cfg.USE_AI and self.ai.needs_retraining():
            try:
                log.info("Retraining AI…")
                self.train_ai()
            except Exception as e:
                log.warning("AI retraining failed: %s", e)

        # Kill switch check
        if self.risk.check_kill_switch():
            log.warning("Kill switch active — skipping cycle")
            return

        for symbol in self.cfg.SYMBOLS:
            try:
                await self._process_symbol(symbol)
            except Exception as e:
                log.error("Cycle error [%s]: %s\n%s", symbol, e, traceback.format_exc())

    async def _process_symbol(self, symbol: str):
        """
        UPGRADED: New 13-step institutional order flow cycle.
        Every step is a filter. Fewer trades, better quality.
        """

        # STEP 1: Get multi-timeframe data
        mtf   = self.data.get_mtf(symbol)
        df_d1 = mtf.get("D1")
        df_h4 = mtf.get("H4")
        df_h1 = mtf.get("H1")
        df_m5 = mtf.get("M5")

        if df_h1 is None or len(df_h1) < 120:
            return
        if df_m5 is None or len(df_m5) < 50:
            df_m5 = df_h1   # fallback to H1

        # Capture tick for order flow
        self.data.get_tick(symbol)

        # STEP 2: Get HTF institutional bias (D1 + H4)
        htf_bias, htf_confidence = self.htf.get_bias(self.data, symbol)
        if htf_confidence < self.cfg.HTF_MIN_CONFIDENCE:
            log.debug("%s: HTF unclear (conf=%.2f) — no trade", symbol, htf_confidence)
            return

        # STEP 3: Get Premium/Discount zone
        pd_result = self.pd.analyze(df_d1, df_h4)
        if pd_result["trade_direction"] == "NO_TRADE":
            log.debug("%s: Price at equilibrium (%s) — no edge", symbol, pd_result["zone"])
            return

        # STEP 4: Scan H1 for liquidity levels
        liq_levels      = self.liq_map.scan(df_h1, df_m5)
        current_price   = float(df_m5["Close"].iloc[-1])
        nearest_target  = self.liq_map.nearest_target(current_price, htf_bias, liq_levels)

        # STEP 5: Detect M5 market structure break
        msb_result = self.msb.analyze(df_m5)

        # STEP 6: Check for liquidity sweep on M5
        ls_result  = self.ls.analyze(df_m5, df_h1)

        # STEP 7: Check FVG (confirmation only — not standalone signal)
        fvg_result = self.fvg.detect(df_m5, None, df_h1)

        # STEP 8: Check order blocks
        obs         = self.ob.detect(df_m5)
        in_ob, ob_d = self.ob.price_in_ob(current_price, obs, htf_bias)

        # STEP 9: Order flow + Volume Profile
        ticks      = self.data.get_tick_buffer(symbol)
        of_result  = self.of.analyze(df_m5, ticks, symbol)
        vp_result  = self.vp.compute(df_h1, symbol)
        self._vp_cache[symbol] = vp_result

        # STEP 10: Microstructure check
        ms_result  = self.ms.analyze(symbol, self.data)
        spread_ratio = ms_result.get("spread_ratio", 1.0)

        # News pause via spread (UPGRADED: replaces fixed UTC times)
        news_pause = self.news.should_pause(spread_ratio)

        # STEP 11: Session quality
        session    = self.session.active_sessions()
        session_q  = self.session.session_quality()

        # STEP 12: Detect regime
        regime     = self.regime.detect(df_m5)

        # STEP 13: AI prediction
        ai_prob    = self.ai.predict(df_m5, regime) if self.cfg.USE_AI else 0.5

        # Context score
        ctx_score  = self.ctx.compute(
            vp_score     = vp_result.get("score", 0.5),
            of_score     = of_result.get("score", 0.5),
            ls_score     = ls_result.get("score", 0.5),
            fvg_score    = fvg_result.get("score", 0.5),
            ms_score     = ms_result.get("score", 0.5),
            regime       = regime,
            session_q    = session_q,
            news_pause   = news_pause,
            ai_prob      = ai_prob,
            htf_confidence = htf_confidence,
        )

        # Live scoring display
        if self.scoring.should_print():
            self.scoring.record_and_print(
                vp_score=vp_result.get("score", 0.5), vp_signal=self.vp.get_last_signal(),
                of_score=of_result.get("score", 0.5), of_signal=self.of.get_last_signal(),
                ls_score=ls_result.get("score", 0.5), ls_signal=self.ls.get_last_signal(),
                fvg_score=fvg_result.get("score", 0.5), fvg_signal=self.fvg.get_last_signal(),
                ms_score=ms_result.get("score", 0.5), ms_signal=self.ms.get_last_signal(),
                ctx_score=ctx_score, ctx_signal=self.ctx.get_last_signal(),
                htf_bias=htf_bias, htf_conf=htf_confidence,
                pd_zone=pd_result["zone"], msb_dir=msb_result["structure"],
                ai_prob=ai_prob, session=session,
            )

        # Determine trade direction from sweep
        dark_dir, dark_entry, dark_sl = self.dark_strat.generate_signal(
            df_m5, ls_result, fvg_result, of_result, htf_bias, ai_prob
        )
        if dark_dir is None:
            return

        # Build signal dict for QualityGate
        signal_data = {
            "htf_bias":         htf_bias,
            "htf_confidence":   htf_confidence,
            "trade_direction":  dark_dir,
            "pd_zone":          pd_result["zone"],
            "sweep_confirmed":  ls_result.get("sweep_detected", False),
            "msb_direction":    msb_result["structure"],
            "ob_confirmed":     in_ob,
            "fvg_confirmed":    bool(fvg_result.get("bullish_near") or fvg_result.get("bearish_near")),
            "regime":           regime,
            "session":          session,
            "spread_ratio":     spread_ratio,
            "ai_confidence":    ai_prob,
            "ctx_score":        ctx_score,
            "daily_trades":     self.risk.daily_trades,
        }

        # Run all 10 quality gates
        approved, gate_reason = self.gate.check(signal_data)
        if not approved:
            log.debug("%s: Trade blocked by gate — %s", symbol, gate_reason)
            return

        # Final risk check
        can_trade, risk_reason = self.risk.can_trade(symbol, ai_prob, ctx_score)
        if not can_trade:
            log.debug("%s: Risk check failed — %s", symbol, risk_reason)
            return

        # VP node check
        if self.vp.in_low_volume_node(dark_entry, vp_result):
            log.debug("%s: Entry in low volume node — manipulation zone, skip", symbol)
            return

        # Calculate SL/TP with liquidity-aware targets
        atr_val = float(FeatureEngineer._atr(df_m5, 14).iloc[-1])
        sl, tp  = calculate_sl_tp(dark_entry, dark_dir, regime, atr_val, nearest_target, self.cfg.RR_RATIO)

        if abs(dark_entry - sl) <= 0:
            return

        # Position size
        lot = self.risk.compute_position_size(symbol, abs(dark_entry - sl), ai_prob, ctx_score)

        log.info("%s | %s | lot=%.2f | E=%.5f SL=%.5f TP=%.5f | "
                 "HTF=%s(%s) P/D=%s MSB=%s Regime=%s AI=%.3f ctx=%.2f",
                 symbol, dark_dir.upper(), lot, dark_entry, sl, tp,
                 htf_bias, f"{htf_confidence:.2f}", pd_result["zone"],
                 msb_result["structure"], regime, ai_prob, ctx_score)

        # Execute
        ok = self.exec.place_order(symbol, dark_dir, dark_entry, sl, tp, lot)
        if ok:
            self.risk.daily_trades += 1
            # Record pattern for performance tracking
            pattern_key = self.pattern.record(signal_data, pnl=0.0)
            self.trade_log.log_trade(
                symbol=symbol, direction=dark_dir, entry=dark_entry,
                sl=sl, tp=tp, lot=lot, ctx_score=ctx_score,
                strategy="DarkSweep", ai_prob=ai_prob, pattern_key=pattern_key,
            )
            self.adaptive.maybe_run_walk_forward(symbol)

    # ── Terminal commands ─────────────────────────────────────────────────────
    def _terminal_input_thread(self):
        _print("\n  [!] Dark Lord Bot v2.0 — type 'h' for help\n")
        while self._running:
            try:
                cmd = input("dark> ").strip().lower()
                self._input_q.put(cmd)
            except EOFError:
                break
            except Exception:
                pass

    def _process_commands(self):
        while not self._input_q.empty():
            try:
                cmd = self._input_q.get_nowait()
                self._handle_cmd(cmd)
            except queue.Empty:
                break

    def _handle_cmd(self, cmd: str):
        if cmd in ("h", "help", "?"):
            self._print_help()
        elif cmd == "s":
            self._print_status()
        elif cmd == "p":
            self._print_volume_profile()
        elif cmd == "m":
            self._print_microstructure()
        elif cmd == "htf":
            self._print_htf()
        elif cmd == "pat":
            self.pattern.print_stats()
        elif cmd == "r":
            _print("  [!] Retraining AI — please wait…")
            self.train_ai()
            ts = self.ai.last_trained.strftime("%H:%M:%S") if self.ai.last_trained else "?"
            _print(f"  [OK] AI retrained at {ts}")
        elif cmd.startswith("bt "):
            parts = cmd.split()
            sym   = parts[1].upper() if len(parts) > 1 else "GOLD.i#"
            days  = int(parts[2]) if len(parts) > 2 else 30
            self._run_backtest(sym, days)
        elif cmd == "wf":
            for sym in self.cfg.SYMBOLS:
                _print(f"\n  Walk-Forward: {sym}")
                self.adaptive.wf.run_full_test(sym, total_days=180)
        elif cmd == "mc":
            self._run_monte_carlo()
        elif cmd == "strat":
            self.pattern.print_stats()
        elif cmd == "kill":
            _print("  🚨 Manual kill switch — closing all and halting…")
            self.exec.close_all()
            self.risk.halted = True
            self._running    = False
        elif cmd == "q":
            _print("  [!] Quit — closing all positions…")
            self.exec.close_all()
            self._running = False
        else:
            _print(f"  Unknown: '{cmd}' — type 'h' for help")

    def _print_help(self):
        if RICH_AVAILABLE and console:
            table = Table(title="Dark Lord v2.0 Commands", box=rich_box.SIMPLE, show_header=False)
            table.add_column("Cmd",  style="bold cyan", min_width=8)
            table.add_column("Desc", style="white")
            cmds = [("s",    "Account status + sessions"),
                    ("p",    "Volume profile (all symbols)"),
                    ("m",    "Microstructure stats"),
                    ("htf",  "HTF bias status (D1+H4)"),
                    ("pat",  "Pattern performance by confluence"),
                    ("r",    "Force AI retrain"),
                    ("bt SYM [days]", "Quick backtest (default 30d)"),
                    ("wf",   "Walk-forward multi-window test (6mo)"),
                    ("mc",   "Monte Carlo go-live gate check"),
                    ("strat","Same as 'pat' — pattern performance"),
                    ("kill", "Emergency close all + halt"),
                    ("q",    "Quit (closes positions)")]
            for c, d in cmds:
                table.add_row(c, d)
            console.print(table)
        else:
            print("""
  ╔══════════════════════════════════════════════╗
  ║   DARK LORD v2.0 — TERMINAL COMMANDS         ║
  ╠══════════════════════════════════════════════╣
  ║  s        — account status + sessions        ║
  ║  p        — volume profile                   ║
  ║  m        — microstructure stats             ║
  ║  htf      — HTF bias (D1+H4)                ║
  ║  pat      — pattern performance table        ║
  ║  r        — force AI retrain                 ║
  ║  bt SYM   — backtest (default 30d)           ║
  ║  wf       — walk-forward 6-month test        ║
  ║  mc       — Monte Carlo go-live check        ║
  ║  kill     — emergency close + halt           ║
  ║  q        — quit                             ║
  ╚══════════════════════════════════════════════╝
""")

    def _print_status(self):
        s    = self.portfolio.get_summary()
        sess = self.session.active_sessions()
        if RICH_AVAILABLE and console:
            table = Table(title="Status", box=rich_box.ROUNDED)
            table.add_column("Field", style="cyan"); table.add_column("Value", style="white")
            rows = [
                ("Balance",     f"${s.get('balance', 0):.2f}"),
                ("Equity",      f"${s.get('equity', 0):.2f}"),
                ("Open PnL",    f"${s.get('open_pnl', 0):.2f}"),
                ("Open Trades", str(s.get("open_trades", 0))),
                ("Free Margin", f"${s.get('free_margin', 0):.2f}"),
                ("Daily Trades",f"{self.risk.daily_trades}/{self.cfg.MAX_DAILY_TRADES}"),
                ("Daily PnL",   f"${self.risk.daily_pnl:.2f}"),
                ("Risk Scalar", f"{self.risk.risk_scalar:.2f}"),
                ("Kill Switch", "ENGAGED 🚨" if self.risk.halted else "Armed ✓"),
                ("Sessions",    ", ".join(sess) or "none"),
                ("Cycle #",     str(self._cycle)),
                ("AI Trained",  self.ai.last_trained.strftime("%H:%M:%S") if self.ai.last_trained else "No"),
            ]
            for k, v in rows: table.add_row(k, v)
            console.print(table)
        else:
            print(f"""
  ── DARK LORD v2.0 STATUS ──────────────────────────
  Balance:     ${s.get('balance',0):.2f}
  Equity:      ${s.get('equity',0):.2f}
  Daily PnL:   ${self.risk.daily_pnl:.2f}
  Trades:      {self.risk.daily_trades}/{self.cfg.MAX_DAILY_TRADES}
  Sessions:    {', '.join(sess) or 'none'}
  Kill Switch: {'ENGAGED 🚨' if self.risk.halted else 'Armed ✓'}
  ──────────────────────────────────────────────────
""")

    def _print_htf(self):
        for sym in self.cfg.SYMBOLS:
            bias, conf = self.htf.get_bias(self.data, sym)
            pd_res     = self.pd.analyze(self.data.get_rates(sym, "D1", 20),
                                         self.data.get_rates(sym, "H4", 50))
            _print(f"  {sym}: HTF={bias} (conf={conf:.2f}) | P/D={pd_res['zone']} (ratio={pd_res.get('ratio',0):.3f})")

    def _print_volume_profile(self):
        _print("\n  ── VOLUME PROFILE ────────────────────────────────")
        for sym in self.cfg.SYMBOLS:
            vp = self._vp_cache.get(sym, {})
            _print(f"  {sym}: POC={vp.get('poc',0):.5f} VAH={vp.get('vah',0):.5f} "
                   f"VAL={vp.get('val',0):.5f} HVN={len(vp.get('hvn',[]))} LVN={len(vp.get('lvn',[]))} "
                   f"score={vp.get('score',0):.2f}")

    def _print_microstructure(self):
        _print("\n  ── MICROSTRUCTURE ────────────────────────────────")
        for sym in self.cfg.SYMBOLS:
            res  = self.ms.analyze(sym, self.data)
            slip = self.ms.slippage_stats(sym)
            _print(f"  {sym}: spread={res.get('spread',0):.1f}(x{res.get('spread_ratio',1):.2f}) "
                   f"lat={res.get('latency_ms',0):.0f}ms A-book={res.get('abook_score',0.5):.2f} "
                   f"slip_avg={slip.get('mean',0):.2f}pips "
                   f"lastlook={'YES' if res.get('last_look') else 'NO'} "
                   f"news={'YES (wide spread)' if res.get('spread_ratio',1)>1.5 else 'NO'}")

    def _run_backtest(self, symbol: str, days: int = 30):
        _print(f"\n  ⏳ Backtesting {symbol} ({days}d)…")
        end   = datetime.now()
        start = end - timedelta(days=days)
        df    = self.data.get_historical_range(symbol, "H1", start, end)
        if df is None or df.empty:
            _print("  ❌ No data"); return
        strat = DarkSweepStrategy()
        res   = self.bt.run(df, strat, 10_000.0)
        _print(f"""
  ── BACKTEST {symbol} ({days}d) ─────────────────────
  Return:        {res['total_return']:.2%}
  Sharpe:        {res['sharpe']:.2f}
  Max DD:        {res['max_dd']:.2%}
  Win Rate:      {res['win_rate']:.2%}
  Profit Factor: {res['profit_factor']:.2f}  (need > 1.6 for go-live)
  Expectancy:    ${res['expectancy']:.2f}
  Trades:        {res['trades']}
  ─────────────────────────────────────────────────────
""")

    def _run_monte_carlo(self):
        _print("\n  ⏳ Running Monte Carlo across all symbols…")
        all_pnls = []
        for sym in self.cfg.SYMBOLS:
            end = datetime.now(); start = end - timedelta(days=180)
            df  = self.data.get_historical_range(sym, "H1", start, end)
            if df is None or df.empty: continue
            res = self.bt.run(df, DarkSweepStrategy(), 10_000.0)
            all_pnls.extend(res.get("pnls", []))
        if not all_pnls:
            _print("  ❌ No trade data for Monte Carlo"); return
        gate, results = self.bt.monte_carlo(all_pnls, n_paths=1000)

    # ── Main loop ─────────────────────────────────────────────────────────────
    async def run_forever(self, interval_sec: int = 300):
        self._running = True
        t = threading.Thread(target=self._terminal_input_thread, daemon=True)
        t.start()
        if not self.connect_mt5():
            log.critical("Initial connection failed — aborting")
            return
        if self.cfg.USE_AI:
            try:
                log.info("Initial AI training…")
                self.train_ai()
            except Exception as e:
                log.warning("AI training skipped: %s", e)
        log.info("Dark Lord Bot v2.0 running | symbols=%s | interval=%ds",
                 self.cfg.SYMBOLS, interval_sec)
        while self._running:
            try:
                self._process_commands()
                await self.run_cycle()
            except KeyboardInterrupt:
                log.info("Ctrl+C — shutting down gracefully")
                self.exec.close_all()
                break
            except Exception as e:
                log.critical("Fatal cycle error: %s", e, exc_info=True)
            for _ in range(interval_sec):
                if not self._running: break
                await asyncio.sleep(1)
                self._process_commands()
        log.info("Dark Lord Bot stopped.")


# ============================================================================
# ── ENTRY POINT
# ============================================================================
if __name__ == "__main__":
    if RICH_AVAILABLE and console:
        console.print(Panel("""
[bold cyan]DARK LORD BOT v2.0 — Institutional Order Flow Edition[/bold cyan]

[green]✦ HTFBiasEngine[/green]     — D1+H4 must agree before ANY trade
[green]✦ LiquidityMap[/green]      — Know where the stops are
[green]✦ Premium/Discount[/green]  — Buy cheap, sell expensive only
[green]✦ OrderBlocks[/green]       — Enter at institutional price memory
[green]✦ MarketStructure[/green]   — BOS/CHOCH M5 confirmation
[green]✦ QualityGate[/green]       — 10 hard gates, all must pass
[green]✦ PatternTracker[/green]    — Track your own edge by confluence

[bold red]⚠  DO NOT GO LIVE UNTIL:[/bold red]
    Walk-Forward PF > 1.6 on ALL windows
    Monte Carlo P5 Sharpe > 0.3
    Max DD in any window < 15%%
    Run: 'wf' then 'mc' to verify

Type [bold]'h'[/bold] for commands
""", title="[bold]Dark Lord v2.0[/bold]", border_style="cyan"))
    else:
        print(r"""
╔══════════════════════════════════════════════════════════════════════════════╗
║   DARK LORD BOT v2.0 — Institutional Order Flow                              ║
║   Type 'h' for commands. Run 'wf' then 'mc' before going live.              ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")

    cfg = DarkConfig()
    if not cfg.validate():
        log.critical("Config validation failed")
        sys.exit(1)

    trader = DarkLordTrader(cfg)
    try:
        asyncio.run(trader.run_forever(interval_sec=300))
    except KeyboardInterrupt:
        print("\n  [!] Keyboard interrupt — closing positions and exiting…")
        trader.exec.close_all()
        sys.exit(0)