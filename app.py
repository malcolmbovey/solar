import json
from pathlib import Path

import plotly.express as px
import streamlit as st

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from simulator import load_data, parse_time_ranges, run_scenarios, simulate


@st.cache_data
def _get_detail_df(
    _df,           # underscore prefix: Streamlit skips hashing large DataFrames
    size,
    peak_str, offpeak_str,   # pass strings so they're easily hashable
    min_soc_pct, allow_grid_charge, grid_charge_kw, grid_charge_target_pct,
    peak_rate, offpeak_rate, shoulder_rate, fit_rate, initial_soc_pct,
):
    _, detail = simulate(
        _df, size,
        min_soc_pct=min_soc_pct,
        allow_grid_charge=allow_grid_charge,
        grid_charge_kw=grid_charge_kw,
        grid_charge_target_pct=grid_charge_target_pct,
        peak_ranges=parse_time_ranges(peak_str),
        offpeak_ranges=parse_time_ranges(offpeak_str),
        peak_rate=peak_rate,
        offpeak_rate=offpeak_rate,
        shoulder_rate=shoulder_rate,
        fit_rate=fit_rate,
        initial_soc_pct=initial_soc_pct,
        return_detail=True,
    )
    return detail

DEFAULT_CSV = Path(__file__).parent / "raw" / "raw.csv"
SETTINGS_FILE = Path(__file__).parent / "settings.json"

DEFAULTS = {
    "current_kwh": 5.0,
    "extra_str": "5, 10, 15, 20",
    "peak_rate": 0.35,
    "shoulder_rate": 0.25,
    "offpeak_rate": 0.15,
    "fit_rate": 0.05,
    "peak_str": "7-11, 17-22",
    "offpeak_str": "22-7",
    "min_soc_pct": 10,
    "allow_grid_charge": True,
    "grid_charge_kw": 3.0,
    "grid_charge_target_pct": 80,
    "initial_soc_pct": 50,
}


def _load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_settings() -> None:
    data = {k: st.session_state[k] for k in DEFAULTS if k in st.session_state}
    SETTINGS_FILE.write_text(json.dumps(data, indent=2))


# Populate session_state from file on first run only
if "settings_loaded" not in st.session_state:
    saved = _load_settings()
    for key, default in DEFAULTS.items():
        st.session_state[key] = saved.get(key, default)
    st.session_state["settings_loaded"] = True

st.set_page_config(page_title="Solar Battery Analyser", layout="wide")
st.title("Solar Battery Analyser")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Data")
    uploaded = st.file_uploader("Upload SigEnergy CSV", type="csv")

    st.header("Battery")
    current_kwh = st.number_input(
        "Current battery capacity (kWh)", min_value=0.0, step=0.5,
        key="current_kwh", on_change=_save_settings,
    )
    extra_str = st.text_input(
        "Additional capacity to evaluate (kWh, comma-separated)",
        help="These are added on top of your current battery. e.g. '5, 10' models current+5 and current+10.",
        key="extra_str", on_change=_save_settings,
    )
    try:
        extra_sizes = [float(x.strip()) for x in extra_str.split(",") if x.strip()]
    except ValueError:
        st.error("Invalid sizes — use comma-separated numbers.")
        st.stop()

    all_sizes = sorted({0.0, current_kwh} | {current_kwh + e for e in extra_sizes})

    st.header("Tariff (£/kWh)")
    col1, col2 = st.columns(2)
    with col1:
        peak_rate = st.number_input(
            "Peak", step=0.01, format="%.3f",
            key="peak_rate", on_change=_save_settings,
        )
        shoulder_rate = st.number_input(
            "Shoulder", step=0.01, format="%.3f",
            key="shoulder_rate", on_change=_save_settings,
        )
    with col2:
        offpeak_rate = st.number_input(
            "Off-peak", step=0.01, format="%.3f",
            key="offpeak_rate", on_change=_save_settings,
        )
        fit_rate = st.number_input(
            "Feed-in", step=0.01, format="%.3f",
            key="fit_rate", on_change=_save_settings,
        )

    peak_str = st.text_input(
        "Peak hours (24 h ranges, comma-separated)",
        key="peak_str", on_change=_save_settings,
    )
    offpeak_str = st.text_input(
        "Off-peak hours (can span midnight)",
        key="offpeak_str", on_change=_save_settings,
    )
    try:
        peak_ranges = parse_time_ranges(peak_str)
        offpeak_ranges = parse_time_ranges(offpeak_str)
    except Exception:
        st.error('Invalid time ranges — use format like "7-11, 17-22".')
        st.stop()

    st.header("Simulation")
    min_soc_pct = st.slider(
        "Min battery state of charge (%)", 0, 30,
        key="min_soc_pct", on_change=_save_settings,
    )
    allow_grid_charge = st.checkbox(
        "Charge from grid during off-peak",
        key="allow_grid_charge", on_change=_save_settings,
    )
    grid_charge_kw = st.number_input(
        "Max grid charge rate (kW)", step=0.5, disabled=not allow_grid_charge,
        key="grid_charge_kw", on_change=_save_settings,
    )
    grid_charge_target_pct = st.slider(
        "Grid charge target (%)", min_value=50, max_value=100,
        disabled=not allow_grid_charge,
        help="How full to charge the battery from the grid overnight. "
             "The remaining headroom is left for solar the next morning.",
        key="grid_charge_target_pct", on_change=_save_settings,
    )
    initial_soc_pct = st.slider(
        "Initial battery SoC (%)", 0, 100,
        key="initial_soc_pct", on_change=_save_settings,
    )

