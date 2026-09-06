"""Offline resolver for UCP schemas pinned in ucp.lock.json."""
from __future__ import annotations

import json
from pathlib import Path
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parent
LOCK = json.loads((ROOT / "ucp.lock.json").read_text(encoding="utf-8"))
VENDOR_ROOT = ROOT / "vendor" / "ucp" / LOCK["commit"] / "source" / "schemas"


def ucp_registry() -> Registry:
    registry = Registry()
    for path in VENDOR_ROOT.rglob("*.json"):
        document = json.loads(path.read_text(encoding="utf-8"))
        resource = Resource.from_contents(document, default_specification=DRAFT202012)
        registry = registry.with_resource(document["$id"], resource)
    return registry


def config_allows(print_job: dict, config: dict) -> bool:
    """Return whether a print job fits one printer's declared v0.2 config."""
    cards = config["business_cards"]
    quantity = cards["quantity"]
    return (
        print_job["finished_size"] in cards["finished_size_presets"]
        and print_job["sides"] in cards["sides"]
        and print_job["stock"]["weight_gsm"] in cards["stock_gsm"]
        and print_job["stock"]["finish"] in cards["stock_finish"]
        and print_job["finishing"] in cards["finishing"]
        and quantity["minimum"] <= print_job["quantity"] <= quantity["maximum"]
        and (print_job["quantity"] - quantity["minimum"]) % quantity["increment"] == 0
    )


def require_config_allowed(print_job: dict, config: dict) -> None:
    if not config_allows(print_job, config):
        raise ValueError("print_job is outside the printer capability config")
