"""
config.py
=========
Central configuration for the Human-in-the-Loop Options Research Bot.

All tunable thresholds, screening parameters, and scoring weights live here so
they can be adjusted without touching business logic.

IMPORTANT: This bot is RESEARCH ONLY. It never connects to a broker and never
places trades. It outputs manual order tickets for the human to enter.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Portfolio state constants
# ---------------------------------------------------------------------------
STATE_0_CASH = "STATE_0_CASH"                 # Screen for Cash-Secured Puts
STATE_1_HOLDING_SHARES = "STATE_1_HOLDING_SHARES"  # Screen for Covered Calls

PORTFOLIO_FILE = os.getenv("PORTFOLIO_FILE", "portfolio.json")
REPORT_FILE = os.getenv("REPORT_FILE", "reports/sunday_night_report.md")

# ---------------------------------------------------------------------------
# Technical screening filters
# ---------------------------------------------------------------------------
RSI_PERIOD = 14
RSI_MIN = 40.0          # Range-bound / consolidation lower bound
RSI_MAX = 60.0          # Range-bound / consolidation upper bound

ATR_PERIOD = 14
# Reject names whose ATR is an excessive fraction of price (too whippy).
ATR_MAX_PCT_OF_PRICE = 0.06   # 6%

# Lookback window for pulling price history (needs > RSI/ATR period).
HISTORY_PERIOD = "6mo"
HISTORY_INTERVAL = "1d"

# Support / resistance are computed from a rolling window of closing prices.
SUPPORT_RESISTANCE_WINDOW = 20   # trading days

# ---------------------------------------------------------------------------
# Option chain filters
# ---------------------------------------------------------------------------
DTE_MIN = 28
DTE_MAX = 45

# Cash-Secured Put OTM band (below spot).
PUT_OTM_MIN_PCT = 0.05   # 5%
PUT_OTM_MAX_PCT = 0.12   # 12%

# Liquidity requirements.
MIN_BID = 0.15                    # minimum bid in dollars
MAX_BID_ASK_SPREAD_PCT = 0.15     # max (ask-bid)/mid ; tight spread requirement
MIN_OPEN_INTEREST = 10            # basic liquidity floor

# ---------------------------------------------------------------------------
# Risk management triggers
# ---------------------------------------------------------------------------
PUT_ROLL_DTE_TRIGGER = 7          # roll a put with <= 7 DTE
SHARE_STOP_LOSS_PCT = 0.08        # flag if shares drop > 8% below cost basis

# ---------------------------------------------------------------------------
# Scoring weights
# ---------------------------------------------------------------------------
# Score_Put weights: annualized ROC, distance below support, OTM %.
PUT_WEIGHTS = {
    "annualized_roc": 0.50,
    "support_distance": 0.30,
    "otm_pct": 0.20,
}

# Score_Call weights: annualized ROC, distance to resistance, clearance above basis.
CALL_WEIGHTS = {
    "annualized_roc": 0.50,
    "resistance_distance": 0.30,
    "basis_clearance": 0.20,
}

# Normalization reference points (used to scale raw metrics into 0-100 range).
ROC_REFERENCE_PCT = 40.0          # an annualized ROC of 40% maps to full marks
DISTANCE_REFERENCE_PCT = 0.10     # 10% distance maps to full marks

# Number of top setups sent to the LLM / included in the report.
TOP_N_PICKS = 3
MAX_SETUPS_TO_LLM = 8

# ---------------------------------------------------------------------------
# LLM (Google Gemini) configuration
# ---------------------------------------------------------------------------
GOOGLE_API_KEY_ENV = "GOOGLE_API_KEY"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")


def get_google_api_key() -> str | None:
    """Return the Google API key from the environment, or None if unset."""
    key = os.getenv(GOOGLE_API_KEY_ENV)
    return key.strip() if key else None