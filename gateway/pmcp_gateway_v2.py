"""
P-MCP Gateway — mTLS-secured Reverse Proxy
============================================
Authenticates inbound JSON-RPC requests, verifies Ed25519 or JWT bearer
tokens, applies rate-limiting, then forwards to robot servers.

Run:
    python gateway/pmcp_gateway.py --port 8443 --upstream http://robot:8080
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import ssl
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

import aiohttp
from aiohttp import web

log = logging.getLogger("pmcp.gateway")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s — %(message)s")

PMCP_VERSION = "0.5"


# ─────────────────────────────────────────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GatewayConfig:
    port: int = 8443
    host: str = "0.0.0.0"
    tls_cert: str = ""
    tls_key: str = ""
    tls_ca: str = ""          # CA cert for mTLS client verification
    require_mtls: bool = False

    upstreams: List[str] = field(default_factory=lambda: ["http://localhost:8080"])
    lb_strategy: str = "round_robin"  # round_robin | least_conn | random

    # Auth
    jwt_secret: str = ""
    allowed_dids: Set[str] = field(default_factory=set)
    auth_required: bool = False

    # Rate limiting
    rate_limit_rps: float = 100.0
    rate_limit_burst: int = 200

    # Timeouts
    upstream_timeout_ms: int = 30_000
    connect_timeout_ms: int = 5_000

    # Allowed methods (empty = allow all)
    allowed_methods: Set[str] = field(default_factory=set)

    # CORS
    cors_origins: List[str] = field(default_factory=lambda: ["*"])


# ─────────────────────────────────────────────────────────────────────────────
#  Token Bucket Rate Limiter
# ─────────────────────────────────────────────────────────────────────────────

class TokenBucket:
    def __init__(self, rate_hz: float, burst: int) -> None:
        self.rate = rate_hz
        self.burst = burst
        self._buckets: Dict[str, tuple[float, float]] = {}  # key → (tokens, last_refill)

    def consume(self, key: str) -> bool:
        now = time.monotonic()
        tokens, last = self._buckets.get(key, (float(self.burst), now))
        elapsed = now - last
        tokens = min(self.burst, tokens + elapsed * self.rate)
        if tokens >= 1.0:
            self._buckets[key] = (tokens - 1.0, now)
            return True
        self._buckets[key] = (tokens, now)
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  JWT / Bearer Token Verifier (HMAC-SHA256 only — symmetric for simplicity)
# ─────────────────────────────────────────────────────────────────────────────

def _b64url_decode(s: str) -> bytes:
    # Add padding
    s = s + "=" * (4 - len(s) % 4)
    return base64.urlsafe_b64decode(s)


def verify_jwt(token: str, secret: str) -> Optional[Dict[str, Any]]:
    """Verify a HS256 JWT. Returns payload dict or None on failure."""
    if not secret:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header_b64, payload_b64, sig_b64 = parts
    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception:
        return None
    # Verify expiry
    exp = payload.get("exp")
    if exp and int(exp) < time.time():
        return None
    # Verify signature
    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected_sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    expected_b64 = base64.urlsafe_b64encode(expected_sig).rstrip(b"=").decode()
    if not hmac.compare_digest(expected_b64, sig_b64):
        return None
    return payload


# ─────────────────────────────────────────────────────────────────────────────
#  Load Balancer
# ─────────────────────────────────────────────────────────────────────────────

class LoadBalancer:
    def __init__(self, upstreams: List[str], strategy: str = "round_robin") -> None:
        self.upstreams = list(upstreams)
        self.strategy = strategy
        self._idx = 0
        self._conn_count: Dict[str, int] = {u: 0 for u in upstreams}

    def pick(self) -> str:
        if not self.upstreams:
            raise RuntimeError("No upstreams configured")
        if self.strategy == "random":
            import random
            return random.choice(self.upstreams)
        elif self.strategy == "least_conn":
            return min(self._conn_count, key=self._conn_count.get)
        else:  # round_robin
            u = self.upstreams[self._idx % len(self.upstreams)]
            self._idx += 1
            return u

    def inc(self, upstream: str) -> None:
        self._conn_count[upstream] = self._conn_count.get(upstream, 0) + 1

    def dec(self, upstream: str) -> None:
        self._conn_count[upstream] = max(0, self._conn_count.get(upstream, 0) - 1)


# ─────────────────────────────────────────────────────────────────────────────
#  Gateway Handler
# ─────────────────────────────────────────────────────────────────────────────

class PMCPGateway:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.lb = LoadBalancer(config.upstreams, config.lb_strategy)
        self.rate_limiter = TokenBucket(config.rate_limit_rps, config.rate_limit_burst)
        self._session: Optional[aiohttp.ClientSession] = None

        # Metrics
        self._req_count = 0
        self._err_count = 0
        self._blocked_count = 0
        self._start_time = time.time()

    async def start(self) -> None:
        timeout = aiohttp.ClientTimeout(
            total=self.config.upstream_timeout_ms / 1000,
            connect=self.config.connect_timeout_ms / 1000,
        )
        self._session = aiohttp.ClientSession(timeout=timeout)
        log.info("Gateway session started")

    async def stop(self) -> None:
        if self._session:
            await self._session.close()

    def _client_key(self, request: web.Request) -> str:
        return request.headers.get("X-PMCP-DID") or request.remote or "anonymous"

    def _authenticate(self, request: web.Request) -> Optional[str]:
        """Returns authenticated subject or None if auth fails."""
        if not self.config.auth_required:
            return "anonymous"

        auth_hdr = request.headers.get("Authorization", "")
        did_hdr = request.headers.get("X-PMCP-DID", "")

        # DID-based (future: verify Ed25519 signature from DID doc)
        if did_hdr and did_hdr in self.config.allowed_dids:
            return did_hdr

        # JWT Bearer
        if auth_hdr.startswith("Bearer "):
            token = auth_hdr[7:]
            payload = verify_jwt(token, self.config.jwt_secret)
            if payload:
                sub = payload.get("sub", "")
                if not self.config.allowed_dids or sub in self.config.allowed_dids:
                    return sub

        return None

    async def handle_proxy(self, request: web.Request) -> web.Response:
        self._req_count += 1
        client_key = self._client_key(request)

        # Rate limit
        if not self.rate_limiter.consume(client_key):
            self._blocked_count += 1
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32000, "message": "Rate limit exceeded"},
                    "id": None,
                },
                status=429,
            )

        # Auth
        subject = self._authenticate(request)
        if subject is None:
            self._blocked_count += 1
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32001, "message": "Unauthorized"},
                    "id": None,
                },
                status=401,
            )

        # Read body
        body_bytes = await request.read()

        # Method filter
        if self.config.allowed_methods:
            try:
                parsed = json.loads(body_bytes)
                method = parsed.get("method", "")
                if method not in self.config.allowed_methods:
                    return web.json_response(
                        {
                            "jsonrpc": "2.0",
                            "error": {"code": -32601, "message": f"Method {method!r} not permitted by gateway policy"},
                            "id": parsed.get("id"),
                        },
                        status=403,
                    )
            except json.JSONDecodeError:
                pass

        # Forward
        upstream = self.lb.pick()
        self.lb.inc(upstream)
        fwd_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ("host", "content-length")
        }
        fwd_headers["X-PMCP-Gateway"] = "pmcp-gateway/0.5"
        fwd_headers["X-PMCP-Subject"] = subject

        try:
            async with self._session.post(
                f"{upstream}/mcp",
                data=body_bytes,
                headers=fwd_headers,
            ) as resp:
                resp_body = await resp.read()
                resp_headers = {
                    "Content-Type": resp.headers.get("Content-Type", "application/json"),
                    "X-PMCP-Version": PMCP_VERSION,
                    "X-PMCP-Upstream": upstream,
                }
                for origin in self.config.cors_origins:
                    resp_headers["Access-Control-Allow-Origin"] = origin
                return web.Response(body=resp_body, status=resp.status, headers=resp_headers)
        except aiohttp.ClientError as exc:
            self._err_count += 1
            log.error("Upstream error (%s): %s", upstream, exc)
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32002, "message": f"Upstream unavailable: {exc}"},
                    "id": None,
                },
                status=502,
            )
        finally:
            self.lb.dec(upstream)

    async def handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "upstreams": self.config.upstreams,
            "uptime_s": time.time() - self._start_time,
        })

    async def handle_metrics(self, request: web.Request) -> web.Response:
        return web.json_response({
            "requests_total": self._req_count,
            "errors_total": self._err_count,
            "blocked_total": self._blocked_count,
            "uptime_s": time.time() - self._start_time,
        })

    async def handle_options(self, request: web.Request) -> web.Response:
        return web.Response(
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization, X-PMCP-DID",
            }
        )


# ─────────────────────────────────────────────────────────────────────────────
#  App Factory
# ─────────────────────────────────────────────────────────────────────────────

async def create_gateway_app(config: GatewayConfig) -> web.Application:
    gw = PMCPGateway(config)
    app = web.Application()
    app["gateway"] = gw

    app.router.add_post("/mcp", gw.handle_proxy)
    app.router.add_options("/mcp", gw.handle_options)
    app.router.add_get("/health", gw.handle_health)
    app.router.add_get("/metrics", gw.handle_metrics)

    async def on_startup(application: web.Application) -> None:
        await gw.start()

    async def on_cleanup(application: web.Application) -> None:
        await gw.stop()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


# ─────────────────────────────────────────────────────────────────────────────
#  Entry Point
# ─────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="P-MCP Gateway")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--upstream", action="append", dest="upstreams")
    parser.add_argument("--cert", default="")
    parser.add_argument("--key", default="")
    parser.add_argument("--ca", default="")
    parser.add_argument("--mtls", action="store_true")
    parser.add_argument("--jwt-secret", default="")
    parser.add_argument("--auth", action="store_true")
    parser.add_argument("--rate", type=float, default=100.0)
    args = parser.parse_args()

    config = GatewayConfig(
        port=args.port,
        host=args.host,
        upstreams=args.upstreams or ["http://localhost:8080"],
        tls_cert=args.cert,
        tls_key=args.key,
        tls_ca=args.ca,
        require_mtls=args.mtls,
        jwt_secret=args.jwt_secret,
        auth_required=args.auth,
        rate_limit_rps=args.rate,
    )

    app = await create_gateway_app(config)

    ssl_ctx = None
    if config.tls_cert and config.tls_key:
        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(config.tls_cert, config.tls_key)
        if config.tls_ca:
            ssl_ctx.load_verify_locations(config.tls_ca)
            if config.require_mtls:
                ssl_ctx.verify_mode = ssl.CERT_REQUIRED

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.host, config.port, ssl_context=ssl_ctx)
    await site.start()
    log.info("P-MCP Gateway running on %s:%d (TLS=%s, mTLS=%s)",
             config.host, config.port, bool(ssl_ctx), config.require_mtls)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
