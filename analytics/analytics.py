"""Generate single-run analytics or compare assignment policies.

Run both example modes::

    .venv/bin/python sample_runs/sample_analytics.py

Generate the simulator outputs, then create a report::

    .venv/bin/python sample_runs/sample_execution.py
    .venv/bin/python analytics/analytics.py report \
        --requests outputs/passenger_summary.csv \
        --positions outputs/elevator_positions_log.csv \
        --output outputs/analysis

Compare assignment policies::

    .venv/bin/python analytics/analytics.py compare \
        --input inputs/request.csv --floors 60
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# Allow direct execution to import modules from the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault(
    "MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "matplotlib-cache")
)
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from elevator_simulation import ASSIGNMENT_POLICIES, ElevatorSimulation


class ElevatorAnalytics:
    """Create charts and summary metrics from one completed simulation run."""

    def __init__(self, request_file: str, position_file: str) -> None:
        self.requests = pd.read_csv(request_file)
        self.positions = pd.read_csv(position_file)

    def distance_traveled(self) -> Dict[str, float]:
        """Return total floor-to-floor travel distance per elevator."""
        return {
            elevator: self.positions[elevator].diff().abs().fillna(0).sum()
            for elevator in self.positions.columns[1:]
        }

    def utilization(self) -> Optional[pd.Series]:
        """Return request count per assigned elevator, when available."""
        if "assigned_elevator" not in self.requests.columns:
            return None
        return self.requests.groupby("assigned_elevator").size().sort_index()

    def floor_activity(self) -> Tuple[pd.Series, pd.Series]:
        """Return pickup and dropoff counts by floor."""
        pickups = self.requests.source.value_counts().sort_index()
        dropoffs = self.requests.dest.value_counts().sort_index()
        return pickups, dropoffs

    def _statistics(self, column: str) -> Optional[Dict[str, float]]:
        """Return descriptive statistics for a request-data column."""
        if column not in self.requests.columns:
            return None
        values = self.requests[column]
        return {
            "min": values.min(),
            "max": values.max(),
            "mean": values.mean(),
            "median": values.median(),
            "std": values.std(),
            "90%": values.quantile(0.90),
            "95%": values.quantile(0.95),
        }

    def wait_statistics(self) -> Optional[Dict[str, float]]:
        return self._statistics("wait_time")

    def total_statistics(self) -> Optional[Dict[str, float]]:
        return self._statistics("total_time")

    def plot_dashboard(self, outdir: str) -> None:
        """Write one consolidated dashboard image for a completed run."""
        fig, axes = plt.subplots(2, 3, figsize=(18, 9))
        axes = axes.flatten()

        time = self.positions.time
        for elevator in self.positions.columns[1:]:
            axes[0].plot(time, self.positions[elevator], label=elevator)
        axes[0].set(xlabel="Simulation Time", ylabel="Floor", title="Elevator Positions")
        axes[0].legend()

        self._plot_distribution(
            axes[1], "wait_time", "Wait Time", "Wait Time Distribution"
        )
        self._plot_distribution(
            axes[2], "total_time", "Total Time", "Total Time Distribution"
        )

        distances = self.distance_traveled()
        axes[3].bar(distances.keys(), distances.values())
        axes[3].set(ylabel="Floors", title="Distance Traveled")

        utilization = self.utilization()
        if utilization is None:
            axes[4].text(0.5, 0.5, "assignment unavailable", ha="center", va="center")
        else:
            utilization.plot(kind="bar", ax=axes[4])
            axes[4].set_ylabel("Requests Served")
        axes[4].set_title("Elevator Utilization")

        pickups, dropoffs = self.floor_activity()
        floors = sorted(set(pickups.index).union(dropoffs.index))
        x_values = list(range(len(floors)))
        width = 0.4
        axes[5].bar(
            [value - width / 2 for value in x_values],
            [pickups.get(floor, 0) for floor in floors],
            width=width,
            label="Pickups",
        )
        axes[5].bar(
            [value + width / 2 for value in x_values],
            [dropoffs.get(floor, 0) for floor in floors],
            width=width,
            label="Dropoffs",
        )
        axes[5].set_xticks(x_values)
        axes[5].set_xticklabels(floors, rotation=45, ha="right")
        axes[5].set(xlabel="Floor", ylabel="Requests", title="Floor Activity")
        axes[5].legend()

        fig.suptitle("Elevator Simulation Analytics", fontsize=16)
        fig.tight_layout()
        fig.savefig(os.path.join(outdir, "analytics_dashboard.png"))
        plt.close(fig)

    def _plot_distribution(
        self, axis: object, column: str, xlabel: str, title: str
    ) -> None:
        """Plot a histogram or an unavailable-data message on an axis."""
        if column in self.requests.columns:
            axis.hist(self.requests[column], bins=15)
            axis.set(xlabel=xlabel, ylabel="Passengers")
        else:
            axis.text(0.5, 0.5, f"{column} unavailable", ha="center", va="center")
        axis.set_title(title)

    @staticmethod
    def _plain_value(value: object) -> object:
        """Convert pandas/numpy scalar values to normal Python values."""
        if hasattr(value, "item"):
            return value.item()
        if isinstance(value, dict):
            return {
                key: ElevatorAnalytics._plain_value(item)
                for key, item in value.items()
            }
        return value

    def write_summary(self, outdir: str) -> None:
        """Write a text summary of the core analytics metrics."""
        sections = [
            ("WAIT STATISTICS", self.wait_statistics()),
            ("TOTAL STATISTICS", self.total_statistics()),
            ("DISTANCE TRAVELED", self.distance_traveled()),
        ]
        with open(os.path.join(outdir, "summary.txt"), "w", encoding="utf-8") as handle:
            handle.write("ELEVATOR ANALYTICS\n\n")
            for heading, values in sections:
                handle.write(f"{heading}\n{self._plain_value(values)}\n\n")

            utilization = self.utilization()
            if utilization is not None:
                handle.write(f"UTILIZATION\n{utilization}\n")

    def generate(self, outdir: str) -> None:
        """Create the consolidated dashboard and text summary."""
        os.makedirs(outdir, exist_ok=True)
        self.plot_dashboard(outdir)
        self.write_summary(outdir)


class PolicyComparison:
    """Run the same raw requests through multiple assignment policies."""

    def __init__(
        self,
        input_file: str,
        floors: int,
        elevators: int,
        capacity: int,
        policies: Sequence[str],
    ) -> None:
        self.input_file = input_file
        self.floors = floors
        self.elevators = elevators
        self.capacity = capacity
        self.policies = policies

    def run_policy(self, policy: str, workdir: str) -> Dict[str, object]:
        """Run one policy and return its comparison metrics."""
        policy_dir = os.path.join(workdir, policy)
        os.makedirs(policy_dir, exist_ok=True)
        position_path = os.path.join(policy_dir, "elevator_positions_log.csv")
        passenger_path = os.path.join(policy_dir, "passenger_summary.csv")

        with open(self.input_file, "r", encoding="utf-8") as handle:
            simulation = ElevatorSimulation(
                floors=self.floors,
                elevators=self.elevators,
                capacity=self.capacity,
                input_stream=handle,
                log_path=position_path,
                passenger_summary_path=passenger_path,
                assignment_policy=policy,
            )
            simulation.run()
            simulation.write_outputs()

        analytics = ElevatorAnalytics(passenger_path, position_path)
        passengers = analytics.requests
        total_distance = sum(analytics.distance_traveled().values())
        return {
            "policy": policy,
            "requests_served": len(passengers),
            "horizon": simulation.total_time_units,
            "max_wait": passengers.wait_time.max(),
            "avg_wait": passengers.wait_time.mean(),
            "p90_wait": passengers.wait_time.quantile(0.90),
            "max_total": passengers.total_time.max(),
            "avg_total": passengers.total_time.mean(),
            "p90_total": passengers.total_time.quantile(0.90),
            "total_distance": total_distance,
        }

    def write_summary(self, results: pd.DataFrame, outdir: str) -> None:
        """Write the policy-comparison text report."""
        best_wait = results.sort_values(["avg_wait", "max_wait"]).iloc[0]
        best_total = results.sort_values(["avg_total", "max_total"]).iloc[0]
        best_distance = results.sort_values(["total_distance", "avg_total"]).iloc[0]

        path = os.path.join(outdir, "comparison_summary.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("POLICY COMPARISON\n\n")
            handle.write(f"Policies compared: {', '.join(results.policy)}\n")
            handle.write(f"Input: {self.input_file}\n")
            handle.write(
                f"Building: {self.floors} floors, {self.elevators} elevators, "
                f"capacity {self.capacity}\n\n"
            )
            handle.write(
                f"Best average wait: {best_wait.policy} ({best_wait.avg_wait:.2f})\n"
            )
            handle.write(
                f"Best average total time: {best_total.policy} "
                f"({best_total.avg_total:.2f})\n"
            )
            handle.write(
                f"Least elevator travel: {best_distance.policy} "
                f"({best_distance.total_distance:.2f} floors)\n\n"
            )
            handle.write(results.to_string(index=False))
            handle.write("\n")

    def plot_dashboard(self, results: pd.DataFrame, outdir: str) -> None:
        """Write one consolidated comparison chart across all policies."""
        fig, axes = plt.subplots(1, 3, figsize=(16, 4.5))
        metrics = [
            ("avg_wait", "Average Wait Time", "Average Wait Time by Policy"),
            ("avg_total", "Average Total Time", "Average Total Time by Policy"),
            ("total_distance", "Floors Traveled", "Total Elevator Travel by Policy"),
        ]
        for axis, (metric, ylabel, title) in zip(axes, metrics):
            axis.bar(results.policy, results[metric])
            axis.set(ylabel=ylabel, title=title)
            axis.tick_params(axis="x", rotation=20)
            for label in axis.get_xticklabels():
                label.set_ha("right")

        fig.suptitle("Assignment Policy Comparison", fontsize=16)
        fig.tight_layout()
        fig.savefig(os.path.join(outdir, "comparison_dashboard.png"))
        plt.close(fig)

    def generate(self, outdir: str) -> pd.DataFrame:
        """Run all policies and write consolidated comparison outputs."""
        os.makedirs(outdir, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="elevator_policy_compare_") as workdir:
            rows = [self.run_policy(policy, workdir) for policy in self.policies]

        results = pd.DataFrame(rows)
        results.to_csv(os.path.join(outdir, "comparison.csv"), index=False)
        self.plot_dashboard(results, outdir)
        self.write_summary(results, outdir)
        return results


def parse_policy_list(value: str) -> List[str]:
    """Parse and validate a comma-separated policy list."""
    policies = [item.strip() for item in value.split(",") if item.strip()]
    if not policies:
        raise ValueError("At least one assignment policy is required.")
    unknown = [policy for policy in policies if policy not in ASSIGNMENT_POLICIES]
    if unknown:
        raise ValueError(
            f"Unknown assignment policy/policies {', '.join(unknown)}; "
            f"choose from {', '.join(ASSIGNMENT_POLICIES)}."
        )
    return policies


def run_report(args: argparse.Namespace) -> None:
    ElevatorAnalytics(args.requests, args.positions).generate(args.output)


def run_comparison(args: argparse.Namespace) -> None:
    if args.floors < 2:
        raise ValueError("The number of floors must be at least 2.")
    if args.elevators < 1:
        raise ValueError("The elevator count must be at least 1.")
    if args.capacity < 1:
        raise ValueError("The elevator capacity must be at least 1.")
    comparison = PolicyComparison(
        input_file=args.input,
        floors=args.floors,
        elevators=args.elevators,
        capacity=args.capacity,
        policies=parse_policy_list(args.policies),
    )
    comparison.generate(args.output)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    report = subparsers.add_parser("report", help="Analyze one completed run.")
    report.add_argument("--requests", required=True, help="Passenger summary CSV.")
    report.add_argument("--positions", required=True, help="Elevator position CSV.")
    report.add_argument("--output", default=os.path.join("outputs", "analysis"))

    compare = subparsers.add_parser("compare", help="Compare assignment policies.")
    compare.add_argument("--input", required=True, help="Raw request CSV.")
    compare.add_argument(
        "--floors",
        required=True,
        type=int,
        help="Building height; required because requests do not declare it.",
    )
    compare.add_argument("--elevators", type=int, default=3)
    compare.add_argument("--capacity", type=int, default=6)
    compare.add_argument("--policies", default=",".join(ASSIGNMENT_POLICIES))
    compare.add_argument("--output", default=os.path.join("outputs", "analysis_compare"))
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Run the analytics CLI."""
    arguments = list(argv if argv is not None else sys.argv[1:])
    if not arguments:
        arguments = [
            "compare",
            "--input", os.path.join("inputs", "request.csv"),
            "--floors", "60",
        ]
    args = build_parser().parse_args(arguments)
    if args.command == "compare":
        run_comparison(args)
    else:
        run_report(args)


if __name__ == "__main__":
    main()
