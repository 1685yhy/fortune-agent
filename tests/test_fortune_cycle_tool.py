"""批次 2 E3：流月流年工具（cap_id: fortune_cycle）——参数校验 + 规则逻辑 + e2e 链路。

规则依据（与 src/engines/bazi.py / bazi_formatter.py 既有资产同源，本测试锁定行为）：
- 流年干支：年柱 + 岁差 mod 60（引擎 liunian_ganzhi），出生干支年 = liunian_full[0].year
  （立春界定，2026-08-20 修复口径）；目标年可为任意公历年（含超出 30 年流年表窗口）
- 流月干支：五虎遁（WUHU_DUN）+ 公历月近似（立春≈2月4日，公历2月≈寅月正月）；
  年内 12 月序列与引擎 liuyue 完全一致
- 十神：D2 派生护栏口径 bazi_formatter._get_shishen(day_gan, gan, for_hidden=True)
  （同干 → 比肩）；地支藏干用 BRANCH_HIDDEN（子平真诠表）
- 十神吉凶（四吉四凶两中）：吉 = 正官/正印/正财/食神；需留意 = 七杀/偏印/伤官/劫财；
  中性 = 比肩/偏财
- 关注维度映射（通识）：事业 = 官杀+印星+比劫；财运 = 正偏财+食伤；感情 = 正偏财+官杀
- 默认值：目标年份/月份缺省 → 今年/本月（标准库 datetime，now 可注入）
"""
import sys
from datetime import datetime

sys.path.insert(0, "src")

from src.bot import capability_registry as reg  # noqa: E402
from src.engines.bazi import (BaziEngine, DIZHI, NAYIN, TIANGAN, WUHU_DUN,  # noqa: E402
                              liunian_ganzhi, liuyue)
from src.engines.bazi_formatter import BRANCH_HIDDEN, _get_shishen  # noqa: E402
from src.tools import fortune_cycle as cycle  # noqa: E402


# ============================================================
# 参数校验（注册表 schema / 说明书一致性）
# ============================================================

def test_cycle_schema_required_keys():
    """schema 必填键恰为 birth；properties 四键齐全均为 string。"""
    schema = reg._TOOL_PARAMS_SCHEMAS["fortune_cycle"]
    assert schema["required"] == ["birth"]
    for key in ("birth", "year", "month", "focus"):
        assert key in schema["properties"]
        assert schema["properties"][key]["type"] == "string"


def test_validate_params_fortune_cycle():
    """参数校验：birth 齐 → None（year/month/focus 可省）；缺 birth/空/非字符串 → 错误串。"""
    ok = reg.validate_params("fortune_cycle", {
        "birth": "1990年5月20日 午时 北京 男", "year": "2027", "month": "6",
        "focus": "财运"})
    assert ok is None
    ok2 = reg.validate_params("fortune_cycle", {"birth": "1990年5月20日 午时 北京 男"})
    assert ok2 is None
    err = reg.validate_params("fortune_cycle", {"year": "2027"})
    assert err is not None and "birth" in err
    err2 = reg.validate_params("fortune_cycle", {})
    assert err2 is not None and "birth" in err2
    err3 = reg.validate_params("fortune_cycle", {"birth": 123})
    assert err3 is not None


def test_cycle_schema_keys_in_description_and_requires():
    """批次 1 P1 #2 教训固化：工具描述/requires 里的参数键必须与 schema
    键完全一致（birth/year/month/focus 四处点名）。"""
    cap = reg.CAPABILITY_BY_ID["fortune_cycle"]
    assert cap.cap_type == "tool"
    for key in ("birth", "year", "month", "focus"):
        assert key in cap.description
        assert key in cap.requires
    desc = reg.build_tool_description()
    assert "流月流年" in desc and "fortune_cycle" in desc
    for key in ("birth", "year", "month", "focus"):
        assert key in desc
    # 属性示例也含各自键名（LLM 按示例填参能过校验）
    props = cap.params_schema["properties"]
    for key in ("birth", "year", "month", "focus"):
        assert key in props[key]["description"]


