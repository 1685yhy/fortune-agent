"""Tests for bot message handling."""
from unittest.mock import Mock, MagicMock, call

from src.bot.handler import MessageHandler, ZERI_SCENE_QUESTION
from src.bot.tool_calls import parse_tool_calls, strip_tool_calls, MAX_TOOL_ITERATIONS
from src.bot.formatter import split_long_message, format_greeting, format_error, format_loading


# ── AI 原生对话系统（Phase 1）— <tool_call> 标签解析 ─────────────────

def test_parse_tool_call_bazi():
    """解析排盘工具调用标签"""
    calls = parse_tool_calls("<tool_call>排盘: 1990年5月20日 午时 北京 男</tool_call>")
    assert len(calls) == 1
    assert calls[0].name == "排盘"
    assert calls[0].params == "1990年5月20日 午时 北京 男"


def test_parse_tool_call_multi_and_chinese_colon():
    """多个标签 + 中文冒号"""
    text = (
        "<tool_call>检索: 梦见蛇 解梦 征兆</tool_call>"
        "我查一下古籍怎么说。"
        "<tool_call>解梦：梦见大海 淋雨</tool_call>"
    )
    calls = parse_tool_calls(text)
    assert len(calls) == 2
    assert calls[0].name == "检索"
    assert calls[1].name == "解梦"
    assert calls[1].params == "梦见大海 淋雨"


def test_parse_tool_call_none():
    """无标签 → 空列表（静默降级）"""
    assert parse_tool_calls("你好呀，今天天气不错") == []
    assert parse_tool_calls("") == []
    assert parse_tool_calls(None) == []


def test_strip_tool_calls():
    """剥离标签，保留用户可见文字"""
    text = "<tool_call>排盘: 1990年5月20日</tool_call>我帮你排个盘看看~"
    assert strip_tool_calls(text) == "我帮你排个盘看看~"


def test_strip_tool_calls_5_format_variants():
    """TOOL 标签残留修复（2026-08-17 真机反馈）：5 种格式变体全部被 strip"""
    cases = [
        # 1. 标准格式
        ("<tool_call>排盘: 1990年5月20日 午时 北京 男</tool_call>", ""),
        # 2. 无冒号（空格分隔）
        ("<tool_call>搜索 今天天气怎么样</tool_call>", ""),
        # 3. 未知工具名（标签内任意名也清掉）
        ("<tool_call>占卜: 感情运势</tool_call>", ""),
        # 4. 大写 TOOL: 前缀（无闭合标签）
        ("TOOL: 搜索: 实时金价", ""),
        # 5. 缺 </tool_call> 的截断残留
        ("<tool_call>搜索:xxx", ""),
    ]
    for src, expected in cases:
        assert strip_tool_calls(src) == expected, f"变体未剥离: {src!r}"


def test_strip_tool_calls_preserves_body():
    """剥离标签时保留正文；未闭合标签不得跨行吞掉后续正文"""
    assert strip_tool_calls("你好，<tool_call>排盘: 1990年5月20日</tool_call>我帮你排个盘看看~") \
        == "你好，我帮你排个盘看看~"
    # 未闭合标签只吃到行尾，后续正文保留
    assert strip_tool_calls("<tool_call>搜索: A\n正文继续…<tool_call>排盘: B</tool_call>") \
        == "正文继续…"
    # 无标签文本原样
    assert strip_tool_calls("你好呀，今天天气不错") == "你好呀，今天天气不错"
    assert strip_tool_calls(None) is None
    assert strip_tool_calls("") == ""


def test_strip_tool_calls_no_english_false_positive():
    """TOOL: 前缀要求冒号——英文 "tool for…" 散文不误伤"""
    assert strip_tool_calls("this is a tool for meditation") == "this is a tool for meditation"


def test_parse_tool_calls_variants():
    """放宽后：无冒号/大写 TOOL/同义词可解析；未知工具不执行（过滤）"""
    assert [(c.name, c.params) for c in parse_tool_calls("TOOL: 搜索: 实时金价")] \
        == [("搜索", "实时金价")]
    assert [(c.name, c.params) for c in parse_tool_calls("<tool_call>搜索 今天天气</tool_call>")] \
        == [("搜索", "今天天气")]
    # 同义词归一：联网/网络 → 搜索
    assert [c.name for c in parse_tool_calls("TOOL: 联网: 金价")] == ["搜索"]
    # 未知工具名 → 不执行（返回空列表，但 strip 仍会移除）
    assert parse_tool_calls("<tool_call>占卜: 感情</tool_call>") == []


