"""Tests for the 12-month NII sensitivity model."""
from __future__ import annotations

import pandas as pd
import pytest

from alm.models.nii_sensitivity import nii_12m_shock, shock_grid
from alm.models.repricing_gap import BUCKETS


def _gap_with(rsa_by_bucket: dict[str, float],
              rsl_by_bucket: dict[str, float] | None = None) -> pd.DataFrame:
    """Build a minimal gap_df fixture from per-bucket RSA / RSL dicts."""
    rsl_by_bucket = rsl_by_bucket or {}
    rows = []
    cum = 0.0
    for b in BUCKETS:
        rsa = rsa_by_bucket.get(b.key, 0.0)
        rsl = rsl_by_bucket.get(b.key, 0.0)
        gap = rsa - rsl
        cum += gap
        rows.append({
            "bucket": b.key, "label": b.label, "midpoint_months": b.midpoint_months,
            "rsa": rsa, "rsl": rsl, "gap": gap, "cum_gap": cum,
        })
    return pd.DataFrame(rows)


def test_zero_shock_yields_zero_delta():
    gap = _gap_with({"le_3m": 100.0}, {"le_3m": 30.0})
    res = nii_12m_shock(gap, shock_bps=0, nmd_balance=1000.0, nmd_beta=0.5)
    assert res["delta_nii"] == pytest.approx(0.0)
    assert res["asset_contribution"] == pytest.approx(0.0)


def test_asset_only_short_bucket_full_horizon_pickup():
    """$100 in ≤3M bucket (midpoint 1.5m), +100 bps shock, no liabilities, no NMDs.

    Expected: $100 × 0.01 × (12-1.5)/12 = $0.875 of NII pickup.
    """
    gap = _gap_with({"le_3m": 100.0})
    res = nii_12m_shock(gap, shock_bps=100, nmd_balance=0.0, nmd_beta=0.0)
    expected = 100.0 * 0.01 * (12 - 1.5) / 12
    assert res["asset_contribution"] == pytest.approx(expected)
    assert res["nmd_contribution"] == pytest.approx(0.0)
    assert res["delta_nii"] == pytest.approx(expected)


def test_long_bucket_contributes_zero_to_12m():
    """Anything past 12 months should contribute zero — fraction_in_12m clamps to 0."""
    gap = _gap_with({"5y_15y": 1_000_000.0})  # midpoint = 120m
    res = nii_12m_shock(gap, shock_bps=200, nmd_balance=0.0, nmd_beta=0.0)
    assert res["asset_contribution"] == pytest.approx(0.0)
    assert res["delta_nii"] == pytest.approx(0.0)


def test_nmd_contribution_is_balance_times_beta_times_shock():
    """NMDs reprice immediately and over the full horizon → balance × β × Δr."""
    gap = _gap_with({})
    res = nii_12m_shock(gap, shock_bps=200, nmd_balance=10_000.0, nmd_beta=0.7)
    expected = 10_000.0 * 0.7 * 0.02
    assert res["nmd_contribution"] == pytest.approx(expected)
    # NMDs are a liability, so they subtract from delta.
    assert res["delta_nii"] == pytest.approx(-expected)


def test_signed_shock_flips_sign_of_delta():
    """A −200 bps shock should produce the negative of the +200 bps result."""
    gap = _gap_with({"le_3m": 100.0}, {"le_3m": 30.0})
    pos = nii_12m_shock(gap, shock_bps=+200, nmd_balance=500.0, nmd_beta=0.5)
    neg = nii_12m_shock(gap, shock_bps=-200, nmd_balance=500.0, nmd_beta=0.5)
    assert pos["delta_nii"] == pytest.approx(-neg["delta_nii"])


def test_high_beta_flips_asset_sensitive_bank_to_liability_sensitive():
    """The SVB story in microcosm: same balance sheet, two different betas, opposite signs."""
    # $1B in <=3M assets (cash + variable loans). $1B in NMDs. Tiny TD.
    gap = _gap_with({"le_3m": 1_000_000.0})  # $1B (in $K)
    nmd = 1_000_000.0  # $1B
    low_beta = nii_12m_shock(gap, shock_bps=200, nmd_balance=nmd, nmd_beta=0.30)
    high_beta = nii_12m_shock(gap, shock_bps=200, nmd_balance=nmd, nmd_beta=0.95)
    assert low_beta["delta_nii"] > 0    # asset-sensitive at low beta
    assert high_beta["delta_nii"] < 0   # liability-sensitive at high beta


def test_shock_grid_produces_one_row_per_cell():
    gap = _gap_with({"le_3m": 100.0})
    g = shock_grid(gap, nmd_balance=500.0, shocks_bps=(-100, 0, 100), betas=(0.3, 0.7))
    assert len(g) == 6
    assert set(g["shock_bps"]) == {-100, 0, 100}
    assert set(g["nmd_beta"]) == {0.3, 0.7}


def test_custom_horizon_changes_time_weighting():
    """A 24-month horizon should pull in buckets past 12 months that 12m doesn't capture."""
    gap = _gap_with({"1y_3y": 100.0})  # midpoint 24m
    res_12m = nii_12m_shock(gap, 100, 0.0, 0.0, horizon_months=12.0)
    res_24m = nii_12m_shock(gap, 100, 0.0, 0.0, horizon_months=24.0)
    assert res_12m["asset_contribution"] == pytest.approx(0.0)
    # midpoint 24m is at the very end of a 24m horizon → fraction = 0. Still zero.
    assert res_24m["asset_contribution"] == pytest.approx(0.0)
    # A 36m horizon, however, gives fraction = (36-24)/36 = 1/3 → 100 × 0.01 × 1/3
    res_36m = nii_12m_shock(gap, 100, 0.0, 0.0, horizon_months=36.0)
    assert res_36m["asset_contribution"] == pytest.approx(100 * 0.01 * 12 / 36)
