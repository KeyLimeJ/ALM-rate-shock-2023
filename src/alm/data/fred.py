"""FRED (St. Louis Fed) macroeconomic data client.

We pull a small registry of series that drive ALM modeling:

  Treasury yield curve (CMT): DGS1MO, DGS3MO, DGS6MO, DGS1, DGS2, DGS3, DGS5,
                              DGS7, DGS10, DGS20, DGS30
  Policy rate:                DFEDTARU (upper bound), DFEDTAR (target, pre-2008)
  Effective fed funds:        DFF
  Deposit rates (proxy):      MMNRNJ (national rate, money market non-jumbo)
                              SBPCY (national rate, savings)
  Mortgage rate (30Y):        MORTGAGE30US
  CPI (context):              CPIAUCSL

References:
    FRED API documentation: https://fred.stlouisfed.org/docs/api/fred/
    Treasury CMT methodology:
        https://home.treasury.gov/policy-issues/financing-the-government/
        interest-rate-statistics/treasury-yield-curve-methodology
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import pandas as pd
import requests

from alm.config import fred_api_key

log = logging.getLogger(__name__)

FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"


# ---------------------------------------------------------------------------
# Series registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Series:
    fred_id: str
    name: str        # short name we use in our codebase
    tenor_years: float | None  # for yield-curve series; None for non-rate series
    description: str


# CMT (Constant Maturity Treasury) — daily.
TREASURY_CURVE: tuple[Series, ...] = (
    Series("DGS1MO",  "ust_1m",   1/12,  "1-Month Treasury Constant Maturity"),
    Series("DGS3MO",  "ust_3m",   0.25,  "3-Month Treasury Constant Maturity"),
    Series("DGS6MO",  "ust_6m",   0.50,  "6-Month Treasury Constant Maturity"),
    Series("DGS1",    "ust_1y",   1.0,   "1-Year Treasury Constant Maturity"),
    Series("DGS2",    "ust_2y",   2.0,   "2-Year Treasury Constant Maturity"),
    Series("DGS3",    "ust_3y",   3.0,   "3-Year Treasury Constant Maturity"),
    Series("DGS5",    "ust_5y",   5.0,   "5-Year Treasury Constant Maturity"),
    Series("DGS7",    "ust_7y",   7.0,   "7-Year Treasury Constant Maturity"),
    Series("DGS10",   "ust_10y", 10.0,   "10-Year Treasury Constant Maturity"),
    Series("DGS20",   "ust_20y", 20.0,   "20-Year Treasury Constant Maturity"),
    Series("DGS30",   "ust_30y", 30.0,   "30-Year Treasury Constant Maturity"),
)

POLICY_AND_DEPOSIT: tuple[Series, ...] = (
    Series("DFF",          "eff_fed_funds",   None, "Effective Federal Funds Rate (daily)"),
    Series("DFEDTARU",     "fed_funds_upper", None, "Fed Funds Target Range Upper (post-2008)"),
    Series("MORTGAGE30US", "mortgage_30y",    None, "30-Year Fixed Mortgage Rate (Freddie PMMS)"),
    Series("CPIAUCSL",     "cpi",             None, "CPI All Urban Consumers, SA"),
)

ALL_SERIES: dict[str, Series] = {
    s.name: s for s in (*TREASURY_CURVE, *POLICY_AND_DEPOSIT)
}


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------

class FredClient:
    """Thin requests-based FRED client. We don't need fredapi or pandas-datareader
    for the handful of series we use, and avoiding the dependency keeps the
    reproducibility story simple."""

    def __init__(self, api_key: str | None = None, timeout: float = 30.0):
        self.api_key = api_key or fred_api_key()
        self.timeout = timeout
        self._session = requests.Session()

    def fetch(
        self,
        series_id: str,
        start: date | str | None = None,
        end: date | str | None = None,
    ) -> pd.DataFrame:
        """Fetch one FRED series. Returns DataFrame with columns: date, value."""
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
        }
        if start is not None:
            params["observation_start"] = str(start)
        if end is not None:
            params["observation_end"] = str(end)

        resp = self._session.get(FRED_BASE_URL, params=params, timeout=self.timeout)
        resp.raise_for_status()
        observations = resp.json().get("observations", [])
        if not observations:
            log.warning("FRED returned 0 observations for %s", series_id)
            return pd.DataFrame(columns=["date", "value"])

        df = pd.DataFrame(observations)[["date", "value"]]
        df["date"] = pd.to_datetime(df["date"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")  # '.' becomes NaN
        return df

    def fetch_many(
        self,
        series_names: list[str],
        start: date | str | None = None,
        end: date | str | None = None,
    ) -> pd.DataFrame:
        """Fetch multiple registered series and return a long-format frame."""
        frames: list[pd.DataFrame] = []
        for name in series_names:
            if name not in ALL_SERIES:
                raise KeyError(f"Unknown series {name!r}. Available: {sorted(ALL_SERIES)}")
            s = ALL_SERIES[name]
            df = self.fetch(s.fred_id, start=start, end=end)
            df["series"] = name
            df["fred_id"] = s.fred_id
            df["tenor_years"] = s.tenor_years
            frames.append(df)
        return pd.concat(frames, ignore_index=True)
