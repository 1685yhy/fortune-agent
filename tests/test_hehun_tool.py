"""批次 2 E1：合婚工具（cap_id: hehun）——参数校验 + 规则逻辑 + e2e 链路。

规则依据（与 src/engines/hehun.py 既有表同源，本测试锁定行为）：
- 生肖六冲：子午、丑未、寅申、卯酉、辰戌、巳亥（最忌）
- 生肖三合：申子辰、亥卯未、寅午戌、巳酉丑（上等）
- 生肖六合：子丑、寅亥、卯戌、辰酉、巳申、午未（上等）
- 五行生克（日主判定口径）：木生火、火生土、土生金、金生水、水生木；
  木克土、土克水、水克火、火克金、金克木；日主同五行 = 比和
- 评级映射（对齐引擎 _generate_advice 档位阈值 65/50/35）：
  ≥65 上等婚配 / 35~64 中等婚配 / <35 普通婚配
"""
import sys

sys.path.insert(0, "src")

from src.bot import capability_registry as reg  # noqa: E402
from src.engines.bazi import BaziEngine, BaziResult  # noqa: E402
from src.engines.hehun import HehunEngine  # noqa: E402
from src.tools import hehun as hehun_tools  # noqa: E402


# ============================================================
# 参数校验（注册表 schema / 说明书一致性）
# ============================================================

def test_hehun_schema_required_keys():
    """schema 必填键恰为 birth_a/birth_b，properties 双键齐全。"""
    schema = reg._TOOL_PARAMS_SCHEMAS["hehun"]
    assert schema["required"] == ["birth_a", "birth_b"]
    for key in ("birth_a", "birth_b"):
        assert key in schema["properties"]
        assert schema["properties"][key]["type"] == "string"


def test_validate_params_hehun():
    """参数校验：双键齐 → None；缺任一键 / 空 / 非字符串 → 错误串含键名。"""
    ok = reg.validate_params("hehun", {
        "birth_a": "1990年5月20日 午时 北京 男",
        "birth_b": "1992年8月15日 巳时 上海 女",
    })
    assert ok is None
    err = reg.validate_params("hehun", {"birth_a": "只有一方"})
    assert err is not None and "birth_b" in err
    err2 = reg.validate_params("hehun", {})
    assert err2 is not None and "birth_a" in err2
    err3 = reg.validate_params("hehun", {"birth_a": 1, "birth_b": "x"})
    assert err3 is not None


def test_hehun_schema_keys_in_description_and_requires():
    """批次 1 P1 #2 教训固化：工具描述/requires 里的参数键必须与 schema
    必填键完全一致（description/requires/示例三处都点名 birth_a/birth_b）。"""
    cap = reg.CAPABILITY_BY_ID["hehun"]
    assert cap.cap_type == "tool"
    assert "birth_a" in cap.description and "birth_b" in cap.description
    assert "birth_a" in cap.requires and "birth_b" in cap.requires
    desc = reg.build_tool_description()
    assert "合婚" in desc and "hehun" in desc
    assert "birth_a" in desc and "birth_b" in desc
    # 属性示例也含各自键名（LLM 按示例填参能过校验）
    props = cap.params_schema["properties"]
    assert "birth_a" in props["birth_a"]["description"]
    assert "birth_b" in props["birth_b"]["description"]


def test_hehun_in_registry_projection():
    """TOOL_REGISTRY / build_tool_schema_list / TOOL_NAME_BY_ID 自动投影含合婚。"""
    from src.bot.tool_calls import TOOL_REGISTRY, TOOL_NAME_BY_ID
    assert "合婚" in TOOL_REGISTRY
    assert TOOL_REGISTRY["合婚"]["key"] == "hehun"
    assert TOOL_NAME_BY_ID["hehun"] == "合婚"
    schemas = {s["name"]: s for s in reg.build_tool_schema_list()}
    assert "hehun" in schemas
    assert schemas["hehun"]["input_schema"] == cap_params_schema("hehun")


def cap_params_schema(cap_id):
    return reg.CAPABILITY_BY_ID[cap_id].params_schema


