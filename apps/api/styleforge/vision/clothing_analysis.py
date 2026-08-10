"""Structured clothing-image analysis: prompt, vocabulary, and mapping.

The vision model returns a strict JSON object using the vocabulary below
(English tokens for structured fields, Chinese for the free-text description).
``map_ai_type_to_item_fields`` translates the model's garment ``type`` into the
project's canonical ``item_type``/``subtype`` (see ``core.categories`` and the
``SUBTYPE_RULES`` taxonomy), so a recognized photo drops straight into the
existing ``create_photo_item`` flow.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError, model_validator

# --- Controlled vocabularies (English tokens the model must output) ---

AI_TYPES = [
    "shirt", "blouse", "polo", "t-shirt", "tank-top", "crop-top", "top",
    "sweater", "cardigan", "hoodie", "vest", "jacket", "denim-jacket",
    "leather-jacket", "coat", "trench-coat", "blazer", "parka",
    "jeans", "pants", "trousers", "leggings", "shorts",
    "skirt", "mini-skirt", "midi-skirt", "maxi-skirt", "pencil-skirt",
    "pleated-skirt", "a-line-skirt", "dress", "mini-dress", "midi-dress",
    "maxi-dress", "bodycon-dress", "cocktail-dress", "evening-dress",
    "shirt-dress", "a-line-dress", "jumpsuit", "wide-leg-jumpsuit", "suit",
    "shoes", "sneakers", "boots", "ankle-boots", "sandals", "heels",
    "high-heels", "flats", "loafers", "oxfords", "pumps",
    "bag", "tote-bag", "shoulder-bag", "crossbody-bag", "satchel", "clutch",
    "backpack", "hat", "cap", "beanie", "fedora", "scarf", "shawl", "belt",
    "tie", "socks", "gloves", "underwear", "sleepwear", "swimwear",
    "activewear", "eyewear", "sunglasses", "necklace", "pendant-necklace",
    "earrings", "bracelet", "bangle", "ring", "watch", "other", "unknown",
]

COLORS = [
    "black", "white", "gray", "navy", "blue", "light-blue", "red", "burgundy",
    "pink", "green", "olive", "yellow", "orange", "purple", "brown", "tan",
    "beige", "cream", "gold", "silver",
]

PATTERNS = [
    "solid", "striped", "plaid", "checkered", "floral", "graphic", "geometric",
    "polka-dot", "camouflage", "animal-print", "paisley", "lace",
]

MATERIALS = [
    "cotton", "denim", "leather", "wool", "polyester", "silk", "linen", "knit",
    "fleece", "suede", "velvet", "nylon", "canvas", "cashmere", "chiffon",
    "satin", "tulle", "lace", "twill", "corduroy", "down", "wool-blend",
]

FORMALITY = [
    "very-casual", "casual", "smart-casual", "business-casual", "formal",
]

STYLES = [
    "casual", "classic", "sporty", "minimalist", "bohemian", "preppy",
    "streetwear", "elegant", "athletic", "vintage", "modern", "rugged",
    "chic", "romantic",
]

SEASONS = ["spring", "summer", "fall", "winter", "all-season"]

FITS = ["slim", "regular", "relaxed", "oversized", "tailored", "cropped"]

OCCASIONS = [
    "daily", "work", "casual", "formal", "party", "outdoor", "sports",
    "travel", "ceremony", "date-night", "home", "school",
]

CULTURAL_ORIGINS = [
    "none", "hanfu", "qipao-cheongsam", "tangzhuang", "ma-mian-skirt",
    "ethnic-chinese", "kimono", "hanbok", "sari", "kilt", "bohemian",
    "poncho", "middle-eastern", "traditional-indian", "african",
    "native-american", "nordic", "victorian", "western-cowboy", "military",
    "preppy", "streetwear", "gothic", "punk", "vintage-retro",
]

SILHOUETTES = [
    "A-line", "fitted", "loose", "boxy", "bodycon", "straight", "flared",
    "tailored", "oversized",
]

NECKLINES = [
    "round", "v-neck", "square", "scoop", "off-shoulder", "boat", "halter",
    "high-neck", "sweetheart", "strapless",
]

COLLARS = [
    "none", "mandarin", "peter-pan", "pointed", "spread", "wing",
    "shirt-collar", "hooded",
]

SLEEVE_LENGTHS = [
    "sleeveless", "short", "elbow", "three-quarter", "long", "full-length",
]

LENGTHS = [
    "cropped", "waist-length", "hip-length", "knee-length", "midi", "maxi",
    "floor-length",
]

CONDITIONS = ["new", "like-new", "excellent", "good", "fair", "worn", "stained"]


# --- AI garment type -> (project item_type, subtype) mapping ---

_TYPE_MAP: dict[str, tuple[str, str]] = {
    "shirt": ("top", "shirt"),
    "blouse": ("top", "shirt"),
    "polo": ("top", "shirt"),
    "t-shirt": ("top", "t_shirt"),
    "tank-top": ("top", "tank_top"),
    "crop-top": ("top", "crop_top"),
    "top": ("top", ""),
    "sweater": ("top", "knitwear"),
    "cardigan": ("top", "knitwear"),
    "hoodie": ("top", "hoodie"),
    "vest": ("outwear", "vest"),
    "jacket": ("outwear", "jacket"),
    "denim-jacket": ("outwear", "denim_jacket"),
    "leather-jacket": ("outwear", "leather_jacket"),
    "coat": ("outwear", "coat"),
    "trench-coat": ("outwear", "trench_coat"),
    "blazer": ("outwear", "blazer"),
    "parka": ("outwear", "coat"),
    "jeans": ("pants", "jeans"),
    "pants": ("pants", ""),
    "trousers": ("pants", "tailored_trousers"),
    "leggings": ("pants", "leggings"),
    "shorts": ("shorts", "shorts"),
    "skirt": ("skirt", ""),
    "mini-skirt": ("skirt", "mini_skirt"),
    "midi-skirt": ("skirt", "midi_skirt"),
    "maxi-skirt": ("skirt", "maxi_skirt"),
    "pencil-skirt": ("skirt", "pencil_skirt"),
    "pleated-skirt": ("skirt", "pleated_skirt"),
    "a-line-skirt": ("skirt", "a_line_skirt"),
    "dress": ("dress", ""),
    "mini-dress": ("dress", "mini_dress"),
    "midi-dress": ("dress", "midi_dress"),
    "maxi-dress": ("dress", "maxi_dress"),
    "bodycon-dress": ("dress", "bodycon_dress"),
    "cocktail-dress": ("dress", "cocktail_dress"),
    "evening-dress": ("dress", "evening_dress"),
    "shirt-dress": ("dress", "shirt_dress"),
    "a-line-dress": ("dress", "a_line_dress"),
    "jumpsuit": ("jumpsuit", ""),
    "wide-leg-jumpsuit": ("jumpsuit", "wide_leg_jumpsuit"),
    "suit": ("suit", ""),
    "shoes": ("shoes", ""),
    "sneakers": ("shoes", "sneakers"),
    "boots": ("shoes", "boots"),
    "ankle-boots": ("shoes", "ankle_boots"),
    "sandals": ("shoes", "sandals"),
    "heels": ("shoes", "high_heels"),
    "high-heels": ("shoes", "high_heels"),
    "flats": ("shoes", "flats"),
    "loafers": ("shoes", "loafers"),
    "oxfords": ("shoes", "oxfords"),
    "pumps": ("shoes", "pumps"),
    "bag": ("bag", ""),
    "tote-bag": ("bag", "tote_bag"),
    "shoulder-bag": ("bag", "shoulder_bag"),
    "crossbody-bag": ("bag", "crossbody_bag"),
    "satchel": ("bag", "satchel"),
    "clutch": ("bag", "clutch"),
    "backpack": ("bag", "backpack"),
    "hat": ("hats", ""),
    "cap": ("hats", "cap"),
    "beanie": ("hats", "beanie"),
    "fedora": ("hats", "fedora"),
    "scarf": ("accessory", "scarf"),
    "shawl": ("accessory", "shawl"),
    "belt": ("belts", ""),
    "tie": ("accessory", ""),
    "socks": ("legwear", ""),
    "gloves": ("accessory", ""),
    "underwear": ("underwear", ""),
    "sleepwear": ("sleepwear", ""),
    "swimwear": ("swimwear", ""),
    "activewear": ("activewear_bra", ""),
    "eyewear": ("eyewear", "eyeglasses"),
    "sunglasses": ("eyewear", "sunglasses"),
    "necklace": ("necklace", ""),
    "pendant-necklace": ("necklace", "pendant_necklace"),
    "earrings": ("earrings", "drop_earrings"),
    "bracelet": ("bracelet", "bangle"),
    "bangle": ("bracelet", "bangle"),
    "ring": ("rings", "statement_ring"),
    "watch": ("accessory", ""),
}

_TYPE_MAP_TOKEN_KEYS = tuple(
    sorted(_TYPE_MAP, key=len, reverse=True)
)


_TYPE_MAP_ALIASES = {
    "t shirt": "t-shirt",
    "t-shirt": "t-shirt",
    "tee": "t-shirt",
    "tanktop": "tank-top",
    "crop top": "crop-top",
    "denim jacket": "denim-jacket",
    "leather jacket": "leather-jacket",
    "trench coat": "trench-coat",
    "ankle boot": "ankle-boots",
    "high heels": "high-heels",
    "high heel": "high-heels",
    "tote bag": "tote-bag",
    "shoulder bag": "shoulder-bag",
    "crossbody bag": "crossbody-bag",
    "mini skirt": "mini-skirt",
    "midi skirt": "midi-skirt",
    "maxi skirt": "maxi-skirt",
    "pencil skirt": "pencil-skirt",
    "pleated skirt": "pleated-skirt",
    "a line skirt": "a-line-skirt",
    "mini dress": "mini-dress",
    "midi dress": "midi-dress",
    "maxi dress": "maxi-dress",
    "bodycon dress": "bodycon-dress",
    "cocktail dress": "cocktail-dress",
    "evening dress": "evening-dress",
    "shirt dress": "shirt-dress",
    "a line dress": "a-line-dress",
    "wide leg jumpsuit": "wide-leg-jumpsuit",
}


_LIST_FIELDS = ("colors", "style", "season", "occasion", "cultural_origin", "features")
_STR_FIELDS = (
    "type", "subtype", "primary_color", "pattern", "material", "formality",
    "fit", "silhouette", "neckline", "collar", "sleeve_length", "length",
    "brand", "condition", "description",
)


class ClothingAttributes(BaseModel):
    """Structured result of one clothing-image analysis (model vocabulary)."""

    type: str = "other"
    subtype: str = ""
    primary_color: str = ""
    colors: list[str] = Field(default_factory=list)
    pattern: str = ""
    material: str = ""
    formality: str = ""
    style: list[str] = Field(default_factory=list)
    season: list[str] = Field(default_factory=list)
    fit: str = ""
    occasion: list[str] = Field(default_factory=list)
    cultural_origin: list[str] = Field(default_factory=list)
    silhouette: str = ""
    neckline: str = ""
    collar: str = ""
    sleeve_length: str = ""
    length: str = ""
    brand: str = ""
    condition: str = ""
    features: list[str] = Field(default_factory=list)
    description: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    raw_response: str = ""

    @model_validator(mode="before")
    @classmethod
    def _tolerate_loose_model_output(cls, data):
        """Keep a usable result even when the model is sloppy.

        Gemini often emits ``"confidence": null`` or a list field as a plain
        string, which would otherwise reject the whole object and degrade the
        entire analysis to a low-confidence empty result. Normalize field types
        so a single bad field cannot discard an otherwise good recognition.
        """
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        # Only touch fields the model actually returned, so absent fields keep
        # their pydantic defaults (e.g. type -> "other").
        for field in _LIST_FIELDS:
            if field not in payload:
                continue
            value = payload[field]
            if isinstance(value, str):
                payload[field] = [value] if value else []
            elif isinstance(value, (list, tuple)):
                payload[field] = [str(v) for v in value]
            else:
                payload[field] = []
        if "confidence" in payload:
            confidence = payload["confidence"]
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                confidence = 0.0
            payload["confidence"] = max(0.0, min(1.0, confidence))
        for field in _STR_FIELDS:
            if field not in payload:
                continue
            value = payload[field]
            if value is None:
                payload[field] = ""
            elif not isinstance(value, str):
                payload[field] = str(value)
        return payload


def build_analysis_prompt() -> str:
    """System prompt that forces a single strict JSON object from the model."""
    return (
        "OUTPUT ONLY JSON. NO TEXT. NO DESCRIPTIONS. NO EXPLANATIONS.\n"
        "Analyze the MAIN clothing item in this image. Use ONLY the English "
        "tokens from the allowed lists below. For unknown/absent details use "
        "null, an empty list, or \"unknown\" — never invent values.\n\n"
        f"TYPE (required, pick one): {', '.join(AI_TYPES)}\n"
        "SUBTYPE (optional): a more specific name within the type, e.g. "
        "button-down, henley, chinos, bomber, chelsea, maxi; use \"\" if the "
        "type is already specific enough (jeans, hoodie, blazer).\n"
        f"PRIMARY_COLOR (required): {', '.join(COLORS)}\n"
        f"COLORS (all visible colors): list from {', '.join(COLORS)}\n"
        f"PATTERN (required): {', '.join(PATTERNS)}\n"
        f"MATERIAL (optional): {', '.join(MATERIALS)}\n"
        f"FORMALITY (required): {', '.join(FORMALITY)}\n"
        f"STYLE (pick 1-2): {', '.join(STYLES)}\n"
        f"SEASON (pick all that apply): {', '.join(SEASONS)}\n"
        f"FIT (optional): {', '.join(FITS)}\n"
        f"OCCASION (pick all that apply): {', '.join(OCCASIONS)}\n"
        "CULTURAL_ORIGIN (pick all that apply, choose \"none\" if it is an "
        f"everyday modern garment): {', '.join(CULTURAL_ORIGINS)}\n"
        f"SILHOUETTE (optional): {', '.join(SILHOUETTES)}\n"
        f"NECKLINE (optional): {', '.join(NECKLINES)}\n"
        f"COLLAR (optional): {', '.join(COLLARS)}\n"
        f"SLEEVE_LENGTH (optional): {', '.join(SLEEVE_LENGTHS)}\n"
        f"LENGTH (optional): {', '.join(LENGTHS)}\n"
        "BRAND (optional): brand name if visible on a label, else \"\".\n"
        f"CONDITION (optional): {', '.join(CONDITIONS)}\n"
        "FEATURES (optional): short list of distinctive details, e.g. pockets, "
        "lace, sequins, embroidery, pleats, ruffles, drawstrings, hood, zipper, "
        "belt-loops, fringe, beading.\n"
        "DESCRIPTION: one short human-readable Chinese sentence (under 25 "
        "words) describing the garment.\n"
        "CONFIDENCE: a float 0.0-1.0 estimating how certain you are about the "
        "type and primary color. Must be a bare number, NEVER null, NEVER a "
        "string. Every list field (COLORS, STYLE, SEASON, OCCASION, "
        "CULTURAL_ORIGIN, FEATURES) must be a JSON array, even a single-item "
        "one; NEVER a string.\n"
        "Output this exact JSON structure (a single object, NOT wrapped in an "
        "array):\n"
        '{"type":"TYPE","subtype":"","primary_color":"COLOR","colors":["COLOR1"],'
        '"pattern":"PATTERN","material":"","formality":"FORMALITY",'
        '"style":["STYLE1"],"season":["SEASON1"],"fit":"",'
        '"occasion":["OCCASION1"],"cultural_origin":["none"],"silhouette":"",'
        '"neckline":"","collar":"","sleeve_length":"","length":"","brand":"",'
        '"condition":"","features":[],"description":"一句中文描述",'
        '"confidence":0.0}'
    )


def map_ai_type_to_item_fields(ai_type: str, ai_subtype: str = "") -> tuple[str, str]:
    """Translate the model's garment ``type`` to (item_type, subtype).

    Falls back to a keyword match against the AI subtype, then to ``other``.
    The returned ``item_type`` is guaranteed to be in ``ALLOWED_ITEM_TYPES``.
    """
    key = (ai_type or "").strip().lower()
    key = _TYPE_MAP_ALIASES.get(key, key)
    if key in _TYPE_MAP:
        item_type, mapped_subtype = _TYPE_MAP[key]
        subtype = ai_subtype.strip() or mapped_subtype
        return item_type, subtype
    subtype = (ai_subtype or "").strip().lower()
    if subtype in _TYPE_MAP:
        return _TYPE_MAP[subtype]
    # Longest-first token match rescues free-form model text, e.g.
    # "shirt with cuff" -> "shirt". "t-shirt" is caught earlier by exact match,
    # so "shirt" cannot shadow it here.
    for token in _TYPE_MAP_TOKEN_KEYS:
        if token in key:
            return _TYPE_MAP[token]
    return "other", ai_subtype.strip()


def parse_attributes(raw: str | dict) -> ClothingAttributes:
    """Parse the model's raw response into ``ClothingAttributes``.

    Accepts either the raw model text or an already-decoded JSON object. JSON or
    schema failures degrade to a low-confidence empty result instead of raising,
    so a bad model response never blocks the upload flow.
    """
    if isinstance(raw, dict):
        payload = raw
    else:
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        return ClothingAttributes.model_validate(payload)
    except ValidationError:
        return ClothingAttributes(confidence=0.0, raw_response=str(raw))


def is_reliable_analysis(attributes: ClothingAttributes) -> bool:
    """True when the recognition is confident enough to prefill a form."""
    return (
        attributes.type not in ("", "other", "unknown")
        and attributes.confidence >= 0.3
    )


def attributes_to_dict(attributes: ClothingAttributes) -> dict[str, Any]:
    """Serializable attributes plus the mapped project fields."""
    item_type, subtype = map_ai_type_to_item_fields(
        attributes.type, attributes.subtype
    )
    return {
        "item_type": item_type,
        "subtype": subtype,
        "primary_color": attributes.primary_color,
        "colors": attributes.colors,
        "pattern": attributes.pattern,
        "material": attributes.material,
        "formality": attributes.formality,
        "style": attributes.style,
        "season": attributes.season,
        "fit": attributes.fit,
        "occasion": attributes.occasion,
        "cultural_origin": attributes.cultural_origin,
        "silhouette": attributes.silhouette,
        "neckline": attributes.neckline,
        "collar": attributes.collar,
        "sleeve_length": attributes.sleeve_length,
        "length": attributes.length,
        "brand": attributes.brand,
        "condition": attributes.condition,
        "features": attributes.features,
        "description": attributes.description,
        "confidence": attributes.confidence,
    }
