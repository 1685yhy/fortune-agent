# -*- coding: utf-8 -*-
"""k85 必修5 · 接续 k83：`advisor_v2` 链与 E 段（无依据具体结论）的接线。

## 背景（k83 登记的第 5 条残留）

k83 报告 §⑧.5：「`advisor_v2` 的 GLM 降级链（k63 D 段消费点）**未接** E 段 ——
它的输入带真实引擎盘面（有依据）…E 段接口已就绪，一行即可接入」。
控制方要求本批**接上并实测**，且要「**有真盘面时不误杀**」的证据。

## 实测结论（本文件把它钉住，不是推断）

先查清"那条 GLM 降级链"到底是什么（k85 更正了 k83 的误记，见
`src/utils/fact_guard.py` D 段注释）：

  · `git log -S "glm_openai_completion" -- src/engines/advisor_v2.py` → **零命中**；
  · 全仓**生产**路径里 `glm_openai_completion` 的唯一调用点是
    `src/llm/client.py:567` = `_chat_lite`（**对话降级链**）—— 出事的正是它，
    而它在 k83 就已接线；
  · `advisor_v2._call_llm` 只调 `deepseek_anthropic_completion`，输入恒是
    `BaziEngine` 实算的真实盘面。

▶ 真调实测（2026-09-23，免费 glm-4-flash ×3 条真实 advisor 输出，
  盘面 = 1999-03-28 10:55 长春 男 ⇒ 己卯 丁卯 己卯 庚午）：

  | 口径 | 命中 | 文本保留率 |
  |---|---|---|
  | `has_real_tool_result=True`（= E 段契约"一字不改"） | 0 / 0 / 0 | 100% |
  | `False` + 盘面作依据表 | 10 / 52 / 15 | **90.1% / 62.9% / 78.4%** |

被删的**全是建议卡的时间窗口日期**（如 `2027年9月15日至10月15日`），而本链
prompt 第 4 条**要求**"时间窗口要具体到日期范围" ⇒ 写 `False` 会删掉本功能的
核心内容（**误杀**）。根因：那些日期是模型综合大运/流年**推出来的新值**，不是
盘面上的字面值，`_ung_grounded` 的"字面包含 / 数字连续子序列"对它不成立。

⇒ 正确接线 = `has_real_tool_result=True`（盘面即依据 ⇒ E 段在该链**不适用**），
并用 `grounded_refs_from_chart` 把"依据表"显式化（同一实现，不另起一套）。
本文件同时钉住**判据本身没坏**：同一句式换成编造值会被拦。

红线：不联网（除既有 `glm_route` 夹具，本文件不一例外呼）、不碰生产库、
不新增 skip/xfail、未放宽任何既有断言。
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

from src.engines.advisor_v2 import LIFE_DOMAINS, AdaptiveAdvisor  # noqa: E402
from src.engines.bazi import BaziEngine                            # noqa: E402
from src.utils import fact_guard as fg                             # noqa: E402


@pytest.fixture(scope="module")
def chart():
    """真实盘面（`BaziEngine` 实算，非构造）。"""
    return BaziEngine().calculate(1999, 3, 28, 10, 55, "长春", "男")


def _mock_llm_json(advice="结合命盘看流年", timing="2027年9月15日-10月15日"):
    return json.dumps({
        "actions": [{"category": c, "advice": advice, "timing": timing,
                     "confidence": "high"} for c in LIFE_DOMAINS],
        "serendipity": "顺带一提：你的桃花星很旺。",
        "daily_tip": "今天宜静不宜动。",
        "style_notes": "命格偏强，宜顺势而为",
    }, ensure_ascii=False)


class TestGroundedRefsFromChart:
    """依据表构造器（单一实现）：盘面上的字面值必须都在里面。"""

    def test_carries_the_chart_literals(self, chart):
        refs = fg.grounded_refs_from_chart(chart)
        assert refs, "依据表为空 —— 接线会变成『无依据』误判"
        for p in chart.bazi:                       # 四柱干支
            assert p in refs, f"四柱 {p} 不在依据表"
        assert chart.day_master in refs
        joined = "|".join(refs)
        for p in chart.bazi:                       # 四柱必须以整串形式也可匹配
            assert p in joined

    def test_none_and_empty_are_safe(self):
        assert fg.grounded_refs_from_chart(None) == ()

    def test_deduplicates(self, chart):
        refs = fg.grounded_refs_from_chart(chart)
        assert len(refs) == len(set(refs)), "依据表有重复项"


class TestRealChartIsNotMisKilled:
    """控制方要的证据：**有真盘面时不误杀**（判据本身没坏，坏的是口径选择）。"""

    def test_chart_grounded_sentence_survives(self, chart):
        """整句都由盘面字面值组成 → 0 命中、一字不改（False 口径下也不误杀）。"""
        refs = fg.grounded_refs_from_chart(chart)
        s = f"你的四柱是{' '.join(chart.bazi)}，日主{chart.day_master}。"
        assert fg.ungrounded_claim_hits(s, False, refs) == [], \
            f"真盘面句被误判为无依据：{fg.ungrounded_claim_hits(s, False, refs)}"
        assert fg.scrub_ungrounded_claims(s, False, refs) == s

    def test_fabricated_values_are_still_caught(self, chart):
        """同一句式换成**不在盘面上**的具体值 → 必须被拦（判据仍有效）。"""
        refs = fg.grounded_refs_from_chart(chart)
        fab = "你命里庚午年、己巳月、乙巳日、丙申时，2027年9月15日至10月15日是窗口。"
        hits = fg.ungrounded_claim_hits(fab, False, refs)
        assert any(h.startswith("date:") for h in hits), f"编造日期没被拦：{hits}"
        assert fg.scrub_ungrounded_claims(fab, False, refs) != fab

    def test_true_flag_is_the_documented_no_op(self, chart):
        """E 段契约：`has_real_tool_result=True` ⇒ 一字不改（本链采用的口径）。"""
        s = "任何文本，包括 2027年9月15日 与 庚午年。"
        assert fg.ungrounded_claim_hits(s, True, ()) == []
        assert fg.scrub_ungrounded_claims(s, True, ()) == s


class TestAdvisorChainIsWired:
    """**接线**本身：advisor_v2 的呈现字段确实过了 E 段，且口径是 True。"""

    def test_presentation_fields_go_through_the_guard(self, chart, monkeypatch):
        advisor = AdaptiveAdvisor()
        monkeypatch.setattr(advisor, "_call_llm",
                            lambda prompt, api_key: _mock_llm_json())
        calls = []
        real = fg.scrub_ungrounded_claims

        def _spy(text, has_real_tool_result, allowed_refs=()):
            calls.append((text, has_real_tool_result, tuple(allowed_refs)))
            return real(text, has_real_tool_result, allowed_refs)

        monkeypatch.setattr(fg, "scrub_ungrounded_claims", _spy)
        out = advisor.generate(chart, user_context="想问事业", api_key="k")

        assert calls, "advisor_v2 的呈现字段没有过 E 段（未接线）"
        # advice / timing / concrete_steps / success_metric × 5 + 3 个标量字段
        assert len(calls) >= len(LIFE_DOMAINS) + 3, f"接线字段数偏少：{len(calls)}"
        assert all(c[1] is True for c in calls), (
            "E 段口径不是 True —— 盘面即依据的链上写 False 会删掉时间窗口日期"
            "（实测保留率最低 62.9%，见本文件 docstring）")
        assert all(c[2] for c in calls), "依据表为空（grounded_refs_from_chart 没接上）"
        # 输出仍可解析、字段完整（接线没把结构改坏）
        assert [a["category"] for a in out["actions"]] == list(LIFE_DOMAINS)


class TestWhyNotFalseReasonPin:
    """**理由钉死**（非安全断言）：这条链上写 False 会误杀本功能的核心内容。

    说明：本用例锁的是"当前判据 + 当前 prompt 要求"下的**因果**。将来若
    `_ung_grounded` 学会放行"由大运/流年推出来的日期窗口"，本用例会红 ——
    那次红是**有信息量的**：届时重估接线口径即可（不是回归）。
    """

    def test_required_date_window_would_be_deleted(self, chart):
        refs = fg.grounded_refs_from_chart(chart)
        timing = "2027年9月15日-10月15日"          # 本链 prompt 第 4 条要求的具体日期范围
        kept = fg.scrub_ungrounded_claims(timing, False, refs)
        assert kept != timing, (
            "判据变了：具体日期窗口不再被删 —— 请重估 advisor 链的 E 段口径")
        assert len(kept) < len(timing) / 2, f"实际保留 {kept!r}（预期被大段删除）"

    def test_real_repo_shaped_advice_survives_under_true(self, chart):
        """仓库既有测试的 advice/timing 原样（含具体日期窗口）在 True 口径下逐字保留。

        这正是"**有真盘面时不误杀**"的端到端形态：mock LLM 返回与
        `tests/test_adaptive_advisor.py` 同形的字段值，generate 出口一字不改。
        """
        advisor = AdaptiveAdvisor()
        advice = "今年适合深耕现有领域，不宜贸然跳槽。"
        timing = "2027年9月15日-10月15日"
        monkeypatch_free = _mock_llm_json(advice=advice, timing=timing)
        advisor._call_llm = lambda prompt, api_key: monkeypatch_free
        out = advisor.generate(chart, user_context="想问事业", api_key="k")
        for a in out["actions"]:
            assert a["advice"] == advice, f"真建议被改写/截断：{a['advice']!r}"
            assert a["timing"] == timing, f"时间窗口被删：{a['timing']!r}"
        assert out["daily_tip"] == "今天宜静不宜动。"
