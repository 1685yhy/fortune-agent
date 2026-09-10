"""批次 2 E4：择业/方位匹配工具（cap_id: career_dir）——参数校验 + 规则逻辑 + e2e 链路。

规则依据（与 src/tools/career_dir.py docstring 同源，本测试锁定行为）：
- 喜用神：BaziEngine.calculate 的 yongshen 字段（"X为用神（喜X、Y）"形态，
  引擎 _calc_yongshen 调候优先 + 扶抑辅助口径）；parse_yongshen 与 ming.py 同口径
- 行业五行映射表：命理通识行业五行归类（金融金属属金/文化教育木竹属木/运输物流
  水产属水/餐饮能源电子属火/地产建筑农业属土），来源见规则层 docstring
- 方位（后天八卦方位）：木→东方、火→南方、土→中央（本地）、金→西方、水→北方
- 禁忌行业：忌神五行（扶抑理论 + 引擎 wuxing_energy.strength 五档）——
  旺/偏旺忌生扶（比劫印）；弱/偏弱忌克泄耗（官杀食伤财）；中和忌最旺五行
  （太过为忌），与喜用重叠剔除
- 测试命例（引擎实测口径）：
  · 1990-05-20 午时 北京 男：乙木偏弱，喜 水/木，忌 金/火/土
  · 1986-06-12 巳时 北京 女：丁火旺，喜 水/土，忌 火/木（金为中性）
  · 1982-03-05 午时 广州 男：丁火中和，喜 木/火/金，忌 最旺水（火重叠剔除）
"""
import sys

sys.path.insert(0, "src")

from src.bot import capability_registry as reg  # noqa: E402
from src.engines.bazi import BaziEngine  # noqa: E402
from src.tools import career_dir as cd  # noqa: E402


# ============================================================
# 参数校验（注册表 schema / 说明书一致性）
# ============================================================

def test_career_schema_required_keys():
    """schema 必填键恰为 birth；properties 两键齐全均为 string。"""
    schema = reg._TOOL_PARAMS_SCHEMAS["career_dir"]
    assert schema["required"] == ["birth"]
    for key in ("birth", "industry"):
        assert key in schema["properties"]
        assert schema["properties"][key]["type"] == "string"


def test_validate_params_career_dir():
    """参数校验：birth 齐 → None（industry 可省）；缺 birth/空/非字符串 → 错误串。"""
    ok = reg.validate_params("career_dir", {
        "birth": "1990年5月20日 午时 北京 男", "industry": "金融"})
    assert ok is None
    ok2 = reg.validate_params("career_dir", {"birth": "1990年5月20日 午时 北京 男"})
    assert ok2 is None
    err = reg.validate_params("career_dir", {"industry": "金融"})
    assert err is not None and "birth" in err
    err2 = reg.validate_params("career_dir", {})
    assert err2 is not None and "birth" in err2
    err3 = reg.validate_params("career_dir", {"birth": 123})
    assert err3 is not None


def test_career_schema_keys_in_description_and_requires():
    """批次 1 P1 #2 教训固化：工具描述/requires 里的参数键必须与 schema
    键完全一致（birth/industry 四处点名）。"""
    cap = reg.CAPABILITY_BY_ID["career_dir"]
    assert cap.cap_type == "tool"
    for key in ("birth", "industry"):
        assert key in cap.description
        assert key in cap.requires
    desc = reg.build_tool_description()
    assert "择业" in desc and "career_dir" in desc
    for key in ("birth", "industry"):
        assert key in desc
    # 属性示例也含各自键名（LLM 按示例填参能过校验）
    props = cap.params_schema["properties"]
    for key in ("birth", "industry"):
        assert key in props[key]["description"]


def test_career_in_registry_projection():
    """TOOL_REGISTRY / build_tool_schema_list / TOOL_NAME_BY_ID 自动投影含择业。"""
    from src.bot.tool_calls import TOOL_REGISTRY, TOOL_NAME_BY_ID
    assert "择业" in TOOL_REGISTRY
    assert TOOL_REGISTRY["择业"]["key"] == "career_dir"
    assert TOOL_NAME_BY_ID["career_dir"] == "择业"
    schemas = {s["name"]: s for s in reg.build_tool_schema_list()}
    assert "career_dir" in schemas
    assert schemas["career_dir"]["input_schema"] == \
        reg.CAPABILITY_BY_ID["career_dir"].params_schema


