"""Tests for the simplified LCR liquidity model."""
from __future__ import annotations

import pandas as pd
import pytest

from alm.models.liquidity import (
    DEFAULT_AFS_HAIRCUT,
    DEFAULT_INSURED_OUTFLOW_RATE,
    DEFAULT_UNINSURED_OUTFLOW_RATE,
    LiquidityComponents,
    breakeven_uninsured_outflow_rate,
    components_for,
    hqla,
    lcr,
    lcr_time_series,
    stressed_outflows_30d,
)

# ---------------------------------------------------------------------------
# HQLA — HTM exclusion is the central modeling claim
# ---------------------------------------------------------------------------

def test_hqla_excludes_htm():
    """HTM must contribute zero to HQLA — that's the SVB story."""
    c = LiquidityComponents(
        cash=10.0,
        afs_fair_value=20.0,
        htm_amortized_cost=100.0,        # massive HTM book — should be invisible to HQLA
        insured_deposits=50.0,
        uninsured_deposits=50.0,
    )
    # HQLA = cash + 0.92 × AFS  (with default 8% haircut)
    expected = 10.0 + (1 - DEFAULT_AFS_HAIRCUT) * 20.0
    assert hqla(c) == pytest.approx(expected)


def test_hqla_haircut_is_configurable():
    c = LiquidityComponents(cash=0.0, afs_fair_value=100.0,
                            htm_amortized_cost=0.0,
                            insured_deposits=0.0, uninsured_deposits=0.0)
    assert hqla(c, afs_haircut=0.0) == pytest.approx(100.0)
    assert hqla(c, afs_haircut=0.15) == pytest.approx(85.0)
    assert hqla(c, afs_haircut=0.50) == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Outflows
# ---------------------------------------------------------------------------

def test_outflows_zero_when_no_deposits():
    c = LiquidityComponents(cash=100.0, afs_fair_value=0.0,
                            htm_amortized_cost=0.0,
                            insured_deposits=0.0, uninsured_deposits=0.0)
    assert stressed_outflows_30d(c) == pytest.approx(0.0)


def test_outflows_split_by_insured_status():
    c = LiquidityComponents(cash=0.0, afs_fair_value=0.0, htm_amortized_cost=0.0,
                            insured_deposits=100.0, uninsured_deposits=200.0)
    expected = (DEFAULT_INSURED_OUTFLOW_RATE * 100.0
                + DEFAULT_UNINSURED_OUTFLOW_RATE * 200.0)
    assert stressed_outflows_30d(c) == pytest.approx(expected)


def test_higher_uninsured_outflow_rate_increases_outflows():
    c = LiquidityComponents(cash=0.0, afs_fair_value=0.0, htm_amortized_cost=0.0,
                            insured_deposits=10.0, uninsured_deposits=90.0)
    low  = stressed_outflows_30d(c, uninsured_outflow_rate=0.25)
    high = stressed_outflows_30d(c, uninsured_outflow_rate=0.70)
    assert high > low


# ---------------------------------------------------------------------------
# LCR
# ---------------------------------------------------------------------------

def test_lcr_returns_inf_when_no_outflows():
    """A bank with no deposits should have infinite LCR (division by zero guard)."""
    c = LiquidityComponents(cash=100.0, afs_fair_value=0.0, htm_amortized_cost=0.0,
                            insured_deposits=0.0, uninsured_deposits=0.0)
    assert lcr(c) == float("inf")


def test_lcr_drops_below_one_with_inadequate_hqla():
    """SVB-like setup: massive HTM, modest AFS, lots of uninsured deposits."""
    c = LiquidityComponents(
        cash=5.0, afs_fair_value=15.0, htm_amortized_cost=100.0,
        insured_deposits=10.0, uninsured_deposits=140.0,
    )
    # HQLA = 5 + 0.92×15 = 18.8
    # Outflows = 0.05×10 + 0.25×140 = 35.5
    # LCR ≈ 0.53
    assert lcr(c) < 1.0
    assert lcr(c) == pytest.approx(18.8 / 35.5, rel=1e-3)


def test_lcr_high_uninsured_outflow_rate_kills_lcr():
    c = LiquidityComponents(
        cash=10.0, afs_fair_value=20.0, htm_amortized_cost=100.0,
        insured_deposits=10.0, uninsured_deposits=140.0,
    )
    base = lcr(c, uninsured_outflow_rate=0.25)
    extreme = lcr(c, uninsured_outflow_rate=0.95)
    assert extreme < base
    assert extreme < 1.0


