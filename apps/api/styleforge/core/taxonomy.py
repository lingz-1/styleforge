"""Bilingual category taxonomy for upload forms and documentation.

Single source of truth for the Chinese/English labels of every selectable
wardrobe category (main category, required on upload) and its garment
subtypes (optional). The subtype set is derived from ``SUBTYPE_RULES`` so the
UI never drifts from the retrieval taxonomy.
"""

from __future__ import annotations

from styleforge.core.categories import ALLOWED_ITEM_TYPES
from styleforge.core.garment_attributes import SUBTYPE_RULES

# (zh, en) label pairs for every allowed main category key.
CATEGORY_LABELS: dict[str, tuple[str, str]] = {
    "top": ("上装", "Top"),
    "pants": ("裤装", "Pants"),
    "shorts": ("短裤", "Shorts"),
    "skirt": ("半身裙", "Skirt"),
    "dress": ("连衣裙", "Dress"),
    "jumpsuit": ("连体装", "Jumpsuit"),
    "suit": ("西装", "Suit"),
    "outfit_set": ("套装", "Outfit Set"),
    "outwear": ("外套", "Outerwear"),
    "shoes": ("鞋", "Shoes"),
    "bag": ("包", "Bag"),
    "eyewear": ("眼镜", "Eyewear"),
    "earrings": ("耳饰", "Earrings"),
    "necklace": ("项链", "Necklace"),
    "bracelet": ("手链", "Bracelet"),
    "rings": ("戒指", "Rings"),
    "belts": ("腰带", "Belts"),
    "hats": ("帽子", "Hats"),
    "hairwear": ("头饰", "Hair Accessories"),
    "jewellery": ("首饰", "Jewellery"),
    "legwear": ("裤袜", "Legwear"),
    "underwear": ("内衣", "Underwear"),
    "sleepwear": ("睡衣", "Sleepwear"),
    "swimwear": ("泳装", "Swimwear"),
    "activewear_bra": ("运动内衣", "Activewear Bra"),
    "accessory": ("配饰", "Accessory"),
    "other": ("其他", "Other"),
}

# (zh, en) label pairs for every garment subtype key in SUBTYPE_RULES.
SUBTYPE_LABELS: dict[str, tuple[str, str]] = {
    # Tops
    "shirt": ("衬衫", "Shirt"),
    "t_shirt": ("T恤", "T-Shirt"),
    "knitwear": ("毛衣/针织衫", "Knitwear"),
    "tank_top": ("背心/吊带", "Tank Top"),
    "hoodie": ("卫衣/连帽衫", "Hoodie"),
    "crop_top": ("短款上衣", "Crop Top"),
    # Pants and skirts
    "tailored_trousers": ("西裤/正装裤", "Tailored Trousers"),
    "jeans": ("牛仔裤", "Jeans"),
    "wide_leg_pants": ("阔腿裤", "Wide-Leg Pants"),
    "shorts": ("短裤", "Shorts"),
    "leggings": ("打底裤/紧身裤", "Leggings"),
    "pencil_skirt": ("铅笔裙", "Pencil Skirt"),
    "pleated_skirt": ("百褶裙", "Pleated Skirt"),
    "mini_skirt": ("短裙", "Mini Skirt"),
    "midi_skirt": ("中长裙", "Midi Skirt"),
    "maxi_skirt": ("长裙", "Maxi Skirt"),
    "a_line_skirt": ("A字裙", "A-Line Skirt"),
    # Shoes
    "high_heels": ("高跟鞋", "High Heels"),
    "pumps": ("浅口鞋/船鞋", "Pumps"),
    "loafers": ("乐福鞋", "Loafers"),
    "sneakers": ("运动鞋/跑鞋", "Sneakers"),
    "boots": ("靴子", "Boots"),
    "ankle_boots": ("踝靴/短靴", "Ankle Boots"),
    "flats": ("平底鞋/单鞋", "Flats"),
    "sandals": ("凉鞋/拖鞋", "Sandals"),
    "oxfords": ("牛津鞋", "Oxfords"),
    # Outerwear
    "blazer": ("西装外套", "Blazer"),
    "trench_coat": ("风衣", "Trench Coat"),
    "coat": ("大衣", "Coat"),
    "jacket": ("夹克", "Jacket"),
    "leather_jacket": ("皮夹克", "Leather Jacket"),
    "denim_jacket": ("牛仔外套", "Denim Jacket"),
    # Dresses and jumpsuits
    "cocktail_dress": ("鸡尾酒裙", "Cocktail Dress"),
    "evening_dress": ("晚礼服", "Evening Dress"),
    "mini_dress": ("短款连衣裙", "Mini Dress"),
    "midi_dress": ("中长连衣裙", "Midi Dress"),
    "maxi_dress": ("长连衣裙", "Maxi Dress"),
    "a_line_dress": ("A字连衣裙", "A-Line Dress"),
    "bodycon_dress": ("紧身连衣裙", "Bodycon Dress"),
    "shirt_dress": ("衬衫裙", "Shirt Dress"),
    "wide_leg_jumpsuit": ("阔腿连体裤", "Wide-Leg Jumpsuit"),
    # Bags
    "tote_bag": ("托特包", "Tote Bag"),
    "crossbody_bag": ("斜挎包", "Crossbody Bag"),
    "shoulder_bag": ("单肩包", "Shoulder Bag"),
    "clutch": ("手拿包/晚宴包", "Clutch"),
    "backpack": ("双肩包", "Backpack"),
    "satchel": ("邮差包", "Satchel"),
    # Eyewear
    "sunglasses": ("太阳镜", "Sunglasses"),
    "eyeglasses": ("光学眼镜", "Eyeglasses"),
    # Neckwear
    "scarf": ("围巾", "Scarf"),
    "shawl": ("披肩/围裹巾", "Shawl"),
    # Hats
    "cap": ("棒球帽", "Cap"),
    "beanie": ("针织帽", "Beanie"),
    "fedora": ("礼帽/毡帽", "Fedora"),
    # Earrings
    "stud_earrings": ("耳钉", "Stud Earrings"),
    "hoop_earrings": ("耳环", "Hoop Earrings"),
    "drop_earrings": ("垂坠耳环", "Drop Earrings"),
    # Necklaces
    "pendant_necklace": ("吊坠项链", "Pendant Necklace"),
    "choker": ("锁骨链/项圈", "Choker"),
    # Bracelets
    "bangle": ("手镯", "Bangle"),
    "cuff_bracelet": ("开口手镯", "Cuff Bracelet"),
    # Rings
    "statement_ring": ("个性戒指", "Statement Ring"),
    # Watches
    "leather_watch": ("皮带手表", "Leather Watch"),
    "metal_watch": ("金属表链手表", "Metal Watch"),
    # Hair accessories
    "headband": ("发带", "Headband"),
    "hair_clip": ("发夹", "Hair Clip"),
    # Gloves
    "leather_gloves": ("皮手套", "Leather Gloves"),
    "knit_gloves": ("针织手套", "Knit Gloves"),
}


