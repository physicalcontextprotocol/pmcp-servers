# Security policy — pmcp-servers

The default policy for this organization lives in
[`pmcp-spec/SECURITY.md`](https://github.com/physicalcontextprotocol/pmcp-spec/blob/main/SECURITY.md)
and applies here in full. This file records what is specific to the
reference servers.

## Reporting

Use **private vulnerability reporting**:
**Security → Report a vulnerability** on this repository, or
[open an org-level advisory](https://github.com/physicalcontextprotocol/security/advisories/new).

Do not open a public issue.

## Read this first

**Everything in this repository is illustrative.** No real hardware
driver ships here. `reference_impl/` uses `MockHospitalRobot` and
`MockFactoryRobot`. `ros2-bridge/` runs in mock mode when a real ROS 2
installation is absent.

Treat this repository as documentation. Copying a server from here into
a real deployment means re-doing its safety review.

## In scope here

- `gateway/` — the protocol gateway. One specific hazard: it falls back
  to a literal `"mock_ca_key"` PEM string when the `cryptography`
  extra is missing. If that fallback can be reached in a
  non-development path, it is a real report, because it means TLS
  identity is being faked rather than failing.
- A reference server that skips a gate (`E-Stop`, `Lease`,
  `Constitution`, `Shadow`) that the specification makes mandatory.
- Leaked secrets or credentials in this repository.

## Out of scope here

- The absence of authentication on the example servers. They bind
  locally and are not a service.
- The `v05.*` and `pmcp_grand_unified` hard imports. Those are a known
  migration artefact, tracked in the repository README.
- Breakage in `simulator/` or the ROS 2 transport. Open an issue.
- Anything under `examples/` beyond a gate bypass. Those files are
  explicitly illustrative.

## Supported

Best-effort. No supported-version table yet.
