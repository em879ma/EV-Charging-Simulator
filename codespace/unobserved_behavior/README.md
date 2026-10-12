# Unobserved behavior synthesis (Person C, Milestone 1)

ACN-Data records only sessions that successfully plugged in. The simulator also needs things the
data never shows: what a driver does when the site is full, how drivers would react to a
move-after-charge rule, requested energy and departure for sessions without app input, each
vehicle's own maximum charging rate, port outages, costs. This folder produces a **table of every
such variable, the evidence the data does give, how to synthesize it, and the scientific basis**,
plus samplers the simulator can call.

**Deliverable:** [`outputs/synthesis_table.md`](outputs/synthesis_table.md) (readable) and
[`outputs/synthesis_table.csv`](outputs/synthesis_table.csv) (spreadsheet, UTF-8), with the
parameter sweep ranges in [`outputs/scenario_parameters.csv`](outputs/scenario_parameters.csv).

## Run

From the `codespace` folder (Python 3.10+ with pandas, numpy and matplotlib; no new packages):

```bash
python -m unobserved_behavior.build_table                   # rebuild outputs/ (offline: reuses the saved time-series sample)
python -m unobserved_behavior.build_table --timeseries fetch   # re-download the time-series sample (~700 small files)
python -m unobserved_behavior.build_table --sessions ../path/clean_sessions.csv --out /tmp/c_check   # Person A's table
python -m unittest discover -s unobserved_behavior/tests -t .   # tests
```

- `--timeseries auto` (default) reuses `outputs/timeseries_sample.csv`; `skip` omits the
  time-series evidence and therefore refuses to overwrite `outputs/` unless `--out` is given.
- Time-series files come one by one from the public `tongxin-li/ACN-Data-Static` repository into
  `unobserved_behavior/.cache/` (git-ignored), so the 2 GB submodule does not need to be cloned.
- Reading `.parquet` needs `pyarrow`; a `.csv` export works without it.

## Input contract for Person A's cleaned table

The loader accepts the raw API JSON or a cleaned table. For the cleaned table to reproduce the
numbers in the deliverable it should:

1. **keep `sessionID`** (maps each session to its time-series file; without it the loader falls
   back to `_id` and the time-series step is skipped);
2. **keep the FIRST user-input version** (`kWhRequestedFirst`, `requestedDepartureFirst`,
   `minutesAvailableFirst`) or the raw `userInputs` JSON. With only the latest version the table
   labels R10 as "LATEST-version" instead of silently calling it first-version;
3. **keep second-resolution timestamps** (minute flooring shifts the 1-min interrupted cutoff; the
   loader records `timestamps_minute_floored` in `diagnostics_metrics.json`);
4. keep `siteID`, `stationID`, `connectionTime`, `disconnectTime`, `doneChargingTime`, `kWhDelivered`.

## Files

| File | Purpose |
|---|---|
| `data.py` | Loads raw JSON or a cleaned table; first and latest user-input versions; completion status; site-specific regime label (Caltech 2018-11-01) |
| `diagnostics.py` | Minute-level occupancy vs capacity, idle share, completion-triggered departures (spike vs placebo, excess share), user-input coverage and errors, realized power, station gaps, daily-count overdispersion |
| `timeseries.py` | Per-stratum checks of `doneChargingTime`, plus a simple random sample (post-2018-11) for vehicle charging classes and scheduler footprint |
| `synthesis.py` | Samplers: `BlockedDriverBehavior`, `MoveAfterChargeResponse`, `DepartureResponse`, `EmpiricalInputErrors`, `VehicleRateMix`, `PortAvailability`, `scale_demand`; `sensitivity_grid()` |
| `catalog.py` | The table rows and the reference list (every reference passed an independent verification) |
| `build_table.py` | Runs everything and writes `outputs/` |

## Outputs

- `synthesis_table.csv / .md`: 18 variables in groups A-E, priority P0 (baseline model), P1 (proposal scenario or policy), P2 (extension)
- `diagnostics_metrics.json`: every number quoted in the table
- `scenario_parameters.csv`: baseline / low / high per synthesized parameter, with a `basis` column that says whether a value is sourced, ours (from ACN data) or an assumption. Assumptions are exactly the parameters whose sweep results must be reported.
- `timeseries_sample.csv`, `figures/`

## Findings the other roles should know

1. **The Caltech site never came within 2 ports of the ports in service** (max 51 of 54). Waiting,
   balking and abandonment cannot be estimated from ACN-Data; they are scenario parameters, and the
   observed plug-ins can be treated as unconstrained demand at the observed capacity.
2. **Regime break on 2018-11-01**: Caltech began charging $0.12/kWh and terminating unclaimed
   sessions after 30 min. User-input coverage jumps from ~6% to ~85% and weekday plug-ins halve.
   Fit demand per regime; pooling the regimes inflates day-to-day overdispersion (variance/mean
   ~10 pooled vs ~2.5-3 within each regime).
3. **Unclaimed post-2018-11 sessions are policy truncations, not demand**: median 0.90 kWh vs
   7.6 kWh for claimed sessions. Exclude them from demand fitting (Person B).
4. **User inputs are biased**: about 70% of drivers stay longer than announced and about 79% request
   more than 1.2x the energy they take. An earliest-departure-first scheduler must use announced
   (noisy) departures; report service against both requested energy and energy demand.
5. **Observed charging is managed.** Do not feed observed charging durations or idle times into the
   simulator; regenerate charging from arrival, dwell, energy, vehicle rate and policy. The observed
   48% idle share of occupied port time is a *lower* bound on what a move-after-charge rule could
   recover under faster policies.
6. **Vehicle max rate is bimodal**: in a random post-2018-11 sample about half the vehicles charge
   at ~3.3 kW (16 A onboard chargers) and half at the 6.66 kW port limit.
7. **About 30% of sessions were still charging at unplug** (time-series adjusted), so their
   delivered energy understates demand (right-censored).

## 中文摘要

Person C 的交付物是 `outputs/synthesis_table.md/.csv`：列出模拟需要、但 ACN 数据里没有的 18 个变量。
每行给出数据能提供的证据（由代码现算）、合成方法、基线值与敏感性范围、科学依据和参考文献。
所有引用都经过独立核实；没有文献依据的参数，在 `scenario_parameters.csv` 里明确标为“假设”，这些参数必须做敏感性扫描。
组里需要注意：
- 站点从未接近满载，等待和放弃只能做情景分析。
- 2018-11-01 有制度断点（开始收费，未登记的会话 30 分钟后终止），需求要分时期建模。
- 断点之后未登记的会话是被规则截断的，不是真实需求。
- 用户填报的离开时间和电量有系统性偏差。
- 观测到的充电过程是受调度器管控的，不能当作“不管控”基线。
Person A 的清洗表请保留 `sessionID`、第一次填报版本和秒级时间戳（见上方的 Input contract）。
