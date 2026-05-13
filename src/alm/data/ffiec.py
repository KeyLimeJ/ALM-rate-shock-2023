"""FFIEC Call Report bulk-data loader.

Workflow:

1. **Download** a quarter's bulk ZIP from the FFIEC Central Data Repository's
   Public Data Distribution (PDD). The PDD landing page is interactive (it
   requires accepting a license through an ASP.NET WebForm), so this module
   does *not* attempt to bypass that. Instead, the user (or a CI job) drops
   the per-quarter ZIP into ``data/raw/`` and we parse from there.
   Landing page: https://cdr.ffiec.gov/public/PWS/DownloadBulkData.aspx

2. **Parse** the ZIP. Each schedule is a tab-delimited text file with two
   header rows: row 1 = MDRM field codes, row 2 = human-readable descriptions.
   Row 3 onward is one row per filing institution, keyed by ``IDRSSD``.

3. **Extract** the bank-level long-format frame ``(rssd_id, quarter, schedule,
   field, value)`` that the rest of the project consumes.

This loader is intentionally narrow: it does *not* try to reconstruct every
Call Report field. It only extracts the MDRM codes registered in
:mod:`alm.data.ffiec_schedules`.

References:
    FFIEC Central Data Repository — Public Data Distribution
        https://cdr.ffiec.gov/public/PWS/DownloadBulkData.aspx
    FFIEC Call Report forms (FFIEC 031 / 041 / 051):
        https://www.ffiec.gov/ffiec_report_forms.htm
"""
from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from alm.data.ffiec_schedules import (
    SCHEDULE_FIELDS,
    SCHEDULES,
    Schedule,
    candidate_columns,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Quarter / date helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Quarter:
    """A reporting quarter, identified by its quarter-end date."""
    year: int
    quarter: int  # 1..4

    @property
    def end_date(self) -> pd.Timestamp:
        month, day = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}[self.quarter]
        return pd.Timestamp(year=self.year, month=month, day=day)

    @property
    def filename_token(self) -> str:
        """The MMDDYYYY token FFIEC embeds in bulk filenames."""
        d = self.end_date
        return f"{d.month:02d}{d.day:02d}{d.year:04d}"

    @property
    def label(self) -> str:
        return f"{self.year}Q{self.quarter}"

    @classmethod
    def parse(cls, label: str) -> Quarter:
        """Parse '2022Q4' or '2022-Q4' into a Quarter."""
        s = label.strip().upper().replace("-", "")
        if "Q" not in s or len(s) < 6:
            raise ValueError(f"Invalid quarter label {label!r}; expected like '2022Q4'")
        year_str, q_str = s.split("Q", 1)
        return cls(year=int(year_str), quarter=int(q_str))


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

# ASSUMPTION: FFIEC bulk ZIPs are named like "FFIEC CDR Call Bulk All Schedules
# MMDDYYYY.zip" (single-period) or "FFIEC CDR Call Bulk All Schedules Five
# Periods MMDDYYYY.zip" (multi-period). The single-period file's filename
# contains the date token; the five-period file contains TSVs for five
# distinct dates inside the same archive. We accept either: first look for a
# ZIP whose *filename* contains the date token (fast path), then fall back to
# inspecting each ZIP's *contents* (slow path, cached after first scan).
_CONTENT_INDEX_CACHE: dict[Path, dict[str, Path]] = {}


def _index_raw_dir_by_date(raw_dir: Path) -> dict[str, Path]:
    """Build a mapping from MMDDYYYY token → ZIP path for ZIPs in ``raw_dir``.

    Opens each Call Report ZIP once and inspects its members. Caches the
    result per directory so subsequent lookups are O(1).
    """
    if raw_dir in _CONTENT_INDEX_CACHE:
        return _CONTENT_INDEX_CACHE[raw_dir]

    token_to_zip: dict[str, Path] = {}
    for p in sorted(raw_dir.glob("*.zip")):
        if "Call" not in p.name:
            continue
        try:
            with zipfile.ZipFile(p) as zf:
                # Tokens that appear in any TSV's filename
                for name in zf.namelist():
                    if not name.lower().endswith(".txt"):
                        continue
                    # MMDDYYYY tokens are 8 contiguous digits at quarter ends
                    for chunk in name.replace(".", " ").split():
                        if len(chunk) == 8 and chunk.isdigit() and chunk[:2] in {"03", "06", "09", "12"}:
                            token_to_zip.setdefault(chunk, p)
        except zipfile.BadZipFile:
            log.warning("Skipping malformed ZIP: %s", p.name)
            continue
    _CONTENT_INDEX_CACHE[raw_dir] = token_to_zip
    return token_to_zip


