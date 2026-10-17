"""Reproducible session-mark benchmark; does not estimate latent arrivals/queues.

Run from any directory: python codespace/person_b_models.py --repeats 10
Dependencies: numpy, pandas, scipy, matplotlib (pyarrow for parquet input).
"""

from pathlib import Path
import argparse
import json
import os

os.environ.setdefault("MPLCONFIGDIR", "/tmp/ev-person-b-matplotlib")
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
FEATURES = ["arrival", "dwell", "energy"]
METHODS = [
    "independent",
    "independent_empirical",
    "conditional_pairs",
    "circular_neighbors",
    "joint_kde",
    "gaussian_copula",
    "session_bootstrap",
    "calendar_bootstrap",
]
LABELS = {
    "independent": "Independent parametric",
    "independent_empirical": "Independent empirical",
    "conditional_pairs": "Conditional pairs",
    "circular_neighbors": "Circular neighbors",
    "joint_kde": "Joint KDE",
    "gaussian_copula": "Gaussian copula",
    "session_bootstrap": "Session bootstrap",
    "calendar_bootstrap": "Calendar bootstrap",
}


def coverage_audit(output):
    """Inventory only: do not decompress the static time-series dataset."""
    static = ROOT / "ACN-Data-Static/time series data"
    counts = {
        str(p.relative_to(static)): sum(1 for _ in p.glob("*.csv.gz"))
        for p in sorted(static.glob("*/*"))
        if p.is_dir()
    }
    status_path = ROOT / "raw_acn/download_status.json"
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    result = {
        "static_time_series_counts": counts,
        "static_total_files": sum(counts.values()),
        "api_snapshot_status": status,
        "interpretation": "Static file counts do not prove API-session completeness or byte integrity. These are distinct snapshots. Model reads entire specified session table, not static time series.",
    }
    (output / "data_coverage_audit.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return result


def load_sessions(path, site="0002"):
    """Minimal B-side adapter, not a replacement for Person A's cleaning.

    Canonical inputs: siteID, connectionTime, disconnectTime, kWhDelivered.
    Raw UTC timestamps are authoritative; no tail clipping or imputation.
    """
    path = Path(path)
    if path.suffix == ".parquet":
        frame = pd.read_parquet(path)
    else:
        frame = pd.read_csv(
            path, dtype={"siteID": "string", "_id": "string"}, low_memory=False
        )
    required = {"siteID", "connectionTime", "disconnectTime", "kWhDelivered"}
    if required - set(frame):
        raise ValueError(f"Input missing columns: {sorted(required - set(frame))}")
    frame = frame.loc[frame.siteID.astype("string").str.zfill(4) == site].copy()
    before = len(frame)
    duplicate = (
        (frame["_id"].notna() & frame["_id"].duplicated(keep="first"))
        if "_id" in frame
        else pd.Series(False, index=frame.index)
    )
    connected = pd.to_datetime(
        frame.connectionTime, utc=True, errors="coerce", format="mixed"
    )
    disconnected = pd.to_datetime(
        frame.disconnectTime, utc=True, errors="coerce", format="mixed"
    )
    local = connected.dt.tz_convert("America/Los_Angeles")
    result = pd.DataFrame(
        {
            "connection": connected,
            "date": local.dt.normalize().dt.tz_localize(None),
            "arrival": local.dt.hour + local.dt.minute / 60 + local.dt.second / 3600,
            "dwell": (disconnected - connected).dt.total_seconds() / 3600,
            "energy": pd.to_numeric(frame.kWhDelivered, errors="coerce"),
        }
    )
    result["dayType"] = np.where(local.dt.dayofweek >= 5, "Weekend", "Weekday")
    finite = np.isfinite(result[FEATURES]).all(axis=1)
    valid = finite & result.dwell.gt(0) & result.energy.gt(0) & ~duplicate
    audit = {
        "input": str(path.resolve()),
        "siteID": site,
        "site_rows": before,
        "duplicate_id_rows": int(duplicate.sum()),
        "missing_or_nonfinite_rows": int((~finite).sum()),
        "nonpositive_dwell_rows": int(result.dwell.le(0).sum()),
        "nonpositive_energy_rows": int(result.energy.le(0).sum()),
        "retained_rows": int(valid.sum()),
        "note": "Exclusion reasons may overlap. Positive-session target; no tail trimming.",
    }
    return result.loc[valid].sort_values("connection").reset_index(drop=True), audit


def chronological_split(frame):
    dates = np.sort(frame.date.unique())
    if len(dates) < 15:
        raise ValueError(
            "At least 15 observed dates required for chronological evaluation."
        )
    a, b = int(len(dates) * 0.6), int(len(dates) * 0.8)
    partitions = [
        frame.loc[frame.date.isin(d)].copy() for d in (dates[:a], dates[a:b], dates[b:])
    ]
    for name, part in zip(["train", "validation", "test"], partitions):
        if len(part) < 30:
            raise ValueError(f"Insufficient {name} sessions: {len(part)}")
    return partitions


class MarkModel:
    """All methods condition on weekday/weekend; sparse groups pool to training data.

    Bootstrap here resamples whole SESSION tuples, not days/users. No claim of
    preserving between-session dependence. KDE uses circular arrival embedding.
    """

    def __init__(self, method, bins=4, bandwidth=1.0, neighbors=200, half_life=90):
        self.method, self.bins, self.bandwidth = method, bins, bandwidth
        self.neighbors, self.half_life = neighbors, half_life
        if method not in METHODS:
            raise ValueError(method)

    def fit(self, train):
        self.groups = {}
        for day_type in ["Weekday", "Weekend"]:
            group = train.loc[train.dayType == day_type]
            self.groups[day_type] = self._fit_group(
                group if len(group) >= 30 else train
            )
        return self

    def _fit_group(self, group):
        values = group[FEATURES].to_numpy(float)
        data = {
            "values": values,
            "dates": group.date.to_numpy(),
            "months": group.date.dt.month.to_numpy(),
        }
        if self.method == "independent":
            fits = []
            for column in [1, 2]:
                candidates = []
                for name, dist in [
                    ("gamma", stats.gamma),
                    ("lognormal", stats.lognorm),
                    ("weibull", stats.weibull_min),
                ]:
                    params = dist.fit(values[:, column], floc=0)
                    aic = 4 - 2 * dist.logpdf(values[:, column], *params).sum()
                    candidates.append((aic, name, dist, params))
                best = min(candidates, key=lambda x: x[0])
                fits.append(best)
            data["fits"] = fits
        elif self.method == "conditional_pairs":
            data["edges"] = np.unique(
                np.quantile(values[:, 0], np.linspace(0, 1, self.bins + 1))
            )
            data["labels"] = np.searchsorted(
                data["edges"][1:-1], values[:, 0], side="right"
            )
        elif self.method == "joint_kde":
            angle = values[:, 0] * 2 * np.pi / 24
            transformed = np.column_stack(
                [np.sin(angle), np.cos(angle), np.log(values[:, 1:])]
            )
            # Project out deterministic/constant dimensions instead of adding artificial noise.
            center = transformed.mean(axis=0)
            eigenvalues, basis = np.linalg.eigh(np.cov(transformed, rowvar=False))
            basis = basis[:, eigenvalues > max(eigenvalues.max() * 1e-10, 1e-12)]
            if basis.shape[1] == 0:
                data["kde"] = None
                return data
            projected = (transformed - center) @ basis
            kde = stats.gaussian_kde(projected.T)
            kde.set_bandwidth(kde.factor * self.bandwidth)
            data["kde"] = kde
            data["kde_center"], data["kde_basis"] = center, basis
        elif self.method == "gaussian_copula":
            # Empirical margins with Gaussian rank dependence; tails remain within training range.
            z = np.column_stack(
                [
                    stats.norm.ppf((stats.rankdata(values[:, j]) - 0.5) / len(values))
                    for j in range(3)
                ]
            )
            corr = np.nan_to_num(np.corrcoef(z, rowvar=False))
            np.fill_diagonal(corr, 1)
            eigenvalues, eigenvectors = np.linalg.eigh(corr)
            corr = (eigenvectors * np.maximum(eigenvalues, 1e-6)) @ eigenvectors.T
            scale = np.sqrt(np.diag(corr))
            data["correlation"] = corr / np.outer(scale, scale)
        return data

    def sample(self, day_type, count, rng, date=None):
        data = self.groups[day_type]
        values = data["values"]
        if count == 0:
            return np.empty((0, 3))
        if self.method == "session_bootstrap":
            return values[rng.integers(len(values), size=count)].copy()
        if self.method == "calendar_bootstrap":
            if date is None:
                raise ValueError("calendar_bootstrap needs a target date")
            age = (np.datetime64(date) - data["dates"]) / np.timedelta64(1, "D")
            if np.any(age <= 0):
                raise ValueError("All fitted records must precede the target date")
            month_distance = np.abs(data["months"] - pd.Timestamp(date).month)
            month_distance = np.minimum(month_distance, 12 - month_distance)
            log_weights = -0.5 * (month_distance / 3.0) ** 2
            if self.half_life is not None:
                log_weights -= np.log(2) * age / self.half_life
            weights = np.exp(log_weights - log_weights.max())
            weights /= weights.sum()
            return values[rng.choice(len(values), size=count, p=weights)].copy()
        if self.method == "independent_empirical":
            return np.column_stack(
                [rng.choice(values[:, j], size=count) for j in range(3)]
            )
        if self.method == "circular_neighbors":
            arrival = rng.choice(values[:, 0], size=count)
            result = np.empty((count, 3))
            result[:, 0] = arrival
            k = min(self.neighbors, len(values))
            for i, hour in enumerate(arrival):
                distance = np.abs(values[:, 0] - hour)
                distance = np.minimum(distance, 24 - distance)
                nearest = np.argpartition(distance, k - 1)[:k]
                result[i, 1:] = values[rng.choice(nearest), 1:]
            return result
        if self.method == "independent":
            arrival = rng.choice(values[:, 0], size=count)
            other = [
                dist.rvs(*params, size=count, random_state=rng)
                for _, _, dist, params in data["fits"]
            ]
            return np.column_stack([arrival, *other])
        if self.method == "conditional_pairs":
            arrival = rng.choice(values[:, 0], size=count)
            labels = np.searchsorted(data["edges"][1:-1], arrival, side="right")
            generated = np.empty((count, 3))
            generated[:, 0] = arrival
            for label in np.unique(labels):
                target = labels == label
                pool = values[data["labels"] == label]
                if len(pool) < 20:
                    pool = values
                generated[target, 1:] = pool[
                    rng.integers(len(pool), size=target.sum()), 1:
                ]
            return generated
        if self.method == "joint_kde":
            if data["kde"] is None:
                return np.repeat(values[:1], count, axis=0)
            draw = (
                data["kde"].resample(count, seed=rng).T @ data["kde_basis"].T
                + data["kde_center"]
            )
            arrival = (
                np.mod(np.arctan2(draw[:, 0], draw[:, 1]), 2 * np.pi) * 24 / (2 * np.pi)
            )
            return np.column_stack([arrival, np.exp(draw[:, 2:])])
        z = rng.multivariate_normal(np.zeros(3), data["correlation"], size=count)
        u = stats.norm.cdf(z)
        return np.column_stack([np.quantile(values[:, j], u[:, j]) for j in range(3)])


def generate(model, reference, seed):
    """Oracle date counts isolate mark quality; not a forecast of daily arrivals."""
    rng = np.random.default_rng(seed)
    parts = []
    for date, group in reference.groupby("date", sort=True):
        day_type = group.dayType.iloc[0]
        sampled = pd.DataFrame(
            model.sample(day_type, len(group), rng, date=date), columns=FEATURES
        )
        sampled["date"], sampled["dayType"] = date, day_type
        # Absolute UTC departures preserve duration over DST changes.
        local_naive = pd.Timestamp(date) + pd.to_timedelta(sampled.arrival, unit="h")
        sampled["connection"] = local_naive.dt.tz_localize(
            "America/Los_Angeles", ambiguous=False, nonexistent="shift_forward"
        ).dt.tz_convert("UTC")
        # DST nonexistent times are shifted, so update the stored clock feature as well.
        actual_local = sampled.connection.dt.tz_convert("America/Los_Angeles")
        sampled["arrival"] = (
            actual_local.dt.hour
            + actual_local.dt.minute / 60
            + actual_local.dt.second / 3600
        )
        sampled["departure"] = sampled.connection + pd.to_timedelta(
            sampled.dwell, unit="h"
        )
        parts.append(sampled)
    return pd.concat(parts, ignore_index=True)


def occupancy(frame, grid):
    start = frame.connection.dt.as_unit("ns").astype("int64").to_numpy()
    end = (
        (frame.connection + pd.to_timedelta(frame.dwell, unit="h"))
        .dt.as_unit("ns")
        .astype("int64")
        .to_numpy()
    )
    times = grid.as_unit("ns").astype("int64").to_numpy()
    return np.searchsorted(np.sort(start), times, side="right") - np.searchsorted(
        np.sort(end), times, side="right"
    )


def evaluate(real, simulated, with_occupancy=True):
    metrics = {}
    for feature in FEATURES:
        metrics[f"ks_{feature}"] = stats.ks_2samp(
            real[feature], simulated[feature]
        ).statistic
        metrics[f"wasserstein_{feature}"] = stats.wasserstein_distance(
            real[feature], simulated[feature]
        )
        for q in [0.5, 0.9, 0.95]:
            metrics[f"q{int(q*100)}_error_{feature}"] = abs(
                real[feature].quantile(q) - simulated[feature].quantile(q)
            )
    for x, y in [("arrival", "dwell"), ("dwell", "energy"), ("arrival", "energy")]:
        r, s = real[x].corr(real[y], method="spearman"), simulated[x].corr(
            simulated[y], method="spearman"
        )
        metrics[f"corr_error_{x}_{y}"] = abs(r - s)
    # Fixed clock bins assess non-monotone structure that one correlation can hide.
    r_bins = real.arrival.floordiv(4).astype(int)
    s_bins = simulated.arrival.floordiv(4).astype(int)
    errors = []
    for bin_id in range(6):
        a, b = real.loc[r_bins == bin_id], simulated.loc[s_bins == bin_id]
        if len(a) >= 20 and len(b) >= 20:
            for variable in ["dwell", "energy"]:
                errors.append(stats.ks_2samp(a[variable], b[variable]).statistic)
    metrics["conditional_ks"] = float(np.mean(errors)) if errors else np.nan
    for day_type in real.dayType.unique():
        a, b = (
            real.loc[real.dayType == day_type],
            simulated.loc[simulated.dayType == day_type],
        )
        for feature in FEATURES:
            metrics[f"{day_type}_ks_{feature}"] = stats.ks_2samp(
                a[feature], b[feature]
            ).statistic
    # All components bounded [0,1]; selection is an explicit heuristic, not a universal score.
    metrics["selection_score"] = np.mean(
        [metrics[f"ks_{f}"] for f in FEATURES]
        + [
            metrics[f"corr_error_{x}_{y}"] / 2
            for x, y in [
                ("arrival", "dwell"),
                ("dwell", "energy"),
                ("arrival", "energy"),
            ]
        ]
    )
    invalid = (
        ~np.isfinite(simulated[FEATURES]).all(axis=1)
        | simulated.dwell.le(0)
        | simulated.energy.le(0)
        | ~simulated.arrival.between(0, 24, inclusive="left")
    )
    metrics["invalid_fraction"] = invalid.mean()
    if with_occupancy:
        first, last = real.date.min(), real.date.max() + pd.Timedelta(days=1)
        grid = pd.date_range(
            first.tz_localize("America/Los_Angeles"),
            last.tz_localize("America/Los_Angeles"),
            freq="15min",
            inclusive="left",
        ).tz_convert("UTC")
        a, b = occupancy(real, grid), occupancy(simulated, grid)
        # Exclude first seven days: no pre-test sessions supplied. Long dwell >7d still a limitation.
        observed = (
            grid.tz_convert("America/Los_Angeles")
            .normalize()
            .tz_localize(None)
            .isin(real.date.unique())
        )
        mask = (grid >= grid[0] + pd.Timedelta(days=7)) & observed
        if not mask.any():
            mask = observed
        metrics["occupancy_mae"] = np.abs(a[mask] - b[mask]).mean()
        metrics["occupancy_peak_error"] = float(abs(a[mask].max() - b[mask].max()))
    return metrics


def tune(train, validation):
    dates = np.sort(train.date.unique())
    earlier = dates[: int(len(dates) * 2 / 3)]
    folds = [
        (train.loc[train.date.isin(earlier)], train.loc[~train.date.isin(earlier)]),
        (train, validation),
    ]
    rows, choices = [], {}
    for method in METHODS:
        configs = (
            [{"bins": n} for n in [2, 4, 6]]
            if method == "conditional_pairs"
            else (
                [{"bandwidth": b} for b in [0.5, 1.0, 1.5]]
                if method == "joint_kde"
                else (
                    [{"neighbors": k} for k in [50, 200, 500]]
                    if method == "circular_neighbors"
                    else (
                        [{"half_life": h} for h in [30, 90, 180, None]]
                        if method == "calendar_bootstrap"
                        else [{}]
                    )
                )
            )
        )
        candidates = []
        for config in configs:
            fold_scores = []
            for fitting, held_out in folds:
                model = MarkModel(method, **config).fit(fitting)
                fold_scores.append(
                    np.mean(
                        [
                            evaluate(held_out, generate(model, held_out, seed), False)[
                                "selection_score"
                            ]
                            for seed in [101, 102, 103]
                        ]
                    )
                )
            score = float(np.mean(fold_scores))
            rows.append(
                {
                    "method": method,
                    "config": json.dumps(config),
                    "validation_score": score,
                }
            )
            rows[-1].update({f"fold_{i+1}_score": s for i, s in enumerate(fold_scores)})
            candidates.append((score, config))
        choices[method] = min(candidates, key=lambda x: x[0])[1]
    return choices, pd.DataFrame(rows)


def plot_comparison(real, examples, output):
    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    for axis, feature in zip(axes, FEATURES):
        for label, frame in {"Held-out real": real, **examples}.items():
            values = np.sort(frame[feature].to_numpy())
            axis.plot(
                values,
                np.arange(1, len(values) + 1) / len(values),
                label=label,
                alpha=0.8,
            )
        axis.set(
            xlabel={
                "arrival": "Local plug-in hour",
                "dwell": "Dwell (hours)",
                "energy": "Delivered energy (kWh)",
            }[feature],
            ylabel="ECDF",
        )
        axis.set_xlim(0, 24 if feature == "arrival" else real[feature].quantile(0.99))
    axes[0].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(output / "marginals.png", dpi=160)
    plt.close(fig)
    for x, y, filename in [
        ("arrival", "dwell", "joint_distributions.png"),
        ("dwell", "energy", "joint_energy.png"),
    ]:
        fig, axes = plt.subplots(
            3, 3, figsize=(12, 10), sharex=True, sharey=True, layout="constrained"
        )
        artists = []
        for axis, (label, frame) in zip(
            axes.flat, {"Held-out real": real, **examples}.items()
        ):
            artist = axis.hexbin(
                frame[x],
                frame[y],
                gridsize=25,
                mincnt=1,
                extent=(
                    0,
                    24 if x == "arrival" else real[x].quantile(0.99),
                    0,
                    real[y].quantile(0.99),
                ),
            )
            artists.append(artist)
            axis.set(xlabel=x, ylabel=y, title=LABELS.get(label, label))
        fig.suptitle(f"{x} and {y}: display limits only; all tails included in metrics")
        color_scale = LogNorm(vmin=1, vmax=max(max(a.get_array()) for a in artists))
        for artist in artists:
            artist.set_norm(color_scale)
        fig.colorbar(
            artists[0],
            ax=list(axes.flat),
            label="Sessions per hexagon (shared log scale)",
            shrink=0.75,
        )
        fig.savefig(output / filename, dpi=150)
        plt.close(fig)
    start = real.date.min() + pd.Timedelta(days=7)
    if start > real.date.max():
        start = real.date.min()
    end = min(start + pd.Timedelta(days=7), real.date.max() + pd.Timedelta(days=1))
    grid = pd.date_range(
        start.tz_localize("America/Los_Angeles"),
        end.tz_localize("America/Los_Angeles"),
        freq="15min",
    ).tz_convert("UTC")
    fig, axis = plt.subplots(figsize=(14, 4))
    for label, frame in {"Held-out real": real, **examples}.items():
        axis.plot(
            grid.tz_convert("America/Los_Angeles"),
            occupancy(frame, grid),
            label=label,
            alpha=0.7,
        )
    axis.set(
        ylabel="Simultaneously plugged-in vehicles",
        title="Oracle daily counts; occupancy is NOT queue length or power",
    )
    axis.legend(fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(output / "occupancy.png", dpi=160)
    plt.close(fig)


def run(input_path=None, output=None, site="0002", repeats=10):
    if repeats < 2:
        raise ValueError("Use at least two random seeds.")
    if input_path is None:
        candidates = [
            ROOT / "codespace/clean_sessions.parquet",
            ROOT / "clean_sessions.parquet",
            ROOT / "raw_acn/all_sessions_PARTIAL.csv",
        ]
        input_path = next(p for p in candidates if p.exists())
    output = Path(output or ROOT / "codespace/person_b_results")
    output.mkdir(parents=True, exist_ok=True)
    coverage = coverage_audit(output)
    frame, audit = load_sessions(input_path, site)
    train, validation, test = chronological_split(frame)
    choices, validation_table = tune(train, validation)
    recommended = validation_table.groupby("method").validation_score.min().idxmin()
    # Selected configurations fixed before opening test; refit using train+validation.
    fitting = pd.concat([train, validation], ignore_index=True)
    rows, examples = [], {}
    for method in METHODS:
        model = MarkModel(method, **choices[method]).fit(fitting)
        for seed in range(200, 200 + repeats):
            simulated = generate(model, test, seed)
            rows.append({"method": method, "seed": seed, **evaluate(test, simulated)})
            if seed == 200:
                examples[method] = simulated
        print(f"Completed {method}", flush=True)
    scores = pd.DataFrame(rows)
    summary = scores.drop(columns="seed").groupby("method").agg(["mean", "std"])
    summary.columns = [f"{a}_{b}" for a, b in summary.columns]
    summary = summary.sort_values("selection_score_mean")
    splits = {
        name: {
            "rows": len(part),
            "observed_dates": part.date.nunique(),
            "first_date": str(part.date.min().date()),
            "last_date": str(part.date.max().date()),
            "day_type_counts": part.dayType.value_counts().to_dict(),
        }
        for name, part in zip(
            ["train", "validation", "test"], [train, validation, test]
        )
    }
    import scipy

    provenance = {
        "audit": audit,
        "splits": splits,
        "selected_configurations": choices,
        "seeds": list(range(200, 200 + repeats)),
        "daily_count_mode": "oracle_test_counts",
        "refit": "train + validation",
        "recommended_by_validation": recommended,
        "validation": "Two expanding-window folds, equal-weight scores, three seeds per fold",
        "evaluation_status": "Exploratory: this holdout was inspected in the previous development round; reserve fresh data for final claims",
        "versions": {
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scipy": scipy.__version__,
            "matplotlib": matplotlib.__version__,
        },
        "target": "positive dwell and delivered energy; observed plug-in sessions",
    }
    (output / "run_metadata.json").write_text(
        json.dumps(provenance, indent=2), encoding="utf-8"
    )
    scores.to_csv(output / "metrics_by_seed.csv", index=False)
    summary.to_csv(output / "model_comparison.csv")
    validation_table.to_csv(output / "validation_scores.csv", index=False)
    example = examples[recommended]
    example.to_parquet(output / "synthetic_sessions_example.parquet", index=False)
    plot_comparison(test, examples, output)
    compact = summary[
        [
            "selection_score_mean",
            "selection_score_std",
            "conditional_ks_mean",
            "occupancy_mae_mean",
            "invalid_fraction_mean",
        ]
    ].rename(
        columns={
            "selection_score_mean": "Score",
            "selection_score_std": "Seed SD",
            "conditional_ks_mean": "Conditional KS",
            "occupancy_mae_mean": "Occupancy MAE",
            "invalid_fraction_mean": "Invalid share",
        }
    )
    table = (
        "| Method | "
        + " | ".join(compact.columns)
        + " |\n|---|"
        + "---:|" * len(compact.columns)
        + "\n"
    )
    table += "\n".join(
        "| "
        + LABELS.get(name, name)
        + " | "
        + " | ".join(f"{v:.4f}" for v in row)
        + " |"
        for name, row in compact.iterrows()
    )
    fig, axes = plt.subplots(1, 3, figsize=(14, 5))
    for axis, metric, title in zip(
        axes,
        ["selection_score", "conditional_ks", "occupancy_mae"],
        ["Exploratory score", "Within-arrival-bin KS", "Occupancy MAE (vehicles)"],
    ):
        axis.barh(
            [LABELS[m] for m in summary.index],
            summary[f"{metric}_mean"],
            xerr=summary[f"{metric}_std"],
            capsize=2,
        )
        axis.invert_yaxis()
        axis.set_title(title)
        axis.grid(axis="x", alpha=0.2)
        fig.suptitle(
            "Lower is better; error bars show generation SD, not confidence intervals"
        )
    fig.tight_layout()
    fig.savefig(output / "comparison_dashboard.png", dpi=160)
    plt.close(fig)
    report = f"""# Joint stochastic modeling: model selection

## Data source and scope

The target is a joint generator of local plug-in hour, dwell time and **delivered energy**.
Departure is derived as connection + dwell. Data: `{audit['input']}`; site `{site}`;
{len(frame):,} positive-dwell, positive-energy sessions retained. The entire selected session table was read.

**Static data and API exports are separate snapshots.** The local static inventory contains
{coverage['static_total_files']:,} compressed time-series files, matching its README counts.
This confirms the inventory count, not file integrity or live API completeness. The model does not
read those time series. The API status file marks Caltech/JPL session exports partial and Office 1 complete.
The static session JSON is an empty export placeholder. These facts do not imply an incomplete Git clone.

## Evaluation design

The final 20% of observed dates are an exploratory evaluation period; the first 80% support two
expanding-window validation folds (40% → next 20%; 60% → next 20%). Configurations and the preferred
method are selected by mean validation score, then refitted on the first 80%.
The evaluation period was already inspected in the previous round, so it is **not a pristine confirmatory test**.
Reserve newly acquired data for final claims. Each evaluation uses {repeats} generation seeds.

Real evaluation-date session counts are supplied to all methods: this isolates mark generation,
not arrival-count forecasting. The score averages three KS distances and three absolute Spearman
correlation errors divided by two. Conditional KS additionally checks dwell/energy within fixed
four-hour arrival bins, with at least 20 real and simulated sessions per bin; omitted sparse bins
do not imply good fit. Occupancy uses 15-minute intervals and observed dates only, skipping the first
seven days where available. No pre-period carry-in is supplied.

## Results and current recommendation

**Validation-preferred method: {LABELS[recommended]}.**
The lowest exploratory evaluation score belongs to {LABELS[summary.index[0]]}.
The synthetic example is from the validation-preferred method, seed 200.

{table}

Lower is better. Seed SD quantifies generator randomness, not estimation uncertainty or confidence
intervals. Inspect conditional fit, occupancy, tails and complexity alongside the heuristic score.

## Candidate methods

- Independent parametric: empirical arrival + AIC-selected Gamma/Lognormal/Weibull dwell and energy.
- Independent empirical: identical empirical margins, sampled independently. This isolates loss of dependence from parametric misspecification.
- Conditional pairs: arrival-quantile groups, keeping dwell/energy tuples intact; tune 2/4/6 bins.
- Circular neighbors: keep a sampled arrival, choose a paired dwell/energy from its 50/200/500 nearest circular clock neighbors. No hard bin boundary.
- Joint KDE: sin/cos arrival and log-positive variables; reduce deterministic dimensions before KDE, tune bandwidth. Not an ordinary three-dimensional KDE.
- Gaussian copula: empirical margins with Gaussian rank dependence. Linear clock hours can miss midnight structure; empirical tails cannot exceed training bounds.
- Session bootstrap: resample complete session tuples by day type. Not a date/user cluster model.
- Calendar bootstrap: resample complete tuples weighted by target month proximity and training age. Tune 30/90/180-day half-life or season-only weights. A drift-aware heuristic, not causal forecasting.

All methods condition on weekday/weekend, pooling training groups with fewer than 30 sessions.
Exact choices, software versions, date ranges and exclusion counts are saved in run_metadata.json.

## Corrections to the initial experiment

1. "Partial" applies to the API export, not Git retrieval or reading only part of a CSV.
2. An empirical independent baseline now separates dependency errors from marginal-family errors.
3. Recommendations use two earlier validation folds rather than selecting a winner from evaluation results.
4. Missing IDs are not treated as duplicate identities; timestamp units are normalized in occupancy to support microsecond Parquet inputs.
5. KDE handles deterministic lower-dimensional data via projection; generated clock hours are reconciled with DST-adjusted timestamps.
6. Larger individual joint panels replace the crowded original figure; uncertainty is labeled as generation SD.

## Modeling decisions and next steps

Retain a simple empirical joint method as baseline. Treat calendar weights and smooth conditional
neighbors as competing improvements, not presumed winners. Recent weighting may reduce apparent drift,
but partial coverage can also produce apparent drift.

Once Person A supplies verified temporal coverage, compare a day-level clustered bootstrap with
an overdispersed daily-count model and conditional timing. Whole-day resampling preserves within-day
co-movement that session-level methods omit. User-cluster/random-effect models require user-held-out
evaluation. For multimodal driver profiles, transformed Gaussian mixtures or mixture-of-experts are
reasonable next candidates; complex flows are premature before coverage and benchmark stability.

Requested energy must remain distinct from delivered energy. Use input versions actually available
at the simulated decision time. Model post-charge dwell jointly with completion/parking so generated
times remain ordered. Both extensions require the A-side schema and missingness contract.

This experiment excludes nonpositive dwell/energy, performs no tail clipping or imputation, and does
not replace Person A's cleaning. It does not model unobserved waiting/abandonment, capacity restrictions,
charging power or new-user generalization. Occupancy is plug-in occupancy, not queue length or load.
Seven-day warm-up does not fully remove very long dwell boundary effects.

References: [ACN field definitions](https://ev.caltech.edu/dataset),
[SciPy KDE documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.gaussian_kde.html).
"""
    (output / "model_selection_report.md").write_text(report, encoding="utf-8")
    return summary, provenance


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--site", default="0002")
    parser.add_argument("--repeats", type=int, default=10)
    args = parser.parse_args()
    summary, _ = run(args.input, args.output, args.site, args.repeats)
    print(summary[["selection_score_mean", "occupancy_mae_mean"]].round(4))