def test_career_workorder_and_native_parse():
    """JSON 工单 / 原生 tool_use：英文 cap_id career_dir 归一为中文名 择业 并带两键。"""
    from src.bot.tool_calls import parse_native_tool_use_blocks, parse_tool_calls
    calls = parse_tool_calls(
        '<tool_calls>[{"tool": "career_dir", "params": {"birth": "B方", '
        '"industry": "金融"}}]</tool_calls>')
    assert [(c.name, c.params_obj) for c in calls] == [
        ("择业", {"birth": "B方", "industry": "金融"})]
    blocks = parse_native_tool_use_blocks([{
        "type": "tool_use", "id": "tu_d", "name": "career_dir",
        "input": {"birth": "B方"}}])
    assert blocks[0].name == "择业"
    assert blocks[0].params_obj == {"birth": "B方"}


def test_career_serialize_roundtrip():
    """serialize_params 产物（k: v 换行）→ parse_career_params 两键还原。"""
    from src.bot.tool_calls import serialize_params
    text = serialize_params({
        "birth": "1990年5月20日 午时 北京 男", "industry": "金融"})
    info = cd.parse_career_params(text)
    assert info == {"birth": "1990年5月20日 午时 北京 男", "industry": "金融"}


# ============================================================
# 规则逻辑：参数拆分 / 行业五行表 / 方位 / 喜用忌神 / 卡片
# ============================================================

def test_parse_career_params_structured_keys():
    """结构化键形态：半/全角冒号、等号、键序无关、industry 可省。"""
    assert cd.parse_career_params(
        "birth: 1990年5月20日 午时 北京 男\nindustry: 金融"
    ) == {"birth": "1990年5月20日 午时 北京 男", "industry": "金融"}
    assert cd.parse_career_params(
        "industry＝餐饮\nbirth: 1991年6月1日 卯时 上海 女"
    ) == {"birth": "1991年6月1日 卯时 上海 女", "industry": "餐饮"}
    assert cd.parse_career_params("birth: 只有出生信息") == {"birth": "只有出生信息"}
    assert cd.parse_career_params("industry: 金融") == {"industry": "金融"}


def test_parse_career_params_text_fallback():
    """文本标签兜底：行业词在出生日期前/后两种语序均可拆出，尾部语境剥净。"""
    # 语序 1：行业词在前
    assert cd.parse_career_params(
        "我考虑做金融，1990年5月20日 午时 北京 男"
    ) == {"birth": "1990年5月20日 午时 北京 男", "industry": "金融"}
    # 语序 2：行业词在后（连带"想做"尾部残留剥净）
    assert cd.parse_career_params(
        "1990年5月20日 午时 北京 男 想做金融"
    ) == {"birth": "1990年5月20日 午时 北京 男", "industry": "金融"}
    # 语序 3：行业词在后带工作/公司语境
    assert cd.parse_career_params(
        "1990年5月20日 午时 北京 男 现在考虑做房地产工作"
    ) == {"birth": "1990年5月20日 午时 北京 男", "industry": "房地产"}
    # 无行业词 → 只出 birth（全推荐路径）
    assert cd.parse_career_params("1990年5月20日 午时 北京 男") == {
        "birth": "1990年5月20日 午时 北京 男"}


def test_parse_career_params_unparseable():
    """拆不出出生信息 → None（不误判）；无出生年但含行业词 → None。"""
    assert cd.parse_career_params("") is None
    assert cd.parse_career_params(None) is None
    assert cd.parse_career_params("你好呀") is None
    assert cd.parse_career_params("我想做金融") is None      # 无出生年


def test_industry_words_table_integrity():
    """行业五行表完整：五元素齐全、每类 ≥3 类别、词表覆盖全五行且无空词。"""
    assert set(cd.INDUSTRY_WUXING) == {"金", "木", "水", "火", "土"}
    for wx in ("金", "木", "水", "火", "土"):
        cats = cd.INDUSTRY_WUXING[wx]
        assert len(cats) >= 3, wx
        for label, examples in cats:
            assert label and examples, (wx, label)
            assert len([w for w in examples.split("、") if w.strip()]) >= 2, (wx, label)
    assert len(cd.INDUSTRY_WORDS) >= 100
    # 词表五行覆盖：每五行至少 10 个登记词
    for wx in ("金", "木", "水", "火", "土"):
        assert sum(1 for w in cd.INDUSTRY_WORDS if cd.INDUSTRY_WORDS[w] == wx) >= 10, wx


