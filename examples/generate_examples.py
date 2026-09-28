"""
P-MCP Examples — Three focused demos using pmcp_grand_unified.py

Run any example:
    python examples/example_harvest_cycle.py
    python examples/example_safety_blocks.py
    python examples/example_multi_robot.py
"""

# ─────────────────────────────────────────────────────────────────────────────
# example_harvest_cycle.py — Minimal end-to-end harvest
# ─────────────────────────────────────────────────────────────────────────────

HARVEST_EXAMPLE = '''#!/usr/bin/env python3
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

    print(f"\\nHarvest result: {result}")
    orch.agent._log  # full plan log

asyncio.run(main())
'''

# ─────────────────────────────────────────────────────────────────────────────
# example_safety_blocks.py — TEE vetoes + vision blocking
# ─────────────────────────────────────────────────────────────────────────────

SAFETY_EXAMPLE = '''#!/usr/bin/env python3
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
    print("\\n=== Test 1: Normal move (should succeed) ===")
    r1 = await agent.call_tool("move_to", x=0.8, y=0.6, z=0.5, speed=0.3)
    print(f"Result: {\'OK\' if r1.success else \'BLOCKED\'} — {r1.message}")

    # Test 2: Human detected — shadow blocks
    print("\\n=== Test 2: Human in zone (should block) ===")
    twin.set_human_present("grow_zone_a", True)
    r2 = await agent.call_tool("move_to", x=1.0, y=1.0, z=0.4, speed=0.5)
    print(f"Result: {\'OK\' if r2.success else \'BLOCKED\'} — {r2.message}")
    twin.set_human_present("grow_zone_a", False)

    # Test 3: Vision detects foot — safe_to_execute→False even if DB clear
    print("\\n=== Test 3: Camera sees foot near robot (should block) ===")
    vsim.simulate_human_approaching(x=0.9, y=0.65, z=0.9)
    await asyncio.sleep(0.05)
    c = await server.invoke("move_to", caller_id="test", x=0.8, y=0.6, z=0.5, speed=0.2)
    print(f"Shadow: safe={c.projected.safe_to_execute} voxels={len(twin.get_vision_voxels())}")
    for r in c.projected.risk_reasons: print(f"  Risk: {r}")
    await server.abort(c.call_id, "demo")
    twin._vision_voxels.clear()

    # Test 4: Speed violation — TEE CONST-01 veto
    print("\\n=== Test 4: Speed=2.5m/s — TEE Constitution veto ===")
    tok, violations = tee.request_hardware_token(
        "t-fast", "arm-01", "move_to",
        {"x": 1.0, "y": 1.0, "z": 0.5, "speed": 2.5},
        energy_j=120.0, shadow_ts=__import__("time").time() - 0.1
    )
    print(f"TEE: {violations[0] if violations else \'PASSED\'}")

    # Test 5: Replay attack blocked
    print("\\n=== Test 5: Token replay attack (should be blocked) ===")
    c5 = await server.invoke("move_to", caller_id="test", x=0.5, y=0.5, z=0.4, speed=0.2)
    if c5.projected and c5.projected.safe_to_execute:
        tok5, _ = tee.request_hardware_token(c5.call_id, "arm-01", "move_to",
            c5.arguments, c5.projected.energy_j, __import__("time").time() - 0.05)
        if tok5:
            ok1, m1 = tee.verify_and_consume_token(tok5)
            ok2, m2 = tee.verify_and_consume_token(tok5)  # replay
            print(f"First use: {\'OK\' if ok1 else \'BLOCKED\'}")
            print(f"Replay:    {\'OK\' if ok2 else \'BLOCKED — \' + m2}")
    await server.abort(c5.call_id, "demo")

    print(f"\\nTEE stats: {tee.stats()}")

asyncio.run(main())
'''

# ─────────────────────────────────────────────────────────────────────────────
# example_multi_robot.py — Multi-robot auction + CRDT ledger
# ─────────────────────────────────────────────────────────────────────────────

