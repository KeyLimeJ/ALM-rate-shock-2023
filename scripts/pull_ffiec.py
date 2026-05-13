"""CLI: parse one or more FFIEC Call Report quarters for our banks.

Usage::

    # Parse a single quarter for the default bank set (SVB + Huntington)
    python -m scripts.pull_ffiec --quarter 2022Q4

    # Parse a range of quarters
    python -m scripts.pull_ffiec --quarter 2019Q1 --quarter 2023Q1

    # Parse a specific bank only
    python -m scripts.pull_ffiec --quarter 2022Q4 --bank svb

Bulk ZIPs must be present in ``data/raw/`` — see :mod:`alm.data.ffiec` for the
download workflow.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
import pandas as pd

from alm.config import PATHS
from alm.data import banks
from alm.data.ffiec import Quarter, extract_bank_quarter, find_bulk_zip

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pull_ffiec")


@click.command()
@click.option("--quarter", "quarters", multiple=True, required=True,
              help="Quarter label like 2022Q4 (repeatable).")
@click.option("--bank", "bank_keys", multiple=True, default=None,
              help="Bank short-key from alm.data.banks (e.g. svb). "
                   "Repeatable. Default: all registered banks.")
@click.option("--out", "out_path", type=click.Path(path_type=Path), default=None,
              help="Output parquet path. Default: data/processed/ffiec_long.parquet")
def main(quarters: tuple[str, ...], bank_keys: tuple[str, ...], out_path: Path | None) -> None:
    PATHS.ensure()

    selected = [banks.get(k) for k in bank_keys] if bank_keys else list(banks.BANKS.values())
    rssd_ids = [b.rssd_id for b in selected]
    log.info("Banks: %s", ", ".join(f"{b.short_name} ({b.rssd_id})" for b in selected))

    frames: list[pd.DataFrame] = []
    for q_label in quarters:
        quarter = Quarter.parse(q_label)
        try:
            zip_path = find_bulk_zip(PATHS.raw, quarter)
        except FileNotFoundError as e:
            log.error("%s", e)
            sys.exit(1)
        log.info("Parsing %s from %s", quarter.label, zip_path.name)
        df = extract_bank_quarter(zip_path, rssd_ids, quarter)
        frames.append(df)

    out = pd.concat(frames, ignore_index=True)
    if out_path is None:
        out_path = PATHS.processed / "ffiec_long.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_path, index=False)
    log.info("Wrote %d rows to %s", len(out), out_path)

    _print_balance_sheet_check(out, selected)


def _print_balance_sheet_check(df: pd.DataFrame, selected: list[banks.Bank]) -> None:
    """Sanity-check: print total_assets per bank-quarter. SVB Q4 2022 should be ~$209B."""
    pivot = (
        df[df["field"] == "total_assets"]
        .pivot_table(index=["quarter", "rssd_id"], values="value", aggfunc="first")
        .reset_index()
    )
    rssd_to_name = {b.rssd_id: b.short_name for b in selected}
    pivot["bank"] = pivot["rssd_id"].map(rssd_to_name)
    pivot["assets_$bn"] = (pivot["value"] / 1_000_000).round(2)  # FFIEC is in $thousands
    print("\nTotal assets (USD billions):")
    print(pivot[["quarter", "bank", "assets_$bn"]].to_string(index=False))


if __name__ == "__main__":
    main()
