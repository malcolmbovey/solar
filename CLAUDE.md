# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Solar Battery Analyser — a Streamlit app that reads raw SigEnergy CSV exports and simulates home energy usage under different battery storage scenarios, factoring in time-of-use tariffs and off-peak grid charging.

## Dependency management

This project uses **uv**. Never use `pip` directly.

```bash
uv add <package>          # add a dependency
uv remove <package>       # remove a dependency
uv sync                   # install all deps from lockfile
```

## Running the app

```bash
uv run streamlit run app.py
```

## Architecture

Two files do all the work:

- **`simulator.py`** — pure logic, no UI. Loads the CSV (`load_data`), parses time ranges (`parse_time_ranges`), and runs the battery simulation (`simulate` / `run_scenarios`). The interval duration is derived from the data and stored in `df.attrs["interval_h"]` so the rest of the code doesn't hardcode 5 minutes.

- **`app.py`** — Streamlit UI. Sidebar collects all parameters (battery sizes, tariff rates, time periods, simulation settings); the main area shows a data summary, a scenario comparison table, and three Plotly charts.

### Simulation logic (`simulate` in `simulator.py`)

For each 5-minute interval:
1. **Solar-first dispatch** — solar covers load; surplus charges the battery; any remaining surplus is exported.
2. **Battery covers deficit** — if load > solar, discharge battery down to `min_soc`; anything left is grid import.
3. **Off-peak grid top-up** (optional) — during off-peak hours, charge battery from grid up to the configured rate.

Sign convention in the raw CSV: negative `From/To Battery` = charging, negative `From/To Grid` = exporting. The simulator re-derives all flows from scratch (ignoring the recorded battery/grid columns) so it can model any hypothetical battery size.

### Raw data

`raw/raw.csv` — SigEnergy 5-minute export. Columns: `Date`, `Solar Production (kW)`, `Load Consumed Power (kW)`, `From/To Battery (kW)`, `From/To Grid (kW)`.
