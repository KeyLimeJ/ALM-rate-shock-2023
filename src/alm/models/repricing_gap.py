"""Repricing gap classification.

The repricing gap is the standard regulator-friendly view of a bank's
interest-rate risk: for each time band, the difference between rate-sensitive
assets (RSA) and rate-sensitive liabilities (RSL). A positive gap means more
assets reprice in that band than liabilities (asset-sensitive — benefits from
rising rates); a negative gap is the opposite.

This module **classifies** Call Report line items into time bands. NII shock
sensitivity is computed in :mod:`alm.models.nii_sensitivity` from the output
of this module.

Modeling choices flagged explicitly here:

- We use the canonical six-bucket scheme (≤3M, 3-12M, 1-3Y, 3-5Y, 5-15Y, >15Y)
  used in FFIEC Schedule RC-B Memo 2 and Schedule RC-C Memo 2. This aligns
  the model with the data without further inference.

- **MBS pass-throughs are bucketed by *contractual* maturity** (Schedule RC-B
  Memo 2.b), not by expected average life. This is the static-gap convention
  and is conservative for rising rates (slower prepayments). Prepayment-aware
  cash-flow modeling appears in M3 EVE, not here.

- **CMOs / other MBS** are bucketed by *expected average life* per the form's
  M2.c schedule (the form publishes only two buckets: ≤3Y, >3Y). We map them
  to the 1-3Y and 5-15Y buckets respectively — defensible midpoints, not
  exact.

- **Time deposits with maturity >3Y** (HK10, HK15) are mapped to the 3-5Y
  bucket. The form doesn't disaggregate further. Most bank CDs >3Y are 4-5Y;
  longer-dated retail CDs are vanishingly rare.

- **Non-maturity deposits (transaction + savings) are NOT placed in the gap
  table.** They have no contractual repricing date. Their rate sensitivity is
  modeled in :mod:`alm.models.nii_sensitivity` via a deposit beta.

- **Other borrowed money / FHLB advances** are not yet pulled. For SVB and
  Huntington they were small relative to deposits at the periods we study;
  added in a later milestone if needed.

References:
    FFIEC Call Report Instructions, Schedule RC-B Memorandum 2, Schedule
        RC-C Memorandum 2, Schedule RC-E Memorandum 3.
    Federal Reserve SR 96-13 (Joint Policy Statement on Interest Rate Risk).
    BIS IRRBB Standards (April 2016), Annex 2 (time-band framework).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# ---------------------------------------------------------------------------
# Bucket definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Bucket:
    key: str
    label: str
    midpoint_months: float


# ASSUMPTION: midpoints are arithmetic means of bucket endpoints, except for
# the unbounded >15Y bucket where we use 240 months (= 20Y), which is a
# common ALM convention. For 12-month NII it doesn't matter — anything past
# 12 months contributes zero to the 12-month sensitivity.
BUCKETS: tuple[Bucket, ...] = (
    Bucket("le_3m",  "≤ 3 months",   1.5),
    Bucket("3m_12m", "3–12 months",  7.5),
    Bucket("1y_3y",  "1–3 years",    24.0),
    Bucket("3y_5y",  "3–5 years",    48.0),
    Bucket("5y_15y", "5–15 years",   120.0),
    Bucket("gt_15y", "> 15 years",   240.0),
)
BUCKET_KEYS: tuple[str, ...] = tuple(b.key for b in BUCKETS)
BUCKET_BY_KEY: dict[str, Bucket] = {b.key: b for b in BUCKETS}


# ---------------------------------------------------------------------------
# Field → (side, bucket) mapping
# ---------------------------------------------------------------------------
# side ∈ {"asset", "liability"}. Fields not listed here are not included in
# the gap (e.g., NMDs handled by deposit beta, equity not rate-sensitive).

FIELD_TO_BUCKET: dict[str, tuple[str, str]] = {
    # Cash — overnight
    "cash_noninterest_bearing":          ("asset", "le_3m"),
    "cash_interest_bearing_balances":    ("asset", "le_3m"),

    # Securities — Treasuries / Agencies / Munis by maturity (RC-B M2.a)
    "secs_treasury_le_3m":               ("asset", "le_3m"),
    "secs_treasury_3m_12m":              ("asset", "3m_12m"),
    "secs_treasury_1y_3y":               ("asset", "1y_3y"),
    "secs_treasury_3y_5y":               ("asset", "3y_5y"),
    "secs_treasury_5y_15y":              ("asset", "5y_15y"),
    "secs_treasury_gt_15y":              ("asset", "gt_15y"),

    # Securities — MBS pass-throughs by contractual maturity (RC-B M2.b)
    "secs_mbs_passthrough_le_3m":        ("asset", "le_3m"),
    "secs_mbs_passthrough_3m_12m":       ("asset", "3m_12m"),
    "secs_mbs_passthrough_1y_3y":        ("asset", "1y_3y"),
    "secs_mbs_passthrough_3y_5y":        ("asset", "3y_5y"),
    "secs_mbs_passthrough_5y_15y":       ("asset", "5y_15y"),
    "secs_mbs_passthrough_gt_15y":       ("asset", "gt_15y"),

    # Securities — CMOs / other MBS by expected average life (RC-B M2.c)
    # ASSUMPTION: ≤3Y WAL → 1-3Y bucket; >3Y WAL → 5-15Y (CMOs >3Y typical 5-7Y)
    "secs_cmo_other_wal_le_3y":          ("asset", "1y_3y"),
    "secs_cmo_other_wal_gt_3y":          ("asset", "5y_15y"),

    # Loans — closed-end 1-4 family residential (RC-C M2.a)
    "loans_1to4fam_le_3m":               ("asset", "le_3m"),
    "loans_1to4fam_3m_12m":              ("asset", "3m_12m"),
    "loans_1to4fam_1y_3y":               ("asset", "1y_3y"),
    "loans_1to4fam_3y_5y":               ("asset", "3y_5y"),
    "loans_1to4fam_5y_15y":              ("asset", "5y_15y"),
    "loans_1to4fam_gt_15y":              ("asset", "gt_15y"),

    # Loans — all other loans (RC-C M2.b)
    "loans_other_le_3m":                 ("asset", "le_3m"),
    "loans_other_3m_12m":                ("asset", "3m_12m"),
    "loans_other_1y_3y":                 ("asset", "1y_3y"),
    "loans_other_3y_5y":                 ("asset", "3y_5y"),
    "loans_other_5y_15y":                ("asset", "5y_15y"),
    "loans_other_gt_15y":                ("asset", "gt_15y"),

    # Time deposits ≥ $250k by maturity (RC-E M3, HK07-HK10)
    # ASSUMPTION: >3Y bucket mapped to 3-5Y; longer-dated retail CDs are rare
    "td_ge250k_le_3m":                   ("liability", "le_3m"),
    "td_ge250k_3m_12m":                  ("liability", "3m_12m"),
    "td_ge250k_1y_3y":                   ("liability", "1y_3y"),
    "td_ge250k_gt_3y":                   ("liability", "3y_5y"),

    # Time deposits < $250k by maturity (RC-E M3, HK12-HK15)
    "td_lt250k_le_3m":                   ("liability", "le_3m"),
    "td_lt250k_3m_12m":                  ("liability", "3m_12m"),
    "td_lt250k_1y_3y":                   ("liability", "1y_3y"),
    "td_lt250k_gt_3y":                   ("liability", "3y_5y"),
}


# Non-maturity deposit fields — handled by deposit beta, not by gap bucket.
NMD_FIELDS: tuple[str, ...] = (
    "transaction_accounts_total",
    "nontransaction_savings_total",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_balance_sheet(df: pd.DataFrame, rssd_id: int, quarter: str) -> pd.DataFrame:
    """Classify Call Report line items into (side, bucket) for one bank-quarter.

    Args:
        df: Long-format FFIEC frame with columns ``rssd_id, quarter, field, value``.
        rssd_id: Bank's FFIEC RSSD ID.
        quarter: Quarter label, e.g. ``"2022Q4"``.

    Returns:
        DataFrame with columns ``field, side, bucket, balance`` (balance in $K).
    """
    sub = df[(df["rssd_id"] == rssd_id) & (df["quarter"] == quarter)]
    records: list[dict] = []
    for field, (side, bucket) in FIELD_TO_BUCKET.items():
        v = sub.loc[sub["field"] == field, "value"]
        balance = float(v.iloc[0]) if not v.empty and pd.notna(v.iloc[0]) else 0.0
        records.append({"field": field, "side": side, "bucket": bucket, "balance": balance})
    return pd.DataFrame(records)


def compute_gap(class_df: pd.DataFrame) -> pd.DataFrame:
    """Compute per-bucket repricing gap and cumulative gap.

    Args:
        class_df: Output of :func:`classify_balance_sheet`.

    Returns:
        DataFrame with one row per bucket and columns:
            ``bucket, label, midpoint_months, rsa, rsl, gap, cum_gap``.
        All monetary columns in the same units as the input (FFIEC $K).
    """
    pivot = class_df.groupby(["bucket", "side"])["balance"].sum().unstack(fill_value=0.0)
    for col in ("asset", "liability"):
        if col not in pivot.columns:
            pivot[col] = 0.0

    rows: list[dict] = []
    cum_gap = 0.0
    for b in BUCKETS:
        rsa = float(pivot.at[b.key, "asset"]) if b.key in pivot.index else 0.0
        rsl = float(pivot.at[b.key, "liability"]) if b.key in pivot.index else 0.0
        gap = rsa - rsl
        cum_gap += gap
        rows.append({
            "bucket": b.key,
            "label": b.label,
            "midpoint_months": b.midpoint_months,
            "rsa": rsa,
            "rsl": rsl,
            "gap": gap,
            "cum_gap": cum_gap,
        })
    return pd.DataFrame(rows)


def nmd_balance(df: pd.DataFrame, rssd_id: int, quarter: str) -> float:
    """Total non-maturity deposit balance for one bank-quarter (in $K)."""
    sub = df[(df["rssd_id"] == rssd_id) & (df["quarter"] == quarter)]
    total = 0.0
    for field in NMD_FIELDS:
        v = sub.loc[sub["field"] == field, "value"]
        if not v.empty and pd.notna(v.iloc[0]):
            total += float(v.iloc[0])
    return total
