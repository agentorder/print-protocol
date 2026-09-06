"""Single-printer AgentOrder reference agent using the interim ES256 binding."""

from __future__ import annotations
import base64, json, secrets, time, hashlib
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from jsonschema import Draft202012Validator, FormatChecker
from schema_support import ucp_registry, config_allows, validate_quote

ROOT = Path(__file__).resolve().parent
SCHEMA = json.loads((ROOT / "schema.json").read_text())
QUOTE = Draft202012Validator(
    {"$ref": "#/$defs/quote", "$defs": SCHEMA["$defs"]},
    registry=ucp_registry(),
    format_checker=FormatChecker(),
)


def b64(v):
    return base64.urlsafe_b64encode(v).rstrip(b"=").decode()


class Agent:
    def __init__(self, profile_url, private_key=None, clock=time.time, allow_insecure=False):
        self.profile_url = profile_url
        self.key = private_key or ec.generate_private_key(ec.SECP256R1())
        self.clock = clock
        self.sent_rfqs = {}
        n = self.key.public_key().public_numbers()
        self.jwk = {
            "kid": "agent-key",
            "kty": "EC",
            "crv": "P-256",
            "x": b64(n.x.to_bytes(32, "big")),
            "y": b64(n.y.to_bytes(32, "big")),
            "alg": "ES256",
        }

    def platform_profile(self):
        return {
            "ucp": {
                "version": "2026-06-15",
                "services": {},
                "capabilities": {
                    "org.agentorder.shopping.print_quote": [{"version": "2026-09-06"}]
                },
                "payment_handlers": {},
            },
            "keys": [self.jwk],
        }

    def discover_printer(self, url):
        with urlopen(url) as r:
            return json.loads(r.read())

    def _request(self, url, method="GET", body=None, key=None):
        raw = b"" if body is None else json.dumps(body, separators=(",", ":")).encode()
        path = urlsplit(url).path
        ts = int(self.clock())
        nonce = secrets.token_urlsafe(12)
        der = self.key.sign(
            f"{method}.{path}.{ts}.{nonce}.".encode() + raw, ec.ECDSA(hashes.SHA256())
        )
        r, s = decode_dss_signature(der)
        h = {
            "UCP-Agent": self.profile_url,
            "X-AgentOrder-Signature": f'agent-key:{ts}:{nonce}:{b64(r.to_bytes(32,"big")+s.to_bytes(32,"big"))}',
        }
        if body is not None:
            h.update(
                {
                    "Content-Type": "application/json",
                    "Content-Length": str(len(raw)),
                    "Idempotency-Key": key,
                }
            )
        try:
            with urlopen(
                Request(url, data=raw if body is not None else None, headers=h, method=method)
            ) as x:
                return x.status, json.loads(x.read())
        except HTTPError as e:
            return e.code, json.loads(e.read())

    def request_quote(
        self, printer_url, print_job, buyer, fulfillment_destination, idempotency_key
    ):
        profile = self.discover_printer(printer_url + "/.well-known")
        caps = profile["ucp"]["capabilities"].get("org.agentorder.shopping.print_quote", [])
        if not any(c["version"] == "2026-09-06" for c in caps):
            raise ValueError("capability_not_negotiated")
        cap = next(c for c in caps if c["version"] == "2026-09-06")
        config = cap["config"]
        if not config_allows(print_job, config):
            raise ValueError("unsupported_print_job")
        rfq = {
            "agentorder_version": "0.2.0",
            "rfq_id": "rfq-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:16],
            "buyer": buyer,
            "fulfillment_destination": fulfillment_destination,
            "print_job": print_job,
        }
        status, out = self._request(printer_url + "/rfqs", "POST", rfq, idempotency_key)
        if status not in (200, 202):
            raise ValueError(out["error"]["code"])
        self.sent_rfqs[rfq["rfq_id"]] = rfq
        return rfq, status, out

    def get_quote(self, printer_url, quote_id):
        status, out = self._request(printer_url + "/quotes-" + quote_id)
        if status != 200:
            raise ValueError(out["error"]["code"])
        if out["rfq_id"] not in self.sent_rfqs:
            raise ValueError("rfq_mismatch")
        if json.dumps(out["print_job"], sort_keys=True, separators=(",", ":")) != json.dumps(
            self.sent_rfqs[out["rfq_id"]]["print_job"], sort_keys=True, separators=(",", ":")
        ):
            raise ValueError("print_job_mismatch")
        if (
            datetime.fromisoformat(out["expires_at"].replace("Z", "+00:00")).timestamp()
            <= self.clock()
        ):
            raise ValueError("quote_expired")
        errors = list(QUOTE.iter_errors(out))
        if errors:
            raise ValueError("invalid_quote")
        validate_quote(out)
        return out


def validate_received_quote(quote: dict) -> None:
    errors = list(QUOTE.iter_errors(quote))
    if errors:
        raise ValueError("invalid_quote")
    validate_quote(quote)