def test_hehun_workorder_and_native_parse():
    """JSON 工单 / 原生 tool_use：英文 cap_id hehun 归一为中文名 合婚 并带双键。"""
    from src.bot.tool_calls import parse_native_tool_use_blocks, parse_tool_calls
    calls = parse_tool_calls(
        '<tool_calls>[{"tool": "hehun", "params": {"birth_a": "A方", '
        '"birth_b": "B方"}}]</tool_calls>')
    assert [(c.name, c.params_obj) for c in calls] == [
        ("合婚", {"birth_a": "A方", "birth_b": "B方"})]
    blocks = parse_native_tool_use_blocks([{
        "type": "tool_use", "id": "tu_h", "name": "hehun",
        "input": {"birth_a": "A方", "birth_b": "B方"}}])
    assert blocks[0].name == "合婚"
    assert blocks[0].params_obj == {"birth_a": "A方", "birth_b": "B方"}


# ============================================================
# 规则逻辑：参数拆分 / 评级 / 冲合表 / 五行口径
# ============================================================

def test_split_birth_pair_structured_keys():
    """结构化键形态：半/全角冒号、等号、键序无关。"""
    assert hehun_tools.split_birth_pair(
        "birth_a: 1990年5月20日 午时 北京 男\nbirth_b: 1992年8月15日 巳时 上海 女"
    ) == ("1990年5月20日 午时 北京 男", "1992年8月15日 巳时 上海 女")
    assert hehun_tools.split_birth_pair(
        "birth_b＝1992年8月15日 巳时 上海 女\nbirth_a=1990年5月20日 午时 北京 男"
    ) == ("1990年5月20日 午时 北京 男", "1992年8月15日 巳时 上海 女")
    # 结构化键只给一方 → None（缺键由执行器追问）
    assert hehun_tools.split_birth_pair("birth_a: 只有一方") is None


def test_split_birth_pair_delimiter_form():
    """文本标签兜底：男…女…、男…，女方…、女方在前、对方/对象标记。"""
    assert hehun_tools.split_birth_pair(
        "男1990年5月20日 午时 北京 女1992年8月15日 巳时 上海"
    ) == ("男1990年5月20日 午时 北京", "1992年8月15日 巳时 上海")
    assert hehun_tools.split_birth_pair(
        "男1990年5月20日 午时 北京，女方1992年8月15日 巳时 上海"
    ) == ("男1990年5月20日 午时 北京", "1992年8月15日 巳时 上海")
    assert hehun_tools.split_birth_pair(
        "女1992年8月15日 巳时 上海 男1990年5月20日 午时 北京"
    ) == ("女1992年8月15日 巳时 上海", "1990年5月20日 午时 北京")
    assert hehun_tools.split_birth_pair(
        "1990年5月20日 午时 北京，对方1992年8月15日 巳时 上海"
    ) == ("1990年5月20日 午时 北京", "1992年8月15日 巳时 上海")


def test_split_birth_pair_unparseable():
    """拆不出两段 → None（不误拆）。"""
    assert hehun_tools.split_birth_pair("") is None
    assert hehun_tools.split_birth_pair("只有一个人的生日1990年5月20日") is None
    assert hehun_tools.split_birth_pair(None) is None


def test_grade_for_score_boundaries():
    """评级边界：65→上等，64/35→中等，34→普通。"""
    assert hehun_tools.grade_for_score(100) == "上等婚配"
    assert hehun_tools.grade_for_score(65) == "上等婚配"
    assert hehun_tools.grade_for_score(64) == "中等婚配"
    assert hehun_tools.grade_for_score(35) == "中等婚配"
    assert hehun_tools.grade_for_score(34) == "普通婚配"
    assert hehun_tools.grade_for_score(0) == "普通婚配"


# ---- 冲合表 / 五行口径（构造 BaziResult 直测 HehunEngine，确定性不依赖日期） ----

_WX_BALANCED = {"金": 1, "木": 1, "水": 1, "火": 1, "土": 5}


def _mk(bazi_pillars, day_master, wuxing=None):
    """构造最小 BaziResult（bazi[0][1]=年支 生肖，bazi[2]=日柱）。"""
    return BaziResult(
        bazi=bazi_pillars, day_master=day_master,
        wuxing=wuxing or dict(_WX_BALANCED),
        shishen=[], dayun=[], liunian={}, geju="", yongshen="",
        shensha=[], nayin=[])


