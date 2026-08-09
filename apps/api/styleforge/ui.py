"""Streamlit user interface for the local StyleForge API."""

from __future__ import annotations

import base64
import os
from typing import Any

import requests
import streamlit as st


API_URL = os.getenv("STYLEFORGE_API_URL", "http://127.0.0.1:8000").rstrip("/")


def _absolute_url(path: str) -> str:
    return path if path.startswith("http") else f"{API_URL}{path}"


def _request(method: str, path: str, **kwargs) -> Any:
    timeout = kwargs.pop("timeout", 120)
    response = requests.request(method, f"{API_URL}{path}", timeout=timeout, **kwargs)
    if response.ok:
        return response.json()
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        detail = response.text
    raise RuntimeError(f"API {response.status_code}: {detail}")


st.set_page_config(page_title="StyleForge", page_icon="👗", layout="wide")
st.title("StyleForge · 个人衣柜多 Agent 穿搭")
st.caption(
    "FashionCLIP 负责理解衣服；语义三 Agent（检索 / 组合 / 评审）负责理解需求、"
    "检索候选、组合方案与评审判定，可离线回退确定性约束引擎。"
)

with st.sidebar:
    st.subheader("连接")
    st.code(API_URL)
    user_id = st.text_input("用户 ID", value="demo-user")
    if st.button("检查服务"):
        try:
            st.success(_request("GET", "/health"))
        except Exception as error:
            st.error(str(error))
    with st.sidebar.expander("我的偏好 · 五维权重", expanded=False):
        try:
            current = _request(
                "GET", f"/preferences/{user_id}/evaluation"
            ).get("weights", {})
        except Exception:
            current = {}
        dimension_labels = (
            ("request_relevance", "需求还原", 0.25),
            ("request_specificity", "请求特异", 0.25),
            ("outfit_coordination", "搭配协调", 0.20),
            ("wearability", "实穿", 0.15),
            ("freshness", "新鲜", 0.15),
        )
        values: dict[str, float] = {}
        for key, label, default in dimension_labels:
            value = current.get(key, default)
            try:
                percent = int(round(float(value) * 100))
            except (TypeError, ValueError):
                percent = int(default * 100)
            values[key] = (
                st.slider(label, 0, 100, percent, key=f"evaluation-{key}") / 100
            )
        if st.button("保存偏好", key="save-evaluation"):
            try:
                _request(
                    "PUT",
                    f"/preferences/{user_id}/evaluation",
                    json={"weights": values},
                )
                st.success("已保存，下次推荐按新权重生效")
            except Exception as error:
                st.error(str(error))
        st.caption("权重影响检索侧重、组合取舍与最终评分（保存后自动归一化）。")

recommend_tab, wardrobe_tab, import_tab, catalog_tab = st.tabs(
    ("穿搭建议", "我的衣柜", "订单导入", "添加目录衣物")
)

_DECISION_LABELS = {
    "accept": "✅ 采纳",
    "recompose": "🔄 重新组合",
    "retrieve_more": "🔍 扩展检索",
    "wardrobe_gap": "🧥 衣橱缺口",
}


def _decision_label(decision: str) -> str:
    return _DECISION_LABELS.get(decision, decision or "—")


def _render_semantic_status(payload: dict[str, Any]) -> None:
    """Top-of-results summary bar shown only when the semantic chain ran."""
    if not payload.get("llm_enabled"):
        return
    metric_columns = st.columns(4)
    metric_columns[0].metric("决策", _decision_label(payload.get("decision", "")))
    metric_columns[1].metric("LLM 调用", payload.get("llm_call_count", 0))
    metric_columns[2].metric("回退次数", payload.get("fallback_count", 0))
    metric_columns[3].metric("候选池", (payload.get("pool") or {}).get("total", 0))
    if payload.get("degraded_reason"):
        st.warning(f"本次有环节降级：{payload['degraded_reason']}")
    best_effort = payload.get("best_effort") or {}
    missing = best_effort.get("missing_items", [])
    if missing:
        details = "、".join(
            f"{item.get('category')}({'/'.join(item.get('desired_features', []))})"
            for item in missing
        )
        st.warning(f"衣橱缺少必要品类：{details}")
    if best_effort.get("feedback"):
        st.info(f"评审反馈：{best_effort['feedback']}")


