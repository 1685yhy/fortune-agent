"""批次 1：统一能力注册表测试（spec 1.4 第 1 条 + 一致性）。"""
import sys

sys.path.insert(0, "src")

from src.bot import capability_registry as reg  # noqa: E402
from src.bot.tool_calls import TOOL_REGISTRY  # noqa: E402


def test_tool_coverage():
    """10 个工具全注册：排盘/检索/搜索/解梦/风水/择日/查记录/合婚/起名/流月流年（批次 2 E3）。"""
    names = {c.name for c in reg.CAPABILITIES if c.cap_type == "tool"}
    assert names == {"排盘", "检索", "搜索", "解梦", "风水", "择日", "查记录",
                     "合婚", "起名", "流月流年"}


def test_tool_registry_projection():
    """TOOL_REGISTRY 从注册表投影：10 键，desc/requires 与注册表一致。"""
    assert set(TOOL_REGISTRY) == {"排盘", "检索", "搜索", "解梦", "风水", "择日",
                                  "查记录", "合婚", "起名", "流月流年"}
    for name, cap in reg.CAPABILITY_BY_NAME.items():
        if cap.cap_type == "tool":
            assert TOOL_REGISTRY[name]["desc"] == cap.description
            assert TOOL_REGISTRY[name]["requires"] == cap.requires


def test_intent_coverage():
    """15 个意图全注册（handler_map 全量，含 hourly/xuetang）。"""
    ids = {c.cap_id for c in reg.CAPABILITIES if c.cap_type == "intent"}
    assert ids == {"bazi", "ziwei", "liuyao", "fengshui", "mianxiang", "zeri",
                   "qimen", "xingming", "hehun", "dream", "calendar",
                   "hourly", "xuetang", "advisor", "career"}


def test_intent_enum_line_unchanged():
    """枚举行与 COMBINED_PROMPT 现原文一字不差（14 个，无 xuetang/hourly）。"""
    assert reg.build_intent_enum_line() == (
        "Classify into EXACTLY ONE: bazi, ziwei, liuyao, fengshui, zeri, "
        "mianxiang, qimen, xingming, hehun, dream, calendar, advisor, career, "
        "free_chat"
    )


def test_combined_prompt_enum_same_source():
    """COMBINED_PROMPT 枚举行由注册表生成，且与原文一致、无占位符残留。"""
    from src.engines.message_analyzer import COMBINED_PROMPT
    assert "__INTENT_ENUM__" not in COMBINED_PROMPT
    assert ("Classify into EXACTLY ONE: bazi, ziwei, liuyao, fengshui, zeri, "
            "mianxiang, qimen, xingming, hehun, dream, calendar, "
            "advisor, career, free_chat") in COMBINED_PROMPT
    # B1-7（护栏盲区）：前缀必须恰出现一次——子串断言抓不住前缀翻倍
    # （"Classify into EXACTLY ONE: " 重复两行仍能过 in 检查）
    assert COMBINED_PROMPT.count("Classify into EXACTLY ONE:") == 1


def test_tool_description_built():
    """工具说明书生成：9 工具齐、含 cap_id 与超时参数。"""
    from src.bot.capability_registry import build_tool_description
    d = build_tool_description()
    for cid in ("bazi_chart", "web_search", "quote_rag", "dream",
                "fengshui", "zeri", "record_lookup", "hehun", "naming",
                "fortune_cycle"):
        assert cid in d
    assert "8s" in d and "重试1次" in d


def test_validate_params():
    """参数校验：缺必填/类型错 → 错误串；合法 → None。"""
    err = reg.validate_params("web_search", {"query": "北京天气"})
    assert err is None
    err = reg.validate_params("web_search", {})
    assert err is not None and "query" in err
    err = reg.validate_params("web_search", {"query": 123})
    assert err is not None
    assert reg.validate_params("no_such_cap", {"query": "x"}) is not None


def test_tool_intent_name_collision():
    """同名 cap_id（fengshui/zeri/dream/hehun）：tool 条目优先于 intent 条目（工单校验走 tool）。

    注：_TOOL_CAPS(10) + _INTENT_CAPS(15) = 25 项列表，但 fengshui/zeri/dream/hehun 的
    cap_id 与中文名在两类中完全相同（工具是对既有 intent 的追加映射，
    非新增键），去重后唯一键为 15+10-4=21（批次 2 E2 起名 naming、批次 2 E3
    流月流年 fortune_cycle 均为全新中文名/cap_id，与既有 intent 不同名、不碰撞，
    故 19 → 20 → 21）。
    """
    assert reg.CAPABILITY_BY_ID["fengshui"].cap_type == "tool"
    assert reg.CAPABILITY_BY_ID["zeri"].cap_type == "tool"
    assert reg.CAPABILITY_BY_ID["dream"].cap_type == "tool"
    assert reg.CAPABILITY_BY_ID["hehun"].cap_type == "tool"
    assert reg.CAPABILITY_BY_NAME["风水"].cap_type == "tool"
    assert reg.CAPABILITY_BY_NAME["择日"].cap_type == "tool"
    assert reg.CAPABILITY_BY_NAME["解梦"].cap_type == "tool"
    assert reg.CAPABILITY_BY_NAME["合婚"].cap_type == "tool"
    # 起名（naming）/ 流月流年（fortune_cycle）为全新中文名/cap_id，不与任何 intent 同名
    assert reg.CAPABILITY_BY_ID["naming"].cap_type == "tool"
    assert reg.CAPABILITY_BY_NAME["起名"].cap_type == "tool"
    assert reg.CAPABILITY_BY_ID["fortune_cycle"].cap_type == "tool"
    assert reg.CAPABILITY_BY_NAME["流月流年"].cap_type == "tool"
    assert len(reg.CAPABILITY_BY_ID) == 21
    assert len(reg.CAPABILITY_BY_NAME) == 21
    # 回归点：intent 覆盖 tool 时，tool 必填校验静默失效（validate_params 返回 None）
    assert reg.validate_params("fengshui", {}) is not None
    assert reg.validate_params("zeri", {}) is not None
    assert reg.validate_params("dream", {}) is not None
    assert reg.validate_params("hehun", {}) is not None
    assert reg.validate_params("hehun", {"birth_a": "x", "birth_b": "y"}) is None


# ---- Task 3：结构化工单协议（JSON 工单解析 + 参数序列化桥接） ----


def test_json_workorder_parse():
    """JSON 工单块解析：合法工单 → ToolCall 带 params_obj。"""
    from src.bot.tool_calls import ToolCall, parse_tool_calls
    calls = parse_tool_calls(
        '好的，我来查。<tool_calls>'
        '[{"tool": "web_search", "params": {"query": "北京天气"}}, '
        '{"tool": "bazi_chart", "params": {"text": "1990年5月20日 北京 男"}}]'
        '</tool_calls>'
    )
    assert [c.name for c in calls] == ["搜索", "排盘"]
    assert calls[0].params_obj == {"query": "北京天气"}
    assert calls[1].params_obj == {"text": "1990年5月20日 北京 男"}


def test_json_workorder_bad_json_falls_back():
    """非法 JSON 工单块 → 正则兜底（旧协议仍工作）。"""
    from src.bot.tool_calls import parse_tool_calls
    calls = parse_tool_calls("<tool_calls>这不是JSON</tool_calls>\n<tool_call>搜索: 北京天气</tool_call>")
    assert [c.name for c in calls] == ["搜索"]
    assert calls[0].params_obj is None
    assert calls[0].params == "北京天气"


def test_json_workorder_unknown_tool_skipped():
    """工单里未知工具 → 跳过不执行。"""
    from src.bot.tool_calls import parse_tool_calls
    calls = parse_tool_calls(
        '<tool_calls>[{"tool": "no_such_tool", "params": {"q": "x"}}]</tool_calls>')
    assert calls == []


