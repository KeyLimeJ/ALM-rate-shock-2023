"""Tests for the EVE / unrealized-loss reconstruction module."""
from __future__ import annotations

import pandas as pd
import pytest

from alm.models.eve import (
    RCB_BUCKETS,
    Bond,
    eve_shock_grid,
    interp_curve,
    portfolio_modified_duration,
    reconstruct_portfolio,
    shift_curve,
    total_unrealized_loss,
    treasury_curve_on,
)

# ---------------------------------------------------------------------------
# Bond pricing
# ---------------------------------------------------------------------------

def test_par_bond_prices_at_par():
    """A bond priced at its own coupon rate should price at face."""
    bond = Bond(face=1000.0, coupon=0.05, maturity_years=10.0)
    assert bond.price(0.05) == pytest.approx(1000.0, rel=1e-6)


def test_discount_bond_prices_below_par():
    """Rising rates produce a price below face for a fixed-coupon bond."""
    bond = Bond(face=1000.0, coupon=0.02, maturity_years=10.0)
    price = bond.price(0.05)
    assert price < 1000.0


def test_premium_bond_prices_above_par():
    """Falling rates produce a price above face for a fixed-coupon bond."""
    bond = Bond(face=1000.0, coupon=0.08, maturity_years=10.0)
    price = bond.price(0.05)
    assert price > 1000.0


def test_zero_maturity_returns_face():
    """A bond at zero maturity should return face value (no discounting)."""
    bond = Bond(face=1000.0, coupon=0.05, maturity_years=0.0)
    assert bond.price(0.10) == pytest.approx(1000.0)


def test_modified_duration_proxy_via_pricing():
    """A long-maturity, low-coupon bond should drop ~20% in price for a +200bp shock."""
    bond = Bond(face=1000.0, coupon=0.0175, maturity_years=10.0)
    base = bond.price(0.0175)             # at par
    shocked = bond.price(0.0175 + 0.0200) # +200 bps
    drop = (base - shocked) / base
    assert 0.15 <= drop <= 0.22


# ---------------------------------------------------------------------------
# Curve helpers
# ---------------------------------------------------------------------------

def test_interp_curve_linear_inside_range():
    curve = {1.0: 0.04, 5.0: 0.05}
    # midpoint should be the arithmetic mean
    assert interp_curve(curve, 3.0) == pytest.approx(0.045)


def test_interp_curve_flat_extrapolation_below_min():
    curve = {1.0: 0.04, 5.0: 0.05}
    assert interp_curve(curve, 0.25) == pytest.approx(0.04)


def test_interp_curve_flat_extrapolation_above_max():
    curve = {1.0: 0.04, 5.0: 0.05}
    assert interp_curve(curve, 20.0) == pytest.approx(0.05)


def test_shift_curve_adds_to_every_tenor():
    curve = {1.0: 0.04, 5.0: 0.05}
    shocked = shift_curve(curve, 100)  # +100 bps
    assert shocked[1.0] == pytest.approx(0.05)
    assert shocked[5.0] == pytest.approx(0.06)


def test_treasury_curve_on_picks_most_recent_obs_on_or_before():
    """Should pick the latest available value at or before the requested date."""
    fred_df = pd.DataFrame([
        {"series": "ust_2y", "date": pd.Timestamp("2022-12-28"), "value": 4.39},
        {"series": "ust_2y", "date": pd.Timestamp("2022-12-29"), "value": 4.41},
        {"series": "ust_2y", "date": pd.Timestamp("2023-01-03"), "value": 4.50},
    ])
    curve = treasury_curve_on(fred_df, "2022-12-30")
    assert curve[2.0] == pytest.approx(0.0441)  # the 12-29 value, not 01-03


def test_treasury_curve_skips_missing_values():
    """NaN observations should not appear in the curve dict."""
    fred_df = pd.DataFrame([
        {"series": "ust_2y",  "date": pd.Timestamp("2022-12-29"), "value": 4.41},
        {"series": "ust_10y", "date": pd.Timestamp("2022-12-29"), "value": float("nan")},
    ])
    curve = treasury_curve_on(fred_df, "2022-12-30")
    assert 2.0 in curve
    assert 10.0 not in curve