def test_shengxiao_liuchong_ziwu():
    """生肖六冲：子午（鼠马）→ 六冲（忌配）。"""
    h = HehunEngine().match(
        _mk(["甲子", "丙寅", "庚午", "壬午"], "庚金"),
        _mk(["庚午", "辛巳", "乙酉", "壬午"], "乙木"),
    )
    assert h.shengxiao_detail["relation"] == "六冲（忌配）"
    assert "六冲" in h.shengxiao


def test_shengxiao_sanhe():
    """生肖三合：申子（申子辰组）→ 三合（上等婚配）。"""
    h = HehunEngine().match(
        _mk(["甲申", "丙寅", "庚午", "壬午"], "庚金"),
        _mk(["丙子", "辛巳", "乙酉", "壬午"], "乙木"),
    )
    assert h.shengxiao_detail["relation"] == "三合（上等婚配）"


def test_shengxiao_liuhe():
    """生肖六合：子丑 → 六合（上等婚配，满 25 分）。"""
    h = HehunEngine().match(
        _mk(["甲子", "丙寅", "庚午", "壬午"], "庚金"),
        _mk(["乙丑", "辛巳", "乙酉", "壬午"], "乙木"),
    )
    assert h.shengxiao_detail["relation"] == "六合（上等婚配）"
    assert h.shengxiao_score == 25


def test_daymaster_sheng_ke_bihe():
    """日主生克口径：木生火 → 相生（吉）；木木 → 比和（中）；木克土 → 相克（凶）。"""
    engine = HehunEngine()
    sheng = engine.match(
        _mk(["甲子", "丙寅", "庚午", "壬午"], "甲木"),
        _mk(["乙丑", "辛巳", "乙酉", "壬午"], "丙火"),
    )
    assert sheng.bazi_match["day_master_relation"] == "相生（吉）"
    bihe = engine.match(
        _mk(["甲子", "丙寅", "庚午", "壬午"], "甲木"),
        _mk(["乙丑", "辛巳", "乙酉", "壬午"], "乙木"),
    )
    assert bihe.bazi_match["day_master_relation"] == "比和（中）"
    ke = engine.match(
        _mk(["甲子", "丙寅", "庚午", "壬午"], "甲木"),
        _mk(["乙丑", "辛巳", "乙酉", "壬午"], "戊土"),
    )
    assert ke.bazi_match["day_master_relation"] == "相克（凶）"


def test_wuxing_complement():
    """五行互补：一方弱项（≤1）在另一方 ≥2 → 计入互补度。"""
    r1 = _mk(["甲子", "丙寅", "庚午", "壬午"], "戊土",
             wuxing={"金": 0, "木": 1, "水": 1, "火": 2, "土": 4})
    r2 = _mk(["乙丑", "辛巳", "乙酉", "壬午"], "庚金",
             wuxing={"金": 3, "木": 2, "水": 1, "火": 1, "土": 1})
    h = HehunEngine().match(r1, r2)
    assert h.bazi_match["complement_count"] >= 2
    assert "互补" in h.bazi_match["complement_desc"]
    assert h.bazi_match["score_breakdown"]["互补得分"] >= 20


def test_format_hehun_card_sections():
    """结果卡片四要素齐：生肖冲合/五行互补/日主生克/总体评级+改善建议。"""
    h = HehunEngine().match(
        _mk(["甲子", "丙寅", "庚午", "壬午"], "甲木"),
        _mk(["乙丑", "辛巳", "乙酉", "壬午"], "丙火"),
    )
    card = hehun_tools.format_hehun_card(
        _mk(["甲子", "丙寅", "庚午", "壬午"], "甲木"),
        _mk(["乙丑", "辛巳", "乙酉", "壬午"], "丙火"), h)
    for section in ("【合婚】", "生肖配对", "五行互补", "日主生克",
                    "日柱关系", "综合评分", "改善建议"):
        assert section in card
    assert hehun_tools.grade_for_score(h.score) in card
    assert "六合" in card or "三合" in card or "六冲" in card  # 生肖关系有实义


