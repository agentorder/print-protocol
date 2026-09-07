"""Loopback-only AgentOrder v0.2 reference server; not hosted-product code."""

from __future__ import annotations
import base64, hashlib, hmac, json, secrets, sqlite3, time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from cryptography.exceptions import InvalidSignature
from jsonschema import Draft202012Validator, FormatChecker
from schema_support import ucp_registry, config_allows
from jcs import jcs_canonicalize

ROOT = Path(__file__).resolve().parent
SCHEMA = json.loads((ROOT / "schema.json").read_text())
VERSION = "0.2.0"
REGISTRY = ucp_registry()
VALIDATORS = {
    name: Draft202012Validator(
        {"$ref": f"#/$defs/{name}", "$defs": SCHEMA["$defs"]},
        registry=REGISTRY,
        format_checker=FormatChecker(),
    )
    for name in SCHEMA["$defs"]
}


def canon(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"))


def b64d(v):
    return base64.urlsafe_b64decode(v + "=" * (-len(v) % 4))


def b64(v):
    return base64.urlsafe_b64encode(v).rstrip(b"=").decode()


def checkout_hash(checkout_jwt):
    """Return AP2's default SHA-256 identifier for a serialized checkout JWT."""
    if not isinstance(checkout_jwt, str):
        raise ValueError("checkout_jwt must be a string")
    return b64(hashlib.sha256(checkout_jwt.encode()).digest())


def checkout_payload(checkout):
    """Return the JCS payload covered by a merchant checkout authorization."""
    if not isinstance(checkout, dict):
        raise ValueError("checkout must be an object")
    body = dict(checkout)
    body.pop("ap2", None)
    return jcs_canonicalize(body)


def build_checkout_jwt(checkout):
    """Attach the checkout's Appendix F merchant signature to its JCS payload."""
    try:
        authorization = checkout["ap2"]["merchant_authorization"]
        header, empty, signature = authorization.split(".")
        if empty or not header or not signature:
            raise ValueError
    except (AttributeError, KeyError, ValueError):
        raise ValueError("checkout requires a detached merchant authorization")
    return header + "." + b64(checkout_payload(checkout)) + "." + signature


def verify_checkout_jwt(token, merchant_jwk):
    """Verify an attached ES256 checkout JWS against the merchant public JWK."""
    try:
        header, payload, signature = token.split(".")
        decoded_header = json.loads(b64d(header))
        if (
            not isinstance(decoded_header, dict)
            or decoded_header.get("alg") != "ES256"
            or (merchant_jwk.get("kid") and decoded_header.get("kid") != merchant_jwk["kid"])
        ):
            return False
        decoded_checkout = json.loads(b64d(payload))
        if (
            not isinstance(decoded_checkout, dict)
            or b64(checkout_payload(decoded_checkout)) != payload
        ):
            return False
        raw_signature = b64d(signature)
        if len(raw_signature) != 64:
            return False
        public = ec.EllipticCurvePublicNumbers(
            int.from_bytes(b64d(merchant_jwk["x"]), "big"),
            int.from_bytes(b64d(merchant_jwk["y"]), "big"),
            ec.SECP256R1(),
        ).public_key()
        public.verify(
            encode_dss_signature(
                int.from_bytes(raw_signature[:32], "big"), int.from_bytes(raw_signature[32:], "big")
            ),
            header.encode() + b"." + payload.encode(),
            ec.ECDSA(hashes.SHA256()),
        )
        return True
    except (AttributeError, InvalidSignature, KeyError, ValueError, json.JSONDecodeError):
        return False


def sign_trusted_surface_jws(claims, trusted_surface_key, kid="trusted-surface"):
    """Sign fully disclosed fixture mandate claims as a compact ES256 JWS."""
    header = b64(
        json.dumps({"alg": "ES256", "kid": kid, "typ": "JWT"}, separators=(",", ":")).encode()
    )
    payload = b64(jcs_canonicalize(claims))
    der = trusted_surface_key.sign((header + "." + payload).encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return header + "." + payload + "." + b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def verify_trusted_surface_jws(token, ts_jwk):
    """Return verified, fully disclosed JWS claims, or None on verification failure."""
    try:
        header, payload, signature = token.split(".")
        decoded_header = json.loads(b64d(header))
        claims = json.loads(b64d(payload))
        if (
            not isinstance(decoded_header, dict)
            or decoded_header.get("alg") != "ES256"
            or not isinstance(claims, dict)
            or b64(jcs_canonicalize(claims)) != payload
            or (ts_jwk.get("kid") and decoded_header.get("kid") != ts_jwk["kid"])
        ):
            return None
        raw_signature = b64d(signature)
        if len(raw_signature) != 64:
            return None
        public = ec.EllipticCurvePublicNumbers(
            int.from_bytes(b64d(ts_jwk["x"]), "big"),
            int.from_bytes(b64d(ts_jwk["y"]), "big"),
            ec.SECP256R1(),
        ).public_key()
        public.verify(
            encode_dss_signature(
                int.from_bytes(raw_signature[:32], "big"), int.from_bytes(raw_signature[32:], "big")
            ),
            (header + "." + payload).encode(),
            ec.ECDSA(hashes.SHA256()),
        )
        return claims
    except (
        AttributeError,
        InvalidSignature,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None


def mint_trusted_surface_mandates(checkout, trusted_surface_key, kid="trusted-surface"):
    """Mint fixture closed mandates for an already merchant-signed checkout."""
    checkout_jwt = build_checkout_jwt(checkout)
    checkout_identifier = checkout_hash(checkout_jwt)
    total = next(total for total in checkout["totals"] if total["type"] == "total")
    checkout_mandate = {
        "vct": "mandate.checkout.1",
        "checkout_jwt": checkout_jwt,
        "checkout_hash": checkout_identifier,
    }
    payment_mandate = {
        "vct": "mandate.payment.1",
        "transaction_id": checkout_identifier,
        "payment_amount": {"amount": total["amount"], "currency": checkout["currency"]},
    }
    return {
        "checkout_mandate": sign_trusted_surface_jws(checkout_mandate, trusted_surface_key, kid),
        "payment_mandate": sign_trusted_surface_jws(payment_mandate, trusted_surface_key, kid),
    }


def checkout_total(checkout):
    """Extract the single charge amount and currency from a UCP checkout."""
    try:
        total = next(item for item in checkout["totals"] if item["type"] == "total")
        amount = total["amount"]
        currency = checkout["currency"]
    except (KeyError, StopIteration, TypeError):
        raise Problem(422, "mandate_scope_mismatch", "Charged checkout has no total.")
    if isinstance(amount, bool) or not isinstance(amount, int) or not isinstance(currency, str):
        raise Problem(422, "mandate_scope_mismatch", "Charged checkout has an invalid total.")
    return amount, currency


def verify_mandate_pair(checkout, mandates, merchant_jwk, trusted_surface_jwk):
    """Verify every signature and immutable binding required to charge a checkout."""
    try:
        checkout_token = mandates["checkout_mandate"]
        payment_token = mandates["payment_mandate"]
    except (KeyError, TypeError):
        raise Problem(422, "mandate_required", "Both closed AP2 mandates are required.")
    checkout_mandate = verify_trusted_surface_jws(checkout_token, trusted_surface_jwk)
    if checkout_mandate is None:
        raise Problem(
            422,
            "mandate_invalid_signature",
            "Checkout Mandate trusted-surface signature is invalid.",
        )
    payment_mandate = verify_trusted_surface_jws(payment_token, trusted_surface_jwk)
    if payment_mandate is None:
        raise Problem(
            422,
            "mandate_invalid_signature",
            "Payment Mandate trusted-surface signature is invalid.",
        )
    if checkout_mandate.get("vct") != "mandate.checkout.1":
        raise Problem(422, "mandate_required", "A closed Checkout Mandate is required.")
    if payment_mandate.get("vct") != "mandate.payment.1":
        raise Problem(422, "mandate_required", "A closed Payment Mandate is required.")
    checkout_jwt = checkout_mandate.get("checkout_jwt")
    if not verify_checkout_jwt(checkout_jwt, merchant_jwk):
        raise Problem(
            422,
            "merchant_authorization_invalid",
            "Checkout Mandate checkout_jwt lacks a valid merchant signature.",
        )
    try:
        _, signed_payload, _ = checkout_jwt.split(".")
        charged_payload = b64(checkout_payload(checkout))
    except (AttributeError, ValueError):
        raise Problem(422, "mandate_scope_mismatch", "Charged checkout payload is invalid.")
    if not hmac.compare_digest(signed_payload, charged_payload):
        raise Problem(
            422,
            "mandate_scope_mismatch",
            "Checkout Mandate authorizes a different checkout.",
        )
    amount, currency = checkout_total(checkout)
    return validate_closed_mandate_bindings(checkout_mandate, payment_mandate, amount, currency)


def validate_closed_mandate_bindings(checkout_mandate, payment_mandate, amount, currency):
    """Validate AP2 closed-mandate bindings after credential verification.

    Signature, disclosure, expiry, and audience validation are deliberately owned by
    the trusted credential verifier. This function checks the immutable payment
    bindings that the merchant must use before initiating a charge.
    """
    if not isinstance(checkout_mandate, dict) or not isinstance(payment_mandate, dict):
        raise Problem(422, "mandate_required", "Closed AP2 mandates are required.")
    if checkout_mandate.get("vct") != "mandate.checkout.1":
        raise Problem(422, "mandate_required", "A closed Checkout Mandate is required.")
    if payment_mandate.get("vct") != "mandate.payment.1":
        raise Problem(422, "mandate_required", "A closed Payment Mandate is required.")
    checkout_jwt = checkout_mandate.get("checkout_jwt")
    if not isinstance(checkout_jwt, str) or not checkout_jwt:
        raise Problem(422, "mandate_required", "Checkout Mandate checkout_jwt is required.")
    expected_hash = checkout_hash(checkout_jwt)
    checkout_mandate_hash = checkout_mandate.get("checkout_hash")
    if not isinstance(checkout_mandate_hash, str) or not hmac.compare_digest(
        checkout_mandate_hash, expected_hash
    ):
        raise Problem(422, "mandate_required", "Checkout Mandate hash does not match checkout_jwt.")
    transaction_id = payment_mandate.get("transaction_id")
    if not isinstance(transaction_id, str) or not hmac.compare_digest(
        transaction_id, expected_hash
    ):
        raise Problem(422, "mandate_required", "Payment Mandate is not bound to checkout_jwt.")
    payment_amount = payment_mandate.get("payment_amount")
    if (
        not isinstance(payment_amount, dict)
        or isinstance(payment_amount.get("amount"), bool)
        or not isinstance(payment_amount.get("amount"), int)
        or payment_amount["amount"] != amount
        or payment_amount.get("currency") != currency
    ):
        raise Problem(422, "mandate_required", "Payment Mandate amount does not match checkout.")
    return expected_hash


def stamp(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


class Problem(Exception):
    def __init__(self, status, code, message, details=[]):
        self.status = status
        self.body = {
            "agentorder_version": VERSION,
            "error": {"code": code, "message": message, "details": details},
        }


def validate(kind, body):
    e = list(VALIDATORS[kind].iter_errors(body))
    if e:
        raise Problem(
            422,
            "invalid_request",
            "Request does not match AgentOrder v0.2 schema.",
            [{"code": x.message} for x in e[:5]],
        )


MIGRATIONS = [
    """CREATE TABLE IF NOT EXISTS printers(id TEXT PRIMARY KEY,name TEXT NOT NULL,config TEXT NOT NULL,signing_key TEXT NOT NULL);CREATE TABLE IF NOT EXISTS printer_capabilities(printer_id TEXT PRIMARY KEY,body TEXT NOT NULL);CREATE TABLE IF NOT EXISTS signing_keys(printer_id TEXT PRIMARY KEY,kid TEXT NOT NULL,public_jwk TEXT NOT NULL);CREATE TABLE IF NOT EXISTS rfqs(id TEXT NOT NULL,printer_id TEXT NOT NULL,platform TEXT NOT NULL,body TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(printer_id,platform,id));CREATE TABLE IF NOT EXISTS quotes(id TEXT PRIMARY KEY,printer_id TEXT NOT NULL,platform TEXT NOT NULL,rfq_id TEXT NOT NULL,body TEXT NOT NULL,status TEXT NOT NULL);CREATE TABLE IF NOT EXISTS idempotency_keys(printer_id TEXT NOT NULL,platform TEXT NOT NULL,key TEXT NOT NULL,fingerprint TEXT NOT NULL,response TEXT NOT NULL,PRIMARY KEY(printer_id,platform,key));CREATE TABLE IF NOT EXISTS nonces(platform TEXT NOT NULL,nonce TEXT NOT NULL,ts INTEGER NOT NULL,PRIMARY KEY(platform,nonce));""",
    """CREATE TABLE IF NOT EXISTS payments(checkout_hash TEXT PRIMARY KEY,printer_id TEXT NOT NULL,result TEXT NOT NULL);""",
]


class FakeStripeAdapter:
    """Deterministic local stand-in for a Stripe Connect payment adapter."""

    def create_payment(self, printer_account, amount_minor, currency, idempotency_key):
        fingerprint = hashlib.sha256(
            canon([printer_account, amount_minor, currency, idempotency_key]).encode()
        ).hexdigest()
        return {
            "id": "fakepay_" + fingerprint[:24],
            "status": "succeeded",
            "amount": amount_minor,
            "currency": currency,
            "printer_account": printer_account,
        }


class Store:
    def __init__(self, path, base, platforms, clock=time.time):
        self.path = str(path)
        self.base = base
        self.platforms = platforms
        self.clock = clock
        self.merchant_keys = {}
        with self.db() as d:
            for q in MIGRATIONS:
                d.executescript(q)

    def db(self):
        d = sqlite3.connect(self.path)
        d.row_factory = sqlite3.Row
        return d

    def seed(self, printer):
        if printer.get("private_key"):
            self.merchant_keys[printer["id"]] = printer["private_key"]
        with self.db() as d:
            d.execute(
                "INSERT OR REPLACE INTO printers VALUES(?,?,?,?)",
                (printer["id"], printer["name"], canon(printer["config"]), "local"),
            )
            d.execute(
                "INSERT OR REPLACE INTO printer_capabilities VALUES(?,?)",
                (printer["id"], canon(printer["config"])),
            )
            d.execute(
                "INSERT OR REPLACE INTO signing_keys VALUES(?,?,?)",
                (printer["id"], printer["kid"], canon(printer["jwk"])),
            )

    def printer(self, pid):
        with self.db() as d:
            r = d.execute("SELECT * FROM printers WHERE id=?", (pid,)).fetchone()
        if not r:
            raise Problem(404, "not_found", "Unknown printer.")
        return r

    def profile(self, pid):
        r = self.printer(pid)
        config = json.loads(r["config"])
        with self.db() as d:
            k = d.execute("SELECT * FROM signing_keys WHERE printer_id=?", (pid,)).fetchone()
        return {
            "ucp": {
                "version": "2026-06-15",
                "services": {
                    "dev.ucp.shopping": [
                        {
                            "version": "2026-06-15",
                            "spec": "https://ucp.dev/specification/",
                            "transport": "rest",
                            "schema": "https://ucp.dev/services/shopping/rest.openapi.json",
                            "endpoint": self.base + "/ucp/v1/printers/" + pid,
                        }
                    ]
                },
                "capabilities": {
                    "dev.ucp.shopping.checkout": [
                        {
                            "version": "2026-06-15",
                            "spec": "https://ucp.dev/specification/",
                            "schema": "https://ucp.dev/schemas/shopping/checkout.json",
                        }
                    ],
                    "dev.ucp.common.payment.ap2_mandate": [
                        {
                            "version": "2026-06-15",
                            "spec": "https://ucp.dev/specification/",
                            "schema": "https://ucp.dev/schemas/common/payment_ap2_mandate.json",
                            "extends": "dev.ucp.shopping.checkout",
                        }
                    ],
                    "org.agentorder.shopping.print_quote": [
                        {
                            "version": "2026-09-06",
                            "spec": "https://agentorder.org/specification/0.2",
                            "schema": "https://agentorder.org/schemas/0.2/schema.json",
                            "extends": ["dev.ucp.shopping.cart", "dev.ucp.shopping.checkout"],
                            "config": config,
                        }
                    ],
                },
                "payment_handlers": {},
            },
            "keys": [json.loads(k["public_jwk"])],
        }

    def verify(self, pid, headers, raw, method, path):
        platform = headers.get("UCP-Agent", "")
        profile = self.platforms.get(platform)
        if not profile:
            raise Problem(401, "invalid_signature", "Unknown platform profile.")
        active = profile["ucp"]["capabilities"].get("org.agentorder.shopping.print_quote", [])
        if not any(x["version"] == "2026-09-06" for x in active):
            raise Problem(
                403, "capability_not_negotiated", "AgentOrder capability was not negotiated."
            )
        try:
            kid, ts, nonce, sig = headers["X-AgentOrder-Signature"].split(":")
            ts = int(ts)
        except Exception:
            raise Problem(401, "invalid_signature", "Malformed request signature.")
        if abs(self.clock() - ts) > 300:
            raise Problem(401, "invalid_signature", "Stale request signature.")
        key = next((x for x in profile["keys"] if x.get("kid") == kid), None)
        if not key:
            raise Problem(401, "invalid_signature", "Unknown signing key.")
        try:
            pub = ec.EllipticCurvePublicNumbers(
                int.from_bytes(b64d(key["x"]), "big"),
                int.from_bytes(b64d(key["y"]), "big"),
                ec.SECP256R1(),
            ).public_key()
            raw_sig = b64d(sig)
            pub.verify(
                encode_dss_signature(
                    int.from_bytes(raw_sig[:32], "big"), int.from_bytes(raw_sig[32:], "big")
                ),
                f"{method}.{path}.{ts}.{nonce}.".encode() + raw,
                ec.ECDSA(hashes.SHA256()),
            )
        except (InvalidSignature, ValueError):
            raise Problem(401, "invalid_signature", "Invalid request signature.")
        return platform, nonce, ts

    def rfq(self, pid, platform, nonce, ts, key, body):
        if not key or not 8 <= len(key) <= 128:
            raise Problem(400, "invalid_request", "Idempotency-Key required.")
        validate("rfq", body)
        r = self.printer(pid)
        config = json.loads(r["config"])
        if not config_allows(body["print_job"], config):
            raise Problem(
                422, "unsupported_print_job", "Print job is outside printer capability config."
            )
        fp = hashlib.sha256(canon(body).encode()).hexdigest()
        response = {"agentorder_version": VERSION, "rfq_id": body["rfq_id"], "status": "received"}
        with self.db() as d:
            d.execute("BEGIN IMMEDIATE")
            old = d.execute(
                "SELECT * FROM idempotency_keys WHERE printer_id=? AND platform=? AND key=?",
                (pid, platform, key),
            ).fetchone()
            if old:
                if old["fingerprint"] != fp:
                    raise Problem(409, "idempotency_conflict", "Key used with different body.")
                return json.loads(old["response"]), True
            try:
                d.execute("DELETE FROM nonces WHERE ts < ?", (int(self.clock()) - 300,))
                try:
                    d.execute("INSERT INTO nonces VALUES(?,?,?)", (platform, nonce, ts))
                except sqlite3.IntegrityError:
                    raise Problem(409, "idempotency_conflict", "Replayed request signature.")
                d.execute(
                    "INSERT INTO rfqs VALUES(?,?,?,?,?,?)",
                    (body["rfq_id"], pid, platform, canon(body), "open", self.clock()),
                )
            except sqlite3.IntegrityError:
                raise Problem(409, "idempotency_conflict", "Replayed RFQ identifier or signature.")
            d.execute(
                "INSERT INTO idempotency_keys VALUES(?,?,?,?,?)",
                (pid, platform, key, fp, canon(response)),
            )
        return response, False

    def get_quote(self, pid, platform, qid):
        with self.db() as d:
            r = d.execute("SELECT * FROM quotes WHERE id=?", (qid,)).fetchone()
        if not r:
            raise Problem(404, "not_found", "Quote not found.")
        if r["printer_id"] != pid or r["platform"] != platform:
            raise Problem(403, "forbidden", "Cross-tenant quote access denied.")
        return json.loads(r["body"])

    def build_checkout(self, pid, platform, qid):
        """Build and merchant-sign a UCP checkout from one live quote."""
        quote = self.get_quote(pid, platform, qid)
        if (
            datetime.fromisoformat(quote["expires_at"].replace("Z", "+00:00")).timestamp()
            <= self.clock()
        ):
            raise Problem(410, "quote_expired", "Quote has expired.")
        key = self.merchant_keys.get(pid)
        if key is None:
            raise Problem(500, "invalid_request", "Merchant signing key unavailable.")
        with self.db() as d:
            rfq = d.execute(
                "SELECT body FROM rfqs WHERE printer_id=? AND platform=? AND id=?",
                (pid, platform, quote["rfq_id"]),
            ).fetchone()
        rfq_body = json.loads(rfq["body"])
        currency = json.loads(self.printer(pid)["config"])["business_cards"]["currency"]
        price = quote["quote_line_item"]["item"]["price"]
        checkout = {
            "ucp": {"version": "2026-06-15", "status": "success", "payment_handlers": {}},
            "id": "checkout:" + qid,
            "line_items": [quote["quote_line_item"]],
            "buyer": rfq_body["buyer"],
            "fulfillment_destination": rfq_body["fulfillment_destination"],
            "status": "ready_for_complete",
            "currency": currency,
            "totals": [{"type": "subtotal", "amount": price}, {"type": "total", "amount": price}],
            "links": [],
        }
        # Detached JWS still base64url-encodes the JCS payload in the Appendix F signing input.
        payload = b64(jcs_canonicalize(checkout)).encode()
        header = b64(
            json.dumps(
                {"alg": "ES256", "kid": self.printer(pid)["id"]}, separators=(",", ":")
            ).encode()
        ).encode()
        der = key.sign(header + b"." + payload, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        checkout["ap2"] = {
            "merchant_authorization": header.decode()
            + ".."
            + b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        }
        return checkout

    def merchant_jwk(self, pid):
        self.printer(pid)
        with self.db() as d:
            key = d.execute(
                "SELECT public_jwk FROM signing_keys WHERE printer_id=?", (pid,)
            ).fetchone()
        if not key:
            raise Problem(500, "invalid_request", "Merchant signing key unavailable.")
        return json.loads(key["public_jwk"])

    def charge_quote(self, pid, platform, qid, mandates, trusted_surface_jwk, adapter):
        """Charge a quote only after the AP2 mandate pair authorizes its checkout."""
        checkout = self.build_checkout(pid, platform, qid)
        checkout_identifier = verify_mandate_pair(
            checkout, mandates, self.merchant_jwk(pid), trusted_surface_jwk
        )
        amount, currency = checkout_total(checkout)
        with self.db() as d:
            d.execute("BEGIN IMMEDIATE")
            recorded = d.execute(
                "SELECT result FROM payments WHERE checkout_hash=?", (checkout_identifier,)
            ).fetchone()
            if recorded:
                return json.loads(recorded["result"])
            result = adapter.create_payment(pid, amount, currency, checkout_identifier)
            d.execute(
                "INSERT INTO payments VALUES(?,?,?)", (checkout_identifier, pid, canon(result))
            )
        return result


def verify_merchant_authorization(checkout, jwk):
    """Verify the detached Appendix F merchant authorization for a checkout."""
    body = dict(checkout)
    authorization = body.pop("ap2")["merchant_authorization"]
    header, empty, signature = authorization.split(".")
    if empty:
        return False
    public = ec.EllipticCurvePublicNumbers(
        int.from_bytes(b64d(jwk["x"]), "big"), int.from_bytes(b64d(jwk["y"]), "big"), ec.SECP256R1()
    ).public_key()
    raw_signature = b64d(signature)
    try:
        public.verify(
            encode_dss_signature(
                int.from_bytes(raw_signature[:32], "big"), int.from_bytes(raw_signature[32:], "big")
            ),
            header.encode() + b"." + b64(jcs_canonicalize(body)).encode(),
            ec.ECDSA(hashes.SHA256()),
        )
        return True
    except (InvalidSignature, ValueError):
        return False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, status, body, extra={}):
        raw = canon(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        [self.send_header(k, v) for k, v in extra.items()]
        self.end_headers()
        self.wfile.write(raw)

    def read(self):
        if self.headers.get_content_type() != "application/json":
            raise Problem(400, "invalid_request", "JSON required.")
        try:
            length = int(self.headers["Content-Length"])
        except Exception:
            raise Problem(400, "invalid_request", "Content-Length required.")
        if length < 0:
            raise Problem(400, "invalid_request", "Invalid Content-Length.")
        if length > 65536:
            raise Problem(413, "invalid_request", "Request body exceeds 64 KiB.")
        return self.rfile.read(length)

    def handle_request(self):
        try:
            p = urlsplit(self.path).path
            parts = p.split("/")
            store = self.server.store
            if (
                self.command == "GET"
                and len(parts) == 6
                and parts[1:4] == ["ucp", "v1", "printers"]
                and parts[5] == ".well-known"
            ):
                return self.reply(200, store.profile(parts[4]))
            if (
                self.command == "GET"
                and len(parts) == 6
                and parts[1:4] == ["ucp", "v1", "printers"]
                and parts[5].startswith("quotes-")
            ):
                return self.reply(
                    200,
                    store.get_quote(
                        parts[4],
                        store.verify(parts[4], self.headers, b"", self.command, p)[0],
                        parts[5][7:],
                    ),
                )
            if (
                self.command == "POST"
                and len(parts) == 6
                and parts[1:4] == ["ucp", "v1", "printers"]
                and parts[5] == "rfqs"
            ):
                raw = self.read()
                platform, nonce, ts = store.verify(parts[4], self.headers, raw, self.command, p)
                body = json.loads(raw)
                out, replay = store.rfq(
                    parts[4], platform, nonce, ts, self.headers.get("Idempotency-Key"), body
                )
                return self.reply(
                    200 if replay else 202, out, {"Idempotency-Replayed": str(replay).lower()}
                )
            raise Problem(404, "not_found", "Endpoint not found.")
        except Problem as e:
            self.reply(e.status, e.body)
        except Exception:
            self.reply(
                500,
                {
                    "agentorder_version": VERSION,
                    "error": {
                        "code": "invalid_request",
                        "message": "Internal reference-server error.",
                        "details": [],
                    },
                },
            )

    do_GET = handle_request
    do_POST = handle_request


def make_server(port, db, platforms, printer, clock=time.time):
    s = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    s.store = Store(db, f"http://127.0.0.1:{s.server_port}", platforms, clock)
    s.store.seed(printer)
    return s