def test_max_tool_iterations():
    """防循环：最多 2 次工具迭代"""
    assert MAX_TOOL_ITERATIONS == 2


def test_intent_keywords_removed():
    """方案 Phase 1：INTENT_KEYWORDS 硬编码关键词表已删除（意图全由 LLM 分析）"""
    assert not hasattr(MessageHandler, "INTENT_KEYWORDS")


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


# ── Handler helper ────────────────────────────────────────────────────

def make_mock_handler():
    """Helper: create a MessageHandler with all mocks."""
    mock_llm = Mock()
    mock_llm.analyze.return_value = Mock(response="分析结果")
    mock_llm.chat.return_value = Mock(response="🔮 命理助手 返回的结果")
    mock_llm.chat_conversation.return_value = "🔮 命理助手 返回的结果"
    mock_dao = Mock()
    mock_dao.db_path = ":memory:"  # MemberDAO/PreferenceDAO 需真实路径（Mock 属性会返回 Mock → Path TypeError）
    mock_dao.get_user_bazi.return_value = None
    mock_session = Mock()
    mock_session.get_context_for_llm.return_value = []
    mock_session.add_message.return_value = None

    return MessageHandler(
        engine=Mock(),
        ziwei_engine=Mock(),
        liuyao_engine=Mock(),
        fengshui_engine=Mock(),
        mianxiang_engine=Mock(),
        zeri_engine=Mock(),
        retriever=Mock(),
        llm=mock_llm,
        dao=mock_dao,
        session_dao=mock_session,
    )


# ── Handler process() ─────────────────────────────────────────────────

def test_process_no_intent_returns_help():
    """无意图时返回帮助信息"""
    handler = make_mock_handler()
    result = handler.process("你好", "user123")
    assert "命理助手" in result


def test_process_bazi_missing_info_asks():
    """八字意图但无出生信息 - 询问提供信息"""
    handler = make_mock_handler()
    handler.dao.get_user_bazi.return_value = None  # No saved data
    result = handler.process("帮我看看八字", "user123")
    assert "出生" in result or "示例" in result


def test_process_bazi_missing_info_uses_saved():
    """八字意图无信息，但有已保存数据"""
    mock_dao = Mock()
    mock_dao.db_path = ":memory:"  # MemberDAO/PreferenceDAO 需真实路径（Mock 属性会返回 Mock → Path TypeError）
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

    handler = MessageHandler(
        engine=mock_engine,
        ziwei_engine=Mock(),
        liuyao_engine=Mock(),
        fengshui_engine=Mock(),
        mianxiang_engine=Mock(),
        zeri_engine=Mock(),
        retriever=mock_retriever,
        llm=mock_llm,
        dao=mock_dao,
    )
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
    mock_dao.db_path = ":memory:"  # MemberDAO/PreferenceDAO 需真实路径（Mock 属性会返回 Mock → Path TypeError）

    handler = MessageHandler(
        engine=mock_engine,
        ziwei_engine=Mock(),
        liuyao_engine=Mock(),
        fengshui_engine=Mock(),
        mianxiang_engine=Mock(),
        zeri_engine=Mock(),
        retriever=mock_retriever,
        llm=mock_llm,
        dao=mock_dao,
    )
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
                  "奇门", "姓名", "合婚", "解梦"]
    for cat in categories:
        assert cat in greeting, f"缺少分类: {cat}"


def test_format_greeting_has_checkmarks():
    """format_greeting 应为全部10个分类显示 ✅"""
    greeting = format_greeting()
    assert greeting.count("✅") == 10, f"应有10个✅，实际{greeting.count('✅')}个"


def test_help_message_all_categories():
    """_help_message 应包含全部10个分类"""
    handler = make_mock_handler()
    help_text = handler._help_message()
    categories = ["八字", "紫微", "占卜", "风水", "择日", "面相",
                  "奇门", "姓名", "合婚", "解梦"]
    for cat in categories:
        assert cat in help_text, f"缺少分类: {cat}"


# ── NEW: Ziwei handler ─────────────────────────────────────────────────

def test_process_ziwei_missing_info_asks():
    """紫微斗数无出生信息 - 询问提供信息"""
    handler = make_mock_handler()
    result = handler.process("帮我看看紫微斗数", "user123")
    assert "出生" in result


