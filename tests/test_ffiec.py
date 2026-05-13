"""Tests for the FFIEC Call Report loader.

We construct a synthetic FFIEC bulk ZIP in a tmp_path and verify the parser
handles the file structure correctly: two header rows, MDRM column codes,
multiple banks per file, IDRSSD coercion, and field extraction with the
RCFD/RCON fallback order.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from alm.data.ffiec import (
    Quarter,
    extract_bank_quarter,
    extract_fields,
    find_bulk_zip,
    load_schedule,
)
from alm.data.ffiec_schedules import FIELDS_RC

# ---------------------------------------------------------------------------
# Synthetic fixture: a minimal FFIEC-shaped bulk ZIP
# ---------------------------------------------------------------------------

def _make_schedule_text(rows: list[dict[str, str]], columns: list[str], descriptions: list[str]) -> str:
    """Build a tab-delimited schedule text with the FFIEC two-header format."""
    header = "\t".join(columns)
    desc = "\t".join(descriptions)
    body = "\n".join("\t".join(r.get(c, "") for c in columns) for r in rows)
    return f"{header}\n{desc}\n{body}\n"


@pytest.fixture
def fake_zip(tmp_path: Path) -> Path:
    """Create a fake FFIEC bulk ZIP with a Schedule RC for two banks."""
    quarter = Quarter(2022, 4)
    token = quarter.filename_token  # 12312022

    # Schedule RC: total assets, total deposits, equity.
    columns = ["IDRSSD", "RCFD2170", "RCON2170", "RCFD2200", "RCFD3210"]
    descriptions = ["Bank ID", "Total assets (cons)", "Total assets (dom)",
                    "Total deposits", "Total equity"]
    rows = [
        # SVB: rssd 802866, total assets = $209,000,000 thousand = $209B
        {"IDRSSD": "802866", "RCFD2170": "209026000",
         "RCON2170": "209026000", "RCFD2200": "175400000", "RCFD3210": "16236000"},
        # Huntington: rssd 12311, total assets = $183B
        {"IDRSSD": "12311", "RCFD2170": "182933000",
         "RCON2170": "182933000", "RCFD2200": "147000000", "RCFD3210": "16432000"},
        # Random other bank for noise
        {"IDRSSD": "99999", "RCFD2170": "5000000",
         "RCON2170": "5000000", "RCFD2200": "4000000", "RCFD3210": "500000"},
    ]
    rc_text = _make_schedule_text(rows, columns, descriptions)

    zip_path = tmp_path / f"FFIEC CDR Call Bulk All Schedules {token}.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(f"FFIEC CDR Call Schedule RC {token}.txt", rc_text)
    return zip_path


# ---------------------------------------------------------------------------
# Quarter tests
# ---------------------------------------------------------------------------

def test_quarter_filename_token():
    assert Quarter(2022, 4).filename_token == "12312022"
    assert Quarter(2019, 1).filename_token == "03312019"
    assert Quarter(2020, 2).filename_token == "06302020"

def test_quarter_parse():
    assert Quarter.parse("2022Q4") == Quarter(2022, 4)
    assert Quarter.parse("2019-Q1") == Quarter(2019, 1)
    with pytest.raises(ValueError):
        Quarter.parse("not-a-quarter")


# ---------------------------------------------------------------------------
# Bulk-ZIP discovery
# ---------------------------------------------------------------------------

def test_find_bulk_zip_locates_file(fake_zip: Path):
    found = find_bulk_zip(fake_zip.parent, Quarter(2022, 4))
    assert found == fake_zip

def test_find_bulk_zip_raises_helpful_message(tmp_path: Path):
    with pytest.raises(FileNotFoundError) as exc:
        find_bulk_zip(tmp_path, Quarter(2022, 4))
    msg = str(exc.value)
    assert "12312022" in msg
    assert "cdr.ffiec.gov" in msg.lower()
    assert "Five Periods" in msg     # advertises the bigger download option


def test_find_bulk_zip_handles_multi_period_archive(tmp_path: Path):
    """A 'Five Periods' ZIP whose filename mentions only one date should still be
    findable for any quarter whose TSVs are inside it."""
    columns = ["IDRSSD", "RCFD2170"]
    descriptions = ["Bank ID", "Total assets"]
    rows = [{"IDRSSD": "802866", "RCFD2170": "100000000"}]
    rc_text = _make_schedule_text(rows, columns, descriptions)

    # ZIP filename says 03312023, but it contains TSVs for 5 quarters
    zip_path = tmp_path / "FFIEC CDR Call Bulk All Schedules Five Periods 03312023.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for token in ("03312023", "12312022", "09302022", "06302022", "03312022"):
            zf.writestr(f"FFIEC CDR Call Schedule RC {token}.txt", rc_text)

    # Filename-only would only find Q1 2023 — content-indexed finds all 5.
    from alm.data.ffiec import _CONTENT_INDEX_CACHE
    _CONTENT_INDEX_CACHE.clear()  # ensure no leakage from other tests

    assert find_bulk_zip(tmp_path, Quarter(2023, 1)) == zip_path  # fast path (filename)
    assert find_bulk_zip(tmp_path, Quarter(2022, 4)) == zip_path  # slow path (content)
    assert find_bulk_zip(tmp_path, Quarter(2022, 3)) == zip_path
    assert find_bulk_zip(tmp_path, Quarter(2022, 2)) == zip_path
    assert find_bulk_zip(tmp_path, Quarter(2022, 1)) == zip_path

    # A quarter NOT inside the archive should still raise cleanly
    with pytest.raises(FileNotFoundError):
        find_bulk_zip(tmp_path, Quarter(2021, 4))


# ---------------------------------------------------------------------------
# Schedule loading
# ---------------------------------------------------------------------------

def test_load_schedule_returns_idrssd_keyed_frame(fake_zip: Path):
    df = load_schedule(fake_zip, "RC", Quarter(2022, 4))
    assert "IDRSSD" in df.columns
    assert df["IDRSSD"].dtype.name == "Int64"
    assert set(df["IDRSSD"].dropna().astype(int)) == {802866, 12311, 99999}
    # Description row must NOT appear as a data row.
    assert "Total assets (cons)" not in df["RCFD2170"].astype(str).values


# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------

def test_extract_fields_prefers_rcfd_over_rcon(fake_zip: Path):
    df = load_schedule(fake_zip, "RC", Quarter(2022, 4))
    long = extract_fields(df, rssd_ids=[802866], field_map=FIELDS_RC)
    total_assets = long[long["field"] == "total_assets"].iloc[0]
    assert total_assets["value"] == pytest.approx(209_026_000)
    assert total_assets["mdrm_code"] == "RCFD2170"

def test_extract_fields_falls_back_to_rcon_when_rcfd_missing(tmp_path: Path):
    # Build a schedule that has only RCON2170 (no RCFD column at all).
    columns = ["IDRSSD", "RCON2170"]
    descriptions = ["Bank ID", "Total assets (dom)"]
    rows = [{"IDRSSD": "12345", "RCON2170": "7500000"}]
    text = _make_schedule_text(rows, columns, descriptions)
    token = Quarter(2022, 4).filename_token
    zp = tmp_path / f"FFIEC CDR Call Bulk Test {token}.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr(f"FFIEC CDR Call Schedule RC {token}.txt", text)
    df = load_schedule(zp, "RC", Quarter(2022, 4))
    long = extract_fields(df, rssd_ids=[12345], field_map={"2170": "total_assets"})
    row = long.iloc[0]
    assert row["value"] == pytest.approx(7_500_000)
    assert row["mdrm_code"] == "RCON2170"

def test_extract_bank_quarter_long_format(fake_zip: Path):
    out = extract_bank_quarter(
        fake_zip,
        rssd_ids=[802866, 12311],
        quarter=Quarter(2022, 4),
        schedules=("RC",),
    )
    assert set(out.columns) == {
        "rssd_id", "quarter", "quarter_end", "schedule", "field", "value", "mdrm_code"
    }
    # Two banks × len(FIELDS_RC) fields per bank.
    assert len(out) == 2 * len(FIELDS_RC)
    assert set(out["rssd_id"]) == {802866, 12311}
    assert (out["schedule"] == "RC").all()
    assert (out["quarter"] == "2022Q4").all()
    # The three fields we populated in the fixture should have values for both banks.
    populated = out[out["field"].isin({"total_assets", "total_deposits", "total_equity_capital"})]
    assert populated["value"].notna().all()
    assert populated["mdrm_code"].notna().all()

def test_extract_bank_quarter_skips_unknown_banks(fake_zip: Path):
    out = extract_bank_quarter(
        fake_zip,
        rssd_ids=[1111111],  # not in the file
        quarter=Quarter(2022, 4),
        schedules=("RC",),
    )
    assert out.empty
