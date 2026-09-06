"""3d-1 regressions: JCS encoder, checkout construction, Appendix F merchant signing."""

from __future__ import annotations

import base64
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ec

import server
from agentorder_title import render_title
from jcs import jcs_canonicalize
from schema_support import ucp_registry
from server import (
    Problem,
    b64,
    build_checkout_jwt,
    checkout_hash,
    make_server,
    validate_closed_mandate_bindings,
    verify_checkout_jwt,
    verify_merchant_authorization,
)
from jsonschema import Draft202012Validator


def _jwk(numbers):
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": b64(numbers.x.to_bytes(32, "big")),
        "y": b64(numbers.y.to_bytes(32, "big")),
        "alg": "ES256",
    }


CONFIG = {
    "business_cards": {
        "currency": "NZD",
        "finished_size_presets": ["au_nz_90x55mm"],
        "sides": [2],
        "stock_gsm": [350],
        "stock_finish": ["silk"],
        "quantity": {"minimum": 100, "maximum": 5000, "increment": 50},
        "finishing": ["none"],
    }
}
JOB = {
    "quantity": 500,
    "finished_size": "au_nz_90x55mm",
    "sides": 2,
    "colour": "CMYK",
    "stock": {"weight_gsm": 350, "finish": "silk"},
    "finishing": "none",
    "artwork": {"url": "https://files.example.invalid/a.pdf"},
}
PLATFORM = "https://agent.example.invalid/p"


class JCSTest(unittest.TestCase):
    def test_object_keys_sorted_by_utf16_codeunit(self):
        self.assertEqual(jcs_canonicalize({"b": 1, "a": 2}), b'{"a":2,"b":1}')

    def test_integers_and_bools_and_null(self):
        self.assertEqual(jcs_canonicalize([1, True, False, None]), b"[1,true,false,null]")

    def test_string_escaping_matches_rfc8785(self):
        self.assertEqual(jcs_canonicalize('a"\\\n\t'), b'"a\\"\\\\\\n\\t"')

    def test_non_ascii_emitted_literally(self):
        self.assertEqual(jcs_canonicalize("é"), '"é"'.encode("utf-8"))

    def test_float_is_rejected(self):
        with self.assertRaises(ValueError):
            jcs_canonicalize({"amount": 1.5})

    def test_non_finite_is_rejected(self):
        with self.assertRaises(ValueError):
            jcs_canonicalize(float("inf"))


class CheckoutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = [1_760_000_000.0]
        self.merchant = ec.generate_private_key(ec.SECP256R1())
        self.merchant_jwk = _jwk(self.merchant.public_key().public_numbers())
        self.merchant_jwk["kid"] = "printer-a"
        agent = ec.generate_private_key(ec.SECP256R1())
        self.agent_jwk = _jwk(agent.public_key().public_numbers())
        self.agent_jwk["kid"] = "ka"
        platforms = {
            PLATFORM: {
                "ucp": {
                    "capabilities": {
                        "org.agentorder.shopping.print_quote": [{"version": "2026-09-06"}]
                    }
                },
                "keys": [self.agent_jwk],
            }
        }
        printer = {
            "id": "printer-a",
            "name": "A",
            "config": CONFIG,
            "kid": "printer-a",
            "jwk": self.merchant_jwk,
            "private_key": self.merchant,
        }
        self.db = Path(self.tmp.name) / "d.sqlite"
        self.server = make_server(0, self.db, platforms, printer, clock=lambda: self.now[0])
        self.store = self.server.store
        self._seed_quote("q1", "r1", "2099-01-01T00:00:00Z")

    def _seed_quote(self, qid, rfq_id, expires_at):
        price = 10900
        quote = {
            "agentorder_version": "0.2.0",
            "quote_id": qid,
            "rfq_id": rfq_id,
            "status": "quoted",
            "expires_at": expires_at,
            "print_job": JOB,
            "lead_time": {"business_days": 3, "starts_after": "artwork_accepted"},
            "review": {"url": "https://review.example.invalid/" + qid},
            "quote_line_item": {
                "id": "quote-line:" + qid,
                "item": {"id": "quote:" + qid, "title": render_title(JOB), "price": price},
                "quantity": 1,
                "totals": [
                    {"type": "subtotal", "amount": price},
                    {"type": "total", "amount": price},
                ],
            },
        }
        rfq = {
            "agentorder_version": "0.2.0",
            "rfq_id": rfq_id,
            "buyer": {"email": "buyer@example.invalid"},
            "fulfillment_destination": {
                "type": "shipping_address",
                "id": "d1",
                "address_country": "NZ",
            },
            "print_job": JOB,
        }
        connection = sqlite3.connect(self.db)
        connection.execute(
            "INSERT INTO rfqs VALUES(?,?,?,?,?,?)",
            (rfq_id, "printer-a", PLATFORM, json.dumps(rfq), "open", self.now[0]),
        )
        connection.execute(
            "INSERT INTO quotes VALUES(?,?,?,?,?,?)",
            (qid, "printer-a", PLATFORM, rfq_id, json.dumps(quote), "quoted"),
        )
        connection.commit()
        connection.close()

    def test_checkout_validates_against_vendored_schema(self):
        checkout = self.store.build_checkout("printer-a", PLATFORM, "q1")
        validator = Draft202012Validator(
            {"$ref": "https://ucp.dev/schemas/shopping/checkout.json"}, registry=ucp_registry()
        )
        self.assertEqual(list(validator.iter_errors(checkout)), [])

    def test_merchant_signature_verifies_with_tenant_jwk(self):
        checkout = self.store.build_checkout("printer-a", PLATFORM, "q1")
        self.assertTrue(verify_merchant_authorization(checkout, self.merchant_jwk))

    def test_detached_format_has_empty_payload_segment(self):
        checkout = self.store.build_checkout("printer-a", PLATFORM, "q1")
        self.assertIn("..", checkout["ap2"]["merchant_authorization"])

    def test_one_byte_tamper_fails_verification(self):
        checkout = self.store.build_checkout("printer-a", PLATFORM, "q1")
        for mutate in (
            lambda c: c["totals"][1].__setitem__("amount", 10901),
            lambda c: c.__setitem__("currency", "USD"),
            lambda c: c["line_items"][0]["item"].__setitem__("price", 1),
        ):
            tampered = copy.deepcopy(checkout)
            mutate(tampered)
            self.assertFalse(verify_merchant_authorization(tampered, self.merchant_jwk))

    def test_wrong_key_fails_verification(self):
        checkout = self.store.build_checkout("printer-a", PLATFORM, "q1")
        self.assertFalse(verify_merchant_authorization(checkout, self.agent_jwk))

    def test_attached_checkout_jwt_verifies_with_merchant_key(self):
        checkout = self.store.build_checkout("printer-a", PLATFORM, "q1")
        token = build_checkout_jwt(checkout)
        self.assertEqual(token.count("."), 2)
        self.assertTrue(verify_checkout_jwt(token, self.merchant_jwk))

    def test_attached_checkout_jwt_rejects_payload_tampering_and_wrong_key(self):
        checkout = self.store.build_checkout("printer-a", PLATFORM, "q1")
        token = build_checkout_jwt(checkout)
        header, payload, signature = token.split(".")
        tampered_payload = b64(jcs_canonicalize({"not": "the signed checkout"}))
        self.assertFalse(verify_checkout_jwt(header + "." + tampered_payload + "." + signature, self.merchant_jwk))
        self.assertFalse(verify_checkout_jwt(token, self.agent_jwk))

    def test_checkout_from_expired_quote_is_refused(self):
        self._seed_quote("q2", "r2", "2020-01-01T00:00:00Z")
        with self.assertRaises(server.Problem) as caught:
            self.store.build_checkout("printer-a", PLATFORM, "q2")
        self.assertEqual(caught.exception.status, 410)
        self.assertEqual(caught.exception.body["error"]["code"], "quote_expired")