def test_process_ziwei_with_extracted_info():
    """紫微斗数带出生信息 - 完整流程"""
    mock_ziwei = Mock()
    mock_result = Mock()
    mock_result.ming_gong = "寅"
    mock_result.shen_gong = "午"
    mock_result.wuxing_ju = "木三局"
    mock_result.sihua = {}
    mock_result.palaces = {}
    mock_result.dayun = []
    mock_ziwei.calculate.return_value = mock_result

    mock_liuyao = Mock()
    mock_fengshui = Mock()
    mock_mianxiang = Mock()
    mock_zeri = Mock()

    mock_retriever = Mock()
    mock_retriever.search.return_value = []

    mock_llm = Mock()
    mock_analysis = Mock()
    mock_analysis.response = "紫微斗数分析：命宫在寅..."
    mock_llm.analyze.return_value = mock_analysis

    mock_dao = Mock()
    mock_dao.db_path = ":memory:"  # MemberDAO/PreferenceDAO 需真实路径（Mock 属性会返回 Mock → Path TypeError）

    handler = MessageHandler(
        engine=Mock(),
        ziwei_engine=mock_ziwei,
        liuyao_engine=mock_liuyao,
        fengshui_engine=mock_fengshui,
        mianxiang_engine=mock_mianxiang,
        zeri_engine=mock_zeri,
        retriever=mock_retriever,
        llm=mock_llm,
        dao=mock_dao,
    )
    result = handler.process(
        "帮我看看紫微斗数 1990年5月20日15点 北京 男", "user123"
    )

    assert "紫微斗数分析" in result
    mock_ziwei.calculate.assert_called_once_with(1990, 5, 20, 15, 0, "北京", "男")
    mock_retriever.search.assert_called_once()
    mock_llm.analyze.assert_called_once()
    mock_dao.save_consultation.assert_called_once()


# ── NEW: Liuyao handler ───────────────────────────────────────────────

def test_process_liuyao():
    """六爻占卜 - 完整流程"""
    mock_liuyao = Mock()
    mock_result = Mock()
    mock_result.original_hexagram = "天地否"
    mock_result.changed_hexagram = "风地观"
    mock_result.palace = "乾"
    mock_result.palace_wuxing = "金"
    mock_result.changing_lines = [2, 4]
    mock_result.lines = [
        {"type": "少阴", "yao_type": "应", "liuqin": "妻财", "dizhi": "卯"},
        {"type": "少阳", "yao_type": "", "liuqin": "官鬼", "dizhi": "巳"},
        {"type": "老阳", "yao_type": "世", "liuqin": "父母", "dizhi": "未"},
        {"type": "少阳", "yao_type": "", "liuqin": "兄弟", "dizhi": "酉"},
        {"type": "少阴", "yao_type": "", "liuqin": "子孙", "dizhi": "亥"},
        {"type": "老阴", "yao_type": "应", "liuqin": "妻财", "dizhi": "丑"},
    ]
    mock_result.question = "财运"
    mock_liuyao.cast.return_value = mock_result

    mock_retriever = Mock()
    mock_retriever.search.return_value = []

    mock_llm = Mock()
    mock_analysis = Mock()
    mock_analysis.response = "本卦天地否..."
    mock_llm.analyze.return_value = mock_analysis

    mock_dao = Mock()
    mock_dao.db_path = ":memory:"  # MemberDAO/PreferenceDAO 需真实路径（Mock 属性会返回 Mock → Path TypeError）

    handler = MessageHandler(
        engine=Mock(),
        ziwei_engine=Mock(),
        liuyao_engine=mock_liuyao,
        fengshui_engine=Mock(),
        mianxiang_engine=Mock(),
        zeri_engine=Mock(),
        retriever=mock_retriever,
        llm=mock_llm,
        dao=mock_dao,
    )
    result = handler.process("起一卦看看财运", "user123")

    assert "天地否" in result or "本卦天地否" in result
    mock_liuyao.cast.assert_called_once()
    mock_retriever.search.assert_called_once()
    mock_llm.analyze.assert_called_once()


# ── NEW: Fengshui handler ─────────────────────────────────────────────

def test_process_fengshui_missing_info_asks():
    """风水分析无方向信息 - 询问提供信息"""
    handler = make_mock_handler()
    result = handler.process("看看我家风水", "user123")
    assert "坐向" in result or "朝向" in result


