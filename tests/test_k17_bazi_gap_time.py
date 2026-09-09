# -*- coding: utf-8 -*-
"""k17-5（k16 审查 Minor-2 防御）：current_stage_facts 交运年表部分段缺 time。

盲区实证（改造前探针）：中段缺 time → now 越过缺口段虚岁岁首后仍停上一段
（过期展示）；首段缺 time → 起运前守卫失效、静默落 k11 虚岁兜底（无告警）。
引擎产出恒全量（_calc_jiaoyun 9 段全带 time），以下仅手工构造/旧数据/时刻串
损坏可达。防御语义：缺口段在虚岁已满后按 k11 兜底口径过渡计入 + warning 一次；
首段缺 time 且 now 早于首条已知时刻 → 整体虚岁兜底 + warning；全量数据零告警
零行为变化（k16 锁定语义由 tests/test_k16_calendar_dst.py 回归背书）。

基准命盘 golden：1999-05-13 09:00 长春 男（同 k16/k11 golden）；dayun 12 段
（sui 3..113）、交运年表 9 条（sui 3..83，交运时刻 2001/2011/…/2081-09-25 09:25）。
"""
import sys
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import pytest  # noqa: E402

from src.engines.bazi import BaziEngine, current_stage_facts  # noqa: E402


@pytest.fixture(scope="module")
def golden():
    r = BaziEngine().calculate(1999, 5, 13, 9, 0, "长春", "男")
    return r


def _jy(golden, drop_sui=None, drop_time_sui=None, corrupt_time_sui=None):
    """构造 jiaoyun 变体：整体删条目 / 删 time / 时刻串损坏（引擎数据不动）。"""
    out = []
    for y in golden.jiaoyun["years"]:
        if y["sui"] == drop_sui:
            continue
        y = dict(y)
        if y["sui"] == drop_time_sui:
            y.pop("time", None)
        if y["sui"] == corrupt_time_sui:
            y["time"] = "2026-99-99 99:99"  # dt() 抛 ValueError → 视为无精确时刻
        out.append(y)
    return {"years": out}


def _probe(golden, jiaoyun, now):
    return current_stage_facts(1999, 5, 13, golden.dayun, jiaoyun,
                               golden.liunian_rel, now=now)


class TestMiddleGapMissingTime:
    def test_gap_entry_missing_now_past_nominal_advances(self, golden):
        """中段整条缺失（sui=53 不在年表）：now 越过虚岁岁首（2051-01-01 虚岁 53
        满）→ 按 k11 兜底口径过渡计入 idx5（不再停 idx4 过期展示）。"""
        f = _probe(golden, _jy(golden, drop_sui=53),
                   datetime(2051, 6, 1, 12, 0))
        assert f["age_xusui"] == 53
        assert f["dayun_index"] == 5
        assert f["dayun_ganzhi"] == golden.dayun[5][1]
        assert f["next_ganzhi"] == golden.dayun[6][1]  # sui63 有精确时刻（未到）

    def test_gap_boundary_jan1_advances_dec31_stays(self, golden):
        """缺口过渡边界 = 虚岁岁首 2051-01-01 00:00（虚岁 53 满）起计入。"""
        jy = _jy(golden, drop_sui=53)
        f1 = _probe(golden, jy, datetime(2050, 12, 31, 23, 59))
        assert f1["dayun_index"] == 4
        f2 = _probe(golden, jy, datetime(2051, 1, 1, 0, 0))
        assert f2["dayun_index"] == 5

    def test_gap_not_reached_stays_exact(self, golden):
        """缺口段虚岁未满（2050-06，虚岁 52）→ 仍精确段 idx4，零兜底。"""
        f = _probe(golden, _jy(golden, drop_sui=53),
                   datetime(2050, 6, 1, 12, 0))
        assert f["age_xusui"] == 52
        assert f["dayun_index"] == 4

    def test_broken_time_entry_gap_advances_with_warning(self, golden, caplog):
        """年表有条目但 time 缺失（time=None）→ 缺口过渡 + logger.warning 一次。"""
        jy = _jy(golden, drop_time_sui=53)
        f = _probe(golden, jy, datetime(2051, 6, 1, 12, 0))
        assert f["dayun_index"] == 5
        assert "k17-5" in caplog.text and "sui=53" in caplog.text

    def test_corrupt_time_string_treated_as_gap(self, golden, caplog):
        """时刻串损坏（dt() 抛错）→ 同缺口处理（过渡 + 告警）。"""
        jy = _jy(golden, corrupt_time_sui=53)
        f = _probe(golden, jy, datetime(2051, 6, 1, 12, 0))
        assert f["dayun_index"] == 5
        assert "k17-5" in caplog.text


class TestHeadBrokenFallback:
    def test_first_segment_time_missing_before_first_known(self, golden, caplog):
        """首段（sui=3）缺 time 且 now 早于首条已知时刻（2011-09-25）→ 整体虚岁
        兜底 idx0 戊辰（k11 原行为，改造前静默→现告警暴露降级）。"""
        jy = _jy(golden, drop_time_sui=3)
        f = _probe(golden, jy, datetime(2001, 3, 1, 12, 0))
        assert f["age_xusui"] == 3
        assert f["dayun_index"] == 0 and f["dayun_ganzhi"] == "戊辰"
        assert "k17-5" in caplog.text

    def test_first_segment_missing_pre_qiyun_still_no_segment(self, golden):
        """首段缺 time 且虚岁未满（2000-06，虚岁 2 < sui3）→ 缺键（起运前语义）。"""
        f = _probe(golden, _jy(golden, drop_time_sui=3),
                   datetime(2000, 6, 1, 12, 0))
        assert "dayun_ganzhi" not in f and "dayun_index" not in f

    def test_first_segment_missing_after_first_known_exact_ok(self, golden, caplog):
        """首段缺 time 但 now 已过后续已知时刻（2021-03）→ 精确链照常（idx1 丁卯，
        与全量数据同答），零告警——缺失仅影响早于首条已知时刻的窗口。"""
        f = _probe(golden, _jy(golden, drop_time_sui=3),
                   datetime(2021, 3, 1, 12, 0))
        assert f["dayun_index"] == 1 and f["dayun_ganzhi"] == "丁卯"
        assert "k17-5" not in caplog.text


class TestFullDataSilence:
    def test_full_data_no_warning_and_k16_semantics(self, golden, caplog):
        """引擎恒全量路径：多处 now 零告警、段选与 k16 锁定语义一致（防御零漂移）。"""
        for now, idx in [(datetime(2021, 1, 1), 1),   # 虚岁岁首早于交运 → 丁卯
                         (datetime(2026, 9, 10, 12, 0), 2),  # 现实 → 丙寅
                         (datetime(2081, 9, 25, 9, 26), 8),
                         (datetime(2095, 6, 1), 9)]:  # 表外虚岁近似
            f = _probe(golden, golden.jiaoyun, now)
            assert f["dayun_index"] == idx
        assert "k17-5" not in caplog.text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
