# Changelog — pmcp-servers

All notable changes to the reference and example robot servers. The
format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0] — 2026-09-28

First tagged public release.

### Added

- `SECURITY.md` that states plainly what this repository is: every
  server here is illustrative, `reference_impl/` uses
  `MockHospitalRobot` / `MockFactoryRobot`, and `ros2-bridge/` runs in
  mock mode without a real ROS 2 install. Copying a server from here
  into a real deployment means redoing its safety review.
  `gateway/` is called out specifically, along with its
  `"mock_ca_key"` fallback.
- `CONTRIBUTING.md` documenting the `PYTHONPATH` requirement and
  listing the five examples that cannot run without `pmcp-labs` access.

### Known limitations (documented, not fixed)

- **Not self-contained.** Servers and most examples hard-import
  `from v05.*`, so this repository does not run as a standalone
  install. `pmcp-python` must be installed alongside it.
- **Five examples are unrunnable for most contributors** because they
  import `pmcp_grand_unified`, which lives in the private `pmcp-labs`
  quarantine: `example_black_swan.py`, `example_multi_robot.py`,
  `example_harvest_cycle.py`, `example_safety_blocks.py`,
  `generate_examples.py`. Making these stop depending on that 161 KB
  file is the highest-value contribution available here.
- **No test suite.** Verification happens in `pmcp-python/tests/` and
  `pmcp-conformance/`.
- **`gateway/` falls back to a literal `"mock_ca_key"` PEM string**
  when the `cryptography` extra is missing — a security control that
  quietly stops existing rather than refusing to start.

### Changed

- Split into its own repository, so the reference servers are not
  mistaken for part of the SDK itself.

## [0.5.0]

Servers established against `pmcp-python/v05/`: arm, mobile, and
agricultural reference servers; hospital and smart-factory reference
implementations; the world/robot simulator; and the protocol gateway.