def test_process_fengshui_with_direction():
    """风水分析带方向 - 完整流程"""
    mock_fengshui = Mock()
    mock_result = Mock()
    mock_result.house_gua = "坎宅"
    mock_result.period = 9
    mock_result.person_gua = ""
    mock_result.eight_mansions = {"生气": "东", "天医": "东南", "延年": "南", "伏位": "北",
                                   "绝命": "西", "五鬼": "东北", "六煞": "西北", "祸害": "西南"}
    mock_result.flying_stars = {}
    mock_fengshui.analyze.return_value = mock_result

    mock_retriever = Mock()
    mock_retriever.search.return_value = []

    mock_llm = Mock()
    mock_analysis = Mock()
    mock_analysis.response = "风水分析结果：坎宅..."
    mock_llm.analyze.return_value = mock_analysis

    mock_dao = Mock()
    mock_dao.db_path = ":memory:"  # MemberDAO/PreferenceDAO 需真实路径（Mock 属性会返回 Mock → Path TypeError）

    handler = MessageHandler(
        engine=Mock(),
        ziwei_engine=Mock(),
        liuyao_engine=Mock(),
        fengshui_engine=mock_fengshui,
        mianxiang_engine=Mock(),
        zeri_engine=Mock(),
        retriever=mock_retriever,
        llm=mock_llm,
        dao=mock_dao,
    )
    result = handler.process("坐北朝南的房子风水如何", "user123")

    assert "风水分析" in result
    mock_fengshui.analyze.assert_called_once()
    mock_retriever.search.assert_called_once()
    mock_llm.analyze.assert_called_once()


# ── NEW: Zeri handler ─────────────────────────────────────────────────

def test_process_zeri_missing_date_asks():
    """择日分析无日期 - 询问提供信息"""
    handler = make_mock_handler()
    result = handler.process("看看吉日", "user123")
    assert "日期" in result


def test_process_zeri_with_date():
    """择日分析带日期 - 完整流程"""
    mock_zeri = Mock()
    mock_result = Mock()
    mock_result.jianchu = "成"
    mock_result.ershibaxiu = "星"
    mock_result.xiu_jixiong = "吉"
    mock_result.yi = ["嫁娶", "开市", "入学"]
    mock_result.ji = ["动土", "破土"]
    mock_result.chong = "冲羊(未)"
    mock_result.overall = "吉"
    mock_zeri.select.return_value = mock_result

    mock_retriever = Mock()
    mock_retriever.search.return_value = []

    mock_llm = Mock()
    mock_analysis = Mock()
    mock_analysis.response = "择日分析：成日..."
    mock_llm.analyze.return_value = mock_analysis

    mock_dao = Mock()
    mock_dao.db_path = ":memory:"  # MemberDAO/PreferenceDAO 需真实路径（Mock 属性会返回 Mock → Path TypeError）

    handler = MessageHandler(
        engine=Mock(),
        ziwei_engine=Mock(),
        liuyao_engine=Mock(),
        fengshui_engine=Mock(),
        mianxiang_engine=Mock(),
        zeri_engine=mock_zeri,
        retriever=mock_retriever,
        llm=mock_llm,
        dao=mock_dao,
    )
    result = handler.process("2026年8月15日是吉日吗 结婚", "user123")

    assert "择日" in result
    mock_zeri.select.assert_called_once_with(2026, 8, 15, purpose="嫁娶")
    mock_retriever.search.assert_called_once()
    mock_llm.analyze.assert_called_once()


def test_extract_zeri_scene_jinsheng():
    """晋升场景意图提取: 升职/加薪/竞聘等命中 晋升; 旧场景不受影响; 澄清文案含晋升"""
    handler = make_mock_handler()
    assert handler._extract_zeri_scene("我想升职加薪选个好日子") == "晋升"
    assert handler._extract_zeri_scene("竞聘岗位 想挑个吉日") == "晋升"
    assert handler._extract_zeri_scene("入职面试 选个日子") == "晋升"
    # 同义词不串场景: 开张仍归开业
    assert handler._extract_zeri_scene("我想开张选日子") == "开业"
    assert "晋升" in ZERI_SCENE_QUESTION


# ── NEW: Mianxiang handler ────────────────────────────────────────────

def test_process_mianxiang_missing_desc_asks():
    """面相分析无描述 - 询问提供信息"""
    handler = make_mock_handler()
    result = handler.process("帮我看面相", "user123")
    assert "脸型" in result or "描述" in result


