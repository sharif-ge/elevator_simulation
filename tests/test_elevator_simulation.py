"""Focused behavioral tests for the elevator simulator."""
from __future__ import annotations

import csv
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

# Allow direct execution to import modules from the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from elevator_simulation import (
    DROPOFF,
    PICKUP,
    Elevator,
    ElevatorSimulation,
    PassengerRequest,
    Stop,
    main,
)


def request(time: int, request_id: str, source: int, dest: int) -> PassengerRequest:
    return PassengerRequest(time, request_id, source, dest)


def run_sim(data: str, **overrides: object) -> ElevatorSimulation:
    settings = {"floors": 10, "elevators": 1, "capacity": 6}
    settings.update(overrides)
    simulation = ElevatorSimulation(input_stream=io.StringIO(data), **settings)
    simulation.run()
    return simulation


class TestRequestLifecycle(unittest.TestCase):
    def test_immediate_pickup_and_timing(self) -> None:
        completed = run_sim("0,R1,1,5\n").assigned_requests[0]
        self.assertEqual(completed.wait_time, 0)
        self.assertEqual(completed.travel_time, 4)
        self.assertEqual(completed.total_time, 4)

    def test_downward_trip_populates_all_times(self) -> None:
        completed = run_sim("0,R1,8,2\n").assigned_requests[0]
        self.assertGreater(completed.pickup_time, completed.request_time)
        self.assertGreater(completed.dropoff_time, completed.pickup_time)
        self.assertEqual(
            completed.total_time, completed.wait_time + completed.travel_time
        )

    def test_all_finite_requests_complete(self) -> None:
        simulation = run_sim("0,R1,1,10\n2,R2,10,1\n4,R3,5,8\n")
        self.assertEqual(len(simulation.assigned_requests), 3)
        self.assertFalse(simulation.pending_requests)
        self.assertTrue(all(r.total_time is not None for r in simulation.assigned_requests))


class TestRoutingAndCapacity(unittest.TestCase):
    def test_idle_car_selects_nearest_pickup(self) -> None:
        elevator = Elevator(1, floors=10, capacity=6, start_floor=5)
        elevator.route = [
            Stop(9, PICKUP, request(0, "far", 9, 10)),
            Stop(6, PICKUP, request(0, "near", 6, 7)),
        ]
        self.assertEqual(elevator.next_goal().floor, 6)

    def test_scan_prefers_stops_ahead(self) -> None:
        elevator = Elevator(1, floors=10, capacity=6, start_floor=5)
        elevator.direction = 1
        elevator.route = [
            Stop(4, PICKUP, request(0, "behind", 4, 2)),
            Stop(9, PICKUP, request(0, "ahead", 9, 10)),
        ]
        self.assertEqual(elevator.next_goal().floor, 9)

    def test_dropoff_is_ignored_before_boarding(self) -> None:
        passenger = request(0, "R1", 8, 2)
        elevator = Elevator(1, floors=10, capacity=6, start_floor=1)
        elevator.add_request(passenger)
        self.assertEqual(elevator.next_goal().action, PICKUP)

    def test_full_car_targets_dropoff(self) -> None:
        onboard = request(0, "onboard", 1, 9)
        onboard.mark_picked_up(0)
        waiting = request(0, "waiting", 2, 3)
        elevator = Elevator(1, floors=10, capacity=1, start_floor=1)
        elevator.passengers = [onboard]
        elevator.route = [
            Stop(9, DROPOFF, onboard),
            Stop(2, PICKUP, waiting),
        ]
        self.assertEqual(elevator.next_goal().floor, 9)

    def test_dropoff_frees_capacity_for_same_floor_pickup(self) -> None:
        exiting = request(0, "exit", 1, 5)
        exiting.mark_picked_up(0)
        boarding = request(0, "board", 5, 8)
        elevator = Elevator(1, floors=10, capacity=1, start_floor=5)
        elevator.passengers = [exiting]
        elevator.route = [Stop(5, DROPOFF, exiting), Stop(5, PICKUP, boarding)]
        elevator.process_current_floor(4)
        self.assertEqual(elevator.passengers, [boarding])
        self.assertEqual(exiting.dropoff_time, 4)
        self.assertEqual(boarding.pickup_time, 4)

    def test_capacity_delays_second_passenger(self) -> None:
        simulation = run_sim("0,R1,1,5\n0,R2,1,3\n", capacity=1)
        first, second = simulation.assigned_requests
        self.assertGreaterEqual(second.pickup_time, first.dropoff_time)


