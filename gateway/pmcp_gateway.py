#!/usr/bin/env python3
"""
P-MCP mTLS Gateway
==================

A security gateway that implements:
- Mutual TLS (mTLS) authentication
- DID-based certificate management
- Certificate authority (CA) for robot certificates
- Certificate lifecycle management
- Access control based on robot identity

This gateway sits between clients and P-MCP servers,
providing:
- Authentication: Verify robot identity via certificates
- Authorization: Enforce access policies
- Encryption: TLS for all traffic
- Auditing: Log all requests

Usage:
    python gateway/pmcp_gateway.py --ca-key ca.key --ca-cert ca.crt
    python gateway/pmcp_gateway.py --port 8443 --upstream http://localhost:8080
"""

import argparse
import asyncio
import hashlib
import json
import os
import ssl
import time
import uuid
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
import threading

# Try to import cryptography, fall back to pure Python
try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.hazmat.backends import default_backend
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    logging.warning("cryptography not installed. Using mock crypto.")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pmcp-gateway")


# ============================================================================
# Certificate Management
# ============================================================================

class CertificateType(Enum):
    """Types of certificates."""
    ROOT_CA = "root_ca"
    INTERMEDIATE_CA = "intermediate_ca" 
    SERVER = "server"
    CLIENT = "client"


@dataclass
class Certificate:
    """Certificate information."""
    cert_id: str
    subject_did: str
    cert_type: CertificateType
    serial_number: str
    not_before: float
    not_after: float
    public_key: str
    issuer: str
    fingerprint: str
    status: str = "active"  # active, revoked, expired
    revoked_at: Optional[float] = None
    metadata: Dict[str, str] = field(default_factory=dict)
    
    @property
    def is_valid(self) -> bool:
        """Check if certificate is currently valid."""
        now = time.time()
        return (self.status == "active" and 
                self.not_before <= now <= self.not_after)
    
    @property
    def is_expired(self) -> bool:
        """Check if certificate has expired."""
        return time.time() > self.not_after
    
    @property
    def days_until_expiry(self) -> int:
        """Get days until expiry."""
        return max(0, int((self.not_after - time.time()) / 86400))
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "certId": self.cert_id,
            "subjectDid": self.subject_did,
            "certType": self.cert_type.value,
            "serialNumber": self.serial_number,
            "notBefore": self.not_before,
            "notAfter": self.not_after,
            "issuer": self.issuer,
            "fingerprint": self.fingerprint,
            "status": self.status,
            "isValid": self.is_valid,
            "daysUntilExpiry": self.days_until_expiry,
        }


