"""Runtime configuration: paths, environment variables, and ALM modeling defaults.

All assumptions that drive model output (deposit beta, prepayment speed, bucket
boundaries, etc.) live here so they can be overridden without touching code. Per
PROJECT_PROMPT §7: "Every assumption must be explicit and configurable."
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("ALM_DATA_DIR", REPO_ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
SAMPLE_DIR = DATA_DIR / "sample"
PROCESSED_DIR = DATA_DIR / "processed"


def fred_api_key() -> str:
    """Return the FRED API key from environment, raising a clear error if absent."""
    key = os.environ.get("FRED_API_KEY")
    if not key or key == "your_fred_api_key_here":
        raise RuntimeError(
            "FRED_API_KEY is not set. Copy .env.example to .env and paste your key, "
            "or set the environment variable directly. Get a free key at "
            "https://fred.stlouisfed.org/docs/api/api_key.html"
        )
    return key


# ---------------------------------------------------------------------------
# Repricing buckets
# ---------------------------------------------------------------------------
# ASSUMPTION: bucket boundaries follow the FFIEC Call Report Schedule RC-C / RC-E
# disclosure boundaries so we can map directly without further inference. These
# match the standard IRRBB time bands used in the Fed's SR 10-1 guidance.
# Source: FFIEC Call Report Instructions, Schedule RC-C Memorandum Item 2; BIS
# IRRBB Standards (April 2016), Annex 2.
REPRICING_BUCKETS_MONTHS: tuple[tuple[str, float, float], ...] = (
    ("overnight", 0.0, 1.0),       # 0 – 1 month
    ("1_3m",      1.0, 3.0),
    ("3_12m",     3.0, 12.0),
    ("1_5y",      12.0, 60.0),
    ("5y_plus",   60.0, float("inf")),
)


@dataclass(frozen=True)
class DepositBetaScenario:
    """A named deposit beta assumption applied to non-maturity deposits.

    Beta = sensitivity of deposit rate to a 1bp move in the policy rate. Time
    deposits are NOT covered by this — they reprice contractually.
    """
    name: str
    nmd_beta: float
    description: str


# ASSUMPTION: low-beta ≈ 0.30 reflects the pre-2022 sticky-deposit consensus for
# retail-heavy franchises (see Drechsler/Savov/Schnabl 2017). High-beta ≈ 0.70
# reflects what actually materialized for tech-concentrated, uninsured deposit
# books in 2022–2023 (SVB, First Republic). 0.50 is a middle case.
DEPOSIT_BETA_SCENARIOS: tuple[DepositBetaScenario, ...] = (
    DepositBetaScenario("low",    0.30, "Retail-sticky franchise (pre-2022 norm)"),
    DepositBetaScenario("mid",    0.50, "Mixed franchise"),
    DepositBetaScenario("high",   0.70, "Uninsured / institutional / tech-concentrated"),
)


# ASSUMPTION: parallel shocks only at this stage. Non-parallel (steepener,
# flattener, short-rate, long-rate) shocks are part of the BIS IRRBB standard
# six-scenario set; we'll add them in a later milestone if time allows.
PARALLEL_SHOCKS_BPS: tuple[int, ...] = (-200, -100, 0, 100, 200, 300, 400)


@dataclass(frozen=True)
class Paths:
    raw: Path = field(default_factory=lambda: RAW_DIR)
    sample: Path = field(default_factory=lambda: SAMPLE_DIR)
    processed: Path = field(default_factory=lambda: PROCESSED_DIR)

    def ensure(self) -> None:
        for p in (self.raw, self.sample, self.processed):
            p.mkdir(parents=True, exist_ok=True)


PATHS = Paths()
