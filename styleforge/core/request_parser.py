"""Deterministic fallback parser for common Chinese and English styling requests."""

from __future__ import annotations

import re

from styleforge.core.garment_attributes import SUBTYPE_RULES
from styleforge.core.schemas import TaskSpec


AUDIENCE_ALIASES = (
    ("baby", ("婴幼儿", "婴儿", "宝宝", "baby", "infant")),
    ("girls", ("女童", "女孩", "girls", "girl")),
    ("boys", ("男童", "男孩", "boys", "boy")),
    ("women", ("女装", "女士", "女性", "女生", "women", "woman")),
    ("men", ("男装", "男士", "男性", "男生", "men", "man")),
    ("life", ("生活方式", "家居", "lifestyle", "homeware")),
)

SUIT_ALIASES = (
    "西装套装",
    "礼服套装",
    "正装套装",
    "tuxedo",
    "tailored suit",
    "suit",
)
OUTFIT_SET_ALIASES = ("成套服装", "儿童套装", "婴儿套装", "outfit set")


def _contains_alias(text: str, alias: str) -> bool:
    if alias.isascii() and alias.replace("-", "").isalnum():
        return re.search(rf"\b{re.escape(alias)}\b", text) is not None
    return alias in text


def _find_target_audiences(text: str) -> tuple[str, ...]:
    return tuple(
        audience
        for audience, aliases in AUDIENCE_ALIASES
        if any(_contains_alias(text, alias) for alias in aliases)
    )


def _find_special_slots(text: str) -> tuple[str, ...]:
    slots: list[str] = []

    if any(alias in text for alias in ("泳装下装", "比基尼下装", "bikini bottom")):
        slots.append("swim_bottom")
    elif any(alias in text for alias in ("泳装罩衫", "沙滩罩衫", "cover-up", "cover up")):
        slots.append("swim_coverup")
    elif any(
        alias in text
        for alias in ("泳装", "游泳", "比基尼", "swimsuit", "swimwear", "bikini")
    ):
        slots.append("swimwear")

    if any(alias in text for alias in ("保暖上层", "保暖上衣", "base layer top")):
        slots.append("base_layer_top")
    if any(alias in text for alias in ("保暖下层", "保暖裤", "base layer bottom")):
        slots.append("base_layer_bottom")
    if any(alias in text for alias in ("滑雪服", "滑雪穿搭", "skiwear", "ski outfit")):
        slots.append("skiwear")

    activewear_bra = any(
        alias in text for alias in ("运动内衣", "运动文胸", "sports bra", "activewear bra")
    )
    if activewear_bra:
        slots.append("activewear_bra")
    elif any(alias in text for alias in ("内衣", "内裤", "underwear", "boxers", "briefs")):
        slots.append("underwear")

    if any(alias in text for alias in ("睡衣", "睡袍", "nightwear", "sleepwear", "pajama", "pyjama")):
        slots.append("sleepwear")
    if any(alias in text for alias in ("洗浴服", "浴袍", "bathtime", "bathrobe")):
        slots.append("bathwear")
    return tuple(dict.fromkeys(slots))


COLOR_ALIASES = {
    "black": ("黑色", "黑", "black"),
    "white": ("白色", "白", "white"),
    "gray": ("灰色", "灰", "grey", "gray"),
    "red": ("红色", "红", "red"),
    "blue": ("蓝色", "蓝", "blue"),
    "navy": ("藏青", "海军蓝", "navy"),
    "green": ("绿色", "绿", "green"),
    "yellow": ("黄色", "黄", "yellow"),
    "pink": ("粉色", "粉", "pink"),
    "purple": ("紫色", "紫", "purple"),
    "brown": ("棕色", "棕", "咖色", "brown"),
    "beige": ("米色", "米白", "beige"),
    "cream": ("奶油色", "cream"),
    "orange": ("橙色", "橘色", "orange"),
    "gold": ("金色", "gold"),
    "silver": ("银色", "silver"),
}

OCCASION_ALIASES = (
    ("business", ("面试", "上班", "通勤", "商务", "职场", "interview", "business", "work")),
    ("formal", ("正式", "婚礼", "晚宴", "年会", "formal", "wedding", "gala")),
    ("date", ("约会", "date")),
    (
        "sport",
        (
            "运动",
            "健身",
            "跑步",
            "打球",
            "篮球",
            "足球",
            "羽毛球",
            "乒乓球",
            "网球",
            "排球",
            "锻炼",
            "sport",
            "gym",
            "running",
            "workout",
            "play ball",
        ),
    ),
    ("casual", ("休闲", "周末", "逛街", "casual", "weekend")),
    ("daily", ("日常", "daily", "everyday")),
)

