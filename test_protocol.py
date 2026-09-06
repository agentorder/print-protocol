from __future__ import annotations
import base64, json, secrets, tempfile, threading, time, unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from server import make_server
from jsonschema import Draft202012Validator
from schema_support import ucp_registry


def b64(v):
    return base64.urlsafe_b64encode(v).rstrip(b"=").decode()


KEY = ec.generate_private_key(ec.SECP256R1())
PUB = KEY.public_key().public_numbers()
JWK = {
    "kid": "platform-key",
    "kty": "EC",
    "crv": "P-256",
    "x": b64(PUB.x.to_bytes(32, "big")),
    "y": b64(PUB.y.to_bytes(32, "big")),
    "alg": "ES256",
}
CONFIG = {
    "business_cards": {
        "currency": "NZD",
        "finished_size_presets": ["au_nz_90x55mm"],
        "sides": [2],
        "stock_gsm": [350],
        "stock_finish": ["silk"],
        "quantity": {"minimum": 100, "maximum": 1000, "increment": 50},
        "finishing": ["none"],
    }
}
RFQ = {
    "agentorder_version": "0.2.0",
    "rfq_id": "rfq-001",
    "buyer": {"email": "buyer@example.invalid"},
    "fulfillment_destination": {
        "type": "shipping_address",
        "id": "dest-1",
        "address_country": "NZ",
    },
    "print_job": {
        "quantity": 500,
        "finished_size": "au_nz_90x55mm",
        "sides": 2,
        "colour": "CMYK",
        "stock": {"weight_gsm": 350, "finish": "silk"},
        "finishing": "none",
        "artwork": {"url": "https://files.example.invalid/a.pdf"},
    },
}


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.now = [1000]
        cls.platform = "https://agent.example.invalid/profile.json"
        platform = {
            "ucp": {
                "capabilities": {"org.agentorder.shopping.print_quote": [{"version": "2026-09-06"}]}
            },
            "keys": [JWK],
        }
        printer = {
            "id": "printer-a",
            "name": "A",
            "config": CONFIG,
            "kid": "printer-key",
            "jwk": JWK,
        }
        cls.app = make_server(
            0,
            Path(cls.tmp.name) / "d.sqlite",
            {cls.platform: platform},
            printer,
            clock=lambda: cls.now[0],
        )
        cls.thread = threading.Thread(target=cls.app.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = cls.app.store.base + "/ucp/v1/printers/printer-a"

    @classmethod
    def tearDownClass(cls):
        cls.app.shutdown()
        cls.app.server_close()
        cls.tmp.cleanup()

    def call(self, path, body=None, key="idem-0001", sig=True, platform=None, ts=None, nonce=None):
        raw = b"" if body is None else json.dumps(body, separators=(",", ":")).encode()
        headers = {}
        if body is not None:
            headers.update(
                {
                    "Content-Type": "application/json",
                    "Content-Length": str(len(raw)),
                    "Idempotency-Key": key,
                }
            )
        if sig:
            ts = self.now[0] if ts is None else ts
            nonce = nonce or secrets.token_urlsafe(12)
            der = KEY.sign(f"{ts}.{nonce}.".encode() + raw, ec.ECDSA(hashes.SHA256()))
            r, s = decode_dss_signature(der)
            headers.update(
                {
                    "UCP-Agent": platform or self.platform,
                    "X-AgentOrder-Signature": f'platform-key:{ts}:{nonce}:{b64(r.to_bytes(32,"big")+s.to_bytes(32,"big"))}',
                }
            )
        try:
            with urlopen(
                Request(
                    self.base + path,
                    data=raw if body is not None else None,
                    headers=headers,
                    method="POST" if body is not None else "GET",
                )
            ) as x:
                return x.status, json.loads(x.read())
        except HTTPError as e:
            return e.code, json.loads(e.read())

    def test_profile_and_rfq(self):
        status, p = self.call("/.well-known", sig=False)
        self.assertEqual(status, 200)
        self.assertEqual(
            list(
                Draft202012Validator(
                    {"$ref": "https://ucp.dev/schemas/ucp.json#/$defs/business_schema"},
                    registry=ucp_registry(),
                ).iter_errors(p["ucp"])
            ),
            [],
        )
        self.assertEqual(
            p["ucp"]["capabilities"]["org.agentorder.shopping.print_quote"][0]["config"], CONFIG
        )
        status, out = self.call("/rfqs", RFQ)
        self.assertEqual((status, out["status"]), (202, "received"))

    def test_negative_paths(self):
        tests = [
            (dict(RFQ, rfq_id="rfq-2"), {"ts": 0}, 401),
            (dict(RFQ, rfq_id="rfq-3"), {"sig": False}, 401),
            (dict(RFQ, rfq_id="rfq-4"), {"platform": "unknown"}, 401),
            (dict(RFQ, rfq_id="rfq-5", unexpected=True), {}, 422),
            (dict(RFQ, rfq_id="rfq-6", print_job=dict(RFQ["print_job"], quantity=101)), {}, 422),
        ]
        for body, kw, code in tests:
            with self.subTest(code=code):
                self.assertEqual(
                    self.call("/rfqs", body, key="key-" + body["rfq_id"], **kw)[0], code
                )

    def test_replay_and_tenant(self):
        body = dict(RFQ, rfq_id="rfq-replay")
        nonce = "unique-replay"
        self.assertEqual(self.call("/rfqs", body, key="same-key", nonce=nonce)[0], 202)
        self.assertEqual(self.call("/rfqs", body, key="same-key", nonce="other")[0], 200)
        changed = dict(body, rfq_id="changed")
        self.assertEqual(self.call("/rfqs", changed, key="same-key", nonce="third")[0], 409)
        self.assertEqual(self.call("/rfqs", body, key="new-key", nonce=nonce)[0], 409)


if __name__ == "__main__":
    unittest.main()
