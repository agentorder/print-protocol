"""Contract tests for the canonical AgentOrder v0.2 schema and fixtures."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import unittest

from jsonschema import Draft202012Validator, FormatChecker

from agentorder_title import render_title
from schema_support import config_allows, require_config_allowed, ucp_registry, validate_quote
from reference_agent import validate_received_quote

ROOT = Path(__file__).resolve().parent
SCHEMA = json.loads((ROOT / "schema.json").read_text(encoding="utf-8"))
EXAMPLES = json.loads((ROOT / "examples.json").read_text(encoding="utf-8"))
RFQ = json.loads((ROOT / "example-rfq.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, registry=ucp_registry(), format_checker=FormatChecker())


def validate_definition(name: str, instance: object) -> None:
    validator = Draft202012Validator(
        {"$ref": f"#/$defs/{name}", "$defs": SCHEMA["$defs"]},
        registry=ucp_registry(),
        format_checker=FormatChecker(),
    )
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.path))
    if errors:
        raise AssertionError("; ".join(error.message for error in errors))


class SchemaTest(unittest.TestCase):
    def test_schema_is_valid(self) -> None:
        Draft202012Validator.check_schema(SCHEMA)

    def test_all_agentorder_fixtures_validate(self) -> None:
        validate_definition("capability_config", EXAMPLES["capability_config"])
        validate_definition("rfq", RFQ)
        for name in ("quoted_quote", "declined_quote"):
            validate_definition("quote", EXAMPLES[name])
        validate_definition("status", EXAMPLES["status"])
        validate_definition("error", EXAMPLES["error"])

    def test_quoted_quote_needs_line_item_but_declined_quote_does_not(self) -> None:
        quoted = copy.deepcopy(EXAMPLES["quoted_quote"])
        del quoted["quote_line_item"]
        with self.assertRaises(AssertionError):
            validate_definition("quote", quoted)
        declined = copy.deepcopy(EXAMPLES["declined_quote"])
        self.assertNotIn("quote_line_item", declined)
        validate_definition("quote", declined)

    def test_object_shaped_ucp_prices_are_rejected(self) -> None:
        quote = copy.deepcopy(EXAMPLES["quoted_quote"])
        quote["quote_line_item"]["item"]["price"] = {"amount": 10900, "currency": "NZD"}
        with self.assertRaises(AssertionError):
            validate_definition("quote", quote)

    def test_quote_line_item_is_one_job_with_total(self) -> None:
        quantity_changed = copy.deepcopy(EXAMPLES["quoted_quote"])
        quantity_changed["quote_line_item"]["quantity"] = 500
        with self.assertRaises(AssertionError):
            validate_definition("quote", quantity_changed)
        no_total = copy.deepcopy(EXAMPLES["quoted_quote"])
        no_total["quote_line_item"]["totals"] = [{"type": "subtotal", "amount": 10900}]
        with self.assertRaises(AssertionError):
            validate_definition("quote", no_total)

    def test_declined_quote_forbids_quoted_fields(self) -> None:
        declined = copy.deepcopy(EXAMPLES["declined_quote"])
        declined["quote_line_item"] = copy.deepcopy(EXAMPLES["quoted_quote"])["quote_line_item"]
        with self.assertRaises(AssertionError):
            validate_definition("quote", declined)

    def test_config_enforcement(self) -> None:
        self.assertTrue(config_allows(RFQ["print_job"], EXAMPLES["capability_config"]))
        disallowed = copy.deepcopy(RFQ["print_job"])
        disallowed["finished_size"] = "us_3.5x2in"
        self.assertFalse(config_allows(disallowed, EXAMPLES["capability_config"]))
        with self.assertRaises(ValueError):
            require_config_allowed(disallowed, EXAMPLES["capability_config"])

    def test_unknown_fields_fail_in_agentorder_objects(self) -> None:
        # Vendored UCP Buyer and Fulfillment Destination permit additional fields.
        # The server must not forward unrecognised buyer/destination fields into checkout.
        cases = {
            "capability_config": EXAMPLES["capability_config"],
            "rfq": RFQ,
            "quote": EXAMPLES["quoted_quote"],
            "status": EXAMPLES["status"],
            "error": EXAMPLES["error"],
        }
        for definition, value in cases.items():
            with self.subTest(definition=definition):
                changed = copy.deepcopy(value)
                changed["unexpected"] = True
                with self.assertRaises(AssertionError):
                    validate_definition(definition, changed)


    def test_quote_semantic_invariants(self) -> None:
        validate_quote(EXAMPLES["quoted_quote"])
        validate_received_quote(EXAMPLES["quoted_quote"])
        mutations = {
            "item_id": lambda quote: quote["quote_line_item"]["item"].__setitem__("id", "wrong"),
            "line_id": lambda quote: quote["quote_line_item"].__setitem__("id", "wrong"),
            "total_amount": lambda quote: quote["quote_line_item"]["totals"][0].__setitem__("amount", 1),
            "title": lambda quote: quote["quote_line_item"]["item"].__setitem__("title", "wrong"),
        }
        for name, mutate in mutations.items():
            with self.subTest(invariant=name):
                changed = copy.deepcopy(EXAMPLES["quoted_quote"])
                mutate(changed)
                with self.assertRaises(ValueError):
                    validate_quote(changed)
                with self.assertRaises(ValueError):
                    validate_received_quote(changed)

    def test_title_is_deterministic_and_matches_line_item(self) -> None:
        expected = EXAMPLES["quoted_quote"]["quote_line_item"]["item"]["title"]
        self.assertEqual(render_title(RFQ["print_job"]), expected)
        self.assertEqual(render_title(copy.deepcopy(RFQ["print_job"])), expected)

    def test_spec_schema_blocks_match_canonical_definitions(self) -> None:
        text = (ROOT / "SPECIFICATION.md").read_text(encoding="utf-8")
        pattern = re.compile(r"<!-- agentorder-schema:([a-z_]+) -->\n```json\n(.*?)\n```", re.S)
        found = dict(pattern.findall(text))
        expected = {name: SCHEMA["$defs"][name] for name in ("capability_config", "rfq", "quote", "status", "error")}
        self.assertEqual(set(found), set(expected))
        for name, block in found.items():
            self.assertEqual(
                json.dumps(json.loads(block), sort_keys=True, separators=(",", ":")).encode(),
                json.dumps(expected[name], sort_keys=True, separators=(",", ":")).encode(),
                name,
            )


if __name__ == "__main__":
    unittest.main()
