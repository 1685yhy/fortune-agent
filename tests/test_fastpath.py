# -*- coding: utf-8 -*-
"""Task 10：问事快路径——已建档/已存盘跳过 RAG 预检索 + 已存结果注入防矛盾。

红线回归：
1. 降级路径（零 LLM）不经过 _should_fastpath 门控，行为不变
2. 无档案 → _should_fastpath False → retriever.search 照跑
3. tool_loop 的 search 工具兜底不受影响（本改动不触碰工具定义）
4. 快路径门控 fail-open：任何异常回落 False（走原检索路径）
"""
import logging
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from unittest.mock import Mock  # noqa: E402

from src.bot.handler import MessageHandler  # noqa: E402
import src.bot.handler as handler_mod  # noqa: E402

BIRTH = {"year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
         "city": "北京", "gender": "男"}

CHART_ROW = {
    "id": 1,
    "birth": {"year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
              "city": "北京", "gender": "男", "calendar": "solar"},
    "bazi_json": {"day_master": "庚金", "geju": "伤官佩印", "yongshen": "水"},
    "created_at": "2026-08-23",
}


def make_handler(**kw) -> MessageHandler:
    """object.__new__ 装配 _do_bazi_analysis 所需 Mock 属性（跑真实方法体）。"""
    h = object.__new__(MessageHandler)
    h.engine = Mock()
    h.llm = Mock()
    h.llm.analyze.return_value = Mock(response="分析")
    h.dao = Mock()
    h.retriever = Mock()
    h.memory = None
    h.memory_system = None
    h._downgraded = {}
    h._analysis_facts = {}
    h.tool_logs = {}
    h._emit_stream_event = Mock()
    h._consume_pregen_instant = Mock(return_value=None)
    h._gen_instant_reply = Mock(return_value="")
    h._save_bazi_records = Mock()
    h._get_personalized_context = Mock(return_value="")
    h._route_by_scenario = Mock(return_value=None)
    h._gen_chart_title = Mock(return_value="测试标题")
    h._gen_followup_questions = Mock(return_value="")
    h._add_feedback_prompt = Mock(side_effect=lambda s: s)
    h._alloc_citations = Mock(return_value=1)
    h._append_citations = Mock()
    h._extract_user_context = Mock(return_value="")
    for k, v in kw.items():
        setattr(h, k, v)
    return h


def bazi_result():
    r = Mock()
    r.day_master = "庚金"
    r.bazi = ["庚午", "辛巳", "甲申", "壬申"]
    r.geju = "伤官佩印"
    r.yongshen = "水"
    r.wuxing = {}
    return r


# ---------------------------------------------------------------- 单元：门控


def test_should_fastpath_chart_same_birth_true():
    """chart_records 有同生辰已存盘 → True（跳过预检索）。"""
    h = make_handler()
    h.chart_dao = Mock()
    h.chart_dao.get_latest_chart.return_value = CHART_ROW
    h.dao.get_user_bazi.return_value = None  # 即使无 dao 档案也 True
    assert h._should_fastpath("u1", BIRTH) is True
    h.chart_dao.get_latest_chart.assert_called_once_with("u1")


def test_should_fastpath_chart_birth_mismatch_falls_to_archive():
    """chart 生辰不同 → 不因 chart 走快路径；但 dao 有档案 → 仍 True。"""
    h = make_handler()
    h.chart_dao = Mock()
    h.chart_dao.get_latest_chart.return_value = {
        "id": 2, "birth": {"year": 1988, "month": 1, "day": 1, "calendar": "solar"},
        "bazi_json": {}, "created_at": "2026-08-01"}
    h.dao.get_user_bazi.return_value = {"year": 1990, "bazi": ["庚午"]}
    assert h._should_fastpath("u1", BIRTH) is True


def test_should_fastpath_no_archive_false():
    """红线 2：无档案无已存盘 → False，retriever.search 照跑。"""
    h = make_handler()
    h.chart_dao = Mock()
    h.chart_dao.get_latest_chart.return_value = None
    h.dao.get_user_bazi.return_value = None
    assert h._should_fastpath("u1", BIRTH) is False


def test_should_fastpath_fail_open_on_exception():
    """红线 4：chart/dao 异常 → 回落 False，绝不因快路径错误让用户拿空引用。"""
    h = make_handler()
    h.chart_dao = Mock()
    h.chart_dao.get_latest_chart.side_effect = RuntimeError("db locked")
    h.dao.get_user_bazi.side_effect = RuntimeError("db locked")
    assert h._should_fastpath("u1", BIRTH) is False


