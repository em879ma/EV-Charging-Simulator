"""Build Person C's synthesis table: every variable the simulator needs that ACN-Data lacks.

Run from the ``codespace`` folder:

    python -m unobserved_behavior.build_table                      # raw Caltech download
    python -m unobserved_behavior.build_table --sessions ../path/clean_sessions.csv --out /tmp/c_out
    python -m unobserved_behavior.build_table --timeseries fetch   # re-download the time-series sample

--timeseries auto (default) reuses outputs/timeseries_sample.csv when it exists (offline-safe) and
downloads it otherwise; 'skip' leaves the time-series evidence out and therefore requires --out.

Outputs (``unobserved_behavior/outputs``):
    synthesis_table.csv / .md     the deliverable table (evidence column filled from the data)
    diagnostics_metrics.json      every number quoted in the table
    scenario_parameters.csv       baseline / low / high values for the synthesized parameters
    timeseries_sample.csv         per-session time-series checks (reused unless --timeseries fetch)
    figures/*.png
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from . import catalog, diagnostics, synthesis, timeseries  # noqa: E402
from .data import load_sessions  # noqa: E402

OUT = Path(__file__).resolve().parent / "outputs"


class _Dot(dict):
    """Allow '{censoring.max_concurrent:.0f}' style templates over nested metric dicts."""

    def __getitem__(self, key):
        value = dict.__getitem__(self, key)
        return _Dot(value) if isinstance(value, dict) else value

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


def fill_evidence(template: str, metrics: dict) -> str:
    """Format the primary template; if a metric is missing, format the fallback after '||'."""
    for candidate in [part.strip() for part in template.split("||")]:
        try:
            return candidate.format_map(_Dot(metrics))
        except (KeyError, AttributeError, ValueError, TypeError):
            continue
    return "(metric not computed in this run)"


# --------------------------------------------------------------------------- figures
def make_figures(df: pd.DataFrame, tables: dict, ts_sample: pd.DataFrame | None, out: Path) -> list[str]:
    out.mkdir(parents=True, exist_ok=True)
    names = []

    occ, cap = tables["occupancy"], tables["capacity"]
    daily = pd.DataFrame({"max_plugged": occ["plugged"].groupby(occ.index.date).max(),
                          "in_service": cap["active_month"].groupby(cap.index.date).max()})
    fig, ax = plt.subplots(figsize=(10, 3.8))
    ax.plot(daily.index, daily["max_plugged"], lw=0.8, label="Daily max plugged-in sessions")
    ax.step(daily.index, daily["in_service"], where="post", lw=1.4, label="Stations with sessions that month")
    nominal = int(cap["nominal"].iloc[0])
    near_full_days = int((daily["max_plugged"] >= daily["in_service"] - 2).sum())
    title = ("Occupancy never came within 2 ports of the ports in service, so blocked drivers are unobserved"
             if near_full_days == 0 else f"Occupancy came within 2 ports of capacity on {near_full_days} days")
    ax.axhline(nominal, color="k", ls="--", lw=1, label=f"Nominal ports ({nominal})")
    ax.set(ylabel="Ports", title=title)
    ax.legend(loc="lower left", fontsize=8)
    fig.tight_layout(); fig.savefig(out / "fig1_occupancy_vs_capacity.png", dpi=150); plt.close(fig)
    names.append("fig1_occupancy_vs_capacity.png")

    wk = occ[occ.index.dayofweek < 5]
    by_hour = wk.groupby(wk.index.hour)[["charging", "idle", "unknown"]].mean()
    fig, ax = plt.subplots(figsize=(8, 3.8))
    ax.stackplot(by_hour.index, by_hour["charging"], by_hour["idle"], by_hour["unknown"],
                 labels=["Charging", "Plugged in, charging finished (idle)", "Unknown (no done time)"])
    ax.set(xlabel="Local hour (weekdays)", ylabel="Mean ports occupied", title="Idle occupancy dominates the afternoon")
    ax.legend(loc="upper left", fontsize=8)
    fig.tight_layout(); fig.savefig(out / "fig2_weekday_occupancy_by_hour.png", dpi=150); plt.close(fig)
    names.append("fig2_weekday_occupancy_by_hour.png")

    comp = df[df["done_status"] == "completed"]
    bins = np.arange(0, 301, 5)
    fig, ax = plt.subplots(figsize=(8, 3.8))
    for flag, label in [(True, "With user input"), (False, "Without user input")]:
        ax.hist(comp.loc[comp["has_input"] == flag, "idle_h"] * 60, bins=bins, histtype="step", density=True, label=label)
    ax.set(xlabel="Minutes between charging completion and unplug", ylabel="Density",
           title="Post-charge idle time (completed sessions, first 5 h)")
    ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(out / "fig3_idle_after_completion.png", dpi=150); plt.close(fig)
    names.append("fig3_idle_after_completion.png")

    err = tables["input_errors"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8))
    axes[0].hist(err["dep_err_min"].clip(-600, 600), bins=60)
    axes[0].axvline(0, color="k", lw=1)
    axes[0].set(xlabel="Announced minus actual departure (min, clipped at +/-600)", ylabel="Sessions",
                title="Drivers usually stay longer than announced")
    axes[1].hist(err["energy_ratio"].clip(0, 2), bins=60)
    axes[1].axvline(1, color="k", lw=1)
    axes[1].set(xlabel="Delivered / requested energy (clipped at 2)", title="Requested energy is usually overstated")
    fig.tight_layout(); fig.savefig(out / "fig4_user_input_errors.png", dpi=150); plt.close(fig)
    names.append("fig4_user_input_errors.png")

    random_kw = (ts_sample.loc[ts_sample["purpose"] == "random", "vehicle_kw_when_pilot_ge_30a"].dropna()
                 if ts_sample is not None and "purpose" in ts_sample else pd.Series(dtype=float))
    if len(random_kw):
        fig, ax = plt.subplots(figsize=(7, 3.6))
        ax.hist(random_kw, bins=np.arange(1.5, 7.01, 0.1))
        ax.axvline(32 * 208 / 1000, color="k", ls="--", lw=1, label="Port rating 32 A x 208 V")
        ax.set(xlabel="kW drawn when the scheduler allowed >= 30 A (95th pct per session)", ylabel="Sessions",
               title=f"Vehicle onboard-charger classes (random post-2018-11 sample, n = {len(random_kw)})")
        ax.legend(fontsize=8)
        fig.tight_layout(); fig.savefig(out / "fig5_vehicle_rate_open_pilot.png", dpi=150); plt.close(fig)
        names.append("fig5_vehicle_rate_open_pilot.png")
    return names


# --------------------------------------------------------------------------- table
def build_table(metrics: dict) -> pd.DataFrame:
    rows = []
    for row in catalog.ROWS:
        refs = [catalog.REFERENCES[k]["short"] for k in row["refs"] if k in catalog.REFERENCES]
        rows.append({
            "id": row["id"],
            "category": row["category"],
            "variable": row["variable"],
            "variable_zh": row["variable_zh"],
            "needed_for": row["needed_for"],
            "why_not_in_data": row["why_not_in_data"],
            "evidence_from_acn_data": fill_evidence(row["evidence"], metrics),
            "synthesis_method": row["method"],
            "baseline_value": row["baseline"],
            "sensitivity_range": row["range"],
            "scientific_basis": row["basis"],
            "references": "; ".join(refs),
            "validation_or_check": fill_evidence(row["validation"], metrics),
            "priority": row["priority"],
            "depends_on": row["depends_on"],
        })
    return pd.DataFrame(rows)


def to_markdown(table: pd.DataFrame, metrics: dict) -> str:
    meta = metrics["meta"]
    lines = [
        "# Unobserved-behavior synthesis table (Person C)",
        "",
        f"Data: `{Path(meta['source']).name}`, site {meta['site']}, {meta['sessions']:,} sessions, "
        f"{meta['first_connection_local'][:10]} to {meta['last_connection_local'][:10]}. "
        "Evidence values are computed by `build_table.py`; rerun it when the full download lands.",
        "",
        "Priority: **P0** needed for the baseline model, **P1** needed for a proposal scenario or policy, **P2** extension.",
        "",
    ]
    for category, part in table.groupby("category", sort=False):
        lines += [f"## {category}", ""]
        for _, r in part.iterrows():
            lines += [
                f"### {r['id']} · {r['variable']} ({r['variable_zh']}) · {r['priority']}",
                "",
                f"- **Needed for:** {r['needed_for']}",
                f"- **Why it is not in the data:** {r['why_not_in_data']}",
                f"- **Evidence from ACN-Data:** {r['evidence_from_acn_data']}",
                f"- **Synthesis method:** {r['synthesis_method']}",
                f"- **Baseline / range:** {r['baseline_value']} / {r['sensitivity_range']}",
                f"- **Scientific basis:** {r['scientific_basis']}",
                f"- **References:** {r['references'] or '—'}",
                f"- **Check:** {r['validation_or_check']}",
                f"- **Depends on:** {r['depends_on']}",
                "",
            ]
    lines += ["## References", ""]
    used = sorted({k for row in catalog.ROWS for k in row["refs"] if k in catalog.REFERENCES},
                  key=lambda k: catalog.REFERENCES[k]["full"])
    for key in used:
        ref = catalog.REFERENCES[key]
        lines.append(f"- {ref['full']}" + (f" {ref['url']}" if ref.get("url") else "") + (f" *({ref['note']})*" if ref.get("note") else ""))
    return "\n".join(lines) + "\n"


def to_jsonable(obj):
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (np.floating, float)):
        return None if not np.isfinite(obj) else round(float(obj), 6)
    if isinstance(obj, np.integer):
        return int(obj)
    return obj


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sessions", default=None, help="session file (default: raw_acn/caltech_sessions_PARTIAL.json)")
    parser.add_argument("--site", default="0002")
    parser.add_argument("--timeseries", choices=["auto", "fetch", "skip"], default="auto",
                        help="auto: reuse the saved time-series sample if present, else download")
    parser.add_argument("--timeseries-per-stratum", type=int, default=100)
    parser.add_argument("--timeseries-random", type=int, default=300)
    parser.add_argument("--seed", type=int, default=474)
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args(argv)
    out = Path(args.out)
    if args.timeseries == "skip" and out.resolve() == OUT.resolve():
        parser.error("--timeseries skip would overwrite the committed outputs without time-series evidence; pass --out")
    out.mkdir(parents=True, exist_ok=True)

    df = load_sessions(args.sessions, site=args.site)
    metrics, tables = diagnostics.run_all(df)

    ts_sample = None
    saved = OUT / "timeseries_sample.csv"
    if args.timeseries == "auto" and saved.exists():
        ts_sample = pd.read_csv(saved)
        if "purpose" not in ts_sample or not ts_sample["session_id"].isin(df["session_id"]).all():
            ts_sample = None  # older script version, or a sample drawn from different sessions; refetch
        else:
            print(f"Reusing time-series sample {saved} (use --timeseries fetch to re-download)")
    if ts_sample is None and args.timeseries in ("auto", "fetch"):
        if not df["has_session_id"].all():
            print("No sessionID column: time-series files cannot be matched; skipping the time-series step")
        else:
            _, ts_sample = timeseries.run(df, n_per_stratum=args.timeseries_per_stratum,
                                          n_random=args.timeseries_random, seed=args.seed)
    if ts_sample is not None and not ts_sample.empty:
        metrics["timeseries"] = timeseries.summarize_sample(ts_sample)
        ts_sample.to_csv(out / "timeseries_sample.csv", index=False)
        ts = metrics["timeseries"]
        before = ts.get("interrupted_done_before_unplug__share_current_within5min_of_unplug")
        after = ts.get("interrupted_done_after_unplug__share_current_within5min_of_unplug")
        if before is not None and after is not None:
            idle = metrics["idle"]
            idle["share_interrupted_ts_adjusted"] = (idle["share_interrupted_done_before_unplug"] * before
                                                     + idle["share_interrupted_done_after_unplug"] * after)

    figures = make_figures(df, tables, ts_sample, out / "figures")
    table = build_table(metrics)
    table.to_csv(out / "synthesis_table.csv", index=False, encoding="utf-8-sig")
    (out / "synthesis_table.md").write_text(to_markdown(table, metrics), encoding="utf-8")

    grid = synthesis.sensitivity_grid(responsive_share=max(metrics["departure_response"]["excess_share_all"], 0.0))
    random_kw = (ts_sample.loc[ts_sample["purpose"] == "random", "vehicle_kw_when_pilot_ge_30a"]
                 if ts_sample is not None and not ts_sample.empty else pd.Series(dtype=float))
    if random_kw.notna().sum() >= 20:
        mix = synthesis.VehicleRateMix.from_timeseries(random_kw)
        grid = pd.concat([grid, pd.DataFrame([
            {"row_id": "R12", "parameter": f"vehicle_kw_class_{i + 1}", "baseline": kw, "low": np.nan, "high": np.nan,
             "used_by": f"VehicleRateMix (probability {p})",
             "basis": f"ours: random sample of post-2018-11 ACN sessions, periods with pilot >= 30 A (n = {int(random_kw.notna().sum())})"}
            for i, (kw, p) in enumerate(zip(mix.kw_classes, mix.probs))
        ])], ignore_index=True)
        grid = grid.sort_values("row_id", kind="stable").reset_index(drop=True)
    grid.to_csv(out / "scenario_parameters.csv", index=False)

    (out / "diagnostics_metrics.json").write_text(json.dumps(to_jsonable(metrics), indent=2, default=str), encoding="utf-8")
    print(f"Wrote {len(table)} table rows, {len(figures)} figures and metrics to {out}")


if __name__ == "__main__":
    main()