# ── Load data ─────────────────────────────────────────────────────────────────
try:
    df = load_data(uploaded if uploaded else DEFAULT_CSV)
except Exception as e:
    st.error(f"Could not load data: {e}")
    st.stop()

# ── Data summary ──────────────────────────────────────────────────────────────
interval_h = df.attrs["interval_h"]
total_solar = (df["solar_kw"] * interval_h).sum()
total_load = (df["load_kw"] * interval_h).sum()

st.subheader("Data Summary")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Period", f"{df['timestamp'].min().date()} → {df['timestamp'].max().date()}")
c2.metric("Intervals", f"{len(df):,} ({interval_h * 60:.0f} min each)")
c3.metric("Total solar", f"{total_solar:.1f} kWh")
c4.metric("Total home load", f"{total_load:.1f} kWh")

st.divider()

# ── Run scenarios ─────────────────────────────────────────────────────────────
sim_kwargs = dict(
    min_soc_pct=min_soc_pct,
    allow_grid_charge=allow_grid_charge,
    grid_charge_kw=grid_charge_kw if allow_grid_charge else 0.0,
    grid_charge_target_pct=grid_charge_target_pct if allow_grid_charge else 0.0,
    peak_ranges=peak_ranges,
    offpeak_ranges=offpeak_ranges,
    peak_rate=peak_rate,
    offpeak_rate=offpeak_rate,
    shoulder_rate=shoulder_rate,
    fit_rate=fit_rate,
    initial_soc_pct=initial_soc_pct,
)

with st.spinner("Running scenarios…"):
    results = run_scenarios(df, all_sizes, **sim_kwargs)


def _label(kwh: float) -> str:
    if kwh == 0:
        return "No battery (0 kWh)"
    if kwh == current_kwh:
        return f"Current ({kwh:.0f} kWh)"
    added = kwh - current_kwh
    return f"+{added:.0f} kWh → {kwh:.0f} kWh total"


results["Scenario"] = results["battery_kwh"].apply(_label)

# Savings column relative to current setup
current_cost = results.loc[results["battery_kwh"] == current_kwh, "net_cost"].values
if len(current_cost):
    results["Savings vs current (£)"] = (current_cost[0] - results["net_cost"]).round(2)

# ── Summary table ─────────────────────────────────────────────────────────────
st.subheader("Scenario Comparison")

display_cols = {
    "Scenario": "Scenario",
    "grid_import_kwh": "Grid Import (kWh)",
    "grid_export_kwh": "Grid Export (kWh)",
    "self_sufficiency_pct": "Self-Sufficiency (%)",
    "self_consumption_pct": "Self-Consumption (%)",
    "import_cost": "Import Cost (£)",
    "export_revenue": "Export Revenue (£)",
    "net_cost": "Net Cost (£)",
}
if "Savings vs current (£)" in results.columns:
    display_cols["Savings vs current (£)"] = "Savings vs Current (£)"

table = results[list(display_cols.keys())].rename(columns=display_cols)
st.dataframe(table.set_index("Scenario"), use_container_width=True)

# ── Charts ────────────────────────────────────────────────────────────────────
st.subheader("Visual Comparison")
tab1, tab2, tab3 = st.tabs(["Self-Sufficiency", "Net Cost", "Grid Flows"])

