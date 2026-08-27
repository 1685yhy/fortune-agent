"""批次 2 E2：起名建议工具（cap_id: naming）——参数校验 + 规则逻辑 + e2e 链路。

规则依据（字库/数理口径与既有引擎同源，本测试锁定行为）：
- 推荐字库：src/engines/ming.py CHAR_LIB —— 通用常用取名字库 796 字
  （人工标注五行/性别倾向/风格标签，五行均衡），本工具不另造字库
- 五格数理：src/engines/xingming.py 五格剖象法（天/人/地/外/总格）+
  81 数理吉凶表（大吉/半吉/凶三档）+ 三才配置（大吉/吉/半吉/凶四档）
- 五格评分 0-100 = 五格按 81 数理（大吉=2/半吉=1/凶=0，0-10 分）+ 三才
  （大吉=2/吉=1/半吉=0/凶=0，0-2 分），总分 0-12 → 0-100
- 补益五行：BaziEngine 排盘 wuxing 计数 → 缺（count=0）/弱（最少计数）
  五行；用神喜用优先（与 ming._score_wuxing 同口径）
- 无出生信息 → 降级：仅按五格数理均衡推荐（不筛五行）
"""
import sys

sys.path.insert(0, "src")

from src.bot import capability_registry as reg  # noqa: E402
from src.engines.bazi import BaziEngine  # noqa: E402
from src.engines.ming import CHAR_LIB, char_wuxing  # noqa: E402
from src.engines.xingming import XingmingResult  # noqa: E402
from src.tools import naming as naming_tools  # noqa: E402

_GRIDS = ["天格", "人格", "地格", "外格", "总格"]


# ============================================================
# 参数校验（注册表 schema / 说明书一致性）
# ============================================================

def test_naming_schema_required_keys():
    """schema 必填键恰为 surname/gender；birth 为可选键。"""
    schema = reg._TOOL_PARAMS_SCHEMAS["naming"]
    assert schema["required"] == ["surname", "gender"]
    for key in ("surname", "gender", "birth"):
        assert key in schema["properties"]
        assert schema["properties"][key]["type"] == "string"


def test_validate_params_naming():
    """参数校验：双键齐（含/不含 birth）→ None；缺键/空/类型错 → 错误串含键名。"""
    ok = reg.validate_params("naming", {"surname": "张", "gender": "男"})
    assert ok is None
    ok2 = reg.validate_params("naming", {
        "surname": "张", "gender": "男", "birth": "2019年3月15日 午时 北京"})
    assert ok2 is None
    err = reg.validate_params("naming", {"surname": "张"})
    assert err is not None and "gender" in err
    err2 = reg.validate_params("naming", {"gender": "女"})
    assert err2 is not None and "surname" in err2
    err3 = reg.validate_params("naming", {})
    assert err3 is not None and "surname" in err3
    err4 = reg.validate_params("naming", {"surname": 1, "gender": "男"})
    assert err4 is not None


def test_naming_schema_keys_in_description_and_requires():
    """批次 1 P1 #2 教训固化：工具描述/requires 里的参数键必须与 schema
    必填/可选键完全一致（description/requires/示例三处都点名 surname/gender/birth）。"""
    cap = reg.CAPABILITY_BY_ID["naming"]
    assert cap.cap_type == "tool"
    assert "surname" in cap.description and "gender" in cap.description
    assert "birth" in cap.description
    assert "surname" in cap.requires and "gender" in cap.requires
    assert "birth" in cap.requires
    desc = reg.build_tool_description()
    assert "起名" in desc and "naming" in desc
    assert "surname" in desc and "gender" in desc
    # 属性示例也含各自键名（LLM 按示例填参能过校验）
    props = cap.params_schema["properties"]
    assert "surname" in props["surname"]["description"]
    assert "gender" in props["gender"]["description"]
    assert "birth" in props["birth"]["description"]


def test_naming_in_registry_projection():
    """TOOL_REGISTRY / build_tool_schema_list / TOOL_NAME_BY_ID 自动投影含起名。"""
    from src.bot.tool_calls import TOOL_REGISTRY, TOOL_NAME_BY_ID
    assert "起名" in TOOL_REGISTRY
    assert TOOL_REGISTRY["起名"]["key"] == "naming"
    assert TOOL_NAME_BY_ID["naming"] == "起名"
    schemas = {s["name"]: s for s in reg.build_tool_schema_list()}
    assert "naming" in schemas
    assert schemas["naming"]["input_schema"] == reg.CAPABILITY_BY_ID["naming"].params_schema


