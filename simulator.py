import pandas as pd
import numpy as np


def load_data(path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["Date"])
    df.rename(columns={
        "Date": "timestamp",
        "Solar Production (kW)": "solar_kw",
        "Load Consumed Power (kW)": "load_kw",
        "From/To Battery (kW)": "battery_kw",
        "From/To Grid (kW)": "grid_kw",
    }, inplace=True)
    df.dropna(subset=["solar_kw", "load_kw"], inplace=True)
    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)

    # Derive interval duration from the data itself
    deltas = df["timestamp"].diff().dropna()
    df.attrs["interval_h"] = deltas.median().total_seconds() / 3600
    return df


def parse_time_ranges(text: str) -> list[tuple[float, float]]:
    """'7-11, 17-22' -> [(7.0, 11.0), (17.0, 22.0)]"""
    result = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        lo, hi = part.split("-", 1)
        result.append((float(lo), float(hi)))
    return result


def _build_period_mask(hours: np.ndarray, minutes: np.ndarray,
                       ranges: list[tuple[float, float]]) -> np.ndarray:
    """Return boolean array True where timestamp falls within any of the ranges."""
    t = hours + minutes / 60.0
    mask = np.zeros(len(t), dtype=bool)
    for lo, hi in ranges:
        if lo <= hi:
            mask |= (t >= lo) & (t < hi)
        else:  # spans midnight
            mask |= (t >= lo) | (t < hi)
    return mask


def simulate(
    df: pd.DataFrame,
    total_kwh: float,
    min_soc_pct: float,
    allow_grid_charge: bool,
    grid_charge_kw: float,
    grid_charge_target_pct: float,
    peak_ranges: list[tuple[float, float]],
    offpeak_ranges: list[tuple[float, float]],
    peak_rate: float,
    offpeak_rate: float,
    shoulder_rate: float,
    fit_rate: float,
    initial_soc_pct: float = 50.0,
) -> dict:
    dt = df.attrs["interval_h"]
    min_soc = min_soc_pct / 100 * total_kwh
    soc = initial_soc_pct / 100 * total_kwh

    hours = df["timestamp"].dt.hour.values
    minutes = df["timestamp"].dt.minute.values
    solar = df["solar_kw"].values * dt
    load = df["load_kw"].values * dt

    peak_mask = _build_period_mask(hours, minutes, peak_ranges)
    offpeak_mask = _build_period_mask(hours, minutes, offpeak_ranges)

    # Per-interval import rate array
    rates = np.where(peak_mask, peak_rate,
             np.where(offpeak_mask, offpeak_rate, shoulder_rate))

    grid_import_arr = np.zeros(len(df))
    grid_export_arr = np.zeros(len(df))

    for i in range(len(df)):
        net = solar[i] - load[i]

        if net >= 0:
            charge = min(net, total_kwh - soc)
            soc += charge
            grid_export_arr[i] = net - charge
        else:
            deficit = -net
            discharge = min(deficit, max(0.0, soc - min_soc))
            soc -= discharge
            grid_import_arr[i] = deficit - discharge

        # Off-peak grid top-up — only charge up to the target SoC, leaving
        # the remaining headroom for solar the next morning.
        if allow_grid_charge and offpeak_mask[i]:
            target = grid_charge_target_pct / 100 * total_kwh
            if soc < target:
                gc = min(grid_charge_kw * dt, target - soc)
                soc += gc
                grid_import_arr[i] += gc

    total_load = load.sum()
    total_solar = solar.sum()
    total_import = grid_import_arr.sum()
    total_export = grid_export_arr.sum()
    import_cost = (grid_import_arr * rates).sum()
    export_revenue = total_export * fit_rate

    self_suff = (total_load - total_import) / total_load * 100 if total_load > 0 else 0.0
    self_cons = (total_solar - total_export) / total_solar * 100 if total_solar > 0 else 0.0

    return {
        "battery_kwh": total_kwh,
        "grid_import_kwh": round(total_import, 1),
        "grid_export_kwh": round(total_export, 1),
        "self_sufficiency_pct": round(self_suff, 1),
        "self_consumption_pct": round(self_cons, 1),
        "import_cost": round(import_cost, 2),
        "export_revenue": round(export_revenue, 2),
        "net_cost": round(import_cost - export_revenue, 2),
    }


def run_scenarios(df: pd.DataFrame, sizes: list[float], **kwargs) -> pd.DataFrame:
    return pd.DataFrame([simulate(df, s, **kwargs) for s in sizes])