def _render_signature(payload: dict[str, Any]) -> None:
    signature = payload.get("request_signature")
    if not signature:
        return
    with st.expander("🧭 请求画像与检索计划"):
        st.markdown(f"**主题**：{signature.get('theme', '')}")
        if signature.get("unique_mood"):
            st.markdown("**独特情绪**：" + "、".join(signature["unique_mood"]))
        if signature.get("practical_context"):
            st.markdown("**实际场景**：" + "、".join(signature["practical_context"]))
        tendencies = signature.get("generic_tendencies_to_avoid", [])
        if tendencies:
            st.markdown("**要避免的泛化倾向**：")
            for tendency in tendencies:
                st.write(f"- {tendency}")
        plans = payload.get("retrieval_plans", [])
        if plans:
            st.markdown("**检索计划（英文，喂 FashionCLIP）**：")
            for plan in plans:
                st.write(
                    f"- `{plan.get('type')}` 权重 {plan.get('score_weight')}：{plan.get('query')}"
                )


def _render_pool(payload: dict[str, Any]) -> None:
    pool = payload.get("pool")
    if not pool:
        return
    with st.expander("🗂 候选池 Top-50"):
        item_ids = pool.get("item_ids", [])
        st.write(f"共 {pool.get('total', len(item_ids))} 件单品")
        if item_ids:
            preview = "、".join(item_ids[:12])
            suffix = " …" if len(item_ids) > 12 else ""
            st.caption(f"单品 ID（前 12）：{preview}{suffix}")
        transfer = pool.get("quota_transfer_log", [])
        if transfer:
            st.caption(
                "品类配额转移："
                + "、".join(
                    f"{item.get('from_category')}→{item.get('to_category')}×{item.get('transferred')}"
                    for item in transfer
                )
            )


def _render_critic(payload: dict[str, Any]) -> None:
    critic = payload.get("critic")
    if not critic:
        return
    with st.expander("⚖️ 评审判定"):
        assessment = critic.get("outfit_assessment", {})
        dimensions = assessment.get("dimension_scores", {})
        st.markdown(f"**首选方案**：{assessment.get('outfit_id', '')}")
        metric_columns = st.columns(5)
        for column, (label, key) in zip(
            metric_columns,
            (
                ("需求还原", "request_relevance"),
                ("请求特异", "request_specificity"),
                ("搭配协调", "outfit_coordination"),
                ("实穿性", "wearability"),
                ("新鲜感", "freshness"),
            ),
        ):
            value = dimensions.get(key)
            column.metric(label, value if value is not None else "—")
        if assessment.get("reasoning"):
            st.write(assessment["reasoning"])
        if assessment.get("improvements"):
            st.caption(f"改进建议：{assessment['improvements']}")
        explanation = critic.get("explanation_assessment", {})
        if not explanation.get("grounded"):
            claims = explanation.get("unsupported_claims", [])
            st.warning("Composer 解释存在无单品依据的主张：" + "、".join(claims))
        alternatives = critic.get("alternatives", [])
        if alternatives:
            st.markdown("**备选方案**：")
            for alternative in alternatives:
                st.write(f"- {alternative.get('outfit_id')}：{alternative.get('strength', '')}")
        if critic.get("feedback"):
            st.info(f"评审反馈：{critic['feedback']}")


