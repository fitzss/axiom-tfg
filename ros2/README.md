# Axiom Pre-flight Gate for Nav2

A ROS 2 adapter that makes Axiom a **pre-flight gate** for Nav2 `NavigateToPose` actions. The proxy intercepts navigation goals, runs Axiom feasibility gates, and only forwards goals that pass. Every goal produces replayable evidence artifacts.

## Architecture

```
Client                   Axiom Proxy                  Nav2
  |                         |                           |
  |-- NavigateToPose ------>|                           |
  |   /axiom/navigate_to_  |-- run_taskspec() -------->|
  |     pose                |   (Axiom gates)          |
  |                         |                           |
  |                         |-- write artifacts ------->|  artifacts/ros2/<uuid>/
  |                         |                           |
  |                     [CAN] forward goal ------------>|  /navigate_to_pose
  |<---- feedback ----------|<---- feedback ------------|
  |<---- result ------------|<---- result --------------|
  |                         |                           |
  |                 [HARD_CANT] abort                    |
  |<---- ABORTED -----------|   (not forwarded)         |
```

## Prerequisites

- ROS 2 Humble or later
- Nav2 (`nav2_bringup`) for the upstream action server
- `axiom-tfg` installed in the same Python environment

## Build

```bash
# From your ROS 2 workspace src/ directory:
ln -s /path/to/axiom-tfg/ros2/axiom_preflight_nav2 .

# Build:
cd ~/ros2_ws
colcon build --packages-select axiom_preflight_nav2
source install/setup.bash
```

## Run

```bash
# Terminal 1: Start Nav2 (or your robot stack)
ros2 launch nav2_bringup tb3_simulation_launch.py

# Terminal 2: Start the Axiom preflight proxy
ros2 run axiom_preflight_nav2 axiom-preflight-nav2

# With parameters:
ros2 run axiom_preflight_nav2 axiom-preflight-nav2 \
  --ros-args \
  -p robot_model:=turtlebot4 \
  -p max_nav_radius_m:=15.0 \
  -p safety_buffer_m:=0.3 \
  -p keepout_yaml:=/path/to/keepout_zones.yaml \
  -p upstream_action:=/navigate_to_pose \
  -p artifact_dir:=artifacts/ros2
```

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `robot_model` | string | `"diffbot"` | Robot identifier for the TaskSpec |
| `max_nav_radius_m` | double | `10.0` | Maximum navigation radius (reachability gate) |
| `safety_buffer_m` | double | `0.2` | Safety buffer around keepout zones |
| `keepout_yaml` | string | `""` | Path to YAML file with keepout zones |
| `upstream_action` | string | `"/navigate_to_pose"` | Upstream Nav2 action server topic |
| `artifact_dir` | string | `"artifacts/ros2"` | Base directory for evidence artifacts |

## Send a goal

```bash
ros2 action send_goal /axiom/navigate_to_pose nav2_msgs/action/NavigateToPose \
  "{pose: {header: {frame_id: 'map'}, pose: {position: {x: 2.0, y: 1.0, z: 0.0}}}}"
```

## Keepout zones file format

```yaml
keepout_zones:
  - id: obstacle_1
    min_xyz: [1.0, 2.0, 0.0]
    max_xyz: [3.0, 4.0, 1.0]
  - id: restricted_area
    min_xyz: [5.0, 5.0, 0.0]
    max_xyz: [8.0, 8.0, 1.0]
```

## Artifacts

Every goal (CAN or HARD_CANT) writes a standard Axiom artifact bundle:

```
artifacts/ros2/<goal_uuid>/
  input.yaml       # TaskSpec used for gate evaluation
  result.json      # Verdict summary
  evidence.json    # Full evidence packet
  junit.xml        # JUnit XML (one testcase)
```

These are compatible with `axiom replay` for regression testing:

```bash
axiom replay artifacts/ros2/ --out artifacts/replay_ros2
```