def test_should_fastpath_no_chart_dao_attr_false():
    """handler 无 chart_dao 属性（db_path=None 配置）→ 不崩，回落 dao 档案判定。"""
    h = make_handler()  # 故意不挂 chart_dao
    h.dao.get_user_bazi.return_value = None
    assert h._should_fastpath("u1", BIRTH) is False


def test_should_fastpath_archive_only_without_chart_dao_true():
    """无 chart_dao 但有 dao 档案 → True（已建档仍走快路径）。"""
    h = make_handler()
    h.dao.get_user_bazi.return_value = {"year": 1990, "bazi": ["庚午"]}
    assert h._should_fastpath("u1", BIRTH) is True


# ------------------------------------------------------------ 集成：主分析


def _run_analysis(h, monkeypatch):
    monkeypatch.setattr(handler_mod, "AdaptiveAdvisor", Mock)
    import src.images.bazi_chart_html as bch
    monkeypatch.setattr(bch, "BaziChartHTML", Mock)
    h.engine.calculate.return_value = bazi_result()
    return h._do_bazi_analysis(1990, 5, 20, 15, 0, "北京", "男", "财运如何", "u1")


def test_integration_fastpath_skips_presearch(monkeypatch, caplog):
    """有已存盘（chart 同生辰）→ retriever.search 不被调用；已存结果注入 prompt。"""
    h = make_handler()
    h.chart_dao = Mock()
    h.chart_dao.get_latest_chart.return_value = CHART_ROW
    h.dao.get_user_bazi.return_value = {"year": 1990, "bazi": ["庚午"]}
    with caplog.at_level(logging.INFO, logger="src.bot.handler"):
        reply = _run_analysis(h, monkeypatch)

    h.retriever.search.assert_not_called()  # 实证：预检索被跳过
    assert "fastpath" in caplog.text and "跳过预检索" in caplog.text
    assert "分析" in reply
    kwargs = h.llm.analyze.call_args.kwargs
    extra = kwargs["extra_system_prompt"]
    assert "已存排盘结果" in extra
    assert "庚金日主" in extra and "伤官佩印" in extra and "用神水" in extra
    assert "回答须与此一致" in extra


def test_integration_no_archive_still_searches(monkeypatch):
    """红线 2 实证：无档案/无已存盘 → retriever.search 照跑（category=bazi, top_k=15）。"""
    h = make_handler()
    h.chart_dao = Mock()
    h.chart_dao.get_latest_chart.return_value = None
    h.dao.get_user_bazi.return_value = None
    _run_analysis(h, monkeypatch)

    h.retriever.search.assert_called_once()
    _, kwargs = h.retriever.search.call_args
    assert kwargs.get("category") == "bazi"
    assert kwargs.get("top_k") == 15
    # 无存量 → 不注入已存结果
    a_kwargs = h.llm.analyze.call_args.kwargs
    assert a_kwargs["extra_system_prompt"] is None


def test_integration_downgrade_path_unaffected(monkeypatch):
    """红线 1：降级（零 LLM）路径不经过快路径门控，行为不变。"""
    h = make_handler()
    h._downgraded = {"u1": True}
    h._do_bazi_lite = Mock(return_value="降级精简回复")
    # 门控若在降级路径被调用即失败（证明快路径只在主分析路径内生效）
    h._should_fastpath = Mock(
        side_effect=AssertionError("降级路径不得调用快路径门控"))
    reply = _run_analysis(h, monkeypatch)

    assert reply == "降级精简回复"
    h._do_bazi_lite.assert_called_once()
    h.retriever.search.assert_not_called()
    h.llm.analyze.assert_not_called()


# ============================================================
# 批次 2 A3：分诊路由快通道兜底链缺口测试
# （审查发现清单 → 缺口 G1/G2/G3，见 task-A3-report.md）
# ============================================================

