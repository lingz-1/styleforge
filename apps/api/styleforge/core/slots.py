"""Shared slot normalization helpers."""

from __future__ import annotations


def base_slot(slot: str) -> str:
    """Map numbered repeatable slots back to their catalog slot."""
    if slot.startswith("accessory_") and slot.removeprefix("accessory_").isdigit():
        return "accessory"
    return slot
