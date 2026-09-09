# -*- coding: utf-8 -*-
"""k17-3/4：search_trigger 判定单扫去重 + 白名单语义记录锁定。

- k17-3（k11b-r1 审查残留 P3「calc 双扫为装饰性开销」）：decide_search 原在软路径
  （无硬锚 + 口语决策族/主题族，如「跳槽什么时候合适」）对 LOCAL_FORTUNE_ANCHOR_RE
  整扫两遍（calc 一次 + has_local_fortune_anchor 内再一次）。改 _local_fortune_verdict
  单扫后：任何路径该正则每 decide_search / has_local_fortune_anchor 只扫 1 遍。
- k17-4：白名单层语义记录（「这个行业怎么样」触发 = 与旧关键词门控等价，非回归）
  —— 本文件锁定该语义（触发 + reason=whitelist + query 非空），防未来误当回归收紧。
- 语义逐字未变：代表性判定对照表锁定（T074/T105/T108 语义族 + 两档本地锚边界）。
"""
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import pytest  # noqa: E402

import src.rag.search_trigger as st  # noqa: E402
from src.rag.search_trigger import (decide_search,  # noqa: E402
                                    has_local_fortune_anchor)


class _CountingRe:
    """包装正则统计 search 调用次数（打桩模块级正则，行为透传）。"""

    def __init__(self, rx):
        self.rx = rx
        self.n = 0

    def search(self, text):
        self.n += 1
        return self.rx.search(text)


@pytest.fixture
def counting(monkeypatch):
    orig = st.LOCAL_FORTUNE_ANCHOR_RE
    counting_re = _CountingRe(orig)
    monkeypatch.setattr(st, "LOCAL_FORTUNE_ANCHOR_RE", counting_re)
    return counting_re


def test_hard_anchor_path_single_scan(counting):
    """硬锚命中路径：calc 短路不再需要二次整扫（改造前=1 次，保持）。"""
    d = decide_search("今年运势如何")
    assert d.should_search is False and d.reason == "local"
    assert counting.n == 1


def test_soft_local_path_single_scan(counting):
    """软路径（无硬锚 + 口语决策族）：改造前同正则整扫 2 遍 → 现 1 遍。"""
    d = decide_search("跳槽什么时候合适")
    assert d.should_search is False and d.reason == "local"
    assert counting.n == 1


def test_has_local_fortune_anchor_single_scan(counting):
    """公开函数独立调用：硬锚/软路径各 1 遍（软路径命中 cue+动词族）。"""
    assert has_local_fortune_anchor("今年运势如何") is True
    assert counting.n == 1
    assert has_local_fortune_anchor("跳槽什么时候合适") is True
    assert counting.n == 2


def test_entity_strong_and_weak_parity():
    """实体层判定对照（k11b T105/T108 语义，单扫改造逐字未变）。"""
    assert decide_search("易宝支付这家公司靠不靠谱").reason == "entity"
    assert decide_search("腾讯这家公司怎么样，适合我的事业吗？").reason == "entity"
    # 实体背景 + 硬锚本地问 → 不搜（弱问词挂在运势上）
    d = decide_search("我在易宝支付上班，今年运势怎么样")
    assert d.should_search is False and d.reason == "local"


def test_local_anchor_parity():
    """硬锚/口语决策族/主题族 负例锁定（k11b P1-A 复现句式 + T074 语义）。"""
    for s in ["今年运势如何", "我明年财运怎么样", "这个月适合搬家吗",
              "跳槽什么时候合适", "去开公司适合我吗",
              "五行属水的行业适合开公司吗", "最近工作运怎么样",
              "今天股市行情怎么样"]:
        d = decide_search(s)
        assert d.should_search is False, s
        assert d.reason in ("local", "finance"), s


def test_timely_and_whitelist_parity():
    """时效层 + 白名单兜底正例（k11b P2-B 合法外部时效问）。"""
    assert decide_search("最近有什么行业新闻").reason == "timely"
    # k17-4 语义锁定：无实体/无时效词的「这个行业怎么样」经研究白名单兜底触发
    # （行业移出 deictic 表后的自然结果 = 与旧关键词门控等价，非回归）；
    # query=整句精简（泛化词），产物=真实检索 + 来源尾注，绝不甩锅
    d = decide_search("这个行业怎么样")
    assert d.should_search is True and d.reason == "whitelist"
    assert d.query  # 非空（泛化 query，宁搜勿漏本位）


def test_empty_msg_no_scan(counting):
    decide_search("")
    assert counting.n == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
