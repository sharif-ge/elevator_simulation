"""Discrete-time destination-dispatch elevator simulation.

The simulator assigns timed passenger requests to a configurable fleet and
moves each car at most one floor per tick. It supports multiple assignment
policies, capacity limits, optional express cars, and SCAN-style routing. See
README.md for usage and design notes.
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, TextIO, Tuple


ASSIGNMENT_POLICIES: Tuple[str, ...] = ("cost", "round_robin", "nearest_car", "zone")
PICKUP = "pickup"
DROPOFF = "dropoff"
DEFAULT_POSITION_LOG = os.path.join("outputs", "elevator_positions_log.csv")
DEFAULT_PASSENGER_SUMMARY = os.path.join("outputs", "passenger_summary.csv")


@dataclass(eq=False)
class PassengerRequest:
    """Passenger request details plus timing fields populated by the run."""

    request_time: int
    id: str
    source: int
    dest: int
    assigned_elevator: Optional[int] = None
    pickup_time: Optional[int] = None
    dropoff_time: Optional[int] = None
    wait_time: Optional[int] = None
    travel_time: Optional[int] = None
    total_time: Optional[int] = None

    def __post_init__(self) -> None:
        self.request_time = int(self.request_time)
        self.source = int(self.source)
        self.dest = int(self.dest)

    def mark_picked_up(self, current_time: int) -> None:
        """Record boarding and derive passenger wait time."""
        self.pickup_time = current_time
        self.wait_time = current_time - self.request_time

    def mark_dropped_off(self, current_time: int) -> None:
        """Record completion and derive travel and total time."""
        if self.pickup_time is None or self.wait_time is None:
            raise RuntimeError(f"Request {self.id} cannot exit before boarding.")
        self.dropoff_time = current_time
        self.travel_time = current_time - self.pickup_time
        self.total_time = self.wait_time + self.travel_time


@dataclass(eq=False)
class Stop:
    """One pending pickup or dropoff in an elevator's route."""

    floor: int
    action: str
    request: PassengerRequest

    def __post_init__(self) -> None:
        self.floor = int(self.floor)


@dataclass(frozen=True)
class SimulationConfig:
    """Validated settings used to construct a simulation fleet."""

    floors: int
    elevators: int
    capacity: int
    assignment_policy: str = "cost"
    express_elevators: int = 0
    express_stops: Optional[Tuple[int, ...]] = None

    def __post_init__(self) -> None:
        if self.floors < 2:
            raise ValueError("The number of floors must be at least 2.")
        if self.elevators < 1:
            raise ValueError("The elevator count must be at least 1.")
        if self.capacity < 1:
            raise ValueError("The elevator capacity must be at least 1.")
        if self.assignment_policy not in ASSIGNMENT_POLICIES:
            raise ValueError(
                f"Unknown assignment_policy {self.assignment_policy!r}; "
                f"choose one of {ASSIGNMENT_POLICIES}."
            )
        if not 0 <= self.express_elevators <= self.elevators:
            raise ValueError(
                "express_elevators must be between 0 and the total elevator "
                f"count ({self.elevators})."
            )
        if self.express_elevators and not self.express_stops:
            raise ValueError(
                "express_stops must be provided when express_elevators > 0."
            )
        for floor in self.express_stops or ():
            if not 1 <= floor <= self.floors:
                raise ValueError(
                    f"Express stop floor {floor} is outside the valid range "
                    f"1..{self.floors}."
                )

    @property
    def express_floor_set(self) -> Optional[set[int]]:
        """Return allowed express floors, including the lobby."""
        if not self.express_elevators:
            return None
        return {1, *(self.express_stops or ())}