def test_industry_element_lookup():
    """行业词 → 五行：五类代表词 + 后缀剥除 + 未收录 → None。"""
    assert cd.industry_element("金融") == "金"
    assert cd.industry_element("银行") == "金"
    assert cd.industry_element("教育") == "木"
    assert cd.industry_element("家具") == "木"
    assert cd.industry_element("物流") == "水"
    assert cd.industry_element("水产") == "水"
    assert cd.industry_element("餐饮") == "火"
    assert cd.industry_element("互联网") == "火"
    assert cd.industry_element("房地产") == "土"
    assert cd.industry_element("农业") == "土"
    # 后缀剥除：金融行业/金融公司 → 金；电商在电子商务文本中按最长登记词归火
    assert cd.industry_element("金融行业") == "金"
    assert cd.industry_element("金融公司") == "金"
    assert cd.industry_element("电子商务") == "火"
    assert cd.industry_element("区块链") is None      # 未收录不臆断
    assert cd.industry_element("") is None
    assert cd.industry_element(None) is None


def test_direction_mapping():
    """五行方位配属（后天八卦方位，命理通识）：木东/火南/土中央/金西/水北。"""
    assert cd.ELEMENT_DIRECTION == {
        "木": "东方", "火": "南方", "土": "中央（本地）", "金": "西方", "水": "北方"}


def test_helpful_elements_engine_consistency():
    """喜用五行与引擎 yongshen 同口径：1990 命例 → 喜 水/木。"""
    r = BaziEngine().calculate(1990, 5, 20, 12, 0, "北京", "男")
    assert r.yongshen == "水为用神（调候优先）（喜水、木）"
    assert cd.helpful_elements(r) == ["水", "木"]


def test_forbidden_elements_weak():
    """身弱（偏弱）→ 忌克泄耗：官杀/食伤/财（1990 乙木 → 金/火/土）。"""
    r = BaziEngine().calculate(1990, 5, 20, 12, 0, "北京", "男")
    assert cd.day_master_strength_of(r) == "偏弱"
    assert set(cd.forbidden_elements(r)) == {"金", "火", "土"}
    assert "日主偏弱，忌克泄耗" in cd.forbidden_reason("偏弱")


def test_forbidden_elements_strong():
    """身旺 → 忌生扶：比劫/印星（1986 丁火旺 → 火/木）。"""
    r = BaziEngine().calculate(1986, 6, 12, 14, 0, "北京", "女")
    assert cd.day_master_strength_of(r) == "旺"
    assert set(cd.forbidden_elements(r)) == {"火", "木"}
    assert "日主偏旺，忌生扶" in cd.forbidden_reason("旺")


def test_forbidden_elements_neutral_overlap():
    """中和 → 忌命局最旺五行；与喜用重叠者剔除（1982 丁火 → 最旺水/火，
    火属喜用 → 剔除，仅余水；不出现既荐又忌）。"""
    r = BaziEngine().calculate(1982, 3, 5, 12, 0, "广州", "男")
    assert cd.day_master_strength_of(r) == "中和"
    assert cd.helpful_elements(r) == ["木", "火", "金"]   # 引擎喜用
    # 命局最旺：水3、火3 → 忌 {水,火}；火 ∈ 喜用 → 剔除 → {水}
    assert cd.forbidden_elements(r) == ["水"]
    assert "命局中和，忌过旺" in cd.forbidden_reason("中和")


def test_forbidden_deterministic():
    """忌神判定确定性：同排盘重复调用同输出。"""
    r = BaziEngine().calculate(1990, 5, 20, 12, 0, "北京", "男")
    assert cd.forbidden_elements(r) == cd.forbidden_elements(r)


def test_format_career_card_sections():
    """结果卡片四要素齐：喜用神结论 / 吉利方位 / 适合行业（五行清单）/
    禁忌行业（含判定依据）+ 考虑行业命中禁忌判定。"""
    r = BaziEngine().calculate(1990, 5, 20, 12, 0, "北京", "男")
    card = cd.format_career_card(r, "金融")
    for section in ("【择业方位】", "日主：乙木", "日主强弱：偏弱",
                    "喜用神结论：水为用神（调候优先）（喜水、木）",
                    "喜用五行：水、木｜吉利方位：北方（水）、东方（木）",
                    "适合行业（水·吉）：运输物流", "适合行业（木·吉）：文化教育",
                    "禁忌行业（日主偏弱，忌克泄耗", "忌金：金融财务",
                    "考虑行业「金融」属金：命中禁忌五行，建议避开。"):
        assert section in card, f"卡片缺: {section}\n{card}"
    assert "南方" not in card.split("吉利方位")[1].split("适合行业")[0]  # 方位无火