def test_naming_workorder_and_native_parse():
    """JSON 工单 / 原生 tool_use：英文 cap_id naming 归一为中文名 起名 并带三键。"""
    from src.bot.tool_calls import parse_native_tool_use_blocks, parse_tool_calls
    calls = parse_tool_calls(
        '<tool_calls>[{"tool": "naming", "params": {"surname": "张", "gender": "男", '
        '"birth": "2019年3月15日 午时 北京"}}]</tool_calls>')
    assert [(c.name, c.params_obj) for c in calls] == [
        ("起名", {"surname": "张", "gender": "男", "birth": "2019年3月15日 午时 北京"})]
    blocks = parse_native_tool_use_blocks([{
        "type": "tool_use", "id": "tu_n", "name": "naming",
        "input": {"surname": "张", "gender": "女"}}])
    assert blocks[0].name == "起名"
    assert blocks[0].params_obj == {"surname": "张", "gender": "女"}


# ============================================================
# 规则逻辑：参数拆分 / 补益五行 / 字库池 / 五格评分 / 候选生成 / 卡片
# ============================================================

def test_split_naming_params_structured_keys():
    """结构化键形态：半/全角冒号、等号、键序无关、birth 可省。"""
    assert naming_tools.split_naming_params(
        "surname: 张\ngender: 男\nbirth: 2019年3月15日 午时 北京"
    ) == {"surname": "张", "gender": "男", "birth": "2019年3月15日 午时 北京"}
    assert naming_tools.split_naming_params(
        "gender＝女\nsurname: 李"
    ) == {"gender": "女", "surname": "李"}
    # 只有 birth（缺姓氏/性别）→ 部分字典，由执行器点名缺项
    assert naming_tools.split_naming_params(
        "birth: 2019年3月15日 午时 北京") == {"birth": "2019年3月15日 午时 北京"}


def test_split_naming_params_text_fallback():
    """文本标签兜底：姓X / X姓 / 姓氏:X + 男孩/女孩/男/女 判定。"""
    assert naming_tools.split_naming_params(
        "姓张，男孩，2019年3月15日 午时出生"
    ) == {"surname": "张", "gender": "男", "birth": "男孩，2019年3月15日 午时出生"}
    assert naming_tools.split_naming_params("张姓女宝宝") == {"surname": "张", "gender": "女"}
    assert naming_tools.split_naming_params("我姓张") == {"surname": "张"}
    assert naming_tools.split_naming_params(
        "张姓男孩2019年3月15日午时生"
    ) == {"surname": "张", "gender": "男", "birth": "男孩2019年3月15日午时生"}
    assert naming_tools.split_naming_params(
        "姓氏：欧阳，女孩") == {"surname": "欧阳", "gender": "女"}
    # 姓王但剩余文本无日期特征 → 不当出生信息
    assert naming_tools.split_naming_params(
        "帮女儿起名，姓王") == {"surname": "王", "gender": "女"}


def test_split_naming_params_unparseable():
    """拆不出姓氏/性别 → None（不误拆）。"""
    assert naming_tools.split_naming_params("") is None
    assert naming_tools.split_naming_params(None) is None
    assert naming_tools.split_naming_params("随便聊聊") is None
    assert naming_tools.split_naming_params("起个名字") is None


def test_target_elements_missing():
    """命局缺五行（count=0）→ 补益集合为该五行。"""
    elements, desc = naming_tools.target_elements(
        {"金": 0, "木": 2, "水": 2, "火": 2, "土": 1})
    assert elements == ["金"]
    assert "缺「金」" in desc


def test_target_elements_weak():
    """无缺五行 → 弱（最少计数）五行；并列弱 → 全列。"""
    elements, desc = naming_tools.target_elements(
        {"金": 2, "木": 1, "水": 2, "火": 2, "土": 2})
    assert elements == ["木"] and "偏弱" in desc
    elements2, desc2 = naming_tools.target_elements(
        {"金": 2, "木": 1, "水": 1, "火": 2, "土": 2})
    assert elements2 == ["木", "水"]