# ---------------------------------------------------------------------------
# Breakeven solver
# ---------------------------------------------------------------------------

def test_breakeven_uninsured_outflow_solves_lcr_at_one():
    """If we plug the solver's answer back into lcr(), we should get ≈1.0."""
    c = LiquidityComponents(
        cash=10.0, afs_fair_value=30.0, htm_amortized_cost=200.0,
        insured_deposits=50.0, uninsured_deposits=150.0,
    )
    r = breakeven_uninsured_outflow_rate(c)
    computed = lcr(c, uninsured_outflow_rate=r)
    assert computed == pytest.approx(1.0, rel=1e-6)


def test_breakeven_solver_with_custom_target():
    """A target of 1.5 should require a lower outflow rate than target=1.0."""
    c = LiquidityComponents(
        cash=10.0, afs_fair_value=30.0, htm_amortized_cost=200.0,
        insured_deposits=50.0, uninsured_deposits=150.0,
    )
    r_at_1 = breakeven_uninsured_outflow_rate(c, lcr_target=1.0)
    r_at_15 = breakeven_uninsured_outflow_rate(c, lcr_target=1.5)
    assert r_at_15 < r_at_1


def test_breakeven_returns_nan_with_no_uninsured_deposits():
    c = LiquidityComponents(cash=10.0, afs_fair_value=0.0, htm_amortized_cost=0.0,
                            insured_deposits=100.0, uninsured_deposits=0.0)
    assert pd.isna(breakeven_uninsured_outflow_rate(c))


# ---------------------------------------------------------------------------
# Long-format pipeline
# ---------------------------------------------------------------------------

def _row(rssd: int, q: str, field: str, value: float) -> dict:
    return {"rssd_id": rssd, "quarter": q, "field": field, "value": value}


def test_components_for_handles_missing_fields_as_zero():
    """A bank-quarter with no fields recorded should produce a zeroed components dict."""
    df = pd.DataFrame(columns=["rssd_id", "quarter", "field", "value"])
    c = components_for(df, rssd_id=1, quarter="2022Q4")
    assert c.cash == 0.0
    assert c.afs_fair_value == 0.0
    assert c.htm_amortized_cost == 0.0
    assert c.total_deposits == 0.0


def test_components_for_extracts_correctly():
    df = pd.DataFrame([
        _row(1, "2022Q4", "cash_noninterest_bearing", 5.0),
        _row(1, "2022Q4", "cash_interest_bearing_balances", 7.0),
        _row(1, "2022Q4", "afs_fair_value_total", 20.0),
        _row(1, "2022Q4", "htm_amortized_cost_total", 100.0),
        _row(1, "2022Q4", "total_deposits", 200.0),
        _row(1, "2022Q4", "estimated_uninsured_deposits", 180.0),
    ])
    c = components_for(df, rssd_id=1, quarter="2022Q4")
    assert c.cash == pytest.approx(12.0)
    assert c.afs_fair_value == pytest.approx(20.0)
    assert c.htm_amortized_cost == pytest.approx(100.0)
    assert c.insured_deposits == pytest.approx(20.0)
    assert c.uninsured_deposits == pytest.approx(180.0)


def test_lcr_time_series_returns_one_row_per_quarter():
    df = pd.DataFrame([
        _row(1, "2022Q3", "cash_noninterest_bearing", 5.0),
        _row(1, "2022Q3", "afs_fair_value_total", 10.0),
        _row(1, "2022Q3", "total_deposits", 100.0),
        _row(1, "2022Q3", "estimated_uninsured_deposits", 50.0),
        _row(1, "2022Q4", "cash_noninterest_bearing", 7.0),
        _row(1, "2022Q4", "afs_fair_value_total", 8.0),
        _row(1, "2022Q4", "total_deposits", 90.0),
        _row(1, "2022Q4", "estimated_uninsured_deposits", 50.0),
    ])
    ts = lcr_time_series(df, rssd_id=1)
    assert len(ts) == 2
    assert sorted(ts["quarter"].tolist()) == ["2022Q3", "2022Q4"]
    assert (ts["lcr"] > 0).all()
