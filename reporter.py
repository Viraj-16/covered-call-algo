"""
reporter.py
===========
AI Qualitative Analysis & Report Generator.

Takes the top mathematical setups (from the screener + scorer) and:
  1. Sends them to Google Gemini to filter out "yield traps" (bad debt,
     earnings traps, structural decline) and assign a Long-Term Quality
     Score (1-10) with rationale.
  2. Renders a clean Markdown "Sunday Night Research Report".

If GOOGLE_API_KEY is not set (or the SDK is unavailable), the reporter falls
back to a math-only report so the pipeline never crashes.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from typing import Any

import config
from risk_manager import RiskAlert
from screener import OptionSetup

try:
    import google.generativeai as genai
except Exception:  # pragma: no cover - import guard
    genai = None


@dataclass
class QualitativeVerdict:
    ticker: str
    quality_score: int          # 1-10
    is_yield_trap: bool
    rationale: str
    fundamental_notes: str = ""


@dataclass
class Pick:
    setup: OptionSetup
    verdict: QualitativeVerdict | None = None
    extras: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Manual order ticket formatting
# ---------------------------------------------------------------------------
def build_order_ticket(setup: OptionSetup, contracts: int = 1) -> str:
    """Return an exact manual order ticket string.

    Example:
      SELL TO OPEN 1 Put AAPL 2026-09-18 $210.00 @ Limit $2.50
    """
    opt_word = "Put" if setup.option_type == "PUT" else "Call"
    limit = setup.mid if setup.mid > 0 else setup.bid
    return (
        f"SELL TO OPEN {contracts} {opt_word} {setup.ticker} "
        f"{setup.expiration} ${setup.strike:.2f} @ Limit ${limit:.2f}"
    )


# ---------------------------------------------------------------------------
# LLM interaction
# ---------------------------------------------------------------------------
def _build_llm_prompt(setups: list[OptionSetup]) -> str:
    rows = [s.to_dict() for s in setups]
    payload = json.dumps(rows, indent=2)
    return (
        "You are a conservative options income analyst specializing in the "
        "Wheel Strategy (Cash-Secured Puts and Covered Calls). You are given "
        "mathematically-attractive option setups. Your job is to filter out "
        "'yield traps' — names with deteriorating fundamentals, unsustainable "
        "debt, imminent earnings landmines, or structural/secular decline — "
        "where a high premium reflects real risk rather than opportunity.\n\n"
        "For EACH ticker below, assess long-term business quality (would you "
        "be comfortable owning the shares if assigned?).\n\n"
        "Return STRICT JSON only (no markdown fences), an array where each "
        "element has keys exactly:\n"
        '  "ticker" (string),\n'
        '  "quality_score" (integer 1-10, 10=highest quality),\n'
        '  "is_yield_trap" (boolean),\n'
        '  "rationale" (string, 1-3 sentences),\n'
        '  "fundamental_notes" (string, brief).\n\n'
        f"SETUPS:\n{payload}\n"
    )


def _parse_llm_json(text: str) -> list[dict[str, Any]]:
    """Best-effort extraction of a JSON array from the model output."""
    text = text.strip()
    # Strip common code fences if present.
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    # Grab the outermost array.
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass
    return []


def get_qualitative_verdicts(
    setups: list[OptionSetup],
) -> dict[str, QualitativeVerdict]:
    """Query Gemini for quality verdicts keyed by ticker.

    Returns an empty dict if the LLM is unavailable (graceful fallback).
    """
    api_key = config.get_google_api_key()
    if not setups:
        return {}
    if genai is None:
        print("[reporter] google-generativeai not installed; skipping AI stage.")
        return {}
    if not api_key:
        print(
            "[reporter] GOOGLE_API_KEY not set; skipping AI stage "
            "(math-only report)."
        )
        return {}

    # De-duplicate tickers to minimize tokens, but keep the best setup each.
    unique: dict[str, OptionSetup] = {}
    for s in setups:
        if s.ticker not in unique or s.score > unique[s.ticker].score:
            unique[s.ticker] = s
    candidates = list(unique.values())[: config.MAX_SETUPS_TO_LLM]

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(config.GEMINI_MODEL)
        prompt = _build_llm_prompt(candidates)
        response = model.generate_content(prompt)
        text = getattr(response, "text", "") or ""
    except Exception as exc:
        print(f"[reporter] Gemini call failed: {exc}. Falling back to math-only.")
        return {}

    verdicts: dict[str, QualitativeVerdict] = {}
    for item in _parse_llm_json(text):
        try:
            ticker = str(item["ticker"]).upper()
            verdicts[ticker] = QualitativeVerdict(
                ticker=ticker,
                quality_score=int(item.get("quality_score", 0)),
                is_yield_trap=bool(item.get("is_yield_trap", False)),
                rationale=str(item.get("rationale", "")),
                fundamental_notes=str(item.get("fundamental_notes", "")),
            )
        except (KeyError, ValueError, TypeError):
            continue
    return verdicts


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------
def select_top_picks(
    setups: list[OptionSetup],
    verdicts: dict[str, QualitativeVerdict],
) -> list[Pick]:
    """Choose the Top N picks, dropping AI-flagged yield traps when possible."""
    picks: list[Pick] = []
    seen_tickers: set[str] = set()

    for setup in setups:  # already sorted by score desc
        verdict = verdicts.get(setup.ticker)
        if verdict and verdict.is_yield_trap:
            continue  # skip flagged traps
        if setup.ticker in seen_tickers:
            continue  # one best setup per ticker in the headline picks
        picks.append(Pick(setup=setup, verdict=verdict))
        seen_tickers.add(setup.ticker)
        if len(picks) >= config.TOP_N_PICKS:
            break

    # If AI filtering removed everything, fall back to raw top setups.
    if not picks:
        for setup in setups[: config.TOP_N_PICKS]:
            picks.append(Pick(setup=setup, verdict=verdicts.get(setup.ticker)))
    return picks


def _fmt_pct(value: float) -> str:
    return f"{value:.2f}%"


def render_report(
    picks: list[Pick],
    risk_alerts: list[RiskAlert],
    ai_enabled: bool,
    total_candidates: int,
) -> str:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: list[str] = []

    lines.append("# 🗓️ Sunday Night Research Report")
    lines.append("")
    lines.append(f"_Generated: {now}_  ")
    lines.append("**RESEARCH ONLY — No broker connection. Enter all orders manually.**")
    lines.append("")
    lines.append(
        f"- Candidate setups screened: **{total_candidates}**  "
    )
    lines.append(
        f"- AI qualitative filter: **{'ENABLED (Gemini)' if ai_enabled else 'DISABLED (math-only)'}**"
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    # -- Top picks ----------------------------------------------------------
    lines.append(f"## 🏆 Top {len(picks)} Pick Recommendations")
    lines.append("")

    if not picks:
        lines.append("_No qualifying setups this week._")
        lines.append("")
    for i, pick in enumerate(picks, start=1):
        s = pick.setup
        v = pick.verdict
        strat_name = "Cash-Secured Put" if s.strategy == "CSP" else "Covered Call"
        contracts = 1
        ticket = build_order_ticket(s, contracts=contracts)

        quality = f"{v.quality_score}/10" if v else "N/A (math-only)"
        lines.append(f"### {i}. {s.ticker} — {strat_name}")
        lines.append("")
        lines.append(f"- **Composite Score:** {s.score:.1f} / 100")
        lines.append(f"- **Long-Term Quality Score:** {quality}")
        lines.append(f"- **Spot:** ${s.spot:.2f}  |  **Strike:** ${s.strike:.2f}  "
                     f"|  **Expiration:** {s.expiration} ({s.dte} DTE)")
        lines.append(f"- **Premium (mid):** ${s.mid:.2f}  "
                     f"(bid ${s.bid:.2f} / ask ${s.ask:.2f})")
        lines.append(f"- **Annualized ROC:** {_fmt_pct(s.annualized_roc)}")
        if s.strategy == "CSP":
            lines.append(f"- **OTM Distance:** {_fmt_pct(s.otm_pct * 100)} below spot")
            lines.append(f"- **Technical Support:** ${s.support:.2f}")
        else:
            lines.append(f"- **Cost Basis:** ${s.cost_basis:.2f}  "
                         f"(clearance above basis)")
            lines.append(f"- **Technical Resistance:** ${s.resistance:.2f}")

        # Rationale
        lines.append("")
        lines.append("**Key Technical & Fundamental Rationale:**")
        tech_bits = []
        if s.strategy == "CSP":
            tech_bits.append(
                f"Strike sits {_fmt_pct(s.otm_pct * 100)} OTM with support at "
                f"${s.support:.2f}, providing a downside cushion."
            )
        else:
            tech_bits.append(
                f"Strike is at/above cost basis (${s.cost_basis:.2f}) with "
                f"resistance near ${s.resistance:.2f}, capping upside sensibly."
            )
        tech_bits.append(
            f"Annualized ROC of {_fmt_pct(s.annualized_roc)} at {s.dte} DTE."
        )
        lines.append("- " + " ".join(tech_bits))
        if v:
            if v.rationale:
                lines.append(f"- _AI:_ {v.rationale}")
            if v.fundamental_notes:
                lines.append(f"- _AI Fundamentals:_ {v.fundamental_notes}")
            if v.is_yield_trap:
                lines.append("- ⚠️ _AI flagged possible yield trap — proceed with caution._")

        # Ticket
        lines.append("")
        lines.append("**Exact Manual Order Ticket:**")
        lines.append("")
        lines.append("```")
        lines.append(ticket)
        lines.append("```")
        lines.append("")
        lines.append("---")
        lines.append("")

    # -- Risk / roll section -----------------------------------------------
    lines.append("## 🔧 Position Management (Rolls & Stop-Losses)")
    lines.append("")
    if not risk_alerts:
        lines.append("_No open-position alerts. All managed positions look healthy._")
        lines.append("")
    else:
        for alert in risk_alerts:
            icon = {
                "CRITICAL": "🛑",
                "WARNING": "⚠️",
                "INFO": "ℹ️",
            }.get(alert.severity, "•")
            lines.append(f"### {icon} {alert.ticker} — {alert.kind} ({alert.severity})")
            lines.append("")
            lines.append(f"{alert.message}")
            if alert.suggested_ticket:
                lines.append("")
                lines.append("```")
                lines.append(alert.suggested_ticket)
                lines.append("```")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "_Disclaimer: This report is generated for research purposes only and "
        "is not financial advice. Verify all data and enter orders manually._"
    )
    lines.append("")
    return "\n".join(lines)