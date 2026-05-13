"""CLI: pull FRED macro series (Treasury curve, policy rates, mortgage rate).

Usage::

    python -m scripts.pull_fred --start 2019-01-01 --end 2023-03-31

The output is a tidy long-format parquet at
``data/processed/fred_macro.parquet``.
"""
from __future__ import annotations

import logging
from pathlib import Path

import click

from alm.config import PATHS
from alm.data.fred import ALL_SERIES, FredClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pull_fred")


@click.command()
@click.option("--start", default="2019-01-01", help="Observation start (YYYY-MM-DD).")
@click.option("--end",   default="2023-03-31", help="Observation end (YYYY-MM-DD).")
@click.option("--out", "out_path", type=click.Path(path_type=Path), default=None,
              help="Output parquet path. Default: data/processed/fred_macro.parquet")
@click.option("--only", "only", multiple=True, default=None,
              help="Restrict to specific registered series names. Repeatable.")
def main(start: str, end: str, out_path: Path | None, only: tuple[str, ...]) -> None:
    PATHS.ensure()
    series = list(only) if only else list(ALL_SERIES.keys())
    log.info("Pulling %d FRED series from %s to %s", len(series), start, end)
    client = FredClient()
    df = client.fetch_many(series, start=start, end=end)
    if out_path is None:
        out_path = PATHS.processed / "fred_macro.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    log.info("Wrote %d rows (%d series) to %s", len(df), df["series"].nunique(), out_path)

    # Quick smoke check: print latest 2Y and 10Y on the end date.
    latest = df.dropna(subset=["value"]).sort_values("date").groupby("series").tail(1)
    print("\nLatest observation per series:")
    print(latest[["series", "fred_id", "date", "value"]].to_string(index=False))


if __name__ == "__main__":
    main()
