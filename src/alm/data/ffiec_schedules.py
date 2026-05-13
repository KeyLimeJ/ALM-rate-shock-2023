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
# Several MDRM codes here were renumbered by FFIEC when Schedule RC was
# restructured circa 2010-2011 and again when HTM disclosure changed. The
# codes below are the live ones as of the FFIEC 031 form in use 2018-2023.
FIELDS_RC: dict[str, str] = {
    # MDRM stem -> human-readable name
    "2170": "total_assets",
    "2200": "total_deposits",
    "3210": "total_equity_capital",
    "0081": "cash_noninterest_bearing",          # Item 1.a (replaces legacy 0010)
    "0071": "cash_interest_bearing_balances",    # Item 1.b
    "JJ34": "htm_securities",                    # Item 2.a (replaces legacy 1754 on RC)
    "1773": "afs_securities",                    # Item 2.b
    "B528": "loans_and_leases_net",              # Item 4.b (replaces legacy 2122)
    "3123": "allowance_loan_losses",             # Item 4.c
}

# Schedule RC-B — Securities (HTM amortized cost + fair value, AFS amortized + fair)
# Critical for HTM unrealized loss reconstruction.
#
# Memorandum item 2 reports debt-securities portfolio by remaining maturity or
# next repricing date, with three sub-categories:
#   M2.a  Securities issued by US Treasury, US Government agencies, and states
#         and political subdivisions (A549–A554) — 6 maturity buckets.
#   M2.b  Mortgage pass-through securities (A555–A560) — 6 maturity buckets.
#         Note: shown by *contractual* maturity, not expected average life.
#         Static-gap convention; prepayment is modeled in M3 EVE separately.
#   M2.c  Other mortgage-backed securities, CMOs / REMICs (A561–A562) — 2
#         buckets by *expected average life* (≤3Y, >3Y).
FIELDS_RCB: dict[str, str] = {
    # Totals
    "1754": "htm_amortized_cost_total",
    "1771": "htm_fair_value_total",
    "1772": "afs_amortized_cost_total",
    "1773": "afs_fair_value_total",
    # M2.a — Treasuries / Agencies / Munis by remaining maturity
    "A549": "secs_treasury_le_3m",
    "A550": "secs_treasury_3m_12m",
    "A551": "secs_treasury_1y_3y",
    "A552": "secs_treasury_3y_5y",
    "A553": "secs_treasury_5y_15y",
    "A554": "secs_treasury_gt_15y",
    # M2.b — MBS pass-throughs by remaining maturity (NOT expected average life)
    "A555": "secs_mbs_passthrough_le_3m",
    "A556": "secs_mbs_passthrough_3m_12m",
    "A557": "secs_mbs_passthrough_1y_3y",
    "A558": "secs_mbs_passthrough_3y_5y",
    "A559": "secs_mbs_passthrough_5y_15y",
    "A560": "secs_mbs_passthrough_gt_15y",
    # M2.c — CMOs / other MBS by expected average life
    "A561": "secs_cmo_other_wal_le_3y",
    "A562": "secs_cmo_other_wal_gt_3y",
}

# Schedule RC-E — Deposits
# The deposit-insurance threshold moved from $100k to $250k in 2008 (Emergency
# Economic Stabilization Act, made permanent by Dodd-Frank). Time-deposit
# bucket codes were renumbered accordingly: J473 / J474 replaced 6648 / 2604.
#
# Memorandum 3 reports time-deposit maturity buckets, separately for time
# deposits of $250k or more (HK07-HK10) and less than $250k (HK12-HK15). Four
# buckets each: ≤3M, 3M-12M, 1Y-3Y, >3Y. (HK11 is intentionally skipped in the
# FFIEC form.)
FIELDS_RCE: dict[str, str] = {
    # Totals
    "2215": "transaction_accounts_total",
    "2385": "nontransaction_savings_total",
    "J473": "time_deposits_less_250k",
    "J474": "time_deposits_250k_plus",
    # M3 — time deposits ≥ $250k by remaining maturity
    "HK07": "td_ge250k_le_3m",
    "HK08": "td_ge250k_3m_12m",
    "HK09": "td_ge250k_1y_3y",
    "HK10": "td_ge250k_gt_3y",
    # M3 — time deposits < $250k by remaining maturity
    "HK12": "td_lt250k_le_3m",
    "HK13": "td_lt250k_3m_12m",
    "HK14": "td_lt250k_1y_3y",
    "HK15": "td_lt250k_gt_3y",
}

# Schedule RC-C (Part I) — Loans and Leases
# Memorandum 2 reports loans by remaining maturity OR next repricing date,
# split into two categories (closed-end 1-4 family residential mortgages and
# all other loans) × six maturity buckets each.
FIELDS_RCC: dict[str, str] = {
    # M2.a — Closed-end 1-4 family residential mortgages by remaining maturity
    "A564": "loans_1to4fam_le_3m",
    "A565": "loans_1to4fam_3m_12m",
    "A566": "loans_1to4fam_1y_3y",
    "A567": "loans_1to4fam_3y_5y",
    "A568": "loans_1to4fam_5y_15y",
    "A569": "loans_1to4fam_gt_15y",
    # M2.b — All other loans by remaining maturity
    "A570": "loans_other_le_3m",
    "A571": "loans_other_3m_12m",
    "A572": "loans_other_1y_3y",
    "A573": "loans_other_3y_5y",
    "A574": "loans_other_5y_15y",
    "A575": "loans_other_gt_15y",
}

# Schedule RC-O — Deposit insurance / uninsured deposits
FIELDS_RCO: dict[str, str] = {
    "5597": "estimated_uninsured_deposits",  # Memo item 2 — the SVB headline number
}

# Schedule RC-R — Tier 1 Capital
# Note: RC-R uses the RCFA / RCOA reporting series (regulatory capital), not
# RCFD / RCON. The leverage ratio (7204) is reported as a percent string (e.g.
# "7.2429%") which doesn't survive numeric coercion — compute it downstream as
# tier1_capital / average_total_assets instead.
FIELDS_RCR: dict[str, str] = {
    "8274": "tier1_capital",            # Tier 1 capital
    "P859": "common_equity_tier1",      # CET1 capital
    "A224": "average_total_assets",     # Average total assets for leverage ratio
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
    "RC-C": FIELDS_RCC,
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
