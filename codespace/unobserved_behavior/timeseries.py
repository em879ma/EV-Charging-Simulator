"""Check session fields against ACN per-session charging-current time series (sample-based).

Why: the session table has no vehicle maximum charging rate, and ``doneChargingTime`` is close to
the unplug time for about a third of Caltech sessions. The time series (ACN-Data-Static submodule,
fetched per file from GitHub so the 2 GB repository does not have to be cloned) shows when current
actually stopped and how much current each vehicle drew.

Caltech ran adaptive charging, so the observed current is min(scheduler limit, vehicle limit).
Peak observed current is therefore a LOWER bound on the vehicle's own maximum rate.
"""
from __future__ import annotations

import io
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

RAW_BASE = "https://raw.githubusercontent.com/tongxin-li/ACN-Data-Static/main/time%20series%20data/caltech/"
GARAGES = ["California_Garage_01", "California_Garage_02", "N_Wilson_Garage_01", "S_Wilson_Garage_01", "LIGO_01"]
TS_FIRST_DAY = pd.Timestamp("2018-05-01", tz="UTC")  # static time series start here
CHARGING_A = 1.0  # current at or above this counts as charging
CACHE = Path(__file__).resolve().parent / ".cache" / "timeseries"


def ts_filename(session_id: str) -> str:
    """'2_39_78_362_2018-05-01 16:18:53.512595' -> '2-39-78-362-2018-05-01T16-18-53-512595.csv.gz'"""
    station, stamp = session_id.rsplit("_", 1)
    return station.replace("_", "-") + "-" + stamp.replace(" ", "T").replace(":", "-").replace(".", "-") + ".csv.gz"


def fetch(session_id: str, retries: int = 3) -> bytes | None:
    name = ts_filename(session_id)
    cached = CACHE / name
    if cached.exists():
        return cached.read_bytes()
    for garage in GARAGES:
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(RAW_BASE + garage + "/" + name, timeout=30) as response:
                    data = response.read()
                CACHE.mkdir(parents=True, exist_ok=True)
                cached.write_bytes(data)
                return data
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    break  # try next garage
                time.sleep(2 ** attempt)
            except (urllib.error.URLError, TimeoutError):
                time.sleep(2 ** attempt)
    return None


def summarize(raw: bytes, session: pd.Series, voltage: float) -> dict:
    ts = pd.read_csv(io.BytesIO(raw), compression="gzip")
    t = pd.to_datetime(ts.iloc[:, 0], utc=True, format="mixed")
    current = pd.to_numeric(ts["Charging Current (A)"], errors="coerce")
    pilot = pd.to_numeric(ts.get("Actual Pilot (A)"), errors="coerce")
    energy = pd.to_numeric(ts.get("Energy Delivered (kWh)"), errors="coerce")
    state = ts.get("Charging State", pd.Series(dtype=str)).astype(str)

    charging = current >= CHARGING_A
    last_charging = t[charging].max() if charging.any() else pd.NaT
    p95 = float(current[charging].quantile(0.95)) if charging.any() else np.nan
    has_pilot = pilot.notna() & charging
    limited = (current < pilot - 2) & has_pilot
    # When the scheduler allows >= 30 A, the current drawn is set by the vehicle (onboard charger / taper).
    open_pilot = has_pilot & (pilot >= 30)
    p95_open = float(current[open_pilot].quantile(0.95)) if open_pilot.sum() >= 20 else np.nan
    return {
        "open_pilot_samples": int(open_pilot.sum()),
        "vehicle_kw_when_pilot_ge_30a": p95_open * voltage / 1000 if np.isfinite(p95_open) else np.nan,
        "session_id": session["session_id"],
        "stratum": session["stratum"],
        "samples": int(len(ts)),
        "max_current_a": float(current.max()),
        "p95_current_a": p95,
        "vehicle_kw_lower_bound": p95 * voltage / 1000 if np.isfinite(p95) else np.nan,
        "pilot_available_share": float(pilot.notna().mean()),
        "vehicle_limited_share": float(limited.sum() / has_pilot.sum()) if has_pilot.any() else np.nan,
        "adaptive_state_share": float((state == "ADAPTIVE").mean()),
        "ts_energy_kwh": float(energy.max()) if energy.notna().any() else np.nan,
        "session_kwh": float(session["kwh"]),
        "done_minus_last_charging_min": (session["done"] - last_charging).total_seconds() / 60 if pd.notna(last_charging) and pd.notna(session["done"]) else np.nan,
        "disconnect_minus_last_charging_min": (session["disconnect"] - last_charging).total_seconds() / 60 if pd.notna(last_charging) else np.nan,
    }


