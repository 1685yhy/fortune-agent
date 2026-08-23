"""对话数据复用体系 Task 5：重看盘直读（0 引擎 0 LLM）。

契约：用户说"看看我的盘/我的八字"等重看表述、且 chart_records 已有该用户
排盘结果时，直接读库秒回（_try_reuse_chart）——引擎绝不计算、LLM 绝不调用；
未命中（无已存结果/显式要重新排盘）返回 None，调用方走原全流程。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
from unittest.mock import Mock  # noqa: E402

from src.bot.handler import MessageHandler  # noqa: E402

REUSE_PATTERNS = ["我的盘", "我的八字", "上次的盘", "重新看看我的盘", "我的命盘"]

_BIRTH = {"year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
          "city": "北京", "gender": "男"}
_BAZI = {"bazi": ["庚午", "辛巳", "甲申", "壬申"], "day_master": "甲",
         "dayun": [["0", "庚辰"]], "liunian": {"2026": "丙午"},
         "shensha": ["天乙贵人"], "geju": "正官格", "yongshen": "甲木"}


def _chart_dao(tmp_path):
    from src.storage.chart_dao import ChartDAO
    return ChartDAO(str(tmp_path / "c.db"))


def _full_handler(tmp_path):
    """process() 全链路手工装配（object.__new__，不跑 __init__）。

    与 test_chart_write_points.py 同风格；llm/engine 由各测试用例覆盖：
    命中短路用例挂 Mock（断言未被调用），未命中用例挂 None（零 LLM）。
    """
    h = object.__new__(MessageHandler)
    h.dao = None
    h.memory_system = None
    h.session_dao = None
    h.member_dao = None
    h.preference_dao = None
    h.memory = None
    h.cache = Mock()
    h.cache.get.return_value = None
    h._deep_night = {}
    h._downgraded = {}
    h._tool_logs = {}
    h._citations = {}
    h._analysis_facts = {}
    h._pregen_instant = {}
    h._pregen_pool = Mock()
    h.engine = Mock()
    h.chart_dao = _chart_dao(tmp_path)
    return h


# ---------------------------------------------------------------- 简报两测试
def test_reuse_keywords_return_chart_without_engine(tmp_path):
    """重看表述 + 已有排盘结果 → 直读文本含四柱/流年，不碰引擎。"""
    from src.storage.chart_dao import ChartDAO
    h = object.__new__(MessageHandler)
    h.chart_dao = ChartDAO(str(tmp_path / "c.db"))
    h.chart_dao.save_chart("u1", 1,
        {"year":1990,"month":5,"day":20,"hour":15,"minute":0,"city":"北京","gender":"男"},
        {"bazi":["庚午","辛巳","甲申","壬申"],"day_master":"甲",
         "dayun":[["0","庚辰"]],"liunian":{"2026":"丙午"},"shensha":["天乙贵人"],
         "geju":"正官格","yongshen":"甲木"})
    text = h._try_reuse_chart("u1", "看看我的盘")
    assert text is not None
    assert "庚午" in text and "辛巳" in text and "甲申" in text and "壬申" in text
    assert "丙午" in text


def test_no_chart_returns_none(tmp_path):
    """无已存结果 → 返回 None（调用方继续原流程）。"""
    from src.storage.chart_dao import ChartDAO
    h = object.__new__(MessageHandler)
    h.chart_dao = ChartDAO(str(tmp_path / "c.db"))
    assert h._try_reuse_chart("u1", "看看我的盘") is None


# ------------------------------------------------------------ 短路验证（契约 2）
def test_process_reuse_hit_short_circuits_engine_and_llm(tmp_path):
    """有已存记录：process("看看我的盘") 命中直读秒回 → 0 引擎 0 LLM。"""
    h = _full_handler(tmp_path)
    h.llm = Mock()
    h.chart_dao.save_chart("u1", 1, _BIRTH, _BAZI)
    reply = h.process("看看我的盘", "u1")
    assert "最近排过的盘" in reply
    assert "庚午" in reply and "丙午" in reply
    assert h.engine.calculate.called is False      # 引擎绝不计算
    assert h.llm.chat.called is False              # LLM 绝不调用
    assert h.llm.chat_conversation.called is False


def test_process_reuse_miss_no_engine_normal_flow(tmp_path):
    """无已存记录（无档案）：直读未命中 → 引擎绝不计算，走正常流程兜底。"""
    h = _full_handler(tmp_path)
    h.llm = None  # 零 LLM：miss 后正常流程走固定兜底引导，同样不调 LLM
    reply = h.process("看看我的盘", "u1")
    assert "最近排过的盘" not in reply             # 不是直读
    assert "庚午" not in reply
    assert h.engine.calculate.called is False      # 引擎绝不计算
    assert "出生年月日" in reply                   # 正常流程的信息收集兜底


def test_reuse_excludes_explicit_repaipan(tmp_path):
    """显式'重新排'（重排意图）→ 不直读，返回 None 走全流程。"""
    h = object.__new__(MessageHandler)
    h.chart_dao = _chart_dao(tmp_path)
    h.chart_dao.save_chart("u1", 1, _BIRTH, _BAZI)
    assert h._try_reuse_chart("u1", "重新排一下我的盘") is None
