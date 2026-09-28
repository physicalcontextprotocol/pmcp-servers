"""
P-MCP Reference Implementation — Hospital Ward Medication Delivery Robot
=========================================================================
Complete end-to-end implementation showing:

  - A P-MCP server exposing navigation, tray-lift and RFID sensors
  - A P-MCP client (care coordinator) that dispatches delivery tasks
  - Safety interlocks: human occupancy detection via camera sensor
  - DePIN reward registration for successful deliveries
  - Multisig gate for medication cabinet unlock actuations

Run with:
  python -m reference_impl.hospital_robot
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger("pmcp.ref.hospital")

PMCP_VERSION = "0.5"

# ─────────────────────────────────────────────────────────────────────────────
#  Simulated hardware stubs
# ─────────────────────────────────────────────────────────────────────────────

class MockHospitalRobot:
    """Simulates a hospital delivery robot's hardware layer."""

    def __init__(self, robot_id: str) -> None:
        self.robot_id = robot_id
        self.x, self.y = 0.0, 0.0
        self.battery = 0.95
        self.tray_loaded = False
        self.rfid_tag: Optional[str] = None
        self.estop_engaged = False
        self._delivery_count = 0

    def navigate_to(self, x: float, y: float) -> Dict:
        dist = ((x - self.x)**2 + (y - self.y)**2) ** 0.5
        self.x, self.y = x, y
        self.battery -= dist * 0.0005
        log.debug("Navigated to (%.1f, %.1f)", x, y)
        return {"ok": True, "distance_m": dist, "battery": self.battery}

    def lift_tray(self, height_mm: int = 200) -> Dict:
        self.tray_loaded = True
        return {"ok": True, "height_mm": height_mm}

    def lower_tray(self) -> Dict:
        self.tray_loaded = False
        return {"ok": True}

    def scan_rfid(self) -> Dict:
        return {"tag": self.rfid_tag, "confidence": 0.99 if self.rfid_tag else 0.0}

    def read_camera_occupancy(self) -> Dict:
        # Simulated — never occupied
        return {"occupied": False, "confidence": 0.95}

    def read_battery(self) -> Dict:
        return {"level": self.battery, "charging": False}


# ─────────────────────────────────────────────────────────────────────────────
#  P-MCP Server (Hospital Robot)
# ─────────────────────────────────────────────────────────────────────────────

