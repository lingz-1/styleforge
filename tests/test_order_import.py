import io

import pytest

from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.personal_embedding_repository import (
    PersonalEmbeddingStore,
    upsert_personal_embedding,
)
from styleforge.repositories.wardrobe_import_repository import (
    commit_import_rows,
    create_import_preview,
)
from styleforge.services.order_import import parse_order_workbook


def _workbook_bytes() -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "订单数据"
    sheet.append(
        (
            "订单号",
            "订单提交时间",
            "订单状态",
            "店铺名称",
            "商品名称",
            "商品链接",
            "型号款式",
            "商品数量",
            "商品金额",
            "实付金额",
        )
    )
    sheet.append(
        (
            "order-1",
            "2026-01-01 12:00:00",
            "交易成功",
            "服装店",
            "学院风蓝白格纹女翻领短袖衬衫",
            "https://item.taobao.com/item.htm?id=123&mi_id=tracking",
            "蓝白格;160/84A",
            1,
            "¥99.00",
            "¥89.00",
        )
    )
    sheet.append(
        (None, None, None, None, "云南白药牙膏", "", "215g", 1, "¥17.00", "¥17.00")
    )
    sheet.append(
        (
            "order-2",
            "2026-02-01 12:00:00",
            "交易关闭",
            "汉服店",
            "汉服吊带下裙套装",
            "https://item.taobao.com/item.htm?id=456",
            "浅湖蓝长衫;F",
            1,
            "¥219.00",
            "",
        )
    )
    sheet.append(
        (
            "order-3",
            "2026-03-01 12:00:00",
            "买家已付款",
            "鞋店",
            "女款黑色平底鞋",
            "https://item.taobao.com/item.htm?id=789",
            "黑色;38",
            1,
            "¥129.00",
            "¥129.00",
        )
    )
    sheet.append(
        (
            "order-4",
            "2026-04-01 12:00:00",
            "交易成功",
            "半身裙店",
            "蓝色百褶半身裙",
            "https://item.taobao.com/item.htm?id=101",
            "蓝色;M",
            1,
            "¥159.00",
            "¥149.00",
        )
    )
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def test_parse_order_workbook_sanitizes_classifies_and_filters_status() -> None:
    parsed = parse_order_workbook(_workbook_bytes(), default_audience="women")

    assert parsed.platform == "taobao"
    assert len(parsed.rows) == 5
    assert parsed.rows[0].predicted_item_type == "top"
    assert parsed.rows[0].predicted_subtype == "shirt"
    assert parsed.rows[0].canonical_url == "https://item.taobao.com/item.htm?id=123"
    assert parsed.rows[0].external_order_id_hash
    assert parsed.rows[0].order_eligibility == "eligible"
    assert parsed.rows[1].order_status == "交易成功"
    assert (
        parsed.rows[1].external_order_id_hash
        == parsed.rows[0].external_order_id_hash
    )
    assert parsed.rows[1].decision == "excluded"
    assert parsed.rows[2].predicted_item_type == "outwear"
    assert parsed.rows[2].predicted_color == "湖蓝"
    assert parsed.rows[2].order_eligibility == "ineligible"
    assert parsed.rows[2].decision == "excluded"
    assert parsed.rows[3].order_eligibility == "ineligible"
    assert parsed.rows[3].decision == "excluded"
    assert parsed.rows[4].decision == "candidate"
    assert parsed.statistics()["candidate_rows"] == 2
    assert parsed.statistics()["eligible_order_rows"] == 3
    assert parsed.statistics()["ineligible_order_rows"] == 2
    assert parsed.statistics()["by_order_status"] == {
        "交易成功": 3,
        "买家已付款": 1,
        "交易关闭": 1,
    }


@pytest.mark.parametrize(
    "product_name",
    [
        "【下拉享优惠】高洁丝安睡裤贴身16条全包围防漏夜安裤卫生巾姨妈",
        "INTEL I5 12490F 微星B760M主板CPU套装",
        "碳酸NEO 40cm棉花娃娃娃衣睡衣",
        "ukiss修眉刀剪女士安全型剃刮眉毛刀神器套装",
        "旅行一次性睡袋酒店火车卧铺双人床三四件套床单被套枕套",
        "黄豆豆拼豆MARD手工diy套装 材料包",
        "雕刻刀手工剪纸刻刀套装木雕纸雕小笔刀",
        "美甲搓条修指甲锉磨甲砂条美甲工具套装",
        "威露士衣物消毒液洗衣服除菌液杀菌内衣裤适用",
        "心形蛋糕迷你首饰盒复古牛仔便携戒指珠宝收纳旅行礼物",
        "EMINUTE少女心小奶狗毛绒挂件公仔包包挂饰礼物钥匙扣",
    ],
)
def test_non_fashion_products_are_excluded(product_name: str) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(("订单号", "订单状态", "商品名称", "型号款式"))
    sheet.append(("order-1", "交易成功", product_name, "均码"))
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()

    parsed = parse_order_workbook(output.getvalue(), default_audience="women")

    assert len(parsed.rows) == 1
    assert parsed.rows[0].decision == "excluded"
    assert parsed.rows[0].decision_reason == "explicit_non_fashion_signal"


