"""GroundingContext — thread-scoped grounding continuity (H3a-2).

This module currently holds only the ThreadGroundingView half of the Grounding
Context tree:

    GroundingContext
    ├── Current Turn Grounding      (H3a-3: GroundingResolver)
    ├── ThreadGroundingView         ← this file
    ├── Device/Profile              (H3a-3, reuses legacy resolvers)
    └── Search-before-Ask           (H3a-3/5)

ThreadGroundingView answers "what has this conversation already agreed on?" so
a follow-up does not re-derive the city / date / activity from a bare reply.
The plan froze the cross-turn contract (评审缺口 1):

    Question → Answer → Grounding continuity.

A clarification question names a field (``pending_field``); the next turn's
bare reply ("上海") is then deterministically interpreted as *that field's
answer* instead of being parsed as a fresh request. It lives in
``session_context["thread_grounding"]`` (Redis session cache) — no table.
Deterministic extraction only; the actual NEED_USER → pending_field write path
lands in the Main Graph's clarification_node (H3a-3).
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timezone
from enum import Enum
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from styleforge.orchestration.location_resolver import LocationResolver
from styleforge.tools.calendar.resolver import resolve_temporal_expression
from styleforge.tools.weather.schemas import ContextRequirements, LocationContextRequirement

# curated city table: names that appear verbatim in a request (exact substring
# match — "去上海看音乐剧" → "上海"). Kept small; the H3a-3 resolver reuses the
# legacy LocationResolver for device/profile/global fallbacks.
_CITY_ALIASES = (
    "北京", "上海", "广州", "深圳", "杭州", "成都", "重庆", "西安", "武汉",
    "南京", "苏州", "天津", "长沙", "郑州", "青岛", "大连", "厦门", "香港",
    "澳门", "台北", "三亚", "丽江", "大理", "桂林", "昆明", "贵阳", "哈尔滨",
    "沈阳", "长春", "济南", "合肥", "南昌", "福州", "宁波", "无锡", "佛山",
    "东莞", "珠海", "中山", "惠州", "泉州", "烟台", "温州", "嘉兴", "绍兴",
)
_CITY_ALIASES_KEYS = sorted(_CITY_ALIASES, key=len, reverse=True)

# fallback destination pattern when no curated city matched: 去/到/前往/飞往 X
_DESTINATION_RE = re.compile(r"(?:去|到|前往|飞往|回)([一-龥]{2,4})(?![一-龥])")

# event nouns that mark an activity (观看《风声》)
_EVENT_WORDS = (
    "音乐剧", "歌剧", "演唱会", "演出", "展览", "展会", "会议", "婚礼",
    "晚宴", "聚会", "演唱会", "音乐节", "live", "Live", "show",
)
_ACTIVITY_NOISE = set("去看听逛参加出席欣赏到和")

# relative time expressions → coarse labels (date_expression keeps the raw text)
_RELATIVE_TIME = {
    "今天": "今天", "今晚": "今晚", "明天": "明天", "明晚": "明晚",
    "后天": "后天", "昨晚": "昨晚", "上周": "上周", "这周": "本周",
    "本周": "本周", "下周": "下周", "上个月": "上月", "这个月": "本月",
    "本月": "本月", "下个月": "下月", "下半年": "下半年", "上半年": "上半年",
    "最近": "最近", "近期": "近期", "年底": "年底", "今年": "今年",
    "春节": "春节", "国庆": "国庆", "圣诞": "圣诞", "元旦": "元旦", "中秋": "中秋",
}
_APPROX_RE = re.compile(r"(今天|今晚|明天|明晚|后天|昨晚|上周|这周|本周|下周|上个月|这个月|本月|下个月|下半年|上半年|最近|近期|年底|今年|春节|国庆|圣诞|元旦|中秋|\d{1,2}月)")
_ISO_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

PENDING_FIELD = Literal["destination_city", "date"]


class ThreadGroundingView:
    """Cross-turn grounding facts already agreed in this session (no DB)."""

    __slots__ = (
        "destination_city", "date_expression", "approximate_time",
        "activity", "pending_field", "updated_at",
    )

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        self.destination_city = data.get("destination_city") or None
        self.date_expression = data.get("date_expression") or None
        self.approximate_time = data.get("approximate_time") or None
        self.activity = data.get("activity") or None
        pending = data.get("pending_field")
        self.pending_field = pending if pending in ("destination_city", "date") else None
        self.updated_at = data.get("updated_at") or None

    def to_dict(self) -> dict[str, Any]:
        return {
            "destination_city": self.destination_city,
            "date_expression": self.date_expression,
            "approximate_time": self.approximate_time,
            "activity": self.activity,
            "pending_field": self.pending_field,
            "updated_at": self.updated_at,
        }


def pending_field_for_question(question: str) -> PENDING_FIELD | None:
    """Which ThreadGrounding field a clarification question is asking for.

    Written by the Main Graph's clarification_node (H3a-3); the next turn's
    bare reply is then interpreted as this field's answer.
    """
    text = (question or "").strip()
    if any(word in text for word in ("城市", "哪里", "地点", "哪个城市", "什么地方")):
        return "destination_city"
    if any(word in text for word in ("时间", "日期", "哪一天", "哪天", "几点", "什么时候")):
        return "date"
    return None


def _extract_destination_city(request: str) -> str | None:
    """'去上海看音乐剧' → '上海'. Curated city names first, then a 去/到 pattern.

    The fallback must never treat an event noun as a place: "去婚礼的" is an
    activity, not a destination, so a "婚礼"/"演出"-bearing match yields None
    (a false destination would wrongly flip has_destination and collapse the
    NEED_USER decision).
    """
    text = (request or "").strip()
    for city in _CITY_ALIASES_KEYS:
        if city in text:
            return city
    match = _DESTINATION_RE.search(text)
    if match:
        candidate = match.group(1)
        if any(word in candidate for word in _EVENT_WORDS + _SENSITIVE_EVENT_WORDS):
            return None
        return candidate
    return None


def _extract_date_expression(request: str) -> tuple[str | None, str | None]:
    """(raw_expression, coarse_label) — ISO date, today/next week, 下半年 …"""
    text = (request or "").strip()
    for expr, label in _RELATIVE_TIME.items():
        if expr in text:
            return expr, label
    match = _APPROX_RE.search(text)
    if match:
        return match.group(0), match.group(0)
    match = _ISO_DATE_RE.search(text)
    if match:
        return match.group(1), None
    return None, None


def _extract_activity(request: str) -> str | None:
    """'下半年去看风声音乐剧' → '观看《风声》'. The 1-4 chars before the event
    noun are the entity; a pure verb run (看音乐剧) yields no activity."""
    text = (request or "").strip()
    for word in _EVENT_WORDS:
        idx = text.find(word)
        if idx < 0:
            continue
        prefix = text[max(0, idx - 4):idx]
        cleaned = "".join(char for char in prefix if char not in _ACTIVITY_NOISE).strip()
        if cleaned:
            return f"观看《{cleaned}》"
        return None
    return None


def update_thread_grounding(
    prev: dict[str, Any],
    request: str,
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """Merge one turn's grounding facts into the session view.

    Contract (frozen #1): when ``pending_field`` is set from the previous
    clarification, the whole request is that field's answer — a bare "上海"
    becomes ``destination_city`` without needing a "去X看" structure. Otherwise
    fresh extraction runs. The session stays sensitive to activity from the
    thread (H3a-3 reads this layer after Current Turn).
    """
    view = ThreadGroundingView(prev)
    text = (request or "").strip()
    if view.pending_field is not None:
        if view.pending_field == "destination_city":
            view.destination_city = text or None
        else:
            view.date_expression = text or None
            if text:
                approx = _APPROX_RE.search(text)
                view.approximate_time = approx.group(0) if approx else text
        view.pending_field = None
        stamp = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
        view.updated_at = stamp
        return view.to_dict()

    city = _extract_destination_city(text)
    if city:
        view.destination_city = city
    expr, approx = _extract_date_expression(text)
    if expr:
        view.date_expression = expr
        if approx:
            view.approximate_time = approx
    activity = _extract_activity(text)
    if activity:
        view.activity = activity
    stamp = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    view.updated_at = stamp
    return view.to_dict()


def thread_grounding_to_prompt(view: dict[str, Any]) -> str:
    """Cross-turn confirmation render: '上轮已确认：观演城市 上海 / 活动 …'"""
    lines = ["【上轮已确认】"]
    rendered = False
    if view.get("destination_city"):
        rendered = True
        lines.append(f"观演城市：{view['destination_city']}")
    if view.get("approximate_time"):
        rendered = True
        lines.append(f"时间：{view['approximate_time']}")
    elif view.get("date_expression"):
        rendered = True
        lines.append(f"时间：{view['date_expression']}")
    if view.get("activity"):
        rendered = True
        lines.append(f"活动：{view['activity']}")
    if view.get("pending_field"):
        rendered = True
        lines.append(f"待确认：{view['pending_field']}")
    if not rendered:
        lines.append("（无）")
    return "\n".join(lines)


# ── GroundingResolver (H3a-3): Context → Search → Materiality → Ask ──────────

class GroundingDecision(str, Enum):
    READY = "ready"
    SEARCH_FIRST = "search_first"
    NEED_USER = "need_user"


class GroundingContext(BaseModel):
    """Frozen per-request grounding facts handed to every agent's C layer.

    ``decision`` is the deterministic Search-before-Ask output: READY when no
    resolvable gap exists, SEARCH_FIRST when a missing kind is resolvable by a
    deployed capability, NEED_USER when a gap remains that no capability can
    fill — the Agent must ask the user instead (never guess).
    """

    current_date: str | None = None
    current_city: str | None = None
    destination_city: str | None = None
    date_expression: str | None = None
    explicit_date: str | None = None
    approximate_time: str | None = None
    activity: str | None = None
    location_source: str = "none"  # request|thread|device|profile|global|none
    date_source: str = "none"  # request|thread|none
    confidence: float = 0.0
    decision: GroundingDecision = GroundingDecision.READY
    missing: list[str] = Field(default_factory=list)
    reason: str = ""


# kind → capability keys that can resolve it. CAP_WEB_SEARCH/CAP_WEATHER live in
# local_tools as "web_search"/"weather"; kept as literals to avoid a
# context→tools import edge. (冻结 缺口 4: SEARCH_FIRST 看缺什么而非有没有工具.)
_RESOLVABLE_BY: dict[str, frozenset[str]] = {
    "event_location": frozenset({"web_search"}),
    "event_date": frozenset({"web_search"}),
    "weather": frozenset({"weather"}),
}

_SENSITIVE_EVENT_WORDS = (
    "演出", "音乐剧", "歌剧", "话剧", "舞剧", "演唱会", "音乐节", "展览",
    "展会", "会议", "婚礼", "晚宴", "聚会", "出差", "旅游", "旅行",
    "海边", "度假", "露营", "通勤", "面试",
)
_SEASON_WORDS = ("春天", "夏天", "秋天", "冬天", "春季", "夏季", "秋季", "冬季", "换季")


def _decision_sensitive(
    request: str,
    thread_grounding: dict[str, Any] | None,
) -> bool:
    """Whether the turn needs occasion/weather grounding at all.

    A request naming an event word / season, or a thread that already agreed on
    an activity, stays sensitive — so a bare "上海" answer to a clarification
    keeps driving SEARCH_FIRST / NEED_USER instead of silently going READY.
    """
    text = (request or "").strip()
    if any(word in text for word in _SENSITIVE_EVENT_WORDS):
        return True
    if any(word in text for word in _SEASON_WORDS):
        return True
    if thread_grounding and thread_grounding.get("activity"):
        return True
    return False


class GroundingResolver:
    """Deterministic Context → Search → Materiality → Ask decision maker.

    Reuses the legacy ``LocationResolver`` (device → profile → global for the
    current city) and ``resolve_temporal_expression`` (ISO single-day windows);
    never reverse-geocodes coordinates.
    """

    def __init__(
        self,
        *,
        default_location: str = "",
        today_provider: Callable[[], date] | None = None,
        location_max_age_seconds: int = 1800,
        location_max_accuracy_m: float = 5000.0,
    ) -> None:
        self.default_location = default_location
        self.today_provider = today_provider or (lambda: date.today())
        self._location_resolver = LocationResolver(
            now_provider=lambda: datetime.combine(
                self.today_provider(), time.min, tzinfo=timezone.utc
            ),
            max_age_seconds=location_max_age_seconds,
            max_accuracy_m=location_max_accuracy_m,
        )

    def resolve(
        self,
        request: str,
        *,
        location_context: Any | None = None,
        environment_profile: dict[str, Any] | None = None,
        thread_context: dict[str, Any] | None = None,
        capabilities: frozenset[str] = frozenset(),
    ) -> GroundingContext:
        """Resolve grounding facts for one request (pure; no DB, no I/O).

        Read order (frozen): Current Turn > Thread Grounding > Device/Profile >
        global default. ``thread_context["thread_grounding"]`` carries the
        cross-turn confirmations updated by api.py.
        """
        today = self.today_provider()
        thread = (thread_context or {}).get("thread_grounding") or {}
        current_date = today.isoformat()

        # current_city: device → profile → global. The request's named city is a
        # *destination* the user is travelling to, never where they are now.
        current_city, current_source = self._resolve_current_city(
            location_context, environment_profile
        )

        destination, dest_from = self._resolve_destination(request, thread)
        date_expr, approx, explicit, date_from = self._resolve_date(
            request, thread, today
        )
        activity = _extract_activity(request) or thread.get("activity")

        has_destination = bool(destination)
        has_date = bool(date_expr)
        sensitive = _decision_sensitive(request, thread)
        missing = self._missing_kinds(
            sensitive, has_destination, has_date, current_city or destination
        )
        decision, reason = self._decide(
            sensitive, missing, capabilities, bool(current_city or destination)
        )

        confidence = 0.4
        if has_destination:
            confidence += 0.2
        if has_date:
            confidence += 0.2
        if current_city:
            confidence += 0.2
        if activity:
            confidence += 0.1
        return GroundingContext(
            current_date=current_date,
            current_city=current_city,
            destination_city=destination,
            date_expression=date_expr,
            explicit_date=explicit,
            approximate_time=approx,
            activity=activity,
            location_source=self._source_priority(dest_from, current_source),
            date_source=date_from,
            confidence=round(min(confidence, 1.0), 2),
            decision=decision,
            missing=missing,
            reason=reason,
        )

    # -- internal ---------------------------------------------------------

    def _resolve_current_city(
        self,
        location_context: Any | None,
        environment_profile: dict[str, Any] | None,
    ) -> tuple[str | None, str]:
        resolution = self._location_resolver.resolve(
            ContextRequirements(
                location=LocationContextRequirement(
                    needed=True, allow_profile_default=True
                )
            ),
            device_location=location_context,
            environment_profile=environment_profile,
            global_default=self.default_location,
        )
        if resolution.error_code != "":
            return None, "none"
        if resolution.mode == "named":
            return (resolution.query or None), resolution.source
        # coordinates with no reverse geocoding → no city name (device source)
        return None, "device"

    def _resolve_destination(
        self,
        request: str,
        thread: dict[str, Any],
    ) -> tuple[str | None, str]:
        city = _extract_destination_city(request)
        if city:
            return city, "request"
        if thread.get("destination_city"):
            return thread["destination_city"], "thread"
        return None, "none"

    def _resolve_date(
        self,
        request: str,
        thread: dict[str, Any],
        today: date,
    ) -> tuple[str | None, str | None, str | None, str]:
        expr, approx = _extract_date_expression(request)
        if expr:
            explicit = None
            resolution = resolve_temporal_expression(expr, today=today)
            if resolution.status == "resolved" and resolution.precision == "day":
                explicit = resolution.start_date
            return expr, approx, explicit, "request"
        if thread.get("date_expression"):
            return (
                thread["date_expression"],
                thread.get("approximate_time"),
                None,
                "thread",
            )
        return None, None, None, "none"

    @staticmethod
    def _source_priority(dest_from: str, current_source: str) -> str:
        # destination beats device/profile/global; request beats thread
        if dest_from in ("request", "thread"):
            return dest_from
        return current_source or "none"

    def _missing_kinds(
        self,
        sensitive: bool,
        has_destination: bool,
        has_date: bool,
        has_weather_location: bool,
    ) -> list[str]:
        if not sensitive:
            return []
        missing: list[str] = []
        if not has_destination:
            missing.append("event_location")
        if not has_date:
            missing.append("event_date")
        if not has_weather_location:
            missing.append("weather")
        return missing

    @staticmethod
    def _decide(
        sensitive: bool,
        missing_kinds: list[str],
        capabilities: frozenset[str],
        has_weather_location: bool,
    ) -> tuple[GroundingDecision, str]:
        if not sensitive:
            return GroundingDecision.READY, "no_event_context"
        if not missing_kinds:
            return GroundingDecision.READY, "fully_grounded"
        required: list[str] = []
        for kind in missing_kinds:
            if kind == "weather":
                # get_weather needs a location argument; without a city the
                # capability cannot resolve the gap (冻结 缺口 4).
                if has_weather_location and _RESOLVABLE_BY[kind] & capabilities:
                    required.append(kind)
            elif _RESOLVABLE_BY[kind] & capabilities:
                required.append(kind)
        if required:
            return GroundingDecision.SEARCH_FIRST, f"resolvable_missing={sorted(required)}"
        return GroundingDecision.NEED_USER, f"unresolvable_missing={sorted(missing_kinds)}"


# ── SEARCH_FIRST 运行时校验：attempted（查证过）vs resolved（拿到事实）──
# (H3a-5, 冻结缺口 3)  Harness 保证生命周期契约：research 在 decision=
# SEARCH_FIRST 时，缺失且当前 capability 可查的 kind 必须先被查证过
# （attempted），才能 RESEARCH_COMPLETE / NEED_USER。attempted ≠ resolved：
# 查过但没查到 → attempted 有、resolved 无 → 允许 uncertainties 收尾（不卡
# 死循环）；resolved 只影响 ResearchEvidence 写事实还是写 uncertainties。

# query 明确针对某 kind 的判定词表（确定性、无 LLM）。
#   event_location: query 含已知城市名，或演出/场地定位结构（在/去X看、X场/展/馆…）
#   event_date:     相对时间词 / 场次 / 具体日期
_LOCATION_TARGET_RE = re.compile(
    r"(?:在|去|到|前往|飞往|赴)([一-龥]{2,6})(?:看|听|参加|出席|游玩|旅游|逛|转)?"
    r"|(?:场|站|展|馆|剧院|剧场|演播厅)"
)
_DATE_TARGET_RE = re.compile(
    r"(今天|今晚|明天|明晚|后天|昨晚|上周|这周|本周|下周|上个月|这个月|本月|下个月|"
    r"下半年|上半年|最近|近期|年底|今年|春节|国庆|圣诞|元旦|中秋|场次|\d{1,2}月|\d{1,2}日|"
    r"周[一二三四五六日天]|几号|什么时候|几点)"
)


def _kinds_targeted_by(query: str) -> set[str]:
    """Which missing kinds a search query explicitly targets."""
    kinds: set[str] = set()
    if any(city in query for city in _CITY_ALIASES_KEYS):
        kinds.add("event_location")
    elif _LOCATION_TARGET_RE.search(query):
        kinds.add("event_location")
    if _DATE_TARGET_RE.search(query):
        kinds.add("event_date")
    return kinds


def _kinds_found_in(text: str) -> set[str]:
    """Which kinds the result text actually carries fillable facts for."""
    kinds: set[str] = set()
    if any(city in text for city in _CITY_ALIASES_KEYS):
        kinds.add("event_location")
    if _DATE_TARGET_RE.search(text):
        kinds.add("event_date")
    return kinds


def _observation_has_weather(text: str) -> bool:
    return "°" in text or "℃" in text or "天气（" in text


def grounding_progress_for_tool(
    tool_name: str,
    arguments: dict[str, Any],
    observation: str,
) -> tuple[set[str], set[str]]:
    """One successful tool execution → (attempted, resolved) grounding kinds.

    The caller only passes ``STATUS_OK`` results (a failed tool is never
    ``attempted``). ``search_web``: attempted = kinds the query explicitly
    targets; resolved = kinds present in the returned text. ``get_weather``:
    attempted = weather; resolved = the observation actually carries data.
    """
    attempted: set[str] = set()
    resolved: set[str] = set()
    if tool_name == "get_weather":
        attempted.add("weather")
        if _observation_has_weather(str(observation)):
            resolved.add("weather")
        return attempted, resolved
    if tool_name == "search_web":
        args = arguments or {}
        query = str(args.get("query") or args.get("q") or "")
        attempted = _kinds_targeted_by(query)
        resolved = _kinds_found_in(str(observation))
        return attempted, resolved
    return attempted, resolved


def required_searchable_kinds(
    missing_kinds: list[str] | None,
    capabilities: frozenset[str] | None,
) -> set[str]:
    """The missing kinds a deployed capability can actually resolve.

    Used by the SEARCH_FIRST runtime gate: ``required ⊆ attempted`` is the
    pass condition for RESEARCH_COMPLETE / NEED_USER under ``search_first``.
    """
    caps = set(capabilities or ())
    return {
        kind
        for kind in (missing_kinds or [])
        if kind in _RESOLVABLE_BY and (_RESOLVABLE_BY[kind] & caps)
    }
