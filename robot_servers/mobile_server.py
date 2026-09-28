"""
P-MCP v0.5 — Mobile Robot Server (AMR / TurtleBot / Spot)
==========================================================
MCP-compatible server for autonomous mobile robots.

Supports: TurtleBot 4, iRobot Create 3, Boston Dynamics Spot,
          Clearpath Husky, custom AMR platforms.

Run:
    python mobile_server.py
    python mobile_server.py --transport http --port 8081
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
import sys
import time

import os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from v05.pmcp_v5_server import PMCPServer, _LeaseManager
from v05.pmcp_v5_types  import (
    ActuationResult, MissionResult, SensorReading, SensorType,
)
from v05.pmcp_safety_v5 import SafetyConstitution, SafetyMiddleware, ShadowSimulator, WorkspaceBoxRule

log = logging.getLogger("pmcp.mobile_server")


# ─────────────────────────────────────────────────────────────────────────────
#  SIMULATED MOBILE ROBOT STATE
# ─────────────────────────────────────────────────────────────────────────────

class MobileState:
    def __init__(self):
        self.x = 0.0; self.y = 0.0; self.yaw_rad = 0.0
        self.vx = 0.0; self.omega = 0.0
        self.battery_pct = 87.0
        self.charging = False
        self.map_frame = "map"
        self.lidar_ranges = [random.uniform(0.5, 5.0) for _ in range(360)]
        self.waypoints: list = []

    def navigate_to(self, target_x: float, target_y: float,
                    max_speed: float = 0.5) -> dict:
        dx = target_x - self.x; dy = target_y - self.y
        dist = math.hypot(dx, dy)
        # Simulate navigation
        self.yaw_rad = math.atan2(dy, dx)
        self.x = target_x; self.y = target_y
        self.battery_pct -= dist * 0.05
        return {
            "reached": True, "x": self.x, "y": self.y,
            "distance_m": round(dist, 3),
            "battery_pct": round(self.battery_pct, 1),
        }

    def rotate(self, angle_deg: float, speed_rads: float = 0.5) -> dict:
        self.yaw_rad = (self.yaw_rad + math.radians(angle_deg)) % (2 * math.pi)
        return {"rotated_deg": angle_deg, "new_yaw_deg": round(math.degrees(self.yaw_rad), 1)}

    def dock(self) -> dict:
        self.charging = True
        self.x = 0.0; self.y = 0.0
        return {"docked": True, "charging": True}

    def undock(self) -> dict:
        self.charging = False
        return {"undocked": True}

    def get_pose(self) -> dict:
        return {
            "x": round(self.x, 4), "y": round(self.y, 4),
            "yaw_deg": round(math.degrees(self.yaw_rad), 2),
            "frame": self.map_frame,
        }


# ─────────────────────────────────────────────────────────────────────────────
#  BUILD SERVER
# ─────────────────────────────────────────────────────────────────────────────

def build_mobile_server(name: str = "amr-01",
                         model: str = "TurtleBot4",
                         serial: str = "2024-M01",
                         location: str = "floor-1") -> PMCPServer:

    # Mobile robots navigate large areas — use a 200m × 200m workspace box
    from v05.pmcp_safety_v5 import (
        EStopRule, SpeedLimitRule, FloorGuardRule, WorkspaceBoxRule,
        EnergyBudgetRule, HumanProximityRule, ForceLimit,
    )
    constitution = SafetyConstitution(name, rules=[
        EStopRule(),
        SpeedLimitRule(max_speed_m_s=2.0),
        FloorGuardRule(floor_z_m=-0.05),
        WorkspaceBoxRule(x_range=(-100.0, 100.0), y_range=(-100.0, 100.0),
                         z_range=(-0.05, 3.0)),
        EnergyBudgetRule(max_energy_j=5000.0),
        HumanProximityRule(min_clearance_m=0.5),
        ForceLimit(max_force_n=150.0),
    ])
    lease_mgr = _LeaseManager()
    safety    = SafetyMiddleware(constitution, ShadowSimulator(workspace_box=(
        (-100.0, -100.0, -0.05), (100.0, 100.0, 3.0))), lease_mgr)

    server = PMCPServer(
        name=name, version="1.0.0",
        robot_class="mobile", model=model,
        serial=serial, location=location,
        safety=safety,
    )
    bot = MobileState()

    # ── Actuations ────────────────────────────────────────────────────────────

    @server.actuation(
        "navigate_to",
        description=(
            "Navigate the robot to a map coordinate (x, y) using the onboard planner. "
            "The robot automatically avoids obstacles using its lidar. "
            "Specify max_speed in m/s (default 0.5, max 1.5)."
        ),
        category="navigation", max_speed_m_s=1.5, max_energy_j=1000.0,
        est_duration_s=15.0, requires_lease=True,
    )
    async def navigate_to(x: float, y: float, max_speed: float = 0.5):
        if max_speed > 1.5:
            return ActuationResult(success=False, robot_id=server.robot_id,
                                    error_message="max_speed exceeds 1.5 m/s limit")
        result = bot.navigate_to(x, y, max_speed)
        return ActuationResult(success=True, robot_id=server.robot_id,
                                output=result, final_pose=bot.get_pose())

    @server.actuation(
        "rotate",
        description=(
            "Rotate the robot in place by the specified angle. "
            "Positive = counter-clockwise, Negative = clockwise."
        ),
        category="motion", max_speed_m_s=0.3, max_energy_j=50.0,
        est_duration_s=3.0, requires_lease=True,
    )
    async def rotate(angle_deg: float, speed_rads: float = 0.5):
        result = bot.rotate(angle_deg, speed_rads)
        return ActuationResult(success=True, robot_id=server.robot_id, output=result)

    @server.actuation(
        "dock",
        description="Drive to the docking station and start charging.",
        category="utility", max_speed_m_s=0.3, max_energy_j=20.0,
        est_duration_s=30.0, requires_lease=True,
    )
    async def dock():
        result = bot.dock()
        return ActuationResult(success=True, robot_id=server.robot_id, output=result)

    @server.actuation(
        "undock",
        description="Undock from charging station and prepare for deployment.",
        category="utility", max_speed_m_s=0.2, max_energy_j=5.0,
        est_duration_s=5.0, requires_lease=False, shadow_required=False,
    )
    async def undock():
        result = bot.undock()
        return ActuationResult(success=True, robot_id=server.robot_id, output=result)

    @server.actuation(
        "patrol",
        description=(
            "Run an autonomous patrol loop through a named route. "
            "Routes: 'perimeter', 'aisle_1', 'aisle_2', 'full_floor'"
        ),
        category="navigation", max_speed_m_s=0.8, max_energy_j=2000.0,
        est_duration_s=120.0, requires_lease=True,
    )
    async def patrol(route: str = "perimeter", loops: int = 1):
        routes = {
            "perimeter": [(5,0), (5,5), (0,5), (0,0)],
            "aisle_1":   [(1,0), (1,8), (1,0)],
            "aisle_2":   [(3,0), (3,8), (3,0)],
            "full_floor": [(5,0), (5,5), (0,5), (1,0), (3,0), (3,8), (0,0)],
        }
        if route not in routes:
            return ActuationResult(
                success=False, robot_id=server.robot_id,
                error_message=f"Unknown route: {route}. Choose from {list(routes.keys())}",
            )
        waypoints_visited = []
        for _ in range(loops):
            for wx, wy in routes[route]:
                r = bot.navigate_to(wx, wy)
                waypoints_visited.append({"x": wx, "y": wy, "reached": r["reached"]})
        return ActuationResult(
            success=True, robot_id=server.robot_id,
            output={"route": route, "loops": loops,
                    "waypoints": waypoints_visited,
                    "final_pose": bot.get_pose()},
        )

    @server.actuation(
        "emergency_stop",
        description="Immediately halt all motion. No safety checks required.",
        category="utility", requires_lease=False, shadow_required=False,
        max_energy_j=0.0,
    )
    async def emergency_stop():
        bot.vx = 0.0; bot.omega = 0.0
        return ActuationResult(success=True, robot_id=server.robot_id,
                                output={"stopped": True, "pose": bot.get_pose()})

    # ── Sensors ───────────────────────────────────────────────────────────────

    @server.sensor("odometry", description="Current robot pose in map frame (x, y, yaw)",
                   sensor_type=SensorType.ODOMETRY, unit="m/deg", hz=50.0)
    async def odometry():
        return SensorReading(
            sensor_name="odometry", robot_id=server.robot_id,
            value=bot.get_pose(), unit="m/deg",
        )

    @server.sensor("lidar_scan", description="360-degree lidar scan (ranges in meters)",
                   sensor_type=SensorType.LIDAR, unit="m", hz=10.0)
    async def lidar_scan():
        # Simulate slight lidar noise
        ranges = [max(0.1, r + random.gauss(0, 0.005)) for r in bot.lidar_ranges]
        return SensorReading(
            sensor_name="lidar_scan", robot_id=server.robot_id,
            value={"ranges": [round(r, 3) for r in ranges[::18]],  # 20 rays for brevity
                    "angle_min_deg": 0, "angle_max_deg": 358,
                    "angle_step_deg": 18, "range_max_m": 12.0},
            unit="m",
        )

    @server.sensor("battery", description="Battery state of charge and voltage",
                   sensor_type=SensorType.BATTERY, unit="%/V", hz=1.0)
    async def battery():
        if not bot.charging:
            bot.battery_pct -= 0.001
        else:
            bot.battery_pct = min(100.0, bot.battery_pct + 0.01)
        return SensorReading(
            sensor_name="battery", robot_id=server.robot_id,
            value={"percent": round(bot.battery_pct, 1),
                    "charging": bot.charging,
                    "voltage_v": round(24.0 * bot.battery_pct / 100 + 18, 2)},
            unit="%",
        )

    @server.sensor("imu", description="IMU linear acceleration and angular velocity",
                   sensor_type=SensorType.IMU, unit="m/s²/rad/s", hz=200.0)
    async def imu():
        return SensorReading(
            sensor_name="imu", robot_id=server.robot_id,
            value={
                "linear_acceleration": {
                    "x": round(random.gauss(0, 0.01), 4),
                    "y": round(random.gauss(0, 0.01), 4),
                    "z": round(9.81 + random.gauss(0, 0.005), 4),
                },
                "angular_velocity": {
                    "x": round(random.gauss(0, 0.001), 5),
                    "y": round(random.gauss(0, 0.001), 5),
                    "z": round(bot.omega + random.gauss(0, 0.001), 5),
                },
            },
        )

    # ── Missions ──────────────────────────────────────────────────────────────

    @server.mission("delivery",
                    description="Navigate to pickup location, then deliver to drop-off",
                    robot_class="mobile")
    async def delivery_mission(pickup_x: float, pickup_y: float,
                                dropoff_x: float, dropoff_y: float):
        return MissionResult(
            mission_name="delivery",
            messages=[
                {"role": "user", "content": [{"type": "text", "text":
                    f"Deliver from ({pickup_x},{pickup_y}) to ({dropoff_x},{dropoff_y})"}]},
                {"role": "assistant", "content": [{"type": "text", "text":
                    "Executing delivery mission:\n"
                    f"1. navigate_to({pickup_x}, {pickup_y}) — pickup\n"
                    f"2. navigate_to({dropoff_x}, {dropoff_y}) — dropoff\n"
                    "3. dock() — return to charging station"}]},
            ],
        )

    return server


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="P-MCP Mobile Robot Server v0.5")
    parser.add_argument("--transport", default="stdio", choices=["stdio", "http"])
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--name", default="amr-01")
    parser.add_argument("--model", default="TurtleBot4")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s",
                        stream=sys.stderr)
    server = build_mobile_server(name=args.name, model=args.model)
    asyncio.run(server.run(args.transport, host=args.host, port=args.port))