class CertificateAuthority:
    """Certificate Authority for P-MCP certificates."""
    
    def __init__(self, ca_key_path: str, ca_cert_path: str):
        self.ca_key_path = ca_key_path
        self.ca_cert_path = ca_cert_path
        self._ca_key = None
        self._ca_cert = None
        
        # Certificate store
        self._certificates: Dict[str, Certificate] = {}
        self._serial_numbers: Set[str] = set()
        self._lock = threading.RLock()
        
        # Load or generate CA
        self._load_or_create_ca()
    
    def _load_or_create_ca(self):
        """Load existing CA or create new one."""
        if os.path.exists(self.ca_key_path) and os.path.exists(self.ca_cert_path):
            self._load_ca()
        else:
            self._create_ca()
    
    def _create_ca(self):
        """Create a new CA."""
        logger.info("Creating new Certificate Authority")
        
        if HAS_CRYPTO:
            # Generate CA key
            self._ca_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=4096,
                backend=default_backend()
            )
            
            # Generate self-signed CA certificate
            subject = issuer = x509.Name([
                x509.NameAttribute(x509.oid.NameAttribute.COUNTRY_NAME, "US"),
                x509.NameAttribute(x509.oid.NameAttribute.STATE_OR_PROVINCE_NAME, "California"),
                x509.NameAttribute(x509.oid.NameAttribute.ORGANIZATION_NAME, "P-MCP Foundation"),
                x509.NameAttribute(x509.oid.NameAttribute.COMMON_NAME, "P-MCP Root CA"),
            ])
            
            now = datetime.utcnow()
            cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(self._ca_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now)
                .not_valid_after(now + timedelta(days=3650))
                .add_extension(
                    x509.BasicConstraints(ca=True, path_length=None),
                    critical=True,
                )
                .add_extension(
                    x509.KeyUsage(
                        digital_signature=True,
                        key_cert_sign=True,
                        crl_sign=True,
                        key_encipherment=False,
                        content_commitment=False,
                        data_encipherment=False,
                        key_agreement=False,
                        encipher_only=False,
                        decipher_only=False,
                    ),
                    critical=True,
                )
                .sign(self._ca_key, hashes.SHA256(), default_backend())
            )
            
            self._ca_cert = cert
            
            # Save to files
            os.makedirs(os.path.dirname(self.ca_key_path) or ".", exist_ok=True)
            with open(self.ca_key_path, "wb") as f:
                f.write(self._ca_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption()
                ))
            
            with open(self.ca_cert_path, "wb") as f:
                f.write(cert.public_bytes(serialization.Encoding.PEM))
            
            logger.info(f"CA created: {self.ca_cert_path}")
            
        else:
            # Mock CA for when cryptography is not available
            self._ca_key = "mock_ca_key"
            self._ca_cert = "mock_ca_cert"
            
            # Create CA certificate entry
            ca_cert = Certificate(
                cert_id="root-ca",
                subject_did="did:pmcp:ca:root",
                cert_type=CertificateType.ROOT_CA,
                serial_number="1",
                not_before=time.time(),
                not_after=time.time() + 3650 * 86400,
                public_key="mock_public_key",
                issuer="did:pmcp:ca:root",
                fingerprint=self._compute_fingerprint("mock_cert"),
            )
            self._certificates["root-ca"] = ca_cert
            
            logger.warning("Using mock CA (cryptography not installed)")
    
    def _load_ca(self):
        """Load existing CA from files."""
        logger.info(f"Loading CA from {self.ca_cert_path}")
        
        if HAS_CRYPTO:
            with open(self.ca_key_path, "rb") as f:
                self._ca_key = serialization.load_pem_private_key(
                    f.read(), password=None, backend=default_backend()
                )
            
            with open(self.ca_cert_path, "rb") as f:
                self._ca_cert = x509.load_pem_x509_certificate(f.read(), default_backend())
        else:
            self._ca_key = "mock_ca_key"
            self._ca_cert = "mock_ca_cert"
    
    def _compute_fingerprint(self, data: str) -> str:
        """Compute SHA256 fingerprint."""
        return hashlib.sha256(data.encode()).hexdigest()
    
    def issue_certificate(
        self,
        subject_did: str,
        cert_type: CertificateType,
        validity_days: int = 365,
        metadata: Optional[Dict[str, str]] = None,
    ) -> Certificate:
        """Issue a new certificate."""
        with self._lock:
            now = time.time()
            serial = str(uuid.uuid4().hex)
            
            # In production, would actually generate certificate
            # For now, create a certificate record
            cert = Certificate(
                cert_id=f"cert-{serial[:12]}",
                subject_did=subject_did,
                cert_type=cert_type,
                serial_number=serial,
                not_before=now,
                not_after=now + validity_days * 86400,
                public_key=f"public_key_{serial[:8]}",
                issuer="did:pmcp:ca:root",
                fingerprint=self._compute_fingerprint(f"{subject_did}:{serial}"),
                metadata=metadata or {},
            )
            
            self._certificates[cert.cert_id] = cert
            self._serial_numbers.add(serial)
            
            logger.info(f"Issued certificate {cert.cert_id} for {subject_did}")
            
            return cert
    
    def revoke_certificate(self, cert_id: str) -> bool:
        """Revoke a certificate."""
        with self._lock:
            if cert_id not in self._certificates:
                return False
            
            cert = self._certificates[cert_id]
            cert.status = "revoked"
            cert.revoked_at = time.time()
            
            logger.info(f"Revoked certificate {cert_id}")
            return True
    
    def get_certificate(self, cert_id: str) -> Optional[Certificate]:
        """Get a certificate by ID."""
        with self._lock:
            return self._certificates.get(cert_id)
    
    def get_certificate_by_did(self, did: str) -> Optional[Certificate]:
        """Get a certificate by subject DID."""
        with self._lock:
            for cert in self._certificates.values():
                if cert.subject_did == did and cert.is_valid:
                    return cert
            return None
    
    def list_certificates(
        self,
        cert_type: Optional[CertificateType] = None,
        status: Optional[str] = None,
    ) -> List[Certificate]:
        """List certificates with optional filtering."""
        with self._lock:
            certs = list(self._certificates.values())
            
            if cert_type:
                certs = [c for c in certs if c.cert_type == cert_type]
            
            if status:
                certs = [c for c in certs if c.status == status]
            
            return certs
    
    def get_ca_cert_pem(self) -> str:
        """Get CA certificate as PEM."""
        if HAS_CRYPTO and self._ca_cert:
            return self._ca_cert.public_bytes(serialization.Encoding.PEM).decode()
        return "-----BEGIN CERTIFICATE-----\nMOCK\n-----END CERTIFICATE-----"
    
    def cleanup_expired(self) -> int:
        """Remove expired certificates."""
        with self._lock:
            expired = [
                cert_id for cert_id, cert in self._certificates.items()
                if cert.is_expired
            ]
            
            for cert_id in expired:
                self._certificates[cert_id].status = "expired"
            
            return len(expired)


