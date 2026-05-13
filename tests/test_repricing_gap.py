"""Tests for the repricing gap classifier."""
from __future__ import annotations

import pandas as pd
import pytest

from alm.models.repricing_gap import (
    BUCKETS,
    FIELD_TO_BUCKET,
    NMD_FIELDS,
    classify_balance_sheet,
    compute_gap,
    nmd_balance,
)


def _make_frame(records: list[dict]) -> pd.DataFrame:
    """Build a long-format FFIEC frame from a list of (rssd_id, field, value) dicts."""
    rows = [{"rssd_id": r.get("rssd_id", 1), "quarter": r.get("quarter", "2022Q4"),
             "field": r["field"], "value": r["value"]} for r in records]
    return pd.DataFrame(rows, columns=["rssd_id", "quarter", "field", "value"])


def test_buckets_have_distinct_keys():
    keys = [b.key for b in BUCKETS]
    assert len(keys) == len(set(keys))


def test_classify_routes_each_field_correctly():
    """A single field with a known value should land in its mapped (side, bucket)."""
    df = _make_frame([{"field": "cash_noninterest_bearing", "value": 1000.0}])
    out = classify_balance_sheet(df, rssd_id=1, quarter="2022Q4")
    cash_row = out[out["field"] == "cash_noninterest_bearing"].iloc[0]
    assert cash_row["side"] == "asset"
    assert cash_row["bucket"] == "le_3m"
    assert cash_row["balance"] == pytest.approx(1000.0)


def test_classify_yields_zero_for_missing_fields():
    """Fields not in the input frame should produce zero balances, not NaN."""
    df = _make_frame([])  # empty
    out = classify_balance_sheet(df, rssd_id=1, quarter="2022Q4")
    assert len(out) == len(FIELD_TO_BUCKET)
    assert (out["balance"] == 0.0).all()


def test_compute_gap_per_bucket_arithmetic():
    """Confirm rsa, rsl, gap, cum_gap are computed correctly."""
    # Synthetic balance sheet: $100 in <=3M loans, $30 in <=3M time deposits.
    df = _make_frame([
        {"field": "loans_other_le_3m", "value": 100.0},
        {"field": "td_ge250k_le_3m", "value": 30.0},
        {"field": "secs_treasury_5y_15y", "value": 200.0},
    ])
    cls = classify_balance_sheet(df, rssd_id=1, quarter="2022Q4")
    gap = compute_gap(cls)

    # Bucket order is canonical.
    assert list(gap["bucket"]) == [b.key for b in BUCKETS]

    le_3m = gap[gap["bucket"] == "le_3m"].iloc[0]
    assert le_3m["rsa"] == pytest.approx(100.0)
    assert le_3m["rsl"] == pytest.approx(30.0)
    assert le_3m["gap"] == pytest.approx(70.0)
    assert le_3m["cum_gap"] == pytest.approx(70.0)

    long_b = gap[gap["bucket"] == "5y_15y"].iloc[0]
    assert long_b["rsa"] == pytest.approx(200.0)
    assert long_b["rsl"] == pytest.approx(0.0)
    assert long_b["gap"] == pytest.approx(200.0)

    # Cumulative gap should be monotonic-additive.
    assert gap.iloc[-1]["cum_gap"] == pytest.approx(gap["gap"].sum())


def test_compute_gap_handles_empty_classification():
    """Empty input should still produce a six-row frame of zeros."""
    df = _make_frame([])
    cls = classify_balance_sheet(df, rssd_id=1, quarter="2022Q4")
    gap = compute_gap(cls)
    assert len(gap) == len(BUCKETS)
    assert gap["rsa"].sum() == 0.0
    assert gap["rsl"].sum() == 0.0


def test_cmo_le_3y_routes_to_1y_3y_bucket():
    """CMO with WAL <=3Y should land in the 1y_3y bucket per modeling assumption."""
    df = _make_frame([{"field": "secs_cmo_other_wal_le_3y", "value": 500.0}])
    cls = classify_balance_sheet(df, rssd_id=1, quarter="2022Q4")
    gap = compute_gap(cls)
    one_to_three = gap[gap["bucket"] == "1y_3y"].iloc[0]
    assert one_to_three["rsa"] == pytest.approx(500.0)


def test_nmd_balance_sums_transaction_and_savings():
    df = _make_frame([
        {"field": "transaction_accounts_total", "value": 60.0},
        {"field": "nontransaction_savings_total", "value": 40.0},
    ])
    assert nmd_balance(df, rssd_id=1, quarter="2022Q4") == pytest.approx(100.0)


def test_nmd_fields_are_not_in_gap_mapping():
    """NMDs are deliberately excluded from the gap; they're handled by beta."""
    for f in NMD_FIELDS:
        assert f not in FIELD_TO_BUCKET
