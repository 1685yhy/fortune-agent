# -*- coding: utf-8 -*-
"""k19：存量 bazi_info bazi 键清理迁移 — 判定函数单测（2026-09-10）。

脚本：scripts/migrate_stale_bazi_keys.py（纯判定函数 + CLI dry-run/execute）。
本测试只测纯函数（stale_verdict / normalize_pillars / has_bazi_key /
has_complete_birth / _chart_birth_matches），零网络零 LLM 零 DB 写。

21:44 事故族样本：
- 脏四柱 = ["丙午","丙申","甲子","甲子"]（2026-08-18 0点子时 他人/择时盘，
  08-16 污染源）；本人 birth=1995-03-28 9点男长春 → 复算四柱与脏四柱不等。
- 真一致盘：本人 birth 的引擎复算四柱 = bazi 键 → 保留。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

from migrate_stale_bazi_keys import (  # noqa: E402
    KEEP_CHART, KEEP_NO_KEY, KEEP_SELF, STALE_CONTRADICT, STALE_ORPHAN,
    STALE_UNVERIFIABLE, normalize_pillars, has_bazi_key,
    has_complete_birth, stale_verdict, _chart_birth_matches,
)

POLLUTED = ["丙午", "丙申", "甲子", "甲子"]  # 2026-08-18 0点盘（事故四柱）


def _row(**kw):
    """1995-03-28 9:0 男 长春 solar 默认行；传 None = 删除该键（模拟不齐）。"""
    base = {"year": 1995, "month": 3, "day": 28, "hour": 9, "minute": 0,
            "gender": "男", "city": "长春", "calendar": "solar"}
    for k, v in kw.items():
        if v is None:
            base.pop(k, None)
        else:
            base[k] = v
    return base


# ── normalize / 键存在性 ──

def test_normalize_pillars_shapes():
    assert normalize_pillars(["甲", "乙", "丙", "丁"]) == ["甲", "乙", "丙", "丁"]
    assert normalize_pillars("甲 乙 丙 丁") == ["甲", "乙", "丙", "丁"]
    assert normalize_pillars(["甲", "乙"]) is None
    assert normalize_pillars("甲乙丙丁") is None  # 8 字长串不可分
    assert normalize_pillars(None) is None
    assert normalize_pillars([1, 2, 3, 4]) == ["1", "2", "3", "4"]


def test_has_bazi_key_only_nonempty_pillars():
    assert has_bazi_key(_row(bazi=POLLUTED)) is True
    assert has_bazi_key(_row(bazi="丙午 丙申 甲子 甲子")) is True
    assert has_bazi_key(_row()) is False          # 无键
    assert has_bazi_key(_row(bazi=None)) is False
    assert has_bazi_key(_row(bazi=[])) is False
    assert has_bazi_key(_row(bazi=["甲", "乙"])) is False  # 形状不可比


def test_has_complete_birth():
    assert has_complete_birth(_row()) is True
    assert has_complete_birth({"year": 1995, "month": 3, "day": 28}) is True
    assert has_complete_birth({"year": 1995, "month": 3}) is False  # 缺日
    assert has_complete_birth({}) is False


# ── 判定核心：21:44 事故族 vs 一致盘 ──

def test_verdict_contradicts_birth_polluted():
    """事故族：bazi 键与本人 birth 复算矛盾 → stale_contradicts_birth。"""
    own_pillars = ["乙亥", "己卯", "丁丑", "癸卯"]  # 1995-03-28 9点复算占位
    info = _row(bazi=POLLUTED)
    assert stale_verdict(info, engine_pillars=own_pillars) == STALE_CONTRADICT


def test_verdict_keep_self_corroborated():
    """真一致：bazi 键 == 本人 birth 复算四柱（真实本人盘镜像）→ 保留。"""
    own_pillars = ["乙亥", "己卯", "丁丑", "癸卯"]
    info = _row(bazi=own_pillars)
    assert stale_verdict(info, engine_pillars=own_pillars) == KEEP_SELF


def test_verdict_orphan_no_birth():
    """孤儿键：bazi 存在但 birth y/m/d 不齐（08-16 污染族形态）→ 清理。"""
    info = {"bazi": POLLUTED, "hour": 0, "city": "北京"}
    assert stale_verdict(info) == STALE_ORPHAN
    info2 = _row(year=None, month=None, day=None, bazi=POLLUTED)
    assert stale_verdict(info2) == STALE_ORPHAN


def test_verdict_unverifiable_then_chart():
    """复算不可得（引擎失败注入 None）→ 盘证决定：
    有匹配盘且 bazi 相等 → 保留；盘证亦无 → 清理。"""
    info = _row(bazi=POLLUTED)
    assert stale_verdict(info, engine_pillars=None,
                         chart_pillars=POLLUTED) == KEEP_CHART
    assert stale_verdict(info, engine_pillars=None,
                         chart_pillars=None) == STALE_UNVERIFIABLE
    # 盘证四柱与键不等 → 清理
    assert stale_verdict(info, engine_pillars=None,
                         chart_pillars=["甲子", "乙丑", "丙寅", "丁卯"]) \
        == STALE_UNVERIFIABLE
    # birth 不齐时盘证不适用（无 birth 可匹配）→ 孤儿清理
    orphan = {"bazi": POLLUTED}
    assert stale_verdict(orphan, chart_pillars=POLLUTED) == STALE_ORPHAN


def test_verdict_no_key():
    assert stale_verdict(_row()) == KEEP_NO_KEY
    assert stale_verdict(None) == KEEP_NO_KEY
    assert stale_verdict("junk") == KEEP_NO_KEY


# ── chart 匹配口径（k8 _chart_birth_matches 同款）──

def test_chart_birth_matches():
    info = _row()  # 1995-03-28 9:0 长春 solar
    same = _row()
    assert _chart_birth_matches(same, info) is True
    # 时辰具体值不同但同有时辰（档案代表整点 9 vs 盘 10 时同属巳时）→ 匹配
    assert _chart_birth_matches(_row(hour=10, minute=55), info) is True
    # hour 0 ≡ None（persons _birth_dict 折叠）→ 匹配
    assert _chart_birth_matches(_row(hour=None), _row(hour=0)) is True
    # 年份不同（他人/择时盘）→ 不匹配
    assert _chart_birth_matches(_row(year=2026, month=8, day=18, hour=0),
                                info) is False
    # 历法不同 → 不匹配
    assert _chart_birth_matches(_row(calendar="lunar"), info) is False
    # 一有时辰一无时辰 → 不匹配
    assert _chart_birth_matches(_row(hour=None), info) is False
    # 空行 → 不匹配
    assert _chart_birth_matches(None, info) is False
    assert _chart_birth_matches({}, info) is False