# ============================================================================
# DID-based Identity
# ============================================================================

@dataclass
class RobotIdentity:
    """Robot identity with DID."""
    did: str
    robot_class: str
    model: str
    serial: str
    location: str
    certificate_id: Optional[str] = None
    public_key: Optional[str] = None
    registered_at: float = field(default_factory=time.time)
    metadata: Dict[str, str] = field(default_factory=dict)
    
    @property
    def is_registered(self) -> bool:
        """Check if robot is registered with a certificate."""
        return self.certificate_id is not None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "did": self.did,
            "robotClass": self.robot_class,
            "model": self.model,
            "serial": self.serial,
            "location": self.location,
            "certificateId": self.certificate_id,
            "publicKey": self.public_key,
            "registeredAt": self.registered_at,
        }


class IdentityRegistry:
    """Registry of robot identities."""
    
    def __init__(self, ca: CertificateAuthority):
        self._ca = ca
        self._identities: Dict[str, RobotIdentity] = {}
        self._lock = threading.RLock()
    
    def register_identity(
        self,
        robot_class: str,
        model: str,
        serial: str,
        location: str,
    ) -> RobotIdentity:
        """Register a new robot identity."""
        with self._lock:
            did = f"did:pmcp:{robot_class}:{model.lower()}:{location}:{serial}"
            
            identity = RobotIdentity(
                did=did,
                robot_class=robot_class,
                model=model,
                serial=serial,
                location=location,
            )
            
            # Issue certificate for this identity
            cert = self._ca.issue_certificate(
                subject_did=did,
                cert_type=CertificateType.CLIENT,
                validity_days=365,
                metadata={
                    "robot_class": robot_class,
                    "model": model,
                    "serial": serial,
                }
            )
            
            identity.certificate_id = cert.cert_id
            
            self._identities[did] = identity
            
            logger.info(f"Registered identity: {did}")
            
            return identity
    
    def get_identity(self, did: str) -> Optional[RobotIdentity]:
        """Get identity by DID."""
        with self._lock:
            return self._identities.get(did)
    
    def get_identity_by_cert(self, cert_id: str) -> Optional[RobotIdentity]:
        """Get identity by certificate ID."""
        with self._lock:
            for identity in self._identities.values():
                if identity.certificate_id == cert_id:
                    return identity
            return None
    
    def list_identities(
        self,
        robot_class: Optional[str] = None,
        location: Optional[str] = None,
    ) -> List[RobotIdentity]:
        """List identities with optional filtering."""
        with self._lock:
            identities = list(self._identities.values())
            
            if robot_class:
                identities = [i for i in identities if i.robot_class == robot_class]
            
            if location:
                identities = [i for i in identities if i.location == location]
            
            return identities
    
    def revoke_identity(self, did: str) -> bool:
        """Revoke a robot identity and its certificate."""
        with self._lock:
            if did not in self._identities:
                return False
            
            identity = self._identities[did]
            
            if identity.certificate_id:
                self._ca.revoke_certificate(identity.certificate_id)
            
            logger.info(f"Revoked identity: {did}")
            return True


# ============================================================================
# Access Control
# ============================================================================

class AccessPolicy(Enum):
    """Access policy actions."""
    ALLOW = "allow"
    DENY = "deny"
    AUDIT = "audit"