def label_strata(df: pd.DataFrame) -> pd.DataFrame:
    """Sessions inside the time-series coverage, labeled by completion-status stratum."""
    d = df.loc[df["connect"] >= TS_FIRST_DAY].copy()
    after = d["done"] > d["disconnect"]
    d["stratum"] = np.select(
        [d["done_status"] == "missing", (d["done_status"] == "interrupted") & after, d["done_status"] == "interrupted"],
        ["missing_done", "interrupted_done_after_unplug", "interrupted_done_before_unplug"],
        default="completed",
    )
    return d


PILOT_ERA_START = pd.Timestamp("2018-11-01", tz="UTC")  # pilot signal is recorded from about here on


def draw_samples(df: pd.DataFrame, n_per_stratum: int, n_random: int, seed: int) -> pd.DataFrame:
    """Two samples with different purposes.

    purpose='done_check': n_per_stratum sessions from each completion-status stratum. Used only for
    per-stratum checks of doneChargingTime, which are never pooled across strata.
    purpose='random': a simple random sample of sessions in the pilot era. Used for population-level
    shares (vehicle charging classes, ADAPTIVE state, vehicle-limited samples), which an
    equal-allocation stratified sample would bias toward rare strata.
    """
    d = label_strata(df)
    checks = pd.concat([g.sample(min(n_per_stratum, len(g)), random_state=seed) for _, g in d.groupby("stratum")])
    era = d.loc[d["connect"] >= PILOT_ERA_START]
    random = era.sample(min(n_random, len(era)), random_state=seed + 1)
    return pd.concat([checks.assign(purpose="done_check"), random.assign(purpose="random")])


def summarize_sample(per_session: pd.DataFrame) -> dict:
    """Summary metrics from a per-session table (freshly fetched or re-read from timeseries_sample.csv)."""
    summary = {"fetched": int(per_session["session_id"].nunique())}
    checks = per_session[per_session["purpose"] == "done_check"]
    for stratum, g in checks.groupby("stratum"):
        gap = g["disconnect_minus_last_charging_min"]
        summary[f"{stratum}__n"] = int(len(g))
        summary[f"{stratum}__share_current_stopped_gt5min_before_unplug"] = float((gap > 5).mean())
        summary[f"{stratum}__share_current_within5min_of_unplug"] = float((gap <= 5).mean())
        summary[f"{stratum}__median_min_unplug_after_last_current"] = float(gap.median())

    rand = per_session[per_session["purpose"] == "random"]
    open_kw = rand["vehicle_kw_when_pilot_ge_30a"].dropna()
    has = len(open_kw) > 0
    summary.update({
        "random__n": int(len(rand)),
        "random_population": "sessions connected on or after 2018-11-01 (pilot recorded)",
        "random__share_with_pilot": float((rand["pilot_available_share"] > 0).mean()),
        "random__median_session_adaptive_state_share": float(rand["adaptive_state_share"].median()),
        "random__median_session_vehicle_limited_share": float(rand["vehicle_limited_share"].median()),
        "random__share_peak_current_ge_31a": float((rand["max_current_a"] >= 31).mean()),
        "median_abs_kwh_diff_session_vs_ts": float((per_session["session_kwh"] - per_session["ts_energy_kwh"]).abs().median()),
        "sessions_with_open_pilot": int(len(open_kw)),
        "open_pilot_low_class_kw_median": float(open_kw[open_kw < 4.5].median()) if (open_kw < 4.5).any() else np.nan,
        "open_pilot_high_class_kw_median": float(open_kw[open_kw >= 4.5].median()) if (open_kw >= 4.5).any() else np.nan,
        "open_pilot_share_le_3p8kw": float((open_kw <= 3.8).mean()) if has else np.nan,
        "open_pilot_share_3p8_to_5p5kw": float(((open_kw > 3.8) & (open_kw < 5.5)).mean()) if has else np.nan,
        "open_pilot_share_ge_5p5kw": float((open_kw >= 5.5).mean()) if has else np.nan,
    })
    return summary


def run(df: pd.DataFrame, n_per_stratum: int = 100, n_random: int = 300, voltage: float = 208.0,
        seed: int = 474, workers: int = 8) -> tuple[dict, pd.DataFrame]:
    sample = draw_samples(df, n_per_stratum, n_random, seed)
    with ThreadPoolExecutor(workers) as pool:
        blobs = dict(zip(sample["session_id"].unique(), pool.map(fetch, sample["session_id"].unique())))
    rows = []
    for _, s in sample.iterrows():
        blob = blobs.get(s["session_id"])
        if blob:
            rows.append({**summarize(blob, s, voltage), "purpose": s["purpose"]})
    per_session = pd.DataFrame(rows)
    if per_session.empty:
        return {"fetched": 0}, per_session
    summary = summarize_sample(per_session)
    summary.update({"requested": int(sample["session_id"].nunique()), "voltage_assumed": voltage})
    return summary, per_session
