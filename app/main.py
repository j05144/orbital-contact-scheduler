"""Satellite ground-station contact scheduler — Streamlit dashboard."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_DIR = Path(__file__).parent.parent / "data"
SCHEDULE_PATH = DATA_DIR / "schedule.csv"
DROPPED_PATH = DATA_DIR / "dropped.csv"
TRIAGE_PATH = DATA_DIR / "triage.json"

# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

@st.cache_data
def load_schedule() -> pd.DataFrame:
    df = pd.read_csv(SCHEDULE_PATH, parse_dates=["start_utc", "end_utc"])
    return df


@st.cache_data
def load_dropped() -> pd.DataFrame:
    df = pd.read_csv(DROPPED_PATH, parse_dates=["earliest_start_utc", "alt_start_utc"])
    return df


@st.cache_data
def load_triage() -> dict:
    with open(TRIAGE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def check_data_files() -> None:
    missing = [p for p in (SCHEDULE_PATH, DROPPED_PATH, TRIAGE_PATH) if not p.exists()]
    if missing:
        for p in missing:
            st.error(f"Missing data file: `{p}`")
        st.stop()


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Orbital Contact Scheduler",
    page_icon="🛰️",
    layout="wide",
)

check_data_files()

schedule = load_schedule()
dropped = load_dropped()
triage = load_triage()

# ---------------------------------------------------------------------------
# Derived metrics
# ---------------------------------------------------------------------------
n_scheduled = len(schedule)
n_dropped = len(dropped)
n_total = n_scheduled + n_dropped

p1_total = (schedule["priority"] == 1).sum() + (dropped["priority"] == 1).sum()
p1_kept = (schedule["priority"] == 1).sum()

# ---------------------------------------------------------------------------
# Section 1 — Metrics row
# ---------------------------------------------------------------------------
st.title("🛰️ Orbital Contact Scheduler")
st.caption(f"AI triage generated at {triage['generated_at']} · model: {triage['model']}")
st.divider()

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Passes Considered", f"{n_total:,}")
col2.metric("Scheduled", f"{n_scheduled:,}")
col3.metric("Dropped", f"{n_dropped:,}")
col4.metric("Priority-1 Protected", f"{p1_kept}/{p1_total}")

st.divider()

# ---------------------------------------------------------------------------
# Section 2 — AI Triage panel
# ---------------------------------------------------------------------------
ACTION_STYLES = {
    "ACCEPT": ("background:#166534;color:#dcfce7;", "✔ ACCEPT"),
    "OVERRIDE": ("background:#92400e;color:#fef3c7;", "⚡ OVERRIDE"),
    "ESCALATE": ("background:#991b1b;color:#fee2e2;", "🚨 ESCALATE"),
}

st.subheader("🤖 AI Triage")

st.info(triage["summary"], icon="ℹ️")

items = triage["items"]
left_items = items[: len(items) // 2 + len(items) % 2]
right_items = items[len(items) // 2 + len(items) % 2 :]

col_left, col_right = st.columns(2)

def render_triage_cards(col: st.delta_generator.DeltaGenerator, card_items: list) -> None:
    with col:
        for item in card_items:
            action = item.get("action", "ACCEPT")
            style, label = ACTION_STYLES.get(action, ACTION_STYLES["ACCEPT"])
            badge = (
                f'<span style="display:inline-block;padding:2px 10px;border-radius:4px;'
                f'font-size:0.75rem;font-weight:700;letter-spacing:0.04em;{style}">'
                f"{label}</span>"
            )
            with st.container(border=True):
                st.markdown(badge, unsafe_allow_html=True)
                header_col, meta_col = st.columns([3, 1])
                with header_col:
                    st.markdown(
                        f"**{item['satellite']}** &nbsp;<span style='color:#57606a;font-size:0.85rem'>"
                        f"{item['pass_id']}</span>",
                        unsafe_allow_html=True,
                    )
                with meta_col:
                    st.markdown(
                        f"<div style='text-align:right;color:#57606a;font-size:0.85rem'>"
                        f"P{item['priority']}</div>",
                        unsafe_allow_html=True,
                    )
                st.markdown(f"_{item['explanation']}_")
                st.markdown(
                    f"<span style='color:#57606a;font-size:0.85rem'>→ {item['reason']}</span>",
                    unsafe_allow_html=True,
                )

render_triage_cards(col_left, left_items)
render_triage_cards(col_right, right_items)

st.divider()

# ---------------------------------------------------------------------------
# Section 3 — Gantt chart
# ---------------------------------------------------------------------------
st.subheader("📅 Contact Schedule — Gantt View")

station_order = sorted(schedule["station"].unique().tolist())

STATION_COLORS = {
    "Svalbard": "#3b82f6",
    "Kiruna":   "#7c5cd8",
    "Hawaii":   "#0ea5e9",
}

gantt_df = schedule.copy()
gantt_df["start_utc"] = pd.to_datetime(gantt_df["start_utc"], utc=True)
gantt_df["end_utc"] = pd.to_datetime(gantt_df["end_utc"], utc=True)
gantt_df["hover_label"] = (
    gantt_df["satellite"]
    + "<br>Duration: "
    + gantt_df["duration_min"].map(lambda x: f"{x:.1f} min")
    + "<br>Max el: "
    + gantt_df["max_elevation_deg"].map(lambda x: f"{x:.1f}°")
    + "<br>Priority: "
    + gantt_df["priority"].astype(str)
)

fig = px.timeline(
    gantt_df,
    x_start="start_utc",
    x_end="end_utc",
    y="station",
    color="station",
    color_discrete_map=STATION_COLORS,
    custom_data=["hover_label", "pass_id"],
    category_orders={"station": station_order},
    labels={"station": "Ground Station"},
)

fig.update_traces(
    hovertemplate="<b>%{customdata[1]}</b><br>%{customdata[0]}<extra></extra>"
)

fig.update_layout(
    height=300,
    margin=dict(l=10, r=10, t=10, b=10),
    legend_title_text="Station",
    xaxis_title="UTC",
    yaxis_title="",
    plot_bgcolor="#ffffff",
    paper_bgcolor="#ffffff",
    font=dict(family="-apple-system, Segoe UI, system-ui, sans-serif", size=13),
)
fig.update_xaxes(showgrid=True, gridcolor="#e5e7eb")
fig.update_yaxes(showgrid=False)

st.plotly_chart(fig, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# Section 4 — Dropped contacts table
# ---------------------------------------------------------------------------
st.subheader("🗑️ Dropped Contacts")

# Filters
filter_col1, filter_col2, filter_col3 = st.columns(3)

with filter_col1:
    sat_options = sorted(dropped["satellite"].unique().tolist())
    sel_satellites = st.multiselect(
        "Satellite", options=sat_options, default=[], placeholder="All satellites"
    )

with filter_col2:
    # candidate_stations can be comma-separated; collect all unique station names
    all_stations: set[str] = set()
    for val in dropped["candidate_stations"].dropna():
        for s in val.split(","):
            all_stations.add(s.strip())
    station_options = sorted(all_stations)
    sel_stations = st.multiselect(
        "Candidate Station", options=station_options, default=[], placeholder="All stations"
    )

with filter_col3:
    priority_options = sorted(dropped["priority"].unique().tolist())
    sel_priorities = st.multiselect(
        "Priority", options=priority_options, default=[], placeholder="All priorities"
    )

filtered = dropped.copy()
if sel_satellites:
    filtered = filtered[filtered["satellite"].isin(sel_satellites)]
if sel_stations:
    filtered = filtered[
        filtered["candidate_stations"].apply(
            lambda v: any(s in [x.strip() for x in str(v).split(",")] for s in sel_stations)
        )
    ]
if sel_priorities:
    filtered = filtered[filtered["priority"].isin(sel_priorities)]

st.caption(f"Showing {len(filtered):,} of {len(dropped):,} dropped contacts")

display_cols = [
    "pass_id",
    "satellite",
    "priority",
    "earliest_start_utc",
    "candidate_stations",
    "blocked_by",
    "has_alternative",
    "alt_pass_id",
    "alt_station",
    "alt_start_utc",
    "delay_min",
]

st.dataframe(
    filtered[display_cols].reset_index(drop=True),
    use_container_width=True,
    hide_index=True,
    column_config={
        "pass_id": st.column_config.TextColumn("Pass ID"),
        "satellite": st.column_config.TextColumn("Satellite"),
        "priority": st.column_config.NumberColumn("Priority", format="%d"),
        "earliest_start_utc": st.column_config.DatetimeColumn("Earliest Start (UTC)", format="YYYY-MM-DD HH:mm"),
        "candidate_stations": st.column_config.TextColumn("Candidate Stations"),
        "blocked_by": st.column_config.TextColumn("Blocked By"),
        "has_alternative": st.column_config.CheckboxColumn("Has Alt?"),
        "alt_pass_id": st.column_config.TextColumn("Alt Pass ID"),
        "alt_station": st.column_config.TextColumn("Alt Station"),
        "alt_start_utc": st.column_config.DatetimeColumn("Alt Start (UTC)", format="YYYY-MM-DD HH:mm"),
        "delay_min": st.column_config.NumberColumn("Delay (min)", format="%.1f"),
    },
)
