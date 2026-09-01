"""Auditable garment subtype taxonomy backed by catalog text metadata."""

from __future__ import annotations

from dataclasses import dataclass

from styleforge.core.schemas import CatalogItem


@dataclass(frozen=True, slots=True)
class SubtypeRule:
    item_types: tuple[str, ...]
    include: tuple[str, ...]
    exclude: tuple[str, ...] = ()
    prompt: str = ""


SUBTYPE_RULES = {
    # Tops
    "shirt": SubtypeRule(
        ("top",),
        ("shirt", "blouse", "button-up", "button up", "button-down", "collared", "衬衫", "衬衣", "对襟衫"),
        ("t-shirt", "tee shirt", "sweatshirt", "crew neck", "round neck"),
        "women's collared button-up shirt or blouse",
    ),
    "t_shirt": SubtypeRule(
        ("top",),
        (
            "t-shirt",
            "tee",
            "crew neck top",
            "round neck top",
            "t恤",
            "运动短袖",
            "短袖上衣",
        ),
        ("sweatshirt",),
        "women's t-shirt or crew-neck tee",
    ),
    "knitwear": SubtypeRule(
        ("top",),
        ("sweater", "knit top", "pullover", "turtleneck", "cardigan", "毛衣", "针织衫", "开衫"),
        (),
        "women's sweater or knitwear",
    ),
    "vest": SubtypeRule(
        ("top", "outwear"),
        ("vest", "waistcoat", "马甲", "针织背心", "西装背心"),
        ("tank top", "camisole", "cami top", "吊带"),
        "women's layering vest or waistcoat",
    ),
    "tank_top": SubtypeRule(
        ("top",),
        ("tank top", "camisole", "cami top", "背心", "吊带"),
        (),
        "women's tank top or camisole",
    ),
    "hoodie": SubtypeRule(
        ("top",),
        ("hoodie", "hooded sweatshirt", "sweatshirt", "卫衣", "连帽衫"),
        (),
        "women's hoodie or sweatshirt",
    ),
    "crop_top": SubtypeRule(
        ("top",),
        ("crop top", "cropped top", "cropped blouse", "cropped shirt", "短款上衣", "露脐上衣"),
        (),
        "women's cropped top",
    ),
    # Pants and skirts
    "tailored_trousers": SubtypeRule(
        ("pants",),
        ("tailored trouser", "tailored pant", "dress pant", "suit pant", "slacks", "trousers", "西裤", "正装裤"),
        ("jeans", "denim", "jogger", "legging"),
        "women's tailored formal trousers",
    ),
    "jeans": SubtypeRule(
        ("pants",),
        ("jeans", "denim pant", "denim trouser", "牛仔裤", "牛仔"),
        (),
        "women's jeans",
    ),
    "wide_leg_pants": SubtypeRule(
        ("pants",),
        ("wide-leg", "wide leg", "palazzo", "阔腿裤"),
        (),
        "women's wide-leg pants",
    ),
    "shorts": SubtypeRule(
        ("pants", "shorts"),
        ("shorts", "short pant", "短裤", "热裤"),
        (),
        "women's shorts",
    ),
    "leggings": SubtypeRule(
        ("pants", "legwear"),
        ("leggings", "legging", "打底裤", "紧身裤", "瑜伽裤", "鲨鱼裤"),
        (),
        "women's leggings",
    ),
    "pencil_skirt": SubtypeRule(
        ("skirt",),
        ("pencil skirt", "铅笔裙", "包臀裙"),
        (),
        "women's pencil skirt",
    ),
    "pleated_skirt": SubtypeRule(
        ("skirt",),
        ("pleated skirt", "accordion pleat", "百褶裙", "百褶"),
        (),
        "women's pleated skirt",
    ),
    "mini_skirt": SubtypeRule(
        ("skirt",),
        ("mini skirt", "miniskirt", "短裙", "迷你裙", "超短裙"),
        (),
        "women's mini skirt",
    ),
    "midi_skirt": SubtypeRule(
        ("skirt",),
        ("midi skirt", "mid-length skirt", "mid length skirt", "中长裙", "及膝裙"),
        (),
        "women's midi skirt",
    ),
    "maxi_skirt": SubtypeRule(
        ("skirt",),
        ("maxi skirt", "floor-length skirt", "long skirt", "长裙", "拖地裙"),
        (),
        "women's maxi skirt",
    ),
    "a_line_skirt": SubtypeRule(
        ("skirt",),
        ("a-line skirt", "a line skirt", "a字裙"),
        (),
        "women's A-line skirt",
    ),
    # Shoes
    "high_heels": SubtypeRule(
        ("shoes",),
        ("high heel", "stiletto", "platform heel", "高跟鞋", "细高跟"),
        (),
        "women's high heels",
    ),
    "pumps": SubtypeRule(
        ("shoes",),
        ("pump", "浅口鞋", "船鞋"),
        (),
        "women's pumps",
    ),
    "loafers": SubtypeRule(
        ("shoes",),
        ("loafer", "moccasin", "乐福鞋"),
        (),
        "women's loafers",
    ),
    "sneakers": SubtypeRule(
        ("shoes",),
        (
            "sneaker",
            "trainer",
            "running shoe",
            "athletic shoe",
            "运动鞋",
            "跑鞋",
            "球鞋",
            "跑步鞋",
            "训练鞋",
        ),
        (),
        "women's sneakers",
    ),
    "boots": SubtypeRule(
        ("shoes",),
        ("boot", "靴", "靴子"),
        (),
        "women's boots",
    ),
    "ankle_boots": SubtypeRule(
        ("shoes",),
        ("ankle boot", "bootie", "踝靴", "短靴"),
        (),
        "women's ankle boots",
    ),
    "flats": SubtypeRule(
        ("shoes",),
        ("ballet flat", "flat shoe", "flat loafer", "平底鞋", "单鞋", "芭蕾鞋"),
        (),
        "women's flat shoes",
    ),
    "sandals": SubtypeRule(
        ("shoes",),
        ("sandal", "凉鞋", "拖鞋"),
        (),
        "women's sandals",
    ),
    "oxfords": SubtypeRule(
        ("shoes",),
        ("oxford", "brogue", "derby shoe", "牛津鞋", "布洛克鞋"),
        (),
        "women's oxford or brogue shoes",
    ),
    # Outerwear
    "blazer": SubtypeRule(
        ("outwear",),
        ("blazer", "suit jacket"),
        (),
        "women's tailored blazer",
    ),
    "trench_coat": SubtypeRule(
        ("outwear",),
        ("trench coat", "trenchcoat"),
        (),
        "women's trench coat",
    ),
    "coat": SubtypeRule(
        ("outwear",),
        ("coat", "overcoat"),
        ("trench coat",),
        "women's coat",
    ),
    "jacket": SubtypeRule(
        ("outwear",),
        ("jacket",),
        ("suit jacket",),
        "women's jacket",
    ),
    "leather_jacket": SubtypeRule(
        ("outwear",),
        ("leather jacket", "moto jacket", "biker jacket"),
        (),
        "women's leather jacket",
    ),
    "denim_jacket": SubtypeRule(
        ("outwear",),
        ("denim jacket", "jean jacket"),
        (),
        "women's denim jacket",
    ),
    # Dresses and jumpsuits
    "cocktail_dress": SubtypeRule(
        ("dress",),
        ("cocktail dress",),
        (),
        "women's cocktail dress",
    ),
    "evening_dress": SubtypeRule(
        ("dress",),
        ("evening dress", "evening gown", "formal gown"),
        (),
        "women's formal evening dress",
    ),
    "mini_dress": SubtypeRule(
        ("dress",),
        ("mini dress",),
        (),
        "women's mini dress",
    ),
    "midi_dress": SubtypeRule(
        ("dress",),
        ("midi dress", "mid-length dress"),
        (),
        "women's midi dress",
    ),
    "maxi_dress": SubtypeRule(
        ("dress",),
        ("maxi dress", "floor-length dress"),
        (),
        "women's maxi dress",
    ),
    "a_line_dress": SubtypeRule(
        ("dress",),
        ("a-line dress", "a line dress"),
        (),
        "women's A-line dress",
    ),
    "bodycon_dress": SubtypeRule(
        ("dress",),
        ("bodycon dress", "body-con dress"),
        (),
        "women's bodycon dress",
    ),
    "shirt_dress": SubtypeRule(
        ("dress",),
        ("shirt dress", "shirtdress"),
        (),
        "women's shirt dress",
    ),
    "wide_leg_jumpsuit": SubtypeRule(
        ("jumpsuit",),
        ("wide-leg jumpsuit", "wide leg jumpsuit"),
        (),
        "women's wide-leg jumpsuit",
    ),
    # Bags
    "tote_bag": SubtypeRule(
        ("bag",),
        ("tote", "shopper bag"),
        (),
        "women's tote bag",
    ),
    "crossbody_bag": SubtypeRule(
        ("bag",),
        ("crossbody", "cross-body"),
        (),
        "women's crossbody bag",
    ),
    "shoulder_bag": SubtypeRule(
        ("bag",),
        ("shoulder bag",),
        (),
        "women's shoulder bag",
    ),
    "clutch": SubtypeRule(
        ("bag",),
        ("clutch", "evening bag"),
        (),
        "women's clutch bag",
    ),
    "backpack": SubtypeRule(
        ("bag",),
        ("backpack", "rucksack"),
        (),
        "women's backpack",
    ),
    "satchel": SubtypeRule(
        ("bag",),
        ("satchel",),
        (),
        "women's satchel bag",
    ),
    # Accessories whose first-level dataset type is already specific.
    "sunglasses": SubtypeRule(
        ("eyewear",),
        ("sunglasses", "sun glasses"),
        (),
        "women's sunglasses",
    ),
    "eyeglasses": SubtypeRule(
        ("eyewear",),
        ("eyeglasses", "optical frame", "reading glasses"),
        (),
        "women's eyeglasses",
    ),
    "scarf": SubtypeRule(
        ("neckwear",),
        ("scarf", "infinity scarf"),
        (),
        "women's scarf",
    ),
    "shawl": SubtypeRule(
        ("neckwear",),
        ("shawl", "wrap"),
        (),
        "women's shawl",
    ),
    "cap": SubtypeRule(
        ("hats",),
        ("cap", "baseball cap"),
        (),
        "women's cap",
    ),
    "beanie": SubtypeRule(
        ("hats",),
        ("beanie", "knit hat"),
        (),
        "women's beanie",
    ),
    "fedora": SubtypeRule(
        ("hats",),
        ("fedora",),
        (),
        "women's fedora hat",
    ),
    "stud_earrings": SubtypeRule(
        ("earrings",),
        ("stud earrings", "ear studs", "stud earring"),
        (),
        "women's stud earrings",
    ),
    "hoop_earrings": SubtypeRule(
        ("earrings",),
        ("hoop earrings", "hoop earring"),
        (),
        "women's hoop earrings",
    ),
    "drop_earrings": SubtypeRule(
        ("earrings",),
        ("drop earrings", "dangle earrings", "chandelier earrings"),
        (),
        "women's drop earrings",
    ),
    "pendant_necklace": SubtypeRule(
        ("necklace",),
        ("pendant necklace", "pendant chain"),
        (),
        "women's pendant necklace",
    ),
    "choker": SubtypeRule(
        ("necklace",),
        ("choker",),
        (),
        "women's choker necklace",
    ),
    "bangle": SubtypeRule(
        ("bracelet",),
        ("bangle",),
        (),
        "women's bangle bracelet",
    ),
    "cuff_bracelet": SubtypeRule(
        ("bracelet",),
        ("cuff bracelet", "wrist cuff"),
        (),
        "women's cuff bracelet",
    ),
    "statement_ring": SubtypeRule(
        ("rings",),
        ("statement ring", "cocktail ring"),
        (),
        "women's statement ring",
    ),
    "leather_watch": SubtypeRule(
        ("watches",),
        ("leather strap watch", "leather watch"),
        (),
        "women's leather strap watch",
    ),
    "metal_watch": SubtypeRule(
        ("watches",),
        ("metal bracelet watch", "stainless steel watch", "chain watch"),
        (),
        "women's metal bracelet watch",
    ),
    "headband": SubtypeRule(
        ("hairwear",),
        ("headband", "hair band"),
        (),
        "women's headband",
    ),
    "hair_clip": SubtypeRule(
        ("hairwear",),
        ("hair clip", "barrette", "hairpin"),
        (),
        "women's hair clip",
    ),
    "leather_gloves": SubtypeRule(
        ("gloves",),
        ("leather gloves", "leather glove"),
        (),
        "women's leather gloves",
    ),
    "knit_gloves": SubtypeRule(
        ("gloves",),
        ("knit gloves", "knitted gloves", "wool gloves"),
        (),
        "women's knit gloves",
    ),
}


def item_matches_subtype(item: CatalogItem, subtype: str) -> bool:
    """Return true only when item type and auditable text support the subtype."""
    rule = SUBTYPE_RULES.get(subtype)
    if rule is None:
        raise ValueError(f"Unknown garment subtype: {subtype}")
    if item.item_type not in rule.item_types:
        return False
    text = f"{item.name} {item.description} {' '.join(item.features)}".lower()
    if any(keyword in text for keyword in rule.exclude):
        return False
    return any(keyword in text for keyword in rule.include)


def subtype_prompt(subtype: str, audience_prefix: str = "") -> str:
    rule = SUBTYPE_RULES.get(subtype)
    if rule is None:
        raise ValueError(f"Unknown garment subtype: {subtype}")
    prompt = rule.prompt
    if prompt.startswith("women's "):
        prompt = prompt.removeprefix("women's ")
    return f"{audience_prefix} {prompt}".strip()
