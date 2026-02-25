"""ROS 2 Action-server proxy that gates NavigateToPose through Axiom.

Lifecycle
---------
1. Receive a NavigateToPose goal on ``/axiom/navigate_to_pose``.
2. Map goal -> TaskSpec via :func:`taskspec_mapping.goal_to_taskspec`.
3. Run Axiom gates via :func:`axiom_tfg.runner.run_taskspec`.
4. Write artifact bundle under ``artifacts/ros2/<goal_uuid>/``.
5. If verdict is **CAN** -> forward to upstream Nav2 action server.
6. If verdict is **HARD_CANT** -> abort immediately with evidence message.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import rclpy
from rclpy.action import ActionClient, ActionServer, GoalResponse, CancelResponse
from rclpy.action.server import ServerGoalHandle
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node

from nav2_msgs.action import NavigateToPose

from axiom_tfg.models import TaskSpec
from axiom_tfg.runner import run_taskspec, write_artifact_bundle

from .taskspec_mapping import goal_to_taskspec, load_keepout_zones


class PreflightProxyNode(Node):
    """Action proxy that validates NavigateToPose goals with Axiom gates."""

    def __init__(self) -> None:
        super().__init__("axiom_preflight_proxy")

        # Declare parameters.
        self.declare_parameter("robot_model", "diffbot")
        self.declare_parameter("max_nav_radius_m", 10.0)
        self.declare_parameter("safety_buffer_m", 0.2)
        self.declare_parameter("keepout_yaml", "")
        self.declare_parameter("upstream_action", "/navigate_to_pose")
        self.declare_parameter("artifact_dir", "artifacts/ros2")

        # Pre-load keepout zones once.
        keepout_path = self.get_parameter("keepout_yaml").get_parameter_value().string_value
        self._keepout_zones = load_keepout_zones(keepout_path or None)

        upstream = self.get_parameter("upstream_action").get_parameter_value().string_value
        cb_group = ReentrantCallbackGroup()

        # Upstream Nav2 action client.
        self._nav_client = ActionClient(
            self, NavigateToPose, upstream, callback_group=cb_group
        )

        # Proxy action server on /axiom/navigate_to_pose.
        self._action_server = ActionServer(
            self,
            NavigateToPose,
            "/axiom/navigate_to_pose",
            execute_callback=self._execute,
            goal_callback=self._goal_callback,
            cancel_callback=self._cancel_callback,
            callback_group=cb_group,
        )

        self.get_logger().info(
            f"Axiom preflight proxy ready — upstream={upstream}, "
            f"keepout_zones={len(self._keepout_zones)}"
        )

    # ── Action callbacks ─────────────────────────────────────────────

    def _goal_callback(self, _goal_request) -> GoalResponse:
        return GoalResponse.ACCEPT

    def _cancel_callback(self, _goal_handle) -> CancelResponse:
        return CancelResponse.ACCEPT

    def _execute(self, goal_handle: ServerGoalHandle) -> NavigateToPose.Result:
        goal = goal_handle.request
        goal_uuid = uuid.uuid4().hex[:12]

        self.get_logger().info(f"[{goal_uuid}] Goal received — running Axiom gates")

        # Read params.
        robot_model = self.get_parameter("robot_model").get_parameter_value().string_value
        max_radius = self.get_parameter("max_nav_radius_m").get_parameter_value().double_value
        safety_buf = self.get_parameter("safety_buffer_m").get_parameter_value().double_value
        artifact_base = self.get_parameter("artifact_dir").get_parameter_value().string_value

        # Build TaskSpec.
        spec_dict = goal_to_taskspec(
            goal_x=goal.pose.pose.position.x,
            goal_y=goal.pose.pose.position.y,
            goal_uuid=goal_uuid,
            robot_model=robot_model,
            max_nav_radius_m=max_radius,
            safety_buffer_m=safety_buf,
            keepout_zones=self._keepout_zones,
        )
        spec = TaskSpec.model_validate(spec_dict)

        # Run gates.
        result, packet = run_taskspec(spec)
        verdict = result["verdict"]

        # Write artifacts.
        out_dir = Path(artifact_base) / goal_uuid
        write_artifact_bundle(spec, packet, result, out_dir, junit=True)
        self.get_logger().info(f"[{goal_uuid}] verdict={verdict} artifacts={out_dir}")

        # Gate decision.
        if verdict == "CAN":
            return self._forward_goal(goal_handle, goal, goal_uuid)

        # HARD_CANT — abort without forwarding.
        msg = (
            f"Axiom HARD_CANT: gate={result.get('failed_gate')} "
            f"reason={result.get('reason_code')}"
        )
        if packet.counterfactual_fixes:
            msg += f" fix={packet.counterfactual_fixes[0].instruction}"

        self.get_logger().warn(f"[{goal_uuid}] BLOCKED — {msg}")
        goal_handle.abort()
        nav_result = NavigateToPose.Result()
        return nav_result

    def _forward_goal(
        self,
        proxy_handle: ServerGoalHandle,
        goal: NavigateToPose.Goal,
        goal_uuid: str,
    ) -> NavigateToPose.Result:
        """Forward the goal to the upstream Nav2 action server."""
        self.get_logger().info(f"[{goal_uuid}] Forwarding to upstream Nav2")

        if not self._nav_client.wait_for_server(timeout_sec=5.0):
            self.get_logger().error(f"[{goal_uuid}] Upstream action server not available")
            proxy_handle.abort()
            return NavigateToPose.Result()

        send_future = self._nav_client.send_goal_async(
            goal, feedback_callback=lambda fb: self._relay_feedback(proxy_handle, fb)
        )
        rclpy.spin_until_future_complete(self, send_future)
        upstream_handle = send_future.result()

        if not upstream_handle.accepted:
            self.get_logger().warn(f"[{goal_uuid}] Upstream rejected goal")
            proxy_handle.abort()
            return NavigateToPose.Result()

        # Wait for result.
        result_future = upstream_handle.get_result_async()
        rclpy.spin_until_future_complete(self, result_future)
        upstream_result = result_future.result()

        if upstream_result.status == 4:  # STATUS_SUCCEEDED
            proxy_handle.succeed()
        else:
            proxy_handle.abort()

        return upstream_result.result

    def _relay_feedback(self, proxy_handle: ServerGoalHandle, feedback_msg) -> None:
        proxy_handle.publish_feedback(feedback_msg.feedback)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = PreflightProxyNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