class TestInputValidation(unittest.TestCase):
    def test_header_blanks_and_out_of_order_rows(self) -> None:
        data = "time,id,source,dest\n\n5,R2,2,3\n0,R1,1,4\n"
        simulation = ElevatorSimulation(
            floors=10, elevators=1, capacity=6, input_stream=io.StringIO(data)
        )
        self.assertEqual([item.id for item in simulation.future_requests], ["R1", "R2"])

    def test_invalid_rows_are_rejected(self) -> None:
        cases = {
            "wrong field count": "0,R1,1,2,extra\n",
            "nonnumeric time": "now,R1,1,2\n",
            "empty passenger ID": "0,,1,2\n",
            "negative time": "-1,R1,1,2\n",
            "source below range": "0,R1,0,2\n",
            "destination above range": "0,R1,1,11\n",
            "same floor": "0,R1,3,3\n",
            "duplicate ID": "0,R1,1,2\n1,R1,2,3\n",
        }
        for label, data in cases.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                ElevatorSimulation(
                    floors=10,
                    elevators=1,
                    capacity=6,
                    input_stream=io.StringIO(data),
                )

    def test_future_request_is_not_enqueued_early(self) -> None:
        simulation = ElevatorSimulation(
            floors=10,
            elevators=1,
            capacity=6,
            input_stream=io.StringIO("5,R1,1,2\n"),
        )
        simulation.enqueue_ready_requests()
        self.assertFalse(simulation.pending_requests)
        simulation.current_time = 5
        simulation.enqueue_ready_requests()
        self.assertEqual([item.id for item in simulation.pending_requests], ["R1"])

    def test_invalid_configuration_is_rejected(self) -> None:
        cases = [
            {"floors": 1, "elevators": 1, "capacity": 1},
            {"floors": 10, "elevators": 0, "capacity": 1},
            {"floors": 10, "elevators": 1, "capacity": 0},
            {"floors": 10, "elevators": 1, "capacity": 1, "assignment_policy": "bad"},
        ]
        for settings in cases:
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                ElevatorSimulation(**settings)


class TestOutputs(unittest.TestCase):
    def test_csv_outputs_have_expected_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            positions = os.path.join(directory, "positions.csv")
            passengers = os.path.join(directory, "passengers.csv")
            simulation = run_sim(
                "0,R1,1,4\n",
                elevators=2,
                log_path=positions,
                passenger_summary_path=passengers,
            )
            simulation.write_outputs()

            with open(positions, newline="", encoding="utf-8") as handle:
                position_rows = list(csv.reader(handle))
            self.assertEqual(position_rows[0], ["time", "elevator_1", "elevator_2"])
            self.assertEqual(position_rows[1], ["0", "1", "1"])
            self.assertEqual(len(position_rows), simulation.total_time_units + 2)
            for previous, current in zip(position_rows[1:], position_rows[2:]):
                for index in range(1, len(current)):
                    self.assertLessEqual(
                        abs(int(current[index]) - int(previous[index])), 1
                    )

            with open(passengers, newline="", encoding="utf-8") as handle:
                passenger_rows = list(csv.DictReader(handle))
            self.assertEqual(passenger_rows[0]["id"], "R1")
            self.assertEqual(passenger_rows[0]["total_time"], "3")

    def test_summary_statistics_and_empty_report(self) -> None:
        simulation = run_sim("0,R1,1,5\n")
        completed_output = io.StringIO()
        with redirect_stdout(completed_output):
            simulation.print_summary()
        self.assertIn("Average wait time: 0.00", completed_output.getvalue())

        empty = run_sim("")
        output = io.StringIO()
        with redirect_stdout(output):
            empty.print_summary()
        self.assertIn("Requests served: 0", output.getvalue())


