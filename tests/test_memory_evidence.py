"""Behavior-evidence folding matrix: every event rule folds into evidence.

Covers the deterministic side of the memory pipeline (``memory_evidence``):
event type -> polarity/strength mapping, feedback sign, wardrobe adopt/remove,
explicit strength dispatch, category induction boundaries (2/3/6 items, both
polarities), item_replaced targeting, and the ``never-global`` contextual scope
rule for weak behavior claims.

The inputs use realistic user phrasing in ``context.request`` so the scoped
evidence carries occasion words the way a real interaction would.
"""

from __future__ import annotations

from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.interaction_event_repository import record_event
from styleforge.repositories.preference_model_repository import get_preference_by_key, list_preferences
from styleforge.services.memory_aggregator import apply_evidence
from styleforge.services.memory_evidence import behavior_evidence_from_event

from tests.helpers import make_item


def _fold(connection, user_id: str, event_type: str, **kwargs) -> list[dict]:
    """Record one event, fold its evidence, return the produced evidence."""
    event = record_event(connection, user_id, event_type, **kwargs)
    evidence = behavior_evidence_from_event(connection, event)
    apply_evidence(connection, user_id, evidence)
    return evidence


def _item_claim(evidence: list[dict], value: str) -> dict:
    return next(e for e in evidence if e["attribute"] == "item" and e["value"] == value)


def _color_claim(evidence: list[dict], value: str) -> dict:
    return next(e for e in evidence if e["attribute"] == "color" and e["value"] == value)


def _category_claims(evidence: list[dict]) -> list[dict]:
    return [e for e in evidence if e["attribute"] == "category"]


def test_feedback_sign_splits_polarity_and_strength(db_dsn: str) -> None:
    """好评折叠 positive 0.7 + color 0.2；差评折叠 negative 0.7 + color 0.1."""
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        upsert_items(connection, [make_item("dress-1", "dress", "黑色连衣裙", "black")], "test")
        good = _fold(
            connection, "u", "feedback_submitted",
            context={"request": "这套约会穿很合适", "item_ids": ["dress-1"]},
            features={"feedback": "positive"},
        )
        assert _item_claim(good, "dress-1")["strength"] == 0.7
        assert _item_claim(good, "dress-1")["polarity"] == "positive"
        assert _color_claim(good, "black")["strength"] == 0.2
        assert _color_claim(good, "black")["polarity"] == "positive"

        bad = _fold(
            connection, "u", "feedback_submitted",
            context={"request": "这套不行，太老气了", "item_ids": ["dress-1"]},
            features={"feedback": "negative"},
        )
        assert _item_claim(bad, "dress-1")["strength"] == 0.7
        assert _item_claim(bad, "dress-1")["polarity"] == "negative"
        assert _color_claim(bad, "black")["strength"] == 0.1
        # 两次差评压过一次好评后，净极性翻转为 negative。
        _fold(
            connection, "u", "feedback_submitted",
            context={"request": "这套还是不行", "item_ids": ["dress-1"]},
            features={"feedback": "negative"},
        )
        pref = get_preference_by_key(connection, "u", "garment", "item", "dress-1")
        assert pref["polarity"] == "negative"
        assert pref["contradiction_count"] == 2


def test_wardrobe_adopt_vs_remove_strengths(db_dsn: str) -> None:
    """采纳单品 +0.5，删除单品 -0.15，颜色归因随极性."""
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        upsert_items(connection, [make_item("top-1", "top", "白衬衫", "white")], "test")
        adopted = _fold(
            connection, "u", "wardrobe_adopted",
            item_id="top-1", context={"request": "这件就留着吧"},
        )
        assert _item_claim(adopted, "top-1")["strength"] == 0.5
        assert _item_claim(adopted, "top-1")["polarity"] == "positive"

        removed = _fold(
            connection, "u", "wardrobe_removed",
            item_id="top-1", context={"request": "穿腻了，删了吧"},
        )
        assert _item_claim(removed, "top-1")["strength"] == 0.15
        assert _item_claim(removed, "top-1")["polarity"] == "negative"


