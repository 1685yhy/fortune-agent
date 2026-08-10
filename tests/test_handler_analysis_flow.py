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
    """needs_search=True 且搜索可用 → 注入 <tool_call>搜索 引导。"""
    import src.rag.web_search as ws
    monkeypatch.setattr(ws, "web_search_available", lambda force=False: True)
    a = MessageAnalysis(needs_soothe=False, soothe_text="", emotion_label=None,
                        intent="career", needs_search=True)
    hint = handler._tool_loop_analysis_hint(a)
    assert "实时信息" in hint
    assert "<tool_call>搜索:" in hint


def test_tool_loop_analysis_hint_search_unavailable(handler, monkeypatch):
    """needs_search=True 但搜索不可用 → 不注入搜索引导（不宣传不可用工具）。"""
    import src.rag.web_search as ws
    monkeypatch.setattr(ws, "web_search_available", lambda force=False: False)
    a = MessageAnalysis(needs_soothe=False, soothe_text="", emotion_label=None,
                        intent="career", needs_search=True)
    hint = handler._tool_loop_analysis_hint(a)
    assert "<tool_call>搜索:" not in hint


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