def parse_requests(
    input_stream: TextIO,
    floors: int,
    reachable_floors: Optional[set[int]],
) -> List[PassengerRequest]:
    """Parse, validate, and sort requests from CSV."""
    requests = []
    seen_ids = set()
    reader = csv.reader(input_stream)
    for fields in reader:
        line_no = reader.line_num
        if not fields or not any(field.strip() for field in fields):
            continue
        fields = [field.strip() for field in fields]
        if [field.lower() for field in fields] == ["time", "id", "source", "dest"]:
            continue
        if len(fields) != 4:
            raise ValueError(
                f"Malformed request on line {line_no}: expected "
                "time,id,source,dest."
            )

        try:
            request = PassengerRequest(*fields)
        except ValueError as error:
            raise ValueError(
                f"Malformed request on line {line_no}: time, source, and "
                "destination must be integers."
            ) from error
        if not request.id:
            raise ValueError(f"Request on line {line_no} must have a passenger ID.")
        if request.id in seen_ids:
            raise ValueError(f"Duplicate passenger ID {request.id!r} on line {line_no}.")
        if request.request_time < 0:
            raise ValueError(f"Request {request.id} has a negative request time.")
        for label, floor in (("source", request.source), ("destination", request.dest)):
            if not 1 <= floor <= floors:
                raise ValueError(
                    f"Request {request.id} has {label} floor {floor} outside "
                    f"the valid range 1..{floors}."
                )
        if request.source == request.dest:
            raise ValueError(f"Request {request.id} must move to a different floor.")
        if reachable_floors is not None and not {
            request.source,
            request.dest,
        }.issubset(reachable_floors):
            raise ValueError(f"Request {request.id} cannot be served by this fleet.")

        seen_ids.add(request.id)
        requests.append(request)

    return sorted(requests, key=lambda request: (request.request_time, request.id))


