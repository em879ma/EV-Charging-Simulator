"""Run from the codespace folder:  python -m unittest discover -s unobserved_behavior/tests -t ."""
import unittest

import numpy as np
import pandas as pd

from unobserved_behavior import diagnostics, synthesis, timeseries


def toy_sessions():
    t0 = pd.Timestamp("2019-06-03 16:00", tz="UTC")  # Monday 09:00 local
    rows = [  # connect offset h, dwell h, charge h, kwh, has_input
        (0.0, 4.0, 1.0, 6.0, True),
        (0.5, 2.0, 2.0, 5.0, False),   # still charging at unplug
        (1.0, 1.0, 0.5, 3.0, False),
    ]
    df = pd.DataFrame(rows, columns=["c", "dwell", "charge", "kwh", "has_input"])
    out = pd.DataFrame({
        "session_id": [f"2_39_1_{i}_2019-06-03 16:00:00.000000" for i in range(len(df))],
        "station_id": [f"s{i}" for i in range(len(df))],
        "connect": t0 + pd.to_timedelta(df["c"], unit="h"),
    })
    out["disconnect"] = out["connect"] + pd.to_timedelta(df["dwell"], unit="h")
    out["done"] = out["connect"] + pd.to_timedelta(df["charge"], unit="h")
    out["kwh"] = df["kwh"]
    out["has_input"] = df["has_input"]
    out["valid_interval"] = True
    gap = (out["disconnect"] - out["done"]).dt.total_seconds() / 60
    out["done_status"] = np.where(gap <= 1, "interrupted", "completed")
    out["charge_h"] = df["charge"]
    out["idle_h"] = df["dwell"] - df["charge"]
    local = out["connect"].dt.tz_convert("America/Los_Angeles")
    out["month_local"] = local.dt.strftime("%Y-%m")
    out["date_local"] = local.dt.date
    return out


class OccupancyTests(unittest.TestCase):
    def test_counts_overlap_and_idle(self):
        occ = diagnostics.occupancy_minutes(toy_sessions())
        self.assertEqual(occ["plugged"].max(), 3)             # all three overlap at 1.0-1.5 h
        self.assertTrue((occ["charging"] + occ["idle"] + occ["unknown"] == occ["plugged"]).all())
        self.assertEqual(int(occ["idle"].sum()), 180 + 30)    # 3 h idle + 0.5 h idle, in minutes


class SynthesisTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(0)

    def test_blocked_driver_shares(self):
        d = synthesis.BlockedDriverBehavior(p_balk=0.3, patience_mean_min=20, p_retry=0.5).decide(20000, self.rng)
        self.assertAlmostEqual((d["action"] == "balk").mean(), 0.3, delta=0.02)
        self.assertAlmostEqual(d.loc[d["action"] == "wait", "patience_min"].mean(), 20, delta=1.5)
        self.assertAlmostEqual(d["retry_if_lost"].mean(), 0.5, delta=0.02)

    def test_move_after_charge_bounds(self):
        natural = np.full(5000, 240.0)
        full = synthesis.MoveAfterChargeResponse(compliance=1.0, grace_min=15, delay_median_min=10).apply(natural, self.rng)
        none = synthesis.MoveAfterChargeResponse(compliance=0.0).apply(natural, self.rng)
        self.assertTrue((full <= natural).all() and np.median(full) < 40)
        self.assertTrue(np.array_equal(none, natural))
        short = synthesis.MoveAfterChargeResponse(compliance=1.0).apply(np.full(10, 3.0), self.rng)
        self.assertTrue((short == 3.0).all())  # a rule never lengthens idle time

    def test_scale_demand(self):
        base = toy_sessions()
        big = pd.concat([base.assign(date_local=base["date_local"])] * 200, ignore_index=True)
        up = synthesis.scale_demand(big, 1.5, self.rng)
        down = synthesis.scale_demand(big, 0.8, self.rng)
        self.assertAlmostEqual(len(up) / len(big), 1.5, delta=0.05)
        self.assertAlmostEqual(len(down) / len(big), 0.8, delta=0.05)
        self.assertTrue(up.loc[up["synthetic"], "station_id"].isna().all())
        self.assertTrue(((up["disconnect"] - up["connect"]) > pd.Timedelta(0)).all())

    def test_port_availability_long_run(self):
        counts = synthesis.PortAvailability(availability=0.9, mean_outage_days=5).daily_in_service(200, 400, self.rng)
        self.assertAlmostEqual(counts[100:].mean() / 200, 0.9, delta=0.02)

    def test_vehicle_rate_mix_capped(self):
        mix = synthesis.VehicleRateMix(kw_classes=(3.3, 7.2), probs=(0.5, 0.5), port_cap_kw=6.656)
        kw = mix.sample(1000, self.rng)
        self.assertLessEqual(kw.max(), 6.656)
        fitted = synthesis.VehicleRateMix.from_timeseries(pd.Series([3.2] * 30 + [6.5] * 70))
        self.assertEqual(fitted.probs, (0.3, 0.7))


class PolicyResponseTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(1)

    def test_waitlist_patience_defaults_to_own_dwell(self):
        d = synthesis.BlockedDriverBehavior(p_balk=0.0, patience_mean_min=None, hold_min=15).decide(1000, self.rng)
        self.assertTrue(np.isinf(d["patience_min"]).all())
        self.assertTrue(d["plug_in_delay_min"].between(0, 15).all())

    def test_valet_move_is_instant(self):
        idle = synthesis.MoveAfterChargeResponse(compliance=1.0, grace_min=0, delay_median_min=0).apply(np.full(50, 120.0), self.rng)
        self.assertTrue((idle == 0).all())

    def test_departure_response(self):
        planned, done = np.full(20000, 480.0), np.full(20000, 120.0)
        out = synthesis.DepartureResponse(responsive_share=0.25, delay_mean_min=10).unplug_after_plugin_min(planned, done, self.rng)
        self.assertAlmostEqual((out < planned).mean(), 0.25, delta=0.02)
        self.assertTrue((out <= planned).all() and (out >= done).all())

    def test_port_blocking_and_failures(self):
        pa = synthesis.PortAvailability(p_blocked_space=0.1, p_session_failure=0.03)
        usable = pa.usable_ports(np.full(5000, 54), self.rng)
        self.assertAlmostEqual(usable.mean() / 54, 0.9, delta=0.01)
        self.assertAlmostEqual(pa.session_fails(50000, self.rng).mean(), 0.03, delta=0.003)


class CatalogTests(unittest.TestCase):
    def test_every_citation_is_verified_and_used(self):
        from unobserved_behavior import catalog
        used = {k for row in catalog.ROWS for k in row["refs"]}
        self.assertEqual(used - set(catalog.REFERENCES), set(), "row cites an unverified reference")
        self.assertEqual(set(catalog.REFERENCES) - used, set(), "reference listed but never cited")
        self.assertEqual(len({row["id"] for row in catalog.ROWS}), len(catalog.ROWS))

    def test_grid_rows_point_to_table_rows(self):
        from unobserved_behavior import catalog
        ids = {row["id"] for row in catalog.ROWS}
        self.assertTrue(set(synthesis.sensitivity_grid()["row_id"]) <= ids)


class LoaderAndRobustnessTests(unittest.TestCase):
    def _clean_table_like_person_a(self, path):
        """A cleaned table as Person A's notebook produces it: no sessionID, minute-floored, one input version."""
        rows = []
        t0 = pd.Timestamp("2019-03-04 16:00", tz="UTC")
        for i in range(120):
            c = t0 + pd.Timedelta(days=i // 6, minutes=10 * (i % 6))
            rows.append({"_id": f"id{i}", "siteID": "2", "stationID": f"2-39-1-{i % 5}",
                         "connectionTime": c.isoformat(), "disconnectTime": (c + pd.Timedelta(hours=5)).isoformat(),
                         "doneChargingTime": (c + pd.Timedelta(hours=2)).isoformat(), "kWhDelivered": 8.0,
                         "kWhRequested": 10.0 if i % 2 else None,
                         "requestedDeparture": (c + pd.Timedelta(hours=4)).isoformat() if i % 2 else None,
                         "userInputVersionCount": 1 if i % 2 else 0, "userID": f"u{i % 7}" if i % 2 else None})
        pd.DataFrame(rows).to_csv(path, index=False)

    def test_person_a_style_table_loads_and_is_labeled_latest(self):
        import tempfile, os
        from unobserved_behavior.data import load_sessions
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "clean_sessions.csv")
            self._clean_table_like_person_a(path)
            df = load_sessions(path)
        self.assertEqual(len(df), 120)
        self.assertFalse(df["has_session_id"].any())
        self.assertTrue(df["timestamps_minute_floored"].all())
        self.assertEqual(df["input_version"].iloc[0], "latest")
        m, _ = diagnostics.input_metrics(df)
        self.assertTrue(m["input_version_label"].startswith("LATEST"))

    def test_dst_fall_back_arrival_does_not_crash(self):
        df = toy_sessions()
        shift = pd.Timestamp("2018-11-04 08:30", tz="UTC") - df["connect"].iloc[0]  # 01:30 PDT, repeated hour
        for col in ["connect", "disconnect", "done"]:
            df[col] = df[col] + shift
        df["site_id"] = "0002"
        local = df["connect"].dt.tz_convert("America/Los_Angeles")
        df["month_local"] = local.dt.strftime("%Y-%m")
        occ = diagnostics.occupancy_minutes(df)
        cap = diagnostics.capacity_minutes(df, occ.index, 54)
        m = diagnostics.censoring_metrics(df, occ, cap)
        self.assertEqual(m["max_concurrent"], 3)

    def test_empirical_input_errors(self):
        rng = np.random.default_rng(2)
        n = 400
        connect = pd.Timestamp("2019-01-07 16:00", tz="UTC") + pd.to_timedelta(rng.integers(0, 600, n), unit="min")
        df = pd.DataFrame({"has_input": True, "kwh_req_first": 10.0, "kwh": 6.0,
                           "connect": connect, "disconnect": connect + pd.Timedelta(hours=6),
                           "arrival_hour": rng.uniform(6, 14, n), "is_weekend": False, "regime": "post_2018-11"})
        df["dep_req_first"] = df["disconnect"] - pd.Timedelta(minutes=30)
        model = synthesis.EmpiricalInputErrors.from_sessions(df, min_cell=50)
        cells = synthesis.stratum(pd.Series([8.0, 23.0]), pd.Series([False, True]))  # second cell has no donors
        req = model.requested_energy(np.array([6.0, 6.0]), cells, rng)
        self.assertTrue(np.allclose(req, 10.0))
        dep = model.announced_departure(pd.Series(pd.to_datetime(["2019-01-07 20:00", "2019-01-12 20:00"], utc=True)), cells, rng)
        self.assertTrue(((dep - pd.to_datetime(["2019-01-07 20:00", "2019-01-12 20:00"], utc=True)) == pd.Timedelta(minutes=-30)).all())


class TimeseriesTests(unittest.TestCase):
    def test_filename(self):
        self.assertEqual(timeseries.ts_filename("2_39_78_362_2018-05-01 16:18:53.512595"),
                         "2-39-78-362-2018-05-01T16-18-53-512595.csv.gz")


if __name__ == "__main__":
    unittest.main()