def test_cycle_in_registry_projection():
    """TOOL_REGISTRY / build_tool_schema_list / TOOL_NAME_BY_ID 自动投影含流月流年。"""
    from src.bot.tool_calls import TOOL_REGISTRY, TOOL_NAME_BY_ID
    assert "流月流年" in TOOL_REGISTRY
    assert TOOL_REGISTRY["流月流年"]["key"] == "fortune_cycle"
    assert TOOL_NAME_BY_ID["fortune_cycle"] == "流月流年"
    schemas = {s["name"]: s for s in reg.build_tool_schema_list()}
    assert "fortune_cycle" in schemas
    assert schemas["fortune_cycle"]["input_schema"] == \
        reg.CAPABILITY_BY_ID["fortune_cycle"].params_schema


def test_cycle_workorder_and_native_parse():
    """JSON 工单 / 原生 tool_use：英文 cap_id fortune_cycle 归一为中文名 流月流年 并带四键。"""
    from src.bot.tool_calls import parse_native_tool_use_blocks, parse_tool_calls
    calls = parse_tool_calls(
        '<tool_calls>[{"tool": "fortune_cycle", "params": {"birth": "B方", '
        '"year": "2027", "focus": "财运"}}]</tool_calls>')
    assert [(c.name, c.params_obj) for c in calls] == [
        ("流月流年", {"birth": "B方", "year": "2027", "focus": "财运"})]
    blocks = parse_native_tool_use_blocks([{
        "type": "tool_use", "id": "tu_c", "name": "fortune_cycle",
        "input": {"birth": "B方", "year": "2027"}}])
    assert blocks[0].name == "流月流年"
    assert blocks[0].params_obj == {"birth": "B方", "year": "2027"}


def test_cycle_serialize_roundtrip():
    """serialize_params 产物（k: v 换行）→ parse_cycle_params 四键还原。"""
    from src.bot.tool_calls import serialize_params
    text = serialize_params({
        "birth": "1990年5月20日 午时 北京 男",
        "year": "2027", "month": "6", "focus": "事业,财运"})
    info = cycle.parse_cycle_params(text)
    assert info == {
        "birth": "1990年5月20日 午时 北京 男",
        "year": "2027", "month": "6", "focus": "事业,财运"}


# ============================================================
# 规则逻辑：参数拆分 / 默认值 / 干支推演 / 十神 / 吉凶月
# ============================================================

def test_parse_cycle_params_structured_keys():
    """结构化键形态：半/全角冒号、等号、键序无关、可选键可省。"""
    assert cycle.parse_cycle_params(
        "birth: 1990年5月20日 午时 北京 男\nyear: 2027\nmonth: 6\nfocus: 财运"
    ) == {"birth": "1990年5月20日 午时 北京 男", "year": "2027",
          "month": "6", "focus": "财运"}
    assert cycle.parse_cycle_params(
        "focus＝事业\nyear=2028\nbirth: 1991年6月1日 卯时 上海 女"
    ) == {"birth": "1991年6月1日 卯时 上海 女", "year": "2028", "focus": "事业"}
    assert cycle.parse_cycle_params("birth: 只有出生信息") == {"birth": "只有出生信息"}


def test_parse_cycle_params_text_fallback():
    """文本标签兜底：目标年/月在出生日期前后两种语序均可拆出（日期感知）。"""
    # 语序 1：目标年在后
    assert cycle.parse_cycle_params(
        "1990年5月20日 午时 北京 男 2027年6月 看财运"
    ) == {"birth": "1990年5月20日 午时 北京 男", "year": "2027",
          "month": "6", "focus": "财运"}
    # 语序 2：目标年在先（不得误吞出生日期 1990年/5月）
    assert cycle.parse_cycle_params(
        "2027年看运势，1990年5月20日 午时 北京 男"
    ) == {"birth": "1990年5月20日 午时 北京 男", "year": "2027"}
    # 无目标年/月 → 只出 birth（默认值由执行器补今年/本月）
    assert cycle.parse_cycle_params("1990年5月20日 午时 北京 男") == {
        "birth": "1990年5月20日 午时 北京 男"}


