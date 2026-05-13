"""Time-weighted 12-month NII sensitivity under parallel rate shocks.

Static-gap NII sensitivity formula
----------------------------------

For each bucket b with midpoint M_b months from now:

    fraction_in_12m = max(0, (12 − M_b) / 12)

This is the share of the next 12 months over which the new rate applies, on
the assumption that items in bucket b reprice at the midpoint of the bucket.

For a parallel rate shock of Δr basis points::

    Δinterest_income_b = RSA_b × (Δr / 10_000) × fraction_in_12m
    Δinterest_expense_b = RSL_b × (Δr / 10_000) × fraction_in_12m

For non-maturity deposits (NMDs), there is no contractual repricing date.
Standard ALM practice models them as repricing immediately with a
*deposit beta* β (the fraction of the policy-rate change that passes through
to deposit rates over a full year)::

    Δinterest_expense_nmd = NMD_balance × β × (Δr / 10_000)   # full year

Net 12-month NII sensitivity::

    ΔNII_12m = Σ_b (RSA_b − RSL_b) × (Δr/10_000) × fraction_in_12m
             − NMD_balance × β × (Δr / 10_000)

Sign convention: ΔNII > 0 means NII rose under the shock — the bank was
asset-sensitive *net of NMDs and their assumed beta*.

Why this is the right starting point
------------------------------------

This is the standard regulator-friendly static-gap formulation (Federal
Reserve SR 96-13; BIS IRRBB Standards 2016, §IV.B). Real banks layer
simulation-based NII with behavioral models for prepayment, deposit
decay, and non-parallel curve shifts. Those refinements belong in later
milestones. For a defensible portfolio piece, the static formulation
correctly captures the *qualitative* shape of the SVB story: asset-
sensitive on paper, liability-sensitive once the NMD beta assumption is
realistic.

The single most important parameter the dashboard exposes is the deposit
beta. The same balance sheet can look perfectly hedged at β=0.3 and
deeply unhedged at β=0.7. This is the entire point of the project.

References:
    Federal Reserve SR 96-13, "Joint Policy Statement on Interest Rate Risk."
    BIS, "Standards: Interest rate risk in the banking book" (April 2016).
    Drechsler, Savov, Schnabl (2017), "The Deposits Channel of Monetary
        Policy," QJE — establishes that deposit beta is the empirically
        dominant driver of bank rate risk.
"""
from __future__ import annotations

import pandas as pd


def nii_12m_shock(
    gap_df: pd.DataFrame,
    shock_bps: int,
    nmd_balance: float,
    nmd_beta: float,
    horizon_months: float = 12.0,
) -> dict[str, float]:
    """Compute 12-month NII delta under one parallel shock + one deposit beta.

    Args:
        gap_df: Output of :func:`alm.models.repricing_gap.compute_gap`. Must
            have columns ``midpoint_months, rsa, rsl``.
        shock_bps: Parallel rate shock in basis points (e.g. ``+200``).
        nmd_balance: Total non-maturity deposit balance (same units as gap_df).
        nmd_beta: Deposit beta on NMDs, in [0, 1].
        horizon_months: NII horizon in months. Default 12.

    Returns:
        dict with: ``shock_bps, nmd_beta, asset_contribution,
        liability_td_contribution, nmd_contribution, delta_nii``. All
        monetary values in the same units as gap_df.
    """
    shock_decimal = shock_bps / 10_000.0

    asset_contrib = 0.0
    liab_td_contrib = 0.0
    for _, row in gap_df.iterrows():
        m = row["midpoint_months"]
        fraction = max(0.0, (horizon_months - m) / horizon_months)
        asset_contrib += float(row["rsa"]) * shock_decimal * fraction
        liab_td_contrib += float(row["rsl"]) * shock_decimal * fraction

    # NMDs assumed to reprice immediately (m=0), applied over the full horizon.
    nmd_contrib = nmd_balance * nmd_beta * shock_decimal

    delta_nii = asset_contrib - liab_td_contrib - nmd_contrib

    return {
        "shock_bps": shock_bps,
        "nmd_beta": nmd_beta,
        "asset_contribution": asset_contrib,
        "liability_td_contribution": liab_td_contrib,
        "nmd_contribution": nmd_contrib,
        "delta_nii": delta_nii,
    }


def shock_grid(
    gap_df: pd.DataFrame,
    nmd_balance: float,
    shocks_bps: tuple[int, ...],
    betas: tuple[float, ...],
) -> pd.DataFrame:
    """Run a full shock × beta grid. Returns one row per (shock, beta) cell."""
    rows: list[dict[str, float]] = []
    for shock in shocks_bps:
        for beta in betas:
            rows.append(nii_12m_shock(gap_df, shock, nmd_balance, beta))
    return pd.DataFrame(rows)
