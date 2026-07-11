"""Tests for bot message handling."""
from unittest.mock import Mock, MagicMock

from src.bot.handler import MessageHandler, INTENT_KEYWORDS
from src.bot.formatter import split_long_message, format_greeting, format_error, format_loading


# ── Intent detection ──────────────────────────────────────────────────

def test_intent_detection_bazi():
    """测试八字意图识别"""
    def detect(msg):
        for intent, keywords in INTENT_KEYWORDS.items():
            for kw in keywords:
                if kw in msg:
                    return intent
        return None

    assert detect("帮我看看八字") == "bazi"
    assert detect("算算命") == "bazi"
    assert detect("看看运势") == "bazi"
    assert detect("你好") is None
    assert detect("今天天气怎么样") is None


def test_intent_detection_other_intents():
    """测试其他意图识别"""
    def detect(msg):
        for intent, keywords in INTENT_KEYWORDS.items():
            for kw in keywords:
                if kw in msg:
                    return intent
        return None

    assert detect("紫微斗数怎么排") == "ziwei"
    assert detect("起一卦") == "liuyao"
    assert detect("看看家居风水") == "fengshui"
    assert detect("结婚选日子") == "zeri"
    assert detect("看看手相") == "mianxiang"
    assert detect("奇门遁甲") == "qimen"
    assert detect("给宝宝起名") == "xingming"
    assert detect("看婚姻配对") == "hehun"


# ── Formatter ─────────────────────────────────────────────────────────

def test_split_long_message():
    text = "这是一条测试消息\n" * 100
    parts = split_long_message(text, max_len=500)
    for p in parts:
        assert len(p) <= 500
    assert len(parts) > 1


def test_split_long_message_short():
    """短消息不应被切分"""
    parts = split_long_message("短消息", max_len=500)
    assert parts == ["短消息"]


def test_split_long_message_empty_line():
    """空行处理"""
    parts = split_long_message("第一行\n\n第三行", max_len=500)
    assert len(parts) == 1


def test_format_greeting():
    greeting = format_greeting()
    assert "命理助手" in greeting
    assert "八字" in greeting
    assert "紫微" in greeting or "斗数" in greeting


def test_format_error():
    assert format_error("出错了") == "⚠️ 出错了"


def test_format_loading():
    assert "排盘" in format_loading()


# ── Handler process() ─────────────────────────────────────────────────

def make_mock_handler():
    """Helper: create a MessageHandler with all mocks."""
    return MessageHandler(
        engine=Mock(),
        retriever=Mock(),
        llm=Mock(),
        dao=Mock(),
    )


def test_process_no_intent_returns_help():
    """无意图时返回帮助信息"""
    handler = make_mock_handler()
    result = handler.process("你好", "user123")
    assert "命理助手" in result


def test_process_unimplemented_intent():
    """已识别但未实现的意图返回开发中提示"""
    handler = make_mock_handler()
    result = handler.process("看看家居风水", "user123")
    assert "模块开发中" in result
    assert "fengshui" in result


def test_process_bazi_missing_info_asks():
    """八字意图但无出生信息 - 询问提供信息"""
    handler = make_mock_handler()
    handler.dao.get_user_bazi.return_value = None  # No saved data
    result = handler.process("帮我看看八字", "user123")
    assert "出生" in result or "示例" in result


