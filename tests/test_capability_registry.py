"""批次 1：统一能力注册表测试（spec 1.4 第 1 条 + 一致性）。"""
import sys

sys.path.insert(0, "src")

from src.bot import capability_registry as reg  # noqa: E402
from src.bot.tool_calls import TOOL_REGISTRY  # noqa: E402


def test_tool_coverage():
    """7 个工具全注册：排盘/检索/搜索/解梦/风水/择日/查记录。"""
    names = {c.name for c in reg.CAPABILITIES if c.cap_type == "tool"}
    assert names == {"排盘", "检索", "搜索", "解梦", "风水", "择日", "查记录"}


def test_tool_registry_projection():
    """TOOL_REGISTRY 从注册表投影：7 键，desc/requires 与注册表一致。"""
    assert set(TOOL_REGISTRY) == {"排盘", "检索", "搜索", "解梦", "风水", "择日", "查记录"}
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


def test_tool_description_built():
    """工具说明书生成：7 工具齐、含 cap_id 与超时参数。"""
    from src.bot.capability_registry import build_tool_description
    d = build_tool_description()
    for cid in ("bazi_chart", "web_search", "quote_rag", "dream",
                "fengshui", "zeri", "record_lookup"):
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
    """同名 cap_id（fengshui/zeri/dream）：tool 条目优先于 intent 条目（工单校验走 tool）。

    注：_TOOL_CAPS(7) + _INTENT_CAPS(15) = 22 项列表，但 fengshui/zeri/dream 的
    cap_id 与中文名在两类中完全相同，去重后唯一键为 19（修复前后一致）。
    """
    assert reg.CAPABILITY_BY_ID["fengshui"].cap_type == "tool"
    assert reg.CAPABILITY_BY_ID["zeri"].cap_type == "tool"
    assert reg.CAPABILITY_BY_ID["dream"].cap_type == "tool"
    assert reg.CAPABILITY_BY_NAME["风水"].cap_type == "tool"
    assert reg.CAPABILITY_BY_NAME["择日"].cap_type == "tool"
    assert reg.CAPABILITY_BY_NAME["解梦"].cap_type == "tool"
    assert len(reg.CAPABILITY_BY_ID) == 19
    assert len(reg.CAPABILITY_BY_NAME) == 19
    # 回归点：intent 覆盖 tool 时，tool 必填校验静默失效（validate_params 返回 None）
    assert reg.validate_params("fengshui", {}) is not None
    assert reg.validate_params("zeri", {}) is not None
    assert reg.validate_params("dream", {}) is not None


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
    """注册表 → Anthropic 风格 tools schema：7 个、与 build_tool_description 同源。"""
    schemas = reg.build_tool_schema_list()
    assert len(schemas) == 7
    ids = {s["name"] for s in schemas}
    assert ids == {"bazi_chart", "quote_rag", "web_search", "dream",
                   "fengshui", "zeri", "record_lookup"}
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
            # 首轮：必须带 tools（payload 有注册表 schema）
            assert tools and [t["name"] for t in tools] == [
                "bazi_chart", "quote_rag", "web_search", "dream",
                "fengshui", "zeri", "record_lookup"]
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
