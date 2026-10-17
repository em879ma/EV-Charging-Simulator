"""Small synthetic checks: no production dataset accessed."""

import unittest
import tempfile
from pathlib import Path
import numpy as np
import pandas as pd

from person_b_models import (
    MarkModel,
    chronological_split,
    generate,
    occupancy,
    load_sessions,
)


class ModelChecks(unittest.TestCase):
    def fixture(self):
        rng = np.random.default_rng(7)
        date = pd.date_range("2024-01-01", periods=60).repeat(8)
        arrival = rng.uniform(6, 18, len(date))
        dwell = 20 - arrival + rng.uniform(0.1, 0.5, len(date))
        energy = 2 * dwell
        return pd.DataFrame(
            {
                "date": date,
                "arrival": arrival,
                "dwell": dwell,
                "energy": energy,
                "dayType": np.where(date.dayofweek >= 5, "Weekend", "Weekday"),
                "connection": date.tz_localize("America/Los_Angeles").tz_convert("UTC")
                + pd.to_timedelta(arrival, unit="h"),
            }
        )

    def test_dates_never_leak_across_split(self):
        a, b, c = chronological_split(self.fixture())
        self.assertLess(a.date.max(), b.date.min())
        self.assertLess(b.date.max(), c.date.min())
        self.assertEqual(len(a) + len(b) + len(c), 480)

    def test_pair_sampling_preserves_energy_identity(self):
        frame = self.fixture()
        model = MarkModel("conditional_pairs").fit(frame)
        sampled = model.sample("Weekday", 500, np.random.default_rng(4))
        np.testing.assert_allclose(sampled[:, 2], 2 * sampled[:, 1])

    def test_independent_baseline_breaks_identity(self):
        sampled = (
            MarkModel("independent")
            .fit(self.fixture())
            .sample("Weekday", 500, np.random.default_rng(4))
        )
        self.assertGreater(np.abs(sampled[:, 2] - 2 * sampled[:, 1]).mean(), 1)

    def test_departure_and_oracle_counts(self):
        frame = self.fixture()
        result = generate(MarkModel("session_bootstrap").fit(frame), frame, 11)
        pd.testing.assert_series_equal(
            result.groupby("date").size(), frame.groupby("date").size()
        )
        np.testing.assert_allclose(
            (result.departure - result.connection).dt.total_seconds() / 3600,
            result.dwell,
            atol=1e-8,
        )

    def test_cross_midnight_occupancy_and_half_open_intervals(self):
        start = pd.to_datetime(["2024-01-01 23:00Z", "2024-01-02 00:00Z"])
        frame = pd.DataFrame({"connection": start, "dwell": [2.0, 1.0]})
        grid = pd.date_range("2024-01-01 23:00Z", periods=4, freq="h")
        np.testing.assert_array_equal(occupancy(frame, grid), [1, 2, 0, 0])

    def test_kde_arrival_wraps_into_valid_clock_hours(self):
        frame = self.fixture()
        rng = np.random.default_rng(8)
        frame.arrival = np.mod(rng.normal(24, 0.3, len(frame)), 24)
        # KDE requires full-rank data; the other fixture deliberately has energy=2*dwell.
        frame.energy *= rng.lognormal(0, 0.1, len(frame))
        sampled = MarkModel("joint_kde").fit(frame).sample("Weekday", 1000, rng)
        self.assertTrue(((sampled[:, 0] >= 0) & (sampled[:, 0] < 24)).all())
        self.assertGreater(((sampled[:, 0] < 2) | (sampled[:, 0] > 22)).mean(), 0.9)
        self.assertTrue((sampled[:, 1:] > 0).all())

    def test_copula_preserves_strong_monotone_dependence(self):
        sampled = (
            MarkModel("gaussian_copula")
            .fit(self.fixture())
            .sample("Weekday", 1000, np.random.default_rng(9))
        )
        self.assertGreater(
            pd.Series(sampled[:, 1]).corr(pd.Series(sampled[:, 2]), method="spearman"),
            0.95,
        )
        self.assertTrue(np.isfinite(sampled).all())

    def test_empirical_independence_isolates_loss_of_dependency(self):
        sampled = (
            MarkModel("independent_empirical")
            .fit(self.fixture())
            .sample("Weekday", 5000, np.random.default_rng(12))
        )
        self.assertLess(
            abs(
                pd.Series(sampled[:, 1]).corr(
                    pd.Series(sampled[:, 2]), method="spearman"
                )
            ),
            0.05,
        )

    def test_nearest_neighbors_keep_paired_energy(self):
        sampled = (
            MarkModel("circular_neighbors", neighbors=50)
            .fit(self.fixture())
            .sample("Weekday", 500, np.random.default_rng(15))
        )
        np.testing.assert_allclose(sampled[:, 2], 2 * sampled[:, 1])
        self.assertLess(
            pd.Series(sampled[:, 0]).corr(pd.Series(sampled[:, 1]), method="spearman"),
            -0.8,
        )

    def test_calendar_sampler_rejects_future_training_records(self):
        model = MarkModel("calendar_bootstrap").fit(self.fixture())
        with self.assertRaisesRegex(ValueError, "precede"):
            model.sample(
                "Weekday", 10, np.random.default_rng(1), date=pd.Timestamp("2024-01-05")
            )

    def test_recent_weighting_adapts_to_a_shift(self):
        frame = self.fixture()
        frame.loc[frame.date < "2024-02-01", "energy"] = 1.0
        frame.loc[frame.date >= "2024-02-01", "energy"] = 20.0
        result = (
            MarkModel("calendar_bootstrap", half_life=7)
            .fit(frame)
            .sample(
                "Weekday",
                1000,
                np.random.default_rng(1),
                date=pd.Timestamp("2024-03-01"),
            )
        )
        self.assertGreater(result[:, 2].mean(), 18.0)

    def test_kde_handles_deterministic_lower_dimension(self):
        sampled = (
            MarkModel("joint_kde")
            .fit(self.fixture())
            .sample("Weekday", 100, np.random.default_rng(16))
        )
        np.testing.assert_allclose(sampled[:, 2], 2 * sampled[:, 1], rtol=1e-6)

    def test_occupancy_supports_microsecond_parquet_timestamps(self):
        starts = pd.Series(pd.to_datetime(["2024-01-01 23:00Z"])).dt.as_unit("us")
        frame = pd.DataFrame({"connection": starts, "dwell": [2.0]})
        grid = pd.date_range("2024-01-01 23:00Z", periods=3, freq="h")
        np.testing.assert_array_equal(occupancy(frame, grid), [1, 1, 0])

    def test_missing_ids_are_not_duplicate_sessions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.csv"
            pd.DataFrame(
                {
                    "_id": [None, None],
                    "siteID": [2, 2],
                    "connectionTime": ["2024-01-01 10:00Z", "2024-01-02 10:00Z"],
                    "disconnectTime": ["2024-01-01 12:00Z", "2024-01-02 12:00Z"],
                    "kWhDelivered": [2, 3],
                }
            ).to_csv(path, index=False)
            frame, audit = load_sessions(path)
            self.assertEqual(len(frame), 2)
            self.assertEqual(audit["duplicate_id_rows"], 0)

    def test_dst_adjustment_keeps_clock_feature_consistent(self):
        class FixedModel:
            def sample(self, day_type, count, rng, date=None):
                return np.tile([2.5, 2.0, 4.0], (count, 1))

        frame = pd.DataFrame(
            {"date": [pd.Timestamp("2024-03-10")], "dayType": ["Weekend"]}
        )
        result = generate(FixedModel(), frame, 1)
        self.assertEqual(result.arrival.iloc[0], 3.0)
        self.assertEqual(
            result.connection.dt.tz_convert("America/Los_Angeles").dt.hour.iloc[0], 3
        )
        self.assertEqual(
            (result.departure - result.connection).dt.total_seconds().iloc[0], 7200
        )


if __name__ == "__main__":
    unittest.main()
