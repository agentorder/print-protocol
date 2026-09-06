"""Stable display title for the UCP line item emitted from a print job."""
from __future__ import annotations

_SIZE_LABELS = {
    "us_3.5x2in": "US 3.5 × 2 in",
    "uk_eu_85x55mm": "UK/EU 85 × 55 mm",
    "au_nz_90x55mm": "AU/NZ 90 × 55 mm",
}


def render_title(print_job: dict) -> str:
    """Render a deterministic, non-authoritative display title for one job."""
    return (
        f"{print_job['quantity']} business cards — "
        f"{_SIZE_LABELS[print_job['finished_size']]}, "
        f"{print_job['sides']}-sided, {print_job['stock']['weight_gsm']} gsm "
        f"{print_job['stock']['finish']}"
    )
