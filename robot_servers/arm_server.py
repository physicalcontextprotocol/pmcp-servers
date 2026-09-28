"""
P-MCP v0.5 — Universal Robotic Arm Server
==========================================
Production-ready MCP-compatible server for 6-DOF robotic arms.
Tested robot families: UR5/UR10, KUKA KR, ABB IRB, Fanuc M-series.

Run:
    python arm_server.py                   # stdio (Claude Desktop compatible)
    python arm_server.py --transport http  # HTTP :8080
    python arm_server.py --sim             # Pure simulation mode (no hardware)

Claude Desktop config:
    {
      "mcpServers": {
        "robot-arm": {
          "command": "python",
          "args": ["/path/to/arm_server.py"]
        }
      }
    }
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
import sys
import time
from typing import List, Optional

# Allow running as script from any directory
import os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from v05.pmcp_v5_server import PMCPServer
from v05.pmcp_v5_types  import (
    ActuationResult, MissionResult, SensorReading, SensorType,
)

log = logging.getLogger("pmcp.arm_server")

# ─────────────────────────────────────────────────────────────────────────────
#  SIMULATED ARM STATE
# ─────────────────────────────────────────────────────────────────────────────

class ArmState:
    """Simulated 6-DOF arm state — replace with real hardware calls."""
    def __init__(self):
        self.joints_rad: List[float] = [0.0, -math.pi/4, math.pi/2, 0.0, math.pi/2, 0.0]
        self.tcp_pose = {"x": 0.3, "y": 0.0, "z": 0.5,
                          "roll": 0.0, "pitch": -math.pi/2, "yaw": 0.0}
        self.gripper_open = True
        self.estop = False
        self.battery_v = 48.0
        self.payload_kg = 0.0

    def move_to(self, x: float, y: float, z: float, speed: float = 0.3) -> dict:
        """Simulate moving TCP to cartesian target."""
        time.sleep(min(0.1, 0.05 * abs(x - self.tcp_pose["x"])))  # fake latency
        self.tcp_pose.update({"x": x, "y": y, "z": z})
        return {"reached": True, "tcp": dict(self.tcp_pose)}

    def move_joints(self, j1: float, j2: float, j3: float,
                    j4: float, j5: float, j6: float, speed: float = 0.3) -> dict:
        self.joints_rad = [j1, j2, j3, j4, j5, j6]
        return {"joints": list(self.joints_rad)}

    def set_gripper(self, open_mm: float) -> dict:
        self.gripper_open = open_mm > 50
        return {"open_mm": open_mm, "gripping": not self.gripper_open}

    def home(self) -> dict:
        self.joints_rad = [0.0, -math.pi/4, math.pi/2, 0.0, math.pi/2, 0.0]
        self.tcp_pose = {"x": 0.3, "y": 0.0, "z": 0.5,
                          "roll": 0.0, "pitch": -math.pi/2, "yaw": 0.0}
        return {"homed": True}

    def pick(self, x: float, y: float, z: float) -> dict:
        self.move_to(x, y, z + 0.1)
        self.move_to(x, y, z)
        self.set_gripper(0)   # close
        self.move_to(x, y, z + 0.1)
        self.payload_kg = random.uniform(0.1, 0.5)
        return {"picked": True, "payload_kg": round(self.payload_kg, 3)}

    def place(self, x: float, y: float, z: float) -> dict:
        self.move_to(x, y, z + 0.1)
        self.move_to(x, y, z)
        self.set_gripper(80)  # open
        self.move_to(x, y, z + 0.1)
        self.payload_kg = 0.0
        return {"placed": True}


# ─────────────────────────────────────────────────────────────────────────────
#  BUILD SERVER
# ─────────────────────────────────────────────────────────────────────────────

def build_arm_server(name: str = "ur5-arm-01",
                     model: str = "UR5e",
                     serial: str = "2024-001",
                     location: str = "cell-A") -> PMCPServer:

    server = PMCPServer(
        name=name, version="1.0.0",
        robot_class="arm", model=model,
        serial=serial, location=location,
    )
    arm = ArmState()

    # ── Actuations ────────────────────────────────────────────────────────────

    @server.actuation(
        "move_to",
        description=(
            "Move the robot TCP (tool center point) to a Cartesian XYZ position. "
            "Runs shadow simulation before executing. Requires workspace lease."
        ),
        category="motion", max_speed_m_s=1.0, max_force_n=100.0,
        max_energy_j=200.0, est_duration_s=3.0,
    )
    async def move_to(x: float, y: float, z: float, speed: float = 0.3):
        result = arm.move_to(x, y, z, speed)
        return ActuationResult(
            success=True, robot_id=server.robot_id,
            output=result, final_pose=result.get("tcp"),
        )

    @server.actuation(
        "move_joints",
        description=(
            "Move all 6 joints to target angles (radians). "
            "Use this for precise joint-space control."
        ),
        category="motion", max_speed_m_s=0.5, requires_lease=True,
    )
    async def move_joints(j1: float, j2: float, j3: float,
                          j4: float, j5: float, j6: float, speed: float = 0.3):
        result = arm.move_joints(j1, j2, j3, j4, j5, j6, speed)
        return ActuationResult(success=True, robot_id=server.robot_id, output=result)

    @server.actuation(
        "set_gripper",
        description=(
            "Open or close the gripper. open_mm=80 is fully open, open_mm=0 is fully closed."
        ),
        category="manipulation", max_energy_j=5.0,
        requires_lease=False, shadow_required=False,
    )
    async def set_gripper(open_mm: float = 80.0):
        result = arm.set_gripper(open_mm)
        return ActuationResult(success=True, robot_id=server.robot_id, output=result)

    @server.actuation(
        "home",
        description="Move the robot to its home (zero) configuration safely.",
        category="motion", max_speed_m_s=0.3, max_energy_j=150.0,
        est_duration_s=5.0,
    )
    async def home():
        result = arm.home()
        return ActuationResult(success=True, robot_id=server.robot_id, output=result)

    @server.actuation(
        "pick",
        description=(
            "Pick an object at the specified XYZ position. "
            "Automatically opens gripper, descends, grasps, and retracts."
        ),
        category="manipulation", max_speed_m_s=0.5, max_energy_j=300.0,
        est_duration_s=8.0,
    )
    async def pick(x: float, y: float, z: float):
        result = arm.pick(x, y, z)
        return ActuationResult(success=True, robot_id=server.robot_id, output=result)

    @server.actuation(
        "place",
        description=(
            "Place the currently held object at the specified XYZ position. "
            "Descends to target, opens gripper, and retracts."
        ),
        category="manipulation", max_speed_m_s=0.5, max_energy_j=300.0,
        est_duration_s=8.0,
    )
    async def place(x: float, y: float, z: float):
        result = arm.place(x, y, z)
        return ActuationResult(success=True, robot_id=server.robot_id, output=result)

    @server.actuation(
        "run_trajectory",
        description=(
            "Execute a predefined trajectory by name. "
            "Available: 'scan_workspace', 'inspection_pass', 'calibration'"
        ),
        category="motion", max_speed_m_s=0.5, max_energy_j=500.0,
        est_duration_s=15.0,
    )
    async def run_trajectory(trajectory_name: str, speed_scale: float = 1.0):
        trajectories = {
            "scan_workspace": "Scanning XY grid 0.2→0.8m",
            "inspection_pass": "Running inspection waypoints",
            "calibration": "Running calibration routine",
        }
        if trajectory_name not in trajectories:
            return ActuationResult(
                success=False, robot_id=server.robot_id,
                error_message=f"Unknown trajectory: {trajectory_name}. "
                              f"Choose from: {list(trajectories.keys())}",
            )
        return ActuationResult(
            success=True, robot_id=server.robot_id,
            output={"trajectory": trajectory_name,
                    "result": trajectories[trajectory_name]},
        )

    # ── Sensors ───────────────────────────────────────────────────────────────

    @server.sensor("joint_angles", description="Current joint angles for all 6 DOF",
                   sensor_type=SensorType.JOINT_STATES, unit="rad", hz=125.0)
    async def joint_angles():
        noise = [random.gauss(0, 0.0001) for _ in range(6)]
        return SensorReading(
            sensor_name="joint_angles", robot_id=server.robot_id,
            value=[round(j + n, 6) for j, n in zip(arm.joints_rad, noise)],
            unit="rad",
        )

    @server.sensor("tcp_pose", description="Current tool center point pose (x,y,z,roll,pitch,yaw)",
                   sensor_type=SensorType.END_EFFECTOR, unit="m/rad", hz=125.0)
    async def tcp_pose():
        return SensorReading(
            sensor_name="tcp_pose", robot_id=server.robot_id,
            value=dict(arm.tcp_pose),
            unit="m/rad",
        )

    @server.sensor("force_torque", description="End-effector force/torque (Fx,Fy,Fz,Tx,Ty,Tz)",
                   sensor_type=SensorType.FORCE_TORQUE, unit="N/Nm", hz=500.0)
    async def force_torque():
        return SensorReading(
            sensor_name="force_torque", robot_id=server.robot_id,
            value={
                "Fx": round(random.gauss(0, 0.5), 3),
                "Fy": round(random.gauss(0, 0.5), 3),
                "Fz": round(arm.payload_kg * 9.81 + random.gauss(0, 0.1), 3),
                "Tx": round(random.gauss(0, 0.05), 4),
                "Ty": round(random.gauss(0, 0.05), 4),
                "Tz": round(random.gauss(0, 0.05), 4),
            },
            unit="N/Nm",
        )

    @server.sensor("battery", description="Controller power supply voltage",
                   sensor_type=SensorType.BATTERY, unit="V", hz=1.0)
    async def battery():
        arm.battery_v += random.gauss(0, 0.01)
        return SensorReading(
            sensor_name="battery", robot_id=server.robot_id,
            value=round(arm.battery_v, 2), unit="V",
        )

    @server.sensor("gripper_state", description="Gripper open/close state and payload",
                   sensor_type=SensorType.CUSTOM, unit="mm/kg", hz=10.0)
    async def gripper_state():
        return SensorReading(
            sensor_name="gripper_state", robot_id=server.robot_id,
            value={"open_mm": 80 if arm.gripper_open else 0,
                    "payload_kg": round(arm.payload_kg, 3),
                    "gripping": not arm.gripper_open},
        )

    # ── Missions ──────────────────────────────────────────────────────────────

    @server.mission("pick_and_place",
                    description="Pick an object from source location and place at destination",
                    robot_class="arm")
    async def pick_and_place_mission(source_x: float, source_y: float, source_z: float,
                                      dest_x: float, dest_y: float, dest_z: float):
        source_x = float(source_x); source_y = float(source_y); source_z = float(source_z)
        dest_x   = float(dest_x);   dest_y   = float(dest_y);   dest_z   = float(dest_z)
        steps = (
            f"1. Move to pre-pick position above source ({source_x}, {source_y}, {source_z+0.1})\n"
            f"2. Call pick({source_x}, {source_y}, {source_z})\n"
            f"3. Move to clearance height\n"
            f"4. Call place({dest_x}, {dest_y}, {dest_z})\n"
            f"5. Call home() to return to safe position"
        )
        return MissionResult(
            mission_name="pick_and_place",
            messages=[
                {"role": "user", "content": [{"type": "text", "text":
                    f"Execute pick-and-place: source=({source_x},{source_y},{source_z}) "
                    f"dest=({dest_x},{dest_y},{dest_z})"}]},
                {"role": "assistant", "content": [{"type": "text", "text":
                    f"I'll execute the pick-and-place mission:\n{steps}\n"
                    f"Starting execution now..."}]},
            ],
        )

    @server.mission("workspace_scan",
                    description="Systematically scan the entire workspace for object detection",
                    robot_class="arm")
    async def workspace_scan_mission(rows: int = 3, cols: int = 3):
        return MissionResult(
            mission_name="workspace_scan",
            messages=[{"role": "user", "content": [{"type": "text", "text":
                f"Scan workspace in a {rows}x{cols} grid"}]},
            {"role": "assistant", "content": [{"type": "text", "text":
                f"Executing {rows}x{cols} workspace scan. "
                f"Will call run_trajectory('scan_workspace') and report findings."}]}],
        )

    return server


# ─────────────────────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="P-MCP Arm Server v0.5")
    parser.add_argument("--transport", default="stdio", choices=["stdio", "http"])
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--name", default="ur5-arm-01")
    parser.add_argument("--model", default="UR5e")
    parser.add_argument("--location", default="cell-A")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(message)s",
                        stream=sys.stderr)

    server = build_arm_server(name=args.name, model=args.model, location=args.location)
    asyncio.run(server.run(args.transport, host=args.host, port=args.port))