def test_parse_cycle_params_unparseable():
    """拆不出出生信息 → None（不误判）。"""
    assert cycle.parse_cycle_params("") is None
    assert cycle.parse_cycle_params(None) is None
    assert cycle.parse_cycle_params("只看2027年财运") is None   # 无出生年
    assert cycle.parse_cycle_params("你好呀") is None


def test_default_targets_injectable():
    """默认值：今年/本月（标准库 datetime，now 注入可确定性测试）。"""
    y, m = cycle.default_targets(datetime(2026, 8, 27))
    assert (y, m) == (2026, 8)


def test_parse_target_year_month_boundaries():
    """目标年/月合法化边界：1900-2300 / 1-12；越界与非法 → None。"""
    assert cycle.parse_target_year("2027") == 2027
    assert cycle.parse_target_year(1990) == 1990
    assert cycle.parse_target_year("1900") == 1900
    assert cycle.parse_target_year("2300") == 2300
    assert cycle.parse_target_year("1899") is None
    assert cycle.parse_target_year("2301") is None
    assert cycle.parse_target_year("abc") is None
    assert cycle.parse_target_month("1") == 1
    assert cycle.parse_target_month("12") == 12
    assert cycle.parse_target_month("0") is None
    assert cycle.parse_target_month("13") is None
    assert cycle.parse_target_month("x") is None


def test_parse_focus_normalization():
    """关注维度规范化：逗号/顿号/空格分隔多选、未知词忽略、去重。"""
    assert cycle.parse_focus("事业") == ["事业"]
    assert cycle.parse_focus("事业,财运") == ["事业", "财运"]
    assert cycle.parse_focus("事业、感情") == ["事业", "感情"]
    assert cycle.parse_focus("事业 财运") == ["事业", "财运"]
    assert cycle.parse_focus("事业,财运,事业") == ["事业", "财运"]
    assert cycle.parse_focus("桃花运") == []        # 未知词忽略
    assert cycle.parse_focus("") == []
    assert cycle.parse_focus(None) == []


def test_flow_year_ganzhi_engine_consistency():
    """流年干支与引擎同口径：liunian_full 窗口内一致 + 窗口外公式成立 + 早于出生年。"""
    r = BaziEngine().calculate(1990, 5, 20, 12, 0, "北京", "男")
    assert cycle.birth_pillar_year(r) == 1990          # liunian_full[0].year
    # 窗口内（1990-2019）：与引擎流年表逐项一致
    for item in r.liunian_full:
        assert cycle.flow_year_gz(r, item["year"]) == item["ganzhi"]
    # 窗口外（2027）：年柱庚午 + 37 岁差 = 丁未（公式直接成立）
    assert cycle.flow_year_gz(r, 2027) == "丁未"
    assert liunian_ganzhi(1990, "庚午", 2027) == "丁未"
    # 早于出生年（负岁差 mod 60 成立）
    assert cycle.flow_year_gz(r, 1987) == "丁卯"
    assert NAYIN["丁未"] == "天河水"


def test_flow_month_ganzhi_consistency_with_engine_liuyue():
    """流月干支：年内 12 月序列与引擎 liuyue 完全一致（仅月序锚点不同）。"""
    year_gz = "丁未"
    engine_seq = liuyue(year_gz)                       # 寅月(正月)起 12 月
    my_seq = [cycle.flow_month_gz(year_gz, m) for m in range(2, 13)] + \
             [cycle.flow_month_gz(year_gz, 1)]         # 公历 2 月(寅月)起 12 月
    assert my_seq == engine_seq
    assert cycle.flow_month_gz("丁未", 6) == "丙午"    # 直接抽查
    assert cycle.flow_month_gz("己卯", 2) == "丙寅"    # 己年正月丙寅（五虎遁）
    # 地支按月循环：公历 2 月=寅(序2)、1 月=丑(序1)、12 月=子(序0)
    assert DIZHI[6 % 12] == "午"
    assert cycle.flow_month_gz("庚午", 6)[1] == "午"


