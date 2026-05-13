"""Tests for the FRED client.

The HTTP layer is mocked — we don't make real network calls in the test suite.
We verify request shape, payload parsing, missing-value handling ('.' → NaN),
and the fetch_many long-format assembly.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from alm.data.fred import ALL_SERIES, FredClient


def _mock_response(observations: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"observations": observations}
    resp.raise_for_status = MagicMock()
    return resp


def test_fetch_returns_typed_dataframe():
    client = FredClient(api_key="testkey")
    obs = [
        {"date": "2022-12-30", "value": "3.99"},
        {"date": "2022-12-31", "value": "4.00"},
        {"date": "2023-01-02", "value": "."},   # FRED's missing-value sentinel
    ]
    with patch.object(client._session, "get", return_value=_mock_response(obs)) as mock_get:
        df = client.fetch("DGS2", start="2022-12-30", end="2023-01-02")

    mock_get.assert_called_once()
    args, kwargs = mock_get.call_args
    params = kwargs["params"]
    assert params["series_id"] == "DGS2"
    assert params["api_key"] == "testkey"
    assert params["observation_start"] == "2022-12-30"

    assert list(df.columns) == ["date", "value"]
    assert df["date"].dtype.kind == "M"   # datetime
    assert df["value"].dtype.kind == "f"  # float
    assert pd.isna(df.loc[2, "value"])    # '.' coerced to NaN
    assert df.loc[1, "value"] == pytest.approx(4.00)


def test_fetch_handles_empty_observations():
    client = FredClient(api_key="testkey")
    with patch.object(client._session, "get", return_value=_mock_response([])):
        df = client.fetch("DGS2")
    assert df.empty
    assert list(df.columns) == ["date", "value"]


def test_fetch_many_assembles_long_format():
    client = FredClient(api_key="testkey")

    def fake_get(url, params, timeout):
        sid = params["series_id"]
        obs = {
            "DGS2":  [{"date": "2022-12-30", "value": "4.41"}],
            "DGS10": [{"date": "2022-12-30", "value": "3.88"}],
        }.get(sid, [])
        return _mock_response(obs)

    with patch.object(client._session, "get", side_effect=fake_get):
        df = client.fetch_many(["ust_2y", "ust_10y"])

    assert set(df["series"]) == {"ust_2y", "ust_10y"}
    assert set(df["fred_id"]) == {"DGS2", "DGS10"}
    # Tenor passes through from the registry.
    assert df.loc[df["series"] == "ust_2y", "tenor_years"].iloc[0] == 2.0
    assert df.loc[df["series"] == "ust_10y", "tenor_years"].iloc[0] == 10.0


def test_fetch_many_rejects_unknown_series():
    client = FredClient(api_key="testkey")
    with pytest.raises(KeyError, match="not_a_series"):
        client.fetch_many(["not_a_series"])


def test_registered_series_have_unique_names():
    # Defensive: name collisions in ALL_SERIES would silently overwrite entries.
    names = list(ALL_SERIES.keys())
    assert len(names) == len(set(names))
