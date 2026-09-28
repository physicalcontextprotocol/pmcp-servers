"""
P-MCP Fleet Simulator — 50-Robot Warehouse Scenario
=====================================================
Launches 50 simulated AMRs in a warehouse world, registers them with the
P-MCP registry, and runs a mission loop dispatching pick-and-place tasks.

Usage:
    python -m simulator.warehouse_scenario --robots 50 --registry http://localhost:8888
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Dict, List, Optional

import aiohttp

from .world import WarehouseWorld, Vec2
from .robot import SimRobot, RobotClass, RobotState

log = logging.getLogger("pmcp.simulator.warehouse")

BASE_PORT = 9100
N_ROBOTS = 50


# ─────────────────────────────────────────────────────────────────────────────
#  Scenario
# ─────────────────────────────────────────────────────────────────────────────

class WarehouseScenario:
    def __init__(
        self,
        n_robots: int = N_ROBOTS,
        registry_url: str = "http://localhost:8888",
        tick_rate_hz: float = 10.0,
    ) -> None:
        self.n_robots = n_robots
        self.registry_url = registry_url
        self.tick_dt = 1.0 / tick_rate_hz
        self.world = WarehouseWorld()
        self.robots: Dict[str, SimRobot] = {}
        self._running = False

    # ── Spawn ────────────────────────────────────────────────────────────────

    async def spawn_robots(self) -> None:
        spawn_xs = [5.0 + (i % 10) * 9.0 for i in range(self.n_robots)]
        spawn_ys = [3.0 + (i // 10) * 3.0 for i in range(self.n_robots)]

        tasks = []
        for i in range(self.n_robots):
            robot_id = f"amr-{i:03d}"
            pos = self.world.nearest_free(Vec2(spawn_xs[i], spawn_ys[i]))
            robot = SimRobot(
                robot_id=robot_id,
                robot_class=RobotClass.AMR,
                position=pos,
                battery_pct=random.uniform(40.0, 100.0),
                max_speed=random.uniform(1.0, 2.0),
            )
            self.robots[robot_id] = robot
            port = BASE_PORT + i
            tasks.append(robot.start_server(port))

        await asyncio.gather(*tasks, return_exceptions=True)
        log.info("Spawned %d robots on ports %d-%d", self.n_robots, BASE_PORT, BASE_PORT + self.n_robots - 1)

    # ── Registry Registration ────────────────────────────────────────────────

    async def register_with_registry(self) -> None:
        async with aiohttp.ClientSession() as session:
            for i, (robot_id, robot) in enumerate(self.robots.items()):
                port = BASE_PORT + i
                payload = {
                    "robot_id": robot_id,
                    "host": "127.0.0.1",
                    "port": port,
                    "robot_class": robot.robot_class.value,
                    "region": "warehouse-sim",
                    "firmware_ver": "0.5.0-sim",
                    "location": f"row-{i // 10}-bay-{i % 10}",
                }
                try:
                    async with session.post(
                        f"{self.registry_url}/api/v1/robots/register",
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        if resp.status == 201:
                            log.debug("Registered %s", robot_id)
                except Exception as exc:
                    log.warning("Registry unavailable: %s", exc)
                    break

    # ── Physics Loop ─────────────────────────────────────────────────────────

    async def _physics_loop(self) -> None:
        while self._running:
            for robot in self.robots.values():
                robot.tick(self.tick_dt)
            await asyncio.sleep(self.tick_dt)

    # ── Mission Dispatch ─────────────────────────────────────────────────────

    async def _mission_loop(self) -> None:
        pickups = self.world.get_waypoints_by_type("pickup")
        dropoffs = self.world.get_waypoints_by_type("dropoff")
        robot_list = list(self.robots.values())

        while self._running:
            # Assign idle robots a random pick-and-place mission
            idle_robots = [r for r in robot_list if r.state == RobotState.IDLE and not r.estop_active]
            for robot in idle_robots[:5]:  # batch 5 at a time
                if not pickups or not dropoffs:
                    break
                pickup = random.choice(pickups)
                dropoff = random.choice(dropoffs)
                robot.move_to(pickup.position.x, pickup.position.y)
                robot.current_mission = f"pick@{pickup.id}->drop@{dropoff.id}"

            # Send robots with low battery to charge
            low_battery = [r for r in robot_list if r.battery_pct < 15.0 and r.state == RobotState.IDLE]
            for robot in low_battery:
                chargers = self.world.get_waypoints_by_type("charging")
                if chargers:
                    charger = min(chargers, key=lambda c: c.position.distance(robot.position))
                    robot.move_to(charger.position.x, charger.position.y)

            await asyncio.sleep(2.0)

    # ── Heartbeat ────────────────────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        async with aiohttp.ClientSession() as session:
            while self._running:
                for robot_id in self.robots:
                    try:
                        async with session.post(
                            f"{self.registry_url}/api/v1/robots/{robot_id}/heartbeat",
                            timeout=aiohttp.ClientTimeout(total=2),
                        ) as _:
                            pass
                    except Exception:
                        pass
                await asyncio.sleep(30.0)

    # ── Run ─────────────────────────────────────────────────────────────────

    async def run(self) -> None:
        self._running = True
        await self.spawn_robots()
        await self.register_with_registry()

        await asyncio.gather(
            self._physics_loop(),
            self._mission_loop(),
            self._heartbeat_loop(),
        )

    def stop(self) -> None:
        self._running = False


# ─────────────────────────────────────────────────────────────────────────────
#  Entry Point
# ─────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s — %(message)s")

    parser = argparse.ArgumentParser(description="P-MCP Warehouse Simulator")
    parser.add_argument("--robots", type=int, default=50)
    parser.add_argument("--registry", default="http://localhost:8888")
    args = parser.parse_args()

    scenario = WarehouseScenario(n_robots=args.robots, registry_url=args.registry)
    try:
        await scenario.run()
    except KeyboardInterrupt:
        scenario.stop()
        log.info("Simulator stopped")


if __name__ == "__main__":
    asyncio.run(main())
