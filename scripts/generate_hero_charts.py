"""Generate static hero chart PNGs for the README.

These are the three charts that a hiring manager scanning the GitHub repo
should see immediately — they encode the entire SVB postmortem in three
images. The dashboard is interactive; the README needs static evidence.

Output: ``docs/charts/{htm_concentration,unrealized_vs_tier1,breakeven_outflow}.png``
"""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from alm.config import PATHS
from alm.data import banks
from alm.models.liquidity import lcr_time_series

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "charts"
OUT_DIR.mkdir(parents=True, exist_ok=True)

BANK_COLORS = {"SVB": "#c0392b", "Huntington": "#2c3e50"}


def _quarter_to_date(q: str) -> pd.Timestamp:
    month_map = {"1": "03-31", "2": "06-30", "3": "09-30", "4": "12-31"}
    return pd.Timestamp(f"{q[:4]}-{month_map[q[-1]]}")


def _get_value(df: pd.DataFrame, rssd: int, q: str, field: str) -> float:
    s = df[(df["rssd_id"] == rssd) & (df["quarter"] == q) & (df["field"] == field)]["value"]
    return float(s.iloc[0]) if not s.empty and pd.notna(s.iloc[0]) else float("nan")


def build_bank_metrics(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Return per-bank wide-format frames with the metrics each chart needs."""
    out: dict[str, pd.DataFrame] = {}
    for bank_key in ("svb", "hban"):
        rssd = banks.get(bank_key).rssd_id
        bank_quarters = sorted(df[df["rssd_id"] == rssd]["quarter"].unique())
        rows = []
        for q in bank_quarters:
            ta = _get_value(df, rssd, q, "total_assets")
            htm_ac = _get_value(df, rssd, q, "htm_amortized_cost_total")
            htm_fv = _get_value(df, rssd, q, "htm_fair_value_total")
            afs_ac = _get_value(df, rssd, q, "afs_amortized_cost_total")
            afs_fv = _get_value(df, rssd, q, "afs_fair_value_total")
            tier1 = _get_value(df, rssd, q, "tier1_capital")
            if any(pd.isna(x) for x in (ta, htm_ac, htm_fv, afs_ac, afs_fv, tier1)):
                continue
            rows.append({
                "quarter": q,
                "date": _quarter_to_date(q),
                "htm_pct_assets": htm_ac / ta,
                "unrealized_loss_pct_tier1": ((htm_ac - htm_fv) + (afs_ac - afs_fv)) / tier1,
            })
        out[banks.get(bank_key).short_name] = pd.DataFrame(rows)
    return out


# ---------------------------------------------------------------------------
# Chart styling
# ---------------------------------------------------------------------------

def _set_style() -> None:
    plt.rcParams.update({
        "figure.figsize": (10, 5.5),
        "figure.dpi": 130,
        "savefig.dpi": 130,
        "savefig.bbox": "tight",
        "savefig.facecolor": "white",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.titlesize": 14,
        "axes.titleweight": "semibold",
        "axes.labelsize": 11,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "legend.fontsize": 10,
        "legend.frameon": False,
    })


# ---------------------------------------------------------------------------
# Chart 1 — HTM concentration over time
# ---------------------------------------------------------------------------

def chart_htm_concentration(metrics: dict[str, pd.DataFrame]) -> Path:
    fig, ax = plt.subplots()
    for bank, frame in metrics.items():
        ax.plot(frame["date"], frame["htm_pct_assets"] * 100,
                label=bank, color=BANK_COLORS[bank], linewidth=2, marker="o")
    ax.set_title("HTM securities as % of total assets")
    ax.set_ylabel("HTM / total assets (%)")
    ax.legend(loc="upper left")
    ax.set_ylim(bottom=0)
    fig.text(0.5, -0.02,
             "SVB built ~47% of its balance sheet into a long-duration HTM book "
             "during 2020–2021 ZIRP.\nHuntington kept HTM under 10%. The duration "
             "mismatch was a pre-2022 strategic choice, not a 2022 mistake.",
             ha="center", fontsize=9, style="italic", color="#555")
    out = OUT_DIR / "htm_concentration.png"
    fig.savefig(out)
    plt.close(fig)
    return out


# ---------------------------------------------------------------------------
# Chart 2 — Unrealized loss as % of Tier 1
# ---------------------------------------------------------------------------

def chart_unrealized_vs_tier1(metrics: dict[str, pd.DataFrame]) -> Path:
    fig, ax = plt.subplots()
    for bank, frame in metrics.items():
        ax.plot(frame["date"], frame["unrealized_loss_pct_tier1"] * 100,
                label=bank, color=BANK_COLORS[bank], linewidth=2, marker="o")
    ax.axhline(100, color="#c0392b", linestyle="--", linewidth=1.5, alpha=0.7)
    ax.text(
        frame["date"].iloc[-1], 103,
        "Tier 1 wiped out, mark-to-market",
        ha="right", va="bottom", fontsize=9, color="#c0392b", weight="semibold",
    )
    ax.set_title("Combined HTM + AFS unrealized loss as % of Tier 1 capital")
    ax.set_ylabel("Loss / Tier 1 (%)")
    ax.legend(loc="upper left")
    fig.text(0.5, -0.02,
             "Mark-to-market, SVB crossed 100% of Tier 1 by Q4 2022 — already past the "
             "technical-solvency line\nthree months before the run. Huntington crested at 33%.",
             ha="center", fontsize=9, style="italic", color="#555")
    out = OUT_DIR / "unrealized_vs_tier1.png"
    fig.savefig(out)
    plt.close(fig)
    return out


# ---------------------------------------------------------------------------
# Chart 3 — Breakeven uninsured outflow (the liquidity headline)
# ---------------------------------------------------------------------------

def chart_breakeven_outflow(df: pd.DataFrame) -> Path:
    fig, ax = plt.subplots()
    for bank_key, bank_label in (("svb", "SVB"), ("hban", "Huntington")):
        rssd = banks.get(bank_key).rssd_id
        ts = lcr_time_series(df, rssd)
        if ts.empty:
            continue
        ts["date"] = [_quarter_to_date(q) for q in ts["quarter"]]
        ax.plot(ts["date"], ts["breakeven_uninsured_outflow"] * 100,
                label=bank_label, color=BANK_COLORS[bank_label],
                linewidth=2, marker="o")
    ax.axhline(25, color="#7f8c8d", linestyle=":", linewidth=1.2)
    ax.axhline(70, color="#c0392b", linestyle=":", linewidth=1.2)
    ax.text(ts["date"].iloc[0], 26, "Basel III baseline (~25%)",
            fontsize=9, color="#7f8c8d")
    ax.text(ts["date"].iloc[0], 71, "SVB-style flash run (~70%)",
            fontsize=9, color="#c0392b")
    ax.set_title("Breakeven uninsured-deposit outflow rate (LCR = 100%)")
    ax.set_ylabel("Outflow rate the bank could absorb (%)")
    ax.legend(loc="upper left")
    ax.set_ylim(0, 90)
    fig.text(0.5, -0.02,
             "SVB rode the Basel III baseline through 2022 — capable of absorbing only "
             "~23-25% uninsured runoff.\nOn 9 March 2023, ~25% of deposits left in a "
             "single day. Huntington's headroom was roughly 2× SVB's.",
             ha="center", fontsize=9, style="italic", color="#555")
    out = OUT_DIR / "breakeven_outflow.png"
    fig.savefig(out)
    plt.close(fig)
    return out


# ---------------------------------------------------------------------------

def main() -> None:
    _set_style()
    df = pd.read_parquet(PATHS.processed / "ffiec_long.parquet")
    metrics = build_bank_metrics(df)
    paths = [
        chart_htm_concentration(metrics),
        chart_unrealized_vs_tier1(metrics),
        chart_breakeven_outflow(df),
    ]
    for p in paths:
        print(f"  wrote {p.relative_to(OUT_DIR.parent.parent)}")


if __name__ == "__main__":
    main()