def test_format_career_card_full_recommendation():
    """无考虑行业 → 全推荐（不出现考虑行业行），喜用五行齐全。"""
    r = BaziEngine().calculate(1990, 5, 20, 12, 0, "北京", "男")
    card = cd.format_career_card(r, None)
    assert "考虑行业" not in card
    assert "适合行业（水·吉）" in card and "适合行业（木·吉）" in card
    assert "禁忌行业" in card


def test_format_career_card_unknown_and_neutral_industry():
    """考虑行业判定三态：未收录 → 提示不臆断；非喜非忌（中性）→ 可行但非最优；
    命中喜用 → 非常适合。"""
    r_weak = BaziEngine().calculate(1990, 5, 20, 12, 0, "北京", "男")
    card_unknown = cd.format_career_card(r_weak, "区块链")
    assert "暂无法归入行业五行表" in card_unknown
    assert "命中禁忌" not in card_unknown
    # 1986 丁火旺：喜水/土、忌火/木 → 金为中性
    r_strong = BaziEngine().calculate(1986, 6, 12, 14, 0, "北京", "女")
    card_neutral = cd.format_career_card(r_strong, "金融")
    assert "考虑行业「金融」属金：非喜用亦非禁忌，可行但非最优。" in card_neutral
    card_good = cd.format_career_card(r_strong, "物流")
    assert "考虑行业「物流」属水：命中喜用五行，非常适合。" in card_good
    card_bad = cd.format_career_card(r_strong, "教育")
    assert "考虑行业「教育」属木：命中禁忌五行，建议避开。" in card_bad


# ============================================================
# e2e（handler 执行器 + 工具循环链路）
# ============================================================

def _make_bot():
    """轻量 MessageHandler（__new__ 避开 __init__ 装配），注入真实引擎。"""
    from src.bot.handler import MessageHandler
    bot = MessageHandler.__new__(MessageHandler)
    bot.engine = BaziEngine()
    return bot


