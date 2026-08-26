"""统一能力注册表（批次 1，spec 1.1）：唯一能力事实源。

两类能力：
- cap_type="tool"：可被 <tool_calls> 工单调用的工具（7 个），executor 由
  handler 启动时 bind_executors() 注入（防循环 import）
- cap_type="intent"：意图分发分支（15 个），executor 指向 _handle_*

设计要点：
- TOOL_REGISTRY（tool_calls.py）从此文件投影，消费方签名不变
- validate_params 用 jsonschema 校验结构化工单参数
- build_intent_enum_line() 与 COMBINED_PROMPT 现枚举原文必须一致（Task 5 同源改造的锚点）
"""
from dataclasses import dataclass, field
from typing import Callable, Optional

import jsonschema

# 7 个工具的 params_schema：全部单文本键（现有执行器吃自然语言文本），
# 结构化工单经 serialize_params 序列化回文本桥接（spec 红线：执行器零改动）
_TOOL_PARAMS_SCHEMAS = {
    "bazi_chart": {"type": "object",
                   "properties": {"text": {"type": "string",
                                            "description": "出生信息自然语言描述，如：1990年5月20日 午时 北京 男"}},
                   "required": ["text"]},
    "quote_rag": {"type": "object",
                  "properties": {"query": {"type": "string", "description": "搜索关键词"}},
                  "required": ["query"]},
    "web_search": {"type": "object",
                   "properties": {"query": {"type": "string", "description": "搜索关键词"}},
                   "required": ["query"]},
    "dream": {"type": "object",
              "properties": {"text": {"type": "string", "description": "梦境描述"}},
              "required": ["text"]},
    "fengshui": {"type": "object",
                 "properties": {"text": {"type": "string", "description": "房屋坐向/布局描述，如：坐北朝南"}},
                 "required": ["text"]},
    "zeri": {"type": "object",
             "properties": {"text": {"type": "string",
                                      "description": "场景+时间范围，如：下个月搬家"}},
             "required": ["text"]},
    "record_lookup": {"type": "object",
                      "properties": {"query": {"type": "string",
                                               "description": "想查的用户本人数据描述，如：我的档案"}},
                      "required": ["query"]},
}


@dataclass(frozen=True)
class Capability:
    cap_id: str                 # 如 "web_search"
    name: str                   # 中文短名，LLM 可见（"搜索"）
    description: str            # 干什么用 + 何时用（决策说明书）
    params_schema: dict         # JSON Schema（tool 类有；intent 类为 {}）
    executor: Optional[Callable] = None   # handler 启动时 bind_executors 注入
    timeout_s: float = 8.0      # 单次执行超时（tool 类）
    retries: int = 1            # 失败重试次数（tool 类）
    requires: str = ""          # 前置条件说明（tool 类）
    cap_type: str = "tool"      # "tool" | "intent"


# ---- tool 类（7 个）：desc/requires 原文迁移自 tool_calls.py TOOL_REGISTRY ----
# timeout_s 按执行器性质显式标注（Task 4 review I-2：8s 默认 < LLM 内部 60s 超时，
# 慢而成功的调用会被误判失败）：
# - LLM 支撑（走 client.py 60s 超时链）→ ≥70s（60s + 余量）
#   · quote_rag：LLM 查询扩展（query_expansion LLM_TIMEOUT=12s）+ bge-reranker 本地
#     模型冷加载，完整管线实测单次 17~94s（src/engines/dream.py 性能注）
#   · dream：engine.analyze 不调 LLM（api_key 未用），但 FAISS 276 万索引冷加载
#     可达数十秒——放 70s 防冷启动误杀（reviewer 点名）
# - 网络类（web_search，Bing SEARCH_TIMEOUT=15s）→ 15s
# - 本地 DB/计算类（bazi_chart/fengshui/zeri/record_lookup）→ 保持 8s
_TOOL_CAPS = [
    Capability("bazi_chart", "排盘",
               "输入出生信息（年月日时、地点、性别）的自然语言描述，输出四柱十神大运流年",
               _TOOL_PARAMS_SCHEMAS["bazi_chart"], timeout_s=8.0,
               requires="出生年月日时、出生地点、性别"),
    Capability("quote_rag", "检索",
               "输入搜索关键词，输出古籍原文 Top5。只引用与用户问题直接相关的内容，不相关忽略",
               _TOOL_PARAMS_SCHEMAS["quote_rag"], timeout_s=70.0,
               requires="搜索关键词"),
    Capability("web_search", "搜索",
               "输入关键词，输出网络搜索结果（带来源 URL，Top 3-5，补充最新/社会信息）",
               _TOOL_PARAMS_SCHEMAS["web_search"], timeout_s=15.0,
               requires="搜索关键词"),
    Capability("dream", "解梦",
               "输入梦境描述，输出象征分析+古籍匹配",
               _TOOL_PARAMS_SCHEMAS["dream"], timeout_s=70.0,
               requires="梦境描述"),
    Capability("fengshui", "风水",
               "输入房屋坐向/布局描述，输出吉凶判断+化解建议",
               _TOOL_PARAMS_SCHEMAS["fengshui"], timeout_s=8.0,
               requires="房屋坐向（如：坐北朝南）"),
    Capability("zeri", "择日",
               "输入场景+时间范围，输出3个推荐吉日（宜忌/吉时/方位/一句理由）。"
               "场景支持：嫁娶/搬家/开业/晋升/出行/提车/签约；时间可写下个月、下周、具体日期",
               _TOOL_PARAMS_SCHEMAS["zeri"], timeout_s=8.0,
               requires="场景（嫁娶/搬家/开业/晋升/出行/提车/签约）+ 时间范围（下个月/下周/具体日期）"),
    Capability("record_lookup", "查记录",
               "查用户自己的存量数据（档案/解梦/历史对话/签/名笺/灯语/择吉/晨笺/收藏/会员）。"
               "输入想查的内容描述，如'我的档案''以前解过什么梦'",
               _TOOL_PARAMS_SCHEMAS["record_lookup"], timeout_s=8.0,
               requires="用户本人数据"),
]

