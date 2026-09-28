"""
P-MCP v0.5 — Agricultural Robot Server
=======================================
MCP-compatible server for precision agriculture robots.

Use cases: crop inspection, irrigation control, harvest robots,
           greenhouse climate management, drone sprayers.

Run:
    python agricultural_server.py
    python agricultural_server.py --transport http --port 8082
"""
from __future__ import annotations

import asyncio
import logging
import random
import sys
import time
from typing import Dict, List

import os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from v05.pmcp_v5_server import PMCPServer
from v05.pmcp_v5_types  import (
    ActuationResult, MissionResult, SensorReading, SensorType,
)

log = logging.getLogger("pmcp.agri_server")


# ─────────────────────────────────────────────────────────────────────────────
#  SIMULATED GREENHOUSE STATE
# ─────────────────────────────────────────────────────────────────────────────

class GreenhouseState:
    def __init__(self):
        self.temperature_c = 22.5
        self.humidity_pct  = 68.0
        self.co2_ppm       = 450.0
        self.light_umol    = 350.0   # photosynthetic photon flux density
        self.soil_moisture: Dict[str, float] = {
            "zone_A": 0.42, "zone_B": 0.38, "zone_C": 0.45, "zone_D": 0.31,
        }
        self.plant_heights: Dict[str, float] = {
            "row_1": 0.32, "row_2": 0.28, "row_3": 0.41, "row_4": 0.25,
        }
        self.irrigation_on: Dict[str, bool] = {k: False for k in self.soil_moisture}
        self.harvest_queue: List[str] = []

    def irrigate(self, zone: str, duration_s: float) -> dict:
        if zone not in self.soil_moisture:
            return {"error": f"Unknown zone: {zone}"}
        old = self.soil_moisture[zone]
        self.soil_moisture[zone] = min(0.85, old + duration_s * 0.002)
        return {
            "zone": zone, "duration_s": duration_s,
            "moisture_before": round(old, 3),
            "moisture_after":  round(self.soil_moisture[zone], 3),
        }

    def harvest(self, row: str, speed: float) -> dict:
        if row not in self.plant_heights:
            return {"error": f"Unknown row: {row}"}
        height = self.plant_heights[row]
        harvested = height > 0.30
        if harvested:
            self.plant_heights[row] = 0.05   # stubble remaining
            self.harvest_queue.append(row)
        return {
            "row": row, "height_m": round(height, 3),
            "harvested": harvested,
            "reason": "ready" if harvested else "plant too short (< 0.30m)",
        }

    def adjust_climate(self, target_temp: float, target_humidity: float) -> dict:
        old_t = self.temperature_c; old_h = self.humidity_pct
        self.temperature_c += (target_temp - self.temperature_c) * 0.3
        self.humidity_pct  += (target_humidity - self.humidity_pct) * 0.3
        return {
            "temperature_c": round(self.temperature_c, 2),
            "humidity_pct":  round(self.humidity_pct, 2),
            "delta_t": round(self.temperature_c - old_t, 3),
            "delta_h": round(self.humidity_pct  - old_h, 3),
        }

    def apply_pesticide(self, zone: str, concentration_pct: float) -> dict:
        if concentration_pct > 2.0:
            return {"error": "Concentration > 2% is unsafe for organic certification"}
        return {
            "zone": zone, "concentration_pct": concentration_pct,
            "applied": True, "volume_ml": round(100 * concentration_pct, 1),
        }


# ─────────────────────────────────────────────────────────────────────────────
#  BUILD SERVER
# ─────────────────────────────────────────────────────────────────────────────