def test_underwear_and_skirt_subtypes_are_reclassified() -> None:
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(("订单号", "订单状态", "商品名称", "型号款式"))
    sheet.append(("order-1", "交易成功", "纯棉抗菌裆三角裤学生云朵内裤女", "M"))
    sheet.append(("order-2", "交易成功", "高腰无痕防走光安全裤平角内裤女", "均码"))
    sheet.append(("order-3", "交易成功", "赵氏汉服马面裙西装半身裙两片裙", "S"))
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()

    parsed = parse_order_workbook(output.getvalue(), default_audience="women")

    underpants = [r for r in parsed.rows if "内裤" in r.product_name]
    assert len(underpants) == 2
    assert all(r.predicted_item_type == "underwear" for r in underpants)
    assert all(r.predicted_subtype == "underpants" for r in underpants)
    skirt = parsed.rows[2]
    assert skirt.predicted_item_type == "skirt"


def test_successful_order_with_refund_signal_is_excluded() -> None:
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(("订单号", "订单状态", "退款状态", "商品名称", "型号款式"))
    sheet.append(("order-1", "交易成功", "退款成功", "蓝色衬衫", "蓝色;M"))
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()

    parsed = parse_order_workbook(output.getvalue(), default_audience="women")

    assert parsed.rows[0].predicted_item_type == "top"
    assert parsed.rows[0].order_eligibility == "ineligible"
    assert parsed.rows[0].decision == "excluded"
    assert parsed.rows[0].decision_reason == "refund_status:退款成功"


def test_preview_commit_and_personal_embedding_are_idempotent(tmp_path) -> None:
    np = pytest.importorskip("numpy")
    database_path = tmp_path / "styleforge.db"
    image_root = tmp_path / "personal-images"
    image_root.mkdir()
    parsed = parse_order_workbook(_workbook_bytes(), default_audience="women")
    initialize_database(database_path)

    with database_session(database_path) as connection:
        batch_id, created, refreshed = create_import_preview(
            connection,
            user_id="test-user",
            source_filename="orders.xlsx",
            workbook=parsed,
        )
        (
            repeated_batch_id,
            repeated_created,
            repeated_refreshed,
        ) = create_import_preview(
            connection,
            user_id="test-user",
            source_filename="orders-renamed.xlsx",
            workbook=parsed,
        )
        candidate = connection.execute(
            "SELECT row_id FROM wardrobe_import_rows "
            "WHERE batch_id = ? AND decision = 'candidate' ORDER BY source_row_number LIMIT 1",
            (batch_id,),
        ).fetchone()
        item_ids = commit_import_rows(
            connection,
            user_id="test-user",
            batch_id=batch_id,
            selections=[{"row_id": candidate["row_id"]}],
            image_root=image_root,
        )

    assert created is True
    assert refreshed is False
    assert repeated_created is False
    assert repeated_refreshed is False
    assert repeated_batch_id == batch_id
    assert len(item_ids) == 1

    vector = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    with database_session(database_path) as connection:
        upsert_personal_embedding(
            connection,
            item_id=item_ids[0],
            vector=vector,
            embedding_kind="text",
            model_revision="test",
        )
        scores = PersonalEmbeddingStore(connection).score_items(vector, item_ids)
        personal = connection.execute(
            "SELECT ownership_status, review_status, order_status, "
            "external_order_id_hash FROM personal_wardrobe_items"
        ).fetchone()
        catalog = connection.execute(
            "SELECT source, embedding_status FROM catalog_items WHERE item_id = ?",
            (item_ids[0],),
        ).fetchone()

    assert scores[item_ids[0]] == pytest.approx(1.0)
    assert personal["ownership_status"] == "owned"
    assert personal["review_status"] == "confirmed"
    assert personal["order_status"] == "交易成功"
    assert personal["external_order_id_hash"]
    assert catalog["source"].startswith("personal-")
    assert catalog["embedding_status"] == "ready"


def test_commit_rejects_non_received_row_even_with_manual_attributes(tmp_path) -> None:
    database_path = tmp_path / "styleforge.db"
    image_root = tmp_path / "personal-images"
    image_root.mkdir()
    parsed = parse_order_workbook(_workbook_bytes(), default_audience="women")
    initialize_database(database_path)

    with database_session(database_path) as connection:
        batch_id, _, _ = create_import_preview(
            connection,
            user_id="test-user",
            source_filename="orders.xlsx",
            workbook=parsed,
        )
        closed_row = connection.execute(
            "SELECT row_id FROM wardrobe_import_rows "
            "WHERE batch_id = ? AND order_status = '交易关闭'",
            (batch_id,),
        ).fetchone()
        with pytest.raises(ValueError, match="Only received orders"):
            commit_import_rows(
                connection,
                user_id="test-user",
                batch_id=batch_id,
                selections=[
                    {
                        "row_id": closed_row["row_id"],
                        "item_type": "outwear",
                        "audience": "women",
                    }
                ],
                image_root=image_root,
            )