def _render_recommendations(payload: dict[str, Any]) -> None:
    result = payload.get("result", {})
    structured_result = payload.get("structured_result", {})
    recommendations = structured_result.get("recommendations", [])
    _render_semantic_status(payload)
    if not recommendations:
        st.warning("当前衣柜没有满足全部约束的完整搭配。")
        st.json(payload.get("diagnostics", {}))
        return
    preferred_id = (
        (payload.get("critic") or {}).get("outfit_assessment", {}).get("outfit_id", "")
    )
    for rank, recommendation in enumerate(recommendations, start=1):
        if not isinstance(recommendation, dict):
            st.warning(str(recommendation))
            continue
        title = f"方案 {rank} · {recommendation.get('score', 0):.1f} 分"
        if recommendation.get("outfit_id") == preferred_id:
            title = f"⭐ {title}（首选）"
        st.subheader(title)
        item_ids = recommendation.get("item_ids", [])
        items = recommendation.get("items", [])
        if items:
            item_ids = [item.get("item_id") for item in items]
        columns = st.columns(max(1, len(item_ids)))
        for column, item_id in zip(columns, item_ids):
            if not item_id:
                continue
            with column:
                try:
                    item = _request("GET", f"/items/{item_id}")
                    if item.get("image_status") == "available":
                        st.image(
                            _absolute_url(f"/items/{item_id}/image"),
                            use_container_width=True,
                        )
                    else:
                        st.caption("暂无图片，当前使用文字嵌入")
                    st.markdown(f"**{item['name']}**")
                    st.caption(f"{item['item_type']} · {item['color']}")
                except Exception:
                    st.caption(item_id)
        for reason in recommendation.get("reasons", []):
            st.write(f"- {reason}")
    _render_signature(payload)
    _render_pool(payload)
    _render_critic(payload)
    advice = result.get("advice", []) if isinstance(result, dict) else []
    if advice:
        with st.expander("完整搭配说明", expanded=True):
            for text in advice:
                st.write(text)
    with st.expander("Agent 执行轨迹"):
        st.json(payload.get("trace", []))
    with st.expander("检索与生成诊断"):
        st.json(payload.get("diagnostics", {}))


with recommend_tab:
    request_text = st.text_area(
        "今天想怎么穿？",
        value="明天参加互联网公司的面试，希望正式但不要太老气，不穿红色。",
        height=110,
        key="recommend-request",
    )
    max_results = st.slider("推荐套数", 1, 5, 3, key="recommend-max")
    if st.button("生成搭配", type="primary", use_container_width=True):
        try:
            with st.spinner("多 Agent 正在检索、组合并复核..."):
                payload = _request(
                    "POST",
                    "/recommendations",
                    json={
                        "user_id": user_id,
                        "request": request_text,
                        "max_results": max_results,
                    },
                )
            st.session_state["recommend_result"] = {
                "request": request_text,
                "payload": payload,
            }
            st.session_state.pop("recommend_error", None)
        except Exception as error:
            st.session_state["recommend_error"] = str(error)
    last = st.session_state.get("recommend_result")
    if last:
        st.caption(f"当前搭配基于：「{last['request']}」")
        if last["request"] != request_text:
            st.caption("问题已修改但未重新生成，点击「生成搭配」更新结果。")
        _render_recommendations(last["payload"])
    if st.session_state.get("recommend_error"):
        st.error(st.session_state["recommend_error"])
        st.session_state.pop("recommend_error", None)

with wardrobe_tab:
    if st.button("刷新衣柜", use_container_width=True):
        st.rerun()
    try:
        wardrobe = _request("GET", f"/wardrobes/{user_id}")
        st.metric("可用衣物", wardrobe["count"])
        items = wardrobe.get("items", [])
        for start in range(0, len(items), 4):
            columns = st.columns(4)
            for column, item in zip(columns, items[start : start + 4]):
                with column:
                    if item.get("image_status") == "available":
                        st.image(_absolute_url(item["image_url"]), use_container_width=True)
                    else:
                        st.caption("暂无实拍图")
                    st.markdown(f"**{item['name']}**")
                    st.caption(f"{item['item_type']} · {item['color']}")
                    if str(item.get("source", "")).startswith("personal-"):
                        image_file = st.file_uploader(
                            "上传实拍图",
                            type=("jpg", "jpeg", "png", "webp"),
                            key=f"image-{item['item_id']}",
                        )
                        if image_file is not None and st.button(
                            "保存并重新嵌入",
                            key=f"upload-{item['item_id']}",
                        ):
                            with st.spinner("正在保存图片并生成图像嵌入..."):
                                result = _request(
                                    "POST",
                                    f"/wardrobes/{user_id}/items/{item['item_id']}/image",
                                    json={
                                        "filename": image_file.name,
                                        "content_base64": base64.b64encode(
                                            image_file.getvalue()
                                        ).decode("ascii"),
                                    },
                                    timeout=600,
                                )
                            if result.get("embedding", {}).get("status") == "completed":
                                st.success("图片和图像嵌入已更新")
                            else:
                                st.warning("图片已保存，但嵌入失败，可稍后重试")
                            st.rerun()
                    if st.button("移出衣柜", key=f"remove-{item['item_id']}"):
                        _request(
                            "DELETE",
                            f"/wardrobes/{user_id}/items/{item['item_id']}",
                        )
                        st.rerun()
    except Exception as error:
        st.error(str(error))