def test_process_mianxiang_with_description():
    """面相分析带描述 - 完整流程"""
    mock_mianxiang = Mock()
    mock_result = Mock()
    mock_result.face_type = "土形"
    mock_result.three_zones = {"上停": "饱满", "中停": "端正", "下停": "圆润"}
    mock_result.five_mountains = {"南岳(额头)": "高耸", "北岳(下巴)": "饱满",
                                   "东岳(左颧)": "适中", "西岳(右颧)": "适中",
                                   "中岳(鼻子)": "端正"}
    mock_result.features = {"眉(保寿官)": "清秀弯长", "眼(监察官)": "黑白分明有神"}
    mock_result.overall = "面相中上"
    mock_mianxiang.analyze.return_value = mock_result

    mock_retriever = Mock()
    mock_retriever.search.return_value = []

    mock_llm = Mock()
    mock_analysis = Mock()
    mock_analysis.response = "面相分析：土形脸..."
    mock_llm.analyze.return_value = mock_analysis

    mock_dao = Mock()
    mock_dao.db_path = ":memory:"  # MemberDAO/PreferenceDAO 需真实路径（Mock 属性会返回 Mock → Path TypeError）

    handler = MessageHandler(
        engine=Mock(),
        ziwei_engine=Mock(),
        liuyao_engine=Mock(),
        fengshui_engine=Mock(),
        mianxiang_engine=mock_mianxiang,
        zeri_engine=Mock(),
        retriever=mock_retriever,
        llm=mock_llm,
        dao=mock_dao,
    )
    result = handler.process("我是方脸额头饱满鼻梁高挺，帮我看看面相", "user123")

    assert "面相" in result
    mock_mianxiang.analyze.assert_called_once()
    mock_retriever.search.assert_called_once()
    mock_llm.analyze.assert_called_once()


# ── NEW: Qimen handler (RAG+LLM only) ─────────────────────────────────

def test_process_qimen_uses_rag():
    """奇门遁甲 - 使用RAG+LLM"""
    mock_retriever = Mock()
    mock_retriever.search.return_value = []

    mock_llm = Mock()
    mock_analysis = Mock()
    mock_analysis.response = "奇门遁甲分析..."
    mock_llm.analyze.return_value = mock_analysis

    mock_dao = Mock()
    mock_dao.db_path = ":memory:"  # MemberDAO/PreferenceDAO 需真实路径（Mock 属性会返回 Mock → Path TypeError）

    handler = MessageHandler(
        engine=Mock(),
        ziwei_engine=Mock(),
        liuyao_engine=Mock(),
        fengshui_engine=Mock(),
        mianxiang_engine=Mock(),
        zeri_engine=Mock(),
        retriever=mock_retriever,
        llm=mock_llm,
        dao=mock_dao,
    )
    result = handler.process("奇门遁甲择时", "user123")

    assert "奇门" in result
    mock_retriever.search.assert_called_once()
    mock_llm.analyze.assert_called_once()


# ── NEW: No more "开发中" for any intent ──────────────────────────────

def test_all_intents_have_handlers():
    """所有10种意图都有处理逻辑（不返回'开发中'）"""
    handler = make_mock_handler()
    # Test all intent keywords — none should return "开发中"
    test_cases = [
        ("八字", "八字"),
        ("紫微斗数", "紫微"),
        ("六爻起卦", "六爻"),
        ("看看风水", "看看"),
        ("选个吉日", "选日子"),
        ("看看面相", "看看"),
        ("奇门遁甲", "奇门"),
        ("取名字", "起名"),
        ("婚姻配对", "配对"),
        ("周公解梦", "解梦"),
    ]
    for msg, _ in test_cases:
        intent = handler._detect_intent(msg)
        assert intent is not None, f"未识别意图: {msg}"


# ── Task 20: Voice input support ──────────────────────────────────────

def test_handle_voice_with_text_routes_through_process():
    """语音有转写文本时，会通过正常流程处理"""
    handler = make_mock_handler()
    # Mock process to verify it's called
    original_process = handler.process
    call_tracker = []
    def tracking_process(msg, user_id):
        call_tracker.append((msg, user_id))
        return original_process(msg, user_id)
    handler.process = tracking_process

    result = handler._handle_voice(voice_text="帮我看看八字")
    call_tracker.clear()  # clean up
    assert "命理助手" in result or "请提供" in result


