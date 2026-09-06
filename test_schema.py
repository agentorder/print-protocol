"""Contract tests for the canonical AgentOrder v0.2 schema and fixtures."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import unittest

from jsonschema import Draft202012Validator, FormatChecker

from agentorder_title import render_title
from schema_support import config_allows, require_config_allowed, ucp_registry

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

    def test_config_enforcement(self) -> None:
        self.assertTrue(config_allows(RFQ["print_job"], EXAMPLES["capability_config"]))
        disallowed = copy.deepcopy(RFQ["print_job"])
        disallowed["finished_size"] = "us_3.5x2in"
        self.assertFalse(config_allows(disallowed, EXAMPLES["capability_config"]))
        with self.assertRaises(ValueError):
            require_config_allowed(disallowed, EXAMPLES["capability_config"])

    def test_unknown_fields_fail_everywhere(self) -> None:
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
