"""Focused tests for analytics and policy comparison."""
from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Iterable, Sequence

# Allow direct execution to import modules from the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analytics import analytics


def write_csv(path: str, rows: Iterable[Sequence[object]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)


class TestAnalytics(unittest.TestCase):
    def test_policy_parsing(self) -> None:
        self.assertEqual(
            analytics.parse_policy_list(" cost, nearest_car,zone "),
            ["cost", "nearest_car", "zone"],
        )
        for value in ("", "cost,unknown"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                analytics.parse_policy_list(value)

    def test_single_run_metrics_and_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            passengers = os.path.join(directory, "passengers.csv")
            positions = os.path.join(directory, "positions.csv")
            output = os.path.join(directory, "report")
            write_csv(
                passengers,
                [
                    [
                        "time", "id", "source", "dest", "assigned_elevator",
                        "pickup_time", "dropoff_time", "wait_time",
                        "travel_time", "total_time",
                    ],
                    [0, "R1", 1, 5, 1, 0, 4, 0, 4, 4],
                    [1, "R2", 5, 2, 2, 5, 8, 4, 3, 7],
                ],
            )
            write_csv(
                positions,
                [
                    ["time", "elevator_1", "elevator_2"],
                    [0, 1, 1],
                    [1, 2, 1],
                    [2, 3, 2],
                ],
            )

            report = analytics.ElevatorAnalytics(passengers, positions)
            self.assertEqual(
                report.distance_traveled(), {"elevator_1": 2, "elevator_2": 1}
            )
            self.assertEqual(report.wait_statistics()["mean"], 2)
            self.assertEqual(report.total_statistics()["max"], 7)
            self.assertEqual(report.utilization().to_dict(), {1: 1, 2: 1})
            report.generate(output)
            self.assertEqual(
                sorted(os.listdir(output)),
                ["analytics_dashboard.png", "summary.txt"],
            )

    def test_policy_comparison_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            requests = os.path.join(directory, "requests.csv")
            output = os.path.join(directory, "comparison")
            write_csv(
                requests,
                [
                    ["time", "id", "source", "dest"],
                    [0, "R1", 1, 8],
                    [2, "R2", 7, 2],
                ],
            )
            analytics.run_comparison(
                argparse.Namespace(
                    input=requests,
                    floors=8,
                    elevators=2,
                    capacity=4,
                    policies="cost,nearest_car",
                    output=output,
                )
            )

            self.assertEqual(
                sorted(os.listdir(output)),
                [
                    "comparison.csv",
                    "comparison_dashboard.png",
                    "comparison_summary.txt",
                ],
            )
            with open(
                os.path.join(output, "comparison.csv"),
                newline="",
                encoding="utf-8",
            ) as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["policy"] for row in rows], ["cost", "nearest_car"])
            self.assertTrue(all(row["requests_served"] == "2" for row in rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