def build_agricultural_server(name: str = "agri-bot-01",
                               model: str = "HarvestBot-X",
                               location: str = "greenhouse-1") -> PMCPServer:

    server = PMCPServer(
        name=name, version="1.0.0",
        robot_class="arm", model=model,
        serial="2024-AG01", location=location,
    )
    gh = GreenhouseState()

    # ── Actuations ────────────────────────────────────────────────────────────

    @server.actuation(
        "irrigate_zone",
        description=(
            "Activate irrigation for a greenhouse zone. "
            "Zones: zone_A, zone_B, zone_C, zone_D. "
            "duration_s: how long to run irrigation (max 300s)."
        ),
        category="utility", max_energy_j=50.0,
        requires_lease=True, shadow_required=False,
    )
    async def irrigate_zone(zone: str, duration_s: float = 30.0):
        if duration_s > 300:
            return ActuationResult(success=False, robot_id=server.robot_id,
                                    error_message="duration_s max is 300 seconds")
        result = gh.irrigate(zone, duration_s)
        if "error" in result:
            return ActuationResult(success=False, robot_id=server.robot_id,
                                    error_message=result["error"])
        return ActuationResult(success=True, robot_id=server.robot_id, output=result)

    @server.actuation(
        "harvest_row",
        description=(
            "Harvest a crop row with the harvesting arm. "
            "Rows: row_1, row_2, row_3, row_4. "
            "Only harvests if plant height > 30cm."
        ),
        category="manipulation", max_speed_m_s=0.2, max_energy_j=800.0,
        est_duration_s=60.0, requires_lease=True,
    )
    async def harvest_row(row: str, speed: float = 0.1):
        result = gh.harvest(row, speed)
        if "error" in result:
            return ActuationResult(success=False, robot_id=server.robot_id,
                                    error_message=result["error"])
        return ActuationResult(success=True, robot_id=server.robot_id, output=result)

    @server.actuation(
        "adjust_climate",
        description=(
            "Adjust greenhouse temperature and humidity to target values. "
            "Safe range: temp 15-35°C, humidity 40-90%."
        ),
        category="utility", max_energy_j=500.0,
        requires_lease=False, shadow_required=False,
    )
    async def adjust_climate(target_temp: float = 22.0, target_humidity: float = 70.0):
        if not (15 <= target_temp <= 35):
            return ActuationResult(success=False, robot_id=server.robot_id,
                                    error_message=f"target_temp {target_temp}°C out of range 15-35°C")
        if not (40 <= target_humidity <= 90):
            return ActuationResult(success=False, robot_id=server.robot_id,
                                    error_message=f"target_humidity {target_humidity}% out of range 40-90%")
        result = gh.adjust_climate(target_temp, target_humidity)
        return ActuationResult(success=True, robot_id=server.robot_id, output=result)

    @server.actuation(
        "apply_pesticide",
        description=(
            "Apply pesticide to a zone with specified concentration. "
            "Max safe concentration for organic certification: 2%."
        ),
        category="manipulation", max_energy_j=100.0,
        requires_lease=True, shadow_required=False,
    )
    async def apply_pesticide(zone: str, concentration_pct: float = 0.5):
        result = gh.apply_pesticide(zone, concentration_pct)
        if "error" in result:
            return ActuationResult(success=False, robot_id=server.robot_id,
                                    error_message=result["error"])
        return ActuationResult(success=True, robot_id=server.robot_id, output=result)

    @server.actuation(
        "inspect_plants",
        description=(
            "Visually inspect a row for disease, pests, or growth anomalies. "
            "Returns health score (0.0-1.0) and detected issues."
        ),
        category="sensing", max_speed_m_s=0.1, max_energy_j=50.0,
        est_duration_s=30.0, requires_lease=True,
    )
    async def inspect_plants(row: str):
        if row not in gh.plant_heights:
            return ActuationResult(success=False, robot_id=server.robot_id,
                                    error_message=f"Unknown row: {row}")
        health = random.uniform(0.75, 0.98)
        issues = []
        if random.random() < 0.1: issues.append("mild_aphid_presence")
        if random.random() < 0.05: issues.append("powdery_mildew_spot")
        return ActuationResult(
            success=True, robot_id=server.robot_id,
            output={
                "row": row,
                "health_score": round(health, 3),
                "plant_height_m": round(gh.plant_heights[row], 3),
                "issues": issues,
                "recommendation": "irrigate" if gh.soil_moisture.get(row.replace("row_", "zone_"), 0.5) < 0.35 else "ok",
            },
        )

    # ── Sensors ───────────────────────────────────────────────────────────────

    @server.sensor("climate", description="Greenhouse temperature, humidity, CO2, PPFD",
                   sensor_type=SensorType.TEMPERATURE, unit="°C/%/ppm/µmol", hz=0.1)
    async def climate():
        return SensorReading(
            sensor_name="climate", robot_id=server.robot_id,
            value={
                "temperature_c": round(gh.temperature_c + random.gauss(0, 0.05), 2),
                "humidity_pct":  round(gh.humidity_pct  + random.gauss(0, 0.1), 2),
                "co2_ppm":       round(gh.co2_ppm       + random.gauss(0, 2.0), 1),
                "ppfd_umol":     round(gh.light_umol    + random.gauss(0, 5.0), 1),
            },
        )

    @server.sensor("soil_moisture", description="Volumetric water content per irrigation zone",
                   sensor_type=SensorType.CUSTOM, unit="m³/m³", hz=0.05)
    async def soil_moisture():
        return SensorReading(
            sensor_name="soil_moisture", robot_id=server.robot_id,
            value={z: round(v + random.gauss(0, 0.002), 4)
                    for z, v in gh.soil_moisture.items()},
            unit="m³/m³",
        )

    @server.sensor("plant_health", description="Per-row plant health assessment",
                   sensor_type=SensorType.PLANT_HEALTH, unit="score", hz=0.016)
    async def plant_health():
        return SensorReading(
            sensor_name="plant_health", robot_id=server.robot_id,
            value={
                "rows": {row: {"height_m": round(h, 3),
                                "health_score": round(random.uniform(0.75, 0.98), 3),
                                "ready_to_harvest": h > 0.30}
                          for row, h in gh.plant_heights.items()},
                "harvest_queue": gh.harvest_queue,
            },
        )

    # ── Mission: Full harvest cycle ───────────────────────────────────────────

    @server.mission("harvest_cycle",
                    description="Full automated harvest cycle: inspect → irrigate → harvest → report",
                    robot_class="arm")
    async def harvest_cycle_mission(rows: str = "all"):
        target_rows = list(gh.plant_heights.keys()) if rows == "all" else rows.split(",")
        steps = "\n".join([
            f"1. inspect_plants('{r}') for each row",
            "2. For dry rows: irrigate_zone(zone, 30s)",
            "3. For ready rows (height > 30cm): harvest_row(row)",
            "4. adjust_climate(22, 70) post-harvest",
            "5. Report harvest summary",
        ])
        return MissionResult(
            mission_name="harvest_cycle",
            messages=[
                {"role": "user", "content": [{"type": "text", "text":
                    f"Execute harvest cycle for rows: {rows}"}]},
                {"role": "assistant", "content": [{"type": "text", "text":
                    f"Executing full harvest cycle for rows: {target_rows}\n\n{steps}"}]},
            ],
        )

    return server


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="P-MCP Agricultural Robot Server v0.5")
    parser.add_argument("--transport", default="stdio", choices=["stdio", "http"])
    parser.add_argument("--port", type=int, default=8082)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s",
                        stream=sys.stderr)
    server = build_agricultural_server()
    asyncio.run(server.run(args.transport, host=args.host, port=args.port))
