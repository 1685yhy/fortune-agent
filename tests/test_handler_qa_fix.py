"""QA 修复回归（qa-12 AC-CHAT-009）：D2 财运排盘错误四柱 + 缓存固化。

QA 实测：'今年财运怎么样' 返回错误四柱 庚午 甲申 乙丑 丙子（已存盘为
庚午 辛巳 乙酉 甲申），第 2/3 次 0.78s 命中缓存固化。根因：润色/工具循环
LLM 改写干支 + 页脚/反馈尾剥离失配。修复三层：
1. 四柱终审：回复按序断言的四柱与已存盘不一致 → 回退引擎原稿；
2. 无原稿可回退时 → 禁止写入缓存（防 0.78s 固化错误）；
3. 页脚/反馈尾按格式（正则/双版本）剥离，跨秒失配也能原样回接。
"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402

from src.bot.handler import MessageHandler  # noqa: E402

CHART_BAZI = ["庚午", "辛巳", "乙酉", "甲申"]  # QA 已存盘四柱


def _handler_with_chart(tmp_path, bazi=None):
    from src.storage.chart_dao import ChartDAO
    h = object.__new__(MessageHandler)
    h.chart_dao = ChartDAO(str(tmp_path / "c.db"))
    h.chart_dao.save_chart("u1", 1,
        {"year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
         "city": "北京", "gender": "男"},
        {"bazi": bazi or CHART_BAZI, "day_master": "乙木",
         "dayun": [["0", "甲申"]], "liunian": {"2026": "丙午"},
         "shensha": [], "geju": "伤官格", "yongshen": "水"})
    return h


# ─────────────────────────── 四柱冲突判定（_pillar_claims_conflict）───────────────────────────

def test_conflict_detects_qa_wrong_pillars(tmp_path):
    """QA 实测错误回复 庚午 甲申 乙丑 丙子 vs 已存 庚午 辛巳 乙酉 甲申 → 冲突。"""
    h = _handler_with_chart(tmp_path)
    reply = "今年财运整体平稳…你的四柱：庚午 甲申 乙丑 丙子，日主乙木…"
    assert h._pillar_claims_conflict(reply, CHART_BAZI) is True


def test_conflict_accepts_correct_pillars(tmp_path):
    """与已存盘一致的四柱 → 不冲突。"""
    h = _handler_with_chart(tmp_path)
    reply = "你的命盘：庚午 辛巳 乙酉 甲申，今年财星得地…"
    assert h._pillar_claims_conflict(reply, CHART_BAZI) is False


def test_conflict_requires_four_ganzhi(tmp_path):
    """不足 4 个干支（非排盘类回复）→ 不判冲突。"""
    h = _handler_with_chart(tmp_path)
    assert h._pillar_claims_conflict("宜穿蓝色，今天适合出行", CHART_BAZI) is False


def test_conflict_order_sensitive(tmp_path):
    """顺序敏感：同组干支错位（QA 的月/日柱错位）也算冲突。"""
    h = _handler_with_chart(tmp_path)
    assert h._pillar_claims_conflict("四柱：庚午 乙酉 辛巳 甲申", CHART_BAZI) is True


def test_conflict_no_chart_returns_false(tmp_path):
    """无已存盘（空库）→ 无冲突（fail-open，不误伤任何回复）。"""
    from src.storage.chart_dao import ChartDAO
    h = object.__new__(MessageHandler)
    h.chart_dao = ChartDAO(str(tmp_path / "c.db"))
    assert h._pillar_claims_conflict("四柱：庚午 甲申 乙丑 丙子", CHART_BAZI) is True
    # _enforce_pillar_integrity 无存档 → 原样通过
    out, blocked = h._enforce_pillar_integrity("四柱：庚午 甲申 乙丑 丙子", None, "u1")
    assert out == "四柱：庚午 甲申 乙丑 丙子" and blocked is False


# ─────────────────────────── 终审执行器（_enforce_pillar_integrity）───────────────────────────

def test_enforce_falls_back_to_engine_draft(tmp_path):
    """冲突 + 有引擎原稿 → 回退原稿（正确性优先），且不禁缓存（回退稿可入缓存，
    顺带把错误缓存路径换成正确值）。"""
    h = _handler_with_chart(tmp_path)
    engine_draft = "【引擎结果】你的四柱：庚午 辛巳 乙酉 甲申…今年财运稳步上升…"
    wrong = "今年财运…四柱：庚午 甲申 乙丑 丙子…"
    out, blocked = h._enforce_pillar_integrity(wrong, engine_draft, "u1")
    assert out == engine_draft
    assert blocked is False


def test_enforce_blocks_cache_when_no_draft(tmp_path):
    """冲突 + 无引擎原稿 → 保留回复但禁入缓存（防 0.78s 固化错误）。"""
    h = _handler_with_chart(tmp_path)
    wrong = "你的四柱：庚午 甲申 乙丑 丙子"
    out, blocked = h._enforce_pillar_integrity(wrong, None, "u1")
    assert out == wrong
    assert blocked is True


def test_enforce_passes_correct_reply(tmp_path):
    """正确回复 → 原样通过，不禁缓存。"""
    h = _handler_with_chart(tmp_path)
    good = "你的四柱：庚午 辛巳 乙酉 甲申，财运稳步上升"
    out, blocked = h._enforce_pillar_integrity(good, None, "u1")
    assert out == good
    assert blocked is False


def test_enforce_fallback_reply_is_cacheable(tmp_path):
    """回退后的引擎原稿内容与已存盘一致 → 后续再判不冲突（可正常缓存）。"""
    h = _handler_with_chart(tmp_path)
    engine_draft = "你的四柱：庚午 辛巳 乙酉 甲申…"
    out, blocked = h._enforce_pillar_integrity("错误四柱：庚午 甲申 乙丑 丙子",
                                               engine_draft, "u1")
    assert out == engine_draft
    out2, blocked2 = h._enforce_pillar_integrity(out, engine_draft, "u1")
    assert out2 == engine_draft and blocked2 is False


# ─────────────────────────── 流年干支断言终审（D2 延伸）───────────────────────────

def test_liunian_conflict_detects_wrong_current_year(tmp_path):
    """'今年是丙子年'（已存盘 2026=丙午）→ 冲突（QA 复测观察的真实案例）。"""
    h = _handler_with_chart(tmp_path)  # liunian {"2026": "丙午"}
    reply = "今年秋天金气当令…好在流年地支有子水（今年是丙子年）…"
    assert h._liunian_claims_conflict(reply, "u1") is True


def test_liunian_conflict_accepts_correct_year_claims(tmp_path):
    """与已存盘一致的年份干支断言 → 不冲突。"""
    h = _handler_with_chart(tmp_path)
    for reply in ("今年（2026丙午年）财运…", "2026年是丙午年，火旺…",
                  "今年丙午流年，食伤生财…"):
        assert h._liunian_claims_conflict(reply, "u1") is False, reply


def test_liunian_conflict_skips_unverifiable_years(tmp_path):
    """年份不在已存流年表（无法比对）→ 放行（fail-open）。"""
    h = _handler_with_chart(tmp_path)  # liunian 仅 2026
    assert h._liunian_claims_conflict("2028年戊申是换运之年…", "u1") is False


def test_liunian_conflict_requires_year_claim(tmp_path):
    """无年份断言（纯干支名词）→ 不冲突。"""
    h = _handler_with_chart(tmp_path)
    assert h._liunian_claims_conflict("乙木日主，火旺克金，官杀并透…", "u1") is False


def test_enforce_liunian_conflict_falls_back_to_draft(tmp_path):
    """流年冲突 + 有引擎原稿 → 回退原稿，不禁缓存。"""
    h = _handler_with_chart(tmp_path)
    engine_draft = "你的四柱：庚午 辛巳 乙酉 甲申…今年丙午流年…"
    wrong = "你的四柱：庚午 辛巳 乙酉 甲申…今年是丙子年…"
    out, blocked = h._enforce_pillar_integrity(wrong, engine_draft, "u1")
    assert out == engine_draft
    assert blocked is False


def test_enforce_liunian_conflict_blocks_cache_without_draft(tmp_path):
    """流年冲突 + 无原稿 → 保留回复但禁入缓存。"""
    h = _handler_with_chart(tmp_path)
    wrong = "你的四柱：庚午 辛巳 乙酉 甲申，今年是丙子年…"
    out, blocked = h._enforce_pillar_integrity(wrong, None, "u1")
    assert out == wrong
    assert blocked is True


# ─────────────────────────── 润色页脚/反馈尾回接（D2 机制修复）───────────────────────────

def _polish_harness():
    """_polish_with_engine_draft 最小装配（Mock LLM 仅回显正文，不碰网络）。"""
    h = object.__new__(MessageHandler)
    h.llm = SimpleNamespace(api_key="test-key", model="test-model")
    h._citations = {"u1": []}
    h.session_dao = None
    return h


def test_polish_footer_reattached_with_timestamp_skew(tmp_path):
    """页脚时间戳与起草时跨秒（QA 场景）→ 正则剥离后润色仍原样回接一次。

    旧逻辑 endswith 逐字比对，起草与润色跨秒即失配 → 页脚被丢进 LLM、
    tail='' → 页脚丢失。修复后 count==1。
    """
    h = _polish_harness()
    old_footer = ("---\n解读版本: v5.0.0 | 生成时间: 2026-08-23T23:20:29+08:00\n"
                  "同一八字同一问题，结果始终一致")
    draft = (f"你的四柱：庚午 辛巳 乙酉 甲申，今年财运稳步上升\n\n"
             f"———\n💬 这个分析对你有帮助吗？👍 有帮助  👎 不太准\n\n{old_footer}")
    with patch("src.llm.client.deepseek_anthropic_completion",
               return_value="润色后的正文（四柱：庚午 辛巳 乙酉 甲申）"):
        out = h._polish_with_engine_draft("今年财运怎么样", "u1", draft)
    assert out.count("解读版本") == 1           # 页脚仅回接一次、未重复
    assert out.count("有帮助吗") == 1           # 反馈尾仅回接一次
    assert "👍" not in out                       # emoji 旧版被换为纯文本版
    assert "庚午 辛巳 乙酉 甲申" in out          # 正文数据保留
    assert out.rstrip().endswith("同一八字同一问题，结果始终一致")  # 页脚在文末


def test_polish_footer_plain_feedback_variant(tmp_path):
    """纯文本反馈尾（老格式）同样剥离并回接。"""
    h = _polish_harness()
    draft = ("你的四柱：庚午 辛巳 乙酉 甲申\n\n"
             "———\n这个分析对你有帮助吗？可回复「准」或「不准」告诉我")
    with patch("src.llm.client.deepseek_anthropic_completion",
               return_value="润色后的正文（四柱：庚午 辛巳 乙酉 甲申）"):
        out = h._polish_with_engine_draft("今年财运怎么样", "u1", draft)
    assert out.count("有帮助吗") == 1
    assert out.endswith("告诉我")


# ───────────── P1 #2：润色路径教学统一 JSON 工单 + 注入工具清单 ─────────────

def test_polish_system_search_hint_json_workorder():
    """P1 #2 Fix 2：search_hint 分支教学改 JSON 工单（web_search），
    system 注入 [可用工具清单]，旧 <tool_call> 单标签教学不再出现。"""
    from unittest.mock import Mock
    h = _polish_harness()
    h.session_dao = Mock()
    h.session_dao.get_context_for_llm.return_value = []
    draft = "你的四柱：庚午 辛巳 乙酉 甲申"
    with patch("src.llm.client.deepseek_anthropic_completion") as m:
        m.return_value = "润色正文"
        h._polish_with_engine_draft("今年财运怎么样", "u1", draft,
                                    search_hint=True, session_id="s1")
    msgs = m.call_args[0][1]  # (api_key, messages, ...)
    system = msgs[0]["content"]
    assert "<tool_calls>" in system
    assert "web_search" in system
    assert '[{"tool": "web_search", "params": {"query": "具体关键词"}}]' in system
    assert "[可用工具清单]" in system
    assert "<tool_call>搜索:" not in system
    assert "<tool_call>检索:" not in system


def test_polish_history_user_hint_json_workorder():
    """P1 #2 Fix 2：history 分支末尾 user 提示同步改 JSON 工单。"""
    from unittest.mock import Mock
    h = _polish_harness()
    h.session_dao = Mock()
    h.session_dao.get_context_for_llm.return_value = [
        {"role": "user", "content": "今年财运怎么样"}]
    draft = "你的四柱：庚午 辛巳 乙酉 甲申"
    with patch("src.llm.client.deepseek_anthropic_completion") as m:
        m.return_value = "润色正文"
        h._polish_with_engine_draft("今年财运怎么样", "u1", draft,
                                    search_hint=True, session_id="s1")
    msgs = m.call_args[0][1]
    last = msgs[-1]
    assert last["role"] == "user"
    assert "<tool_calls>" in last["content"]
    assert "web_search" in last["content"]
    assert "<tool_call>搜索:" not in last["content"]
