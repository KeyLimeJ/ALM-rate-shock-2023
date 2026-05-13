"""Simplified LCR-style liquidity model.

The full Basel III Liquidity Coverage Ratio is::

    LCR = HQLA  /  Net cash outflows over 30 days under stress

We implement a deliberately simplified version that captures the core SVB
insight without re-deriving every regulatory detail:

    HQLA = cash + AFS securities (haircut-adjusted)        [HTM excluded]

    Outflows = insured_deposits × insured_outflow_rate
             + uninsured_deposits × uninsured_outflow_rate

The central modeling choice is **excluding HTM securities from HQLA**.
Under both Basel III and US Reg WW, a bank cannot sell HTM securities to
meet liquidity needs without taint risk that forces re-categorization of
its *entire* HTM portfolio to AFS — recognizing all the unrealized losses
through AOCI. That is exactly what SVB had to do on March 8, 2023, and is
exactly what triggered the run.

So the model says: HTM is liquidity-zero in normal operations. The
question is whether the bank has enough AFS + cash to meet outflows
before being forced to break HTM.

Default outflow rates here are calibrated for the *narrative* — not for
exact regulatory compliance:

    insured_outflow_rate   = 0.05   (close to Basel III retail "stable")
    uninsured_outflow_rate = 0.25   (close to Basel III less-stable / wholesale)

The dashboard exposes these as sliders. The SVB-specific point is that
their actual deposit base behaved much more like ``uninsured_outflow_rate
= 0.7-1.0`` in early March 2023 — the model lets the reader dial that in.

AFS haircut: a single blended 8% haircut. Treasuries (Level 1) get 0%,
Agency MBS (Level 2A) get 15%, with portfolio mix-dependent blending.
The exact split isn't in the Call Report; 8% is a defensible blended
default. Configurable.

References:
    Basel Committee, "Basel III: The Liquidity Coverage Ratio and liquidity
        risk monitoring tools" (January 2013).
    Federal Reserve Regulation WW (12 CFR Part 249) — US implementation.
    FDIC, "Failure of Silicon Valley Bank" (April 2023) — section on
        deposit outflow velocity.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# ASSUMPTION: AFS gets a single blended haircut. Basel III: Level 1 (Treasuries,
# central-bank reserves) = 0%; Level 2A (Agency MBS) = 15%. For a portfolio
# weighted ~half/half between Treasuries and Agency MBS, the blended haircut
# is ~7-8%. We use 8% as a defensible default and expose it as a parameter.
DEFAULT_AFS_HAIRCUT: float = 0.08

# ASSUMPTION: outflow rates follow Basel III at the lower end. The SVB-style
# scenario corresponds to >>50% uninsured outflow; expose as sliders.
DEFAULT_INSURED_OUTFLOW_RATE: float = 0.05      # close to Basel III retail "stable"
DEFAULT_UNINSURED_OUTFLOW_RATE: float = 0.25    # close to Basel III less-stable wholesale


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LiquidityComponents:
    """Bank-quarter snapshot of the inputs to a simplified LCR."""
    cash: float                          # noninterest + interest-bearing balances
    afs_fair_value: float                # AFS securities at fair value
    htm_amortized_cost: float            # HTM (NOT in HQLA)
    insured_deposits: float
    uninsured_deposits: float

    @property
    def total_deposits(self) -> float:
        return self.insured_deposits + self.uninsured_deposits


def components_for(df: pd.DataFrame, rssd_id: int, quarter: str) -> LiquidityComponents:
    """Extract the LCR inputs from a long-format FFIEC frame for one bank-quarter."""
    sub = df[(df["rssd_id"] == rssd_id) & (df["quarter"] == quarter)]

    def g(field: str) -> float:
        series = sub.loc[sub["field"] == field, "value"]
        return float(series.iloc[0]) if not series.empty and pd.notna(series.iloc[0]) else 0.0

    total_dep = g("total_deposits")
    uninsured = g("estimated_uninsured_deposits")
    return LiquidityComponents(
        cash=g("cash_noninterest_bearing") + g("cash_interest_bearing_balances"),
        afs_fair_value=g("afs_fair_value_total"),
        htm_amortized_cost=g("htm_amortized_cost_total"),
        insured_deposits=max(0.0, total_dep - uninsured),
        uninsured_deposits=uninsured,
    )


# ---------------------------------------------------------------------------
# LCR core
# ---------------------------------------------------------------------------

def hqla(components: LiquidityComponents, afs_haircut: float = DEFAULT_AFS_HAIRCUT) -> float:
    """High-quality liquid assets (cash + haircut-adjusted AFS). **HTM excluded.**"""
    return components.cash + (1 - afs_haircut) * components.afs_fair_value


def stressed_outflows_30d(
    components: LiquidityComponents,
    insured_outflow_rate: float = DEFAULT_INSURED_OUTFLOW_RATE,
    uninsured_outflow_rate: float = DEFAULT_UNINSURED_OUTFLOW_RATE,
) -> float:
    """Net cash outflows over a 30-day stress horizon."""
    return (insured_outflow_rate * components.insured_deposits
            + uninsured_outflow_rate * components.uninsured_deposits)


def lcr(
    components: LiquidityComponents,
    insured_outflow_rate: float = DEFAULT_INSURED_OUTFLOW_RATE,
    uninsured_outflow_rate: float = DEFAULT_UNINSURED_OUTFLOW_RATE,
    afs_haircut: float = DEFAULT_AFS_HAIRCUT,
) -> float:
    """Liquidity coverage ratio (HQLA / stressed 30-day outflows)."""
    outflows = stressed_outflows_30d(components, insured_outflow_rate, uninsured_outflow_rate)
    if outflows <= 0:
        return float("inf")
    return hqla(components, afs_haircut) / outflows


def breakeven_uninsured_outflow_rate(
    components: LiquidityComponents,
    insured_outflow_rate: float = DEFAULT_INSURED_OUTFLOW_RATE,
    afs_haircut: float = DEFAULT_AFS_HAIRCUT,
    lcr_target: float = 1.0,
) -> float:
    """Solve for the uninsured outflow rate that drives LCR down to ``lcr_target``.

    Solves analytically::

        HQLA = lcr_target × (insured_outflow_rate × insured + r × uninsured)
        → r = (HQLA / lcr_target − insured_outflow_rate × insured) / uninsured

    Returns NaN if the bank has no uninsured deposits, or a value > 1 if
    even 100% uninsured runoff can't drive LCR to the target (i.e., the
    bank is already comfortably above the target).
    """
    if components.uninsured_deposits <= 0:
        return float("nan")
    available_hqla = hqla(components, afs_haircut)
    needed_outflows = available_hqla / lcr_target
    insured_drag = insured_outflow_rate * components.insured_deposits
    remaining = needed_outflows - insured_drag
    return remaining / components.uninsured_deposits


def lcr_time_series(
    df: pd.DataFrame,
    rssd_id: int,
    insured_outflow_rate: float = DEFAULT_INSURED_OUTFLOW_RATE,
    uninsured_outflow_rate: float = DEFAULT_UNINSURED_OUTFLOW_RATE,
    afs_haircut: float = DEFAULT_AFS_HAIRCUT,
) -> pd.DataFrame:
    """LCR for one bank across every quarter in the long-format frame."""
    rows = []
    for q in sorted(df.loc[df["rssd_id"] == rssd_id, "quarter"].unique()):
        c = components_for(df, rssd_id, q)
        rows.append({
            "quarter": q,
            "hqla": hqla(c, afs_haircut),
            "outflows_30d": stressed_outflows_30d(c, insured_outflow_rate, uninsured_outflow_rate),
            "lcr": lcr(c, insured_outflow_rate, uninsured_outflow_rate, afs_haircut),
            "cash": c.cash,
            "afs_fair_value": c.afs_fair_value,
            "htm_amortized_cost": c.htm_amortized_cost,
            "insured_deposits": c.insured_deposits,
            "uninsured_deposits": c.uninsured_deposits,
            "breakeven_uninsured_outflow": breakeven_uninsured_outflow_rate(
                c, insured_outflow_rate, afs_haircut, lcr_target=1.0
            ),
        })
    return pd.DataFrame(rows)
