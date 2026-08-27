"""
risk_manager.py
===============
Roll & Stop-Loss Engine.

Evaluates OPEN positions recorded in ``portfolio.json`` and produces
actionable recommendations. It does NOT touch a broker; it only flags what a
human should consider.

Rules:
  * Put Rolling Trigger:
      An active PUT with DTE <= PUT_ROLL_DTE_TRIGGER  OR  that is Tested (ITM,
      i.e. spot below strike) => recommend rolling forward for a net credit.
  * Share Stop Loss:
      If assigned shares are more than SHARE_STOP_LOSS_PCT below cost basis =>
      raise a capital-preservation warning.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import config
from portfolio import Portfolio, Position

try:
    import yfinance as yf
except Exception:  # pragma: no cover - import guard
    yf = None


@dataclass
class RiskAlert:
    kind: str                 # "PUT_ROLL" or "STOP_LOSS"
    ticker: str
    severity: str             # "INFO", "WARNING", "CRITICAL"
    message: str
    suggested_ticket: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ticker": self.ticker,
            "severity": self.severity,
            "message": self.message,
            "suggested_ticket": self.suggested_ticket,
            "details": self.details,
        }


def _spot_price(ticker: str) -> float | None:
    if yf is None:
        return None
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="5d", interval="1d")
        if hist is None or hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as exc:
        print(f"[risk] Could not fetch spot for {ticker}: {exc}")
        return None


def _dte(expiration: str, today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    try:
        exp = dt.date.fromisoformat(expiration)
    except ValueError:
        return 0
    return (exp - today).days


def _evaluate_put(position: Position, spot: float | None) -> list[RiskAlert]:
    alerts: list[RiskAlert] = []
    today = dt.date.today()
    for opt in position.active_options:
        if opt.option_type != "PUT":
            continue
        dte = _dte(opt.expiration, today)
        is_tested = spot is not None and spot < opt.strike
        near_expiry = dte <= config.PUT_ROLL_DTE_TRIGGER

        if near_expiry or is_tested:
            reasons = []
            if near_expiry:
                reasons.append(f"DTE={dte} <= {config.PUT_ROLL_DTE_TRIGGER}")
            if is_tested:
                reasons.append(
                    f"ITM/Tested (spot {spot:.2f} < strike {opt.strike:.2f})"
                )
            ticket = (
                f"ROLL: BUY TO CLOSE {opt.contracts} Put {position.ticker} "
                f"{opt.expiration} ${opt.strike:.2f}  ->  SELL TO OPEN "
                f"{opt.contracts} Put {position.ticker} [next 28-45 DTE] "
                f"[strike <= ${opt.strike:.2f}] @ NET CREDIT"
            )
            alerts.append(
                RiskAlert(
                    kind="PUT_ROLL",
                    ticker=position.ticker,
                    severity="WARNING" if is_tested else "INFO",
                    message=(
                        f"Roll put on {position.ticker} for a net credit "
                        f"({'; '.join(reasons)})."
                    ),
                    suggested_ticket=ticket,
                    details={
                        "expiration": opt.expiration,
                        "strike": opt.strike,
                        "contracts": opt.contracts,
                        "dte": dte,
                        "spot": spot,
                        "tested": is_tested,
                    },
                )
            )
    return alerts


def _evaluate_stop_loss(position: Position, spot: float | None) -> list[RiskAlert]:
    alerts: list[RiskAlert] = []
    if position.quantity <= 0 or position.cost_basis <= 0 or spot is None:
        return alerts
    drawdown = (position.cost_basis - spot) / position.cost_basis
    if drawdown > config.SHARE_STOP_LOSS_PCT:
        alerts.append(
            RiskAlert(
                kind="STOP_LOSS",
                ticker=position.ticker,
                severity="CRITICAL",
                message=(
                    f"CAPITAL PRESERVATION: {position.ticker} down "
                    f"{drawdown*100:.1f}% below cost basis "
                    f"(basis ${position.cost_basis:.2f}, spot ${spot:.2f}). "
                    f"Review thesis; consider defensive action."
                ),
                details={
                    "cost_basis": position.cost_basis,
                    "spot": spot,
                    "drawdown_pct": round(drawdown * 100, 2),
                    "quantity": position.quantity,
                },
            )
        )
    return alerts


def evaluate_portfolio(portfolio: Portfolio) -> list[RiskAlert]:
    """Run all risk checks across every position."""
    alerts: list[RiskAlert] = []
    for position in portfolio.all_positions():
        spot = _spot_price(position.ticker)
        alerts.extend(_evaluate_put(position, spot))
        alerts.extend(_evaluate_stop_loss(position, spot))
    # Order by severity for reporting.
    severity_rank = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
    alerts.sort(key=lambda a: severity_rank.get(a.severity, 99))
    return alerts