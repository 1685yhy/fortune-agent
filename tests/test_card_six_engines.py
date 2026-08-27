# -*- coding: utf-8 -*-
"""批次 2 计划 C Task C1：6 类引擎结果卡片化（handler 级集成测试）。

契约（E2-1 扩展，宁漏勿误）：
- 6 类引擎（紫微/六爻/风水/面相/奇门/解梦）意图路径完成真实引擎计算 →
  _mark_card_turn 记录 → 统一出口 _maybe_wrap_card 包对应类型卡片
- 引擎未执行/失败（异常 → ⚠️ 降级文案 / 引擎未注入）→ 不记录标记、
  不包装卡片（宁可漏包不可误包）
- 工具失败（hit=False）与错误签名拒包双闭环不受影响（纯函数侧已单测）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
from unittest.mock import Mock  # noqa: E402

from src.bot.handler import MessageHandler  # noqa: E402
from src.engines.dream import DreamResult  # noqa: E402


def _engine_handler() -> MessageHandler:
    """object.__new__ 手工装配（与 tests/test_chart_reuse.py 同风格）。

    llm/retriever/dao 全 Mock：引擎完成真实计算路径后回复确定性
    （"这是分析正文"），卡片判定上下文 _card_turn 独立装配。
    """
    h = object.__new__(MessageHandler)
    h._card_turn = {}
    h._citations = {}
    h.dao = Mock()
    h.dao.save_consultation.return_value = None
    h.dao.get_user_bazi.return_value = None
    h.retriever = Mock()
    h.retriever.search.return_value = []
    h.llm = Mock()
    h.llm.analyze.return_value = Mock(response="这是分析正文")
    return h


# ── 正向：6 类引擎完成分析 → 卡片标记 ───────────────────────────

def test_ziwei_analysis_wraps_card():
    """紫微斗数引擎完成排盘 → [card:ziwei …]"""
    h = _engine_handler()
    h.ziwei_engine = Mock()
    h.ziwei_engine.calculate.return_value = Mock(
        ming_gong="命宫", shen_gong="身宫", wuxing_ju="水二局",
        sihua={}, palaces={}, dayun=[])
    reply = h._do_ziwei_analysis(
        1990, 5, 20, 15, 0, "北京", "男", "看看我的紫微盘", "u1")
    assert h._card_turn["u1"].get("ziwei") is True
    wrapped = h._maybe_wrap_card(reply, "u1")
    assert '[card:ziwei title="紫微命盘"]' in wrapped
    assert wrapped.endswith("[/card]")
    assert "这是分析正文" in wrapped


def test_liuyao_analysis_wraps_card():
    """六爻引擎完成起卦 → [card:liuyao …]"""
    h = _engine_handler()
    h.liuyao_engine = Mock()
    h.liuyao_engine.cast.return_value = Mock(
        question="一般运势", original_hexagram="乾为天", changed_hexagram="",
        palace="乾宫", palace_wuxing="金", changing_lines=[], lines=[])
    reply = h._do_liuyao_analysis("一般运势", "六爻看看我运势", "u1")
    assert h._card_turn["u1"].get("liuyao") is True
    wrapped = h._maybe_wrap_card(reply, "u1")
    assert '[card:liuyao title="六爻卦象"]' in wrapped
    assert wrapped.endswith("[/card]")


def test_fengshui_analysis_wraps_card():
    """风水引擎完成分析 → [card:fengshui …]"""
    h = _engine_handler()
    h.fengshui_engine = Mock()
    h.fengshui_engine.analyze.return_value = Mock(
        house_gua="乾", period=8, person_gua="", eight_mansions={}, flying_stars={})
    reply = h._do_fengshui_analysis("坐北朝南", 1990, "男", "看看我家风水", "u1")
    assert h._card_turn["u1"].get("fengshui") is True
    wrapped = h._maybe_wrap_card(reply, "u1")
    assert '[card:fengshui title="风水分析"]' in wrapped
    assert wrapped.endswith("[/card]")


def test_mianxiang_analysis_wraps_card():
    """面相引擎完成分析 → [card:mianxiang …]"""
    h = _engine_handler()
    h.mianxiang_engine = Mock()
    h.mianxiang_engine.analyze.return_value = Mock(
        face_type="方脸", three_zones={}, five_mountains={}, features={},
        overall="综合不错")
    reply = h._do_mianxiang_analysis("方脸，额头饱满，眼睛大而有神", "看看我的面相", "u1")
    assert h._card_turn["u1"].get("mianxiang") is True
    wrapped = h._maybe_wrap_card(reply, "u1")
    assert '[card:mianxiang title="面相分析"]' in wrapped
    assert wrapped.endswith("[/card]")


def test_qimen_analysis_wraps_card():
    """奇门引擎完成排盘 → [card:qimen …]，反馈语拆到卡片外"""
    h = _engine_handler()
    h.qimen_engine = Mock()
    h.qimen_engine.calculate.return_value = Mock()
    h.qimen_engine.print_chart.return_value = "奇门盘"
    reply = h._handle_qimen("奇门遁甲看看我这月事业运", "u1")
    assert h._card_turn["u1"].get("qimen") is True
    wrapped = h._maybe_wrap_card(reply, "u1")
    assert '[card:qimen title="奇门遁甲局"]' in wrapped
    assert wrapped.endswith("[/card]\n\n———\n💬 这个分析对你有帮助吗？👍 有帮助  👎 不太准")
    # 反馈语在卡片外（split_tail 已拆出）
    assert "有帮助" not in wrapped.split("[/card]")[0]


def test_dream_analysis_wraps_card():
    """解梦引擎完成分析 → [card:dream …]"""
    h = _engine_handler()
    h.dream_engine = Mock()
    h.dream_engine.analyze.return_value = DreamResult(
        original_text="梦见一条大蟒蛇在追我，我很害怕",
        dream_type="压力类",
        keywords=["蛇", "追"],
        interpretations=["古籍解读一", "古籍解读二"],
        source="周公解梦",
    )
    reply = h._do_dream_analysis("梦见一条大蟒蛇在追我，我很害怕", "u1")
    assert h._card_turn["u1"].get("dream") is True
    wrapped = h._maybe_wrap_card(reply, "u1")
    assert '[card:dream title="解梦结果"]' in wrapped
    assert wrapped.endswith("[/card]")


# ── 宁漏勿误：引擎失败/未注入 → 不包装 ──────────────────────────

def test_ziwei_engine_failure_no_card():
    """紫微引擎异常 → ⚠️ 降级文案原样返回；标记未记录（宁漏勿误）"""
    h = _engine_handler()
    h.ziwei_engine = Mock()
    h.ziwei_engine.calculate.side_effect = RuntimeError("engine down")
    reply = h._do_ziwei_analysis(
        1990, 5, 20, 15, 0, "北京", "男", "看看我的紫微盘", "u1")
    assert reply.startswith("⚠️")
    assert h._card_turn.get("u1", {}).get("ziwei") is None
    assert h._maybe_wrap_card(reply, "u1") == reply


def test_liuyao_engine_failure_no_card():
    """六爻引擎异常 → ⚠️ 降级文案原样返回；标记未记录"""
    h = _engine_handler()
    h.liuyao_engine = Mock()
    h.liuyao_engine.cast.side_effect = RuntimeError("engine down")
    reply = h._do_liuyao_analysis("一般运势", "六爻看看我运势", "u1")
    assert reply.startswith("⚠️")
    assert h._card_turn.get("u1", {}).get("liuyao") is None
    assert h._maybe_wrap_card(reply, "u1") == reply


def test_qimen_engine_failure_no_mark():
    """奇门引擎异常冒泡（process 出口降级 ⚠️）→ 标记未记录"""
    h = _engine_handler()
    h.qimen_engine = Mock()
    h.qimen_engine.calculate.side_effect = RuntimeError("engine down")
    with pytest.raises(RuntimeError):
        h._handle_qimen("奇门遁甲看看我", "u1")
    assert h._card_turn.get("u1", {}).get("qimen") is None


def test_dream_engine_none_fallback_no_card():
    """解梦引擎未注入（空壳 DreamResult 兜底）→ 宁漏勿误，不包 dream 卡"""
    h = _engine_handler()
    h.dream_engine = None
    reply = h._do_dream_analysis("梦见大蟒蛇", "u1")
    assert h._card_turn.get("u1", {}).get("dream") is None
    assert h._maybe_wrap_card(reply, "u1") == reply


def test_engine_reply_error_signature_not_wrapped():
    """LLM 分析回复含错误签名 → 即使标记已记录也不包装（wrap_card 防御层）"""
    h = _engine_handler()
    h.llm.analyze.return_value = Mock(response="⚠️ 服务暂时不可用：资源不足")
    h.ziwei_engine = Mock()
    h.ziwei_engine.calculate.return_value = Mock(
        ming_gong="命宫", shen_gong="身宫", wuxing_ju="水二局",
        sihua={}, palaces={}, dayun=[])
    reply = h._do_ziwei_analysis(
        1990, 5, 20, 15, 0, "北京", "男", "看看我的紫微盘", "u1")
    assert h._card_turn["u1"].get("ziwei") is True  # 标记已记录
    wrapped = h._maybe_wrap_card(reply, "u1")
    assert wrapped == reply  # 但错误文案绝不包卡
