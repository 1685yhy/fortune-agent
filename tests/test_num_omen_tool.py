"""批次 2 E5：数字吉凶工具（cap_id: num_omen）——参数校验 + 规则逻辑 + e2e 链路。

规则依据（与 src/tools/num_omen.py docstring 同源，本测试锁定行为）：
- 81 数理表：src/engines/xingming.py NUMEROLOGY_81 同源复用（与 E2 起名工具
  五格评分同表，E5 与 E2 口径一致，不另造表）；大吉/半吉/凶三档
- 尾号五行：xingming.py DIGIT_TO_WUXING（1,2木 3,4火 5,6土 7,8金 9,0水）
- 数理归约：n % 81，余 0 取 81（1-81 全表可及）
- 尾号数理：末两位整数（不足两位取全部）归约；整体数理：数字和归约
- 场景识别：关键词优先（手机/电话/号码→手机号；车牌/牌照/号牌/车号→车牌；
  门牌/房间/室/栋/单元/号房→门牌；楼层/层/楼→楼层），无关键词按位数
  （11 位手机号、5-7 位车牌、1-4 位门牌/楼层）
- 解析容错：非数字字符（含全角、横线、空格）剔除；空 → None；>20 位拒绝
- 测试命例（确定性口径）：
  · 13812345678（手机号）：数字和 48 → 数理48 大吉（青松立鹤）；
    尾号 78 → 数理78 半吉（晚境凄凉）
  · 8楼「8」：尾号/整体均 数理8 大吉（努力发达）
  · 楼层「4」：数理4 凶（坎坷不平）→ 换尾号建议
  · 京A88888「88888」（车牌）：数字和 40 → 数理40 半吉（豪胆迈进）；
    尾号 88 → 88%81=7 → 数理7 大吉（精神旺盛）
  · 44444（车牌）：4 连避讳 + 尾号/整体双凶
  · 3号楼501室「3501」：门牌；数字和 9 → 数理9 凶；尾号 01 → 数理1 大吉
"""
import sys

sys.path.insert(0, "src")

from src.bot import capability_registry as reg  # noqa: E402
from src.engines.xingming import NUMEROLOGY_81  # noqa: E402
from src.tools import num_omen as no  # noqa: E402


# ============================================================
# 参数校验（注册表 schema / 说明书一致性）
# ============================================================

def test_num_schema_required_keys():
    """schema 必填键恰为 number；properties 两键齐全均为 string。"""
    schema = reg._TOOL_PARAMS_SCHEMAS["num_omen"]
    assert schema["required"] == ["number"]
    for key in ("number", "context"):
        assert key in schema["properties"]
        assert schema["properties"][key]["type"] == "string"


def test_validate_params_num_omen():
    """参数校验：number 齐 → None（context 可省）；缺 number/空/非字符串 → 错误串。"""
    ok = reg.validate_params("num_omen", {
        "number": "13812345678", "context": "手机号"})
    assert ok is None
    ok2 = reg.validate_params("num_omen", {"number": "8楼"})
    assert ok2 is None
    err = reg.validate_params("num_omen", {"context": "手机号"})
    assert err is not None and "number" in err
    err2 = reg.validate_params("num_omen", {})
    assert err2 is not None and "number" in err2
    err3 = reg.validate_params("num_omen", {"number": 123})
    assert err3 is not None


def test_num_schema_keys_in_description_and_requires():
    """批次 1 P1 #2 教训固化：工具描述/requires 里的参数键必须与 schema
    键完全一致（number/context 四处点名）。"""
    cap = reg.CAPABILITY_BY_ID["num_omen"]
    assert cap.cap_type == "tool"
    for key in ("number", "context"):
        assert key in cap.description
        assert key in cap.requires
    desc = reg.build_tool_description()
    assert "数字吉凶" in desc and "num_omen" in desc
    for key in ("number", "context"):
        assert key in desc
    # 属性示例也含各自键名（LLM 按示例填参能过校验）
    props = cap.params_schema["properties"]
    for key in ("number", "context"):
        assert key in props[key]["description"]


