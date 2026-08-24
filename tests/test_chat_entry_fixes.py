"""对话入口三个实测缺陷修复测试（D7 / D8 / D9，2026-08-24 生产 8767 实测立案）。

D7 长句口语生辰提取失败：
    "我1999年阴历三月28出生，男，接近11点出生，在吉林省长春市榆树市出生的，
     我目前看我的八字身弱不担财，我想破这个"
    —— 用户话里信息齐全（阴历三月28/男/接近11点/榆树市）却回复
    「得先有准确的出生信息才能排盘」。要求：宁可多步确认也不放弃解析。
D8 建档后问事仍引导建档：
    排盘成功已落库（chart_records）的 user 问"我该佩戴什么东西能让身弱变强"
    → 仍回建档引导。要求：persons 或 chart_records 任一有数据即走档案快路径。
D9 无档案问事无知识兜底：
    "我该佩戴什么东西能让身弱变强"/"有没有几天几周快速发财的办法" →
    千篇一律"先提供生辰"。要求：先答通用知识（含古籍可查向量库），
    末尾附"告诉我生辰可精确分析"式引导。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

from unittest.mock import Mock  # noqa: E402

from src.bot.handler import MessageHandler  # noqa: E402

# D7 生产实测原句（一字不改）
D7_MSG = ("我1999年阴历三月28出生，男，接近11点出生，"
          "在吉林省长春市榆树市出生的，我目前看我的八字身弱不担财，我想破这个")

# 对照组（标准格式，能正常排盘）
CONTROL_MSG = "1999年5月13日 10:55 榆树 男，帮我排盘看看"

# 建档引导特征文案（D7/D8 修复前都会出现）
_GUIDANCE_MARKERS = ("出生年月日", "出生信息", "生辰", "示例")


def _chart_dao(tmp_path):
    from src.storage.chart_dao import ChartDAO
    return ChartDAO(str(tmp_path / "c.db"))


def _full_handler(tmp_path):
    """object.__new__ 手工装配（与 tests/test_chart_reuse.py 同风格）。

    llm.api_key=""：_quick_flash/_gen_info_collection_prompt 走固定文案
    兜底（零网络、零 LLM 调用，确定性）；retriever 为 Mock：D9 知识兜底
    走静态知识（Mock 返回值非 list → 不触发 LLM 合成路径）。
    """
    h = object.__new__(MessageHandler)
    h.dao = Mock(db_path=str(tmp_path / "u.db"))
    h.dao.get_user_bazi.return_value = None
    h.chart_dao = _chart_dao(tmp_path)
    h.llm = Mock(api_key="")
    h.retriever = Mock()
    h.engine = Mock()
    h.memory = None
    h.memory_system = None
    h.session_dao = None
    h.member_dao = None
    h.preference_dao = None
    h.cache = Mock()
    h.cache.get.return_value = None
    h._deep_night = {}
    h._downgraded = {}
    h._tool_logs = {}
    h._citations = {}
    h._analysis_facts = {}
    h._pregen_instant = {}
    h._pregen_pool = Mock()
    return h


# ================================================================
# D7 长句口语生辰提取
# ================================================================

def test_d7_extract_spoken_lunar_message():
    """生产原句：阴历三月28 → 转阳历 1999-05-13；接近11点 → 10:55；
    嵌套城市 → 榆树市；性别 → 男。全部提取成功（不再返回 None）。"""
    h = object.__new__(MessageHandler)
    info = h._extract_bazi_info(D7_MSG)
    assert info is not None, "口语长句生辰必须可提取，不得放弃解析"
    year, month, day, hour, minute, city, gender = info
    # 阴历 1999-03-28 = 阳历 1999-05-13（与对照组同一生日）
    assert (year, month, day) == (1999, 5, 13), info
    # 模糊时辰"接近11点"→ 边界取 10:55（先排盘，回复后确认）
    assert (hour, minute) == (10, 55), info
    # 嵌套城市"吉林省长春市榆树市"→ 最内层"榆树市"
    assert city == "榆树市", info
    assert gender == "男", info


def test_d7_control_format_still_parses():
    """对照组标准格式不回归。"""
    h = object.__new__(MessageHandler)
    info = h._extract_bazi_info(CONTROL_MSG)
    assert info is not None
    assert (info[0], info[1], info[2]) == (1999, 5, 13), info
    assert info[3] == 10 and info[4] == 55, info


def test_d7_extract_fuzzy_time_boundary():
    """模糊时辰边界策略：接近/将近/快到→10:55；大约/大概/约→11:00。"""
    h = object.__new__(MessageHandler)
    cases = [
        ("1999年3月28日 接近11点 北京 男", 10, 55),
        ("1999年3月28日 将近11点 北京 男", 10, 55),
        ("1999年3月28日 快到11点 北京 男", 10, 55),
        ("1999年3月28日 大约11点 北京 女", 11, 0),
        ("1999年3月28日 大概11点 北京 女", 11, 0),
        ("1999年3月28日 约11点 北京 女", 11, 0),
    ]
    for msg, exp_h, exp_m in cases:
        info = h._extract_bazi_info(msg)
        assert info is not None, msg
        assert (info[3], info[4]) == (exp_h, exp_m), f"{msg} → {info}"


def test_d7_extract_lunar_variants():
    """农历变体：农历+中文数字日、阴历+阿拉伯日+号、闰月（无闰月年份按平月近似）。"""
    h = object.__new__(MessageHandler)
    # 农历三月初八 → 阳历 1999-04-23
    info = h._extract_bazi_info("我1999年农历三月初八生的，女")
    assert info is not None
    assert (info[0], info[1], info[2]) == (1999, 4, 23), info
    # 阴历三月28号
    info = h._extract_bazi_info("我1999年阴历三月28号出生 女")
    assert info is not None
    assert (info[1], info[2]) == (5, 13), info
    # 闰三月28（1999 无闰三月 → 平月近似，绝不传负月）
    info = h._extract_bazi_info("1999年闰三月28 子时 广州 男")
    assert info is not None
    assert info[1] == 5 and info[2] == 13 and info[3] == 23, info
    # 2004 真有闰二月 → 按闰月转阳历
    info = h._extract_bazi_info("2004年闰二月28 女")
    assert info is not None
    assert (info[1], info[2]) == (4, 17), info


def test_d7_nested_city_keeps_innermost():
    """嵌套城市：省市区市 → 最内层；带市后缀单城市不变；无城市默认北京。"""
    h = object.__new__(MessageHandler)
    info = h._extract_bazi_info("1990年5月20日 15点 在湖南省长沙市宁乡市出生的 男")
    assert info is not None
    assert info[5] == "宁乡市", info
    info = h._extract_bazi_info("1990年5月20日 15点 广州市 男")
    assert info is not None
    assert info[5] == "广州市", info
    info = h._extract_bazi_info("1990年5月20日 15点 男")
    assert info is not None
    assert info[5] == "北京", info


def test_d7_handler_routes_to_analysis_not_guidance(tmp_path):
    """D7 原句走 _handle_bazi：命中解析 → 直达分析（引擎收到转阳历+边界时辰+
    内层城市），绝不返回"先提供出生信息"式引导文案。"""
    h = _full_handler(tmp_path)
    h._do_bazi_analysis = Mock(return_value="✅ 已排盘分析完成")
    result = h._handle_bazi(D7_MSG, "u7")
    assert "已排盘分析完成" in result
    assert not any(m in result for m in _GUIDANCE_MARKERS), result
    assert h._do_bazi_analysis.call_count == 1
    args = h._do_bazi_analysis.call_args.args
    assert args[0] == 1999 and args[1] == 5 and args[2] == 13, args  # 转阳历
    assert args[3] == 10 and args[4] == 55, args                     # 模糊时辰边界
    assert args[5] == "榆树市", args                                  # 内层城市
    assert args[6] == "男", args


def test_d7_fast_path_pattern_matches_lunar_message():
    """规则快判（_quick_intent/_rule_analyze 同源）：口语长句生辰必须命中
    BIRTH_DATE_PATTERN——降级/无 LLM 链路也能把完整出生信息判为 bazi 而非自由聊天。"""
    from src.engines.message_analyzer import MessageAnalyzer
    assert MessageAnalyzer.BIRTH_DATE_PATTERN.search(D7_MSG), D7_MSG
    h = object.__new__(MessageHandler)
    assert h._quick_intent(D7_MSG) == "bazi"


# ================================================================
# D8 建档后问事仍引导建档
# ================================================================

def test_d8_chart_records_profile_found(tmp_path):
    """chart_records 已排盘落库 → _get_user_birth_profile 能取到出生档案
    （users.bazi_info / persons 均无数据时也不判"无档案"）。"""
    h = _full_handler(tmp_path)
    h.chart_dao.save_chart(
        "u8", None,
        {"year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 30,
         "city": "榆树", "gender": "男", "calendar": "solar"},
        {"bazi": ["庚午", "辛巳", "甲申", "壬申"], "day_master": "甲"})
    saved = h._get_user_birth_profile("u8")
    assert saved is not None
    assert saved["year"] == 1990 and saved["month"] == 5 and saved["day"] == 20
    assert saved["hour"] == 15 and saved["minute"] == 30
    assert saved["city"] == "榆树" and saved["gender"] == "男"
    assert saved.get("bazi") == ["庚午", "辛巳", "甲申", "壬申"]  # 供复用确认


def test_d8_persons_profile_found(tmp_path):
    """persons 有出生数据 → 同样视为有档案（不回归 Brief 档案兜底）。"""
    from src.storage.person_dao import PersonDAO
    h = _full_handler(tmp_path)
    pdao = PersonDAO(h.dao.db_path)
    pdao.create_person(
        "u8p", "我", "自己", is_default=True,
        birth={"birth_year": 1988, "birth_month": 6, "birth_day": 15,
               "birth_hour": 14, "birth_minute": 0, "gender": "女",
               "city": "成都", "calendar": "solar"})
    saved = h._get_user_birth_profile("u8p")
    assert saved is not None
    assert (saved["year"], saved["month"], saved["day"]) == (1988, 6, 15)
    assert saved["gender"] == "女" and saved["city"] == "成都"


def test_d8_handle_bazi_uses_chart_profile_not_guidance(tmp_path):
    """D8 原句（排盘已落库 chart_records 后问佩戴）→ 走档案快路径直达分析，
    不再返回建档引导文案。"""
    h = _full_handler(tmp_path)
    h.chart_dao.save_chart(
        "u8b", None,
        {"year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 30,
         "city": "榆树", "gender": "男", "calendar": "solar"},
        {"bazi": ["庚午", "辛巳", "甲申", "壬申"], "day_master": "甲"})
    h._do_bazi_analysis = Mock(return_value="✅ 已按档案分析完成")
    result = h._handle_bazi("我该佩戴什么东西能让身弱变强", "u8b")
    assert "已按档案分析完成" in result
    assert not any(m in result for m in _GUIDANCE_MARKERS), result
    assert h._do_bazi_analysis.call_count == 1
    args = h._do_bazi_analysis.call_args.args
    assert args[0] == 1990 and args[1] == 5 and args[2] == 20, args
    assert args[3] == 15 and args[4] == 30, args
    assert args[5] == "榆树" and args[6] == "男", args


# ================================================================
# D9 无档案问事：先答通用知识，再要档案
# ================================================================

def test_d9_no_archive_wear_question_knowledge_first(tmp_path):
    """无档案 + 佩戴问事 → 先给实质知识回答（佩戴/五行调理），
    末尾附建档引导——先答问题，再要档案。"""
    h = _full_handler(tmp_path)
    result = h._handle_bazi("我该佩戴什么东西能让身弱变强", "u9")
    # 实质内容（非纯引导）
    assert "佩戴" in result and "五行" in result, result
    assert "喜用神" in result, result
    # 末尾建档引导
    assert any(m in result for m in _GUIDANCE_MARKERS), result
    # 引导在知识之后（先答问题再要档案）
    first_guidance = min(result.find(m) for m in _GUIDANCE_MARKERS
                         if m in result)
    assert result.index("佩戴") < first_guidance, result


def test_d9_no_archive_rich_question_knowledge_first(tmp_path):
    """无档案 + 快速发财问事 → 实质财运知识回答（含身弱不担财），附引导。"""
    h = _full_handler(tmp_path)
    result = h._handle_bazi("有没有几天几周快速发财的办法", "u9b")
    assert "身弱不担财" in result and "偏财" in result, result
    assert any(m in result for m in _GUIDANCE_MARKERS), result


def test_d9_plain_bazi_request_keeps_guidance(tmp_path):
    """无档案 + 纯排盘请求（"帮我看看八字"）→ 保持原引导文案（不误伤）。"""
    h = _full_handler(tmp_path)
    result = h._handle_bazi("帮我看看八字", "u9c")
    assert any(m in result for m in _GUIDANCE_MARKERS), result
    # 纯引导请求不应被知识回答顶替（回归 test_process_bazi_missing_info_asks 行为）
    assert "佩戴" not in result and "身弱不担财" not in result, result


def test_d9_knowledge_available_without_retriever_or_llm(tmp_path):
    """静态知识兜底确定性：retriever/llm 均不可用时也必须给实质内容（绝不退回纯引导）。"""
    h = _full_handler(tmp_path)
    h.retriever.search.side_effect = RuntimeError("vector db down")  # 检索异常
    h.llm = None                                                     # 无 LLM
    result = h._handle_bazi("我该戴什么饰品", "u9d")
    assert "五行" in result and "佩戴" in result, result
    assert any(m in result for m in _GUIDANCE_MARKERS), result
