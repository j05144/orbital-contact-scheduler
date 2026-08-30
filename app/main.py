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
    layout="wide",
)

# ---------------------------------------------------------------------------
# Global styles
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Public+Sans:wght@300;400;600&display=swap');

    /* Streamlit chrome suppression */
    #MainMenu { visibility: hidden; }
    footer    { visibility: hidden; }
    header    { visibility: hidden; }
    [data-testid="stToolbar"]      { display: none !important; }
    [data-testid="stDeployButton"] { display: none !important; }
    header[data-testid="stHeader"] { height: 0 !important; }

    /* Page framing */
    .block-container {
        padding-top: 1.25rem !important;
        padding-left: 2.5rem !important;
        padding-right: 2.5rem !important;
    }

    /* Base typography */
    html, body, [class*="css"] {
        font-family: 'Public Sans', sans-serif;
        font-weight: 400;
        letter-spacing: 0.16px;
        color: #17191a;
    }

    /* Monospace identifiers */
    .mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }

    /* Section headings */
    .section-heading {
        font-family: 'Public Sans', sans-serif;
        font-size: 13px;
        font-weight: 600;
        color: #54585a;
        margin: 0 0 12px 0;
        padding-bottom: 6px;
        border-bottom: 1px solid #dfe1e1;
    }

    /* Triage list container */
    .triage-list { border: 1px solid #dfe1e1; padding: 0 16px; margin-bottom: 24px; background: #ffffff; }

    /* Triage rows */
    .triage-row { display: flex; align-items: flex-start; gap: 14px; padding: 14px 0; background: #ffffff; }
    .triage-row + .triage-row { border-top: 1px solid #dfe1e1; }

    /* Status chips */
    .triage-chip {
        display: inline-block;
        font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 0;
        padding: 2px 8px;
        min-width: 72px;
        text-align: center;
        color: #ffffff;
        border-radius: 0;
        flex-shrink: 0;
        margin-top: 3px;
        line-height: 1.6;
    }
    .chip-accept   { background: #1d7f43; }
    .chip-override { background: #e0a400; color: #17191a; }
    .chip-escalate { background: #c22a33; }

    /* Triage row body */
    .triage-body   { flex: 1; min-width: 0; }
    .triage-meta   { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
    .triage-sat    {
        font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
        font-weight: 600;
        font-size: 14px;
        color: #17191a;
    }
    .triage-pass   {
        font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
        font-size: 12px;
        color: #54585a;
    }
    .triage-prio   {
        font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
        font-size: 11px;
        color: #54585a;
        border: 1px solid #dfe1e1;
        padding: 0 5px;
        line-height: 1.6;
        border-radius: 0;
        background: #ffffff;
    }
    .triage-content { display: flex; align-items: baseline; gap: 16px; margin-top: 3px; }
    .triage-explain { font-size: 14px; color: #17191a; line-height: 1.5; flex: 6; min-width: 0; }
    .triage-reason  { font-size: 13px; color: #54585a; line-height: 1.4; flex: 4; min-width: 0; text-align: right; }

    /* Metric tiles */
    .metric-grid {
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        border-bottom: 1px solid #dfe1e1;
        margin-bottom: 0;
    }
    .metric-tile { padding: 16px 20px; }
    .metric-tile + .metric-tile { border-left: 1px solid #dfe1e1; }
    .metric-num {
        font-family: 'Public Sans', sans-serif;
        font-weight: 300;
        font-size: 52px;
        color: #17191a;
        line-height: 1.1;
    }
    .metric-label {
        font-family: 'Public Sans', sans-serif;
        font-size: 12px;
        font-weight: 400;
        letter-spacing: 0.32px;
        color: #54585a;
        margin-top: 4px;
    }
    .metric-dagger { color: #0b6a72; font-size: 14px; vertical-align: super; line-height: 0; }

    /* P1 footnote */
    .p1-footnote {
        font-size: 13px;
        color: #54585a;
        margin: 8px 0 20px 0;
        line-height: 1.6;
        border-top: 1px solid #dfe1e1;
        padding-top: 6px;
    }
    .p1-footnote-dagger { color: #0b6a72; font-size: 13px; }

    /* Filter bar */
    .filter-bar {
        background: #f3f4f4;
        padding: 12px 16px;
        margin-bottom: 12px;
        border-bottom: 1px solid #dfe1e1;
    }

    /* Count line under filter bar */
    .row-count { font-size: 12px; color: #8b9092; margin: 4px 0 8px 0; }

    /* Proportion bar */
    .prop-bar { display: flex; width: 100%; height: 10px; margin-bottom: 6px; }
    .prop-bar-fill  { background: #0b6a72; }
    .prop-bar-empty { background: #dfe1e1; }
    .prop-labels { display: flex; justify-content: space-between; margin-bottom: 16px; }
    .prop-label  { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #54585a; }
    .prop-swatch { display: inline-block; width: 8px; height: 8px; flex-shrink: 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

check_data_files()

schedule = load_schedule()
dropped = load_dropped()
triage = load_triage()

# ---------------------------------------------------------------------------
# Derived metrics  (unchanged)
# ---------------------------------------------------------------------------
n_scheduled = len(schedule)
n_dropped = len(dropped)
n_total = n_scheduled + n_dropped

p1_total = (schedule["priority"] == 1).sum() + (dropped["priority"] == 1).sum()
p1_kept = (schedule["priority"] == 1).sum()

# Planning window span
win_start = pd.to_datetime(schedule["start_utc"]).min()
win_end = pd.to_datetime(schedule["end_utc"]).max()
win_str = (
    win_start.strftime("%Y-%m-%d %H:%MZ")
    + " \u2014 "
    + win_end.strftime("%Y-%m-%d %H:%MZ")
)

MONO = "ui-monospace,SFMono-Regular,Consolas,monospace"

# ---------------------------------------------------------------------------
# Section 1 — Header bar
# ---------------------------------------------------------------------------
SVG_MARK = (
    '<svg width="34" height="34" viewBox="0 0 28 28" style="flex-shrink:0;">'
    '<rect width="28" height="28" fill="#0b6a72"/>'
    '<rect x="3" y="13" width="22" height="1.5" fill="#fff" opacity="0.35"/>'
    '<rect x="3" y="8" width="10" height="7" fill="#fff"/>'
    '<rect x="10" y="8" width="10" height="7" fill="#fff" opacity="0.35"/>'
    '<rect x="10" y="8" width="10" height="7" fill="none" stroke="#fff" stroke-width="1.5"/>'
    '<rect x="13" y="6" width="2" height="11" fill="#fff"/>'
    '</svg>'
)
st.markdown(
    f'<div style="display:flex;justify-content:space-between;align-items:flex-end;'
    f'padding-bottom:12px;margin-bottom:20px;border-bottom:1px solid #dfe1e1;">'
    f'<div style="display:flex;align-items:center;gap:14px;">'
    f'{SVG_MARK}'
    f'<span style="font-family:\'Public Sans\',sans-serif;font-weight:300;font-size:38px;'
    f'color:#17191a;line-height:1.1;letter-spacing:0;">Orbital contact scheduler</span>'
    f'</div>'
    f'<span style="font-family:{MONO};font-size:12px;color:#8b9092;text-align:right;line-height:1.7;">'
    f'Window&nbsp;{win_str}<br>'
    f'Triage&nbsp;{triage["generated_at"]}&nbsp;&nbsp;{triage["model"]}'
    f'</span></div>',
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Section 2 — Metrics row
# ---------------------------------------------------------------------------
st.markdown(
    f'<div class="metric-grid">'
    f'<div class="metric-tile">'
    f'<div class="metric-num">{n_total:,}</div>'
    f'<div class="metric-label">Total passes</div>'
    f'</div>'
    f'<div class="metric-tile">'
    f'<div class="metric-num">{n_scheduled:,}</div>'
    f'<div class="metric-label">Scheduled</div>'
    f'</div>'
    f'<div class="metric-tile">'
    f'<div class="metric-num">{n_dropped:,}</div>'
    f'<div class="metric-label">Dropped</div>'
    f'</div>'
    f'<div class="metric-tile">'
    f'<div class="metric-num">{p1_kept}/{p1_total}<span class="metric-dagger">&dagger;</span></div>'
    f'<div class="metric-label">Priority-1 scheduled</div>'
    f'</div>'
    f'</div>',
    unsafe_allow_html=True,
)

# Priority-1 footnote (replaces the old callout block)
st.markdown(
    f'<p class="p1-footnote">'
    f'<span class="p1-footnote-dagger">&dagger;</span> '
    f'{p1_total - p1_kept} priority-1 pass{"es" if (p1_total - p1_kept) != 1 else ""} '
    f'not scheduled: <span class="mono">METOP-B-031</span> and '
    f'<span class="mono">METOP-B-032</span>, each blocked by '
    f'<span class="mono">NOAA\u00a020\u00a0(JPSS-1)</span> (also priority\u00a01). '
    f'Two priority-1 missions contending for the same antenna is the one conflict the '
    f'scheduler cannot resolve on its own.'
    f'</p>',
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Section 3 — Exceptions requiring review (triage)
# ---------------------------------------------------------------------------
ACTION_CHIP_CLASS = {
    "ACCEPT":   "chip-accept",
    "OVERRIDE": "chip-override",
    "ESCALATE": "chip-escalate",
}

st.markdown('<p class="section-heading">Exceptions requiring review</p>', unsafe_allow_html=True)

# Proportion bar — replaces the old callout block
dropped_with_alt = dropped["has_alternative"].astype(str).str.lower().eq("true").sum()
dropped_without_alt = len(dropped) - dropped_with_alt
delays = pd.to_numeric(
    dropped.loc[dropped["has_alternative"].astype(str).str.lower() == "true", "delay_min"],
    errors="coerce",
)
median_delay = round(float(delays.median()), 1) if not delays.empty else None
median_str = f"{median_delay}\u00a0min" if median_delay is not None else "unknown"

fill_pct = dropped_with_alt / len(dropped) * 100 if len(dropped) else 0
empty_pct = 100 - fill_pct

st.markdown(
    f'<div class="prop-bar">'
    f'<div class="prop-bar-fill" style="width:{fill_pct:.4f}%;"></div>'
    f'<div class="prop-bar-empty" style="width:{empty_pct:.4f}%;"></div>'
    f'</div>'
    f'<div class="prop-labels">'
    f'<span class="prop-label">'
    f'<span class="prop-swatch" style="background:#0b6a72;"></span>'
    f'<span class="mono">{dropped_with_alt:,}</span> have a later alternative'
    f'\u00a0\u00b7\u00a0median wait <span class="mono">{median_str}</span>'
    f'</span>'
    f'<span class="prop-label">'
    f'<span class="prop-swatch" style="background:#dfe1e1;"></span>'
    f'<span class="mono">{dropped_without_alt:,}</span> have none'
    f'</span>'
    f'</div>',
    unsafe_allow_html=True,
)

# Triage list — one st.markdown per row (blank-line truncation fix must not regress)
items = triage["items"]

st.markdown('<div class="triage-list">', unsafe_allow_html=True)
for item in items:
    action = item.get("action", "ACCEPT")
    chip_cls = ACTION_CHIP_CLASS.get(action, "chip-accept")
    sat = item["satellite"]
    pid = item["pass_id"]
    pri = item["priority"]
    expl = item["explanation"]
    reas = item["reason"]
    html = (
        f'<div class="triage-row">'
        f'<span class="triage-chip {chip_cls}">{action}</span>'
        f'<div class="triage-body">'
        f'<div class="triage-meta">'
        f'<span class="triage-sat">{sat}</span>'
        f'<span class="triage-pass">{pid}</span>'
        f'<span class="triage-prio">P{pri}</span>'
        f'</div>'
        f'<div class="triage-content">'
        f'<div class="triage-explain">{expl}</div>'
        f'<div class="triage-reason">{reas}</div>'
        f'</div>'
        f'</div>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)
st.markdown('</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Section 4 — Antenna allocation (Gantt)
# ---------------------------------------------------------------------------
st.markdown('<p class="section-heading">Antenna allocation</p>', unsafe_allow_html=True)

station_order = sorted(schedule["station"].unique().tolist())

STATION_COLORS = {
    "Svalbard": "#053a40",
    "Kiruna":   "#0b6a72",
    "Hawaii":   "#0d7d87",
}

gantt_df = schedule.copy()
gantt_df["start_utc"] = pd.to_datetime(gantt_df["start_utc"], utc=True)
gantt_df["end_utc"] = pd.to_datetime(gantt_df["end_utc"], utc=True)
gantt_df["hover_label"] = (
    gantt_df["satellite"]
    + "<br>Duration: "
    + gantt_df["duration_min"].map(lambda x: f"{x:.1f} min")
    + "<br>Max el: "
    + gantt_df["max_elevation_deg"].map(lambda x: f"{x:.1f}\u00b0")
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
    labels={"station": "Station"},
    template="plotly_white",
)

fig.update_traces(
    hovertemplate="<b>%{customdata[1]}</b><br>%{customdata[0]}<extra></extra>",
    marker_line_width=0,
    opacity=0.9,
)

fig.update_layout(
    height=280,
    margin=dict(l=0, r=0, t=4, b=4),
    legend_title_text="Station",
    xaxis_title="",
    yaxis_title="",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family=MONO, size=12, color="#54585a"),
    legend=dict(bgcolor="rgba(0,0,0,0)", borderwidth=0),
)
fig.update_xaxes(
    showgrid=True,
    gridcolor="#dfe1e1",
    gridwidth=1,
    showline=False,
    zeroline=False,
    tickfont=dict(family=MONO, size=11, color="#8b9092"),
)
fig.update_yaxes(
    showgrid=False,
    showline=False,
    zeroline=False,
    tickfont=dict(family=MONO, size=11, color="#54585a"),
)

st.plotly_chart(fig, use_container_width=True)

st.markdown("<div style='margin-bottom:20px;'></div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Section 5 — All dropped contacts
# ---------------------------------------------------------------------------
st.markdown('<p class="section-heading">All dropped contacts</p>', unsafe_allow_html=True)

# Filter bar on surface-1
st.markdown('<div class="filter-bar">', unsafe_allow_html=True)
filter_col1, filter_col2, filter_col3 = st.columns(3)

with filter_col1:
    sat_options = sorted(dropped["satellite"].unique().tolist())
    sel_satellites = st.multiselect(
        "Satellite", options=sat_options, default=[], placeholder="All satellites"
    )

with filter_col2:
    all_stations: set[str] = set()
    for val in dropped["candidate_stations"].dropna():
        for s in val.split(","):
            all_stations.add(s.strip())
    station_options = sorted(all_stations)
    sel_stations = st.multiselect(
        "Candidate station", options=station_options, default=[], placeholder="All stations"
    )

with filter_col3:
    priority_options = sorted(dropped["priority"].unique().tolist())
    sel_priorities = st.multiselect(
        "Priority", options=priority_options, default=[], placeholder="All priorities"
    )

st.markdown('</div>', unsafe_allow_html=True)

# Filtering logic (unchanged)
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

st.markdown(
    f'<p class="row-count">Showing {len(filtered):,} of {len(dropped):,} dropped contacts</p>',
    unsafe_allow_html=True,
)

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
        "pass_id":            st.column_config.TextColumn("Pass ID"),
        "satellite":          st.column_config.TextColumn("Satellite"),
        "priority":           st.column_config.NumberColumn("Priority", format="%d"),
        "earliest_start_utc": st.column_config.DatetimeColumn(
                                  "Earliest Start (UTC)", format="YYYY-MM-DD HH:mm"
                              ),
        "candidate_stations": st.column_config.TextColumn("Candidate Stations"),
        "blocked_by":         st.column_config.TextColumn("Blocked By"),
        "has_alternative":    st.column_config.CheckboxColumn("Has Alt"),
        "alt_pass_id":        st.column_config.TextColumn("Alt Pass ID"),
        "alt_station":        st.column_config.TextColumn("Alt Station"),
        "alt_start_utc":      st.column_config.DatetimeColumn(
                                  "Alt Start (UTC)", format="YYYY-MM-DD HH:mm"
                              ),
        "delay_min":          st.column_config.NumberColumn("Delay (min)", format="%.1f"),
    },
)