def test_process_bazi_missing_info_uses_saved():
    """八字意图无信息，但有已保存数据"""
    mock_dao = Mock()
    mock_dao.get_user_bazi.return_value = {
        "year": 1990, "month": 5, "day": 20,
        "hour": 15, "minute": 0, "city": "北京", "gender": "男",
    }

    mock_engine = Mock()
    mock_result = Mock(spec=["bazi", "day_master", "wuxing", "shishen", "dayun",
                              "liunian", "geju", "yongshen", "shensha", "nayin"])
    mock_result.bazi = ["庚午", "辛巳", "乙酉", "甲申"]
    mock_result.day_master = "乙木"
    mock_engine.calculate.return_value = mock_result

    mock_retriever = Mock()
    mock_retriever.search.return_value = []

    mock_llm = Mock()
    mock_analysis = Mock()
    mock_analysis.response = "您的八字分析结果：日主乙木..."
    mock_llm.analyze.return_value = mock_analysis

    handler = MessageHandler(mock_engine, mock_retriever, mock_llm, mock_dao)
    result = handler.process("我的运势如何", "user123")

    assert "八字分析结果" in result
    mock_engine.calculate.assert_called_once_with(
        1990, 5, 20, 15, 0, "北京", "男",
    )
    mock_dao.save_user_bazi.assert_called_once()
    mock_dao.save_consultation.assert_called_once()
    mock_retriever.search.assert_called_once()
    mock_llm.analyze.assert_called_once()


def test_process_bazi_with_extracted_info():
    """带有出生信息的八字请求 - 完整流程"""
    mock_engine = Mock()
    mock_result = Mock(spec=["bazi", "day_master", "wuxing", "shishen", "dayun",
                              "liunian", "geju", "yongshen", "shensha", "nayin"])
    mock_result.bazi = ["庚午", "辛巳", "乙酉", "甲申"]
    mock_result.day_master = "乙木"
    mock_engine.calculate.return_value = mock_result

    mock_retriever = Mock()
    mock_retriever.search.return_value = []

    mock_llm = Mock()
    mock_analysis = Mock()
    mock_analysis.response = "您的八字分析结果：日主乙木..."
    mock_llm.analyze.return_value = mock_analysis

    mock_dao = Mock()

    handler = MessageHandler(mock_engine, mock_retriever, mock_llm, mock_dao)
    result = handler.process("帮我看看八字 1990年5月20日15点 北京 男", "user123")

    assert "八字分析结果" in result
    mock_engine.calculate.assert_called_once_with(
        1990, 5, 20, 15, 0, "北京", "男",
    )
    mock_dao.save_user_bazi.assert_called_once()
    mock_dao.save_consultation.assert_called_once()
    mock_retriever.search.assert_called_once()
    mock_llm.analyze.assert_called_once()


def test_detailed_bazi_intent_method():
    """测试 MessageHandler._detect_intent 方法"""
    handler = make_mock_handler()
    assert handler._detect_intent("帮我算八字") == "bazi"
    assert handler._detect_intent("看运势") == "bazi"
    assert handler._detect_intent("起一卦看看") == "liuyao"
    assert handler._detect_intent("奇门遁甲") == "qimen"
    assert handler._detect_intent("帮我改名字") == "xingming"
    assert handler._detect_intent("hello world") is None


# ── Issue 1: PM/AM 转换 ─────────────────────────────────────────────────

def test_extract_bazi_info_pm_conversion():
    """下午3点应转为15点"""
    handler = make_mock_handler()
    result = handler._extract_bazi_info("1990年5月20日 下午3点 北京 男")
    assert result is not None
    _, _, _, hour, minute, city, gender = result
    assert hour == 15, f"下午3点应转为15点，但得到{hour}"
    assert minute == 0
    assert gender == "男"


def test_extract_bazi_info_pm_with_minutes():
    """下午3点30分应转为15点30分"""
    handler = make_mock_handler()
    result = handler._extract_bazi_info("1990年5月20日 下午3点30分 北京 女")
    assert result is not None
    _, _, _, hour, minute, _, gender = result
    assert hour == 15, f"下午3应转为15，但得到{hour}"
    assert minute == 30


def test_extract_bazi_info_evening():
    """晚上9点应转为21点"""
    handler = make_mock_handler()
    result = handler._extract_bazi_info("1990年5月20日 晚上9点 北京 男")
    assert result is not None
    _, _, _, hour, _, _, _ = result
    assert hour == 21, f"晚上9点应转为21点，但得到{hour}"


def test_extract_bazi_info_am_kept():
    """上午10点应保持10点"""
    handler = make_mock_handler()
    result = handler._extract_bazi_info("1990年5月20日 上午10点 北京 男")
    assert result is not None
    _, _, _, hour, _, _, _ = result
    assert hour == 10, f"上午10应保持10，但得到{hour}"


