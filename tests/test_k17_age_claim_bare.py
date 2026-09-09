# -*- coding: utf-8 -*-
"""k17-6（k15 审查 Minor-2 记录补拦）：age_claim 岁字省略形态。

既有缺口（base 实测漏拦）：「虚岁33了/本人虚岁33。/今年我虚岁33。/我现在
虚岁33啦」无「岁」字 → _AGE_CLAIM_RES 全漏（真实年龄事故句形态）。k17 补两个
pattern（完成体收尾型 + 声明前缀型，排除面镜像 pattern2）。

事实源 = 真实引擎 golden 1999-05-13 09:00 长春 男（与 test_k11_fact_discipline
TestEvalDerived 同款），窗口 [26,29]（27 周岁/28 虚岁 ±1）。本文件只锚
k17-6 行为；事故句/双单位/require_mention 等既有回归在 test_k11_fact_discipline
（k15 段 35 passed）背书。
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import pytest  # noqa: E402

from src.engines.bazi import BaziEngine  # noqa: E402
from scripts.eval_agent.l2_eval import _age_claim_check  # noqa: E402


@pytest.fixture(scope="module")
def facts():
    r = BaziEngine().calculate(1999, 5, 13, 9, 0, "长春", "男")
    cs = r.current_stage
    return {"age_zhousui": cs["age_zhousui"], "age_xusui": cs["age_xusui"]}


def _fails(reply, facts):
    """age_claim 判定为 FAIL = 回复含窗口外当前年龄声明。"""
    return not _age_claim_check(reply, facts, {})["ok"]


class TestBareAgeClaimNowCaught:
    """岁字省略事故句（改造前漏拦）→ 现拦。"""

    @pytest.mark.parametrize("s", [
        "虚岁33了",
        "我已经虚岁33了",
        "本人虚岁33。",
        "今年我虚岁33。",
        "我现在虚岁33啦",
        "命主虚岁33，正走乙丑大运",   # 33+句读 = 前缀声明的当前年龄断言
        "今年我虚岁33岁，正走乙丑大运。",  # 岁字版既有事故句（k15 锚）仍拦
    ])
    def test_bare_accident_claims_blocked(self, s, facts):
        assert _fails(s, facts), s


class TestDayunEndpointNoMisfire:
    """换运/大运端点表述（含虚岁 N 无岁字形态）不误拦。"""

    @pytest.mark.parametrize("s", [
        "虚岁23到32岁走丙寅大运",
        "从虚岁33起走乙丑大运",
        "乙丑大运虚岁33交运",
        "虚岁33时换入乙丑大运",
        "虚岁33开始走乙丑运",
        "今年运势不错，33岁进入乙丑大运",
        "虚岁23岁到32岁。",          # k15 双单位收紧样例回归
        "丙寅运从23岁到虚岁32岁。",  # k15 双单位收紧样例回归
    ])
    def test_dayun_boundary_phrases_pass(self, s, facts):
        assert not _fails(s, facts), s


class TestWindowNumbersAndBaseline:
    """窗内数字不受影响 + 既有事故句回归 + 既有误拦族记录。"""

    def test_window_numbers_bare_forms_pass(self, facts):
        assert not _fails("虚岁28了", facts)
        assert not _fails("今年虚岁28了", facts)
        assert not _fails("我现在28岁，虚岁29也快了。", facts)

    def test_existing_accident_sentences_still_blocked(self, facts):
        for s in ["你今年33岁", "我现在33岁了", "命主今年33岁",
                  "命主虚岁33岁", "我今年33岁，正走乙丑大运"]:
            assert _fails(s, facts), s

    def test_known_misfire_families_documented(self, facts):
        """既有误拦族（非本批引入，记录在案）：
        - 反问/引用否定「你虚岁33了？不，我今年28岁。」——岁字版（你虚岁33岁？）
          k15 review 已记录 base 同误拦，本批为同族形态延伸；
        - 「虚岁33岁时进入乙丑大运」——pattern2 前瞻缺「时」（k15 双单位收紧
          复现样例未覆盖），base 已误拦（实测证据），本批未扩未改，列后续项。
        本测试仅锁定「两族均为 FAIL 判定且不误伤窗内表述」的组合行为。"""
        assert _fails("你虚岁33了？不，我今年28岁。", facts)
        assert _fails("虚岁33岁时进入乙丑大运", facts)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