def test_target_elements_balanced():
    """五行完全均衡 → 全五行均衡补益。"""
    elements, desc = naming_tools.target_elements(
        {"金": 2, "木": 2, "水": 2, "火": 2, "土": 2})
    assert elements == naming_tools.WX_ORDER
    assert "均衡" in desc


def test_target_elements_yongshen_priority():
    """用神喜用优先（与 ming._score_wuxing 同口径）；缺五行并入补益集合。"""
    counts = {"金": 0, "木": 2, "水": 2, "火": 2, "土": 1}
    elements, desc = naming_tools.target_elements(counts, "土为用神（喜土、金）")
    assert elements == ["土", "金"]
    assert "用神喜「土金」" in desc
    elements2, desc2 = naming_tools.target_elements(counts, "水为用神（喜水、木）")
    assert elements2 == ["水", "木", "金"]  # 缺「金」并入
    assert "缺「金」" in desc2


def test_build_pool_gender_and_element():
    """字库池过滤：性别倾向 + 五行命中 + 笔画已知 + 排除负面字。"""
    pool_m_water = naming_tools.build_pool("男", ["水"])
    assert len(pool_m_water) >= 20
    for ch in pool_m_water:
        assert char_wuxing(ch) == "水"
        assert CHAR_LIB[ch]["g"] in ("m", "b")
    pool_f = naming_tools.build_pool("女", None)
    assert len(pool_f) > 100
    for ch in pool_f:
        assert CHAR_LIB[ch]["g"] in ("f", "b")
    assert pool_m_water == sorted(pool_m_water)  # 确定性排序
    assert naming_tools.build_pool("男", []) == []  # 空元素集 → 空池


def test_wuge_score_boundaries():
    """五格评分口径：全大吉+三才大吉 → 100；全凶 → 0；半吉组合按公式。"""
    def _mk(sancai_ji="大吉", grades=None):
        g = grades or {k: "大吉" for k in _GRIDS}
        return XingmingResult(
            wuge={k: 1 for k in _GRIDS}, sancai="木木木", sancai_ji=sancai_ji,
            stroke_counts={}, analysis={k: {"吉凶": g[k]} for k in _GRIDS},
            overall="", wuxing={})

    assert naming_tools.wuge_score(_mk()) == 100                      # (10+2)/12
    assert naming_tools.wuge_score(_mk("凶", {k: "凶" for k in _GRIDS})) == 0
    assert naming_tools.wuge_score(
        _mk("半吉", {k: "半吉" for k in _GRIDS})) == round(5 * 100 / 12)  # 42
    mixed = {"天格": "大吉", "人格": "大吉", "地格": "大吉",
             "外格": "半吉", "总格": "凶"}
    assert naming_tools.wuge_score(_mk("吉", mixed)) == round(8 * 100 / 12)  # 67


def test_generate_candidates_deterministic():
    """候选生成：确定性（同参同结果）、limit 个、全部命中补益五行、评分降序。"""
    c1 = naming_tools.generate_candidates("张", "男", ["水"], limit=5)
    c2 = naming_tools.generate_candidates("张", "男", ["水"], limit=5)
    assert c1 == c2  # 确定性：无随机
    assert len(c1) == 5
    for cand in c1:
        for ch in cand["given"]:
            assert char_wuxing(ch) == "水"
        assert 0 <= cand["score"] <= 100
        assert "五格" in cand["wuge_line"] or "三才" in cand["wuge_line"]
        assert "｜" in cand["meaning"]
    scores = [c["score"] for c in c1]
    assert scores == sorted(scores, reverse=True)
    # 首字不重复（避免清一色同首字）
    assert len({c["given"][0] for c in c1}) == 5
    # 空元素集 → 无候选
    assert naming_tools.generate_candidates("张", "男", []) == []


def test_generate_candidates_degraded_no_elements():
    """降级路径（elements=None）：全性别池、双字名、评分正常。"""
    cands = naming_tools.generate_candidates("张", "女", None, limit=5)
    assert len(cands) == 5
    for cand in cands:
        assert len(cand["given"]) == 2
        assert 0 <= cand["score"] <= 100


