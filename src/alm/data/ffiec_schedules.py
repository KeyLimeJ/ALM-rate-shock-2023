"""Map of FFIEC Call Report schedules and MDRM field codes we care about.

A Call Report is a collection of schedules (RC, RC-B, RC-C, RC-E, RC-K, RC-N,
RC-O, RC-R, RI, etc.). Each schedule's bulk-data file inside the FFIEC ZIP is
named like::

    FFIEC CDR Call Schedule RC 12312022.txt
    FFIEC CDR Call Schedule RCB 12312022.txt   # RC-B securities
    FFIEC CDR Call Schedule RCEI 12312022.txt  # RC-E part I (domestic deposits)

The hyphen in the schedule name is dropped in the filename.

MDRM codes (Micro Data Reference Manual) are the Fed's universal data
dictionary. A code like "RCFD2170" decomposes as:

    RCFD = reporting series (Consolidated Foreign+Domestic offices)
    2170 = item number (Total assets)

For domestic-only or smaller banks, fields appear under RCON instead of RCFD.
We keep both keys where applicable and resolve to whichever is reported.

References:
    FFIEC Call Report Instructions (Form FFIEC 031/041):
        https://www.ffiec.gov/ffiec_report_forms.htm
    MDRM Public Index:
        https://www.federalreserve.gov/apps/mdrm/
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Schedule:
    code: str             # "RC", "RC-B", ...
    filename_token: str   # "RC", "RCB", "RCEI", ...
    title: str
    purpose: str          # one-liner: why we care for ALM


SCHEDULES: dict[str, Schedule] = {
    "RC":   Schedule("RC",   "RC",   "Balance Sheet",
                     "Top-of-house totals: assets, deposits, equity"),
    "RC-B": Schedule("RC-B", "RCB",  "Securities",
                     "HTM/AFS by type & maturity — drives EVE / HTM unrealized loss"),
    "RC-C": Schedule("RC-C", "RCCI", "Loans and Leases (Part I)",
                     "Loans by category; Memorandum item 2 gives repricing buckets"),
    "RC-E": Schedule("RC-E", "RCEI", "Deposits (Part I, Domestic)",
                     "NMD vs. time deposits — beta scenarios apply to NMD only"),
    "RC-K": Schedule("RC-K", "RCK",  "Quarterly Averages",
                     "Earning-asset / liability averages — NIM denominator"),
    "RC-N": Schedule("RC-N", "RCN",  "Past Due and Nonaccrual",
                     "Credit-quality context (secondary for this project)"),
    "RC-O": Schedule("RC-O", "RCO",  "Other Data for Deposit Insurance",
                     "Estimated uninsured deposits (Memo item 2)"),
    "RC-R": Schedule("RC-R", "RCRI", "Regulatory Capital (Part I)",
                     "Tier 1 capital — denominator for ΔEVE / T1"),
    "RI":   Schedule("RI",   "RI",   "Income Statement",
                     "NII baseline: interest income, interest expense"),
}


# ---------------------------------------------------------------------------
# MDRM codes we extract. We list both RCFD (consolidated) and RCON (domestic-
# only) variants; the loader picks whichever is populated. The numeric stem is
# the same; only the prefix differs.
# ---------------------------------------------------------------------------

# Schedule RC — Balance Sheet
FIELDS_RC: dict[str, str] = {
    # MDRM stem -> human-readable name
    "2170": "total_assets",
    "2200": "total_deposits",
    "3210": "total_equity_capital",
    "0010": "cash_and_balances_due",
    "1754": "htm_securities",      # Held-to-maturity securities, amortized cost
    "1773": "afs_securities",      # Available-for-sale securities, fair value
    "2122": "loans_and_leases_net",
}

# Schedule RC-B — Securities (HTM amortized cost + fair value, AFS amortized + fair)
# Critical for HTM unrealized loss reconstruction.
FIELDS_RCB: dict[str, str] = {
    # Totals (HTM amortized cost and fair value across all security types)
    "1754": "htm_amortized_cost_total",
    "1771": "htm_fair_value_total",
    "1772": "afs_amortized_cost_total",
    "1773": "afs_fair_value_total",
}

# Schedule RC-E — Deposits
FIELDS_RCE: dict[str, str] = {
    # Memo item: non-interest-bearing demand vs interest-bearing,
    # plus total transaction / non-transaction / time deposits.
    "2215": "transaction_accounts_total",
    "2385": "nontransaction_savings_total",
    "6648": "time_deposits_less_100k",   # legacy threshold
    "2604": "time_deposits_100k_plus",
}

# Schedule RC-O — Deposit insurance / uninsured deposits
FIELDS_RCO: dict[str, str] = {
    "5597": "estimated_uninsured_deposits",  # Memo item 2 — the SVB headline number
}

# Schedule RC-R — Tier 1 Capital
FIELDS_RCR: dict[str, str] = {
    "8274": "tier1_capital",            # Common Equity Tier 1 + Additional Tier 1 (legacy)
    "P859": "common_equity_tier1",      # CET1 capital
    "7204": "tier1_leverage_ratio",     # Tier 1 leverage ratio
}

# Schedule RI — Income Statement
FIELDS_RI: dict[str, str] = {
    "4107": "total_interest_income",
    "4073": "total_interest_expense",
    "4074": "net_interest_income",      # 4107 - 4073
    "4340": "net_income",
}

# Map schedule code -> field dict, used by the loader.
SCHEDULE_FIELDS: dict[str, dict[str, str]] = {
    "RC":   FIELDS_RC,
    "RC-B": FIELDS_RCB,
    "RC-E": FIELDS_RCE,
    "RC-O": FIELDS_RCO,
    "RC-R": FIELDS_RCR,
    "RI":   FIELDS_RI,
}


def candidate_columns(stem: str) -> tuple[str, ...]:
    """Return the MDRM column names to try for a numeric field stem.

    Order matters: we prefer the consolidated (RCFD) reporting if present, then
    fall back to domestic-only (RCON). Some capital / income items use RIAD.
    """
    return (f"RCFD{stem}", f"RCON{stem}", f"RCFA{stem}", f"RCOA{stem}", f"RIAD{stem}", stem)
