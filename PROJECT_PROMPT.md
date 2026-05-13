# ALM Rate Shock 2023 — Project Prompt

> **Purpose of this document:** Initial brief for Claude Code / Antigravity. Read this in full before writing any code. Ask clarifying questions if anything is ambiguous before scaffolding.

---

## 1. Author & Context

I'm Jared Limon, a senior risk management leader with 15+ years across credit risk, market making, and quantitative risk strategy. Past roles include Principal Risk Architect at Bosonic Digital (institutional FX market making, A/B book, cross-custodian netting), Lead Credit Risk Analyst at Tosh (ML-driven credit scoring, PD/LGD forecasting), and Senior Risk Management Analyst at IBFX/TradeStation ($1.2T institutional FX flow, A/B book methodology, ALM principles applied to broker book operations).

This is a **portfolio project** to support my job search for senior risk leadership and quantitative strategist roles in financial services (banks, fintechs, prop trading, treasury functions). The audience for the final deliverable is **hiring managers — CROs, Heads of Risk, Treasurers, Quant leads** — not academic reviewers and not bootcamp graders. The project must read like a senior practitioner built it, not a student.

---

## 2. Project Goal

Build a working **Asset and Liability Management (ALM) / Balance Sheet Modeling** demonstration that reconstructs the 2022–2023 US interest-rate shock and shows how an ALM framework would have flagged the duration mismatch and deposit risk that took down Silicon Valley Bank.

The deliverable has two equally important parts:
1. **A working quantitative model** (Python) — NII sensitivity, EVE sensitivity, repricing gap analysis, deposit beta scenarios, liquidity gap.
2. **A narrative writeup** (README + a dashboard or notebook) that walks a non-technical executive through the findings.

**Both halves matter.** A perfect model with no story is useless for a portfolio. A great story with broken math is worse.

---

## 3. Core Scope (must-haves)

### 3.1 Data sources
- **FFIEC Call Report data** — pull quarterly balance sheet and income statement data for at least two banks: one casualty (Silicon Valley Bank, RSSD 802866) and one survivor of similar size profile (suggest candidates — e.g., Zions Bancorp, Comerica, First Horizon). Date range: 2019 Q1 through 2023 Q1 minimum.
- **FRED (Federal Reserve Economic Data)** — pull the relevant Treasury yield curve, Fed Funds rate, mortgage rates, and deposit rate series for the same period. Free API key required; instructions go in README.
- All data ingestion must be reproducible — scripts that pull fresh data, not committed CSVs (commit small sample data only for testing).

### 3.2 Models to build
- **Repricing gap analysis** — classify assets and liabilities into repricing buckets (overnight, 1–3M, 3–12M, 1–5Y, 5Y+); compute gap and cumulative gap per period.
- **Net Interest Income (NII) sensitivity** — project 12-month NII under parallel rate shocks (+100, +200, +300, +400 bps) vs. baseline. Show NII at risk as % of baseline NII.
- **Economic Value of Equity (EVE) sensitivity** — discount all cash flows under shock scenarios; report ∆EVE / Tier 1 capital.
- **Deposit beta modeling** — at least two scenarios (low beta ~0.3 vs. high beta ~0.7). Show how sensitive results are to this single assumption. *This is the key insight of the SVB story and must be a centerpiece.*
- **Liquidity gap / HQLA analysis** — simplified LCR-style view: HQLA vs. 30-day projected outflows under stress.

### 3.3 The SVB story
The project's narrative spine is a **postmortem reconstruction**: using only data available as of, say, Q3 2022, would a competent ALM framework have flagged SVB? Show:
- The HTM portfolio buildup and duration extension during 2020–2021.
- Unrealized loss accumulation as rates rose through 2022.
- Deposit concentration risk (uninsured deposit %).
- The combined NII + EVE + liquidity picture under a high-beta deposit scenario.

Contrast with the survivor bank's equivalent metrics.

---

## 4. Out of Scope (do NOT build these unless I ask later)