def test_serialize_params():
    """结构化参数 → 执行器文本：单键直接取值，多键 k: v 拼接。"""
    from src.bot.tool_calls import serialize_params
    assert serialize_params({"query": "北京天气"}) == "北京天气"
    assert serialize_params({"text": "1990年5月20日"}) == "1990年5月20日"
    assert serialize_params({"query": 123}) == "123"
    assert serialize_params({"a": "1", "b": "2"}) == "a: 1\nb: 2"
    assert serialize_params({}) == ""


def test_no_tool_call_no_workorder():
    """无工单无标签 → 空列表（不误判）。"""
    from src.bot.tool_calls import parse_tool_calls
    assert parse_tool_calls("今天天气不错") == []


def test_strip_tool_calls_workorder_layer():
    """strip 三层清理：JSON 工单块 → 文本标签 → 裸标签符全移除，保留正文。"""
    from src.bot.tool_calls import strip_tool_calls
    s = strip_tool_calls(
        '好的。<tool_calls>[{"tool": "web_search", "params": {"query": "x"}}]'
        '</tool_calls><tool_call>搜索: 天气</tool_call>以下是正文</tool_call>')
    assert s == "好的。以下是正文"


def test_strip_workorder_residue():
    """复数工单标签残留必须剥净，JSON 载荷（含用户查询参数）绝不泄漏到可见文本。"""
    from src.bot.tool_calls import strip_tool_calls
    # 未闭合块：JSON 载荷+同行正文剥到行尾（防泄漏优先）
    assert strip_tool_calls(
        '好的。<tool_calls>[{"tool": "web_search", "params": {"query": "1990年出生信息"}}]以下是正文'
    ) == "好的。"
    # 孤立闭合符
    assert strip_tool_calls('正文</tool_calls>尾') == "正文尾"
    # 大写完整块：整块剥离，正文保留
    assert strip_tool_calls(
        '正文<TOOL_CALLS>[{"tool": "web_search"}]</TOOL_CALLS>尾') == "正文尾"
    # 完整合法小写块：整块剥离，正文保留
    assert strip_tool_calls(
        '<tool_calls>[{"tool": "web_search", "params": {"query": "北京天气"}}]</tool_calls>你好') == "你好"
    # 单数标签残留回归（旧协议真机修复）
    assert strip_tool_calls('正文<tool_call>搜索: 北京</tool_call>尾') == "正文尾"


# ---- Task 4：执行层改造（注册表分派 + 超时重试 + JSON 回喂） ----


