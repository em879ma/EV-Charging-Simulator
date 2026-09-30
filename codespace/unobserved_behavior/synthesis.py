"""Samplers for behavior the simulator needs but ACN-Data does not record.

Every class takes its parameters explicitly so the simulator can sweep them as sensitivity
scenarios. Defaults and ranges are documented, with their basis, in the synthesis table
(``outputs/synthesis_table.csv``). Nothing here fits arrival, dwell or energy distributions;
that belongs to the joint demand model (Person B). These samplers sit on top of it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

ARRIVAL_PERIODS = [0, 6, 10, 14, 18, 24]
ARRIVAL_LABELS = ["overnight", "morning", "midday", "afternoon", "evening"]


def stratum(arrival_hour: pd.Series, is_weekend: pd.Series) -> pd.Series:
    """Day type x arrival period, the conditioning cells used by the empirical samplers."""
    period = pd.cut(pd.Series(arrival_hour), ARRIVAL_PERIODS, labels=ARRIVAL_LABELS, right=False).astype(str)
    day = pd.Series(np.where(pd.Series(is_weekend).to_numpy(), "weekend", "weekday"), index=period.index)
    return (day + "|" + period).to_numpy()


# --------------------------------------------------------------------------- blocked drivers
@dataclass(frozen=True)
class BlockedDriverBehavior:
    """What an arriving driver does when every in-service port is occupied.

    p_balk               probability of not joining a waitlist (parks elsewhere without charging)
    patience_mean_min    mean of an exponential patience for drivers on the waitlist. ``None`` means
                         the driver stays on the list until their own departure (a workplace driver
                         parks elsewhere and waits); the engine caps patience at remaining dwell.
    hold_min             after a port frees, the notified driver takes Uniform(0, hold_min) to move
                         the car and plug in; the port is reserved but idle meanwhile
    p_retry              probability that a lost driver returns once for another attempt
    retry_delay_mean_min mean of the exponential delay before that return (default: back around lunch)
    """
    p_balk: float = 1.0
    patience_mean_min: float | None = None
    hold_min: float = 15.0
    p_retry: float = 0.0
    retry_delay_mean_min: float = 180.0

    def decide(self, n: int, rng: np.random.Generator) -> pd.DataFrame:
        waits = rng.random(n) >= self.p_balk
        if self.patience_mean_min is None:
            patience = np.where(waits, np.inf, 0.0)
        else:
            patience = np.where(waits, rng.exponential(self.patience_mean_min, n), 0.0)
        hold = np.where(waits, rng.uniform(0, self.hold_min, n), np.nan)
        retry = rng.random(n) < self.p_retry
        delay = np.where(retry, rng.exponential(self.retry_delay_mean_min, n), np.nan)
        return pd.DataFrame({"action": np.where(waits, "wait", "balk"), "patience_min": patience,
                             "plug_in_delay_min": hold, "retry_if_lost": retry, "retry_delay_min": delay})


BLOCKED_SCENARIOS = {
    "loss": BlockedDriverBehavior(p_balk=1.0),
    "loss_with_retry": BlockedDriverBehavior(p_balk=1.0, p_retry=0.5),
    "waitlist": BlockedDriverBehavior(p_balk=0.0, patience_mean_min=None, hold_min=15),
    "waitlist_impatient": BlockedDriverBehavior(p_balk=0.3, patience_mean_min=30, hold_min=30),
}


# --------------------------------------------------------------------------- move-after-charge
@dataclass(frozen=True)
class MoveAfterChargeResponse:
    """Post-charge idle time under a move-after-charge rule.

    A compliant driver unplugs at min(natural idle, grace + response delay); a non-compliant driver
    keeps the idle time they would have had without the rule. ``natural_idle_min`` must come from
    the simulated session under the SAME charging policy, because completion time is policy-dependent.
    """
    compliance: float = 0.6
    grace_min: float = 15.0
    delay_median_min: float = 10.0
    delay_sigma: float = 0.8  # lognormal shape; delay_median_min = 0 gives an instant (valet) move

    def apply(self, natural_idle_min: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        natural = np.asarray(natural_idle_min, dtype=float)
        comply = rng.random(natural.shape) < self.compliance
        if self.delay_median_min > 0:
            delay = rng.lognormal(np.log(self.delay_median_min), self.delay_sigma, natural.shape)
        else:
            delay = np.zeros(natural.shape)
        return np.where(comply, np.minimum(natural, self.grace_min + delay), natural)


@dataclass(frozen=True)
class DepartureResponse:
    """Drivers whose unplug time reacts to charging completion under ANY policy (R08).

    A share ``responsive_share`` unplugs at min(planned departure, completion + Exp(delay_mean_min));
    everyone else keeps the exogenous planned departure drawn by the demand model. Calibrate the share
    to the EXCESS of unplugs just after completion over the background level (departure_response_test
    'excess_share'), because the dwell model already reproduces background departures.
    """
    responsive_share: float = 0.03
    delay_mean_min: float = 10.0

    def unplug_after_plugin_min(self, planned_dwell_min: np.ndarray, completion_min: np.ndarray,
                                rng: np.random.Generator) -> np.ndarray:
        planned = np.asarray(planned_dwell_min, dtype=float)
        done = np.asarray(completion_min, dtype=float)
        responsive = rng.random(planned.shape) < self.responsive_share
        react = done + rng.exponential(self.delay_mean_min, planned.shape)
        return np.where(responsive, np.minimum(planned, react), planned)


# --------------------------------------------------------------------------- announced inputs
@dataclass
class EmpiricalInputErrors:
    """Announced departure and requested energy for sessions without user input.

    Learned from sessions WITH user input (first version = what a scheduler sees at plug-in):
      departure error  = announced departure - actual unplug (minutes)
      energy ratio     = delivered kWh / requested kWh
    Errors are resampled within day-type x arrival-period cells (fallback: day type, then all).
    """
    dep_err: dict = field(default_factory=dict)
    energy_ratio: dict = field(default_factory=dict)
    min_cell: int = 50

    @classmethod
    def from_sessions(cls, df: pd.DataFrame, min_cell: int = 50, regime: str | None = "auto") -> "EmpiricalInputErrors":
        """Fit on sessions with input. regime='auto' keeps only the Caltech app-registration regime
        (post_2018-11, see data.REGIME_BREAKS) when present; None uses every session."""
        if regime == "auto":
            regime = "post_2018-11" if "regime" in df and (df["regime"] == "post_2018-11").any() else None
        if regime is not None:
            df = df[df["regime"] == regime]
        d = df[df["has_input"] & df["kwh_req_first"].gt(0) & df["dep_req_first"].notna()].copy()
        d["cell"] = stratum(d["arrival_hour"], d["is_weekend"])
        d["day"] = np.where(d["is_weekend"], "weekend", "weekday")
        d["dep_err"] = (d["dep_req_first"] - d["disconnect"]).dt.total_seconds() / 60
        d["ratio"] = d["kwh"] / d["kwh_req_first"]
        model = cls(min_cell=min_cell)
        for key, col in [("dep_err", "dep_err"), ("energy_ratio", "ratio")]:
            store = getattr(model, key)
            store["all"] = d[col].to_numpy()
            for level in ["day", "cell"]:
                for name, g in d.groupby(level):
                    if len(g) >= min_cell:
                        store[name] = g[col].to_numpy()
        return model

    def _draw(self, store: dict, cells: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        out = np.empty(len(cells))
        for i, cell in enumerate(cells):
            pool = store.get(cell) if cell in store else store.get(cell.split("|")[0], store["all"])
            out[i] = pool[rng.integers(len(pool))]
        return out

    def announced_departure(self, actual_unplug: pd.Series, cells: np.ndarray, rng: np.random.Generator) -> pd.Series:
        return actual_unplug + pd.to_timedelta(self._draw(self.dep_err, np.asarray(cells), rng), unit="min")

    def requested_energy(self, delivered_kwh: np.ndarray, cells: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        ratio = self._draw(self.energy_ratio, np.asarray(cells), rng)
        return np.asarray(delivered_kwh, dtype=float) / np.clip(ratio, 0.05, None)


# --------------------------------------------------------------------------- vehicle rates
@dataclass(frozen=True)
class VehicleRateMix:
    """Vehicle maximum charging power, capped by the port rating.

    Default mix: open-pilot periods (scheduler allowing >= 30 A) in the ACN time-series sample show
    two onboard-charger classes, about 3.3 kW and 6.6 kW (see timeseries.py / diagnostics_metrics.json).
    """
    kw_classes: tuple = (3.3, 6.6)
    probs: tuple = (0.51, 0.49)
    port_cap_kw: float = 32 * 208 / 1000

    @classmethod
    def from_timeseries(cls, open_pilot_kw: pd.Series, split_kw: float = 4.5, port_cap_kw: float = 32 * 208 / 1000):
        kw = open_pilot_kw.dropna()
        low = float((kw < split_kw).mean())
        return cls(kw_classes=(round(float(kw[kw < split_kw].median()), 2), round(float(kw[kw >= split_kw].median()), 2)),
                   probs=(round(low, 3), round(1 - low, 3)), port_cap_kw=port_cap_kw)

    def sample(self, n: int, rng: np.random.Generator) -> np.ndarray:
        kw = rng.choice(np.asarray(self.kw_classes), size=n, p=np.asarray(self.probs) / np.sum(self.probs))
        return np.minimum(kw, self.port_cap_kw)


# --------------------------------------------------------------------------- port availability
@dataclass(frozen=True)
class PortAvailability:
    """Ports that can actually serve a driver.

    availability        long-run share of port-days in service (independent two-state chain per port)
    mean_outage_days    mean outage length; daily repair probability = 1 / mean_outage_days
    p_blocked_space     share of in-service ports whose space is taken by a vehicle that is not charging
    p_session_failure   probability that a plugged-in session delivers ~0 kWh but still occupies the port
    """
    availability: float = 0.97
    mean_outage_days: float = 7.0
    p_blocked_space: float = 0.01
    p_session_failure: float = 0.0

    def usable_ports(self, in_service: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        """In-service ports minus those blocked by non-charging vehicles (binomial thinning)."""
        return rng.binomial(np.asarray(in_service, dtype=int), 1 - self.p_blocked_space)

    def session_fails(self, n: int, rng: np.random.Generator) -> np.ndarray:
        return rng.random(n) < self.p_session_failure

    def daily_in_service(self, n_ports: int, n_days: int, rng: np.random.Generator) -> np.ndarray:
        p_repair = 1 / self.mean_outage_days
        p_fail = p_repair * (1 - self.availability) / self.availability
        up = rng.random(n_ports) < self.availability
        counts = np.empty(n_days, dtype=int)
        for day in range(n_days):
            counts[day] = up.sum()
            u = rng.random(n_ports)
            up = np.where(up, u >= p_fail, u < p_repair)
        return counts


# --------------------------------------------------------------------------- demand scaling
TIME_COLUMNS = ["connect", "disconnect", "done", "dep_req_first", "dep_req_last"]


def scale_demand(sessions: pd.DataFrame, factor: float, rng: np.random.Generator, jitter_min: float = 15.0) -> pd.DataFrame:
    """Scale session demand within each day, keeping each day's own mix.

    factor < 1: independent thinning (keep each session with probability ``factor``).
    factor > 1: keep all sessions and add Poisson((factor - 1) * n_day) bootstrap copies drawn from
    the same day, with all timestamps of a copy shifted by one uniform jitter in +/- ``jitter_min``.
    Working day by day preserves day-to-day overdispersion; copies are flagged ``synthetic``.
    """
    if factor <= 0:
        raise ValueError("factor must be positive")
    if factor <= 1:
        out = sessions.loc[rng.random(len(sessions)) < factor].copy()
        out["synthetic"] = False
        return out
    parts = []
    for day, group in sessions.groupby("date_local"):
        extra = rng.poisson((factor - 1) * len(group))
        if extra == 0:
            continue
        copies = group.iloc[rng.integers(len(group), size=extra)].copy()
        shift = pd.to_timedelta(rng.uniform(-jitter_min, jitter_min, extra), unit="min")
        for col in TIME_COLUMNS:
            if col in copies:
                copies[col] = copies[col] + shift
        copies["session_id"] = copies["session_id"].astype(str) + "_syn" + pd.Series(range(extra), index=copies.index).astype(str) + "_" + str(day)
        copies["station_id"] = pd.NA
        parts.append(copies)
    base = sessions.copy()
    base["synthetic"] = False
    if not parts:
        return base
    extra_df = pd.concat(parts)
    extra_df["synthetic"] = True
    return pd.concat([base, extra_df]).sort_values("connect").reset_index(drop=True)


# --------------------------------------------------------------------------- sensitivity grid
def sensitivity_grid(responsive_share: float = 0.03) -> pd.DataFrame:
    """One row per synthesized parameter: baseline, low/high to sweep, and where the values come from.

    ``basis`` says whether a value is from a verified source, from our own ACN analysis, or an
    assumption. Assumptions are exactly the parameters whose sweep results must be reported.
    """
    rows = [
        ("R02", "p_balk", 1.0, 0.0, 1.0, "BlockedDriverBehavior", "modeling convention (Erlang-loss structure as in Aveklouris 2017; qualitative support Sutton 2022); p_balk itself is unmeasured"),
        ("R03", "hold_min", 15.0, 15.0, 30.0, "BlockedDriverBehavior", "source: ChargePoint Waitlist 15 min; Cal Poly 30 min"),
        ("R03", "patience_mean_min", None, 10.0, 60.0, "BlockedDriverBehavior", "baseline = own remaining dwell; impatient range is an assumption"),
        ("R04", "p_retry", 0.0, 0.0, 0.5, "BlockedDriverBehavior", "assumption (only qualitative evidence: Sutton 2022)"),
        ("R04", "retry_delay_mean_min", 180.0, 60.0, 240.0, "BlockedDriverBehavior", "assumption: return around lunchtime (Sutton 2022, qualitative)"),
        ("R05", "arrival_shift_share", 0.0, 0.0, 0.08, "arrival model (Person B)", "source: Garg et al. 2024 WP timing effects (10% significance)"),
        ("R06", "compliance", 0.6, 0.2, 1.0, "MoveAfterChargeResponse", "assumption; no validated compliance estimate exists (long idle under a voluntary policy: INL 2014)"),
        ("R07", "grace_min", 15.0, 5.0, 30.0, "MoveAfterChargeResponse", "assumption; 15/30 min are operator hold windows for the NEXT driver (ChargePoint, Cal Poly), not grace periods"),
        ("R07", "delay_median_min", 10.0, 0.0, 60.0, "MoveAfterChargeResponse", "assumption; 0 = automated interchange only (Zeng 2019: human/valet takes minutes to hours)"),
        ("R08", "responsive_share", round(responsive_share, 3), 0.0, round(2 * responsive_share, 3), "DepartureResponse", "ours: excess of ACN unplugs 2-12 min after completion over the background level"),
        ("R09", "mnar_multiplier", 1.0, 0.8, 1.2, "EmpiricalInputErrors.requested_energy", "method: delta adjustment (van Buuren 2018); range is an assumption"),
        ("R12", "transition_soc", 0.8, 0.7, 0.9, "battery taper", "source: ACN-Sim Linear2Stage battery (Lee et al. 2021)"),
        ("R15", "site_limit_kw", 150.0, 20.0, 150.0, "network model", "source: Caltech t1 150 kVA; ACN papers sweep ~20-150 kW (Lee et al. 2021)"),
        ("R16", "availability", 0.97, 0.73, 1.0, "PortAvailability", "0.97 = NEVI floor for publicly funded chargers (23 CFR 680.116), not a measurement; 0.73 = DCFC field audit, a pessimistic analogue (Rempel 2024: 73.3% of 655 ports)"),
        ("R16", "mean_outage_days", 7.0, 1.0, 30.0, "PortAvailability", "assumption"),
        ("R16", "p_blocked_space", 0.01, 0.0, 0.09, "PortAvailability", "source: TomTom Berlin snapshot via Guillet et al. 2022 (weakly transferable)"),
        ("R16", "p_session_failure", 0.0, 0.0, 0.03, "PortAvailability", "baseline 0 is an assumption (ACN records exclude failed sessions); high 0.03 = EV WATTS 2.9% of public Level-2 sessions <0.3 kWh"),
        ("R17", "demand_factor", 1.0, 0.8, 1.5, "scale_demand", "proposal design"),
        ("R18", "capex_per_port_usd_2019", 5500.0, 3400.0, 7300.0, "cost model", "source: ICCT (Nicholas 2019) hardware + CA installation; DOE EV Project workplace install"),
        ("R18", "demand_charge_usd_per_kw_month", 15.51, 3.0, 40.0, "cost model", "source: SCE TOU-EV-4 (Lee et al. 2021 Table III); range DOE (Smith & Castellano 2015)"),
        ("R18", "network_fee_usd_per_port_year", 300.0, 100.0, 900.0, "cost model", "source: Smith & Castellano 2015 range; baseline is an assumption"),
        ("R18", "evse_life_years", 10.0, 7.0, 12.0, "cost model", "some stakeholders assume at least 10 yr (Smith & Castellano 2015); range is an assumption"),
    ]
    return pd.DataFrame(rows, columns=["row_id", "parameter", "baseline", "low", "high", "used_by", "basis"])
