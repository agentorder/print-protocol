"""Reference-agent quote intake validation shared by MCP/A2A transports later."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from schema_support import ucp_registry, validate_quote

ROOT = Path(__file__).resolve().parent
SCHEMA = json.loads((ROOT / "schema.json").read_text(encoding="utf-8"))
QUOTE_VALIDATOR = Draft202012Validator(
    {"$ref": "#/$defs/quote", "$defs": SCHEMA["$defs"]},
    registry=ucp_registry(),
    format_checker=FormatChecker(),
)


def validate_received_quote(quote: dict) -> None:
    """Validate every received quote before the reference agent uses it."""
    errors = sorted(QUOTE_VALIDATOR.iter_errors(quote), key=lambda error: list(error.path))
    if errors:
        raise ValueError("invalid quote: " + "; ".join(error.message for error in errors))
    validate_quote(quote)