with tab1:
    fig = px.bar(
        results, x="Scenario", y="self_sufficiency_pct",
        labels={"self_sufficiency_pct": "Self-Sufficiency (%)"},
        text_auto=".1f",
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(yaxis_range=[0, 100])
    st.plotly_chart(fig, use_container_width=True)

with tab2:
    fig = px.bar(
        results, x="Scenario", y="net_cost",
        labels={"net_cost": "Net Cost (£)"},
        text_auto=".2f",
    )
    fig.update_traces(textposition="outside")
    st.plotly_chart(fig, use_container_width=True)

with tab3:
    fig = px.bar(
        results.melt(
            id_vars="Scenario",
            value_vars=["grid_import_kwh", "grid_export_kwh"],
            var_name="Flow",
            value_name="kWh",
        ).replace({"grid_import_kwh": "Grid Import", "grid_export_kwh": "Grid Export"}),
        x="Scenario", y="kWh", color="Flow", barmode="group",
        text_auto=".1f",
    )
    fig.update_traces(textposition="outside")
    st.plotly_chart(fig, use_container_width=True)

# ── Daily detail ──────────────────────────────────────────────────────────────
st.divider()
st.subheader("Daily Detail")

available_dates = sorted(df["timestamp"].dt.date.unique())
col1, col2 = st.columns([1, 2])
with col1:
    selected_date = st.date_input(
        "Select date",
        value=available_dates[-1],
        min_value=available_dates[0],
        max_value=available_dates[-1],
    )
with col2:
    scenario_labels = [_label(s) for s in all_sizes]
    selected_label = st.selectbox("Battery scenario", scenario_labels,
                                   index=scenario_labels.index(_label(current_kwh)))
    selected_size = all_sizes[scenario_labels.index(selected_label)]

with st.spinner("Simulating daily detail…"):
    detail_df = _get_detail_df(
        df, selected_size,
        peak_str=peak_str,
        offpeak_str=offpeak_str,
        min_soc_pct=min_soc_pct,
        allow_grid_charge=allow_grid_charge,
        grid_charge_kw=grid_charge_kw if allow_grid_charge else 0.0,
        grid_charge_target_pct=grid_charge_target_pct if allow_grid_charge else 0.0,
        peak_rate=peak_rate,
        offpeak_rate=offpeak_rate,
        shoulder_rate=shoulder_rate,
        fit_rate=fit_rate,
        initial_soc_pct=initial_soc_pct,
    )

# Filter to selected date and aggregate to hourly
day_df = detail_df[detail_df["timestamp"].dt.date == selected_date].copy()

if day_df.empty:
    st.warning("No data for the selected date.")
else:
    st.subheader(f"Energy totals for {selected_date}")
    m1, m2, m3 = st.columns(3)
    m1.metric("Grid Import", f"{day_df['grid_import_kwh'].sum():.2f} kWh")
    m2.metric("Battery Discharge", f"{day_df['battery_discharge_kwh'].sum():.2f} kWh")
    m3.metric("Grid Export", f"{day_df['grid_export_kwh'].sum():.2f} kWh")

    hourly = (
        day_df.set_index("timestamp")
        .resample("1h")
        .agg(
            solar_kwh=("solar_kwh", "sum"),
            load_kwh=("load_kwh", "sum"),
            grid_export_kwh=("grid_export_kwh", "sum"),
            battery_soc_kwh=("battery_soc_kwh", "last"),
        )
        .reset_index()
    )
    hourly["hour"] = hourly["timestamp"].dt.hour

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    fig.add_trace(
        go.Scatter(x=hourly["hour"], y=hourly["solar_kwh"], name="Solar Generated",
                   fill="tozeroy", line=dict(color="gold", width=2)),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(x=hourly["hour"], y=hourly["load_kwh"], name="Load",
                   line=dict(color="steelblue", width=2)),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(x=hourly["hour"], y=hourly["grid_export_kwh"], name="Grid Export",
                   line=dict(color="seagreen", width=2)),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(x=hourly["hour"], y=hourly["battery_soc_kwh"], name="Battery SoC",
                   line=dict(color="mediumpurple", width=2, dash="dash"),
                   mode="lines+markers"),
        secondary_y=True,
    )

    fig.update_xaxes(title_text="Hour of day", tickmode="linear", dtick=1, range=[0, 23])
    fig.update_yaxes(title_text="Energy (kWh)", secondary_y=False, rangemode="tozero")
    fig.update_yaxes(title_text="Battery SoC (kWh)", secondary_y=True, rangemode="tozero")
    fig.update_layout(hovermode="x unified", legend=dict(orientation="h", y=1.12))

    st.plotly_chart(fig, use_container_width=True)
