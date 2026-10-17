# Joint stochastic modeling: model selection

## Data source and scope

The target is a joint generator of local plug-in hour, dwell time and **delivered energy**.
Departure is derived as connection + dwell. Data: `/Users/alexshang/Desktop/EV-Charing-Simulator/raw_acn/office/all_sessions.csv`; site `0019`;
1,683 positive-dwell, positive-energy sessions retained. The entire selected session table was read.

**Static data and API exports are separate snapshots.** The local static inventory contains
85,877 compressed time-series files, matching its README counts.
This confirms the inventory count, not file integrity or live API completeness. The model does not
read those time series. The API status file marks Caltech/JPL session exports partial and Office 1 complete.
The static session JSON is an empty export placeholder. These facts do not imply an incomplete Git clone.

## Evaluation design

The final 20% of observed dates are an exploratory evaluation period; the first 80% support two
expanding-window validation folds (40% → next 20%; 60% → next 20%). Configurations and the preferred
method are selected by mean validation score, then refitted on the first 80%.
The evaluation period was already inspected in the previous round, so it is **not a pristine confirmatory test**.
Reserve newly acquired data for final claims. Each evaluation uses 10 generation seeds.

Real evaluation-date session counts are supplied to all methods: this isolates mark generation,
not arrival-count forecasting. The score averages three KS distances and three absolute Spearman
correlation errors divided by two. Conditional KS additionally checks dwell/energy within fixed
four-hour arrival bins, with at least 20 real and simulated sessions per bin; omitted sparse bins
do not imply good fit. Occupancy uses 15-minute intervals and observed dates only, skipping the first
seven days where available. No pre-period carry-in is supplied.

## Results and current recommendation

**Validation-preferred method: Calendar bootstrap.**
The lowest exploratory evaluation score belongs to Calendar bootstrap.
The synthetic example is from the validation-preferred method, seed 200.

| Method | Score | Seed SD | Conditional KS | Occupancy MAE | Invalid share |
|---|---:|---:|---:|---:|---:|
| Calendar bootstrap | 0.1001 | 0.0159 | 0.2212 | 0.3639 | 0.0000 |
| Conditional pairs | 0.1114 | 0.0080 | 0.2498 | 0.3756 | 0.0000 |
| Circular neighbors | 0.1149 | 0.0130 | 0.2338 | 0.3718 | 0.0000 |
| Session bootstrap | 0.1177 | 0.0176 | 0.2532 | 0.3589 | 0.0000 |
| Gaussian copula | 0.1220 | 0.0156 | 0.2496 | 0.3787 | 0.0000 |
| Joint KDE | 0.1321 | 0.0119 | 0.2616 | 0.3683 | 0.0000 |
| Independent empirical | 0.1369 | 0.0150 | 0.2713 | 0.4047 | 0.0000 |
| Independent parametric | 0.1565 | 0.0110 | 0.2923 | 0.4316 | 0.0000 |

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
