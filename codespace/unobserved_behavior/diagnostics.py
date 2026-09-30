"""Data diagnostics that quantify what ACN-Data does NOT observe.

Each function returns plain numbers (for the synthesis table) and, where useful, a small
DataFrame (for figures). Every metric is computed from the normalized table in ``data.py``.

Interpretation notes
--------------------
* ACN-Data contains only sessions that plugged in. If the site was rarely full, blocked drivers
  were rare at the observed demand, and waiting/abandonment cannot be estimated from these data;
  they must be synthesized for counterfactual scenarios (fewer ports, more demand).
* Caltech ran adaptive (managed) charging, so charging completion times are policy-dependent.
  Dwell (connect -> disconnect) is the behavioral primitive; idle time is derived.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .data import LOCAL_TZ, REGIME_BREAKS

EVSE_KW_32A_208V = 32 * 208 / 1000  # 6.656 kW nominal Level-2 port at Caltech (32 A, 208 V)


# --------------------------------------------------------------------------- occupancy
def occupancy_minutes(df: pd.DataFrame) -> pd.DataFrame:
    """Minute-level count of plugged-in, charging and idle sessions (local time index).

    A session occupies [connect, disconnect). It is 'charging' before its (clipped) done time and
    'idle' after; sessions with missing done time are counted as 'unknown'.
    """
    v = df.loc[df["valid_interval"]]
    start = v["connect"].min().floor("D")
    end = v["disconnect"].max().ceil("D")
    n = int((end - start).total_seconds() // 60) + 1

    def minute(ts: pd.Series) -> np.ndarray:
        return ((ts - start).dt.total_seconds() // 60).astype(int).to_numpy()

    def count(s_idx: np.ndarray, e_idx: np.ndarray) -> np.ndarray:
        diff = np.zeros(n + 1, dtype=np.int32)
        keep = e_idx > s_idx
        np.add.at(diff, s_idx[keep], 1)
        np.add.at(diff, e_idx[keep], -1)
        return np.cumsum(diff)[:-1]

    s, e = minute(v["connect"]), minute(v["disconnect"])
    known = v["done_status"].to_numpy() != "missing"
    done = v["connect"] + pd.to_timedelta(v["charge_h"].fillna(0), unit="h")
    d = minute(done)
    plugged = count(s, e)
    charging = count(s[known], d[known])
    idle = count(d[known], e[known])
    unknown = count(s[~known], e[~known])
    index = pd.date_range(start, periods=n, freq="1min").tz_convert(LOCAL_TZ)
    return pd.DataFrame({"plugged": plugged, "charging": charging, "idle": idle, "unknown": unknown}, index=index)


def capacity_minutes(df: pd.DataFrame, index: pd.DatetimeIndex, nominal: int) -> pd.DataFrame:
    """Nominal ports and a proxy for ports in service: stations with >= 1 session that local month."""
    active = df.groupby("month_local")["station_id"].nunique()
    months = index.strftime("%Y-%m")
    return pd.DataFrame({"nominal": nominal, "active_month": active.reindex(months).to_numpy()}, index=index)


def censoring_metrics(df: pd.DataFrame, occ: pd.DataFrame, cap: pd.DataFrame) -> dict:
    """How often the site was at or near capacity, i.e. how much blocked demand could be missing."""
    plugged = occ["plugged"]
    active = cap["active_month"]
    nominal = int(cap["nominal"].iloc[0])
    weekday_day = (plugged.index.dayofweek < 5) & (plugged.index.hour >= 7) & (plugged.index.hour < 19)

    # occupancy seen by each arriving driver: the minute before plug-in (floor in UTC: flooring local
    # wall time is ambiguous in the repeated hour of a daylight-saving fall-back night)
    minute_before = (df.loc[df["valid_interval"], "connect"].dt.floor("min") - pd.Timedelta(minutes=1)).dt.tz_convert(LOCAL_TZ)
    occ_before = plugged.reindex(minute_before).to_numpy()
    active_before = active.reindex(minute_before).to_numpy()
    seeing_near_full = occ_before >= active_before - 2

    by_regime = {}
    brk = REGIME_BREAKS.get(str(df["site_id"].iloc[0]))
    if brk is not None:
        for label, part in [(f"pre_{brk:%Y-%m}", plugged[plugged.index < brk]),
                            (f"post_{brk:%Y-%m}", plugged[plugged.index >= brk])]:
            if len(part):  # the loaded data may not span the break
                by_regime[f"max_concurrent_{label}"] = int(part.max())

    daily_max = plugged.groupby(plugged.index.date).max()
    daily_active = active.groupby(active.index.date).max()
    return {
        **by_regime,
        "n_arrivals_seeing_within2_active": int(np.nansum(seeing_near_full)),
        "nominal_ports": nominal,
        "stations_observed": int(df["station_id"].nunique()),
        "active_ports_month_min": int(active.min()),
        "active_ports_month_max": int(active.max()),
        "max_concurrent": int(plugged.max()),
        "occ_p50": float(plugged.quantile(0.50)),
        "occ_p99": float(plugged.quantile(0.99)),
        "weekday_7_19_occ_p99": float(plugged[weekday_day].quantile(0.99)),
        "share_min_full_nominal": float((plugged >= nominal).mean()),
        "share_min_within2_nominal": float((plugged >= nominal - 2).mean()),
        "share_min_full_active": float((plugged >= active).mean()),
        "share_min_within2_active": float((plugged >= active - 2).mean()),
        "weekday_7_19_share_within2_active": float((plugged[weekday_day] >= active[weekday_day] - 2).mean()),
        "share_arrivals_seeing_within2_nominal": float(np.nanmean(occ_before >= nominal - 2)),
        "share_arrivals_seeing_within2_active": float(np.nanmean(seeing_near_full)),
        "days_observed": int(len(daily_max)),
        "days_reaching_active_minus2": int((daily_max >= daily_active - 2).sum()),
        "days_reaching_nominal_minus4": int((daily_max >= nominal - 4).sum()),
    }


def idle_share_metrics(occ: pd.DataFrame) -> dict:
    """Share of occupied port-minutes that deliver no energy (post-charge idle)."""
    known = occ["charging"] + occ["idle"]
    wk = occ.index.dayofweek < 5
    afternoon = wk & (occ.index.hour >= 12) & (occ.index.hour < 16)
    hours = occ.index[wk].hour
    idle_share_by_hour = occ.loc[wk, "idle"].groupby(hours).sum() / known[wk].groupby(hours).sum()
    busiest_hour = int(occ.loc[wk, "plugged"].groupby(hours).mean().idxmax())
    return {
        "idle_share_of_occupied_minutes": float(occ["idle"].sum() / known.sum()),
        "weekday_12_16_idle_share": float(occ.loc[afternoon, "idle"].sum() / known[afternoon].sum()),
        "weekday_busiest_hour": busiest_hour,
        "weekday_busiest_hour_mean_plugged": float(occ.loc[wk, "plugged"].groupby(hours).mean().max()),
        "weekday_busiest_hour_idle_share": float(idle_share_by_hour.loc[busiest_hour]),
    }


# --------------------------------------------------------------------------- idle / departure
IDLE_BINS_MIN = [0, 1, 15, 30, 60, 120, 240, 480, np.inf]


def idle_metrics(df: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    known = df.loc[df["done_status"] != "missing"].copy()
    known["idle_min"] = known["idle_h"] * 60
    completed = known.loc[known["done_status"] == "completed", "idle_min"]
    table = (pd.cut(known["idle_min"], IDLE_BINS_MIN, right=True, include_lowest=True)
             .value_counts(normalize=True).sort_index().rename("share").to_frame())
    table.index = table.index.astype(str)
    by_regime = {}
    for regime, part in known.groupby("regime"):
        comp = part.loc[part["done_status"] == "completed", "idle_min"]
        by_regime[f"share_completed_idle_gt_60_{regime}"] = float((comp > 60).mean())
        by_regime[f"share_sessions_interrupted_{regime}"] = float((part["done_status"] == "interrupted").mean())
    interrupted = df["done_status"] == "interrupted"
    return {
        **by_regime,
        "share_sessions_interrupted": float(interrupted.mean()),
        # 'interrupted' mixes completion recorded just before unplug with completion recorded AFTER
        # unplug; the time-series check shows the second group is often mislabeled (see timeseries.py)
        "share_interrupted_done_before_unplug": float((interrupted & (df["done"] <= df["disconnect"])).mean()),
        "share_interrupted_done_after_unplug": float((interrupted & (df["done"] > df["disconnect"])).mean()),
        "share_sessions_missing_done": float((df["done_status"] == "missing").mean()),
        "idle_min_median_completed": float(completed.median()),
        "idle_min_p90_completed": float(completed.quantile(0.9)),
        "share_completed_idle_gt_60": float((completed > 60).mean()),
        "share_completed_idle_gt_240": float((completed > 240).mean()),
        "share_completed_idle_le_15": float((completed <= 15).mean()),
        "dwell_h_median": float(df["dwell_h"].median()),
        "charge_h_median_completed": float(known.loc[known["done_status"] == "completed", "charge_h"].median()),
    }, table


def departure_response_test(df: pd.DataFrame, shift_min: float = 60.0) -> dict:
    """Do unplug times cluster right after charging completes (drivers reacting to completion)?

    Spike ratio = sessions unplugging 2-12 min after completion divided by the mean count in the next
    five 10-min bins. The first bin starts at 2 min so it does not share an edge with the 1-min
    'interrupted' cutoff (which matters for minute-floored timestamps). A placebo moves completion
    ``shift_min`` later (only where it still falls before unplugging). Split by user-input status:
    without an announced departure the scheduler cannot time completion to the departure, so a spike
    there points to driver response. ``excess_share`` = spike count above the background level as a
    share of completed sessions: the share of drivers who plausibly react to completion.
    """
    out = {}
    edges = np.arange(2, 63, 10)  # [2,12), [12,22), ... [52,62]

    def spike(idle_min: pd.Series) -> tuple[float, float]:
        counts, _ = np.histogram(idle_min, bins=edges)
        base = counts[1:].mean()
        ratio = float(counts[0] / base) if base > 0 else float("nan")
        return ratio, float((counts[0] - base) / len(idle_min)) if len(idle_min) else float("nan")

    comp = df.loc[df["done_status"] == "completed"].copy()
    comp["idle_min"] = comp["idle_h"] * 60
    for label, part in [("all", comp), ("with_input", comp[comp["has_input"]]), ("no_input", comp[~comp["has_input"]])]:
        placebo = part["idle_min"] - shift_min
        out[f"spike_ratio_{label}"], out[f"excess_share_{label}"] = spike(part["idle_min"])
        out[f"placebo_spike_ratio_{label}"] = spike(placebo[placebo > 2])[0]
        out[f"n_completed_{label}"] = int(len(part))
    return out


# --------------------------------------------------------------------------- user inputs
def _smd(a: pd.Series, b: pd.Series) -> float:
    a, b = a.dropna(), b.dropna()
    pooled = np.sqrt((a.var() + b.var()) / 2)
    return float((a.mean() - b.mean()) / pooled) if pooled > 0 else float("nan")


def input_metrics(df: pd.DataFrame, calibration_regime: str | None = None) -> tuple[dict, pd.DataFrame]:
    """Coverage, missingness mechanism check and error of FIRST user inputs (what a scheduler sees).

    Coverage differs by regime (see data.REGIME_BREAK_LOCAL), so the missingness check and the
    error distributions are computed inside ``calibration_regime``, where most sessions have input.
    """
    if calibration_regime is None:  # the latest regime, where most sessions carry input
        calibration_regime = "post_2018-11" if "post_2018-11" in set(df["regime"]) else str(df["regime"].iloc[-1])
    version = str(df["input_version"].iloc[0]) if "input_version" in df else "first"
    m = {
        "input_version": version,
        "input_version_label": {"first": "First-version", "latest": "LATEST-version (first version not in this input)"}.get(version, "No"),
        "input_coverage": float(df["has_input"].mean()),
        "input_coverage_weekday": float(df.loc[~df["is_weekend"], "has_input"].mean()),
        "input_coverage_weekend": float(df.loc[df["is_weekend"], "has_input"].mean()),
        "share_sessions_multiple_input_versions": float((df["n_input_versions"] > 1).mean()),
        "share_sessions_with_user_id": float(df["user_id"].notna().mean()),
        "calibration_regime": calibration_regime,
    }
    for regime, part in df.groupby("regime"):
        m[f"input_coverage_{regime}"] = float(part["has_input"].mean())
        m[f"sessions_{regime}"] = int(len(part))
    cal = df[df["regime"] == calibration_regime]
    with_in, without = cal[cal["has_input"]], cal[~cal["has_input"]]
    for col in ["arrival_hour", "dwell_h", "kwh"]:
        m[f"smd_with_minus_without_{col}"] = _smd(with_in[col], without[col])
    # From 2018-11-01 unclaimed (no-input) Caltech sessions were terminated after 30 min (Lee et al. 2019)
    m["no_input_calibration_sessions"] = int(len(without))
    m["share_no_input_charged_le_35min"] = float((without["charge_h"] <= 35 / 60).mean())
    m["median_kwh_no_input"] = float(without["kwh"].median())
    m["p90_kwh_no_input"] = float(without["kwh"].quantile(0.9))
    m["median_kwh_with_input"] = float(with_in["kwh"].median())

    e = with_in.copy()
    e["dep_err_min"] = (e["dep_req_first"] - e["disconnect"]).dt.total_seconds() / 60  # >0: left earlier than announced
    e["energy_ratio"] = e["kwh"] / e["kwh_req_first"]
    e["dwell_vs_announced_min"] = e["dwell_h"] * 60 - e["min_avail_first"]
    m.update({
        "dep_err_min_median": float(e["dep_err_min"].median()),
        "dep_err_min_iqr": float(e["dep_err_min"].quantile(0.75) - e["dep_err_min"].quantile(0.25)),
        "share_dep_within_30min": float((e["dep_err_min"].abs() <= 30).mean()),
        "share_dep_within_60min": float((e["dep_err_min"].abs() <= 60).mean()),
        "share_left_later_than_announced": float((e["dep_err_min"] < 0).mean()),
        "energy_ratio_median": float(e["energy_ratio"].median()),
        "share_request_met_95pct": float((e["energy_ratio"] >= 0.95).mean()),
        "share_request_overstated_gt20pct": float((e["energy_ratio"] < 1 / 1.2).mean()),
        "share_request_met_95pct_completed": float((e.loc[e["done_status"] == "completed", "energy_ratio"] >= 0.95).mean()),
        "share_request_met_95pct_interrupted": float((e.loc[e["done_status"] == "interrupted", "energy_ratio"] >= 0.95).mean()),
    })
    errors = e[["dep_err_min", "energy_ratio", "done_status", "is_weekend", "arrival_hour", "dwell_h", "kwh", "kwh_req_first"]]
    return m, errors


# --------------------------------------------------------------------------- rates, stations, counts
def rate_metrics(df: pd.DataFrame) -> dict:
    """Average realized power while charging (completed sessions, >= 10 min of charging)."""
    c = df[(df["done_status"] == "completed") & (df["charge_h"] >= 1 / 6)]
    avg_kw = c["kwh"] / c["charge_h"]
    return {
        "min_session_kwh": float(df["kwh"].min()),
        "share_sessions_kwh_lt_0p55": float((df["kwh"] < 0.55).mean()),
        "avg_charging_kw_p10": float(avg_kw.quantile(0.10)),
        "avg_charging_kw_median": float(avg_kw.median()),
        "avg_charging_kw_p90": float(avg_kw.quantile(0.90)),
        "share_avg_kw_above_6p656": float((avg_kw > EVSE_KW_32A_208V).mean()),
    }


def station_availability_metrics(df: pd.DataFrame, gap_weekdays: int = 10) -> dict:
    """Stations with long runs of weekdays without any session: candidate outages (or unused ports)."""
    days = pd.bdate_range(min(df["date_local"]), max(df["date_local"]))
    used = df.loc[~df["is_weekend"]].groupby("station_id")["date_local"].agg(lambda s: set(s))
    long_runs, lost_days = 0, 0
    for station_days in used:
        run = 0
        for day in days.date:
            if day in station_days:
                if run >= gap_weekdays:
                    long_runs += 1
                    lost_days += run
                run = 0
            else:
                run += 1
        if run >= gap_weekdays:
            long_runs += 1
            lost_days += run
    return {
        "station_gap_threshold_weekdays": gap_weekdays,
        "station_runs_without_sessions": int(long_runs),
        "share_station_weekdays_in_long_gaps": float(lost_days / (len(days) * len(used))),
    }


def arrival_count_metrics(df: pd.DataFrame) -> dict:
    """Daily plug-in counts including zero days (overdispersion relative to Poisson), overall and by regime."""
    out = {}
    for regime_label, part in [("all", df)] + list(df.groupby("regime")):
        days = pd.date_range(min(part["date_local"]), max(part["date_local"]), freq="D")
        counts = part.groupby("date_local").size().reindex(days.date, fill_value=0)
        wk = np.array([d.weekday() < 5 for d in counts.index])
        suffix = "" if regime_label == "all" else f"_{regime_label}"
        for label, mask in [("weekday", wk), ("weekend", ~wk)]:
            c = counts[mask]
            out[f"daily_arrivals_mean_{label}{suffix}"] = float(c.mean())
            out[f"daily_arrivals_var_to_mean_{label}{suffix}"] = float(c.var() / c.mean())
            out[f"zero_arrival_days_{label}{suffix}"] = int((c == 0).sum())
    return out


# --------------------------------------------------------------------------- all
def run_all(df: pd.DataFrame) -> tuple[dict, dict]:
    nominal = int(df.attrs.get("nominal_ports") or df["station_id"].nunique())
    occ = occupancy_minutes(df)
    cap = capacity_minutes(df, occ.index, nominal)
    metrics = {
        "meta": {
            "source": df.attrs.get("source"),
            "site": df.attrs.get("site"),
            "input_version": str(df["input_version"].iloc[0]),
            "timestamps_minute_floored": bool(df["timestamps_minute_floored"].iloc[0]),
            "has_session_id": bool(df["has_session_id"].iloc[0]),
            "sessions": int(len(df)),
            "first_connection_local": str(df["connect_local"].min()),
            "last_connection_local": str(df["connect_local"].max()),
        },
        "censoring": censoring_metrics(df, occ, cap),
        "idle_occupancy": idle_share_metrics(occ),
        "departure_response": departure_response_test(df),
        "rates": rate_metrics(df),
        "stations": station_availability_metrics(df),
        "arrival_counts": arrival_count_metrics(df),
    }
    idle, idle_table = idle_metrics(df)
    inputs, input_errors = input_metrics(df)
    metrics["idle"] = idle
    metrics["inputs"] = inputs
    tables = {"occupancy": occ, "capacity": cap, "idle_bins": idle_table, "input_errors": input_errors}
    return metrics, tables
