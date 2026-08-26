# -*- coding: utf-8 -*-
"""P1 阶段 2/5 单测：执行计划引导注入 + 关键字段保护（方案 v5，2026-08-09）。

覆盖：
- _tool_loop_analysis_hint：secondary_needs 逐项覆盖引导 / needs_search+搜索可用→搜索引导
- save_bazi_info 字段级合并：缺字段不覆盖旧值
- gender 保护：unknown 不覆盖已有值；冲突（女 vs 男）不覆盖并返回 conflict 标记
- subject=other（帮他人排盘）不写入本人画像
"""
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from src.bot.handler import MessageHandler  # noqa: E402
from src.engines.message_analyzer import MessageAnalysis  # noqa: E402
from src.memory.user_memory import UserMemory  # noqa: E402


@pytest.fixture
def handler():
    return object.__new__(MessageHandler)  # 不跑 __init__（Mock 装配基线问题与本测试无关）


def test_tool_loop_analysis_hint_empty(handler):
    """无 analysis → 无附加引导。"""
    assert handler._tool_loop_analysis_hint(None) == ""


def test_tool_loop_analysis_hint_secondary_needs(handler):
    """secondary_needs → 注入逐项覆盖引导。"""
    a = MessageAnalysis(needs_soothe=False, soothe_text="", emotion_label=None,
                        intent="career", secondary_needs=["财运", "失眠安抚"])
    hint = handler._tool_loop_analysis_hint(a)
    assert "附加需求" in hint
    assert "财运" in hint and "失眠安抚" in hint
    assert "逐项覆盖" in hint


def test_tool_loop_analysis_hint_needs_search(handler, monkeypatch):
    """needs_search=True 且搜索可用 → 注入 web_search JSON 工单引导（Task 5 改版）。"""
    import src.rag.web_search as ws
    monkeypatch.setattr(ws, "web_search_available", lambda force=False: True)
    a = MessageAnalysis(needs_soothe=False, soothe_text="", emotion_label=None,
                        intent="career", needs_search=True)
    hint = handler._tool_loop_analysis_hint(a)
    assert "实时信息" in hint
    assert '<tool_calls>[{"tool": "web_search"' in hint


def test_tool_loop_analysis_hint_search_unavailable(handler, monkeypatch):
    """needs_search=True 但搜索不可用 → 不注入搜索引导（不宣传不可用工具）。"""
    import src.rag.web_search as ws
    monkeypatch.setattr(ws, "web_search_available", lambda force=False: False)
    a = MessageAnalysis(needs_soothe=False, soothe_text="", emotion_label=None,
                        intent="career", needs_search=True)
    hint = handler._tool_loop_analysis_hint(a)
    assert "<tool_call>搜索:" not in hint          # 旧文本标签教学不注入
    # B1-8（姊妹用例断言补强）：新 JSON 工单教学同样不注入（原断言只防旧格式回潮，
    # 若教学改成 JSON 工单仍会漏注入——一并对新旧两种形态锁死）
    assert "web_search" not in hint
    assert "<tool_calls>" not in hint


@pytest.fixture
def memory(tmp_path):
    return UserMemory(base_dir=str(tmp_path))


def test_save_bazi_info_field_merge(memory, tmp_path):
    """字段级合并：新数据缺字段时保留旧值，不覆盖成默认值。"""
    memory.save_bazi_info("u1", {"year": 1990, "month": 5, "day": 20,
                                 "hour": 15, "minute": 0, "city": "北京",
                                 "gender": "男", "bazi": ["庚午", "辛巳"]})
    # 第二次只提供部分字段（如只报生日无时辰）
    memory.save_bazi_info("u1", {"year": 1990, "month": 5, "day": 20})
    data = memory._load("u1")
    b = data["bazi_info"]
    assert b["hour"] == 15  # 旧值保留
    assert b["city"] == "北京"
    assert b["gender"] == "男"


def test_save_bazi_info_gender_unknown_not_overwrite(memory):
    """gender 保护：已有值且新值 unknown → 不覆盖。"""
    memory.save_bazi_info("u2", {"gender": "女", "year": 1995})
    memory.save_bazi_info("u2", {"gender": "unknown", "year": 1995})
    assert memory._load("u2")["bazi_info"]["gender"] == "女"