def test_extract_bazi_info_am_12():
    """上午12点（中午）应保持12点"""
    handler = make_mock_handler()
    result = handler._extract_bazi_info("1990年5月20日 上午12点 北京 男")
    assert result is not None
    _, _, _, hour, _, _, _ = result
    assert hour == 12, f"上午12应保持12，但得到{hour}"


def test_extract_bazi_info_pm_12():
    """下午12点（凌晨）应转为24点（即24点/0点）"""
    handler = make_mock_handler()
    result = handler._extract_bazi_info("1990年5月20日 下午12点 北京 男")
    assert result is not None
    _, _, _, hour, _, _, _ = result
    assert hour == 24, f"下午12应转为24，但得到{hour}"


# ── Issue 2: 城市提取 ─────────────────────────────────────────────────

def test_extract_bazi_info_city_without_suffix():
    """城市名称没有'市'后缀时仍能识别"""
    handler = make_mock_handler()
    result = handler._extract_bazi_info("1990年5月20日 15点 上海 男")
    assert result is not None
    _, _, _, _, _, city, _ = result
    assert city == "上海", f"应识别'上海'，但得到{city}"


def test_extract_bazi_info_city_shenzhen():
    """深圳（无后缀）应被识别"""
    handler = make_mock_handler()
    result = handler._extract_bazi_info("1990-05-20 15:00 深圳 女")
    assert result is not None
    _, _, _, _, _, city, _ = result
    assert city == "深圳", f"应识别'深圳'，但得到{city}"


def test_extract_bazi_info_city_with_suffix():
    """带市后缀的城市优先匹配"""
    handler = make_mock_handler()
    result = handler._extract_bazi_info("1990年5月20日 15点 广州市 男")
    assert result is not None
    _, _, _, _, _, city, _ = result
    assert city == "广州市", f"应识别'广州市'，但得到{city}"


def test_extract_bazi_info_city_fallback_default():
    """没有城市信息时默认北京"""
    handler = make_mock_handler()
    result = handler._extract_bazi_info("1990年5月20日 15点 男")
    assert result is not None
    _, _, _, _, _, city, _ = result
    assert city == "北京", f"默认应为北京，但得到{city}"


# ── Issue 4: 超长单行拆分 ─────────────────────────────────────────────

def test_split_long_message_overflow_line():
    """单行超过 max_len 时应被拆分为多段"""
    long_line = "测试消息 " * 100  # 400 chars
    parts = split_long_message(long_line, max_len=50)
    for p in parts:
        assert len(p) <= 50, f"分段[{p[:20]}...]长度{len(p)}超过50"
    assert len(parts) >= 2


def test_split_long_message_overflow_line_no_space():
    """无空格超长行应硬切"""
    line = "a" * 600
    parts = split_long_message(line, max_len=500)
    for p in parts:
        assert len(p) <= 500
    assert len(parts) >= 2


def test_split_long_message_mixed_normal_and_overflow():
    """混合正常行和超长行"""
    lines = ("短行\n" + "a" * 600 + "\n短行")
    parts = split_long_message(lines, max_len=500)
    for p in parts:
        assert len(p) <= 500
    assert len(parts) >= 2


# ── Greeting has all 9 categories ──────────────────────────────────────

def test_format_greeting_all_categories():
    """format_greeting 应包含全部9个分类"""
    greeting = format_greeting()
    categories = ["八字", "紫微", "占卜", "风水", "择日", "面相",
                  "奇门", "姓名", "合婚"]
    for cat in categories:
        assert cat in greeting, f"缺少分类: {cat}"


def test_help_message_all_categories():
    """_help_message 应包含全部9个分类"""
    handler = make_mock_handler()
    help_text = handler._help_message()
    categories = ["八字", "紫微", "占卜", "风水", "择日", "面相",
                  "奇门", "姓名", "合婚"]
    for cat in categories:
        assert cat in help_text, f"缺少分类: {cat}"