def test_career_execute_tool_call_e2e():
    """e2e：JSON 工单两键 → 校验 → 序列化 → 真实排盘 + 择业卡片。"""
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

    bot = _make_bot()
    cap = CAPABILITY_BY_NAME["择业"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("career_dir")
    try:
        bind_executors({"career_dir": lambda p, user_id="", user_question="":
                        bot._tool_career_dir(p, user_id)}, {})
        r = bot._execute_tool_call("择业", {
            "birth": "1990年5月20日 午时 北京 男",
            "industry": "金融",
        }, "u1")
        assert r.ok is True
        # 1990 庚午 乙木偏弱 → 喜 水/木，忌 金/火/土；金融属金 → 禁忌
        assert "喜用神结论：水为用神（调候优先）（喜水、木）" in r.text
        assert "喜用五行：水、木｜吉利方位：北方（水）、东方（木）" in r.text
        assert "适合行业（水·吉）：运输物流" in r.text
        assert "忌金：金融财务" in r.text and "忌火：餐饮食品" in r.text
        assert "考虑行业「金融」属金：命中禁忌五行，建议避开。" in r.text
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("career_dir", None)
        else:
            reg._tool_executors["career_dir"] = orig_ex


def test_career_full_recommendation_e2e():
    """e2e：只给 birth → 按喜用神五行全量推荐（无考虑行业判定行）。"""
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

    bot = _make_bot()
    cap = CAPABILITY_BY_NAME["择业"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("career_dir")
    try:
        bind_executors({"career_dir": lambda p, user_id="", user_question="":
                        bot._tool_career_dir(p, user_id)}, {})
        r = bot._execute_tool_call(
            "择业", {"birth": "1990年5月20日 午时 北京 男"}, "u1")
        assert r.ok is True
        assert "考虑行业" not in r.text
        assert "适合行业（水·吉）" in r.text and "适合行业（木·吉）" in r.text
        assert "禁忌行业" in r.text
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("career_dir", None)
        else:
            reg._tool_executors["career_dir"] = orig_ex


def test_career_needs_info_paths():
    """信息不全 → needs_info 澄清，不调引擎不算成功。"""
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

    bot = _make_bot()
    cap = CAPABILITY_BY_NAME["择业"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("career_dir")
    try:
        bind_executors({"career_dir": lambda p, user_id="", user_question="":
                        bot._tool_career_dir(p, user_id)}, {})
        # 全无出生信息 → 追问（含 birth 键名示例）
        r0 = bot._execute_tool_call("择业", "我适合做什么行业", "u1")
        assert r0.ok is False and r0.needs_info is True
        assert "出生信息" in r0.text and "birth" in r0.text
        # 出生信息可拆出但解析失败（日期非法，5月32日）→ 点名出生信息（含原样回显）
        r1 = bot._execute_tool_call(
            "择业", {"birth": "1990年5月32日 午时 北京 男", "industry": "金融"}, "u1")
        assert r1.ok is False and r1.needs_info is True
        assert "出生信息" in r1.text and "1990年5月32日" in r1.text
        # 结构化工单缺 birth 键 → 框架校验拦截（参数不合法，不执行）
        r2 = bot._execute_tool_call("择业", {"industry": "金融"}, "u1")
        assert r2.ok is False and "参数不合法" in r2.text and "birth" in r2.text
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("career_dir", None)
        else:
            reg._tool_executors["career_dir"] = orig_ex


def test_career_chain_reachability_json_workorder():
    """工具循环链路可达（D5 同型回归）：reply 内 JSON 工单 career_dir →
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

    cap = CAPABILITY_BY_NAME["择业"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("career_dir")
    seen_msgs: list = []

    def fake_messages(api_key, messages, model=None, max_tokens=0,
                      temperature=0.0, timeout=0.0, tools=None,
                      tool_choice=None, **kwargs):
        seen_msgs.append(messages)
        return {"stop_reason": "end_turn",
                "content": [{"type": "text", "text": "你命局喜水木，宜从事物流、教育类行业。"}]}

    try:
        bind_executors({"career_dir": lambda p, user_id="", user_question="":
                        bot._tool_career_dir(p, user_id)}, {})
        with patch("src.llm.client.deepseek_anthropic_messages",
                   side_effect=fake_messages):
            out = bot._run_tool_loop(
                "我适合做什么行业", "u1",
                '好的，我来分析。<tool_calls>'
                '[{"tool": "career_dir", "params": {"birth": "1990年5月20日 午时 北京 男", '
                '"industry": "金融"}}]</tool_calls>')
        assert out == "你命局喜水木，宜从事物流、教育类行业。"
        tail = seen_msgs[0][-1]["content"]
        assert '"tool": "择业"' in tail and '"ok": true' in tail
        assert "喜用神结论" in tail and "禁忌行业" in tail
        assert bot._tool_logs["u1"]["calls"][0]["type"] == "择业"
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("career_dir", None)
        else:
            reg._tool_executors["career_dir"] = orig_ex


def test_career_native_tool_use_loop():
    """原生 tool_use 链路：JSON 工单先执行 → 首轮 LLM 出同参 career_dir 原生块
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

    cap = CAPABILITY_BY_NAME["择业"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("career_dir")
    seen_round2: list = []
    called: list = []

    def fake_messages(api_key, messages, model=None, max_tokens=0,
                      temperature=0.0, timeout=0.0, tools=None,
                      tool_choice=None, **kwargs):
        if not hasattr(fake_messages, "turn"):
            fake_messages.turn = 0
        fake_messages.turn += 1
        if fake_messages.turn == 1:
            assert tools and any(t["name"] == "career_dir" for t in tools)
            return {"stop_reason": "tool_use",
                    "content": [{"type": "tool_use", "id": "tu_d1",
                                 "name": "career_dir",
                                 "input": {"birth": "1990年5月20日 午时 北京 男",
                                           "industry": "金融"}}]}
        seen_round2.append(messages[-1])
        return {"stop_reason": "end_turn",
                "content": [{"type": "text", "text": "金融五行属金，是命局禁忌，建议改做物流。"}]}

    def spy(params, user_id="", user_question=""):
        called.append(params)
        return bot._tool_career_dir(params, user_id)

    try:
        bind_executors({"career_dir": spy}, {})
        with patch("src.llm.client.deepseek_anthropic_messages",
                   side_effect=fake_messages):
            out = bot._run_tool_loop(
                "我适合做什么行业", "u1",
                '好的，我来分析。<tool_calls>'
                '[{"tool": "career_dir", "params": {"birth": "1990年5月20日 午时 北京 男", '
                '"industry": "金融"}}]</tool_calls>')
        assert out == "金融五行属金，是命局禁忌，建议改做物流。"
        assert fake_messages.turn == 2
        # 同参原生块被去重跳过（review I-1）：仅工单执行 1 次
        assert len(called) == 1
        last = seen_round2[0]
        assert last["role"] == "user"
        results = last["content"]
        assert [r["type"] for r in results] == ["tool_result"]
        assert results[0]["tool_use_id"] == "tu_d1"
        assert "喜用神结论" in results[0]["content"]
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("career_dir", None)
        else:
            reg._tool_executors["career_dir"] = orig_ex