# Preferred display order for the upload picker. Common wardrobe staples come
# first; ``other`` always last. Unlisted categories are sorted alphabetically
# after every listed one.
_CATEGORY_PRIORITY = [
    "top", "pants", "skirt", "dress", "jumpsuit", "outwear", "shoes", "bag",
    "shorts", "suit", "outfit_set", "legwear", "underwear", "sleepwear",
    "swimwear", "activewear_bra", "belts", "hats", "eyewear", "earrings",
    "necklace", "bracelet", "rings", "hairwear", "jewellery", "accessory",
    "other",
]


def _selectable_categories() -> set[str]:
    """Main categories a user may actually choose on an upload form."""
    return set(ALLOWED_ITEM_TYPES)


def build_taxonomy() -> list[dict[str, object]]:
    """Return a bilingual, user-facing category tree.

    Each category lists its subtypes (every rule whose ``item_types`` matches
    the category). Subtypes whose rules only reference non-selectable groups
    (neckwear / watches / gloves) are folded under ``accessory`` so nothing is
    orphaned.
    """
    selectable = _selectable_categories()
    subtypes_by_category: dict[str, list[dict[str, str]]] = {
        category: [] for category in selectable
    }
    for subtype, rule in sorted(SUBTYPE_RULES.items()):
        zh, en = SUBTYPE_LABELS.get(subtype, (subtype, subtype))
        entry = {"key": subtype, "zh": zh, "en": en}
        matches = [category for category in rule.item_types if category in selectable]
        if matches:
            for category in matches:
                subtypes_by_category[category].append(entry)
        else:
            # Orphan group (neckwear / watches / gloves): expose under accessory.
            subtypes_by_category["accessory"].append(entry)

    def _rank(category: str) -> tuple[int, str]:
        """Sort key: priority index first, then key for a stable tiebreak."""
        if category in _CATEGORY_PRIORITY:
            return (_CATEGORY_PRIORITY.index(category), category)
        return (len(_CATEGORY_PRIORITY), category)

    return [
        {
            "key": category,
            "zh": CATEGORY_LABELS.get(category, (category, category))[0],
            "en": CATEGORY_LABELS.get(category, (category, category))[1],
            "subtypes": subtypes_by_category[category],
        }
        for category in sorted(selectable, key=_rank)
    ]
