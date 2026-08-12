"""Prompt template for LLM-based preference-evidence extraction."""

from __future__ import annotations

MEMORY_PROMPT_VERSION = "memory-evidence-v2.4"

DIMENSION_GUIDE = (
    "- style: 风格与正式度（如 极简、复古、街头、休闲、商务）\n"
    "- garment: 单品、版型、材质、颜色（如 衬衫、修身、棉、黑色）\n"
    "- appearance: 身形与整体形象（如 显瘦、抬腰线、配饰克制）\n"
    "- shopping: 购物与场合习惯（如 通勤、约会、常年回购）\n"
)

ATTRIBUTE_GUIDE = (
    "attribute 只能是以下 9 个之一（英文小写，不要自造新词）：\n"
    "  category  品类（如 衬衫、西装、半裙、牛仔裤）\n"
    "  color     颜色/色系（如 黑色、米白、大地色）\n"
    "  fit       版型/剪裁（如 修身、宽松、高腰）\n"
    "  material  材质（如 棉、羊绒、真丝、牛仔布）\n"
    "  style     风格（如 极简、复古、街头、甜美）\n"
    "  brand     品牌（如 MaxMara、优衣库）\n"
    "  occasion  场合（如 通勤、约会、面试、度假）\n"
    "  formality 正式度（如 正式、休闲、商务）\n"
    "  detail    细节特征（如 泡泡袖、金属扣、翻领；或身形效果：显瘦、抬腰线）\n"
)

ITEM_RULE = (
    "**不提炼具体某一件单品**：出现「这件/那条/这双」等单件指代时，不提炼该件为偏好"
    "（单品级由行为事件追踪，语言只提炼可泛化到一类的偏好）。"
    "只有泛指一类（如「我不喜欢大衣」「爱穿衬衫」）才提炼 category。\n"
)

DISAMBIGUATION_GUIDE = (
    "多义消歧：\n"
    "  「正式/休闲/商务」作程度修饰（如 更正式一点）→ formality；作场合名词（如 正式场合、婚礼）→ occasion。\n"
    "  「牛仔」默认指牛仔类单品（牛仔裤/牛仔外套/牛仔裙）→ category；仅明确在说面料质感（如「牛仔布的面料」）→ material。\n"
    "  「花花绿绿/花哨/印花/素色」这类整体外观风格词 → style（不是 color）；只有单色词（黑/白/米/灰…）才归 color。\n"
    "  occasion 的 value 用裸场合名词（正式/通勤/面试/婚礼），不带后缀（「正式场合」→ value=正式）。\n"
    "  一句话里多个属性成分各自拆开（如「黑色羊绒衬衫」→ color=黑色 + material=羊绒 + category=衬衫 三条）。\n"
)

SCOPE_GUIDE = (
    "scope 判定（按优先级）：\n"
    "  ① 习惯陈述（「平时/日常/一直/总是/就爱」）中的单品/颜色/风格偏好 → 该类偏好 type=global；"
    "场合词不把「平时上班我就爱穿衬衫」的衬衫改标成 contextual——衬衫是日常习惯，标 global。"
    "但场合词若构成稳定的场合偏好，仍单独提炼 occasion 证据（见 ④）。\n"
    "  ② 明确限定「X的话 / 在X / 只有X才 / X场合 / 通勤穿惯了X」把偏好绑定到唯一场景 → type=contextual"
    "（「通勤的话我喜欢宽松」→ contextual(通勤)；「通勤穿惯了优衣库的宽松衬衫」→ 优衣库/宽松 contextual(通勤)）。\n"
    "  ③ 对某个具体场景的着装要求（「面试要正式」「婚礼要正式」）→ type=contextual(该场景)。\n"
    "  ④ 场合本身作为喜好的对象（occasion 偏好，如「正式场合还是它靠谱」→ occasion=正式 contextual；"
    "「平时上班就爱穿衬衫」→ occasion=上班 contextual）→ type=contextual(该场合)。\n"
    "  ⑤ 其余一般提及 → type=global。\n"
    "  ⑥ 一次性场景（「明天面试」「周五见闺蜜」）不改变其他偏好的 scope：其中的单品/颜色/风格仍 global，"
    "只有对场景本身的着装要求（「面试穿正式」）才 contextual(该场景)。\n"
    "  ⑦ 否定/厌恶句（「太紧的难受」「不喜欢修身」）默认 type=global——厌恶是通用态度，"
    "除非在同一句里被「X的话」明确限定到场景（「通勤的话我喜欢宽松」的宽松是 contextual，但同句「太紧的难受」的紧仍是 global）。\n"
)

