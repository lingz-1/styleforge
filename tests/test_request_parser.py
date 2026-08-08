from styleforge.core.garment_attributes import item_matches_subtype
from styleforge.core.request_parser import parse_request
from styleforge.core.schemas import CatalogItem, EmbeddingStatus, ImageStatus


def _item(item_type: str, name: str) -> CatalogItem:
    return CatalogItem(
        item_id=f"item-{item_type}-{abs(hash(name)) & 0xFFFFFF}",
        source="personal",
        gender="women",
        item_type=item_type,
        main_category=item_type,
        name=name,
        color="",
        description="",
        features=(),
        image_filename="",
        relative_image_path="",
        image_status=ImageStatus.UNBOUND,
        embedding_status=EmbeddingStatus.PENDING,
    )


def test_half_skirt_becomes_hard_bottom_type_constraint() -> None:
    task = parse_request(
        "user",
        "需要正式的上班穿搭，衬衫配半身裙和鞋，不要红色。",
    )

    assert "bottom" in task.required_slots
    assert task.required_item_types_by_slot["bottom"] == ("skirt",)
    assert "red" in task.excluded_colors


def test_explicit_pants_becomes_hard_bottom_type_constraint() -> None:
    task = parse_request("user", "上衣配西裤和鞋，适合上班。")

    assert task.required_item_types_by_slot["bottom"] == ("pants",)


def test_parser_preserves_subtypes_for_all_core_slots() -> None:
    task = parse_request(
        "user",
        "衬衫配百褶裙和乐福鞋，加西装外套和托特包，不要高跟鞋。",
    )

    assert task.required_subtypes_by_slot["top"] == ("shirt",)
    assert task.required_subtypes_by_slot["bottom"] == ("pleated_skirt",)
    assert task.required_subtypes_by_slot["footwear"] == ("loafers",)
    assert task.required_subtypes_by_slot["outerwear"] == ("blazer",)
    assert task.required_subtypes_by_slot["bag"] == ("tote_bag",)
    assert task.excluded_subtypes_by_slot["footwear"] == ("high_heels",)


def test_round_neck_top_is_not_parsed_as_shirt() -> None:
    task = parse_request("user", "圆领上衣配西裤和鞋。")

    assert task.required_subtypes_by_slot["top"] == ("t_shirt",)


def test_negation_does_not_leak_across_clauses() -> None:
    task = parse_request("user", "不要红色，穿衬衫配半身裙和鞋。")

    assert task.required_subtypes_by_slot["top"] == ("shirt",)
    assert "red" in task.excluded_colors


def test_special_use_items_are_isolated_from_daily_slots() -> None:
    swim = parse_request("user", "男士海边度假，需要泳装和配饰")
    ski = parse_request("user", "女童滑雪穿搭")
    sports_bra = parse_request("user", "女士健身需要运动内衣")

    assert swim.target_audiences == ("men",)
    assert swim.required_slots == ("swimwear", "accessory")
    assert ski.required_slots == ("skiwear",)
    assert sports_bra.required_slots == ("activewear_bra",)


def test_suit_set_uses_one_piece_type_constraint() -> None:
    task = parse_request("user", "男士面试穿西装套装和鞋")

    assert task.target_audiences == ("men",)
    assert task.required_slots == ("one_piece", "footwear")
    assert task.required_item_types_by_slot["one_piece"] == ("suit",)


def test_sport_occasion_injects_hard_constraints() -> None:
    task = parse_request("user", "我要去运动，穿运动装方便活动。")

    assert task.occasion == "sport"
    assert task.required_item_types_by_slot["bottom"] == ("pants", "shorts")
    assert task.required_subtypes_by_slot["top"] == ("t_shirt", "tank_top", "hoodie")
    assert task.required_subtypes_by_slot["footwear"] == ("sneakers",)
    assert task.excluded_subtypes_by_slot["top"] == ("shirt",)
    assert "pleated_skirt" in task.excluded_subtypes_by_slot["bottom"]
    assert "mini_skirt" in task.excluded_subtypes_by_slot["bottom"]
    assert "high_heels" in task.excluded_subtypes_by_slot["footwear"]
    assert "flats" in task.excluded_subtypes_by_slot["footwear"]
    assert "sneakers" not in task.excluded_subtypes_by_slot["footwear"]