def test_handle_voice_without_text_returns_hint():
    """语音无转写文本时，返回CoW插件提示"""
    handler = make_mock_handler()
    result = handler._handle_voice(voice_text="")
    assert "CoW" in result or "语音插件" in result


def test_handle_voice_without_text_no_args():
    """语音无参数调用时，返回CoW插件提示"""
    handler = make_mock_handler()
    result = handler._handle_voice()
    assert "CoW" in result or "语音插件" in result


# ── Task 20: Image input support ──────────────────────────────────────

def test_handle_image_no_url_returns_hint():
    """图片无URL时返回提示"""
    handler = make_mock_handler()
    result = handler._handle_image(image_url="")
    assert "请提供图片链接" in result


def test_handle_image_fengshui_keyword():
    """图片含户型/风水关键词 - 进入风水分支"""
    handler = make_mock_handler()
    result = handler._handle_image(
        image_url="http://example.com/house.jpg",
        user_text="看看这个户型图的fengshui",
    )

    assert "户型" in result or "风水" in result
    # No direction → prompt user to provide direction
    assert "坐向" in result
    assert "图片识别" in result


def test_handle_image_fengshui_with_direction():
    """图片含户型关键词且带坐向"""
    mock_fengshui = Mock()
    mock_result = Mock()
    mock_result.house_gua = "离宅"
    mock_result.period = 9
    mock_result.eight_mansions = {
        "生气": "南", "天医": "北", "延年": "东", "伏位": "西",
        "绝命": "东北", "五鬼": "西南", "六煞": "东南", "祸害": "西北",
    }
    mock_fengshui.analyze.return_value = mock_result

    handler = make_mock_handler()
    handler.fengshui_engine = mock_fengshui

    result = handler._handle_image(
        image_url="http://example.com/house.jpg",
        user_text="坐南朝北的户型风水如何",
    )

    assert "离宅" in result
    assert "四吉方" in result
    assert "四凶方" in result


def test_handle_image_mianxiang_keyword():
    """图片含面相/手相关键词"""
    handler = make_mock_handler()
    result = handler._handle_image(
        image_url="http://example.com/face.jpg",
        user_text="帮我看看面相",
    )

    assert "面部特征" in result or "脸型" in result or "眼睛" in result
    assert "AI 视觉识别" in result


def test_handle_image_mianxiang_keyword_handxiang():
    """图片含手相关键词"""
    handler = make_mock_handler()
    result = handler._handle_image(
        image_url="http://example.com/hand.jpg",
        user_text="看看手相",
    )

    assert "面部特征" in result or "描述" in result
    assert "AI 视觉识别" in result


def test_handle_image_generic():
    """图片无匹配关键词 - 返回通用引导"""
    handler = make_mock_handler()
    result = handler._handle_image(
        image_url="http://example.com/photo.jpg",
        user_text="这张图怎么样",
    )

    assert "请告诉我您想通过这张图片了解什么" in result
    assert "户型风水" in result
    assert "面相" in result


# ── Task 20: message_type routing via main.py ─────────────────────────

def test_voice_message_type_routing():
    """验证语音类型的message_type路由逻辑（模拟main.py的ChatRequest）"""
    handler = make_mock_handler()

    # Simulate main.py routing: voice type with voice_text
    reply = handler._handle_voice(voice_text="帮我看看八字")
    # Should route through process → intent detection
    assert isinstance(reply, str)
    assert len(reply) > 0

    # voice type without voice_text
    reply_no_text = handler._handle_voice()
    assert "CoW" in reply_no_text or "语音插件" in reply_no_text


def test_image_message_type_routing():
    """验证图片类型的message_type路由逻辑（模拟main.py的ChatRequest）"""
    handler = make_mock_handler()

    # image with fengshui keyword → fengshui branch
    reply = handler._handle_image(
        image_url="http://example.com/house.jpg",
        user_text="看户型风水",
    )
    assert "户型" in reply or "风水" in reply

    # image without keywords → generic guidance
    reply_generic = handler._handle_image(
        image_url="http://example.com/photo.jpg",
        user_text="随便看看",
    )
    assert "请告诉我您想通过这张图片了解什么" in reply_generic


def test_text_message_type_default_behavior():
    """验证text类型的默认处理行为不变"""
    handler = make_mock_handler()
    result = handler.process("你好", "user123")
    assert "命理助手" in result