def test_num_in_registry_projection():
    """TOOL_REGISTRY / build_tool_schema_list / TOOL_NAME_BY_ID 自动投影含数字吉凶。"""
    from src.bot.tool_calls import TOOL_REGISTRY, TOOL_NAME_BY_ID
    assert "数字吉凶" in TOOL_REGISTRY
    assert TOOL_REGISTRY["数字吉凶"]["key"] == "num_omen"
    assert TOOL_NAME_BY_ID["num_omen"] == "数字吉凶"
    schemas = {s["name"]: s for s in reg.build_tool_schema_list()}
    assert "num_omen" in schemas
    assert schemas["num_omen"]["input_schema"] == \
        reg.CAPABILITY_BY_ID["num_omen"].params_schema


def test_num_workorder_and_native_parse():
    """JSON 工单 / 原生 tool_use：英文 cap_id num_omen 归一为中文名 数字吉凶 并带两键。"""
    from src.bot.tool_calls import parse_native_tool_use_blocks, parse_tool_calls
    calls = parse_tool_calls(
        '<tool_calls>[{"tool": "num_omen", "params": {"number": "13812345678", '
        '"context": "手机号"}}]</tool_calls>')
    assert [(c.name, c.params_obj) for c in calls] == [
        ("数字吉凶", {"number": "13812345678", "context": "手机号"})]
    blocks = parse_native_tool_use_blocks([{
        "type": "tool_use", "id": "tu_n", "name": "num_omen",
        "input": {"number": "8楼"}}])
    assert blocks[0].name == "数字吉凶"
    assert blocks[0].params_obj == {"number": "8楼"}


def test_num_serialize_roundtrip():
    """serialize_params 产物（k: v 换行）→ parse_num_params 两键还原。"""
    from src.bot.tool_calls import serialize_params
    text = serialize_params({
        "number": "13812345678", "context": "手机号"})
    info = no.parse_num_params(text)
    assert info == {"number": "13812345678", "context": "手机号"}
    # 单键工单 serialize 产物是纯数字串（无键前缀）→ 文本兜底按位数识别
    info2 = no.parse_num_params(serialize_params({"number": "13812345678"}))
    assert info2 == {"number": "13812345678", "context": "手机号"}


# ============================================================
# 规则逻辑：参数拆分 / 场景识别 / 数理归约 / 尾号整体 / 建议 / 卡片
# ============================================================

def test_parse_num_params_structured_keys():
    """结构化键形态：半/全角冒号、等号、键序无关、context 可省（按位数识别）。"""
    assert no.parse_num_params(
        "number: 13812345678\ncontext: 手机号"
    ) == {"number": "13812345678", "context": "手机号"}
    assert no.parse_num_params(
        "context＝车牌\nnumber: 88888"
    ) == {"number": "88888", "context": "车牌"}
    assert no.parse_num_params("number: 8楼") == {
        "number": "8", "context": "楼层"}
    assert no.parse_num_params("number: 404") == {"number": "404", "context": "门牌/楼层"}
    assert no.parse_num_params("number: 13812345678") == {
        "number": "13812345678", "context": "手机号"}


def test_parse_num_params_text_fallback_and_tolerance():
    """文本标签兜底 + 解析容错：横线/空格/全角数字剔除、场景关键词识别。"""
    # 手机号（含横线、空格分隔）
    assert no.parse_num_params("帮我看看手机号 138-1234-5678 吉不吉") == {
        "number": "13812345678", "context": "手机号"}
    assert no.parse_num_params("１３８１２３４５６７８ 这手机号咋样") == {
        "number": "13812345678", "context": "手机号"}
    # 车牌（关键词）
    assert no.parse_num_params("京A88888 这个车牌好不好") == {
        "number": "88888", "context": "车牌"}
    # 楼层 / 门牌
    assert no.parse_num_params("8楼") == {"number": "8", "context": "楼层"}
    assert no.parse_num_params("住 3号楼501室 这号码咋样") == {
        "number": "3501", "context": "门牌"}
    # 无关键词按位数兜底
    assert no.parse_num_params("12345") == {"number": "12345", "context": "车牌"}
    assert no.parse_num_params("404") == {"number": "404", "context": "门牌/楼层"}
    assert no.parse_num_params("12345678") == {"number": "12345678", "context": "其他号码"}


