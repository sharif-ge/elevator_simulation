import os
import sys
from pathlib import Path
from typing import Dict, Iterable, Optional, Union

# Allow direct execution to import modules from the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from elevator_simulation import ElevatorSimulation


def execute_dispatch(
    request_path: str,
    *,
    floors: int,
    elevators: int,
    capacity: int,
    log_path: str,
    passenger_summary_path: str = os.path.join(
        "outputs", "passenger_summary.csv"
    ),
    assignment_policy: str = "cost",
    express_elevators: int = 0,
    express_stops: Optional[Iterable[int]] = None,
) -> Dict[str, Union[int, str]]:
    """Run the simulator with a CSV input file and return output locations."""
    with open(request_path, "r", encoding="utf-8") as handle:
        sim = ElevatorSimulation(
            floors=floors,
            elevators=elevators,
            capacity=capacity,
            input_stream=handle,
            log_path=log_path,
            passenger_summary_path=passenger_summary_path,
            assignment_policy=assignment_policy,
            express_elevators=express_elevators,
            express_stops=express_stops,
        )
        sim.run()
        sim.write_outputs()
        sim.print_summary()

    return {
        "requests_served": len(sim.assigned_requests),
        "horizon": sim.total_time_units,
        "position_log": sim.log_path,
        "passenger_summary": sim.passenger_summary_path,
    }


def main() -> None:
    """Run one included scenario; change the selected option to try another."""
    input_options = [
        ("inputs/request.csv", 60), # 0
        ("inputs/basic_10_floor.csv", 10), # 1
        ("inputs/lobby_morning_up_peak.csv", 60), # 2
        ("inputs/evening_down_peak.csv", 60), # 3
        ("inputs/interfloor_mixed_traffic.csv", 60), # 4
        ("inputs/capacity_stress.csv", 10), # 5
        ("inputs/staggered_requests.csv", 60), # 6
        ("inputs/zone_policy_showcase.csv", 60), # 7
        ("inputs/express_elevator_showcase.csv", 60), # 8
        ("inputs/invalid_out_of_range.csv", 60), # 9
        ("inputs/invalid_same_floor.csv", 60), # 10
        ("inputs/invalid_duplicate_id.csv", 60), # 11
    ]
    request_path, floors = input_options[0] # Change the index to select a different input file.

    # Choose: "cost", "round_robin", "nearest_car", or "zone".
    assignment_policy = "cost"

    # To try express service, set this to 1 and provide stops such as
    # [20, 40, 60]. Floor 1 is included automatically.
    express_elevators = 0
    express_stops = None

    result = execute_dispatch(
        request_path,
        floors=floors,
        elevators=3,
        capacity=6,
        log_path=os.path.join("outputs", "elevator_positions_log.csv"),
        passenger_summary_path=os.path.join("outputs", "passenger_summary.csv"),
        assignment_policy=assignment_policy,
        express_elevators=express_elevators,
        express_stops=express_stops,
    )
    print(result)


if __name__ == "__main__":
    main()
