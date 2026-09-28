# pmcp-servers

Reference and example robot servers built on top of the `pmcp-python`
SDK (specifically the `v05/` shipping code).

## Contents

- `robot_servers/` — arm, mobile, agricultural reference servers
  (mirrored from `pmcp-python/v05/robot_servers/`).
- `reference_impl/` — `hospital_robot.py`, `smart_factory.py`
  reference implementations. Both currently use `MockHospitalRobot` /
  `MockFactoryRobot` classes; they are illustrative, not
  hardware-connected.
- `examples/` — runnable examples (multi-robot, warehouse, safety
  blocks, hello-world TurtleBot, Grok agent, harvest cycle).
- `simulator/` — world/robot simulator used by examples.
- `gateway/` — `pmcp_gateway.py` / `pmcp_gateway_v2.py` protocol
  gateway. Falls back to a `"mock_ca_key"` PEM string when the
  `cryptography` extra is missing — do **not** run the gateway that
  way in a real deployment; install `pmcp-python[dev]` or equivalent.
- `ros2-bridge/` — ROS 2 bridge (Python + `ros2_bridge.cpp` transport).
  Runs in "mock mode" when a real ROS 2 installation is not present.

## How to run

The servers and most examples hard-import from `v05.*`. They do **not**
work as a standalone install of this sub-project alone. To run:

```bash
# From the pmcp-org root:
pip install -e pmcp-python
export PYTHONPATH="$PWD/pmcp-python:$PWD/pmcp-servers"
python pmcp-servers/robot_servers/arm_server.py --help
```

Several files under `examples/` also `from pmcp_grand_unified import …`.
That module has not been split out of `pmcp-labs/` yet; if you want to
run those examples, add `pmcp-labs/` to `PYTHONPATH` as well:

```bash
export PYTHONPATH="$PWD/pmcp-python:$PWD/pmcp-servers:$PWD/pmcp-labs"
```

Examples that need `pmcp_grand_unified`:
`example_black_swan.py`, `example_multi_robot.py`, `example_harvest_cycle.py`,
`example_safety_blocks.py`, `generate_examples.py`.

## Status

- Every server and example under this sub-project is
  **illustrative** — no real hardware driver ships here.
- There is no test suite in this sub-project. Verification happens in
  `pmcp-python/tests/` and `pmcp-conformance/`.
- The cross-repo `from v05.*` and `from pmcp_grand_unified` imports are
  a known migration artefact. Consolidating them (either by making
  `pmcp-servers` depend on `pmcp-python` as a proper package, or by
  vendoring the needed types) is called out in
  [`MIGRATION_MAP` in pmcp-spec](https://github.com/physicalcontextprotocol/pmcp-spec/blob/main/MIGRATION_MAP.md).

## License

Apache 2.0 (see [`LICENSE`](LICENSE)).