MULTI_ROBOT_EXAMPLE = '''#!/usr/bin/env python3
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
    print("\\n=== Vickrey Auction — two arms compete for zone ===")
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
            print(f"Result: {\'OK\' if result.success else \'FAIL\'}  {result.message}")

    # ── Test 2: CRDT decentralized ledger ─────────────────────────────
    print("\\n=== Decentralized Ledger — robots self-schedule ===")
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
        print(f"  [{rid}]→[{zone}] bid={bid}J: {\'✅\' if ok else \'⏳\'} {status}")
        if ok and entry: claims.append((rid, entry.entry_id))

    # Show zone schedule — all peers see the same view (CRDT convergence)
    sched = dfc.zone_schedule("grow_zone_a")
    print(f"\\n  grow_zone_a schedule ({len(sched)} entries):")
    for s in sched:
        print(f"    [{s[\'robot_id\']}] {s[\'start_ms\']:.0f}→{s[\'end_ms\']:.0f}ms bid={s[\'energy_bid_j\']}J")

    # ── Test 3: DePIN — robots refuse unprofitable tasks ──────────────
    print("\\n=== DePIN Machine Economy — robots evaluate utility ===")
    from pmcp_grand_unified import ProjectedOutcome
    arm_did  = RobotDID("arm-01")
    arm_w    = RoboWallet("arm-01", arm_did, initial_balance_usd=0.50)
    fleet    = DePINFleetManager(dfc)
    fleet.register_robot_wallet(arm_w)

    # High-value task — robot accepts
    task_id = fleet.post_task("harvest_basil", 0.12, "grow_zone_a", 5000)
    dummy_p = ProjectedOutcome(3.0, 120.0, None, "low", [], utility_score=0.9)
    accept, tx = arm_w.evaluate_task("harvest_basil", dummy_p, 0.12, energy_price_usd_kwh=0.08)
    print(f"  High-value task ($0.12): {\'ACCEPT\' if accept else \'REFUSE\'} U=${tx.utility:.6f}")

    # Low-value drudge task — robot refuses
    dummy_p2 = ProjectedOutcome(1.0, 50.0, None, "low", [], utility_score=0.1)
    accept2, tx2 = arm_w.evaluate_task("recalibrate_sensor", dummy_p2, 0.0001, energy_price_usd_kwh=0.15)
    print(f"  Low-value task ($0.0001): {\'ACCEPT\' if accept2 else \'REFUSE\'} U=${tx2.utility:.6f}")
    if not accept2:
        print(f"  → Robot re-auctioning task to fleet for higher-value node")

    print(f"\\nServer stats: {server.stats()[\'calls_by_status\']}")

asyncio.run(main())
'''

# ─────────────────────────────────────────────────────────────────────────────
# example_black_swan.py — Black Swan fault injection + ZK safety halt
# ─────────────────────────────────────────────────────────────────────────────