class TestAssignmentPolicies(unittest.TestCase):
    def test_cost_and_nearest_choose_expected_car(self) -> None:
        for policy in ("cost", "nearest_car"):
            simulation = ElevatorSimulation(
                floors=10, elevators=2, capacity=6, assignment_policy=policy
            )
            simulation.elevators[0].current_floor = 1
            simulation.elevators[1].current_floor = 9
            chosen = simulation.select_best_elevator(request(0, "R1", 8, 2))
            with self.subTest(policy=policy):
                self.assertEqual(chosen.elevator_id, 2)

    def test_cost_simulates_route_and_capacity_without_mutating_cars(self) -> None:
        simulation = ElevatorSimulation(
            floors=10, elevators=2, capacity=1, assignment_policy="cost"
        )
        busy, idle = simulation.elevators
        busy.current_floor = 5
        busy.direction = 1
        onboard = request(0, "onboard", 1, 10)
        onboard.mark_picked_up(0)
        busy.passengers = [onboard]
        busy.route = [Stop(10, DROPOFF, onboard)]
        idle.current_floor = 1

        chosen = simulation.select_best_elevator(request(0, "new", 6, 7))

        self.assertEqual(chosen.elevator_id, 2)
        self.assertEqual(busy.current_floor, 5)
        self.assertEqual(busy.passengers, [onboard])
        self.assertIsNone(onboard.dropoff_time)

    def test_cost_uses_less_loaded_car_when_completion_costs_tie(self) -> None:
        simulation = run_sim(
            "0,R1,1,10\n0,R2,1,8\n",
            elevators=2,
            assignment_policy="cost",
        )
        assignments = [
            request.assigned_elevator for request in simulation.assigned_requests
        ]
        self.assertEqual(assignments, [1, 2])

    def test_future_requests_do_not_change_current_cost_assignment(self) -> None:
        inputs = [
            "0,now,8,2\n",
            "0,now,8,2\n1,future,9,1\n",
        ]
        assignments = []
        for data in inputs:
            simulation = ElevatorSimulation(
                floors=10,
                elevators=2,
                capacity=6,
                assignment_policy="cost",
                input_stream=io.StringIO(data),
            )
            simulation.elevators[0].current_floor = 1
            simulation.elevators[1].current_floor = 10
            simulation.enqueue_ready_requests()
            simulation.assign_pending_requests()
            assignments.append(simulation.assigned_requests[0].assigned_elevator)

        self.assertEqual(assignments, [2, 2])

    def test_round_robin_cycles(self) -> None:
        simulation = ElevatorSimulation(
            floors=10, elevators=3, capacity=6, assignment_policy="round_robin"
        )
        chosen = [
            simulation.select_best_elevator(request(0, f"R{i}", 1, 2)).elevator_id
            for i in range(4)
        ]
        self.assertEqual(chosen, [1, 2, 3, 1])

    def test_zone_ownership_and_uneven_split(self) -> None:
        simulation = ElevatorSimulation(
            floors=10, elevators=3, capacity=6, assignment_policy="zone"
        )
        self.assertEqual(simulation.zone_bounds, [(1, 4), (5, 7), (8, 10)])
        chosen = simulation.select_best_elevator(request(0, "R1", 9, 1))
        self.assertEqual(chosen.elevator_id, 3)

        extra_car = ElevatorSimulation(
            floors=2, elevators=3, capacity=6, assignment_policy="zone"
        )
        self.assertEqual(extra_car.zone_bounds, [(1, 1), (2, 2), None])
        chosen = extra_car.select_best_elevator(request(0, "R2", 2, 1))
        self.assertEqual(chosen.elevator_id, 2)

    def test_every_policy_completes_same_workload(self) -> None:
        data = "0,R1,1,10\n1,R2,10,1\n2,R3,5,8\n3,R4,8,2\n"
        for policy in ("cost", "round_robin", "nearest_car", "zone"):
            simulation = run_sim(data, elevators=3, assignment_policy=policy)
            with self.subTest(policy=policy):
                self.assertEqual(len(simulation.assigned_requests), 4)
                self.assertTrue(all(r.total_time is not None for r in simulation.assigned_requests))


class TestExpressElevators(unittest.TestCase):
    def test_express_eligibility(self) -> None:
        elevator = Elevator(1, floors=30, capacity=6, allowed_floors={1, 10, 20, 30})
        self.assertTrue(elevator.can_serve(request(0, "ok", 10, 20)))
        self.assertFalse(elevator.can_serve(request(0, "bad", 5, 20)))

    def test_non_express_request_falls_back_to_regular_car(self) -> None:
        simulation = run_sim(
            "0,R1,5,15\n",
            floors=30,
            elevators=2,
            express_elevators=1,
            express_stops=[10, 20, 30],
        )
        self.assertEqual(simulation.assigned_requests[0].assigned_elevator, 2)
        self.assertIn(1, simulation.elevators[0].allowed_floors)

    def test_invalid_express_configuration_is_rejected(self) -> None:
        cases = [
            {"express_elevators": 1, "express_stops": None},
            {"express_elevators": 3, "express_stops": [1, 5]},
            {"express_elevators": 1, "express_stops": [1, 50]},
        ]
        for options in cases:
            with self.subTest(options=options), self.assertRaises(ValueError):
                ElevatorSimulation(floors=10, elevators=2, capacity=6, **options)

    def test_unreachable_all_express_request_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ElevatorSimulation(
                floors=20,
                elevators=2,
                capacity=6,
                input_stream=io.StringIO("0,R1,5,8\n"),
                express_elevators=2,
                express_stops=[1, 10, 20],
            )


class TestCLI(unittest.TestCase):
    def test_cli_rejects_invalid_numeric_settings(self) -> None:
        arguments = [
            ["--floors", "1"],
            ["--elevators", "0"],
            ["--capacity", "0"],
        ]
        for flags in arguments:
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                main([*flags, "--input", os.devnull])


if __name__ == "__main__":
    unittest.main(verbosity=2)
