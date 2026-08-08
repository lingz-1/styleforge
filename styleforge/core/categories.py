"""Canonical wardrobe slots for the Polyvore category vocabulary."""

from __future__ import annotations


TYPE_TO_SLOT = {
    "top": "top",
    "pants": "bottom",
    "shorts": "bottom",
    "skirt": "bottom",
    "dress": "one_piece",
    "jumpsuit": "one_piece",
    "suit": "one_piece",
    "outfit_set": "one_piece",
    "outwear": "outerwear",
    "shoes": "footwear",
    "bag": "bag",
    "belts": "accessory",
    "jewellery": "accessory",
    "eyewear": "accessory",
    "earrings": "accessory",
    "bracelet": "accessory",
    "necklace": "accessory",
    "rings": "accessory",
    "hats": "accessory",
    "watches": "accessory",
    "neckwear": "accessory",
    "brooch": "accessory",
    "hairwear": "accessory",
    "gloves": "accessory",
    "legwear": "accessory",
    "swimwear": "swimwear",
    "swim_bottom": "swim_bottom",
    "swim_coverup": "swim_coverup",
    "skiwear": "skiwear",
    "base_layer_top": "base_layer_top",
    "base_layer_bottom": "base_layer_bottom",
    "activewear_bra": "activewear_bra",
    "sleepwear": "sleepwear",
    "underwear": "underwear",
    "bathwear": "bathwear",
}


def infer_slot(item_type: str) -> str:
    return TYPE_TO_SLOT.get(item_type.strip().lower(), "other")
