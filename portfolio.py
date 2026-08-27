"""
portfolio.py
============
Portfolio State Engine.

Tracks positions locally in ``portfolio.json`` WITHOUT any broker connection.

Each ticker is in one of two states:
  * STATE_0_CASH            -> screen for Cash-Secured Puts (CSPs)
  * STATE_1_HOLDING_SHARES  -> screen for Covered Calls (CCs)

Metadata stored per position:
  * ticker
  * state
  * cost_basis      (average cost basis of held shares)
  * assigned_price  (price at which shares were assigned, if any)
  * quantity        (number of shares held)
  * active_options  (list of open option contracts we have sold)
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any

import config


@dataclass
class ActiveOption:
    """Represents a single option contract we have SOLD (short premium)."""

    option_type: str          # "PUT" or "CALL"
    expiration: str           # ISO date string, e.g. "2026-09-18"
    strike: float
    contracts: int = 1
    entry_premium: float = 0.0  # credit received per share when opened

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ActiveOption":
        return ActiveOption(
            option_type=str(d.get("option_type", "")).upper(),
            expiration=str(d.get("expiration", "")),
            strike=float(d.get("strike", 0.0)),
            contracts=int(d.get("contracts", 1)),
            entry_premium=float(d.get("entry_premium", 0.0)),
        )


@dataclass
class Position:
    """A single ticker position with its state and metadata."""

    ticker: str
    state: str = config.STATE_0_CASH
    cost_basis: float = 0.0
    assigned_price: float = 0.0
    quantity: int = 0
    active_options: list[ActiveOption] = field(default_factory=list)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Position":
        options = [ActiveOption.from_dict(o) for o in d.get("active_options", [])]
        return Position(
            ticker=str(d.get("ticker", "")).upper(),
            state=str(d.get("state", config.STATE_0_CASH)),
            cost_basis=float(d.get("cost_basis", 0.0)),
            assigned_price=float(d.get("assigned_price", 0.0)),
            quantity=int(d.get("quantity", 0)),
            active_options=options,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data

    @property
    def is_cash(self) -> bool:
        return self.state == config.STATE_0_CASH

    @property
    def is_holding(self) -> bool:
        return self.state == config.STATE_1_HOLDING_SHARES


class Portfolio:
    """Loads / saves / mutates the local portfolio state file."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or config.PORTFOLIO_FILE
        self.positions: dict[str, Position] = {}

    # -- persistence --------------------------------------------------------
    def load(self) -> "Portfolio":
        if not os.path.exists(self.path):
            raise FileNotFoundError(
                f"Portfolio file not found: {self.path}. "
                "Create one (see example portfolio.json)."
            )
        with open(self.path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)

        positions = raw.get("positions", [])
        self.positions = {}
        for item in positions:
            pos = Position.from_dict(item)
            if pos.ticker:
                self.positions[pos.ticker] = pos
        return self

    def save(self) -> None:
        payload = {
            "positions": [pos.to_dict() for pos in self.positions.values()],
        }
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    # -- accessors ----------------------------------------------------------
    def all_positions(self) -> list[Position]:
        return list(self.positions.values())

    def cash_tickers(self) -> list[Position]:
        """Positions to screen for Cash-Secured Puts."""
        return [p for p in self.positions.values() if p.is_cash]

    def holding_tickers(self) -> list[Position]:
        """Positions to screen for Covered Calls."""
        return [p for p in self.positions.values() if p.is_holding]

    def get(self, ticker: str) -> Position | None:
        return self.positions.get(ticker.upper())

    # -- mutation helpers (for manual bookkeeping after fills) --------------
    def upsert(self, position: Position) -> None:
        self.positions[position.ticker.upper()] = position

    def set_state(self, ticker: str, state: str) -> None:
        pos = self.get(ticker)
        if pos:
            pos.state = state