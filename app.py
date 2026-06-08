from pathlib import Path

import plotly.express as px
import streamlit as st

from simulator import load_data, parse_time_ranges, run_scenarios

DEFAULT_CSV = Path(__file__).parent / "raw" / "raw.csv"

st.set_page_config(page_title="Solar Battery Analyser", layout="wide")
st.title("Solar Battery Analyser")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Data")
    uploaded = st.file_uploader("Upload SigEnergy CSV", type="csv")

    st.header("Battery")
    current_kwh = st.number_input(
        "Current battery capacity (kWh)", min_value=0.0, value=10.0, step=0.5
    )
    extra_str = st.text_input(
        "Additional capacity to evaluate (kWh, comma-separated)",
        value="5, 10, 15, 20",
        help="These are added on top of your current battery. e.g. '5, 10' models current+5 and current+10.",
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
        peak_rate = st.number_input("Peak", value=0.35, step=0.01, format="%.3f")
        shoulder_rate = st.number_input("Shoulder", value=0.25, step=0.01, format="%.3f")
    with col2:
        offpeak_rate = st.number_input("Off-peak", value=0.15, step=0.01, format="%.3f")
        fit_rate = st.number_input("Feed-in", value=0.05, step=0.01, format="%.3f")

    peak_str = st.text_input(
        "Peak hours (24 h ranges, comma-separated)", value="7-11, 17-22"
    )
    offpeak_str = st.text_input(
        "Off-peak hours (can span midnight)", value="22-7"
    )
    try:
        peak_ranges = parse_time_ranges(peak_str)
        offpeak_ranges = parse_time_ranges(offpeak_str)
    except Exception:
        st.error('Invalid time ranges — use format like "7-11, 17-22".')
        st.stop()

    st.header("Simulation")
    min_soc_pct = st.slider("Min battery state of charge (%)", 0, 30, 10)
    allow_grid_charge = st.checkbox("Charge from grid during off-peak", value=True)
    grid_charge_kw = st.number_input(
        "Max grid charge rate (kW)", value=3.0, step=0.5, disabled=not allow_grid_charge
    )
    grid_charge_target_pct = st.slider(
        "Grid charge target (%)",
        min_value=50, max_value=100, value=80, disabled=not allow_grid_charge,
        help="How full to charge the battery from the grid overnight. "
             "The remaining headroom is left for solar the next morning.",
    )
    initial_soc_pct = st.slider("Initial battery SoC (%)", 0, 100, 50)

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