with import_tab:
    st.subheader("从购物订单建立个人衣柜")
    st.caption("先预览和人工确认；订单不会直接全部加入衣柜。")
    order_file = st.file_uploader("订单文件", type=("xlsx",), key="order-workbook")
    default_audience = st.selectbox(
        "无法从商品名识别时使用的默认人群",
        ("", "women", "men", "girls", "boys", "baby", "life"),
        format_func=lambda value: "不设置，逐条确认" if not value else value,
    )
    if order_file is not None and st.button("生成导入预览", use_container_width=True):
        try:
            with st.spinner("正在解析、脱敏并识别服饰候选..."):
                preview = _request(
                    "POST",
                    f"/wardrobes/{user_id}/imports",
                    json={
                        "filename": order_file.name,
                        "content_base64": base64.b64encode(order_file.getvalue()).decode(
                            "ascii"
                        ),
                        "default_audience": default_audience,
                    },
                    timeout=300,
                )
            st.session_state["wardrobe_import_preview"] = preview
        except Exception as error:
            st.error(str(error))

    preview = st.session_state.get("wardrobe_import_preview")
    if preview and preview.get("batch", {}).get("user_id") != user_id:
        st.session_state.pop("wardrobe_import_preview", None)
        preview = None
    if preview:
        batch = preview.get("batch", {})
        statistics = batch.get("statistics", {})
        metric_columns = st.columns(4)
        metric_columns[0].metric("订单行", statistics.get("total_rows", 0))
        metric_columns[1].metric("已收货行", statistics.get("eligible_order_rows", 0))
        metric_columns[2].metric("服饰候选", statistics.get("candidate_rows", 0))
        metric_columns[3].metric("默认排除", statistics.get("excluded_rows", 0))
        show_excluded = st.checkbox("显示未识别或非服饰记录", value=False)
        visible_rows = [
            row
            for row in preview.get("rows", [])
            if show_excluded or row.get("decision") != "excluded"
        ]
        editor_rows = [
            {
                "选择": row.get("decision") in {"candidate", "committed"},
                "row_id": row.get("row_id"),
                "订单状态": row.get("order_status", ""),
                "订单准入": row.get("order_eligibility", "unknown"),
                "排除原因": row.get("order_eligibility_reason", ""),
                "商品名称": row.get("product_name", ""),
                "款式": row.get("variant_text", ""),
                "品类": row.get("predicted_item_type", ""),
                "子类": row.get("predicted_subtype", ""),
                "颜色": row.get("predicted_color", ""),
                "尺码": row.get("predicted_size", ""),
                "人群": row.get("predicted_audience", ""),
                "置信度": row.get("confidence", 0),
            }
            for row in visible_rows
        ]
        edited_rows = st.data_editor(
            editor_rows,
            disabled=(
                "row_id",
                "订单状态",
                "订单准入",
                "排除原因",
                "商品名称",
                "款式",
                "置信度",
            ),
            hide_index=True,
            use_container_width=True,
            key=f"wardrobe-import-editor-{batch.get('batch_id')}",
        )
        editable_records = (
            edited_rows.to_dict("records")
            if hasattr(edited_rows, "to_dict")
            else list(edited_rows)
        )
        auto_embed = st.checkbox("确认后自动生成FashionCLIP嵌入", value=True)
        gender_override = st.selectbox(
            "你的性别（可选）",
            ("", "women", "men"),
            format_func=lambda value: (
                "按商品名自动判断（推荐）"
                if not value
                else ("女" if value == "women" else "男")
            ),
        )
        st.caption(
            "无需手动填写任何字段：已识别品类和性别的记录直接提交，"
            "性别判断不出的用你选的性别（未选则默认女），未识别品类的记录自动跳过。"
        )
        if st.button("确认所选记录并加入衣柜", type="primary", use_container_width=True):
            selected = [row for row in editable_records if row.get("选择")]
            if not selected:
                st.warning("请至少选择一条记录")
            else:
                skipped = [
                    row for row in selected if not str(row.get("品类", "")).strip()
                ]
                keep = [row for row in selected if str(row.get("品类", "")).strip()]
                if skipped:
                    names = "、".join(
                        str(row.get("商品名称", ""))[:16] or row.get("row_id", "")
                        for row in skipped[:5]
                    )
                    more = f" 等 {len(skipped)} 条" if len(skipped) > 5 else ""
                    st.warning(
                        f"以下记录未识别品类，无法加入衣柜，已自动跳过：{names}{more}"
                    )
                if keep:
                    selections = [
                        {
                            "row_id": row["row_id"],
                            "item_type": row["品类"],
                            "subtype": row["子类"],
                            "color": row["颜色"],
                            "size": row["尺码"],
                            "audience": str(row.get("人群", "")).strip()
                            or gender_override
                            or "women",
                        }
                        for row in keep
                    ]
                    try:
                        with st.spinner("正在提交衣柜并生成增量嵌入..."):
                            result = _request(
                                "POST",
                                f"/wardrobes/{user_id}/imports/{batch['batch_id']}/commit",
                                json={"selections": selections, "auto_embed": auto_embed},
                                timeout=900,
                            )
                        st.success(f"已加入 {result['committed_item_count']} 件衣物")
                        embedding = result.get("embedding") or {}
                        if embedding.get("status") == "failed":
                            st.warning(f"衣柜已保存，但嵌入失败：{embedding.get('error', '')}")
                        st.session_state.pop("wardrobe_import_preview", None)
                    except Exception as error:
                        st.error(str(error))

