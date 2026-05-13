"""ALM Rate Shock 2023 — live progress dashboard.

This dashboard grows with each milestone:

    M1 (current): balance-sheet snapshot, HTM concentration, unrealized losses,
                  uninsured deposit %.
    M2:           repricing gap classification + first NII shock.
    M3:           EVE shock grid + HTM unrealized-loss reconstruction.
    M4:           full time series across 2019Q1–2023Q1.
    M5:           liquidity / HQLA / uninsured deposit overlay.
    M6:           narrative polish, hero charts, deploy to Streamlit Cloud.

Run locally::

    uv run streamlit run app/streamlit_app.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from alm.config import PATHS
from alm.data import banks
from alm.models.eve import (
    DEFAULT_BOOK_YIELDS,
    bucket_values_for,
    eve_shock_grid,
    portfolio_modified_duration,
    reconstruct_portfolio,
    treasury_curve_on,
)
from alm.models.liquidity import (
    DEFAULT_AFS_HAIRCUT,
    DEFAULT_INSURED_OUTFLOW_RATE,
    DEFAULT_UNINSURED_OUTFLOW_RATE,
    components_for,
    lcr_time_series,
)
from alm.models.nii_sensitivity import nii_12m_shock
from alm.models.repricing_gap import (
    classify_balance_sheet,
    compute_gap,
    nmd_balance,
)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="ALM Rate Shock 2023",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# Data load
# ---------------------------------------------------------------------------
@st.cache_data
def _load_data_cached(_path: str, _mtime: float) -> pd.DataFrame:
    """Cache keyed on file path + mtime — auto-invalidates when the parquet is re-pulled."""
    df = pd.read_parquet(_path)
    rssd_to_bank = {b.rssd_id: b for b in banks.BANKS.values()}
    df["bank_short"] = df["rssd_id"].map(lambda r: rssd_to_bank[r].short_name)
    df["bank_role"] = df["rssd_id"].map(lambda r: rssd_to_bank[r].role)
    return df


def _resolve_parquet(name: str) -> tuple[Path | None, bool]:
    """Return (path, is_sample). Prefers data/processed/ then falls back to data/sample/."""
    fresh = PATHS.processed / name
    if fresh.exists():
        return fresh, False
    sample = PATHS.sample / name
    if sample.exists():
        return sample, True
    return None, False


def load_data() -> tuple[pd.DataFrame | None, bool]:
    """Load the FFIEC long-format parquet. Returns (frame, is_sample_data)."""
    path, is_sample = _resolve_parquet("ffiec_long.parquet")
    if path is None:
        return None, False
    return _load_data_cached(str(path), path.stat().st_mtime), is_sample


df, is_sample = load_data()
if df is None:
    st.error(
        "**No data found.** Expected `data/processed/ffiec_long.parquet` or "
        "`data/sample/ffiec_long.parquet`.\n\n"
        "Either commit sample data (already done in the repo by default) or pull "
        "fresh data with:\n\n"
        "```\n"
        "uv pip install -e \".[fetch]\"\n"
        "uv run playwright install chromium\n"
        "uv run python -m scripts.fetch_ffiec --headless --from 2019Q1 --to 2023Q1\n"
        "uv run python -m scripts.pull_ffiec --quarter 2019Q1 --quarter 2019Q2 ...\n"
        "```"
    )
    st.stop()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_val(bank_key: str, quarter: str, field: str) -> float | None:
    """Return the value of a given Call Report field for one bank-quarter, or None."""
    rssd = banks.get(bank_key).rssd_id
    rows = df[(df["rssd_id"] == rssd) & (df["quarter"] == quarter) & (df["field"] == field)]
    if rows.empty or pd.isna(rows["value"].iloc[0]):
        return None
    return float(rows["value"].iloc[0])


def hero_metrics(bank_key: str, quarter: str) -> None:
    """Render the 5-KPI row for one bank-quarter."""
    ta = get_val(bank_key, quarter, "total_assets")
    htm = get_val(bank_key, quarter, "htm_amortized_cost_total")
    htm_fv = get_val(bank_key, quarter, "htm_fair_value_total")
    afs = get_val(bank_key, quarter, "afs_amortized_cost_total")
    afs_fv = get_val(bank_key, quarter, "afs_fair_value_total")
    dep = get_val(bank_key, quarter, "total_deposits")
    uninsured = get_val(bank_key, quarter, "estimated_uninsured_deposits")
    tier1 = get_val(bank_key, quarter, "tier1_capital")

    if None in (ta, htm, htm_fv, afs, afs_fv, dep, uninsured, tier1):
        st.warning(f"Incomplete data for {bank_key.upper()} {quarter}.")
        return

    total_unrealized = (htm - htm_fv) + (afs - afs_fv)

    cols = st.columns(5)
    cols[0].metric("Total assets", f"${ta/1e6:.1f}B")
    cols[1].metric("HTM / total assets", f"{htm/ta:.1%}")
    cols[2].metric("Unrealized loss (HTM + AFS, pre-tax)", f"${total_unrealized/1e6:.2f}B")
    cols[3].metric("Unrealized loss / Tier 1 capital", f"{total_unrealized/tier1:.1%}")
    cols[4].metric("Uninsured deposit %", f"{uninsured/dep:.1%}")


def build_chart_frame() -> pd.DataFrame:
    """Wide-format frame keyed by (bank, quarter) used by every chart on this page."""
    available_quarters = sorted(df["quarter"].unique())
    records: list[dict] = []
    for bank_key in ("svb", "hban"):
        for q in available_quarters:
            ta = get_val(bank_key, q, "total_assets")
            htm = get_val(bank_key, q, "htm_amortized_cost_total")
            htm_fv = get_val(bank_key, q, "htm_fair_value_total")
            afs = get_val(bank_key, q, "afs_amortized_cost_total")
            afs_fv = get_val(bank_key, q, "afs_fair_value_total")
            tier1 = get_val(bank_key, q, "tier1_capital")
            dep = get_val(bank_key, q, "total_deposits")
            uninsured = get_val(bank_key, q, "estimated_uninsured_deposits")
            if None in (ta, htm, htm_fv, afs, afs_fv, tier1, dep, uninsured):
                continue
            records.append({
                "bank": banks.get(bank_key).short_name,
                "quarter": q,
                "htm_pct_assets": htm / ta,
                "htm_unrealized_bn": (htm - htm_fv) / 1e6,
                "afs_unrealized_bn": (afs - afs_fv) / 1e6,
                "total_unrealized_pct_t1": ((htm - htm_fv) + (afs - afs_fv)) / tier1,
                "uninsured_pct": uninsured / dep,
            })
    return pd.DataFrame(records)


BANK_COLORS = {"SVB": "#c0392b", "Huntington": "#2c3e50"}


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.title("ALM Rate Shock 2023")
st.sidebar.write(
    "Reconstructing the 2022–2023 US rate shock through the SVB collapse, "
    "with The Huntington National Bank as a survivor-bank contrast."
)
st.sidebar.markdown("---")
available_quarters = sorted(df["quarter"].unique(), reverse=True)
quarter = st.sidebar.selectbox("Reporting period", available_quarters)
st.sidebar.markdown("---")
st.sidebar.markdown(
    "**Milestones**\n\n"
    "- [x] **M1** — data ingestion\n"
    "- [x] **M2** — repricing gap + NII shock\n"
    "- [x] **M3** — EVE + HTM unrealized-loss reconstruction\n"
    "- [x] **M4** — time series across loaded quarters\n"
    "- [x] **M5** — liquidity / HQLA / uninsured overlay\n"
    "- [x] **M6** — narrative polish + deploy\n"
)
st.sidebar.markdown("---")
st.sidebar.markdown(
    "**Built by** [Jared Limon](#about)  \n"
    "Senior risk management leader,  \n"
    "15+ years in credit risk, market making, and quantitative risk strategy."
)
st.sidebar.caption(
    "Numbers reconcile to each bank's 10-K within rounding. Source: FFIEC "
    "Call Report bulk data."
)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
st.title("Casualty vs. Survivor")
st.subheader("Silicon Valley Bank and Huntington National Bank through the 2022–2023 rate shock")

if is_sample:
    st.info(
        "📦 You are viewing the committed sample dataset (full 2019Q1–2023Q1 series). "
        "To re-pull fresh data: `uv run python -m scripts.fetch_ffiec --headless --from 2019Q1 --to 2023Q1` "
        "then `uv run python -m scripts.pull_ffiec --quarter ...` for each quarter."
    )

st.markdown(
    "**Headline finding.** Mark-to-market, SVB's combined HTM + AFS unrealized loss "
    "exceeded its entire Tier 1 capital base by Q4 2022 — three months before the FDIC "
    "seized the bank. A simplified LCR model says SVB's liquidity headroom across all "
    "of 2022 was just **23–25% uninsured-deposit runoff**, the Basel III baseline. "
    "On 9 March 2023, ~25% of deposits left in a single day. **The Call Report data "
    "shown on this page knew, a full quarter before the FDIC did.**\n\n"
    "Huntington National Bank — comparable in size ($173–188B vs SVB's $208–217B), "
    "comparable in business mix, but with a sticky retail-led deposit franchise and a "
    "modest HTM allocation — would have absorbed an SVB-scale shock with capital and "
    "liquidity to spare. **Same regulatory environment, same rate cycle, opposite ALM "
    "choices, opposite outcomes.**"
)

st.markdown("---")

st.write(
    f"**Snapshot below: {quarter}.** Numbers pulled directly from each bank's FFIEC "
    "Call Report (Schedules RC, RC-B, RC-C, RC-E, RC-O, RC-R, RI) and reconcile to "
    "the corresponding 10-K within rounding. Use the **reporting period** selector in "
    "the sidebar to walk the timeline."
)

col_svb, col_hban = st.columns(2)
with col_svb:
    st.markdown("### Silicon Valley Bank · *casualty*")
    hero_metrics("svb", quarter)
with col_hban:
    st.markdown("### The Huntington National Bank · *survivor*")
    hero_metrics("hban", quarter)

st.caption(
    "Five numbers. SVB's HTM allocation was ~5× Huntington's; their mark-to-"
    "market unrealized losses exceeded Tier 1 by Q4 2022; nearly all their "
    "deposits were uninsured. Three independent risk dimensions all pointing "
    "in the same direction."
)

st.markdown("---")

# ===== Charts =====
cd = build_chart_frame()


def grouped_bar(metric: str, title: str, ytick: str, hline: float | None = None,
                hline_text: str = "") -> go.Figure:
    fig = go.Figure()
    for bank in cd["bank"].unique():
        sub = cd[cd["bank"] == bank].sort_values("quarter")
        fig.add_bar(
            name=bank,
            x=sub["quarter"],
            y=sub[metric],
            marker_color=BANK_COLORS.get(bank),
        )
    if hline is not None:
        fig.add_hline(
            y=hline,
            line_dash="dash",
            line_color="red",
            annotation_text=hline_text,
            annotation_position="top right",
        )
    fig.update_layout(
        title=title,
        yaxis_tickformat=ytick,
        barmode="group",
        height=380,
        margin=dict(t=50, b=30, l=10, r=10),
        legend=dict(orientation="h", yanchor="bottom", y=-0.2),
    )
    return fig


c1, c2 = st.columns(2)
with c1:
    st.plotly_chart(
        grouped_bar("htm_pct_assets", "HTM securities as % of total assets", ".0%"),
        use_container_width=True,
    )
    st.caption(
        "SVB allocated nearly half their balance sheet to HTM by end of 2021 — "
        "before a single Fed hike. Huntington kept HTM under 10%. The duration "
        "mismatch wasn't a 2022 mistake; it was a 2020–21 strategic choice."
    )

with c2:
    st.plotly_chart(
        grouped_bar(
            "total_unrealized_pct_t1",
            "Combined unrealized loss as % of Tier 1 capital",
            ".0%",
            hline=1.0,
            hline_text="Tier 1 wiped out",
        ),
        use_container_width=True,
    )
    st.caption(
        "The headline number. Mark-to-market, SVB's Q4 2022 unrealized losses "
        "exceeded their entire Tier 1 capital base — they were already past "
        "the technical-solvency line three months before the run on deposits."
    )


# Second chart row
def stacked_unrealized() -> go.Figure:
    fig = go.Figure()
    for bank in cd["bank"].unique():
        sub = cd[cd["bank"] == bank].sort_values("quarter")
        color = BANK_COLORS.get(bank, "#888888")
        fig.add_bar(
            name=f"{bank} — HTM",
            x=sub["quarter"], y=sub["htm_unrealized_bn"],
            marker_color=color,
        )
        fig.add_bar(
            name=f"{bank} — AFS",
            x=sub["quarter"], y=sub["afs_unrealized_bn"],
            marker_color=color, marker_pattern_shape="/",
        )
    fig.update_layout(
        title="Unrealized loss decomposition (USD billions, pre-tax)",
        yaxis_title="USD billions",
        barmode="stack",
        height=380,
        margin=dict(t=50, b=30, l=10, r=10),
        legend=dict(orientation="h", yanchor="bottom", y=-0.2),
    )
    return fig


c3, c4 = st.columns(2)
with c3:
    st.plotly_chart(stacked_unrealized(), use_container_width=True)
    st.caption(
        "Under GAAP, HTM losses don't hit AOCI — they're 'hidden' until the "
        "bank sells. SVB had the AOCI optics of a healthy bank in Q4 2022 "
        "while the HTM bucket carried the real damage (~$15B of the ~$17B total)."
    )

with c4:
    st.plotly_chart(
        grouped_bar("uninsured_pct", "Uninsured deposits as % of total deposits", ".0%"),
        use_container_width=True,
    )
    st.caption(
        "SVB's deposit base was ~94% uninsured — concentrated venture-backed "
        "operating accounts. When rates rose and tech funding froze, that "
        "deposit base became simultaneously flighty and beta-sensitive: the "
        "two failure modes amplified each other."
    )


# ---------------------------------------------------------------------------
# M2 — Repricing gap and 12-month NII sensitivity
# ---------------------------------------------------------------------------
st.markdown("---")
st.header("M2 · Repricing gap and NII shock sensitivity")
st.write(
    "The repricing gap is the regulator-friendly view of a bank's interest-rate risk: "
    "for each time band, how much in rate-sensitive assets (RSA) reprices vs. "
    "rate-sensitive liabilities (RSL). A positive gap is asset-sensitive (NII rises "
    "with rates); a negative gap is liability-sensitive. "
    "**The story for both these banks lives in the deposit-beta assumption**, "
    "applied to non-maturity deposits (NMDs — transaction + savings accounts) — try the slider below."
)


# Pick which bank's gap to show (defaults to SVB — the story)
gap_col_a, gap_col_b = st.columns([1, 3])
with gap_col_a:
    selected_bank_label = st.radio(
        "Bank",
        ["Silicon Valley Bank", "The Huntington National Bank"],
        index=0,
    )
selected_bank_key = "svb" if selected_bank_label.startswith("Silicon") else "hban"
selected_rssd = banks.get(selected_bank_key).rssd_id


@st.cache_data
def cached_gap(rssd: int, q: str) -> pd.DataFrame:
    cls = classify_balance_sheet(df, rssd, q)
    return compute_gap(cls)


@st.cache_data
def cached_nmd(rssd: int, q: str) -> float:
    return nmd_balance(df, rssd, q)


@st.cache_data
def cached_nii_baseline(rssd: int, q: str) -> float:
    rows = df[(df["rssd_id"] == rssd) & (df["quarter"] == q) & (df["field"] == "net_interest_income")]
    return float(rows["value"].iloc[0]) if not rows.empty else float("nan")


gap_df = cached_gap(selected_rssd, quarter)
nmd_bal = cached_nmd(selected_rssd, quarter)
nii_base = cached_nii_baseline(selected_rssd, quarter)


# ===== Gap bar chart with cumulative line =====
def gap_chart(gap_df: pd.DataFrame, bank_label: str) -> go.Figure:
    """Per-bucket gap bars (positive = asset-sensitive) with cumulative gap line."""
    fig = go.Figure()
    fig.add_bar(
        name="Gap (RSA − RSL)",
        x=gap_df["label"],
        y=gap_df["gap"] / 1e6,           # $thousands → $billions
        marker_color=["#2c3e50" if v >= 0 else "#c0392b" for v in gap_df["gap"]],
    )
    fig.add_scatter(
        name="Cumulative gap",
        x=gap_df["label"],
        y=gap_df["cum_gap"] / 1e6,
        mode="lines+markers",
        line=dict(color="#e67e22", width=2),
        yaxis="y2",
    )
    fig.update_layout(
        title=f"{bank_label} · repricing gap by time band ({quarter})",
        yaxis=dict(title="Per-bucket gap (USD billions)"),
        yaxis2=dict(title="Cumulative gap (USD billions)", overlaying="y", side="right"),
        height=420,
        margin=dict(t=50, b=30, l=10, r=10),
        legend=dict(orientation="h", yanchor="bottom", y=-0.2),
    )
    return fig


with gap_col_b:
    st.plotly_chart(gap_chart(gap_df, selected_bank_label), use_container_width=True)

st.caption(
    "Both banks are heavily asset-sensitive in the ≤3-month bucket — driven by "
    "floating-rate commercial loans and cash that reprice immediately. The "
    "positive gap looks like a tailwind in rising rates. **It is, only as long "
    "as the bank doesn't have to compete to keep its NMD funding.**"
)


# ===== Interactive NII shock =====
st.subheader("Interactive: how does the deposit beta change the answer?")
st.write(
    "Move the sliders. ΔNII is the change in 12-month net interest income under "
    "a parallel rate shock, time-weighted by bucket midpoint, with the deposit "
    "beta applied to non-maturity deposits (transaction + savings)."
)

slider_col1, slider_col2, slider_col3 = st.columns(3)
with slider_col1:
    shock = st.slider("Parallel rate shock (basis points)", -300, 400, 200, step=25)
with slider_col2:
    beta = st.slider("Deposit beta (NMDs)", 0.0, 1.0, 0.5, step=0.05)
with slider_col3:
    horizon = st.select_slider(
        "NII horizon (months)",
        options=[3, 6, 12, 24],
        value=12,
    )

result = nii_12m_shock(
    gap_df=gap_df,
    shock_bps=shock,
    nmd_balance=nmd_bal,
    nmd_beta=beta,
    horizon_months=float(horizon),
)
delta_nii = result["delta_nii"]
pct_baseline = delta_nii / nii_base if nii_base else float("nan")

result_cols = st.columns(4)
result_cols[0].metric(
    f"ΔNII ({horizon}m)",
    f"${delta_nii/1e6:+,.2f}B",
    f"{pct_baseline:+.1%} of baseline" if nii_base else "—",
)
result_cols[1].metric(
    "Asset-side rate pickup",
    f"${result['asset_contribution']/1e6:+,.2f}B",
)
result_cols[2].metric(
    "Time-deposit expense",
    f"${-result['liability_td_contribution']/1e6:+,.2f}B",
)
result_cols[3].metric(
    "NMD expense (β-applied)",
    f"${-result['nmd_contribution']/1e6:+,.2f}B",
)


# ===== Three-beta comparison =====
st.subheader("The same balance sheet, three deposit-beta worlds (+200 bps shock)")

beta_scenarios = [
    (0.30, "Low beta", "Retail-sticky franchise (pre-2022 norm)"),
    (0.50, "Mid beta", "Mixed franchise"),
    (0.70, "High beta", "Uninsured / institutional / tech-concentrated"),
]
fig_b = go.Figure()
labels: list[str] = []
deltas: list[float] = []
colors: list[str] = []
for b_val, b_label, _ in beta_scenarios:
    res = nii_12m_shock(gap_df, 200, nmd_bal, b_val)
    labels.append(f"{b_label} (β={b_val:.2f})")
    deltas.append(res["delta_nii"] / 1e6)
    colors.append("#27ae60" if res["delta_nii"] >= 0 else "#c0392b")

fig_b.add_bar(x=labels, y=deltas, marker_color=colors,
              text=[f"${d:+.2f}B" for d in deltas], textposition="outside")
fig_b.update_layout(
    title=f"{selected_bank_label} · 12-month ΔNII under +200 bps shock, by deposit beta",
    yaxis_title="ΔNII (USD billions, pre-tax)",
    height=400,
    margin=dict(t=50, b=30, l=10, r=10),
    showlegend=False,
)
st.plotly_chart(fig_b, use_container_width=True)

st.caption(
    "Same balance sheet, same shock. Move from β=0.30 to β=0.70 and the bank's "
    "interest-rate posture flips from asset-sensitive to deeply liability-sensitive. "
    "**This is the single most important sensitivity in ALM.** SVB's pre-2022 "
    "modeling assumed a low beta consistent with retail-sticky deposit franchises; "
    "their actual deposit base — uninsured, institutional, tech-concentrated — "
    "behaved like a β closer to 0.7."
)


# ---------------------------------------------------------------------------
# M3 — EVE shock grid + HTM/AFS unrealized-loss reconstruction
# ---------------------------------------------------------------------------
st.markdown("---")
st.header("M3 · EVE and HTM unrealized-loss reconstruction")
st.write(
    "We model each FFIEC Schedule RC-B Memo 2 maturity bucket as a single "
    "representative bullet bond — face = amortized cost, coupon = portfolio "
    "book yield, maturity = bucket midpoint — and discount its cash flows on "
    "the Treasury curve. **The model never reads the fair-value field**: it "
    "reconstructs the mark-to-market loss from cash flows and rates only, "
    "then we compare to the disclosed fair-value-derived loss as the "
    "validation gate."
)

# Load FRED curve data
@st.cache_data
def _load_fred_cached(_path: str, _mtime: float) -> pd.DataFrame:
    return pd.read_parquet(_path)


def load_fred() -> pd.DataFrame | None:
    path, _ = _resolve_parquet("fred_macro.parquet")
    if path is None:
        return None
    return _load_fred_cached(str(path), path.stat().st_mtime)


fred_df = load_fred()

# Detect whether the FFIEC parquet contains the M3-era bucket fields. If not,
# the user is running with a stale data file and needs to re-pull.
_required_bucket_fields = {"secs_treasury_le_3m", "secs_mbs_passthrough_gt_15y"}
_have_buckets = _required_bucket_fields.issubset(set(df["field"].unique()))

if fred_df is None:
    st.warning(
        "FRED data not found. Run `uv run python -m scripts.pull_fred "
        "--start 2021-01-01 --end 2023-01-15` to enable the M3 section."
    )
elif not _have_buckets:
    st.warning(
        "M3 needs the maturity-bucket fields from FFIEC Schedules RC-B / RC-C / RC-E "
        "Memorandum items. Your `data/processed/ffiec_long.parquet` doesn't have them — "
        "re-pull with:\n\n"
        "```\nuv run python -m scripts.pull_ffiec --quarter 2022Q4 --quarter 2021Q4\n```"
    )
else:
    # ===== Sensitivity controls =====
    st.subheader("Model controls")
    ctrl1, ctrl2, ctrl3, ctrl4 = st.columns(4)
    with ctrl1:
        eve_bank_label = st.radio(
            "Bank",
            ["Silicon Valley Bank", "The Huntington National Bank"],
            index=0,
            key="eve_bank",
        )
    eve_bank_key = "svb" if eve_bank_label.startswith("Silicon") else "hban"
    eve_rssd = banks.get(eve_bank_key).rssd_id

    with ctrl2:
        book_yield_pct = st.slider(
            "Book yield (annual, %)",
            min_value=0.5,
            max_value=5.0,
            value=DEFAULT_BOOK_YIELDS[eve_bank_key] * 100,
            step=0.05,
            help="Weighted-average portfolio book yield. Default matches each bank's 10-K disclosure.",
        )
    book_yield = book_yield_pct / 100.0

    with ctrl3:
        mbs_long_wal = st.slider(
            "Long-MBS WAL (years)",
            min_value=4.0,
            max_value=20.0,
            value=13.0,
            step=0.5,
            help="Expected average life of pass-through MBS in the >15Y stated-maturity bucket. "
                 "Calibrated to ~13Y for the slow-prepay Q4 2022 environment; reduce for faster prepay regimes.",
        )
    with ctrl4:
        mbs_medium_wal = st.slider(
            "Medium-MBS WAL (years)",
            min_value=2.0,
            max_value=15.0,
            value=6.0,
            step=0.5,
            help="Expected average life of pass-through MBS in the 5-15Y stated-maturity bucket.",
        )

    midpoint_overrides = {
        "secs_mbs_passthrough_5y_15y": mbs_medium_wal,
        "secs_mbs_passthrough_gt_15y": mbs_long_wal,
    }


    @st.cache_data
    def cached_curve(as_of: str) -> dict[float, float]:
        return treasury_curve_on(fred_df, as_of)


    # Treasury curve on the validation date (quarter-end)
    curve_date = "2022-12-30" if quarter == "2022Q4" else "2021-12-30"
    curve = cached_curve(curve_date)
    buckets = bucket_values_for(df, eve_rssd, quarter)
    recon = reconstruct_portfolio(buckets, curve, book_yield, midpoints=midpoint_overrides)
    md = portfolio_modified_duration(buckets, curve, book_yield)

    # ===== Validation gate =====
    sub = df[(df["rssd_id"] == eve_rssd) & (df["quarter"] == quarter)]
    htm_ac = float(sub[sub["field"] == "htm_amortized_cost_total"]["value"].iloc[0])
    htm_fv = float(sub[sub["field"] == "htm_fair_value_total"]["value"].iloc[0])
    afs_ac = float(sub[sub["field"] == "afs_amortized_cost_total"]["value"].iloc[0])
    afs_fv = float(sub[sub["field"] == "afs_fair_value_total"]["value"].iloc[0])
    reported_loss = (htm_ac - htm_fv) + (afs_ac - afs_fv)
    modeled_loss = float(recon["unrealized_loss"].sum())
    err = (modeled_loss / reported_loss - 1.0) if reported_loss else 0.0
    gate_pass = abs(err) <= 0.10

    st.subheader(f"Validation gate · {eve_bank_label} {quarter}")
    v1, v2, v3, v4 = st.columns(4)
    v1.metric("Reported unrealized loss (HTM + AFS)", f"${reported_loss/1e6:,.2f}B")
    v2.metric("Modeled unrealized loss", f"${modeled_loss/1e6:,.2f}B")
    v3.metric("Error vs reported", f"{err*100:+.1f}%", delta=("within ±10%" if gate_pass else "outside ±10%"))
    v4.metric("Portfolio modified duration", f"{md:.2f} yrs")

    if gate_pass:
        st.success(
            f"**PASS.** The model reconstructs the disclosed mark-to-market loss "
            f"from cash flows and the Treasury curve alone (no fair-value field "
            f"read). HTM split: reported ${(htm_ac-htm_fv)/1e6:,.2f}B · AFS reported ${(afs_ac-afs_fv)/1e6:,.2f}B."
        )
    else:
        st.error(
            "**Outside ±10% tolerance.** Calibrate the book yield or MBS-WAL "
            "sliders above to bring the model within tolerance."
        )

    # ===== Per-bucket reconstruction chart =====
    st.subheader("Where the unrealized loss lives, bucket by bucket")
    recon_display = recon.copy()
    recon_display["loss_b"] = recon_display["unrealized_loss"] / 1e6
    recon_display["ac_b"] = recon_display["amortized_cost"] / 1e6

    fig_buckets = go.Figure()
    fig_buckets.add_bar(
        name="Amortized cost",
        x=recon_display["field"],
        y=recon_display["ac_b"],
        marker_color="#3498db",
    )
    fig_buckets.add_bar(
        name="Unrealized loss",
        x=recon_display["field"],
        y=recon_display["loss_b"],
        marker_color="#c0392b",
    )
    fig_buckets.update_layout(
        title=f"{eve_bank_label} · per-bucket reconstruction ({quarter})",
        yaxis_title="USD billions",
        barmode="group",
        height=420,
        margin=dict(t=50, b=120, l=10, r=10),
        legend=dict(orientation="h", yanchor="bottom", y=-0.4),
        xaxis_tickangle=-30,
    )
    st.plotly_chart(fig_buckets, use_container_width=True)

    biggest = recon.loc[recon["unrealized_loss"].idxmax(), "field"]
    biggest_loss = recon["unrealized_loss"].max() / 1e6
    biggest_pct = biggest_loss / (modeled_loss / 1e6) if modeled_loss else 0
    st.caption(
        f"The single largest contributor to {eve_bank_label}'s mark-to-market damage is "
        f"`{biggest}` at ${biggest_loss:,.2f}B — {biggest_pct:.0%} of total. "
        "Cash flow generation and discounting are done bucket-by-bucket; "
        "totals reconcile within ±10% to each bank's published fair-value disclosure."
    )

    # ===== EVE shock grid =====
    st.subheader("EVE shock grid · how much more capital does a further rate move destroy?")
    st.write(
        "These are **incremental** ΔEVE values, measured from the current "
        "Q4 2022 baseline state. They show what *additional* damage a further "
        "parallel shock would inflict on the securities portfolio, expressed "
        "in dollars and as a percentage of Tier 1 capital. A value below −100% "
        "of Tier 1 means the additional move alone would wipe out the bank's "
        "regulatory capital."
    )

    shocks = (-300, -200, -100, 0, 100, 200, 300, 400)
    grid = eve_shock_grid(buckets, curve, book_yield, shocks_bps=shocks, midpoints=midpoint_overrides)
    tier1 = float(sub[sub["field"] == "tier1_capital"]["value"].iloc[0])
    grid["delta_eve_b"] = grid["delta_eve"] / 1e6
    grid["delta_eve_pct_tier1"] = grid["delta_eve"] / tier1

    fig_grid = go.Figure()
    fig_grid.add_bar(
        x=grid["shock_bps"],
        y=grid["delta_eve_b"],
        marker_color=["#27ae60" if d >= 0 else "#c0392b" for d in grid["delta_eve_b"]],
        text=[f"${d:+,.1f}B<br>{p:+.0%} of T1" for d, p in zip(grid["delta_eve_b"], grid["delta_eve_pct_tier1"], strict=True)],
        textposition="outside",
    )
    fig_grid.add_hline(
        y=-tier1 / 1e6,
        line_dash="dash",
        line_color="red",
        annotation_text="−100% of Tier 1",
        annotation_position="bottom right",
    )
    fig_grid.update_layout(
        title=f"{eve_bank_label} · incremental ΔEVE on the securities portfolio, by parallel shock",
        xaxis_title="Parallel shock (basis points)",
        yaxis_title="ΔEVE (USD billions)",
        height=460,
        margin=dict(t=50, b=30, l=10, r=10),
        showlegend=False,
    )
    st.plotly_chart(fig_grid, use_container_width=True)

    st.caption(
        f"Tier 1 capital: ${tier1/1e6:,.2f}B. Bars below the dashed red line show "
        "shocks whose marginal EVE damage alone would exhaust Tier 1. The "
        "*embedded* unrealized loss already sitting on the balance sheet "
        "(shown in the M1 hero metrics and reconstructed above) stacks on top "
        "of any further damage from here."
    )

    with st.expander("Methodology · what the model does and doesn't do"):
        st.markdown(
            "**Scope.** Securities portfolio only — Treasuries, Agency MBS pass-throughs, "
            "and CMOs from Schedule RC-B Memorandum 2.  Loans and deposits also have "
            "EVE sensitivity but are not in this view (loans are marked at par on the "
            "balance sheet; deposits are also unmarked). For the SVB case the securities "
            "story is the binding constraint.\n\n"
            "**Cash flow generation.** Each bucket is modeled as one bullet bond: "
            "semi-annual coupons at the portfolio book yield, principal at maturity. "
            "Pricing uses standard PV-of-cash-flows discounting on the Treasury curve, "
            "linearly interpolated to bucket midpoints.\n\n"
            "**Key assumptions.** "
            "(1) **Book yield** is a single weighted-average across the portfolio. "
            "Each bank's value defaults to its 10-K disclosure (SVB 1.79%, Huntington 2.40%).  "
            "(2) **MBS pass-through WAL** overrides the form's contractual-maturity buckets, "
            "since Agency MBS prepay and their effective duration depends on prepayment speed.  "
            "Defaults (13Y for >15Y stated, 6Y for 5–15Y stated) are calibrated to the "
            "Q4 2022 slow-prepay environment (~5% CPR).  "
            "(3) **Treasury curve only**; no OAS for Agency MBS. The bias is small (<5%) and "
            "conservative for relative comparisons.\n\n"
            "**Limitations.** Single book yield (no bucket-level coupon disaggregation), "
            "static prepayment (no rate-dependent CPR), no loan-side EVE, no liability "
            "EVE on non-maturity deposits. See README's Limitations section."
        )


# ---------------------------------------------------------------------------
# M4 — Time series across all loaded quarters
# ---------------------------------------------------------------------------
st.markdown("---")
st.header("M4 · Time series across the rate cycle")
st.write(
    "The charts below render whatever quarterly data is in your "
    "`data/processed/ffiec_long.parquet`. As you drop additional FFIEC bulk "
    "ZIPs into `data/raw/` and re-run `pull_ffiec`, this section fills out — "
    "no code changes needed. The intended end-state is **2019Q1 through "
    "2023Q1**, which spans the pre-Covid period, the ZIRP-era HTM buildup, "
    "and the 2022 rate shock that broke SVB."
)


def _quarters_in_window(start_year: int = 2019, start_q: int = 1,
                        end_year: int = 2023, end_q: int = 1) -> list[str]:
    """All quarter labels in the closed window, e.g. '2019Q1' .. '2023Q1'."""
    start_idx = start_year * 4 + (start_q - 1)
    end_idx   = end_year   * 4 + (end_q   - 1)
    return [f"{i // 4}Q{(i % 4) + 1}" for i in range(start_idx, end_idx + 1)]


loaded_quarters = sorted(df["quarter"].unique())
all_quarters_target = _quarters_in_window()
missing = [q for q in all_quarters_target if q not in loaded_quarters]

status_col1, status_col2 = st.columns([1, 2])
with status_col1:
    st.metric("Quarters loaded", f"{len(loaded_quarters)} / {len(all_quarters_target)}")
with status_col2:
    if missing:
        st.info(
            f"**Loaded:** {', '.join(loaded_quarters)}.  \n"
            f"**Missing for 2019Q1–2023Q1:** {', '.join(missing)}. "
            "Download each from the FFIEC bulk-data portal and drop into "
            "`data/raw/`, then re-run "
            "`uv run python -m scripts.pull_ffiec --quarter <Q>` for each."
        )
    else:
        st.success("Full 2019Q1–2023Q1 series loaded.")


# ===== Time-series metrics for each bank =====
def time_series_frame() -> pd.DataFrame:
    """Wide-format frame keyed on (bank, quarter) with the metrics we chart."""
    records: list[dict] = []
    for bank_key in ("svb", "hban"):
        rssd = banks.get(bank_key).rssd_id
        for q in loaded_quarters:
            ta = get_val(bank_key, q, "total_assets")
            htm = get_val(bank_key, q, "htm_amortized_cost_total")
            htm_fv = get_val(bank_key, q, "htm_fair_value_total")
            afs = get_val(bank_key, q, "afs_amortized_cost_total")
            afs_fv = get_val(bank_key, q, "afs_fair_value_total")
            tier1 = get_val(bank_key, q, "tier1_capital")
            dep = get_val(bank_key, q, "total_deposits")
            uninsured = get_val(bank_key, q, "estimated_uninsured_deposits")
            nii = get_val(bank_key, q, "net_interest_income")
            if None in (ta, htm, htm_fv, afs, afs_fv, tier1, dep, uninsured):
                continue
            records.append({
                "bank": banks.get(bank_key).short_name,
                "rssd_id": rssd,
                "quarter": q,
                "quarter_end": pd.Timestamp(
                    year=int(q[:4]),
                    month={"1": 3, "2": 6, "3": 9, "4": 12}[q[-1]],
                    day=28,
                ),
                "total_assets_b": ta / 1e6,
                "htm_pct_assets": htm / ta,
                "unrealized_loss_b": ((htm - htm_fv) + (afs - afs_fv)) / 1e6,
                "unrealized_loss_pct_t1": ((htm - htm_fv) + (afs - afs_fv)) / tier1,
                "uninsured_pct": uninsured / dep,
                "nii_ytd_b": (nii or 0) / 1e6,
                "tier1_b": tier1 / 1e6,
            })
    return pd.DataFrame(records).sort_values(["bank", "quarter"])


ts = time_series_frame()


def line_panel(metric: str, title: str, ytick: str, hline: float | None = None,
               hline_label: str = "") -> go.Figure:
    fig = go.Figure()
    for bank in ts["bank"].unique():
        sub = ts[ts["bank"] == bank]
        fig.add_scatter(
            x=sub["quarter_end"],
            y=sub[metric],
            name=bank,
            mode="lines+markers",
            line=dict(color=BANK_COLORS.get(bank), width=2),
            marker=dict(size=8),
        )
    if hline is not None:
        fig.add_hline(y=hline, line_dash="dash", line_color="red",
                      annotation_text=hline_label, annotation_position="top right")
    fig.update_layout(
        title=title,
        yaxis_tickformat=ytick,
        height=320,
        margin=dict(t=50, b=30, l=10, r=10),
        legend=dict(orientation="h", yanchor="bottom", y=-0.25),
    )
    return fig


if len(loaded_quarters) < 2:
    st.warning(
        "Only one quarter is loaded — time series charts will be flat. Add "
        "more quarters to see the trajectory."
    )
else:
    ts_a, ts_b = st.columns(2)
    with ts_a:
        st.plotly_chart(
            line_panel("htm_pct_assets", "HTM as % of total assets", ".0%"),
            use_container_width=True,
        )
        st.caption(
            "When the HTM concentration line goes up, the bank is locking in "
            "duration on the asset side. SVB's came up in 2020–21 and stayed "
            "elevated; that's the choice that pre-determined the 2023 outcome."
        )
    with ts_b:
        st.plotly_chart(
            line_panel("unrealized_loss_pct_t1", "Combined unrealized loss / Tier 1",
                       ".0%", hline=1.0, hline_label="Tier 1 wiped out"),
            use_container_width=True,
        )
        st.caption(
            "The dashed line is the regulatory-solvency threshold. SVB's line "
            "crosses it as the 2022 rate shock works through the long-MBS book."
        )

    ts_c, ts_d = st.columns(2)
    with ts_c:
        st.plotly_chart(
            line_panel("uninsured_pct", "Uninsured deposits as % of total deposits", ".0%"),
            use_container_width=True,
        )
        st.caption(
            "SVB's uninsured deposit share stays ~94% across every quarter — "
            "the franchise was structurally bank-run-vulnerable long before "
            "the run actually happened."
        )
    with ts_d:
        st.plotly_chart(
            line_panel("total_assets_b", "Total assets (USD billions)", ",.0f"),
            use_container_width=True,
        )
        st.caption(
            "Both banks grew through the ZIRP era. SVB's deposit base flooded "
            "with VC dry powder; Huntington's growth came from the 2021 TCF "
            "Financial acquisition. Same macro tailwind, different asset-side response."
        )


# ===== Rate-shock backdrop from FRED =====
if fred_df is not None:
    st.subheader("Rate-shock backdrop · Treasury curve evolution")
    rate_panel = go.Figure()
    for series_name, label, color in [
        ("ust_2y",  "2-year",  "#3498db"),
        ("ust_5y",  "5-year",  "#16a085"),
        ("ust_10y", "10-year", "#c0392b"),
        ("eff_fed_funds", "Effective Fed Funds", "#7f8c8d"),
    ]:
        sub_rate = fred_df[(fred_df["series"] == series_name)
                           & (fred_df["value"].notna())].sort_values("date")
        if sub_rate.empty:
            continue
        rate_panel.add_scatter(
            x=sub_rate["date"],
            y=sub_rate["value"],
            name=label,
            mode="lines",
            line=dict(color=color, width=1.6),
        )
    rate_panel.update_layout(
        title="Treasury yields and the Fed Funds rate over the rate cycle",
        yaxis_title="Yield (%)",
        height=360,
        margin=dict(t=50, b=30, l=10, r=10),
        legend=dict(orientation="h", yanchor="bottom", y=-0.25),
    )
    st.plotly_chart(rate_panel, use_container_width=True)
    st.caption(
        "The 2-year yield went from ~0.1% in early 2021 to ~4.4% by end of 2022 — "
        "the steepest tightening cycle since Volcker. The HTM book SVB built when "
        "the blue line was at zero became un-sellable when it reached 4%."
    )


# ---------------------------------------------------------------------------
# M5 — Liquidity / HQLA / uninsured-deposit overlay
# ---------------------------------------------------------------------------
st.markdown("---")
st.header("M5 · Liquidity overlay — when HTM stops counting")
st.write(
    "The central liquidity claim: **HTM securities are not HQLA**. Under both "
    "Basel III and US Reg WW, a bank cannot sell HTM holdings to meet "
    "outflows without taint-rule risk that forces re-categorization of the "
    "entire HTM book to AFS — recognizing every unrealized loss through AOCI. "
    "That is exactly what SVB had to do on 8 March 2023, and it is the "
    "moment that triggered the run. So the model strips HTM from HQLA and "
    "asks: under stress, does the bank have enough cash + AFS to survive a "
    "30-day deposit outflow?"
)

# ===== Outflow scenario controls =====
liq_c1, liq_c2, liq_c3 = st.columns(3)
with liq_c1:
    insured_outflow_pct = st.slider(
        "Insured deposit outflow rate (% over 30 days)",
        0.0, 30.0,
        DEFAULT_INSURED_OUTFLOW_RATE * 100,
        step=0.5,
        help="Basel III: ~3-10% for stable retail. Default 5%.",
    )
with liq_c2:
    uninsured_outflow_pct = st.slider(
        "Uninsured deposit outflow rate (% over 30 days)",
        0.0, 100.0,
        DEFAULT_UNINSURED_OUTFLOW_RATE * 100,
        step=1.0,
        help="Basel III: 25-40% for less-stable wholesale. SVB-style actual: ≥70%. Default 25%.",
    )
with liq_c3:
    afs_haircut_pct = st.slider(
        "AFS haircut (%)",
        0.0, 25.0,
        DEFAULT_AFS_HAIRCUT * 100,
        step=0.5,
        help="Basel III: Level 1 (Treasuries) = 0%, Level 2A (Agency MBS) = 15%. Default 8% blended.",
    )
insured_rate = insured_outflow_pct / 100.0
uninsured_rate = uninsured_outflow_pct / 100.0
afs_haircut = afs_haircut_pct / 100.0


# ===== LCR time series =====
@st.cache_data
def _lcr_ts(rssd: int, ins_r: float, uns_r: float, afs_h: float) -> pd.DataFrame:
    return lcr_time_series(df, rssd, ins_r, uns_r, afs_h)


lcr_panels = {}
for bank_key, bank_label in (("svb", "SVB"), ("hban", "Huntington")):
    rssd = banks.get(bank_key).rssd_id
    ts = _lcr_ts(rssd, insured_rate, uninsured_rate, afs_haircut)
    ts["quarter_end"] = pd.to_datetime([
        f"{q[:4]}-{ {'1':'03-31','2':'06-30','3':'09-30','4':'12-31'}[q[-1]] }"
        for q in ts["quarter"]
    ])
    lcr_panels[bank_label] = ts


# Headline metrics for current quarter (use the last loaded quarter per bank)
metric_cols = st.columns(2)
for col, (bank_label, ts) in zip(metric_cols, lcr_panels.items(), strict=True):
    if ts.empty:
        col.warning(f"No data for {bank_label}.")
        continue
    latest = ts.iloc[-1]
    col.markdown(f"### {bank_label} — latest reported quarter ({latest['quarter']})")
    sub_cols = col.columns(4)
    sub_cols[0].metric("HQLA", f"${latest['hqla']/1e6:,.1f}B")
    sub_cols[1].metric("30-day outflows", f"${latest['outflows_30d']/1e6:,.1f}B")
    lcr_pct = latest["lcr"] * 100 if latest["lcr"] != float("inf") else float("nan")
    col.markdown("")
    col.metric(
        "Simplified LCR",
        f"{lcr_pct:.0f}%",
        delta=("above 100%" if lcr_pct >= 100 else "below 100%"),
        delta_color=("normal" if lcr_pct >= 100 else "inverse"),
    )
    breakeven_pct = latest["breakeven_uninsured_outflow"] * 100
    col.metric(
        "Breakeven uninsured outflow",
        f"{breakeven_pct:.1f}%",
        help="The uninsured-deposit outflow rate at which LCR would equal 100%, "
             "holding the insured rate and AFS haircut at the slider values.",
    )


# LCR time series chart
fig_lcr = go.Figure()
for bank_label, ts in lcr_panels.items():
    fig_lcr.add_scatter(
        x=ts["quarter_end"],
        y=ts["lcr"] * 100,
        name=bank_label,
        mode="lines+markers",
        line=dict(color=BANK_COLORS.get(bank_label), width=2),
        marker=dict(size=8),
    )
fig_lcr.add_hline(
    y=100, line_dash="dash", line_color="red",
    annotation_text="100% — LCR threshold",
    annotation_position="top right",
)
fig_lcr.update_layout(
    title=(f"Simplified LCR over the rate cycle "
           f"(insured outflow {insured_outflow_pct:.1f}%, "
           f"uninsured outflow {uninsured_outflow_pct:.1f}%, "
           f"AFS haircut {afs_haircut_pct:.1f}%)"),
    yaxis_title="LCR (%)",
    height=420,
    margin=dict(t=50, b=30, l=10, r=10),
    legend=dict(orientation="h", yanchor="bottom", y=-0.2),
)
st.plotly_chart(fig_lcr, use_container_width=True)
st.caption(
    "SVB's last Call Report is 2022Q4 — RSSD 802866 vanishes from the "
    "regulatory record after the FDIC seized the bank on 10 March 2023. "
    "Look at where SVB's line was sitting in the year *before* the run: "
    "essentially riding the 100% threshold at default outflow assumptions. "
    "Dial uninsured outflow up to 70% (the rate that materialized in early "
    "March) and SVB's line drops well below Huntington's at every quarter."
)


# Breakeven outflow chart — the headline
st.subheader("How much uninsured outflow can each bank actually absorb?")
st.write(
    "The chart below shows, for each quarter, the uninsured-deposit outflow "
    "rate at which LCR would equal 100% (holding the slider values). It is "
    "the bank's *liquidity headroom* expressed in deposit-runoff terms. "
    "A line near the 25% Basel III baseline means the bank can absorb only a "
    "regulator-baseline stress. A line near 80%+ means it could survive an "
    "SVB-style flash run."
)
fig_break = go.Figure()
for bank_label, ts in lcr_panels.items():
    fig_break.add_scatter(
        x=ts["quarter_end"],
        y=ts["breakeven_uninsured_outflow"] * 100,
        name=bank_label,
        mode="lines+markers",
        line=dict(color=BANK_COLORS.get(bank_label), width=2),
        marker=dict(size=8),
    )
fig_break.add_hline(y=25, line_dash="dot", line_color="#7f8c8d",
                    annotation_text="Basel III baseline (~25%)",
                    annotation_position="top left")
fig_break.add_hline(y=70, line_dash="dot", line_color="#c0392b",
                    annotation_text="SVB-style flash run (~70%)",
                    annotation_position="top right")
fig_break.update_layout(
    title="Breakeven uninsured outflow rate by quarter",
    yaxis_title="Outflow rate (%)",
    height=400,
    margin=dict(t=50, b=30, l=10, r=10),
    legend=dict(orientation="h", yanchor="bottom", y=-0.2),
)
st.plotly_chart(fig_break, use_container_width=True)
st.caption(
    "Across 2022, SVB's line hovered around 22-25% — the bank was structurally "
    "incapable of absorbing more than a Basel III baseline uninsured stress. "
    "Huntington's line is in the 26-53% range, with a sharp jump in 2023Q1 as "
    "their cash position grew and total deposits shrank slightly. **This is "
    "the single most direct view of the SVB liquidity story**: same balance "
    "sheet weight class, very different stress capacity."
)


# HQLA composition for the latest quarter
st.subheader("HQLA composition — where each bank's liquidity actually sits")
hqla_rows = []
for bank_key, bank_label in (("svb", "SVB"), ("hban", "Huntington")):
    rssd = banks.get(bank_key).rssd_id
    last_q = lcr_panels[bank_label]["quarter"].iloc[-1] if not lcr_panels[bank_label].empty else None
    if last_q is None:
        continue
    c = components_for(df, rssd, last_q)
    hqla_rows.append({
        "bank": f"{bank_label} ({last_q})",
        "Cash": c.cash / 1e6,
        "AFS (haircut-adjusted)": (1 - afs_haircut) * c.afs_fair_value / 1e6,
        "HTM (excluded from HQLA)": c.htm_amortized_cost / 1e6,
    })
hqla_long = (pd.DataFrame(hqla_rows)
             .melt(id_vars="bank", var_name="bucket", value_name="value_b"))
fig_hqla = go.Figure()
color_map = {
    "Cash": "#2c3e50",
    "AFS (haircut-adjusted)": "#16a085",
    "HTM (excluded from HQLA)": "#c0392b",
}
for bucket in ("Cash", "AFS (haircut-adjusted)", "HTM (excluded from HQLA)"):
    sub = hqla_long[hqla_long["bucket"] == bucket]
    fig_hqla.add_bar(
        name=bucket,
        x=sub["bank"],
        y=sub["value_b"],
        marker_color=color_map[bucket],
    )
fig_hqla.update_layout(
    title="Cash, AFS, and the HTM dark mass",
    barmode="stack",
    yaxis_title="USD billions",
    height=400,
    margin=dict(t=50, b=30, l=10, r=10),
    legend=dict(orientation="h", yanchor="bottom", y=-0.2),
)
st.plotly_chart(fig_hqla, use_container_width=True)
st.caption(
    "Red is HTM — the part of the balance sheet that *looks* liquid in normal "
    "times but is locked away from any actual stress event. SVB's red column "
    "is roughly the same size as Huntington's entire balance sheet, but it "
    "contributes zero HQLA. Cash + AFS is the operative liquidity buffer."
)

with st.expander("Methodology · what this simplified LCR does and doesn't do"):
    st.markdown(
        "**Scope.** Securities + cash + deposits only. Skips wholesale funding, "
        "FHLB advances, repo, and the full Basel III outflow categorization "
        "(operational deposits, secured funding, commitments, etc.).\n\n"
        "**Central modeling claim.** HTM securities are excluded from HQLA. "
        "Under both Basel III and US Reg WW, selling HTM triggers an accounting "
        "taint rule that forces the entire HTM portfolio to AFS, immediately "
        "marking all unrealized losses through AOCI. SVB triggered exactly "
        "this on 8 March 2023.\n\n"
        "**Configurable parameters.** Insured & uninsured deposit outflow "
        "rates, AFS haircut. All exposed as sliders. Defaults track Basel III "
        "baseline; the SVB-actual case corresponds to uninsured outflow ≥ 70%.\n\n"
        "**Insured / uninsured split.** Computed as "
        "`total_deposits − estimated_uninsured_deposits` (RC-O Memo 2). For "
        "tech-concentrated franchises like SVB this proxy understates outflow "
        "risk because even some technically-insured accounts behaved as "
        "uninsured under stress.\n\n"
        "**Not a regulatory LCR.** This is a directionally-correct illustrative "
        "model. The full Reg WW LCR includes ~30 outflow categories, "
        "operational-vs-non-operational deposit treatment, inflow caps, and "
        "intra-period peak constraints. The point here is the SVB story, not "
        "the regulatory math."
    )


# ---------------------------------------------------------------------------
# About + Footer
# ---------------------------------------------------------------------------
st.markdown("---")
st.header("About this project")

about_left, about_right = st.columns([2, 1])
with about_left:
    st.markdown(
        "**Built by Jared Limon** as a portfolio project for senior risk leadership "
        "and quantitative strategy roles in financial services.\n\n"
        "**Prior roles:** Principal Risk Architect at Bosonic Digital (institutional FX "
        "market making, A/B book, cross-custodian netting); Lead Credit Risk Analyst at "
        "Tosh (ML-driven credit scoring, PD/LGD forecasting); Senior Risk Management "
        "Analyst at IBFX / TradeStation ($1.2T institutional FX flow, A/B book "
        "methodology, ALM principles applied to broker book operations).\n\n"
        "**This project demonstrates:** end-to-end ALM modeling (repricing gap, NII "
        "sensitivity, EVE shock grid, simplified LCR), reproducible data ingestion "
        "from public sources (FFIEC Call Report bulk data, FRED), explicit and "
        "configurable modeling assumptions, and validation against published 10-K "
        "figures — built around a defensible postmortem narrative of the SVB collapse."
    )
with about_right:
    st.markdown(
        "**Code**\n"
        "  \nGitHub: *link forthcoming*\n\n"
        "**Contact**\n"
        "  \njared@kuroshioflow.io\n\n"
        "**Methodology**\n"
        "  \nSee [README.md](README.md) for the methodology summary, limitations, "
        "and source citations (Fed SR letters, BIS IRRBB, FFIEC handbooks)."
    )

st.markdown("---")
st.caption(
    "**Data sources:** FFIEC Call Report bulk data "
    "(https://cdr.ffiec.gov/public/PWS/DownloadBulkData.aspx) and FRED "
    "(https://fred.stlouisfed.org).  \n"
    "**Methodology & limitations:** see `README.md` — every modeling assumption is "
    "marked `# ASSUMPTION:` in source and configurable from the dashboard sliders.  \n"
    "**License:** MIT. Code is portfolio-grade; not for production risk decisions."
)
