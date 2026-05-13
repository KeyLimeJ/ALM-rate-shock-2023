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

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from alm.config import PATHS
from alm.data import banks
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
def load_data() -> pd.DataFrame | None:
    path = PATHS.processed / "ffiec_long.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    rssd_to_bank = {b.rssd_id: b for b in banks.BANKS.values()}
    df["bank_short"] = df["rssd_id"].map(lambda r: rssd_to_bank[r].short_name)
    df["bank_role"] = df["rssd_id"].map(lambda r: rssd_to_bank[r].role)
    return df


df = load_data()
if df is None:
    st.error(
        "**No data found.** Expected `data/processed/ffiec_long.parquet`.\n\n"
        "Drop the FFIEC bulk Call Report ZIP for the period you want into "
        "`data/raw/`, then run:\n\n"
        "```\nuv run python -m scripts.pull_ffiec --quarter 2022Q4\n```"
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
    "**Milestone status**\n\n"
    "- [x] **M1** — data ingestion\n"
    "- [x] **M2** — repricing gap + NII shock\n"
    "- [ ] M3 — EVE + HTM unrealized-loss reconstruction\n"
    "- [ ] M4 — full 2019–2023 time series\n"
    "- [ ] M5 — liquidity / HQLA / uninsured overlay\n"
    "- [ ] M6 — narrative polish + deploy\n"
)
st.sidebar.caption(
    "Numbers reconcile to each bank's 10-K within rounding. Source: FFIEC "
    "Call Report bulk data."
)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
st.title("Casualty vs. Survivor")
st.subheader("SVB and Huntington National Bank through the 2022–2023 rate shock")
st.write(
    f"**Reporting period: {quarter}.** Numbers below are pulled directly from "
    "each bank's FFIEC Call Report (Schedules RC, RC-B, RC-E, RC-O, RC-R, RI) "
    "and reconcile to the corresponding 10-K within rounding. This is the M1 "
    "cut — the repricing-gap, NII, EVE, and liquidity overlays will appear "
    "here as later milestones land."
)

st.markdown("---")

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
# Footer
# ---------------------------------------------------------------------------
st.markdown("---")
st.caption(
    "**Data:** FFIEC Call Report bulk data — "
    "https://cdr.ffiec.gov/public/PWS/DownloadBulkData.aspx. "
    "**Source code & methodology:** see `README.md` in the repo. "
    "**Limitations:** see the README's Limitations section — every modeling "
    "assumption is explicit, configurable, and named."
)