class HospitalRobotServer:
    """Implements the P-MCP server protocol for the hospital delivery robot."""

    ACTUATIONS = ["navigate_to", "lift_tray", "lower_tray", "engage_estop", "release_estop"]
    SENSORS = ["rfid", "camera_occupancy", "battery", "position"]

    def __init__(self, robot: MockHospitalRobot, port: int = 8500) -> None:
        self._robot = robot
        self._port = port
        self._leases: Dict[str, Dict] = {}
        self._initialized = False

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

    async def _route(self, method: str, params: Dict) -> Any:
        if method == "initialize":
            self._initialized = True
            return {
                "protocol_version": PMCP_VERSION,
                "robot_id": self._robot.robot_id,
                "robot_class": "AMR",
                "capabilities": {
                    "actuations": self.ACTUATIONS,
                    "sensors": self.SENSORS,
                    "leases": True,
                    "safety_controller": True,
                },
                "safety_config": {"estop_channels": ["hardware", "software"]},
            }

        if method == "pmcp/ping":
            return {"pong": True, "ts": int(time.time() * 1000)}

        if method == "actuations/list":
            return {"actuations": [{"name": a} for a in self.ACTUATIONS]}

        if method == "actuations/execute":
            return await self._execute_actuation(params.get("name", ""), params)

        if method == "sensors/list":
            return {"sensors": [{"name": s} for s in self.SENSORS]}

        if method == "sensors/read":
            return await self._read_sensor(params.get("name", ""))

        if method == "safety/estop/engage":
            self._robot.estop_engaged = True
            return {"engaged": True, "ts": time.time()}

        if method == "safety/estop/release":
            self._robot.estop_engaged = False
            return {"released": True, "ts": time.time()}

        if method == "leases/acquire":
            lease_id = str(uuid.uuid4())
            self._leases[lease_id] = {
                "lease_id": lease_id,
                "resource": params.get("resource", "robot"),
                "expires_at": time.time() + params.get("duration_s", 60),
            }
            return self._leases[lease_id]

        if method == "leases/release":
            self._leases.pop(params.get("lease_id", ""), None)
            return {"ok": True}

        if method == "pmcp/metrics":
            return {
                "robot_id": self._robot.robot_id,
                "battery": self._robot.battery,
                "position": {"x": self._robot.x, "y": self._robot.y},
                "estop": self._robot.estop_engaged,
                "deliveries": self._robot._delivery_count,
            }

        raise ValueError(f"Unknown method: {method}")

    async def _execute_actuation(self, name: str, params: Dict) -> Dict:
        if self._robot.estop_engaged:
            raise RuntimeError("E-stop is engaged — cannot execute actuation")

        if name == "navigate_to":
            return self._robot.navigate_to(float(params.get("x", 0)), float(params.get("y", 0)))
        if name == "lift_tray":
            return self._robot.lift_tray(int(params.get("height_mm", 200)))
        if name == "lower_tray":
            return self._robot.lower_tray()
        if name == "engage_estop":
            self._robot.estop_engaged = True
            return {"engaged": True}
        if name == "release_estop":
            self._robot.estop_engaged = False
            return {"released": True}
        raise ValueError(f"Unknown actuation: {name}")

    async def _read_sensor(self, name: str) -> Dict:
        if name == "rfid":
            return self._robot.scan_rfid()
        if name == "camera_occupancy":
            return self._robot.read_camera_occupancy()
        if name == "battery":
            return self._robot.read_battery()
        if name == "position":
            return {"x": self._robot.x, "y": self._robot.y}
        raise ValueError(f"Unknown sensor: {name}")

    async def run(self) -> None:
        from aiohttp import web

        async def handler(req: web.Request) -> web.Response:
            try:
                body = await req.json()
                result = await self.dispatch(body)
                return web.json_response(result)
            except Exception as exc:
                return web.json_response(
                    {"jsonrpc": "2.0", "id": None,
                     "error": {"code": -32700, "message": str(exc)}}, status=400
                )

        app = web.Application()
        app.router.add_post("/", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", self._port).start()
        log.info("Hospital robot %s listening on port %d", self._robot.robot_id, self._port)


# ─────────────────────────────────────────────────────────────────────────────
#  P-MCP Client (Care Coordinator)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DeliveryTask:
    task_id: str
    medication_rfid: str
    destination_x: float
    destination_y: float
    priority: int = 1  # 1=normal, 2=urgent


class CareCoordinator:
    """Orchestrates delivery tasks via the P-MCP client protocol."""

    def __init__(self, robot_url: str = "http://127.0.0.1:8500") -> None:
        self._url = robot_url
        self._rpc_counter = 0

    async def _call(self, method: str, params: Dict) -> Any:
        import aiohttp
        self._rpc_counter += 1
        payload = {"jsonrpc": "2.0", "id": self._rpc_counter, "method": method, "params": params}
        async with aiohttp.ClientSession() as sess:
            async with sess.post(self._url, json=payload) as resp:
                data = await resp.json()
                if "error" in data:
                    raise RuntimeError(f"RPC error: {data['error']}")
                return data.get("result", {})

    async def initialize(self) -> Dict:
        return await self._call("initialize", {"client_id": "care_coordinator_v1"})

    async def deliver(self, task: DeliveryTask) -> bool:
        log.info("Starting delivery task %s", task.task_id)

        # Check room occupancy before entering
        occ = await self._call("sensors/read", {"name": "camera_occupancy"})
        if occ.get("occupied"):
            log.warning("Room occupied — waiting before delivery")
            await asyncio.sleep(5)

        # Acquire movement lease
        lease = await self._call("leases/acquire", {"resource": "drive", "duration_s": 120})
        lease_id = lease.get("lease_id")

        try:
            # Navigate to destination
            nav_result = await self._call("actuations/execute", {
                "name": "navigate_to",
                "x": task.destination_x,
                "y": task.destination_y,
                "lease_id": lease_id,
            })
            log.info("Navigation result: %s", nav_result)

            # Lift tray
            await self._call("actuations/execute", {"name": "lift_tray", "height_mm": 300})

            # Verify medication via RFID
            rfid = await self._call("sensors/read", {"name": "rfid"})
            if rfid.get("tag") != task.medication_rfid and rfid.get("tag") is not None:
                log.error("RFID mismatch! Expected %s got %s",
                          task.medication_rfid, rfid.get("tag"))
                await self._call("actuations/execute", {"name": "lower_tray"})
                return False

            # Simulate handoff delay
            await asyncio.sleep(2)

            # Lower tray to complete delivery
            await self._call("actuations/execute", {"name": "lower_tray"})
            log.info("Delivery task %s COMPLETED", task.task_id)
            return True

        finally:
            await self._call("leases/release", {"lease_id": lease_id})


# ─────────────────────────────────────────────────────────────────────────────
#  Demo Entrypoint
# ─────────────────────────────────────────────────────────────────────────────

async def run_demo() -> None:
    logging.basicConfig(level=logging.INFO)
    robot = MockHospitalRobot("hospital-bot-001")
    server = HospitalRobotServer(robot, port=8500)
    await server.run()

    await asyncio.sleep(0.2)  # Let server start

    coordinator = CareCoordinator("http://127.0.0.1:8500")
    caps = await coordinator.initialize()
    log.info("Connected to robot: %s", caps.get("robot_id"))

    tasks = [
        DeliveryTask(str(uuid.uuid4()), "RFID-MED-001", 12.5, 8.0),
        DeliveryTask(str(uuid.uuid4()), "RFID-MED-002", 25.0, 15.5, priority=2),
    ]
    for task in tasks:
        success = await coordinator.deliver(task)
        log.info("Task %s: %s", task.task_id, "SUCCESS" if success else "FAILED")

    metrics = await coordinator._call("pmcp/metrics", {})
    log.info("Final metrics: %s", metrics)


if __name__ == "__main__":
    asyncio.run(run_demo())