SYSTEM_PROMPT = (
    "你是 StyleForge 的偏好证据提炼器。从一段造型请求中提炼标准化偏好证据：\n"
    "只保留用户明确表达或反复出现、跨对话仍值得记住的内容；\n"
    "**忽略一次性场景**（如「明天面试穿什么」中只与本次相关的「面试」不算长期场合偏好，"
    "但「我通勤都穿衬衫」里的「通勤」「衬衫」算）。\n"
    f"dimension 只能是以下之一：\n{DIMENSION_GUIDE}\n"
    f"{ATTRIBUTE_GUIDE}\n"
    f"{ITEM_RULE}\n"
    f"{DISAMBIGUATION_GUIDE}\n"
    f"{SCOPE_GUIDE}\n"
    "value 用简洁中文（不超过 64 字）。\n"
    "polarity: positive=喜欢/想要，negative=讨厌/要避免（否定词如「不要」「避免」「不喜欢」一律 negative）。\n"
    "strength 0~1：明确强调/反复出现给 0.7 以上，一般提及给 0.3~0.5。\n"
    "**拆分规则**：\n"
    "  ① 多属性拆分：一句话同时表达多个属性时拆成多条证据"
    "（「黑色羊绒衬衫」→ color=黑色 + material=羊绒 + category=衬衫 三条）。\n"
    "  ② 否定拆分：一句话里被否定的每个成分各出一条 negative"
    "（「不要黑色裤子」→ color=黑色 negative + category=裤子 negative 两条；"
    "「不喜欢修身」→ fit=修身 negative）。"
    "只有整句否定且无具体属性（如「这套不喜欢」）才不出证据。"
    "不要把「否定黑色」曲解成偏好其他颜色；"
    "「配饰多了反而累赘」这类「X多了反而难受」是按 配饰多=negative 提炼，不要翻转成 positive 的配饰克制。\n"
    "  ③ 弱信号不提炼：「还好/可以/还行/不算讨厌/马马虎虎」这类模糊保留态度，不足以形成偏好证据，跳过。"
    "只有明确的喜欢/想要（爱穿/喜欢/就要）或明确的厌恶（不喜欢/不要/穿不了/难受）才提炼。\n"
    "输出 JSON：{\"evidence\": [{\"dimension\": ..., \"attribute\": ..., \"value\": ..., "
    "\"polarity\": ..., \"strength\": ..., \"scope\": {...}}]}。"
    "没有长期偏好时返回 {\"evidence\": []}。"
)

