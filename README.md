# ALM Rate Shock 2023

> **Status: M1 (data ingestion) — in progress.** README will reach its final shape in M6. Treat what follows as a working skeleton.

Asset and Liability Management demonstration reconstructing the 2022–2023 US interest-rate shock, with the Silicon Valley Bank collapse as the headline case study. Built by [Jared Limon](#about) as a portfolio piece for senior risk and quantitative strategy roles.

---

## Headline finding (placeholder — fills in at M3)

> _Under a high-beta deposit assumption (β ≈ 0.7) and the Q3 2022 yield curve, SVB's combined NII + EVE picture flips from "managed" to "non-viable" — a result a competent ALM framework would have flagged months before the March 2023 run._

---

## The SVB story (placeholder — fills in at M4)

Three or four paragraphs of narrative go here in the final version: HTM duration extension, AOCI accumulation, deposit concentration, the high-beta sensitivity that made the franchise structurally unstable in a hiking cycle. No code.

---

## Methodology (summary)

- **Data:** FFIEC Call Report bulk data ([CDR Public Data Distribution](https://cdr.ffiec.gov/public/PWS/DownloadBulkData.aspx)) plus FRED ([api docs](https://fred.stlouisfed.org/docs/api/fred/)) Treasury curve, Fed Funds, mortgage rate.
- **Repricing gap** — buckets per IRRBB / SR 10-1: overnight, 1–3M, 3–12M, 1–5Y, 5Y+.
- **NII sensitivity** — 12-month projection under parallel shocks of ±100, ±200, +300, +400 bps.
- **EVE sensitivity** — cash-flow discounting under the same shock set; ΔEVE expressed as % of Tier 1.
- **Deposit beta** — non-maturity deposits only; scenarios at β = 0.30, 0.50, 0.70.
- **Validation** — must reproduce SVB Q4 2022 HTM unrealized loss (~$15B per 10-K) within ±10%.

Sources cited inline in code comments and again in the final methodology doc.

---

## How to reproduce

### 1. Install

```bash
git clone <repo-url>
cd alm-rate-shock-2023
uv sync                   # creates .venv, installs core + dev deps
uv pip install -e ".[dashboard,dev]"
```

### 2. Configure secrets

```bash
cp .env.example .env
# edit .env and paste your FRED API key (free: https://fred.stlouisfed.org/docs/api/api_key.html)
```

### 3. Pull data

FFIEC bulk ZIPs need to be downloaded from the public-data portal (each one is behind a license click-through). The portal's "Four Periods" Call Reports product covers only RC / RI / RC-N — missing the securities, deposit, and capital schedules we need — so the only viable approach is one quarter per "Single Period" download. Two ways to do that:

**Option A: Automated (recommended).** A Playwright-based fetcher drives a headless Chromium through the FFIEC form for each quarter. One-time setup:

```bash
uv pip install -e ".[fetch]"
uv run playwright install chromium
```

Then to grab the full 2019Q1–2023Q1 series in one shot (~4 minutes):

```bash
uv run python -m scripts.fetch_ffiec --headless --from 2019Q1 --to 2023Q1
```

The fetcher is idempotent: it skips quarters whose ZIP already exists. It also writes screenshots + HTML dumps to `data/fetch_debug/` if anything fails so the breakage is debuggable.

**Option B: Manual.** Visit https://cdr.ffiec.gov/public/PWS/DownloadBulkData.aspx, pick "Call Reports — Single Period", choose a quarter-end date, format "Tab Delimited", click Download, accept the license, save the ZIP into `data/raw/`. Repeat for each quarter. ~30 seconds each; ~8 minutes for 17 quarters.

The parser supports multi-period bulk archives too (it indexes ZIP contents by date token), so if FFIEC's product mix changes you can drop combined ZIPs in `data/raw/` without renaming.

Then:

```bash
# FFIEC: parse every quarter from 2019Q1 through 2023Q1 (works with either
# single-period or five-period ZIPs in data/raw/)
uv run python -m scripts.pull_ffiec \
  --quarter 2019Q1 --quarter 2019Q2 --quarter 2019Q3 --quarter 2019Q4 \
  --quarter 2020Q1 --quarter 2020Q2 --quarter 2020Q3 --quarter 2020Q4 \
  --quarter 2021Q1 --quarter 2021Q2 --quarter 2021Q3 --quarter 2021Q4 \
  --quarter 2022Q1 --quarter 2022Q2 --quarter 2022Q3 --quarter 2022Q4 \
  --quarter 2023Q1

# FRED: pull the Treasury curve and policy rates
uv run python -m scripts.pull_fred --start 2019-01-01 --end 2023-03-31
```

Outputs land in `data/processed/` as parquet.

### 4. Run the dashboard

```bash
uv run streamlit run app/streamlit_app.py
```

Then open http://localhost:8501 in your browser. The dashboard grows with each milestone — M1 shows the balance-sheet snapshot, HTM concentration, unrealized losses, and uninsured deposit %. Repricing-gap, NII, EVE, and liquidity overlays will appear as later milestones land.

---

## Repo layout

```
alm-rate-shock-2023/
├── README.md
├── PROJECT_PROMPT.md          # The original brief
├── LICENSE                    # MIT
├── pyproject.toml
├── .env.example
├── data/
│   ├── raw/                   # FFIEC bulk ZIPs (gitignored)
│   └── sample/                # Small samples for tests
├── src/alm/
│   ├── config.py              # Paths + tunable ALM assumptions
│   └── data/
│       ├── banks.py           # RSSD registry (SVB, Huntington)
│       ├── ffiec.py           # Call Report bulk-ZIP loader
│       ├── ffiec_schedules.py # MDRM code → field-name map
│       └── fred.py            # FRED API client
├── scripts/
│   ├── pull_ffiec.py          # Parse one or more quarters
│   └── pull_fred.py           # Pull macro series
├── tests/                     # pytest, no real network calls
├── notebooks/                 # exploratory only — never production logic
└── app/                       # Streamlit dashboard (M6)
```

---

## Limitations

This list will grow as the model matures; the M1 cut is:

- **Constant CPR prepayment** — no rate-dependent prepayment speeds. Real MBS optionality is not modeled.
- **No optionality on non-maturity deposits** beyond the static deposit-beta scenarios.
- **Bank-level Call Reports**, not holding-company FR Y-9C — fine for SVB (the chartered bank held nearly all the relevant assets) but readers comparing to the SVB Financial Group consolidated 10-K should expect small discrepancies.
- **Parallel shocks only** at present. BIS IRRBB's full six-scenario set (steepener / flattener / short-up / short-down) is out of scope unless added later.
- **Securities footnote augmentation** is used only for SVB's HTM book (the validation hinge); the rest of the balance sheet uses Call Report aggregates.

A senior risk professional names their model's weaknesses; a junior one hides them.

---

## Roadmap

| Milestone | Deliverable | Validation gate |
|---|---|---|
| **M1** | Data ingestion (FFIEC + FRED), parsers, tests | Total assets reconcile to published $209B for SVB Q4 2022 |
| **M2** | Repricing gap + first NII shock | Gap totals tie to balance sheet |
| **M3** | EVE + HTM unrealized-loss reconstruction | ±10% of SVB's published ~$15B HTM loss |
| **M4** | Survivor bank (Huntington) + full time series | Both banks reconcile each quarter, 2019Q1–2023Q1 |
| **M5** | Liquidity / HQLA / uninsured deposit overlay | Uninsured deposit % within 1 pp of published |
| **M6** | Streamlit dashboard, narrative polish, hero charts | 5-minute clone-to-running reproducibility test |

---

## About

**Jared Limon** — senior risk management leader, 15+ years across credit risk, market making, and quantitative risk strategy. Past roles include Principal Risk Architect at Bosonic Digital, Lead Credit Risk Analyst at Tosh, and Senior Risk Management Analyst at IBFX/TradeStation. Portfolio site link forthcoming.

---

## License

[MIT](LICENSE).