def test_parse_num_params_unparseable():
    """拆不出数字 → None（不误判）。"""
    assert no.parse_num_params("") is None
    assert no.parse_num_params(None) is None
    assert no.parse_num_params("你好呀") is None
    assert no.parse_num_params("abc###") is None


def test_detect_context_keyword_precedence():
    """关键词优先于位数；门牌关键词先于「楼」（3号楼501室 归门牌不归楼层）。"""
    assert no.detect_context("手机号码 13812345678", "13812345678") == "手机号"
    assert no.detect_context("车牌号 88888", "88888") == "车牌"
    assert no.detect_context("3号楼501室", "3501") == "门牌"
    assert no.detect_context("8楼", "8") == "楼层"
    assert no.detect_context("", "13812345678") == "手机号"   # 无关键词按位数
    assert no.detect_context("", "88888") == "车牌"
    assert no.detect_context("", "8") == "门牌/楼层"
    assert no.detect_context("", "12345678") == "其他号码"


def test_reduce_to_81():
    """数理归约：n % 81，余 0 取 81（1-81 全表可及；<81 即原值）。"""
    assert no._reduce_to_81(48) == 48
    assert no._reduce_to_81(88) == 7        # 尾号 88 → 数理 7
    assert no._reduce_to_81(99) == 18       # 尾号 99 → 数理 18
    assert no._reduce_to_81(81) == 81
    assert no._reduce_to_81(162) == 81      # 81 的倍数余 0 → 81
    assert no._reduce_to_81(0) == 81        # 尾号 00 → 数理 81


def test_great_auspicious_from_table():
    """大吉数理号从 NUMEROLOGY_81 程序化推导（与表同源，共 36 个）。"""
    great = no.great_auspicious_numbers()
    expected = sorted(n for n, (ji, _t, _m) in NUMEROLOGY_81.items() if ji == "大吉")
    assert great == expected == [1, 3, 5, 6, 7, 8, 11, 13, 15, 16, 17, 18,
                                 21, 23, 24, 25, 29, 31, 32, 33, 35, 37, 39,
                                 41, 45, 47, 48, 52, 57, 61, 63, 65, 67, 68,
                                 73, 81]
    assert len(great) == 36
    assert 1 in great and 8 in great and 48 in great and 81 in great
    assert 2 not in great and 4 not in great and 9 not in great and 66 not in great


def test_analyze_phone():
    """手机号 13812345678：尾号 78 → 数理 78 半吉；数字和 48 → 数理 48 大吉。"""
    r = no.analyze_number("13812345678", "手机号")
    assert r["tail_digits"] == "78" and r["tail_num"] == 78
    assert r["tail_ji"] == "半吉" and r["tail_title"] == "晚境凄凉"
    assert r["tail_wuxing"] == "7金 8金"          # DIGIT_TO_WUXING 同源复用
    assert r["digit_sum"] == 48 and r["overall_num"] == 48
    assert r["overall_ji"] == "大吉" and r["overall_title"] == "青松立鹤"
    assert r["bad_chain4"] is None and r["repeat_warn"] is None


def test_analyze_floor_and_tail():
    """楼层 8 → 数理 8 大吉（努力发达）；楼层 4 → 数理 4 凶（坎坷不平）。"""
    r8 = no.analyze_number("8")
    assert r8["tail_digits"] == "8" and r8["tail_num"] == 8
    assert r8["tail_ji"] == "大吉" and r8["overall_ji"] == "大吉"
    assert r8["tail_meaning"] == "努力发达，把握良机。性格勤奋，志向远大，终有所成。"
    r4 = no.analyze_number("4")
    assert r4["tail_num"] == 4 and r4["tail_ji"] == "凶"
    assert r4["overall_ji"] == "凶" and r4["tail_title"] == "坎坷不平"