def test_execute_invalid_params_not_executed():
    """参数非法 → 不执行 executor，回 ok:false 参数不合法（Task 4 review I-1 重写）。

    纯静态层测试：MessageHandler.__new__ 避开 __init__（不装配引擎/不连网），
    bind_executors 注入 spy 记录 executor 是否被调用。
    """
    from src.bot.handler import MessageHandler
    from src.bot.tool_calls import ToolResult
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

    cap = CAPABILITY_BY_NAME["搜索"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("web_search")
    called: list = []

    def spy(params, user_id="", user_question=""):
        called.append(params)
        return ToolResult("搜索", True, "不应被执行")

    bot = MessageHandler.__new__(MessageHandler)
    try:
        bind_executors({"web_search": spy}, {})
        # 非法参数（缺必填 query）：校验失败 → 不执行 executor，不计重试
        r = bot._execute_tool_call("搜索", {"bad": 1}, "u1")
        assert r.ok is False
        assert "参数不合法" in r.text
        assert called == []
        # 正控：合法参数必须命中 spy（证明断言链路有效，spy 确实会被调用）
        r2 = bot._execute_tool_call("搜索", {"query": "北京天气"}, "u1")
        assert r2.ok is True and r2.text == "不应被执行"
        assert called == ["北京天气"]
    finally:
        # 恢复原绑定，避免污染后续测试（bind 进 _tool_executors + cap.__dict__）
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("web_search", None)
        else:
            reg._tool_executors["web_search"] = orig_ex


def test_bind_executors_typed_split():
    """M-3 回归（Task 4 review）：同 cap_id 的 tool/intent 双记录分型绑定互不覆盖。

    fengshui/zeri/dream 在 _TOOL_CAPS 与 _INTENT_CAPS 各有一条同 cap_id 记录，
    单 map 后写覆盖会把 tool lambda 顶掉——双参数分型绑定后按 cap_type 各取各的。
    """
    from src.bot.capability_registry import bind_executors

    tool_cap = next(c for c in reg.CAPABILITIES
                    if c.cap_type == "tool" and c.cap_id == "fengshui")
    intent_cap = next(c for c in reg.CAPABILITIES
                      if c.cap_type == "intent" and c.cap_id == "fengshui")
    orig_tool_exec, orig_intent_exec = tool_cap.executor, intent_cap.executor
    orig_tool_ex = reg._tool_executors.get("fengshui")
    orig_intent_ex = reg._intent_executors.get("fengshui")

    def tool_spy(params, user_id="", user_question=""):
        return "tool-spy"

    def intent_spy(*args, **kwargs):
        return "intent-spy"

    try:
        bind_executors({"fengshui": tool_spy}, {"fengshui": intent_spy})
        # 分型绑定：tool cap 拿 tool spy，intent cap 拿 intent spy，互不覆盖
        assert tool_cap.executor is tool_spy
        assert intent_cap.executor is intent_spy
        assert tool_cap.executor is not intent_cap.executor
        # 名字/ID 查表走 tool 条目（工单校验语义），executor 也应是 tool spy
        assert reg.CAPABILITY_BY_NAME["风水"].executor is tool_spy
        assert reg.CAPABILITY_BY_ID["fengshui"].executor is tool_spy
    finally:
        # 恢复原绑定（executor 可能为 None 或真执行器，bind_executors 的
        # `src.get(id) or c.executor` 语义无法显式解绑为 None，故直接写 __dict__）
        tool_cap.__dict__["executor"] = orig_tool_exec
        intent_cap.__dict__["executor"] = orig_intent_exec
        if orig_tool_ex is None:
            reg._tool_executors.pop("fengshui", None)
        else:
            reg._tool_executors["fengshui"] = orig_tool_ex
        if orig_intent_ex is None:
            reg._intent_executors.pop("fengshui", None)
        else:
            reg._intent_executors["fengshui"] = orig_intent_ex


def test_result_json_wrapper():
    """回喂格式：统一 {"tool","ok","data"/"error"} JSON。"""
    from src.bot.tool_calls import ToolResult
    from src.bot.handler import format_tool_results_json
    ok = ToolResult("搜索", True, "北京：晴 25℃")
    err = ToolResult("排盘", False, "缺少出生信息，请询问", needs_info=True)
    out = format_tool_results_json([ok, err])
    assert '"ok": true' in out and '"data": "北京：晴 25℃"' in out
    assert '"ok": false' in out and '"needs_info": true' in out


# ---- Task 3B：deepseek 原生 tool_use（Anthropic 协议块解析 + 注册表 schema） ----


def test_native_tool_use_blocks_parse():
    """原生 tool_use 块解析：英文 cap_id 归一化为中文名 + tool_use_id 透传。"""
    from src.bot.tool_calls import ToolCall, parse_native_tool_use_blocks
    blocks = parse_native_tool_use_blocks([{
        "type": "tool_use", "id": "tu_1", "name": "web_search",
        "input": {"query": "北京天气"},
    }])
    assert blocks == [ToolCall(name="搜索", params_obj={"query": "北京天气"},
                               tool_use_id="tu_1")]


def test_native_tool_use_blocks_chinese_name_ok():
    """原生块 name 直接给中文名（如 搜索）同样识别。"""
    from src.bot.tool_calls import ToolCall, parse_native_tool_use_blocks
    blocks = parse_native_tool_use_blocks([{
        "type": "tool_use", "id": "tu_2", "name": "搜索",
        "input": {"query": "今日运势"},
    }])
    assert blocks == [ToolCall(name="搜索", params_obj={"query": "今日运势"},
                               tool_use_id="tu_2")]


def test_native_tool_use_blocks_unknown_skipped():
    """未知工具名 → 跳过不执行（程序确认原则）。"""
    from src.bot.tool_calls import parse_native_tool_use_blocks
    blocks = parse_native_tool_use_blocks([{
        "type": "tool_use", "id": "tu_3", "name": "no_such_tool",
        "input": {"q": "x"},
    }])
    assert blocks == []


def test_native_tool_use_blocks_str_input_wrapped():
    """input 为 str → 包装为 {"text": ...}（与 JSON 工单路径一致）。"""
    from src.bot.tool_calls import ToolCall, parse_native_tool_use_blocks
    blocks = parse_native_tool_use_blocks([{
        "type": "tool_use", "id": "tu_4", "name": "bazi_chart",
        "input": "1990年5月20日 午时 北京 男",
    }])
    assert blocks == [ToolCall(name="排盘",
                               params_obj={"text": "1990年5月20日 午时 北京 男"},
                               tool_use_id="tu_4")]


def test_native_tool_use_blocks_non_dict_skipped():
    """非 dict 元素 / 非 tool_use 类型块 / input 非 dict 非 str → 跳过或兜底 {}。"""
    from src.bot.tool_calls import ToolCall, parse_native_tool_use_blocks
    blocks = parse_native_tool_use_blocks([
        "不是dict",
        123,
        {"type": "text", "text": "正文内容"},
        {"type": "tool_use", "id": "tu_5", "name": "dream", "input": 42},
    ])
    assert blocks == [ToolCall(name="解梦", params_obj={}, tool_use_id="tu_5")]


def test_native_tool_use_blocks_id_passthrough():
    """多块：id 逐块透传，顺序保持。"""
    from src.bot.tool_calls import ToolCall, parse_native_tool_use_blocks
    blocks = parse_native_tool_use_blocks([
        {"type": "tool_use", "id": "a1", "name": "quote_rag",
         "input": {"query": "婚姻"}},
        {"type": "tool_use", "id": "b2", "name": "web_search",
         "input": {"query": "今日天气"}},
    ])
    assert [c.tool_use_id for c in blocks] == ["a1", "b2"]
    assert [c.name for c in blocks] == ["检索", "搜索"]
    assert blocks == [
        ToolCall(name="检索", params_obj={"query": "婚姻"}, tool_use_id="a1"),
        ToolCall(name="搜索", params_obj={"query": "今日天气"}, tool_use_id="b2"),
    ]


def test_tool_call_tool_use_id_default_empty():
    """ToolCall 新字段 tool_use_id 默认空串（现有构造不破）。"""
    from src.bot.tool_calls import ToolCall
    assert ToolCall(name="搜索").tool_use_id == ""
    assert ToolCall(name="搜索", params="北京天气").tool_use_id == ""
    assert ToolCall(name="搜索", params_obj={"query": "x"}).tool_use_id == ""


def test_build_tool_schema_list():
    """注册表 → Anthropic 风格 tools schema：10 个、与 build_tool_description 同源。"""
    schemas = reg.build_tool_schema_list()
    assert len(schemas) == 10
    ids = {s["name"] for s in schemas}
    assert ids == {"bazi_chart", "quote_rag", "web_search", "dream",
                   "fengshui", "zeri", "record_lookup", "hehun", "naming",
                   "fortune_cycle"}
    by_id = {s["name"]: s for s in schemas}
    for c in reg.CAPABILITIES:
        if c.cap_type != "tool":
            continue
        s = by_id[c.cap_id]
        assert s["description"] == c.description          # 与 build_tool_description 同源
        assert s["description"] in reg.build_tool_description()
        assert s["input_schema"] == c.params_schema
        assert s["input_schema"]["type"] == "object"
        assert "properties" in s["input_schema"]


def test_anthropic_payload_tools_optional():
    """_anthropic_payload：tools/tool_choice 不传不写入（行为与现状完全一致）。"""
    from src.llm.client import _anthropic_payload
    base = _anthropic_payload([{"role": "user", "content": "hi"}],
                              "deepseek-v4-flash", 100, 0.7)
    assert "tools" not in base and "tool_choice" not in base
    p = _anthropic_payload([{"role": "user", "content": "hi"}],
                           "deepseek-v4-flash", 100, 0.7,
                           tools=[{"name": "web_search"}],
                           tool_choice={"type": "auto"})
    assert p["tools"] == [{"name": "web_search"}]
    assert p["tool_choice"] == {"type": "auto"}


def test_deepseek_anthropic_messages_returns_full_dict():
    """deepseek_anthropic_messages：返回完整响应 dict（含 tool_use 块），不抽取文本。"""
    from unittest.mock import Mock
    from src.llm.client import deepseek_anthropic_messages
    resp_body = {
        "id": "msg_1", "type": "message", "stop_reason": "tool_use",
        "content": [
            {"type": "text", "text": "让我查一下"},
            {"type": "tool_use", "id": "tu_1", "name": "web_search",
             "input": {"query": "北京天气"}},
        ],
    }
    client = Mock()
    client.post.return_value.json.return_value = resp_body
    data = deepseek_anthropic_messages("k", [{"role": "user", "content": "hi"}],
                                       tools=[{"name": "web_search"}], client=client)
    assert data == resp_body
    sent = client.post.call_args.kwargs["json"]
    assert sent["tools"] == [{"name": "web_search"}]
    assert sent["stream"] is False
    # 上游 error 响应 → 抛 RuntimeError（由调用方决定重试或降级）
    err_client = Mock()
    err_client.post.return_value.json.return_value = {
        "type": "error", "error": {"message": "boom"}}
    try:
        deepseek_anthropic_messages("k", [], client=err_client)
        assert False, "应当抛出 RuntimeError"
    except RuntimeError as e:
        assert "boom" in str(e)


def test_deepseek_anthropic_completion_tools_passthrough():
    """deepseek_anthropic_completion：tools 透传进 payload，文本抽取/emoji 剔除不变。"""
    from unittest.mock import Mock
    from src.llm.client import deepseek_anthropic_completion
    client = Mock()
    client.post.return_value.json.return_value = {
        "type": "message",
        "content": [{"type": "text", "text": " 回答内容🎉 "}]}
    out = deepseek_anthropic_completion("k", [{"role": "user", "content": "hi"}],
                                        tools=[{"name": "x"}], client=client)
    assert out == "回答内容"
    sent = client.post.call_args.kwargs["json"]
    assert sent["tools"] == [{"name": "x"}]


def test_native_tool_use_loop_end_to_end():
    """原生循环端到端（mock client）：JSON 工单 → LLM 带 tools 返回 tool_use 块 →
    同参数原生块被去重跳过（review I-1，不重复执行）→ tool_result 复用上次结果
    回传 → 最终文本回复。"""
    from unittest.mock import Mock, patch
    from src.bot.handler import MessageHandler
    from src.bot.tool_calls import ToolResult
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

    cap = CAPABILITY_BY_NAME["搜索"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("web_search")
    called: list = []
    seen_round2: list = []

    def spy(params, user_id="", user_question=""):
        called.append(params)
        return ToolResult("搜索", True, "北京：晴 25℃")

    bot = MessageHandler.__new__(MessageHandler)
    llm = Mock()
    llm.api_key = "test-key"
    llm.model = "deepseek-v4-flash"
    llm.provider = "deepseek"
    bot.llm = llm
    bot.session_dao = None
    bot._downgraded = {}
    bot._tool_logs = {}
    bot._citations = {}
    bot._analysis_facts = {}

    def fake_messages(api_key, messages, model=None, max_tokens=0,
                      temperature=0.0, timeout=0.0, tools=None,
                      tool_choice=None, **kwargs):
        if not hasattr(fake_messages, "turn"):
            fake_messages.turn = 0
        fake_messages.turn += 1
        if fake_messages.turn == 1:
            # 首轮：必须带 tools（payload 有注册表 schema，批次 2 E3 起 10 个含 fortune_cycle）
            assert tools and [t["name"] for t in tools] == [
                "bazi_chart", "quote_rag", "web_search", "dream",
                "fengshui", "zeri", "record_lookup", "hehun", "naming",
                "fortune_cycle"]
            return {
                "stop_reason": "tool_use",
                "content": [
                    {"type": "text", "text": "让我搜索一下"},
                    {"type": "tool_use", "id": "tu_1", "name": "web_search",
                     "input": {"query": "北京天气"}},
                ],
            }
        # 第二轮（原生链续调）：末条消息必须是 tool_result 回传（id 匹配）
        seen_round2.append(messages[-1])
        return {"stop_reason": "end_turn",
                "content": [{"type": "text", "text": "北京今天晴 25℃。"}]}

    try:
        bind_executors({"web_search": spy}, {})
        with patch("src.llm.client.deepseek_anthropic_messages",
                   side_effect=fake_messages):
            out = bot._run_tool_loop(
                "北京天气怎么样", "u1",
                '好的，我来查。<tool_calls>'
                '[{"tool": "web_search", "params": {"query": "北京天气"}}]'
                '</tool_calls>')
        # review I-1：原生块与已执行工单同参数 → 去重跳过，仅执行一次
        assert called == ["北京天气"]
        assert fake_messages.turn == 2                    # 恰两轮（JSON 首轮 + 原生链）
        assert "北京今天晴 25℃" in out                    # 最终文本回复
        last = seen_round2[0]
        assert last["role"] == "user"
        assert last["content"] == [{
            "type": "tool_result", "tool_use_id": "tu_1",
            "content": "北京：晴 25℃"}]                    # 复用首轮执行的结果文本回传
        assert bot._tool_logs["u1"]["calls"] == [
            {"type": "搜索", "params": '{"query": "北京天气"}', "hit": True}]
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("web_search", None)
        else:
            reg._tool_executors["web_search"] = orig_ex


def test_native_dedup_two_blocks_same_params_run_once():
    """同参数两次原生块 → 只执行一次（首块执行，次块被去重跳过，工具不重放）。"""
    from unittest.mock import Mock, patch
    from src.bot.handler import MessageHandler
    from src.bot.tool_calls import ToolResult
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

    cap = CAPABILITY_BY_NAME["搜索"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("web_search")
    called: list = []
    seen_round2: list = []

    def spy(params, user_id="", user_question=""):
        called.append(params)
        return ToolResult("搜索", True, "北京：晴 25℃")

    bot = MessageHandler.__new__(MessageHandler)
    llm = Mock()
    llm.api_key = "test-key"
    llm.model = "deepseek-v4-flash"
    llm.provider = "deepseek"
    bot.llm = llm
    bot.session_dao = None
    bot._downgraded = {}
    bot._tool_logs = {}
    bot._citations = {}
    bot._analysis_facts = {}

    def fake_messages(api_key, messages, model=None, max_tokens=0,
                      temperature=0.0, timeout=0.0, tools=None,
                      tool_choice=None, **kwargs):
        if not hasattr(fake_messages, "turn"):
            fake_messages.turn = 0
        fake_messages.turn += 1
        if fake_messages.turn == 1:
            # 转换调用：返回两个同参数原生块（与工单参数不同 → 去重只发生在块之间）
            return {
                "stop_reason": "tool_use",
                "content": [
                    {"type": "tool_use", "id": "tu_1", "name": "web_search",
                     "input": {"query": "北京天气"}},
                    {"type": "tool_use", "id": "tu_2", "name": "web_search",
                     "input": {"query": "北京天气"}},
                ],
            }
        if fake_messages.turn >= 2:
            seen_round2.append(messages[-1])
            return {"stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "两地天气都查到了。"}]}
        return {"stop_reason": "end_turn",
                "content": [{"type": "text", "text": "两地天气都查到了。"}]}

    try:
        bind_executors({"web_search": spy}, {})
        with patch("src.llm.client.deepseek_anthropic_messages",
                   side_effect=fake_messages):
            out = bot._run_tool_loop(
                "上海和北京天气", "u1",
                '好的。<tool_calls>'
                '[{"tool": "web_search", "params": {"query": "上海天气"}}]'
                '</tool_calls>')
        # 工单 1 次 + 同参数原生块仅 1 次（第二个被去重跳过）= 共 2 次执行
        assert called == ["上海天气", "北京天气"]
        assert fake_messages.turn == 2
        assert "两地天气都查到了" in out
        # 协议修复（真实 API 冒烟发现）：单轮多原生块必须合并为一条 user 消息回传
        last = seen_round2[0]
        assert last["role"] == "user"
        results = last["content"]
        assert [r["type"] for r in results] == ["tool_result", "tool_result"]
        assert [r["tool_use_id"] for r in results] == ["tu_1", "tu_2"]
        assert [r["content"] for r in results] == ["北京：晴 25℃", "北京：晴 25℃"]  # 首块执行结果 + 次块复用去重结果
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("web_search", None)
        else:
            reg._tool_executors["web_search"] = orig_ex


def test_native_two_blocks_diff_params_run_once_messages():
    """异参数双原生块（并行双查询）→ 执行两次，且合并为一条 user 消息回传全部
    tool_result（Anthropic 协议：每个 tool_use 都要有匹配 tool_result，必须同消息回传）。
    真实 API 冒烟曾 400：逐块回传 'ids were found without tool_result blocks immediately after'。"""
    from unittest.mock import Mock, patch
    from src.bot.handler import MessageHandler
    from src.bot.tool_calls import ToolResult
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

    cap = CAPABILITY_BY_NAME["搜索"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("web_search")
    called: list = []
    seen_round2: list = []

    def spy(params, user_id="", user_question=""):
        called.append(params)
        return ToolResult("搜索", True, f"结果:{params}")

    bot = MessageHandler.__new__(MessageHandler)
    llm = Mock()
    llm.api_key = "test-key"
    llm.model = "deepseek-v4-flash"
    llm.provider = "deepseek"
    bot.llm = llm
    bot.session_dao = None
    bot._downgraded = {}
    bot._tool_logs = {}
    bot._citations = {}
    bot._analysis_facts = {}

    def fake_messages(api_key, messages, model=None, max_tokens=0,
                      temperature=0.0, timeout=0.0, tools=None,
                      tool_choice=None, **kwargs):
        if not hasattr(fake_messages, "turn"):
            fake_messages.turn = 0
        fake_messages.turn += 1
        if fake_messages.turn == 1:
            # 转换调用：返回两个异参数原生块（deepseek 并行双查询高频行为）
            return {
                "stop_reason": "tool_use",
                "content": [
                    {"type": "tool_use", "id": "tu_a", "name": "web_search",
                     "input": {"query": "2026教育政策"}},
                    {"type": "tool_use", "id": "tu_b", "name": "web_search",
                     "input": {"query": "2026教育现状"}},
                ],
            }
        # 原生链续调：末条必须是合并回传的单条 user 消息
        seen_round2.append(messages[-1])
        return {"stop_reason": "end_turn",
                "content": [{"type": "text", "text": "两个查询都完成了。"}]}

    try:
        bind_executors({"web_search": spy}, {})
        with patch("src.llm.client.deepseek_anthropic_messages",
                   side_effect=fake_messages):
            out = bot._run_tool_loop(
                "查一下教育行业", "u1",
                '好的。<tool_calls>'
                '[{"tool": "web_search", "params": {"query": "先看看"}}]'
                '</tool_calls>')
        # 异参数 → 两个原生块都执行
        assert called == ["先看看", "2026教育政策", "2026教育现状"]
        assert fake_messages.turn == 2
        assert "两个查询都完成了" in out
        # 协议修复：单条 user 消息含全部 tool_result（同消息回传）
        last = seen_round2[0]
        assert last["role"] == "user"
        results = last["content"]
        assert [r["type"] for r in results] == ["tool_result", "tool_result"]
        assert [r["tool_use_id"] for r in results] == ["tu_a", "tu_b"]
        assert [r["content"] for r in results] == ["结果:2026教育政策", "结果:2026教育现状"]
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("web_search", None)
        else:
            reg._tool_executors["web_search"] = orig_ex


def test_native_dedup_blocks_different_params_both_run():
    """不同参数原生块 → 都执行（去重不误杀，仅同参数才算重复）。"""
    from unittest.mock import Mock, patch
    from src.bot.handler import MessageHandler
    from src.bot.tool_calls import ToolResult
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

    cap = CAPABILITY_BY_NAME["搜索"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("web_search")
    called: list = []

    def spy(params, user_id="", user_question=""):
        called.append(params)
        return ToolResult("搜索", True, "北京：晴 25℃")

    bot = MessageHandler.__new__(MessageHandler)
    llm = Mock()
    llm.api_key = "test-key"
    llm.model = "deepseek-v4-flash"
    llm.provider = "deepseek"
    bot.llm = llm
    bot.session_dao = None
    bot._downgraded = {}
    bot._tool_logs = {}
    bot._citations = {}
    bot._analysis_facts = {}

    def fake_messages(api_key, messages, model=None, max_tokens=0,
                      temperature=0.0, timeout=0.0, tools=None,
                      tool_choice=None, **kwargs):
        if not hasattr(fake_messages, "turn"):
            fake_messages.turn = 0
        fake_messages.turn += 1
        if fake_messages.turn == 1:
            # 转换调用：两个不同参数的原生块（均与工单参数不同）
            return {
                "stop_reason": "tool_use",
                "content": [
                    {"type": "tool_use", "id": "tu_1", "name": "web_search",
                     "input": {"query": "北京天气"}},
                    {"type": "tool_use", "id": "tu_2", "name": "web_search",
                     "input": {"query": "广州天气"}},
                ],
            }
        return {"stop_reason": "end_turn",
                "content": [{"type": "text", "text": "多地天气都查到了。"}]}

    try:
        bind_executors({"web_search": spy}, {})
        with patch("src.llm.client.deepseek_anthropic_messages",
                   side_effect=fake_messages):
            out = bot._run_tool_loop(
                "三个城市天气", "u1",
                '好的。<tool_calls>'
                '[{"tool": "web_search", "params": {"query": "上海天气"}}]'
                '</tool_calls>')
        # 工单 1 次 + 两个不同参数原生块各 1 次 = 共 3 次执行（全部执行）
        assert called == ["上海天气", "北京天气", "广州天气"]
        assert fake_messages.turn == 2
        assert "多地天气都查到了" in out
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("web_search", None)
        else:
            reg._tool_executors["web_search"] = orig_ex


def test_native_tool_use_loop_chain_failure_no_replay():
    """原生链中途失败（第二轮 LLM 抛错）→ 已执行的工单/原生块不重放（防重复执行）。"""
    from unittest.mock import Mock, patch
    from src.bot.handler import MessageHandler
    from src.bot.tool_calls import ToolResult
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

    cap = CAPABILITY_BY_NAME["搜索"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("web_search")
    called: list = []

    def spy(params, user_id="", user_question=""):
        called.append(params)
        return ToolResult("搜索", True, "北京：晴 25℃")

    bot = MessageHandler.__new__(MessageHandler)
    llm = Mock()
    llm.api_key = "test-key"
    llm.model = "deepseek-v4-flash"
    llm.provider = "deepseek"
    bot.llm = llm
    bot.session_dao = None
    bot._downgraded = {}
    bot._tool_logs = {}
    bot._citations = {}
    bot._analysis_facts = {}

    def fake_messages(api_key, messages, model=None, max_tokens=0,
                      temperature=0.0, timeout=0.0, tools=None,
                      tool_choice=None, **kwargs):
        if not hasattr(fake_messages, "turn"):
            fake_messages.turn = 0
        fake_messages.turn += 1
        if fake_messages.turn == 1:
            return {
                "stop_reason": "tool_use",
                "content": [
                    {"type": "tool_use", "id": "tu_1", "name": "web_search",
                     "input": {"query": "北京天气"}},
                ],
            }
        raise RuntimeError("第二轮上游失败")

    try:
        bind_executors({"web_search": spy}, {})
        with patch("src.llm.client.deepseek_anthropic_messages",
                   side_effect=fake_messages):
            out = bot._run_tool_loop(
                "北京天气怎么样", "u1",
                '好的，我来查。<tool_calls>'
                '[{"tool": "web_search", "params": {"query": "北京天气"}}]'
                '</tool_calls>')
        # review I-1 后：仅工单 1 次执行（原生块同参数被去重跳过），
        # 失败后不重放原工单
        assert called == ["北京天气"]
        assert fake_messages.turn == 2
        assert out == "好的，我来查。"                   # 静默降级返回原文（去标签）
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("web_search", None)
        else:
            reg._tool_executors["web_search"] = orig_ex


# ---- 批次 2 B1：批次 1 Minor 收尾（JSON 路径去重 / 超时边界 / 护栏固化） ----


def test_json_workorder_round_duplicate_dedup():
    """B1-4：同一轮 reply 内相同 JSON 工单重复 → 只执行一次，次份复用首次结果文本。

    修复前 JSON 路径只写 executed_keys 不查（去重仅原生链生效），同轮相同工单
    双执行（写型工具重复落库/双分配引用编号）。修复后与原生链同语义：跳过重复
    执行、不发"正在…"事件、不重复落库；结果以两条同文本注入 LLM。
    """
    from unittest.mock import Mock, patch
    from src.bot.handler import MessageHandler
    from src.bot.tool_calls import ToolResult
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

    cap = CAPABILITY_BY_NAME["搜索"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("web_search")
    called: list = []
    seen_msgs: list = []

    def spy(params, user_id="", user_question=""):
        called.append(params)
        return ToolResult("搜索", True, "北京：晴 25℃")

    bot = MessageHandler.__new__(MessageHandler)
    llm = Mock()
    llm.api_key = "test-key"
    llm.model = "deepseek-v4-flash"
    llm.provider = "deepseek"
    bot.llm = llm
    bot.session_dao = None
    bot._downgraded = {}
    bot._tool_logs = {}
    bot._citations = {}
    bot._analysis_facts = {}

    def fake_messages(api_key, messages, model=None, max_tokens=0,
                      temperature=0.0, timeout=0.0, tools=None,
                      tool_choice=None, **kwargs):
        seen_msgs.append(messages)
        return {"stop_reason": "end_turn",
                "content": [{"type": "text", "text": "北京今天晴 25℃。"}]}

    try:
        bind_executors({"web_search": spy}, {})
        with patch("src.llm.client.deepseek_anthropic_messages",
                   side_effect=fake_messages):
            out = bot._run_tool_loop(
                "北京天气怎么样", "u1",
                '好的，我来查。<tool_calls>'
                '[{"tool": "web_search", "params": {"query": "北京天气"}}, '
                '{"tool": "web_search", "params": {"query": "北京天气"}}]'
                '</tool_calls>')
        # 同参工单只执行一次；次份复用首次结果文本注入 LLM
        assert called == ["北京天气"]
        assert out == "北京今天晴 25℃。"
        # 工具结果以两条同文本 JSON 注入（{"ok": true, "data": ...} ×2）
        tail = seen_msgs[0][-1]["content"]
        assert tail.count('{"tool": "搜索", "ok": true, "data": "北京：晴 25℃"}') == 2
        # 落库只记一次（去重不重复落库）
        assert bot._tool_logs["u1"]["calls"] == [
            {"type": "搜索", "params": '{"query": "北京天气"}', "hit": True}]
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("web_search", None)
        else:
            reg._tool_executors["web_search"] = orig_ex


def test_text_tag_json_params_cross_protocol_dedup():
    """B1-3：legacy 文本标签带 JSON 参数（`搜索: {"query": ...}`）→ 参数归一为
    dict 键参与跨协议去重：后续同参原生块跳过执行（复用结果回传 tool_result）。

    修复前 legacy 文本路径键形是原始字符串（含空格/键序原样）、原生块键形是
    sort_keys 归一 JSON dict——非规范书写的 JSON 文本标签（如内嵌空格）键形
    不同 → 跨协议同参双执行。修复后解析归一统一键形。纯文本参数（如"北京天气"）
    与结构化 dict 语义归一不可行（legacy 正在淘汰），按设计固化不跨协议去重。
    """
    from unittest.mock import Mock, patch
    from src.bot.handler import MessageHandler
    from src.bot.tool_calls import ToolResult
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

    cap = CAPABILITY_BY_NAME["搜索"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("web_search")
    called: list = []
    seen_round2: list = []

    def spy(params, user_id="", user_question=""):
        called.append(params)
        return ToolResult("搜索", True, "北京：晴 25℃")

    bot = MessageHandler.__new__(MessageHandler)
    llm = Mock()
    llm.api_key = "test-key"
    llm.model = "deepseek-v4-flash"
    llm.provider = "deepseek"
    bot.llm = llm
    bot.session_dao = None
    bot._downgraded = {}
    bot._tool_logs = {}
    bot._citations = {}
    bot._analysis_facts = {}

    def fake_messages(api_key, messages, model=None, max_tokens=0,
                      temperature=0.0, timeout=0.0, tools=None,
                      tool_choice=None, **kwargs):
        if not hasattr(fake_messages, "turn"):
            fake_messages.turn = 0
        fake_messages.turn += 1
        if fake_messages.turn == 1:
            # 转换调用返回与 legacy 文本标签同参（结构化）的原生块
            return {"stop_reason": "tool_use",
                    "content": [{"type": "tool_use", "id": "tu_1",
                                 "name": "web_search",
                                 "input": {"query": "北京天气"}}]}
        seen_round2.append(messages[-1])
        return {"stop_reason": "end_turn",
                "content": [{"type": "text", "text": "北京今天晴 25℃。"}]}

    try:
        bind_executors({"web_search": spy}, {})
        with patch("src.llm.client.deepseek_anthropic_messages",
                   side_effect=fake_messages):
            out = bot._run_tool_loop(
                "北京天气怎么样", "u1",
                # 非规范 JSON 书写（冒号后双空格）：原始串键形 ≠ sort_keys dict 键形
                '好的，我来查。<tool_call>搜索: {"query":  "北京天气"}</tool_call>')
        # legacy 文本标签执行 1 次；同参原生块被跨协议去重跳过（不再执行）
        assert called == ['{"query":  "北京天气"}']
        assert fake_messages.turn == 2
        assert out == "北京今天晴 25℃。"
        last = seen_round2[0]
        assert last["role"] == "user"
        assert last["content"] == [{
            "type": "tool_result", "tool_use_id": "tu_1",
            "content": "北京：晴 25℃"}]          # 复用 legacy 执行结果回传（幂等）
        assert bot._tool_logs["u1"]["calls"] == [
            {"type": "搜索", "params": '{"query":  "北京天气"}', "hit": True}]
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("web_search", None)
        else:
            reg._tool_executors["web_search"] = orig_ex


def test_web_search_timeout_margin_over_internal():
    """B1-5：web_search 外层超时 > Bing 内部 SEARCH_TIMEOUT（15s）→ 边界竞态消除。

    15=15 时外层 fut.result(timeout) 与内部 httpx 超时同刻竞争：外层先触发 →
    误判超时 → 白重试一次（最坏 ~30s + 双请求）。余量对齐 70s/60s 原则（Task 4
    I-2）：内部超时确定性先触发（正常返回失败结果，不触发重试），外层仅兜底。
    """
    from src.rag.web_search import SEARCH_TIMEOUT
    cap = reg.CAPABILITY_BY_ID["web_search"]
    assert cap.timeout_s > SEARCH_TIMEOUT, \
        f"web_search 外层 {cap.timeout_s}s 必须大于内部 {SEARCH_TIMEOUT}s（边界竞态）"
    assert cap.timeout_s - SEARCH_TIMEOUT >= 5.0, "余量应 ≥5s（对齐 70s/60s 余量原则）"


def test_real_timeout_executor_actual_timeout():
    """B1-10：真超时用例——executor 内部真实网络超时（本地 HTTP 只接不答 →
    真实 httpx.ReadTimeout），而非 sleep 假超时。

    关键区分：旧用例 sleep 1.0s 由 wrapper 掐表（fut.result timeout）杀死；
    本例 executor 自身发起真实请求并在内部 0.3s 处超时抛出（ReadTimeout 实测
    捕获），wrapper 按工具异常重试 → 兜底文案。总耗时 ≈ 2×0.3s << 外层 20s，
    证明内部超时先于外层触发（B1-5 边界修复的行为侧）。
    """
    import dataclasses
    import socket
    import threading
    import time

    import httpx

    from src.bot.handler import MessageHandler
    from src.bot.tool_calls import ToolResult
    from src.bot.capability_registry import CAPABILITY_BY_NAME

    # 本地真实超时源：监听套接字接受连接但永不写响应（确定性 ReadTimeout）
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    caught = []

    def acceptor():
        try:
            conn, _ = srv.accept()
            time.sleep(2.0)          # 只收连接不响应 → 客户端 0.3s 超时
            conn.close()
        except OSError:
            pass

    threading.Thread(target=acceptor, daemon=True).start()
    calls: list = []

    def real_slow_executor(params, user_id="", user_question=""):
        calls.append(params)
        try:
            httpx.get(f"http://127.0.0.1:{port}/", timeout=0.3)
        except httpx.TimeoutException as e:   # 真实网络超时（非 sleep 模拟）
            caught.append(type(e).__name__)
            raise
        return ToolResult("搜索", True, "不应到达")

    bot = MessageHandler.__new__(MessageHandler)
    cap = dataclasses.replace(
        CAPABILITY_BY_NAME["搜索"], executor=real_slow_executor,
        timeout_s=20.0, retries=1)
    try:
        t0 = time.monotonic()
        r = bot._run_with_timeout(cap, "北京天气", "u1", "今天天气？")
        elapsed = time.monotonic() - t0
    finally:
        srv.close()
    assert caught == ["ReadTimeout", "ReadTimeout"]   # 两次尝试都是真实网络超时
    assert len(calls) == 2                            # 首次 + 重试 1 次
    assert r.ok is False
    assert "执行超时/异常（已重试1次）" in r.text
    assert elapsed < 5.0, \
        f"内部超时应先于外层 20s 触发（真实耗时 {elapsed:.2f}s ≈ 2×0.3s）"


def test_timeout_zombie_thread_self_terminates_bounded():
    """B1-6：wrapper 超时后不等待僵尸线程（非阻塞），线程靠自身内部超时自然结束
    ——解释器 atexit 对非 daemon 线程的 join 阻塞上界 = 工具内部超时（有界不卡死）。

    修复前 shutdown(wait=True) 每次重试都要等被超时线程跑完；wait=False 已缓解，
    本测试固化该机制（生产 LLM 60s / 网络 15s 同理有界）。
    """
    import dataclasses
    import threading
    import time

    from src.bot.handler import MessageHandler
    from src.bot.tool_calls import ToolResult
    from src.bot.capability_registry import CAPABILITY_BY_NAME

    captured: dict = {"finished": False}

    def slow(params, user_id="", user_question=""):
        captured["thread"] = threading.current_thread()
        time.sleep(0.4)          # 模拟内部超时 0.4s 后自终止（生产 60s/15s）
        captured["finished"] = True
        return ToolResult("搜索", True, "迟到的成功")

    bot = MessageHandler.__new__(MessageHandler)
    cap = dataclasses.replace(
        CAPABILITY_BY_NAME["搜索"], executor=slow, timeout_s=0.1, retries=0)
    r = bot._run_with_timeout(cap, "北京天气", "u1", "")
    # B1-6 强化（全量回归 1 次偶发失败排查后）：改用「完成标志」证明非阻塞——
    # 原 elapsed<0.3 墙钟断言在重负载（全量套件 3.7GB RSS）下偶发超 0.3s 误报。
    # 标志断言与负载无关且语义更直接：wrapper 返回时僵尸线程必然尚未跑完
    # （旧 wait=True 行为会等它跑完 → finished=True，被本断言抓住）。
    assert r.ok is False
    assert captured["finished"] is False, "wrapper 不应等僵尸线程跑完（旧 wait=True 会等 0.4s+）"
    th = captured["thread"]
    assert th.is_alive(), "僵尸线程此刻仍在运行（未被 join 等死）"
    assert th.join(timeout=1.0) is None, "内部超时(0.4s)后线程自然结束 → atexit join 有界"


def test_max_iterations_2_guard_drops_iteration2_blocks():
    """B1-9：MAX_TOOL_ITERATIONS=2 护栏语义固化——迭代 2 再出 tool_use 块 →
    不执行、不提取文本，循环耗尽静默丢弃（用户看到迭代 1 引导句）。

    设计护栏（Task 7 台账）：真实搜索一轮收敛为常态；迭代 2 的新块是 LLM
    幻觉/失控信号，丢弃是红线设计而非缺陷。本测试锁死该语义防回潮。
    """
    from unittest.mock import Mock, patch
    from src.bot.handler import MessageHandler
    from src.bot.tool_calls import ToolResult
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

    cap = CAPABILITY_BY_NAME["搜索"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("web_search")
    called: list = []

    def spy(params, user_id="", user_question=""):
        called.append(params)
        return ToolResult("搜索", True, f"结果:{params}")

    bot = MessageHandler.__new__(MessageHandler)
    llm = Mock()
    llm.api_key = "test-key"
    llm.model = "deepseek-v4-flash"
    llm.provider = "deepseek"
    bot.llm = llm
    bot.session_dao = None
    bot._downgraded = {}
    bot._tool_logs = {}
    bot._citations = {}
    bot._analysis_facts = {}

    def fake_messages(api_key, messages, model=None, max_tokens=0,
                      temperature=0.0, timeout=0.0, tools=None,
                      tool_choice=None, **kwargs):
        if not hasattr(fake_messages, "turn"):
            fake_messages.turn = 0
        fake_messages.turn += 1
        if fake_messages.turn == 1:
            return {"stop_reason": "tool_use",
                    "content": [{"type": "tool_use", "id": "tu_1",
                                 "name": "web_search",
                                 "input": {"query": "北京天气"}}]}
        # 迭代 2 再出新工具块（幻觉/失控信号）→ 护栏应静默丢弃
        return {"stop_reason": "tool_use",
                "content": [{"type": "tool_use", "id": "tu_2",
                             "name": "web_search",
                             "input": {"query": "广州天气"}}]}

    try:
        bind_executors({"web_search": spy}, {})
        with patch("src.llm.client.deepseek_anthropic_messages",
                   side_effect=fake_messages):
            out = bot._run_tool_loop(
                "北京天气怎么样", "u1",
                '好的，我来查。<tool_calls>'
                '[{"tool": "web_search", "params": {"query": "上海天气"}}]'
                '</tool_calls>')
        # 迭代 1：JSON 工单(上海) + 转换调用块(北京) 各执行一次
        # 迭代 2：执行北京块后 LLM 又出新块(广州) → 循环耗尽，广州不执行
        assert called == ["上海天气", "北京天气"]
        assert fake_messages.turn == 2            # 恰 2 轮，无第三轮
        assert "广州天气" not in "".join(called)  # 迭代 2 新块未执行
        assert out == "好的，我来查。"            # 用户看到迭代 1 引导句，无标签残留
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("web_search", None)
        else:
            reg._tool_executors["web_search"] = orig_ex


# ---- Task 4 review C-1/I-1：真实 executor 绑定签名 + 超时重试（生产回归） ----


def test_real_lambda_signature_matches_submit():
    """C-1 回归：__init__ 真实绑定 lambda 形参必须接受 _run_with_timeout 的 submit 关键字。

    最终审查实证的生产 bug：__init__ 绑定 lambda 形参是 uid/uq，而
    _run_with_timeout 的 ex.submit(cap.executor, params, user_id=...,
    user_question=...) 传关键字 user_id/user_question → TypeError →
    except Exception: pass 吞掉 → 重试同败 → 兜底文案。此前全部测试用自造 spy
    （签名恰好匹配 submit kwargs），真实绑定路径零覆盖。

    两层锁死，防止盲区复发：
    (a) AST 提取 __init__ 源码里 bind_executors 的 9 个 tool lambda 真实形参名，
        必须为 user_id/user_question（代码若回退 uid/uq 立即红，直锁真实 __init__）；
    (b) 按 __init__ 逐字形态的 lambda 绑定后，经 _run_with_timeout 真实提交路径
        执行：不抛 TypeError、结果正确、无兜底文案；inspect.signature.bind
        锁死约定（绑定签名必须接受 submit 的 kwargs）。
    """
    import ast
    import inspect
    import textwrap

    from src.bot.handler import MessageHandler
    from src.bot.tool_calls import ToolResult
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_ID

    # (a) 真实 __init__ 绑定签名守卫（直读源码，不经 mock）
    tree = ast.parse(textwrap.dedent(inspect.getsource(MessageHandler.__init__)))
    bind_calls = [n for n in ast.walk(tree)
                  if isinstance(n, ast.Call)
                  and getattr(n.func, "id", None) == "bind_executors"]
    assert len(bind_calls) == 1, "handler.py __init__ 应恰有一次 bind_executors 调用"
    tool_dict = bind_calls[0].args[0]
    assert isinstance(tool_dict, ast.Dict)
    real_params: dict = {}
    for key_node, val_node in zip(tool_dict.keys, tool_dict.values):
        if isinstance(key_node, ast.Constant) and isinstance(val_node, ast.Lambda):
            real_params[key_node.value] = [a.arg for a in val_node.args.args]
    assert set(real_params) == {"bazi_chart", "quote_rag", "web_search", "dream",
                                "fengshui", "zeri", "record_lookup", "hehun",
                                "naming", "fortune_cycle"}
    assert len(real_params) == 10
    for cid, params in real_params.items():
        assert "user_id" in params, f"{cid} lambda 缺 user_id 形参: {params}"
        assert "user_question" in params, f"{cid} lambda 缺 user_question 形参: {params}"
        assert "uid" not in params and "uq" not in params, \
            f"{cid} lambda 仍用旧形参名 uid/uq: {params}"

    # (b) 逐字形态 lambda + 真实提交路径（覆盖 review 点名的零覆盖路径）
    bot = MessageHandler.__new__(MessageHandler)
    # 实例级 spy 工具实现（转发参数逐字与 __init__ 内部一致）
    bot._tool_bazi = lambda p, user_id: ToolResult("排盘", True, f"bazi:{user_id}:{p}")
    bot._tool_search = lambda p, user_id="", user_question="": ToolResult(
        "检索", True, f"search:{user_id}:{user_question}:{p}")
    bot._tool_web_search = lambda p, user_id="": ToolResult(
        "搜索", True, f"web:{user_id}:{p}")
    bot._tool_dream = lambda p, user_id="": ToolResult("解梦", True, f"dream:{user_id}:{p}")
    bot._tool_fengshui = lambda p: ToolResult("风水", True, f"fs:{p}")
    bot._tool_zeri = lambda p, user_id="": ToolResult("择日", True, f"zeri:{user_id}:{p}")
    bot._tool_query_records = lambda p, user_id="": ToolResult(
        "查记录", True, f"rec:{user_id}:{p}")
    bot._tool_hehun = lambda p, user_id="": ToolResult(
        "合婚", True, f"hehun:{user_id}:{p}")
    bot._tool_naming = lambda p, user_id="": ToolResult(
        "起名", True, f"naming:{user_id}:{p}")
    bot._tool_fortune_cycle = lambda p, user_id="": ToolResult(
        "流月流年", True, f"cycle:{user_id}:{p}")

    # 与 handler.py __init__（bind_executors 块）逐字一致的 lambda 形态：
    # 形参名 user_id/user_question（修复后的约定），内部转发不变
    executors = {
        "bazi_chart": lambda p, user_id="", user_question="": bot._tool_bazi(p, user_id),
        "quote_rag": lambda p, user_id="", user_question="": bot._tool_search(
            p, user_id=user_id, user_question=user_question),
        "web_search": lambda p, user_id="", user_question="": bot._tool_web_search(
            p, user_id=user_id),
        "dream": lambda p, user_id="", user_question="": bot._tool_dream(p, user_id),
        "fengshui": lambda p, user_id="", user_question="": bot._tool_fengshui(p),
        "zeri": lambda p, user_id="", user_question="": bot._tool_zeri(p, user_id),
        "record_lookup": lambda p, user_id="", user_question="": bot._tool_query_records(
            p, user_id),
        "hehun": lambda p, user_id="", user_question="": bot._tool_hehun(p, user_id),
        "naming": lambda p, user_id="", user_question="": bot._tool_naming(p, user_id),
        "fortune_cycle": lambda p, user_id="", user_question="": bot._tool_fortune_cycle(
            p, user_id),
    }
    expected = {
        "bazi_chart": "bazi:u1:P",
        "quote_rag": "search:u1:q1:P",
        "web_search": "web:u1:P",
        "dream": "dream:u1:P",
        "fengshui": "fs:P",
        "zeri": "zeri:u1:P",
        "record_lookup": "rec:u1:P",
        "hehun": "hehun:u1:P",
        "naming": "naming:u1:P",
        "fortune_cycle": "cycle:u1:P",
    }
    orig_exec = {cid: CAPABILITY_BY_ID[cid].executor for cid in executors}
    orig_ex = {cid: reg._tool_executors.get(cid) for cid in executors}
    try:
        bind_executors(executors, {})
        for cid, want in expected.items():
            cap = CAPABILITY_BY_ID[cid]
            # 锁死约定：绑定签名必须 bind 住 submit 的关键字（user_id/user_question）
            inspect.signature(cap.executor).bind("P", user_id="u1", user_question="q1")
            # 真实提交路径：submit(cap.executor, params, user_id=..., user_question=...)
            r = bot._run_with_timeout(cap, "P", "u1", "q1")
            assert r.ok is True, f"{cid} 执行失败: {r.text!r}"
            assert "执行超时/异常" not in r.text   # 无兜底文案
            assert r.text == want, f"{cid} 结果错误: {r.text!r} != {want!r}"
    finally:
        # 恢复原绑定，避免污染后续测试
        for cid in executors:
            CAPABILITY_BY_ID[cid].__dict__["executor"] = orig_exec[cid]
            if orig_ex[cid] is None:
                reg._tool_executors.pop(cid, None)
            else:
                reg._tool_executors[cid] = orig_ex[cid]


def test_timeout_retry_fallback():
    """I-1：慢工具超时 → 非阻塞重试 1 次 → 兜底文案（spec 1.4 第 4 条）。

    dataclasses.replace 造 timeout_s=0.2/retries=1 的 cap（不动全局注册表），
    桩 executor sleep 模拟慢工具：总耗时 < 1.5s（证明非阻塞重试，不等僵尸线程）、
    executor 被调 2 次（首次超时 + 重试 1 次）、兜底文案含「执行超时/异常（已重试1次）」。
    """
    import dataclasses
    import time

    from src.bot.handler import MessageHandler
    from src.bot.tool_calls import ToolResult
    from src.bot.capability_registry import CAPABILITY_BY_NAME

    calls: list = []

    def slow(params, user_id="", user_question=""):
        calls.append((params, user_id, user_question))
        time.sleep(1.0)  # 模拟慢工具（生产 LLM/网络 60s/15s，这里 1s 足够超时）
        return ToolResult("搜索", True, "不应到达")

    bot = MessageHandler.__new__(MessageHandler)
    cap = dataclasses.replace(
        CAPABILITY_BY_NAME["搜索"], executor=slow, timeout_s=0.2, retries=1)
    t0 = time.monotonic()
    r = bot._run_with_timeout(cap, "北京天气", "u1", "今天天气？")
    elapsed = time.monotonic() - t0
    assert elapsed < 1.5, f"重试应非阻塞（不等慢工具线程），实际 {elapsed:.2f}s"
    assert r.ok is False
    assert "执行超时/异常（已重试1次）" in r.text
    assert len(calls) == 2                       # 首次超时 + 重试 1 次
    assert all(a[1:] == ("u1", "今天天气？") for a in calls)  # 关键字正确透传


def test_exception_retry_fallback():
    """I-1：异常重试 → 重试 1 次成功即恢复；两次全败 → 兜底文案。"""
    import dataclasses

    from src.bot.handler import MessageHandler
    from src.bot.tool_calls import ToolResult
    from src.bot.capability_registry import CAPABILITY_BY_NAME

    bot = MessageHandler.__new__(MessageHandler)

    # 先抛后成：重试后恢复成功结果（证明确实重调 executor，非一次定生死）
    calls: list = []

    def flaky(params, user_id="", user_question=""):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("首次内部故障")
        return ToolResult("搜索", True, "恢复成功")

    cap = dataclasses.replace(CAPABILITY_BY_NAME["搜索"], executor=flaky, retries=1)
    r = bot._run_with_timeout(cap, "北京天气", "u1", "")
    assert r.ok is True and r.text == "恢复成功"
    assert len(calls) == 2

    # 两次全败：兜底文案
    calls2: list = []

    def always_boom(params, user_id="", user_question=""):
        calls2.append(1)
        raise RuntimeError("永远失败")

    cap2 = dataclasses.replace(CAPABILITY_BY_NAME["搜索"], executor=always_boom, retries=1)
    r2 = bot._run_with_timeout(cap2, "北京天气", "u1", "")
    assert r2.ok is False
    assert "执行超时/异常（已重试1次）" in r2.text
    assert len(calls2) == 2


def test_native_tool_use_loop_exception_falls_back_to_json():
    """原生路径异常（LLM 抛错）→ 立即降级：不执行原生块，走 JSON 工单文本路径。"""
    from unittest.mock import Mock, patch
    from src.bot.handler import MessageHandler
    from src.bot.tool_calls import ToolResult
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

    cap = CAPABILITY_BY_NAME["搜索"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("web_search")
    called: list = []

    def spy(params, user_id="", user_question=""):
        called.append(params)
        return ToolResult("搜索", True, "北京：晴 25℃")

    bot = MessageHandler.__new__(MessageHandler)
    llm = Mock()
    llm.api_key = "test-key"
    llm.model = "deepseek-v4-flash"
    llm.provider = "deepseek"
    bot.llm = llm
    bot.session_dao = None
    bot._downgraded = {}
    bot._tool_logs = {}
    bot._citations = {}
    bot._analysis_facts = {}

    def boom(*a, **k):
        raise RuntimeError("上游不可用")

    try:
        bind_executors({"web_search": spy}, {})
        with patch("src.llm.client.deepseek_anthropic_messages",
                   side_effect=boom):
            out = bot._run_tool_loop(
                "北京天气怎么样", "u1",
                '好的，我来查。<tool_calls>'
                '[{"tool": "web_search", "params": {"query": "北京天气"}}]'
                '</tool_calls>')
        # LLM 失败 → 静默降级返回原文（去标签），工具已执行（JSON 工单路径完好）
        assert called == ["北京天气"]
        assert out == "好的，我来查。"
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("web_search", None)
        else:
            reg._tool_executors["web_search"] = orig_ex