with catalog_tab:
    audience = st.selectbox(
        "目标人群",
        ("", "women", "men", "girls", "boys", "baby", "life"),
        format_func=lambda value: "全部" if not value else value,
    )
    catalog_source = st.selectbox(
        "数据来源",
        ("", "polyvore", "mytheresa"),
        format_func=lambda value: "全部" if not value else value,
    )
    query = st.text_input("按名称、颜色或描述搜索", value="black")
    item_type = st.text_input("品类（可选，如 top、pants、shoes）")
    if st.button("搜索目录", use_container_width=True):
        try:
            params = {"q": query, "limit": 24}
            if audience:
                params["audience"] = audience
            if catalog_source:
                params["source"] = catalog_source
            if item_type.strip():
                params["item_type"] = item_type.strip()
            catalog = _request("GET", "/catalog/search", params=params)
            items = catalog.get("items", [])
            for start in range(0, len(items), 4):
                columns = st.columns(4)
                for column, item in zip(columns, items[start : start + 4]):
                    with column:
                        st.image(_absolute_url(item["image_url"]), use_container_width=True)
                        st.markdown(f"**{item['name']}**")
                        st.caption(f"{item['source']} · {item['gender']}")
                        st.caption(f"{item['item_type']} · {item['color']}")
                        if st.button("加入衣柜", key=f"add-{item['item_id']}"):
                            _request(
                                "POST",
                                f"/wardrobes/{user_id}/items",
                                json={"item_id": item["item_id"]},
                            )
                            st.success("已加入衣柜")
        except Exception as error:
            st.error(str(error))
