#!/usr/bin/env python3
"""
P-MCP Hello Robot Tutorial
==========================

A 5-minute tutorial demonstrating how to connect Claude Desktop to a simulated TurtleBot.

This example creates a P-MCP server for a mobile robot (TurtleBot) and shows how to:
1. Define robot actuations (movement commands)
2. Expose sensors as MCP resources
3. Run the server in stdio mode (compatible with Claude Desktop)
4. Test with the MCP client

Usage:
    # Run the server (for Claude Desktop):
    python examples/hello_robot_turtlebot.py

    # Or test programmatically:
    python examples/hello_robot_turtlebot.py --test
"""

import argparse
import asyncio
import json
import math
import time
from math import cos, sin

from v05.pmcp_v5_server import PMCPServer
from v05.pmcp_v5_types import (
    ActuationResult,
    SensorReading,
    SensorType,
    PMCP_VERSION,
)


class SimulatedTurtleBot:
    """
    Simulated TurtleBot4 for testing without hardware.
    
    This simulates:
    - Position (x, y, theta)
    - Battery level
    - Obstacle detection
    """

    def __init__(self):
        self.x = 0.0
        self.y = 0.0
        self.theta = 0.0
        self.battery = 100.0
        self.moving = False

    def move(self, linear_vel: float, angular_vel: float, duration: float) -> dict:
        """Simulate robot movement."""
        dt = 0.1  # simulation timestep
        steps = int(duration / dt)

        for _ in range(steps):
            self.x += linear_vel * dt * cos(self.theta)
            self.y += linear_vel * dt * sin(self.theta)
            self.theta += angular_vel * dt

            # Drain battery
            self.battery -= 0.01 * (abs(linear_vel) + abs(angular_vel))

        self.battery = max(0, self.battery)

        return {
            "x": round(self.x, 3),
            "y": round(self.y, 3),
            "theta": round(self.theta, 3),
            "battery": round(self.battery, 1),
        }

    def get_pose(self) -> dict:
        """Get current pose."""
        return {
            "x": self.x,
            "y": self.y,
            "theta": self.theta,
        }

    def get_battery(self) -> float:
        """Get battery level."""
        return self.battery


# Global simulated robot
robot = SimulatedTurtleBot()


def create_turtlebot_server() -> PMCPServer:
    """Create a P-MCP server for TurtleBot."""

    server = PMCPServer(
        name="turtlebot-demo",
        version="1.0.0",
        robot_id="turtlebot-001",
        robot_class="mobile",
        model="TurtleBot4",
        serial="TB4-2024-001",
        location="demo-lab",
    )

    # =========================================================================
    # Register Actuation (MCP Tool equivalent)
    # =========================================================================

    @server.actuation(
        "drive",
        description="Drive the robot with linear and angular velocity",
        category="navigation",
        max_speed_m_s=0.5,
        est_duration_s=10.0,
    )
    async def drive(linear_vel: float, angular_vel: float, duration: float):
        """Drive the robot."""
        result = robot.move(linear_vel, angular_vel, duration)
        return ActuationResult(
            success=True,
            output=result,
        )

    @server.actuation(
        "go_to_point",
        description="Navigate to a target point (x, y)",
        category="navigation",
        max_speed_m_s=0.3,
        est_duration_s=30.0,
    )
    async def go_to_point(x: float, y: float):
        """Navigate to target point using simple proportional control."""
        dx = x - robot.x
        dy = y - robot.y
        distance = (dx**2 + dy**2) ** 0.5

        # Simple proportional control
        while distance > 0.05:
            linear_vel = min(0.2, distance)
            angular_vel = (robot.y - y) * -0.5  # Simplified

            robot.move(linear_vel, angular_vel, 0.1)

            dx = x - robot.x
            dy = y - robot.y
            distance = (dx**2 + dy**2) ** 0.5

        return ActuationResult(
            success=True,
            output={
                "reached": True,
                "x": robot.x,
                "y": robot.y,
            },
        )

    @server.actuation(
        "stop",
        description="Stop all robot motion",
        category="navigation",
        max_speed_m_s=0.0,
        est_duration_s=0.5,
    )
    async def stop():
        """Stop the robot."""
        robot.move(0, 0, 0.1)
        return ActuationResult(
            success=True,
            output={"stopped": True},
        )

    @server.actuation(
        "set_pose",
        description="Set robot pose (for simulation reset)",
        category="utility",
        max_speed_m_s=0.0,
        est_duration_s=0.1,
        shadow_required=False,
    )
    async def set_pose(x: float, y: float, theta: float):
        """Set robot pose - utility for testing."""
        robot.x = x
        robot.y = y
        robot.theta = theta
        return ActuationResult(
            success=True,
            output={"x": x, "y": y, "theta": theta},
        )

    # =========================================================================
    # Register Sensors (MCP Resource equivalent)
    # =========================================================================

    @server.sensor(
        "pose",
        description="Current robot pose (x, y, theta)",
        sensor_type=SensorType.ODOMETRY,
        unit="m, m, rad",
        hz=10.0,
    )
    async def pose() -> SensorReading:
        """Get current pose."""
        return SensorReading(
            sensor_name="pose",
            robot_id=server.robot_id,
            value=robot.get_pose(),
            unit="m, m, rad",
        )

    @server.sensor(
        "battery",
        description="Battery level percentage",
        sensor_type=SensorType.BATTERY,
        unit="%",
        hz=1.0,
    )
    async def battery() -> SensorReading:
        """Get battery level."""
        return SensorReading(
            sensor_name="battery",
            robot_id=server.robot_id,
            value={"level": robot.get_battery(), "charging": False},
            unit="%",
        )

    @server.sensor(
        "laser_scan",
        description="Simulated laser scan (front 180 degrees)",
        sensor_type=SensorType.LIDAR,
        unit="m",
        hz=5.0,
    )
    async def laser_scan() -> SensorReading:
        """Get simulated laser scan."""
        # Generate fake laser scan
        ranges = [2.0] * 180  # 180 samples
        return SensorReading(
            sensor_name="laser_scan",
            robot_id=server.robot_id,
            value={"ranges": ranges, "angle_min": -1.57, "angle_max": 1.57},
            unit="m",
        )

    # =========================================================================
    # Register Mission (MCP Prompt equivalent)
    # =========================================================================

    @server.mission("patrol", description="Patrol a rectangular path", robot_class="mobile")
    async def patrol(width: float, height: float, loops: int = 1):
        """Generate a patrol mission sequence."""
        from v05.pmcp_v5_types import MissionResult

        steps = []
        w, h = width / 2, height / 2
        points = [
            (w, h),
            (-w, h),
            (-w, -h),
            (w, -h),
            (w, h),
        ]

        for _ in range(loops):
            for x, y in points:
                steps.append(f"go_to_point(x={x}, y={y})")

        return MissionResult(
            mission_name="patrol",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Execute patrol: {len(steps)} waypoints, "
                            f"{width}m x {height}m area, {loops} loop(s)",
                        }
                    ],
                }
            ],
        )

    return server