def find_bulk_zip(raw_dir: Path, quarter: Quarter) -> Path:
    """Locate the bulk Call Report ZIP that holds a given quarter's data.

    First tries filename matching (single-period ZIPs). If no filename
    matches, falls back to content-indexing every Call Report ZIP in the
    directory (handles "Five Periods" ZIPs that bundle 5 quarters per file).
    """
    token = quarter.filename_token

    # Fast path: filename contains the date token
    by_name = [
        p for p in raw_dir.glob("*.zip")
        if "Call" in p.name and token in p.name
    ]
    if by_name:
        if len(by_name) > 1:
            log.warning("Multiple ZIPs match %s by name; using %s",
                        quarter.label, by_name[0].name)
        return by_name[0]

    # Slow path: scan contents (covers Five-Period ZIPs)
    by_content = _index_raw_dir_by_date(raw_dir)
    if token in by_content:
        return by_content[token]

    msg = (
        f"No FFIEC Call Report bulk ZIP found for {quarter.label} in {raw_dir}.\n"
        f"Expected a single-period ZIP whose filename contains '{token}', or a "
        f"multi-period ZIP whose contents include TSVs for {token}.\n\n"
        f"Download from https://cdr.ffiec.gov/public/PWS/DownloadBulkData.aspx — "
        f"either 'Call Reports -- Single Period' for {quarter.end_date.strftime('%m/%d/%Y')}, "
        f"or 'Call Reports -- Five Periods' ending on a date that includes {quarter.label}.\n"
        f"Drop the ZIP into {raw_dir} and re-run."
    )
    raise FileNotFoundError(msg)


# ---------------------------------------------------------------------------
# Schedule loading
# ---------------------------------------------------------------------------

def _schedule_member_name(zf: zipfile.ZipFile, schedule: Schedule, quarter: Quarter) -> str:
    """Find the .txt member inside the ZIP that holds a given schedule."""
    token = quarter.filename_token
    needle_a = f"Schedule {schedule.filename_token} "
    needle_b = f"Schedule {schedule.filename_token}"  # tolerate no trailing space
    matches = [
        n for n in zf.namelist()
        if n.lower().endswith(".txt")
        and token in n
        and (needle_a in n or n.split("/")[-1].split(".")[0].endswith(schedule.filename_token + " " + token))
        and "Schedule " + schedule.filename_token in n
    ]
    if not matches:
        # Fall back to a looser match.
        matches = [
            n for n in zf.namelist()
            if n.lower().endswith(".txt")
            and token in n
            and needle_b in n
        ]
    if not matches:
        raise KeyError(
            f"Could not find schedule {schedule.code} ({schedule.filename_token}) "
            f"for {quarter.label} inside ZIP. Members: {zf.namelist()[:5]}..."
        )
    if len(matches) > 1:
        log.debug("Multiple member matches for %s: %s", schedule.code, matches)
    return matches[0]


