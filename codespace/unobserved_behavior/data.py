"""Load ACN charging sessions into one normalized table for the unobserved-behavior analysis.

Input priority
--------------
1. A cleaned session table from Person A (``clean_sessions.parquet`` or ``.csv``), passed with ``path=``.
2. The raw API export ``raw_acn/caltech_sessions.json`` (default once the complete download is in
   the repo; until then ``raw_acn/caltech_sessions_PARTIAL.json``).

The raw API export keeps every user-input version. Connection-time decisions (for example an
earliest-departure-first scheduler) may only use the FIRST version, so both the first and the
latest version are extracted.

Nothing is dropped here. Anomalies are flagged so each diagnostic can decide what to exclude.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPLETE_RAW = REPO_ROOT / "raw_acn" / "caltech_sessions.json"
DEFAULT_RAW = _COMPLETE_RAW if _COMPLETE_RAW.exists() else REPO_ROOT / "raw_acn" / "caltech_sessions_PARTIAL.json"
LOCAL_TZ = "America/Los_Angeles"

# Caltech ACN (siteID 0002, cluster 0039): 54 EVSEs appear in the session records.
NOMINAL_PORTS = {"0002": 54, "0001": 52, "0019": 8}
# Site 0002 spans several Caltech garages in ACN-Data-Static (California Garage 01 = cluster 39 with
# station IDs 2-39-*, California Garage 02 = 2-6-*, N/S Wilson = 2-137-*/2-105-*, LIGO = 2-154-*).
# The modeled facility is the 54-EVSE California Garage 01, so Caltech sessions are restricted to it.
GARAGE_CLUSTER = {"0002": "0039"}
SITE_NAMES = {"0002": "Caltech", "0001": "JPL", "0019": "Office 1"}

# Structural break in the Caltech data: from 2018-11-01 the share of sessions with user input jumps
# from about 20% to about 85% within a week, and weekday plug-ins fall by roughly half. On that date
# Caltech began charging $0.12/kWh and terminating unclaimed sessions after 30 min (Lee et al. 2019).
# The break is site-specific; other sites get a single regime "all" until checked.
REGIME_BREAK_LOCAL = pd.Timestamp("2018-11-01", tz=LOCAL_TZ)
REGIME_BREAKS = {"0002": REGIME_BREAK_LOCAL}

RFC1123 = "%a, %d %b %Y %H:%M:%S GMT"
INTERRUPT_TOLERANCE_MIN = 1.0  # done within 1 min of unplugging = charging still running at departure


def _to_utc(values: pd.Series) -> pd.Series:
    """Parse API timestamps ("Wed, 25 Apr 2018 11:08:04 GMT") or ISO strings to UTC."""
    if pd.api.types.is_datetime64_any_dtype(values):
        out = pd.to_datetime(values, utc=True)
    else:
        out = pd.to_datetime(values, format=RFC1123, utc=True, errors="coerce")
        retry = out.isna() & values.notna()
        if retry.any():
            out.loc[retry] = pd.to_datetime(values[retry], utc=True, errors="coerce", format="mixed")
    return out


def _parse_versions(value) -> list[dict]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return []
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        value = json.loads(value)
    return list(value) if isinstance(value, (list, tuple)) else []


def _extract_user_inputs(raw: pd.Series) -> pd.DataFrame:
    """Return first- and latest-version request fields for every session."""
    records = []
    for versions in raw.apply(_parse_versions):
        if not versions:
            records.append({"n_input_versions": 0})
            continue
        stamped = [(pd.to_datetime(v.get("modifiedAt"), utc=True, errors="coerce", format="mixed"), v)
                   for v in versions]
        stamped.sort(key=lambda item: (pd.Timestamp.min.tz_localize("UTC") if pd.isna(item[0]) else item[0]))
        first, last = stamped[0][1], stamped[-1][1]
        records.append({
            "n_input_versions": len(versions),
            "kwh_req_first": first.get("kWhRequested"),
            "dep_req_first": first.get("requestedDeparture"),
            "min_avail_first": first.get("minutesAvailable"),
            "kwh_req_last": last.get("kWhRequested"),
            "dep_req_last": last.get("requestedDeparture"),
            "input_user_id": first.get("userID"),
        })
    out = pd.DataFrame.from_records(records, index=raw.index)
    for col in ["kwh_req_first", "min_avail_first", "kwh_req_last"]:
        out[col] = pd.to_numeric(out.get(col), errors="coerce")
    for col in ["dep_req_first", "dep_req_last"]:
        out[col] = _to_utc(out[col]) if col in out else pd.NaT
    if "input_user_id" in out:
        out["input_user_id"] = out["input_user_id"].astype("string")
    return out


def _read(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return pd.DataFrame(json.loads(path.read_text(encoding="utf-8")))
    if suffix == ".parquet":
        try:
            return pd.read_parquet(path)
        except ImportError as exc:
            raise ImportError("Reading .parquet needs pyarrow (pip install pyarrow); or export the table as .csv") from exc
    if suffix == ".csv":
        return pd.read_csv(path, dtype=str, low_memory=False)
    raise ValueError(f"Unsupported session file: {path}")


def _first_present(raw: pd.DataFrame, names: list[str]):
    for name in names:
        if name in raw:
            return raw[name]
    return None


def _inputs_from_clean_table(raw: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """User-input fields from a cleaned table that no longer holds the raw userInputs JSON.

    First-version columns are used when present (see README 'Input contract'); otherwise the
    table's single version (usually the latest) is used and labeled as such.
    """
    kwh_first = _first_present(raw, ["kwh_req_first", "kWhRequestedFirst", "kWhRequested_first"])
    dep_first = _first_present(raw, ["dep_req_first", "requestedDepartureFirst", "requestedDeparture_first"])
    version = "first" if kwh_first is not None and dep_first is not None else "latest"
    kwh = kwh_first if version == "first" else raw.get("kWhRequested")
    dep = dep_first if version == "first" else raw.get("requestedDeparture")
    inputs = pd.DataFrame(index=raw.index)
    inputs["kwh_req_first"] = pd.to_numeric(kwh, errors="coerce")
    inputs["dep_req_first"] = _to_utc(dep) if dep is not None else pd.NaT
    minutes = _first_present(raw, ["min_avail_first", "minutesAvailableFirst", "minutesAvailable"])
    inputs["min_avail_first"] = pd.to_numeric(minutes, errors="coerce") if minutes is not None else np.nan
    inputs["kwh_req_last"] = pd.to_numeric(raw.get("kWhRequested", kwh), errors="coerce")
    last_dep = raw.get("requestedDeparture", dep)
    inputs["dep_req_last"] = _to_utc(last_dep) if last_dep is not None else pd.NaT
    counts = _first_present(raw, ["userInputVersionCount", "n_input_versions"])
    inputs["n_input_versions"] = (pd.to_numeric(counts, errors="coerce").fillna(0).astype(int)
                                  if counts is not None else inputs["kwh_req_first"].notna().astype(int))
    user = _first_present(raw, ["inputUserID", "userID"])
    inputs["input_user_id"] = user.astype("string") if user is not None else pd.NA
    return inputs, version


def load_sessions(path: str | Path | None = None, site: str = "0002", cluster: str | None = "default") -> pd.DataFrame:
    """Load and normalize sessions for one site. See module docstring for input priority.

    cluster: keep only this cluster (garage). "default" uses GARAGE_CLUSTER (California Garage 01
    for Caltech); None keeps every cluster of the site.
    """
    path = Path(path) if path else DEFAULT_RAW
    raw = _read(path)

    df = pd.DataFrame(index=raw.index)
    # sessionID maps a session to its time-series file; cleaned tables may have dropped it
    session_key = _first_present(raw, ["sessionID", "_id"])
    if session_key is None:
        raise KeyError("session table needs a 'sessionID' (or '_id') column")
    df["session_id"] = session_key.astype("string")
    df["has_session_id"] = "sessionID" in raw
    df["site_id"] = raw["siteID"].astype("string").str.zfill(4)
    df["station_id"] = raw["stationID"].astype("string")
    if "clusterID" in raw:
        df["cluster_id"] = raw["clusterID"].astype("string").str.zfill(4)
    else:  # cleaned tables may drop clusterID; station IDs read <site>-<cluster>-...
        df["cluster_id"] = df["station_id"].str.split("-").str[1].str.zfill(4)
    df["user_id"] = raw["userID"].astype("string") if "userID" in raw else pd.NA
    df["connect"] = _to_utc(raw["connectionTime"])
    df["disconnect"] = _to_utc(raw["disconnectTime"])
    df["done"] = _to_utc(raw["doneChargingTime"])
    df["kwh"] = pd.to_numeric(raw["kWhDelivered"], errors="coerce")

    if "userInputs" in raw:
        inputs, version = _extract_user_inputs(raw["userInputs"]), "first"
    elif "kWhRequested" in raw or "kwh_req_first" in raw:
        inputs, version = _inputs_from_clean_table(raw)
    else:
        inputs, version = pd.DataFrame({"n_input_versions": 0}, index=raw.index), "none"
    df = df.join(inputs)
    df["has_input"] = df["n_input_versions"].fillna(0).astype(int) > 0
    df["input_version"] = version  # a column, not attrs: attrs do not survive joins and filters

    df = df.loc[df["site_id"] == site].copy()
    cluster = GARAGE_CLUSTER.get(site) if cluster == "default" else cluster
    if cluster is not None:
        df = df.loc[df["cluster_id"] == cluster].copy()
    df["valid_interval"] = df["disconnect"] > df["connect"]
    # Minute-floored timestamps (e.g. a cleaned table) blur the 1-min interrupted cutoff; record it.
    seconds = pd.concat([df["connect"].dt.second, df["disconnect"].dt.second])
    df["timestamps_minute_floored"] = bool((seconds == 0).all())

    local = df["connect"].dt.tz_convert(LOCAL_TZ)
    df["connect_local"] = local
    df["date_local"] = local.dt.date
    df["month_local"] = local.dt.strftime("%Y-%m")
    df["weekday"] = local.dt.dayofweek
    df["is_weekend"] = df["weekday"] >= 5
    df["arrival_hour"] = local.dt.hour + local.dt.minute / 60
    brk = REGIME_BREAKS.get(site)
    df["regime"] = np.where(local < brk, f"pre_{brk:%Y-%m}", f"post_{brk:%Y-%m}") if brk is not None else "all"

    df["dwell_h"] = (df["disconnect"] - df["connect"]).dt.total_seconds() / 3600
    gap_min = (df["disconnect"] - df["done"]).dt.total_seconds() / 60
    df["done_status"] = np.select(
        [df["done"].isna(), gap_min <= INTERRUPT_TOLERANCE_MIN],
        ["missing", "interrupted"],
        default="completed",
    )
    done_clipped = df["done"].where(df["done"] <= df["disconnect"], df["disconnect"])
    done_clipped = done_clipped.where(done_clipped >= df["connect"], df["connect"])
    df["charge_h"] = (done_clipped - df["connect"]).dt.total_seconds() / 3600
    df["idle_h"] = (df["disconnect"] - done_clipped).dt.total_seconds() / 3600
    df.loc[df["done_status"] == "missing", ["charge_h", "idle_h"]] = np.nan

    df.attrs["source"] = str(path)
    df.attrs["site"] = site
    df.attrs["nominal_ports"] = NOMINAL_PORTS.get(site)
    df.attrs["cluster"] = cluster
    return df.sort_values("connect").reset_index(drop=True)