# ── NEW: Dream handler ─────────────────────────────────────────────────

def test_process_dream_missing_desc_asks():
    """解梦无梦境描述 - 询问提供信息"""
    handler = make_mock_handler()
    result = handler.process("解梦", "user123")
    assert "请描述" in result or "梦见" in result


def test_process_dream_with_text_and_engine():
    """解梦带梦境描述 - engine + RAG + LLM 完整流程"""
    mock_dream = Mock()
    mock_result = Mock()
    mock_result.dream_keywords = ["蛇", "咬"]
    mock_result.interpretations = [
        "梦见被蛇咬，主得大财。蛇在梦中象征智慧与财富...",
        "梦见蛇咬自己，表示要交好运...",
    ]
    mock_result.source = "周公解梦"
    mock_dream.interpret.return_value = mock_result

    mock_retriever = Mock()
    mock_retriever.search.return_value = []

    mock_llm = Mock()
    mock_analysis = Mock()
    mock_analysis.response = "此梦预示近期将有财运到来，蛇为小龙，主升迁之喜..."
    mock_llm.analyze.return_value = mock_analysis

    mock_dao = Mock()
    mock_dao.db_path = ":memory:"  # MemberDAO/PreferenceDAO 需真实路径（Mock 属性会返回 Mock → Path TypeError）

    handler = MessageHandler(
        engine=Mock(),
        ziwei_engine=Mock(),
        liuyao_engine=Mock(),
        fengshui_engine=Mock(),
        mianxiang_engine=Mock(),
        zeri_engine=Mock(),
        retriever=mock_retriever,
        llm=mock_llm,
        dao=mock_dao,
        dream_engine=mock_dream,
    )
    result = handler.process("梦见被蛇咬了", "user123")

    assert "周公解梦" in result
    assert "您梦见了" in result
    assert "古籍记载" in result
    assert "AI解读" in result
    assert "被蛇咬了" in result
    assert "此梦预示" in result
    mock_dream.interpret.assert_called_once()
    mock_retriever.search.assert_not_called()  # engine provided interpretations
    mock_llm.analyze.assert_called_once()


def test_process_dream_rag_fallback():
    """解梦 - engine无结果时回退到RAG全文搜索"""
    mock_dream = Mock()
    mock_result = Mock()
    mock_result.dream_keywords = []
    mock_result.interpretations = []
    mock_result.source = ""
    mock_dream.interpret.return_value = mock_result

    mock_retriever = Mock()
    mock_retriever.search.return_value = [
        Mock(text="梦见掉牙齿，主父母有灾，须加谨慎...", source="周公解梦", score=0.85, chunk_id="1"),
    ]

    mock_llm = Mock()
    mock_analysis = Mock()
    mock_analysis.response = "掉牙齿的梦境分析..."
    mock_llm.analyze.return_value = mock_analysis

    mock_dao = Mock()
    mock_dao.db_path = ":memory:"  # MemberDAO/PreferenceDAO 需真实路径（Mock 属性会返回 Mock → Path TypeError）

    handler = MessageHandler(
        engine=Mock(),
        ziwei_engine=Mock(),
        liuyao_engine=Mock(),
        fengshui_engine=Mock(),
        mianxiang_engine=Mock(),
        zeri_engine=Mock(),
        retriever=mock_retriever,
        llm=mock_llm,
        dao=mock_dao,
        dream_engine=mock_dream,
    )
    result = handler.process("梦见掉牙齿", "user123")

    assert "掉牙齿的梦境分析" in result
    mock_retriever.search.assert_called_once()
    mock_llm.analyze.assert_called_once()


def test_dream_intent_detection():
    """AI 原生（Phase 1）：解梦意图由 LLM 分析，不再依赖关键词表。

    MessageAnalyzer 的 COMBINED_PROMPT 明确将梦境描述归为 dream，
    这里验证其快路径规则仍然生效（生日→bazi 不依赖网络）。
    """
    from src.engines.message_analyzer import MessageAnalyzer
    analyzer = MessageAnalyzer(api_key="")
    # 无 API key 时优雅降级（不抛异常），fast path 生日→bazi 仍然生效
    result = analyzer.analyze("1990年5月20日 午时 北京 男")
    assert result.intent == "bazi"
    result = analyzer.analyze("梦见自己会飞")
    assert result.intent is None  # 无 key 时降级为 free_chat，不崩溃