class ClosedMandateBindingsTest(unittest.TestCase):
    def setUp(self):
        self.checkout_jwt = "merchant.signed.checkout"
        self.checkout_hash = checkout_hash(self.checkout_jwt)
        self.checkout_mandate = {
            "vct": "mandate.checkout.1",
            "checkout_jwt": self.checkout_jwt,
            "checkout_hash": self.checkout_hash,
        }
        self.payment_mandate = {
            "vct": "mandate.payment.1",
            "transaction_id": self.checkout_hash,
            "payment_amount": {"amount": 10900, "currency": "NZD"},
        }

    def assert_rejected(self, checkout=None, payment=None):
        with self.assertRaises(Problem) as caught:
            validate_closed_mandate_bindings(
                checkout or self.checkout_mandate, payment or self.payment_mandate, 10900, "NZD"
            )
        self.assertEqual(caught.exception.status, 422)
        self.assertEqual(caught.exception.body["error"]["code"], "mandate_required")

    def test_accepts_closed_mandates_with_matching_bindings(self):
        self.assertEqual(
            validate_closed_mandate_bindings(
                self.checkout_mandate, self.payment_mandate, 10900, "NZD"
            ),
            self.checkout_hash,
        )

    def test_rejects_open_mandate_variants(self):
        checkout = copy.deepcopy(self.checkout_mandate)
        checkout["vct"] = "mandate.checkout.open.1"
        self.assert_rejected(checkout=checkout)
        payment = copy.deepcopy(self.payment_mandate)
        payment["vct"] = "mandate.payment.open.1"
        self.assert_rejected(payment=payment)

    def test_rejects_checkout_hash_or_transaction_binding_mismatch(self):
        checkout = copy.deepcopy(self.checkout_mandate)
        checkout["checkout_hash"] = "not-the-checkout-hash"
        self.assert_rejected(checkout=checkout)
        payment = copy.deepcopy(self.payment_mandate)
        payment["transaction_id"] = "another-checkout"
        self.assert_rejected(payment=payment)

    def test_rejects_payment_amount_or_currency_mismatch(self):
        payment = copy.deepcopy(self.payment_mandate)
        payment["payment_amount"]["amount"] = 10901
        self.assert_rejected(payment=payment)
        payment = copy.deepcopy(self.payment_mandate)
        payment["payment_amount"]["currency"] = "USD"
        self.assert_rejected(payment=payment)


if __name__ == "__main__":
    unittest.main()