# Hard constraints injected for a detected occasion. These keep an "outfit"
# from pairing obviously wrong pieces (e.g. hanfu skirt for a workout) even
# when FashionCLIP retrieval or the stylist would otherwise rank them highly.
OCCASION_HARD_CONSTRAINTS = {
    "sport": {
        "required_item_types_by_slot": {"bottom": ("pants", "shorts")},
        "required_subtypes_by_slot": {
            "top": ("t_shirt", "tank_top", "hoodie"),
            "footwear": ("sneakers",),
        },
        "excluded_name_keywords": (
            "汉服",
            "汉元素",
            "国风",
            "宋制",
            "唐制",
            "明制",
            "褙子",
            "比甲",
            "齐胸",
            "马面",
            "汉家",
        ),
        "excluded_subtypes_by_slot": {
            "top": ("shirt",),
            "bottom": (
                "pencil_skirt",
                "pleated_skirt",
                "mini_skirt",
                "midi_skirt",
                "maxi_skirt",
                "a_line_skirt",
            ),
            "footwear": (
                "high_heels",
                "pumps",
                "loafers",
                "flats",
                "boots",
                "ankle_boots",
                "sandals",
                "oxfords",
            ),
        },
    },
}

NEGATIVE_MARKERS = (
    "不要",
    "不想",
    "不喜欢",
    "避免",
    "避开",
    "不穿",
    "讨厌",
    "排除",
    "禁用",
    "without",
    "avoid",
    "no ",
)