def test_shishen_for_hidden_guardrail():
    """十神 D2 派生护栏口径：同干 → 比肩；藏干表与 for_hidden 映射正确。"""
    assert _get_shishen("乙", "乙", for_hidden=True) == "比肩"
    assert cycle.shishen_of("乙", "乙") == "比肩"
    assert cycle.shishen_of("乙", "丁") == "食神"
    assert cycle.shishen_of("乙", "己") == "偏财"
    # 藏干（子平真诠表）：未藏 己丁乙，对日主乙 → 偏财/食神/比肩
    assert cycle.hidden_stems_of("未") == ["己", "丁", "乙"]
    assert BRANCH_HIDDEN["巳"] == ["丙", "庚", "戊"]
    ss = cycle.year_ten_shen_set("乙", "丁未")
    assert ss == {"食神", "偏财", "比肩"}


def test_focus_mapping_tables():
    """关注维度 → 十神映射（通识）：事业=官杀印比劫；财运=正偏财+食伤；感情=正偏财+官杀。"""
    assert set(cycle.FOCUS_TEN_SHEN["事业"]) == {"正官", "七杀", "正印", "偏印",
                                                 "比肩", "劫财"}
    assert set(cycle.FOCUS_TEN_SHEN["财运"]) == {"正财", "偏财", "食神", "伤官"}
    assert set(cycle.FOCUS_TEN_SHEN["感情"]) == {"正财", "偏财", "正官", "七杀"}
    assert set(cycle.TEN_SHEN_LUCK["吉"]) == {"正官", "正印", "正财", "食神"}
    assert set(cycle.TEN_SHEN_LUCK["需留意"]) == {"七杀", "偏印", "伤官", "劫财"}
    assert set(cycle.TEN_SHEN_LUCK["中"]) == {"比肩", "偏财"}


def test_month_luck_scoring():
    """单月吉凶评分：月干 + 月支本气 计分（吉+1/中0/凶-1，-2..+2）与吉凶词。"""
    # 乙木日主：2027年6月 丙午 —— 丙=伤官(-1)，午本气丁=食神(+1) → 0 平
    assert cycle.luck_score("乙", "丙午") == 0
    assert cycle.luck_word(0) == "平"
    # 2027年5月 乙巳 —— 乙=比肩(0)，巳本气丙=伤官(-1) → -1 需留意
    assert cycle.luck_score("乙", "乙巳") == -1
    assert cycle.luck_word(-1) == "需留意"
    # 2027年3月 癸卯 —— 癸=偏印(-1)，卯本气乙=比肩(0) → -1
    assert cycle.luck_score("乙", "癸卯") == -1
    # 2027年8月 戊申 —— 戊=正财(+1)，申本气庚=正官(+1) → 2 吉
    assert cycle.luck_score("乙", "戊申") == 2
    assert cycle.luck_word(2) == "吉"


def test_best_worst_months_deterministic():
    """全年 12 流月吉凶 → 最吉/最需留意月（确定性；同分时最吉月取最早、
    最需留意月取最晚，与 best_worst_months 文档一致）。"""
    r = BaziEngine().calculate(1990, 5, 20, 12, 0, "北京", "男")
    day_gan = r.bazi[2][0]
    best, worst = cycle.best_worst_months(day_gan, "丁未")
    assert 1 <= best <= 12 and 1 <= worst <= 12
    # 重复调用确定性一致
    assert cycle.best_worst_months(day_gan, "丁未") == (best, worst)
    # 最吉月评分 ≥ 任意月；最需留意月评分 ≤ 任意月
    scores = [cycle.luck_score(day_gan, cycle.flow_month_gz("丁未", m))
              for m in range(1, 13)]
    assert scores[best - 1] == max(scores)
    assert scores[worst - 1] == min(scores)