async def test_server():
    """Test the server programmatically."""
    print("=" * 60)
    print("P-MCP Hello Robot Tutorial - Test Mode")
    print("=" * 60)

    server = create_turtlebot_server()

    # Test 1: Initialize
    print("\n[Test 1] Initialize...")
    req = {"jsonrpc": "2.0", "id": "1", "method": "initialize", "params": {}}
    resp = await server.handle_message(req)
    print(f"  → {resp['result']['serverInfo']}")

    # Test 2: List tools
    print("\n[Test 2] List tools (actuations)...")
    req = {"jsonrpc": "2.0", "id": "2", "method": "tools/list", "params": {}}
    resp = await server.handle_message(req)
    tools = resp['result']['tools']
    print(f"  → {len(tools)} tools: {', '.join(t['name'] for t in tools)}")

    # Test 3: List resources
    print("\n[Test 3] List resources (sensors)...")
    req = {"jsonrpc": "2.0", "id": "3", "method": "resources/list", "params": {}}
    resp = await server.handle_message(req)
    resources = resp['result']['resources']
    print(f"  → {len(resources)} resources: {', '.join(r['name'] for r in resources)}")

    # Test 4: Drive command
    print("\n[Test 4] Drive command...")
    req = {
        "jsonrpc": "2.0",
        "id": "4",
        "method": "tools/call",
        "params": {
            "name": "drive",
            "arguments": {"linear_vel": 0.2, "angular_vel": 0.0, "duration": 1.0},
        },
    }
    resp = await server.handle_message(req)
    if resp.get('result'):
        output = json.loads(resp['result']['content'][0]['text'])
        print(f"  → Position: x={output['output']['x']}, y={output['output']['y']}")

    # Test 5: Read sensor
    print("\n[Test 5] Read pose sensor...")
    req = {"jsonrpc": "2.0", "id": "5", "method": "resources/read", "params": {"uri": "pmcp://turtlebot-001/sensors/pose"}}
    resp = await server.handle_message(req)
    if resp.get('result'):
        reading = json.loads(resp['result']['contents'][0]['text'])
        print(f"  → Pose: {reading['value']}")

    # Test 6: Status
    print("\n[Test 6] Server status...")
    req = {"jsonrpc": "2.0", "id": "6", "method": "pmcp/status", "params": {}}
    resp = await server.handle_message(req)
    status = resp['result']
    print(f"  → Uptime: {status['uptime_s']:.1f}s, Calls: {status['call_count']}")

    print("\n" + "=" * 60)
    print("All tests passed!")
    print("=" * 60)


async def run_server():
    """Run the server in stdio mode (for Claude Desktop)."""
    print("[P-MCP] TurtleBot Demo Server v0.5 ready")
    print("[P-MCP] Connect Claude Desktop to control this robot")

    server = create_turtlebot_server()
    await server.run()


def main():
    parser = argparse.ArgumentParser(description="P-MCP Hello Robot Tutorial")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run in test mode (programmatic testing)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport mode (default: stdio)",
    )

    args = parser.parse_args()

    if args.test:
        asyncio.run(test_server())
    else:
        asyncio.run(run_server())


if __name__ == "__main__":
    main()