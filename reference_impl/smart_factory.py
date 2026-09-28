"""
P-MCP Reference Implementation — Smart Factory Cell
=====================================================
Shows P-MCP used in a 4-robot manufacturing cell:
  - UR5 arm: picks raw parts from feeder belt
  - Franka Panda: precision assembly operation
  - TurtleBot3: transports finished assemblies to QA station
  - Vision robot: QA inspection via camera sensor

The cell controller coordinates all four robots using the P-MCP fleet client.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional

log = logging.getLogger("pmcp.ref.factory")

PMCP_VERSION = "0.5"


@dataclass
class Part:
    part_id: str
    part_type: str
    location: str = "feeder"
    quality_pass: Optional[bool] = None


class MockFactoryRobot:
    """Generic mock for factory robots."""

    def __init__(self, robot_id: str, robot_class: str, port: int) -> None:
        self.robot_id = robot_id
        self.robot_class = robot_class
        self.port = port
        self.estop = False
        self.battery = 1.0
        self.x, self.y = 0.0, 0.0
        self._call_count = 0

    def tick(self) -> None:
        self.battery = max(0.0, self.battery - 0.001)


class FactoryCellServer:
    """
    P-MCP server for a factory robot — handles all four roles with
    role-specific actuation/sensor dispatch.
    """

    ROLE_CAPS = {
        "arm": {
            "actuations": ["moveJ", "moveL", "open_gripper", "close_gripper", "home"],
            "sensors": ["joints", "tcp_pose", "joint_temps"],
        },
        "amr": {
            "actuations": ["navigate_to", "forward", "rotate", "stop"],
            "sensors": ["odom", "lidar", "battery"],
        },
        "vision": {
            "actuations": [],
            "sensors": ["camera", "depth", "inspection_result"],
        },
    }

    def __init__(self, robot: MockFactoryRobot) -> None:
        self._robot = robot
        role = "arm" if robot.robot_class in {"UR5", "Panda"} else \
               "vision" if robot.robot_class == "Vision" else "amr"
        caps = self.ROLE_CAPS[role]
        self._actuations = caps["actuations"]
        self._sensors = caps["sensors"]
        self._leases: Dict[str, Dict] = {}

    async def dispatch(self, request: Dict) -> Dict:
        method = request.get("method", "")
        params = request.get("params", {})
        rpc_id = request.get("id")
        try:
            result = await self._route(method, params)
            return {"jsonrpc": "2.0", "id": rpc_id, "result": result}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": rpc_id,
                    "error": {"code": -32000, "message": str(exc)}}

    async def _route(self, method: str, params: Dict):
        r = self._robot
        if method == "initialize":
            return {
                "protocol_version": PMCP_VERSION,
                "robot_id": r.robot_id,
                "robot_class": r.robot_class,
                "capabilities": {
                    "actuations": self._actuations,
                    "sensors": self._sensors,
                    "leases": True,
                },
            }
        if method == "pmcp/ping":
            return {"pong": True}
        if method == "actuations/list":
            return {"actuations": [{"name": a} for a in self._actuations]}
        if method == "sensors/list":
            return {"sensors": [{"name": s} for s in self._sensors]}
        if method == "actuations/execute":
            name = params.get("name", "")
            if name not in self._actuations:
                raise ValueError(f"Unknown actuation: {name}")
            await asyncio.sleep(0.1)  # simulate execution time
            r._call_count += 1
            return {"ok": True, "actuation": name, "ts": time.time()}
        if method == "sensors/read":
            name = params.get("name", "")
            if name not in self._sensors:
                raise ValueError(f"Unknown sensor: {name}")
            return self._mock_sensor(name)
        if method == "safety/estop/engage":
            r.estop = True; return {"engaged": True}
        if method == "safety/estop/release":
            r.estop = False; return {"released": True}
        if method == "leases/acquire":
            lid = str(uuid.uuid4())
            self._leases[lid] = {"lease_id": lid, "expires_at": time.time() + 60}
            return self._leases[lid]
        if method == "leases/release":
            self._leases.pop(params.get("lease_id", ""), None); return {"ok": True}
        if method == "pmcp/metrics":
            return {"robot_id": r.robot_id, "battery": r.battery, "calls": r._call_count}
        raise ValueError(f"Unknown method: {method}")

    def _mock_sensor(self, name: str) -> Dict:
        r = self._robot
        mock = {
            "joints": {"q": [0.0]*6, "dq": [0.0]*6},
            "tcp_pose": {"x": r.x, "y": r.y, "z": 0.3, "rx": 0.0, "ry": 0.0, "rz": 0.0},
            "joint_temps": {"temps_C": [35.0]*6},
            "odom": {"x": r.x, "y": r.y, "theta": 0.0},
            "lidar": {"ranges": [2.0]*360, "angle_min": -3.14, "angle_max": 3.14},
            "battery": {"level": r.battery},
            "camera": {"width": 640, "height": 480, "encoding": "rgb8"},
            "depth": {"width": 640, "height": 480, "encoding": "32FC1"},
            "inspection_result": {"pass": True, "defects": [], "confidence": 0.97},
        }
        return mock.get(name, {})

    async def run(self) -> None:
        from aiohttp import web
        async def handler(req: web.Request) -> web.Response:
            body = await req.json()
            result = await self.dispatch(body)
            return web.json_response(result)

        app = web.Application()
        app.router.add_post("/", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", self._robot.port).start()
        log.info("Factory robot %s (%s) on port %d",
                 self._robot.robot_id, self._robot.robot_class, self._robot.port)


class FactoryCellController:
    """Coordinates all four factory robots through a manufacturing cycle."""

    def __init__(self, robots: Dict[str, str]) -> None:
        """robots: {robot_id: base_url}"""
        self._robots = robots
        self._counter = 0

    async def _call(self, url: str, method: str, params: Dict = {}) -> Dict:
        import aiohttp
        self._counter += 1
        payload = {"jsonrpc": "2.0", "id": self._counter, "method": method, "params": params}
        async with aiohttp.ClientSession() as sess:
            async with sess.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                if "error" in data:
                    raise RuntimeError(data["error"]["message"])
                return data.get("result", {})

    async def run_production_cycle(self, part: Part) -> bool:
        urls = self._robots
        log.info("=== Production cycle for part %s ===", part.part_id)

        # Step 1: UR5 picks part from feeder
        log.info("Step 1: UR5 picking part")
        await self._call(urls["ur5"], "actuations/execute", {"name": "open_gripper"})
        await self._call(urls["ur5"], "actuations/execute",
                         {"name": "moveL", "pose": [0.3, 0.1, 0.4, 0, 0, 0]})
        await self._call(urls["ur5"], "actuations/execute", {"name": "close_gripper"})
        part.location = "ur5_gripper"

        # Step 2: UR5 hands off to assembly station
        await self._call(urls["ur5"], "actuations/execute",
                         {"name": "moveL", "pose": [0.5, 0.0, 0.3, 0, 0, 0]})
        await self._call(urls["ur5"], "actuations/execute", {"name": "open_gripper"})

        # Step 3: Panda does precision assembly
        log.info("Step 3: Panda assembling")
        await self._call(urls["panda"], "actuations/execute",
                         {"name": "moveL", "pose": [0.5, 0.0, 0.3, 0, 0, 0]})
        await self._call(urls["panda"], "actuations/execute", {"name": "close_gripper"})
        await self._call(urls["panda"], "actuations/execute",
                         {"name": "moveL", "pose": [0.5, 0.0, 0.1, 0, 0, 0]})
        await self._call(urls["panda"], "actuations/execute", {"name": "open_gripper"})
        part.location = "assembly_station"

        # Step 4: Vision robot inspects
        log.info("Step 4: Vision inspection")
        inspection = await self._call(urls["vision"], "sensors/read", {"name": "inspection_result"})
        part.quality_pass = inspection.get("pass", False)
        log.info("Inspection: %s (confidence=%.2f)",
                 "PASS" if part.quality_pass else "FAIL",
                 inspection.get("confidence", 0))

        # Step 5: TurtleBot transports to QA or reject bin
        target_x = 10.0 if part.quality_pass else 5.0
        target_y = 2.0
        log.info("Step 5: TurtleBot transporting to %s",
                 "QA station" if part.quality_pass else "reject bin")
        await self._call(urls["turtlebot"], "actuations/execute",
                         {"name": "navigate_to", "x": target_x, "y": target_y})

        log.info("=== Cycle complete — part %s: %s ===",
                 part.part_id, "PASS" if part.quality_pass else "REJECT")
        return part.quality_pass or False


async def run_factory_demo() -> None:
    logging.basicConfig(level=logging.INFO)

    configs = [
        ("ur5-001", "UR5", 8600),
        ("panda-001", "Panda", 8601),
        ("turtlebot-001", "TurtleBot3", 8602),
        ("vision-001", "Vision", 8603),
    ]

    # Start all servers
    servers = []
    for robot_id, robot_class, port in configs:
        robot = MockFactoryRobot(robot_id, robot_class, port)
        srv = FactoryCellServer(robot)
        servers.append(srv)
        await srv.run()

    await asyncio.sleep(0.3)

    robot_urls = {
        "ur5": "http://127.0.0.1:8600",
        "panda": "http://127.0.0.1:8601",
        "turtlebot": "http://127.0.0.1:8602",
        "vision": "http://127.0.0.1:8603",
    }
    controller = FactoryCellController(robot_urls)

    # Initialize all robots
    for url in robot_urls.values():
        await controller._call(url, "initialize", {"client_id": "factory_controller"})

    # Run 3 production cycles
    for i in range(3):
        part = Part(str(uuid.uuid4()), "widget_v2")
        await controller.run_production_cycle(part)

    log.info("Factory demo complete")


if __name__ == "__main__":
    asyncio.run(run_factory_demo())
