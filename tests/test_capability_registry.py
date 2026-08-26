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