def test_format_naming_card_sections():
    """结果卡片要素齐：补益五行 / 候选名（字+五格评分+寓意）/ 字库说明。"""
    cands = naming_tools.generate_candidates("张", "男", ["水", "木"], limit=3)
    card = naming_tools.format_naming_card("张", "男", "用神喜「水木」", cands)
    assert "【起名建议】" in card and "姓氏：张" in card and "性别：男" in card
    assert "补益五行：用神喜「水木」" in card
    for idx, cand in enumerate(cands, 1):
        assert f"{idx}. 张{cand['given']} —— 五格评分 {cand['score']}/100" in card
        assert cand["wuge_line"] in card
        assert cand["meaning"] in card
    assert "通用常用字库" in card


def test_format_naming_card_degraded_no_birth():
    """降级卡片：无出生信息 → 均衡推荐文案（不涉及八字补益）。"""
    cands = naming_tools.generate_candidates("张", "女", None, limit=2)
    card = naming_tools.format_naming_card("张", "女", "", cands)
    assert "未提供出生信息" in card and "均衡推荐" in card
    assert "不涉及八字补益" in card
    assert "1. 张" in card and "五格评分" in card


# ============================================================
# e2e（handler 执行器 + 工具循环链路）
# ============================================================

def _make_bot():
    """轻量 MessageHandler（__new__ 避开 __init__ 装配），注入真实 BaziEngine。"""
    from src.bot.handler import MessageHandler
    bot = MessageHandler.__new__(MessageHandler)
    bot.engine = BaziEngine()
    return bot