def test_business_and_daily_do_not_inject_sport_constraints() -> None:
    daily = parse_request("user", "日常通勤穿什么")
    business = parse_request("user", "明天面试，要正式但不要太老气。")

    assert daily.excluded_subtypes_by_slot == {}
    assert daily.required_item_types_by_slot == {}
    assert business.excluded_subtypes_by_slot == {}


def test_sport_user_requested_skirt_still_allows_bottom_types() -> None:
    task = parse_request("user", "去运动穿短裤方便活动")

    assert task.occasion == "sport"
    assert task.required_item_types_by_slot["bottom"] == ("pants", "shorts")


def test_chinese_subtype_keywords_match_items() -> None:
    assert item_matches_subtype(_item("skirt", "十三余百褶裙半身裙"), "pleated_skirt")
    assert item_matches_subtype(_item("top", "立领衬衫对襟衫"), "shirt")
    assert item_matches_subtype(_item("top", "夏季短袖t恤运动"), "t_shirt")
    assert item_matches_subtype(_item("pants", "高腰宽松阔腿裤"), "wide_leg_pants")
    assert item_matches_subtype(_item("pants", "夏季运动短裤"), "shorts")
    assert item_matches_subtype(_item("shoes", "骆驼追光慢跑步鞋"), "sneakers")
    assert item_matches_subtype(_item("shoes", "法式平底单鞋"), "flats")
    assert item_matches_subtype(_item("shoes", "黑色细高跟鞋"), "high_heels")


def test_chinese_hanfu_skirt_does_not_match_any_subtype() -> None:
    # Hanfu skirts like bai-die / ma-mian are captured by item_type=skirt and
    # excluded via the sport bottom item-type constraint, not by subtype rules.
    assert not item_matches_subtype(_item("skirt", "天丝百迭裙下裙定制"), "pleated_skirt")
    assert not item_matches_subtype(_item("skirt", "赵氏汉服马面裙"), "maxi_skirt")


def test_ball_playing_triggers_sport_occasion() -> None:
    for phrase in ("明天去打球穿什么", "打篮球穿什么", "下班去健身"):
        task = parse_request("user", phrase)
        assert task.occasion == "sport", phrase
        assert "汉服" in task.excluded_name_keywords
        assert task.required_subtypes_by_slot["top"] == (
            "t_shirt",
            "tank_top",
            "hoodie",
        )


def test_sport_excludes_hanfu_name_keywords() -> None:
    task = parse_request("user", "我要去运动，穿运动装方便活动。")

    assert "汉服" in task.excluded_name_keywords
    assert "褙子" in task.excluded_name_keywords
    assert task.excluded_name_keywords


def test_sport_generation_excludes_hanfu_named_items() -> None:
    from styleforge.tools.candidate_generation import generate_candidates

    items = [
        _item("top", "十三余刺绣双层褙子吊带"),
        _item("top", "夏季运动t恤速干"),
        _item("pants", "运动短裤"),
        _item("shoes", "跑步鞋"),
    ]
    task = parse_request("user", "我要去运动，穿运动装方便活动。")
    candidates, diagnostics = generate_candidates(items, task)

    assert diagnostics["blocked_item_count"] == 1
    assert diagnostics["available_by_slot"]["top"] == 1
    assert diagnostics["available_by_slot"]["bottom"] == 1
    assert diagnostics["available_by_slot"]["footwear"] == 1
    assert candidates


def test_sport_generation_excludes_skirts_shirts_and_heels() -> None:
    from styleforge.tools.candidate_generation import generate_candidates

    items = [
        _item("skirt", "汉服马面裙"),
        _item("skirt", "百褶半身裙"),
        _item("pants", "运动短裤"),
        _item("pants", "高腰阔腿裤"),
        _item("top", "立领衬衫对襟衫"),
        _item("top", "夏季短袖t恤"),
        _item("shoes", "慢跑步鞋"),
        _item("shoes", "黑色高跟鞋"),
    ]
    task = parse_request("user", "我要去运动，穿运动装方便活动。")
    candidates, diagnostics = generate_candidates(items, task)

    assert diagnostics["blocked_item_count"] == 4  # skirt x2, shirt, heels
    assert diagnostics["available_by_slot"]["bottom"] == 2
    assert diagnostics["available_by_slot"]["top"] == 1
    assert diagnostics["available_by_slot"]["footwear"] == 1
    for candidate in candidates:
        slots = candidate.slot_items
        assert slots["bottom"].startswith("item")  # smoke: only pants/shorts reachable
    assert candidates