class Elevator:
    """State and per-tick behavior for one elevator car."""

    def __init__(
        self,
        elevator_id: int,
        floors: int,
        capacity: int,
        start_floor: int = 1,
        allowed_floors: Optional[Iterable[int]] = None,
    ) -> None:
        self.elevator_id: int = elevator_id
        self.floors: int = floors
        self.capacity: int = capacity
        self.current_floor: int = start_floor
        self.direction: int = 0
        self.passengers: List[PassengerRequest] = []
        self.route: List[Stop] = []
        self.allowed_floors: Optional[set[int]] = (
            set(allowed_floors) if allowed_floors is not None else None
        )

    @property
    def is_express(self) -> bool:
        return self.allowed_floors is not None

    def can_serve(self, request: PassengerRequest) -> bool:
        """Return True if this elevator is physically able to fulfill request.

        A regular elevator (allowed_floors is None) can serve any request.
        An express elevator can only serve a request whose source AND
        destination are both floors it is willing to stop at.
        """
        if self.allowed_floors is None:
            return True
        return (
            request.source in self.allowed_floors
            and request.dest in self.allowed_floors
        )

    def add_request(self, request: PassengerRequest) -> None:
        """Add the pickup/dropoff pair for an assigned request."""
        self.route.append(Stop(request.source, PICKUP, request))
        self.route.append(Stop(request.dest, DROPOFF, request))
        request.assigned_elevator = self.elevator_id

    def next_goal(self) -> Optional[Stop]:
        """Return the next actionable stop using SCAN-style routing.

        Dropoffs are actionable only after boarding, and pickups only while
        capacity is available.
        """
        return self._next_goal_for_state(
            self.route,
            self.passengers,
            self.capacity,
            self.current_floor,
            self.direction,
        )

    @staticmethod
    def _next_goal_for_state(
        route: Sequence[Stop],
        passengers: Sequence[PassengerRequest],
        capacity: int,
        current_floor: int,
        direction: int,
    ) -> Optional[Stop]:
        """Select the next SCAN stop from an elevator-state snapshot."""
        has_room = len(passengers) < capacity
        actionable = [
            stop for stop in route
            if (stop.action == DROPOFF and stop.request in passengers)
            or (stop.action == PICKUP and has_room)
        ]
        if not actionable:
            return None

        if direction > 0:
            ahead = [stop for stop in actionable if stop.floor >= current_floor]
            candidates = ahead if ahead else actionable
        elif direction < 0:
            ahead = [stop for stop in actionable if stop.floor <= current_floor]
            candidates = ahead if ahead else actionable
        else:
            candidates = actionable

        return min(
            candidates,
            key=lambda stop: (abs(stop.floor - current_floor), stop.floor),
        )

    def process_current_floor(self, current_time: int) -> None:
        """Board/alight passengers waiting at the current floor.

        Dropoffs are processed before pickups: passengers exiting free up
        capacity that a waiting passenger at the same floor may need in
        order to board within this same tick.
        """
        processed: set[Stop] = set()

        dropoffs = [
            stop
            for stop in self.route
            if stop.floor == self.current_floor and stop.action == DROPOFF
        ]
        for stop in dropoffs:
            request = stop.request
            if request in self.passengers:
                self.passengers.remove(request)
                request.mark_dropped_off(current_time)
                processed.add(stop)

        pickups = [
            stop
            for stop in self.route
            if stop.floor == self.current_floor and stop.action == PICKUP
        ]
        for stop in pickups:
            request = stop.request
            if request in self.passengers:
                processed.add(stop)
                continue
            if len(self.passengers) < self.capacity:
                self.passengers.append(request)
                request.mark_picked_up(current_time)
                processed.add(stop)

        self.route = [stop for stop in self.route if stop not in processed]

    def step(self, current_time: int) -> None:
        """Advance this elevator by at most one floor and process arrivals."""
        self.process_current_floor(current_time)
        goal = self.next_goal()
        if goal is None:
            self.direction = 0
            return

        if goal.floor > self.current_floor:
            self.direction = 1
            self.current_floor += 1
        elif goal.floor < self.current_floor:
            self.direction = -1
            self.current_floor -= 1
        else:
            self.direction = 0

        arrival_time = current_time + 1 if self.direction else current_time
        self.process_current_floor(arrival_time)

    def projected_cost(self, request: PassengerRequest) -> float:
        """Return the request's incremental route cost for this elevator.

        The estimate runs lightweight snapshots of the elevator's current SCAN
        route with and without the new request. Its cost is the increase
        in the sum of remaining passenger completion times. Only work already
        assigned to this car is visible, so the estimate cannot peek at future
        requests waiting in the simulation input.
        """
        baseline = self._remaining_completion_time_sum()
        with_request = self._remaining_completion_time_sum(request)
        return float(with_request - baseline)

    def _remaining_completion_time_sum(
        self, extra_request: Optional[PassengerRequest] = None
    ) -> int:
        """Simulate this car's current route and sum relative completion times."""
        route = list(self.route)
        passengers = list(self.passengers)
        if extra_request is not None:
            route.extend([
                Stop(extra_request.source, PICKUP, extra_request),
                Stop(extra_request.dest, DROPOFF, extra_request),
            ])

        active_requests = set(passengers)
        active_requests.update(stop.request for stop in route)
        completion_times: Dict[PassengerRequest, int] = {}
        current_floor = self.current_floor
        direction = self.direction
        elapsed = 0

        while route or passengers:
            processed: set[Stop] = set()
            for stop in route:
                if (
                    stop.floor == current_floor
                    and stop.action == DROPOFF
                    and stop.request in passengers
                ):
                    passengers.remove(stop.request)
                    completion_times[stop.request] = elapsed
                    processed.add(stop)

            for stop in route:
                if stop.floor != current_floor or stop.action != PICKUP:
                    continue
                if stop.request in passengers:
                    processed.add(stop)
                elif len(passengers) < self.capacity:
                    passengers.append(stop.request)
                    processed.add(stop)

            route = [stop for stop in route if stop not in processed]
            if not route and not passengers:
                break

            goal = self._next_goal_for_state(
                route, passengers, self.capacity, current_floor, direction
            )
            if goal is None:
                raise RuntimeError("Projected elevator route cannot make progress.")

            direction = (goal.floor > current_floor) - (goal.floor < current_floor)
            current_floor += direction
            elapsed += 1

        return sum(completion_times[request] for request in active_requests)