# ---- intent 类（15 个）：handler_map（handler.py:2758-2774）全量快照 ----
_INTENT_CAPS = [
    Capability("bazi", "八字", "八字排盘：出生信息+命理分析", {}, cap_type="intent"),
    Capability("ziwei", "紫微", "紫微斗数排盘分析", {}, cap_type="intent"),
    Capability("liuyao", "六爻", "易经六爻占卜", {}, cap_type="intent"),
    Capability("fengshui", "风水", "风水分析", {}, cap_type="intent"),
    Capability("mianxiang", "面相", "面相分析", {}, cap_type="intent"),
    Capability("zeri", "择日", "择吉日", {}, cap_type="intent"),
    Capability("qimen", "奇门", "奇门遁甲分析", {}, cap_type="intent"),
    Capability("xingming", "姓名", "姓名学分析", {}, cap_type="intent"),
    Capability("hehun", "合婚", "双人合盘/合婚分析", {}, cap_type="intent"),
    Capability("dream", "解梦", "梦境解析", {}, cap_type="intent"),
    Capability("calendar", "黄历", "今日运势/今日宜忌/黄历查询", {}, cap_type="intent"),
    Capability("hourly", "时辰", "时辰/每日时段分析", {}, cap_type="intent"),
    Capability("xuetang", "学堂", "学堂/教育命理分析", {}, cap_type="intent"),
    Capability("advisor", "顾问", "生活建议/怎么办类咨询", {}, cap_type="intent"),
    Capability("career", "事业", "事业适配：职业/公司/行业选择类问题", {}, cap_type="intent"),
]

CAPABILITIES: list = _TOOL_CAPS + _INTENT_CAPS

TOOL_NAME_BY_ID: dict = {c.cap_id: c.name for c in _TOOL_CAPS}
CAPABILITY_BY_NAME: dict = {c.name: c for c in _INTENT_CAPS}
CAPABILITY_BY_NAME.update({c.name: c for c in _TOOL_CAPS})
CAPABILITY_BY_ID: dict = {c.cap_id: c for c in _INTENT_CAPS}
CAPABILITY_BY_ID.update({c.cap_id: c for c in _TOOL_CAPS})

# COMBINED_PROMPT 现枚举原文（14 个，无 xuetang/hourly，含 free_chat）——顺序不可改
INTENT_ENUM_ORDER = ["bazi", "ziwei", "liuyao", "fengshui", "zeri", "mianxiang",
                     "qimen", "xingming", "hehun", "dream", "calendar",
                     "advisor", "career", "free_chat"]

_tool_executors: dict = {}
_intent_executors: dict = {}


def bind_executors(tool_executors: dict, intent_executors: dict) -> None:
    """handler 侧注入执行器：tool 与 intent 分开绑定（cap_id 同名时互不覆盖）。

    Task 2 review M-1 修复：fengshui/zeri/dream 在 _TOOL_CAPS 与 _INTENT_CAPS
    各有一条同 cap_id 记录，单 map 后写覆盖会把 tool lambda 顶掉；
    双参数分型绑定后按 cap_type 各取各的执行器。
    """
    _tool_executors.update(tool_executors)
    _intent_executors.update(intent_executors)
    for c in CAPABILITIES:
        src = _tool_executors if c.cap_type == "tool" else _intent_executors
        c.__dict__["executor"] = src.get(c.cap_id) or c.executor


def validate_params(cap_id: str, params: dict) -> Optional[str]:
    """按 params_schema 校验结构化工单参数。返回 None=合法，否则错误信息串。"""
    cap = CAPABILITY_BY_ID.get(cap_id)
    if cap is None:
        return f"未知能力「{cap_id}」"
    if not cap.params_schema:
        return None
    try:
        jsonschema.validate(instance=params, schema=cap.params_schema)
        return None
    except jsonschema.ValidationError as e:
        return f"参数不合法: {e.message}"


def build_intent_enum_line() -> str:
    """意图枚举行（Task 5 同源改造锚点）：生成结果必须与 COMBINED_PROMPT 原文一致。"""
    return "Classify into EXACTLY ONE: " + ", ".join(INTENT_ENUM_ORDER)


def build_tool_description() -> str:
    """工具说明书文本（给 LLM 选工具的决策说明书，Task 5 注入 tool_loop）。"""
    lines = ["【可用工具清单】（需要时按 JSON 工单调用，不需要就不调用）"]
    for c in _TOOL_CAPS:
        lines.append(f"- {c.name}（{c.cap_id}）：{c.description}。参数：{c.requires}。"
                     f"超时{c.timeout_s:.0f}s，失败自动重试{c.retries}次")
    return "\n".join(lines)