def test_analyze_plate_tail88():
    """车牌 88888：尾号 88 → 88%81=7 大吉（精神旺盛）；数字和 40 → 数理 40 半吉。"""
    r = no.analyze_number("88888", "车牌")
    assert r["tail_digits"] == "88" and r["tail_num"] == 7
    assert r["tail_ji"] == "大吉" and r["tail_title"] == "精神旺盛"
    assert r["digit_sum"] == 40 and r["overall_num"] == 40
    assert r["overall_ji"] == "半吉" and r["overall_title"] == "豪胆迈进"


def test_analyze_bad_chain_and_repeat():
    """4 连（连续 ≥3 个 4）→ bad_chain4；其他数字 4 连以上 → repeat_warn。"""
    r4 = no.analyze_number("44444")
    assert r4["bad_chain4"] == "44444"
    assert r4["tail_num"] == 44 and r4["tail_ji"] == "凶"
    assert r4["overall_num"] == 20 and r4["overall_ji"] == "凶"
    r8 = no.analyze_number("18888888")
    assert r8["bad_chain4"] is None and r8["repeat_warn"] == "8888888"
    r_ok = no.analyze_number("12345678")
    assert r_ok["bad_chain4"] is None and r_ok["repeat_warn"] is None
    # 尾号 00 → 0 % 81 余 0 → 数理 81（万物回春）
    r00 = no.analyze_number("100")
    assert r00["tail_digits"] == "00" and r00["tail_num"] == 81
    assert r00["tail_ji"] == "大吉"


def test_improvement_suggestions():
    """改善建议三态：均吉保持 / 4 连+双凶换号对冲 / 尾号凶列大吉数理。"""
    tips_ok = no.improvement_suggestions(no.analyze_number("13812345678"))
    assert len(tips_ok) == 1 and "均吉" in tips_ok[0]
    tips_bad = no.improvement_suggestions(no.analyze_number("44444"))
    assert any("避开「4 连」" in t for t in tips_bad)
    assert any("尾号「44」数理为凶" in t and "大吉数理" in t for t in tips_bad)
    assert any("整体数理为凶" in t for t in tips_bad)
    # 尾号大吉（88→数理7）且整体大吉 → 无换号类建议
    tips_tail = no.improvement_suggestions(no.analyze_number("18888"))
    assert not any("数理为凶" in t for t in tips_tail)
    # 尾号大吉（45→数理45）但整体凶（数字和 9→数理9）→ 出整体对冲建议
    tips_ov = no.improvement_suggestions(no.analyze_number("45"))
    assert any("整体数理为凶" in t for t in tips_ov)
    assert not any("尾号「45」数理为凶" in t for t in tips_ov)


def test_format_num_card_sections():
    """结果卡片六要素齐：场景/号码 / 尾号数理（吉凶+运势+含义+五行）/
    整体数理 / 吉凶总评 / 改善建议 / 口径说明。"""
    card = no.format_num_card(no.analyze_number("13812345678"), "13812345678", "手机号")
    for section in ("【数字吉凶】场景：手机号｜号码：13812345678",
                    "尾号 78 → 数理 78（半吉·晚境凄凉）",
                    "尾号五行：7金 8金",
                    "整体数理：数字和 48 → 数理 48（大吉·青松立鹤）",
                    "吉凶总评：整体大吉、尾号半吉",
                    "改善建议：",
                    "说明：81 数理与起名工具同源"):
        assert section in card, f"卡片缺: {section}\n{card}"
    card_bad = no.format_num_card(no.analyze_number("4"), "4", "楼层")
    assert "尾号 4 → 数理 4（凶·坎坷不平）" in card_bad
    assert "吉凶总评：整体凶、尾号凶" in card_bad
    assert "数理吉凶与谐音联想口径不同" in card_bad


# ============================================================
# e2e（handler 执行器 + 工具循环链路）
# ============================================================

def _make_bot():
    """轻量 MessageHandler（__new__ 避开 __init__ 装配）；数字吉凶纯查表，
    不依赖引擎（engine 留 None 亦可）。"""
    from src.bot.handler import MessageHandler
    bot = MessageHandler.__new__(MessageHandler)
    bot.engine = None
    return bot