def test_explicit_strength_dispatch_and_long_term(db_dsn: str) -> None:
    """explicit_preference 自带 claim：手动 0.9 / 权重 0.3，均直达 long_term."""
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        manual = _fold(
            connection, "u", "explicit_preference",
            features={"dimension": "style", "attribute": "style", "value": "简约",
                      "polarity": "positive", "strength": 0.9},
        )
        assert manual[0]["strength"] == 0.9
        assert manual[0]["scope"] == {"type": "global"}
        assert manual[0]["source"] == "explicit_statement"

        weight = _fold(
            connection, "u", "explicit_preference",
            features={"dimension": "style", "attribute": "style", "value": "复古",
                      "polarity": "positive", "strength": 0.3},
        )
        assert weight[0]["strength"] == 0.3
        assert weight[0]["scope"] == {"type": "global"}

        # Both promote straight to long-term with no decay.
        rows = {r["value"]: r for r in list_preferences(connection, "u")}
        assert rows["简约"]["lifecycle"] == "long_term"
        assert rows["简约"]["decay_policy"] == "none"
        assert rows["复古"]["lifecycle"] == "long_term"


def test_category_induction_two_items_stays_item_level(db_dsn: str) -> None:
    """同品类 2 件被拒只留 item 级，绝不归纳品类偏好."""
    initialize_database(db_dsn)
    items = [
        make_item("dress-1", "dress", "连衣裙一", "red"),
        make_item("dress-2", "dress", "连衣裙二", "blue"),
    ]
    with database_session(db_dsn) as connection:
        upsert_items(connection, items, "test")
        first = _fold(
            connection, "u", "outfit_rejected",
            context={"request": "这套约会不合适", "item_ids": ["dress-1"]},
        )
        assert _category_claims(first) == []
        second = _fold(
            connection, "u", "outfit_rejected",
            context={"request": "这套也差点意思", "item_ids": ["dress-2"]},
        )
        assert _category_claims(second) == []


def test_category_induction_emits_at_three_and_six(db_dsn: str) -> None:
    """3 件同品类不同单品出第一条 category，6 件出第二条（count % 3 边界）."""
    initialize_database(db_dsn)
    items = [
        make_item(f"dress-{i}", "dress", f"连衣裙{i}", color)
        for i, color in zip(range(1, 7), ["red", "blue", "green", "black", "white", "gray"])
    ]
    with database_session(db_dsn) as connection:
        upsert_items(connection, items, "test")
        seen_category = 0
        for i in range(1, 7):
            evidence = _fold(
                connection, "u", "outfit_rejected",
                context={"request": "换一套吧", "item_ids": [f"dress-{i}"]},
            )
            seen_category += len(_category_claims(evidence))
        assert seen_category == 2


def test_category_induction_negative_polarity(db_dsn: str) -> None:
    """同品类 3 件全被拒 → 归纳出 category *negative*，而非只测 positive 归纳."""
    initialize_database(db_dsn)
    items = [
        make_item("dress-1", "dress", "连衣裙一", "red"),
        make_item("dress-2", "dress", "连衣裙二", "blue"),
        make_item("dress-3", "dress", "连衣裙三", "green"),
    ]
    with database_session(db_dsn) as connection:
        upsert_items(connection, items, "test")
        claims: list[dict] = []
        for i in range(1, 4):
            claims += _fold(
                connection, "u", "outfit_rejected",
                context={"request": "都不太行", "item_ids": [f"dress-{i}"]},
            )
        induced = [c for c in claims if c["attribute"] == "category"]
        assert len(induced) == 1
        assert induced[0]["value"] == "dress"
        assert induced[0]["polarity"] == "negative"


def test_item_replaced_only_targets_replaced_ids(db_dsn: str) -> None:
    """item_replaced 只对 replaced_item_ids 折叠，不波及 replacement 与无关单品."""
    initialize_database(db_dsn)
    items = [
        make_item("coat-1", "outwear", "灰色大衣", "gray"),
        make_item("blazer-1", "outwear", "深蓝西装外套", "navy"),
    ]
    with database_session(db_dsn) as connection:
        upsert_items(connection, items, "test")
        evidence = _fold(
            connection, "u", "item_replaced",
            context={"request": "外套换件西装吧，这大衣太随意了"},
            features={"replaced_item_ids": ["coat-1"], "replacement_item_ids": ["blazer-1"]},
        )
        assert _item_claim(evidence, "coat-1")["strength"] == 0.25
        assert _item_claim(evidence, "coat-1")["polarity"] == "negative"
        assert not any(e["value"] == "blazer-1" for e in evidence)


def test_behavior_evidence_never_global(db_dsn: str) -> None:
    """行为弱证据一律 contextual 且携带场景词，绝不写 global（防旧 bug 复发）."""
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        upsert_items(connection, [make_item("top-1", "top", "白衬衫", "white")], "test")
        evidence = _fold(
            connection, "u", "outfit_selected",
            context={"request": "通勤穿这套很合适", "item_ids": ["top-1"]},
        )
        assert evidence
        for claim in evidence:
            assert claim["scope"]["type"] == "contextual"
            assert claim["scope"]["occasions"] == ["通勤"]
