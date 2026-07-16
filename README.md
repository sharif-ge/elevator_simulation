# Elevator System Simulation

A discrete-time destination-dispatch simulation. Passengers provide their
source and destination in advance, requests are assigned immediately, and each
car moves at most one floor per time unit while respecting direction and
capacity constraints.

The submission also includes four assignment policies, optional express
elevators, analytics, sample workloads, and tests.

## Setup and quick start

Python 3.9 or newer is recommended.

```bash
git clone https://github.com/sharif-ge/elevator_simulation.git
cd elevator_simulation

python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# Run the included example
.venv/bin/python sample_runs/sample_execution.py

# Or use the configurable CLI
.venv/bin/python elevator_simulation.py \
    --input inputs/basic_10_floor.csv \
    --floors 10 --elevators 3 --capacity 6
```

The simulator writes:

- `outputs/elevator_positions_log.csv`: every car's position at every tick,
  starting at time 0
- `outputs/passenger_summary.csv`: assignment, pickup, dropoff, wait, travel,
  and total times
- Min, max, and average timing statistics to stdout

Use `--input -` to read requests from stdin.

## Input format

```csv
time,id,source,dest
0,passenger1,1,5
0,passenger2,1,3
10,passenger3,2,1
```

IDs must be unique, times non-negative, and endpoints different floors within
the configured building. A header and blank lines are accepted. Requests are
sorted by `(time, id)` but become visible to the dispatcher only when their
timestamp arrives.

## Simulator options

| Option | Default | Meaning |
|---|---|---|
| `--floors` | `10` | Number of floors, at least 2 |
| `--elevators` | `3` | Number of cars |
| `--capacity` | `6` | Maximum passengers per car |
| `--input` | `-` | Request CSV path, or stdin |
| `--log` | `outputs/elevator_positions_log.csv` | Position output |
| `--passenger-summary` | `outputs/passenger_summary.csv` | Passenger output |
| `--assignment-policy` | `cost` | `cost`, `round_robin`, `nearest_car`, or `zone` |
| `--express-elevators` | `0` | Number of express cars |
| `--express-stops` | none | Comma-separated express floors |
| `--verbose` | off | Tick-level logging |

Express example:

```bash
.venv/bin/python elevator_simulation.py \
    --input inputs/express_elevator_showcase.csv \
    --floors 60 --elevators 4 --capacity 6 \
    --assignment-policy zone \
    --express-elevators 1 --express-stops "1,20,40,60"
```

## Analytics

Run both analytics modes:

```bash
.venv/bin/python sample_runs/sample_analytics.py
```

Analyze one completed run:

```bash
.venv/bin/python analytics/analytics.py report \
    --requests outputs/passenger_summary.csv \
    --positions outputs/elevator_positions_log.csv \
    --output outputs/analysis
```

This creates `summary.txt` and `analytics_dashboard.png`.

Compare policies on the same workload:

```bash
.venv/bin/python analytics/analytics.py compare \
    --input inputs/lobby_morning_up_peak.csv \
    --floors 60 --elevators 3 --capacity 6 \
    --output outputs/analysis_compare
```

This creates `comparison.csv`, `comparison_summary.txt`, and
`comparison_dashboard.png`. The building height is explicit because it
cannot be inferred reliably from the requests.

Run commands from the project root. Nested runnable files use a small
`sys.path` bootstrap so they work without installing the project. A long-lived
version would replace this with `pyproject.toml` packaging.

## Design

The core responsibilities remain in one runnable module:

- `SimulationConfig`: configuration validation
- `parse_requests`: CSV parsing, validation, and ordering
- `Elevator`: car state, capacity, movement, and SCAN routing
- `ElevatorSimulation`: request assignment and the discrete-time loop
- `SimulationReporter`: CSV output and console statistics

### Request lifecycle

```text
future -> pending -> assigned -> picked up -> dropped off
```

```text
wait_time   = pickup_time - request_time
travel_time = dropoff_time - pickup_time
total_time  = wait_time + travel_time
```

Dropoffs occur before pickups at a floor, so an exiting passenger can free a
seat during the same tick. Capacity is enforced when boarding rather than when
assigning; a currently full car may unload before reaching a future pickup.

### Assignment and routing

- `cost` (default): simulates each eligible car's current SCAN route with and
  without the new request, then minimizes the increase in remaining passenger
  completion time
- `round_robin`: cycles through eligible cars
- `nearest_car`: chooses the eligible car closest to the pickup
- `zone`: prefers the pickup-zone owner and falls back to `cost` when that
  car cannot serve the request

Assignment and routing are separate decisions. After assignment, each car uses
SCAN routing: continue toward actionable stops in the current direction, then
reverse. Dropoffs become actionable only after boarding, and a full car
prioritizes dropoffs over pickups it cannot currently serve.

There is no lookahead. The input is loaded for validation, but assignment sees
only requests whose timestamp has arrived plus the cars' current state and
already-assigned work. Future requests are used only to keep the clock ticking.

An express car accepts a request only when both endpoints are allowed stops;
floor 1 is included automatically.

## Complexity

Let `N` be requests, `E` elevators, `R` the maximum route length, `T`
ticks, and `S` movements examined by one projected route.

| Operation | Time |
|---|---:|
| Parse and sort requests | `O(N log N)` |
| Round-robin assignment | `O(E)` worst case, usually `O(1)` |
| Nearest-car assignment | `O(E)` |
| Zone assignment | `O(E)`, with possible cost fallback |
| Cost assignment | `O(E * S * R)` |
| Select a SCAN stop | `O(R)` |
| Process one car for one tick | `O(R)` |

Capacity is treated as a small configured bound. If it were unbounded, route
processing would include a factor of `C`. Simulation state uses
`O(N + T * E)` memory, including the required position history.

## Included scenarios

- Basic examples: `request.csv`, `basic_10_floor.csv`
- Traffic patterns: `lobby_morning_up_peak.csv`,
  `evening_down_peak.csv`, `interfloor_mixed_traffic.csv`,
  `staggered_requests.csv`
- Feature and stress cases: `capacity_stress.csv`,
  `zone_policy_showcase.csv`, `express_elevator_showcase.csv`
- Invalid fixtures: `invalid_out_of_range.csv`,
  `invalid_duplicate_id.csv`, `invalid_same_floor.csv`

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

The 30 tests cover timing, capacity, SCAN behavior, no lookahead, all policies,
express eligibility, validation, CLI settings, output invariants, analytics,
and eventual service.

## Assumptions and future improvements

- One time unit represents one floor of movement; acceleration and door dwell
  time are omitted.
- Assignments are immediate and are not reconsidered.
- Zone boundaries and express stop sets are static; all express cars share the
  same stops.

With more time, I would:

- Model the time needed to open doors and let passengers enter or exit; those
  actions are currently instant.
- Generate repeatable random request files to test quiet periods, rush hour,
  different buildings, and different elevator capacities.
- Run every assignment policy against the same set of workloads and summarize
  their wait times, total trip times, and travel distances in one report.
- Use standard Python packaging so the project provides simple commands and
  imports without manually adding the project root to `sys.path`.

## Time spent

Approximately 4 hours.