# ============================================================
# e2e（handler 执行器 + 工具循环链路）
# ============================================================

def _make_bot():
    """轻量 MessageHandler（__new__ 避开 __init__ 装配），注入真实引擎。"""
    from src.bot.handler import MessageHandler
    bot = MessageHandler.__new__(MessageHandler)
    bot.engine = BaziEngine()
    bot.hehun_engine = HehunEngine()
    return bot


def test_hehun_execute_tool_call_e2e():
    """e2e：JSON 工单双键 → 校验 → 序列化 → 真实双排盘 + 合婚卡片。"""
    from src.bot.handler import MessageHandler
    from src.bot.tool_calls import ToolResult
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

    bot = _make_bot()
    cap = CAPABILITY_BY_NAME["合婚"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("hehun")
    try:
        bind_executors({"hehun": lambda p, user_id="", user_question="":
                        bot._tool_hehun(p, user_id)}, {})
        r = bot._execute_tool_call("合婚", {
            "birth_a": "1996年8月15日 巳时 上海 男",
            "birth_b": "1990年5月20日 午时 北京 女",
        }, "u1")
        assert r.ok is True
        # 1996 丙子（鼠）× 1990 庚午（马）→ 生肖六冲；卡片含四要素
        assert "生肖鼠与马" in r.text and "六冲" in r.text
        assert "五行互补" in r.text and "日主生克" in r.text
        assert "综合评分" in r.text and "改善建议" in r.text
        assert hehun_tools.grade_for_score(int(_extract_score(r.text))) in r.text
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("hehun", None)
        else:
            reg._tool_executors["hehun"] = orig_ex


def _extract_score(text: str) -> int:
    """从卡片提取综合评分（"综合评分：30/100"）。"""
    import re
    m = re.search(r"综合评分：(\d+)/100", text)
    assert m, f"卡片缺综合评分: {text[:200]}"
    return m.group(1)


def test_hehun_needs_info_paths():
    """信息不全 → needs_info 澄清，不调引擎不算成功。"""
    from src.bot.handler import MessageHandler
    from src.bot.tool_calls import ToolResult
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

    bot = _make_bot()
    cap = CAPABILITY_BY_NAME["合婚"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("hehun")
    try:
        bind_executors({"hehun": lambda p, user_id="", user_question="":
                        bot._tool_hehun(p, user_id)}, {})
        # 全无出生信息（纯文本标签路径）→ 追问双方（含 birth_a/birth_b 键名）
        r0 = bot._execute_tool_call("合婚", "我们俩的生日都忘了", "u1")
        assert r0.ok is False and r0.needs_info is True
        assert "birth_a" in r0.text and "birth_b" in r0.text
        # 双方都给了但都不可解析 → 点名第一方（birth_a）
        r1 = bot._execute_tool_call("合婚", {"birth_a": "x", "birth_b": "y"}, "u1")
        assert r1.ok is False and r1.needs_info is True
        assert "birth_a" in r1.text
        # 结构化工单缺 birth_b 键 → 框架校验拦截（参数不合法，不执行）
        r2 = bot._execute_tool_call("合婚", {
            "birth_a": "1996年8月15日 巳时 上海 男"}, "u1")
        assert r2.ok is False and "参数不合法" in r2.text and "birth_b" in r2.text
        # 文本标签路径只有一方（可解析）→ 点名缺另一方
        r2b = bot._execute_tool_call("合婚", "1996年8月15日 巳时 上海 男", "u1")
        assert r2b.ok is False and r2b.needs_info is True
        assert "另一方" in r2b.text
        # 第一方不可解析 → 点名哪一方
        r3 = bot._execute_tool_call("合婚", {
            "birth_a": "乱码描述", "birth_b": "1990年5月20日 午时 北京 女"}, "u1")
        assert r3.ok is False and r3.needs_info is True
        assert "birth_a" in r3.text
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("hehun", None)
        else:
            reg._tool_executors["hehun"] = orig_ex


def test_hehun_chain_reachability_json_workorder():
    """工具循环链路可达（D5 同型回归）：reply 内 JSON 工单 hehun →
    真实执行 → 结果注入 LLM 消息 → 最终文本回复。"""
    from unittest.mock import Mock, patch
    from src.bot.handler import MessageHandler
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

    cap = CAPABILITY_BY_NAME["合婚"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("hehun")
    seen_msgs: list = []

    def fake_messages(api_key, messages, model=None, max_tokens=0,
                      temperature=0.0, timeout=0.0, tools=None,
                      tool_choice=None, **kwargs):
        seen_msgs.append(messages)
        return {"stop_reason": "end_turn",
                "content": [{"type": "text", "text": "你们是鼠马六冲配对，需多磨合。"}]}

    try:
        bind_executors({"hehun": lambda p, user_id="", user_question="":
                        bot._tool_hehun(p, user_id)}, {})
        with patch("src.llm.client.deepseek_anthropic_messages",
                   side_effect=fake_messages):
            out = bot._run_tool_loop(
                "看看我们合不合", "u1",
                '好的，我来为你们合婚。<tool_calls>'
                '[{"tool": "hehun", "params": {"birth_a": "1996年8月15日 巳时 上海 男", '
                '"birth_b": "1990年5月20日 午时 北京 女"}}]</tool_calls>')
        assert out == "你们是鼠马六冲配对，需多磨合。"
        tail = seen_msgs[0][-1]["content"]
        assert '"tool": "合婚"' in tail and '"ok": true' in tail
        assert "生肖鼠与马" in tail and "六冲" in tail and "综合评分" in tail
        assert bot._tool_logs["u1"]["calls"][0]["type"] == "合婚"
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("hehun", None)
        else:
            reg._tool_executors["hehun"] = orig_ex


def test_hehun_native_tool_use_loop():
    """原生 tool_use 链路（与 web_search 端到端同型）：JSON 工单先执行 →
    首轮 LLM 出同参 hehun 原生块 → 去重跳过执行、复用结果回传 tool_result。"""
    from unittest.mock import Mock, patch
    from src.bot.handler import MessageHandler
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

    cap = CAPABILITY_BY_NAME["合婚"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("hehun")
    seen_round2: list = []
    called: list = []

    def fake_messages(api_key, messages, model=None, max_tokens=0,
                      temperature=0.0, timeout=0.0, tools=None,
                      tool_choice=None, **kwargs):
        if not hasattr(fake_messages, "turn"):
            fake_messages.turn = 0
        fake_messages.turn += 1
        if fake_messages.turn == 1:
            assert tools and any(t["name"] == "hehun" for t in tools)
            return {"stop_reason": "tool_use",
                    "content": [{"type": "tool_use", "id": "tu_h1",
                                 "name": "hehun",
                                 "input": {"birth_a": "1996年8月15日 巳时 上海 男",
                                           "birth_b": "1990年5月20日 午时 北京 女"}}]}
        seen_round2.append(messages[-1])
        return {"stop_reason": "end_turn",
                "content": [{"type": "text", "text": "综合来看需多磨合。"}]}

    def spy(params, user_id="", user_question=""):
        called.append(params)
        return bot._tool_hehun(params, user_id)

    try:
        bind_executors({"hehun": spy}, {})
        with patch("src.llm.client.deepseek_anthropic_messages",
                   side_effect=fake_messages):
            out = bot._run_tool_loop(
                "看看我们合不合", "u1",
                '好的，我来为你们合婚。<tool_calls>'
                '[{"tool": "hehun", "params": {"birth_a": "1996年8月15日 巳时 上海 男", '
                '"birth_b": "1990年5月20日 午时 北京 女"}}]</tool_calls>')
        assert out == "综合来看需多磨合。"
        assert fake_messages.turn == 2
        # 同参原生块被去重跳过（review I-1）：仅工单执行 1 次
        assert len(called) == 1
        last = seen_round2[0]
        assert last["role"] == "user"
        results = last["content"]
        assert [r["type"] for r in results] == ["tool_result"]
        assert results[0]["tool_use_id"] == "tu_h1"
        assert "生肖鼠与马" in results[0]["content"] and "六冲" in results[0]["content"]
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("hehun", None)
        else:
            reg._tool_executors["hehun"] = orig_ex