def test_dayun_step_and_qiyun_context():
    """大运上下文：虚岁定位（目标年 − 出生干支年 + 1，s~s+9 十年一步）。"""
    r = BaziEngine().calculate(1990, 5, 20, 12, 0, "北京", "男")
    # 虚岁 = 2027-1990+1 = 38 → 大运 (36, 乙酉)；起运虚岁 6
    assert r.dayun[0][0] == 6
    assert cycle.dayun_step_for(r, 2027) == (36, "乙酉")   # 虚岁 38 → 乙酉步
    assert cycle.dayun_step_for(r, 2026) == (36, "乙酉")   # 虚岁 37 → 乙酉步
    assert cycle.dayun_step_for(r, 2002) == (6, "壬午")    # 虚岁 13 → 壬午步（6~15）
    assert cycle.dayun_step_for(r, 2025) == (36, "乙酉")   # 虚岁 36 → 乙酉步起点（36~45）


def test_format_cycle_card_sections():
    """结果卡片四要素齐：流年干支+十神 / 流月干支+十神 / 关注维度要点 / 吉凶月提示。"""
    r = BaziEngine().calculate(1990, 5, 20, 12, 0, "北京", "男")
    card = cycle.format_cycle_card(r, 2027, 0, ["财运"])
    for section in ("【流月流年】", "2027年流年：丁未（天河水）", "流年十神",
                    "未藏", "所在大运：乙酉（36岁起", "关注维度：财运",
                    "2027年流月：", "吉凶月提示：最吉", "最需留意"):
        assert section in card, f"卡片缺: {section}\n{card}"
    assert "偏财引动" in card          # 财运维度命中 未藏本气偏财
    assert "食神" in card and "比肩" in card
    # 单月形态：目标月 6 → 单月行 + 全年 12 月一览省略
    card6 = cycle.format_cycle_card(r, 2027, 6, [])
    assert "6月单月：丙午" in card6
    assert "2027年流月：" not in card6


def test_focus_summary_miss():
    """维度未命中 → 平稳之年文案（不臆造吉凶）。"""
    # 甲日主对 丙午：丙=食神、午藏丁己=伤官/正财——事业集合（官杀印比劫）全不显
    out = cycle.focus_summary("甲", "丙午", ["事业"])
    assert out[0].startswith("事业：") and "平稳之年" in out[0]


# ============================================================
# e2e（handler 执行器 + 工具循环链路）
# ============================================================

def _make_bot():
    """轻量 MessageHandler（__new__ 避开 __init__ 装配），注入真实引擎。"""
    from src.bot.handler import MessageHandler
    bot = MessageHandler.__new__(MessageHandler)
    bot.engine = BaziEngine()
    return bot


