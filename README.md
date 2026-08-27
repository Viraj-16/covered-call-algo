# 🗓️ Human-in-the-Loop Options Research Bot

A Python research assistant for the **Wheel Strategy** (Cash-Secured Puts →
assignment → Covered Calls). It screens option chains, tracks your portfolio
state locally, evaluates rolls/stop-losses, applies an AI "yield-trap" filter,
and produces a clean **Sunday Night Research Report** with **exact manual order
tickets**.

> ⚠️ **RESEARCH ONLY.** This bot does **NOT** connect to any broker. It never
> places trades and never touches real money. Every recommendation is for
> **manual entry** by you.

---

## Features

- **Portfolio State Engine** — local `portfolio.json` tracks each ticker as
  `STATE_0_CASH` (screen CSPs) or `STATE_1_HOLDING_SHARES` (screen CCs), with
  cost basis, assigned price, quantity, and active option contracts.
- **Quantitative Screener** (`yfinance` + `pandas_ta`)
  - RSI(14) gate: must be **40–60** (range-bound / consolidation).
  - ATR(14) volatility sanity check vs. price.
  - Scans **28–45 DTE** expirations.
  - CSPs: strikes **5–12% OTM** with liquidity checks (bid > \$0.15, tight spread, OI).
  - CCs: strikes **at/above cost basis**.
  - **Annualized ROC%** = `(Mid Premium / Strike) * (365 / DTE) * 100`.
- **Scoring** — separate `Score_Put` and `Score_Call` composite formulas.
- **Roll & Stop-Loss Engine**
  - Put roll trigger: DTE ≤ 7 **or** tested/ITM → roll-for-credit ticket.
  - Share stop-loss: > 8% below cost basis → capital-preservation warning.
- **AI Qualitative Analysis** (Google Gemini) — filters yield traps, assigns a
  Long-Term Quality Score (1–10). Falls back to a **math-only** report if no
  API key is configured.
- **Markdown report** — Top 3 picks, rationale, and exact order tickets.

---

## Project Layout

```
config.py          # thresholds, DTE range, OTM %, scoring weights, Gemini model
portfolio.py       # Portfolio State Engine (reads/writes portfolio.json)
screener.py        # yfinance data + pandas_ta RSI/ATR + option chain filters
scoring.py         # Score_Put / Score_Call
risk_manager.py    # put roll trigger + share stop-loss
reporter.py        # Gemini qualitative analysis + Markdown report
main.py            # Sunday Night report orchestrator (entrypoint)
portfolio.json     # example portfolio (mixed CASH + HOLDING states)
requirements.txt
.env.example       # GOOGLE_API_KEY placeholder
README.md
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

> **Note on `pandas_ta`:** newer NumPy versions occasionally break older
> `pandas_ta` builds. If you hit an import error, upgrade with
> `pip install -U pandas_ta` or pin `numpy<2.0`.

### 2. Configure your Gemini key (optional but recommended)

Get a key from **Google AI Studio**: <https://aistudio.google.com/apikey>

Copy the example env file and fill in the key:

```bash
copy .env.example .env      # Windows
# cp .env.example .env      # macOS/Linux
```

Then edit `.env`:

```
GOOGLE_API_KEY=your_real_key_here
```

Or set it directly as an environment variable:

```powershell
# Windows PowerShell
$env:GOOGLE_API_KEY = "your_real_key_here"
```

```cmd
:: Windows CMD
set GOOGLE_API_KEY=your_real_key_here
```

```bash
# macOS/Linux
export GOOGLE_API_KEY="your_real_key_here"
```

If no key is set, the bot still runs and produces a **math-only** report.

### 3. Edit your portfolio

Open `portfolio.json` and set your positions. Two states:

- `STATE_0_CASH` → you hold cash and want to sell **Cash-Secured Puts**.
- `STATE_1_HOLDING_SHARES` → you own shares (set `cost_basis` / `quantity`) and
  want to sell **Covered Calls** at/above your basis.

Record any open contracts under `active_options` so the roll/stop-loss engine
can manage them.

---

## Running the Sunday Night Report

```bash
python main.py
```

Common options:

```bash
python main.py --no-ai                       # skip Gemini (math-only)
python main.py --portfolio my_book.json      # use a different portfolio file
python main.py --out reports/2026-08-16.md   # custom output path
```

The report is written to `reports/sunday_night_report.md` by default and is
also echoed to the console log during the run.

---

## Example Order Ticket

The report emits tickets you type into your broker manually, e.g.:

```
SELL TO OPEN 1 Put AAPL 2026-09-18 $210.00 @ Limit $2.50
```

And roll recommendations, e.g.:

```
ROLL: BUY TO CLOSE 1 Put F 2026-08-15 $11.00  ->  SELL TO OPEN 1 Put F [next 28-45 DTE] [strike <= $11.00] @ NET CREDIT
```

---

## Tuning

All thresholds and scoring weights live in `config.py`:

- `RSI_MIN` / `RSI_MAX`, `ATR_MAX_PCT_OF_PRICE`
- `DTE_MIN` / `DTE_MAX`
- `PUT_OTM_MIN_PCT` / `PUT_OTM_MAX_PCT`
- `MIN_BID`, `MAX_BID_ASK_SPREAD_PCT`, `MIN_OPEN_INTEREST`
- `PUT_ROLL_DTE_TRIGGER`, `SHARE_STOP_LOSS_PCT`
- `PUT_WEIGHTS`, `CALL_WEIGHTS`, `ROC_REFERENCE_PCT`, `DISTANCE_REFERENCE_PCT`
- `TOP_N_PICKS`, `MAX_SETUPS_TO_LLM`, `GEMINI_MODEL`

---

## Disclaimer

This software is provided for **educational and research purposes only**. It is
**not financial advice**. Options trading involves substantial risk. Always
verify data independently and make your own decisions. You place all orders
manually.