class SimulationReporter:
    """Write files and render summaries from a completed simulation."""

    PASSENGER_COLUMNS = [
        "time",
        "id",
        "source",
        "dest",
        "assigned_elevator",
        "pickup_time",
        "dropoff_time",
        "wait_time",
        "travel_time",
        "total_time",
    ]

    def __init__(self, simulation: ElevatorSimulation) -> None:
        self.simulation = simulation

    @staticmethod
    def summarize(values: Sequence[int]) -> Dict[str, float]:
        """Return min, max, and average for a non-empty sequence."""
        return {
            "min": min(values),
            "max": max(values),
            "avg": sum(values) / len(values),
        }

    @staticmethod
    def _ensure_parent(path: str) -> None:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def write_outputs(self) -> None:
        self.write_position_log()
        self.write_passenger_summary()

    def write_position_log(self) -> None:
        simulation = self.simulation
        self._ensure_parent(simulation.log_path)
        with open(
            simulation.log_path, "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv.writer(handle)
            columns = ["time"] + [
                f"elevator_{index + 1}"
                for index in range(len(simulation.elevators))
            ]
            writer.writerow(columns)
            writer.writerows(simulation.log_rows)

    def write_passenger_summary(self) -> None:
        simulation = self.simulation
        self._ensure_parent(simulation.passenger_summary_path)
        with open(
            simulation.passenger_summary_path,
            "w",
            newline="",
            encoding="utf-8",
        ) as handle:
            writer = csv.writer(handle)
            writer.writerow(self.PASSENGER_COLUMNS)
            for request in sorted(
                simulation.assigned_requests,
                key=lambda item: (item.request_time, item.id),
            ):
                writer.writerow([
                    request.request_time,
                    request.id,
                    request.source,
                    request.dest,
                    request.assigned_elevator,
                    request.pickup_time,
                    request.dropoff_time,
                    request.wait_time,
                    request.travel_time,
                    request.total_time,
                ])

    def print_summary(self) -> None:
        print("\n".join(self.summary_lines()))

    def summary_lines(self) -> List[str]:
        simulation = self.simulation
        lines = [
            "Simulation completed.",
            f"- Positions log: {simulation.log_path}",
            f"- Passenger summary: {simulation.passenger_summary_path}",
        ]
        wait_times = self._timings("wait_time")
        if not wait_times:
            return lines + [
                "- Requests served: 0",
                "- Simulation horizon: 0 time units",
            ]

        travel_times = self._timings("travel_time")
        total_times = self._timings("total_time")
        wait_summary = self.summarize(wait_times)
        travel_summary = self.summarize(travel_times)
        total_summary = self.summarize(total_times)
        lines.append(f"- Assignment policy: {simulation.assignment_policy}")
        if simulation.express_elevator_count:
            lines.append(
                f"- Express elevators: {simulation.express_elevator_count}"
            )
        lines.extend([
            f"- Requests served: {len(simulation.assigned_requests)}",
            f"- Simulation horizon: {simulation.total_time_units} time units",
            self._timing_line("wait", wait_summary),
            self._timing_line("travel", travel_summary),
            self._timing_line("total", total_summary),
        ])
        return lines

    def _timings(self, attribute: str) -> List[int]:
        values = (
            getattr(request, attribute)
            for request in self.simulation.assigned_requests
        )
        return [value for value in values if value is not None]

    @staticmethod
    def _timing_line(label: str, summary: Dict[str, float]) -> str:
        return (
            f"- Min / Max / Average {label} time: {summary['min']:.2f} / "
            f"{summary['max']:.2f} / {summary['avg']:.2f}"
        )


class ElevatorSimulation:
    """Coordinate elevator assignment and the discrete-time run loop."""

    def __init__(
        self,
        floors: int,
        elevators: int,
        capacity: int,
        input_stream: Optional[TextIO] = None,
        log_path: str = DEFAULT_POSITION_LOG,
        passenger_summary_path: str = DEFAULT_PASSENGER_SUMMARY,
        assignment_policy: str = "cost",
        express_elevators: int = 0,
        express_stops: Optional[Iterable[int]] = None,
    ) -> None:
        normalized_stops = (
            tuple(int(floor) for floor in express_stops)
            if express_stops is not None
            else None
        )
        self.config = SimulationConfig(
            floors=floors,
            elevators=elevators,
            capacity=capacity,
            assignment_policy=assignment_policy,
            express_elevators=express_elevators,
            express_stops=normalized_stops,
        )

        # Public aliases preserve the original API used by callers and tests.
        self.floors = self.config.floors
        self.capacity = self.config.capacity
        self.assignment_policy = self.config.assignment_policy
        self.express_elevator_count = self.config.express_elevators

        express_floors = self.config.express_floor_set
        self.elevators = [
            Elevator(
                elevator_id=index + 1,
                floors=self.floors,
                capacity=self.capacity,
                allowed_floors=(
                    express_floors
                    if index < self.express_elevator_count
                    else None
                ),
            )
            for index in range(self.config.elevators)
        ]
        self.zone_bounds = self._compute_zones(self.floors, len(self.elevators))
        self._reachable_floors = self._find_reachable_floors()

        self._round_robin_cursor: int = 0

        self.future_requests: List[PassengerRequest] = []
        self._future_index: int = 0
        self.pending_requests: List[PassengerRequest] = []
        self.assigned_requests: List[PassengerRequest] = []
        self.log_path: str = log_path
        self.passenger_summary_path: str = passenger_summary_path
        self.log_rows: List[List[int]] = []
        self.current_time: int = 0
        self.total_time_units: int = 0
        self.logger = logging.getLogger(__name__)

        if input_stream is not None:
            self.load_requests(input_stream)

    def _find_reachable_floors(self) -> Optional[set[int]]:
        """Return constrained fleet reach, or None when a regular car exists."""
        if any(elevator.allowed_floors is None for elevator in self.elevators):
            return None
        reachable = set()
        for elevator in self.elevators:
            reachable.update(elevator.allowed_floors or ())
        return reachable

    @staticmethod
    def _compute_zones(
        floors: int, elevator_count: int
    ) -> List[Optional[Tuple[int, int]]]:
        """Split [1, floors] into elevator_count contiguous zones.

        Any remainder floors (floors not evenly divisible by elevator
        count) are distributed one-per-zone starting from the first zone,
        so zone sizes differ by at most 1 floor. If there are more elevators
        than floors, the extra elevators have no primary zone.
        """
        base_size, remainder = divmod(floors, elevator_count)
        bounds: List[Optional[Tuple[int, int]]] = []
        low = 1
        for idx in range(elevator_count):
            if low > floors:
                bounds.append(None)
                continue
            size = base_size + (1 if idx < remainder else 0)
            high = min(low + size - 1, floors)
            bounds.append((low, high))
            low = high + 1
        return bounds

    def _log(self, level: int, message: str, *args: object) -> None:
        self.logger.log(level, message, *args)

    def load_requests(self, input_stream: TextIO) -> None:
        """Replace staged requests with validated requests from CSV."""
        self.future_requests = parse_requests(
            input_stream, self.floors, self._reachable_floors
        )
        self._future_index = 0
        self._log(logging.INFO, "Loaded %s request(s).", len(self.future_requests))

    def enqueue_ready_requests(self) -> None:
        """Move newly available requests into the pending queue."""
        arrivals = []
        while (
            self._future_index < len(self.future_requests)
            and self.future_requests[self._future_index].request_time == self.current_time
        ):
            arrivals.append(self.future_requests[self._future_index])
            self._future_index += 1

        if not arrivals:
            return

        self.pending_requests.extend(arrivals)
        self._log(
            logging.INFO,
            "Enqueued %s ready request(s) at time %s.",
            len(arrivals),
            self.current_time,
        )

    def _eligible_elevators(self, request: PassengerRequest) -> List[Elevator]:
        """Elevators physically able to fulfill this request.

        Capacity is intentionally not part of assignment eligibility:
        destination dispatch assigns a request immediately, while boarding
        capacity is enforced later when an elevator reaches the pickup floor.
        """
        return [
            elevator for elevator in self.elevators
            if elevator.can_serve(request)
        ]

    def select_best_elevator(self, request: PassengerRequest) -> Optional[Elevator]:
        """Choose an elevator for request using the configured assignment policy."""
        if self.assignment_policy == "round_robin":
            return self._select_round_robin(request)
        if self.assignment_policy == "nearest_car":
            return self._select_nearest_car(request)
        if self.assignment_policy == "zone":
            return self._select_zone(request)
        return self._select_cost(request)

    def _select_cost(self, request: PassengerRequest) -> Optional[Elevator]:
        candidates = self._eligible_elevators(request)
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda elevator: (
                elevator.projected_cost(request),
                len(elevator.route),
                len(elevator.passengers),
                elevator.elevator_id,
            ),
        )

    def _select_round_robin(self, request: PassengerRequest) -> Optional[Elevator]:
        """Return the next eligible car from a persistent fleet cursor."""
        n = len(self.elevators)
        for offset in range(n):
            idx = (self._round_robin_cursor + offset) % n
            elevator = self.elevators[idx]
            if elevator.can_serve(request):
                self._round_robin_cursor = (idx + 1) % n
                return elevator
        return None

    def _select_nearest_car(self, request: PassengerRequest) -> Optional[Elevator]:
        """Return the eligible car closest to the pickup floor."""
        candidates = self._eligible_elevators(request)
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda elevator: (
                abs(elevator.current_floor - request.source),
                elevator.elevator_id,
            ),
        )

    def _select_zone(self, request: PassengerRequest) -> Optional[Elevator]:
        """Prefer the pickup-zone owner, falling back to cost selection."""
        owner_idx = None
        for idx, bounds in enumerate(self.zone_bounds):
            if bounds is None:
                continue
            low, high = bounds
            if low <= request.source <= high:
                owner_idx = idx
                break

        if owner_idx is not None:
            owner = self.elevators[owner_idx]
            if owner.can_serve(request):
                return owner

        # Fall back to cost-based selection among whatever else is eligible.
        return self._select_cost(request)

    def assign_pending_requests(self) -> None:
        """Assign every currently pending request that has an eligible elevator."""
        remaining = []
        for request in self.pending_requests:
            elevator = self.select_best_elevator(request)
            if elevator is None:
                self._log(
                    logging.WARNING,
                    "Request %s could not be assigned; leaving it pending.",
                    request.id,
                )
                remaining.append(request)
                continue
            elevator.add_request(request)
            self.assigned_requests.append(request)
            self._log(
                logging.INFO,
                "Assigned request %s to elevator_%s.",
                request.id,
                elevator.elevator_id,
            )

        self.pending_requests = remaining

    def record_positions(self) -> None:
        """Append the current elevator positions."""
        positions = [elevator.current_floor for elevator in self.elevators]
        self.log_rows.append([self.current_time, *positions])

    def run(self) -> None:
        """Run until no future, pending, routed, or onboard work remains."""
        self.record_positions()
        self._log(
            logging.INFO,
            "Simulation start: floors=%s elevators=%s capacity=%s policy=%s.",
            self.floors,
            len(self.elevators),
            self.capacity,
            self.assignment_policy,
        )

        while True:
            self._log_tick()
            self.enqueue_ready_requests()
            self.assign_pending_requests()

            if not self._work_remains():
                self._log(
                    logging.INFO,
                    "Simulation terminating at time %s because no work remains.",
                    self.current_time,
                )
                break

            self._step_active_elevators()
            self.current_time += 1
            self.total_time_units = self.current_time
            self.record_positions()

    def _work_remains(self) -> bool:
        future_work = self._future_index < len(self.future_requests)
        active_car = any(
            elevator.route or elevator.passengers for elevator in self.elevators
        )
        return bool(self.pending_requests or future_work or active_car)

    def _log_tick(self) -> None:
        future_count = len(self.future_requests) - self._future_index
        self._log(
            logging.INFO,
            "Tick %s: pending=%s future=%s.",
            self.current_time,
            len(self.pending_requests),
            future_count,
        )

    def _step_active_elevators(self) -> None:
        for elevator in self.elevators:
            if not elevator.route and not elevator.passengers:
                continue
            self._log(
                logging.INFO,
                "Elevator_%s before step: floor=%s route=%s passengers=%s.",
                elevator.elevator_id,
                elevator.current_floor,
                [stop.floor for stop in elevator.route],
                [request.id for request in elevator.passengers],
            )
            elevator.step(self.current_time)
            self._log(
                logging.INFO,
                "Elevator_%s after step: floor=%s.",
                elevator.elevator_id,
                elevator.current_floor,
            )

    def write_outputs(self) -> None:
        """Write the position log and per-passenger summary CSV files."""
        SimulationReporter(self).write_outputs()

    def print_summary(self) -> None:
        """Print a compact human-readable run summary."""
        SimulationReporter(self).print_summary()