def test_num_execute_tool_call_e2e():
    """e2e：JSON 工单两键 → 校验 → 序列化 → 查表出卡片。"""
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

    bot = _make_bot()
    cap = CAPABILITY_BY_NAME["数字吉凶"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("num_omen")
    try:
        bind_executors({"num_omen": lambda p, user_id="", user_question="":
                        bot._tool_num_omen(p, user_id)}, {})
        r = bot._execute_tool_call("数字吉凶", {
            "number": "13812345678",
            "context": "手机号",
        }, "u1")
        assert r.ok is True
        assert "【数字吉凶】场景：手机号｜号码：13812345678" in r.text
        assert "尾号 78 → 数理 78（半吉·晚境凄凉）" in r.text
        assert "整体数理：数字和 48 → 数理 48（大吉·青松立鹤）" in r.text
        assert "吉凶总评：整体大吉、尾号半吉" in r.text
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("num_omen", None)
        else:
            reg._tool_executors["num_omen"] = orig_ex


def test_num_plate_text_e2e():
    """e2e：纯文本 车牌（无 context 键）→ 文本兜底解析 + 位数识别。"""
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

    bot = _make_bot()
    cap = CAPABILITY_BY_NAME["数字吉凶"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("num_omen")
    try:
        bind_executors({"num_omen": lambda p, user_id="", user_question="":
                        bot._tool_num_omen(p, user_id)}, {})
        r = bot._execute_tool_call("数字吉凶", "京A88888 这个车牌好不好", "u1")
        assert r.ok is True
        assert "场景：车牌｜号码：88888" in r.text
        assert "尾号 88 → 数理 7（大吉·精神旺盛）" in r.text
        assert "整体数理：数字和 40 → 数理 40（半吉·豪胆迈进）" in r.text
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("num_omen", None)
        else:
            reg._tool_executors["num_omen"] = orig_ex


def test_num_needs_info_paths():
    """信息不全/非法 → needs_info 澄清，不算成功；结构化工单缺 number 被框架拦截。"""
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

    bot = _make_bot()
    cap = CAPABILITY_BY_NAME["数字吉凶"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("num_omen")
    try:
        bind_executors({"num_omen": lambda p, user_id="", user_question="":
                        bot._tool_num_omen(p, user_id)}, {})
        # 无数字 → 追问（含 number 键名示例）
        r0 = bot._execute_tool_call("数字吉凶", "看看我的号码吉不吉", "u1")
        assert r0.ok is False and r0.needs_info is True
        assert "number" in r0.text and "13812345678" in r0.text
        # 数字串过长（21 位）→ 拒绝
        r1 = bot._execute_tool_call("数字吉凶", {"number": "123456789012345678901"}, "u1")
        assert r1.ok is False and r1.needs_info is True
        assert "过长" in r1.text and "21 位" in r1.text
        # 结构化工单缺 number 键 → 框架校验拦截（参数不合法，不执行）
        r2 = bot._execute_tool_call("数字吉凶", {"context": "手机号"}, "u1")
        assert r2.ok is False and "参数不合法" in r2.text and "number" in r2.text
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("num_omen", None)
        else:
            reg._tool_executors["num_omen"] = orig_ex


def test_num_chain_reachability_json_workorder():
    """工具循环链路可达（D5 同型回归）：reply 内 JSON 工单 num_omen →
    真实执行 → 结果注入 LLM 消息 → 最终文本回复。"""
    from unittest.mock import Mock, patch
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

    bot = _make_bot()
    llm = Mock()
    llm.api_key = "test-key"
    llm.model = "deepseek-flash"
    llm.provider = "deepseek"
    bot.llm = llm
    bot.session_dao = None
    bot._downgraded = {}
    bot._tool_logs = {}
    bot._citations = {}
    bot._analysis_facts = {}

    cap = CAPABILITY_BY_NAME["数字吉凶"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("num_omen")
    seen_msgs: list = []

    def fake_messages(api_key, messages, model=None, max_tokens=0,
                      temperature=0.0, timeout=0.0, tools=None,
                      tool_choice=None, **kwargs):
        seen_msgs.append(messages)
        return {"stop_reason": "end_turn",
                "content": [{"type": "text", "text": "你这个尾号 78 数理为半吉，整体 48 大吉，还不错。"}]}

    try:
        bind_executors({"num_omen": lambda p, user_id="", user_question="":
                        bot._tool_num_omen(p, user_id)}, {})
        with patch("src.llm.client.deepseek_anthropic_messages",
                   side_effect=fake_messages):
            out = bot._run_tool_loop(
                "13812345678 这手机号咋样", "u1",
                '好的，我来看看。<tool_calls>'
                '[{"tool": "num_omen", "params": {"number": "13812345678", '
                '"context": "手机号"}}]</tool_calls>')
        assert out == "你这个尾号 78 数理为半吉，整体 48 大吉，还不错。"
        tail = seen_msgs[0][-1]["content"]
        assert '"tool": "数字吉凶"' in tail and '"ok": true' in tail
        assert "数理 48（大吉·青松立鹤）" in tail and "改善建议" in tail
        assert bot._tool_logs["u1"]["calls"][0]["type"] == "数字吉凶"
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("num_omen", None)
        else:
            reg._tool_executors["num_omen"] = orig_ex


def test_num_native_tool_use_loop():
    """原生 tool_use 链路：JSON 工单先执行 → 首轮 LLM 出同参 num_omen 原生块
    → 去重跳过执行、复用结果回传 tool_result。"""
    from unittest.mock import Mock, patch
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

    bot = _make_bot()
    llm = Mock()
    llm.api_key = "test-key"
    llm.model = "deepseek-flash"
    llm.provider = "deepseek"
    bot.llm = llm
    bot.session_dao = None
    bot._downgraded = {}
    bot._tool_logs = {}
    bot._citations = {}
    bot._analysis_facts = {}

    cap = CAPABILITY_BY_NAME["数字吉凶"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("num_omen")
    seen_round2: list = []
    called: list = []

    def fake_messages(api_key, messages, model=None, max_tokens=0,
                      temperature=0.0, timeout=0.0, tools=None,
                      tool_choice=None, **kwargs):
        if not hasattr(fake_messages, "turn"):
            fake_messages.turn = 0
        fake_messages.turn += 1
        if fake_messages.turn == 1:
            assert tools and any(t["name"] == "num_omen" for t in tools)
            return {"stop_reason": "tool_use",
                    "content": [{"type": "tool_use", "id": "tu_n1",
                                 "name": "num_omen",
                                 "input": {"number": "88888", "context": "车牌"}}]}
        seen_round2.append(messages[-1])
        return {"stop_reason": "end_turn",
                "content": [{"type": "text", "text": "尾号 88 数理 7 大吉，车牌不错。"}]}

    def spy(params, user_id="", user_question=""):
        called.append(params)
        return bot._tool_num_omen(params, user_id)

    try:
        bind_executors({"num_omen": spy}, {})
        with patch("src.llm.client.deepseek_anthropic_messages",
                   side_effect=fake_messages):
            out = bot._run_tool_loop(
                "88888 这个车牌咋样", "u1",
                '好的，我来看看。<tool_calls>'
                '[{"tool": "num_omen", "params": {"number": "88888", "context": "车牌"}}]'
                '</tool_calls>')
        assert out == "尾号 88 数理 7 大吉，车牌不错。"
        assert fake_messages.turn == 2
        # 同参原生块被去重跳过（review I-1）：仅工单执行 1 次
        assert len(called) == 1
        last = seen_round2[0]
        assert last["role"] == "user"
        results = last["content"]
        assert [r["type"] for r in results] == ["tool_result"]
        assert results[0]["tool_use_id"] == "tu_n1"
        assert "数理 7（大吉·精神旺盛）" in results[0]["content"]
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("num_omen", None)
        else:
            reg._tool_executors["num_omen"] = orig_ex
