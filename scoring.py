"""
scoring.py
==========
Scoring & Strategy Rules.

Two distinct scoring formulas producing a 0-100 composite score:

  * Score_Put   -> Cash-Secured Puts
      weights: Annualized ROC, distance BELOW technical support, OTM %.
  * Score_Call  -> Covered Calls
      weights: Annualized ROC, distance TO technical resistance,
               clearance above cost basis.

Each raw metric is normalized to a 0-100 sub-score, then combined using the
weights defined in ``config.py``. Higher score = more attractive setup.
"""

from __future__ import annotations

import config
from screener import OptionSetup


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _normalize_roc(annualized_roc_pct: float) -> float:
    """Map annualized ROC (%) to a 0-100 sub-score."""
    if config.ROC_REFERENCE_PCT <= 0:
        return 0.0
    return _clamp((annualized_roc_pct / config.ROC_REFERENCE_PCT) * 100.0)


def _normalize_distance(distance_fraction: float) -> float:
    """Map a fractional distance (e.g. 0.08 = 8%) to a 0-100 sub-score."""
    if config.DISTANCE_REFERENCE_PCT <= 0:
        return 0.0
    return _clamp((distance_fraction / config.DISTANCE_REFERENCE_PCT) * 100.0)


def score_put(setup: OptionSetup) -> OptionSetup:
    """Score a Cash-Secured Put candidate.

    Rewards:
      * higher annualized ROC
      * strike sitting further BELOW technical support (safety cushion)
      * greater OTM distance from spot
    """
    roc_sub = _normalize_roc(setup.annualized_roc)

    # Distance below support: positive when the strike is under support.
    if setup.support > 0:
        support_distance = (setup.support - setup.strike) / setup.support
    else:
        support_distance = 0.0
    support_sub = _normalize_distance(max(support_distance, 0.0))

    otm_sub = _normalize_distance(max(setup.otm_pct, 0.0))

    w = config.PUT_WEIGHTS
    breakdown = {
        "annualized_roc": roc_sub * w["annualized_roc"],
        "support_distance": support_sub * w["support_distance"],
        "otm_pct": otm_sub * w["otm_pct"],
    }
    setup.score = sum(breakdown.values())
    setup.score_breakdown = breakdown
    return setup


def score_call(setup: OptionSetup) -> OptionSetup:
    """Score a Covered Call candidate.

    Rewards:
      * higher annualized ROC
      * strike closer to (but at/under) technical resistance (room to run,
        capped where sellers appear)
      * greater clearance of the strike above the cost basis (locks in gain)
    """
    roc_sub = _normalize_roc(setup.annualized_roc)

    # Distance to resistance: strikes near/under resistance are preferred so we
    # capture upside without capping too far above where price stalls. We reward
    # proximity: closer to resistance -> higher sub-score.
    if setup.resistance > 0 and setup.spot > 0:
        gap_to_resistance = abs(setup.resistance - setup.strike) / setup.resistance
        # Convert "closeness" into a score: 0 gap = full marks.
        resistance_sub = _clamp(100.0 - _normalize_distance(gap_to_resistance))
    else:
        resistance_sub = 0.0

    # Clearance above cost basis.
    if setup.cost_basis > 0:
        clearance = (setup.strike - setup.cost_basis) / setup.cost_basis
    else:
        clearance = 0.0
    clearance_sub = _normalize_distance(max(clearance, 0.0))

    w = config.CALL_WEIGHTS
    breakdown = {
        "annualized_roc": roc_sub * w["annualized_roc"],
        "resistance_distance": resistance_sub * w["resistance_distance"],
        "basis_clearance": clearance_sub * w["basis_clearance"],
    }
    setup.score = sum(breakdown.values())
    setup.score_breakdown = breakdown
    return setup


def score_setups(setups: list[OptionSetup]) -> list[OptionSetup]:
    """Score a mixed list of setups in place and return it sorted by score."""
    for s in setups:
        if s.strategy == "CSP":
            score_put(s)
        elif s.strategy == "CC":
            score_call(s)
    setups.sort(key=lambda s: s.score, reverse=True)
    return setups