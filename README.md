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

FFIEC bulk ZIPs must be downloaded once from the public-data portal (it requires a license click-through, not scriptable). For each quarter you want to model:

1. Visit https://cdr.ffiec.gov/public/PWS/DownloadBulkData.aspx
2. Pick **"Call Reports -- Single Period"**, the quarter-end date, format **"Tab Delimited"**.
3. Save the resulting ZIP into `data/raw/`. The filename will look like `FFIEC CDR Call Bulk All Schedules 12312022.zip`.

Then:

```bash
# FFIEC: parse one or more quarters for SVB + Huntington
uv run python -m scripts.pull_ffiec --quarter 2022Q4

# FRED: pull the Treasury curve and policy rates
uv run python -m scripts.pull_fred --start 2019-01-01 --end 2023-03-31
```

Outputs land in `data/processed/` as parquet.

### 4. Run the dashboard *(coming in M6)*

```bash
uv run streamlit run app/streamlit_app.py
```

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