def test_cycle_execute_tool_call_e2e():
    """e2e：JSON 工单四键 → 校验 → 序列化 → 真实排盘 + 流月流年卡片。"""
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

    bot = _make_bot()
    cap = CAPABILITY_BY_NAME["流月流年"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("fortune_cycle")
    try:
        bind_executors({"fortune_cycle": lambda p, user_id="", user_question="":
                        bot._tool_fortune_cycle(p, user_id)}, {})
        r = bot._execute_tool_call("流月流年", {
            "birth": "1990年5月20日 午时 北京 男",
            "year": "2027", "month": "6", "focus": "财运",
        }, "u1")
        assert r.ok is True
        # 1990 庚午 乙木日主 → 2027 丁未（天河水）；丁=食神；未藏 己偏财 丁食神 乙比肩
        assert "2027年流年：丁未（天河水）" in r.text
        assert "流年十神：食神" in r.text
        assert "己 偏财" in r.text and "乙 比肩" in r.text
        assert "6月单月：丙午" in r.text
        assert "关注维度：财运" in r.text and "偏财引动" in r.text
        assert "吉凶月提示" in r.text
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("fortune_cycle", None)
        else:
            reg._tool_executors["fortune_cycle"] = orig_ex


def test_cycle_defaults_e2e():
    """e2e：只给 birth → 目标年份/月份默认今年/本月（标准库 datetime）。"""
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

    bot = _make_bot()
    cap = CAPABILITY_BY_NAME["流月流年"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("fortune_cycle")
    try:
        bind_executors({"fortune_cycle": lambda p, user_id="", user_question="":
                        bot._tool_fortune_cycle(p, user_id)}, {})
        r = bot._execute_tool_call(
            "流月流年", {"birth": "1990年5月20日 午时 北京 男"}, "u1")
        assert r.ok is True
        from src.tools.fortune_cycle import default_targets
        now_year, now_month = default_targets()
        assert f"{now_year}年流年：" in r.text
        assert "2027年流年：" not in r.text
        assert "吉凶月提示" in r.text
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("fortune_cycle", None)
        else:
            reg._tool_executors["fortune_cycle"] = orig_ex


def test_cycle_needs_info_paths():
    """信息不全 → needs_info 澄清，不调引擎不算成功。"""
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

    bot = _make_bot()
    cap = CAPABILITY_BY_NAME["流月流年"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("fortune_cycle")
    try:
        bind_executors({"fortune_cycle": lambda p, user_id="", user_question="":
                        bot._tool_fortune_cycle(p, user_id)}, {})
        # 全无出生信息 → 追问（含 birth 键名示例）
        r0 = bot._execute_tool_call("流月流年", "只看2027年财运", "u1")
        assert r0.ok is False and r0.needs_info is True
        assert "出生信息" in r0.text and "birth" in r0.text
        # 出生信息可拆出但解析失败（日期非法，5月32日）→ 点名出生信息（含原样回显）
        r1 = bot._execute_tool_call("流月流年", {"birth": "1990年5月32日 午时 北京 男"}, "u1")
        assert r1.ok is False and r1.needs_info is True
        assert "出生信息" in r1.text and "1990年5月32日" in r1.text
        # 目标年份非法 → 点名 year（不默认静默纠正）
        r2 = bot._execute_tool_call("流月流年", {
            "birth": "1990年5月20日 午时 北京 男", "year": "abc"}, "u1")
        assert r2.ok is False and r2.needs_info is True
        assert "year" in r2.text
        # 目标年份越界 → 点名 year
        r2b = bot._execute_tool_call("流月流年", {
            "birth": "1990年5月20日 午时 北京 男", "year": "2500"}, "u1")
        assert r2b.ok is False and r2b.needs_info is True and "year" in r2b.text
        # 目标月份非法 → 点名 month
        r3 = bot._execute_tool_call("流月流年", {
            "birth": "1990年5月20日 午时 北京 男", "month": "13"}, "u1")
        assert r3.ok is False and r3.needs_info is True
        assert "month" in r3.text
        # 结构化工单缺 birth 键 → 框架校验拦截（参数不合法，不执行）
        r4 = bot._execute_tool_call("流月流年", {"year": "2027"}, "u1")
        assert r4.ok is False and "参数不合法" in r4.text and "birth" in r4.text
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("fortune_cycle", None)
        else:
            reg._tool_executors["fortune_cycle"] = orig_ex


def test_cycle_chain_reachability_json_workorder():
    """工具循环链路可达（D5 同型回归）：reply 内 JSON 工单 fortune_cycle →
    真实执行 → 结果注入 LLM 消息 → 最终文本回复。"""
    from unittest.mock import Mock, patch
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

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

    cap = CAPABILITY_BY_NAME["流月流年"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("fortune_cycle")
    seen_msgs: list = []

    def fake_messages(api_key, messages, model=None, max_tokens=0,
                      temperature=0.0, timeout=0.0, tools=None,
                      tool_choice=None, **kwargs):
        seen_msgs.append(messages)
        return {"stop_reason": "end_turn",
                "content": [{"type": "text", "text": "2027丁未年财星入局，宜稳中求进。"}]}

    try:
        bind_executors({"fortune_cycle": lambda p, user_id="", user_question="":
                        bot._tool_fortune_cycle(p, user_id)}, {})
        with patch("src.llm.client.deepseek_anthropic_messages",
                   side_effect=fake_messages):
            out = bot._run_tool_loop(
                "看看2027年财运", "u1",
                '好的，我来推演。<tool_calls>'
                '[{"tool": "fortune_cycle", "params": {"birth": "1990年5月20日 午时 北京 男", '
                '"year": "2027", "focus": "财运"}}]</tool_calls>')
        assert out == "2027丁未年财星入局，宜稳中求进。"
        tail = seen_msgs[0][-1]["content"]
        assert '"tool": "流月流年"' in tail and '"ok": true' in tail
        assert "2027年流年：丁未" in tail and "吉凶月提示" in tail
        assert bot._tool_logs["u1"]["calls"][0]["type"] == "流月流年"
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("fortune_cycle", None)
        else:
            reg._tool_executors["fortune_cycle"] = orig_ex


def test_cycle_native_tool_use_loop():
    """原生 tool_use 链路：JSON 工单先执行 → 首轮 LLM 出同参 fortune_cycle 原生块
    → 去重跳过执行、复用结果回传 tool_result。"""
    from unittest.mock import Mock, patch
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME

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

    cap = CAPABILITY_BY_NAME["流月流年"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("fortune_cycle")
    seen_round2: list = []
    called: list = []

    def fake_messages(api_key, messages, model=None, max_tokens=0,
                      temperature=0.0, timeout=0.0, tools=None,
                      tool_choice=None, **kwargs):
        if not hasattr(fake_messages, "turn"):
            fake_messages.turn = 0
        fake_messages.turn += 1
        if fake_messages.turn == 1:
            assert tools and any(t["name"] == "fortune_cycle" for t in tools)
            return {"stop_reason": "tool_use",
                    "content": [{"type": "tool_use", "id": "tu_c1",
                                 "name": "fortune_cycle",
                                 "input": {"birth": "1990年5月20日 午时 北京 男",
                                           "year": "2027"}}]}
        seen_round2.append(messages[-1])
        return {"stop_reason": "end_turn",
                "content": [{"type": "text", "text": "丁未年财运稳中有进。"}]}

    def spy(params, user_id="", user_question=""):
        called.append(params)
        return bot._tool_fortune_cycle(params, user_id)

    try:
        bind_executors({"fortune_cycle": spy}, {})
        with patch("src.llm.client.deepseek_anthropic_messages",
                   side_effect=fake_messages):
            out = bot._run_tool_loop(
                "看看2027年运势", "u1",
                '好的，我来推演。<tool_calls>'
                '[{"tool": "fortune_cycle", "params": {"birth": "1990年5月20日 午时 北京 男", '
                '"year": "2027"}}]</tool_calls>')
        assert out == "丁未年财运稳中有进。"
        assert fake_messages.turn == 2
        # 同参原生块被去重跳过（review I-1）：仅工单执行 1 次
        assert len(called) == 1
        last = seen_round2[0]
        assert last["role"] == "user"
        results = last["content"]
        assert [r["type"] for r in results] == ["tool_result"]
        assert results[0]["tool_use_id"] == "tu_c1"
        assert "2027年流年：丁未" in results[0]["content"]
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("fortune_cycle", None)
        else:
            reg._tool_executors["fortune_cycle"] = orig_ex
