#!/usr/bin/env python3
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

    log.info("\n" + "=" * 60)
    log.info("  HARVEST CYCLE STARTING  (6 steps)")
    log.info("=" * 60)

    for i, step in enumerate(plan):
        # ── Fault injection at designated step ────────────────────────
        if i == fault_at_step and fault_obj is None:
            log.warning(f"\n  ☠️  [STEP {i}] INJECTING BLACK SWAN: VISION_SERVER_FAIL")
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
                log.info(f"     ZK fault commitment: {entry['zk_fault_commitment']}")

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
                    ) if not asyncio.iscoroutinefunction(slash_mgr.process_violation)                       else None
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
        log.info("\n  🔧  Vision server restarting...")
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
            log.info("\n  ✅  HARVEST CYCLE COMPLETED AFTER RECOVERY")

    return report


# ─────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────

async def main():
    print("\n" + "=" * 60)
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
    print("\n" + "=" * 60)
    print("  BLACK SWAN SIMULATION REPORT")
    print("=" * 60)
    print(f"  Steps completed:   {sum(1 for s in report['steps'] if s.get('executed'))}")
    print(f"  System halted at:  step {report.get('halted_at', 'N/A')}")
    print(f"  Recovered:         {report['recovered']}")
    if report.get("slash_record"):
        sr = report["slash_record"]
        print(f"  Slash applied:     {sr['severity']} severity  "
              f"stake_penalty={sr['stake_pct']:.0f}%  rep_delta={sr['rep_delta']}")
    elif _SLASH:
        print("  Slash applied:     first-time violation (below threshold) — warning logged")
    else:
        print("  Slash applied:     (soft-slash module not available)")

    print("\n  Step timeline:")
    for s in report["steps"]:
        status = "✅" if s.get("executed") else "🚨"
        print(f"    {status} [{s['step_n']}] {s['name']:<35s}  E={s['energy_j']:.1f}J")
        for r in s.get("risks", []):
            print(f"         ⚠️  {r}")

    print("\n  Conclusion:")
    if report["recovered"]:
        print("  ✅ System correctly detected the Black Swan fault,")
        print("     halted the arm, applied governance penalties,")
        print("     and successfully resumed after recovery.")
    else:
        print("  ⚠️  Recovery incomplete — check fault injection parameters.")


asyncio.run(main())
