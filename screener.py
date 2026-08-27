"""
screener.py
===========
Quantitative Stock & Option Screener.

Uses ``yfinance`` for market data and ``pandas_ta`` for technicals.

Responsibilities:
  * Compute RSI(14) and ATR(14).
  * Apply technical filters (RSI in [40, 60], ATR/price sane).
  * Scan option expirations between DTE_MIN and DTE_MAX.
  * Cash-Secured Puts: strikes 5%-12% OTM with liquidity checks.
  * Covered Calls: strikes at/above cost basis.
  * Compute Annualized Return on Capital (ROC %).

All network access is defensive: any failure for a single ticker is logged and
that ticker is skipped so the overall report still generates.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

try:
    import pandas_ta as ta
except Exception:  # pragma: no cover - import guard
    ta = None

try:
    import yfinance as yf
except Exception:  # pragma: no cover - import guard
    yf = None

import config


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------
@dataclass
class TechnicalSnapshot:
    ticker: str
    price: float
    rsi: float
    atr: float
    atr_pct: float
    support: float
    resistance: float
    passes_technicals: bool
    reason: str = ""


@dataclass
class OptionSetup:
    """A single candidate option to sell (short premium)."""

    ticker: str
    strategy: str            # "CSP" or "CC"
    option_type: str         # "PUT" or "CALL"
    expiration: str          # ISO date
    dte: int
    strike: float
    bid: float
    ask: float
    mid: float
    open_interest: int
    spot: float
    otm_pct: float           # distance from spot (fractional)
    annualized_roc: float    # percent
    support: float = 0.0
    resistance: float = 0.0
    cost_basis: float = 0.0
    score: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "strategy": self.strategy,
            "option_type": self.option_type,
            "expiration": self.expiration,
            "dte": self.dte,
            "strike": round(self.strike, 2),
            "bid": round(self.bid, 2),
            "ask": round(self.ask, 2),
            "mid": round(self.mid, 2),
            "open_interest": self.open_interest,
            "spot": round(self.spot, 2),
            "otm_pct": round(self.otm_pct * 100, 2),
            "annualized_roc": round(self.annualized_roc, 2),
            "support": round(self.support, 2),
            "resistance": round(self.resistance, 2),
            "cost_basis": round(self.cost_basis, 2),
            "score": round(self.score, 2),
            "score_breakdown": {k: round(v, 2) for k, v in self.score_breakdown.items()},
        }


# ---------------------------------------------------------------------------
# Technical analysis
# ---------------------------------------------------------------------------
def _dte(expiration: str, today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    exp = dt.date.fromisoformat(expiration)
    return (exp - today).days


def analyze_technicals(ticker: str) -> TechnicalSnapshot | None:
    """Return a TechnicalSnapshot, or None if data could not be fetched."""
    if yf is None:
        print(f"[screener] yfinance not installed; cannot analyze {ticker}")
        return None
    if ta is None:
        print(f"[screener] pandas_ta not installed; cannot analyze {ticker}")
        return None

    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(
            period=config.HISTORY_PERIOD, interval=config.HISTORY_INTERVAL
        )
    except Exception as exc:  # network / API error
        print(f"[screener] Failed to fetch history for {ticker}: {exc}")
        return None

    if hist is None or hist.empty or len(hist) < config.RSI_PERIOD + 2:
        print(f"[screener] Insufficient history for {ticker}; skipping.")
        return None

    close = hist["Close"]
    high = hist["High"]
    low = hist["Low"]

    try:
        rsi_series = ta.rsi(close, length=config.RSI_PERIOD)
        atr_series = ta.atr(high, low, close, length=config.ATR_PERIOD)
    except Exception as exc:
        print(f"[screener] Indicator computation failed for {ticker}: {exc}")
        return None

    if rsi_series is None or atr_series is None:
        print(f"[screener] Indicators returned None for {ticker}; skipping.")
        return None

    rsi = float(rsi_series.dropna().iloc[-1])
    atr = float(atr_series.dropna().iloc[-1])
    price = float(close.iloc[-1])
    atr_pct = atr / price if price else 0.0

    window = min(config.SUPPORT_RESISTANCE_WINDOW, len(close))
    support = float(low.tail(window).min())
    resistance = float(high.tail(window).max())

    passes = True
    reasons: list[str] = []
    if not (config.RSI_MIN <= rsi <= config.RSI_MAX):
        passes = False
        reasons.append(f"RSI {rsi:.1f} outside [{config.RSI_MIN}, {config.RSI_MAX}]")
    if atr_pct > config.ATR_MAX_PCT_OF_PRICE:
        passes = False
        reasons.append(
            f"ATR {atr_pct*100:.1f}% > {config.ATR_MAX_PCT_OF_PRICE*100:.0f}% of price"
        )

    return TechnicalSnapshot(
        ticker=ticker,
        price=price,
        rsi=rsi,
        atr=atr,
        atr_pct=atr_pct,
        support=support,
        resistance=resistance,
        passes_technicals=passes,
        reason="; ".join(reasons) if reasons else "OK",
    )


# ---------------------------------------------------------------------------
# Option chain helpers
# ---------------------------------------------------------------------------
def _valid_expirations(tk: "yf.Ticker", today: dt.date | None = None) -> list[str]:
    today = today or dt.date.today()
    out: list[str] = []
    try:
        expirations = tk.options or []
    except Exception as exc:
        print(f"[screener] Could not list expirations: {exc}")
        return out
    for exp in expirations:
        try:
            dte = _dte(exp, today)
        except ValueError:
            continue
        if config.DTE_MIN <= dte <= config.DTE_MAX:
            out.append(exp)
    return out


def _mid(bid: float, ask: float) -> float:
    if bid <= 0 and ask <= 0:
        return 0.0
    if bid <= 0:
        return ask
    if ask <= 0:
        return bid
    return (bid + ask) / 2.0


def _passes_liquidity(bid: float, ask: float, oi: int) -> bool:
    if bid < config.MIN_BID:
        return False
    if oi < config.MIN_OPEN_INTEREST:
        return False
    mid = _mid(bid, ask)
    if mid <= 0:
        return False
    spread_pct = (ask - bid) / mid if mid else 1.0
    return spread_pct <= config.MAX_BID_ASK_SPREAD_PCT


def annualized_roc(mid_premium: float, strike: float, dte: int) -> float:
    """(Mid Premium / Strike) * (365 / DTE) * 100."""
    if strike <= 0 or dte <= 0:
        return 0.0
    return (mid_premium / strike) * (365.0 / dte) * 100.0


# ---------------------------------------------------------------------------
# CSP screening
# ---------------------------------------------------------------------------
def screen_cash_secured_puts(
    ticker: str, snapshot: TechnicalSnapshot
) -> list[OptionSetup]:
    if yf is None:
        return []
    setups: list[OptionSetup] = []
    try:
        tk = yf.Ticker(ticker)
    except Exception as exc:
        print(f"[screener] Could not build ticker {ticker}: {exc}")
        return []

    today = dt.date.today()
    spot = snapshot.price
    low_strike = spot * (1 - config.PUT_OTM_MAX_PCT)   # deepest OTM
    high_strike = spot * (1 - config.PUT_OTM_MIN_PCT)  # nearest OTM

    for exp in _valid_expirations(tk, today):
        try:
            chain = tk.option_chain(exp)
            puts = chain.puts
        except Exception as exc:
            print(f"[screener] Could not fetch put chain {ticker} {exp}: {exc}")
            continue
        if puts is None or puts.empty:
            continue

        dte = _dte(exp, today)
        for _, row in puts.iterrows():
            strike = float(row.get("strike", 0.0))
            if not (low_strike <= strike <= high_strike):
                continue
            bid = float(row.get("bid", 0.0) or 0.0)
            ask = float(row.get("ask", 0.0) or 0.0)
            oi = int(row.get("openInterest", 0) or 0)
            if not _passes_liquidity(bid, ask, oi):
                continue
            mid = _mid(bid, ask)
            otm_pct = (spot - strike) / spot if spot else 0.0
            roc = annualized_roc(mid, strike, dte)
            setups.append(
                OptionSetup(
                    ticker=ticker,
                    strategy="CSP",
                    option_type="PUT",
                    expiration=exp,
                    dte=dte,
                    strike=strike,
                    bid=bid,
                    ask=ask,
                    mid=mid,
                    open_interest=oi,
                    spot=spot,
                    otm_pct=otm_pct,
                    annualized_roc=roc,
                    support=snapshot.support,
                    resistance=snapshot.resistance,
                )
            )
    return setups


# ---------------------------------------------------------------------------
# Covered Call screening
# ---------------------------------------------------------------------------
def screen_covered_calls(
    ticker: str, snapshot: TechnicalSnapshot, cost_basis: float
) -> list[OptionSetup]:
    if yf is None:
        return []
    setups: list[OptionSetup] = []
    try:
        tk = yf.Ticker(ticker)
    except Exception as exc:
        print(f"[screener] Could not build ticker {ticker}: {exc}")
        return []

    today = dt.date.today()
    spot = snapshot.price
    # Calls must be AT or ABOVE the recorded cost basis (never lock in a loss).
    floor_strike = max(cost_basis, 0.0)

    for exp in _valid_expirations(tk, today):
        try:
            chain = tk.option_chain(exp)
            calls = chain.calls
        except Exception as exc:
            print(f"[screener] Could not fetch call chain {ticker} {exp}: {exc}")
            continue
        if calls is None or calls.empty:
            continue

        dte = _dte(exp, today)
        for _, row in calls.iterrows():
            strike = float(row.get("strike", 0.0))
            if strike < floor_strike:
                continue
            bid = float(row.get("bid", 0.0) or 0.0)
            ask = float(row.get("ask", 0.0) or 0.0)
            oi = int(row.get("openInterest", 0) or 0)
            if not _passes_liquidity(bid, ask, oi):
                continue
            mid = _mid(bid, ask)
            otm_pct = (strike - spot) / spot if spot else 0.0
            roc = annualized_roc(mid, strike, dte)
            setups.append(
                OptionSetup(
                    ticker=ticker,
                    strategy="CC",
                    option_type="CALL",
                    expiration=exp,
                    dte=dte,
                    strike=strike,
                    bid=bid,
                    ask=ask,
                    mid=mid,
                    open_interest=oi,
                    spot=spot,
                    otm_pct=otm_pct,
                    annualized_roc=roc,
                    support=snapshot.support,
                    resistance=snapshot.resistance,
                    cost_basis=cost_basis,
                )
            )
    return setups