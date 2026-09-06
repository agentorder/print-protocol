from __future__ import annotations
import asyncio, base64, json, secrets, tempfile, threading, time, unittest
from pathlib import Path
from unittest import mock
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
from urllib.error import HTTPError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from server import make_server

EXAMPLES = json.loads((Path(__file__).with_name("examples.json")).read_text())
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
        "artwork": {"url": "https://files.example.invalid/artwork/example-001.pdf"},
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
            {cls.platform: platform, "https://other.example.invalid/profile.json": platform},
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
        full_url = self.base + path
        signed_path = urlsplit(full_url).path
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
            der = KEY.sign(
                f"{'POST' if body is not None else 'GET'}.{signed_path}.{ts}.{nonce}.".encode()
                + raw,
                ec.ECDSA(hashes.SHA256()),
            )
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
        self.assertEqual(self.call("/rfqs", body, key="new-key-0001", nonce=nonce)[0], 409)

    def test_signed_quote_read_ownership(self):
        quote = EXAMPLES["quoted_quote"]
        with self.app.store.db() as db:
            db.execute(
                "INSERT INTO quotes VALUES(?,?,?,?,?,?)",
                (
                    quote["quote_id"],
                    "printer-a",
                    self.platform,
                    quote["rfq_id"],
                    json.dumps(quote),
                    "quoted",
                ),
            )
        self.assertEqual(self.call("/quotes-" + quote["quote_id"], sig=True)[0], 200)
        self.assertEqual(
            self.call(
                "/quotes-" + quote["quote_id"],
                sig=True,
                platform="https://other.example.invalid/profile.json",
            )[0],
            403,
        )

    def test_known_platform_without_capability_is_rejected(self):
        self.app.store.platforms["https://no-cap.example.invalid/profile.json"] = {
            "ucp": {"capabilities": {}},
            "keys": [JWK],
        }
        self.assertEqual(
            self.call(
                "/rfqs",
                dict(RFQ, rfq_id="rfq-no-cap"),
                platform="https://no-cap.example.invalid/profile.json",
                key="no-cap-key",
            )[0],
            403,
        )

    def test_idempotency_key_length_bounds(self):
        self.assertEqual(self.call("/rfqs", dict(RFQ, rfq_id="rfq-short"), key="short-7")[0], 400)
        self.assertEqual(self.call("/rfqs", dict(RFQ, rfq_id="rfq-long"), key="x" * 129)[0], 400)

    def test_path_binding_and_body_limit(self):
        body = dict(RFQ, rfq_id="rfq-path")
        self.assertEqual(self.call("/rfqs", body, key="path-key", nonce="path-nonce")[0], 202)
        # A relative-route signature is not a signature for the dispatched full path.
        raw = json.dumps(body, separators=(",", ":")).encode()
        ts, nonce = self.now[0], "relative-route"
        der = KEY.sign(f"POST./rfqs.{ts}.{nonce}.".encode() + raw, ec.ECDSA(hashes.SHA256()))
        r, sig = decode_dss_signature(der)
        headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(raw)),
            "Idempotency-Key": "relative-key",
            "UCP-Agent": self.platform,
            "X-AgentOrder-Signature": f"platform-key:{ts}:{nonce}:{b64(r.to_bytes(32, 'big') + sig.to_bytes(32, 'big'))}",
        }
        try:
            urlopen(Request(self.base + "/rfqs", data=raw, headers=headers, method="POST"))
        except HTTPError as error:
            self.assertEqual(error.code, 401)
        oversized = b"x" * 65537
        try:
            urlopen(
                Request(
                    self.base + "/rfqs",
                    data=oversized,
                    headers={"Content-Type": "application/json", "Content-Length": "65537"},
                    method="POST",
                )
            )
        except HTTPError as error:
            self.assertEqual(error.code, 413)


if __name__ == "__main__":
    unittest.main()


class AgentEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Tests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        Tests.tearDownClass()

    def test_agent_receives_and_validates_quote(self):
        from reference_agent import Agent

        app = Tests.app
        agent = Agent(
            "https://agent.example.invalid/profile.json",
            clock=lambda: Tests.now[0],
            allow_insecure=True,
        )
        app.store.platforms[agent.profile_url] = agent.platform_profile()
        job = RFQ["print_job"]
        rfq, status, response = agent.request_quote(
            Tests.base, job, RFQ["buyer"], RFQ["fulfillment_destination"], "agent-e2e-001"
        )
        self.assertEqual(status, 202)
        self.assertEqual(response["currency"], "NZD")
        quote = json.loads((Path(__file__).with_name("examples.json")).read_text())["quoted_quote"]
        quote["rfq_id"] = rfq["rfq_id"]
        with app.store.db() as db:
            db.execute(
                "INSERT INTO quotes VALUES(?,?,?,?,?,?)",
                (
                    quote["quote_id"],
                    "printer-a",
                    agent.profile_url,
                    quote["rfq_id"],
                    json.dumps(quote),
                    "quoted",
                ),
            )
        received_quote = agent.get_quote(Tests.base, quote["quote_id"])
        self.assertEqual(received_quote["quote_id"], quote["quote_id"])
        self.assertEqual(received_quote["currency"], "NZD")
        quote["quote_line_item"]["item"]["title"] = "tampered"
        with app.store.db() as db:
            db.execute(
                "UPDATE quotes SET body=? WHERE id=?", (json.dumps(quote), quote["quote_id"])
            )
        with self.assertRaises(ValueError):
            agent.get_quote(Tests.base, quote["quote_id"])

    def test_agent_rejects_expired_quote(self):
        from reference_agent import Agent

        app = Tests.app
        agent = Agent(
            "https://agent.example.invalid/profile.json",
            clock=lambda: Tests.now[0],
            allow_insecure=True,
        )
        app.store.platforms[agent.profile_url] = agent.platform_profile()
        rfq, status, _ = agent.request_quote(
            Tests.base,
            RFQ["print_job"],
            RFQ["buyer"],
            RFQ["fulfillment_destination"],
            "agent-expired-001",
        )
        self.assertEqual(status, 202)
        quote = json.loads((Path(__file__).with_name("examples.json")).read_text())["quoted_quote"]
        quote["quote_id"] = "quote-expired-001"
        quote["rfq_id"] = rfq["rfq_id"]
        quote["expires_at"] = "1970-01-01T00:00:00Z"
        with app.store.db() as db:
            db.execute(
                "INSERT INTO quotes VALUES(?,?,?,?,?,?)",
                (
                    quote["quote_id"],
                    "printer-a",
                    agent.profile_url,
                    quote["rfq_id"],
                    json.dumps(quote),
                    "quoted",
                ),
            )
        with self.assertRaisesRegex(ValueError, "^quote_expired$"):
            agent.get_quote(Tests.base, quote["quote_id"])


class DiscoveryHardening(unittest.TestCase):
    def test_http_without_allow_insecure_is_rejected(self):
        from reference_agent import Agent

        with self.assertRaisesRegex(ValueError, "^invalid_profile$"):
            Agent("https://agent.example.invalid/profile.json").discover_printer(
                "http://127.0.0.1:8787/.well-known"
            )

    def test_oversized_profile_is_rejected(self):
        from reference_agent import Agent, PROFILE_MAX_BYTES, PROFILE_TIMEOUT_SECONDS

        with mock.patch("reference_agent.urlopen") as urlopen_mock:
            urlopen_mock.return_value.__enter__.return_value.geturl.return_value = (
                "https://printer.example.invalid/.well-known/ucp"
            )
            urlopen_mock.return_value.__enter__.return_value.read.return_value = b"x" * (
                PROFILE_MAX_BYTES + 1
            )
            with self.assertRaisesRegex(ValueError, "^invalid_profile$"):
                Agent("https://agent.example.invalid/profile.json").discover_printer(
                    "https://printer.example.invalid/.well-known/ucp"
                )
        urlopen_mock.assert_called_once_with(
            "https://printer.example.invalid/.well-known/ucp",
            timeout=PROFILE_TIMEOUT_SECONDS,
        )

    def test_schema_invalid_profile_is_rejected(self):
        from reference_agent import Agent

        with mock.patch("reference_agent.urlopen") as urlopen_mock:
            urlopen_mock.return_value.__enter__.return_value.geturl.return_value = (
                "https://printer.example.invalid/.well-known/ucp"
            )
            urlopen_mock.return_value.__enter__.return_value.read.return_value = json.dumps(
                {"ucp": {"version": "not-a-version", "services": {}, "payment_handlers": {}}}
            ).encode()
            with self.assertRaisesRegex(ValueError, "^invalid_profile$"):
                Agent("https://agent.example.invalid/profile.json").discover_printer(
                    "https://printer.example.invalid/.well-known/ucp"
                )


class McpServer(unittest.TestCase):
    def test_tools_have_descriptions(self):
        from mcp_server import mcp

        tools = asyncio.run(mcp.list_tools())
        self.assertEqual(len(tools), 3)
        self.assertTrue(all(tool.description for tool in tools))
        descriptions = {tool.name: tool.description for tool in tools}
        self.assertIn("cannot approve, check out, or pay", descriptions["request_quote"])
        self.assertIn("currency and expiry", descriptions["get_quote"])