def load_schedule(zip_path: Path, schedule_code: str, quarter: Quarter) -> pd.DataFrame:
    """Read one schedule's TSV from a bulk ZIP into a DataFrame keyed by IDRSSD.

    Header handling: FFIEC bulk files have two header rows. Row 1 = MDRM
    codes. Row 2 = human-readable descriptions. We use row 1 as the column
    names and skip row 2.
    """
    if schedule_code not in SCHEDULES:
        raise KeyError(f"Unknown schedule {schedule_code!r}. Known: {sorted(SCHEDULES)}")
    sched = SCHEDULES[schedule_code]

    with zipfile.ZipFile(zip_path) as zf:
        member = _schedule_member_name(zf, sched, quarter)
        with zf.open(member) as fh:
            raw = fh.read()

    # FFIEC files are typically Latin-1 / cp1252; try utf-8 first, then fall back.
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise UnicodeDecodeError(f"Could not decode {member} with utf-8/cp1252/latin-1.")

    df = pd.read_csv(
        io.StringIO(text),
        sep="\t",
        header=0,
        skiprows=[1],          # skip the human-readable description row
        dtype=str,             # read everything as string; coerce later
        na_values=["", "NA"],
        keep_default_na=True,
        low_memory=False,
    )

    if "IDRSSD" not in df.columns:
        raise ValueError(
            f"Schedule {schedule_code} TSV is missing the IDRSSD column. "
            f"Got columns: {list(df.columns[:8])}"
        )

    df["IDRSSD"] = pd.to_numeric(df["IDRSSD"], errors="coerce").astype("Int64")
    df = df.dropna(subset=["IDRSSD"]).reset_index(drop=True)
    return df


def coerce_numeric(series: pd.Series) -> pd.Series:
    """Convert a string Call Report column to numeric, treating blanks as NaN."""
    return pd.to_numeric(series, errors="coerce")


# ---------------------------------------------------------------------------
# Long-format extraction
# ---------------------------------------------------------------------------

def extract_fields(
    df: pd.DataFrame,
    rssd_ids: list[int],
    field_map: dict[str, str],
) -> pd.DataFrame:
    """Pull our registered MDRM fields out of a schedule frame.

    Returns a long-format DataFrame with one row per (rssd_id, field).
    Picks the first non-null candidate column (RCFD / RCON / etc.) per field.
    """
    sub = df[df["IDRSSD"].isin(rssd_ids)].copy()
    if sub.empty:
        return pd.DataFrame(columns=["rssd_id", "field", "value", "mdrm_code"])

    records: list[dict] = []
    for _, row in sub.iterrows():
        rssd = int(row["IDRSSD"])
        for stem, name in field_map.items():
            chosen_col: str | None = None
            chosen_val: float | None = None
            for col in candidate_columns(stem):
                if col in df.columns:
                    val = pd.to_numeric(row.get(col), errors="coerce")
                    if pd.notna(val):
                        chosen_col, chosen_val = col, float(val)
                        break
            records.append({
                "rssd_id": rssd,
                "field": name,
                "value": chosen_val,
                "mdrm_code": chosen_col,
            })
    return pd.DataFrame.from_records(records)


def extract_bank_quarter(
    zip_path: Path,
    rssd_ids: list[int],
    quarter: Quarter,
    schedules: tuple[str, ...] = tuple(SCHEDULE_FIELDS),
) -> pd.DataFrame:
    """Build the long-format frame for one or more banks in a single quarter.

    Columns: ``rssd_id, quarter, schedule, field, value, mdrm_code``.
    Values are in thousands of US dollars (FFIEC's native unit) for balance
    sheet and income items, except ratios which are in basis points or percent
    as reported.
    """
    frames: list[pd.DataFrame] = []
    for sched_code in schedules:
        field_map = SCHEDULE_FIELDS[sched_code]
        try:
            df_sched = load_schedule(zip_path, sched_code, quarter)
        except KeyError as e:
            log.warning("Schedule %s not loaded for %s: %s", sched_code, quarter.label, e)
            continue
        long = extract_fields(df_sched, rssd_ids, field_map)
        long["schedule"] = sched_code
        long["quarter"] = quarter.label
        long["quarter_end"] = quarter.end_date
        frames.append(long)

    if not frames:
        return pd.DataFrame(columns=[
            "rssd_id", "quarter", "quarter_end", "schedule", "field", "value", "mdrm_code"
        ])

    out = pd.concat(frames, ignore_index=True)
    return out[[
        "rssd_id", "quarter", "quarter_end", "schedule", "field", "value", "mdrm_code"
    ]]
