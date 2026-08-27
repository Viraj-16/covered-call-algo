"""
main.py
=======
Orchestrator / entrypoint for the Human-in-the-Loop Options Research Bot.

Pipeline (the "Sunday Night" run):
  1. Load portfolio state from portfolio.json.
  2. For STATE_0_CASH tickers  -> screen Cash-Secured Puts.
     For STATE_1_HOLDING tickers -> screen Covered Calls (strikes >= cost basis).
  3. Score every candidate setup.
  4. Run the Roll & Stop-Loss engine over open positions.
  5. Send the top setups to Gemini for a qualitative "yield trap" filter.
  6. Render + write the Markdown Sunday Night Research Report.

RESEARCH ONLY. No broker connection. All output is for manual entry.

Usage:
    python main.py                 # run full report
    python main.py --no-ai         # force math-only (skip Gemini)
    python main.py --portfolio x.json --out report.md
"""

from __future__ import annotations

import argparse
import os
import sys

# Load .env if python-dotenv is available (optional convenience).
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

import config
import reporter
import risk_manager
import scoring
import screener
from portfolio import Portfolio


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Human-in-the-Loop Options Research Bot (research only)."
    )
    parser.add_argument(
        "--portfolio",
        default=config.PORTFOLIO_FILE,
        help="Path to portfolio.json (default: %(default)s)",
    )
    parser.add_argument(
        "--out",
        default=config.REPORT_FILE,
        help="Path to write the Markdown report (default: %(default)s)",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Skip the Gemini qualitative stage (math-only report).",
    )
    return parser.parse_args(argv)


def run(portfolio_path: str, out_path: str, use_ai: bool) -> str:
    print("=" * 70)
    print("  Sunday Night Options Research Bot  (RESEARCH ONLY — no broker)")
    print("=" * 70)

    # 1. Load portfolio ----------------------------------------------------
    pf = Portfolio(portfolio_path)
    pf.load()
    print(f"[main] Loaded {len(pf.all_positions())} positions from {portfolio_path}")

    all_setups: list[screener.OptionSetup] = []

    # 2. Screen CASH tickers -> CSPs --------------------------------------
    for pos in pf.cash_tickers():
        print(f"[main] Screening CSPs for {pos.ticker} ...")
        snap = screener.analyze_technicals(pos.ticker)
        if snap is None:
            continue
        if not snap.passes_technicals:
            print(f"[main]   {pos.ticker} failed technicals: {snap.reason}")
            continue
        setups = screener.screen_cash_secured_puts(pos.ticker, snap)
        print(f"[main]   {len(setups)} put candidates for {pos.ticker}")
        all_setups.extend(setups)

    # 2b. Screen HOLDING tickers -> Covered Calls -------------------------
    for pos in pf.holding_tickers():
        print(f"[main] Screening Covered Calls for {pos.ticker} ...")
        snap = screener.analyze_technicals(pos.ticker)
        if snap is None:
            continue
        # For covered calls we do not hard-gate on RSI (we already own shares),
        # but we still record technicals for the report.
        setups = screener.screen_covered_calls(pos.ticker, snap, pos.cost_basis)
        print(f"[main]   {len(setups)} call candidates for {pos.ticker}")
        all_setups.extend(setups)

    total_candidates = len(all_setups)
    print(f"[main] Total candidate setups: {total_candidates}")

    # 3. Score -------------------------------------------------------------
    scoring.score_setups(all_setups)

    # 4. Risk / roll engine ------------------------------------------------
    print("[main] Evaluating open positions for rolls / stop-losses ...")
    alerts = risk_manager.evaluate_portfolio(pf)
    print(f"[main] {len(alerts)} risk alert(s) generated")

    # 5. Qualitative AI stage ---------------------------------------------
    verdicts: dict[str, reporter.QualitativeVerdict] = {}
    ai_enabled = False
    if use_ai:
        verdicts = reporter.get_qualitative_verdicts(all_setups)
        ai_enabled = bool(verdicts)
    else:
        print("[main] --no-ai set; skipping Gemini stage.")

    # 6. Select + render ---------------------------------------------------
    picks = reporter.select_top_picks(all_setups, verdicts)
    report_md = reporter.render_report(
        picks=picks,
        risk_alerts=alerts,
        ai_enabled=ai_enabled,
        total_candidates=total_candidates,
    )

    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(report_md)

    print("=" * 70)
    print(f"[main] Report written to: {out_path}")
    print("=" * 70)
    return report_md


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        run(
            portfolio_path=args.portfolio,
            out_path=args.out,
            use_ai=not args.no_ai,
        )
    except FileNotFoundError as exc:
        print(f"[main] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())