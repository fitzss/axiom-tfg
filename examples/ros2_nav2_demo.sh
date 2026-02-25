#!/usr/bin/env bash
# ros2_nav2_demo.sh — Send sample NavigateToPose goals through the Axiom preflight proxy.
#
# Prerequisites:
#   1. Nav2 stack running (or at least the proxy node — it will timeout on forward)
#   2. axiom-preflight-nav2 node running:
#        ros2 run axiom_preflight_nav2 axiom-preflight-nav2 \
#          --ros-args -p max_nav_radius_m:=10.0
#
# Usage:
#   bash examples/ros2_nav2_demo.sh

set -euo pipefail

ACTION="/axiom/navigate_to_pose"
TYPE="nav2_msgs/action/NavigateToPose"

echo "=== Axiom Nav2 Pre-flight Demo ==="
echo ""

# Goal 1: CAN — within 10m radius, no keepout
echo "--- Goal 1: CAN (within radius) ---"
echo "Sending goal: x=2.0, y=1.0"
ros2 action send_goal "$ACTION" "$TYPE" \
  "{pose: {header: {frame_id: 'map'}, pose: {position: {x: 2.0, y: 1.0, z: 0.0}}}}" \
  || true
echo ""

# Goal 2: HARD_CANT — beyond 10m radius
echo "--- Goal 2: HARD_CANT (beyond radius) ---"
echo "Sending goal: x=50.0, y=50.0"
ros2 action send_goal "$ACTION" "$TYPE" \
  "{pose: {header: {frame_id: 'map'}, pose: {position: {x: 50.0, y: 50.0, z: 0.0}}}}" \
  || true
echo ""

echo "=== Artifacts ==="
echo "Check artifacts/ros2/ for evidence bundles:"
ls -la artifacts/ros2/ 2>/dev/null || echo "  (no artifacts yet — is the proxy node running?)"
echo ""
echo "Replay all ROS2 artifacts:"
echo "  axiom replay artifacts/ros2/ --out artifacts/replay_ros2"