def _parse_express_stops(value: str) -> Optional[List[int]]:
    """Parse comma-separated express-stop floors from the CLI."""
    if not value:
        return None
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Build and parse command-line arguments for the simulator."""
    parser = argparse.ArgumentParser(
        description="Discrete-time destination-dispatch elevator simulation."
    )
    parser.add_argument(
        "--floors", type=int, default=10, help="Number of building floors."
    )
    parser.add_argument(
        "--elevators", type=int, default=3, help="Number of elevator cars."
    )
    parser.add_argument(
        "--capacity", type=int, default=6, help="Maximum passengers per car."
    )
    parser.add_argument(
        "--input",
        default="-",
        help="Request CSV in time,id,source,dest format; '-' reads stdin.",
    )
    parser.add_argument(
        "--log", default=DEFAULT_POSITION_LOG, help="Position-log output path."
    )
    parser.add_argument(
        "--passenger-summary",
        default=DEFAULT_PASSENGER_SUMMARY,
        help="Passenger timing-summary output path.",
    )
    parser.add_argument(
        "--assignment-policy",
        choices=ASSIGNMENT_POLICIES,
        default="cost",
        help="Assignment policy (default: cost).",
    )
    parser.add_argument(
        "--express-elevators",
        type=int,
        default=0,
        help="Number of cars, starting at car 1, configured as express.",
    )
    parser.add_argument(
        "--express-stops",
        type=_parse_express_stops,
        help="Comma-separated express floors; floor 1 is added automatically.",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable tick-level logging."
    )
    return parser.parse_args(argv)


def read_input_stream(path: str) -> TextIO:
    """Return stdin for '-' or an open input file handle otherwise."""
    if path == "-":
        return sys.stdin
    return open(path, "r", encoding="utf-8", newline="")


def main(argv: Optional[Sequence[str]] = None) -> None:
    """CLI entry point."""
    argv = argv if argv is not None else sys.argv[1:]
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    stream = read_input_stream(args.input)
    try:
        simulation = ElevatorSimulation(
            floors=args.floors,
            elevators=args.elevators,
            capacity=args.capacity,
            input_stream=stream,
            log_path=args.log,
            passenger_summary_path=args.passenger_summary,
            assignment_policy=args.assignment_policy,
            express_elevators=args.express_elevators,
            express_stops=args.express_stops,
        )
        simulation.run()
        simulation.write_outputs()
        simulation.print_summary()
    finally:
        if args.input != "-":
            stream.close()


if __name__ == "__main__":
    main()
