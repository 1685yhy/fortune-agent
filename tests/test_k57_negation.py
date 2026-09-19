# -*- coding: utf-8 -*-
"""k57：`_affirmed_pick` "全被否定 → 首命中"口径收口（P0）。

现象：`我不是1990年生的，我老婆是1991年7月8日的`
- 基线 `8d690ca`：取 **(1991,7,8)** = 对方的生辰当本人；
- k56 `36382a1`：取 **(1990,·)** = 用户明确否认的年；
- **两者都错 → 正解 = 不取**（走"问一句/不写档"）。

修法：`_affirmed_pick` 增第 4 返回值 `blocked` —— **全部候选组都被否定**时返回
"不取"（调用方连"基线首命中"与年兜底链都不走）；否则维持既有口径。
**但"全被否定"必须是严格口径**（`_negated_group_ids(strict=True)`）：
  ① 只认**价值否定**标记——`记错/填错/写错/说错/弄错` 是"前一条记错了"的**行为**
     描述（`记错了是农历3月28日` 的 3月28日 恰恰是纠正后的正确值）；
     `记错了` 里的 `错了` 与 `记错` 重叠 → 一并排除；
  ② 标记与候选必须**同一小句**——`我其实是1999年出生的，不是1995` 的"不是"跨了小句、
     且指向的 1995 根本不是候选。
（这两条是**先按"全被否定就一律不取"实现后、实测抓到 3 条合法纠正消息被误杀**
外加 1 条既有测试翻红后补的，见报告 r2/诚实披露。）

隔离：零网络零 LLM（纯提取层）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

import pytest  # noqa: E402

from src.bot.handler import MessageHandler, _negated_group_ids  # noqa: E402


def _p(msg, **kw):
    return object.__new__(MessageHandler)._extract_partial_birth(msg, **kw)


def _b(msg):
    return object.__new__(MessageHandler)._extract_bazi_info(msg)


def _empty(msg):
    got = _p(msg)
    return not (got.get("year") or got.get("month") or got.get("day"))


# ════════════════════════════════════════════════════════════════
# ① 全被否定 → 不取（本条本体 + ≥6 条同族变体）
# ════════════════════════════════════════════════════════════════
ALL_NEGATED = [
    ("我不是1990年生的，我老婆是1991年7月8日的", "审查原例：改前取对方生辰或否认的年"),  # noqa: E501
    ("我不是1990年生的", "单年否认（改前写 1990）"),
    ("我不是1990年生的，也不是1991年生的", "双年否认（改前写 1990）"),
    ("我不是5月13日生的", "单月日否认（改前写 5/13）"),
    ("我不是5月13日生的，也不是6月7日生的", "双月日否认（改前写 5/13）"),
    ("我不是腊月廿六生的", "中文月日否认（改前写 12/26）"),
    ("我不是二〇〇一年生的", "中文年否认（改前写 2001）"),
    ("我不是1995年3月8日出生的", "完整日期否认"),
    ("我不是5月13日生的，我老公是6月7日生的", "否认本人 + 对方候选（归属层丢弃）"),
]


class TestAllNegatedNoFallback:
    @pytest.mark.parametrize("msg,why", ALL_NEGATED)
    def test_nothing_taken(self, msg, why):
        assert _empty(msg), (why, msg, _p(msg))

    def test_full_extractor_returns_none(self):
        """全量提取器同判据（不排盘、不建档）。"""
        assert _b("我不是1990年生的") is None
        assert _b("我不是1990年生的，我老婆是1991年7月8日的") is None

    def test_age_and_cn_year_fallbacks_blocked(self):
        """年兜底链（中文年/年龄推算）不得把被否认的值捡回来。"""
        assert _empty("我不是二〇〇一年生的，我今年50岁了")
        assert _empty("我今年50岁了，不是二〇〇一年生的")

    def test_partial_keeps_other_fields(self):
        """不取只针对被否认的年/月日；同句其他出生字段（城市/性别）不受影响。"""
        got = _p("我出生在长春，我不是1990年生的，男")
        assert got.get("year") is None, got
        assert got.get("city") == "长春" and got.get("gender") == "男", got

    def test_strict_predicate_direct(self):
        """结构直证：strict 口径 vs 宽口径。"""
        import re
        from src.bot.handler import (_CnMdCand, _MD_CAND_RE, _cn_month_day_of,
                                     _date_groups, _valid_month_day)
        msg = "我不是1990年生的"
        ym = list(re.finditer(r'(\d{4})\s*年', msg))
        groups = _date_groups(msg, ym, [])
        assert _negated_group_ids(msg, groups) == {0}
        assert _negated_group_ids(msg, groups, strict=True) == {0}


# ════════════════════════════════════════════════════════════════
# ② 合法纠正族（**naive 版会误杀**，本批用严格口径保住）
# ════════════════════════════════════════════════════════════════
class TestLegitCorrectionsKept:
    @pytest.mark.parametrize("msg,year,md", [
        ("我其实是1999年3月28日出生，之前填错了", 1999, (3, 28)),
        ("我其实是1999年出生的，不是1995", 1999, None),
        ("记错了是农历3月28日", None, (3, 28)),
        ("之前填错了，其实是1995年3月8日出生的", 1995, (3, 8)),
    ])
    def test_corrected_value_taken(self, msg, year, md):
        """`填错/记错` 说的是**旧记录**错，被声明的是纠正后的正确值 → 必须取。"""
        got = _p(msg)
        if year is not None:
            assert got.get("year") == year, (msg, got)
        if md is not None:
            assert (got.get("month"), got.get("day")) == md, (msg, got)

    def test_value_denial_still_blocks(self):
        """价值否定（`是不对的`）仍属"全被否定" → 不取。"""
        assert _empty("5月20日是不对的")

    def test_strict_excludes_entry_act_overlap(self):
        """`记错了` 里的 `错了` 与行为标记 `记错` 重叠 → strict 不算否认。"""
        import re
        from src.bot.handler import _date_groups
        msg = "记错了是农历3月28日"
        ym = list(re.finditer(r'(\d{4})\s*年', msg))
        md = [c for c in re.finditer(r'农历3\s*月\s*28\s*[日号]?', msg)]
        groups = _date_groups(msg, ym, md)
        assert len(groups) == 1
        assert _negated_group_ids(msg, groups) == {0}          # 宽口径（既有）
        assert _negated_group_ids(msg, groups, strict=True) == set()   # 严格 → 不否认


# ════════════════════════════════════════════════════════════════
# ③ 既有否定裁决族逐条保持（k49/k50/k56 战果不许回退）
# ════════════════════════════════════════════════════════════════
AFFIRMED = [
    ("我不是1995年生的，是1999年生的", 1999, None),
    ("我1995年生的？不是，我1999年生的", 1999, None),
    ("我1999年3月28日生的，不是1995年3月8日生的", 1999, (3, 28)),
    ("不是3月28日，是5月13日", None, (5, 13)),
    ("不是正月初一，是腊月廿六生的", None, (12, 26)),
    ("我不是腊月廿六生的，是正月初一生的", None, (1, 1)),
    ("我不是三月廿六生的，是四月初一生的", None, (4, 1)),
    ("我不是3月28日，不是5月13日，是6月7日", None, (6, 7)),
    ("我不是二〇〇一年生的，是二〇〇三年生的", 2003, None),
    ("不是腊月廿六生的，是5月20日生的", None, (5, 20)),
]


class TestAffirmedFamilyKept:
    @pytest.mark.parametrize("msg,year,md", AFFIRMED)
    def test_affirmed_value_taken(self, msg, year, md):
        got = _p(msg)
        if year is not None:
            assert got.get("year") == year, (msg, got)
        if md is not None:
            assert (got.get("month"), got.get("day")) == md, (msg, got)

    def test_unaffected_non_correction_messages(self):
        """非纠正消息走既有口径（不受本批影响）。"""
        assert _p("我1990年生的").get("year") == 1990
        assert _p("我今年50岁了", current_year=2026).get("year") == 1976