- Full CECL / IFRS 9 expected credit loss modeling.
- Credit risk scorecards (that's a separate portfolio project).
- Real-time data feeds or production deployment.
- Multi-currency / FX ALM (this is US-only, USD-only).
- Behavioral models for prepayment beyond a simple constant CPR assumption.
- Anything requiring paid data sources.

---

## 5. Tech Stack

- **Language:** Python 3.11+
- **Core libraries:** pandas, numpy, scipy, matplotlib/plotly. Use `QuantLib-Python` for cash flow modeling **only if** it materially simplifies the EVE calculation; otherwise pure numpy is fine and more transparent.
- **Dashboard / final presentation layer:** Streamlit (preferred — fast to build, easy to demo, easy to deploy to Streamlit Community Cloud for a live link on my portfolio site).
- **Environment management:** `uv` or `poetry` — pick one and stick with it.
- **Testing:** pytest. Unit tests for every cash flow / discounting / sensitivity calculation. I want to be able to defend every number.
- **No notebooks in the final repo** except a single `notebooks/exploratory.ipynb` for the data exploration narrative. All production logic lives in `.py` modules.

---

## 6. Repo Structure (target)

```
alm-rate-shock-2023/
├── README.md                  # Executive-level narrative + how to run
├── PROJECT_PROMPT.md          # This file
├── pyproject.toml             # Or requirements.txt
├── data/
│   ├── raw/                   # Gitignored — pulled fresh by scripts
│   └── sample/                # Small committed sample for tests
├── src/
│   └── alm/
│       ├── __init__.py
│       ├── data/              # FFIEC + FRED ingestion
│       ├── cashflows/         # Asset and liability cash flow generators
│       ├── models/            # Repricing gap, NII, EVE, liquidity
│       ├── scenarios/         # Rate shocks, deposit betas
│       └── reporting/         # Charts, summary tables
├── tests/
├── scripts/                   # CLI entry points to pull data, run scenarios
├── notebooks/
│   └── exploratory.ipynb
└── app/
    └── streamlit_app.py       # The demo dashboard
```

---

## 7. Quality Standards (this is what makes it a portfolio piece, not a tutorial)

- **Every assumption must be explicit and configurable.** Deposit betas, prepayment speeds, repricing bucket boundaries — all in a config file, not hardcoded.
- **Every chart must have a one-sentence caption** explaining what a hiring manager should take away from it.
- **Validate against published numbers.** SVB's Q4 2022 10-K shows specific HTM unrealized losses (~$15B) and AOCI hits. If my model can't reproduce those within ~10%, something is wrong and I want to know about it before I publish.
- **Limitations section in the README is mandatory** and should be honest: simplified prepayment, no optionality on non-maturity deposits, no behavioral models, etc. A senior risk professional names their model's weaknesses; a junior one hides them.
- **README structure:**
  1. One-paragraph executive summary (what + why + headline finding)
  2. The SVB story in 3–4 paragraphs (no code)
  3. Methodology summary (link to deeper docs)
  4. Key results table + 2–3 hero charts
  5. How to reproduce
  6. Limitations and what I'd build next
  7. About the author / link back to portfolio site

---

## 8. How I Want Claude Code to Work

- **Start by asking clarifying questions** if anything in this brief is ambiguous. Do not start writing code until the scope is clear.
- **Propose the project plan before scaffolding.** I want to see the milestone breakdown and approve it.
- **Build in vertical slices**, not horizontal layers. Get one bank, one scenario, one metric end-to-end before generalizing. A working ugly thing beats a beautiful half-thing.
- **Commit frequently with descriptive messages.** I want a clean git history that itself tells the story of how the project was built.
- **Flag any assumption you make in code with `# ASSUMPTION:` comments** so I can review them later.
- **Push back if I ask for something dumb.** Especially on the modeling math — if I propose a methodology that doesn't make sense for ALM, tell me. I'd rather be corrected than ship something wrong.
- **Cite sources for any methodology choice.** Federal Reserve SR letters, BIS IRRBB principles, FFIEC handbooks, academic papers — link them in code comments and the README.

---

## 9. Definition of Done

The project is done when:
- A hiring manager can clone the repo, follow the README, and have the Streamlit dashboard running in under 5 minutes.
- The SVB postmortem narrative is compelling enough to be the centerpiece of a 15-minute interview conversation.
- All key numbers are reproducible from public data.
- Tests pass, linting is clean, README is polished.
- I can point a CRO at the repo and they'd say "yes, this person knows ALM."

---

## 10. First Task

Before writing any code:
1. Confirm you've read this document.
2. Ask any clarifying questions you have about scope, methodology, or tech choices.
3. Propose a milestone plan (I'd suggest 4–6 milestones, each shippable on its own).
4. Recommend the survivor-bank comparison candidate and justify the choice.

Then we'll lock the plan and start building.
