"""Run examples of both analytics modes using the included request data."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict

# Allow direct execution to import modules from the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analytics.analytics import ElevatorAnalytics, PolicyComparison
from sample_runs.sample_execution import execute_dispatch
from elevator_simulation import ASSIGNMENT_POLICIES


def generate_sample_analytics() -> Dict[str, str]:
    """Generate a single-run report and an assignment-policy comparison."""
    request_path = os.path.join("inputs", "request.csv")
    position_path = os.path.join("outputs", "elevator_positions_log.csv")
    passenger_path = os.path.join("outputs", "passenger_summary.csv")
    report_dir = os.path.join("outputs", "analysis")
    comparison_dir = os.path.join("outputs", "analysis_compare")

    execute_dispatch(
        request_path,
        floors=60,
        elevators=3,
        capacity=6,
        log_path=position_path,
        passenger_summary_path=passenger_path,
    )

    ElevatorAnalytics(passenger_path, position_path).generate(report_dir)
    PolicyComparison(
        input_file=request_path,
        floors=60,
        elevators=3,
        capacity=6,
        policies=ASSIGNMENT_POLICIES,
    ).generate(comparison_dir)

    return {
        "report": report_dir,
        "comparison": comparison_dir,
    }


def main() -> None:
    """Generate the sample report and policy-comparison outputs."""
    generated = generate_sample_analytics()
    print(f"Single-run report: {generated['report']}")
    print(f"Policy comparison: {generated['comparison']}")


if __name__ == "__main__":
    main()
