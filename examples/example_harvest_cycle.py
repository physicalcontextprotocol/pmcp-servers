#!/usr/bin/env python3
"""Minimal P-MCP harvest cycle — shows the full invoke→shadow→confirm loop."""

import asyncio, sys, logging
sys.path.insert(0, "..")  # or wherever pmcp_grand_unified.py lives

from pmcp_grand_unified import (
    DigitalTwin, PMCPServer, Pose, Vec3,
    RoboticArmAgent, AMRAgent, HarvestOrchestrator,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")

async def main():
    # 1. Create twin + server
    twin   = DigitalTwin(power_budget_w=4000.0)
    server = PMCPServer("farm-demo", twin, confirm_timeout_ms=5000)

    # 2. Register robots
    arm = RoboticArmAgent(server, "arm-01", Pose(position=Vec3(1.5, 1.0, 0.5)))
    amr = AMRAgent(server, "amr-01", Pose(position=Vec3(4.5, 0.5, 0.0)))

    # 3. Run one harvest cycle
    orch = HarvestOrchestrator(server, arm_id="arm-01", amr_id="amr-01")
    result = await orch.run_harvest_cycle(tray_x=1.0, tray_y=0.8)

    print(f"\nHarvest result: {result}")
    orch.agent._log  # full plan log

asyncio.run(main())
