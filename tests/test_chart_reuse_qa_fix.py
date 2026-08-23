"""QA 修复回归（qa-12 EXT-005）：D5 重看盘意图识别过宽。

'结合我的八字，看看我今年秋天的运势要点' 曾因命中 REUSE_KEYWORDS'我的八字'
被 0.47s 秒回固定盘面复述模板、不回答问题。修复：去掉重看关键词后剩余
文本必须为空或纯语气词，否则视为'带盘问具体问题'走全流程（返回 None）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402

from src.bot.handler import MessageHandler  # noqa: E402

_BIRTH = {"year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
          "city": "北京", "gender": "男"}
_BAZI = {"bazi": ["庚午", "辛巳", "甲申", "壬申"], "day_master": "甲",
         "dayun": [["0", "庚辰"]], "liunian": {"2026": "丙午"},
         "shensha": ["天乙贵人"], "geju": "正官格", "yongshen": "甲木"}


def _handler(tmp_path):
    from src.storage.chart_dao import ChartDAO
    h = object.__new__(MessageHandler)
    h.chart_dao = ChartDAO(str(tmp_path / "c.db"))
    h.chart_dao.save_chart("u1", 1, _BIRTH, _BAZI)
    return h


def test_qa_phrase_with_substantive_question_not_reused(tmp_path):
    """QA EXT-005 原句：'结合我的八字，看看我今年秋天的运势要点'
    → 不得秒回盘面复述模板，返回 None 走全流程。"""
    h = _handler(tmp_path)
    assert h._try_reuse_chart("u1", "结合我的八字，看看我今年秋天的运势要点") is None


def test_substantive_question_variants_not_reused(tmp_path):
    """其他'带盘问具体问题'表述同样不得被直读劫持。"""
    h = _handler(tmp_path)
    for q in ("结合我的八字，分析一下我的事业",
              "根据我的八字看看我适合什么行业",
              "我的八字今年要注意什么"):
        assert h._try_reuse_chart("u1", q) is None, q


def test_pure_review_phrases_still_reuse(tmp_path):
    """纯重看表述仍直读秒回（防过度修复）。"""
    h = _handler(tmp_path)
    for q in ("看看我的盘", "帮我看看我的盘", "我的八字是什么",
              "重新看看我的盘", "看一下我的盘", "我的命盘"):
        assert h._try_reuse_chart("u1", q) is not None, q


def test_no_chart_returns_none_even_for_pure_review(tmp_path):
    """无已存盘：即使纯重看表述也不直读（与既有契约一致）。"""
    from src.storage.chart_dao import ChartDAO
    h = object.__new__(MessageHandler)
    h.chart_dao = ChartDAO(str(tmp_path / "c.db"))
    assert h._try_reuse_chart("u1", "帮我看看我的盘") is None