# ---------------------------------------------------------------------------
# Portfolio reconstruction
# ---------------------------------------------------------------------------

def _flat_curve(rate: float) -> dict[float, float]:
    return {t: rate for t in (0.083, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 20.0, 30.0)}


def test_reconstruct_portfolio_zero_loss_when_curve_equals_book_yield():
    """A portfolio priced at its own book yield should produce zero unrealized loss."""
    buckets = {"secs_treasury_1y_3y": 1000.0, "secs_mbs_passthrough_gt_15y": 5000.0}
    curve = _flat_curve(0.0179)
    recon = reconstruct_portfolio(buckets, curve, book_yield=0.0179)
    assert recon["unrealized_loss"].sum() == pytest.approx(0.0, abs=1e-3)


def test_reconstruct_portfolio_loss_increases_with_higher_curve():
    """Higher rates ⇒ bigger MTM loss (monotonic for a fixed-coupon portfolio)."""
    buckets = {"secs_mbs_passthrough_gt_15y": 10000.0}
    loss_3pct = total_unrealized_loss(reconstruct_portfolio(buckets, _flat_curve(0.03), 0.02))
    loss_5pct = total_unrealized_loss(reconstruct_portfolio(buckets, _flat_curve(0.05), 0.02))
    assert loss_5pct > loss_3pct > 0


def test_reconstruct_portfolio_skips_zero_balance_buckets():
    """Empty buckets should not appear as rows in the output."""
    buckets = {f: 0.0 for f, _ in RCB_BUCKETS}
    buckets["secs_treasury_5y_15y"] = 1000.0
    recon = reconstruct_portfolio(buckets, _flat_curve(0.04), book_yield=0.02)
    assert len(recon) == 1
    assert recon.iloc[0]["field"] == "secs_treasury_5y_15y"


def test_reconstruct_portfolio_honors_midpoint_overrides():
    """An override should change the bond's pricing maturity."""
    buckets = {"secs_mbs_passthrough_gt_15y": 1000.0}
    curve = _flat_curve(0.04)
    recon_default = reconstruct_portfolio(buckets, curve, 0.02)
    recon_short = reconstruct_portfolio(buckets, curve, 0.02,
                                         midpoints={"secs_mbs_passthrough_gt_15y": 5.0})
    # Shorter maturity → smaller MTM loss
    assert recon_short.iloc[0]["unrealized_loss"] < recon_default.iloc[0]["unrealized_loss"]
    assert recon_short.iloc[0]["midpoint_years"] == 5.0


# ---------------------------------------------------------------------------
# Shock grid
# ---------------------------------------------------------------------------

def test_eve_shock_grid_zero_shock_yields_zero_delta():
    buckets = {"secs_treasury_5y_15y": 1000.0}
    grid = eve_shock_grid(buckets, _flat_curve(0.04), book_yield=0.02,
                          shocks_bps=(0,))
    assert grid.iloc[0]["delta_eve"] == pytest.approx(0.0, abs=1e-6)


def test_eve_shock_grid_positive_shock_destroys_value():
    """Rising rates ⇒ negative ΔEVE for a long-duration fixed-coupon portfolio."""
    buckets = {"secs_treasury_5y_15y": 1000.0}
    grid = eve_shock_grid(buckets, _flat_curve(0.04), book_yield=0.02,
                          shocks_bps=(+200,))
    assert grid.iloc[0]["delta_eve"] < 0


def test_eve_shock_grid_negative_shock_creates_value():
    """Falling rates ⇒ positive ΔEVE for a long-duration fixed-coupon portfolio."""
    buckets = {"secs_treasury_5y_15y": 1000.0}
    grid = eve_shock_grid(buckets, _flat_curve(0.04), book_yield=0.02,
                          shocks_bps=(-200,))
    assert grid.iloc[0]["delta_eve"] > 0


def test_portfolio_modified_duration_positive_for_normal_portfolio():
    buckets = {"secs_treasury_5y_15y": 1000.0}
    md = portfolio_modified_duration(buckets, _flat_curve(0.04), book_yield=0.02)
    assert 5.0 < md < 12.0  # 10-year midpoint, low coupon → MD roughly between 5 and 12