BLACK_SWAN_EXAMPLE = '''#!/usr/bin/env python3
"""
P-MCP Black Swan Simulation
============================
"What happens when the vision server dies mid-harvest?"

This script demonstrates the full resilience stack:

  1. A harvest cycle starts normally (UR5 picks crops from row A)
  2. VISION_SERVER_FAIL is injected mid-cycle (camera goes offline)
  3. The HIL gate detects the fault and blocks further arm moves
  4. The ZK safety layer records a violation proof
  5. SoftSlashManager applies a MINOR penalty to the arm
  6. The fault is cleared (vision server restarts)
  7. The harvest cycle resumes and completes successfully

Run from the repo root:
    python examples/example_black_swan.py
"""

import asyncio, sys, logging, time
sys.path.insert(0, "..")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
)
log = logging.getLogger("black_swan_demo")

# ── Core imports ──────────────────────────────────────────────────────────
from pmcp_grand_unified import DigitalTwin, PMCPServer, Pose, Vec3

# ── v0.4 imports ─────────────────────────────────────────────────────────
try:
    from v04.pmcp_hil_v4 import HILGate, HILMode, FaultInjector, FaultType, FaultSeverity
    _HIL = True
except ImportError:
    log.warning("v04.pmcp_hil_v4 not found — using stub")
    _HIL = False

try:
    from v04.pmcp_soft_slash import SoftSlashManager, SlashReason, SlashSeverity
    _SLASH = True
except ImportError:
    log.warning("v04.pmcp_soft_slash not found — slash checks skipped")
    _SLASH = False

try:
    from v04.pmcp_zk_safety import ZKSafetyProver, ZKSafetyVerifier
    _ZK = True
except ImportError:
    log.warning("v04.pmcp_zk_safety not found — ZK proofs skipped")
    _ZK = False


# ─────────────────────────────────────────────────────────────────────────
# SIMULATION HELPERS
# ─────────────────────────────────────────────────────────────────────────

class HarvestStep:
    """One step in the harvest cycle."""
    def __init__(self, name: str, tool: str, args: dict):
        self.name  = name
        self.tool  = tool
        self.args  = args
        self.done  = False
        self.safe  = None
        self.risk  = []


def build_harvest_plan():
    """Return a simple 6-step harvest cycle for row A (coords within 0.85m envelope)."""
    return [
        HarvestStep("approach_row_A",   "move_to",  dict(x=0.5, y=0.4, z=0.5, speed=0.3)),
        HarvestStep("lower_to_canopy",  "move_to",  dict(x=0.5, y=0.4, z=0.2, speed=0.2)),
        HarvestStep("pick_tomato_1",    "pick",     dict(x=0.5, y=0.4, z=0.15, speed=0.15)),
        HarvestStep("retract_arm",      "move_to",  dict(x=0.5, y=0.4, z=0.5, speed=0.3)),
        HarvestStep("drop_in_bin",      "place",    dict(x=0.3, y=0.2, z=0.3, speed=0.2)),
        HarvestStep("return_home",      "move_to",  dict(x=0.1, y=0.0, z=0.6, speed=0.4)),
    ]


async def run_harvest_with_fault(hil_gate, slash_mgr, fault_at_step: int = 2):
    """
    Execute the harvest plan.  At step `fault_at_step`, inject VISION_SERVER_FAIL.

    Returns a detailed report dict.
    """
    plan       = build_harvest_plan()
    report     = {"steps": [], "halted_at": None, "recovered": False, "slash_record": None}
    fault_obj  = None
    fault_cleared = False

    log.info("\\n" + "=" * 60)
    log.info("  HARVEST CYCLE STARTING  (6 steps)")
    log.info("=" * 60)

    for i, step in enumerate(plan):
        # ── Fault injection at designated step ────────────────────────
        if i == fault_at_step and fault_obj is None:
            log.warning(f"\\n  ☠️  [STEP {i}] INJECTING BLACK SWAN: VISION_SERVER_FAIL")
            fault_obj = hil_gate._fault_injector.inject(
                FaultType.VISION_SERVER_FAIL,
                target_id  = "vision-server",
                severity   = FaultSeverity.HARD,
                duration_s = 0,    # permanent until cleared manually
            )
            log.warning(f"       fault_id={fault_obj.fault_id}  "
                        f"target=vision-server  severity=HARD")

        # ── HIL simulation gate ────────────────────────────────────────
        sim = hil_gate.simulate_before_execute(
            {"tool_name": step.tool, "arguments": step.args},
            "ur5-01"
        )
        step.safe = sim.safe
        step.risk = sim.risk_reasons

        entry = {
            "step_n":    i,
            "name":      step.name,
            "tool":      step.tool,
            "safe":      sim.safe,
            "risks":     sim.risk_reasons,
            "energy_j":  sim.energy_j,
        }

        if sim.safe:
            log.info(f"  ✅  [{i}] {step.name:<25s} "
                     f"dur={sim.duration_s:.2f}s  E={sim.energy_j:.1f}J")
            step.done  = True
            entry["executed"] = True
            await asyncio.sleep(0.02)   # simulate execution time
        else:
            log.error(f"  🚨  [{i}] {step.name:<25s} BLOCKED — {sim.risk_reasons}")
            report["halted_at"] = i
            entry["executed"]   = False

            # ── ZK violation evidence ─────────────────────────────────
            # Full ZK proof requires a projected outcome (from shadow validation).
            # For the blocked fault case we record a lightweight violation
            # fingerprint using HMAC over the fault evidence instead.
            if _ZK:
                import hmac as _hmac, hashlib as _hl, secrets as _sec
                _nonce   = _sec.token_hex(8)
                _key     = b"zk-fault-demo-key"
                _payload = f"ur5-01|VISION-FAULT-HALT|vision_server_online=0|{_nonce}"
                _commit  = _hmac.new(_key, _payload.encode(), _hl.sha256).hexdigest()
                entry["zk_fault_commitment"] = _commit[:24] + "..."
                log.info(f"     ZK fault commitment: {entry[\'zk_fault_commitment\']}")

            # ── Soft slash ─────────────────────────────────────────────
            if _SLASH and slash_mgr:
                try:
                    import asyncio as _aio, types as _t
                    # Build a minimal proof object matching the SoftSlashManager API
                    fake_proof = _t.SimpleNamespace(
                        robot_id    = "ur5-01",
                        commitment  = "fault-evidence-no-zk-proof",
                        call_id     = "bs-demo",
                        nonce       = "00000000",
                    )
                    violations = ["VISION_FAULT_HALT: vision_server OFFLINE during pick"]
                    slash_rec = _aio.get_event_loop().run_until_complete(
                        slash_mgr.process_violation(fake_proof, violations)
                    ) if not asyncio.iscoroutinefunction(slash_mgr.process_violation) \
                      else None
                    # process_violation is a coroutine — await it
                    slash_rec = await slash_mgr.process_violation(fake_proof, violations)
                    if slash_rec:
                        report["slash_record"] = {
                            "record_id": slash_rec.record_id,
                            "severity":  slash_rec.severity.value,
                            "stake_pct": slash_rec.stake_penalty_pct,
                            "rep_delta": slash_rec.reputation_delta,
                            "reason":    slash_rec.reason.value,
                        }
                        log.warning(f"     SLASH APPLIED: {slash_rec.severity.value} "
                                    f"stake_penalty={slash_rec.stake_penalty_pct:.0f}%  "
                                    f"rep_delta={slash_rec.reputation_delta}")
                    else:
                        log.info("     Slash: no record generated (proof below threshold)")
                except Exception as e:
                    log.debug(f"Slash error (non-fatal): {e}")

            report["steps"].append(entry)
            # System halts — do not execute remaining steps with fault active
            log.warning("  ⛔  HARVEST HALTED — waiting for fault recovery...")
            break

        report["steps"].append(entry)

    # ── Fault recovery ─────────────────────────────────────────────────
    if fault_obj is not None and report["halted_at"] is not None:
        log.info("\\n  🔧  Vision server restarting...")
        await asyncio.sleep(0.1)
        hil_gate._fault_injector.clear(fault_obj.fault_id)
        log.info("  🔧  Vision server RESTORED — retrying halted steps")

        # Retry the steps that were blocked
        for step in plan[report["halted_at"]:]:
            sim = hil_gate.simulate_before_execute(
                {"tool_name": step.tool, "arguments": step.args},
                "ur5-01"
            )
            entry = {
                "step_n":    plan.index(step),
                "name":      step.name + " [RETRY]",
                "tool":      step.tool,
                "safe":      sim.safe,
                "risks":     sim.risk_reasons,
                "energy_j":  sim.energy_j,
            }
            if sim.safe:
                log.info(f"  ✅  [RETRY] {step.name:<25s} OK  E={sim.energy_j:.1f}J")
                step.done       = True
                entry["executed"] = True
                await asyncio.sleep(0.02)
            else:
                log.error(f"  ❌  [RETRY] {step.name} still blocked: {sim.risk_reasons}")
                entry["executed"] = False
            report["steps"].append(entry)

        all_done = all(s.done for s in plan)
        report["recovered"] = all_done
        if all_done:
            log.info("\\n  ✅  HARVEST CYCLE COMPLETED AFTER RECOVERY")

    return report


# ─────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────

async def main():
    print("\\n" + "=" * 60)
    print("  P-MCP Black Swan Simulation")
    print("  Vision Server Failure During Active Harvest Cycle")
    print("=" * 60)

    # ── Infrastructure ────────────────────────────────────────────────
    twin   = DigitalTwin(power_budget_w=4000.0)
    server = PMCPServer("black-swan-demo", twin, confirm_timeout_ms=5000)

    # ── HIL Gate ──────────────────────────────────────────────────────
    if _HIL:
        hil_gate = HILGate(server=server, default_mode=HILMode.VIRTUAL)
        hil_gate.register_driver("ur5-01", None, "arm")
    else:
        # Minimal stub
        class _StubGate:
            class _FI:
                def inject(self, *a, **kw):
                    return type("F", (), {"fault_id": "stub"})()
                def clear(self, fid): pass
            _fault_injector = _FI()
            def simulate_before_execute(self, call, driver_id=""):
                from dataclasses import dataclass
                @dataclass
                class R:
                    safe=True; duration_s=0.5; energy_j=50.0
                    risk_reasons=[]
                    def to_projected_outcome_dict(self): return {}
                return R()
        hil_gate = _StubGate()

    # ── Soft-Slash Manager ────────────────────────────────────────────
    slash_mgr = SoftSlashManager() if _SLASH else None

    # ── Run the Black Swan scenario ───────────────────────────────────
    report = await run_harvest_with_fault(
        hil_gate,
        slash_mgr,
        fault_at_step=2,   # inject fault at step 2 (pick_tomato_1)
    )

    # ── Summary ───────────────────────────────────────────────────────
    import json
    print("\\n" + "=" * 60)
    print("  BLACK SWAN SIMULATION REPORT")
    print("=" * 60)
    print(f"  Steps completed:   {sum(1 for s in report['steps'] if s.get('executed'))}")
    print(f"  System halted at:  step {report.get('halted_at', 'N/A')}")
    print(f"  Recovered:         {report['recovered']}")
    if report.get("slash_record"):
        sr = report["slash_record"]
        print(f"  Slash applied:     {sr[\'severity\']} severity  "
              f"stake_penalty={sr[\'stake_pct\']:.0f}%  rep_delta={sr[\'rep_delta\']}")
    elif _SLASH:
        print("  Slash applied:     first-time violation (below threshold) — warning logged")
    else:
        print("  Slash applied:     (soft-slash module not available)")

    print("\\n  Step timeline:")
    for s in report["steps"]:
        status = "✅" if s.get("executed") else "🚨"
        print(f"    {status} [{s['step_n']}] {s['name']:<35s}  E={s['energy_j']:.1f}J")
        for r in s.get("risks", []):
            print(f"         ⚠️  {r}")

    print("\\n  Conclusion:")
    if report["recovered"]:
        print("  ✅ System correctly detected the Black Swan fault,")
        print("     halted the arm, applied governance penalties,")
        print("     and successfully resumed after recovery.")
    else:
        print("  ⚠️  Recovery incomplete — check fault injection parameters.")


asyncio.run(main())
'''

import os

examples = {
    "example_harvest_cycle.py": HARVEST_EXAMPLE,
    "example_safety_blocks.py": SAFETY_EXAMPLE,
    "example_multi_robot.py": MULTI_ROBOT_EXAMPLE,
    "example_black_swan.py": BLACK_SWAN_EXAMPLE,
}

if __name__ == "__main__":
    out_dir = os.path.dirname(os.path.abspath(__file__))
    for fname, content in examples.items():
        path = os.path.join(out_dir, fname)
        with open(path, "w") as f:
            f.write(content.lstrip())
        print(f"Written: {path}")