SUBTYPE_ALIASES = (
    # Tops
    ("top", "shirt", ("衬衫", "衬衣", "shirt", "blouse", "button-up")),
    ("top", "t_shirt", ("T恤", "圆领上衣", "t-shirt", "tee")),
    ("top", "knitwear", ("针织衫", "毛衣", "高领衫", "sweater", "knitwear", "cardigan")),
    ("top", "tank_top", ("背心", "吊带", "tank top", "camisole")),
    ("top", "hoodie", ("卫衣", "连帽衫", "hoodie", "sweatshirt")),
    ("top", "crop_top", ("短款上衣", "露脐上衣", "crop top", "cropped top")),
    # Pants and skirts
    ("bottom", "tailored_trousers", ("西裤", "正装裤", "tailored trousers", "dress pants", "slacks")),
    ("bottom", "jeans", ("牛仔裤", "jeans", "denim pants")),
    ("bottom", "wide_leg_pants", ("阔腿裤", "wide-leg pants", "palazzo pants")),
    ("bottom", "shorts", ("短裤", "shorts")),
    ("bottom", "leggings", ("打底裤", "紧身裤", "leggings")),
    ("bottom", "pencil_skirt", ("铅笔裙", "包臀裙", "pencil skirt")),
    ("bottom", "pleated_skirt", ("百褶裙", "pleated skirt")),
    ("bottom", "mini_skirt", ("迷你裙", "超短裙", "mini skirt")),
    ("bottom", "midi_skirt", ("中长裙", "及膝裙", "midi skirt")),
    ("bottom", "maxi_skirt", ("长裙", "拖地裙", "maxi skirt")),
    ("bottom", "a_line_skirt", ("A字裙", "a-line skirt", "a line skirt")),
    # Shoes
    ("footwear", "high_heels", ("高跟鞋", "细高跟", "high heels", "stiletto")),
    ("footwear", "pumps", ("浅口鞋", "船鞋", "pumps")),
    ("footwear", "loafers", ("乐福鞋", "loafer", "moccasin")),
    ("footwear", "sneakers", ("运动鞋", "球鞋", "sneakers", "trainers")),
    ("footwear", "ankle_boots", ("踝靴", "短靴", "ankle boots", "booties")),
    ("footwear", "boots", ("靴子", "长靴", "boots")),
    ("footwear", "flats", ("平底鞋", "芭蕾鞋", "flats", "ballet flats")),
    ("footwear", "sandals", ("凉鞋", "sandals")),
    ("footwear", "oxfords", ("牛津鞋", "布洛克鞋", "oxfords", "brogues")),
    # Outerwear
    ("outerwear", "blazer", ("西装外套", "西服外套", "blazer")),
    ("outerwear", "trench_coat", ("风衣", "trench coat")),
    ("outerwear", "leather_jacket", ("皮夹克", "机车夹克", "leather jacket", "moto jacket")),
    ("outerwear", "denim_jacket", ("牛仔外套", "denim jacket")),
    ("outerwear", "coat", ("大衣", "呢子大衣", "coat", "overcoat")),
    ("outerwear", "jacket", ("夹克", "jacket")),
    # Dresses and jumpsuits
    ("one_piece", "cocktail_dress", ("鸡尾酒裙", "cocktail dress")),
    ("one_piece", "evening_dress", ("晚礼服", "晚宴裙", "evening dress", "evening gown")),
    ("one_piece", "mini_dress", ("迷你连衣裙", "mini dress")),
    ("one_piece", "midi_dress", ("中长连衣裙", "midi dress")),
    ("one_piece", "maxi_dress", ("长款连衣裙", "maxi dress")),
    ("one_piece", "a_line_dress", ("A字连衣裙", "a-line dress")),
    ("one_piece", "bodycon_dress", ("紧身连衣裙", "bodycon dress")),
    ("one_piece", "shirt_dress", ("衬衫裙", "shirt dress", "shirtdress")),
    ("one_piece", "wide_leg_jumpsuit", ("阔腿连体裤", "wide-leg jumpsuit")),
    # Bags
    ("bag", "tote_bag", ("托特包", "购物袋", "tote bag")),
    ("bag", "crossbody_bag", ("斜挎包", "crossbody bag")),
    ("bag", "shoulder_bag", ("单肩包", "shoulder bag")),
    ("bag", "clutch", ("手拿包", "晚宴包", "clutch")),
    ("bag", "backpack", ("双肩包", "背包", "backpack")),
    ("bag", "satchel", ("剑桥包", "邮差包", "satchel")),
    # Accessories
    ("accessory", "sunglasses", ("太阳镜", "墨镜", "sunglasses")),
    ("accessory", "eyeglasses", ("眼镜", "镜框", "eyeglasses")),
    ("accessory", "scarf", ("围巾", "丝巾", "scarf")),
    ("accessory", "shawl", ("披肩", "shawl")),
    ("accessory", "cap", ("棒球帽", "鸭舌帽", "baseball cap")),
    ("accessory", "beanie", ("针织帽", "毛线帽", "beanie")),
    ("accessory", "fedora", ("礼帽", "fedora")),
    ("accessory", "stud_earrings", ("耳钉", "stud earrings")),
    ("accessory", "hoop_earrings", ("圈形耳环", "圆环耳环", "hoop earrings")),
    ("accessory", "drop_earrings", ("垂坠耳环", "吊坠耳环", "drop earrings")),
    ("accessory", "pendant_necklace", ("吊坠项链", "pendant necklace")),
    ("accessory", "choker", ("颈链", "锁骨链", "choker")),
    ("accessory", "bangle", ("手镯", "bangle")),
    ("accessory", "cuff_bracelet", ("宽手环", "cuff bracelet")),
    ("accessory", "statement_ring", ("造型戒指", "statement ring")),
    ("accessory", "leather_watch", ("皮带手表", "皮表带腕表", "leather watch")),
    ("accessory", "metal_watch", ("钢带手表", "金属腕表", "metal watch")),
    ("accessory", "headband", ("发带", "头箍", "headband")),
    ("accessory", "hair_clip", ("发夹", "发卡", "hair clip")),
    ("accessory", "leather_gloves", ("皮手套", "leather gloves")),
    ("accessory", "knit_gloves", ("针织手套", "毛线手套", "knit gloves")),
)

ACCESSORY_ITEM_TYPE_ALIASES = {
    "earrings": ("耳环", "耳钉", "earrings"),
    "necklace": ("项链", "necklace"),
    "bracelet": ("手链", "手镯", "bracelet"),
    "rings": ("戒指", "rings"),
    "watches": ("手表", "腕表", "watch"),
    "brooch": ("胸针", "brooch"),
    "gloves": ("手套", "gloves"),
    "hairwear": ("发饰", "发带", "hair accessory"),
}