def test_save_bazi_info_gender_conflict(memory):
    """gender 冲突（女 vs 男）：不覆盖并返回 conflict 标记。"""
    memory.save_bazi_info("u3", {"gender": "女", "year": 1995})
    conflict = memory.save_bazi_info("u3", {"gender": "男", "year": 1995})
    assert conflict == {"conflict": True, "conflict_field": "gender"}
    assert memory._load("u3")["bazi_info"]["gender"] == "女"  # 旧值保留


def test_save_bazi_info_subject_other(memory):
    """subject=other（帮他人排盘）→ 不写入本人画像。"""
    memory.save_bazi_info("u4", {"gender": "女", "year": 1995})
    r = memory.save_bazi_info("u4", {"gender": "男", "year": 2000}, subject="other")
    assert r == {}
    assert memory._load("u4").get("bazi_info") == {"gender": "女", "year": 1995}
    assert memory._load("u4").get("consultation_count", 0) == 1  # 未新增计数


# ------------------------------------------------------------ 批次 2 B1-11
# A3 审查 Minor ①：fail-open 路径观测性——_try_reuse_chart / _get_welcome_back
# 兜底时补 logger.warning（只加日志，不改任何行为逻辑）


def test_welcome_back_outer_fallback_logs_warning(handler, caplog):
    """B1-11a：_get_welcome_back 外层兜底触发（任意一步异常 → 放弃开场白）→
    logger.warning 记录；行为不变（返回 ""，不阻塞主回复）。"""
    import logging
    from unittest.mock import Mock

    handler.session_dao = object()          # truthy → 走到 has_memory 一步
    ms = Mock()
    ms.has_memory.side_effect = RuntimeError("记忆层故障")   # 外层 try 内冒泡
    handler.memory_system = ms
    with caplog.at_level(logging.WARNING, logger="src.bot.handler"):
        assert handler._get_welcome_back("u1") == ""        # 行为不变：放弃开场白
    assert any("欢迎" in r.message for r in caplog.records), \
        "fail-open 兜底必须留 warning 日志（观测性）"


def test_try_reuse_chart_fail_open_logs_warning(handler, caplog):
    """B1-11b：_try_reuse_chart 整函数 fail-open（图数据层/组装异常 → None 回落
    全流程）→ logger.warning 记录；行为不变（返回 None，不阻塞 process 入口）。"""
    import logging
    from unittest.mock import Mock

    dao = Mock()
    dao.get_latest_chart.side_effect = RuntimeError("chart 层故障")
    handler.chart_dao = dao
    handler._analysis_facts = {}
    with caplog.at_level(logging.WARNING, logger="src.bot.handler"):
        assert handler._try_reuse_chart("u1", "我的盘") is None
    assert any("重看盘" in r.message for r in caplog.records), \
        "fail-open 兜底必须留 warning 日志（观测性）"


def test_try_reuse_chart_normal_path_no_warning(handler, caplog):
    """B1-11 邻域：fail-open 未触发（关键词不匹配早退）→ 不产生多余 warning 日志。"""
    import logging
    from unittest.mock import Mock

    handler.chart_dao = Mock()              # get_latest_chart 返回 None（无盘）
    handler._analysis_facts = {}
    with caplog.at_level(logging.WARNING, logger="src.bot.handler"):
        assert handler._try_reuse_chart("u1", "今天天气怎么样") is None  # 无关消息早退
    assert not [r for r in caplog.records if "重看盘" in r.message]


def test_handle_hehun_incomplete_info_returns_guide_card(handler):
    """Task 8：hehun 双方信息不全 → 返回引导卡片（文案 + /pages/hehun/hehun 跳转路径）。"""
    reply = handler._handle_hehun("我和TA合不合", "u1")
    assert isinstance(reply, str)
    assert "/pages/hehun/hehun" in reply
    assert "双人合盘" in reply
    assert "生辰" in reply
    assert "缘笺" in reply


def test_handle_hehun_single_birth_still_guide_card(handler):
    """Task 8：只给一方生辰（信息不全）→ 同样返回引导卡片而非旧示例文案。"""
    reply = handler._handle_hehun("1990年5月20日 男 我和TA合不合", "u1")
    assert isinstance(reply, str)
    assert "/pages/hehun/hehun" in reply
    assert "请提供双方的信息" not in reply  # 旧文案已替换