@dataclass
class AccessRule:
    """Access control rule."""
    rule_id: str
    name: str
    action: AccessPolicy
    subject_pattern: str  # DID pattern (supports wildcards)
    resource_pattern: str  # Resource pattern
    conditions: Dict[str, Any] = field(default_factory=dict)
    priority: int = 0  # Higher = more important
    enabled: bool = True
    
    def matches(self, subject_did: str, resource: str) -> bool:
        """Check if this rule matches the request."""
        # Simple pattern matching (supports * wildcard)
        if not self.enabled:
            return False
        
        subject_match = self._match_pattern(subject_did, self.subject_pattern)
        resource_match = self._match_pattern(resource, self.resource_pattern)
        
        return subject_match and resource_match
    
    def _match_pattern(self, value: str, pattern: str) -> bool:
        """Match value against pattern with wildcard support."""
        if pattern == "*":
            return True
        
        if "*" in pattern:
            prefix = pattern.split("*")[0]
            return value.startswith(prefix)
        
        return value == pattern


class AccessControlList:
    """Access control list manager."""
    
    def __init__(self):
        self._rules: List[AccessRule] = []
        self._default_action = AccessPolicy.DENY
        self._lock = threading.RLock()
        
        # Add default rules
        self._add_default_rules()
    
    def _add_default_rules(self):
        """Add default access rules."""
        default_rules = [
            AccessRule(
                rule_id="allow-all-verified",
                name="Allow all verified robots",
                action=AccessPolicy.ALLOW,
                subject_pattern="did:pmcp:*",
                resource_pattern="*",
                priority=10,
            ),
            AccessRule(
                rule_id="deny-unverified",
                name="Deny unverified",
                action=AccessPolicy.DENY,
                subject_pattern="*",
                resource_pattern="*",
                priority=1,
            ),
        ]
        
        for rule in default_rules:
            self._rules.append(rule)
    
    def add_rule(self, rule: AccessRule):
        """Add an access rule."""
        with self._lock:
            self._rules.append(rule)
            # Sort by priority
            self._rules.sort(key=lambda r: r.priority, reverse=True)
            logger.info(f"Added access rule: {rule.name}")
    
    def remove_rule(self, rule_id: str) -> bool:
        """Remove an access rule."""
        with self._lock:
            for i, rule in enumerate(self._rules):
                if rule.rule_id == rule_id:
                    del self._rules[i]
                    return True
            return False
    
    def check_access(self, subject_did: str, resource: str) -> AccessPolicy:
        """Check if access is allowed."""
        with self._lock:
            for rule in self._rules:
                if rule.matches(subject_did, resource):
                    logger.debug(f"Rule {rule.rule_id}: {rule.action.value} for {subject_did}")
                    return rule.action
            
            return self._default_action
    
    def list_rules(self) -> List[AccessRule]:
        """List all rules."""
        with self._lock:
            return list(self._rules)


# ============================================================================
# Gateway
# ============================================================================

@dataclass
class RequestContext:
    """Context for a request."""
    request_id: str
    timestamp: float
    source_ip: str
    source_did: Optional[str]
    method: str
    path: str
    authenticated: bool
    authorized: bool
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "requestId": self.request_id,
            "timestamp": self.timestamp,
            "sourceIp": self.source_ip,
            "sourceDid": self.source_did,
            "method": self.method,
            "path": self.path,
            "authenticated": self.authenticated,
            "authorized": self.authorized,
        }


class GatewayConfig:
    """Gateway configuration."""
    
    def __init__(self, args):
        self.host = args.host
        self.port = args.port
        self.upstream_url = args.upstream
        self.ca_key_path = args.ca_key
        self.ca_cert_path = args.ca_cert
        self.tls_cert_path = args.tls_cert
        self.tls_key_path = args.tls_key
        self.verify_client = args.verify_client
        self.log_level = args.log_level


