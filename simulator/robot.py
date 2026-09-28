"""
P-MCP Fleet Simulator — Simulated Robot Agent
===============================================
Each SimRobot exposes a minimal P-MCP HTTP server so real PMCPClients
can connect to it during integration tests and demos.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from aiohttp import web

from .world import Vec2, WarehouseWorld

log = logging.getLogger("pmcp.simulator.robot")

PMCP_VERSION = "0.5"


# ─────────────────────────────────────────────────────────────────────────────
#  State Machines
# ─────────────────────────────────────────────────────────────────────────────

class RobotState(str, Enum):
    IDLE = "idle"
    MOVING = "moving"
    EXECUTING = "executing"
    CHARGING = "charging"
    ESTOP = "estop"
    FAULT = "fault"


class RobotClass(str, Enum):
    AMR = "amr"            # Autonomous Mobile Robot
    ARM = "arm"            # Robotic Arm
    DRONE = "drone"
    FORKLIFT = "forklift"


# ─────────────────────────────────────────────────────────────────────────────
#  Simulated Robot
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SimRobot:
    robot_id: str
    robot_class: RobotClass = RobotClass.AMR

    # Physical state
    position: Vec2 = field(default_factory=lambda: Vec2(50.0, 30.0))
    heading: float = 0.0         # radians
    velocity: float = 0.0        # m/s
    max_speed: float = 1.5       # m/s
    battery_pct: float = 100.0

    # Status
    state: RobotState = RobotState.IDLE
    current_mission: Optional[str] = None
    estop_active: bool = False

    # Simulation bookkeeping
    _target: Optional[Vec2] = field(default=None, repr=False)
    _world: Optional[WarehouseWorld] = field(default=None, repr=False)
    _start_time: float = field(default_factory=time.time, repr=False)

    # Server
    _app: Optional[web.Application] = field(default=None, repr=False)
    _port: int = 0

    def __post_init__(self) -> None:
        self._world = WarehouseWorld()

    # ── Physics update (called every 100 ms) ────────────────────────────────

    def tick(self, dt: float = 0.1) -> None:
        if self.estop_active or self.state == RobotState.FAULT:
            self.velocity = 0.0
            return

        # Battery drain
        self.battery_pct = max(0.0, self.battery_pct - 0.001 * dt)

        if self.state == RobotState.CHARGING:
            self.battery_pct = min(100.0, self.battery_pct + 1.0 * dt)
            return

        if self._target is None or self.state not in (RobotState.MOVING, RobotState.IDLE):
            self.velocity = 0.0
            return

        # Move toward target
        diff = self._target - self.position
        dist = math.hypot(diff.x, diff.y)

        if dist < 0.1:
            self.position = self._target
            self._target = None
            self.state = RobotState.IDLE
            self.velocity = 0.0
            return

        self.heading = math.atan2(diff.y, diff.x)
        speed = min(self.max_speed, dist / 0.5)  # slow down near target
        step = speed * dt
        direction = diff.normalized()
        new_pos = Vec2(
            self.position.x + direction.x * step,
            self.position.y + direction.y * step,
        )
        if self._world and self._world.is_free(new_pos):
            self.position = new_pos
        else:
            # Simple obstacle avoidance: rotate heading
            self.heading += math.radians(15)
        self.velocity = speed
        self.state = RobotState.MOVING

    # ── Command interface ────────────────────────────────────────────────────

    def move_to(self, x: float, y: float) -> None:
        if self.estop_active:
            return
        target = Vec2(x, y)
        if self._world:
            target = self._world.nearest_free(target)
        self._target = target
        self.state = RobotState.MOVING

    def engage_estop(self) -> None:
        self.estop_active = True
        self.state = RobotState.ESTOP
        self.velocity = 0.0
        self._target = None

    def release_estop(self) -> None:
        self.estop_active = False
        self.state = RobotState.IDLE

    def charge(self) -> None:
        self.state = RobotState.CHARGING
        self._target = None

    # ── Sensor readings ──────────────────────────────────────────────────────

    def sensor_reading(self, sensor_name: str) -> Dict[str, Any]:
        now = int(time.time() * 1000)
        readings: Dict[str, Dict] = {
            "position_x": {"value": self.position.x, "unit": "m"},
            "position_y": {"value": self.position.y, "unit": "m"},
            "heading": {"value": math.degrees(self.heading), "unit": "deg"},
            "velocity": {"value": self.velocity, "unit": "m/s"},
            "battery": {"value": self.battery_pct, "unit": "%"},
            "state": {"value": self.state.value, "unit": "enum"},
            "uptime": {"value": time.time() - self._start_time, "unit": "s"},
        }
        r = readings.get(sensor_name, {"value": 0.0, "unit": "unknown"})
        return {
            "sensor_name": sensor_name,
            "value": r["value"],
            "unit": r["unit"],
            "timestamp_ms": now,
            "quality": 1.0 if not self.estop_active else 0.5,
        }

    # ── P-MCP HTTP Server ───────────────────────────────────────────────────

    def _pmcp_response(self, result: Any, req_id: Any) -> Dict:
        return {"jsonrpc": "2.0", "result": result, "id": req_id}

    def _pmcp_error(self, code: int, msg: str, req_id: Any) -> Dict:
        return {"jsonrpc": "2.0", "error": {"code": code, "message": msg}, "id": req_id}

    def _dispatch(self, method: str, params: Dict, req_id: Any) -> Dict:
        if method == "initialize":
            return self._pmcp_response({
                "protocolVersion": PMCP_VERSION,
                "serverInfo": {"name": self.robot_id, "version": "0.5.0"},
                "capabilities": {
                    "actuations": True,
                    "sensors": True,
                    "leases": True,
                    "safety": True,
                },
            }, req_id)

        elif method == "actuations/list":
            return self._pmcp_response({
                "actuations": [
                    {"name": "move_to", "description": "Move robot to (x,y)", "parameters": {"x": "float", "y": "float"}},
                    {"name": "engage_estop", "description": "Emergency stop"},
                    {"name": "release_estop", "description": "Release emergency stop"},
                    {"name": "charge", "description": "Go to charging state"},
                ]
            }, req_id)

        elif method == "actuations/execute":
            name = (params or {}).get("name", "")
            act_params = (params or {}).get("parameters", {})
            if name == "move_to":
                self.move_to(float(act_params.get("x", 0)), float(act_params.get("y", 0)))
            elif name == "engage_estop":
                self.engage_estop()
            elif name == "release_estop":
                self.release_estop()
            elif name == "charge":
                self.charge()
            else:
                return self._pmcp_error(-32601, f"Unknown actuation: {name}", req_id)
            return self._pmcp_response({"success": True, "energy_wh": 0.01, "duration_ms": 100}, req_id)

        elif method == "actuations/batch":
            results = []
            for act in (params or {}).get("actuations", []):
                r = self._dispatch("actuations/execute", act, req_id)
                results.append(r.get("result", {}))
            return self._pmcp_response({
                "results": results,
                "total_duration_ms": len(results) * 100,
            }, req_id)

        elif method == "sensors/list":
            return self._pmcp_response({
                "sensors": [
                    {"name": n, "description": f"Robot sensor: {n}"}
                    for n in ["position_x", "position_y", "heading", "velocity", "battery", "state", "uptime"]
                ]
            }, req_id)

        elif method == "sensors/read":
            name = (params or {}).get("name", "")
            return self._pmcp_response(self.sensor_reading(name), req_id)

        elif method == "leases/acquire":
            return self._pmcp_response({
                "granted": True,
                "lease_id": f"lease-{self.robot_id}-{int(time.time())}",
                "expires_ms": int(time.time() * 1000) + 30_000,
                "robot_id": self.robot_id,
                "zone_id": "sim-zone-0",
            }, req_id)

        elif method == "leases/release":
            return self._pmcp_response({"released": True}, req_id)

        elif method == "pmcp/ping":
            return self._pmcp_response({
                "pong": True,
                "timestamp_ms": int(time.time() * 1000),
                "robot_id": self.robot_id,
            }, req_id)

        elif method == "pmcp/metrics":
            return self._pmcp_response({
                "uptime_s": time.time() - self._start_time,
                "requests_total": 0,
                "errors_total": 0,
                "active_leases": 0,
                "actuations_executed": 0,
                "battery_pct": self.battery_pct,
                "state": self.state.value,
                "cpu_pct": 0.0,
                "memory_mb": 0.0,
            }, req_id)

        elif method == "safety/estop/engage":
            self.engage_estop()
            return self._pmcp_response({"engaged": True}, req_id)

        elif method == "safety/estop/disengage":
            self.release_estop()
            return self._pmcp_response({"disengaged": True}, req_id)

        else:
            return self._pmcp_error(-32601, f"Method not found: {method}", req_id)

    async def _handle_mcp(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response(self._pmcp_error(-32700, "Parse error", None))

        method = body.get("method", "")
        params = body.get("params", {})
        req_id = body.get("id")
        result = self._dispatch(method, params, req_id)
        return web.json_response(result)

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "robot_id": self.robot_id,
            "state": self.state.value,
            "battery_pct": self.battery_pct,
        })

    async def start_server(self, port: int) -> None:
        self._port = port
        app = web.Application()
        app.router.add_post("/mcp", self._handle_mcp)
        app.router.add_get("/health", self._handle_health)
        self._app = app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", port)
        await site.start()
        log.info("SimRobot %s listening on port %d", self.robot_id, port)
