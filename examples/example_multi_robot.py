#!/usr/bin/env python3
"""Multi-robot coordination: Vickrey auction + decentralized ledger."""

import asyncio, sys, logging
sys.path.insert(0, "..")

from pmcp_grand_unified import (
    DigitalTwin, PMCPServer, Pose, Vec3,
    RoboticArmAgent, AMRAgent, HarvesterRobot,
    DecentralizedFarmCoordinator, RobotDID, RoboWallet, DePINFleetManager,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")

async def main():
    twin   = DigitalTwin(power_budget_w=8000.0)
    server = PMCPServer("multi-demo", twin, confirm_timeout_ms=6000)

    arm1 = RoboticArmAgent(server, "arm-01", Pose(position=Vec3(1.5, 1.0, 0.5)))
    arm2 = RoboticArmAgent(server, "arm-02", Pose(position=Vec3(2.0, 1.0, 0.5)))
    amr  = AMRAgent(server, "amr-01", Pose(position=Vec3(4.5, 0.5, 0.0)))

    # ── Test 1: Vickrey auction for zone conflict ──────────────────────
    print("\n=== Vickrey Auction — two arms compete for zone ===")
    twin.update_pose("arm-02", Pose(position=Vec3(1.4, 0.9, 0.5)))  # near target

    ca = await server.invoke("move_to", caller_id="arm-01-agent",
                              x=1.5, y=0.9, z=0.4, speed=0.25, bid_energy_j=80.0)
    cb = await server.invoke("move_to", caller_id="arm-02-agent",
                              x=1.5, y=0.9, z=0.4, speed=0.1,  bid_energy_j=30.0)

    if ca.projected.workspace_conflicts:
        zone = ca.projected.workspace_conflicts[0]
        server._auction_pool.setdefault(zone, [])
        server._auction_pool[zone] = [ca, cb]
        winner = await server.run_auction(zone)
        if winner:
            twin.update_pose("arm-02", Pose(position=Vec3(4.0, 2.0, 0.5)))
            result = await server.confirm(winner.call_id)
            print(f"Winner: [{winner.caller_id}] bid={winner.bid_energy_j}J "
                  f"pays={winner.projected.auction_price:.0f}J (Vickrey)")
            print(f"Result: {'OK' if result.success else 'FAIL'}  {result.message}")

    # ── Test 2: CRDT decentralized ledger ─────────────────────────────
    print("\n=== Decentralized Ledger — robots self-schedule ===")
    dfc = DecentralizedFarmCoordinator()
    for rid in ["arm-01", "arm-02", "amr-01"]:
        dfc.add_robot(rid)

    # All three robots claim slots simultaneously — no central coordinator
    claims = []
    for rid, zone, dur, bid in [
        ("arm-01", "grow_zone_a", 3000, 80),
        ("arm-02", "grow_zone_a", 4000, 60),   # competing!
        ("amr-01", "harvest_station", 8000, 40),
    ]:
        ok, entry = dfc.claim(rid, zone, dur, "demo_tool", bid)
        status = f"slot@{entry.start_ms:.0f}ms" if ok and entry else "queued/lost"
        print(f"  [{rid}]→[{zone}] bid={bid}J: {'✅' if ok else '⏳'} {status}")
        if ok and entry: claims.append((rid, entry.entry_id))

    # Show zone schedule — all peers see the same view (CRDT convergence)
    sched = dfc.zone_schedule("grow_zone_a")
    print(f"\n  grow_zone_a schedule ({len(sched)} entries):")
    for s in sched:
        print(f"    [{s['robot_id']}] {s['start_ms']:.0f}→{s['end_ms']:.0f}ms bid={s['energy_bid_j']}J")

    # ── Test 3: DePIN — robots refuse unprofitable tasks ──────────────
    print("\n=== DePIN Machine Economy — robots evaluate utility ===")
    from pmcp_grand_unified import ProjectedOutcome
    arm_did  = RobotDID("arm-01")
    arm_w    = RoboWallet("arm-01", arm_did, initial_balance_usd=0.50)
    fleet    = DePINFleetManager(dfc)
    fleet.register_robot_wallet(arm_w)

    # High-value task — robot accepts
    task_id = fleet.post_task("harvest_basil", 0.12, "grow_zone_a", 5000)
    dummy_p = ProjectedOutcome(3.0, 120.0, None, "low", [], utility_score=0.9)
    accept, tx = arm_w.evaluate_task("harvest_basil", dummy_p, 0.12, energy_price_usd_kwh=0.08)
    print(f"  High-value task ($0.12): {'ACCEPT' if accept else 'REFUSE'} U=${tx.utility:.6f}")

    # Low-value drudge task — robot refuses
    dummy_p2 = ProjectedOutcome(1.0, 50.0, None, "low", [], utility_score=0.1)
    accept2, tx2 = arm_w.evaluate_task("recalibrate_sensor", dummy_p2, 0.0001, energy_price_usd_kwh=0.15)
    print(f"  Low-value task ($0.0001): {'ACCEPT' if accept2 else 'REFUSE'} U=${tx2.utility:.6f}")
    if not accept2:
        print(f"  → Robot re-auctioning task to fleet for higher-value node")

    print(f"\nServer stats: {server.stats()['calls_by_status']}")

asyncio.run(main())