def _bind_naming(bot, executor=None):
    """临时绑定 naming 执行器（E1 同款守卫：恢复原绑定）。"""
    from src.bot.handler import MessageHandler
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME
    cap = CAPABILITY_BY_NAME["起名"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("naming")
    if executor is None:
        executor = lambda p, user_id="", user_question="": bot._tool_naming(p, user_id)  # noqa: E731
    bind_executors({"naming": executor}, {})

    def restore():
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("naming", None)
        else:
            reg._tool_executors["naming"] = orig_ex
    return restore


def test_naming_execute_tool_call_e2e():
    """e2e：JSON 工单三键 → 校验 → 序列化 → 真实排盘（补益五行）+ 候选名卡片。"""
    from src.bot.handler import MessageHandler
    from src.bot.tool_calls import ToolResult
    import re as _re

    bot = _make_bot()
    restore = _bind_naming(bot)
    try:
        r = bot._execute_tool_call("起名", {
            "surname": "张", "gender": "男", "birth": "2019年3月15日 午时 北京",
        }, "u1")
        assert r.ok is True, r.text
        # 2019-03-15 午时 北京 → 土为用神（喜土、金）→ 补益「土金」
        assert "【起名建议】" in r.text
        assert "补益五行：用神喜「土金」" in r.text
        assert r.text.count("五格评分") == 5  # 5 个候选
        # 首个候选的两字五行都在补益集合内
        m = _re.search(r'^1\. 张(\S+) ——', r.text, _re.M)
        assert m, r.text[:300]
        first_given = m.group(1)
        targets = {"土", "金"}
        assert all(char_wuxing(ch) in targets for ch in first_given)
    finally:
        restore()


def test_naming_degraded_no_birth_e2e():
    """e2e 降级路径：无出生信息 → 均衡推荐卡片（仍有候选+五格评分）。"""
    bot = _make_bot()
    restore = _bind_naming(bot)
    try:
        r = bot._execute_tool_call("起名", {"surname": "张", "gender": "女"}, "u1")
        assert r.ok is True
        assert "未提供出生信息" in r.text and "均衡推荐" in r.text
        assert "1. 张" in r.text and "五格评分" in r.text
    finally:
        restore()


def test_naming_needs_info_paths():
    """信息不全 → needs_info 澄清，不调引擎不算成功。"""
    bot = _make_bot()
    restore = _bind_naming(bot)
    try:
        # 全无参数（文本路径）→ 点名缺姓氏与性别
        r0 = bot._execute_tool_call("起名", "随便聊聊", "u1")
        assert r0.ok is False and r0.needs_info is True
        assert "姓氏" in r0.text and "性别" in r0.text and "surname" in r0.text
        # 只给性别 → 点名缺姓氏
        r1 = bot._execute_tool_call("起名", {"gender": "女"}, "u1")
        assert r1.ok is False and "参数不合法" in r1.text and "surname" in r1.text
        # 文本路径只给姓氏 → 点名缺性别
        r1b = bot._execute_tool_call("起名", "我姓张", "u1")
        assert r1b.ok is False and r1b.needs_info is True
        assert "性别" in r1b.text
        # 出生信息不可解析 → 点名 birth 段
        r2 = bot._execute_tool_call("起名", {
            "surname": "张", "gender": "男", "birth": "乱码描述"}, "u1")
        assert r2.ok is False and r2.needs_info is True
        assert "出生信息没看懂" in r2.text
        # 姓氏笔画不在字库 → 非 needs_info 的数据缺口说明
        r3 = bot._execute_tool_call("起名", {"surname": "龘", "gender": "男"}, "u1")
        assert r3.ok is False and r3.needs_info is False
        assert "笔画" in r3.text and "常用字库" in r3.text
    finally:
        restore()


def test_naming_chain_reachability_json_workorder():
    """工具循环链路可达（D5 同型回归）：reply 内 JSON 工单 naming →
    真实执行 → 结果注入 LLM 消息 → 最终文本回复。"""
    from unittest.mock import Mock, patch

    bot = _make_bot()
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

    seen_msgs: list = []

    def fake_messages(api_key, messages, model=None, max_tokens=0,
                      temperature=0.0, timeout=0.0, tools=None,
                      tool_choice=None, **kwargs):
        seen_msgs.append(messages)
        return {"stop_reason": "end_turn",
                "content": [{"type": "text", "text": "给你起了几个候选名，供参考。"}]}

    restore = _bind_naming(bot)
    try:
        with patch("src.llm.client.deepseek_anthropic_messages",
                   side_effect=fake_messages):
            out = bot._run_tool_loop(
                "给姓张的男孩起名", "u1",
                '好的，我来为你起名。<tool_calls>'
                '[{"tool": "naming", "params": {"surname": "张", "gender": "男", '
                '"birth": "2019年3月15日 午时 北京"}}]</tool_calls>')
        assert out == "给你起了几个候选名，供参考。"
        tail = seen_msgs[0][-1]["content"]
        assert '"tool": "起名"' in tail and '"ok": true' in tail
        assert "补益五行：用神喜「土金」" in tail and "五格评分" in tail
        assert bot._tool_logs["u1"]["calls"][0]["type"] == "起名"
    finally:
        restore()


def test_naming_native_tool_use_loop():
    """原生 tool_use 链路：JSON 工单先执行 → 首轮 LLM 出同参 naming 原生块 →
    去重跳过执行、复用结果回传 tool_result。"""
    from unittest.mock import Mock, patch

    bot = _make_bot()
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

    seen_round2: list = []
    called: list = []

    def fake_messages(api_key, messages, model=None, max_tokens=0,
                      temperature=0.0, timeout=0.0, tools=None,
                      tool_choice=None, **kwargs):
        if not hasattr(fake_messages, "turn"):
            fake_messages.turn = 0
        fake_messages.turn += 1
        if fake_messages.turn == 1:
            assert tools and any(t["name"] == "naming" for t in tools)
            return {"stop_reason": "tool_use",
                    "content": [{"type": "tool_use", "id": "tu_n1",
                                 "name": "naming",
                                 "input": {"surname": "张", "gender": "男",
                                           "birth": "2019年3月15日 午时 北京"}}]}
        seen_round2.append(messages[-1])
        return {"stop_reason": "end_turn",
                "content": [{"type": "text", "text": "参考这几个候选名。"}]}

    def spy(params, user_id="", user_question=""):
        called.append(params)
        return bot._tool_naming(params, user_id)

    restore = _bind_naming(bot, executor=spy)
    try:
        with patch("src.llm.client.deepseek_anthropic_messages",
                   side_effect=fake_messages):
            out = bot._run_tool_loop(
                "给姓张的男孩起名", "u1",
                '好的，我来为你起名。<tool_calls>'
                '[{"tool": "naming", "params": {"surname": "张", "gender": "男", '
                '"birth": "2019年3月15日 午时 北京"}}]</tool_calls>')
        assert out == "参考这几个候选名。"
        assert fake_messages.turn == 2
        # 同参原生块被去重跳过（review I-1）：仅工单执行 1 次
        assert len(called) == 1
        last = seen_round2[0]
        assert last["role"] == "user"
        results = last["content"]
        assert [r["type"] for r in results] == ["tool_result"]
        assert results[0]["tool_use_id"] == "tu_n1"
        assert "补益五行：用神喜「土金」" in results[0]["content"]
        assert "五格评分" in results[0]["content"]
    finally:
        restore()