def _make_process_handler() -> MessageHandler:
    """真实 __init__ 装配 process() 全链路（llm 全 Mock，api_key 空 → 规则快判）。"""
    mock_llm = Mock()
    mock_llm.api_key = ""
    mock_llm.model = "deepseek-v4-flash"
    mock_llm.chat_conversation.return_value = "🔮 精简回复"
    mock_llm.chat.return_value = Mock(response="🔮 精简回复")
    mock_dao = Mock()
    mock_dao.get_user_bazi.return_value = None
    mock_dao.db_path = ""  # 无 db → chart_dao/record_query/preference_dao 均 None
    mock_session = Mock()
    mock_session.get_context_for_llm.return_value = []
    mock_session.add_message.return_value = None
    return MessageHandler(
        engine=Mock(), ziwei_engine=Mock(), liuyao_engine=Mock(),
        fengshui_engine=Mock(), mianxiang_engine=Mock(), zeri_engine=Mock(),
        retriever=Mock(), llm=mock_llm, dao=mock_dao, session_dao=mock_session,
    )


# ── G1：T5 重看盘直读 fail-open 未覆盖字段解析（缺键/脏数据 → KeyError 冒泡）──


def test_reuse_chart_malformed_row_fail_open():
    """缺口 G1：chart 行缺 created_at 键 → 直读回落 None 走全流程（修前 KeyError 冒泡）。"""
    h = object.__new__(MessageHandler)
    h.chart_dao = Mock()
    h.chart_dao.get_latest_chart.return_value = {"bazi_json": {"bazi": ["庚午"]}}
    h._card_turn = {}
    assert h._try_reuse_chart("u1", "看看我的盘") is None


def test_reuse_chart_malformed_bazi_json_fail_open():
    """缺口 G1：chart 行缺 bazi_json 键 → 直读回落 None 走全流程。"""
    h = object.__new__(MessageHandler)
    h.chart_dao = Mock()
    h.chart_dao.get_latest_chart.return_value = {"created_at": "2026-08-23"}
    h._card_turn = {}
    assert h._try_reuse_chart("u1", "看看我的盘") is None


def test_reuse_chart_wellformed_unchanged():
    """G1 正向控制：结构完好的 chart 行仍直读秒回（修复不回归）。"""
    h = object.__new__(MessageHandler)
    h.chart_dao = Mock()
    h.chart_dao.get_latest_chart.return_value = CHART_ROW
    h._card_turn = {}
    text = h._try_reuse_chart("u1", "看看我的盘")
    assert text is not None
    assert "这是你最近排过的盘" in text


# ── G2：process() 出口写缓存无兜底（回复已算好，cache.set 异常冒泡丢整条回复）──


def test_process_exit_cache_set_fail_open(monkeypatch):
    """缺口 G2：回复已算好，出口 cache.set 抛异常 → 回复照常返回（修前 RuntimeError 冒泡）。"""
    from src.bot.handler import MessageHandler as MH
    monkeypatch.setattr(MH, "_handle_bazi", Mock(return_value="测试回复"))
    h = _make_process_handler()
    h.cache = Mock()
    h.cache.get.return_value = None
    h.cache.set.side_effect = RuntimeError("cache oom")
    # 消息必须同时满足：规则快判 intent=bazi（走 handler 分发）+ is_cacheable
    # True（len<20，出口才写缓存）——"1990年5月20日"（10 字符）两者兼备
    reply = h.process("1990年5月20日", "g2u1")
    assert reply  # 用户拿到回复，不被缓存写入失败吞掉
    h.cache.set.assert_called_once()


# ── G3：欢迎回来开场白（快通道前置）记忆层异常无兜底 → 丢整条回复 ──


def test_welcome_back_fail_open_on_memory_error():
    """缺口 G3 单元：记忆层 has_memory 抛异常 → 放弃开场白返回 ""（修前异常冒泡）。"""
    h = object.__new__(MessageHandler)
    h.session_dao = Mock()
    h.memory_system = Mock()
    h.memory_system.has_memory.side_effect = RuntimeError("memory db locked")
    assert h._get_welcome_back("u1") == ""


def test_process_welcome_fail_open(monkeypatch):
    """缺口 G3 集成：流式模式下开场白生成失败 → 主回复照常返回（修前 process 抛异常）。"""
    h = _make_process_handler()
    h.memory_system.has_memory = Mock(side_effect=RuntimeError("memory db locked"))
    chunks = []

    def collect(evt, data):
        chunks.append((evt, data))

    reply = h.process("你好呀", "w1", stream_cb=collect)
    assert reply  # 主回复照常，开场白失败不影响交付
    assert reply == "🔮 精简回复"