GENERIC_ACCESSORY_ALIASES = ("配饰", "饰品", "首饰", "accessory", "accessories", "jewelry")
PLURAL_ACCESSORY_MARKERS = ("一些", "几个", "两件", "两个", "多件", "多种", "some", "several")


def _find_colors(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    preferred: list[str] = []
    excluded: list[str] = []
    for canonical, aliases in COLOR_ALIASES.items():
        positions = [text.find(alias) for alias in aliases if text.find(alias) >= 0]
        if not positions:
            continue
        position = min(positions)
        if _is_negated(text, position):
            excluded.append(canonical)
        else:
            preferred.append(canonical)
    return tuple(preferred), tuple(excluded)


def _find_occasion(text: str) -> str:
    for occasion, aliases in OCCASION_ALIASES:
        if any(alias in text for alias in aliases):
            return occasion
    return "daily"


def _find_required_slots(text: str) -> tuple[str, ...]:
    special_slots = _find_special_slots(text)
    one_piece_requested = any(
        keyword in text for keyword in ("连衣裙", "连体裤", "连体衣", "one-piece", "dress")
    )
    one_piece_requested = one_piece_requested or any(
        _contains_alias(text, alias) for alias in (*SUIT_ALIASES, *OUTFIT_SET_ALIASES)
    )
    separates_requested = any(
        keyword in text for keyword in ("上衣", "衬衫", "裤", "半身裙", "top", "pants", "skirt")
    )
    if special_slots:
        slots = list(special_slots)
        if any(keyword in text for keyword in ("鞋", "footwear", "shoes")):
            slots.append("footwear")
    elif one_piece_requested and not separates_requested:
        slots = ["one_piece", "footwear"]
    else:
        slots = ["top", "bottom", "footwear"]
    if any(keyword in text for keyword in ("外套", "夹克", "大衣", "outerwear", "jacket", "coat")):
        slots.append("outerwear")
    if any(keyword in text for keyword in ("包包", "手提包", "挎包", "bag", "handbag")):
        slots.append("bag")
    accessory_aliases = tuple(
        alias
        for aliases in ACCESSORY_ITEM_TYPE_ALIASES.values()
        for alias in aliases
    ) + tuple(
        alias
        for slot, _, aliases in SUBTYPE_ALIASES
        if slot == "accessory"
        for alias in aliases
    )
    specific_accessory_requested = any(keyword in text for keyword in accessory_aliases)
    generic_accessory_requested = any(keyword in text for keyword in GENERIC_ACCESSORY_ALIASES)
    if specific_accessory_requested:
        slots.append("accessory")
    elif generic_accessory_requested and any(marker in text for marker in PLURAL_ACCESSORY_MARKERS):
        slots.extend(("accessory_1", "accessory_2"))
    elif generic_accessory_requested:
        slots.append("accessory")
    return tuple(slots)


def _find_required_item_types(text: str) -> dict[str, tuple[str, ...]]:
    constraints: dict[str, tuple[str, ...]] = {}
    if any(_contains_alias(text, alias) for alias in SUIT_ALIASES):
        constraints["one_piece"] = ("suit",)
    elif any(_contains_alias(text, alias) for alias in OUTFIT_SET_ALIASES):
        constraints["one_piece"] = ("outfit_set",)
    bottom_types: list[str] = []
    if any(keyword in text for keyword in ("半身裙", "裙装", "skirt")):
        bottom_types.append("skirt")
    pants_requested = any(
        keyword in text for keyword in ("裤子", "长裤", "西裤", "牛仔裤", "pants", "trousers", "jeans")
    )
    if pants_requested:
        bottom_types.append("pants")
    if bottom_types:
        constraints["bottom"] = tuple(dict.fromkeys(bottom_types))

    if any(keyword in text for keyword in ("连衣裙", "one-piece dress", "dress")):
        constraints["one_piece"] = ("dress",)
    elif any(keyword in text for keyword in ("连体裤", "jumpsuit")):
        constraints["one_piece"] = ("jumpsuit",)
    accessory_types = [
        item_type
        for item_type, aliases in ACCESSORY_ITEM_TYPE_ALIASES.items()
        if any(alias in text for alias in aliases)
    ]
    if accessory_types:
        constraints["accessory"] = tuple(dict.fromkeys(accessory_types))
    return constraints


def _is_negated(text: str, position: int) -> bool:
    context = text[max(0, position - 12) : position]
    for delimiter in ("，", ",", "。", ";", "；", "!", "！", "?", "？"):
        context = context.rsplit(delimiter, 1)[-1]
    return any(marker in context for marker in NEGATIVE_MARKERS)


def _find_subtype_constraints(
    text: str,
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    matches: list[tuple[int, int, str, str]] = []
    for slot, subtype, aliases in SUBTYPE_ALIASES:
        for alias in aliases:
            start = text.find(alias.lower())
            while start >= 0:
                matches.append((start, start + len(alias), slot, subtype))
                start = text.find(alias.lower(), start + len(alias))

    # Prefer the longest phrase when aliases overlap, e.g. ankle boots before boots.
    accepted: list[tuple[int, int, str, str]] = []
    occupied: list[tuple[int, int]] = []
    for start, end, slot, subtype in sorted(matches, key=lambda value: (-(value[1] - value[0]), value[0])):
        if any(start < other_end and end > other_start for other_start, other_end in occupied):
            continue
        accepted.append((start, end, slot, subtype))
        occupied.append((start, end))

    required: dict[str, list[str]] = {}
    excluded: dict[str, list[str]] = {}
    for start, _, slot, subtype in sorted(accepted):
        target = excluded if _is_negated(text, start) else required
        target.setdefault(slot, []).append(subtype)
    for slot, subtypes in excluded.items():
        if slot in required:
            required[slot] = [value for value in required[slot] if value not in subtypes]
    return (
        {slot: tuple(dict.fromkeys(values)) for slot, values in required.items() if values},
        {slot: tuple(dict.fromkeys(values)) for slot, values in excluded.items() if values},
    )


def parse_request(user_id: str, request: str, max_results: int = 3) -> TaskSpec:
    normalized = request.strip().lower()
    if not normalized:
        raise ValueError("request cannot be empty")
    preferred_colors, excluded_colors = _find_colors(normalized)
    required_subtypes, excluded_subtypes = _find_subtype_constraints(normalized)
    # Occasion-injected subtypes must not widen required_slots (e.g. a sports-bra
    # request should stay isolated from generic top/footwear slots), so keep the
    # user-explicit set aside for the required_slots step below.
    user_required_subtypes = dict(required_subtypes)
    required_item_types = _find_required_item_types(normalized)
    occasion = _find_occasion(normalized)
    for slot, item_types in OCCASION_HARD_CONSTRAINTS.get(
        occasion, {}
    ).get("required_item_types_by_slot", {}).items():
        existing = required_item_types.get(slot, ())
        required_item_types[slot] = tuple(dict.fromkeys((*existing, *item_types)))
    for slot, subtypes in OCCASION_HARD_CONSTRAINTS.get(
        occasion, {}
    ).get("required_subtypes_by_slot", {}).items():
        existing = required_subtypes.get(slot, ())
        required_subtypes[slot] = tuple(dict.fromkeys((*existing, *subtypes)))
    for slot, subtypes in OCCASION_HARD_CONSTRAINTS.get(
        occasion, {}
    ).get("excluded_subtypes_by_slot", {}).items():
        existing = excluded_subtypes.get(slot, ())
        excluded_subtypes[slot] = tuple(dict.fromkeys((*existing, *subtypes)))
    for slot, subtypes in required_subtypes.items():
        inferred_types = tuple(
            dict.fromkeys(
                item_type
                for subtype in subtypes
                for item_type in SUBTYPE_RULES[subtype].item_types
            )
        )
        if inferred_types:
            existing = required_item_types.get(slot, ())
            required_item_types[slot] = tuple(dict.fromkeys((*existing, *inferred_types)))
    required_slots = list(_find_required_slots(normalized))
    for slot in user_required_subtypes:
        if slot not in required_slots:
            required_slots.append(slot)
    return TaskSpec(
        user_id=user_id,
        occasion=occasion,
        target_audiences=_find_target_audiences(normalized),
        required_slots=tuple(required_slots),
        required_item_types_by_slot=required_item_types,
        required_subtypes_by_slot=required_subtypes,
        excluded_subtypes_by_slot=excluded_subtypes,
        excluded_colors=excluded_colors,
        excluded_name_keywords=OCCASION_HARD_CONSTRAINTS.get(
            occasion, {}
        ).get("excluded_name_keywords", ()),
        preferred_colors=preferred_colors,
        max_results=max_results,
    )
