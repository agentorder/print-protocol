"""Restricted RFC 8785 JSON Canonicalization Scheme encoder."""

from __future__ import annotations

import json
import math


def jcs_canonicalize(value: object) -> bytes:
    """Return JCS bytes for JSON values without floats or non-finite numbers.

    Checkout signing only permits strings, integers, booleans, null, arrays, and
    objects. Floats are rejected because RFC 8785 number serialization is not
    implemented by this deliberately narrow dependency-free encoder.
    """

    def encode(item: object) -> str:
        if item is None:
            return "null"
        if item is True:
            return "true"
        if item is False:
            return "false"
        if isinstance(item, int):
            return str(item)
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("JCS does not permit non-finite numbers")
            raise ValueError("JCS float serialization is not supported")
        if isinstance(item, str):
            return json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        if isinstance(item, list):
            return "[" + ",".join(encode(child) for child in item) + "]"
        if isinstance(item, dict):
            if not all(isinstance(key, str) for key in item):
                raise ValueError("JCS object keys must be strings")
            keys = sorted(item, key=lambda key: key.encode("utf-16-be"))
            return "{" + ",".join(encode(key) + ":" + encode(item[key]) for key in keys) + "}"
        raise ValueError("JCS value is not JSON-compatible")

    return encode(value).encode("utf-8")
