#!/usr/bin/env python3
"""Demonstrates the v0.3/v0.4 safety layers: TEE, vision, human clearance."""

import asyncio, sys, logging
sys.path.insert(0, "..")

from pmcp_grand_unified import (
    DigitalTwin, PMCPServer, Pose, Vec3,
    RoboticArmAgent, TEESimulator, TEECommandGate, RobotDID,
    VisionVoxelBridge, VisionSimulator, VisionDetection,
    PlanningAgent,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")

async def main():
    twin   = DigitalTwin(power_budget_w=4000.0)
    server = PMCPServer("safety-demo", twin, confirm_timeout_ms=5000)
    arm    = RoboticArmAgent(server, "arm-01", Pose(position=Vec3(1.5, 1.0, 0.5)))

    # ── TEE setup ────────────────────────────────────────────────────────
    tee     = TEESimulator()
    arm_did = RobotDID("arm-01")
    gate    = TEECommandGate(server, tee, {"arm-01": arm_did})

    # ── Vision setup ─────────────────────────────────────────────────────
    bridge = VisionVoxelBridge(twin, voxel_ttl_s=0.5)
    vsim   = VisionSimulator(bridge)

    agent  = PlanningAgent(server, "safety-agent")

    # Test 1: Normal move — should succeed
    print("\n=== Test 1: Normal move (should succeed) ===")
    r1 = await agent.call_tool("move_to", x=0.8, y=0.6, z=0.5, speed=0.3)
    print(f"Result: {'OK' if r1.success else 'BLOCKED'} — {r1.message}")

    # Test 2: Human detected — shadow blocks
    print("\n=== Test 2: Human in zone (should block) ===")
    twin.set_human_present("grow_zone_a", True)
    r2 = await agent.call_tool("move_to", x=1.0, y=1.0, z=0.4, speed=0.5)
    print(f"Result: {'OK' if r2.success else 'BLOCKED'} — {r2.message}")
    twin.set_human_present("grow_zone_a", False)

    # Test 3: Vision detects foot — safe_to_execute→False even if DB clear
    print("\n=== Test 3: Camera sees foot near robot (should block) ===")
    vsim.simulate_human_approaching(x=0.9, y=0.65, z=0.9)
    await asyncio.sleep(0.05)
    c = await server.invoke("move_to", caller_id="test", x=0.8, y=0.6, z=0.5, speed=0.2)
    print(f"Shadow: safe={c.projected.safe_to_execute} voxels={len(twin.get_vision_voxels())}")
    for r in c.projected.risk_reasons: print(f"  Risk: {r}")
    await server.abort(c.call_id, "demo")
    twin._vision_voxels.clear()

    # Test 4: Speed violation — TEE CONST-01 veto
    print("\n=== Test 4: Speed=2.5m/s — TEE Constitution veto ===")
    tok, violations = tee.request_hardware_token(
        "t-fast", "arm-01", "move_to",
        {"x": 1.0, "y": 1.0, "z": 0.5, "speed": 2.5},
        energy_j=120.0, shadow_ts=__import__("time").time() - 0.1
    )
    print(f"TEE: {violations[0] if violations else 'PASSED'}")

    # Test 5: Replay attack blocked
    print("\n=== Test 5: Token replay attack (should be blocked) ===")
    c5 = await server.invoke("move_to", caller_id="test", x=0.5, y=0.5, z=0.4, speed=0.2)
    if c5.projected and c5.projected.safe_to_execute:
        tok5, _ = tee.request_hardware_token(c5.call_id, "arm-01", "move_to",
            c5.arguments, c5.projected.energy_j, __import__("time").time() - 0.05)
        if tok5:
            ok1, m1 = tee.verify_and_consume_token(tok5)
            ok2, m2 = tee.verify_and_consume_token(tok5)  # replay
            print(f"First use: {'OK' if ok1 else 'BLOCKED'}")
            print(f"Replay:    {'OK' if ok2 else 'BLOCKED — ' + m2}")
    await server.abort(c5.call_id, "demo")

    print(f"\nTEE stats: {tee.stats()}")

asyncio.run(main())