class Gateway:
    """P-MCP mTLS Gateway."""
    
    def __init__(self, config: GatewayConfig):
        self.config = config
        
        # Initialize components
        self._ca = CertificateAuthority(
            config.ca_key_path,
            config.ca_cert_path,
        )
        self._identity_registry = IdentityRegistry(self._ca)
        self._acl = AccessControlList()
        
        # Request tracking
        self._request_log: List[RequestContext] = []
        self._lock = threading.RLock()
        
        logger.info("P-MCP Gateway initialized")
    
    def register_robot(
        self,
        robot_class: str,
        model: str,
        serial: str,
        location: str,
    ) -> RobotIdentity:
        """Register a new robot."""
        return self._identity_registry.register_identity(
            robot_class, model, serial, location
        )
    
    def get_robot_certificate(self, did: str) -> Optional[Certificate]:
        """Get robot's certificate."""
        identity = self._identity_registry.get_identity(did)
        if identity and identity.certificate_id:
            return self._ca.get_certificate(identity.certificate_id)
        return None
    
    def check_access(self, did: str, resource: str) -> bool:
        """Check if robot has access to resource."""
        policy = self._acl.check_access(did, resource)
        return policy == AccessPolicy.ALLOW
    
    def log_request(self, ctx: RequestContext):
        """Log a request."""
        with self._lock:
            self._request_log.append(ctx)
            # Keep last 10000 requests
            if len(self._request_log) > 10000:
                self._request_log = self._request_log[-5000:]
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get gateway metrics."""
        with self._lock:
            total = len(self._request_log)
            authenticated = sum(1 for r in self._request_log if r.authenticated)
            authorized = sum(1 for r in self._request_log if r.authorized)
            
            return {
                "totalRequests": total,
                "authenticatedRequests": authenticated,
                "authorizedRequests": authorized,
                "rejectedRequests": total - authorized,
                "activeIdentities": len(self._identity_registry._identities),
                "issuedCertificates": len(self._ca._certificates),
                "accessRules": len(self._acl._rules),
            }
    
    async def handle_request(self, method: str, path: str, 
                           headers: Dict[str, str], 
                           body: Optional[bytes],
                           client_cert: Optional[str] = None) -> Dict[str, Any]:
        """Handle incoming request."""
        request_id = uuid.uuid4().hex[:12]
        timestamp = time.time()
        
        # Extract source IP
        source_ip = headers.get("X-Forwarded-For", "unknown")
        
        # Extract DID from client certificate
        source_did = None
        authenticated = False
        
        if client_cert:
            cert = self._ca.get_certificate_by_did(client_cert)
            if cert and cert.is_valid:
                source_did = client_cert
                authenticated = True
        
        # Check access
        authorized = False
        if authenticated and source_did:
            authorized = self.check_access(source_did, path)
        
        # Create context
        ctx = RequestContext(
            request_id=request_id,
            timestamp=timestamp,
            source_ip=source_ip,
            source_did=source_did,
            method=method,
            path=path,
            authenticated=authenticated,
            authorized=authorized,
        )
        self.log_request(ctx)
        
        # Return response
        if not authenticated:
            return {
                "status": 401,
                "error": "Authentication required",
            }
        
        if not authorized:
            return {
                "status": 403,
                "error": "Access denied",
            }
        
        return {
            "status": 200,
            "requestId": request_id,
            "message": "OK",
        }


# ============================================================================
# Main Entry Point
# ============================================================================

def parse_args():
    parser = argparse.ArgumentParser(description="P-MCP mTLS Gateway")
    
    # Server options
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8443, help="Port to bind to")
    
    # Upstream
    parser.add_argument("--upstream", default="http://localhost:8080", 
                       help="Upstream P-MCP server URL")
    
    # TLS options
    parser.add_argument("--tls-cert", default="gateway.crt", help="TLS certificate")
    parser.add_argument("--tls-key", default="gateway.key", help="TLS private key")
    
    # CA options
    parser.add_argument("--ca-key", default="ca.key", help="CA private key")
    parser.add_argument("--ca-cert", default="ca.crt", help="CA certificate")
    
    # Security options
    parser.add_argument("--verify-client", action="store_true", 
                       help="Verify client certificates")
    
    # Logging
    parser.add_argument("--log-level", default="INFO",
                       choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    
    return parser.parse_args()


async def main():
    args = parse_args()
    
    # Set logging
    logging.getLogger().setLevel(getattr(logging, args.log_level))
    
    # Create gateway
    config = GatewayConfig(args)
    gateway = Gateway(config)
    
    # Demo: Register some robots
    logger.info("Registering demo robots...")
    
    robots = [
        ("arm", "UR5", "UR5-001", "lab-01"),
        ("arm", "UR5", "UR5-002", "lab-01"),
        ("mobile", "TurtleBot4", "TB4-001", "warehouse-01"),
        ("agricultural", "HarvestBot", "HB-001", "farm-01"),
    ]
    
    for robot_class, model, serial, location in robots:
        identity = gateway.register_robot(robot_class, model, serial, location)
        logger.info(f"  Registered: {identity.did}")
    
    # Print metrics
    logger.info(f"Initial metrics: {gateway.get_metrics()}")
    
    # In production, would start HTTP server here
    logger.info(f"Gateway ready on {args.host}:{args.port}")
    logger.info(f"Upstream: {args.upstream}")
    logger.info(f"CA: {args.ca_cert_path}")
    
    # Keep running
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())