FEW_SHOT_EXAMPLES = """\
示例输入：「黑色羊绒衬衫 + 通勤正式」
示例输出：{"evidence": [
  {"dimension": "garment", "attribute": "color", "value": "黑色", "polarity": "positive", "strength": 0.6, "scope": {"type": "global"}},
  {"dimension": "garment", "attribute": "material", "value": "羊绒", "polarity": "positive", "strength": 0.5, "scope": {"type": "global"}},
  {"dimension": "garment", "attribute": "category", "value": "衬衫", "polarity": "positive", "strength": 0.6, "scope": {"type": "global"}},
  {"dimension": "shopping", "attribute": "occasion", "value": "通勤", "polarity": "positive", "strength": 0.7, "scope": {"type": "contextual", "occasions": ["通勤"]}},
  {"dimension": "style", "attribute": "formality", "value": "正式", "polarity": "positive", "strength": 0.6, "scope": {"type": "global"}}
]}

示例输入：「不要黑色裤子，修身款也不喜欢」
示例输出：{"evidence": [
  {"dimension": "garment", "attribute": "color", "value": "黑色", "polarity": "negative", "strength": 0.8, "scope": {"type": "global"}},
  {"dimension": "garment", "attribute": "category", "value": "裤子", "polarity": "negative", "strength": 0.8, "scope": {"type": "global"}},
  {"dimension": "garment", "attribute": "fit", "value": "修身", "polarity": "negative", "strength": 0.7, "scope": {"type": "global"}}
]}

示例输入：「我不喜欢黑色裤子，西装裤还好，修身那种穿不了」
示例输出：{"evidence": [
  {"dimension": "garment", "attribute": "color", "value": "黑色", "polarity": "negative", "strength": 0.7, "scope": {"type": "global"}},
  {"dimension": "garment", "attribute": "category", "value": "裤子", "polarity": "negative", "strength": 0.7, "scope": {"type": "global"}},
  {"dimension": "garment", "attribute": "fit", "value": "修身", "polarity": "negative", "strength": 0.7, "scope": {"type": "global"}}
]}
（「还好/可以/还行」是模糊弱保留，不提炼为偏好，所以没有西装裤 positive）

示例输入：「通勤穿惯了优衣库的宽松衬衫，面试要正式一些」
示例输出：{"evidence": [
  {"dimension": "shopping", "attribute": "brand", "value": "优衣库", "polarity": "positive", "strength": 0.5, "scope": {"type": "contextual", "occasions": ["通勤"]}},
  {"dimension": "garment", "attribute": "fit", "value": "宽松", "polarity": "positive", "strength": 0.5, "scope": {"type": "contextual", "occasions": ["通勤"]}},
  {"dimension": "garment", "attribute": "category", "value": "衬衫", "polarity": "positive", "strength": 0.5, "scope": {"type": "global"}},
  {"dimension": "style", "attribute": "formality", "value": "正式", "polarity": "positive", "strength": 0.6, "scope": {"type": "contextual", "occasions": ["面试"]}}
]}

示例输入：「平时上班我就爱穿衬衫，这件大衣别推」
示例输出：{"evidence": [
  {"dimension": "garment", "attribute": "category", "value": "衬衫", "polarity": "positive", "strength": 0.6, "scope": {"type": "global"}},
  {"dimension": "shopping", "attribute": "occasion", "value": "上班", "polarity": "positive", "strength": 0.5, "scope": {"type": "contextual", "occasions": ["上班"]}}
]}
（习惯陈述 → 衬衫 global，场景词只是背景、不改变单品 scope；「上班」作为场合偏好单独 contextual；「这件大衣」是单件指代，不提炼）

示例输入：「婚礼要正式一些，别给我上牛仔」
示例输出：{"evidence": [
  {"dimension": "shopping", "attribute": "occasion", "value": "婚礼", "polarity": "positive", "strength": 0.6, "scope": {"type": "contextual", "occasions": ["婚礼"]}},
  {"dimension": "style", "attribute": "formality", "value": "正式", "polarity": "positive", "strength": 0.5, "scope": {"type": "contextual", "occasions": ["婚礼"]}},
  {"dimension": "garment", "attribute": "category", "value": "牛仔", "polarity": "negative", "strength": 0.6, "scope": {"type": "global"}}
]}
（「正式」作程度修饰 → formality；「牛仔」指牛仔类单品 → category）

示例输入：「明天面试穿什么？」
示例输出：{"evidence": []}
"""


def build_memory_extraction_prompt(request: str) -> tuple[str, str]:
    """Return ``(system, user)`` for one memory-extraction LLM call."""
    user = (
        f"{FEW_SHOT_EXAMPLES}\n"
        "---\n"
        f"现在提炼下面这段请求的长期偏好：\n\n{request.strip()}"
    )
    return SYSTEM_PROMPT, user
