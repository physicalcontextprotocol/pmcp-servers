# Contributing to pmcp-servers

Reference and example robot servers. Everything here is **illustrative** —
no real hardware driver ships in this repository.

The organization-wide contributor policy lives in
[`physicalcontextprotocol/.github`](https://github.com/physicalcontextprotocol/.github/blob/main/CONTRIBUTING.md).
This file covers what is specific to this repository.

## Running the servers

These files hard-import from `v05.*`, so this repository does not run
standalone:

```bash
pip install -e ../pmcp-python
export PYTHONPATH="$PWD/../pmcp-python:$PWD"
python robot_servers/arm_server.py --help
```

Some files under `examples/` also import `pmcp_grand_unified`, which
still lives in the private `pmcp-labs` quarantine repository. Those
examples will not run for contributors without labs access:
`example_black_swan.py`, `example_multi_robot.py`,
`example_harvest_cycle.py`, `example_safety_blocks.py`,
`generate_examples.py`.

**If you can make an example stop depending on `pmcp_grand_unified`,
that is a real contribution.** A 161 KB file with no clear ownership is
not a dependency any example should have.

## The gateway fallback is a trap

`gateway/` falls back to a literal `"mock_ca_key"` PEM string when the
`cryptography` extra is missing. Do not run the gateway that way in any
real deployment. If you touch this code, consider making the missing
dependency a hard failure instead of a silent downgrade — a security
control that quietly stops existing is worse than one that refuses to
start.

## Adding a server

- Keep it demonstrative. A new server should be readable in one sitting
  and runnable without hardware.
- Do not weaken a gate. If your example needs to bypass E-Stop, Lease,
  Constitution, or Shadow to produce output, that is a sign the example
  is showing something the protocol does not permit. Open an issue
  instead of working around it.

## Releasing

No package metadata. Reference material, versioned with the SDK it
targets.
